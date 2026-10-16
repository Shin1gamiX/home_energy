"""Synthetic single-use interval exception tests; no live runtime or network."""
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from insights_broker import encode
import insights_recurring_exception as exception
import insights_recurring_recovery as recovery
import insights_recurring_revision as revision
import insights_recurring_run as runner
import insights_recurring_startup as startup
import insights_recurring_store as storage
from test_insights_recurring import context, grant
from test_insights_recurring_recovery import RecoveryFixture, FIXED_BUNDLE, DiscoveryOrderingTests


EXCEPTION_BUNDLE = 'd' * 64


class ExceptionFixture(RecoveryFixture):
    def setUp(self):
        super().setUp()
        self.activate_fixture()
        self.clock.return_value += 60
        tip = storage.digest(self.files.read(storage.filename('terminal', recovery.FAILED_RUN)))
        for name, value in {'PREVIOUS_BUNDLE': FIXED_BUNDLE, 'PREVIOUS_RUN': recovery.FAILED_RUN,
                'EXCEPTION_RUN': f'{int(recovery.FAILED_RUN) + 1:06d}', 'PREVIOUS_TIP': tip,
                'PREVIOUS_RECORD_PINS': {name: recovery.record_hash(self.records[name])
                    for name in (startup.RECOVERY_REVIEW, startup.RECOVERY_ACTIVATION)}}.items():
            self.enterContext(patch.object(exception, name, value))
        now = int(self.clock())
        self.authorization = exception.authorization_body(EXCEPTION_BUNDLE, now, now + 1800,
                                                           now + 12 * 3600, revision)
        self.records[exception.AUTHORIZATION] = self.authorization
        self.exception_type = exception.exception_store(storage, revision, recovery, EXCEPTION_BUNDLE,
            self.approval, self.review, self.authorization)

    def new_exception(self, run_id=None):
        store = self.exception_type(EXCEPTION_BUNDLE, run_id, files=self.files)
        self.addCleanup(store.close)
        return store

    def candidate(self):
        ctx = context()
        ctx.bundle, ctx.original_storage = EXCEPTION_BUNDLE, storage
        ctx.revision, ctx.recovery, ctx.exception = revision, recovery, exception
        return ctx

    def bind(self, run_id=None, token=None, role=None):
        ctx = self.candidate()
        with patch.object(storage, 'Files', return_value=self.files):
            result = exception.bind(ctx, self.reader, active_run=run_id, token=token, role=role)
        return ctx, result

    def reserve_early(self, *, claimed=True):
        store = self.new_exception()
        reserved = store.reserve_exception()
        if claimed:
            store.claim_worker(reserved['token'])
        return store, reserved

    def finish_success(self, store, reserved):
        store.record('runtime_verified')
        store.arm(reserved['token'])
        store.start_model(reserved['token'])
        self.complete(store, reserved['token'])
        store.close()


class ExceptionAuthorizationTests(ExceptionFixture):
    def test_original_grant_and_evidence_are_unchanged_by_binding(self):
        before = self.bodies()
        ctx, status = self.bind()
        self.assertTrue(ctx.repair_active)
        self.assertEqual(status['attempts_consumed'], 2)
        self.assertEqual(status['remaining_attempts'], grant()['max_runs'] - 2)
        self.assertEqual(before, self.bodies())

    def test_schema_types_bounds_and_exact_identity(self):
        for field in self.authorization:
            bad = dict(self.authorization)
            bad.pop(field)
            with self.subTest(missing=field), self.assertRaises(ValueError):
                exception.validate_authorization(bad, EXCEPTION_BUNDLE, grant(), revision, storage)
        now = int(self.clock())
        for change in ({'schema_version': True}, {'issued_unix': True}, {'expires_unix': True},
                {'max_attempts': True}, {'max_reports': True}, {'max_model_turns': True},
                {'retries_allowed': 0}, {'schedule_changed': 0}, {'previous_attempt_remains_consumed': 1},
                {'max_attempts': 2}, {'run_id': '000099'}, {'previous_terminal_sha256': '0' * 64},
                {'bundle_sha256': FIXED_BUNDLE}, {'original_authorization_sha256': 'f' * 64},
                {'issued_unix': now + 1, 'not_before_unix': now + 1},
                {'not_before_unix': now - 1}, {'expires_unix': now + 1801},
                {'expires_unix': now + storage.MAX_SECONDS},
                {'next_scheduled_unix': now + storage.INTERVAL_SECONDS}, {'extra': 'untrusted'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                exception.validate_authorization(self.authorization | change, EXCEPTION_BUNDLE,
                                                 grant(), revision, storage)

    def test_missing_authorization_does_not_write(self):
        self.records[exception.AUTHORIZATION] = None
        before = self.bodies()
        with self.assertRaisesRegex(ValueError, 'authorization_required'):
            self.bind()
        self.assertEqual(before, self.bodies())

    def test_every_previous_sidecar_is_still_hash_bound(self):
        for name in (recovery.PREVIOUS_RECORD_PINS | recovery.FAILURE_LOG_PINS | exception.PREVIOUS_RECORD_PINS):
            old = self.records[name]
            self.records[name] = old | {'changed': True}
            try:
                with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'prior_records_changed'):
                    self.bind()
            finally:
                self.records[name] = old

    def test_old_grant_expiry_and_count_cannot_be_extended_or_replaced(self):
        self.authorize(max_runs=119)
        with self.assertRaisesRegex(ValueError, 'repair_binding_changed'):
            self.new_exception().reserve_exception()
        self.authorize()
        self.clock.return_value = grant()['expires_unix']
        with self.assertRaisesRegex(ValueError, 'authorization_expired'):
            self.new_exception().reserve_exception()

    def test_sidecar_expiry_does_not_invalidate_ordinary_future_operation(self):
        self.clock.return_value = self.authorization['expires_unix'] + 1
        self.assertTrue(self.bind()[0].repair_active)
        with self.assertRaisesRegex(ValueError, 'exception_expired'):
            self.new_exception().reserve_exception()
        self.assertNotIn(storage.filename('receipt', exception.EXCEPTION_RUN), self.files.names())

    def test_one_fixed_private_read_name_and_error_codes_only(self):
        self.assertEqual(startup.INTERVAL_EXCEPTION, exception.AUTHORIZATION)
        self.assertIn(exception.AUTHORIZATION, startup.READABLE_RECORDS)
        self.assertIn("'" + exception.AUTHORIZATION + "'", startup.PRIVATE_IO_SCRIPT)
        self.assertIn('run-exception', startup.MODES)
        for code in ('recurring_exception_expired', 'recurring_exception_consumed'):
            self.assertEqual(startup.error_record(ValueError(code), 'run-exception', 'authorization')['code'], code)
        with patch.object(startup, 'private_io') as io_call:
            with self.assertRaises(ValueError):
                startup.read_record('../interval-exception-20261016-authorization.json')
            io_call.assert_not_called()


class ExceptionLifecycleTests(ExceptionFixture):
    def test_ordinary_reservation_still_obeys_six_hour_floor(self):
        before = self.bodies()
        self.assertEqual(self.new_exception().reserve()['status'], 'not_due')
        self.assertEqual(before, self.bodies())

    def test_early_attempt_is_quota_consuming_and_preserves_all_old_bytes(self):
        before = self.bodies()
        store, reserved = self.reserve_early()
        self.assertEqual(reserved['run_id'], exception.EXCEPTION_RUN)
        self.assertEqual(store.read('receipt')['previous_terminal_sha256'], exception.PREVIOUS_TIP)
        self.assertEqual(store.read('receipt')['authorization_sha256'], storage.digest(grant()))
        self.finish_success(store, reserved)
        status = self.bind()[1]
        self.assertTrue(status['exception_consumed'])
        self.assertEqual(status['attempts_consumed'], 3)
        self.assertEqual(status['remaining_attempts'], grant()['max_runs'] - 3)
        self.assertEqual({name: self.bodies()[name] for name in before}, before)
        self.assertEqual(self.files.read(storage.filename('terminal', recovery.FAILED_RUN))['outcome'], 'uncertain')

    def test_pending_or_uncertain_attempt_is_never_retried(self):
        store, reserved = self.reserve_early()
        with self.assertRaisesRegex(ValueError, 'busy'):
            self.new_exception().reserve_exception()
        with self.assertRaisesRegex(ValueError, 'prior_run_needs_review'):
            store.finish(reserved['token'], 'uncertain')
        store.close()
        before = self.bodies()
        for method in ('reserve', 'reserve_exception'):
            attempted = self.new_exception()
            with self.subTest(method=method), self.assertRaisesRegex(ValueError, 'prior_run_needs_review'):
                getattr(attempted, method)()
            attempted.close()
        self.assertEqual(before, self.bodies())

    def test_completed_exception_cannot_be_reused(self):
        store, reserved = self.reserve_early()
        self.finish_success(store, reserved)
        before = self.bodies()
        with self.assertRaisesRegex(ValueError, 'exception_consumed'):
            self.new_exception().reserve_exception()
        self.assertEqual(before, self.bodies())

    def test_history_after_exception_expiry_and_next_scheduled_run_remain_valid(self):
        store, reserved = self.reserve_early()
        self.finish_success(store, reserved)
        self.clock.return_value = self.authorization['expires_unix'] + 1
        self.assertEqual(self.bind()[1]['attempts_consumed'], 3)
        held = self.new_exception()
        self.assertEqual(held.reserve()['status'], 'not_due')
        held.close()
        self.clock.return_value = self.authorization['next_scheduled_unix']
        scheduled = self.new_exception()
        next_run = scheduled.reserve()
        self.assertEqual(next_run['run_id'], f'{int(exception.EXCEPTION_RUN) + 1:06d}')
        scheduled.claim_worker(next_run['token'])
        self.finish_success(scheduled, next_run)
        self.assertEqual(self.bind()[1]['attempts_consumed'], 4)

    def test_unused_exception_does_not_prevent_later_regular_attempt(self):
        self.clock.return_value += storage.INTERVAL_SECONDS
        store = self.new_exception()
        reserved = store.reserve()
        store.claim_worker(reserved['token'])
        self.finish_success(store, reserved)
        self.assertEqual(self.bind()[1]['attempts_consumed'], 3)
        with self.assertRaisesRegex(ValueError, 'exception_consumed'):
            self.new_exception().reserve_exception()

    def test_prefix_corruption_and_orphan_artifacts_block_both_modes(self):
        path = self.root / storage.filename('audit', recovery.FAILED_RUN)
        path.write_bytes(path.read_bytes() + b'\n')
        for method in ('reserve', 'reserve_exception'):
            store = self.new_exception()
            with self.subTest(method=method), self.assertRaises(ValueError):
                getattr(store, method)()
            store.close()

    def test_no_overlap_or_spacing_exception_for_other_run_or_wrong_tip(self):
        store, reserved = self.reserve_early()
        receipt = store.read('receipt')
        history = {'count': int(exception.PREVIOUS_RUN), 'created': receipt['created_unix'] - 1,
                   'previous': exception.PREVIOUS_TIP}
        self.assertTrue(exception.spacing_allowed(receipt, history, self.authorization, storage))
        for change in ({'run_id': '000099'}, {'previous_terminal_sha256': '0' * 64},
                {'created_unix': self.authorization['not_before_unix'] - 1},
                {'created_unix': self.authorization['expires_unix']}):
            with self.subTest(change=change):
                self.assertFalse(exception.spacing_allowed(receipt | change, history, self.authorization, storage))


class ExceptionDiscoveryTests(ExceptionFixture):
    discovery = DiscoveryOrderingTests.discovery

    def test_actual_mcp_entry_discovers_before_arm_without_transport(self):
        store, reserved = self.reserve_early()
        before = self.bodies()
        with patch('insights_production_trial_session.connection.load_destination') as transport:
            rows = self.discovery(self.candidate(), reserved)
            transport.assert_not_called()
        self.assertEqual({tool['name'] for tool in rows[1]['result']['tools']}, {'prepare', 'publish'})
        self.assertEqual(before, self.bodies())

    def test_real_prepare_is_blocked_before_arm_and_before_model(self):
        store, reserved = self.reserve_early()
        with patch('insights_production_trial_session.connection.load_destination') as transport:
            with self.assertRaisesRegex(ValueError, 'audit_failed'):
                self.discovery(self.candidate(), reserved, call=True)
            transport.assert_not_called()
        store.arm(reserved['token'])
        with patch('insights_production_trial_session.connection.load_destination') as transport:
            rows = self.discovery(self.candidate(), reserved, call=True)
            transport.assert_not_called()
        self.assertIn('error', rows[-1])
        self.assertNotIn(storage.filename('model', reserved['run_id']), self.files.names())

    def test_worker_and_mcp_handoffs_validate_pending_exception(self):
        store, reserved = self.reserve_early(claimed=False)
        self.assertTrue(self.bind(reserved['run_id'], reserved['token'], 'worker')[0].repair_active)
        with self.assertRaises((ValueError, FileNotFoundError)):
            self.bind(reserved['run_id'], reserved['token'], 'mcp')
        store.claim_worker(reserved['token'])
        self.assertTrue(self.bind(reserved['run_id'], reserved['token'], 'mcp')[0].repair_active)
        with self.assertRaisesRegex(ValueError, 'token_refused'):
            self.bind(reserved['run_id'], 'a' * 64, 'mcp')

    def test_early_unapproved_receipt_and_expired_handoff_are_rejected(self):
        store, reserved = self.reserve_early()
        path = self.root / storage.filename('receipt', reserved['run_id'])
        value = json.loads(path.read_bytes())
        path.write_bytes(encode(value | {'created_unix': self.authorization['issued_unix'] - 1}) + b'\n')
        with self.assertRaisesRegex(ValueError, 'receipt_changed'):
            self.bind(reserved['run_id'], reserved['token'], 'worker')
        path.write_bytes(encode(value) + b'\n')
        self.clock.return_value += storage.MAX_SECONDS
        with self.assertRaisesRegex(ValueError, 'run_inactive'):
            self.bind(reserved['run_id'], reserved['token'], 'mcp')


class ExceptionRunnerTests(unittest.TestCase):
    def test_explicit_mode_selection_never_changes_scheduled_mode(self):
        for mode, enabled in (('run-due', False), ('run-exception', True)):
            ctx = context()
            ctx.repair_active = True
            with self.subTest(mode=mode), patch.object(runner, 'bootstrap', return_value=ctx), \
                    patch.object(runner, 'load_support', return_value=Mock()), \
                    patch.object(runner, 'supervise', return_value={}) as supervisor, \
                    patch('sys.argv', ['runner', '--' + mode]), patch('sys.stdout', io.StringIO()):
                runner.main()
            supervisor.assert_called_once_with(ctx, interval_exception=enabled)

    def test_supervisor_makes_only_one_selected_reservation_and_never_retries(self):
        for enabled in (False, True):
            ctx = context()
            store = Mock()
            ctx.storage = SimpleNamespace(Store=Mock(return_value=store))
            method = store.reserve_exception if enabled else store.reserve
            method.side_effect = ValueError('synthetic_stop')
            with self.subTest(exception=enabled), patch.object(runner.subprocess, 'Popen') as process, \
                    patch.object(ctx.trial, 'close_worker'), self.assertRaisesRegex(ValueError, 'synthetic_stop'):
                runner.supervise(ctx, interval_exception=enabled)
            method.assert_called_once_with()
            (store.reserve if enabled else store.reserve_exception).assert_not_called()
            process.assert_not_called()
            store.finish.assert_not_called()

    def test_no_schedule_edits_or_model_transport_in_exception_policy(self):
        source = Path(exception.__file__).read_text(encoding='utf-8')
        for forbidden in ('subprocess', 'Popen', 'Start-ScheduledTask', 'Set-ScheduledTask',
                          '.unlink(', '.write_bytes(', '.write_text(', 'INTERVAL_SECONDS ='):
            self.assertNotIn(forbidden, source)


if __name__ == '__main__':
    unittest.main()
