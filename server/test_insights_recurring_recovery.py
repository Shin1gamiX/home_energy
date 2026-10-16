"""Synthetic startup-order/recovery tests; no Codex, SSH, model or live store."""
import hashlib
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from insights_broker import encode
import insights_recurring_recovery as recovery
import insights_recurring_revision as revision
import insights_recurring_run as runner
import insights_recurring_startup as startup
import insights_recurring_store as storage
from test_insights_recurring import context, grant
from test_insights_recurring_revision import NEW_BUNDLE, RepairFixture


FIXED_BUNDLE = 'f' * 64


class RecoveryFixture(RepairFixture):
    def setUp(self):
        super().setUp()
        failed = self.repaired()
        reservation = failed.reserve()
        failed.claim_worker(reservation['token'])
        self.clock.return_value += 5
        with self.assertRaisesRegex(ValueError, 'prior_run_needs_review'):
            failed.finish(reservation['token'], 'uncertain')
        failed.close()
        pins = {kind: hashlib.sha256((self.root / storage.filename(kind, reservation['run_id'])).read_bytes()).hexdigest()
                for kind in ('receipt', 'worker', 'audit', 'terminal')}
        self.old_activation = {'schema_version': 1, 'scope': 'recurring_runtime_activation',
            'approval_sha256': storage.digest(self.approval), 'bundle_sha256': NEW_BUNDLE,
            'activated_unix': self.approval['approved_unix']}
        self.records = {startup.APPROVAL: self.approval, startup.ACTIVATION: self.old_activation}
        logs = {name: {'scope': 'synthetic', 'index': index}
                for index, name in enumerate(recovery.FAILURE_LOG_PINS)}
        self.records.update(logs)
        for name, value in {'PREVIOUS_BUNDLE': NEW_BUNDLE, 'FAILED_RUN': reservation['run_id'],
                'FAILURE_PINS': pins, 'FAILURE_LOG_PINS': {name: recovery.record_hash(value) for name, value in logs.items()},
                'PREVIOUS_RECORD_PINS': {name: recovery.record_hash(self.records[name])
                                        for name in (startup.APPROVAL, startup.ACTIVATION)}}.items():
            self.enterContext(patch.object(recovery, name, value))
        self.clock.return_value += 1
        self.review = recovery.review_body(FIXED_BUNDLE, int(self.clock()), revision)
        self.records[startup.RECOVERY_REVIEW] = self.review
        self.records[startup.RECOVERY_ACTIVATION] = None
        self.reader = SimpleNamespace(APPROVAL=startup.APPROVAL, ACTIVATION=startup.ACTIVATION,
            RECOVERY_REVIEW=startup.RECOVERY_REVIEW, RECOVERY_ACTIVATION=startup.RECOVERY_ACTIVATION,
            read_record=self.records.get)
        self.store_type = recovery.recovered_store(storage, revision, FIXED_BUNDLE, self.approval, self.review)

    def recovered(self, run_id=None):
        store = self.store_type(FIXED_BUNDLE, run_id, files=self.files)
        self.addCleanup(store.close)
        return store

    def candidate(self):
        ctx = context()
        ctx.bundle, ctx.original_storage = FIXED_BUNDLE, storage
        ctx.revision, ctx.recovery = revision, recovery
        # These tests exercise the preserved recovery policy independently.
        ctx.exception = recovery
        return ctx

    def bind(self, run_id=None, token=None, role=None):
        ctx = self.candidate()
        with patch.object(storage, 'Files', return_value=self.files):
            result = recovery.bind(ctx, self.reader, active_run=run_id, token=token, role=role)
        return ctx, result

    def activate_fixture(self):
        self.records[startup.RECOVERY_ACTIVATION] = {
            'schema_version': 1, 'scope': 'recurring_recovery_activation',
            'review_sha256': storage.digest(self.review), 'bundle_sha256': FIXED_BUNDLE,
            'activated_unix': int(self.clock())}

    def next_run(self, *, claimed=True):
        self.clock.return_value += storage.INTERVAL_SECONDS
        store = self.recovered()
        reserved = store.reserve()
        if claimed:
            store.claim_worker(reserved['token'])
        return store, reserved


class RecoveryHistoryTests(RecoveryFixture):
    def test_review_keeps_all_bytes_grant_and_consumed_failure(self):
        before = self.bodies()
        ctx, result = self.bind()
        self.assertFalse(ctx.repair_active)
        self.assertEqual(result['attempts_consumed'], 2)
        self.assertEqual(result['completed_attempts'], 1)
        self.assertEqual(result['reviewed_failed_attempts'], 1)
        self.assertEqual(result['remaining_attempts'], grant()['max_runs'] - 2)
        self.assertEqual(result['authorization_expires_unix'], grant()['expires_unix'])
        self.assertEqual(before, self.bodies())
        self.assertEqual(self.recovered().history(grant())['count'], 2)

    def test_new_run_links_to_real_uncertain_terminal_and_counts_old_attempt(self):
        before = self.bodies()
        failed_terminal = self.files.read(storage.filename('terminal', recovery.FAILED_RUN))
        store, reserved = self.next_run()
        self.assertEqual(reserved['run_id'], '000003')
        self.assertEqual(store.read('receipt')['previous_terminal_sha256'], storage.digest(failed_terminal))
        self.assertEqual(store.read('receipt')['bundle_sha256'], FIXED_BUNDLE)
        self.assertEqual(store.read('receipt')['authorization_sha256'], storage.digest(grant()))
        store.record('runtime_verified')
        store.arm(reserved['token'])
        store.start_model(reserved['token'])
        self.complete(store, reserved['token'])
        store.close()
        self.assertEqual(self.recovered().history(grant())['count'], 3)
        self.assertEqual({name: self.bodies()[name] for name in before}, before)
        self.assertEqual(self.files.read(storage.filename('terminal', recovery.FAILED_RUN))['outcome'], 'uncertain')

    def test_six_hour_floor_still_applies_to_reviewed_failure(self):
        before = self.bodies()
        store = self.recovered()
        self.assertEqual(store.reserve()['status'], 'not_due')
        self.assertEqual(before, self.bodies())

    def test_later_uncertain_run_is_not_automatically_recovered(self):
        store, reserved = self.next_run()
        with self.assertRaisesRegex(ValueError, 'prior_run_needs_review'):
            store.finish(reserved['token'], 'uncertain')
        store.close()
        before = self.bodies()
        with self.assertRaisesRegex(ValueError, 'prior_run_needs_review'):
            self.recovered().history(grant())
        self.assertEqual(before, self.bodies())

    def test_every_failed_artifact_is_hash_bound(self):
        for kind in recovery.FAILURE_PINS:
            path = self.root / storage.filename(kind, recovery.FAILED_RUN)
            original = path.read_bytes()
            path.write_bytes(original + b'\n')
            try:
                with self.subTest(kind=kind), self.assertRaises((ValueError, FileNotFoundError)):
                    self.recovered().history(grant())
            finally:
                path.write_bytes(original)

    def test_added_arm_or_model_or_publication_evidence_prevents_recovery(self):
        for kind in ('armed', 'model', 'claim', 'analysis', 'submission', 'result', 'diagnostic'):
            path = self.root / storage.filename(kind, recovery.FAILED_RUN)
            path.write_bytes(encode({'synthetic': True}) + b'\n')
            try:
                with self.subTest(kind=kind), self.assertRaises(ValueError):
                    self.recovered().history(grant())
            finally:
                path.unlink()

    def test_removing_failed_attempt_does_not_reset_numbering(self):
        for kind in recovery.FAILURE_PINS:
            (self.root / storage.filename(kind, recovery.FAILED_RUN)).unlink()
        with self.assertRaisesRegex(ValueError, 'prior_run_needs_review'):
            self.recovered().history(grant())

    def test_successful_history_and_chain_still_verified(self):
        path = self.root / storage.filename('submission', '000001')
        value = json.loads(path.read_bytes())
        value['summary']['en'] += ' Synthetic tampering.'
        path.write_bytes(encode(value) + b'\n')
        with self.assertRaises(ValueError):
            self.recovered().history(grant())

    def test_grant_expiry_limits_and_identity_are_preserved(self):
        before = self.bodies()
        self.clock.return_value = grant()['expires_unix']
        with self.assertRaisesRegex(ValueError, 'authorization_expired'):
            self.recovered()._grant()
        self.assertEqual(before, self.bodies())
        self.clock.return_value = self.review['approved_unix']
        self.authorize(max_runs=119)
        with self.assertRaisesRegex(ValueError, 'repair_binding_changed'):
            self.recovered()._grant()


class RecoveryApprovalTests(RecoveryFixture):
    def test_review_has_exact_schema_types_and_bundle(self):
        for field in self.review:
            bad = dict(self.review)
            bad.pop(field)
            with self.subTest(missing=field), self.assertRaises(ValueError):
                recovery.validate_review(bad, FIXED_BUNDLE, grant(), revision)
        for changes in ({'schema_version': True}, {'approved_unix': True}, {'attempt_consumed': 1},
                {'requires_separate_activation': 1}, {'approved_unix': int(self.clock()) + 1},
                {'bundle_sha256': NEW_BUNDLE}, {'failed_run_id': '000099'}, {'extra': 'untrusted'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                recovery.validate_review(self.review | changes, FIXED_BUNDLE, grant(), revision)

    def test_review_is_required_and_never_implies_activation(self):
        self.assertFalse(self.bind()[0].repair_active)
        self.records[startup.RECOVERY_REVIEW] = None
        before = self.bodies()
        with self.assertRaisesRegex(ValueError, 'review_required'):
            self.bind()
        self.assertEqual(before, self.bodies())

    def test_activation_is_bound_to_review_bundle_and_time(self):
        self.activate_fixture()
        self.assertTrue(self.bind()[0].repair_active)
        good = self.records[startup.RECOVERY_ACTIVATION]
        for changes in ({'schema_version': True}, {'activated_unix': True}, {'scope': 'other'},
                {'bundle_sha256': NEW_BUNDLE}, {'review_sha256': '0' * 64},
                {'activated_unix': int(self.clock()) + 1}, {'extra': True}):
            self.records[startup.RECOVERY_ACTIVATION] = good | changes
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, 'activation_changed'):
                self.bind()

    def test_old_approval_activation_and_error_logs_must_stay_identical(self):
        for name in (*recovery.PREVIOUS_RECORD_PINS, *recovery.FAILURE_LOG_PINS):
            original = self.records[name]
            self.records[name] = original | {'unexpected': True}
            try:
                with self.subTest(name=name), self.assertRaises(ValueError):
                    self.bind()
            finally:
                self.records[name] = original


class DiscoveryOrderingTests(RecoveryFixture):
    def discovery(self, ctx, reserved, *, call=False):
        frames = [
            {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
             'params': {'protocolVersion': '2025-06-18', 'capabilities': {}, 'clientInfo': {'name': 'synthetic'}}},
            {'jsonrpc': '2.0', 'method': 'notifications/initialized', 'params': {}},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list', 'params': {}},
        ]
        if call:
            frames.append({'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
                           'params': {'name': 'prepare', 'arguments': {}}})
        source = io.BytesIO(b''.join(encode(frame) + b'\n' for frame in frames))
        destination = io.BytesIO()
        with patch.object(runner, 'bootstrap', return_value=ctx), \
                patch.object(runner, 'load_support', return_value=self.reader), \
                patch.object(storage, 'Files', return_value=self.files), \
                patch('sys.argv', ['runner', '--mcp-report']), \
                patch('sys.stdin', SimpleNamespace(buffer=source)), \
                patch('sys.stdout', SimpleNamespace(buffer=destination)), \
                patch.dict(os.environ, {runner.TOKEN_ENV: reserved['token'], runner.RUN_ENV: reserved['run_id']}):
            runner.main()
        return [json.loads(line) for line in destination.getvalue().splitlines()]

    def test_actual_main_and_mcp_discovery_succeed_before_arm_without_writes(self):
        self.activate_fixture()
        store, reserved = self.next_run()
        before = self.bodies()
        rows = self.discovery(self.candidate(), reserved)
        self.assertEqual([row['id'] for row in rows], [1, 2])
        self.assertTrue(all('error' not in row for row in rows))
        self.assertEqual({tool['name'] for tool in rows[1]['result']['tools']}, {'prepare', 'publish'})
        self.assertEqual(before, self.bodies())
        self.assertNotIn(storage.filename('armed', reserved['run_id']), self.files.names())

    def test_prepare_before_arm_terminates_without_transport_or_new_evidence(self):
        self.activate_fixture()
        store, reserved = self.next_run()
        before = self.bodies()
        with patch('insights_production_trial_session.connection.load_destination') as destination:
            # No armed token is available even for rejection auditing: fail closed.
            with self.assertRaisesRegex(ValueError, '^recurring_audit_failed$'):
                self.discovery(self.candidate(), reserved, call=True)
            destination.assert_not_called()
        self.assertEqual(before, self.bodies())

    def test_prepare_before_model_is_rejected_without_transport(self):
        self.activate_fixture()
        store, reserved = self.next_run()
        store.arm(reserved['token'])
        with patch('insights_production_trial_session.connection.load_destination') as destination:
            rows = self.discovery(self.candidate(), reserved, call=True)
            destination.assert_not_called()
        self.assertIn('error', rows[-1])
        self.assertNotIn(storage.filename('model', reserved['run_id']), self.files.names())
        self.assertNotIn(storage.filename('claim', reserved['run_id']), self.files.names())

    def test_worker_marker_and_correct_token_are_required_for_discovery(self):
        self.activate_fixture()
        store, reserved = self.next_run(claimed=False)
        before = self.bodies()
        with self.assertRaises((ValueError, FileNotFoundError)):
            self.discovery(self.candidate(), reserved)
        self.assertEqual(before, self.bodies())
        store.claim_worker(reserved['token'])
        before = self.bodies()
        with self.assertRaisesRegex(ValueError, 'token_refused'):
            self.discovery(self.candidate(), reserved | {'token': 'a' * 64})
        self.assertEqual(self.bodies(), before)

    def test_failed_run_or_malformed_handoff_cannot_be_reused(self):
        before = self.bodies()
        for run_id, token, role in ((recovery.FAILED_RUN, 'a' * 64, 'mcp'),
                (None, None, 'mcp'), ('000003', None, None), ('000003', 'a' * 64, 'arbitrary')):
            with self.subTest(role=role), self.assertRaises(ValueError):
                self.bind(run_id, token, role)
        self.assertEqual(before, self.bodies())

    def test_actual_worker_initializes_helper_then_arms_not_the_other_way_around(self):
        self.activate_fixture()
        store, reserved = self.next_run(claimed=False)
        ctx = self.candidate()
        with patch.object(storage, 'Files', return_value=self.files):
            recovery.bind(ctx, self.reader, active_run=reserved['run_id'], token=reserved['token'], role='worker')
        observed = []
        fixture = self
        class StopAfterArming(Exception):
            pass
        class Session:
            def __init__(self, config): pass
            def gates(self, config):
                fixture.assertNotIn(storage.filename('armed', reserved['run_id']), fixture.files.names())
                fixture.discovery(fixture.candidate(), reserved)
                observed.append('discovery_before_arm')
            def generate_once(self, worker_store, token):
                fixture.assertEqual(worker_store.read('armed'), worker_store.read('receipt'))
                observed.append('armed_after_discovery')
                raise StopAfterArming()
            def close(self): pass
        with patch.object(runner, 'session_class', return_value=Session), \
                patch.object(storage, 'Files', return_value=self.files), \
                patch.dict(os.environ, {runner.WORKER_ENV: 'recurring-owned-worker-v1'}), \
                self.assertRaises(StopAfterArming):
            runner.worker(ctx, reserved['token'], reserved['run_id'])
        self.assertEqual(observed, ['discovery_before_arm', 'armed_after_discovery'])
        self.assertNotIn(storage.filename('model', reserved['run_id']), self.files.names())


class RecoverySourceTests(unittest.TestCase):
    def test_all_new_record_names_are_explicit_in_both_reader_allowlists(self):
        for name in (startup.RECOVERY_REVIEW, startup.RECOVERY_ACTIVATION, *recovery.FAILURE_LOG_PINS):
            self.assertIn(name, startup.READABLE_RECORDS)
            self.assertIn("'" + name + "'", startup.PRIVATE_IO_SCRIPT)
        with patch.object(startup, 'private_io') as private:
            with self.assertRaises(ValueError):
                startup.read_record('id_ed25519')
            private.assert_not_called()

    def test_recovery_has_no_persistence_or_execution_implementation(self):
        source = Path(recovery.__file__).read_text(encoding='utf-8')
        for forbidden in ('subprocess', 'Popen', '.write_bytes(', '.write_text(', '.unlink(', 'Start-ScheduledTask'):
            self.assertNotIn(forbidden, source)


if __name__ == '__main__':
    unittest.main()
