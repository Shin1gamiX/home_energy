"""Recovery regression tests: synthetic files and mocks, never root or SSH."""
import importlib.util
import json
from pathlib import Path
import stat
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch


HELPER = Path(__file__).resolve().parents[1] / 'deploy/provision-report-staging.py'
SPEC = importlib.util.spec_from_file_location('staging_recovery', HELPER)
provision = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(provision)

UID = 999
GID = 987


def account():
    return SimpleNamespace(pw_uid=UID, pw_gid=GID, pw_dir=str(provision.HOME),
                           pw_shell='/bin/dash')


def command_result(stdout='', stderr='', returncode=0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


def probe_response(allowed=False, **changes):
    result = {'uid': UID, 'euid': UID, 'gid': GID, 'egid': GID,
              'groups': [GID], 'allowed': allowed}
    result.update(changes)
    return json.dumps(result)


def recovery_plan():
    return {'backup': '/home/shini/homeenergy-staging-backup.synthetic',
            'prior_release': '/home/shini/homeenergy-staging-release.synthetic',
            'prior_manifest_sha256': '1' * 64, 'uid': UID, 'gid': GID}


def recovery_manifest():
    return {'manifest_version': 2, 'recovery': recovery_plan(),
            'files': {name: '2' * 64 for name in provision.SOURCE_FILES},
            'connection': provision.CONNECTION, 'baseline_sshd': provision.BASELINE_SSHD}


class SudoDenialTests(unittest.TestCase):
    def test_confirmed_successful_listing_with_exact_denial_is_accepted(self):
        message = 'User ' + provision.USER + ' is not allowed to run sudo on Shini-server.\n'
        with patch.object(provision, 'run', return_value=command_result(message)) as run:
            provision.assert_no_sudo()
        self.assertEqual(run.call_args.args[0],
                         ['/usr/bin/sudo', '-n', '-l', '-U', provision.USER])
        self.assertFalse(run.call_args.kwargs.get('check', True))

    def test_only_conclusive_exact_denial_counts(self):
        message = 'User ' + provision.USER + ' is not allowed to run sudo on Shini-server.\n'
        cases = [command_result(message, returncode=1),
                 command_result(message, returncode=2),
                 command_result(message, stderr='sudo: a password is required\n'),
                 command_result('', stderr=message),
                 command_result(message.replace(provision.USER, 'another-user')),
                 command_result(message + '    (ALL) ALL\n'),
                 command_result('User ' + provision.USER + ' may run sudo on Shini-server.\n'),
                 command_result(''), command_result('sudo: account validation failure\n')]
        for result in cases:
            with self.subTest(result=result), patch.object(provision, 'run', return_value=result), \
                    self.assertRaises(RuntimeError):
                provision.assert_no_sudo()


class AccountAuditTests(unittest.TestCase):
    def invoke(self, *, expired=False, expiry=None, password_state='L',
               selected_account=None, groups=None):
        selected_account = selected_account or account()
        groups = [GID] if groups is None else groups
        expiry = ('Jan 02, 1970' if expired else 'never') if expiry is None else expiry
        fake_pwd = SimpleNamespace(getpwnam=Mock(return_value=selected_account))
        fake_grp = SimpleNamespace(getgrgid=Mock(return_value=SimpleNamespace(
            gr_name=provision.USER, gr_gid=GID, gr_mem=[])))

        def run(args, **kwargs):
            if args == ['/usr/bin/passwd', '-S', provision.USER]:
                return command_result(provision.USER + ' ' + password_state + ' 2026-10-13 -1 -1 -1 -1\n')
            if args == ['/usr/bin/chage', '-l', provision.USER]:
                return command_result('Last password change : Oct 13, 2026\n'
                                      'Password expires : never\n'
                                      'Password inactive : never\n'
                                      'Account expires : ' + expiry + '\n'
                                      'Minimum number of days between password change : -1\n'
                                      'Maximum number of days between password change : -1\n'
                                      'Number of days of warning before password expires : -1\n')
            raise AssertionError('Unexpected account-audit command: ' + repr(args))

        with patch.dict(provision.sys.modules, {'pwd': fake_pwd, 'grp': fake_grp}), \
                patch.object(provision.os, 'getgrouplist', return_value=groups, create=True), \
                patch.object(provision, 'run', side_effect=run):
            return provision.audit_account(expired=expired)

    def test_locked_retained_expired_account_and_locked_active_account_are_distinct(self):
        for expired in (True, False):
            with self.subTest(expired=expired):
                self.assertEqual(self.invoke(expired=expired).pw_uid, UID)

    def test_expired_state_must_match_the_requested_audit(self):
        for expired, expiry in ((True, 'never'), (False, 'Jan 02, 1970'),
                                (True, 'Jan 03, 1970'), (False, ''),
                                (False, 'never\nAccount expires : Jan 02, 1970')):
            with self.subTest(expired=expired, expiry=expiry), self.assertRaises(RuntimeError):
                self.invoke(expired=expired, expiry=expiry)

    def test_password_must_remain_locked(self):
        for state in ('P', 'NP', '', 'LK'):
            with self.subTest(state=state), self.assertRaises(RuntimeError):
                self.invoke(password_state=state)

    def test_supplementary_groups_are_rejected(self):
        for groups in ([GID, 27], [GID, 0], [], [0]):
            with self.subTest(groups=groups), self.assertRaises(RuntimeError):
                self.invoke(groups=groups)

    def test_root_identity_wrong_home_and_wrong_shell_are_rejected(self):
        for key, value in (('pw_uid', 0), ('pw_dir', '/home/shini'), ('pw_shell', '/bin/bash')):
            candidate = account()
            setattr(candidate, key, value)
            with self.subTest(field=key), self.assertRaises(RuntimeError):
                self.invoke(selected_account=candidate)


class EffectiveAccessProbeTests(unittest.TestCase):
    def invoke(self, result, *, flag='-w', path=None):
        path = path or Path('/var/lib/homeenergy-reports')
        fake_pwd = SimpleNamespace(getpwnam=Mock(return_value=account()))
        with patch.dict(provision.sys.modules, {'pwd': fake_pwd}), \
                patch.object(provision.os, 'getgrouplist', return_value=[GID], create=True), \
                patch.object(Path, 'is_absolute', return_value=True), \
                patch.object(Path, 'exists', return_value=True), \
                patch.object(provision.subprocess, 'run', return_value=result) as run:
            allowed = provision.permitted(provision.USER, flag, path)
        return allowed, run.call_args

    def test_positive_and_negative_access_are_successful_identity_checked_probes(self):
        for expected in (True, False):
            with self.subTest(allowed=expected):
                allowed, invocation = self.invoke(command_result(probe_response(expected)))
                self.assertIs(allowed, expected)
                args, kwargs = invocation.args[0], invocation.kwargs
                self.assertEqual(args[:4], ['/usr/bin/python3', '-I', '-B', '-c'])
                self.assertEqual(args[4], provision.ACCESS_PROBE)
                self.assertEqual(args[5:], ['-w', str(Path('/var/lib/homeenergy-reports'))])
                self.assertEqual(kwargs['user'], UID)
                self.assertEqual(kwargs['group'], GID)
                self.assertEqual(kwargs['extra_groups'], [GID])
                self.assertEqual(kwargs['env'], provision.ENVIRONMENT)
                self.assertFalse(kwargs.get('shell', False))

    def test_process_failures_and_stderr_never_masquerade_as_denied_access(self):
        for result in (command_result(probe_response(False), returncode=1),
                       command_result(probe_response(False), returncode=2),
                       command_result('', 'runuser: account expired\n', returncode=1),
                       command_result(probe_response(False), 'unexpected diagnostic\n')):
            with self.subTest(result=result), self.assertRaises(RuntimeError):
                self.invoke(result)

    def test_identity_drift_and_non_boolean_results_are_rejected(self):
        for changes in ({'uid': 0}, {'euid': 0}, {'gid': 0}, {'egid': 0},
                        {'groups': [GID, 27]}, {'groups': []},
                        {'allowed': 0}, {'allowed': 'false'}, {'uid': '999'},
                        {'uid': 999.0}, {'groups': ['987']}, {'groups': [987.0]},
                        {'unexpected': 'field'}):
            with self.subTest(changes=changes), self.assertRaises(RuntimeError):
                self.invoke(command_result(probe_response(**changes)))

    def test_malformed_or_ambiguous_probe_output_is_rejected(self):
        duplicate = probe_response().replace('"allowed": false',
                                             '"allowed": false, "allowed": true')
        for output in ('', 'false', '{}', '[]', 'null', duplicate,
                       probe_response() + '\n' + probe_response(),
                       probe_response().replace('"allowed": false', '"allowed": NaN')):
            with self.subTest(output=output), self.assertRaises((RuntimeError, ValueError)):
                self.invoke(command_result(output))

    def test_unsupported_test_operator_is_rejected_before_launch(self):
        fake_pwd = SimpleNamespace(getpwnam=Mock(return_value=account()))
        with patch.dict(provision.sys.modules, {'pwd': fake_pwd}), \
                patch.object(provision.os, 'getgrouplist', return_value=[GID], create=True), \
                patch.object(Path, 'exists', return_value=True), \
                patch.object(provision.subprocess, 'run') as run, \
                self.assertRaises(RuntimeError):
            provision.permitted(provision.USER, '-e', Path('/var/lib/homeenergy-reports'))
        run.assert_not_called()

    def test_launch_error_and_timeout_propagate_instead_of_claiming_denial(self):
        fake_pwd = SimpleNamespace(getpwnam=Mock(return_value=account()))
        for error in (OSError('synthetic launch failure'),
                      subprocess.TimeoutExpired('synthetic-probe', 10)):
            with self.subTest(error=type(error).__name__), \
                    patch.dict(provision.sys.modules, {'pwd': fake_pwd}), \
                    patch.object(provision.os, 'getgrouplist', return_value=[GID], create=True), \
                    patch.object(Path, 'is_absolute', return_value=True), \
                    patch.object(Path, 'exists', return_value=True), \
                    patch.object(provision.subprocess, 'run', side_effect=error), \
                    self.assertRaises((OSError, subprocess.TimeoutExpired, RuntimeError)):
                provision.permitted(provision.USER, '-w', Path('/var/lib/homeenergy-reports'))

    def test_root_probe_identity_is_rejected_before_launch(self):
        candidate = account()
        candidate.pw_uid = 0
        fake_pwd = SimpleNamespace(getpwnam=Mock(return_value=candidate))
        with patch.dict(provision.sys.modules, {'pwd': fake_pwd}), \
                patch.object(Path, 'is_absolute', return_value=True), \
                patch.object(Path, 'exists', return_value=True), \
                patch.object(provision.subprocess, 'run') as run, self.assertRaises(RuntimeError):
            provision.permitted(provision.USER, '-w', Path('/var/lib/homeenergy-reports'))
        run.assert_not_called()

    def test_missing_target_is_not_reported_as_permission_denial(self):
        fake_pwd = SimpleNamespace(getpwnam=Mock(return_value=account()))
        with patch.dict(provision.sys.modules, {'pwd': fake_pwd}), \
                patch.object(Path, 'is_absolute', return_value=True), \
                patch.object(Path, 'exists', return_value=False), \
                patch.object(provision.subprocess, 'run') as run, self.assertRaises(RuntimeError):
            provision.permitted(provision.USER, '-w', Path('/nonexistent-protected-target'))
        run.assert_not_called()


class RetainedFixtureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.bodies = provision.synthetic_fixtures(1791919201)
        for name, body in self.bodies.items():
            (self.root / name).write_bytes(body)
        self.history_patch = patch.object(provision, 'HISTORY', self.root)
        self.history_patch.start()
        self.addCleanup(self.history_patch.stop)
        self.trusted_patch = patch.object(provision, 'trusted')
        self.trusted_patch.start()
        self.addCleanup(self.trusted_patch.stop)

    def check(self):
        return provision.check_recovery_fixtures()

    def mutate_document(self, mutator):
        name = sorted(self.bodies)[-1]
        path = self.root / name
        document = json.loads(path.read_bytes())
        mutator(document)
        path.write_bytes(json.dumps(document, separators=(',', ':')).encode())

    def test_exact_eight_day_generated_fixture_is_accepted_without_changes(self):
        expected = {name: provision.digest(body) for name, body in self.bodies.items()}
        self.assertEqual(self.check(), expected)
        self.assertEqual({path.name: path.read_bytes() for path in self.root.iterdir()},
                         self.bodies)

    def test_one_modified_measurement_is_rejected(self):
        self.mutate_document(lambda document: document['points'][0]['values'].__setitem__('pv', 99999))
        with self.assertRaises(RuntimeError):
            self.check()

    def test_missing_minute_is_rejected(self):
        self.mutate_document(lambda document: document['points'].pop(0))
        with self.assertRaises(RuntimeError):
            self.check()

    def test_missing_day_is_rejected(self):
        (self.root / sorted(self.bodies)[0]).unlink()
        with self.assertRaises(RuntimeError):
            self.check()

    def test_unexpected_non_fixture_file_is_rejected(self):
        (self.root / 'unexpected.txt').write_text('synthetic extra file', encoding='utf-8')
        with self.assertRaises(RuntimeError):
            self.check()

    def test_changed_provenance_label_is_rejected(self):
        self.mutate_document(lambda document: document.__setitem__('synthetic_fixture', False))
        with self.assertRaises(RuntimeError):
            self.check()

    def test_extra_metadata_even_with_unchanged_points_is_rejected(self):
        self.mutate_document(lambda document: document.__setitem__('unexpected', 'field'))
        with self.assertRaises(RuntimeError):
            self.check()

    def test_malformed_json_is_rejected(self):
        (self.root / sorted(self.bodies)[-1]).write_bytes(b'not json')
        with self.assertRaises((RuntimeError, ValueError)):
            self.check()


class RecoveryPlanTests(unittest.TestCase):
    def test_exact_reviewed_paths_system_identity_and_manifest_pin_are_required(self):
        provision.validate_recovery_plan(recovery_plan())
        cases = ({'backup': '/tmp/homeenergy-staging-backup.synthetic'},
                 {'backup': '/home/shini/../root/homeenergy-staging-backup.synthetic'},
                 {'prior_release': '/home/shini/arbitrary-release'},
                 {'prior_manifest_sha256': 'A' * 64}, {'prior_manifest_sha256': 'short'},
                 {'uid': 0}, {'gid': 0}, {'uid': 1000}, {'gid': True},
                 {'uid': '999'}, {'unexpected': 'field'})
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(RuntimeError):
                provision.validate_recovery_plan({**recovery_plan(), **changes})
        with self.assertRaises(RuntimeError):
            provision.validate_recovery_plan(None)


class RecoveryEvidenceTests(unittest.TestCase):
    def invoke(self, *, changed_source=None, changed_metadata=None, pin_changed=False,
               extra_file=False, evidence_changes=None):
        manifest = recovery_manifest()
        plan = manifest['recovery']
        backup = Path(plan['backup'])
        prior_release = Path(plan['prior_release'])
        prior_bytes = b'synthetic original manifest bytes'
        plan['prior_manifest_sha256'] = provision.digest(prior_bytes)
        previous = {key: value for key, value in manifest.items() if key != 'recovery'}
        previous['manifest_version'] = 1
        previous['files'] = dict(manifest['files'])
        previous['files']['provision-report-staging.py'] = '3' * 64
        before = {'protected': {}, 'services': 'synthetic state', 'administrator_ssh': {}}
        evidence = {'manifest': previous, 'before': before, 'fixtures': {}, 'backup': str(backup)}
        if evidence_changes:
            evidence.update(evidence_changes)
        if changed_source:
            manifest['files'][changed_source] = '4' * 64
        saved_ssh = b'synthetic saved SSH configuration'
        file_bodies = {backup / 'preflight.json': json.dumps(evidence).encode(),
                       backup / 'sshd_config': saved_ssh,
                       prior_release / 'manifest.json': prior_bytes + (b'changed' if pin_changed else b'')}

        def metadata(path):
            if path == backup.parent:
                result = SimpleNamespace(st_uid=1000, st_gid=1000, st_mode=stat.S_IFDIR | 0o750)
            elif path == backup:
                result = SimpleNamespace(st_uid=0, st_gid=0, st_mode=stat.S_IFDIR | 0o700)
            else:
                result = SimpleNamespace(st_uid=0, st_gid=0, st_mode=stat.S_IFREG | 0o600)
            if changed_metadata and path.name == changed_metadata[0]:
                setattr(result, changed_metadata[1], changed_metadata[2])
            return result

        entries = [backup / 'preflight.json', backup / 'sshd_config']
        if extra_file:
            entries.append(backup / 'unexpected')
        fake_pwd = SimpleNamespace(getpwnam=Mock(return_value=SimpleNamespace(pw_uid=1000)))
        with patch.dict(provision.sys.modules, {'pwd': fake_pwd}), \
                patch.object(provision, 'canonical'), patch.object(provision, 'trusted'), \
                patch.object(Path, 'lstat', metadata), patch.object(Path, 'iterdir', return_value=iter(entries)), \
                patch.object(provision, 'read_file', side_effect=lambda path, *args: file_bodies[path]), \
                patch.object(provision, 'read_bundle', return_value=(previous, {})), \
                patch.object(provision, 'BASELINE_SSHD', provision.digest(saved_ssh)), \
                patch.object(provision, 'verify_invariants') as verify:
            actual = provision.recovery_evidence(manifest)
        self.assertEqual(actual, before)
        verify.assert_called_once_with(before)

    def test_original_root_evidence_allows_only_helper_to_change(self):
        self.invoke()

    def test_changed_code_key_or_template_is_rejected(self):
        for name in provision.SOURCE_FILES:
            if name == 'provision-report-staging.py':
                continue
            with self.subTest(name=name), self.assertRaises(RuntimeError):
                self.invoke(changed_source=name)

    def test_original_manifest_pin_mismatch_is_rejected(self):
        with self.assertRaises(RuntimeError):
            self.invoke(pin_changed=True)

    def test_backup_ownership_modes_and_exact_contents_are_required(self):
        cases = (('homeenergy-staging-backup.synthetic', 'st_uid', 1000),
                 ('homeenergy-staging-backup.synthetic', 'st_gid', 1000),
                 ('homeenergy-staging-backup.synthetic', 'st_mode', stat.S_IFDIR | 0o755),
                 ('preflight.json', 'st_uid', 1000),
                 ('preflight.json', 'st_mode', stat.S_IFREG | 0o644),
                 ('sshd_config', 'st_gid', 1000),
                 ('shini', 'st_mode', stat.S_IFDIR | 0o777))
        for changed_metadata in cases:
            with self.subTest(changed_metadata=changed_metadata), self.assertRaises(RuntimeError):
                self.invoke(changed_metadata=changed_metadata)
        with self.assertRaises(RuntimeError):
            self.invoke(extra_file=True)

    def test_mismatched_original_receipt_is_rejected(self):
        for changes in ({'fixtures': {'invented.json': '0' * 64}},
                        {'backup': '/home/shini/another-backup'}, {'unexpected': 'field'},
                        {'before': {'protected': {}}}):
            with self.subTest(changes=changes), self.assertRaises(RuntimeError):
                self.invoke(evidence_changes=changes)


class RecoveryPreflightTests(unittest.TestCase):
    def invoke(self, *, existing=None, account_change=None, unexpected=None, process_result=None):
        manifest = recovery_manifest()
        before = {'protected': {}, 'services': 'synthetic', 'administrator_ssh': {}}
        bodies = {'template': b'synthetic'}
        selected = account()
        if account_change:
            setattr(selected, *account_change)
        entries = {provision.HOME: [],
                   provision.MODULES: [provision.MODULES / name for name in provision.MODULE_NAMES],
                   provision.FIXTURES: [provision.HISTORY],
                   provision.STATE: [provision.STATE / 'public'],
                   provision.STATE / 'public': []}
        if unexpected:
            entries[unexpected].append(unexpected / 'unexpected')

        def execute(args, **kwargs):
            if args == ['/usr/bin/pgrep', '-u', str(UID)]:
                return process_result or command_result(returncode=1)
            if args == ['/usr/sbin/sshd', '-t']:
                return command_result()
            raise AssertionError('Unexpected preflight command: ' + repr(args))

        with patch.object(provision, 'require_root'), \
                patch.object(provision, 'recovery_evidence', return_value=before), \
                patch.object(provision, 'absent', side_effect=lambda path: path != existing), \
                patch.object(provision, 'audit_account', return_value=selected) as audit_account, \
                patch.object(provision, 'trusted'), \
                patch.object(Path, 'iterdir', lambda path: iter(entries[path])), \
                patch.object(provision, 'check_recovery_fixtures', return_value={'day.json': 'hash'}), \
                patch.object(provision, 'run', side_effect=execute) as run, \
                patch.object(provision, 'candidate_policy') as candidate, \
                patch.object(provision, 'audit') as audit, \
                patch.object(provision, 'verify_invariants') as verify, \
                patch.object(provision, 'create_file') as create, \
                patch.object(provision, 'create_directory') as mkdir:
            result = provision.recovery_preflight(manifest, bodies)
        audit_account.assert_called_once_with(expired=True)
        audit.assert_called_once_with(manifest, bodies, key_present=False,
                                      expired=True, policy_installed=False)
        candidate.assert_called_once_with(bodies)
        verify.assert_called_once_with(before)
        self.assertEqual(run.call_args_list,
                         [call(['/usr/bin/pgrep', '-u', str(UID)], check=False),
                          call(['/usr/sbin/sshd', '-t'])])
        create.assert_not_called()
        mkdir.assert_not_called()
        self.assertEqual(result, {'manifest': manifest, 'before': before,
                                 'fixtures': {'day.json': 'hash'},
                                 'backup': manifest['recovery']['backup']})

    def test_recovery_preflight_is_readonly_and_account_stays_expired(self):
        self.invoke()

    def test_existing_key_include_or_success_receipt_blocks_recovery(self):
        for path in (provision.KEY, provision.INCLUDE, provision.RECEIPT):
            with self.subTest(path=path), self.assertRaises(RuntimeError):
                self.invoke(existing=path)

    def test_retained_uid_and_gid_must_match_reviewed_plan(self):
        for change in (('pw_uid', UID - 1), ('pw_gid', GID - 1)):
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                self.invoke(account_change=change)

    def test_running_identity_or_failed_process_probe_blocks_recovery(self):
        for result in (command_result('1234\n'), command_result(returncode=2),
                       command_result(stderr='synthetic probe error', returncode=1)):
            with self.subTest(result=result), self.assertRaises(RuntimeError):
                self.invoke(process_result=result)

    def test_non_pristine_retained_paths_are_rejected(self):
        for path in (provision.HOME, provision.MODULES, provision.FIXTURES,
                     provision.STATE, provision.STATE / 'public'):
            with self.subTest(path=path), self.assertRaises(RuntimeError):
                self.invoke(unexpected=path)


class RecoveryOrderingTests(unittest.TestCase):
    def invoke(self, failure=None):
        events = []
        manifest = recovery_manifest()
        bodies = {'reports-staging-sshd.conf.example': b'policy', 'staging-key.pub': b'public'}
        receipt = {'manifest': manifest, 'before': {}, 'fixtures': {}, 'backup': 'synthetic'}

        def event(label, result=None):
            events.append(label)
            if label == failure:
                raise RuntimeError('synthetic failure')
            return result

        def run(args, **kwargs):
            return event(tuple(args), command_result('active\n'))

        with patch.object(provision, 'recovery_preflight', side_effect=lambda *args: event('preflight', receipt)), \
                patch.object(provision.os, 'umask'), \
                patch.object(provision, 'create_file', side_effect=lambda path, *args: event(path)), \
                patch.object(provision, 'run', side_effect=run), \
                patch.object(provision, 'audit', side_effect=lambda *args, **kwargs: event(('audit', kwargs))), \
                patch.object(provision, 'audit_account', side_effect=lambda **kwargs: event(('account', kwargs))), \
                patch.object(provision, 'verify_invariants'), \
                patch.object(provision, 'check_recovery_fixtures', side_effect=lambda: event('fixtures', {})), \
                patch.object(provision, 'authorized_line', return_value=b'fixed authorized line'), \
                patch.object(provision, 'verify', side_effect=lambda *args: event('verify')), \
                patch.object(provision, 'rollback') as rollback, patch('builtins.print'):
            if failure is None:
                provision.recover(manifest, bodies)
            else:
                with self.assertRaisesRegex(RuntimeError, 'synthetic failure'):
                    provision.recover(manifest, bodies)
        return events, rollback

    def test_policy_audit_reload_and_account_checks_precede_key_authorization(self):
        events, rollback = self.invoke()
        ordered = ['preflight', provision.INCLUDE,
                   ('audit', {'key_present': False, 'expired': True}),
                   ('/usr/bin/systemctl', 'reload', 'ssh.service'),
                   ('/usr/sbin/usermod', '--expiredate', '-1', provision.USER),
                   ('account', {'expired': False}), 'fixtures', provision.KEY,
                   ('audit', {'key_present': True}), provision.RECEIPT, 'verify']
        indices = [events.index(item) for item in ordered]
        self.assertEqual(indices, sorted(indices))
        rollback.assert_not_called()

    def test_preflight_failure_never_mutates_or_runs_rollback(self):
        events, rollback = self.invoke(failure='preflight')
        self.assertEqual(events, ['preflight'])
        rollback.assert_not_called()

    def test_every_mutating_stage_failure_has_appropriate_rollback_flags(self):
        cases = [(provision.INCLUDE, False, False),
                 (('/usr/bin/systemctl', 'reload', 'ssh.service'), False, False),
                 (('/usr/sbin/usermod', '--expiredate', '-1', provision.USER), True, False),
                 ('fixtures', True, False), (provision.KEY, True, True),
                 (provision.RECEIPT, True, True), ('verify', True, True)]
        for failure, expiry_changed, key_attempted in cases:
            with self.subTest(failure=failure):
                events, rollback = self.invoke(failure=failure)
                self.assertEqual(rollback.call_count, 1)
                self.assertEqual(rollback.call_args.kwargs,
                                 {'user_created': expiry_changed, 'include_attempted': True,
                                  'key_attempted': key_attempted})
                if not key_attempted:
                    self.assertNotIn(provision.KEY, events)


if __name__ == '__main__':
    unittest.main()
