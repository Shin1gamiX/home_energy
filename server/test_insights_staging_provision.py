"""Provisioning contract tests: mocks and synthetic files, never root or SSH."""
import base64
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch


HELPER = Path(__file__).resolve().parents[1] / 'deploy/provision-report-staging.py'
SPEC = importlib.util.spec_from_file_location('staging_provision', HELPER)
provision = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(provision)
TEMPLATE = HELPER.parent / 'reports-staging-sshd.conf.example'


def public_key():
    # Fixed non-secret bytes with the wire shape of an Ed25519 PUBLIC key.
    blob = b'\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20' + bytes(range(32))
    return b'ssh-ed25519 ' + base64.b64encode(blob) + b' synthetic-only\n'


class ProvisioningContractTests(unittest.TestCase):
    def test_key_line_has_two_independent_restrictions_and_no_comment(self):
        result = provision.authorized_line(public_key()).decode()
        self.assertTrue(result.startswith('restrict,command="' + provision.FORCED_COMMAND + '" ssh-ed25519 '))
        self.assertNotIn('synthetic-only', result)
        self.assertEqual(result.count('\n'), 1)

    def test_invalid_or_multiple_keys_rejected(self):
        for body in (b'', b'ssh-rsa AAAA', public_key() + public_key(),
                     public_key().replace(b'ssh-ed25519', b'other'), b'ssh-ed25519 !!!',
                     b'ssh-ed25519 ' + base64.b64encode(bytes(51))):
            with self.subTest(body=body[:20]), self.assertRaises((RuntimeError, ValueError)):
                provision.authorized_line(body)

    def test_ssh_template_is_account_bound_and_expected(self):
        body = TEMPLATE.read_bytes()
        provision.validate_template(body)
        for candidate in (body.replace(b'Match User homeenergy-reporter-staging', b'Match all'),
                          body.replace(b'DisableForwarding yes', b'DisableForwarding no'),
                          body + b'PermitRootLogin yes\n', body.replace(b'Match all', b''),
                          body.replace(b'MaxSessions 1', b'MaxSessions 1\n    MaxSessions 1')):
            with self.subTest(body=candidate[-50:]), self.assertRaises(RuntimeError):
                provision.validate_template(candidate)

    def test_expected_server_paths_use_posix_even_when_tests_run_on_windows(self):
        self.assertEqual(provision.EXPECTED_SSH['authorizedkeysfile'],
                         '/etc/ssh/authorized_keys/homeenergy-reporter-staging')
        self.assertIn(Path('/var/lib/homeenergy-reports'), provision.PROTECTED_TREES)

    def test_candidate_check_preserves_include_boundaries_and_order(self):
        original = b'# start\nInclude /etc/ssh/sshd_config.d/*.conf\nUsePAM yes\nSubsystem sftp internal-sftp\n'
        includes = [provision.INCLUDE.parent / '50-cloud-init.conf']
        candidate = provision.candidate_main(original, '/proc/123/fd/4', includes)
        expected = b'Include /proc/123/fd/4 /etc/ssh/sshd_config.d/50-cloud-init.conf'
        self.assertIn(expected, candidate)
        self.assertEqual(candidate.replace(expected, b'Include /etc/ssh/sshd_config.d/*.conf'), original)
        self.assertNotIn(b'Match User', candidate)
        with self.assertRaises(RuntimeError):
            provision.candidate_main(b'UsePAM yes\n', '/proc/123/fd/4', includes)

    def test_effective_policy_rejects_unsafe_inherited_defaults(self):
        base = {**provision.EXPECTED_SSH, 'usepam': 'yes', 'acceptenv': 'LANG LC_*'}
        provision.safe_authentication(base, installed=True)
        for key, value in [('authorizedkeyscommand', '/bin/true'), ('trustedusercakeys', '/etc/ca'),
                           ('permituserenvironment', 'yes'), ('acceptenv', 'LANG PYTHONPATH'),
                           ('usepam', 'no'), ('forcecommand', 'none'), ('permittty', 'yes')]:
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                provision.safe_authentication({**base, key: value}, installed=True)
        # Unknown-user preflight still rejects inherited authentication hooks.
        with self.assertRaises(RuntimeError):
            provision.safe_authentication({**base, 'authorizedkeyscommand': '/bin/true'}, installed=False)

    def test_multiple_acceptenv_lines_are_not_lost(self):
        result = provision.parse_settings('acceptenv LANG\nacceptenv PYTHONPATH\npermittty no\n')
        self.assertEqual(result['acceptenv'], 'LANG PYTHONPATH')

    def test_fixture_generation_is_deterministic_complete_and_synthetic(self):
        now = 1791919201
        first = provision.synthetic_fixtures(now)
        self.assertEqual(first, provision.synthetic_fixtures(now + 10))
        rows = []
        for name, body in first.items():
            document = json.loads(body)
            self.assertEqual(name, document['day'] + '.json')
            self.assertIs(document['synthetic_fixture'], True)
            self.assertLessEqual(len(document['points']), 1500)
            self.assertLess(len(body), 4_000_000)
            rows.extend(document['points'])
        stamps = [row['t'] for row in rows]
        self.assertEqual(len(stamps), 8 * 1440)
        self.assertEqual(stamps, list(range(now // 60 * 60 - 8 * 86400, now // 60 * 60, 60)))
        self.assertTrue(all(set(row['values']) == {'pv', 'grid', 'load', 'battery', 'soc'} for row in rows))

    def test_write_allowlist_excludes_production_and_arbitrary_files(self):
        for target in (provision.KEY, provision.INCLUDE, provision.RECEIPT,
                       provision.MODULES / 'insights_publish.py', provision.HISTORY / '2026-10-13.json'):
            self.assertTrue(provision.allowed_new_file(target))
        for target in (Path('/etc/ssh/sshd_config'), Path('/var/www/homeenergy/public/index.html'),
                       provision.MODULES / 'arbitrary.py', provision.HISTORY / '../outside.json'):
            self.assertFalse(provision.allowed_new_file(target))

    def test_existing_file_refused_before_open(self):
        with patch.object(provision, 'absent', return_value=False), \
                patch.object(provision.os, 'open') as opened, self.assertRaises(RuntimeError):
            provision.create_file(provision.KEY, b'not-a-key')
        opened.assert_not_called()

    def test_rollback_only_removes_exact_owned_key_or_include(self):
        with patch.object(provision, 'absent', return_value=False), \
                patch.object(provision, 'trusted'), \
                patch.object(provision, 'read_file', return_value=b'later change'), \
                patch.object(Path, 'unlink') as unlink:
            with self.assertRaisesRegex(RuntimeError, 'Later change'):
                provision.remove_owned_file(provision.KEY, b'expected')
            unlink.assert_not_called()
        with patch.object(provision, 'absent') as absent, self.assertRaises(RuntimeError):
            provision.remove_owned_file(Path('/etc/ssh/sshd_config'), b'anything')
        absent.assert_not_called()

    def test_rollback_revokes_key_before_expiring_account_and_removing_include(self):
        events = []
        bodies = {'staging-key.pub': public_key(), 'reports-staging-sshd.conf.example': b'policy'}
        with patch.object(provision, 'remove_owned_file', side_effect=lambda path, body: events.append(path)), \
                patch.object(provision, 'run', side_effect=lambda args: events.append(args)):
            provision.rollback(bodies, user_created=True, include_attempted=True, key_attempted=True)
        self.assertEqual(events[0], provision.KEY)
        self.assertEqual(events[1][:2], ['/usr/sbin/usermod', '--expiredate'])
        self.assertEqual(events[2], provision.INCLUDE)
        self.assertEqual(events[-2:], [['/usr/sbin/sshd', '-t'],
                                       ['/usr/bin/systemctl', 'reload', 'ssh.service']])

    def test_rollback_failure_still_expires_account_and_reports_failure(self):
        bodies = {'staging-key.pub': public_key(), 'reports-staging-sshd.conf.example': b'policy'}
        with patch.object(provision, 'remove_owned_file', side_effect=[RuntimeError('changed'), None]), \
                patch.object(provision, 'run') as run, self.assertRaisesRegex(RuntimeError, 'key revocation'):
            provision.rollback(bodies, user_created=True, include_attempted=True, key_attempted=True)
        self.assertEqual(run.call_args_list[0].args[0][0], '/usr/sbin/usermod')
        self.assertIn(call(['/usr/sbin/sshd', '-t']), run.call_args_list)

    def test_failed_rollback_syntax_never_reloads_invalid_config(self):
        bodies = {'staging-key.pub': public_key(), 'reports-staging-sshd.conf.example': b'policy'}
        with patch.object(provision, 'remove_owned_file'), \
                patch.object(provision, 'run', side_effect=RuntimeError('syntax')) as run, \
                self.assertRaisesRegex(RuntimeError, 'SSH syntax check'):
            provision.rollback(bodies, user_created=False, include_attempted=True, key_attempted=False)
        self.assertEqual(run.call_args_list, [call(['/usr/sbin/sshd', '-t'])])

    def test_preflight_is_readonly_and_checks_unknown_username(self):
        result = SimpleNamespace(stdout='')
        with patch.object(provision, 'require_root'), patch.object(provision, 'account_absent'), \
                patch.object(provision, 'verify_dash'), \
                patch.object(provision, 'absent', return_value=True), patch.object(provision, 'trusted'), \
                patch.object(provision, 'read_file', return_value=b'global'), \
                patch.object(provision, 'digest', return_value=provision.BASELINE_SSHD), \
                patch.object(Path, 'exists', return_value=False), patch.object(Path, 'glob', return_value=[]), \
                patch.object(provision, 'run', return_value=result), \
                patch.object(provision, 'effective_settings', return_value={}) as effective, \
                patch.object(provision, 'safe_authentication') as safe, \
                patch.object(provision, 'candidate_policy') as candidate, \
                patch.object(provision, 'protected_snapshot', return_value={}), \
                patch.object(provision, 'services', return_value='active'), \
                patch.object(provision, 'create_file') as create_file, \
                patch.object(provision, 'create_directory') as create_directory:
            provision.preflight({'template': b'synthetic'})
        self.assertEqual(effective.call_args_list, [call(provision.USER), call('shini')])
        safe.assert_called_once_with({}, installed=False)
        candidate.assert_called_once_with({'template': b'synthetic'})
        create_file.assert_not_called()
        create_directory.assert_not_called()

    def test_source_read_is_bounded_regular_only_and_does_not_modify(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            source = root / 'example'
            source.write_bytes(b'fixed synthetic content')
            self.assertEqual(provision.read_file(source), b'fixed synthetic content')
            with self.assertRaises(RuntimeError):
                provision.read_file(source, limit=2)
            with self.assertRaises(RuntimeError):
                provision.read_file(root)
            self.assertEqual(source.read_bytes(), b'fixed synthetic content')

    def test_activation_authorizes_key_only_after_policy_audit_and_reload(self):
        events = []
        manifest = {'files': {name: '0' * 64 for name in provision.SOURCE_FILES}}
        bodies = {name: b'# synthetic' for name in provision.SOURCE_FILES}
        bodies['staging-key.pub'] = public_key()
        account = SimpleNamespace(pw_uid=1234, pw_gid=1234)
        fake_pwd = SimpleNamespace(getpwnam=Mock(return_value=account))
        with patch.object(provision, 'preflight', return_value={}), \
                patch.object(provision.os, 'umask'), patch.object(provision.os, 'chown', create=True), \
                patch.object(provision.os, 'chmod'), \
                patch.object(provision.tempfile, 'mkdtemp', return_value='/home/shini/homeenergy-staging-backup.synthetic'), \
                patch.object(Path, 'write_text'), patch.object(Path, 'write_bytes'), \
                patch.object(Path, 'exists', return_value=False), \
                patch.object(provision, 'read_file', return_value=b'global'), \
                patch.object(provision, 'account_absent'), patch.object(provision, 'verify_invariants'), \
                patch.dict(provision.sys.modules, {'pwd': fake_pwd}), \
                patch.object(provision, 'run', side_effect=lambda args: (events.append(args) or SimpleNamespace(stdout='active\n'))), \
                patch.object(provision, 'create_directory'), \
                patch.object(provision, 'create_file', side_effect=lambda path, *args: events.append(path)), \
                patch.object(provision, 'synthetic_fixtures', return_value={'2026-10-13.json': b'synthetic'}), \
                patch.object(provision, 'audit', side_effect=lambda *args, **kw: events.append(('audit', kw['key_present']))), \
                patch.object(provision, 'verify'), patch.object(provision, 'rollback') as rollback:
            provision.activate(manifest, bodies)
        include = events.index(provision.INCLUDE)
        prekey_audit = events.index(('audit', False))
        reload = events.index(['/usr/bin/systemctl', 'reload', 'ssh.service'])
        key = events.index(provision.KEY)
        self.assertLess(include, prekey_audit)
        self.assertLess(prekey_audit, reload)
        self.assertLess(reload, key)
        self.assertLess(key, events.index(('audit', True)))
        rollback.assert_not_called()


if __name__ == '__main__':
    unittest.main()
