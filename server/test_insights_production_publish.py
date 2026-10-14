"""Offline production-boundary tests: temporary synthetic data only.

No server access, key access, model call, real report publication or provisioning.
"""
import copy
import hashlib
import io
import json
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import insights_publish as publisher
import insights_production_publish as production


def localized():
    return {'en': 'Synthetic local fixture only.',
            'el': 'Μόνο τοπικά συνθετικά δεδομένα.',
            'ru': 'Только локальные синтетические данные.'}


class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.state = self.root / 'fixture-state'
        (self.state / 'public').mkdir(parents=True)
        self.history = self.root / 'fixture-history'
        self.history.mkdir()
        self.sentinel = self.root / 'unchanged.txt'
        self.sentinel.write_bytes(b'unchanged')
        self.now = 1791800040

    def call(self, command, payload=None, **kwargs):
        raw = b'' if payload is None else publisher.encoded(payload)
        return production.dispatch(command, raw, state=self.state, history=self.history,
                                   publisher=publisher, now=kwargs.get('now', self.now))

    def payload(self, prepared):
        return {'analysis_id': prepared['result']['analysis_id'],
                'summary': localized(), 'notes': []}

    def test_fixture_prepare_and_publication_have_explicit_production_provenance(self):
        with patch.object(publisher, 'prepare', wraps=publisher.prepare) as prepare:
            result = self.call('prepare')
        prepare.assert_called_once_with(state=self.state, history=self.history, now=self.now)
        self.assertEqual(set(result), {'scope', 'synthetic', 'result'})
        self.assertEqual(result['scope'], 'production')
        self.assertIs(result['synthetic'], False)
        payload = self.payload(result)
        with patch.object(publisher, 'publish', wraps=publisher.publish) as publish:
            response = self.call('publish', payload)
        publish.assert_called_once_with(publisher.encoded(payload), state=self.state, now=self.now)
        self.assertEqual(response, {'scope': 'production', 'synthetic': False,
                         'result': {'status': 'published', 'id': payload['analysis_id']}})
        feed = json.loads((self.state / 'public/index.json').read_text(encoding='utf-8'))
        self.assertEqual(feed['reports'][0]['narrative']['summary'], localized())
        self.assertEqual(list(self.history.iterdir()), [])
        self.assertEqual(self.sentinel.read_bytes(), b'unchanged')

    def test_identical_retry_preserves_feed_and_changed_report_is_refused(self):
        payload = self.payload(self.call('prepare'))
        self.call('publish', payload)
        feed = self.state / 'public/index.json'
        before = feed.read_bytes()
        stamp = feed.stat().st_mtime_ns
        self.assertEqual(self.call('publish', payload)['result']['status'], 'already_published')
        self.assertEqual(feed.stat().st_mtime_ns, stamp)
        changed = copy.deepcopy(payload)
        changed['summary']['en'] = 'Attempted replacement.'
        with self.assertRaises(publisher.Rejected):
            self.call('publish', changed)
        self.assertEqual(feed.read_bytes(), before)

    def test_all_languages_and_known_findings_are_still_required(self):
        payload = self.payload(self.call('prepare'))
        for invalid in [dict(payload, summary={'en': 'English only'}),
                        dict(payload, notes=[{'finding_id': 'unknown', 'commentary': localized()}]),
                        dict(payload, state='/tmp/attacker')]:
            with self.subTest(invalid=invalid), self.assertRaises(publisher.Rejected):
                self.call('publish', invalid)
        self.assertFalse((self.state / 'public/index.json').exists())

    def test_payload_size_and_commands_are_checked_before_publisher(self):
        fake = Mock()
        for command, raw in [('', b''), ('prepare /tmp/x', b''), ('publish; id', b''),
                             ('scp -t /tmp/x', b''), (['prepare'], b''),
                             ('prepare', b'{}'), ('prepare', ''), ('publish', b''),
                             ('publish', b'x' * (production.MAX_INPUT + 1))]:
            with self.subTest(command=command), self.assertRaises(production.Rejected):
                production.dispatch(command, raw, state=self.state, history=self.history,
                                    publisher=fake, now=self.now)
        self.assertEqual(fake.mock_calls, [])

    def test_malformed_json_never_publishes(self):
        for raw in [b'NaN', b'{"x":1,"x":2}', b'{}{}', b'\xff']:
            with self.subTest(raw=raw), self.assertRaises(publisher.Rejected):
                production.dispatch('publish', raw, state=self.state, history=self.history,
                                    publisher=publisher, now=self.now)
        self.assertEqual(list(self.state.iterdir()), [self.state / 'public'])

    def test_response_ids_and_closed_shapes_are_validated(self):
        result = self.call('prepare')['result']
        invalid_prepare = [dict(result, extra='value'), dict(result, analysis_id='0' * 64),
                           dict(result, analysis=dict(result['analysis'], arbitrary='value')),
                           dict(result, analysis=dict(result['analysis'], schema_version=True))]
        for invalid in invalid_prepare:
            with self.subTest(invalid=invalid), self.assertRaises(production.Rejected):
                production.validate_result('prepare', invalid)
        for invalid in [{'status': 'staged', 'id': 'a' * 64},
                        {'status': 'published', 'id': 'A' * 64},
                        {'status': 'published', 'id': 'a' * 64, 'secret': 'no'},
                        {'status': 'published', 'id': []}, []]:
            with self.subTest(invalid=invalid), self.assertRaises(production.Rejected):
                production.validate_result('publish', invalid)

    def test_maximum_analysis_is_bounded_before_response(self):
        result = self.call('prepare')['result']
        result['analysis']['limitations'] = ['x' * production.MAX_INPUT]
        result['analysis_id'] = hashlib.sha256(production.encoded(result['analysis'])).hexdigest()
        with self.assertRaises(production.Rejected):
            production.validate_result('prepare', result)


class GateTests(unittest.TestCase):
    def test_false_gate_denies_and_true_gate_has_exact_contract(self):
        gate = {'schema_version': 1, 'scope': 'production', 'public_publishing_enabled': False}
        with patch.object(production, '_read_trusted_file', return_value=production.encoded(gate)), \
                self.assertRaises(production.Rejected):
            production.verify_gate()
        gate['public_publishing_enabled'] = True
        with patch.object(production, '_read_trusted_file', return_value=production.encoded(gate)) as read:
            production.verify_gate()
        read.assert_called_once_with(production.GATE, 256)

    def test_missing_malformed_duplicate_or_non_boolean_gate_denies(self):
        valid = {'schema_version': 1, 'scope': 'production', 'public_publishing_enabled': True}
        invalid = [dict(valid, schema_version=True), dict(valid, scope='staging'),
                   dict(valid, public_publishing_enabled=1), dict(valid, public_publishing_enabled='true'),
                   dict(valid, extra=True), {}]
        bodies = [production.encoded(item) for item in invalid]
        bodies += [b'{"schema_version":1,"scope":"production","public_publishing_enabled":false,"public_publishing_enabled":true}',
                   b'NaN', b'\xff', b'[]', b'{']
        for raw in bodies:
            with self.subTest(raw=raw), patch.object(production, '_read_trusted_file', return_value=raw), \
                    self.assertRaises((ValueError, production.Rejected)):
                production.verify_gate()
        with patch.object(production, '_read_trusted_file', side_effect=FileNotFoundError), \
                self.assertRaises(FileNotFoundError):
            production.verify_gate()


class InstallationTests(unittest.TestCase):
    def environment(self, **changes):
        account = SimpleNamespace(pw_uid=1234, pw_gid=1234,
                                  pw_dir=str(production.HOME), pw_shell='/usr/bin/dash')
        for key, value in changes.items():
            setattr(account, key, value)
        def path_factory(value):
            resolved = production.SHELL if value in ('/bin/dash', '/usr/bin/dash') else production.ENTRY
            return SimpleNamespace(resolve=lambda **_: resolved)
        patches = [patch.object(production.sys, 'platform', 'linux'),
                   patch.object(production.sys, 'flags', SimpleNamespace(isolated=1, dont_write_bytecode=1)),
                   patch.dict(production.sys.modules, {
                       'pwd': SimpleNamespace(getpwnam=Mock(return_value=account)),
                       'grp': SimpleNamespace(getgrnam=Mock(return_value=SimpleNamespace(gr_gid=1234)))}),
                   patch.object(production.os, 'getuid', return_value=1234, create=True),
                   patch.object(production.os, 'geteuid', return_value=1234, create=True),
                   patch.object(production.os, 'getgid', return_value=1234, create=True),
                   patch.object(production.os, 'getegid', return_value=1234, create=True),
                   patch.object(production.os, 'getgroups', return_value=[1234], create=True),
                   patch.object(production, 'Path', side_effect=path_factory)]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def test_gate_failure_precedes_state_history_and_publisher(self):
        self.environment()
        with patch.object(production, 'trusted_path') as trusted, \
                patch.object(production, 'verify_gate', side_effect=production.Rejected), \
                patch.object(production, 'verify_readonly_history') as history, \
                patch.object(production, 'load_publisher') as load, self.assertRaises(production.Rejected):
            production.verify_environment()
        history.assert_not_called()
        load.assert_not_called()
        self.assertEqual([call.args[0] for call in trusted.call_args_list],
                         [production.HOME, production.MODULES, production.ENTRY])

    def test_valid_environment_checks_fixed_installation(self):
        self.environment()
        with patch.object(production, 'trusted_path') as trusted, \
                patch.object(production, 'verify_gate') as gate, \
                patch.object(production, 'verify_readonly_history') as history, \
                patch.object(production, 'load_publisher', return_value=publisher) as load:
            self.assertIs(production.verify_environment(), publisher)
        gate.assert_called_once_with()
        history.assert_called_once_with()
        load.assert_called_once_with()
        trusted.assert_any_call(production.STATE, 1234, 0o711, directory=True)
        trusted.assert_any_call(production.STATE / 'public', 1234, 0o755,
                                directory=True, allowed_parent=production.STATE)

    def test_runtime_flags_root_ids_and_extra_groups_fail_closed(self):
        self.environment()
        cases = [('flags', SimpleNamespace(isolated=0, dont_write_bytecode=1)),
                 ('flags', SimpleNamespace(isolated=1, dont_write_bytecode=0)),
                 ('platform', 'win32')]
        with patch.object(production, 'trusted_path') as trusted:
            for name, value in cases:
                with self.subTest(name=name, value=value), patch.object(production.sys, name, value), \
                        self.assertRaises(production.Rejected):
                    production.verify_environment()
            for name, value in [('getuid', 0), ('geteuid', 0), ('getgid', 0),
                                ('getegid', 5678), ('getgroups', [1234, 42])]:
                with self.subTest(name=name), patch.object(production.os, name, return_value=value), \
                        self.assertRaises(production.Rejected):
                    production.verify_environment()
        trusted.assert_not_called()

    def test_wrong_account_home_or_shell_denied(self):
        self.environment(pw_dir='/home/attacker')
        with patch.object(production, 'trusted_path') as trusted, self.assertRaises(production.Rejected):
            production.verify_environment()
        trusted.assert_not_called()

    def test_reviewed_usrmerge_shell_alias_is_accepted(self):
        self.environment(pw_shell='/bin/dash')
        with patch.object(production, 'trusted_path'), patch.object(production, 'verify_gate'), \
                patch.object(production, 'verify_readonly_history'), \
                patch.object(production, 'load_publisher', return_value=publisher):
            self.assertIs(production.verify_environment(), publisher)

    def test_wrong_shell_is_rejected_before_gate(self):
        self.environment(pw_shell='/bin/bash')
        with patch.object(production, 'verify_gate') as gate, self.assertRaises(production.Rejected):
            production.verify_environment()
        gate.assert_not_called()

    def test_source_hashes_match_reviewed_local_modules(self):
        for name, expected in production.SOURCE_HASHES.items():
            body = (Path(__file__).parent / (name + '.py')).read_bytes()
            self.assertEqual(hashlib.sha256(body).hexdigest(), expected)

    def test_source_only_loader_uses_verified_bytes_in_dependency_order(self):
        bodies = {'fake_dependency': b'value = 42\n',
                  'insights_publish': b'import fake_dependency\nvalue = fake_dependency.value\n'}
        hashes = {name: hashlib.sha256(body).hexdigest() for name, body in bodies.items()}
        saved = production.sys.modules.pop('insights_publish')
        try:
            with patch.object(production, 'SOURCE_HASHES', hashes), \
                    patch.object(production, '_read_trusted_file', side_effect=list(bodies.values())):
                loaded = production.load_publisher()
            self.assertEqual(loaded.value, 42)
            self.assertEqual(loaded.__file__, str(production.MODULES / 'insights_publish.py'))
        finally:
            production.sys.modules.pop('fake_dependency', None)
            production.sys.modules['insights_publish'] = saved

    def test_source_digest_mismatch_cannot_execute_even_earlier_module(self):
        code = b'raise AssertionError("must not execute")\n'
        with patch.object(production, 'SOURCE_HASHES', {'unused_production_source': '0' * 64}), \
                patch.object(production, '_read_trusted_file', return_value=code), \
                self.assertRaises(production.Rejected):
            production.load_publisher()
        self.assertNotIn('unused_production_source', production.sys.modules)

    def test_existing_module_is_rejected_not_reused(self):
        with patch.object(production, '_read_trusted_file') as read, self.assertRaises(production.Rejected):
            production.load_publisher()
        read.assert_not_called()

    def path_mock(self, *, owner=0, mode=0o644, links=1, parent_owner=0, parent_mode=0o755):
        parent = Mock()
        parent.lstat.return_value = SimpleNamespace(st_mode=stat.S_IFDIR | parent_mode, st_uid=parent_owner)
        path = Mock()
        path.is_absolute.return_value = True
        path.resolve.return_value = path
        path.lstat.return_value = SimpleNamespace(st_mode=stat.S_IFREG | mode, st_uid=owner, st_nlink=links)
        path.parents = [parent]
        return path, parent

    def test_trusted_paths_reject_wrong_owner_modes_links_or_writable_parent(self):
        for options in [{'owner': 1234}, {'mode': 0o666}, {'links': 2},
                        {'parent_owner': 1234}, {'parent_mode': 0o775}]:
            path, _ = self.path_mock(**options)
            with self.subTest(options=options), self.assertRaises(production.Rejected):
                production.trusted_path(path, 0, 0o644)
        path, _ = self.path_mock()
        production.trusted_path(path, 0, 0o644)
        path.resolve.return_value = Mock()
        with self.assertRaises(production.Rejected):
            production.trusted_path(path, 0, 0o644)

    def test_readonly_history_rejects_writable_history_or_any_ancestor(self):
        root, parent = self.path_mock(mode=0o755)
        root.lstat.return_value.st_mode = stat.S_IFDIR | 0o755
        with patch.object(production, 'HISTORY', root), \
                patch.object(production.os, 'access', side_effect=[False, False, True]):
            production.verify_readonly_history()
        for access in [[True], [False, True], [False, False, False]]:
            with self.subTest(access=access), patch.object(production, 'HISTORY', root), \
                    patch.object(production.os, 'access', side_effect=access), self.assertRaises(production.Rejected):
                production.verify_readonly_history()

    def test_cli_rejects_arguments_and_nonexact_commands_before_environment(self):
        with patch.object(production, 'verify_environment') as verify:
            for argv, command in [(['entry', '--enable'], 'prepare'), (['entry'], 'publish; id'),
                                  (['entry'], 'prepare /tmp/x'), (['entry'], '')]:
                with self.subTest(argv=argv, command=command), patch.object(production.sys, 'argv', argv), \
                        patch.dict(production.os.environ, {'SSH_ORIGINAL_COMMAND': command}), \
                        self.assertRaises(production.Rejected):
                    production.main()
        verify.assert_not_called()

    def test_cli_ignores_path_environment_and_uses_fixed_paths(self):
        response = {'scope': 'production', 'synthetic': False, 'result': {}}
        with patch.object(production.sys, 'argv', ['entry']), \
                patch.dict(production.os.environ, {'SSH_ORIGINAL_COMMAND': 'prepare',
                           'STATE': '/tmp/other', 'HISTORY': '/tmp/other'}), \
                patch.object(production, 'verify_environment', return_value=publisher), \
                patch.object(production.os, 'umask'), \
                patch.object(production, 'dispatch', return_value=response) as dispatch, \
                patch.object(production.sys, 'stdout', new_callable=io.StringIO) as output:
            production.main()
        dispatch.assert_called_once_with('prepare', b'', state=production.STATE,
                                         history=production.HISTORY, publisher=publisher)
        self.assertEqual(json.loads(output.getvalue()), response)

    def test_new_ssh_template_enforces_fixed_isolated_gated_entry(self):
        template = (Path(__file__).parent.parent / 'deploy/reports-production-sshd.conf.example').read_text()
        for directive in [
            'Match User homeenergy-reporter', 'AuthenticationMethods publickey',
            'PubkeyAuthentication yes', 'PasswordAuthentication no',
            'KbdInteractiveAuthentication no', 'AuthorizedKeysCommand none',
            'AuthorizedKeysFile /etc/ssh/authorized_keys/homeenergy-reporter',
            'TrustedUserCAKeys none', 'PubkeyAcceptedAlgorithms ssh-ed25519',
            'DisableForwarding yes', 'AllowAgentForwarding no', 'AllowTcpForwarding no',
            'X11Forwarding no', 'PermitTTY no', 'PermitTunnel no', 'PermitUserRC no',
            'MaxSessions 1',
            'ForceCommand /usr/bin/timeout --signal=TERM --kill-after=5s 45s /usr/bin/python3 -I -B /opt/homeenergy-reports/insights_production_publish.py',
        ]:
            self.assertIn(directive, template)
        self.assertNotIn('insights_staging_publish.py', template)
        self.assertNotIn(' -I /opt/homeenergy-reports/insights_publish.py', template)


if __name__ == '__main__':
    unittest.main()
