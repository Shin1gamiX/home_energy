"""Offline supervisor regressions; no subprocess, SSH, model, or private files."""
import copy
import hashlib
import io
import json
import queue
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import insights_staging_launcher as launcher
import insights_private_review_mcp as adapter
import insights_private_review_run as run
import insights_private_review_store as storage
from test_insights_private_review_mcp import NOW, TOKEN, fixture, prose
from test_insights_private_review_store import LocalFixture
from test_insights_staging_launcher import config, thread


def changed_config(token=TOKEN):
    return run.run_config(config(), 'Fixed offline guidance.', token)


def inventory():
    return {'data': [
        {'name': run.SERVER, 'tools': {tool['name']: copy.deepcopy(tool) for tool in adapter.TOOLS}},
        {'name': 'node_repl', 'tools': {}},
    ], 'nextCursor': None}


def private_result(snapshot=None):
    snapshot = fixture() if snapshot is None else snapshot
    identifier = snapshot['analysis_id']
    return {'schema_version': 1, 'scope': 'private_review', 'synthetic': False,
        'snapshot_sha256': hashlib.sha256(adapter.encode(snapshot)).hexdigest(),
        'analysis': {'scope': 'private_review', 'synthetic': False,
                     'result': {'analysis_id': identifier, 'analysis': copy.deepcopy(snapshot['analysis'])}},
        'submission': {'analysis_id': identifier, 'summary': prose(), 'notes': []},
        'acknowledgement': {'scope': 'private_review', 'synthetic': False,
                            'result': {'status': 'saved_private', 'id': identifier}}}


def session_without_process():
    cls = run.session_class(launcher)
    session = cls.__new__(cls)
    session.evidence = run.Evidence()
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


class ConfigTests(unittest.TestCase):
    def test_config_is_in_memory_copy_preserving_restrictions(self):
        original = config()
        before = copy.deepcopy(original)
        candidate = run.run_config(original, 'Fixed offline guidance.', TOKEN)
        self.assertEqual(original, before)
        for key in ('permissions', 'agents', 'features', 'windows', 'notify',
                    'approval_policy', 'default_permissions', 'model',
                    'model_reasoning_effort', 'apps', 'web_search'):
            self.assertEqual(candidate[key], before[key])
        candidate['permissions'][launcher.PROFILE]['network']['enabled'] = True
        self.assertEqual(original, before)

    def test_exact_new_namespace_and_capability_env_only_on_active_server(self):
        candidate = changed_config()
        self.assertEqual(set(candidate['mcp_servers']), {run.SERVER, 'node_repl'})
        active = candidate['mcp_servers'][run.SERVER]
        self.assertEqual(active['args'], ['-I', '-B', str(run.ENTRY), '--mcp-private-review'])
        self.assertEqual(active['env'], {run.TOKEN_ENV: TOKEN})
        self.assertEqual(active['enabled_tools'], ['prepare', 'publish'])
        self.assertEqual(candidate['mcp_servers']['node_repl'], config()['mcp_servers']['node_repl'])
        self.assertIn('NOT public publication', candidate['developer_instructions'])
        self.assertEqual(candidate['model_provider'], 'openai')
        self.assertEqual(candidate['model_providers'], {})

    def test_invalid_token_or_oversized_instructions_rejected(self):
        for token in (None, True, 'a' * 63, 'g' * 64, b'a' * 64):
            with self.subTest(token_type=type(token)), self.assertRaises(ValueError):
                run.run_config(config(), 'Fixed guidance.', token)
        with self.assertRaisesRegex(ValueError, 'instructions_too_large'):
            run.run_config(config(), 'α' * 8192, TOKEN)

    def test_verified_config_accepts_exact_and_closed_preflight(self):
        for token in (TOKEN, run.CLOSED_TOKEN):
            candidate = changed_config(token)
            run.verify_config(launcher, copy.deepcopy(candidate), candidate)

    def test_effective_config_rejects_permission_tools_and_provider_drift(self):
        expected = changed_config()
        mutations = [
            lambda value: value['permissions'][launcher.PROFILE]['network'].update(enabled=True),
            lambda value: value['agents'].update(enabled=True),
            lambda value: value['features'].update(shell_tool=True),
            lambda value: value.update(notify=['command']),
            lambda value: value['apps']['_default'].update(enabled=True),
            lambda value: value.update(model_provider='custom'),
            lambda value: value.update(model_providers={'custom': {'base_url': 'https://invalid.invalid'}}),
            lambda value: value['mcp_servers'][run.SERVER]['env'].update({run.TOKEN_ENV: 'b' * 64}),
            lambda value: value['mcp_servers'][run.SERVER].update(args=['-c', 'arbitrary']),
            lambda value: value['mcp_servers'].update(other={'enabled': True}),
        ]
        for mutate in mutations:
            actual = copy.deepcopy(expected)
            mutate(actual)
            with self.subTest(mutation=mutate), self.assertRaises(ValueError):
                run.verify_config(launcher, actual, expected)

    def test_inventory_requires_exact_namespace_tools_and_no_pagination(self):
        run.verify_inventory(inventory(), adapter.TOOLS)
        mutations = [
            lambda value: value.update(nextCursor='more'),
            lambda value: value['data'][0].update(name='home_energy_staging'),
            lambda value: value['data'][0]['tools'].update(restart={}),
            lambda value: value['data'][1].update(tools={'exec': {}}),
            lambda value: value['data'][0]['tools']['prepare'].update(description='changed'),
        ]
        for mutate in mutations:
            value = inventory()
            mutate(value)
            with self.subTest(mutation=mutate), self.assertRaises(ValueError):
                run.verify_inventory(value, adapter.TOOLS)

    def test_error_codes_cannot_reflect_paths_or_prose(self):
        self.assertEqual(run.fixed_code(ValueError('fixed_code')), 'fixed_code')
        for error in (ValueError('C:\\secret\\path'), RuntimeError('secret_code'),
                      ValueError('private text'), ValueError('a' * 81), ValueError('a', 'b')):
            self.assertEqual(run.fixed_code(error), 'private_review_failed')


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.evidence = run.Evidence()
        self.evidence.thread_id = 'synthetic-thread'
        self.evidence.turn_id = 'synthetic-turn'

    def event(self, method, **params):
        return {'method': method, 'params': {'threadId': 'synthetic-thread',
                                            'turnId': 'synthetic-turn', **params}}

    def test_child_threads_other_threads_turns_and_hooks_rejected(self):
        events = [
            {'method': 'thread/started', 'params': {'thread': {'id': 'child'}}},
            self.event('item/started', threadId='other'),
            self.event('item/started', turnId='other'),
            *[self.event(method) for method in ('hook/started', 'hook/completed',
                                                'model/rerouted', 'turn/diff/updated')],
        ]
        for value in events:
            with self.subTest(method=value['method']), self.assertRaises(ValueError):
                self.evidence.accept(value)

    def test_collaboration_shell_and_file_items_rejected(self):
        for kind in ('commandExecution', 'fileChange', 'webSearch', 'collabAgentToolCall',
                     'dynamicToolCall', 'imageGeneration', 'unknown'):
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, 'unexpected_tool_item'):
                self.evidence.accept(self.event('item/started', item={'type': kind, 'id': 'test'}))

    def test_mcp_server_and_tool_names_are_allowlisted(self):
        for server, tool in (('home_energy_staging', 'prepare'), (run.SERVER, 'restart'),
                             ('other', 'publish'), (None, None)):
            item = {'type': 'mcpToolCall', 'id': 'test', 'server': server, 'tool': tool}
            with self.subTest(server=server, tool=tool), self.assertRaisesRegex(ValueError, 'unexpected_mcp_item'):
                self.evidence.accept(self.event('item/completed', item=item))
        for tool in ('prepare', 'publish'):
            item = {'type': 'mcpToolCall', 'id': tool, 'server': run.SERVER, 'tool': tool}
            self.evidence.accept(self.event('item/completed', item=item))
        self.assertEqual(self.evidence.mcp_items, {'prepare', 'publish'})

    def test_tool_ids_counts_and_final_message_are_bounded(self):
        for identifier in (None, 1, 'x' * 257):
            with self.subTest(identifier_type=type(identifier)), self.assertRaises(ValueError):
                self.evidence.accept(self.event('rawResponseItem/completed',
                    item={'type': 'custom_tool_call', 'call_id': identifier}))
        for number in range(64):
            self.evidence.accept(self.event('rawResponseItem/completed',
                item={'type': 'function_call', 'call_id': str(number)}))
        with self.assertRaisesRegex(ValueError, 'tool_count_limit'):
            self.evidence.accept(self.event('rawResponseItem/completed',
                item={'type': 'function_call', 'call_id': 'one-too-many'}))
        with self.assertRaisesRegex(ValueError, 'final_message_limit'):
            self.evidence.accept(self.event('item/completed',
                item={'type': 'agentMessage', 'phase': 'final_answer', 'text': 'x' * 8001}))

    def test_usage_failed_completion_and_diagnostic_contract(self):
        for total in (True, -1, 1_000_000, None):
            with self.subTest(total=total), self.assertRaisesRegex(ValueError, 'invalid_usage_event'):
                self.evidence.accept(self.event('thread/tokenUsage/updated',
                    tokenUsage={'total': {'totalTokens': total}}))
        with self.assertRaisesRegex(ValueError, 'model_turn_failed'):
            self.evidence.accept(self.event('turn/completed', turn={'id': 'synthetic-turn', 'status': 'failed'}))
        value = self.evidence.diagnostic(False, 'fixed_failure')
        self.assertEqual(value['scope'], 'private_review')
        self.assertIs(value['synthetic'], False)
        self.assertEqual(value['failure_code'], 'fixed_failure')
        self.assertNotIn('synthetic-thread', json.dumps(value))
        self.assertNotIn('synthetic-turn', json.dumps(value))


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.session = session_without_process()

    def test_start_requires_armed_thread_and_consumes_exactly_once(self):
        with self.assertRaises(ValueError):
            self.session.send(start_message())
        self.session.armed = True
        self.session.send(start_message())
        before = self.session.process.stdin.getvalue()
        self.assertEqual(json.loads(before), start_message())
        with self.assertRaisesRegex(ValueError, 'generation_not_armed_or_already_consumed'):
            self.session.send(start_message(id=2))
        self.assertEqual(before, self.session.process.stdin.getvalue())

    def test_partial_or_failed_start_write_is_still_consumed(self):
        self.session.armed = True
        self.session.process.stdin = Mock()
        self.session.process.stdin.write.return_value = 0
        with self.assertRaisesRegex(ValueError, 'runtime_short_write'):
            self.session.send(start_message())
        self.assertTrue(self.session.turn_sent)
        with self.assertRaises(ValueError):
            self.session.send(start_message(id=2))
        self.session.process.stdin.write.assert_called_once()

    def test_custom_prompts_other_threads_and_api_calls_are_refused(self):
        self.session.armed = True
        for params in ({'threadId': 'other', 'input': [{'type': 'text', 'text': run.PROMPT}]},
                       {'threadId': 'synthetic-thread', 'input': [{'type': 'text', 'text': 'arbitrary'}]}):
            with self.subTest(params=params), self.assertRaises(ValueError):
                self.session.send(start_message(params=params))
        for method in ('command/exec', 'thread/resume', 'turn/interrupt', 'config/value/write'):
            with self.subTest(method=method), self.assertRaises(ValueError):
                self.session.send({'id': 1, 'method': method, 'params': {}})
        self.assertFalse(self.session.turn_sent)

    def test_auth_refresh_and_loaded_list_arbitrary_params_refused(self):
        for method, params in (('account/read', {'refreshToken': True}),
                               ('thread/loaded/list', {'cursor': 'outside'})):
            with self.subTest(method=method), self.assertRaises(ValueError):
                self.session.send({'id': 1, 'method': method, 'params': params})

    def test_receive_refuses_approval_requests_and_rpc_errors(self):
        for event in ({'id': 1, 'method': 'approve', 'params': {}},
                      {'id': 1, 'error': {'message': 'private'}}):
            self.session.inbox.put(event)
            with patch.object(run.time, 'monotonic', return_value=100), self.assertRaises(ValueError):
                self.session.receive()

    def test_gates_verify_twice_and_never_arm_or_start(self):
        expected = changed_config()

        def respond(method, params):
            return {'initialize': {}, 'config/read': {'config': expected},
                    'account/read': {'requiresOpenaiAuth': True, 'account': {'type': 'chatgpt'}},
                    'thread/start': thread(), 'mcpServerStatus/list': inventory()}[method]
        self.session.request = Mock(side_effect=respond)
        self.session.gates(expected, adapter.TOOLS)
        methods = [call.args[0] for call in self.session.request.call_args_list]
        self.assertEqual(methods.count('config/read'), 2)
        self.assertNotIn('turn/start', methods)
        self.assertFalse(self.session.armed)
        self.assertFalse(self.session.turn_sent)

    def test_gates_reject_nonchatgpt_auth_before_start(self):
        expected = changed_config()

        def respond(method, params):
            return {'initialize': {}, 'config/read': {'config': expected},
                    'account/read': {'requiresOpenaiAuth': True, 'account': {'type': 'apiKey'}}}[method]
        self.session.request = Mock(side_effect=respond)
        with self.assertRaisesRegex(ValueError, 'chatgpt_authentication_required'):
            self.session.gates(expected, adapter.TOOLS)
        self.assertNotIn('thread/start', [call.args[0] for call in self.session.request.call_args_list])


class ResultTests(unittest.TestCase):
    def test_valid_result_has_matching_snapshot_and_three_translations(self):
        snapshot = fixture()
        result = run.validate_result(adapter, private_result(snapshot), snapshot)
        self.assertEqual(result, {'analysis_id': snapshot['analysis_id'],
                                 'status': 'saved_private', 'translations_present': True})

    def test_bool_version_synthetic_public_scope_and_extra_keys_rejected(self):
        snapshot = fixture()
        for changes in ({'schema_version': True}, {'scope': 'public'},
                        {'synthetic': True}, {'extra': 'raw data'}):
            value = private_result(snapshot)
            value.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                run.validate_result(adapter, value, snapshot)

    def test_wrong_snapshot_digest_analysis_and_ids_rejected(self):
        snapshot = fixture()
        mutations = [
            lambda value: value.update(snapshot_sha256='0' * 64),
            lambda value: value['analysis'].update(scope='public'),
            lambda value: value['analysis']['result'].update(analysis_id='0' * 64),
            lambda value: value['submission'].update(analysis_id='0' * 64),
            lambda value: value['acknowledgement']['result'].update(id='0' * 64),
            lambda value: value['acknowledgement']['result'].update(status='published'),
        ]
        for mutate in mutations:
            value = private_result(snapshot)
            mutate(value)
            with self.subTest(mutation=mutate), self.assertRaises(ValueError):
                run.validate_result(adapter, value, snapshot)

    def test_missing_translation_rejected(self):
        value = private_result()
        del value['submission']['summary']['ru']
        with self.assertRaises(ValueError):
            run.validate_result(adapter, value, fixture())


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(run.subprocess, 'Popen', side_effect=AssertionError('no real subprocess')))
        self.enterContext(patch.dict(run.os.environ, {run.WORKER_ENV: 'one-approved-private-review-v1'}))
        self.enterContext(patch.object(run.time, 'time', return_value=NOW))
        self.snapshot = fixture()
        self.result = private_result(self.snapshot)
        self.events = []
        self.data = {'snapshot': self.snapshot}
        self.store = Mock()
        self.store.read.side_effect = lambda kind: copy.deepcopy(self.events if kind == 'audit' else self.data[kind])
        self.store.exists.side_effect = lambda kind: kind in self.data
        self.store.create.side_effect = lambda kind, value: self.data.update({kind: copy.deepcopy(value)})
        self.store.reserve.side_effect = lambda digest: self.events.append({'event': 'reserved'}) or TOKEN
        self.store.record.side_effect = lambda event: self.events.append({'event': event})
        self.storage = SimpleNamespace(Store=Mock(return_value=self.store))
        self.session = Mock()
        self.session.evidence = run.Evidence()
        self.session.request.return_value = {'config': changed_config()}

        def generate():
            self.assertTrue(self.session.armed)
            self.store.arm.assert_called_once_with(TOKEN)
            self.events.extend([{'event': 'prepared'}, {'event': 'saved'}])
            self.data['result'] = self.result
            self.session.evidence.completed = True
            self.session.evidence.total_tokens = 123
        self.session.generate_once.side_effect = generate
        self.factory = Mock(return_value=self.session)
        self.enterContext(patch.object(run, 'session_class', return_value=self.factory))

    def invoke(self):
        return run.worker(launcher, adapter, self.storage, config(), 'Fixed offline guidance.')

    def test_success_reserves_generates_once_persists_diagnostic_then_closes(self):
        result = self.invoke()
        self.assertTrue(result['private_review_completed'])
        self.assertFalse(result['public_publishing_enabled'])
        self.assertFalse(result['schedule_changed'])
        self.assertEqual(result['total_tokens'], 123)
        self.assertIn('diagnostic', self.data)
        self.assertTrue(self.data['diagnostic']['model_completed'])
        self.store.reserve.assert_called_once_with(hashlib.sha256(adapter.encode(self.snapshot)).hexdigest())
        self.session.generate_once.assert_called_once()
        self.session.close.assert_called_once()
        self.store.finish.assert_called_once_with(TOKEN)
        self.store.close.assert_called_once()
        self.assertNotIn(run.WORKER_ENV, run.os.environ)

    def test_missing_owner_marker_cannot_construct_store(self):
        run.os.environ.pop(run.WORKER_ENV)
        with self.assertRaisesRegex(ValueError, 'owned_worker_required'):
            self.invoke()
        self.storage.Store.assert_not_called()

    def test_stale_snapshot_stops_before_reservation_and_session(self):
        self.enterContext(patch.object(run.time, 'time', return_value=NOW + 1801))
        with self.assertRaises(ValueError):
            self.invoke()
        self.store.reserve.assert_not_called()
        self.factory.assert_not_called()
        self.store.finish.assert_not_called()
        self.store.close.assert_called_once()

    def test_failed_reservation_cannot_construct_session_or_model(self):
        self.store.reserve.side_effect = ValueError('receipt_consumed')
        with self.assertRaisesRegex(ValueError, 'receipt_consumed'):
            self.invoke()
        self.factory.assert_not_called()
        self.store.finish.assert_not_called()
        self.store.close.assert_called_once()

    def test_gate_failure_consumes_receipt_without_model_and_closes(self):
        self.session.gates.side_effect = ValueError('gate_rejected')
        with self.assertRaisesRegex(ValueError, 'gate_rejected'):
            self.invoke()
        self.session.generate_once.assert_not_called()
        self.store.arm.assert_not_called()
        self.assertFalse(self.data['diagnostic']['model_started'])
        self.assertEqual(self.data['diagnostic']['failure_code'], 'gate_rejected')
        self.session.close.assert_called_once()
        self.store.finish.assert_called_once_with(TOKEN)

    def test_model_failure_no_retry_and_diagnostic_preserved(self):
        self.session.generate_once.side_effect = ValueError('model_failed')
        with self.assertRaisesRegex(ValueError, 'model_failed'):
            self.invoke()
        self.session.generate_once.assert_called_once()
        self.assertEqual(self.data['diagnostic']['failure_code'], 'model_failed')
        self.session.close.assert_called_once()
        self.store.finish.assert_called_once_with(TOKEN)

    def test_post_close_result_rejection_still_writes_diagnostic(self):
        self.result['snapshot_sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'result_snapshot_mismatch'):
            self.invoke()
        self.assertIn('diagnostic', self.data)
        self.assertEqual(self.data['diagnostic']['failure_code'], 'result_snapshot_mismatch')
        self.session.close.assert_called_once()
        self.store.finish.assert_called_once_with(TOKEN)

    def test_post_close_audit_rejection_still_writes_diagnostic(self):
        original = self.session.generate_once.side_effect

        def generate():
            original()
            self.events.append({'event': 'failed'})
        self.session.generate_once.side_effect = generate
        with self.assertRaisesRegex(ValueError, 'private_evidence_incomplete'):
            self.invoke()
        self.assertIn('diagnostic', self.data)
        self.assertEqual(self.data['diagnostic']['failure_code'], 'private_evidence_incomplete')
        self.store.finish.assert_called_once_with(TOKEN)

    def test_session_close_failure_still_closes_receipt_and_store(self):
        self.session.close.side_effect = ValueError('close_failed')
        with self.assertRaisesRegex(ValueError, 'close_failed'):
            self.invoke()
        self.store.finish.assert_called_once_with(TOKEN)
        self.store.close.assert_called_once()

    def test_audit_failure_still_finalizes(self):
        self.store.record.side_effect = ValueError('audit_failed')
        with self.assertRaisesRegex(ValueError, 'audit_failed'):
            self.invoke()
        self.session.generate_once.assert_not_called()
        self.store.finish.assert_called_once_with(TOKEN)
        self.store.close.assert_called_once()


class StoreAdapterIntegrationTests(LocalFixture):
    def test_real_store_contract_and_adapter_save_verify_once_in_temp_only(self):
        self.clock.return_value = NOW
        value = fixture()
        self.store.create('capture', {'schema_version': 1, 'scope': 'private_review',
                                     'synthetic': False, 'created_at': NOW})
        self.store.create('snapshot', value)
        token = self.store.reserve(hashlib.sha256(adapter.encode(value)).hexdigest())
        self.store.arm(token)
        reports = adapter.Reports(storage.Store, token)
        self.addCleanup(reports.close)
        prepared = reports.call('prepare', {})
        submission = {'analysis_id': value['analysis_id'], 'summary': prose(), 'notes': []}
        acknowledgement = reports.call('publish', submission)
        duplicate = reports.call('publish', copy.deepcopy(submission))
        result = self.store.read('result')
        self.assertEqual(result['analysis'], prepared)
        self.assertEqual(result['acknowledgement'], acknowledgement)
        self.assertEqual(duplicate['result']['status'], 'already_saved_private')
        self.assertEqual(run.validate_result(adapter, result, value)['status'], 'saved_private')
        reports.close()
        self.store.finish(token)
        self.assertEqual([row['event'] for row in self.store.read('audit')],
                         ['reserved', 'prepared', 'saved', 'finished'])
        with self.assertRaises(ValueError):
            adapter.Reports(storage.Store, token).call('prepare', {})


if __name__ == '__main__':
    unittest.main()
