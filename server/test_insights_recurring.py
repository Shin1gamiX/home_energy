"""Synthetic recurring-run tests. No real runtime, SSH or model call is made."""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import queue
import subprocess
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from insights_broker import encode
import insights_production_launcher as candidate
import insights_production_trial_run as trial
import insights_production_trial_session as reports
import insights_recurring_run as runner
import insights_recurring_store as storage
import insights_staging_launcher as runtime
from test_insights_production_launcher import config as candidate_config, thread as candidate_thread
from test_insights_production_mcp import fixture, prose, NOW
import test_insights_production_mcp as synthetic


BUNDLE = 'a' * 64


def grant(**changes):
    return {'schema_version': 1, 'scope': 'production', 'purpose': 'recurring_public_reports',
        'bundle_sha256': BUNDLE, 'issued_unix': NOW, 'not_before_unix': NOW,
        'expires_unix': NOW + storage.MAX_GRANT_SECONDS, 'interval_seconds': storage.INTERVAL_SECONDS,
        'max_runs': storage.MAX_RUNS, 'max_model_turns_per_run': 1, 'max_reports_per_run': 1,
        'server_activation_verified': True, **changes}


class TempFiles(storage.Files):
    """Portable byte-level fixtures; OS ACL/handle assertions are a separate probe."""
    def __init__(self, root):
        self.root = root
        self.locked = False

    def names(self):
        names = {p.name for p in self.root.iterdir() if p.name.startswith(storage.PREFIX)}
        for name in names:
            storage.validate_name(name)
        return names

    def open(self, name, mode='read'):
        storage.validate_name(name)
        if mode != 'read' and name == storage.filename('authorization'):
            raise ValueError('recurring_authorization_read_only')
        path = self.root / name
        if mode == 'lock':
            if self.locked:
                raise ValueError('recurring_busy')
            self.locked = True
            stream = path.open('a+b', buffering=0)
            def close():
                self.locked = False
                stream.close()
            return SimpleNamespace(close=close)
        return path.open('x+b' if mode == 'create' else ('r+b' if mode == 'append' else 'rb'), buffering=0)


class Fixture(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix='he-recurring-tests-')))
        self.files = TempFiles(self.root)
        self.clock = self.enterContext(patch.object(storage.time, 'time', return_value=NOW))
        self.store = self.new_store()

    def new_store(self, run_id=None):
        store = storage.Store(BUNDLE, run_id, files=self.files)
        self.addCleanup(store.close)
        return store

    def authorize(self, **changes):
        (self.root / storage.filename('authorization')).write_bytes(encode(grant(**changes)) + b'\n')

    def bodies(self):
        return {p.name: p.read_bytes() for p in self.root.iterdir()}

    def ready(self):
        self.authorize()
        reserved = self.store.reserve()
        token = reserved['token']
        self.store.claim_worker(token)
        self.store.record('runtime_verified')
        self.store.arm(token)
        self.store.start_model(token)
        return token

    def complete(self, store=None, token=None):
        store = store or self.store
        if token is None:
            token = self.ready()
        store.claim(token)
        with patch.object(synthetic, 'NOW', int(self.clock()) // 60 * 60):
            analysis = fixture()
        submission = {'analysis_id': analysis['result']['analysis_id'], 'summary': prose(), 'notes': []}
        result = {'scope': 'production', 'synthetic': False,
                  'result': {'id': submission['analysis_id'], 'status': 'published'}}
        store.record('transport_started', method='prepare', attempt=1)
        store.record('transport_returned', method='prepare', attempt=1)
        store.save('analysis', analysis)
        store.record('analysis_saved', method='prepare', attempt=1)
        store.save('submission', submission)
        store.record('submission_saved', method='publish', attempt=1)
        store.record('transport_started', method='publish', attempt=2)
        store.record('transport_returned', method='publish', attempt=2)
        store.save('result', result)
        store.record('publication_acknowledged', method='publish', attempt=2)
        store.record('model_completed')
        store.save('diagnostic', {'schema_version': 1, 'scope': 'production', 'model_started': True,
            'model_completed': True, 'mcp_items': 2, 'total_tokens': 10})
        store.finish(token, 'completed')
        return token


class AuthorizationTests(Fixture):
    def test_missing_grant_creates_nothing(self):
        with self.assertRaisesRegex(ValueError, 'not_authorized'):
            self.store.reserve()
        self.assertEqual(self.bodies(), {})

    def test_grant_schema_and_types(self):
        for key in grant():
            bad = grant()
            bad.pop(key)
            with self.subTest(missing=key), self.assertRaises(ValueError):
                storage.validate_authorization(bad, BUNDLE, NOW)
        for key, value in {'schema_version': True, 'scope': 'staging', 'purpose': 'anything',
            'bundle_sha256': 'b' * 64, 'issued_unix': NOW + 1, 'not_before_unix': NOW - 1,
            'expires_unix': NOW, 'interval_seconds': True, 'max_runs': True,
            'max_model_turns_per_run': 2, 'max_reports_per_run': 2,
            'server_activation_verified': False, 'extra': 'secret'}.items():
            with self.subTest(field=key), self.assertRaises(ValueError):
                storage.validate_authorization(grant(**{key: value}), BUNDLE, NOW)

    def test_expiry_interval_and_limits(self):
        for change in ({'expires_unix': NOW + storage.MAX_GRANT_SECONDS + 1},
                       {'interval_seconds': 300}, {'max_runs': 0}, {'max_runs': 121},
                       {'not_before_unix': NOW + storage.MAX_GRANT_SECONDS}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                storage.validate_authorization(grant(**change), BUNDLE, NOW)

    def test_does_not_create_authorization(self):
        with self.assertRaisesRegex(ValueError, 'read_only'):
            self.files.create(storage.filename('authorization'), grant())
        self.assertEqual(self.bodies(), {})

    def test_before_start_is_noop(self):
        self.authorize(not_before_unix=NOW + 60)
        self.assertEqual(self.store.reserve(), {'status': 'not_due', 'next_due_unix': NOW + 60})
        self.assertFalse(self.store.available()['run_evidence_present'])

    def test_expiry_too_close_for_bounded_worker(self):
        self.authorize(expires_unix=NOW + storage.MAX_SECONDS)
        with self.assertRaisesRegex(ValueError, 'authorization_ending'):
            self.store.reserve()
        self.assertFalse(self.store.available()['run_evidence_present'])

    def test_read_only_status_with_probe(self):
        self.files.create(storage.filename('storage-probe'), storage.PROBE)
        before = self.bodies()
        self.assertEqual(self.store.available(), {'authorization_present': False, 'run_evidence_present': False})
        self.assertEqual(before, self.bodies())


class LifecycleTests(Fixture):
    def test_receipt_consumed_once_with_no_plain_token(self):
        token = self.ready()
        before = self.bodies()
        self.assertNotIn(token.encode(), b''.join(before.values()))
        with self.assertRaises(ValueError):
            self.store.reserve()
        self.assertEqual(before, self.bodies())

    def test_global_overlap_refused(self):
        self.ready()
        second = self.new_store()
        before = self.bodies()
        with self.assertRaisesRegex(ValueError, 'busy'):
            second.reserve()
        second.close()
        self.assertTrue(self.files.locked)
        self.assertEqual(before, self.bodies())

    def test_incomplete_run_after_lock_release_blocks_next(self):
        self.ready()
        self.store.close()  # Simulated process crash; OS releases handle, receipt remains.
        self.clock.return_value = NOW + 86400
        with self.assertRaisesRegex(ValueError, 'prior_run_needs_review'):
            self.new_store().reserve()

    def test_uncertain_run_blocks_even_after_30_hours(self):
        token = self.ready()
        with self.assertRaisesRegex(ValueError, 'prior_run_needs_review'):
            self.store.finish(token, 'uncertain')
        self.assertEqual(self.store.read('terminal')['outcome'], 'uncertain')
        self.store.close()
        self.clock.return_value = NOW + 30 * 3600
        with self.assertRaisesRegex(ValueError, 'prior_run_needs_review'):
            self.new_store().reserve()

    def test_six_hours_not_yet_due(self):
        self.complete()
        self.store.close()
        before = self.bodies()
        self.clock.return_value = NOW + storage.INTERVAL_SECONDS - 1
        self.assertEqual(self.new_store().reserve(), {'status': 'not_due', 'next_due_unix': NOW + 21600})
        self.assertEqual(before, self.bodies())

    def test_exactly_six_hours_creates_separate_chained_run(self):
        self.complete()
        terminal = self.store.read('terminal')
        self.store.close()
        old = self.bodies()
        self.clock.return_value = NOW + storage.INTERVAL_SECONDS
        next_store = self.new_store()
        result = next_store.reserve()
        self.assertEqual(result['run_id'], '000002')
        self.assertEqual(next_store.read('receipt')['previous_terminal_sha256'], storage.digest(terminal))
        self.assertEqual({k: self.bodies()[k] for k in old}, old)

    def test_missed_intervals_are_not_backfilled(self):
        self.complete()
        self.store.close()
        self.clock.return_value = NOW + 3 * 86400
        next_store = self.new_store()
        self.assertEqual(next_store.reserve()['run_id'], '000002')
        self.assertEqual(next_store.read('receipt')['created_unix'], NOW + 3 * 86400)
        self.assertFalse(any('000003' in name for name in self.bodies()))

    def test_multiple_completed_runs_validate_old_snapshots_at_original_time(self):
        self.complete()
        self.store.close()
        first = self.bodies()
        self.clock.return_value = NOW + storage.INTERVAL_SECONDS
        second = self.new_store()
        token = second.reserve()['token']
        second.claim_worker(token)
        second.record('runtime_verified')
        second.arm(token)
        second.start_model(token)
        self.complete(second, token)
        second.close()
        self.clock.return_value = NOW + 2 * storage.INTERVAL_SECONDS
        third = self.new_store()
        self.assertEqual(third.reserve()['run_id'], '000003')
        self.assertEqual({k: self.bodies()[k] for k in first}, first)

    def test_clock_rollback_refused(self):
        self.ready()
        self.clock.return_value = NOW + 100
        self.complete(self.store, self.store.token)
        self.store.close()
        self.clock.return_value = NOW + 99
        with self.assertRaisesRegex(ValueError, 'clock_reversed'):
            self.new_store().reserve()

    def test_quota_is_independent_of_time(self):
        self.authorize(max_runs=1)
        reservation = self.store.reserve()
        token = reservation['token']
        self.store.claim_worker(token)
        self.store.record('runtime_verified')
        self.store.arm(token)
        self.store.start_model(token)
        self.complete(self.store, token)
        self.store.close()
        self.clock.return_value = NOW + 21600
        with self.assertRaisesRegex(ValueError, 'run_limit'):
            self.new_store().reserve()

    def test_one_worker_model_and_mcp_claim(self):
        token = self.ready()
        for action in (self.store.claim_worker, self.store.arm, self.store.start_model):
            with self.subTest(action=action.__name__), self.assertRaises(FileExistsError):
                action(token)
        self.store.claim(token)
        with self.assertRaises(FileExistsError):
            self.store.claim(token)

    def test_cannot_claim_before_model_and_cannot_use_wrong_token(self):
        self.authorize()
        token = self.store.reserve()['token']
        with self.assertRaises((ValueError, FileNotFoundError)):
            self.store.claim(token)
        with self.assertRaises(ValueError):
            self.store.active('b' * 64, armed=False)
        with self.assertRaises(ValueError):
            self.store.active('0' * 64, armed=False)

    def test_run_lifetime_and_terminal_stop_activity(self):
        token = self.ready()
        self.clock.return_value = NOW + storage.MAX_SECONDS
        with self.assertRaisesRegex(ValueError, 'inactive'):
            self.store.active(token)
        with self.assertRaises(ValueError):
            self.store.finish(token, 'uncertain')
        self.clock.return_value = NOW
        with self.assertRaisesRegex(ValueError, 'inactive'):
            self.store.active(token)

    def test_authorization_change_cannot_reset_existing_state(self):
        self.complete()
        self.store.close()
        self.authorize(max_runs=119)
        self.clock.return_value = NOW + 21600
        with self.assertRaisesRegex(ValueError, 'receipt_changed'):
            self.new_store().reserve()

    def test_revocation_stops_active_mcp(self):
        token = self.ready()
        # Fixture-only removal simulates administrator revocation; runner never deletes.
        (self.root / storage.filename('authorization')).unlink()
        with self.assertRaisesRegex(ValueError, 'not_authorized'):
            self.store.active(token)

    def test_partial_or_orphan_evidence_stops_reservation(self):
        self.authorize()
        name = storage.filename('receipt', '000001')
        (self.root / name).write_bytes(b'{')
        before = self.bodies()
        with self.assertRaisesRegex(ValueError, 'prior_run_needs_review'):
            self.store.reserve()
        self.assertEqual({k: self.bodies()[k] for k in before}, before)

    def test_noncontiguous_sequence_stops_reservation(self):
        self.authorize()
        self.files.create(storage.filename('receipt', '000002'), {'synthetic': True})
        with self.assertRaisesRegex(ValueError, 'prior_run_needs_review'):
            self.store.reserve()

    def test_unknown_namespace_file_is_not_ignored(self):
        self.authorize()
        (self.root / 'recurring-v1-surprise.json').write_bytes(b'{}')
        with self.assertRaisesRegex(ValueError, 'unknown_evidence'):
            self.store.reserve()

    def test_old_trial_and_unrelated_files_untouched(self):
        sentinel = self.root / 'public-trial-01-terminal.json'
        sentinel.write_bytes(b'OLD EVIDENCE')
        self.complete()
        self.assertEqual(sentinel.read_bytes(), b'OLD EVIDENCE')


class EvidenceTests(Fixture):
    def test_artifacts_and_terminal_are_create_once(self):
        token = self.complete()
        before = self.bodies()
        with self.assertRaisesRegex(ValueError, 'terminal_exists'):
            self.store.finish(token, 'completed')
        for kind in storage.trial.ARTIFACTS:
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.store.save(kind, {'replacement': True})
        self.assertEqual(before, self.bodies())

    def test_tampered_completed_artifact_is_rejected(self):
        self.complete()
        self.store.close()
        path = self.root / storage.filename('submission', '000001')
        value = json.loads(path.read_bytes())
        value['summary']['en'] += ' Tampered.'
        path.write_bytes(encode(value) + b'\n')
        self.clock.return_value = NOW + 21600
        with self.assertRaisesRegex(ValueError, 'evidence_changed'):
            self.new_store().reserve()

    def test_incomplete_success_becomes_uncertain(self):
        token = self.ready()
        with self.assertRaises((ValueError, FileNotFoundError)):
            self.store.finish(token, 'completed')
        self.assertEqual(self.store.read('terminal')['outcome'], 'uncertain')

    def test_mismatched_ack_is_rejected_even_with_matching_stored_hash(self):
        self.complete()
        self.store.close()
        result_path = self.root / storage.filename('result', '000001')
        terminal_path = self.root / storage.filename('terminal', '000001')
        value = json.loads(result_path.read_bytes())
        value['result']['id'] = 'f' * 64
        result_path.write_bytes(encode(value) + b'\n')
        terminal = json.loads(terminal_path.read_bytes())
        terminal['evidence_sha256']['result'] = storage.digest(value)
        terminal_path.write_bytes(encode(terminal) + b'\n')
        self.clock.return_value = NOW + 21600
        with self.assertRaises(ValueError):
            self.new_store().reserve()

    def test_audit_event_and_size_limits(self):
        self.ready()
        before = self.bodies()
        with patch.object(storage.trial, 'MAX_EVENTS', 1):
            with self.assertRaisesRegex(ValueError, 'audit_failed'):
                self.store.record('failed')
        self.assertEqual(before, self.bodies())
        with self.assertRaises(ValueError):
            storage.validate_audit(b'x' * (storage.trial.MAX_AUDIT + 1))

    def test_malformed_json_is_not_normalized_or_repaired(self):
        name = storage.filename('storage-probe')
        for body in (b'{"a":1,"a":2}\n', b'{ "a": 1 }\n', b'{"a":NaN}\n', b'{}\ntrailing'):
            with self.subTest(body=body):
                (self.root / name).write_bytes(body)
                with self.assertRaises(ValueError):
                    self.files.read(name)
                self.assertEqual((self.root / name).read_bytes(), body)

    def test_audit_is_fixed_schema_and_secret_free(self):
        token = self.ready()
        before = self.bodies()
        with self.assertRaisesRegex(ValueError, 'audit_failed'):
            self.store.record('failed', code='SECRET EXCEPTION DETAILS')
        self.assertEqual(before, self.bodies())
        with self.assertRaises(ValueError):
            self.store.finish(token, 'uncertain')
        self.assertNotIn(b'SECRET', b''.join(self.bodies().values()))

    def test_partial_audit_preserved_and_terminal_uncertain(self):
        token = self.ready()
        path = self.root / storage.filename('audit', '000001')
        path.write_bytes(path.read_bytes() + b'{')
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            self.store.record('failed')
        with self.assertRaises(ValueError):
            self.store.finish(token, 'uncertain')
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.store.read('terminal')['outcome'], 'uncertain')

    def test_failed_audit_creation_preserves_consumed_receipt(self):
        self.authorize()
        real_open = self.files.open
        def broken(name, mode='read'):
            if name.endswith('-audit.jsonl') and mode == 'create':
                raise OSError('synthetic disk failure')
            return real_open(name, mode)
        with patch.object(self.files, 'open', side_effect=broken):
            with self.assertRaises(OSError):
                self.store.reserve()
        self.assertTrue((self.root / storage.filename('receipt', '000001')).exists())
        self.store.close()
        with self.assertRaisesRegex(ValueError, 'prior_run_needs_review'):
            self.new_store().reserve()

    def test_failed_terminal_write_preserves_pending_evidence(self):
        token = self.ready()
        real_create = self.files.create
        def broken(name, value):
            if name.endswith('-terminal.json'):
                raise OSError('synthetic disk failure')
            return real_create(name, value)
        with patch.object(self.files, 'create', side_effect=broken):
            with self.assertRaises(OSError):
                self.store.finish(token, 'uncertain')
        self.store.close()
        with self.assertRaisesRegex(ValueError, 'prior_run_needs_review'):
            self.new_store().reserve()

    def test_invalid_paths_never_touch_files(self):
        for value in ('../receipt', r'C:\Windows\win.ini', '000000', '000121', '1', 1, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                storage.run_identifier(value)
        for name in ('../recurring-v1-authorization.json', 'public-trial-01-receipt.json',
                     'id_ed25519', 'recurring-v1-000001-audit.json', 'recurring-v1-000001-result.jsonl'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                storage.validate_name(name)
        self.assertEqual(self.bodies(), {})


class TransportTests(Fixture):
    def session(self):
        token = self.ready()
        session = reports.Reports(lambda: self.new_store('000001'), token)
        self.addCleanup(session.close)
        original = reports.adapter.Reports
        self.enterContext(patch.object(reports.adapter, 'Reports',
            side_effect=lambda transport: original(transport, clock=self.clock)))
        self.enterContext(patch.object(reports.connection, 'load_destination', return_value=
            reports.transport.Destination(**{k: reports.connection.EXPECTED_BINDING[k] for k in
                ('host', 'port', 'ssh_sha256', 'known_hosts_sha256')})))
        analysis = fixture()
        submission = {'analysis_id': analysis['result']['analysis_id'], 'summary': prose(), 'notes': []}
        ack = {'scope': 'production', 'synthetic': False,
               'result': {'id': submission['analysis_id'], 'status': 'published'}}
        def send(argv, body, **kwargs):
            if argv[-1] == 'prepare':
                return encode(analysis)
            self.assertEqual(self.store.read('submission'), submission)
            return encode(ack)
        self.send = self.enterContext(patch.object(reports.transport, 'bounded_process', side_effect=send))
        return session, submission, ack

    def test_single_submission_and_confirmed_duplicate_do_not_resend(self):
        session, submission, ack = self.session()
        session.call('prepare', {})
        self.assertEqual(session.call('publish', submission), ack)
        self.assertEqual(session.call('publish', copy.deepcopy(submission)), ack)
        self.assertEqual(self.send.call_count, 2)

    def test_uncertain_submission_is_not_retried(self):
        session, submission, _ = self.session()
        session.call('prepare', {})
        self.send.side_effect = TimeoutError('private upstream detail')
        with self.assertRaises(ValueError):
            session.call('publish', submission)
        before = self.bodies()
        with self.assertRaises(ValueError):
            session.call('publish', submission)
        self.assertEqual(self.send.call_count, 2)
        self.assertEqual(before, self.bodies())

    def test_saved_success_chain_verifies_with_recurring_store(self):
        session, submission, _ = self.session()
        session.call('prepare', {})
        session.call('publish', submission)
        ctx = SimpleNamespace(adapter=reports.adapter, storage=storage)
        self.assertEqual(trial.validate_result(ctx, self.store)['analysis_id'], submission['analysis_id'])
        self.store.record('model_completed')
        self.store.save('diagnostic', {'schema_version': 1, 'scope': 'production', 'model_started': True,
            'model_completed': True, 'mcp_items': 2, 'total_tokens': None})
        self.store.finish(self.store.token, 'completed')
        self.assertEqual(self.store.history(grant())['count'], 1)


def context():
    return SimpleNamespace(storage=storage, trial=trial, candidate=candidate, runtime=runtime,
        adapter=reports.adapter, sessions=reports, config=candidate_config(),
        guidance='Synthetic reporting guidance', bundle=BUNDLE, imports=Mock(),
        revision=SimpleNamespace(check_version=Mock()), recovery=SimpleNamespace(bind=Mock()),
        repair_status={'repair_active': False})


class ThreadBindingTests(unittest.TestCase):
    def session(self):
        cls = runner.session_class(context())
        session = cls.__new__(cls)  # No process, file, transport or model is opened.
        session.evidence = trial.Evidence()
        session.turn_sent = session.verified = False
        session.store = session.token = session.thread_id = None
        session.counter = 0
        session.deadline = time.monotonic() + 5
        session.inbox = queue.Queue()
        session.process = SimpleNamespace(stdin=io.BytesIO())
        return session

    def start(self, session, result=None):
        session.inbox.put({'id': session.counter + 1,
                           'result': candidate_thread() if result is None else result})
        return session.request('thread/start', {'cwd': str(candidate.WORKSPACE),
            'ephemeral': True, 'permissions': candidate.PROFILE, 'approvalPolicy': 'never',
            'model': runtime.MODEL, 'environments': [], 'runtimeWorkspaceRoots': []})

    def test_matching_startup_before_started_notification_is_accepted(self):
        session = self.session()
        self.start(session)
        self.assertEqual(session.evidence.thread, 'synthetic')
        for event in (
            {'method': 'mcpServer/startupStatus/updated', 'params': {'threadId': 'synthetic'}},
            {'method': 'thread/started', 'params': {'thread': {'id': 'synthetic'}}},
            {'id': 2, 'result': {'config': {}}},
        ):
            session.inbox.put(event)
        self.assertEqual(session.request('config/read', {'cwd': str(candidate.WORKSPACE)}), {'config': {}})
        self.assertFalse(session.turn_sent)
        self.assertFalse(session.verified)

    def test_started_notification_before_response_still_requires_matching_id(self):
        session = self.session()
        session.inbox.put({'method': 'thread/started', 'params': {'thread': {'id': 'synthetic'}}})
        self.start(session)
        self.assertEqual(session.evidence.thread, 'synthetic')
        session = self.session()
        session.inbox.put({'method': 'thread/started', 'params': {'thread': {'id': 'different'}}})
        with self.assertRaisesRegex(ValueError, 'unexpected_thread'):
            self.start(session)
        self.assertEqual(session.evidence.thread, 'different')

    def test_unvalidated_startup_event_before_response_is_still_rejected(self):
        session = self.session()
        session.inbox.put({'method': 'mcpServer/startupStatus/updated', 'params': {'threadId': 'synthetic'}})
        with self.assertRaisesRegex(ValueError, 'unexpected_thread'):
            self.start(session)
        self.assertIsNone(session.evidence.thread)
        self.assertFalse(session.turn_sent)

    def test_invalid_thread_policy_is_rejected_before_binding(self):
        for changes in (
            {'sandbox': {'type': 'dangerFullAccess'}},
            {'sandbox': {'type': 'readOnly', 'networkAccess': True}},
            {'activePermissionProfile': {'id': 'unreviewed', 'extends': None}},
            {'approvalPolicy': 'on-request'}, {'runtimeWorkspaceRoots': ['C:\\']},
            {'model': 'unreviewed'}, {'modelProvider': 'unreviewed'},
            {'thread': {'id': 'synthetic', 'ephemeral': False}}, {'instructionSources': []},
            {'thread': {'id': '', 'ephemeral': True}},
        ):
            session = self.session()
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.start(session, candidate_thread() | changes)
            self.assertIsNone(session.evidence.thread)
            self.assertFalse(session.verified)

    def test_other_threads_are_rejected_after_binding(self):
        for event in (
            {'method': 'mcpServer/startupStatus/updated', 'params': {'threadId': 'different'}},
            {'method': 'thread/started', 'params': {'thread': {'id': 'different'}}},
        ):
            session = self.session()
            self.start(session)
            session.inbox.put(event)
            with self.subTest(event=event), self.assertRaisesRegex(ValueError, 'unexpected_thread'):
                session.receive()
            self.assertEqual(session.evidence.thread, 'synthetic')

    def test_second_response_cannot_replace_the_bound_thread(self):
        session = self.session()
        self.start(session)
        different = candidate_thread()
        different['thread']['id'] = 'different'
        with self.assertRaisesRegex(ValueError, 'unexpected_thread'):
            self.start(session, different)
        self.assertEqual(session.evidence.thread, 'synthetic')

    def test_binding_does_not_arm_a_turn_or_allow_unrelated_rpcs(self):
        session = self.session()
        self.start(session)
        before = session.process.stdin.getvalue()
        for method in ('thread/fork', 'thread/resume', 'process/spawn', 'config/value/write'):
            with self.subTest(method=method), self.assertRaisesRegex(ValueError, 'rpc_refused'):
                session.request(method, {})
        with self.assertRaisesRegex(ValueError, 'not_armed'):
            session.request('turn/start', {'threadId': 'synthetic',
                'input': [{'type': 'text', 'text': trial.PROMPT}]})
        self.assertEqual(session.process.stdin.getvalue(), before)
        self.assertFalse(session.turn_sent)


class RunnerTests(unittest.TestCase):
    def test_pinned_trial_unchanged(self):
        self.assertEqual(hashlib.sha256(trial.ENTRY.read_bytes()).hexdigest(), runner.TRIAL_PIN)
        self.assertEqual(hashlib.sha256((runner.SOURCE / 'insights_recurring_store.py').read_bytes()).hexdigest(),
                         runner.STORE_PIN)

    def test_config_retains_runtime_restrictions_without_saved_mutations(self):
        ctx = context()
        before = copy.deepcopy(ctx.config)
        config = runner.run_config(ctx)
        candidate.verify_config(runtime, config, config)
        self.assertEqual(ctx.config, before)
        self.assertEqual(config['model'], 'gpt-6.1-sol')
        self.assertEqual(config['model_reasoning_effort'], 'medium')
        self.assertEqual(config['agents'], {'enabled': False})
        self.assertEqual(config['notify'], [])
        self.assertEqual(config['mcp_servers'][candidate.SERVER]['args'],
                         ['-I', '-B', str(runner.ENTRY), '--mcp-report'])
        self.assertEqual(config['mcp_servers'][candidate.SERVER]['env'],
                         {runner.TOKEN_ENV: runner.CLOSED_TOKEN, runner.RUN_ENV: runner.CLOSED_RUN})

    def test_closed_and_live_context_cannot_be_mixed(self):
        for token, run_id in ((runner.CLOSED_TOKEN, '000001'), ('a' * 64, runner.CLOSED_RUN),
                              ('secret', '000001'), ('a' * 64, '../bad')):
            with self.assertRaises(ValueError):
                runner.run_config(context(), token, run_id)

    def test_preflight_mcp_is_always_blocked_and_never_opens_store(self):
        ctx = context()
        with patch.object(ctx.candidate, 'serve_preflight') as serve, \
                patch.object(ctx.sessions, 'Reports') as live, patch.object(ctx.storage, 'Store') as store:
            runner.serve(ctx, runner.CLOSED_TOKEN, runner.CLOSED_RUN)
        serve.assert_called_once()
        live.assert_not_called()
        store.assert_not_called()

    def test_preflight_never_calls_generation(self):
        ctx = context()
        store, session = Mock(), Mock()
        store.available.return_value = {'authorization_present': False, 'run_evidence_present': False}
        session.turn_sent = False
        session.request.return_value = {'config': runner.run_config(ctx)}
        with patch.object(ctx.storage, 'Store', return_value=store), \
                patch.object(runner, 'no_model_session_class', return_value=Mock(return_value=session)):
            result = runner.no_model_check(ctx, 'preflight')
        self.assertTrue(result['runtime_checked'])
        self.assertFalse(result['model_started'])
        session.generate_once.assert_not_called()
        session.gates.assert_called_once()
        session.close.assert_called_once()

    def test_live_storage_probe_is_refused_before_runtime(self):
        ctx = context()
        store = Mock()
        store.available.return_value = {'authorization_present': True, 'run_evidence_present': False}
        with patch.object(ctx.storage, 'Store', return_value=store), \
                patch.object(runner, 'session_class') as session:
            with self.assertRaisesRegex(ValueError, 'live_probe_refused'):
                runner.no_model_check(ctx, 'storage-check')
        session.assert_not_called()
        store.close.assert_not_called()

    def test_no_grant_or_not_due_never_spawns(self):
        ctx = context()
        for result in (ValueError('recurring_not_authorized'), {'status': 'not_due', 'next_due_unix': NOW + 100}):
            store = Mock()
            if isinstance(result, Exception):
                store.reserve.side_effect = result
            else:
                store.reserve.return_value = result
            with self.subTest(result=result), patch.object(ctx.storage, 'Store', return_value=store), \
                    patch.object(runner.subprocess, 'Popen') as popen, \
                    patch.object(ctx.runtime, 'OwnedRuntimeTree') as tree:
                if isinstance(result, Exception):
                    with self.assertRaises(ValueError):
                        runner.supervise(ctx)
                else:
                    self.assertEqual(runner.supervise(ctx)['status'], 'not_due')
                popen.assert_not_called()
                tree.assert_not_called()
                store.finish.assert_not_called()
                store.close.assert_called_once()

    def test_worker_without_owned_marker_cannot_open_storage(self):
        ctx = context()
        with patch.dict(os.environ, {}, clear=True), patch.object(ctx.storage, 'Store') as store:
            with self.assertRaisesRegex(ValueError, 'owned_worker'):
                runner.worker(ctx, 'b' * 64, '000001')
        store.assert_not_called()

    def supervisor(self, timeout=False, validate_error=False):
        ctx = context()
        store, process, tree = Mock(), Mock(), Mock()
        expected = {'public_report_acknowledged': True, 'analysis_id': 'b' * 64,
                    'submission_sha256': 'c' * 64, 'status': 'published'}
        store.reserve.return_value = {'status': 'reserved', 'run_id': '000001', 'token': 'd' * 64}
        store.read.return_value = {'model_started': True, 'model_completed': True}
        process.stdout = io.BytesIO(encode(expected) + b'\n')
        process.poll.return_value = process.returncode = 0
        if timeout:
            process.wait.side_effect = [subprocess.TimeoutExpired('synthetic', 360), 0]
        with patch.object(ctx.storage, 'Store', return_value=store), \
                patch.object(ctx.runtime, 'OwnedRuntimeTree', return_value=tree), \
                patch.object(runner.subprocess, 'Popen', return_value=process) as popen, \
                patch.object(ctx.trial, 'validate_result', return_value=expected,
                             side_effect=ValueError('bad_ack') if validate_error else None):
            if timeout or validate_error:
                with self.assertRaises((ValueError, subprocess.TimeoutExpired)):
                    runner.supervise(ctx)
            else:
                self.assertEqual(runner.supervise(ctx), expected | {'run_id': '000001'})
        self.assertTrue(process.stdout.closed)
        self.assertEqual(popen.call_count, 1)
        self.assertEqual(popen.call_args.args[0][-2:], [str(runner.ENTRY), '--worker'])
        environment = popen.call_args.kwargs['env']
        self.assertEqual(environment[runner.RUN_ENV], '000001')
        self.assertEqual(environment[runner.WORKER_ENV], 'recurring-owned-worker-v1')
        self.assertNotIn('d' * 64, ' '.join(popen.call_args.args[0]))
        store.finish.assert_called_once_with('d' * 64, 'uncertain' if timeout or validate_error else 'completed')
        store.close.assert_called_once()
        tree.close.assert_called()

    def test_success_validates_after_owned_process_cleanup(self):
        self.supervisor()

    def test_timeout_stops_without_retry_and_closes_owned_tree(self):
        self.supervisor(timeout=True)

    def test_worker_success_cannot_replace_saved_acknowledgement(self):
        self.supervisor(validate_error=True)

    def test_public_diagnostic_does_not_print_arbitrary_exception(self):
        self.assertEqual(runner.fixed_code(OSError('SECRET')), 'recurring_run_failed')
        self.assertEqual(runner.fixed_code(ValueError('SECRET PATH /key')), 'recurring_run_failed')
        self.assertEqual(runner.fixed_code(ValueError('recurring_busy')), 'recurring_busy')


if __name__ == '__main__':
    unittest.main()
