"""Local-only regression tests for completing a disabled preparation receipt."""
from collections import namedtuple
from contextlib import ExitStack, redirect_stdout
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch


SOURCE = Path(__file__).resolve().parents[1] / 'deploy/finalize-report-preparation.py'
SPEC = importlib.util.spec_from_file_location('report_preparation_finalize', SOURCE)
finalizer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(finalizer)
Shadow = namedtuple('Shadow', 'sp_expire sp_pwdp sp_inact')


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


class FinalizationFlowTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        self.receipt_path = self.root / 'receipt.json'
        self.stack.enter_context(patch.object(finalizer, 'RECEIPT', self.receipt_path))
        self.sync = self.stack.enter_context(patch.object(finalizer, 'sync_receipt_parent'))
        self.output = self.stack.enter_context(redirect_stdout(io.StringIO()))
        self.account = SimpleNamespace(pw_uid=996, pw_gid=986, pw_name='homeenergy-reporter')
        self.shadow = Shadow(1, '!SYNTHETIC-NEVER-PRINT', -1)
        self.manifest = {'synthetic': 'manifest'}
        self.bodies = {'synthetic.py': b'# synthetic body'}
        self.before = {'files': {'synthetic': 'unchanged'}, 'services': {'synthetic': 'unchanged'}}
        self.evidence = {'baseline.json': 'a' * 64, 'identity.json': 'b' * 64}
        self.recovery = {
            'identity': Mock(return_value=self.account), 'shadow_state': Mock(return_value=self.shadow),
            'retained_state': Mock(return_value=(self.manifest, self.bodies, self.before)),
            'evidence_digest': Mock(return_value=self.evidence),
            'BACKUP': Path('/synthetic/retained-backup'),
            'recover': Mock(side_effect=AssertionError('Recovery must never be invoked')),
            'main': Mock(side_effect=AssertionError('Recovery main must never be invoked')),
        }
        self.events = []

        def verify(*args, **kwargs):
            self.events.append('partial' if kwargs.get('receipt_required') is False else 'full')

        def create_file(path, body, mode):
            self.events.append('create')
            self.assertEqual(path, self.receipt_path)
            self.assertEqual(mode, 0o600)
            with path.open('xb') as stream:
                stream.write(body)

        self.checks = {
            'require_root': Mock(), 'RECEIPT': self.receipt_path,
            'absent': Mock(side_effect=lambda path: not os.path.lexists(path)),
            'verify_disabled': Mock(side_effect=verify), 'create_file': Mock(side_effect=create_file),
            'encoded': encode, 'read_file': Mock(side_effect=lambda path, limit: path.read_bytes()),
            'run': Mock(side_effect=AssertionError('No mutating command permitted')),
            'prepare_disabled': Mock(side_effect=AssertionError('No provisioning permitted')),
        }

    def expected_receipt(self):
        return {'schema_version': 1, 'scope': 'disabled-production-preparation',
                'manifest': self.manifest, 'before': self.before, 'backup': str(self.recovery['BACKUP']),
                'uid': 996, 'gid': 986}

    def run_finalizer(self, apply=True):
        return finalizer.finalize(self.recovery, self.checks, apply=apply)

    def assert_no_mutations(self):
        self.checks['create_file'].assert_not_called()
        self.checks['run'].assert_not_called()
        self.checks['prepare_disabled'].assert_not_called()
        self.recovery['recover'].assert_not_called()
        self.recovery['main'].assert_not_called()
        self.sync.assert_not_called()
        self.assertFalse(self.receipt_path.exists())

    def test_read_only_check_runs_partial_verification_but_creates_nothing(self):
        self.run_finalizer(apply=False)
        self.checks['require_root'].assert_called_once_with()
        self.checks['verify_disabled'].assert_called_once_with(
            self.manifest, self.bodies, before=self.before, receipt_required=False)
        self.assertEqual(self.recovery['retained_state'].call_count, 2)
        self.assert_no_mutations()
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertIn('No receipt or other resource was created', self.output.getvalue())

    def test_only_original_format_receipt_is_created_then_fully_verified(self):
        self.run_finalizer()
        body = encode(self.expected_receipt()) + b'\n'
        self.assertEqual(self.receipt_path.read_bytes(), body)
        self.assertEqual(list(self.root.iterdir()), [self.receipt_path])
        self.checks['create_file'].assert_called_once_with(self.receipt_path, body, 0o600)
        self.sync.assert_called_once_with(self.checks)
        self.assertEqual(self.events, ['partial', 'create', 'full'])
        self.assertEqual(self.checks['verify_disabled'].call_args_list, [
            call(self.manifest, self.bodies, before=self.before, receipt_required=False),
            call(self.manifest, self.bodies)])
        self.checks['run'].assert_not_called()
        self.checks['prepare_disabled'].assert_not_called()
        self.recovery['recover'].assert_not_called()
        self.recovery['main'].assert_not_called()
        self.assertIn('Public publishing remains disabled', self.output.getvalue())
        self.assertNotIn(self.shadow.sp_pwdp, self.output.getvalue())
        self.assertNotIn('a' * 64, self.output.getvalue())

    def test_wrong_destination_is_refused_before_any_other_check(self):
        self.checks['RECEIPT'] = self.root / 'other.json'
        with self.assertRaisesRegex(RuntimeError, 'Unexpected receipt destination'):
            self.run_finalizer()
        self.checks['absent'].assert_not_called()
        self.recovery['identity'].assert_not_called()
        self.assert_no_mutations()

    def test_existing_receipt_is_preserved_and_not_treated_as_success(self):
        original = b'preserve-existing-receipt'
        self.receipt_path.write_bytes(original)
        with self.assertRaisesRegex(RuntimeError, 'Receipt already exists'):
            self.run_finalizer()
        self.assertEqual(self.receipt_path.read_bytes(), original)
        self.checks['create_file'].assert_not_called()
        self.checks['verify_disabled'].assert_not_called()
        self.recovery['identity'].assert_not_called()

    def test_existing_directory_is_refused_without_replacing_it(self):
        self.receipt_path.mkdir()
        with self.assertRaisesRegex(RuntimeError, 'Receipt already exists'):
            self.run_finalizer()
        self.assertTrue(self.receipt_path.is_dir())
        self.checks['create_file'].assert_not_called()

    def test_existing_symlink_is_refused_even_if_target_is_missing(self):
        try:
            self.receipt_path.symlink_to(self.root / 'missing')
        except OSError:
            self.skipTest('Creating a synthetic symlink is unavailable on this platform')
        with self.assertRaisesRegex(RuntimeError, 'Receipt already exists'):
            self.run_finalizer()
        self.assertTrue(self.receipt_path.is_symlink())
        self.checks['create_file'].assert_not_called()

    def test_uncorrected_expiry_is_refused_without_recovery(self):
        self.recovery['shadow_state'].return_value = self.shadow._replace(sp_expire=-1)
        with self.assertRaisesRegex(RuntimeError, 'expiry must already be corrected'):
            self.run_finalizer()
        self.recovery['retained_state'].assert_not_called()
        self.assert_no_mutations()

    def test_invalid_identity_stops_before_shadow_or_retained_state(self):
        self.recovery['identity'].side_effect = RuntimeError('Existing reporting identity changed')
        with self.assertRaisesRegex(RuntimeError, 'identity changed'):
            self.run_finalizer()
        self.recovery['shadow_state'].assert_not_called()
        self.recovery['retained_state'].assert_not_called()
        self.assert_no_mutations()

    def test_unlocked_shadow_stops_before_retained_state(self):
        self.recovery['shadow_state'].side_effect = RuntimeError('Account is not locked')
        with self.assertRaisesRegex(RuntimeError, 'not locked'):
            self.run_finalizer()
        self.recovery['retained_state'].assert_not_called()
        self.assert_no_mutations()

    def test_retained_state_or_gate_failure_prevents_receipt(self):
        self.recovery['retained_state'].side_effect = RuntimeError('Publishing gate is not exactly closed')
        with self.assertRaisesRegex(RuntimeError, 'not exactly closed'):
            self.run_finalizer()
        self.checks['verify_disabled'].assert_not_called()
        self.assert_no_mutations()

    def test_failed_partial_check_prevents_receipt(self):
        self.checks['verify_disabled'].side_effect = RuntimeError('Protected service drift')
        with self.assertRaisesRegex(RuntimeError, 'Protected service drift'):
            self.run_finalizer()
        self.assert_no_mutations()

    def test_account_drift_during_partial_check_prevents_receipt(self):
        self.recovery['identity'].side_effect = [self.account, SimpleNamespace(pw_uid=997, pw_gid=986)]
        with self.assertRaisesRegex(RuntimeError, 'Account or original evidence changed'):
            self.run_finalizer()
        self.assert_no_mutations()

    def test_shadow_drift_during_partial_check_prevents_receipt(self):
        self.recovery['shadow_state'].side_effect = [self.shadow, self.shadow._replace(sp_inact=0)]
        with self.assertRaisesRegex(RuntimeError, 'Account or original evidence changed'):
            self.run_finalizer()
        self.assert_no_mutations()

    def test_evidence_drift_during_partial_check_prevents_receipt(self):
        self.recovery['evidence_digest'].side_effect = [self.evidence, {'baseline.json': 'changed'}]
        with self.assertRaisesRegex(RuntimeError, 'Account or original evidence changed'):
            self.run_finalizer()
        self.assert_no_mutations()

    def test_changed_second_retained_snapshot_prevents_receipt(self):
        self.recovery['retained_state'].side_effect = [
            (self.manifest, self.bodies, self.before), ({'changed': True}, self.bodies, self.before)]
        with self.assertRaisesRegex(RuntimeError, 'Retained preparation changed'):
            self.run_finalizer()
        self.assert_no_mutations()

    def test_exclusive_creation_race_never_overwrites_other_receipt(self):
        competing = b'competing-receipt'

        def race(path, body, mode):
            path.write_bytes(competing)
            with path.open('xb') as stream:
                stream.write(body)

        self.checks['create_file'].side_effect = race
        with self.assertRaises(FileExistsError):
            self.run_finalizer()
        self.assertEqual(self.receipt_path.read_bytes(), competing)
        self.assertEqual(self.checks['verify_disabled'].call_count, 1)
        self.sync.assert_not_called()

    def test_oversized_receipt_is_refused_before_any_write(self):
        self.checks['encoded'] = lambda value: b'x' * 65536
        with self.assertRaisesRegex(RuntimeError, 'receipt.*bound|receipt.*large|receipt.*limit'):
            self.run_finalizer()
        self.assert_no_mutations()

    def test_parent_fsync_failure_retains_created_receipt(self):
        self.sync.side_effect = OSError('Synthetic fsync failure')
        with self.assertRaisesRegex(OSError, 'fsync failure'):
            self.run_finalizer()
        self.assertEqual(self.receipt_path.read_bytes(), encode(self.expected_receipt()) + b'\n')
        self.assertEqual(self.checks['verify_disabled'].call_count, 1)

    def test_failed_full_verification_retains_receipt_and_reports_no_success(self):
        self.checks['verify_disabled'].side_effect = [None, RuntimeError('Full check failed')]
        with self.assertRaisesRegex(RuntimeError, 'Full check failed'):
            self.run_finalizer()
        self.assertEqual(self.receipt_path.read_bytes(), encode(self.expected_receipt()) + b'\n')
        self.assertIn('Receipt created', self.output.getvalue())
        self.assertNotIn('finalized and verified', self.output.getvalue())

    def test_post_write_identity_drift_is_reported_without_receipt_deletion(self):
        self.recovery['identity'].side_effect = [self.account, self.account, SimpleNamespace(pw_uid=997)]
        with self.assertRaisesRegex(RuntimeError, 'Account or original evidence changed'):
            self.run_finalizer()
        self.assertTrue(self.receipt_path.exists())

    def test_post_write_shadow_drift_is_reported_without_receipt_deletion(self):
        self.recovery['shadow_state'].side_effect = [self.shadow, self.shadow, self.shadow._replace(sp_expire=-1)]
        with self.assertRaisesRegex(RuntimeError, 'Account or original evidence changed'):
            self.run_finalizer()
        self.assertTrue(self.receipt_path.exists())

    def test_post_write_evidence_drift_is_reported_without_receipt_deletion(self):
        self.recovery['evidence_digest'].side_effect = [self.evidence, self.evidence, {'changed': True}]
        with self.assertRaisesRegex(RuntimeError, 'Account or original evidence changed'):
            self.run_finalizer()
        self.assertTrue(self.receipt_path.exists())

    def test_post_verification_receipt_tampering_is_refused_and_preserved(self):
        def verify(*args, **kwargs):
            if not kwargs:
                self.receipt_path.write_bytes(b'changed-receipt')

        self.checks['verify_disabled'].side_effect = verify
        with self.assertRaisesRegex(RuntimeError, 'Completion receipt changed'):
            self.run_finalizer()
        self.assertEqual(self.receipt_path.read_bytes(), b'changed-receipt')


class ReviewedRecoverySourceTests(unittest.TestCase):
    def source_setup(self, *, body=None, expected_digest=None, file_changes=None,
                     parent_changes=None, opened_changes=None, linked_path=False):
        if body is None:
            body = b'SOURCE_EXECUTED=True\nif __name__ == "__main__": raise AssertionError("No main")\n'
        stack = ExitStack()
        self.addCleanup(stack.close)
        metadata = dict(st_mode=stat.S_IFREG | 0o600, st_uid=1000, st_gid=1000,
                        st_nlink=1, st_size=len(body), st_dev=1, st_ino=2)
        metadata.update(file_changes or {})
        info = SimpleNamespace(**metadata)

        def lstat(path):
            if path == finalizer.RECOVERY:
                return info
            fields = {'st_mode': stat.S_IFDIR | (0o700 if path == finalizer.RECOVERY.parent else 0o750),
                      'st_uid': 1000 if path in (finalizer.RECOVERY.parent, Path('/home/shini')) else 0}
            if path == finalizer.RECOVERY.parent:
                fields.update(parent_changes or {})
            return SimpleNamespace(**fields)

        class SourceBytes(io.BytesIO):
            def fileno(self):
                return 123

        stack.enter_context(patch.dict('sys.modules', {'pwd': SimpleNamespace(
            getpwnam=lambda _: SimpleNamespace(pw_uid=1000))}))
        stack.enter_context(patch.object(Path, 'resolve', lambda path, **kwargs: (
            path / 'linked' if linked_path else path)))
        stack.enter_context(patch.object(Path, 'lstat', lstat))
        stack.enter_context(patch.object(Path, 'stat', lstat))
        self.open_mock = stack.enter_context(patch.object(finalizer.os, 'open', return_value=123))
        stack.enter_context(patch.object(finalizer.os, 'fdopen', return_value=SourceBytes(body)))
        stack.enter_context(patch.object(finalizer.os, 'fstat', return_value=SimpleNamespace(
            **dict(metadata, **(opened_changes or {})))))
        stack.enter_context(patch.object(finalizer.os, 'O_NOFOLLOW', 0, create=True))
        stack.enter_context(patch.object(finalizer.os, 'O_NONBLOCK', 0, create=True))
        stack.enter_context(patch.object(finalizer, 'RECOVERY_SHA256',
                                        expected_digest or hashlib.sha256(body).hexdigest()))
        return stack

    def test_only_verified_source_definitions_are_executed(self):
        self.source_setup()
        namespace = finalizer.load_recovery()
        self.assertTrue(namespace['SOURCE_EXECUTED'])
        self.assertEqual(namespace['__file__'], str(finalizer.RECOVERY))
        self.assertNotEqual(namespace['__name__'], '__main__')

    def test_recovery_hash_mismatch_prevents_source_execution(self):
        self.source_setup(body=b'raise AssertionError("Must not execute")', expected_digest='0' * 64)
        with self.assertRaisesRegex(RuntimeError, 'Reviewed recovery digest changed'):
            finalizer.load_recovery()

    def test_linked_recovery_path_is_refused_before_open(self):
        self.source_setup(linked_path=True)
        with self.assertRaisesRegex(RuntimeError, 'Reviewed recovery path changed'):
            finalizer.load_recovery()
        self.open_mock.assert_not_called()

    def test_group_writable_parent_is_refused_before_open(self):
        self.source_setup(parent_changes={'st_mode': stat.S_IFDIR | 0o720})
        with self.assertRaisesRegex(RuntimeError, 'Reviewed recovery parent changed'):
            finalizer.load_recovery()
        self.open_mock.assert_not_called()

    def test_nonprivate_recovery_directory_is_refused_before_open(self):
        self.source_setup(parent_changes={'st_mode': stat.S_IFDIR | 0o750})
        with self.assertRaisesRegex(RuntimeError, 'Recovery directory is not private'):
            finalizer.load_recovery()
        self.open_mock.assert_not_called()

    def test_unexpected_parent_owner_is_refused_before_open(self):
        self.source_setup(parent_changes={'st_uid': 99})
        with self.assertRaisesRegex(RuntimeError, 'Reviewed recovery parent changed'):
            finalizer.load_recovery()
        self.open_mock.assert_not_called()

    def test_unsafe_recovery_metadata_is_refused_before_open(self):
        for change in ({'st_mode': stat.S_IFLNK | 0o600}, {'st_mode': stat.S_IFREG | 0o644},
                       {'st_uid': 99}, {'st_nlink': 2}, {'st_size': 0}, {'st_size': 65537}):
            with self.subTest(change=change):
                with self.source_setup(file_changes=change):
                    with self.assertRaisesRegex(RuntimeError, 'Reviewed recovery file metadata changed'):
                        finalizer.load_recovery()
                    self.open_mock.assert_not_called()

    def test_descriptor_metadata_drift_is_refused_before_execution(self):
        self.source_setup(body=b'raise AssertionError("Must not execute")', opened_changes={'st_ino': 3})
        with self.assertRaisesRegex(RuntimeError, 'Recovery changed while opening'):
            finalizer.load_recovery()


class ParentDurabilityTests(unittest.TestCase):
    def test_parent_fsync_is_read_only_open_and_always_closed(self):
        checks = {'trusted': Mock()}
        with patch.object(finalizer.os, 'O_DIRECTORY', 0x10000, create=True), \
                patch.object(finalizer.os, 'O_NOFOLLOW', 0x20000, create=True), \
                patch.object(finalizer.os, 'open', return_value=321) as opened, \
                patch.object(finalizer.os, 'fsync') as synced, patch.object(finalizer.os, 'close') as closed:
            finalizer.sync_receipt_parent(checks)
        checks['trusted'].assert_called_once_with(finalizer.RECEIPT.parent, mode=0o755)
        opened.assert_called_once_with(finalizer.RECEIPT.parent, os.O_RDONLY | 0x10000 | 0x20000)
        synced.assert_called_once_with(321)
        closed.assert_called_once_with(321)

    def test_fsync_failure_still_closes_descriptor(self):
        with patch.object(finalizer.os, 'O_DIRECTORY', 0, create=True), \
                patch.object(finalizer.os, 'O_NOFOLLOW', 0, create=True), \
                patch.object(finalizer.os, 'open', return_value=321), \
                patch.object(finalizer.os, 'fsync', side_effect=OSError('Synthetic failure')), \
                patch.object(finalizer.os, 'close') as closed:
            with self.assertRaisesRegex(OSError, 'Synthetic failure'):
                finalizer.sync_receipt_parent({'trusted': Mock()})
        closed.assert_called_once_with(321)


class EntrypointTests(unittest.TestCase):
    def invoke(self, flag='--check', *, platform='linux', uid=0, isolated=1, bytecode=1):
        recovery = {'load_reviewed_checks': Mock(return_value={'synthetic': 'checks'})}
        with patch.object(finalizer.sys, 'argv', [str(SOURCE), flag]), \
                patch.object(finalizer.sys, 'platform', platform), \
                patch.object(finalizer.os, 'geteuid', return_value=uid, create=True), \
                patch.object(finalizer.sys, 'flags', SimpleNamespace(isolated=isolated, dont_write_bytecode=bytecode)), \
                patch.object(finalizer, 'load_recovery', return_value=recovery) as loaded, \
                patch.object(finalizer, 'finalize') as finalized:
            finalizer.main()
        return loaded, finalized, recovery

    def test_check_entrypoint_never_requests_apply(self):
        loaded, finalized, recovery = self.invoke()
        loaded.assert_called_once_with()
        recovery['load_reviewed_checks'].assert_called_once_with()
        finalized.assert_called_once_with(recovery, {'synthetic': 'checks'}, apply=False)

    def test_finalize_entrypoint_selects_only_receipt_finalization(self):
        _, finalized, recovery = self.invoke('--finalize')
        finalized.assert_called_once_with(recovery, {'synthetic': 'checks'}, apply=True)

    def test_unreviewed_execution_modes_are_rejected(self):
        for change in ({'platform': 'win32'}, {'uid': 1000}, {'isolated': 0}, {'bytecode': 0}):
            with self.subTest(change=change), self.assertRaisesRegex(RuntimeError, 'sudo /usr/bin/python3 -I -B'):
                self.invoke(**change)


if __name__ == '__main__':
    unittest.main()
