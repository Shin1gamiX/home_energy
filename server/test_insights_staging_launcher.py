"""Offline candidate-launcher tests: no runtime, SSH, model or saved settings."""

import copy
import io
import json
from pathlib import Path
import queue
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import insights_staging_launcher as launcher
import insights_staging_mcp as adapter
import insights_windows_transport as transport


def original_config():
    return {
        'sandbox_mode': 'danger-full-access',
        'approval_policy': 'on-request',
        'default_permissions': 'old',
        'permissions': {'old': {'filesystem': {':root': 'write'}}},
        'features': {'shell_tool': False, 'unified_exec': False, 'code_mode_host': True,
                     'apps': False, 'plugins': False, 'multi_agent': False},
        'apps': {'_default': {'enabled': False}},
        'mcp_servers': {'old': {'command': 'unused', 'enabled': True}},
    }


def config():
    return launcher.build_config(original_config(), 'Synthetic pinned candidate instructions.')


def thread():
    return {
        'activePermissionProfile': {'id': launcher.PROFILE, 'extends': None},
        'sandbox': {'type': 'readOnly', 'networkAccess': False},
        'approvalPolicy': 'never', 'cwd': str(launcher.WORKSPACE),
        'runtimeWorkspaceRoots': [], 'model': launcher.MODEL, 'modelProvider': 'openai',
        'thread': {'id': 'synthetic-thread', 'ephemeral': True},
        'instructionSources': [str(launcher.GLOBAL_INSTRUCTIONS)],
    }


def inventory():
    return {'data': [
        {'name': launcher.SERVER, 'tools': {tool['name']: copy.deepcopy(tool) for tool in adapter.TOOLS}},
        {'name': 'node_repl', 'tools': {}},
    ], 'nextCursor': None}


class ConfigurationTests(unittest.TestCase):
    def test_configuration_is_deep_copy_not_saved_mutation(self):
        original = original_config()
        before = copy.deepcopy(original)
        candidate = launcher.build_config(original, 'Synthetic pinned candidate instructions.')
        self.assertEqual(original, before)
        candidate['features']['shell_tool'] = True
        candidate['apps']['_default']['enabled'] = True
        self.assertEqual(original, before)

    def test_candidate_removes_inherited_notify_command_without_mutating_source(self):
        original = original_config()
        original['notify'] = ['synthetic-notifier.exe', '--private-argument']
        before = copy.deepcopy(original)
        candidate = launcher.build_config(original, 'Synthetic pinned candidate instructions.')
        self.assertEqual(candidate['notify'], [])
        self.assertIs(type(candidate['notify']), list)
        self.assertEqual(original, before)
        candidate['notify'].append('only-mutates-candidate')
        self.assertEqual(original, before)

    def test_candidate_sets_empty_notify_even_when_source_has_no_notify(self):
        original = original_config()
        self.assertNotIn('notify', original)
        candidate = launcher.build_config(original, 'Synthetic pinned candidate instructions.')
        self.assertEqual(candidate['notify'], [])
        self.assertIs(type(candidate['notify']), list)
        self.assertNotIn('notify', original)

    def test_effective_notify_requires_exact_empty_list(self):
        class ListSubclass(list):
            pass

        expected = config()
        missing = object()
        for value in (missing, None, '', 'notifier.exe', False, True, 0, {}, (),
                      ['notifier.exe'], ListSubclass()):
            actual = copy.deepcopy(expected)
            if value is missing:
                actual.pop('notify', None)
            else:
                actual['notify'] = value
            with self.subTest(value_type=type(value).__name__), self.assertRaisesRegex(
                    launcher.GuardFailure, '^runtime_notify_mismatch$'):
                launcher.verify_effective(actual, expected)
        launcher.verify_effective(copy.deepcopy(expected), expected)

    def test_candidate_selects_explicit_root_deny_profile(self):
        candidate = config()
        self.assertNotIn('sandbox_mode', candidate)
        self.assertEqual(candidate['default_permissions'], launcher.PROFILE)
        self.assertEqual(set(candidate['permissions']), {launcher.PROFILE})
        profile = candidate['permissions'][launcher.PROFILE]
        self.assertFalse(profile['network']['enabled'])
        self.assertEqual(profile['filesystem'][':root'], 'deny')
        self.assertEqual(profile['filesystem'][':minimal'], 'read')
        self.assertEqual(profile['filesystem'][str(launcher.WORKSPACE)], 'read')
        for path in (launcher.SOURCE, launcher.DUMMY, launcher.MATERIAL,
                     launcher.USER_CONFIG.parent, launcher.WORKSPACE / 'denied.txt'):
            self.assertEqual(profile['filesystem'][str(path)], 'deny')
        self.assertEqual(candidate['agents'], {'enabled': False})
        self.assertEqual(candidate['approval_policy'], 'never')
        self.assertEqual(candidate['windows'], {'sandbox': 'mxc'})
        self.assertTrue(all(candidate['features'][name] is False for name in launcher.EXTRA_DISABLED))

    def test_explicit_instructions_are_mandatory_bounded_and_effectively_verified(self):
        for value in (None, '', 'x' * 8193, 'α' * 4097):
            with self.subTest(kind=type(value).__name__), self.assertRaises(launcher.GuardFailure):
                launcher.build_config(original_config(), value)
        expected = config()
        self.assertEqual(expected['developer_instructions'], 'Synthetic pinned candidate instructions.')
        for value in (None, 'Different instructions'):
            actual = copy.deepcopy(expected)
            actual['developer_instructions'] = value
            with self.assertRaisesRegex(launcher.GuardFailure, 'runtime_instructions_mismatch'):
                launcher.verify_effective(actual, expected)

    def test_only_candidate_mcp_and_inert_repl_are_configured(self):
        servers = config()['mcp_servers']
        self.assertEqual(set(servers), {launcher.SERVER, 'node_repl'})
        enabled = servers[launcher.SERVER]
        self.assertEqual(enabled['args'], ['-I', '-B', str(launcher.ENTRY), '--mcp-preflight'])
        self.assertEqual(enabled['command'], str(launcher.PYTHON))
        self.assertEqual(enabled['enabled_tools'], ['prepare', 'publish'])
        self.assertEqual(enabled['env'], {})
        self.assertEqual(enabled['env_vars'], [])
        self.assertTrue(enabled['enabled'])
        self.assertTrue(enabled['required'])
        self.assertFalse(servers['node_repl']['enabled'])
        self.assertFalse(servers['node_repl']['required'])

    def test_effective_exact_candidate_and_parser_nulls_are_accepted(self):
        expected = config()
        actual = copy.deepcopy(expected)
        actual['permissions'][launcher.PROFILE]['extends'] = None
        actual['mcp_servers'][launcher.SERVER]['env_vars'] = None
        launcher.verify_effective(actual, expected)

    def test_effective_permission_drift_is_rejected(self):
        expected = config()
        cases = [
            ('approval_policy', 'on-request'), ('default_permissions', 'other'),
            ('sandbox_mode', 'danger-full-access'), ('agents', {'enabled': True}),
            ('agents', {}), ('agents', None), ('windows', {'sandbox': 'legacy'}),
            ('web_search', 'live'), ('model', 'other'), ('model_reasoning_effort', 'high'),
        ]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                actual = copy.deepcopy(expected)
                actual[key] = value
                with self.assertRaises(launcher.GuardFailure):
                    launcher.verify_effective(actual, expected)
        for value in ('read', 'write'):
            actual = copy.deepcopy(expected)
            actual['permissions'][launcher.PROFILE]['filesystem']['C:\\extra'] = value
            with self.assertRaisesRegex(launcher.GuardFailure, 'runtime_profile_mismatch'):
                launcher.verify_effective(actual, expected)

    def test_effective_features_apps_and_extra_server_are_rejected(self):
        expected = config()
        for section, key, value in (
            ('features', 'shell_tool', True), ('features', 'unified_exec', True),
            ('features', 'code_mode_host', False), ('mcp_servers', 'unexpected', {}),
            ('apps', '_default', {'enabled': True}),
        ):
            with self.subTest(section=section, key=key):
                actual = copy.deepcopy(expected)
                actual[section][key] = value
                with self.assertRaises(launcher.GuardFailure):
                    launcher.verify_effective(actual, expected)

    def test_effective_mcp_transport_and_environment_cannot_drift(self):
        expected = config()
        changes = {
            'command': 'other.exe', 'args': ['--run'], 'cwd': 'C:\\elsewhere',
            'env': {'SECRET': 'unused'}, 'env_vars': ['SECRET'], 'enabled': False,
            'required': False, 'enabled_tools': ['prepare', 'publish', 'shell'],
            'url': 'https://invalid.example', 'bearer_token_env_var': 'SECRET',
            'http_headers': {'Authorization': 'unused'}, 'env_http_headers': {'X': 'SECRET'},
            'http_headers_helper': 'other.exe', 'experimental_environment': 'other',
        }
        for key, value in changes.items():
            with self.subTest(key=key):
                actual = copy.deepcopy(expected)
                actual['mcp_servers'][launcher.SERVER][key] = value
                with self.assertRaises(launcher.GuardFailure):
                    launcher.verify_effective(actual, expected)

    def test_clean_environment_removes_tokens_and_overrides(self):
        result = launcher.clean_environment({
            'SystemRoot': 'C:\\Windows', 'PROGRAMDATA': 'C:\\ProgramData',
            'OPENAI_API_KEY': 'SECRET', 'SSH_AUTH_SOCK': 'SECRET', 'NODE_OPTIONS': 'SECRET',
            'PYTHONPATH': 'SECRET', 'HTTP_PROXY': 'SECRET', 'CODEX_HOME': 'SECRET', 'PATH': 'SECRET',
        })
        self.assertNotIn('SECRET', json.dumps(result))
        self.assertEqual(result['CODEX_HOME'], str(launcher.USER_CONFIG.parent))
        self.assertEqual(result['PYTHONDONTWRITEBYTECODE'], '1')


class RuntimeGuardsTests(unittest.TestCase):
    def test_thread_requires_restricted_ephemeral_correct_context(self):
        launcher.verify_thread(thread())
        for key, value in (
            ('activePermissionProfile', None),
            ('activePermissionProfile', {'id': launcher.PROFILE, 'extends': 'full'}),
            ('sandbox', {'type': 'dangerFullAccess'}),
            ('sandbox', {'type': 'readOnly', 'networkAccess': True}),
            ('approvalPolicy', 'on-request'), ('cwd', str(launcher.DUMMY)),
            ('runtimeWorkspaceRoots', ['C:\\']), ('model', 'other'),
            ('modelProvider', 'other'), ('thread', {'id': 'synthetic', 'ephemeral': False}),
            ('instructionSources', [str(launcher.DUMMY / 'AGENTS.md')]),
            ('instructionSources', []), ('instructionSources', None),
            ('instructionSources', [str(launcher.GLOBAL_INSTRUCTIONS), str(launcher.WORKSPACE / 'AGENTS.md')]),
            ('instructionSources', [{'path': str(launcher.GLOBAL_INSTRUCTIONS)}]),
        ):
            with self.subTest(key=key, value=value):
                with self.assertRaises(launcher.GuardFailure):
                    launcher.verify_thread({**thread(), key: value})

    def test_inventory_requires_complete_exact_server_tools(self):
        self.assertEqual(launcher.verify_inventory(inventory(), adapter.TOOLS),
                         {launcher.SERVER: ['prepare', 'publish'], 'node_repl': []})
        modified = inventory()
        modified['nextCursor'] = 'more'
        with self.assertRaisesRegex(launcher.GuardFailure, 'runtime_inventory_incomplete'):
            launcher.verify_inventory(modified, adapter.TOOLS)
        for rows in ([], inventory()['data'][:1], [*inventory()['data'], {'name': 'other', 'tools': {}}]):
            with self.assertRaises(launcher.GuardFailure):
                launcher.verify_inventory({'data': rows}, adapter.TOOLS)
        modified = inventory()
        modified['data'][1]['tools'] = {'exec': {}}
        with self.assertRaisesRegex(launcher.GuardFailure, 'runtime_repl_exposed'):
            launcher.verify_inventory(modified, adapter.TOOLS)

    def test_matching_tool_names_do_not_hide_changed_schemas(self):
        for tool in ('prepare', 'publish'):
            modified = inventory()
            modified['data'][0]['tools'][tool]['inputSchema']['additionalProperties'] = True
            with self.assertRaisesRegex(launcher.GuardFailure, 'runtime_tool_schema_mismatch'):
                launcher.verify_inventory(modified, adapter.TOOLS)
        modified = inventory()
        modified['data'][0]['tools']['prepare']['name'] = 'shell'
        with self.assertRaisesRegex(launcher.GuardFailure, 'runtime_tool_schema_mismatch'):
            launcher.verify_inventory(modified, adapter.TOOLS)

    def test_rpc_method_allowlist_blocks_generation_shell_and_approvals(self):
        session = launcher.PreflightSession.__new__(launcher.PreflightSession)
        session.process = SimpleNamespace(stdin=Mock())
        for method in ('turn/start', 'turn/resume', 'command/exec', 'shell', 'tools/call',
                       'item/fileChange/requestApproval', 'thread/rollback', 'config/value/write'):
            with self.subTest(method=method):
                with self.assertRaisesRegex(launcher.GuardFailure, 'runtime_rpc_not_permitted'):
                    session.request(method, {})
                with self.assertRaisesRegex(launcher.GuardFailure, 'runtime_rpc_not_permitted'):
                    session.send({'id': 1, 'method': method, 'params': {}})
        with self.assertRaises(launcher.GuardFailure):
            session.send({'id': 1, 'result': {'decision': 'approved'}})
        session.process.stdin.write.assert_not_called()

    def test_allowed_notification_is_exact(self):
        session = launcher.PreflightSession.__new__(launcher.PreflightSession)
        session.process = SimpleNamespace(stdin=Mock())
        session.process.stdin.write.side_effect = len
        session.send({'method': 'initialized', 'params': {}})
        self.assertEqual(session.process.stdin.write.call_count, 1)
        for message in ({'method': 'initialized', 'params': {'override': True}},
                        {'id': 1, 'method': 'initialized', 'params': {}}):
            with self.assertRaises(launcher.GuardFailure):
                session.send(message)

    def test_unbuffered_short_write_fails_without_retry(self):
        session = launcher.PreflightSession.__new__(launcher.PreflightSession)
        session.process = SimpleNamespace(stdin=Mock())
        session.process.stdin.write.return_value = 1
        with self.assertRaisesRegex(launcher.GuardFailure, 'runtime_short_write'):
            session.send({'method': 'initialized', 'params': {}})
        session.process.stdin.write.assert_called_once()
        session.process.stdin.flush.assert_not_called()

    def test_server_approval_request_is_never_answered(self):
        session = launcher.PreflightSession.__new__(launcher.PreflightSession)
        session.counter = 0
        session.deadline = launcher.time.monotonic() + 5
        session.inbox = queue.Queue()
        session.inbox.put({'id': 100, 'method': 'item/commandExecution/requestApproval', 'params': {}})
        with patch.object(session, 'send') as send:
            with self.assertRaisesRegex(launcher.GuardFailure, 'runtime_client_request_refused'):
                session.request('config/read', {})
            self.assertEqual(send.call_count, 1)
            self.assertEqual(send.call_args.args[0]['method'], 'config/read')

    def test_preflight_uses_only_inspection_calls_and_rechecks_config(self):
        candidate = config()
        session = launcher.PreflightSession.__new__(launcher.PreflightSession)
        responses = [{}, {'config': candidate}, thread(), inventory(), {'config': candidate}]
        with patch.object(session, 'request', side_effect=responses) as request, patch.object(session, 'send'):
            result = session.check(candidate, adapter.TOOLS)
        self.assertEqual([call.args[0] for call in request.call_args_list],
                         ['initialize', 'config/read', 'thread/start', 'mcpServerStatus/list', 'config/read'])
        self.assertFalse(result['model_started'])
        self.assertFalse(result['report_calls_enabled'])
        self.assertFalse(result['isolation_approved'])
        self.assertTrue(result['candidate_instructions_configured'])
        self.assertFalse(result['schedule_changed'])
        self.assertTrue(result['schemas_verified'])
        started = request.call_args_list[2].args[1]
        self.assertTrue(started['ephemeral'])
        self.assertNotIn('config', started)

    def test_preflight_notify_drift_stops_before_thread_or_tool_inventory(self):
        candidate = config()
        changed = copy.deepcopy(candidate)
        changed['notify'] = ['synthetic-notifier.exe']
        session = launcher.PreflightSession.__new__(launcher.PreflightSession)
        with patch.object(session, 'request', side_effect=[{}, {'config': changed}]) as request, \
                patch.object(session, 'send'):
            with self.assertRaisesRegex(launcher.GuardFailure, '^runtime_notify_mismatch$'):
                session.check(candidate, adapter.TOOLS)
        self.assertEqual([item.args[0] for item in request.call_args_list], ['initialize', 'config/read'])

    def test_preflight_notify_drift_on_second_read_is_rejected(self):
        candidate = config()
        changed = copy.deepcopy(candidate)
        changed['notify'] = ['synthetic-notifier.exe']
        session = launcher.PreflightSession.__new__(launcher.PreflightSession)
        responses = [{}, {'config': candidate}, thread(), inventory(), {'config': changed}]
        with patch.object(session, 'request', side_effect=responses), patch.object(session, 'send'):
            with self.assertRaisesRegex(launcher.GuardFailure, '^runtime_notify_mismatch$'):
                session.check(candidate, adapter.TOOLS)

    def test_late_effective_drift_fails_preflight(self):
        candidate = config()
        changed = copy.deepcopy(candidate)
        changed['agents']['enabled'] = True
        session = launcher.PreflightSession.__new__(launcher.PreflightSession)
        responses = [{}, {'config': candidate}, thread(), inventory(), {'config': changed}]
        with patch.object(session, 'request', side_effect=responses), patch.object(session, 'send'):
            with self.assertRaisesRegex(launcher.GuardFailure, 'runtime_capability_mismatch'):
                session.check(candidate, adapter.TOOLS)

    def test_version_mismatch_stops_before_session(self):
        result = SimpleNamespace(returncode=0, stdout=b'future-runtime')
        with patch.object(launcher.subprocess, 'run', return_value=result), \
                patch.object(launcher, 'PreflightSession') as session:
            with self.assertRaisesRegex(launcher.GuardFailure, 'unreviewed_runtime_version'):
                launcher.runtime_preflight(config(), adapter)
            session.assert_not_called()

    def test_failed_session_check_still_closes_runtime(self):
        version = SimpleNamespace(returncode=0, stdout=launcher.VERSION.encode())
        with patch.object(launcher.subprocess, 'run', return_value=version), \
                patch.object(launcher, 'PreflightSession') as session:
            session.return_value.check.side_effect = launcher.GuardFailure('synthetic-failure')
            with self.assertRaises(launcher.GuardFailure):
                launcher.runtime_preflight(config(), adapter)
            session.return_value.close.assert_called_once()


class ProtocolTests(unittest.TestCase):
    def run_frames(self, frames):
        source = io.BytesIO(b''.join(json.dumps(frame).encode() + b'\n' for frame in frames))
        destination = io.BytesIO()
        with patch.object(transport, 'StagingSSH', side_effect=AssertionError('SSH constructed')) as ssh, \
                patch.object(transport, 'bounded_process', side_effect=AssertionError('process started')) as process:
            launcher.serve_preflight(adapter, source, destination)
            ssh.assert_not_called()
            process.assert_not_called()
        return [json.loads(line) for line in destination.getvalue().splitlines()]

    @staticmethod
    def initialize():
        return {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                'params': {'protocolVersion': '2025-03-26', 'capabilities': {},
                           'clientInfo': {'name': 'offline-test', 'version': '1'}}}

    def test_initialize_list_and_ping_work_without_transport(self):
        responses = self.run_frames([
            self.initialize(), {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'},
            {'jsonrpc': '2.0', 'id': 3, 'method': 'ping'},
        ])
        self.assertEqual([response['id'] for response in responses], [1, 2, 3])
        self.assertEqual(responses[1]['result']['tools'], adapter.TOOLS)
        self.assertEqual(responses[2]['result'], {})

    def test_every_valid_reporting_call_is_blocked(self):
        payload = {'analysis_id': 'a' * 64,
                   'summary': {'en': 'Synthetic.', 'el': 'Δοκιμή.', 'ru': 'Тест.'}, 'notes': []}
        responses = self.run_frames([self.initialize(), *[
            {'jsonrpc': '2.0', 'id': index, 'method': 'tools/call',
             'params': {'name': name, 'arguments': arguments}}
            for index, (name, arguments) in enumerate((('prepare', {}), ('publish', payload),
                                                       ('prepare', {}), ('publish', payload)), 2)
        ]])
        for response in responses[1:]:
            self.assertEqual(response['error'], {'code': -32600, 'message': adapter.ERROR})
            self.assertNotIn('result', response)

    def test_unknown_tools_or_credentials_cannot_select_transport(self):
        responses = self.run_frames([self.initialize(), *[
            {'jsonrpc': '2.0', 'id': index, 'method': 'tools/call',
             'params': {'name': name, 'arguments': {'host': 'unused', 'key': 'SECRET'}}}
            for index, name in enumerate(('shell', 'read_file', 'prepare', 'publish'), 2)
        ]])
        self.assertTrue(all('error' in response for response in responses[1:]))
        self.assertNotIn('SECRET', json.dumps(responses))


class EntrypointAndPinsTests(unittest.TestCase):
    def test_candidate_instructions_are_pinned(self):
        self.assertEqual(launcher.FILE_PINS[launcher.WORKSPACE / 'AGENTS.md'],
                         '68a356705be884ddd8f7fdabbe6af2b214d4113789ba718065cc9b7bca63a5b6')

    def test_no_default_run_and_no_model_run_flag(self):
        for arguments in ([], ['--run'], ['--run-staging'], ['--run-dummy'], ['--mcp-staging']):
            with self.subTest(arguments=arguments), patch.object(launcher.sys, 'argv', ['candidate', *arguments]), \
                    patch.object(launcher, 'load_inputs') as load, patch('sys.stderr', io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    launcher.main()
                self.assertEqual(error.exception.code, 2)
                load.assert_not_called()

    def test_isolated_and_no_bytecode_flags_are_mandatory(self):
        for isolated, no_bytecode in ((0, True), (1, False), (0, False)):
            with self.subTest(isolated=isolated, no_bytecode=no_bytecode), \
                    patch.object(launcher.sys, 'argv', ['candidate', '--mcp-preflight']), \
                    patch.object(launcher.sys, 'flags', SimpleNamespace(isolated=isolated)), \
                    patch.object(launcher.sys, 'dont_write_bytecode', no_bytecode), \
                    patch.object(launcher, 'load_inputs') as load:
                with self.assertRaisesRegex(launcher.GuardFailure, 'isolated_interpreter_required'):
                    launcher.main()
                load.assert_not_called()

    def test_mcp_entrypoint_only_uses_blocked_adapter(self):
        source, destination = SimpleNamespace(buffer=io.BytesIO()), SimpleNamespace(buffer=io.BytesIO())
        with patch.object(launcher.sys, 'argv', ['candidate', '--mcp-preflight']), \
                patch.object(launcher.sys, 'flags', SimpleNamespace(isolated=1)), \
                patch.object(launcher.sys, 'dont_write_bytecode', True), \
                patch.object(launcher.sys, 'stdin', source), patch.object(launcher.sys, 'stdout', destination), \
                patch.object(launcher, 'load_inputs', return_value=(config(), adapter)), \
                patch.object(launcher, 'serve_preflight') as serve, \
                patch.object(launcher, 'runtime_preflight') as runtime:
            launcher.main()
            serve.assert_called_once_with(adapter, source.buffer, destination.buffer)
            runtime.assert_not_called()

    def test_static_check_never_starts_runtime_or_serves(self):
        with patch.object(launcher.sys, 'argv', ['candidate', '--check']), \
                patch.object(launcher.sys, 'flags', SimpleNamespace(isolated=1)), \
                patch.object(launcher.sys, 'dont_write_bytecode', True), \
                patch.object(launcher, 'load_inputs', return_value=(config(), adapter)), \
                patch.object(launcher, 'serve_preflight') as serve, \
                patch.object(launcher, 'runtime_preflight') as runtime, patch('builtins.print') as output:
            launcher.main()
            serve.assert_not_called()
            runtime.assert_not_called()
            result = json.loads(output.call_args.args[0])
            self.assertTrue(result['static_checks_passed'])
            self.assertFalse(result['model_started'])
            self.assertFalse(result['report_calls_enabled'])
            self.assertFalse(result['isolation_approved'])

    def test_integration_check_only_checks_local_material(self):
        integration = Mock()
        integration.check_integration.return_value = {'report_calls_enabled': False}
        with patch.object(launcher.sys, 'argv', ['candidate', '--integration-check']), \
                patch.object(launcher.sys, 'flags', SimpleNamespace(isolated=1)), \
                patch.object(launcher.sys, 'dont_write_bytecode', True), \
                patch.object(launcher, 'load_inputs', return_value=(config(), adapter)), \
                patch.object(launcher, 'load_integration', return_value=integration), \
                patch.object(launcher, 'serve_preflight') as serve, \
                patch.object(launcher, 'runtime_preflight') as runtime, patch('builtins.print') as output:
            launcher.main()
        integration.check_integration.assert_called_once_with()
        integration.serve_staging.assert_not_called()
        integration.StagingSession.assert_not_called()
        serve.assert_not_called()
        runtime.assert_not_called()
        self.assertEqual(json.loads(output.call_args.args[0]), {'report_calls_enabled': False})

    def test_existing_modes_never_load_integration(self):
        for mode in ('--check', '--preflight', '--mcp-preflight'):
            with self.subTest(mode=mode), \
                    patch.object(launcher.sys, 'argv', ['candidate', mode]), \
                    patch.object(launcher.sys, 'flags', SimpleNamespace(isolated=1)), \
                    patch.object(launcher.sys, 'dont_write_bytecode', True), \
                    patch.object(launcher.sys, 'stdin', SimpleNamespace(buffer=io.BytesIO())), \
                    patch.object(launcher.sys, 'stdout', SimpleNamespace(buffer=io.BytesIO())), \
                    patch.object(launcher, 'load_inputs', return_value=(config(), adapter)), \
                    patch.object(launcher, 'load_integration') as integration, \
                    patch.object(launcher, 'serve_preflight'), \
                    patch.object(launcher, 'runtime_preflight', return_value={}), patch('builtins.print'):
                launcher.main()
                integration.assert_not_called()

    def test_bad_integration_pin_stops_before_import(self):
        for failure in launcher.INTEGRATION_PINS:
            def pin(path, expected):
                if path.stem == failure:
                    raise launcher.GuardFailure('reviewed_input_changed')
            with self.subTest(module=failure), patch.object(launcher, 'check_pin', side_effect=pin), \
                    patch.object(launcher.importlib, 'import_module') as imported:
                with self.assertRaisesRegex(launcher.GuardFailure, 'reviewed_input_changed'):
                    launcher.load_integration()
                imported.assert_not_called()

    def test_preloaded_integration_from_other_path_is_rejected(self):
        fake = SimpleNamespace(__file__=str(launcher.REPO / 'unexpected.py'))
        with patch.dict(launcher.sys.modules, {'insights_staging_integration': fake}), \
                patch.object(launcher, 'check_pin'), patch.object(launcher.importlib, 'import_module') as imported:
            with self.assertRaisesRegex(launcher.GuardFailure, 'unexpected_import_origin'):
                launcher.load_integration()
            imported.assert_not_called()

    def test_every_source_pin_is_verified_before_import(self):
        events = []
        def pin(path, expected):
            events.append(('pin', path.stem))
        def imported(name):
            events.append(('import', name))
            return adapter
        with patch.object(launcher, 'check_pin', side_effect=pin), \
                patch.object(launcher.importlib, 'import_module', side_effect=imported), \
                patch.object(launcher.sys, 'path', list(launcher.sys.path)):
            self.assertIs(launcher.load_adapter(), adapter)
        self.assertEqual(events[:-1], [('pin', name) for name in launcher.MODULE_PINS])
        self.assertEqual(events[-1], ('import', 'insights_staging_mcp'))

    def test_bad_source_pin_stops_before_import(self):
        for failure in launcher.MODULE_PINS:
            def pin(path, expected):
                if path.stem == failure:
                    raise launcher.GuardFailure('reviewed_input_changed')
            with self.subTest(module=failure), patch.object(launcher, 'check_pin', side_effect=pin), \
                    patch.object(launcher.importlib, 'import_module') as imported:
                with self.assertRaisesRegex(launcher.GuardFailure, 'reviewed_input_changed'):
                    launcher.load_adapter()
                imported.assert_not_called()

    def test_preloaded_module_from_other_path_is_rejected(self):
        fake = SimpleNamespace(__file__=str(launcher.REPO / 'unexpected.py'))
        with patch.dict(launcher.sys.modules, {'insights_broker': fake}), \
                patch.object(launcher, 'check_pin'), patch.object(launcher.importlib, 'import_module') as imported:
            with self.assertRaisesRegex(launcher.GuardFailure, 'unexpected_import_origin'):
                launcher.load_adapter()
            imported.assert_not_called()


class OwnedTreeTests(unittest.TestCase):
    @staticmethod
    def kernel():
        kernel = Mock()
        kernel.CreateJobObjectW.return_value = 101
        kernel.SetInformationJobObject.return_value = True
        kernel.AssignProcessToJobObject.return_value = True
        kernel.CloseHandle.return_value = True
        return kernel

    def test_job_kills_tree_without_forbidding_mcp_children(self):
        import ctypes
        kernel = self.kernel()
        flags = []
        kernel.SetInformationJobObject.side_effect = lambda handle, kind, pointer, size: flags.append(
            (handle, kind, pointer._obj.basic.flags, pointer._obj.basic.active)) or True
        with patch.object(ctypes, 'WinDLL', return_value=kernel):
            tree = launcher.OwnedRuntimeTree()
            tree.close()
            tree.close()
        self.assertEqual(flags, [(101, 9, 0x2000, 0)])
        kernel.CloseHandle.assert_called_once_with(101)

    def test_job_setup_failure_closes_job_before_any_spawn(self):
        import ctypes
        kernel = self.kernel()
        kernel.SetInformationJobObject.return_value = False
        with patch.object(ctypes, 'WinDLL', return_value=kernel), \
                patch.object(launcher.subprocess, 'Popen') as process:
            with self.assertRaisesRegex(launcher.GuardFailure, 'runtime_job_setup_failed'):
                launcher.OwnedRuntimeTree()
        kernel.CloseHandle.assert_called_once_with(101)
        process.assert_not_called()

    def test_assignment_precedes_resume_and_failed_assignment_never_resumes(self):
        import ctypes
        kernel = self.kernel()
        calls = []
        kernel.AssignProcessToJobObject.side_effect = lambda *args: calls.append('assign') or True
        with patch.object(ctypes, 'WinDLL', return_value=kernel):
            tree = launcher.OwnedRuntimeTree()
        with patch.object(tree, 'resume_owned_thread', side_effect=lambda pid: calls.append(('resume', pid))) as resume:
            tree.assign_and_resume(SimpleNamespace(_handle=7, pid=123))
            self.assertEqual(calls, ['assign', ('resume', 123)])
            kernel.AssignProcessToJobObject.side_effect = None
            kernel.AssignProcessToJobObject.return_value = False
            with self.assertRaisesRegex(launcher.GuardFailure, 'runtime_job_assignment_failed'):
                tree.assign_and_resume(SimpleNamespace(_handle=8, pid=456))
            self.assertEqual(resume.call_count, 1)
        tree.close()

    def test_resume_opens_only_single_thread_owned_by_new_pid(self):
        import ctypes
        kernel = self.kernel()
        kernel.CreateToolhelp32Snapshot.return_value = 202
        kernel.OpenThread.return_value = 303
        kernel.ResumeThread.return_value = 1
        entries = iter(((51, 999), (52, 123), (53, 888)))
        def step(snapshot, pointer):
            entry = next(entries, None)
            if entry is None:
                return False
            pointer._obj.thread_id, pointer._obj.process_id = entry
            return True
        kernel.Thread32First.side_effect = step
        kernel.Thread32Next.side_effect = step
        with patch.object(ctypes, 'WinDLL', return_value=kernel), \
                patch.object(ctypes, 'get_last_error', return_value=18):
            tree = launcher.OwnedRuntimeTree()
            tree.resume_owned_thread(123)
        kernel.OpenThread.assert_called_once_with(0x0002, False, 52)
        kernel.ResumeThread.assert_called_once_with(303)
        self.assertEqual([call.args[0] for call in kernel.CloseHandle.call_args_list], [202, 303])
        tree.close()

    def test_ambiguous_owned_threads_are_never_resumed(self):
        import ctypes
        kernel = self.kernel()
        kernel.CreateToolhelp32Snapshot.return_value = 202
        entries = iter(((51, 123), (52, 123)))
        def step(snapshot, pointer):
            entry = next(entries, None)
            if entry is None:
                return False
            pointer._obj.thread_id, pointer._obj.process_id = entry
            return True
        kernel.Thread32First.side_effect = step
        kernel.Thread32Next.side_effect = step
        with patch.object(ctypes, 'WinDLL', return_value=kernel), \
                patch.object(ctypes, 'get_last_error', return_value=18):
            tree = launcher.OwnedRuntimeTree()
            with self.assertRaisesRegex(launcher.GuardFailure, 'runtime_primary_thread_unverified'):
                tree.resume_owned_thread(123)
        kernel.OpenThread.assert_not_called()
        kernel.ResumeThread.assert_not_called()
        tree.close()

    def test_resume_failure_still_closes_owned_thread_and_snapshot(self):
        import ctypes
        kernel = self.kernel()
        kernel.CreateToolhelp32Snapshot.return_value = 202
        kernel.OpenThread.return_value = 303
        kernel.ResumeThread.return_value = 0xffffffff
        def first(snapshot, pointer):
            pointer._obj.thread_id, pointer._obj.process_id = 52, 123
            return True
        kernel.Thread32First.side_effect = first
        kernel.Thread32Next.return_value = False
        with patch.object(ctypes, 'WinDLL', return_value=kernel), \
                patch.object(ctypes, 'get_last_error', return_value=18):
            tree = launcher.OwnedRuntimeTree()
            with self.assertRaisesRegex(launcher.GuardFailure, 'runtime_thread_resume_failed'):
                tree.resume_owned_thread(123)
        self.assertEqual([call.args[0] for call in kernel.CloseHandle.call_args_list], [202, 303])
        tree.close()


class ProcessCleanupTests(unittest.TestCase):
    @staticmethod
    def process():
        process = Mock()
        process.pid = 123
        process.poll.return_value = 0
        process.stdin = io.BytesIO()
        process.stdout = io.BytesIO()
        return process

    def test_constructor_suspends_and_assigns_before_reader_start(self):
        process = self.process()
        events = []
        tree, reader = Mock(), Mock()
        reader.ident = 1
        reader.is_alive.return_value = False
        tree.assign_and_resume.side_effect = lambda value: events.append(('assign_resume', value))
        reader.start.side_effect = lambda: events.append(('reader_start', None))
        with patch.object(launcher, 'OwnedRuntimeTree', return_value=tree), \
                patch.object(launcher.subprocess, 'Popen', return_value=process) as popen, \
                patch.object(launcher.threading, 'Thread', return_value=reader):
            session = launcher.PreflightSession(config())
            self.assertEqual(events, [('assign_resume', process), ('reader_start', None)])
            self.assertEqual(popen.call_args.kwargs['creationflags'],
                             launcher.subprocess.CREATE_NO_WINDOW | launcher.CREATE_SUSPENDED)
            self.assertEqual(popen.call_args.kwargs['bufsize'], 0)
            self.assertTrue(popen.call_args.kwargs['close_fds'])
            session.close()
        tree.close.assert_called_once()

    def test_constructor_failure_terminates_suspended_owned_process(self):
        process = self.process()
        process.poll.return_value = None
        tree = Mock()
        tree.assign_and_resume.side_effect = launcher.GuardFailure('assignment_failed')
        with patch.object(launcher, 'OwnedRuntimeTree', return_value=tree), \
                patch.object(launcher.subprocess, 'Popen', return_value=process), \
                patch.object(launcher.threading, 'Thread') as reader:
            with self.assertRaisesRegex(launcher.GuardFailure, 'assignment_failed'):
                launcher.PreflightSession(config())
        tree.close.assert_called_once()
        process.kill.assert_called_once()
        process.wait.assert_called_once_with(timeout=5)
        reader.assert_not_called()
        self.assertTrue(process.stdin.closed)
        self.assertTrue(process.stdout.closed)

    def test_reader_start_failure_closes_tree_and_pipes(self):
        process, tree, reader = self.process(), Mock(), Mock()
        reader.ident = None
        reader.start.side_effect = RuntimeError('synthetic failure')
        with patch.object(launcher, 'OwnedRuntimeTree', return_value=tree), \
                patch.object(launcher.subprocess, 'Popen', return_value=process), \
                patch.object(launcher.threading, 'Thread', return_value=reader):
            with self.assertRaisesRegex(RuntimeError, 'synthetic failure'):
                launcher.PreflightSession(config())
        tree.close.assert_called_once()
        reader.join.assert_not_called()
        self.assertTrue(process.stdout.closed)

    def test_spawn_failure_still_closes_empty_owned_job(self):
        tree = Mock()
        with patch.object(launcher, 'OwnedRuntimeTree', return_value=tree), \
                patch.object(launcher.subprocess, 'Popen', side_effect=OSError('synthetic failure')):
            with self.assertRaisesRegex(OSError, 'synthetic failure'):
                launcher.PreflightSession(config())
        tree.close.assert_called_once()
        tree.assign_and_resume.assert_not_called()

    def test_normal_close_always_kills_tree_even_when_parent_already_exited(self):
        session = launcher.PreflightSession.__new__(launcher.PreflightSession)
        session.process, session.tree, session.reader = self.process(), Mock(), Mock()
        session.reader.ident = 1
        session.reader.is_alive.return_value = False
        with patch.object(launcher.subprocess, 'run') as external:
            session.close()
        session.tree.close.assert_called_once()
        session.process.kill.assert_not_called()
        external.assert_not_called()
        self.assertTrue(session.process.stdout.closed)

    def test_collector_failure_closes_tree_instead_of_only_parent(self):
        for stream in (b'not-json\n', b'[]\n', b'{"partial":true}', b'x' * (launcher.MAX_FRAME + 1)):
            with self.subTest(size=len(stream)):
                session = launcher.PreflightSession.__new__(launcher.PreflightSession)
                session.process, session.tree = self.process(), Mock()
                session.process.stdout = io.BytesIO(stream)
                session.inbox = queue.Queue(maxsize=128)
                session.collect()
                session.tree.close.assert_called_once()
                session.process.kill.assert_not_called()
                self.assertEqual(session.inbox.get_nowait(), {'error': 'runtime_closed'})

    def test_collector_queue_overflow_closes_owned_tree(self):
        session = launcher.PreflightSession.__new__(launcher.PreflightSession)
        session.process, session.tree = self.process(), Mock()
        session.process.stdout = io.BytesIO(b'{"id":1}\n{"id":2}\n')
        session.inbox = queue.Queue(maxsize=1)
        session.collect()
        session.tree.close.assert_called_once()

    def test_chunk_reader_preserves_bounded_complete_json_frames(self):
        session = launcher.PreflightSession.__new__(launcher.PreflightSession)
        session.process, session.tree = self.process(), Mock()
        session.process.stdout = Mock()
        session.process.stdout.read.side_effect = [b'{"id":', b'1}\n{"id":2}\n', b'']
        session.inbox = queue.Queue(maxsize=128)
        session.collect()
        session.tree.close.assert_not_called()
        self.assertEqual([session.inbox.get_nowait() for _ in range(3)],
                         [{'id': 1}, {'id': 2}, {'error': 'runtime_closed'}])

    def test_late_collector_failure_cannot_be_a_successful_close(self):
        session = launcher.PreflightSession.__new__(launcher.PreflightSession)
        session.process, session.tree, session.reader = self.process(), Mock(), None
        session.reader_failure = 'runtime_stream_failed'
        with self.assertRaisesRegex(launcher.GuardFailure, 'runtime_stream_failed'):
            session.close()
        session.tree.close.assert_called_once()
        self.assertTrue(session.process.stdout.closed)

    def test_reader_cleanup_has_bounded_joins_and_fails_if_reader_survives(self):
        session = launcher.PreflightSession.__new__(launcher.PreflightSession)
        session.process, session.tree, session.reader = self.process(), Mock(), Mock()
        session.reader.ident = 1
        session.reader.is_alive.return_value = True
        with self.assertRaisesRegex(launcher.GuardFailure, 'runtime_reader_cleanup_failed'):
            session.close()
        self.assertEqual([call.kwargs for call in session.reader.join.call_args_list],
                         [{'timeout': 3}, {'timeout': 1}])
        self.assertTrue(session.process.stdout.closed)


if __name__ == '__main__':
    unittest.main()
