"""Offline local-diagnostic tests: no runtime, network, model, or SSH calls."""
import copy
import io
import json
from pathlib import Path
import queue
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import insights_staging_mcp as adapter
import insights_staging_rejection_probe as probe


def event(code='report_gate_closed', attempt=4, method='prepare'):
    return {'timestamp_utc': '2026-10-14T00:00:00+00:00', 'attempt': attempt,
            'method': method, 'code': code}


class RejectionProbeTests(unittest.TestCase):
    def test_fixture_has_no_transport_and_always_rejects(self):
        reports = probe.ClosedReports()
        for name, arguments in probe.fixed_calls():
            with self.assertRaises(ValueError):
                reports.call(name, arguments)
        self.assertEqual(reports.rejection_code, 'report_gate_closed')
        self.assertEqual(reports.__dict__, {})

    def test_event_schema_rejects_unbounded_or_secret_fields(self):
        self.assertEqual(probe.validate_event(event(), adapter.REJECTION_CODES), event())
        cases = [dict(event(), secret='DO_NOT_LOG'), dict(event(), code='DO_NOT_LOG'),
                 dict(event(), attempt=True), dict(event(), attempt=65), dict(event(), method='DO_NOT_LOG'),
                 dict(event(), timestamp_utc='2026-99-99T00:00:00+00:00')]
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ValueError):
                probe.validate_event(value, adapter.REJECTION_CODES)

    def test_sink_bounds_events_and_permanently_fails(self):
        stream = Mock()
        stream.write.side_effect = len
        sink = probe.EvidenceSink(stream, adapter.REJECTION_CODES)
        with patch.object(probe.os, 'fsync'):
            for attempt in range(1, probe.MAX_EVENTS + 1):
                sink.record(event(attempt=attempt))
            with self.assertRaises(ValueError):
                sink.record(event(attempt=17))
            with self.assertRaises(ValueError):
                sink.record(event(attempt=1))
        self.assertTrue(sink.failed)
        self.assertEqual(stream.write.call_count, probe.MAX_EVENTS)

    def test_sink_error_does_not_reflect_exception_text(self):
        stream = Mock()
        stream.write.side_effect = OSError('SECRET PATH TOKEN')
        sink = probe.EvidenceSink(stream, adapter.REJECTION_CODES)
        with self.assertRaisesRegex(ValueError, '^rejection_probe_sink_failed$'):
            sink.record(event())
        self.assertTrue(sink.failed)

    def test_real_adapter_fixture_logs_gate_but_client_keeps_generic_error(self):
        messages = [
            {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
             'params': {'protocolVersion': '2025-03-26'}},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
             'params': {'name': 'prepare', 'arguments': {}}},
            {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
             'params': {'name': 'prepare', 'arguments': {}, '_meta': {'progressToken': 'SECRET/path'}}},
        ]
        source = io.BytesIO(b''.join(probe.encoded(message) + b'\n' for message in messages))
        destination, stored = io.BytesIO(), io.BytesIO()
        wrapped = Mock(wraps=stored)
        wrapped.fileno.return_value = 1
        with patch.object(probe.os, 'fsync'):
            probe.serve_fixture(adapter, source, destination, wrapped)
        events = [json.loads(line) for line in stored.getvalue().splitlines()]
        self.assertEqual([item['code'] for item in events], ['report_gate_closed', 'protocol_metadata_token'])
        self.assertNotIn(b'SECRET', stored.getvalue())
        self.assertNotIn(b'report_gate_closed', destination.getvalue())
        replies = [json.loads(line) for line in destination.getvalue().splitlines()]
        self.assertEqual(replies[-1]['error']['message'], adapter.ERROR)

    def test_directory_validation_is_scoped_to_fresh_named_temp_child(self):
        with tempfile.TemporaryDirectory(prefix=probe.PREFIX) as directory:
            self.assertEqual(probe.evidence_path(directory), Path(directory) / probe.EVIDENCE_NAME)
            for invalid in (None, '.', str(Path(directory) / 'child'), str(probe.launcher.MATERIAL)):
                with self.subTest(invalid=invalid), self.assertRaises((ValueError, OSError)):
                    probe.evidence_path(invalid)

    def test_discovery_does_not_claim_file_and_multiple_clients_can_discover(self):
        initialize = probe.encoded({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                                    'params': {'protocolVersion': '2025-03-26'}}) + b'\n'
        with tempfile.TemporaryDirectory(prefix=probe.PREFIX) as directory:
            path = Path(directory) / probe.EVIDENCE_NAME
            for _ in range(2):
                probe.serve_file_fixture(adapter, io.BytesIO(initialize), io.BytesIO(), path)
                self.assertFalse(path.exists())
                for name, _ in probe.fixed_calls():
                    self.assertFalse(path.with_name(probe.METADATA_NAME.format(tool=name)).exists())
            rejected = probe.encoded({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
                                      'params': {'name': 'prepare', 'arguments': {}}}) + b'\n'
            probe.serve_file_fixture(adapter, io.BytesIO(initialize + rejected), io.BytesIO(), path)
            original = path.read_bytes()
            metadata_path = path.with_name(probe.METADATA_NAME.format(tool='prepare'))
            original_metadata = metadata_path.read_bytes()
            self.assertEqual(json.loads(original)['code'], 'report_gate_closed')
            with self.assertRaises(FileExistsError):
                probe.serve_file_fixture(adapter, io.BytesIO(initialize + rejected), io.BytesIO(), path)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(metadata_path.read_bytes(), original_metadata)

    def metadata_frame(self, index=0, metadata=None, present=True):
        name, arguments = probe.fixed_calls()[index]
        params = {'name': name, 'arguments': arguments}
        if present:
            params['_meta'] = metadata
        return probe.encoded({'jsonrpc': '2.0', 'id': index + 2,
                              'method': 'tools/call', 'params': params}) + b'\n'

    def test_metadata_source_records_only_top_level_names_and_types(self):
        metadata = {'sessionId': 'SECRET_SESSION_VALUE', 'progressToken': 987654321,
                    'context': {'nestedSecret': 'SECRET_NESTED_VALUE'},
                    'items': ['SECRET_ARRAY_VALUE'], 'enabled': True,
                    'nullable': None, 'fraction': 1.25}
        frame = self.metadata_frame(metadata=metadata)
        source = probe.MetadataSource(io.BytesIO(frame))
        self.assertEqual(source.readline(65536), frame)
        self.assertEqual(source.observations, [{
            'tool': 'prepare', 'metadata_present': True, 'metadata_type': 'object',
            'fields': [{'name': 'context', 'type': 'object'},
                       {'name': 'enabled', 'type': 'boolean'},
                       {'name': 'fraction', 'type': 'number'},
                       {'name': 'items', 'type': 'array'},
                       {'name': 'nullable', 'type': 'null'},
                       {'name': 'progressToken', 'type': 'integer'},
                       {'name': 'sessionId', 'type': 'string'}]}])
        observed = probe.encoded(source.observations)
        for sentinel in (b'SECRET', b'987654321', b'nestedSecret', b'1.25'):
            self.assertNotIn(sentinel, observed)

    def test_metadata_source_preserves_original_wire_bytes(self):
        frame = b'{ "id": 2, "jsonrpc": "2.0", "method": "tools/call", ' \
                b'"params": {"arguments": {}, "name": "prepare", ' \
                b'"_meta": {"sessionId": "SECRET"}} }\r\n'
        source = probe.MetadataSource(io.BytesIO(frame))
        self.assertEqual(source.readline(65536), frame)
        self.assertEqual(source.readline(65536), b'')
        self.assertEqual(len(source.observations), 1)

    def test_metadata_source_distinguishes_absent_null_and_scalar_without_values(self):
        cases = [(None, False, 'null'), (None, True, 'null'),
                 ('SECRET_SCALAR', True, 'string'), (['SECRET_ARRAY'], True, 'array'),
                 (True, True, 'boolean'), (1, True, 'integer'), (1.5, True, 'number')]
        for value, present, expected_type in cases:
            with self.subTest(expected_type=expected_type, present=present):
                frame = self.metadata_frame(metadata=value, present=present)
                source = probe.MetadataSource(io.BytesIO(frame))
                self.assertEqual(source.readline(65536), frame)
                self.assertEqual(source.observations[0], {
                    'tool': 'prepare', 'metadata_present': present,
                    'metadata_type': expected_type, 'fields': []})
                self.assertNotIn(b'SECRET', probe.encoded(source.observations))

    def test_metadata_source_redacts_non_protocol_or_overlong_key_names(self):
        metadata = {'session-123-SECRET': 'SECRET_VALUE', 'κλειδί': 'SECRET_VALUE',
                    'a' * 97: 'SECRET_VALUE', 'x\nSECRET': 'SECRET_VALUE',
                    'openai/session': 'SECRET_VALUE'}
        source = probe.MetadataSource(io.BytesIO(self.metadata_frame(metadata=metadata)))
        source.readline(65536)
        fields = source.observations[0]['fields']
        self.assertEqual(sum(item['name'] == '[redacted-key]' for item in fields), 4)
        self.assertIn({'name': 'openai/session', 'type': 'string'}, fields)
        self.assertNotIn(b'SECRET', probe.encoded(source.observations))

    def test_metadata_source_enforces_fixed_synthetic_arguments_and_order(self):
        invalid_frames = [self.metadata_frame(index=1, metadata={}),
                          probe.encoded({'method': 'tools/call', 'params': {
                              'name': 'prepare', 'arguments': {'unexpected': 'SECRET'}}}) + b'\n',
                          probe.encoded({'method': 'tools/call', 'params': {
                              'name': 'execute', 'arguments': {}}}) + b'\n']
        for frame in invalid_frames:
            source = probe.MetadataSource(io.BytesIO(frame))
            with self.subTest(frame=frame), self.assertRaisesRegex(
                    ValueError, '^rejection_probe_metadata_call_invalid$'):
                source.readline(65536)
            self.assertEqual(source.observations, [])

    def test_metadata_source_limits_two_calls_and_sixteen_fields(self):
        frames = [self.metadata_frame(index=index, metadata={}) for index in (0, 1, 0)]
        source = probe.MetadataSource(io.BytesIO(b''.join(frames)))
        self.assertEqual(source.readline(65536), frames[0])
        self.assertEqual(source.readline(65536), frames[1])
        with self.assertRaisesRegex(ValueError, '^rejection_probe_metadata_limit$'):
            source.readline(65536)
        self.assertEqual(len(source.observations), 2)
        for count in (16, 17):
            frame = self.metadata_frame(metadata={chr(65 + index): None for index in range(count)})
            source = probe.MetadataSource(io.BytesIO(frame))
            if count == 16:
                self.assertEqual(source.readline(65536), frame)
                self.assertEqual(len(source.observations[0]['fields']), count)
            else:
                with self.assertRaisesRegex(ValueError, '^rejection_probe_metadata_limit$'):
                    source.readline(65536)
                self.assertEqual(source.observations, [])

    def test_metadata_source_passes_non_call_invalid_and_partial_frames_unchanged(self):
        frames = [b'not json\n', b'\xff\n', b'{}', b'[]\n',
                  b'{"method":"tools/list","params":{}}\n',
                  b'{"method":"tools/call","params":null}\n']
        for frame in frames:
            source = probe.MetadataSource(io.BytesIO(frame))
            with self.subTest(frame=frame):
                self.assertEqual(source.readline(65536), frame)
                self.assertEqual(source.observations, [])
        source = probe.MetadataSource(io.BytesIO(self.metadata_frame(metadata={})))
        self.assertEqual(source.readline(8), b'{"id":2,')
        self.assertEqual(source.observations, [])

    def test_metadata_retention_is_bounded_exclusive_and_never_writes_empty_discovery(self):
        with tempfile.TemporaryDirectory(prefix=probe.PREFIX) as directory:
            path = Path(directory) / probe.EVIDENCE_NAME
            metadata_path = path.with_name(probe.METADATA_NAME.format(tool='prepare'))
            probe.retain_metadata(path, [])
            self.assertFalse(metadata_path.exists())
            with self.assertRaisesRegex(ValueError, '^rejection_probe_metadata_limit$'):
                probe.retain_metadata(path, [{'tool': 'prepare', 'name': 'x' * probe.MAX_EVIDENCE_BYTES}])
            self.assertFalse(metadata_path.exists())
            source = probe.MetadataSource(io.BytesIO(self.metadata_frame(metadata={'sessionId': 'SECRET'})))
            source.readline(65536)
            probe.retain_metadata(path, source.observations)
            original = metadata_path.read_bytes()
            self.assertNotIn(b'SECRET', original)
            self.assertEqual(json.loads(original), source.observations[0])
            with self.assertRaises(FileExistsError):
                probe.retain_metadata(path, source.observations)
            self.assertEqual(metadata_path.read_bytes(), original)

    def test_metadata_is_fsynced_before_readline_returns_without_waiting_for_eof(self):
        with tempfile.TemporaryDirectory(prefix=probe.PREFIX) as directory:
            path = Path(directory) / probe.EVIDENCE_NAME
            frames = [self.metadata_frame(index=index, metadata={'sessionId': 'SECRET'})
                      for index in (0, 1)]
            source = probe.MetadataSource(io.BytesIO(b''.join(frames)),
                                          lambda item: probe.retain_metadata(path, [item]))
            with patch.object(probe.os, 'fsync', wraps=probe.os.fsync) as fsync:
                for index, (name, _) in enumerate(probe.fixed_calls()):
                    self.assertEqual(source.readline(65536), frames[index])
                    metadata_path = path.with_name(probe.METADATA_NAME.format(tool=name))
                    self.assertEqual(json.loads(metadata_path.read_bytes()), source.observations[index])
                    self.assertEqual(fsync.call_count, index + 1)
                # No EOF read or teardown has occurred; both artifacts already exist.
                self.assertEqual(source.source.tell(), sum(map(len, frames)))

    def test_fixture_persists_metadata_before_reply_and_abrupt_source_termination(self):
        initialize = probe.encoded({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                                    'params': {'protocolVersion': '2025-03-26'}}) + b'\n'
        frame = self.metadata_frame(metadata={'sessionId': 'SECRET'})
        with tempfile.TemporaryDirectory(prefix=probe.PREFIX) as directory:
            path = Path(directory) / probe.EVIDENCE_NAME
            metadata_path = path.with_name(probe.METADATA_NAME.format(tool='prepare'))
            source = Mock()
            source.readline.side_effect = [initialize, frame, RuntimeError('synthetic abrupt termination')]
            stored = io.BytesIO()
            destination = Mock(wraps=stored)

            def write(reply):
                if json.loads(reply)['id'] == 2:
                    self.assertTrue(metadata_path.exists())
                    self.assertEqual(json.loads(metadata_path.read_bytes())['tool'], 'prepare')
                    self.assertGreaterEqual(fsync.call_count, 2)
                return stored.write(reply)

            destination.write.side_effect = write
            with patch.object(probe.os, 'fsync', wraps=probe.os.fsync) as fsync:
                with self.assertRaisesRegex(RuntimeError, '^synthetic abrupt termination$'):
                    probe.serve_file_fixture(adapter, source, destination, path)
            self.assertEqual(json.loads(metadata_path.read_bytes())['tool'], 'prepare')
            self.assertNotIn(b'SECRET', metadata_path.read_bytes())
            self.assertEqual(json.loads(path.read_bytes())['code'], 'protocol_metadata_shape')
            self.assertFalse(path.with_name(probe.METADATA_NAME.format(tool='publish')).exists())

    def test_metadata_retention_rejects_non_fixed_tool_before_opening_file(self):
        with tempfile.TemporaryDirectory(prefix=probe.PREFIX) as directory:
            path = Path(directory) / probe.EVIDENCE_NAME
            for name in ('other', '../prepare', 'prepare/anything', None):
                with self.subTest(name=name), self.assertRaisesRegex(
                        ValueError, '^rejection_probe_metadata_call_invalid$'):
                    probe.retain_metadata(path, [{'tool': name}])
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_metadata_observation_does_not_relax_parser_or_change_generic_error(self):
        initialize = probe.encoded({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                                    'params': {'protocolVersion': '2025-03-26'}}) + b'\n'
        calls = b''.join(self.metadata_frame(index=index, metadata={
            'progressToken': index + 1, 'unsupportedContext': 'SECRET_SESSION_VALUE'}) for index in (0, 1))
        with tempfile.TemporaryDirectory(prefix=probe.PREFIX) as directory:
            path = Path(directory) / probe.EVIDENCE_NAME
            destination = io.BytesIO()
            probe.serve_file_fixture(adapter, io.BytesIO(initialize + calls), destination, path)
            events = [json.loads(line) for line in path.read_bytes().splitlines()]
            self.assertEqual([item['code'] for item in events],
                             ['protocol_metadata_shape', 'protocol_metadata_shape'])
            replies = [json.loads(line) for line in destination.getvalue().splitlines()]
            for reply in replies[1:]:
                self.assertEqual(reply['error']['message'], adapter.ERROR)
            self.assertNotIn(b'protocol_metadata_shape', destination.getvalue())
            metadata_bodies = [path.with_name(probe.METADATA_NAME.format(tool=name)).read_bytes()
                               for name, _ in probe.fixed_calls()]
            self.assertEqual([json.loads(body)['tool'] for body in metadata_bodies], ['prepare', 'publish'])
            for body in (destination.getvalue(), path.read_bytes(), *metadata_bodies):
                self.assertNotIn(b'SECRET', body)

    def test_fixture_config_preserves_original_restrictions(self):
        original = {'permissions': {'profile': {'filesystem': {':root': 'deny'}}},
                    'agents': {'enabled': False}, 'mcp_servers': {
                        probe.launcher.SERVER: {'args': ['original'], 'env': {}, 'command': 'python'},
                        'node_repl': {'enabled': False}}}
        expected = copy.deepcopy(original)
        with tempfile.TemporaryDirectory(prefix=probe.PREFIX) as directory:
            result = probe.fixture_config(original, directory)
            self.assertEqual(result['mcp_servers'][probe.launcher.SERVER]['env'],
                             {probe.DIRECTORY_ENV: directory})
        self.assertEqual(original, expected)
        self.assertEqual(result['permissions'], original['permissions'])
        self.assertEqual(result['agents'], {'enabled': False})
        self.assertEqual(result['mcp_servers']['node_repl'], {'enabled': False})

    def test_diagnostic_config_pin_is_exact_and_only_temporary_input_change(self):
        original_pins = probe.launcher.FILE_PINS
        expected_original = dict(original_pins)
        launcher_bytes = probe.launcher.ENTRY.read_bytes()
        expected_result = ({'synthetic': 'config'}, object())
        self.assertEqual(probe.DIAGNOSTIC_CONFIG_SHA256,
                         '66eb0479fc51a914aae18782cda79de5d107a57fa7ac88f20d938703f6ac3905')

        def inspect_load():
            self.assertIsNot(probe.launcher.FILE_PINS, original_pins)
            self.assertEqual(probe.launcher.FILE_PINS, {
                **expected_original, probe.launcher.USER_CONFIG: probe.DIAGNOSTIC_CONFIG_SHA256})
            self.assertEqual(set(probe.launcher.FILE_PINS), set(expected_original))
            self.assertEqual(original_pins, expected_original)
            return expected_result

        with patch.object(probe.launcher, 'load_inputs', side_effect=inspect_load) as load:
            self.assertIs(probe.load_probe_inputs(), expected_result)
            load.assert_called_once_with()
        self.assertIs(probe.launcher.FILE_PINS, original_pins)
        self.assertEqual(probe.launcher.FILE_PINS, expected_original)
        self.assertEqual(probe.launcher.ENTRY.read_bytes(), launcher_bytes)

    def test_diagnostic_config_pin_restores_original_identity_on_failure(self):
        original_pins = probe.launcher.FILE_PINS
        expected_original = dict(original_pins)
        for error in (probe.launcher.GuardFailure('input_hash_changed'), RuntimeError('synthetic')):
            def fail_load():
                self.assertEqual(probe.launcher.FILE_PINS[probe.launcher.USER_CONFIG],
                                 probe.DIAGNOSTIC_CONFIG_SHA256)
                raise error

            with self.subTest(error=type(error).__name__), \
                    patch.object(probe.launcher, 'load_inputs', side_effect=fail_load):
                with self.assertRaises(type(error)) as observed:
                    probe.load_probe_inputs()
                self.assertIs(observed.exception, error)
            self.assertIs(probe.launcher.FILE_PINS, original_pins)
            self.assertEqual(probe.launcher.FILE_PINS, expected_original)

    def test_observed_thread_id_shape_and_legacy_shape_both_reach_closed_gate(self):
        initialize = probe.encoded({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                                    'params': {'protocolVersion': '2025-03-26'}}) + b'\n'
        metadata = {'progressToken': 1, 'threadId': 'SYNTHETIC_THREAD_VALUE'}
        original_metadata = dict(metadata)
        for include_thread_id in (True, False):
            current = metadata if include_thread_id else {'progressToken': metadata['progressToken']}
            frames = b''.join(self.metadata_frame(index=index, metadata=current) for index in (0, 1))
            source = probe.MetadataSource(io.BytesIO(initialize + frames))
            destination, stored = io.BytesIO(), io.BytesIO()
            wrapped = Mock(wraps=stored)
            wrapped.fileno.return_value = 1
            with self.subTest(include_thread_id=include_thread_id), patch.object(probe.os, 'fsync'):
                probe.serve_fixture(adapter, source, destination, wrapped)
            events = [json.loads(line) for line in stored.getvalue().splitlines()]
            self.assertEqual([item['code'] for item in events], ['report_gate_closed', 'report_gate_closed'])
            for observation in source.observations:
                expected_fields = [{'name': 'progressToken', 'type': 'integer'}]
                if include_thread_id:
                    expected_fields.append({'name': 'threadId', 'type': 'string'})
                self.assertEqual(observation['fields'], expected_fields)
            for reply in map(json.loads, destination.getvalue().splitlines()[1:]):
                self.assertEqual(reply['error']['message'], adapter.ERROR)
            for body in (stored.getvalue(), destination.getvalue(), probe.encoded(source.observations)):
                self.assertNotIn(b'SYNTHETIC_THREAD_VALUE', body)
        self.assertEqual(metadata, original_metadata)

    def session(self):
        session = object.__new__(probe.DirectSession)
        session.thread_id, session.call_index, session.counter = 'synthetic-thread', 0, 0
        session.process = SimpleNamespace(stdin=io.BytesIO())
        session.deadline = probe.time.monotonic() + 5
        session.inbox = queue.Queue()
        return session

    def test_no_generation_or_arbitrary_rpc_can_be_sent(self):
        session = self.session()
        for method in ('turn/start', 'thread/resume', 'fs/readFile', 'command/exec', 'model/list'):
            with self.subTest(method=method), self.assertRaises(ValueError):
                session.send({'id': 1, 'method': method, 'params': {}})
            with self.subTest(request=method), self.assertRaises(ValueError):
                session.request(method, {})
        self.assertEqual(session.process.stdin.getvalue(), b'')

    def test_direct_calls_are_exact_order_and_at_most_two(self):
        session = self.session()
        for index, (name, arguments) in enumerate(probe.fixed_calls(), start=1):
            session.send({'id': index, 'method': probe.METHOD, 'params': {
                'threadId': session.thread_id, 'server': probe.launcher.SERVER,
                'tool': name, 'arguments': arguments}})
        self.assertEqual(session.call_index, 2)
        with self.assertRaises(ValueError):
            session.send({'id': 3, 'method': probe.METHOD, 'params': {}})
        self.assertEqual(len(session.process.stdin.getvalue().splitlines()), 2)

    def test_direct_call_rejects_target_argument_and_thread_override(self):
        for key, value in (('threadId', 'existing-chat'), ('server', 'other'), ('tool', 'exec'),
                           ('arguments', {'command': 'anything'})):
            session = self.session()
            params = {'threadId': session.thread_id, 'server': probe.launcher.SERVER,
                      'tool': 'prepare', 'arguments': {}}
            params[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                session.send({'id': 1, 'method': probe.METHOD, 'params': params})
            self.assertEqual(session.call_index, 0)

    def test_failed_send_is_consumed_without_retry(self):
        session = self.session()
        session.process.stdin = Mock()
        session.process.stdin.write.side_effect = OSError('synthetic')
        with self.assertRaises(OSError):
            session.send({'id': 1, 'method': probe.METHOD, 'params': {
                'threadId': session.thread_id, 'server': probe.launcher.SERVER,
                'tool': 'prepare', 'arguments': {}}})
        self.assertEqual(session.call_index, 1)

    def test_call_recognizes_generic_error_without_retaining_wrapper(self):
        session = self.session()
        session.inbox.put({'id': 1, 'error': {'code': -32603, 'message': adapter.ERROR}})
        self.assertIsNone(session.call_fixture('prepare', {}, adapter.ERROR))
        self.assertEqual(session.call_index, 1)

    def test_call_refuses_approval_requests_generation_and_success(self):
        for value in ({'id': 1, 'method': 'approval', 'params': {}},
                      {'method': 'turn/started', 'params': {}},
                      {'id': 1, 'result': {'content': []}}):
            session = self.session()
            session.inbox.put(value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                session.call_fixture('prepare', {}, adapter.ERROR)

    def test_cli_failure_does_not_expose_arbitrary_exception_content(self):
        for error in (OSError('SECRET'), ValueError('rejection_probe_secret'),
                      probe.launcher.GuardFailure('private_path')):
            self.assertEqual(probe.failure_code(error), 'rejection_probe_failed')
        self.assertEqual(probe.failure_code(probe.launcher.GuardFailure('rejection_probe_timeout')),
                         'rejection_probe_timeout')

    def test_reader_requires_exact_bounded_evidence(self):
        with tempfile.TemporaryDirectory(prefix=probe.PREFIX) as directory:
            path = Path(directory) / probe.EVIDENCE_NAME
            with path.open('xb') as stream:
                stream.write(probe.encoded(event()) + b'\n')
                stream.write(probe.encoded(event('protocol_call_shape', 5, 'unknown')) + b'\n')
            observed = probe.read_evidence(path, adapter.REJECTION_CODES)
            self.assertEqual([item['code'] for item in observed], ['report_gate_closed', 'protocol_call_shape'])


if __name__ == '__main__':
    unittest.main()
