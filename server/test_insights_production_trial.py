"""Disposable synthetic tests only; no model calls or real network operations."""
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

import insights_production_trial_store as storage
import insights_production_trial_session as reports
import insights_production_trial_run as runner
import insights_production_launcher as candidate
import insights_staging_launcher as runtime
import insights_production_mcp as adapter
from insights_broker import encode, decode
from test_insights_production_mcp import fixture, prose, NOW
from test_insights_production_launcher import config as candidate_config


BUNDLE = 'a' * 64


def authorization(now=NOW):
    return {'schema_version': 1, 'scope': 'production', 'purpose': 'single_public_report',
            'bundle_sha256': BUNDLE, 'issued_unix': now, 'expires_unix': now + 900,
            'max_model_turns': 1, 'max_reports': 1, 'server_activation_verified': True}


class LocalFixture(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.paths = {name: self.root / path.name for name, path in storage.FILES.items()}
        self.enterContext(patch.object(storage, 'ROOT', self.root))
        self.enterContext(patch.object(storage, 'FILES', self.paths))
        self.root_check = self.enterContext(patch.object(storage, '_root_check'))
        self.clock = self.enterContext(patch.object(storage.time, 'time', return_value=NOW))

        def open_file(kind, *, create=False, append=False):
            if create and kind == 'authorization':
                raise ValueError('fixture_authorization_is_read_only')
            if append and kind != 'audit':
                raise ValueError('fixture_append_only_audit')
            return self.paths[kind].open('x+b' if create else ('r+b' if append else 'rb'), buffering=0)
        self.open = self.enterContext(patch.object(storage, '_open', side_effect=open_file))
        self.store = storage.Store(BUNDLE)
        self.addCleanup(self.store.close)

    def authorize(self, value=None):
        self.paths['authorization'].write_bytes(encode(authorization() if value is None else value) + b'\n')

    def reserve(self):
        self.authorize()
        return self.store.reserve()

    def ready(self):
        token = self.reserve()
        self.store.claim_worker(token)
        self.store.arm(token)
        self.store.start_model(token)
        return token

    def bodies(self):
        return {name: path.read_bytes() for name, path in self.paths.items() if path.exists()}

    def events(self):
        return [json.loads(row) for row in self.paths['audit'].read_bytes().splitlines()]


class StoreTests(LocalFixture):
    def test_missing_authorization_creates_nothing(self):
        with self.assertRaisesRegex(ValueError, 'not_authorized'):
            self.store.reserve()
        self.assertEqual(self.bodies(), {})

    def test_authorization_cannot_be_created_by_store(self):
        with self.assertRaisesRegex(ValueError, 'read_only'):
            storage._create('authorization', authorization())
        self.assertEqual(self.bodies(), {})

    def test_available_is_read_only(self):
        self.assertEqual(self.store.available(), {'authorization_present': False,
            'attempt_already_consumed': False, 'terminal_present': False})
        self.assertEqual(self.bodies(), {})

    def test_grant_schema_limits_binding_and_expiry(self):
        changes = {'schema_version': True, 'scope': 'staging', 'purpose': 'anything',
                   'bundle_sha256': 'b' * 64, 'issued_unix': NOW + 1,
                   'expires_unix': NOW, 'max_model_turns': 2, 'max_reports': True,
                   'server_activation_verified': False, 'extra': 'SECRET'}
        for name, value in changes.items():
            with self.subTest(name=name), self.assertRaises(ValueError):
                storage.validate_authorization(authorization() | {name: value}, BUNDLE, NOW)
        with self.assertRaises(ValueError):
            storage.validate_authorization(authorization() | {'expires_unix': NOW + 901}, BUNDLE, NOW)

    def test_single_use_marker_and_no_raw_token(self):
        token = self.reserve()
        receipt = self.store.read('receipt')
        self.assertEqual(receipt['bundle_sha256'], BUNDLE)
        self.assertEqual(receipt['token_sha256'], hashlib.sha256(token.encode()).hexdigest())
        self.assertNotIn(token.encode(), b''.join(self.bodies().values()))
        before = self.bodies()
        with self.assertRaises(ValueError):
            self.store.reserve()
        self.assertEqual(self.bodies(), before)

    def test_partial_receipt_consumes_attempt(self):
        self.authorize()
        self.paths['receipt'].write_bytes(b'{')
        before = self.bodies()
        with self.assertRaisesRegex(ValueError, 'already_consumed'):
            self.store.reserve()
        self.assertEqual(self.bodies(), before)

    def test_audit_creation_failure_still_consumes_receipt(self):
        self.authorize()
        open_file = self.open.side_effect

        def failing_audit(kind, **kwargs):
            if kind == 'audit' and kwargs.get('create'):
                raise OSError('synthetic disk failure')
            return open_file(kind, **kwargs)

        self.open.side_effect = failing_audit
        with self.assertRaises(OSError):
            self.store.reserve()
        self.assertTrue(self.paths['receipt'].exists())
        before = self.bodies()
        with self.assertRaisesRegex(ValueError, 'already_consumed'):
            self.store.reserve()
        self.assertEqual(self.bodies(), before)

    def test_armed_required_and_one_worker_and_one_model(self):
        token = self.reserve()
        with self.assertRaises((ValueError, FileNotFoundError)):
            self.store.active(token)
        self.store.claim_worker(token)
        with self.assertRaises(FileExistsError):
            self.store.claim_worker(token)
        self.store.arm(token)
        with self.assertRaises((ValueError, FileNotFoundError)):
            self.store.claim(token)
        self.store.start_model(token)
        with self.assertRaises(FileExistsError):
            self.store.start_model(token)
        self.store.claim(token)
        with self.assertRaises(FileExistsError):
            self.store.claim(token)

    def test_wrong_token_expiry_and_terminal_block_activity(self):
        token = self.ready()
        with self.assertRaises(ValueError):
            self.store.active('b' * 64)
        self.clock.return_value = NOW + 360
        with self.assertRaises(ValueError):
            self.store.active(token)
        self.store.finish(token, 'uncertain')
        self.clock.return_value = NOW
        with self.assertRaises(ValueError):
            self.store.active(token)

    def test_changed_grant_blocks_existing_receipt(self):
        token = self.ready()
        self.paths['authorization'].write_bytes(encode(authorization() | {'expires_unix': NOW + 899}) + b'\n')
        with self.assertRaises(ValueError):
            self.store.active(token)

    def test_all_artifacts_are_exclusive_and_prior_evidence_untouched(self):
        sentinel = self.root / 'old-review-result.json'
        sentinel.write_bytes(b'EXISTING EVIDENCE')
        token = self.ready()
        self.store.claim(token)
        for kind in storage.ARTIFACTS:
            self.store.save(kind, {'synthetic_test': True})
            before = self.bodies()
            with self.assertRaises(FileExistsError):
                self.store.save(kind, {'replacement': True})
            self.assertEqual(self.bodies(), before)
        self.assertEqual(sentinel.read_bytes(), b'EXISTING EVIDENCE')

    def test_audit_schema_is_secret_free_and_fail_closed(self):
        self.ready()
        self.store.record('transport_started', method='prepare', attempt=1)
        before = self.paths['audit'].read_bytes()
        with self.assertRaisesRegex(ValueError, 'audit_failed'):
            self.store.record('failed', code='SECRET-PAYLOAD')
        self.assertEqual(self.paths['audit'].read_bytes(), before)
        self.assertNotIn(b'SECRET', before)
        self.assertTrue(self.store.failed)
        with self.assertRaises(ValueError):
            self.store.record('failed')
        for row in self.events():
            self.assertEqual(set(row), {'timestamp_utc', 'event', 'method', 'attempt', 'code'})

    def test_append_failure_keeps_partial_evidence_and_allows_terminal(self):
        token = self.ready()
        self.paths['audit'].write_bytes(self.paths['audit'].read_bytes() + b'{')
        before = self.paths['audit'].read_bytes()
        with self.assertRaises(ValueError):
            self.store.record('failed')
        self.store.finish(token, 'uncertain')
        self.assertEqual(self.paths['audit'].read_bytes(), before)
        self.assertEqual(self.store.read('terminal')['outcome'], 'uncertain')

    def test_event_limit_blocks_further_writes(self):
        self.reserve()
        self.enterContext(patch.object(storage, 'MAX_EVENTS', 1))
        before = self.paths['audit'].read_bytes()
        with self.assertRaises(ValueError):
            self.store.record('failed')
        self.assertEqual(self.paths['audit'].read_bytes(), before)

    def test_terminal_cannot_be_rewritten_with_different_outcome(self):
        token = self.ready()
        self.store.finish(token, 'completed')
        before = self.bodies()
        self.store.finish(token, 'completed')
        self.assertEqual(self.bodies(), before)
        with self.assertRaises(ValueError):
            self.store.finish(token, 'uncertain')
        self.assertEqual(self.bodies(), before)

    def test_storage_probe_is_retained_not_an_authorization(self):
        self.assertTrue(storage.storage_check()['synthetic_probe_created'])
        before = self.bodies()
        self.assertFalse(storage.storage_check()['synthetic_probe_created'])
        self.assertEqual(before, self.bodies())
        self.assertEqual(set(before), {'storage-probe'})
        self.authorize()
        with self.assertRaises(ValueError):
            storage.storage_check()

    def test_unknown_path_and_traversal_rejected(self):
        for kind in ('../receipt', str(self.root / 'elsewhere'), 'key'):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                storage._path(kind)


class SessionTests(LocalFixture):
    def session(self):
        token = self.ready()
        factory = Mock(return_value=self.store)
        adapter_class = reports.adapter.Reports
        # Constructor defaults capture the real clock at import time; the
        # synthetic snapshot and its validator must share this fixture's clock.
        self.enterContext(patch.object(reports.adapter, 'Reports',
            side_effect=lambda transport: adapter_class(transport, clock=self.clock)))
        session = reports.Reports(factory, token)
        self.enterContext(patch.object(reports.connection, 'load_destination', return_value=
            reports.transport.Destination(**{k: reports.connection.EXPECTED_BINDING[k] for k in
                ('host', 'port', 'ssh_sha256', 'known_hosts_sha256')})))
        analysis = fixture()
        sends = []
        def send(argv, body, **kwargs):
            method = argv[-1]
            sends.append((method, body))
            if method == 'prepare':
                self.assertEqual(body, b'')
                return encode(analysis)
            self.assertTrue(self.paths['submission'].exists())
            identifier = json.loads(body)['analysis_id']
            return encode({'scope': 'production', 'synthetic': False,
                           'result': {'status': 'published', 'id': identifier}})
        self.send = self.enterContext(patch.object(reports.transport, 'bounded_process', side_effect=send))
        self.addCleanup(session.close)
        return session, analysis, sends

    def submission(self, analysis):
        return {'analysis_id': analysis['result']['analysis_id'], 'summary': prose(), 'notes': []}

    def test_closed_token_never_opens_store_or_connection(self):
        factory = Mock(side_effect=AssertionError('Must not open'))
        session = reports.Reports(factory, reports.CLOSED_TOKEN)
        with self.assertRaisesRegex(ValueError, 'not_authorized'):
            session.call('prepare', {})
        factory.assert_not_called()

    def test_one_report_full_evidence_and_idempotent_success(self):
        session, analysis, sends = self.session()
        prepared = session.call('prepare', {})
        self.assertEqual(prepared, analysis)
        prepared['result']['analysis_id'] = 'f' * 64
        self.assertEqual(session.call('prepare', {}), analysis)
        submission = self.submission(analysis)
        result = session.call('publish', submission)
        self.assertEqual(session.call('publish', copy.deepcopy(submission)), result)
        self.assertEqual([method for method, _ in sends], ['prepare', 'publish'])
        self.assertEqual(self.store.read('analysis'), analysis)
        self.assertEqual(self.store.read('submission'), submission)
        self.assertEqual(self.store.read('result'), result)
        events = [row['event'] for row in self.events()]
        self.assertLess(events.index('submission_saved'), len(events) - 1)
        self.assertEqual(events.count('publication_acknowledged'), 1)
        raw = self.paths['audit'].read_bytes()
        self.assertNotIn(submission['summary']['en'].encode(), raw)

    def test_changed_report_after_success_is_rejected_without_send(self):
        session, analysis, sends = self.session()
        session.call('prepare', {})
        submission = self.submission(analysis)
        session.call('publish', submission)
        submission['summary']['en'] += ' Changed.'
        with self.assertRaises(ValueError):
            session.call('publish', submission)
        self.assertEqual(len(sends), 2)

    def test_timeout_no_automatic_retry_or_replacement(self):
        session, analysis, sends = self.session()
        session.call('prepare', {})
        self.send.side_effect = TimeoutError('SECRET UPSTREAM DIAGNOSTIC')
        with self.assertRaisesRegex(ValueError, '^production_trial_call_failed_review_evidence$'):
            session.call('publish', self.submission(analysis))
        count = self.send.call_count
        before = self.paths['submission'].read_bytes()
        with self.assertRaises(ValueError):
            session.call('publish', self.submission(analysis))
        self.assertEqual(self.send.call_count, count)
        self.assertEqual(self.paths['submission'].read_bytes(), before)
        self.assertFalse(self.paths['result'].exists())
        self.assertNotIn(b'SECRET', self.paths['audit'].read_bytes())

    def test_bad_acknowledgement_not_recorded_as_success(self):
        session, analysis, sends = self.session()
        session.call('prepare', {})
        self.send.side_effect = lambda *a, **kw: encode({'scope': 'staging', 'synthetic': True, 'result': {}})
        with self.assertRaises(ValueError):
            session.call('publish', self.submission(analysis))
        self.assertFalse(self.paths['result'].exists())
        self.assertNotIn('publication_acknowledged', [row['event'] for row in self.events()])

    def test_audit_failure_prevents_network_send(self):
        session, analysis, sends = self.session()
        with patch.object(self.store, 'record', side_effect=ValueError('audit_failed')):
            with self.assertRaises(ValueError):
                session.call('prepare', {})
        self.send.assert_not_called()

    def test_configuration_rechecked_before_every_send(self):
        session, analysis, sends = self.session()
        with patch.object(reports.connection, 'load_destination', side_effect=ValueError('pin_changed')):
            with self.assertRaises(ValueError):
                session.call('prepare', {})
        self.send.assert_not_called()

    def test_short_remaining_deadline_stops_before_network(self):
        session, analysis, sends = self.session()
        session.call('prepare', {})
        session.deadline = time.monotonic() + 10
        before = self.send.call_count
        with self.assertRaises(ValueError):
            session.call('publish', self.submission(analysis))
        self.assertEqual(self.send.call_count, before)

    def test_command_uses_only_production_key_user_and_host_pin(self):
        dest = reports.transport.Destination('91.98.44.219', 22, 'a' * 64, 'b' * 64)
        argv = reports.ssh_command(dest, 'prepare')
        self.assertEqual(argv[-2:], ['homeenergy-reporter@91.98.44.219', 'prepare'])
        self.assertIn(str(reports.connection.KEY), argv)
        self.assertNotIn(str(reports.transport.KEY), argv)
        for option in ('StrictHostKeyChecking=yes', 'GlobalKnownHostsFile=none', 'IdentityAgent=none',
                       'CertificateFile=none', 'ProxyCommand=none', 'ProxyJump=none',
                       'PermitLocalCommand=no', 'ClearAllForwardings=yes'):
            self.assertIn(option, argv)
        with self.assertRaises(ValueError):
            reports.ssh_command(dest, 'prepare; id')
        with self.assertRaises(ValueError):
            reports.ssh_command(reports.transport.Destination('127.0.0.1', 22, 'a' * 64, 'b' * 64), 'prepare')


def context():
    return SimpleNamespace(runtime=runtime, candidate=candidate, adapter=adapter, storage=storage,
                           config=candidate_config(), guidance='Synthetic reporting guidance', bundle=BUNDLE)


class RunnerTests(unittest.TestCase):
    def test_pins_preserve_previous_candidate(self):
        self.assertEqual(hashlib.sha256(candidate.ENTRY.read_bytes()).hexdigest(), runner.CANDIDATE_PIN)
        for name, pin in runner.PINS.items():
            self.assertEqual(hashlib.sha256((runner.SOURCE / (name + '.py')).read_bytes()).hexdigest(), pin)

    def test_config_is_in_memory_and_only_one_bounded_mcp(self):
        ctx = context()
        before = copy.deepcopy(ctx.config)
        config = runner.run_config(ctx, runner.CLOSED_TOKEN)
        self.assertEqual(ctx.config, before)
        candidate.verify_config(runtime, config, config)
        self.assertEqual(config['mcp_servers'][candidate.SERVER]['args'],
                         ['-I', '-B', str(runner.ENTRY), '--mcp-report'])
        self.assertEqual(config['mcp_servers'][candidate.SERVER]['env'], {runner.TOKEN_ENV: runner.CLOSED_TOKEN})
        self.assertEqual(config['agents'], {'enabled': False})
        self.assertEqual(config['notify'], [])

    def session(self):
        session = runner.session_class(context()).__new__(runner.session_class(context()))
        session.evidence = runner.Evidence()
        session.turn_sent = False
        session.verified = False
        session.store = None
        session.token = None
        session.thread_id = 'synthetic'
        session.counter = 0
        session.deadline = time.monotonic() + 1
        session.inbox = queue.Queue()
        session.process = SimpleNamespace(stdin=io.BytesIO())
        return session

    def test_unarmed_turn_never_writes_bytes(self):
        session = self.session()
        with self.assertRaisesRegex(ValueError, 'not_armed'):
            session.send({'id': 1, 'method': 'turn/start',
                'params': {'threadId': 'synthetic', 'input': [{'type': 'text', 'text': runner.PROMPT}]}})
        self.assertEqual(session.process.stdin.getvalue(), b'')

    def test_one_turn_fuse_before_send_and_no_overrides(self):
        session = self.session()
        session.verified = True
        session.store = Mock()
        session.token = 'a' * 64
        params = {'threadId': 'synthetic', 'input': [{'type': 'text', 'text': runner.PROMPT}]}
        with self.assertRaises(ValueError):
            session.send({'id': 1, 'method': 'turn/start', 'params': params | {'sandboxPolicy': 'full'}})
        session.store.start_model.assert_not_called()
        session.send({'id': 1, 'method': 'turn/start', 'params': params})
        session.store.start_model.assert_called_once_with('a' * 64)
        before = session.process.stdin.getvalue()
        with self.assertRaises(ValueError):
            session.send({'id': 2, 'method': 'turn/start', 'params': params})
        self.assertEqual(session.process.stdin.getvalue(), before)

    def test_durable_marker_failure_blocks_model(self):
        session = self.session()
        session.verified = True
        session.store = Mock()
        session.store.start_model.side_effect = OSError('synthetic disk failure')
        session.token = 'a' * 64
        with self.assertRaises(OSError):
            session.send({'id': 1, 'method': 'turn/start',
                'params': {'threadId': 'synthetic', 'input': [{'type': 'text', 'text': runner.PROMPT}]}})
        self.assertEqual(session.process.stdin.getvalue(), b'')

    def test_unrelated_rpcs_and_runtime_approval_requests_rejected(self):
        for method in ('thread/fork', 'thread/resume', 'process/spawn', 'turn/steer', 'config/value/write'):
            with self.assertRaises(ValueError):
                self.session().request(method, {})
        session = self.session()
        session.inbox.put({'id': 15, 'method': 'item/permissions/requestApproval', 'params': {}})
        with self.assertRaisesRegex(ValueError, 'client_request_refused'):
            session.receive()

    def test_evidence_rejects_child_hook_tools_and_unexpected_turn(self):
        for event in (
            {'method': 'hook/started', 'params': {}},
            {'method': 'thread/started', 'params': {'thread': {'id': 'different'}}},
            {'method': 'turn/started', 'params': {'threadId': 'synthetic', 'turn': {'id': 't'}}},
            {'method': 'item/started', 'params': {'threadId': 'synthetic', 'item': {'type': 'commandExecution'}}},
        ):
            evidence = runner.Evidence()
            evidence.thread = 'synthetic'
            with self.assertRaises(ValueError):
                evidence.accept(event, False)

    def test_diagnostics_do_not_include_model_text(self):
        evidence = runner.Evidence()
        evidence.thread = 'synthetic'
        evidence.accept({'method': 'item/completed', 'params': {'threadId': 'synthetic',
            'item': {'type': 'agentMessage', 'text': 'SECRET MODEL TEXT'}}}, True)
        self.assertNotIn('SECRET', json.dumps(evidence.diagnostic(True)))

    def test_missing_authorization_never_spawns_worker_or_runtime(self):
        ctx = context()
        store = Mock()
        store.reserve.side_effect = ValueError('production_trial_not_authorized')
        with patch.object(ctx.storage, 'Store', return_value=store), \
                patch.object(runner.subprocess, 'Popen') as popen, \
                patch.object(ctx.runtime, 'OwnedRuntimeTree') as tree:
            with self.assertRaisesRegex(ValueError, 'not_authorized'):
                runner.supervise(ctx)
        popen.assert_not_called()
        tree.assert_not_called()
        store.close.assert_called_once()
        store.finish.assert_not_called()

    def test_public_result_needs_matched_analysis_submission_and_ack(self):
        ctx = context()
        analysis = fixture()
        submission = {'analysis_id': analysis['result']['analysis_id'], 'summary': prose(), 'notes': []}
        ack = {'scope': 'production', 'synthetic': False,
               'result': {'id': submission['analysis_id'], 'status': 'published'}}
        values = {'analysis': analysis, 'submission': submission, 'result': ack}
        store = Mock()
        store.read.side_effect = values.__getitem__
        with patch.object(adapter.time, 'time', return_value=NOW):
            self.assertEqual(runner.validate_result(ctx, store)['status'], 'published')
            ack['result']['id'] = 'b' * 64
            with self.assertRaises(ValueError):
                runner.validate_result(ctx, store)

    def test_cleanup_failure_still_attempts_parent_reader_and_pipe(self):
        tree, process, reader = Mock(), Mock(), Mock()
        tree.close.side_effect = OSError('synthetic job failure')
        process.poll.return_value = None
        reader.ident = 1
        reader.is_alive.return_value = False
        with self.assertRaisesRegex(ValueError, 'cleanup_failed'):
            runner.close_worker(tree, process, reader)
        process.kill.assert_called_once()
        process.wait.assert_called_once_with(timeout=5)
        process.stdout.close.assert_called_once()
        self.assertEqual(reader.join.call_count, 2)

    def test_cleanup_requires_reader_to_finish(self):
        tree, process, reader = Mock(), Mock(), Mock()
        process.poll.return_value = 0
        reader.ident = 1
        reader.is_alive.return_value = True
        with self.assertRaisesRegex(ValueError, 'cleanup_failed'):
            runner.close_worker(tree, process, reader)
        process.kill.assert_not_called()

    def test_outer_timeout_closes_owned_worker_and_records_uncertain(self):
        ctx = context()
        store, process, tree = Mock(), Mock(), Mock()
        token = 'b' * 64
        store.reserve.return_value = token
        process.stdout = io.BytesIO(b'')
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired('synthetic', 360), 0]
        with patch.object(ctx.storage, 'Store', return_value=store), \
                patch.object(runner.subprocess, 'Popen', return_value=process), \
                patch.object(ctx.runtime, 'OwnedRuntimeTree', return_value=tree):
            with self.assertRaises(subprocess.TimeoutExpired):
                runner.supervise(ctx)
        tree.assign_and_resume.assert_called_once_with(process)
        tree.close.assert_called()
        process.kill.assert_called_once()
        self.assertTrue(process.stdout.closed)
        store.finish.assert_called_once_with(token, 'uncertain')
        store.close.assert_called_once()

    def test_supervisor_assignment_failure_keeps_uncertain_receipt(self):
        ctx = context()
        store, process, tree = Mock(), Mock(), Mock()
        token = 'b' * 64
        store.reserve.return_value = token
        process.stdout = io.BytesIO(b'')
        process.poll.return_value = None
        tree.assign_and_resume.side_effect = OSError('synthetic assignment failure')
        with patch.object(ctx.storage, 'Store', return_value=store), \
                patch.object(runner.subprocess, 'Popen', return_value=process), \
                patch.object(ctx.runtime, 'OwnedRuntimeTree', return_value=tree):
            with self.assertRaises(OSError):
                runner.supervise(ctx)
        process.kill.assert_called_once()
        tree.close.assert_called_once()
        store.finish.assert_called_once_with(token, 'uncertain')

    def test_supervisor_success_requires_saved_result_chain_and_diagnostic(self):
        ctx = context()
        store, process, tree = Mock(), Mock(), Mock()
        token = 'b' * 64
        store.reserve.return_value = token
        expected = {'public_report_acknowledged': True, 'analysis_id': 'c' * 64,
                    'status': 'published', 'submission_sha256': 'd' * 64}
        store.read.return_value = {'model_started': True, 'model_completed': True}
        process.stdout = io.BytesIO(encode(expected) + b'\n')
        process.poll.return_value = process.returncode = 0
        with patch.object(ctx.storage, 'Store', return_value=store), \
                patch.object(runner.subprocess, 'Popen', return_value=process), \
                patch.object(ctx.runtime, 'OwnedRuntimeTree', return_value=tree), \
                patch.object(runner, 'validate_result', return_value=expected) as validate:
            self.assertEqual(runner.supervise(ctx), expected)
        validate.assert_called_once_with(ctx, store)
        store.read.assert_called_once_with('diagnostic')
        store.finish.assert_called_once_with(token, 'completed')
        process.kill.assert_not_called()

    def test_supervisor_cannot_trust_worker_success_alone(self):
        ctx = context()
        store, process, tree = Mock(), Mock(), Mock()
        token = 'b' * 64
        store.reserve.return_value = token
        process.stdout = io.BytesIO(encode({'public_report_acknowledged': True}) + b'\n')
        process.poll.return_value = process.returncode = 0
        with patch.object(ctx.storage, 'Store', return_value=store), \
                patch.object(runner.subprocess, 'Popen', return_value=process), \
                patch.object(ctx.runtime, 'OwnedRuntimeTree', return_value=tree), \
                patch.object(runner, 'validate_result', side_effect=ValueError('missing_saved_ack')):
            with self.assertRaisesRegex(ValueError, 'missing_saved_ack'):
                runner.supervise(ctx)
        store.finish.assert_called_once_with(token, 'uncertain')


if __name__ == '__main__':
    unittest.main()
