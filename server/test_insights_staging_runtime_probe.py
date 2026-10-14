"""Pure offline tests: no runtime process, sockets, models, SSH or report calls."""
import copy
import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import insights_staging_runtime_probe as probe


class ProbePureTests(unittest.TestCase):
    def event(self, item):
        return {'method': 'rawResponseItem/completed', 'params': {
            'threadId': 'synthetic-thread', 'turnId': 'synthetic-turn', 'item': item}}

    def evidence(self):
        evidence = probe.Evidence()
        evidence.thread_id, evidence.turn_id = 'synthetic-thread', 'synthetic-turn'
        return evidence

    def test_plan_is_fixed_bounded_and_uses_only_synthetic_targets(self):
        plan = probe.plans()
        self.assertEqual(plan, probe.plans())
        self.assertEqual(len(plan), 24)
        self.assertEqual(len({item['call_id'] for item in plan}), 24)
        calls = [item for item in plan if item['type'] == 'function_call']
        self.assertEqual(len(calls), 18)
        for item in calls:
            args = json.loads(item['arguments'])
            if 'target' in args:
                self.assertEqual(args['target'], probe.MISSING_TARGET)
        self.assertIn(probe.SENTINEL.as_posix(), json.loads(
            next(i for i in plan if i['call_id'] == 'he_absent_apply_patch')['input']
            .removeprefix('text(await tools.apply_patch(').removesuffix('));')))

    def test_sse_stream_has_argument_delta_and_completion(self):
        for item in probe.plans():
            events = probe.response_events(item, 1)
            self.assertEqual(events[0]['type'], 'response.created')
            self.assertEqual(events[-1]['type'], 'response.completed')
            field = 'arguments' if item['type'] == 'function_call' else 'input'
            self.assertEqual(events[1]['item'][field], '')
            self.assertEqual(events[2]['delta'], item[field])
            self.assertEqual(events[3][field], item[field])

    def test_instructions_must_be_in_developer_message_not_prompt_or_output(self):
        text = 'EXACT PRIVATE INSTRUCTION TEXT'
        for role in ('user', 'assistant', 'tool'):
            self.assertFalse(probe.developer_instructions_present(
                {'input': [{'role': role, 'content': text}]}, text))
        self.assertFalse(probe.developer_instructions_present({'input': [{
            'type': 'custom_tool_call_output', 'role': 'developer', 'content': text}]}, text))
        self.assertTrue(probe.developer_instructions_present({'input': [{
            'type': 'message', 'role': 'developer', 'content': [
                {'type': 'input_text', 'text': 'Prefix\n' + text + '\nSuffix'}]}]}, text))
        self.assertFalse(probe.developer_instructions_present(
            {'instructions': text, 'input': []}, text))

    def test_catalog_rejects_extra_tools_and_wrong_types(self):
        body = {'tools': [{'type': 'namespace', 'name': 'functions',
                          'tools': [{'type': 'custom', 'name': 'exec'}]}]}
        self.assertEqual(probe.catalog(body), (('functions.exec', 'custom'),))
        for item in ({'type': 'function', 'name': 'shell'},
                     {'type': 'web_search', 'name': 'functions.wait'},
                     {'type': 'namespace', 'name': 'collaboration',
                      'tools': [{'type': 'function', 'name': 'spawn_agent'}]}):
            changed = copy.deepcopy(body)
            changed['tools'].append(item)
            with self.assertRaises(ValueError):
                probe.catalog(changed)

    def test_duplicate_json_rejected(self):
        for raw in (b'{"input":[],"input":[]}', b'{"x":NaN}'):
            with self.assertRaises(ValueError):
                probe.decode(raw)

    def test_headers_exact_endpoint_no_auth_no_chunked_or_duplicate(self):
        headers = [('Host', '127.0.0.1:12345'), ('Content-Length', '30'),
                   ('Content-Type', 'application/json')]
        self.assertEqual(probe.validate_headers('POST', '/v1/responses', headers, 12345), 30)
        metadata = [(name, 'synthetic-value') for name in (
            'session-id', 'thread-id', 'x-codex-window-id',
            'x-openai-internal-codex-responses-lite')]
        self.assertEqual(probe.validate_headers('POST', '/v1/responses',
                                              headers + metadata, 12345), 30)
        for extra in [('Authorization', 'DO_NOT_LOG'), ('Cookie', 'DO_NOT_LOG'),
                      ('Content-Length', '30'), ('Transfer-Encoding', 'chunked'),
                      ('X-Unreviewed', 'value')]:
            with self.assertRaises(ValueError):
                probe.validate_headers('POST', '/v1/responses', headers + [extra], 12345)
        for method, path in [('GET', '/v1/models'), ('POST', '/responses'),
                             ('POST', '/v1/responses?secret=value')]:
            with self.assertRaises(ValueError):
                probe.validate_headers(method, path, headers, 12345)
        for length in ('-1', '0', '1000001', 'x'):
            with self.assertRaises(ValueError):
                probe.validate_headers('POST', '/v1/responses',
                    [headers[0], ('Content-Length', length), headers[2]], 12345)

    def test_config_has_loopback_only_no_auth_retries_or_websockets(self):
        original = {'model_provider': 'openai'}
        result = probe.probe_config(original, 12345)
        self.assertEqual(original, {'model_provider': 'openai'})
        self.assertEqual(set(result['model_providers']), {probe.PROVIDER})
        provider = result['model_providers'][probe.PROVIDER]
        self.assertEqual(provider['base_url'], 'http://127.0.0.1:12345/v1')
        self.assertIs(provider['requires_openai_auth'], False)
        self.assertIs(provider['supports_websockets'], False)
        self.assertIs(provider['supports_standalone_web_search'], False)
        self.assertEqual(provider['request_max_retries'], 0)
        self.assertEqual(provider['stream_max_retries'], 0)
        with patch.object(probe.launcher, 'verify_effective'):
            probe.verify_config(result, result)
            for key, value in [('base_url', 'https://example.invalid'),
                               ('env_key', 'SYNTHETIC_KEY'), ('request_max_retries', 1),
                               ('supports_standalone_web_search', True)]:
                altered = copy.deepcopy(result)
                altered['model_providers'][probe.PROVIDER][key] = value
                with self.assertRaises(ValueError):
                    probe.verify_config(altered, result)

    def test_model_catalog_route_headers_and_two_request_budget_are_fixed(self):
        headers = [('Host', '127.0.0.1:12345'), ('Accept', 'application/json'),
                   ('Originator', 'synthetic-client'), ('User-Agent', 'synthetic-client')]
        for count in (0, 1):
            probe.validate_model_headers(probe.MODEL_CATALOG_ROUTE, headers, 12345, count)
        for count in (-1, 2, 100, True):
            with self.assertRaises(ValueError):
                probe.validate_model_headers(probe.MODEL_CATALOG_ROUTE, headers, 12345, count)
        for path in ('/v1/models', '/v1/models?client_version=0.162.1',
                     '/v1/models?client_version=0.162.0&extra=1', '/other'):
            with self.assertRaises(ValueError):
                probe.validate_model_headers(path, headers, 12345, 0)
        for extra in [('Authorization', 'SECRET'), ('Cookie', 'SECRET'),
                      ('X-Api-Key', 'SECRET'), ('X-Unreviewed', 'value'),
                      ('Content-Length', '1'), ('Host', '127.0.0.1:12345')]:
            with self.assertRaises(ValueError):
                probe.validate_model_headers(probe.MODEL_CATALOG_ROUTE, headers + [extra], 12345, 0)
        with self.assertRaises(ValueError):
            probe.validate_model_headers(probe.MODEL_CATALOG_ROUTE,
                                         [('Host', 'example.invalid')], 12345, 0)

    def test_diagnostics_only_expose_fixed_guard_code_format(self):
        self.assertEqual(probe.failure_code(probe.launcher.GuardFailure('probe_unexpected_header')),
                         'probe_unexpected_header')
        for error in (ValueError('SECRET_HEADER_VALUE'), OSError('/private/path'),
                      probe.launcher.GuardFailure('SECRET_HEADER_VALUE'),
                      probe.launcher.GuardFailure('authorization: secret'),
                      probe.launcher.GuardFailure('x' * 81),
                      probe.launcher.GuardFailure('safe_code', 'secret')):
            self.assertEqual(probe.failure_code(error), 'probe_unexpected_failure')

    def test_evidence_requires_call_identity_and_matching_output(self):
        expected = probe.plans()[1]
        output = {'type': 'function_call_output', 'call_id': expected['call_id'],
                  'output': 'unsupported call: ' + expected.get('namespace', '') + expected['name']}
        with self.assertRaises(ValueError):
            self.evidence().accept(self.event(output))
        evidence = self.evidence()
        evidence.accept(self.event(expected))
        evidence.accept(self.event(output))
        self.assertEqual(evidence.results, {expected['call_id']})
        with self.assertRaises(ValueError):
            evidence.accept(self.event(output))
        altered = dict(expected, arguments='{}')
        if altered == expected:
            altered['arguments'] = '{"unexpected":true}'
        with self.assertRaises(ValueError):
            self.evidence().accept(self.event(altered))

    def test_catalog_output_must_be_exact_and_paired(self):
        for names in (probe.NESTED + ['exec_command'], list(reversed(probe.NESTED)), []):
            evidence = self.evidence()
            evidence.accept(self.event(probe.plans()[0]))
            with self.assertRaises(ValueError):
                evidence.accept(self.event({'type': 'custom_tool_call_output',
                    'call_id': 'he_catalog', 'output': json.dumps(names)}))
        evidence = self.evidence()
        evidence.accept(self.event(probe.plans()[0]))
        evidence.accept(self.event({'type': 'custom_tool_call_output', 'call_id': 'he_catalog',
                                   'output': json.dumps(probe.NESTED)}))
        self.assertTrue(evidence.nested_verified)

    def test_unavailable_and_blocked_outputs_cannot_be_success(self):
        for identifier in ('he_absent_apply_patch', 'he_blocked_prepare', 'he_blocked_publish'):
            evidence = self.evidence()
            evidence.accept(self.event(evidence.expected[identifier]))
            with self.assertRaises(ValueError):
                evidence.accept(self.event({'type': 'custom_tool_call_output',
                    'call_id': identifier, 'output': '{"status":"staged"}'}))

    def test_provider_requires_previous_paired_output_before_next_call(self):
        wanted = probe.plans()[0]
        item = {'type': 'custom_tool_call_output', 'call_id': wanted['call_id'],
                'output': json.dumps(probe.NESTED)}
        probe.verify_previous_output({'input': [item]}, wanted)
        for items in ([], [dict(item, call_id='wrong')], [item, item],
                      [dict(item, output=json.dumps(probe.NESTED + ['apply_patch']))]):
            with self.assertRaises(ValueError):
                probe.verify_previous_output({'input': items}, wanted)

    def test_child_thread_event_fails_closed(self):
        evidence = self.evidence()
        evidence.accept({'method': 'thread/started', 'params': {'thread': {'id': 'synthetic-thread'}}})
        with self.assertRaises(ValueError):
            evidence.accept({'method': 'thread/started', 'params': {'thread': {'id': 'unexpected-child'}}})

    def test_extra_rpc_does_not_allow_arbitrary_turn_prompt(self):
        session = object.__new__(probe.ProbeSession)
        session.evidence = self.evidence()
        session.process = SimpleNamespace(stdin=io.BytesIO())
        for message in ({'id': 1, 'method': 'turn/start', 'params': {'threadId': 'synthetic-thread',
                         'input': [{'type': 'text', 'text': 'unapproved prompt'}]}},
                        {'id': 1, 'method': 'thread/loaded/list', 'params': {'extra': True}}):
            with self.assertRaises(ValueError):
                session.send(message)
        self.assertEqual(session.process.stdin.getvalue(), b'')
        self.assertNotIn('turn/start', probe.launcher.RPC_METHODS)


if __name__ == '__main__':
    unittest.main()
