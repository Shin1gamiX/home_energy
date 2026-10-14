"""Offline/synthetic activation tests. No real sudo, server, model or schedule."""
from contextlib import contextmanager, nullcontext, redirect_stdout
import copy
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


SOURCE = Path(__file__).resolve().parents[1] / 'deploy/activate-report-production.py'
SPEC = importlib.util.spec_from_file_location('report_production_activation', SOURCE)
h = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(h)
HASH = 'a' * 64


def context():
    return SimpleNamespace(helper_hash=HASH, access_hash='b' * 64,
        checks={'preserved_snapshot': Mock(return_value={'synthetic': True}),
                'validate_services': Mock(), 'verify_gate_rejection': Mock(),
                'no_write_access': Mock(), 'EMPTY_FEED_SHA256': 'c' * 64})


def baseline():
    return {'schema_version': 1, 'scope': h.SCOPE, 'helper_sha256': HASH,
        'access_baseline_sha256': 'b' * 64, 'uid': h.UID, 'gid': h.GID,
        'directories': {str(p): {'device': 1, 'inode': i + 10, 'uid': 0, 'gid': 0, 'mode': mode}
                        for i, (p, mode) in enumerate(h.DIRECTORIES)},
        'preserved': {'files': {}, 'services': {}}}


def response(stdout='', stderr='', returncode=0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


class ContractTests(unittest.TestCase):
    def test_source_dependency_pin_is_unchanged(self):
        body = (SOURCE.parent / 'activate-report-access.py').read_bytes()
        self.assertEqual(hashlib.sha256(body).hexdigest(), h.ACCESS_SHA256)

    def test_gate_has_exact_schema_and_boolean(self):
        for enabled in (False, True):
            self.assertEqual(json.loads(h.gate_body(enabled)), {'schema_version': 1,
                'scope': 'production', 'public_publishing_enabled': enabled})
        for bad in (0, 1, 'true', None):
            with self.subTest(bad=bad), self.assertRaises(RuntimeError):
                h.gate_body(bad)

    def test_evidence_rejects_duplicates_noncanonical_and_unbounded_data(self):
        for body in (b'{"a":1,"a":2}\n', b'{"a": 1}\n', b'{}', b'[]\n', b'0\n',
                     b'{"a":NaN}\n', b'x' * 65537):
            with self.subTest(body=body[:40]), self.assertRaises((RuntimeError, ValueError)):
                h.decode(body)
        self.assertEqual(h.decode(h.encoded({'a': 1})), {'a': 1})

    def test_readiness_probe_imports_only_and_never_dispatches(self):
        compile(h.READY_PROBE, '<synthetic-readiness-probe>', 'exec')
        self.assertIn('checks["verify_environment"]()', h.READY_PROBE)
        for forbidden in ('["main"]', 'run_name=', '.prepare(', '.publish(', '["dispatch"]'):
            self.assertNotIn(forbidden, h.READY_PROBE)

    def test_helper_has_no_cleanup_delete_or_schedule_device_commands(self):
        source = SOURCE.read_text(encoding='utf-8')
        for forbidden in ('.unlink(', 'os.remove(', 'rmtree(', "'chage'", "'useradd'",
                          "'reload'", "'restart'", 'schtasks', 'Register-ScheduledTask'):
            self.assertNotIn(forbidden, source)


class PortableFiles(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.modules = self.root / 'modules'
        self.modules.mkdir()
        self.gate = self.modules / 'production-gate.json'
        self.enterContext(patch.object(h, 'MODULES', self.modules))
        self.enterContext(patch.object(h, 'GATE', self.gate))
        self.enterContext(patch.object(h, 'root_path', side_effect=lambda path, *a, **kw: path.lstat()))
        self.enterContext(patch.object(h, 'backup_path'))
        self.enterContext(patch.object(h, 'sync_directory'))
        # These fixtures test portable control flow/bytes, not Linux privileges.
        self.enterContext(patch.object(h.os, 'O_NOFOLLOW', getattr(os, 'O_NOFOLLOW', 0), create=True))
        self.enterContext(patch.object(h.os, 'O_NONBLOCK', getattr(os, 'O_NONBLOCK', 0), create=True))
        self.enterContext(patch.object(h.os, 'fchown', create=True))
        self.enterContext(patch.object(h.os, 'fchmod', create=True))

    def test_known_gate_transition_and_repeat_is_noop(self):
        self.gate.write_bytes(h.gate_body(False))
        h.write_gate(True)
        self.assertEqual(self.gate.read_bytes(), h.gate_body(True))
        with patch.object(h.tempfile, 'mkstemp') as create:
            h.write_gate(True)
        create.assert_not_called()
        h.write_gate(False)
        self.assertEqual(self.gate.read_bytes(), h.gate_body(False))

    def test_unknown_gate_is_never_replaced(self):
        self.gate.write_bytes(b'{"unknown":true}\n')
        before = self.gate.read_bytes()
        with patch.object(h.os, 'replace') as replace:
            with self.assertRaises(RuntimeError):
                h.write_gate(False)
        replace.assert_not_called()
        self.assertEqual(self.gate.read_bytes(), before)

    def test_failed_gate_replace_preserves_original_and_temporary(self):
        self.gate.write_bytes(h.gate_body(False))
        with patch.object(h.os, 'replace', side_effect=OSError('synthetic fault')):
            with self.assertRaises(OSError):
                h.write_gate(True)
        self.assertEqual(self.gate.read_bytes(), h.gate_body(False))
        temporary = list(self.modules.glob('.production-gate-*.tmp'))
        self.assertEqual(len(temporary), 1)
        self.assertEqual(temporary[0].read_bytes(), h.gate_body(True))

    def test_invalid_gate_value_does_not_read_or_write(self):
        with patch.object(h, 'read_gate') as read:
            with self.assertRaises(RuntimeError):
                h.write_gate(1)
        read.assert_not_called()

    def test_evidence_is_create_new_and_preserves_existing(self):
        h.new_evidence(self.root, 'baseline.json', {'test': 1})
        before = (self.root / 'baseline.json').read_bytes()
        with self.assertRaises(FileExistsError):
            h.new_evidence(self.root, 'baseline.json', {'test': 2})
        self.assertEqual((self.root / 'baseline.json').read_bytes(), before)

    def test_evidence_refuses_unknown_name_or_large_body(self):
        for name in ('../key', 'activation.json', 'report.json'):
            with self.subTest(name=name), self.assertRaises(RuntimeError):
                h.new_evidence(self.root, name, {})
        with self.assertRaises(RuntimeError):
            h.new_evidence(self.root, 'baseline.json', {'large': 'x' * 65536})
        self.assertFalse((self.root / 'baseline.json').exists())

    def test_hardlinked_file_refused(self):
        self.gate.write_bytes(h.gate_body(False))
        os.link(self.gate, self.modules / 'other-link')
        with self.assertRaisesRegex(RuntimeError, 'Linked'):
            h.read_file(self.gate)

    def test_bounded_file_reader(self):
        self.gate.write_bytes(b'x' * 40)
        with self.assertRaises(RuntimeError):
            h.read_file(self.gate, 20)
        self.assertEqual(h.read_file(self.gate, 40), b'x' * 40)


class BaselineTests(unittest.TestCase):
    def setUp(self):
        self.ctx = context()
        self.before = baseline()
        self.backup = h.ADMIN_HOME / 'homeenergy-report-activation-backup.synthetic'
        self.intent = {'schema_version': 1, 'scope': h.SCOPE, 'backup': str(self.backup),
            'helper_sha256': HASH, 'baseline_sha256': h.digest(h.encoded(self.before))}
        self.enterContext(patch.object(h, 'backup_path'))
        self.enterContext(patch.object(h, 'root_path'))
        self.read = self.enterContext(patch.object(h, 'read_file', return_value=h.encoded(self.before)))

    def test_accepts_exact_baseline(self):
        self.assertEqual(h.load_baseline(self.ctx, self.intent), (self.backup, self.before))

    def test_intent_schema_and_runner_binding(self):
        for name, value in (('helper_sha256', 'c' * 64), ('scope', 'other'),
                            ('schema_version', True), ('extra', 'data'), ('baseline_sha256', '0' * 64)):
            with self.subTest(name=name), self.assertRaises(RuntimeError):
                h.load_baseline(self.ctx, self.intent | {name: value})

    def test_rejects_modified_baseline_and_identity(self):
        for name, value in (('uid', 0), ('gid', 0), ('uid', True), ('scope', 'other'),
                            ('access_baseline_sha256', 'c' * 64), ('schema_version', True)):
            with self.subTest(name=name):
                changed = self.before | {name: value}
                self.read.return_value = h.encoded(changed)
                intent = self.intent | {'baseline_sha256': h.digest(h.encoded(changed))}
                with self.assertRaises(RuntimeError):
                    h.load_baseline(self.ctx, intent)

    def test_rejects_arbitrary_or_non_root_original_directory(self):
        for directories in ({'/unrelated': {}},
            {name: row | {'uid': h.UID} for name, row in self.before['directories'].items()}):
            changed = self.before | {'directories': directories}
            self.read.return_value = h.encoded(changed)
            with self.assertRaises(RuntimeError):
                h.load_baseline(self.ctx, self.intent | {'baseline_sha256': h.digest(h.encoded(changed))})


class ActivationTests(unittest.TestCase):
    def setUp(self):
        self.ctx = context()
        self.before = {'preserved': {'synthetic': True}}
        self.events = []
        self.backup = h.ADMIN_HOME / 'homeenergy-report-activation-backup.synthetic'
        self.enterContext(redirect_stdout(io.StringIO()))
        self.preflight = self.enterContext(patch.object(h, 'preflight', return_value=self.before))
        self.create_backup = self.enterContext(patch.object(h.tempfile, 'mkdtemp', return_value=str(self.backup)))
        self.enterContext(patch.object(h.os, 'chown', create=True))
        self.enterContext(patch.object(h.os, 'chmod'))
        self.evidence = self.enterContext(patch.object(h, 'new_evidence'))

        @contextmanager
        def lock(intent):
            self.events.append('intent')
            yield intent
        self.lock = self.enterContext(patch.object(h, 'activation_lock', side_effect=lock))
        self.enterContext(patch.object(h, 'read_gate', return_value=False))
        self.directories = self.enterContext(patch.object(h, 'set_directories',
            side_effect=lambda *a, **kw: self.events.append(('directories', kw['enabled']))))
        self.verify = self.enterContext(patch.object(h, 'verify_ready',
            side_effect=lambda *a, **kw: self.events.append(('verify', kw['gate_enabled']))))
        self.gate = self.enterContext(patch.object(h, 'write_gate',
            side_effect=lambda value: self.events.append(('gate', value))))
        self.record = self.enterContext(patch.object(h, 'record',
            side_effect=lambda backup, event, **kw: self.events.append(('record', event))))
        self.rollback = self.enterContext(patch.object(h, 'rollback'))

    def test_gate_opens_last_and_no_rollback_on_success(self):
        h.activate(self.ctx)
        self.assertEqual(self.events, ['intent', ('directories', True), ('verify', False),
            ('gate', True), ('verify', True), ('record', 'activated')])
        self.assertEqual(self.preflight.call_count, 2)
        self.evidence.assert_called_once_with(self.backup, 'baseline.json', self.before)
        self.rollback.assert_not_called()

    def test_first_preflight_failure_cannot_create_or_activate(self):
        self.preflight.side_effect = RuntimeError('synthetic baseline drift')
        with self.assertRaises(RuntimeError):
            h.activate(self.ctx)
        self.create_backup.assert_not_called()
        self.lock.assert_not_called()
        self.gate.assert_not_called()
        self.rollback.assert_not_called()

    def test_second_preflight_drift_preserves_backup_without_activation(self):
        self.preflight.side_effect = [self.before, {'preserved': {'changed': True}}]
        with self.assertRaisesRegex(RuntimeError, 'State changed'):
            h.activate(self.ctx)
        self.evidence.assert_called_once()
        self.lock.assert_not_called()
        self.rollback.assert_not_called()

    def test_losing_intent_race_must_not_rollback_another_run(self):
        self.lock.side_effect = FileExistsError('existing intent')
        with self.assertRaises(FileExistsError):
            h.activate(self.ctx)
        self.gate.assert_not_called()
        self.rollback.assert_not_called()

    def test_ownership_failure_rolls_back_only_owned_intent(self):
        self.directories.side_effect = OSError('synthetic partial ownership failure')
        with self.assertRaises(OSError):
            h.activate(self.ctx)
        self.gate.assert_not_called()
        self.rollback.assert_called_once_with(self.ctx, self.backup, self.before)

    def test_failed_closed_readiness_never_opens_gate(self):
        self.verify.side_effect = RuntimeError('synthetic permission failure')
        with self.assertRaises(RuntimeError):
            h.activate(self.ctx)
        self.gate.assert_not_called()
        self.rollback.assert_called_once()

    def test_failed_open_readiness_closes_through_rollback(self):
        self.verify.side_effect = [None, RuntimeError('synthetic readiness failure')]
        with self.assertRaises(RuntimeError):
            h.activate(self.ctx)
        self.gate.assert_called_once_with(True)
        self.rollback.assert_called_once()

    def test_gate_write_uncertainty_rolls_back(self):
        self.gate.side_effect = OSError('synthetic atomic write failure')
        with self.assertRaises(OSError):
            h.activate(self.ctx)
        self.rollback.assert_called_once()

    def test_failed_success_receipt_also_rolls_back(self):
        def record(backup, event, **values):
            if event == 'activated':
                raise OSError('synthetic evidence write failure')
        self.record.side_effect = record
        with self.assertRaises(OSError):
            h.activate(self.ctx)
        self.rollback.assert_called_once()
        self.assertEqual(self.record.call_args, call(self.backup, 'failed', rollback_verified=True))

    def test_failed_rollback_is_not_recorded_as_verified_or_retried(self):
        self.verify.side_effect = RuntimeError('synthetic permission failure')
        self.rollback.side_effect = OSError('synthetic rollback failure')
        with self.assertRaises(RuntimeError):
            h.activate(self.ctx)
        self.rollback.assert_called_once()
        self.assertEqual(self.record.call_args, call(self.backup, 'failed', rollback_verified=False))
        self.gate.assert_not_called()


class RollbackTests(unittest.TestCase):
    def setUp(self):
        self.ctx = context()
        self.events = []
        self.backup, self.before = Path('/synthetic/backup'), {}
        self.files = {str(h.PUBLIC / 'index.json'): {'sha256': 'newly-published-content'}}
        self.enterContext(redirect_stdout(io.StringIO()))
        def step(name, result=None):
            def invoke(*args, **kwargs):
                self.events.append(name)
                return result
            return invoke
        self.gate = self.enterContext(patch.object(h, 'write_gate', side_effect=step('close')))
        self.enterContext(patch.object(h, 'read_gate', return_value=False))
        self.drain = self.enterContext(patch.object(h, 'drain_reporter', side_effect=step('drain')))
        self.directories = self.enterContext(patch.object(h, 'set_directories', side_effect=step('directories')))
        self.inventory = self.enterContext(patch.object(h, 'report_files', side_effect=step('inventory', self.files)))
        self.freeze = self.enterContext(patch.object(h, 'freeze_files', side_effect=step('freeze', self.files)))
        self.verify = self.enterContext(patch.object(h, 'verify_common', side_effect=step('audit', 'account')))
        self.record = self.enterContext(patch.object(h, 'record',
            side_effect=lambda backup, event, **kw: self.events.append(event)))

    def test_close_then_drain_then_freeze_without_restoring_old_report(self):
        h.rollback(self.ctx, self.backup, self.before)
        self.assertEqual(self.events, ['close', 'drain', 'directories', 'inventory',
            'rollback-before', 'freeze', 'audit', 'inventory', 'rollback-complete'])
        self.gate.assert_called_once_with(False)
        self.directories.assert_called_once_with(self.before, enabled=False)
        self.freeze.assert_called_once_with(self.files)
        self.ctx.checks['verify_gate_rejection'].assert_called_once_with('account')

    def test_drain_failure_does_not_mutate_in_flight_storage(self):
        self.drain.side_effect = RuntimeError('not drained')
        with self.assertRaises(RuntimeError):
            h.rollback(self.ctx, self.backup, self.before)
        self.gate.assert_called_once_with(False)
        self.directories.assert_not_called()
        self.freeze.assert_not_called()

    def test_unexpected_state_is_preserved_for_review(self):
        self.inventory.side_effect = RuntimeError('unknown file')
        with self.assertRaises(RuntimeError):
            h.rollback(self.ctx, self.backup, self.before)
        self.gate.assert_called_once_with(False)
        self.freeze.assert_not_called()
        self.record.assert_not_called()

    def test_failed_freeze_cannot_claim_rollback_complete(self):
        self.freeze.side_effect = OSError('synthetic ownership failure')
        with self.assertRaises(OSError):
            h.rollback(self.ctx, self.backup, self.before)
        self.assertNotIn('rollback-complete', self.events)
        self.verify.assert_not_called()

    def test_unrelated_drift_is_checked_after_closing_and_freezing(self):
        self.verify.side_effect = RuntimeError('unrelated file drift')
        with self.assertRaises(RuntimeError):
            h.rollback(self.ctx, self.backup, self.before)
        self.freeze.assert_called_once()
        self.gate.assert_called_once_with(False)
        self.assertNotIn('rollback-complete', self.events)


class DrainTests(unittest.TestCase):
    def setUp(self):
        self.gate = self.enterContext(patch.object(h, 'read_gate', return_value=False))
        self.process = self.enterContext(patch.object(h.subprocess, 'run'))
        self.sleep = self.enterContext(patch.object(h.time, 'sleep'))
        self.enterContext(patch.object(h.time, 'monotonic', return_value=0))

    def test_waits_only_for_fixed_uid_without_killing(self):
        self.process.side_effect = [response('123\n'), response(returncode=1)]
        h.drain_reporter()
        self.assertEqual(self.process.call_count, 2)
        self.assertEqual(self.process.call_args.args[0], ['/usr/bin/ps', '-o', 'pid=', '-u', str(h.UID)])
        self.sleep.assert_called_once_with(.25)

    def test_inspection_error_is_not_treated_as_empty(self):
        for result in (response(), response('123', returncode=1), response('nonsense'),
                       response(stderr='error', returncode=1), response(returncode=2)):
            with self.subTest(result=result):
                self.process.return_value = result
                with self.assertRaises(RuntimeError):
                    h.drain_reporter()

    def test_bounded_wait_refuses_ownership_change_after_timeout(self):
        self.process.return_value = response('123\n')
        with patch.object(h.time, 'monotonic', side_effect=[0, h.DRAIN_SECONDS + 1]):
            with self.assertRaisesRegex(RuntimeError, 'did not drain'):
                h.drain_reporter()
        self.sleep.assert_not_called()

    def test_open_gate_refuses_drain(self):
        self.gate.return_value = True
        with self.assertRaises(RuntimeError):
            h.drain_reporter()
        self.process.assert_not_called()


class OwnershipHandleTests(unittest.TestCase):
    """Synthetic Linux metadata/handles; no chown of real host paths."""
    def setUp(self):
        self.before = baseline()
        self.rows = copy.deepcopy(self.before['directories'])
        self.handles = {}
        self.enterContext(patch.object(h, 'read_gate', return_value=False))
        self.acl = self.enterContext(patch.object(h, 'no_acl'))
        self.enterContext(patch.object(h.os, 'O_NOFOLLOW', 0, create=True))
        self.enterContext(patch.object(h.os, 'O_DIRECTORY', 0, create=True))

        def info(name):
            row = self.rows[str(name)]
            return SimpleNamespace(st_dev=row['device'], st_ino=row['inode'],
                st_uid=row['uid'], st_gid=row['gid'], st_mode=stat.S_IFDIR | row['mode'], st_nlink=2)

        def open_handle(path, flags):
            descriptor = len(self.handles) + 100
            self.handles[descriptor] = str(path)
            return descriptor

        self.enterContext(patch.object(h, 'canonical', side_effect=info))
        self.open = self.enterContext(patch.object(h.os, 'open', side_effect=open_handle))
        self.stat = self.enterContext(patch.object(h.os, 'fstat', side_effect=lambda fd: info(self.handles[fd])))
        self.chown = self.enterContext(patch.object(h.os, 'fchown', create=True,
            side_effect=lambda fd, uid, gid: self.rows[self.handles[fd]].update(uid=uid, gid=gid)))
        self.enterContext(patch.object(h.os, 'fchmod', create=True,
            side_effect=lambda fd, mode: self.rows[self.handles[fd]].update(mode=mode)))
        self.enterContext(patch.object(h.os, 'fsync'))
        self.close = self.enterContext(patch.object(h.os, 'close'))

    def test_only_recorded_directories_change_and_restore(self):
        h.set_directories(self.before, enabled=True)
        for name, row in self.rows.items():
            self.assertEqual(row, self.before['directories'][name] | {'uid': h.UID, 'gid': h.GID})
        h.set_directories(self.before, enabled=False)
        self.assertEqual(self.rows, self.before['directories'])
        self.assertEqual(self.chown.call_count, 4)
        self.assertEqual(self.close.call_count, 4)

    def test_replaced_directory_is_not_chowned(self):
        self.rows[str(h.STATE)]['inode'] += 1
        with self.assertRaisesRegex(RuntimeError, 'replaced'):
            h.set_directories(self.before, enabled=True)
        self.open.assert_not_called()
        self.chown.assert_not_called()

    def test_unreviewed_acl_is_not_silently_removed(self):
        self.acl.side_effect = RuntimeError('unexpected ACL')
        with self.assertRaises(RuntimeError):
            h.set_directories(self.before, enabled=True)
        self.open.assert_not_called()
        self.chown.assert_not_called()

    def test_opened_handle_mismatch_closes_without_chown(self):
        self.stat.return_value = SimpleNamespace(st_dev=1, st_ino=9999, st_mode=stat.S_IFDIR | 0o711,
                                                st_uid=0, st_gid=0, st_nlink=2)
        self.stat.side_effect = None
        with self.assertRaisesRegex(RuntimeError, 'while opening'):
            h.set_directories(self.before, enabled=True)
        self.chown.assert_not_called()
        self.close.assert_called_once_with(100)

    def test_gate_must_be_closed_before_any_metadata_mutation(self):
        with patch.object(h, 'read_gate', return_value=True), self.assertRaises(RuntimeError):
            h.set_directories(self.before, enabled=False)
        self.open.assert_not_called()


class FreezeHandleTests(unittest.TestCase):
    def setUp(self):
        self.rows = {str(h.PUBLIC / 'index.json'): {'device': 1, 'inode': 31, 'uid': h.UID,
            'gid': h.GID, 'mode': 0o644, 'sha256': 'a' * 64},
            str(h.STATE / 'reports.sqlite3'): {'device': 1, 'inode': 32, 'uid': h.UID,
            'gid': h.GID, 'mode': 0o600, 'sha256': 'b' * 64}}
        self.before = copy.deepcopy(self.rows)
        self.handles = {}
        self.enterContext(patch.object(h, 'read_gate', return_value=False))
        self.inventory = self.enterContext(patch.object(h, 'report_files', side_effect=lambda: copy.deepcopy(self.rows)))
        self.enterContext(patch.object(h.os, 'O_NOFOLLOW', 0, create=True))
        self.enterContext(patch.object(h.os, 'O_NONBLOCK', 0, create=True))

        def open_handle(path, flags):
            descriptor = len(self.handles) + 100
            self.handles[descriptor] = str(path)
            return descriptor

        def info(fd):
            row = self.rows[self.handles[fd]]
            return SimpleNamespace(st_dev=row['device'], st_ino=row['inode'], st_uid=row['uid'],
                st_gid=row['gid'], st_mode=stat.S_IFREG | row['mode'], st_nlink=1)

        self.open = self.enterContext(patch.object(h.os, 'open', side_effect=open_handle))
        self.stat = self.enterContext(patch.object(h.os, 'fstat', side_effect=info))
        self.chown = self.enterContext(patch.object(h.os, 'fchown', create=True,
            side_effect=lambda fd, uid, gid: self.rows[self.handles[fd]].update(uid=uid, gid=gid)))
        self.enterContext(patch.object(h.os, 'fchmod', create=True,
            side_effect=lambda fd, mode: self.rows[self.handles[fd]].update(mode=mode)))
        self.enterContext(patch.object(h.os, 'fsync'))
        self.close = self.enterContext(patch.object(h.os, 'close'))

    def test_freeze_changes_only_owner_and_modes_preserves_hashes_and_inodes(self):
        result = h.freeze_files(self.before)
        for name, row in result.items():
            self.assertEqual(row, self.before[name] | {'uid': 0, 'gid': 0})
        self.assertEqual(self.close.call_count, 2)

    def test_changed_inventory_blocks_freeze(self):
        self.rows[str(h.STATE / 'reports.sqlite3')]['sha256'] = 'c' * 64
        with self.assertRaisesRegex(RuntimeError, 'before freeze'):
            h.freeze_files(self.before)
        self.open.assert_not_called()

    def test_handle_link_or_inode_mismatch_cannot_modify_another_object(self):
        first = next(iter(self.rows.values()))
        self.stat.side_effect = None
        self.stat.return_value = SimpleNamespace(st_dev=first['device'], st_ino=first['inode'],
            st_uid=first['uid'], st_gid=first['gid'], st_mode=stat.S_IFREG | first['mode'], st_nlink=2)
        with self.assertRaisesRegex(RuntimeError, 'handle mismatch'):
            h.freeze_files(self.before)
        self.chown.assert_not_called()
        self.close.assert_called_once()

    def test_hash_difference_after_freeze_is_not_success(self):
        changed = {name: row | {'uid': 0, 'gid': 0, 'sha256': 'd' * 64} for name, row in self.before.items()}
        self.inventory.side_effect = [self.before, changed]
        with self.assertRaisesRegex(RuntimeError, 'changed bytes'):
            h.freeze_files(self.before)


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.ctx = context()
        self.enterContext(patch.object(h, 'verify_common', return_value='account'))
        self.empty = self.enterContext(patch.object(h, 'empty_state'))
        self.process = self.enterContext(patch.object(h.subprocess, 'run'))

    def test_closed_check_cannot_import_writer_or_prepare(self):
        with patch.object(h, 'read_gate', return_value=False):
            h.verify_ready(self.ctx, {}, gate_enabled=False)
        self.ctx.checks['verify_gate_rejection'].assert_called_once_with('account')
        self.process.assert_not_called()

    def test_open_readiness_uses_dropped_identity_and_read_only_probe(self):
        value = {'uid': h.UID, 'euid': h.UID, 'gid': h.GID, 'egid': h.GID,
                 'groups': [h.GID], 'publisher_loaded': True}
        self.process.return_value = response(json.dumps(value))
        with patch.object(h, 'read_gate', return_value=True):
            h.verify_ready(self.ctx, {}, gate_enabled=True)
        self.assertEqual(self.process.call_args.kwargs['user'], h.UID)
        self.assertEqual(self.process.call_args.kwargs['extra_groups'], [h.GID])
        self.assertEqual(self.process.call_args.args[0][-1], h.READY_PROBE)
        self.empty.assert_called_once_with(self.ctx)

    def test_probe_errors_and_wrong_identity_never_pass(self):
        for result in (response('{}'), response(stderr='error', returncode=1),
                       response('x' * 3000)):
            with self.subTest(result=result), patch.object(h, 'read_gate', return_value=True):
                self.process.return_value = result
                with self.assertRaises(RuntimeError):
                    h.verify_ready(self.ctx, {}, gate_enabled=True)


class EntryPointTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(redirect_stdout(io.StringIO()))
        self.enterContext(patch.object(h.sys, 'platform', 'linux'))
        self.enterContext(patch.object(h.os, 'geteuid', return_value=0, create=True))
        self.enterContext(patch.object(h.sys, 'flags', SimpleNamespace(isolated=1)))
        self.enterContext(patch.object(h.sys, 'dont_write_bytecode', True))
        self.enterContext(patch.object(h.os, 'umask'))
        self.release = self.enterContext(patch.object(h, 'validate_release', return_value=HASH))
        self.lock = self.enterContext(patch.object(h, 'procedure_lock', side_effect=nullcontext))
        self.ctx = context()
        self.load = self.enterContext(patch.object(h, 'load_context', return_value=self.ctx))
        self.preflight = self.enterContext(patch.object(h, 'preflight'))
        self.activate = self.enterContext(patch.object(h, 'activate'))
        self.rollback = self.enterContext(patch.object(h, 'rollback'))
        self.gate = self.enterContext(patch.object(h, 'write_gate'))

    def test_check_only_checks(self):
        h.main(['--check'])
        self.preflight.assert_called_once_with(self.ctx)
        self.activate.assert_not_called()
        self.rollback.assert_not_called()
        self.gate.assert_not_called()

    def test_activation_needs_separate_explicit_flag(self):
        with self.assertRaises(RuntimeError):
            h.main(['--activate'])
        self.release.assert_not_called()
        self.activate.assert_not_called()
        h.main(['--activate', '--approve-public-publishing'])
        self.activate.assert_called_once_with(self.ctx)

    def test_enabling_flag_rejected_for_other_modes(self):
        for mode in ('--check', '--rollback', '--close-gate'):
            with self.subTest(mode=mode), self.assertRaises(RuntimeError):
                h.main([mode, '--approve-public-publishing'])
        self.release.assert_not_called()

    def test_gate_only_stop_does_not_depend_on_old_checks_or_change_storage(self):
        self.load.side_effect = RuntimeError('changed unrelated dependencies')
        h.main(['--close-gate'])
        self.load.assert_not_called()
        self.gate.assert_called_once_with(False)
        self.rollback.assert_not_called()
        self.activate.assert_not_called()

    def test_concurrent_operation_refuses_before_gate_or_context(self):
        self.lock.side_effect = BlockingIOError('another procedure')
        with self.assertRaises(BlockingIOError):
            h.main(['--close-gate'])
        self.gate.assert_not_called()
        self.load.assert_not_called()


if __name__ == '__main__':
    unittest.main()
