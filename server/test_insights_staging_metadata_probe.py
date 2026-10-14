"""Offline metadata-path tests; no runtime, network, model or SSH invocation."""
import ast
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import insights_staging_mcp as adapter
import insights_staging_metadata_probe as probe
import insights_staging_rejection_probe as capture
import insights_staging_runtime_probe as runtime


def metadata_frame(index=0, metadata=None, arguments=None):
    name, expected = probe.fixed_calls()[index]
    return capture.encoded({'jsonrpc': '2.0', 'id': index + 2, 'method': 'tools/call',
        'params': {'name': name, 'arguments': expected if arguments is None else arguments,
                   '_meta': metadata}}) + b'\n'


def shape(tool='prepare'):
    return {'tool': tool, 'metadata_present': True, 'metadata_type': 'object', 'fields': [
        {'name': 'codex/sandbox-state', 'type': 'object'},
        {'name': 'progressToken', 'type': 'integer'},
        {'name': 'threadId', 'type': 'string'}]}


class MetadataProbeTests(unittest.TestCase):
    def test_fixed_calls_match_pinned_simulator_plan(self):
        self.assertIsNone(probe.verify_plan(runtime))
        calls = probe.fixed_calls()
        self.assertEqual([name for name, _ in calls], ['prepare', 'publish'])
        self.assertEqual(calls[0][1], {})
        self.assertEqual(calls[1][1]['analysis_id'], 'a' * 64)
        self.assertEqual(set(calls[1][1]['summary']), {'en', 'el', 'ru'})
        self.assertEqual(calls[1][1]['notes'], [])
        calls[1][1]['notes'].append('MUTATION')
        self.assertEqual(probe.fixed_calls()[1][1]['notes'], [])

    def test_plan_drift_is_rejected(self):
        original = runtime.plans()
        for changed in (original[:-1], list(reversed(original)), original + [original[-1]]):
            with self.subTest(length=len(changed)), patch.object(runtime, 'plans', return_value=changed):
                with self.assertRaisesRegex(ValueError, '^metadata_probe_plan_changed$'):
                    probe.verify_plan(runtime)

    def test_helper_pins_match_files_without_loading_runtime(self):
        self.assertEqual(set(probe.HELPER_PINS), {
            'insights_staging_rejection_probe', 'insights_staging_runtime_probe'})
        for name, expected in probe.HELPER_PINS.items():
            with self.subTest(module=name):
                source = probe.launcher.SOURCE / (name + '.py')
                self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), expected)

    def test_all_helper_pins_are_checked_before_any_import(self):
        order = []
        modules = {name: SimpleNamespace(__file__=str(probe.launcher.SOURCE / (name + '.py')))
                   for name in probe.HELPER_PINS}
        with patch.object(probe.launcher, 'check_pin', side_effect=lambda *args: order.append('pin')), \
                patch.object(probe.importlib, 'import_module', side_effect=lambda name: (
                    order.append('import') or modules[name])):
            self.assertEqual(probe.load_helpers(), tuple(modules.values()))
        self.assertEqual(order, ['pin', 'pin', 'import', 'import'])

    def test_helper_pin_failure_prevents_import(self):
        with patch.object(probe.launcher, 'check_pin', side_effect=ValueError('pin')), \
                patch.object(probe.importlib, 'import_module') as imported:
            with self.assertRaises(ValueError):
                probe.load_helpers()
        imported.assert_not_called()

    def test_helper_import_origin_is_checked(self):
        with patch.object(probe.launcher, 'check_pin'), \
                patch.object(probe.importlib, 'import_module', return_value=SimpleNamespace(
                    __file__=str(probe.launcher.SOURCE / 'unexpected.py'))):
            with self.assertRaisesRegex(ValueError, '^metadata_probe_import_origin$'):
                probe.load_helpers()

    def test_probe_does_not_import_reporting_run_audit_or_integration(self):
        tree = ast.parse(probe.ENTRY.read_text(encoding='utf-8'))
        imports = []
        for item in ast.walk(tree):
            if isinstance(item, ast.Import):
                imports.extend(alias.name for alias in item.names)
            elif isinstance(item, ast.ImportFrom):
                imports.append(item.module or '')
        for forbidden in ('insights_staging_run', 'insights_staging_run_audit',
                          'insights_staging_integration', 'insights_staging_transport'):
            self.assertNotIn(forbidden, imports)
            self.assertNotIn(forbidden, probe.HELPER_PINS)
        self.assertNotIn('subprocess', imports)
        self.assertNotIn('socket', imports)

    def test_fixture_config_is_separate_and_preserves_restrictions(self):
        original = {'permissions': {'test': {'filesystem': {':root': 'deny'}, 'network': 'deny'}},
            'agents': {'enabled': False}, 'notify': [], 'web_search': 'disabled',
            'mcp_servers': {probe.launcher.SERVER: {'command': 'python', 'args': ['original'],
                'env': {}}, 'node_repl': {'enabled': False}}}
        expected = copy.deepcopy(original)
        with tempfile.TemporaryDirectory(prefix=capture.PREFIX) as directory:
            result = probe.fixture_config(capture, original, directory)
            active = result['mcp_servers'][probe.launcher.SERVER]
            self.assertEqual(active['args'], ['-I', '-B', str(probe.ENTRY), '--mcp-fixture'])
            self.assertEqual(active['env'], {capture.DIRECTORY_ENV: directory})
        self.assertEqual(original, expected)
        self.assertIsNot(result, original)
        for key in ('permissions', 'agents', 'notify', 'web_search'):
            self.assertEqual(result[key], expected[key])
        self.assertEqual(result['mcp_servers']['node_repl'], {'enabled': False})
        self.assertEqual(active['command'], 'python')

    def test_fixture_uses_fixed_plan_and_preserves_wire_bytes(self):
        frames = b''.join(metadata_frame(index, {'progressToken': index + 1,
            'threadId': 'PRIVATE_THREAD_VALUE',
            'codex/sandbox-state': {'private_nested_name': ['PRIVATE_NESTED_VALUE']}})
            for index in (0, 1))
        fake_adapter = Mock()
        captured = []

        def inspect(received_adapter, source, destination, path, *, source_type):
            self.assertIs(received_adapter, fake_adapter)
            wrapped = source_type(source)
            for line in frames.splitlines(keepends=True):
                self.assertEqual(wrapped.readline(65536), line)
            self.assertEqual(wrapped.readline(65536), b'')
            captured.extend(wrapped.observations)

        with patch.object(capture, 'serve_file_fixture', side_effect=inspect):
            probe.serve_fixture(capture, fake_adapter, io.BytesIO(frames), io.BytesIO(), Path('synthetic'))
        self.assertEqual(captured, [shape('prepare'), shape('publish')])
        retained = capture.encoded(captured)
        for secret in (b'PRIVATE_THREAD_VALUE', b'private_nested_name', b'PRIVATE_NESTED_VALUE'):
            self.assertNotIn(secret, retained)

    def test_closed_fixture_keeps_parser_strict_and_errors_generic(self):
        initialize = capture.encoded({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                                      'params': {'protocolVersion': '2025-03-26'}}) + b'\n'
        calls = b''.join(metadata_frame(index, {'progressToken': index + 1,
            'threadId': 'PRIVATE_THREAD_VALUE',
            'codex/sandbox-state': {'credentials': {'nested': 'PRIVATE_NESTED_VALUE'}}})
            for index in (0, 1))
        with tempfile.TemporaryDirectory(prefix=capture.PREFIX) as directory:
            path = Path(directory) / capture.EVIDENCE_NAME
            destination = io.BytesIO()
            probe.serve_fixture(capture, adapter, io.BytesIO(initialize + calls), destination, path)
            observations = probe.read_shapes(capture, path)
            self.assertEqual(observations, [shape('prepare'), shape('publish')])
            events = capture.read_evidence(path, adapter.REJECTION_CODES)
            self.assertEqual([item['code'] for item in events],
                             ['protocol_metadata_shape', 'protocol_metadata_shape'])
            replies = [json.loads(line) for line in destination.getvalue().splitlines()]
            for reply in replies[1:]:
                self.assertEqual(reply['error']['message'], adapter.ERROR)
            for item in Path(directory).iterdir():
                body = item.read_bytes()
                for secret in (b'PRIVATE_THREAD_VALUE', b'PRIVATE_NESTED_VALUE', b'credentials', b'nested'):
                    self.assertNotIn(secret, body)
            self.assertNotIn(b'protocol_metadata_shape', destination.getvalue())

    def test_fixture_accepts_valid_metadata_only_to_permanent_closed_gate(self):
        initialize = capture.encoded({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                                      'params': {'protocolVersion': '2025-03-26'}}) + b'\n'
        calls = b''.join(metadata_frame(index, {'progressToken': index + 1,
            'threadId': 'SYNTHETIC_THREAD'}) for index in (0, 1))
        with tempfile.TemporaryDirectory(prefix=capture.PREFIX) as directory:
            path = Path(directory) / capture.EVIDENCE_NAME
            probe.serve_fixture(capture, adapter, io.BytesIO(initialize + calls), io.BytesIO(), path)
            events = capture.read_evidence(path, adapter.REJECTION_CODES)
            self.assertEqual([event['code'] for event in events], ['report_gate_closed'] * 2)

    def test_observed_field_shapes_reach_only_closed_gate_without_retaining_values(self):
        # Names/types match the local simulator observation. Values are invented;
        # this does not replay or retain the original model request or its values.
        metadata = {'callId': 'SYNTHETIC_CALL_VALUE', 'itemId': 'SYNTHETIC_ITEM_VALUE',
            'progressToken': 7, 'sessionId': 'SYNTHETIC_SESSION_VALUE',
            'threadId': 'SYNTHETIC_THREAD_VALUE', 'windowId': 'SYNTHETIC_WINDOW_VALUE',
            'x-codex-turn-metadata': {'private_nested_name': 'SYNTHETIC_NESTED_VALUE'}}
        initialize = capture.encoded({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                                      'params': {'protocolVersion': '2025-03-26'}}) + b'\n'
        reduced = {key: value for key, value in metadata.items() if key in ('progressToken', 'threadId')}
        unknown = {**metadata, 'unsupportedContext': 'SYNTHETIC_UNKNOWN_VALUE'}
        for current, code in ((metadata, 'report_gate_closed'), (reduced, 'report_gate_closed'),
                              (unknown, 'protocol_metadata_shape')):
            calls = b''.join(metadata_frame(index, current) for index in (0, 1))
            with self.subTest(code=code), tempfile.TemporaryDirectory(prefix=capture.PREFIX) as directory:
                path = Path(directory) / capture.EVIDENCE_NAME
                destination = io.BytesIO()
                probe.serve_fixture(capture, adapter, io.BytesIO(initialize + calls), destination, path)
                events = capture.read_evidence(path, adapter.REJECTION_CODES)
                self.assertEqual([event['code'] for event in events], [code, code])
                observations = probe.read_shapes(capture, path)
                expected_fields = [{'name': key, 'type': capture.json_type(value)}
                                   for key, value in sorted(current.items())]
                self.assertEqual([item['fields'] for item in observations], [expected_fields] * 2)
                for item in Path(directory).iterdir():
                    body = item.read_bytes()
                    self.assertNotIn(b'SYNTHETIC_', body)
                    self.assertNotIn(b'private_nested_name', body)
                for reply in map(json.loads, destination.getvalue().splitlines()[1:]):
                    self.assertEqual(reply['error']['message'], adapter.ERROR)

    def test_fixture_rejects_wrong_order_arguments_and_extra_call(self):
        cases = [metadata_frame(1, {}), metadata_frame(0, {}, {'unexpected': 'PRIVATE'}),
                 metadata_frame(0, {}) + metadata_frame(1, {}) + metadata_frame(0, {})]
        for frames in cases:
            with self.subTest(frames=len(frames)), tempfile.TemporaryDirectory(prefix=capture.PREFIX) as directory:
                path = Path(directory) / capture.EVIDENCE_NAME
                with self.assertRaisesRegex(ValueError, '^rejection_probe_metadata_(call_invalid|limit)$'):
                    probe.serve_fixture(capture, adapter, io.BytesIO(frames), io.BytesIO(), path)
                for item in Path(directory).iterdir():
                    self.assertNotIn(b'PRIVATE', item.read_bytes())

    def write_shapes(self, path, first=None, second=None):
        for name, value in (('prepare', shape() if first is None else first),
                            ('publish', shape('publish') if second is None else second)):
            path.with_name(capture.METADATA_NAME.format(tool=name)).write_bytes(capture.encoded(value))

    def test_shape_reader_requires_complete_two_file_evidence(self):
        with tempfile.TemporaryDirectory(prefix=capture.PREFIX) as directory:
            path = Path(directory) / capture.EVIDENCE_NAME
            with self.assertRaises((ValueError, OSError)):
                probe.read_shapes(capture, path)
            self.write_shapes(path)
            self.assertEqual(probe.read_shapes(capture, path), [shape(), shape('publish')])
            path.with_name(capture.METADATA_NAME.format(tool='publish')).unlink()
            with self.assertRaises((ValueError, OSError)):
                probe.read_shapes(capture, path)

    def test_shape_reader_rejects_oversized_or_malformed_json(self):
        with tempfile.TemporaryDirectory(prefix=capture.PREFIX) as directory:
            path = Path(directory) / capture.EVIDENCE_NAME
            self.write_shapes(path)
            first = path.with_name(capture.METADATA_NAME.format(tool='prepare'))
            for body in (b'x' * (capture.MAX_EVIDENCE_BYTES + 1), b'not-json'):
                first.write_bytes(body)
                with self.subTest(size=len(body)), self.assertRaises(ValueError):
                    probe.read_shapes(capture, path)

    def test_shape_reader_refuses_values_unknown_fields_and_invalid_names(self):
        variants = [dict(shape(), value='PRIVATE'), dict(shape(), tool='other'),
            dict(shape(), metadata_present=1), dict(shape(), metadata_type='unknown'),
            dict(shape(), metadata_type='string'), dict(shape(), fields={}),
            dict(shape(), fields=[{'name': 'threadId', 'type': 'string', 'value': 'PRIVATE'}]),
            dict(shape(), fields=[{'name': 'session123PRIVATE', 'type': 'string'}]),
            dict(shape(), fields=[{'name': 'key', 'type': 'unreviewed'}]),
            dict(shape(), fields=[{'name': 'a' * 97, 'type': 'string'}]),
            dict(shape(), fields=[{'name': 'key', 'type': 'string'}] * 17)]
        with tempfile.TemporaryDirectory(prefix=capture.PREFIX) as directory:
            path = Path(directory) / capture.EVIDENCE_NAME
            for value in variants:
                self.write_shapes(path, first=value)
                with self.subTest(value=value), self.assertRaisesRegex(ValueError, '^metadata_probe_shape_invalid$'):
                    probe.read_shapes(capture, path)

    def test_shape_reader_accepts_redacted_names_and_nonobject_shapes_without_fields(self):
        with tempfile.TemporaryDirectory(prefix=capture.PREFIX) as directory:
            path = Path(directory) / capture.EVIDENCE_NAME
            for json_type in sorted(probe.JSON_TYPES):
                first = dict(shape(), metadata_type=json_type, fields=[])
                second = dict(shape('publish'), fields=[{'name': '[redacted-key]', 'type': 'string'}])
                self.write_shapes(path, first, second)
                self.assertEqual(probe.read_shapes(capture, path), [first, second])

    def run_with_mocks(self, runtime_result=None, codes=None):
        result = {'local_synthetic_probe_passed': True, 'external_model_called': False,
                  'report_transport_enabled': False, 'matched_blocked_report_calls': 2,
                  'only_parent_thread_loaded': True}
        if runtime_result:
            result.update(runtime_result)
        helper = Mock(PREFIX='synthetic-prefix-', GATE_CODE='report_gate_closed')
        helper.evidence_path.return_value = Path('synthetic-evidence')
        helper.read_evidence.return_value = [{'code': code} for code in
            (codes or ['protocol_metadata_shape', 'protocol_metadata_shape'])]
        local_runtime = Mock()
        local_runtime.run_probe.return_value = result
        config, fake_adapter = {'notify': []}, SimpleNamespace(REJECTION_CODES={'protocol_metadata_shape'})
        with patch.object(probe, 'load_helpers', return_value=(helper, local_runtime)), \
                patch.object(probe.launcher, 'load_inputs', return_value=(config, fake_adapter)), \
                patch.object(probe.launcher, 'plain_file') as plain, \
                patch.object(probe.launcher, 'check_pin') as pin, \
                patch.object(probe, 'verify_plan') as plan, \
                patch.object(probe.tempfile, 'mkdtemp', return_value='synthetic-directory') as temporary, \
                patch.object(probe, 'fixture_config', return_value={'fixed': 'config'}) as configure, \
                patch.object(probe, 'read_shapes', return_value=[shape(), shape('publish')]):
            observed = probe.run_probe()
        plain.assert_called_once_with(probe.ENTRY)
        plan.assert_called_once_with(local_runtime)
        temporary.assert_called_once_with(prefix='synthetic-prefix-')
        configure.assert_called_once_with(helper, config, 'synthetic-directory')
        local_runtime.run_probe.assert_called_once_with(candidate_config={'fixed': 'config'})
        pin.assert_called_once_with(probe.launcher.USER_CONFIG,
                                    probe.launcher.FILE_PINS[probe.launcher.USER_CONFIG])
        return observed

    def test_run_reports_only_validated_shapes_and_fixed_safety_flags(self):
        result = self.run_with_mocks()
        self.assertTrue(result['local_metadata_probe_completed'])
        self.assertTrue(result['metadata_shape_rejection_reproduced'])
        self.assertFalse(result['closed_gate_reached'])
        self.assertEqual(result['client_path'], 'functions_exec_simulated_response')
        self.assertEqual(result['metadata_shapes'], [shape(), shape('publish')])
        for flag in ('external_model_called', 'original_request_captured', 'ssh_connected',
                     'report_transport_enabled', 'public_publishing_enabled', 'schedule_changed'):
            self.assertIs(result[flag], False)

    def test_run_distinguishes_closed_gate_from_metadata_rejection(self):
        result = self.run_with_mocks(codes=['report_gate_closed', 'report_gate_closed'])
        self.assertTrue(result['closed_gate_reached'])
        self.assertFalse(result['metadata_shape_rejection_reproduced'])

    def test_run_refuses_incomplete_or_unsafe_runtime_outcomes(self):
        for key, value in (('local_synthetic_probe_passed', False), ('external_model_called', True),
                           ('report_transport_enabled', True), ('matched_blocked_report_calls', 1),
                           ('only_parent_thread_loaded', False)):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, '^metadata_probe_runtime_incomplete$'):
                self.run_with_mocks(runtime_result={key: value})

    def test_cli_rejects_arbitrary_arguments_and_destinations_before_any_work(self):
        for arguments in (['--output', 'arbitrary'], ['--directory', 'arbitrary'],
                          ['--run-once'], ['--mcp-fixture', 'arbitrary']):
            with self.subTest(arguments=arguments), \
                    patch.object(probe.sys, 'flags', SimpleNamespace(isolated=1)), \
                    patch.object(probe.sys, 'dont_write_bytecode', True), \
                    patch.object(probe.sys, 'argv', [str(probe.ENTRY), *arguments]), \
                    patch.object(probe, 'load_helpers') as helpers, \
                    patch.object(probe, 'run_probe') as run:
                with self.assertRaisesRegex(ValueError, '^metadata_probe_arguments_refused$'):
                    probe.main()
                helpers.assert_not_called()
                run.assert_not_called()

    def test_cli_requires_isolated_bytecode_free_execution(self):
        for isolated, bytecode in ((0, True), (1, False)):
            with self.subTest(isolated=isolated, bytecode=bytecode), \
                    patch.object(probe.sys, 'flags', SimpleNamespace(isolated=isolated)), \
                    patch.object(probe.sys, 'dont_write_bytecode', bytecode), \
                    patch.object(probe, 'run_probe') as run:
                with self.assertRaisesRegex(ValueError, '^metadata_probe_isolated_invocation_required$'):
                    probe.main()
                run.assert_not_called()

    def test_cli_normal_path_prints_only_probe_result(self):
        with patch.object(probe.sys, 'flags', SimpleNamespace(isolated=1)), \
                patch.object(probe.sys, 'dont_write_bytecode', True), \
                patch.object(probe.sys, 'argv', [str(probe.ENTRY)]), \
                patch.object(probe, 'run_probe', return_value={'synthetic': True}) as run, \
                patch('builtins.print') as printed:
            probe.main()
        run.assert_called_once_with()
        printed.assert_called_once_with('{"synthetic": true}')


if __name__ == '__main__':
    unittest.main()
