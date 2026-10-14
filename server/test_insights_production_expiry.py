"""Local-only regression checks for the one-account expiry recovery helper."""
from collections import namedtuple
from contextlib import ExitStack, redirect_stdout
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


SOURCE = Path(__file__).resolve().parents[1] / 'deploy/recover-report-expiry.py'
SPEC = importlib.util.spec_from_file_location('report_expiry_recovery', SOURCE)
recovery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(recovery)
Shadow = namedtuple('Shadow', 'sp_namp sp_pwdp sp_lstchg sp_min sp_max sp_warn sp_inact sp_expire sp_flag')


def account(**changes):
    fields = {'pw_name': recovery.USER, 'pw_uid': recovery.UID, 'pw_gid': recovery.GID,
              'pw_dir': '/var/lib/homeenergy-reporter-home', 'pw_shell': '/bin/dash',
              'pw_passwd': 'x', 'pw_gecos': ''}
    return SimpleNamespace(**dict(fields, **changes))


def shadow(**changes):
    original = Shadow(recovery.USER, '!SYNTHETIC-DO-NOT-PRINT', 20000, -1, -1, -1, -1, -1, 0)
    return original._replace(**changes)


class IdentityTests(unittest.TestCase):
    def inspect_identity(self, user=None, group_name=None, groups=None):
        modules = {'pwd': SimpleNamespace(getpwnam=Mock(return_value=user or account())),
                   'grp': SimpleNamespace(getgrgid=Mock(return_value=SimpleNamespace(
                       gr_name=group_name or recovery.USER)))}
        with patch.dict('sys.modules', modules), patch.object(
                recovery.os, 'getgrouplist', create=True,
                return_value=[recovery.GID] if groups is None else groups):
            return recovery.identity()

    def test_exact_existing_identity_required(self):
        self.assertEqual(self.inspect_identity(), account())
        changes = [{'pw_name': 'another-user'}, {'pw_uid': 0}, {'pw_uid': 997},
                   {'pw_gid': 987}, {'pw_dir': '/tmp'}, {'pw_shell': '/bin/bash'}]
        for update in changes:
            with self.subTest(update=update), self.assertRaisesRegex(RuntimeError, 'identity changed'):
                self.inspect_identity(account(**update))

    def test_only_the_existing_primary_group_is_allowed(self):
        for kwargs in [{'group_name': 'other'}, {'groups': []},
                       {'groups': [recovery.GID, 27]}, {'groups': [27]}]:
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(RuntimeError, 'groups changed'):
                self.inspect_identity(**kwargs)

    def test_locked_shadow_and_only_reviewed_expiry_states_are_allowed(self):
        for expiry in (-1, 1):
            value = shadow(sp_expire=expiry)
            with patch.dict('sys.modules', {'spwd': SimpleNamespace(getspnam=Mock(return_value=value))}):
                self.assertEqual(recovery.shadow_state(), value)
        changes = [{'sp_namp': 'other'}, {'sp_pwdp': 'SYNTHETIC-HASH'},
                   {'sp_pwdp': '*'}, {'sp_pwdp': ''}, {'sp_inact': 0},
                   {'sp_expire': 0}, {'sp_expire': 20000}]
        for update in changes:
            with self.subTest(update=update), patch.dict('sys.modules', {
                    'spwd': SimpleNamespace(getspnam=Mock(return_value=shadow(**update)))}), \
                    self.assertRaisesRegex(RuntimeError, 'locked expiry-recovery state'):
                recovery.shadow_state()


class RecoveryFlowTests(unittest.TestCase):
    def setUp(self):
        self.identity = account()
        self.before = shadow()
        self.after = shadow(sp_expire=1)
        self.manifest = {'synthetic': 'manifest'}
        self.bodies = {'synthetic': b'body'}
        self.baseline = {'synthetic': 'baseline'}
        self.checks = {'require_root': Mock(), 'run': Mock(), 'verify_disabled': Mock(),
                       'absent': Mock(return_value=True), 'RECEIPT': Path('/synthetic/no-receipt')}
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.identity_mock = self.stack.enter_context(patch.object(recovery, 'identity', return_value=self.identity))
        self.shadow_mock = self.stack.enter_context(patch.object(
            recovery, 'shadow_state', side_effect=[self.before, self.after, self.after]))
        self.retained_mock = self.stack.enter_context(patch.object(
            recovery, 'retained_state', return_value=(self.manifest, self.bodies, self.baseline)))
        self.evidence_mock = self.stack.enter_context(patch.object(
            recovery, 'evidence_digest', return_value={'baseline.json': 'a' * 64, 'identity.json': 'b' * 64}))
        self.output = self.stack.enter_context(redirect_stdout(io.StringIO()))

    def test_read_only_preflight_does_not_mutate_or_run_partial_verification(self):
        recovery.recover(self.checks, apply=False)
        self.checks['require_root'].assert_called_once_with()
        self.checks['run'].assert_not_called()
        self.checks['verify_disabled'].assert_not_called()
        self.shadow_mock.assert_called_once_with()
        self.retained_mock.assert_called_once_with(self.checks)
        self.assertIn('No changes made', self.output.getvalue())
        self.assertNotIn(self.before.sp_pwdp, self.output.getvalue())

    def test_only_fixed_chage_command_and_original_partial_verifier_are_used(self):
        recovery.recover(self.checks, apply=True)
        self.checks['run'].assert_called_once_with(
            ['/usr/bin/chage', '--expiredate', '1', 'homeenergy-reporter'])
        self.checks['verify_disabled'].assert_called_once_with(
            self.manifest, self.bodies, before=self.baseline, receipt_required=False)
        self.checks['absent'].assert_called_once_with(self.checks['RECEIPT'])
        self.assertNotIn(self.before.sp_pwdp, self.output.getvalue())
        self.assertIn('Public publishing remains disabled', self.output.getvalue())

    def test_already_expired_account_is_verified_without_chage(self):
        self.shadow_mock.side_effect = [self.after, self.after, self.after]
        recovery.recover(self.checks, apply=True)
        self.checks['run'].assert_not_called()
        self.checks['verify_disabled'].assert_called_once()

    def test_preflight_failure_never_runs_chage(self):
        self.retained_mock.side_effect = RuntimeError('Protected state drift')
        with self.assertRaisesRegex(RuntimeError, 'Protected state drift'):
            recovery.recover(self.checks, apply=True)
        self.checks['run'].assert_not_called()
        self.checks['verify_disabled'].assert_not_called()

    def test_failed_chage_never_runs_verifier_or_another_command(self):
        self.checks['run'].side_effect = RuntimeError('Required local check failed')
        with self.assertRaisesRegex(RuntimeError, 'Required local check failed'):
            recovery.recover(self.checks, apply=True)
        self.checks['run'].assert_called_once()
        self.checks['verify_disabled'].assert_not_called()

    def test_ineffective_expiry_change_does_not_run_verifier(self):
        self.shadow_mock.side_effect = [self.before, self.before]
        with self.assertRaisesRegex(RuntimeError, 'did not take effect'):
            recovery.recover(self.checks, apply=True)
        self.checks['verify_disabled'].assert_not_called()

    def test_every_other_shadow_field_must_remain_unchanged(self):
        for name in recovery.SHADOW_UNCHANGED:
            original = getattr(self.after, name)
            changed = original + ('-changed' if isinstance(original, str) else 1)
            self.shadow_mock.side_effect = [self.before, self.after._replace(**{name: changed})]
            self.checks['verify_disabled'].reset_mock()
            with self.subTest(field=name), self.assertRaisesRegex(RuntimeError, 'unrelated account field changed'):
                recovery.recover(self.checks, apply=True)
            self.checks['verify_disabled'].assert_not_called()
        self.assertNotIn(self.before.sp_pwdp, self.output.getvalue())

    def test_identity_drift_after_chage_stops_before_verifier(self):
        self.identity_mock.side_effect = [self.identity, account(pw_gecos='changed')]
        with self.assertRaisesRegex(RuntimeError, 'unrelated account field changed'):
            recovery.recover(self.checks, apply=True)
        self.checks['verify_disabled'].assert_not_called()

    def test_evidence_drift_stops_before_verifier(self):
        self.evidence_mock.side_effect = [{'baseline': 'original'}, {'baseline': 'changed'}]
        with self.assertRaisesRegex(RuntimeError, 'Original evidence changed'):
            recovery.recover(self.checks, apply=True)
        self.checks['verify_disabled'].assert_not_called()

    def test_partial_verifier_failure_does_not_attempt_rollback_or_receipt(self):
        self.checks['verify_disabled'].side_effect = RuntimeError('A protected service changed')
        with self.assertRaisesRegex(RuntimeError, 'A protected service changed'):
            recovery.recover(self.checks, apply=True)
        self.checks['run'].assert_called_once_with(recovery.CHAGE)
        self.checks['absent'].assert_not_called()
        self.assertNotIn('verification passed', self.output.getvalue())

    def test_post_verification_account_drift_is_detected(self):
        self.shadow_mock.side_effect = [self.before, self.after, self.after._replace(sp_max=90)]
        with self.assertRaisesRegex(RuntimeError, 'Account or evidence changed during verification'):
            recovery.recover(self.checks, apply=True)
        self.checks['run'].assert_called_once()

    def test_unexpected_receipt_is_not_accepted(self):
        self.checks['absent'].return_value = False
        with self.assertRaisesRegex(RuntimeError, 'Unexpected completion receipt'):
            recovery.recover(self.checks, apply=True)
        self.checks['run'].assert_called_once()


class RetainedStateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.backup = self.root / 'backup'
        self.modules = self.root / 'modules'
        self.state = self.root / 'state'
        self.home = self.root / 'home'
        for path in (self.backup, self.modules, self.state / 'public', self.home):
            path.mkdir(parents=True)
        self.before = {'files': {'synthetic': 'unchanged'}, 'services': {'synthetic': 'unchanged'}}
        identity = {'schema_version': 1, 'user': recovery.USER, 'uid': recovery.UID, 'gid': recovery.GID}
        for name, value in [('baseline.json', self.before), ('identity.json', identity)]:
            (self.backup / name).write_text(json.dumps(value), encoding='utf-8')
        (self.state / 'public/index.json').write_bytes(b'{"reports":[]}')
        (self.modules / 'old.py').write_bytes(b'# synthetic old module')
        wrapper = self.modules / 'wrapper.py'
        wrapper.write_bytes(b'# synthetic wrapper')
        gate = self.modules / 'gate.json'
        gate_value = {'public_publishing_enabled': False}
        gate.write_bytes(json.dumps(gate_value).encode() + b'\n')
        self.bodies = {wrapper.name: wrapper.read_bytes()}
        self.checks = {
            'read_bundle': Mock(return_value=({'synthetic': 'manifest'}, self.bodies)),
            'trusted': Mock(), 'decode': json.loads,
            'read_file': lambda path, *args: path.read_bytes(),
            'MODULES': self.modules, 'STATE': self.state, 'HOME': self.home,
            'OLD_MODULES': {'old.py': 'synthetic'}, 'WRAPPER': wrapper, 'GATE': gate,
            'KEY': self.root / 'key', 'INCLUDE': self.root / 'include',
            'ACTIVATION': self.modules / 'activation.json', 'RECEIPT': self.modules / 'receipt.json',
            'absent': lambda path: not path.exists(), 'encoded': lambda value: json.dumps(value).encode(),
            'DISABLED_GATE': gate_value, 'preserved_snapshot': Mock(return_value=self.before),
            'compare_baseline': Mock(), 'digest': lambda body: hashlib.sha256(body).hexdigest(),
        }
        self.patch = patch.object(recovery, 'BACKUP', self.backup)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def contents(self):
        return {str(path.relative_to(self.root)): path.read_bytes()
                for path in self.root.rglob('*') if path.is_file()}

    def test_retained_checks_and_evidence_hashing_do_not_change_any_file(self):
        before = self.contents()
        result = recovery.retained_state(self.checks)
        digests = recovery.evidence_digest(self.checks)
        self.assertEqual(result[2], self.before)
        self.assertEqual(set(digests), {'baseline.json', 'identity.json'})
        self.assertTrue(all(len(value) == 64 for value in digests.values()))
        self.assertEqual(self.contents(), before)
        self.assertFalse(self.checks['RECEIPT'].exists())

    def test_changed_original_identity_evidence_is_refused(self):
        (self.backup / 'identity.json').write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(RuntimeError, 'identity evidence changed'):
            recovery.retained_state(self.checks)

    def test_enabled_gate_is_refused(self):
        self.checks['GATE'].write_bytes(b'{"public_publishing_enabled":true}\n')
        with self.assertRaisesRegex(RuntimeError, 'gate is not exactly closed'):
            recovery.retained_state(self.checks)

    def test_changed_installed_wrapper_is_refused(self):
        self.checks['WRAPPER'].write_bytes(b'# altered')
        with self.assertRaisesRegex(RuntimeError, 'Installed wrapper changed'):
            recovery.retained_state(self.checks)

    def test_existing_key_or_policy_is_refused(self):
        for name in ('KEY', 'INCLUDE'):
            with self.subTest(name=name), patch.dict(self.checks, {
                    'absent': lambda path, denied=self.checks[name]: path != denied}):
                with self.assertRaisesRegex(RuntimeError, 'activation or completion resource'):
                    recovery.retained_state(self.checks)

    def test_missing_original_baseline_match_is_refused(self):
        self.checks['preserved_snapshot'].return_value = {'files': {}, 'services': {}}
        with self.assertRaisesRegex(RuntimeError, 'since preparation'):
            recovery.retained_state(self.checks)


class ReviewedSourceTests(unittest.TestCase):
    def source_setup(self, helper_body=b'SOURCE_EXECUTED = True\n', manifest_body=b'{}', *,
                     helper_digest=None, manifest_digest=None, changed_open=False):
        """Mock the fixed paths and descriptors, never access server resources."""
        stack = ExitStack()
        self.addCleanup(stack.close)
        metadata = SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=1000, st_gid=1000,
                                   st_nlink=1, st_size=len(helper_body), st_dev=1, st_ino=2)

        def lstat(path):
            if path in (recovery.HELPER, recovery.RELEASE / 'manifest.json'):
                return metadata
            return SimpleNamespace(st_mode=stat.S_IFDIR | (0o700 if path == recovery.RELEASE else 0o750),
                                   st_uid=1000 if path in (recovery.RELEASE, Path('/home/shini')) else 0)

        class SourceBytes(io.BytesIO):
            def fileno(self):
                return 123

        stack.enter_context(patch.dict('sys.modules', {'pwd': SimpleNamespace(
            getpwnam=lambda _: SimpleNamespace(pw_uid=1000))}))
        stack.enter_context(patch.object(Path, 'resolve', lambda path, **kwargs: path))
        stack.enter_context(patch.object(Path, 'lstat', lstat))
        stack.enter_context(patch.object(Path, 'stat', lstat))
        stack.enter_context(patch.object(recovery.os, 'open', return_value=123))
        stack.enter_context(patch.object(recovery.os, 'fdopen', side_effect=[
            SourceBytes(helper_body), SourceBytes(manifest_body)]))
        stack.enter_context(patch.object(recovery.os, 'fstat', return_value=(
            SimpleNamespace(**dict(vars(metadata), st_ino=3)) if changed_open else metadata)))
        stack.enter_context(patch.object(recovery.os, 'O_NOFOLLOW', 0, create=True))
        stack.enter_context(patch.object(recovery.os, 'O_NONBLOCK', 0, create=True))
        stack.enter_context(patch.object(recovery, 'HELPER_SHA256',
                                        helper_digest or hashlib.sha256(helper_body).hexdigest()))
        stack.enter_context(patch.object(recovery, 'MANIFEST_SHA256',
                                        manifest_digest or hashlib.sha256(manifest_body).hexdigest()))
        return stack

    def test_reviewed_source_bytes_execute_without_main_entrypoint(self):
        self.source_setup()
        checks = recovery.load_reviewed_checks()
        self.assertTrue(checks['SOURCE_EXECUTED'])
        self.assertNotEqual(checks['__name__'], '__main__')
        self.assertEqual(checks['__file__'], str(recovery.HELPER))

    def test_helper_digest_mismatch_is_refused_before_execution(self):
        self.source_setup(helper_body=b'raise AssertionError("must never execute")', helper_digest='0' * 64)
        with self.assertRaisesRegex(RuntimeError, 'Original source digest changed'):
            recovery.load_reviewed_checks()

    def test_manifest_digest_mismatch_is_refused_before_helper_execution(self):
        self.source_setup(helper_body=b'raise AssertionError("must never execute")', manifest_digest='0' * 64)
        with self.assertRaisesRegex(RuntimeError, 'Original source digest changed'):
            recovery.load_reviewed_checks()

    def test_source_descriptor_change_is_refused_before_execution(self):
        self.source_setup(changed_open=True)
        with self.assertRaisesRegex(RuntimeError, 'changed while opening'):
            recovery.load_reviewed_checks()


if __name__ == '__main__':
    unittest.main()
