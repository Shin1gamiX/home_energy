"""Offline one-shot regressions: no model, SSH, private files or child processes."""
import copy
import hashlib
import io
import json
import queue
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

import insights_staging_launcher as launcher
import insights_staging_mcp as adapter
import insights_staging_run as run
from test_insights_staging_launcher import config, inventory, thread


TOKEN = 'a' * 64


def changed_config():
    return run.run_config(config(), 'Synthetic reporting guidance.', TOKEN)


def private_result():
    # Minimal bounded synthetic contract; no fixture or private file is read.
    analysis = {name: None for name in ('generated_at', 'window', 'baseline',
        'data_through', 'coverage_pct', 'severity', 'metrics', 'limitations')}
    analysis.update(schema_version=2, findings=[])
    identifier = hashlib.sha256(adapter.encode(analysis)).hexdigest()
    return {
        'analysis': {'scope': 'staging', 'synthetic': True, 'result': {
            'analysis_id': identifier, 'analysis': analysis}},
        'submission': {'analysis_id': identifier, 'summary': {
            'en': 'Private synthetic review.', 'el': 'Ιδιωτική δοκιμαστική αναφορά.',
            'ru': 'Частный тестовый отчёт.'}, 'notes': []},
        'acknowledgement': {'scope': 'staging', 'synthetic': True, 'result': {
            'status': 'staged', 'id': identifier}},
    }


def session_without_process():
    cls = run.session_class(launcher)
    session = cls.__new__(cls)
    session.evidence = run.RunEvidence()
    session.evidence.thread_id = 'synthetic-thread'
    session.turn_sent = False
    session.armed = False
    session.process = SimpleNamespace(stdin=io.BytesIO())
    session.counter = 0
    session.deadline = 1000.0
    session.inbox = queue.Queue()
    return session


def start_message(**changes):
    value = {'id': 1, 'method': 'turn/start', 'params': {
        'threadId': 'synthetic-thread', 'input': [{'type': 'text', 'text': run.PROMPT}]}}
    value.update(changes)
    return value


class ConfigurationTests(unittest.TestCase):
    def test_config_is_deep_copy_and_preserves_root_deny_profile(self):
        original = config()
        before = copy.deepcopy(original)
        changed = run.run_config(original, 'Synthetic guidance.', TOKEN)
        self.assertEqual(original, before)
        for key in ('permissions', 'agents', 'features', 'windows', 'approval_policy',
                    'default_permissions', 'model', 'model_reasoning_effort', 'apps', 'web_search', 'notify'):
            self.assertEqual(changed[key], before[key])
        changed['permissions'][launcher.PROFILE]['network']['enabled'] = True
        self.assertEqual(original, before)

    def test_one_shot_config_preserves_empty_notify_without_aliasing_original(self):
        original = config()
        changed = run.run_config(original, 'Synthetic guidance.', TOKEN)
        self.assertEqual(original['notify'], [])
        self.assertEqual(changed['notify'], [])
        self.assertIs(type(changed['notify']), list)
        self.assertIsNot(changed['notify'], original['notify'])
        changed['notify'].append('synthetic-notifier.exe')
        self.assertEqual(original['notify'], [])

    def test_effective_one_shot_config_rejects_nonempty_or_missing_notify(self):
        expected = changed_config()
        missing = object()
        for value in (missing, None, False, True, '', {}, (), ['synthetic-notifier.exe']):
            actual = copy.deepcopy(expected)
            if value is missing:
                actual.pop('notify', None)
            else:
                actual['notify'] = value
            with self.subTest(value_type=type(value).__name__), self.assertRaisesRegex(
                    ValueError, '^runtime_notify_mismatch$'):
                run.verify_config(launcher, actual, expected)

    def test_only_active_server_command_gets_capability_token(self):
        original = config()
        changed = run.run_config(original, 'Synthetic guidance.', TOKEN)
        active = changed['mcp_servers'][launcher.SERVER]
        self.assertEqual(active['args'], ['-I', '-B', str(run.ENTRY), '--mcp-one-shot'])
        self.assertEqual(active['env'], {run.TOKEN_ENV: TOKEN})
        self.assertEqual(active['enabled_tools'], ['prepare', 'publish'])
        self.assertEqual(changed['mcp_servers']['node_repl'], original['mcp_servers']['node_repl'])
        self.assertEqual(changed['model_provider'], 'openai')
        self.assertEqual(changed['model_providers'], {})
        self.assertIn(run.AUTHORIZATION, changed['developer_instructions'])
        self.assertIn('Synthetic guidance.', changed['developer_instructions'])

    def test_invalid_token_is_rejected_without_mutating_config(self):
        original = config()
        before = copy.deepcopy(original)
        for token in (None, True, b'a' * 64, 'a' * 63, 'A' * 64, 'g' * 64, 'a' * 65):
            with self.subTest(token_type=type(token).__name__), self.assertRaises(ValueError):
                run.run_config(original, 'Synthetic guidance.', token)
        self.assertEqual(original, before)

    def test_instruction_limit_is_utf8_byte_bounded(self):
        with self.assertRaisesRegex(ValueError, 'instructions_too_large'):
            run.run_config(config(), 'α' * 8192, TOKEN)

    def test_effective_exact_config_is_accepted(self):
        expected = changed_config()
        run.verify_config(launcher, copy.deepcopy(expected), expected)

    def test_provider_rerouting_is_rejected(self):
        expected = changed_config()
        for key, value in [('model_provider', None), ('model_provider', 'synthetic'),
                           ('model_providers', {'custom': {'base_url': 'https://example.invalid'}})]:
            actual = copy.deepcopy(expected)
            actual[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                run.verify_config(launcher, actual, expected)

    def test_permission_command_and_token_drift_are_rejected(self):
        expected = changed_config()
        mutations = [
            lambda value: value['permissions'][launcher.PROFILE]['network'].update(enabled=True),
            lambda value: value['agents'].update(enabled=True),
            lambda value: value['mcp_servers'][launcher.SERVER]['env'].update({run.TOKEN_ENV: 'b' * 64}),
            lambda value: value['mcp_servers'][launcher.SERVER].update(args=['-c', 'arbitrary']),
        ]
        for mutation in mutations:
            actual = copy.deepcopy(expected)
            mutation(actual)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                run.verify_config(launcher, actual, expected)

    def test_only_existing_chatgpt_authentication_is_accepted(self):
        run.verify_account({'requiresOpenaiAuth': True, 'account': {'type': 'chatgpt'}})
        for value in (None, {}, {'requiresOpenaiAuth': True, 'account': None},
                      {'requiresOpenaiAuth': False, 'account': {'type': 'chatgpt'}},
                      {'requiresOpenaiAuth': 1, 'account': {'type': 'chatgpt'}},
                      {'requiresOpenaiAuth': True, 'account': {'type': 'apiKey'}},
                      {'requiresOpenaiAuth': True, 'account': {'type': 'chatgptAuthTokens'}}):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'chatgpt_authentication_required'):
                run.verify_account(value)

    def test_error_codes_do_not_reflect_exception_details(self):
        self.assertEqual(run.fixed_code(ValueError('bounded_code')), 'bounded_code')
        for value in (RuntimeError('private_path'), ValueError('sensitive path or token'),
                      ValueError('a' * 81), ValueError('A'), ValueError('one', 'two')):
            self.assertEqual(run.fixed_code(value), 'one_shot_failed')


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.session = session_without_process()

    def test_turn_requires_armed_state_and_verified_thread(self):
        with self.assertRaisesRegex(ValueError, 'generation_not_armed_or_already_consumed'):
            self.session.send(start_message())
        self.session.armed = True
        self.session.evidence.thread_id = None
        with self.assertRaises(ValueError):
            self.session.send(start_message())
        self.assertEqual(self.session.process.stdin.getvalue(), b'')
        self.assertFalse(self.session.turn_sent)

    def test_one_exact_turn_and_no_second_turn(self):
        self.session.armed = True
        self.session.send(start_message())
        before = self.session.process.stdin.getvalue()
        self.assertEqual(json.loads(before), start_message())
        self.assertTrue(self.session.turn_sent)
        with self.assertRaisesRegex(ValueError, 'generation_not_armed_or_already_consumed'):
            self.session.send(start_message(id=2))
        self.assertEqual(self.session.process.stdin.getvalue(), before)

    def test_turn_is_consumed_before_short_write_write_error_or_flush_error(self):
        for failure in ('short', 'write', 'flush'):
            session = session_without_process()
            session.armed = True
            sink = Mock()
            sink.write.side_effect = (OSError('synthetic') if failure == 'write'
                                      else lambda body: len(body) - (failure == 'short'))
            if failure == 'flush':
                sink.flush.side_effect = OSError('synthetic')
            session.process.stdin = sink
            with self.subTest(failure=failure), self.assertRaises((OSError, ValueError)):
                session.send(start_message())
            self.assertTrue(session.turn_sent)
            with self.assertRaisesRegex(ValueError, 'generation_not_armed_or_already_consumed'):
                session.send(start_message(id=2))
            sink.write.assert_called_once()

    def test_turn_cannot_override_prompt_model_permissions_or_input_type(self):
        self.session.armed = True
        parameters = start_message()['params']
        cases = [{**parameters, 'model': 'other'}, {**parameters, 'permissions': 'full'},
                 {**parameters, 'threadId': 'other'}, {**parameters, 'input': []},
                 {**parameters, 'input': [{'type': 'text', 'text': 'Changed instruction'}]}]
        for value in cases:
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'one_shot_rpc_refused'):
                self.session.send(start_message(params=value))
        self.assertFalse(self.session.turn_sent)
        self.assertEqual(self.session.process.stdin.getvalue(), b'')

    def test_special_rpc_ids_are_strict_integers(self):
        for identifier in (None, True, '1', 1.0):
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                self.session.send({'id': identifier, 'method': 'account/read',
                                   'params': {'refreshToken': False}})
        self.assertEqual(self.session.process.stdin.getvalue(), b'')

    def test_account_read_never_requests_refresh(self):
        self.session.send({'id': 1, 'method': 'account/read', 'params': {'refreshToken': False}})
        before = self.session.process.stdin.getvalue()
        for params in ({}, {'refreshToken': True}, {'refreshToken': False, 'apiKey': 'not-a-key'}):
            with self.subTest(params=params), self.assertRaisesRegex(ValueError, 'one_shot_rpc_refused'):
                self.session.send({'id': 2, 'method': 'account/read', 'params': params})
        self.assertEqual(self.session.process.stdin.getvalue(), before)

    def test_loaded_thread_list_requires_empty_params(self):
        self.session.send({'id': 1, 'method': 'thread/loaded/list', 'params': {}})
        with self.assertRaisesRegex(ValueError, 'one_shot_rpc_refused'):
            self.session.send({'id': 2, 'method': 'thread/loaded/list', 'params': {'limit': 100}})

    def test_rpc_allowlist_rejects_resume_fork_login_and_arbitrary_methods(self):
        for method in ('turn/steer', 'thread/resume', 'thread/fork', 'account/login/start',
                       'command/exec', 'collaboration/spawn_agent', 'filesystem/readFile'):
            with self.subTest(method=method), self.assertRaises(ValueError):
                self.session.request(method, {})
        self.assertEqual(self.session.process.stdin.getvalue(), b'')

    def test_receive_rejects_client_requests_rpc_errors_and_expired_deadline(self):
        for event in ({'id': 2, 'method': 'item/commandExecution/requestApproval', 'params': {}},
                      {'id': 2, 'error': {'message': 'synthetic'}}):
            self.session.inbox.put(event)
            with patch.object(run.time, 'monotonic', return_value=100), self.assertRaises(ValueError):
                self.session.receive()
        with patch.object(run.time, 'monotonic', return_value=1001), \
                self.assertRaisesRegex(ValueError, 'one_shot_runtime_timeout'):
            self.session.receive()

    def test_receive_empty_queue_has_bounded_wait(self):
        self.session.inbox = Mock()
        self.session.inbox.get.side_effect = queue.Empty
        with patch.object(run.time, 'monotonic', return_value=999), \
                self.assertRaisesRegex(ValueError, 'one_shot_runtime_timeout'):
            self.session.receive()
        self.session.inbox.get.assert_called_once_with(timeout=1)

    def test_gates_verify_twice_and_never_arm_or_generate(self):
        expected = changed_config()
        responses = [{}, {'config': copy.deepcopy(expected)},
                     {'requiresOpenaiAuth': True, 'account': {'type': 'chatgpt'}},
                     thread(), inventory(), {'config': copy.deepcopy(expected)}]
        self.session.request = Mock(side_effect=responses)
        self.session.send = Mock()
        self.session.gates(expected, adapter.TOOLS)
        self.assertEqual([item.args[0] for item in self.session.request.call_args_list],
                         ['initialize', 'config/read', 'account/read', 'thread/start',
                          'mcpServerStatus/list', 'config/read'])
        self.assertFalse(self.session.armed)
        self.assertFalse(self.session.turn_sent)
        self.assertEqual(self.session.evidence.thread_id, 'synthetic-thread')
        self.assertEqual(self.session.request.call_args_list[3].args[1], {
            'cwd': str(launcher.WORKSPACE), 'ephemeral': True, 'permissions': launcher.PROFILE,
            'approvalPolicy': 'never', 'model': launcher.MODEL,
            'environments': [], 'runtimeWorkspaceRoots': [], 'experimentalRawEvents': True})

    def test_gates_abort_before_thread_when_authentication_is_wrong(self):
        expected = changed_config()
        self.session.request = Mock(side_effect=[{}, {'config': expected},
            {'requiresOpenaiAuth': True, 'account': {'type': 'apiKey'}}])
        self.session.send = Mock()
        with self.assertRaisesRegex(ValueError, 'chatgpt_authentication_required'):
            self.session.gates(expected, adapter.TOOLS)
        self.assertEqual(self.session.request.call_count, 3)
        self.assertFalse(self.session.armed)

    def test_notify_drift_aborts_gates_before_account_thread_tools_or_generation(self):
        expected = changed_config()
        changed = copy.deepcopy(expected)
        changed['notify'] = ['synthetic-notifier.exe']
        self.session.request = Mock(side_effect=[{}, {'config': changed}])
        self.session.send = Mock()
        with self.assertRaisesRegex(ValueError, '^runtime_notify_mismatch$'):
            self.session.gates(expected, adapter.TOOLS)
        self.assertEqual([item.args[0] for item in self.session.request.call_args_list],
                         ['initialize', 'config/read'])
        self.session.send.assert_called_once_with({'method': 'initialized', 'params': {}})
        self.assertFalse(self.session.armed)
        self.assertFalse(self.session.turn_sent)
        self.assertEqual(self.session.process.stdin.getvalue(), b'')


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.evidence = run.RunEvidence()
        self.evidence.thread_id = 'synthetic-thread'
        self.evidence.turn_id = 'synthetic-turn'

    def event(self, method, **params):
        return {'method': method, 'params': {'threadId': 'synthetic-thread',
            'turnId': 'synthetic-turn', **params}}

    def test_child_thread_and_model_reroute_are_rejected(self):
        self.evidence.accept({'method': 'thread/started', 'params': {'thread': {'id': 'synthetic-thread'}}})
        with self.assertRaisesRegex(ValueError, 'unexpected_child_thread'):
            self.evidence.accept({'method': 'thread/started', 'params': {'thread': {'id': 'child'}}})
        for method in ('model/rerouted', 'turn/diff/updated', 'hook/started', 'hook/completed'):
            with self.subTest(method=method), self.assertRaisesRegex(ValueError, 'unexpected_runtime_action'):
                self.evidence.accept({'method': method, 'params': {}})

    def test_known_dangerous_item_types_are_rejected(self):
        for kind in ('commandExecution', 'fileChange', 'collabToolCall', 'webSearch',
                     'imageView', 'dynamicToolCall', 'newUnreviewedTool', None):
            for method in ('item/started', 'item/completed'):
                with self.subTest(kind=kind, method=method), self.assertRaisesRegex(ValueError, 'unexpected_tool_item'):
                    self.evidence.accept(self.event(method, item={'type': kind}))

    def test_only_two_report_mcp_tools_are_accepted(self):
        for name in ('prepare', 'publish'):
            self.evidence.accept(self.event('item/started', item={
                'type': 'mcpToolCall', 'server': 'home_energy_staging', 'tool': name, 'id': name}))
        for server, name in [('other', 'prepare'), ('home_energy_staging', 'exec'), ('node_repl', 'js')]:
            with self.subTest(server=server, name=name), self.assertRaisesRegex(ValueError, 'unexpected_mcp_item'):
                self.evidence.accept(self.event('item/completed', item={
                    'type': 'mcpToolCall', 'server': server, 'tool': name, 'id': name}))

    def test_other_turn_is_rejected_and_failed_turn_is_not_completion(self):
        with self.assertRaisesRegex(ValueError, 'unexpected_turn'):
            self.evidence.accept(self.event('item/started', turnId='other-turn', item={'type': 'agentMessage'}))
        for status in ('failed', 'interrupted', 'inProgress', None):
            with self.subTest(status=status), self.assertRaisesRegex(ValueError, 'model_turn_failed'):
                self.evidence.accept(self.event('turn/completed', turn={'id': 'synthetic-turn', 'status': status}))
            self.assertFalse(self.evidence.completed)

    def test_wrong_thread_and_nested_turn_ids_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'unexpected_thread_event'):
            self.evidence.accept(self.event('item/started', threadId='child', item={'type': 'agentMessage'}))
        for method in ('turn/started', 'turn/completed'):
            for identifier in (None, 'other-turn'):
                with self.subTest(method=method, identifier=identifier), self.assertRaisesRegex(ValueError, 'unexpected_turn'):
                    self.evidence.accept(self.event(method, turn={'id': identifier, 'status': 'completed'}))
        self.assertFalse(self.evidence.completed)

    def test_started_notification_can_bind_ids_before_rpc_response(self):
        evidence = run.RunEvidence()
        evidence.accept({'method': 'thread/started', 'params': {'thread': {'id': 'synthetic-thread'}}})
        evidence.accept(self.event('turn/started', turn={'id': 'synthetic-turn'}))
        self.assertEqual(evidence.thread_id, 'synthetic-thread')
        self.assertEqual(evidence.turn_id, 'synthetic-turn')

    def test_completion_and_usage_are_bounded(self):
        self.evidence.accept(self.event('turn/completed', turn={'id': 'synthetic-turn', 'status': 'completed'}))
        self.assertTrue(self.evidence.completed)
        self.evidence.accept(self.event('thread/tokenUsage/updated', tokenUsage={'total': {'totalTokens': 1234}}))
        self.assertEqual(self.evidence.total_tokens, 1234)
        for value in (None, True, -1, 1_000_000, 3.2, '1234'):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'invalid_usage_event'):
                self.evidence.accept(self.event('thread/tokenUsage/updated', tokenUsage={'total': {'totalTokens': value}}))

    def test_diagnostic_retains_completed_final_text_not_reasoning_or_commentary(self):
        for kind, phase in [('reasoning', None), ('agentMessage', 'commentary'),
                            ('userMessage', None)]:
            self.evidence.accept(self.event('item/completed', item={
                'type': kind, 'phase': phase, 'text': 'Not diagnostic final text.'}))
        self.evidence.accept(self.event('item/started', item={
            'type': 'agentMessage', 'phase': 'final_answer', 'text': 'Incomplete final text.'}))
        self.assertEqual(self.evidence.diagnostic(True)['final_message'], '')
        self.evidence.accept(self.event('item/completed', item={
            'type': 'agentMessage', 'phase': 'final_answer', 'text': 'Synthetic final outcome.'}))
        self.evidence.accept(self.event('item/completed', item={
            'type': 'agentMessage', 'phase': 'commentary', 'text': 'Later commentary.'}))
        self.assertEqual(self.evidence.diagnostic(True), {
            'model_started': True, 'model_completed': False, 'mcp_items': 0,
            'tool_calls': 0, 'total_tokens': None, 'final_message': 'Synthetic final outcome.'})
        self.evidence.accept(self.event('item/completed', item={
            'type': 'agentMessage', 'text': 'Legacy phase-absent final.'}))
        self.assertEqual(self.evidence.diagnostic(True)['final_message'], 'Legacy phase-absent final.')

    def test_final_message_limit_and_invalid_text_are_rejected(self):
        for text in (None, [], {'text': 'synthetic'}, 'x' * 8001):
            with self.subTest(text_type=type(text).__name__), self.assertRaisesRegex(ValueError, 'final_message_limit'):
                self.evidence.accept(self.event('item/completed', item={
                    'type': 'agentMessage', 'phase': 'final_answer', 'text': text}))
        self.assertEqual(self.evidence.final_message, '')
        self.evidence.accept(self.event('item/completed', item={
            'type': 'agentMessage', 'phase': 'final_answer', 'text': 'x' * 8000}))
        self.assertEqual(len(self.evidence.diagnostic(True)['final_message']), 8000)

    def test_tool_counts_are_unique_ids_not_started_completed_events(self):
        item = {'type': 'mcpToolCall', 'server': 'home_energy_staging', 'tool': 'prepare', 'id': 'mcp-id'}
        for method in ('item/started', 'item/completed', 'item/completed'):
            self.evidence.accept(self.event(method, item=item))
        for kind in ('function_call', 'custom_tool_call', 'function_call'):
            self.evidence.accept(self.event('rawResponseItem/completed', item={
                'type': kind, 'call_id': 'raw-id'}))
        for kind in ('reasoning', 'message', 'function_call_output', 'custom_tool_call_output'):
            self.evidence.accept(self.event('rawResponseItem/completed', item={
                'type': kind, 'call_id': 'ignored-id'}))
        diagnostic = self.evidence.diagnostic(True)
        self.assertEqual(diagnostic['mcp_items'], 1)
        self.assertEqual(diagnostic['tool_calls'], 1)
        self.assertNotIn('mcp-id', json.dumps(diagnostic))
        self.assertNotIn('raw-id', json.dumps(diagnostic))

    def test_tool_identifier_type_length_and_counts_are_bounded(self):
        for mode in ('mcp', 'raw'):
            for identifier in (None, 1, [], 'x' * 257):
                item = ({'type': 'mcpToolCall', 'server': 'home_energy_staging',
                         'tool': 'prepare', 'id': identifier} if mode == 'mcp' else
                        {'type': 'function_call', 'call_id': identifier})
                method = 'item/completed' if mode == 'mcp' else 'rawResponseItem/completed'
                with self.subTest(mode=mode, identifier_type=type(identifier).__name__), \
                        self.assertRaisesRegex(ValueError, 'invalid_tool_identifier'):
                    self.evidence.accept(self.event(method, item=item))
            evidence = run.RunEvidence()
            evidence.thread_id = 'synthetic-thread'
            evidence.turn_id = 'synthetic-turn'
            for number in range(64):
                item = ({'type': 'mcpToolCall', 'server': 'home_energy_staging',
                         'tool': 'prepare', 'id': str(number)} if mode == 'mcp' else
                        {'type': 'function_call', 'call_id': str(number)})
                evidence.accept(self.event(method, item=item))
            item['id' if mode == 'mcp' else 'call_id'] = '65th-unique-id'
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, 'tool_count_limit'):
                evidence.accept(self.event(method, item=item))

    def test_diagnostic_is_fixed_fields_and_does_not_expose_runtime_ids(self):
        self.evidence.completed = True
        self.evidence.total_tokens = 456
        diagnostic = self.evidence.diagnostic(False)
        self.assertEqual(set(diagnostic), {'model_started', 'model_completed', 'mcp_items',
                                         'tool_calls', 'total_tokens', 'final_message'})
        self.assertIs(diagnostic['model_started'], False)
        self.assertIs(diagnostic['model_completed'], True)
        self.assertEqual(diagnostic['total_tokens'], 456)
        self.assertNotIn('synthetic-thread', json.dumps(diagnostic))
        self.assertNotIn('synthetic-turn', json.dumps(diagnostic))


class DiagnosticProbeTests(unittest.TestCase):
    def run_probe(self, *, completed=True, final='Fixed final.', calls=2):
        probe = Mock(FINAL='Fixed final.')
        probe.plans.return_value = ['first', 'second']

        def simulate(*, candidate_config, observer):
            self.assertEqual(candidate_config, {'synthetic': True})
            observer({'method': 'thread/started', 'params': {'thread': {'id': 'probe'}}})

            def emit(method, **params):
                observer({'method': method, 'params': {'threadId': 'probe', **params}})

            for index in range(calls):
                emit('rawResponseItem/completed', item={'type': 'custom_tool_call', 'call_id': str(index)})
            emit('item/completed', item={'type': 'agentMessage', 'phase': 'final_answer', 'text': final})
            if completed:
                emit('turn/completed', turn={'id': 'probe-turn', 'status': 'completed'})
            return {'synthetic_probe_completed': True}

        probe.run_probe.side_effect = simulate
        return run.diagnostic_probe(probe, {'synthetic': True})

    def test_all_three_observed_signals_are_required(self):
        self.assertEqual(self.run_probe(), {'synthetic_probe_completed': True,
            'diagnostic_capture_verified': True, 'diagnostic_tool_calls': 2})

    def test_completion_failure_stops_before_real_run(self):
        with self.assertRaisesRegex(ValueError, 'diagnostic_probe_not_completed'):
            self.run_probe(completed=False)

    def test_missing_or_wrong_final_stops_before_real_run(self):
        for final in ('', 'Different final.'):
            with self.subTest(final=final), self.assertRaisesRegex(ValueError, 'diagnostic_probe_final_missing'):
                self.run_probe(final=final)

    def test_missing_or_extra_raw_calls_stop_before_real_run(self):
        for count in (0, 1, 3):
            with self.subTest(count=count), self.assertRaisesRegex(ValueError, 'diagnostic_probe_calls_missing'):
                self.run_probe(calls=count)


class GateTests(unittest.TestCase):
    def setUp(self):
        self.audit = Mock()
        self.integration = Mock()
        self.stream = self.audit.open_mcp_audit.return_value
        self.session = self.integration.StagingSession.return_value
        self.reports = run.GatedReports(TOKEN, self.audit, self.integration)
        self.addCleanup(self.reports.close)

    def test_construction_has_no_audit_or_transport_side_effect(self):
        self.audit.open_mcp_audit.assert_not_called()
        self.integration.AuditLog.assert_not_called()
        self.integration.StagingSession.assert_not_called()

    def test_closed_probe_token_cannot_construct_transport(self):
        reports = run.GatedReports(run.CLOSED_TOKEN, self.audit, self.integration)
        with self.assertRaisesRegex(ValueError, 'report_gate_closed'):
            reports.call('prepare', {})
        self.audit.open_mcp_audit.assert_not_called()
        self.integration.StagingSession.assert_not_called()

    def test_gate_failure_precedes_session_and_permanently_fails(self):
        self.audit.open_mcp_audit.side_effect = OSError('private synthetic failure')
        with self.assertRaisesRegex(ValueError, 'report_gate_closed'):
            self.reports.call('prepare', {})
        with self.assertRaisesRegex(ValueError, 'report_session_failed'):
            self.reports.call('prepare', {})
        self.audit.open_mcp_audit.assert_called_once_with(TOKEN)
        self.integration.AuditLog.assert_not_called()
        self.integration.StagingSession.assert_not_called()

    def test_invalid_audit_prevents_session_and_closes_open_stream(self):
        self.integration.AuditLog.side_effect = ValueError('synthetic invalid audit')
        with self.assertRaisesRegex(ValueError, 'report_gate_closed'):
            self.reports.call('prepare', {})
        self.integration.StagingSession.assert_not_called()
        self.reports.close()
        self.stream.close.assert_called_once()

    def test_one_session_is_reused_for_cached_prepare_and_publish(self):
        events = []
        self.audit.open_mcp_audit.side_effect = lambda token: events.append('gate') or self.stream
        self.integration.AuditLog.side_effect = lambda sink: events.append('audit') or Mock()
        self.integration.StagingSession.side_effect = lambda audit: events.append('session') or self.session
        self.reports.call('prepare', {})
        self.reports.call('prepare', {})
        self.assertEqual(events, ['gate', 'audit', 'session'])
        self.session.call.assert_has_calls([call('prepare', {}), call('prepare', {})])
        self.audit.write_result.assert_not_called()

    def test_validated_publish_is_persisted_once_after_session_returns(self):
        value = private_result()
        self.session.reports._prepared = value['analysis']
        self.session.call.return_value = value['acknowledgement']
        self.assertEqual(self.reports.call('publish', value['submission']), value['acknowledgement'])
        with self.assertRaisesRegex(ValueError, 'publication_attempts_exhausted'):
            self.reports.call('publish', copy.deepcopy(value['submission']))
        self.audit.write_result.assert_called_once_with(TOKEN, value)
        self.session.call.assert_called_once()

    def test_failed_transport_ack_is_not_persisted_and_can_retry_same_session(self):
        self.session.call.side_effect = [ValueError('synthetic lost acknowledgement'), {'synthetic': True}]
        with self.assertRaises(ValueError):
            self.reports.call('publish', {'synthetic': True})
        self.audit.write_result.assert_not_called()
        self.reports.call('publish', {'synthetic': True})
        self.integration.StagingSession.assert_called_once()
        self.audit.write_result.assert_called_once()

    def test_failed_persistence_poison_prevents_second_upstream_call(self):
        self.audit.write_result.side_effect = OSError('synthetic private persistence failure')
        with self.assertRaisesRegex(ValueError, 'private_result_persistence_failed'):
            self.reports.call('publish', {'synthetic': True})
        with self.assertRaisesRegex(ValueError, 'report_session_failed'):
            self.reports.call('publish', {'synthetic': True})
        self.session.call.assert_called_once()
        self.integration.StagingSession.assert_called_once()

    def test_only_one_ambiguous_publish_retry_is_permitted(self):
        self.session.call.side_effect = ValueError('synthetic lost acknowledgement')
        for _ in range(2):
            with self.assertRaisesRegex(ValueError, 'synthetic lost acknowledgement'):
                self.reports.call('publish', {'synthetic': True})
        with self.assertRaisesRegex(ValueError, 'publication_attempts_exhausted'):
            self.reports.call('publish', {'synthetic': True})
        self.assertEqual(self.session.call.call_count, 2)
        self.audit.write_result.assert_not_called()

    def test_unknown_operation_is_refused_before_any_audit_or_session(self):
        for name in ('exec', 'read', 'write', None):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'unknown_report_operation'):
                self.reports.call(name, {})
        self.audit.open_mcp_audit.assert_not_called()
        self.integration.StagingSession.assert_not_called()

    def test_real_adapter_caches_prepare_and_permits_identical_retry_only(self):
        value = private_result()
        upstream = Mock(side_effect=[adapter.encode(value['analysis']),
                                    OSError('synthetic lost response'),
                                    adapter.encode(value['acknowledgement'])])
        real_reports = adapter.StagingReports(upstream)
        self.integration.StagingSession.return_value = SimpleNamespace(
            reports=real_reports, call=real_reports.call, close=Mock())
        prepared = self.reports.call('prepare', {})
        prepared['result']['analysis']['coverage_pct'] = 999
        self.assertEqual(self.reports.call('prepare', {}), value['analysis'])
        with self.assertRaises(OSError):
            self.reports.call('publish', value['submission'])
        self.reports.call('publish', copy.deepcopy(value['submission']))
        self.assertEqual(upstream.call_args_list, [call('prepare', b''),
            call('publish', adapter.encode(value['submission'])),
            call('publish', adapter.encode(value['submission']))])
        self.audit.write_result.assert_called_once_with(TOKEN, value)

    def test_real_adapter_refuses_changed_payload_after_lost_ack(self):
        value = private_result()
        upstream = Mock(side_effect=[adapter.encode(value['analysis']), OSError('synthetic lost response')])
        real_reports = adapter.StagingReports(upstream)
        self.integration.StagingSession.return_value = SimpleNamespace(
            reports=real_reports, call=real_reports.call, close=Mock())
        self.reports.call('prepare', {})
        with self.assertRaises(OSError):
            self.reports.call('publish', value['submission'])
        changed = copy.deepcopy(value['submission'])
        changed['summary']['en'] = 'Changed submission.'
        with self.assertRaises(ValueError):
            self.reports.call('publish', changed)
        self.assertEqual(upstream.call_count, 2)
        self.audit.write_result.assert_not_called()

    def test_audit_events_do_not_construct_session_before_gate(self):
        self.reports.record({'event': 'synthetic'})
        self.integration.AuditLog.assert_not_called()
        self.reports.call('prepare', {})
        self.reports.record({'event': 'synthetic'})
        self.integration.AuditLog.return_value.record.assert_called_once_with({'event': 'synthetic'})

    def test_close_always_closes_stream_even_if_session_close_fails(self):
        self.reports.call('prepare', {})
        self.session.close.side_effect = ValueError('synthetic close failure')
        with self.assertRaises(ValueError):
            self.reports.close()
        self.stream.close.assert_called_once()
        self.session.close.side_effect = None


class ResultTests(unittest.TestCase):
    def test_valid_private_result_and_identical_retry_ack(self):
        value = private_result()
        for status in ('staged', 'already_staged'):
            value['acknowledgement']['result']['status'] = status
            self.assertEqual(run.validate_result(adapter, value), {
                'analysis_id': value['submission']['analysis_id'], 'status': status,
                'translations_present': True})

    def test_result_shape_and_extra_fields_fail_closed(self):
        for value in (None, [], {}, {**private_result(), 'path': 'ignored'}):
            with self.subTest(value_type=type(value).__name__), self.assertRaises(ValueError):
                run.validate_result(adapter, value)

    def test_public_scope_nonsynthetic_flags_and_wrong_ids_are_rejected(self):
        cases = [('analysis', 'scope', 'public'), ('analysis', 'synthetic', False),
                 ('acknowledgement', 'scope', 'public'), ('acknowledgement', 'synthetic', 1)]
        for container, field, change in cases:
            value = private_result()
            value[container][field] = change
            with self.subTest(container=container, field=field), self.assertRaises(ValueError):
                run.validate_result(adapter, value)
        for container, field in [('submission', 'analysis_id'), ('acknowledgement', 'id')]:
            value = private_result()
            target = value[container] if container == 'submission' else value[container]['result']
            target[field] = '0' * 64
            with self.subTest(container=container), self.assertRaises(ValueError):
                run.validate_result(adapter, value)

    def test_missing_translations_unknown_notes_or_changed_analysis_rejected(self):
        for mutation in (
            lambda value: value['submission']['summary'].pop('el'),
            lambda value: value['submission']['notes'].append({'finding_id': 'invented',
                'commentary': dict(value['submission']['summary'])}),
            lambda value: value['analysis']['result']['analysis'].update(coverage_pct=100),
            lambda value: value['acknowledgement']['result'].update(status='published')):
            value = private_result()
            mutation(value)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                run.validate_result(adapter, value)


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.audit, self.integration, self.session = Mock(), Mock(), Mock()
        self.reservation = self.audit.reserve_run.return_value
        self.reservation.token = TOKEN
        self.session.turn_sent = False
        self.session.evidence.total_tokens = 1234
        self.session.evidence.diagnostic.return_value = {
            'model_started': True, 'model_completed': True, 'mcp_items': 2, 'tool_calls': 2,
            'total_tokens': 1234, 'final_message': 'Synthetic final outcome.'}
        self.session.request.return_value = {'config': changed_config()}
        self.audit.read_result.return_value = private_result()
        self.audit.read_audit_summary.return_value = {'prepare_accepted': 1, 'publish_accepted': 1,
                                                     'transport_started': 2, 'transport_returned': 2}
        self.factory = self.enterContext(patch.object(run, 'session_class'))
        self.factory.return_value.return_value = self.session
        self.enterContext(patch.dict(run.os.environ, {run.WORKER_ENV: 'one-approved-private-test-v1'}))

    def work(self):
        return run.worker(launcher, adapter, self.integration, self.audit, config(), 'Synthetic reporting guidance.')

    def test_unowned_worker_cannot_reserve_or_start_runtime(self):
        run.os.environ.pop(run.WORKER_ENV)
        with self.assertRaisesRegex(ValueError, 'owned_worker_required'):
            self.work()
        self.integration.check_integration.assert_not_called()
        self.audit.reserve_run.assert_not_called()
        self.factory.assert_not_called()

    def test_local_integration_failure_never_reserves(self):
        self.integration.check_integration.side_effect = ValueError('synthetic integration failure')
        with self.assertRaises(ValueError):
            self.work()
        self.audit.reserve_run.assert_not_called()
        self.factory.assert_not_called()

    def test_success_closes_runtime_before_result_read_and_never_retries(self):
        order = []
        self.session.gates.side_effect = lambda *args: order.append('runtime_gates')
        self.audit.arm_run.side_effect = lambda *args: order.append('arm')
        self.session.generate_once.side_effect = lambda: order.append('generate')
        self.audit.write_diagnostic.side_effect = lambda *args: order.append('diagnostic')
        self.session.close.side_effect = lambda: order.append('close_runtime')
        self.audit.read_audit_summary.side_effect = lambda *args: order.append('read_audit') or {
            'prepare_accepted': 1, 'publish_accepted': 1, 'transport_started': 2, 'transport_returned': 2}
        self.audit.read_result.side_effect = lambda *args: order.append('read_result') or private_result()
        result = self.work()
        self.assertEqual(order, ['runtime_gates', 'arm', 'generate', 'diagnostic',
                                 'close_runtime', 'read_audit', 'read_result'])
        self.assertTrue(result['private_test_completed'])
        self.assertFalse(result['public_publishing_enabled'])
        self.assertFalse(result['schedule_changed'])
        self.assertEqual(result['total_tokens'], 1234)
        self.assertNotIn('summary', result)
        self.assertNotIn('final_message', result)
        self.audit.write_diagnostic.assert_called_once_with(TOKEN,
            self.session.evidence.diagnostic.return_value)
        self.audit.reserve_run.assert_called_once_with()
        self.session.generate_once.assert_called_once_with()
        self.audit.disarm_run.assert_called_once_with(TOKEN)
        self.reservation.close.assert_called_once_with()

    def test_gate_failure_does_not_arm_or_generate_and_still_cleans_up(self):
        self.session.gates.side_effect = ValueError('synthetic bad runtime')
        with self.assertRaises(ValueError):
            self.work()
        self.audit.arm_run.assert_not_called()
        self.session.generate_once.assert_not_called()
        self.audit.write_diagnostic.assert_not_called()
        self.session.close.assert_called_once()
        self.audit.disarm_run.assert_called_once_with(TOKEN)
        self.reservation.close.assert_called_once()
        self.reservation.record.assert_called_once_with('verification_failed')

    def test_model_failure_is_not_automatically_retried(self):
        self.session.generate_once.side_effect = ValueError('synthetic turn failure')
        with self.assertRaises(ValueError):
            self.work()
        self.session.generate_once.assert_called_once_with()
        self.audit.reserve_run.assert_called_once_with()
        self.audit.read_result.assert_not_called()
        self.audit.write_diagnostic.assert_called_once_with(TOKEN,
            self.session.evidence.diagnostic.return_value)
        self.session.close.assert_called_once()
        self.audit.disarm_run.assert_called_once_with(TOKEN)
        self.reservation.close.assert_called_once()

    def test_generation_audit_failure_prevents_model(self):
        self.reservation.record.side_effect = lambda event: (
            (_ for _ in ()).throw(OSError('synthetic audit failure')) if event == 'model_started' else None)
        with self.assertRaises(OSError):
            self.work()
        self.session.generate_once.assert_not_called()
        self.audit.disarm_run.assert_called_once_with(TOKEN)
        self.reservation.close.assert_called_once()

    def test_disarm_failure_still_closes_reservation(self):
        self.audit.disarm_run.side_effect = OSError('synthetic cleanup failure')
        with self.assertRaises(OSError):
            self.work()
        self.reservation.close.assert_called_once()

    def test_failure_audit_exception_does_not_hide_primary_failure(self):
        self.session.gates.side_effect = ValueError('original_failure')
        self.reservation.record.side_effect = OSError('synthetic audit failure')
        with self.assertRaisesRegex(ValueError, 'original_failure'):
            self.work()
        self.audit.disarm_run.assert_called_once_with(TOKEN)
        self.reservation.close.assert_called_once()

    def test_insufficient_audit_evidence_cannot_be_success(self):
        for field, value in [('prepare_accepted', 0), ('publish_accepted', 0), ('publish_accepted', 2),
                             ('transport_started', 1), ('transport_started', 4), ('transport_returned', 1)]:
            run.os.environ[run.WORKER_ENV] = 'one-approved-private-test-v1'
            evidence = {'prepare_accepted': 1, 'publish_accepted': 1,
                        'transport_started': 2, 'transport_returned': 2}
            evidence[field] = value
            self.audit.read_audit_summary.return_value = evidence
            with self.subTest(field=field, value=value), self.assertRaisesRegex(ValueError, 'private_audit_evidence_incomplete'):
                self.work()
        self.assertEqual(self.audit.disarm_run.call_count, 6)
        self.audit.read_result.assert_not_called()
        self.assertEqual(self.audit.write_diagnostic.call_count, 6)

    def test_closed_session_missing_result_is_classified_as_model_failure(self):
        self.audit.read_result.side_effect = ValueError('missing_private_result')
        with self.assertRaisesRegex(ValueError, 'missing_private_result'):
            self.work()
        self.assertEqual(self.reservation.record.call_args_list, [
            call('runtime_verified'), call('model_started'), call('model_completed'), call('model_failed')])
        self.audit.write_diagnostic.assert_called_once()
        self.session.close.assert_called_once()
        self.session.generate_once.assert_called_once()
        self.audit.reserve_run.assert_called_once()

    def test_post_generation_verification_failure_keeps_diagnostic_without_regeneration(self):
        self.session.request.return_value = {'config': {}}
        with self.assertRaises(ValueError):
            self.work()
        self.audit.write_diagnostic.assert_called_once()
        self.session.close.assert_called_once()
        self.session.generate_once.assert_called_once()
        self.audit.reserve_run.assert_called_once()
        self.audit.read_result.assert_not_called()
        self.reservation.record.assert_any_call('model_failed')

    def test_fallback_diagnostic_failure_still_cleans_up_without_retry(self):
        self.session.generate_once.side_effect = ValueError('original_generation_failure')
        self.audit.write_diagnostic.side_effect = OSError('synthetic diagnostic failure')
        with self.assertRaises((OSError, ValueError)):
            self.work()
        self.session.close.assert_called_once()
        self.audit.disarm_run.assert_called_once_with(TOKEN)
        self.reservation.close.assert_called_once()
        self.audit.reserve_run.assert_called_once()
        self.session.generate_once.assert_called_once()


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.tree = Mock()
        self.process = Mock()
        self.process.stdout = io.BytesIO(b'{"private_test_completed":true}\n')
        self.process.returncode = 0
        self.process.poll.return_value = 0
        self.owner = SimpleNamespace(OwnedRuntimeTree=Mock(return_value=self.tree),
            clean_environment=Mock(return_value={}), PYTHON='synthetic-python',
            WORKSPACE='synthetic-workspace', CREATE_SUSPENDED=4)
        self.popen = self.enterContext(patch.object(run.subprocess, 'Popen', return_value=self.process))
        self.reader = Mock()
        self.reader.ident = 123
        self.reader.is_alive.return_value = False
        self.reader_factory = self.enterContext(patch.object(run.threading, 'Thread'))
        def make_reader(**kwargs):
            self.reader.start.side_effect = kwargs['target']
            return self.reader
        self.reader_factory.side_effect = make_reader

    def test_success_owns_suspended_worker_and_bounds_wait(self):
        result = run.supervise(self.owner)
        self.assertTrue(result['private_test_completed'])
        self.tree.assign_and_resume.assert_called_once_with(self.process)
        arguments = self.popen.call_args
        self.assertEqual(arguments.args[0], ['synthetic-python', '-I', '-B', str(run.ENTRY), '--worker'])
        self.assertEqual(arguments.kwargs['env'], {run.WORKER_ENV: 'one-approved-private-test-v1'})
        self.assertEqual(arguments.kwargs['creationflags'], run.subprocess.CREATE_NO_WINDOW | 4)
        self.assertEqual(arguments.kwargs['stdin'], run.subprocess.DEVNULL)
        self.assertEqual(arguments.kwargs['stderr'], run.subprocess.DEVNULL)
        self.assertTrue(arguments.kwargs['close_fds'])
        self.process.wait.assert_any_call(timeout=run.OUTER_SECONDS)
        self.assertTrue(self.process.stdout.closed)
        self.assertGreaterEqual(self.tree.close.call_count, 1)

    def test_timeout_kills_owned_tree_and_parent_without_retry(self):
        self.process.poll.return_value = None
        self.process.wait.side_effect = [run.subprocess.TimeoutExpired('synthetic', 360), 1]
        with self.assertRaises(run.subprocess.TimeoutExpired):
            run.supervise(self.owner)
        self.tree.close.assert_called()
        self.process.kill.assert_called_once()
        self.popen.assert_called_once()
        self.assertTrue(self.process.stdout.closed)

    def test_oversized_or_invalid_output_cannot_be_success(self):
        for body in (b'x' * (run.WORKER_OUTPUT_LIMIT + 1), b'not-json', b'[]',
                     b'{"private_test_completed":false}', b''):
            self.process.stdout = io.BytesIO(body)
            with self.subTest(size=len(body)), self.assertRaises((ValueError, json.JSONDecodeError)):
                run.supervise(self.owner)
            self.assertTrue(self.process.stdout.closed)
        self.assertEqual(self.popen.call_count, 5)

    def test_spawn_failure_closes_empty_tree_without_starting_reader(self):
        self.popen.side_effect = OSError('synthetic process failure')
        with self.assertRaises(OSError):
            run.supervise(self.owner)
        self.tree.close.assert_called_once()
        self.tree.assign_and_resume.assert_not_called()
        self.reader.start.assert_not_called()

    def test_assignment_failure_kills_suspended_worker(self):
        self.tree.assign_and_resume.side_effect = ValueError('synthetic ownership failure')
        self.process.poll.return_value = None
        with self.assertRaises(ValueError):
            run.supervise(self.owner)
        self.process.kill.assert_called_once()
        self.tree.close.assert_called_once()
        self.reader.start.assert_not_called()
        self.assertTrue(self.process.stdout.closed)

    def test_reader_start_failure_skips_join_but_still_closes_pipe_and_tree(self):
        self.reader.ident = None
        self.reader_factory.side_effect = None
        self.reader_factory.return_value = self.reader
        self.reader.start.side_effect = RuntimeError('synthetic thread start failure')
        self.process.poll.return_value = None
        with self.assertRaisesRegex(RuntimeError, 'synthetic thread start failure'):
            run.supervise(self.owner)
        self.tree.close.assert_called_once()
        self.process.kill.assert_called_once()
        self.reader.join.assert_not_called()
        self.assertTrue(self.process.stdout.closed)

    def test_nonzero_worker_exit_cannot_be_accepted_from_success_json(self):
        self.process.returncode = 1
        with self.assertRaisesRegex(ValueError, 'invalid_worker_result'):
            run.supervise(self.owner)
        self.popen.assert_called_once()
        self.assertTrue(self.process.stdout.closed)

    def test_fixed_worker_failure_code_is_preserved_without_sensitive_details(self):
        self.process.returncode = 1
        self.process.stdout = io.BytesIO(b'{"error":"synthetic private detail","code":"bounded_failure"}\n')
        with self.assertRaisesRegex(ValueError, '^bounded_failure$'):
            run.supervise(self.owner)
        self.process.stdout = io.BytesIO(b'{"error":"synthetic private detail","code":"sensitive path"}\n')
        with self.assertRaisesRegex(ValueError, '^one_shot_failed$'):
            run.supervise(self.owner)


if __name__ == '__main__':
    unittest.main()
