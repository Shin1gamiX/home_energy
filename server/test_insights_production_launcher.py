"""Offline tests only: no model, real SSH, saved settings or scheduler mutations."""
import ast
import copy
import hashlib
import io
import json
from pathlib import Path
import queue
import subprocess
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import insights_staging_launcher as runtime
import insights_production_launcher as launcher
import insights_production_mcp as adapter
import insights_production_connection as connection
import insights_production_os_probe as probe
from test_insights_staging_launcher import original_config
from test_insights_staging_acl import snapshot as valid_snapshot


def config():
    base = runtime.build_config(original_config(), 'OLD SYNTHETIC SCOPE')
    return launcher.build_config(runtime, base, 'NEW DISABLED PRODUCTION SCOPE')


def thread():
    return {'activePermissionProfile': {'id': launcher.PROFILE, 'extends': None},
            'cwd': str(launcher.WORKSPACE), 'sandbox': {'type': 'readOnly', 'networkAccess': False},
            'approvalPolicy': 'never', 'runtimeWorkspaceRoots': [], 'model': runtime.MODEL,
            'modelProvider': 'openai', 'thread': {'id': 'synthetic', 'ephemeral': True},
            'instructionSources': [str(runtime.GLOBAL_INSTRUCTIONS)]}


def inventory():
    return {'nextCursor': None, 'data': [
        {'name': launcher.SERVER, 'tools': {tool['name']: copy.deepcopy(tool) for tool in adapter.TOOLS}},
        {'name': 'node_repl', 'tools': {}},
    ]}


class ConfigurationTests(unittest.TestCase):
    def test_separate_workspace_and_profile_without_old_mutation(self):
        base = runtime.build_config(original_config(), 'old')
        before = copy.deepcopy(base)
        candidate = launcher.build_config(runtime, base, 'new')
        self.assertEqual(base, before)
        self.assertNotEqual(launcher.PROFILE, runtime.PROFILE)
        self.assertNotEqual(launcher.WORKSPACE, runtime.WORKSPACE)
        self.assertEqual(candidate['default_permissions'], launcher.PROFILE)
        self.assertEqual(set(candidate['permissions']), {launcher.PROFILE})
        policy = candidate['permissions'][launcher.PROFILE]
        self.assertEqual(policy['filesystem'][':root'], 'deny')
        self.assertEqual(policy['filesystem'][str(launcher.WORKSPACE)], 'read')
        for path in (launcher.MATERIAL, runtime.MATERIAL, runtime.USER_CONFIG.parent, runtime.SOURCE):
            self.assertEqual(policy['filesystem'][str(path)], 'deny')
        self.assertFalse(policy['network']['enabled'])

    def test_closed_mcp_only_no_agent_or_notifications(self):
        actual = config()
        self.assertEqual(actual['agents'], {'enabled': False})
        self.assertEqual(actual['notify'], [])
        self.assertEqual(actual['mcp_servers'][launcher.SERVER]['args'],
                         ['-I', '-B', str(launcher.ENTRY), '--mcp-preflight'])
        self.assertEqual(actual['mcp_servers'][launcher.SERVER]['env'], {})
        self.assertFalse(actual['mcp_servers']['node_repl']['enabled'])
        self.assertEqual(actual['model_providers'], {})

    def test_effective_exact_accepts_without_mutation(self):
        expected = config()
        before = copy.deepcopy(expected)
        launcher.verify_config(runtime, expected, expected)
        self.assertEqual(before, expected)

    def test_critical_runtime_relaxations_rejected(self):
        expected = config()
        mutations = (
            lambda c: c.update(default_permissions=runtime.PROFILE),
            lambda c: c.update(sandbox_mode='danger-full-access'),
            lambda c: c.update(approval_policy='on-request'),
            lambda c: c.update(notify=['SECRET-NOTIFIER']),
            lambda c: c.update(agents={'enabled': True}),
            lambda c: c.update(model_provider='custom'),
            lambda c: c.update(model_providers={'custom': {'base_url': 'SECRET'}}),
            lambda c: c['permissions'][launcher.PROFILE]['filesystem'].update({str(launcher.MATERIAL): 'read'}),
            lambda c: c['permissions'][launcher.PROFILE]['network'].update(enabled=True),
            lambda c: c['features'].update(shell_tool=True),
            lambda c: c['features'].update(multi_agent=True),
            lambda c: c['apps'].update(extra={'enabled': True}),
            lambda c: c['mcp_servers'].update(extra={'enabled': True}),
            lambda c: c['mcp_servers'][launcher.SERVER].update(env={'TOKEN': 'SECRET'}),
            lambda c: c['mcp_servers'][launcher.SERVER].update(env_vars=['TOKEN']),
            lambda c: c['mcp_servers'][launcher.SERVER].update(url='https://example.invalid'),
            lambda c: c['mcp_servers'][launcher.SERVER].update(args=['--live']),
            lambda c: c['mcp_servers']['node_repl'].update(enabled=True),
            lambda c: c.update(developer_instructions='different'),
        )
        for mutate in mutations:
            actual = copy.deepcopy(expected)
            mutate(actual)
            with self.subTest(index=mutations.index(mutate)), self.assertRaises(ValueError):
                launcher.verify_config(runtime, actual, expected)

    def test_thread_checked_without_mutation(self):
        actual = thread()
        before = copy.deepcopy(actual)
        self.assertEqual(launcher.verify_thread(runtime, actual), 'synthetic')
        self.assertEqual(before, actual)

    def test_thread_relaxations_rejected(self):
        changes = {
            'cwd': str(runtime.WORKSPACE), 'activePermissionProfile': {'id': runtime.PROFILE},
            'sandbox': {'type': 'dangerFullAccess'}, 'runtimeWorkspaceRoots': ['C:\\'],
            'approvalPolicy': 'on-request', 'modelProvider': 'unexpected',
            'thread': {'id': 'synthetic', 'ephemeral': False}, 'instructionSources': [],
        }
        for key, value in changes.items():
            with self.subTest(key=key), self.assertRaises(ValueError):
                launcher.verify_thread(runtime, thread() | {key: value})

    def test_exact_inventory(self):
        self.assertEqual(launcher.verify_inventory(inventory(), adapter.TOOLS),
                         {launcher.SERVER: ['prepare', 'publish'], 'node_repl': []})

    def test_inventory_rejects_extra_tools_servers_or_modified_schemas(self):
        cases = []
        value = inventory(); value['nextCursor'] = 'more'; cases.append(value)
        value = inventory(); value['data'].append({'name': 'extra'}); cases.append(value)
        value = inventory(); value['data'][1]['tools']['shell'] = {}; cases.append(value)
        value = inventory(); value['data'][0]['tools']['other'] = {}; cases.append(value)
        value = inventory(); value['data'][0]['tools']['prepare']['inputSchema'] = {}; cases.append(value)
        value = inventory(); value['data'][0]['tools']['prepare']['description'] = 'different'; cases.append(value)
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ValueError):
                launcher.verify_inventory(value, adapter.TOOLS)

    def test_instructions_bounds(self):
        for value in (None, '', 'x' * 8193):
            with self.subTest(value_type=type(value).__name__), self.assertRaises(ValueError):
                launcher.build_config(runtime, {}, value)


class ProtocolTests(unittest.TestCase):
    def test_discovery_and_each_call_blocked_before_connection(self):
        for name in ('prepare', 'publish'):
            frames = [
                {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-06-18'}},
                {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list', 'params': {}},
                {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
                 'params': {'name': name, 'arguments': {'private': 'DO-NOT-LOG'}}},
                {'jsonrpc': '2.0', 'id': 4, 'method': 'tools/list', 'params': {}},
            ]
            source = io.BytesIO(b''.join(json.dumps(item).encode() + b'\n' for item in frames))
            out = io.BytesIO()
            with patch.object(adapter, 'Reports', side_effect=AssertionError('Must not construct reports')), \
                    patch.object(connection, 'load_destination', side_effect=AssertionError('No credentials')):
                launcher.serve_preflight(adapter, source, out)
            replies = [json.loads(row) for row in out.getvalue().splitlines()]
            self.assertEqual(len(replies), 3)
            self.assertEqual(replies[1]['result']['tools'], adapter.TOOLS)
            self.assertEqual(replies[2]['error']['message'], adapter.ERROR)
            self.assertNotIn(b'DO-NOT-LOG', out.getvalue())

    def session(self):
        kind = launcher.session_class(runtime)
        instance = kind.__new__(kind)
        instance.thread_id = None
        instance.counter = 0
        instance.inbox = queue.Queue()
        instance.deadline = time.monotonic() + 2
        instance.process = SimpleNamespace(stdin=io.BytesIO())
        return instance

    def test_no_generation_or_other_rpc(self):
        for method in ('turn/start', 'account/read', 'thread/resume', 'thread/fork',
                       'command/exec', 'config/value/write', 'thread/loaded/list'):
            session = self.session()
            with self.subTest(method=method), self.assertRaises(ValueError):
                session.request(method, {})
            self.assertEqual(session.process.stdin.getvalue(), b'')

    def test_thread_override_forbidden_before_write(self):
        for params in ({'cwd': str(launcher.WORKSPACE)}, {'sandbox': 'danger-full-access'}, {}):
            session = self.session()
            with self.assertRaises(ValueError):
                session.send({'id': 1, 'method': 'thread/start', 'params': params})
            self.assertEqual(session.process.stdin.getvalue(), b'')

    def test_any_generation_event_or_client_request_refused(self):
        for event in [{'method': name, 'params': {}} for name in launcher.FORBIDDEN_EVENTS] + [
                {'method': 'approval/request', 'id': 25, 'params': {}}]:
            session = self.session()
            session.inbox.put(event)
            with self.assertRaises(ValueError):
                session.request('config/read', {'cwd': str(launcher.WORKSPACE)})

    def test_model_methods_absent_from_cli(self):
        tree = ast.parse(launcher.ENTRY.read_text())
        constants = {node.value for node in ast.walk(tree) if isinstance(node, ast.Constant)
                     and isinstance(node.value, str)}
        for value in ('--run', '--run-once', '--activate', '--enable', 'turn/start'):
            self.assertNotIn(value, constants)

    def test_runtime_cleanup_on_check_failure(self):
        session = Mock()
        session.check.side_effect = ValueError('synthetic_failure')
        with patch.object(launcher.subprocess, 'run', return_value=SimpleNamespace(
                returncode=0, stdout=runtime.VERSION.encode())), \
                patch.object(launcher, 'session_class', return_value=Mock(return_value=session)):
            with self.assertRaisesRegex(ValueError, 'synthetic_failure'):
                launcher.runtime_preflight(runtime, config(), adapter)
        session.close.assert_called_once()


class SourceTests(unittest.TestCase):
    def test_existing_and_new_source_pins_match(self):
        for name, pin in launcher.MODULE_PINS.items():
            body = (launcher.SOURCE / (name + '.py')).read_bytes()
            self.assertEqual(hashlib.sha256(body).hexdigest(), pin, name)
        self.assertEqual(hashlib.sha256((launcher.SOURCE / 'insights_private_review_run.py').read_bytes()).hexdigest(),
                         launcher.HELPER_PIN)

    def test_file_pins_match(self):
        for path, pin in launcher.FILE_PINS.items():
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), pin, str(path))

    def test_bad_hash_and_hardlink_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder).resolve() / 'synthetic.py'
            path.write_bytes(b'# synthetic\n')
            pin = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(launcher.read_pinned(path, pin), b'# synthetic\n')
            with self.assertRaisesRegex(ValueError, 'production_source_changed'):
                launcher.read_pinned(path, '0' * 64)
            link = path.with_name('second.py')
            link.hardlink_to(path)
            with self.assertRaisesRegex(ValueError, 'production_source_type'):
                launcher.read_pinned(path, pin)


class ConnectionTests(unittest.TestCase):
    def binding(self):
        return (json.dumps(connection.EXPECTED_BINDING, sort_keys=True, separators=(',', ':')) + '\n').encode()

    def test_fixed_production_binding(self):
        destination = connection.parse_binding(self.binding())
        self.assertEqual(destination.host, '91.98.44.219')
        self.assertEqual(destination.port, 22)

    def test_modified_bindings_always_fail(self):
        for key, value in (('scope', 'staging'), ('synthetic', True), ('host', '127.0.0.1'),
                           ('port', True), ('schema_version', True), ('unknown', 'value')):
            body = self.binding()
            parsed = json.loads(body)
            parsed[key] = value
            with self.assertRaises(ValueError):
                connection.parse_binding(json.dumps(parsed).encode())

    def test_acl_script_only_fixed_production_paths(self):
        script = connection.acl_script()
        self.assertNotIn('HomeEnergyReporterStaging', script)
        self.assertEqual(script.count(str(connection.ROOT)), 4)
        for token in ('ReadAllText', 'ReadAllBytes', 'Get-Content', 'Set-Acl', 'icacls'):
            self.assertNotIn(token, script)

    def test_acl_probe_runs_pinned_executable_with_no_inherited_environment(self):
        body = json.dumps(valid_snapshot()).encode()
        with patch.object(connection.acl, '_verify_powershell') as verify, \
                patch.object(connection.subprocess, 'run', return_value=SimpleNamespace(
                    returncode=0, stdout=body, stderr=b'')) as run:
            self.assertEqual(connection.verify_permissions(), {'acl_verified': True})
        verify.assert_called_once()
        self.assertEqual(run.call_args.kwargs['env'], connection.acl.SAFE_ENV)
        self.assertEqual(run.call_args.kwargs['stdin'], subprocess.DEVNULL)

    def test_acl_error_not_returned(self):
        with patch.object(connection.acl, '_verify_powershell'), \
                patch.object(connection.subprocess, 'run', return_value=SimpleNamespace(
                    returncode=1, stdout=b'PRIVATE', stderr=b'SECRET')):
            with self.assertRaisesRegex(ValueError, '^production_acl_probe_failed$'):
                connection.verify_permissions()

    def test_key_never_read_and_only_ssh_allows_hardlinks(self):
        bodies = {connection.BINDING: self.binding(), connection.HOST_PIN: connection.HOST_BYTES,
                  connection.PUBLIC_KEY: b'synthetic public key', connection.transport.SSH: b'synthetic ssh'}
        original_binding = copy.deepcopy(connection.EXPECTED_BINDING)
        original_binding['ssh_sha256'] = hashlib.sha256(b'synthetic ssh').hexdigest()
        destination = connection.transport.Destination('91.98.44.219', 22,
            original_binding['ssh_sha256'], hashlib.sha256(connection.HOST_BYTES).hexdigest())
        calls = []
        def read(path):
            calls.append(path)
            self.assertNotEqual(path, connection.KEY)
            return bodies[path]
        with patch.object(connection, 'verify_permissions'), \
                patch.object(connection, 'parse_binding', return_value=destination), \
                patch.object(connection, 'PUBLIC_KEY_SHA256', hashlib.sha256(bodies[connection.PUBLIC_KEY]).hexdigest()), \
                patch.object(connection.transport, 'regular_file') as guard, patch.object(Path, 'read_bytes', read):
            connection.load_destination()
        self.assertNotIn(connection.KEY, calls)
        for call in guard.call_args_list:
            self.assertEqual(call.kwargs.get('allow_system_hardlinks', False), call.args[0] == connection.transport.SSH)

    def test_check_cannot_open_ssh(self):
        with patch.object(connection, 'load_destination') as load, \
                patch.object(connection.subprocess, 'Popen', side_effect=AssertionError('No SSH')):
            result = connection.check_integration()
        load.assert_called_once_with()
        self.assertFalse(result['ssh_connected'])


class CanaryTests(unittest.TestCase):
    def test_probe_uses_new_canaries_and_exact_profile(self):
        argv = probe.command(runtime, config(), 49123)
        script = argv[-1]
        self.assertIn(str(launcher.WORKSPACE), script)
        self.assertNotIn(str(runtime.WORKSPACE), script)
        self.assertNotIn(str(launcher.MATERIAL), script)
        self.assertNotIn(str(connection.KEY), script)
        self.assertEqual(argv[argv.index('--permission-profile') + 1], launcher.PROFILE)
        self.assertIn('CreateNew', script)
        self.assertIn('49123', script)

    def test_probe_refuses_broader_config(self):
        for field in ('shell_tool', 'unified_exec'):
            actual = config()
            actual['features'][field] = True
            with self.assertRaises(ValueError):
                probe.command(runtime, actual, 49123)

    def test_probe_does_not_use_live_data_or_existing_evidence(self):
        workspace, root, target = probe.production_paths(runtime)
        self.assertEqual(workspace, launcher.WORKSPACE)
        self.assertEqual(target.parent, launcher.WORKSPACE)
        self.assertEqual(root.name, 'windows-report-production-root-canary.txt')


if __name__ == '__main__':
    unittest.main()
