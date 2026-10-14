"""Offline integration regression tests: no SSH, models, keys or live binding."""
import copy
import hashlib
import io
import json
from pathlib import Path
import runpy
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, call, patch

import insights_broker as contract
from insights_analysis import analyze
import insights_staging_integration as integration
from test_insights_publish import localized


def binding_fields():
    return {'schema_version': 1, 'scope': 'staging', 'synthetic': True,
            'host': '192.0.2.10', 'port': 22,
            'ssh_sha256': 'a' * 64, 'known_hosts_sha256': 'b' * 64}


def destination():
    return integration.transport.Destination('192.0.2.10', 22, 'a' * 64, 'b' * 64)


def event(**changes):
    result = {'timestamp_utc': '2026-10-14T12:00:00+00:00',
              'event': 'staging_transport', 'attempt': 1,
              'method': 'prepare', 'outcome': 'started'}
    result.update(changes)
    return result


class BindingTests(unittest.TestCase):
    def setUp(self):
        self.ssh = self.enterContext(patch.object(integration.transport, 'StagingSSH'))
        self.raw = contract.encode(binding_fields())

    def test_exact_staging_binding_becomes_fixed_destination(self):
        self.assertEqual(integration.parse_binding(self.raw), destination())
        self.ssh.assert_not_called()

    def test_missing_or_extra_fields_rejected(self):
        for field in binding_fields():
            missing = binding_fields()
            del missing[field]
            with self.subTest(missing=field), self.assertRaises(ValueError):
                integration.parse_binding(contract.encode(missing))
        for field in ('user', 'key', 'command', 'password', 'path', 'url'):
            with self.subTest(extra=field), self.assertRaises(ValueError):
                integration.parse_binding(contract.encode({**binding_fields(), field: 'ignored'}))

    def test_scope_schema_and_synthetic_flags_are_exact(self):
        for key, value in [('scope', 'production'), ('scope', 'Staging'),
                           ('synthetic', False), ('synthetic', 1), ('synthetic', 'true'),
                           ('schema_version', True), ('schema_version', 1.0),
                           ('schema_version', 2)]:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                integration.parse_binding(contract.encode({**binding_fields(), key: value}))

    def test_destination_validation_rejects_noncanonical_values(self):
        for key, value in [('host', 'example.invalid'), ('host', '-F injected'),
                           ('host', '0.0.0.0'), ('host', '224.0.0.1'),
                           ('host', '192.000.002.010'), ('host', 10),
                           ('port', True), ('port', 0), ('port', 65536),
                           ('port', '22'), ('ssh_sha256', 'A' * 64),
                           ('ssh_sha256', 'a' * 63), ('known_hosts_sha256', None)]:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                integration.parse_binding(contract.encode({**binding_fields(), key: value}))

    def test_malformed_duplicate_and_oversized_binding_rejected(self):
        for raw in (b'[]', b'null', b'{}', b'{"schema_version":1,"schema_version":1}',
                    b'{"port":NaN}', b'\xff', b' ' * (integration.BINDING_LIMIT + 1)):
            with self.subTest(raw=raw[:40]), self.assertRaises(ValueError):
                integration.parse_binding(raw)

    def local_binding(self, *, raw=None, digest=None):
        raw = self.raw if raw is None else raw
        fake_binding = MagicMock(spec=Path)
        fake_binding.open.return_value = io.BytesIO(raw)
        fake_key = MagicMock(spec=Path)
        self.enterContext(patch.object(integration, 'os', SimpleNamespace(name='nt')))
        self.enterContext(patch.object(integration, 'BINDING', fake_binding))
        self.enterContext(patch.object(integration, 'BINDING_SHA256',
            hashlib.sha256(raw).hexdigest() if digest is None else digest))
        self.enterContext(patch.object(integration.transport, 'KEY', fake_key))
        acl = self.enterContext(patch.object(integration.acl, 'verify_permissions'))
        regular = self.enterContext(patch.object(integration.transport, 'regular_file'))
        material = self.enterContext(patch.object(integration.transport, 'verify_material'))
        return fake_binding, fake_key, acl, regular, material

    def test_local_binding_checks_acl_metadata_hash_and_material_without_key_read(self):
        binding, key, acl, regular, material = self.local_binding()
        self.assertEqual(integration.load_destination(), destination())
        acl.assert_called_once_with()
        regular.assert_called_once_with(binding, integration.BINDING_LIMIT)
        binding.open.assert_called_once_with('rb')
        material.assert_called_once_with(destination())
        key.open.assert_not_called()
        key.read_bytes.assert_not_called()
        key.read_text.assert_not_called()
        self.ssh.assert_not_called()

    def test_acl_rejection_precedes_binding_read_and_material_verification(self):
        binding, _, acl, regular, material = self.local_binding()
        acl.side_effect = contract.Rejected('denied')
        with self.assertRaises(ValueError):
            integration.load_destination()
        regular.assert_not_called()
        binding.open.assert_not_called()
        material.assert_not_called()
        self.ssh.assert_not_called()

    def test_binding_metadata_rejection_precedes_read(self):
        binding, _, _, regular, material = self.local_binding()
        regular.side_effect = contract.Rejected('denied')
        with self.assertRaises(ValueError):
            integration.load_destination()
        binding.open.assert_not_called()
        material.assert_not_called()

    def test_changed_binding_hash_rejected_before_material_verification(self):
        _, _, _, _, material = self.local_binding(digest='0' * 64)
        with self.assertRaises(ValueError):
            integration.load_destination()
        material.assert_not_called()
        self.ssh.assert_not_called()

    def test_correct_pin_cannot_authorize_invalid_binding_schema(self):
        _, _, _, _, material = self.local_binding(raw=contract.encode(
            {**binding_fields(), 'scope': 'production'}))
        with self.assertRaises(ValueError):
            integration.load_destination()
        material.assert_not_called()

    def test_material_pin_rejection_propagates_without_ssh(self):
        _, _, _, _, material = self.local_binding()
        material.side_effect = contract.Rejected('pin mismatch')
        with self.assertRaises(ValueError):
            integration.load_destination()
        self.ssh.assert_not_called()

    def test_nonwindows_platform_rejected_before_reading_anything(self):
        binding, _, acl, regular, material = self.local_binding()
        with patch.object(integration, 'os', SimpleNamespace(name='posix')):
            with self.assertRaises(ValueError):
                integration.load_destination()
        acl.assert_not_called()
        regular.assert_not_called()
        binding.open.assert_not_called()
        material.assert_not_called()

    def test_local_check_never_constructs_session_or_ssh(self):
        with patch.object(integration, 'load_destination', return_value=destination()) as load, \
                patch.object(integration, 'StagingSession') as session:
            result = integration.check_integration()
        load.assert_called_once_with()
        session.assert_not_called()
        self.ssh.assert_not_called()
        self.assertEqual(result, {'integration_prepared': True,
            'private_binding_verified': True, 'material_acl_verified': True,
            'material_pins_verified': True, 'ssh_connected': False,
            'model_started': False, 'report_calls_enabled': False,
            'schedule_changed': False})


class AuditTests(unittest.TestCase):
    def test_valid_fixed_fields_only(self):
        sink = io.BytesIO()
        audit = integration.AuditLog(sink)
        audit.record(event())
        audit.record(event(event='staging_adapter', method='unknown', outcome='rejected'))
        self.assertEqual([json.loads(line) for line in sink.getvalue().splitlines()],
                         [event(), event(event='staging_adapter', method='unknown', outcome='rejected')])

    def test_invalid_events_disable_audit_before_any_write(self):
        for changes in ({'path': 'secret'}, {'timestamp_utc': '2026-10-14'},
                        {'timestamp_utc': '2026-10-14T12:00:00+03:00'},
                        {'event': 'private'}, {'attempt': True}, {'attempt': 0},
                        {'attempt': integration.adapter.MAX_CALLS + 1},
                        {'method': 'exec'}, {'outcome': 'published'},
                        {'event': 'staging_adapter', 'attempt': 65},
                        {'event': 'staging_adapter', 'outcome': 'started'}):
            with self.subTest(changes=changes):
                sink = io.BytesIO()
                audit = integration.AuditLog(sink)
                with self.assertRaises(ValueError):
                    audit.record(event(**changes))
                self.assertTrue(audit.failed)
                self.assertEqual(sink.getvalue(), b'')
                with self.assertRaises(ValueError):
                    audit.record(event())

    def test_short_write_or_flush_failure_permanently_disables_audit(self):
        for failure in ('write', 'short', 'flush'):
            with self.subTest(failure=failure):
                sink = MagicMock()
                sink.write.side_effect = (OSError('sensitive sink error') if failure == 'write'
                                          else lambda data: len(data) - (failure == 'short'))
                if failure == 'flush':
                    sink.flush.side_effect = OSError('sensitive sink error')
                audit = integration.AuditLog(sink)
                with self.assertRaisesRegex(contract.Rejected, integration.adapter.ERROR):
                    audit.record(event())
                self.assertTrue(audit.failed)
                writes = sink.write.call_count
                with self.assertRaises(ValueError):
                    audit.record(event())
                self.assertEqual(sink.write.call_count, writes)

    def test_event_and_byte_budgets_are_fail_closed(self):
        for field, limit in (('_events', integration.MAX_AUDIT_EVENTS),
                             ('_bytes', integration.MAX_AUDIT_BYTES)):
            with self.subTest(field=field):
                sink = io.BytesIO()
                audit = integration.AuditLog(sink)
                setattr(audit, field, limit)
                with self.assertRaises(ValueError):
                    audit.record(event())
                self.assertEqual(sink.getvalue(), b'')
                self.assertTrue(audit.failed)

    def test_missing_or_model_stdout_sink_rejected(self):
        for sink in (None, object(), SimpleNamespace(write=lambda _: None)):
            with self.subTest(sink=type(sink).__name__), self.assertRaises(ValueError):
                integration.AuditLog(sink)
        sink = io.BytesIO()
        with patch.object(integration, 'sys', SimpleNamespace(stdout=SimpleNamespace(buffer=sink))):
            with self.assertRaises(ValueError):
                integration.AuditLog(sink)


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.load = self.enterContext(patch.object(integration, 'load_destination', return_value=destination()))
        self.clock = self.enterContext(patch.object(integration.time, 'monotonic', return_value=100.0))
        self.ssh = self.enterContext(patch.object(integration.transport, 'StagingSSH'))
        self.client = self.ssh.return_value
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        history = Path(temporary.name).resolve() / 'history'
        history.mkdir()
        analysis = analyze(history, 1791882000)
        identifier = hashlib.sha256(contract.encode(analysis)).hexdigest()
        self.snapshot = {'scope': 'staging', 'synthetic': True,
                         'result': {'analysis_id': identifier, 'analysis': analysis}}
        self.payload = {'analysis_id': identifier, 'summary': localized('SYNTHETIC_PRIVATE_PROSE'), 'notes': []}
        self.ack = {'scope': 'staging', 'synthetic': True,
                    'result': {'status': 'staged', 'id': identifier}}
        self.client.side_effect = lambda method, _: contract.encode(self.snapshot if method == 'prepare' else self.ack)
        self.sink = io.BytesIO()
        self.audit = integration.AuditLog(self.sink)
        self.session = integration.StagingSession(self.audit)
        self.addCleanup(self.session.close)

    def test_construction_checks_only_local_material(self):
        self.load.assert_called_once_with()
        self.ssh.assert_not_called()

    def test_one_frozen_analysis_cached_with_defensive_copy(self):
        result = self.session.call('prepare', {})
        result['result']['analysis']['coverage_pct'] = 99
        second = self.session.call('prepare', {})
        self.assertEqual(second, self.snapshot)
        self.client.assert_called_once_with('prepare', b'')
        self.assertEqual(self.load.call_count, 2)

    def test_publish_and_identical_retry_only(self):
        self.session.call('prepare', {})
        self.session.call('publish', self.payload)
        self.ack['result']['status'] = 'already_staged'
        retry = self.session.call('publish', copy.deepcopy(self.payload))
        self.assertEqual(retry['result']['status'], 'already_staged')
        changed = {**self.payload, 'summary': localized('CHANGED_PRIVATE_PROSE')}
        with self.assertRaises(ValueError):
            self.session.call('publish', changed)
        body = contract.encode(self.payload)
        self.assertEqual(self.client.call_args_list,
                         [call('prepare', b''), call('publish', body), call('publish', body)])

    def test_failed_ack_requires_identical_retry(self):
        self.session.call('prepare', {})
        self.ack['result']['status'] = 'published'
        with self.assertRaises(ValueError):
            self.session.call('publish', self.payload)
        with self.assertRaises(ValueError):
            self.session.call('publish', {**self.payload, 'summary': localized('CHANGED_PRIVATE_PROSE')})
        self.assertEqual(self.client.call_count, 2)
        self.ack['result']['status'] = 'already_staged'
        self.assertEqual(self.session.call('publish', self.payload)['result']['status'], 'already_staged')

    def test_lost_transport_response_requires_identical_retry(self):
        self.session.call('prepare', {})
        original = self.client.side_effect
        self.client.side_effect = OSError('PRIVATE_SSH_DIAGNOSTIC')
        with self.assertRaisesRegex(contract.Rejected, integration.adapter.ERROR):
            self.session.call('publish', self.payload)
        self.client.side_effect = original
        with self.assertRaises(ValueError):
            self.session.call('publish', {**self.payload, 'summary': localized('Changed')})
        self.session.call('publish', self.payload)
        self.assertEqual(self.client.call_count, 3)
        self.assertNotIn(b'PRIVATE_SSH_DIAGNOSTIC', self.sink.getvalue())

    def test_four_attempt_budget_includes_upstream_failures(self):
        self.client.side_effect = OSError('synthetic failure')
        for _ in range(integration.adapter.MAX_CALLS):
            with self.assertRaises(ValueError):
                self.session.call('prepare', {})
        with self.assertRaises(ValueError):
            self.session.call('prepare', {})
        self.assertEqual(self.client.call_count, 4)
        self.assertEqual(self.load.call_count, 5)
        self.assertEqual(self.session.transport.attempt, 4)

    def test_invalid_request_never_reaches_transport(self):
        for name, arguments in [('exec', {}), ('prepare', {'host': '192.0.2.11'}),
                                ('publish', self.payload)]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.session.call(name, arguments)
        self.client.assert_not_called()
        self.ssh.assert_not_called()
        self.assertEqual(self.sink.getvalue(), b'')

    def test_expired_session_cannot_use_transport_or_cached_prepare(self):
        self.session.call('prepare', {})
        count = self.load.call_count
        self.session.transport.deadline = 99
        for name, arguments in [('prepare', {}), ('publish', self.payload)]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.session.call(name, arguments)
        self.assertEqual(self.load.call_count, count)
        self.client.assert_called_once()

    def test_remaining_lifetime_reserves_complete_ssh_timeout_and_checks(self):
        self.session.transport.deadline = 100 + integration.transport.SSH_SECONDS
        with self.assertRaises(ValueError):
            self.session.call('prepare', {})
        self.load.assert_called_once_with()
        self.ssh.assert_not_called()

    def test_local_checks_cannot_consume_remaining_ssh_window(self):
        def checks():
            self.clock.return_value = self.session.transport.deadline - integration.transport.SSH_SECONDS + 1
            return destination()
        self.load.side_effect = checks
        with self.assertRaises(ValueError):
            self.session.call('prepare', {})
        self.ssh.assert_not_called()

    def test_expired_return_is_not_accepted_or_cached(self):
        def late_reply(*_):
            self.clock.return_value = self.session.transport.deadline + 1
            return contract.encode(self.snapshot)
        self.client.side_effect = late_reply
        with self.assertRaises(ValueError):
            self.session.call('prepare', {})
        self.assertIsNone(self.session.reports._prepared)

    def test_pins_and_acl_checked_before_every_actual_upstream_call(self):
        observations = []
        def checked():
            observations.append('local_acl_and_pins')
            return destination()
        def upstream(method, _):
            observations.append(method)
            return contract.encode(self.snapshot if method == 'prepare' else self.ack)
        self.load.side_effect = checked
        self.client.side_effect = upstream
        self.session.call('prepare', {})
        self.session.call('prepare', {})
        self.session.call('publish', self.payload)
        self.session.call('publish', self.payload)
        self.assertEqual(observations, ['local_acl_and_pins', 'prepare',
                                       'local_acl_and_pins', 'publish',
                                       'local_acl_and_pins', 'publish'])

    def test_changed_binding_destination_prevents_next_upstream_call(self):
        self.session.call('prepare', {})
        self.load.return_value = integration.transport.Destination('192.0.2.11', 22, 'a' * 64, 'b' * 64)
        with self.assertRaises(ValueError):
            self.session.call('publish', self.payload)
        self.client.assert_called_once()
        self.assertEqual(self.session.transport.attempt, 2)

    def test_local_revalidation_error_prevents_upstream_and_is_sanitized(self):
        self.load.side_effect = contract.Rejected('PRIVATE_ACL_DIAGNOSTIC')
        with self.assertRaisesRegex(contract.Rejected, integration.adapter.ERROR):
            self.session.call('prepare', {})
        self.ssh.assert_not_called()
        self.assertNotIn(b'PRIVATE_ACL_DIAGNOSTIC', self.sink.getvalue())

    def test_audit_prewrite_failure_prevents_ssh_and_disables_session(self):
        self.sink.close()
        with self.assertRaises(ValueError):
            self.session.call('prepare', {})
        self.ssh.assert_not_called()
        self.assertTrue(self.audit.failed)
        self.load.assert_called_once_with()
        with self.assertRaises(ValueError):
            self.session.call('prepare', {})
        self.assertEqual(self.session.transport.attempt, 1)

    def test_audit_postwrite_failure_blocks_all_further_calls(self):
        sink = MagicMock()
        writes = []
        def write(data):
            writes.append(data)
            if len(writes) == 2:
                raise OSError('PRIVATE_AUDIT_DETAIL')
            return len(data)
        sink.write.side_effect = write
        audit = integration.AuditLog(sink)
        session = integration.StagingSession(audit)
        self.addCleanup(session.close)
        with self.assertRaisesRegex(contract.Rejected, integration.adapter.ERROR):
            session.call('prepare', {})
        self.assertTrue(audit.failed)
        self.client.assert_called_once()
        with self.assertRaises(ValueError):
            session.call('prepare', {})
        self.client.assert_called_once()
        self.assertIsNone(session.reports._prepared)

    def test_closed_session_cannot_return_cached_prepare(self):
        self.session.call('prepare', {})
        self.session.close()
        self.session.close()
        with self.assertRaises(ValueError):
            self.session.call('prepare', {})
        self.assertTrue(self.session.closed)
        self.assertTrue(self.session.transport.closed)
        self.client.assert_called_once()

    def test_audit_has_only_fixed_fields_and_no_body_prose_or_destination(self):
        self.session.call('prepare', {})
        self.session.call('publish', self.payload)
        log = self.sink.getvalue()
        events = [json.loads(line) for line in log.splitlines()]
        self.assertEqual([item['outcome'] for item in events], ['started', 'returned', 'started', 'returned'])
        self.assertTrue(all(set(item) == {'timestamp_utc', 'event', 'attempt', 'method', 'outcome'} for item in events))
        for forbidden in (b'SYNTHETIC_PRIVATE_PROSE', self.payload['analysis_id'].encode(),
                          b'192.0.2.10', b'id_ed25519', b'known_hosts', b'analysis', b'summary'):
            self.assertNotIn(forbidden, log)

    def test_session_requires_exact_healthy_audit_object(self):
        for audit in (MagicMock(), None):
            with self.subTest(audit=type(audit).__name__), self.assertRaises(ValueError):
                integration.StagingSession(audit)
        self.audit.failed = True
        with self.assertRaises(ValueError):
            integration.StagingSession(self.audit)

    @staticmethod
    def initialize():
        return {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                'params': {'protocolVersion': '2025-03-26', 'capabilities': {},
                           'clientInfo': {'name': 'offline-integration-test'}}}

    def protocol_source(self):
        requests = [self.initialize(), {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
                    {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'},
                    {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
                     'params': {'name': 'prepare', 'arguments': {}}},
                    {'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call',
                     'params': {'name': 'publish', 'arguments': self.payload}},
                    {'jsonrpc': '2.0', 'id': 5, 'method': 'tools/call',
                     'params': {'name': 'publish', 'arguments': self.payload}}]
        return io.BytesIO(b''.join(contract.encode(item) + b'\n' for item in requests))

    def test_full_in_memory_mcp_roundtrip_and_close(self):
        target = io.BytesIO()
        with patch.object(integration, 'StagingSession', return_value=self.session):
            integration.serve_staging(self.protocol_source(), target, self.audit)
        responses = [json.loads(line) for line in target.getvalue().splitlines()]
        self.assertEqual(len(responses), 5)
        self.assertEqual(responses[1]['result']['tools'], integration.adapter.TOOLS)
        self.assertEqual(json.loads(responses[-1]['result']['content'][0]['text'])['result']['status'], 'staged')
        self.assertEqual(self.client.call_count, 3)
        self.assertTrue(self.session.closed)
        events = [json.loads(line) for line in self.sink.getvalue().splitlines()]
        self.assertEqual(sum(item['event'] == 'staging_adapter' for item in events), 5)
        self.assertEqual(sum(item['event'] == 'staging_transport' for item in events), 6)
        self.assertNotIn(b'SYNTHETIC_PRIVATE_PROSE', self.sink.getvalue())

    def test_server_closes_session_when_output_or_source_fails(self):
        for failure in ('read', 'write', 'flush'):
            with self.subTest(failure=failure):
                session = integration.StagingSession(integration.AuditLog(io.BytesIO()))
                source, target = self.protocol_source(), MagicMock()
                if failure == 'read':
                    source = MagicMock()
                    source.readline.side_effect = OSError('synthetic read error')
                else:
                    getattr(target, failure).side_effect = OSError('synthetic output error')
                with patch.object(integration, 'StagingSession', return_value=session):
                    with self.assertRaises(OSError):
                        integration.serve_staging(source, target, session.audit)
                self.assertTrue(session.closed)
                self.assertTrue(session.transport.closed)

    def test_server_closes_session_when_adapter_audit_fails(self):
        self.sink.close()
        with patch.object(integration, 'StagingSession', return_value=self.session):
            with self.assertRaises(ValueError):
                integration.serve_staging(self.protocol_source(), io.BytesIO(), self.audit)
        self.assertTrue(self.session.closed)
        self.ssh.assert_not_called()

    def test_entrypoint_is_disabled_without_subprocess_or_live_mode(self):
        output, errors = io.StringIO(), io.StringIO()
        with patch('sys.stdout', output), patch('sys.stderr', errors), \
                patch('sys.argv', ['insights_staging_integration.py', '--serve']):
            with self.assertRaises(SystemExit) as stopped:
                runpy.run_path(str(Path(integration.__file__)), run_name='__main__')
        self.assertEqual(stopped.exception.code, 1)
        self.assertEqual(output.getvalue(), '')
        self.assertEqual(errors.getvalue().strip(), 'Staging execution is not activated.')
        self.ssh.assert_not_called()


if __name__ == '__main__':
    unittest.main()
