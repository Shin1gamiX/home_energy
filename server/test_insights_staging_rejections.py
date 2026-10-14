"""Offline rejection diagnostics; synthetic inputs, no model or private files."""
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import insights_staging_mcp as adapter
import insights_staging_run as run
import insights_staging_run_audit as audit


TOKEN = 'a' * 64
SENTINEL = 'Bearer synthetic-credential-DO-NOT-RETAIN /private/key.pem'


def rpc(method, params=None, identifier=1):
    return {'jsonrpc': '2.0', 'id': identifier, 'method': method,
            'params': {} if params is None else params}


def frame(value):
    return adapter.encode(value) + b'\n'


def initialize():
    return rpc('initialize', {'protocolVersion': '2025-06-18'})


def observed_metadata(**changes):
    """Observed field names/types with entirely invented, inert test values."""
    value = {'progressToken': 'SyntheticProgressSecret', 'threadId': 'SyntheticThreadSecret',
             'callId': 'SyntheticCallSecret', 'itemId': 'SyntheticItemSecret',
             'sessionId': 'SyntheticSessionSecret', 'windowId': '',
             'x-codex-turn-metadata': {'synthetic_nested': 'SyntheticNestedSecret'}}
    value.update(changes)
    return value


def event(**changes):
    value = {'timestamp_utc': '2026-10-14T12:00:00+00:00',
             'attempt': 2, 'method': 'prepare', 'code': 'gate_root_rejected'}
    value.update(changes)
    return value


class AdapterRejectionTests(unittest.TestCase):
    def serve(self, frames, reports=None, callback=None):
        reports = reports or SimpleNamespace(call=Mock(return_value={'synthetic': True}))
        output = io.BytesIO()
        events, rejections = [], []
        adapter.serve(io.BytesIO(b''.join(frames)), output, reports, events.append,
                      rejections.append if callback is None else callback)
        responses = [json.loads(line) for line in output.getvalue().splitlines()]
        return responses, events, rejections

    def assert_private_code(self, code, frames):
        responses, events, rejected = self.serve(frames)
        self.assertEqual(rejected[-1]['code'], code)
        self.assertEqual(set(rejected[-1]), {'timestamp_utc', 'attempt', 'method', 'code'})
        audit.validate_rejection(rejected[-1])
        self.assertEqual(responses[-1]['error'], {'code': -32600, 'message': adapter.ERROR})
        self.assertNotIn(code, json.dumps(responses))
        self.assertNotIn(SENTINEL, json.dumps((responses, events, rejected)))

    def test_each_protocol_rejection_stage_is_fixed_and_private(self):
        cases = [
            ('protocol_frame', [b'{}']),
            ('protocol_json', [b'{broken\n']),
            ('protocol_envelope', [frame({'jsonrpc': '1.0', 'token': SENTINEL})]),
            ('protocol_id', [frame(rpc('ping', identifier={'secret': SENTINEL}))]),
            ('protocol_params', [frame(rpc('ping', [SENTINEL]))]),
            ('protocol_notification', [frame(rpc('notifications/initialized', identifier=None))]),
            ('protocol_metadata_shape', [frame(rpc('initialize', {'_meta': {'secret': SENTINEL}}))]),
            ('protocol_metadata_token', [frame(rpc('initialize', {'_meta': {'progressToken': SENTINEL}}))]),
            ('protocol_metadata_thread', [frame(initialize()), frame(rpc('tools/call', {
                'name': 'prepare', 'arguments': {}, '_meta': {'progressToken': 1, 'threadId': SENTINEL}}))]),
            ('protocol_metadata_context', [frame(initialize()), frame(rpc('tools/call', {
                'name': 'prepare', 'arguments': {}, '_meta': {'progressToken': 1, 'callId': SENTINEL + '\n'}}))]),
            ('protocol_metadata_turn', [frame(initialize()), frame(rpc('tools/call', {
                'name': 'prepare', 'arguments': {}, '_meta': {'progressToken': 1, 'x-codex-turn-metadata': SENTINEL}}))]),
            ('protocol_initialize', [frame(initialize()), frame(initialize())]),
            ('protocol_version', [frame(rpc('initialize', {'protocolVersion': SENTINEL}))]),
            ('protocol_discovery', [frame(rpc('tools/list'))]),
            ('protocol_call_shape', [frame(initialize()), frame(rpc('tools/call', {'secret': SENTINEL}))]),
            ('protocol_tool_name', [frame(initialize()), frame(rpc('tools/call', {'name': SENTINEL, 'arguments': {}}))]),
        ]
        for code, frames in cases:
            with self.subTest(code=code):
                self.assert_private_code(code, frames)

    def test_invalid_tool_name_never_dispatches(self):
        reports = SimpleNamespace(call=Mock())
        for name in ([], {}, 7, None, 'shell'):
            with self.subTest(name=name):
                _, _, rejected = self.serve([frame(initialize()),
                    frame(rpc('tools/call', {'name': name, 'arguments': {}}))], reports)
                self.assertEqual(rejected[0]['code'], 'protocol_tool_name')
        reports.call.assert_not_called()

    def test_arbitrary_exception_and_arguments_are_not_retained(self):
        reports = SimpleNamespace(call=Mock(side_effect=ValueError(SENTINEL)),
                                  rejection_code=SENTINEL)
        responses, events, rejected = self.serve([frame(initialize()),
            frame(rpc('tools/call', {'name': 'prepare', 'arguments': {'password': SENTINEL}}))], reports)
        self.assertEqual(rejected[0]['code'], 'tool_execution_rejected')
        self.assertEqual(rejected[0]['method'], 'prepare')
        self.assertNotIn(SENTINEL, json.dumps((responses, events, rejected)))
        self.assertEqual(responses[-1]['error']['message'], adapter.ERROR)

    def test_only_allowed_gate_codes_refine_execution_rejection(self):
        for code in adapter.GATE_REJECTION_CODES:
            with self.subTest(code=code):
                reports = SimpleNamespace(call=Mock(side_effect=ValueError(SENTINEL)), rejection_code=code)
                responses, _, rejected = self.serve([frame(initialize()),
                    frame(rpc('tools/call', {'name': 'prepare', 'arguments': {}}))], reports)
                self.assertEqual(rejected[0]['code'], code)
                self.assertEqual(responses[-1]['error']['message'], adapter.ERROR)
                self.assertNotIn(code, json.dumps(responses))

    def test_non_gate_code_cannot_be_forged_as_gate_diagnostic(self):
        reports = SimpleNamespace(call=Mock(side_effect=ValueError(SENTINEL)),
                                  rejection_code='protocol_envelope')
        _, _, rejected = self.serve([frame(initialize()),
            frame(rpc('tools/call', {'name': 'prepare', 'arguments': {}}))], reports)
        self.assertEqual(rejected[0]['code'], 'tool_execution_rejected')

    def test_unserializable_response_uses_response_code(self):
        reports = SimpleNamespace(call=Mock(return_value={SENTINEL: object()}))
        responses, events, rejected = self.serve([frame(initialize()),
            frame(rpc('tools/call', {'name': 'prepare', 'arguments': {}}))], reports)
        self.assertEqual(rejected[0]['code'], 'protocol_response')
        self.assertNotIn(SENTINEL, json.dumps((responses, events, rejected)))

    def test_valid_progress_token_is_not_recorded_or_dispatched(self):
        reports = SimpleNamespace(call=Mock(return_value={'synthetic': True}))
        params = {'name': 'prepare', 'arguments': {}, '_meta': {'progressToken': 'synthetic-secret-token'}}
        responses, events, rejected = self.serve([frame(initialize()), frame(rpc('tools/call', params))], reports)
        reports.call.assert_called_once_with('prepare', {})
        self.assertEqual(rejected, [])
        self.assertNotIn('synthetic-secret-token', json.dumps((responses, events)))

    def test_tool_metadata_thread_id_is_bounded_stripped_and_never_recorded(self):
        for method in ('prepare', 'publish'):
            for thread_id in ('a', 'SyntheticThreadSecret_012-abc', 'a' * 128):
                reports = SimpleNamespace(call=Mock(return_value={'synthetic': True}))
                arguments = {} if method == 'prepare' else {'synthetic_argument': True}
                params = {'name': method, 'arguments': arguments, '_meta': {
                    'progressToken': 'SyntheticProgressSecret', 'threadId': thread_id}}
                with self.subTest(method=method, thread_length=len(thread_id)):
                    responses, events, rejected = self.serve([
                        frame(initialize()), frame(rpc('tools/call', params))], reports)
                    reports.call.assert_called_once_with(method, arguments)
                    self.assertEqual(rejected, [])
                    self.assertIn('result', responses[-1])
                    logged = json.dumps((responses, events))
                    self.assertNotIn('SyntheticProgressSecret', logged)
                    self.assertNotIn('threadId', logged)
                    if len(thread_id) > 1:
                        self.assertNotIn(thread_id, logged)
                self.assertIn('_meta', params)

    def test_invalid_thread_metadata_values_never_dispatch_or_leak(self):
        invalid = (None, True, False, 0, 1, 1.5, [], {}, '', 'a' * 129,
                   'SYNTHETIC\nSECRET', 'SYNTHETIC\rSECRET', 'SYNTHETIC\tSECRET',
                   'SYNTHETIC\x00SECRET', 'SYNTHETIC SECRET', 'SYNTHETIC/SECRET',
                   'SYNTHETIC\\SECRET', '../SECRET', 'SYNTHETIC:SECRET',
                   'συνθετικό', 'thread.id', SENTINEL)
        for value in invalid:
            reports = SimpleNamespace(call=Mock())
            params = {'name': 'prepare', 'arguments': {}, '_meta': observed_metadata(threadId=value)}
            with self.subTest(value=value):
                responses, events, rejected = self.serve([
                    frame(initialize()), frame(rpc('tools/call', params))], reports)
                reports.call.assert_not_called()
                self.assertEqual(rejected[0]['code'], 'protocol_metadata_thread')
                audit.validate_rejection(rejected[0])
                self.assertEqual(responses[-1]['error']['message'], adapter.ERROR)
                for marker in ('SECRET', 'threadId', 'συνθετικό', SENTINEL):
                    self.assertNotIn(marker, json.dumps((responses, events, rejected)))

    def test_thread_metadata_requires_progress_token_and_no_unknown_fields(self):
        metadata_cases = ({'threadId': 'SyntheticThreadSecret'}, {},
                          {'progressToken': 1, 'threadId': 'SyntheticThreadSecret', 'extra': 'SECRET'},
                          {'progressToken': 1, 'unsupportedContext': 'SyntheticThreadSecret'},
                          None, [], 'SECRET')
        for metadata in metadata_cases:
            reports = SimpleNamespace(call=Mock())
            params = {'name': 'prepare', 'arguments': {}, '_meta': metadata}
            with self.subTest(metadata=metadata):
                responses, events, rejected = self.serve([
                    frame(initialize()), frame(rpc('tools/call', params))], reports)
                reports.call.assert_not_called()
                self.assertEqual(rejected[0]['code'], 'protocol_metadata_shape')
                self.assertEqual(responses[-1]['error']['message'], adapter.ERROR)
                self.assertNotIn('SECRET', json.dumps((responses, events, rejected)))

    def test_thread_metadata_not_allowed_on_initialization_discovery_or_other_methods(self):
        metadata = {'progressToken': 1, 'threadId': 'SyntheticThreadSecret'}
        for method in ('initialize', 'tools/list', 'ping', 'shell/exec', 'notifications/initialized'):
            reports = SimpleNamespace(call=Mock())
            params = {'_meta': metadata}
            frames = [] if method == 'initialize' else [frame(initialize())]
            if method == 'initialize':
                params['protocolVersion'] = '2025-06-18'
            frames.append(frame(rpc(method, params)))
            with self.subTest(method=method):
                responses, events, rejected = self.serve(frames, reports)
                self.assertEqual(rejected[0]['code'], 'protocol_metadata_shape')
                self.assertEqual(responses[-1]['error']['message'], adapter.ERROR)
                reports.call.assert_not_called()
                self.assertNotIn('SyntheticThreadSecret', json.dumps((responses, events, rejected)))

    def test_observed_metadata_is_stripped_without_mutating_original_arguments(self):
        for method in ('prepare', 'publish'):
            reports = SimpleNamespace(call=Mock(return_value={'synthetic': True}))
            arguments = {'synthetic_argument': 'kept'}
            metadata = observed_metadata()
            params = {'name': method, 'arguments': arguments, '_meta': metadata}
            with self.subTest(method=method):
                responses, events, rejected = self.serve([
                    frame(initialize()), frame(rpc('tools/call', params))], reports)
                reports.call.assert_called_once_with(method, arguments)
                self.assertEqual(rejected, [])
                self.assertIn('result', responses[-1])
                logged = json.dumps((responses, events))
                for key in metadata:
                    self.assertNotIn(key, logged)
                self.assertNotIn('Secret', logged)
                self.assertEqual(params['_meta'], metadata)
                self.assertEqual(arguments, {'synthetic_argument': 'kept'})

    def test_context_string_boundaries_and_printable_ascii_are_inert(self):
        printable = ''.join(chr(value) for value in range(32, 127))
        for field in ('callId', 'itemId', 'sessionId', 'windowId'):
            for value in ('', 'S', 'S' * 256, printable, SENTINEL):
                reports = SimpleNamespace(call=Mock(return_value={'synthetic': True}))
                params = {'name': 'prepare', 'arguments': {}, '_meta': {
                    'progressToken': 1, field: value}}
                with self.subTest(field=field, length=len(value)):
                    responses, events, rejected = self.serve([
                        frame(initialize()), frame(rpc('tools/call', params))], reports)
                    reports.call.assert_called_once_with('prepare', {})
                    self.assertEqual(rejected, [])
                    self.assertNotIn(field, json.dumps((responses, events)))
                    self.assertNotIn(SENTINEL, json.dumps((responses, events)))

    def assert_metadata_rejected(self, metadata, code):
        reports = SimpleNamespace(call=Mock())
        responses, events, rejected = self.serve([
            frame(initialize()), frame(rpc('tools/call', {
                'name': 'prepare', 'arguments': {}, '_meta': metadata}))], reports)
        reports.call.assert_not_called()
        self.assertEqual(rejected[0]['code'], code)
        audit.validate_rejection(rejected[0])
        self.assertEqual(responses[-1]['error']['message'], adapter.ERROR)
        for marker in ('Secret', 'SECRET', SENTINEL, 'x-codex-turn-metadata'):
            self.assertNotIn(marker, json.dumps((responses, events, rejected)))

    def test_context_strings_reject_non_strings_non_ascii_controls_and_overflow(self):
        for field in ('callId', 'itemId', 'sessionId', 'windowId'):
            for value in (None, True, False, 0, 1.5, [], {}, 'S' * 257,
                          '\n', '\r', '\t', '\x00', '\x1f', '\x7f', 'é', '😀'):
                with self.subTest(field=field, value=value):
                    self.assert_metadata_rejected({'progressToken': 1, field: value},
                                                  'protocol_metadata_context')

    def test_turn_metadata_accepts_only_bounded_opaque_json_without_retention(self):
        # Primitive leaves may be one level below the fourth and final container.
        allowed = ({}, {'a': {'b': {'c': {'d': 'SyntheticNestedSecret'}}}},
                   {'a': [[['SyntheticNestedSecret']]]},
                   {'a': [None, True, False, 0, -1, 1.25, 'SyntheticNestedSecret']},
                   {'a': [None] * 16}, {str(index): None for index in range(16)},
                   {'k' * 128: 'v' * 1024}, {'': ''},
                   {'a': [None] * 16, 'b': [None] * 16, 'c': [None] * 16,
                    'd': [None] * 11})  # 64 value nodes, including the root.
        for value in allowed:
            reports = SimpleNamespace(call=Mock(return_value={'synthetic': True}))
            params = {'name': 'prepare', 'arguments': {}, '_meta': {
                'progressToken': 1, 'x-codex-turn-metadata': value}}
            with self.subTest(value=value):
                responses, events, rejected = self.serve([
                    frame(initialize()), frame(rpc('tools/call', params))], reports)
                reports.call.assert_called_once_with('prepare', {})
                self.assertEqual(rejected, [])
                self.assertNotIn('SyntheticNestedSecret', json.dumps((responses, events)))
                self.assertNotIn('x-codex-turn-metadata', json.dumps((responses, events)))

    def test_turn_metadata_rejects_structural_and_value_limits(self):
        invalid = (None, [], True, False, 0, 1.25, 'SyntheticSecret',
                   {'a': {'b': {'c': {'d': {}}}}}, {'a': [[[[]]]]},
                   {'a': [None] * 17}, {str(index): None for index in range(17)},
                   {'k' * 129: None}, {'a': 'v' * 1025},
                   {'a': [None] * 16, 'b': [None] * 16, 'c': [None] * 16,
                    'd': [None] * 12})  # 65 value nodes.
        for value in invalid:
            with self.subTest(value=value):
                self.assert_metadata_rejected({'progressToken': 1, 'x-codex-turn-metadata': value},
                                              'protocol_metadata_turn')

    def test_turn_metadata_byte_limit_uses_encoded_utf8_not_character_count(self):
        value = {'a': 'v' * 1024, 'b': 'v' * 1024, 'c': 'v' * 1024, 'd': ''}
        value['d'] = 'v' * (4096 - len(adapter.encode(value)))
        self.assertEqual(len(adapter.encode(value)), 4096)
        reports = SimpleNamespace(call=Mock(return_value={'synthetic': True}))
        _, _, rejected = self.serve([frame(initialize()), frame(rpc('tools/call', {
            'name': 'prepare', 'arguments': {}, '_meta': {
                'progressToken': 1, 'x-codex-turn-metadata': value}}))], reports)
        self.assertEqual(rejected, [])
        reports.call.assert_called_once_with('prepare', {})
        value['d'] += 'v'
        self.assertEqual(len(adapter.encode(value)), 4097)
        for oversized in (value, {'a': '😀' * 1024}):
            with self.subTest(encoded_bytes=len(adapter.encode(oversized))):
                self.assert_metadata_rejected({'progressToken': 1, 'x-codex-turn-metadata': oversized},
                                              'protocol_metadata_turn')

    def test_turn_metadata_rejects_nonfinite_exponents_before_dispatch(self):
        for exponent in ('1e999', '-1e999'):
            reports = SimpleNamespace(call=Mock())
            raw = ('{"jsonrpc":"2.0","id":2,"method":"tools/call","params":'
                   '{"name":"prepare","arguments":{},"_meta":{"progressToken":1,'
                   '"x-codex-turn-metadata":{"number":' + exponent + '}}}}\n').encode()
            with self.subTest(exponent=exponent):
                responses, _, rejected = self.serve([frame(initialize()), raw], reports)
                reports.call.assert_not_called()
                self.assertEqual(rejected[0]['code'], 'protocol_metadata_turn')
                self.assertEqual(responses[-1]['error']['message'], adapter.ERROR)

    def test_duplicate_nested_keys_remain_protocol_json_rejections(self):
        reports = SimpleNamespace(call=Mock())
        raw = (b'{"jsonrpc":"2.0","id":2,"method":"tools/call","params":'
               b'{"name":"prepare","arguments":{},"_meta":{"progressToken":1,'
               b'"x-codex-turn-metadata":{"nested":{"key":"SECRET","key":"SECRET"}}}}}\n')
        responses, events, rejected = self.serve([frame(initialize()), raw], reports)
        reports.call.assert_not_called()
        self.assertEqual(rejected[0]['code'], 'protocol_json')
        self.assertEqual(responses[-1]['error']['message'], adapter.ERROR)
        self.assertNotIn('SECRET', json.dumps((responses, events, rejected)))

    def test_turn_validator_requires_exact_json_types_even_for_internal_callers(self):
        class DerivedDict(dict):
            pass
        class DerivedList(list):
            pass
        class DerivedString(str):
            pass
        class DerivedInteger(int):
            pass
        invalid = (DerivedDict(), {'a': DerivedDict()}, {'a': DerivedList()},
                   {'a': DerivedString('SECRET')}, {'a': DerivedInteger(1)},
                   {DerivedString('key'): None}, {'a': ()}, {'a': set()},
                   {'a': float('inf')}, {'a': float('-inf')}, {'a': float('nan')})
        for value in invalid:
            with self.subTest(value_type=type(value).__name__), self.assertRaises((TypeError, ValueError)):
                adapter.validate_turn_metadata(value)

    def test_malformed_context_never_opens_real_gate_or_constructs_session(self):
        cases = (observed_metadata(callId='SECRET\n'),
                 observed_metadata(**{'x-codex-turn-metadata': []}),
                 observed_metadata(unsupportedContext='SECRET'))
        for metadata in cases:
            module = SimpleNamespace(AuditFailure=audit.AuditFailure, open_mcp_audit=Mock(), write_result=Mock())
            integration = SimpleNamespace(AuditLog=Mock(), StagingSession=Mock())
            reports = run.GatedReports(TOKEN, module, integration)
            with self.subTest(metadata=metadata):
                self.serve([frame(initialize()), frame(rpc('tools/call', {
                    'name': 'prepare', 'arguments': {}, '_meta': metadata}))], reports)
                module.open_mcp_audit.assert_not_called()
                module.write_result.assert_not_called()
                integration.AuditLog.assert_not_called()
                integration.StagingSession.assert_not_called()

    def test_new_metadata_fields_are_rejected_outside_tools_call(self):
        fields = {'callId': 'SyntheticSecret', 'itemId': 'SyntheticSecret',
                  'sessionId': 'SyntheticSecret', 'windowId': '', 'x-codex-turn-metadata': {}}
        for method in ('initialize', 'tools/list', 'ping', 'shell/exec', 'notifications/initialized'):
            for field, value in fields.items():
                params = {'_meta': {'progressToken': 1, field: value}}
                frames = [] if method == 'initialize' else [frame(initialize())]
                if method == 'initialize':
                    params['protocolVersion'] = '2025-06-18'
                frames.append(frame(rpc(method, params)))
                reports = SimpleNamespace(call=Mock())
                with self.subTest(method=method, field=field):
                    responses, _, rejected = self.serve(frames, reports)
                    reports.call.assert_not_called()
                    self.assertEqual(rejected[0]['code'], 'protocol_metadata_shape')
                    self.assertEqual(responses[-1]['error']['message'], adapter.ERROR)

    def test_observed_metadata_still_requires_progress_token_and_known_top_level_fields(self):
        missing = observed_metadata()
        del missing['progressToken']
        for metadata in (missing, observed_metadata(unsupportedContext='SyntheticSecret'),
                         observed_metadata(notify=[]), observed_metadata(permissions={})):
            with self.subTest(keys=list(metadata)):
                self.assert_metadata_rejected(metadata, 'protocol_metadata_shape')

    def test_turn_context_cannot_authorize_or_route_report_dispatch(self):
        metadata = observed_metadata(**{'x-codex-turn-metadata': {
            'sandbox': 'danger-full-access', 'approval_policy': 'never',
            'name': 'shell', 'arguments': {'command': 'SyntheticSecret'},
            'destination': '/synthetic/private', 'token': SENTINEL}})
        reports = SimpleNamespace(call=Mock(return_value={'synthetic': True}))
        responses, events, rejected = self.serve([frame(initialize()), frame(rpc('tools/call', {
            'name': 'prepare', 'arguments': {}, '_meta': metadata}))], reports)
        self.assertEqual(rejected, [])
        reports.call.assert_called_once_with('prepare', {})
        for marker in ('danger-full-access', 'SyntheticSecret', 'destination', SENTINEL):
            self.assertNotIn(marker, json.dumps((responses, events)))

    def test_thread_metadata_does_not_relax_progress_token_validation(self):
        for token in (True, False, None, 1.5, [], {}, '', 'a' * 129,
                      2**53, -(2**53), 'SYNTHETIC/SECRET', 'συνθετικό'):
            reports = SimpleNamespace(call=Mock())
            params = {'name': 'prepare', 'arguments': {}, '_meta': {
                **observed_metadata(), 'progressToken': token}}
            with self.subTest(token=token):
                responses, _, rejected = self.serve([
                    frame(initialize()), frame(rpc('tools/call', params))], reports)
                self.assertEqual(rejected[0]['code'], 'protocol_metadata_token')
                self.assertEqual(responses[-1]['error']['message'], adapter.ERROR)
                reports.call.assert_not_called()

    def test_thread_metadata_does_not_bypass_initialization_or_tool_checks(self):
        metadata = observed_metadata()
        cases = [(False, {'name': 'prepare', 'arguments': {}}, 'protocol_call_shape'),
                 (True, {'name': 'shell', 'arguments': {}}, 'protocol_tool_name'),
                 (True, {'name': [], 'arguments': {}}, 'protocol_tool_name'),
                 (True, {'name': 'prepare'}, 'protocol_call_shape'),
                 (True, {'name': 'prepare', 'arguments': {}, 'extra': 'SECRET'}, 'protocol_call_shape')]
        for initialized, params, code in cases:
            reports = SimpleNamespace(call=Mock())
            frames = [frame(initialize())] if initialized else []
            frames.append(frame(rpc('tools/call', {**params, '_meta': metadata})))
            with self.subTest(params=params, initialized=initialized):
                responses, _, rejected = self.serve(frames, reports)
                self.assertEqual(rejected[0]['code'], code)
                self.assertEqual(responses[-1]['error']['message'], adapter.ERROR)
                reports.call.assert_not_called()

    def test_thread_metadata_cannot_bypass_real_argument_validation(self):
        cases = [('prepare', {'extra': 'SECRET'}), ('prepare', []),
                 ('publish', {}), ('publish', {'analysis_id': 'invalid'}),
                 ('publish', None)]
        for method, arguments in cases:
            transport = Mock()
            reports = adapter.StagingReports(transport)
            params = {'name': method, 'arguments': arguments, '_meta': observed_metadata()}
            with self.subTest(method=method, arguments=arguments):
                responses, events, rejected = self.serve([
                    frame(initialize()), frame(rpc('tools/call', params))], reports)
                self.assertEqual(rejected[0]['code'], 'tool_execution_rejected')
                self.assertEqual(responses[-1]['error']['message'], adapter.ERROR)
                self.assertNotIn('SyntheticThreadSecret', json.dumps((responses, events, rejected)))
                transport.assert_not_called()

    def test_thread_metadata_cannot_open_closed_one_shot_gate(self):
        for method in ('prepare', 'publish'):
            module = SimpleNamespace(AuditFailure=audit.AuditFailure, open_mcp_audit=Mock(), write_result=Mock())
            integration = SimpleNamespace(AuditLog=Mock(), StagingSession=Mock())
            reports = run.GatedReports(run.CLOSED_TOKEN, module, integration)
            params = {'name': method, 'arguments': {}, '_meta': observed_metadata(**{
                'x-codex-turn-metadata': {'gate_token': TOKEN, 'path': '/synthetic/private',
                                        'command': 'SyntheticSecret'}})}
            with self.subTest(method=method):
                responses, events, rejected = self.serve([
                    frame(initialize()), frame(rpc('tools/call', params))], reports)
                self.assertEqual(rejected[0]['code'], 'report_gate_closed')
                self.assertEqual(responses[-1]['error']['message'], adapter.ERROR)
                self.assertNotIn('SyntheticThreadSecret', json.dumps((responses, events, rejected)))
                module.open_mcp_audit.assert_not_called()
                module.write_result.assert_not_called()
                integration.AuditLog.assert_not_called()
                integration.StagingSession.assert_not_called()

    def test_diagnostic_callback_failure_stops_before_next_dispatch(self):
        reports = SimpleNamespace(call=Mock(return_value={}))
        output = io.BytesIO()
        callback = Mock(side_effect=audit.AuditFailure('private_rejection_write_failed'))
        source = io.BytesIO(frame(initialize()) + frame(rpc('tools/call', {'name': 'bad', 'arguments': {}}))
                            + frame(rpc('tools/call', {'name': 'prepare', 'arguments': {}})))
        with self.assertRaisesRegex(audit.AuditFailure, '^private_rejection_write_failed$'):
            adapter.serve(source, output, reports, Mock(), callback)
        reports.call.assert_not_called()
        self.assertEqual(callback.call_count, 1)
        self.assertEqual(len(output.getvalue().splitlines()), 1)

    def test_adapter_is_bounded_to_64_rejections(self):
        responses, events, rejected = self.serve([b'{broken\n'] * 70)
        self.assertEqual((len(responses), len(events), len(rejected)), (64, 64, 64))
        self.assertEqual([item['attempt'] for item in rejected], list(range(1, 65)))

    def test_oversized_frame_is_fatal_without_dispatch(self):
        reports = SimpleNamespace(call=Mock())
        responses, _, rejected = self.serve([b' ' * (adapter.MAX_FRAME + 1) + b'\n',
                                             frame(initialize())], reports)
        self.assertEqual(len(responses), 1)
        self.assertEqual(rejected[0]['code'], 'protocol_frame')
        reports.call.assert_not_called()

    def test_callback_remains_optional(self):
        output = io.BytesIO()
        adapter.serve(io.BytesIO(b'{broken\n'), output, SimpleNamespace(call=Mock()), Mock())
        self.assertEqual(json.loads(output.getvalue())['error']['message'], adapter.ERROR)


class GatedRejectionTests(unittest.TestCase):
    def setUp(self):
        self.module = SimpleNamespace(AuditFailure=audit.AuditFailure,
                                      open_mcp_audit=Mock(), write_result=Mock())
        self.integration = SimpleNamespace(AuditLog=Mock(), StagingSession=Mock())
        self.reports = run.GatedReports(TOKEN, self.module, self.integration)

    def test_typed_allowlisted_gate_error_is_preserved_only_as_code(self):
        codes = ('gate_root_rejected', 'gate_receipt_rejected', 'gate_expired',
                 'gate_terminal', 'gate_armed_rejected', 'gate_claim_rejected',
                 'gate_audit_rejected', 'gate_recheck_rejected')
        for code in codes:
            with self.subTest(code=code):
                self.module.open_mcp_audit.side_effect = audit.AuditFailure(code)
                reports = run.GatedReports(TOKEN, self.module, self.integration)
                with self.assertRaisesRegex(ValueError, '^report_gate_closed$'):
                    reports.call('prepare', {})
                self.assertEqual(reports.rejection_code, code)
                self.assertTrue(reports.failed)
        self.integration.AuditLog.assert_not_called()
        self.integration.StagingSession.assert_not_called()

    def test_untrusted_exceptions_are_not_parsed_or_reflected(self):
        class DerivedAuditFailure(audit.AuditFailure):
            pass
        errors = (ValueError('gate_root_rejected'), ValueError(SENTINEL),
                  audit.AuditFailure(SENTINEL), audit.AuditFailure('gate_root_rejected', SENTINEL),
                  DerivedAuditFailure('gate_root_rejected'), OSError(SENTINEL))
        for error in errors:
            with self.subTest(error_type=type(error).__name__):
                self.module.open_mcp_audit.side_effect = error
                reports = run.GatedReports(TOKEN, self.module, self.integration)
                with self.assertRaisesRegex(ValueError, '^report_gate_closed$'):
                    reports.call('prepare', {})
                self.assertEqual(reports.rejection_code, 'gate_open_rejected')

    def test_failed_gate_is_not_reopened(self):
        self.module.open_mcp_audit.side_effect = audit.AuditFailure('gate_terminal')
        for attempt in range(2):
            with self.assertRaises(ValueError):
                self.reports.call('prepare', {})
            self.assertEqual(self.reports.rejection_code,
                             'gate_terminal' if attempt == 0 else 'report_session_failed')
        self.module.open_mcp_audit.assert_called_once_with(TOKEN)

    def test_audit_and_session_setup_failures_have_distinct_fixed_codes(self):
        for component, code in (('AuditLog', 'gate_audit_setup_rejected'),
                                ('StagingSession', 'gate_session_setup_rejected')):
            with self.subTest(component=component):
                integration = SimpleNamespace(AuditLog=Mock(), StagingSession=Mock())
                getattr(integration, component).side_effect = ValueError(SENTINEL)
                reports = run.GatedReports(TOKEN, self.module, integration)
                with self.assertRaisesRegex(ValueError, '^report_gate_closed$'):
                    reports.call('prepare', {})
                self.assertEqual(reports.rejection_code, code)
                reports.close()
        self.assertEqual(self.module.open_mcp_audit.return_value.close.call_count, 2)

    def test_closed_token_never_opens_gate(self):
        reports = run.GatedReports(run.CLOSED_TOKEN, self.module, self.integration)
        with self.assertRaisesRegex(ValueError, '^report_gate_closed$'):
            reports.call('prepare', {})
        self.assertEqual(reports.rejection_code, 'report_gate_closed')
        self.module.open_mcp_audit.assert_not_called()

    def test_success_clears_previous_code_and_execution_failure_is_generic(self):
        session = self.integration.StagingSession.return_value
        session.call.side_effect = [ValueError(SENTINEL), {'synthetic': True}]
        with self.assertRaises(ValueError):
            self.reports.call('prepare', {})
        self.assertEqual(self.reports.rejection_code, 'tool_execution_rejected')
        self.assertEqual(self.reports.call('prepare', {}), {'synthetic': True})
        self.assertIsNone(self.reports.rejection_code)

    def test_result_persistence_failure_poisoned_and_not_retried(self):
        session = self.integration.StagingSession.return_value
        session.reports._prepared = {'synthetic': True}
        self.module.write_result.side_effect = OSError(SENTINEL)
        with self.assertRaisesRegex(ValueError, '^private_result_persistence_failed$'):
            self.reports.call('publish', {})
        self.assertEqual(self.reports.rejection_code, 'private_result_persistence_failed')
        with self.assertRaisesRegex(ValueError, '^report_session_failed$'):
            self.reports.call('publish', {})
        self.assertEqual(session.call.call_count, 1)
        self.assertEqual(self.module.write_result.call_count, 1)

    def test_gate_step_suppresses_exception_details(self):
        operation = Mock(side_effect=OSError(SENTINEL))
        with self.assertRaisesRegex(audit.AuditFailure, '^gate_root_rejected$') as caught:
            audit.gate_step('gate_root_rejected', operation)
        self.assertTrue(caught.exception.__suppress_context__)
        operation.assert_called_once_with()


class GateStageTests(unittest.TestCase):
    def setUp(self):
        self.root = self.enterContext(patch.object(audit, '_root_check'))
        self.read_marker = self.enterContext(patch.object(audit, '_read_marker', return_value=1000))
        self.marker = self.enterContext(patch.object(audit, '_marker'))
        self.open = self.enterContext(patch.object(audit, '_open'))
        self.active = self.enterContext(patch.object(audit, '_active'))
        self.enterContext(patch.object(audit.time, 'time', return_value=1001))
        self.exists = self.enterContext(patch.object(audit.os.path, 'lexists', return_value=False))
        self.stat = self.enterContext(patch.object(audit.os, 'fstat', return_value=SimpleNamespace(st_size=0)))

    def test_root_failure_has_fixed_code_and_no_following_access(self):
        self.root.side_effect = OSError(SENTINEL)
        with self.assertRaisesRegex(audit.AuditFailure, '^gate_root_rejected$'):
            audit.open_mcp_audit(TOKEN)
        self.read_marker.assert_not_called()
        self.marker.assert_not_called()
        self.open.assert_not_called()

    def test_receipt_and_armed_failures_are_distinguished(self):
        for failing_kind, code in (('receipt', 'gate_receipt_rejected'),
                                   ('armed', 'gate_armed_rejected')):
            def read(kind, token):
                if kind == failing_kind:
                    raise OSError(SENTINEL)
                return 1000
            self.read_marker.side_effect = read
            with self.subTest(kind=failing_kind), self.assertRaisesRegex(audit.AuditFailure, '^' + code + '$'):
                audit.open_mcp_audit(TOKEN)
        self.marker.assert_not_called()
        self.open.assert_not_called()

    def test_terminal_and_expired_gates_never_claim(self):
        self.exists.return_value = True
        with self.assertRaisesRegex(audit.AuditFailure, '^gate_terminal$'):
            audit.open_mcp_audit(TOKEN)
        self.exists.return_value = False
        self.read_marker.return_value = 0
        with self.assertRaisesRegex(audit.AuditFailure, '^gate_expired$'):
            audit.open_mcp_audit(TOKEN)
        self.marker.assert_not_called()
        self.open.assert_not_called()

    def test_claim_failure_does_not_open_audit(self):
        self.marker.side_effect = OSError(SENTINEL)
        with self.assertRaisesRegex(audit.AuditFailure, '^gate_claim_rejected$'):
            audit.open_mcp_audit(TOKEN)
        self.marker.assert_called_once_with('claim', TOKEN)
        self.open.assert_not_called()

    def test_audit_open_failure_has_fixed_code(self):
        self.open.side_effect = OSError(SENTINEL)
        with self.assertRaisesRegex(audit.AuditFailure, '^gate_audit_rejected$'):
            audit.open_mcp_audit(TOKEN)
        self.active.assert_not_called()

    def test_nonempty_audit_is_closed_and_not_overwritten(self):
        self.stat.return_value = SimpleNamespace(st_size=100)
        with self.assertRaisesRegex(audit.AuditFailure, '^gate_audit_rejected$'):
            audit.open_mcp_audit(TOKEN)
        self.open.assert_called_once_with('audit', write=True)
        self.open.return_value.write.assert_not_called()
        self.open.return_value.close.assert_called_once_with()
        self.active.assert_not_called()

    def test_gate_recheck_failure_closes_audit(self):
        self.active.side_effect = OSError(SENTINEL)
        with self.assertRaisesRegex(audit.AuditFailure, '^gate_recheck_rejected$'):
            audit.open_mcp_audit(TOKEN)
        self.open.return_value.close.assert_called_once_with()
        self.open.return_value.write.assert_not_called()


class RejectionLogTests(unittest.TestCase):
    def setUp(self):
        self.stream = Mock()
        self.stream.write.side_effect = len
        self.stream.fileno.return_value = 123
        self.fsync = self.enterContext(patch.object(audit.os, 'fsync'))
        self.log = audit.RejectionLog(self.stream)

    def test_valid_event_is_durable_and_contains_only_fixed_fields(self):
        self.log.record(event())
        payload = self.stream.write.call_args.args[0]
        self.assertEqual(json.loads(payload), event())
        self.assertEqual(payload.count(b'\n'), 1)
        self.assertEqual((self.log.events, self.log.size), (1, len(payload)))
        self.stream.flush.assert_called_once_with()
        self.fsync.assert_called_once_with(123)
        self.log.close()
        self.stream.close.assert_called_once_with()

    def test_all_fixed_codes_are_accepted(self):
        for code in adapter.REJECTION_CODES:
            with self.subTest(code=code):
                audit.validate_rejection(event(code=code))

    def test_schema_rejects_extra_fields_wrong_types_and_arbitrary_values(self):
        invalid = [None, [], event(password=SENTINEL), event(code=SENTINEL),
                   event(method=SENTINEL), event(method=[]), event(code=[]),
                   event(attempt=True), event(attempt=0), event(attempt=65),
                   event(attempt=1.0), event(timestamp_utc=SENTINEL),
                   event(timestamp_utc='2026-10-14T12:00:00+03:00'),
                   event(timestamp_utc='2026-02-31T12:00:00+00:00')]
        for value in invalid:
            with self.subTest(value_type=type(value).__name__):
                stream = Mock()
                log = audit.RejectionLog(stream)
                with self.assertRaisesRegex(audit.AuditFailure, '^private_rejection_write_failed$'):
                    log.record(value)
                self.assertTrue(log.failed)
                stream.write.assert_not_called()
        self.fsync.assert_not_called()

    def test_missing_field_is_rejected(self):
        for key in event():
            value = event()
            del value[key]
            with self.subTest(key=key), self.assertRaises(audit.AuditFailure):
                audit.validate_rejection(value)

    def test_event_limit_is_64_and_poisoned_after_failure(self):
        for attempt in range(1, 65):
            self.log.record(event(attempt=attempt))
        with self.assertRaisesRegex(audit.AuditFailure, '^private_rejection_write_failed$'):
            self.log.record(event())
        with self.assertRaises(audit.AuditFailure):
            self.log.record(event())
        self.assertEqual(self.stream.write.call_count, 64)
        self.assertTrue(self.log.failed)

    def test_byte_limit_prevents_write(self):
        self.log.size = audit.MAX_AUDIT_BYTES
        with self.assertRaises(audit.AuditFailure):
            self.log.record(event())
        self.stream.write.assert_not_called()

    def test_write_flush_fsync_failures_are_fixed_and_terminal(self):
        for stage in ('write', 'short_write', 'flush', 'fsync'):
            with self.subTest(stage=stage):
                stream = Mock()
                stream.write.side_effect = len
                if stage == 'short_write':
                    stream.write.side_effect = lambda body: len(body) - 1
                elif stage in ('write', 'flush'):
                    getattr(stream, stage).side_effect = OSError(SENTINEL)
                self.fsync.side_effect = OSError(SENTINEL) if stage == 'fsync' else None
                log = audit.RejectionLog(stream)
                with self.assertRaisesRegex(audit.AuditFailure, '^private_rejection_write_failed$'):
                    log.record(event())
                count = stream.write.call_count
                with self.assertRaises(audit.AuditFailure):
                    log.record(event())
                self.assertEqual(stream.write.call_count, count)
                self.assertTrue(log.failed)


class RejectionOpenTests(unittest.TestCase):
    def setUp(self):
        self.temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.path = Path(self.temporary) / 'synthetic-rejections.jsonl'
        self.receipt = self.enterContext(patch.object(audit, '_read_marker', return_value=1000))
        self.terminal = self.enterContext(patch.object(audit.os.path, 'lexists', return_value=False))

        def fixture_open(kind, **options):
            self.assertEqual(kind, 'rejections')
            self.assertEqual(options, {'write': True})
            return self.path.open('r+b', buffering=0)
        self.open = self.enterContext(patch.object(audit, '_open', side_effect=fixture_open))

    def test_pre_reserved_empty_file_opens_without_create_or_arm(self):
        self.path.write_bytes(b'')
        log = audit.open_rejection_log(TOKEN)
        self.addCleanup(log.close)
        self.assertIsInstance(log, audit.RejectionLog)
        self.receipt.assert_called_once_with('receipt', TOKEN)
        self.open.assert_called_once_with('rejections', write=True)

    def test_missing_sink_is_not_created(self):
        with self.assertRaises(FileNotFoundError):
            audit.open_rejection_log(TOKEN)
        self.assertFalse(self.path.exists())
        self.assertEqual(list(Path(self.temporary).iterdir()), [])

    def test_terminal_gate_does_not_open_sink(self):
        self.terminal.return_value = True
        with self.assertRaisesRegex(audit.AuditFailure, '^private_audit_terminal$'):
            audit.open_rejection_log(TOKEN)
        self.open.assert_not_called()
        self.assertFalse(self.path.exists())

    def test_bad_receipt_does_not_open_sink(self):
        self.receipt.side_effect = audit.AuditFailure('private_audit_rejected')
        with self.assertRaises(audit.AuditFailure):
            audit.open_rejection_log(TOKEN)
        self.open.assert_not_called()
        self.terminal.assert_not_called()

    def test_expired_receipt_can_log_rejection_but_cannot_open_reporting_gate(self):
        self.path.write_bytes(b'')
        with patch.object(audit.time, 'time', return_value=2000), \
                patch.object(audit, '_root_check'), patch.object(audit, '_marker') as marker:
            log = audit.open_rejection_log(TOKEN)
            try:
                log.record(event(code='gate_expired'))
                with self.assertRaisesRegex(audit.AuditFailure, '^gate_expired$'):
                    audit.open_mcp_audit(TOKEN)
            finally:
                log.close()
        marker.assert_not_called()
        self.open.assert_called_once_with('rejections', write=True)
        self.assertEqual(json.loads(self.path.read_bytes())['code'], 'gate_expired')

    def test_existing_nonempty_evidence_is_preserved(self):
        original = b'Existing synthetic evidence must remain unchanged.\n'
        self.path.write_bytes(original)
        with self.assertRaisesRegex(audit.AuditFailure, '^private_rejection_not_empty$'):
            audit.open_rejection_log(TOKEN)
        self.assertEqual(self.path.read_bytes(), original)
        # The evidence remains readable after the failed open.
        with self.path.open('r+b') as stream:
            self.assertEqual(stream.read(), original)

    def test_stat_failure_closes_handle_without_repair(self):
        self.path.write_bytes(b'')
        with patch.object(audit.os, 'fstat', side_effect=OSError(SENTINEL)):
            with self.assertRaises(OSError):
                audit.open_rejection_log(TOKEN)
        self.assertEqual(self.path.read_bytes(), b'')


class LazyRejectionTests(unittest.TestCase):
    def setUp(self):
        self.open = self.enterContext(patch.object(audit, 'open_rejection_log'))
        self.log = audit.LazyRejectionLog(TOKEN)

    def test_multiple_discovery_instances_and_close_do_not_open_or_write(self):
        other = audit.LazyRejectionLog(TOKEN)
        self.log.close()
        other.close()
        self.open.assert_not_called()
        self.open.return_value.record.assert_not_called()
        self.open.return_value.close.assert_not_called()

    def test_first_rejection_opens_once_and_later_events_reuse_sink(self):
        self.log.record(event())
        self.log.record(event(attempt=3))
        self.open.assert_called_once_with(TOKEN)
        self.assertEqual(self.open.return_value.record.call_count, 2)
        self.log.close()
        self.open.return_value.close.assert_called_once_with()

    def test_invalid_event_is_rejected_before_open_and_poisoned(self):
        with self.assertRaisesRegex(audit.AuditFailure, '^private_rejection_write_failed$'):
            self.log.record(event(code=SENTINEL))
        with self.assertRaises(audit.AuditFailure):
            self.log.record(event())
        self.assertTrue(self.log.failed)
        self.open.assert_not_called()

    def test_failed_open_is_never_retried(self):
        self.open.side_effect = OSError(SENTINEL)
        for unused in range(2):
            with self.assertRaisesRegex(audit.AuditFailure, '^private_rejection_write_failed$'):
                self.log.record(event())
        self.open.assert_called_once_with(TOKEN)
        self.assertTrue(self.log.failed)
        self.log.close()

    def test_failed_record_is_never_reopened_or_retried(self):
        self.open.return_value.record.side_effect = OSError(SENTINEL)
        for unused in range(2):
            with self.assertRaisesRegex(audit.AuditFailure, '^private_rejection_write_failed$'):
                self.log.record(event())
        self.open.assert_called_once_with(TOKEN)
        self.open.return_value.record.assert_called_once_with(event())
        self.assertTrue(self.log.failed)
        self.log.close()
        self.open.return_value.close.assert_called_once_with()


class CliRejectionTests(unittest.TestCase):
    def invoke(self, arguments, error=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(run, 'main', side_effect=error) as main, \
                patch.object(run.sys, 'argv', ['synthetic-runner.py', *arguments]), \
                patch.object(run.sys, 'stdout', stdout), patch.object(run.sys, 'stderr', stderr):
            result = run.cli()
        main.assert_called_once_with()
        return result, stdout.getvalue(), stderr.getvalue()

    def test_mcp_failure_has_empty_stdout_and_only_generic_stderr(self):
        for error in (audit.AuditFailure('gate_root_rejected'),
                      ValueError('private_rejection_write_failed'), OSError(SENTINEL),
                      ValueError(SENTINEL)):
            with self.subTest(error_type=type(error).__name__):
                result, stdout, stderr = self.invoke(['--mcp-one-shot'], error)
                self.assertEqual(result, 1)
                self.assertEqual(stdout, '')
                self.assertEqual(stderr, adapter.ERROR + '\n')
                self.assertNotIn(SENTINEL, stderr)

    def test_other_mode_preserves_bounded_host_error_json(self):
        result, stdout, stderr = self.invoke(['--preflight'], ValueError('gate_root_rejected'))
        self.assertEqual(result, 1)
        self.assertEqual(stderr, '')
        self.assertEqual(json.loads(stdout), {
            'error': 'Private one-shot evaluation stopped; no automatic retry',
            'code': 'gate_root_rejected'})

    def test_other_mode_still_hides_arbitrary_exception_details(self):
        result, stdout, stderr = self.invoke(['--preflight'], OSError(SENTINEL))
        self.assertEqual(result, 1)
        self.assertEqual(json.loads(stdout)['code'], 'one_shot_failed')
        self.assertNotIn(SENTINEL, stdout + stderr)

    def test_success_does_not_add_output(self):
        self.assertEqual(self.invoke(['--mcp-one-shot']), (0, '', ''))


if __name__ == '__main__':
    unittest.main()
