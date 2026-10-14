"""Staging publisher tests; temporary synthetic state only, no SSH or models."""
import copy
from contextlib import closing
from datetime import datetime
import io
import json
from pathlib import Path
import sqlite3
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import insights_analysis as analyzer
import insights_publish as publisher
import insights_staging_publish as staging


def localized():
    return {'en': 'Synthetic staging review only.',
            'el': 'Μόνο δοκιμαστική αναφορά με συνθετικά δεδομένα.',
            'ru': 'Только тестовый отчёт с синтетическими данными.'}


class StagingPublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.state = self.root / 'staging-state'
        (self.state / 'public').mkdir(parents=True)
        self.history = self.root / 'synthetic-history'
        self.history.mkdir()
        self.sentinel = self.root / 'unrelated.txt'
        self.sentinel.write_text('unchanged', encoding='utf-8')
        self.now = 1791800040  # A complete minute; all fixtures remain local.

    def call(self, command, payload=None, now=None):
        raw = b'' if payload is None else publisher.encoded(payload)
        return staging.dispatch(command, raw, state=self.state, history=self.history,
                                now=self.now if now is None else now)

    def payload(self, snapshot):
        return {'analysis_id': snapshot['result']['analysis_id'],
                'summary': localized(), 'notes': []}

    def test_empty_history_uses_real_analyzer_and_explicit_staging_paths(self):
        with patch.object(publisher, 'prepare', wraps=publisher.prepare) as prepare:
            snapshot = self.call('prepare')
        self.assertEqual(prepare.call_args.kwargs,
                         {'state': self.state, 'history': self.history, 'now': self.now})
        self.assertEqual(set(snapshot), {'scope', 'synthetic', 'result'})
        self.assertEqual(snapshot['scope'], 'staging')
        self.assertIs(snapshot['synthetic'], True)
        self.assertEqual(snapshot['result']['analysis']['schema_version'], 2)
        self.assertEqual(snapshot['result']['analysis']['severity'], 'data_issue')
        self.assertEqual(snapshot['result']['analysis']['coverage_pct'], 0)
        payload = self.payload(snapshot)
        with patch.object(publisher, 'publish', wraps=publisher.publish) as publish:
            response = self.call('publish', payload)
        self.assertEqual(publish.call_args.kwargs, {'state': self.state, 'now': self.now})
        self.assertEqual(response, {'scope': 'staging', 'synthetic': True, 'result':
                         {'status': 'staged', 'id': payload['analysis_id']}})
        feed = json.loads((self.state / 'public/index.json').read_text(encoding='utf-8'))
        self.assertEqual(feed['reports'][0]['analysis'], snapshot['result']['analysis'])
        self.assertEqual(feed['reports'][0]['narrative']['summary'], localized())
        self.assertEqual(self.sentinel.read_text(encoding='utf-8'), 'unchanged')
        self.assertEqual(list(self.history.iterdir()), [])

    def test_synthetic_history_is_read_without_modifying_fixture_or_sentinel(self):
        timestamp = self.now - 60
        day = datetime.fromtimestamp(timestamp, analyzer.ATHENS).date().isoformat()
        path = self.history / (day + '.json')
        values = {'pv': 1200, 'grid': 0, 'load': 600, 'battery': 500, 'soc': 65}
        fixture = publisher.encoded({'day': day, 'points': [
            {'t': timestamp, 'values': values, 'counts': {key: 1 for key in values}}]})
        path.write_bytes(fixture)
        snapshot = self.call('prepare')
        self.assertGreater(snapshot['result']['analysis']['coverage_pct'], 0)
        self.call('publish', self.payload(snapshot))
        self.assertEqual(path.read_bytes(), fixture)
        self.assertEqual(self.sentinel.read_text(encoding='utf-8'), 'unchanged')
        self.assertEqual(set(self.root.iterdir()), {self.state, self.history, self.sentinel})

    def test_identical_retry_is_idempotent_and_changed_content_cannot_overwrite(self):
        payload = self.payload(self.call('prepare'))
        self.call('publish', payload)
        before = (self.state / 'public/index.json').read_bytes()
        self.assertEqual(self.call('publish', payload)['result']['status'], 'already_staged')
        changed = copy.deepcopy(payload)
        changed['summary']['en'] = 'Attempt to overwrite the archived report.'
        with self.assertRaises(publisher.Rejected):
            self.call('publish', changed)
        self.assertEqual((self.state / 'public/index.json').read_bytes(), before)
        with closing(sqlite3.connect(self.state / 'reports.sqlite3')) as database:
            self.assertEqual(database.execute('SELECT count(*) FROM reports').fetchone()[0], 1)

    def test_all_translations_and_known_findings_remain_authoritative(self):
        payload = self.payload(self.call('prepare'))
        for invalid in [dict(payload, summary={'en': 'Missing languages'}),
                        dict(payload, notes=[{'finding_id': 'unknown', 'commentary': localized()}]),
                        dict(payload, path='/var/lib/homeenergy-reports')]:
            with self.subTest(payload=invalid), self.assertRaises(publisher.Rejected):
                self.call('publish', invalid)
        self.assertFalse((self.state / 'public/index.json').exists())

    def test_malformed_payloads_do_not_publish(self):
        for raw in [b'', b'NaN', b'{"x":1,"x":2}', b'{}{}', b'\xff',
                    b'a' * (publisher.MAX_INPUT + 1)]:
            with self.subTest(raw=raw[:30]), self.assertRaises((ValueError, publisher.Rejected)):
                staging.dispatch('publish', raw, state=self.state, history=self.history, now=self.now)
        self.assertEqual(list(self.state.iterdir()), [self.state / 'public'])

    def test_unknown_commands_and_prepare_input_rejected_before_publisher(self):
        with patch.object(publisher, 'prepare') as prepare, patch.object(publisher, 'publish') as publish:
            for command in ['', 'sh', 'prepare /tmp/x', 'publish; id', 'scp -t /tmp/x', ['prepare']]:
                with self.subTest(command=command), self.assertRaises(publisher.Rejected):
                    staging.dispatch(command, b'', state=self.state, history=self.history)
            with self.assertRaises(publisher.Rejected):
                staging.dispatch('prepare', b'{"path":"/tmp"}', state=self.state, history=self.history)
            prepare.assert_not_called()
            publish.assert_not_called()
        self.assertEqual(list(self.state.iterdir()), [self.state / 'public'])


class InstallationGateTests(unittest.TestCase):
    def test_constants_cannot_select_production_store(self):
        self.assertEqual(str(staging.STATE).replace('\\', '/'), '/var/lib/homeenergy-reports-staging')
        self.assertNotEqual(staging.STATE, publisher.STATE)
        self.assertNotEqual(staging.HISTORY, publisher.HISTORY)
        self.assertEqual(staging.USER, 'homeenergy-reporter-staging')

    def test_cli_arguments_and_bad_commands_rejected_before_environment_or_reads(self):
        cases = [(['program', '--state=/tmp'], 'prepare'), (['program'], 'prepare /tmp'),
                 (['program'], 'publish; id'), (['program'], '')]
        with patch.object(staging, 'verify_environment') as verify, patch.object(staging, 'dispatch') as dispatch:
            for argv, command in cases:
                with self.subTest(argv=argv, command=command), patch.object(staging.sys, 'argv', argv), \
                        patch.dict(staging.os.environ, {'SSH_ORIGINAL_COMMAND': command}), \
                        self.assertRaises(publisher.Rejected):
                    staging.main()
            verify.assert_not_called()
            dispatch.assert_not_called()

    def test_cli_ignores_path_environment_and_passes_only_fixed_constants(self):
        expected = {'scope': 'staging', 'synthetic': True, 'result': {}}
        with patch.object(staging.sys, 'argv', ['program']), \
                patch.dict(staging.os.environ, {'SSH_ORIGINAL_COMMAND': 'prepare',
                           'STATE': '/tmp/attacker', 'HISTORY': '/tmp/attacker'}), \
                patch.object(staging, 'verify_environment'), patch.object(staging.os, 'umask'), \
                patch.object(staging, 'dispatch', return_value=expected) as dispatch, \
                patch.object(staging.sys, 'stdout', new_callable=io.StringIO) as output:
            staging.main()
        dispatch.assert_called_once_with('prepare', b'', state=staging.STATE, history=staging.HISTORY)
        self.assertEqual(json.loads(output.getvalue()), expected)

    def test_non_linux_environment_rejected(self):
        with patch.object(staging.sys, 'platform', 'win32'), self.assertRaises(publisher.Rejected):
            staging.verify_environment()

    def test_root_and_wrong_user_rejected_before_path_checks(self):
        account = SimpleNamespace(pw_uid=1234)
        fake_pwd = SimpleNamespace(getpwnam=Mock(return_value=account))
        for effective_uid, expected_uid in [(0, 1234), (5678, 1234), (0, 0)]:
            account.pw_uid = expected_uid
            with self.subTest(uid=effective_uid, expected=expected_uid), \
                    patch.object(staging.sys, 'platform', 'linux'), \
                    patch.dict(staging.sys.modules, {'pwd': fake_pwd}), \
                    patch.object(staging.os, 'geteuid', return_value=effective_uid, create=True), \
                    patch.object(staging, 'trusted_path') as trusted, \
                    self.assertRaises(publisher.Rejected):
                staging.verify_environment()
            trusted.assert_not_called()
        fake_pwd.getpwnam.assert_called_with('homeenergy-reporter-staging')

    def path_mock(self, *, owner=0, mode=0o644, links=1, parent_owner=0, parent_mode=0o755):
        parent = Mock()
        parent.lstat.return_value = SimpleNamespace(st_mode=stat.S_IFDIR | parent_mode, st_uid=parent_owner)
        path = Mock()
        path.is_absolute.return_value = True
        path.resolve.return_value = path
        path.lstat.return_value = SimpleNamespace(st_mode=stat.S_IFREG | mode, st_uid=owner, st_nlink=links)
        path.parents = [parent]
        return path, parent

    def test_trusted_paths_reject_writable_or_wrong_owner_or_hardlinked_modules(self):
        for options in [{'owner': 1234}, {'mode': 0o666}, {'links': 2},
                        {'parent_owner': 1234}, {'parent_mode': 0o775}]:
            path, _ = self.path_mock(**options)
            with self.subTest(options=options), self.assertRaises(publisher.Rejected):
                staging.trusted_path(path, 0, 0o644)
        path, _ = self.path_mock()
        staging.trusted_path(path, 0, 0o644)
        path.resolve.return_value = Mock()
        with self.assertRaises(publisher.Rejected):
            staging.trusted_path(path, 0, 0o644)

    def test_private_state_public_directory_allows_only_explicit_staging_parent(self):
        path, parent = self.path_mock(owner=1234, mode=0o700, parent_owner=1234, parent_mode=0o700)
        path.lstat.return_value.st_mode = stat.S_IFDIR | 0o700
        with self.assertRaises(publisher.Rejected):
            staging.trusted_path(path, 1234, 0o700, directory=True)
        staging.trusted_path(path, 1234, 0o700, directory=True, allowed_parent=parent)


if __name__ == '__main__':
    unittest.main()
