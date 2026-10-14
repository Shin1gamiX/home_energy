"""Local staging integration candidate; no model generation or report calls.

The existing dummy task/configuration is never edited. This supervisor only
inspects a fresh ephemeral runtime with in-memory configuration. Its MCP entry
point advertises the reviewed schemas but rejects every tool call before SSH.
An actual reporting run requires a separately reviewed implementation change.
"""
import argparse
import copy
import hashlib
import importlib
import json
import os
from pathlib import Path
import queue
import stat
import subprocess
import sys
import threading
import time
import tomllib


REPO = Path(r'C:\Users\anest\Documents\New project\home-energy-repo')
SOURCE = REPO / 'server'
WORKSPACE = REPO / 'deploy/windows-report-staging-candidate'
ENTRY = SOURCE / 'insights_staging_launcher.py'
DUMMY = Path(r'C:\Users\anest\Documents\ChatGPT\Home Energy Reporter')
USER_CONFIG = Path(r'C:\Users\anest\.codex\config.toml')
GLOBAL_INSTRUCTIONS = USER_CONFIG.parent / 'AGENTS.md'
MATERIAL = Path(r'C:\ProgramData\HomeEnergyReporterStaging')
CODEX = Path(r'C:\Users\anest\AppData\Local\OpenAI\Codex\bin\2e5e00daee91c61d\codex.exe')
PYTHON = Path(r'C:\Users\anest\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe')
PROFILE = 'home-energy-report-staging-preflight'
SERVER = 'home_energy_staging'
VERSION = 'codex-cli 0.162.0-alpha.17.2'
MODEL = 'gpt-6.1-sol'
MAX_OUTPUT = 2 * 1024 * 1024
MAX_FRAME = 512 * 1024
RUNTIME_SECONDS = 45
CREATE_SUSPENDED = 0x00000004
MODULE_PINS = {
    'insights_staging_mcp': 'e0dd02eb2758e463d4f871351bb62770aa99c70f8208ddcd2d3255b0d4054939',
    'insights_windows_transport': 'c57abeb40cf7be444316677d31fd8cdf86d3317da6d8fabb61bac23a601ac4b4',
    'insights_broker': '91bd9d0be1a8df22e9e972dcc4958ac5ed63066256f8aebe65dddf22a77a73f1',
    'insights_publish': 'c7df78994cdf99a6d0be131f2365e36c37f8a9d9eb9ac83df52ae61995a4c567',
    'insights_analysis': '3c48a5254c025c8d3bfaf190c4d785bfef2ab800015de0e4fdb69f5363e0c70e',
    'insights_i18n': '957e784430904770d291bfaf2c402b513007d7ff953ae8c30a31f96ff11c31c5',
}
INTEGRATION_PINS = {
    'insights_staging_integration': 'ca09a9a3fd3a8dc0b75e0845d7a551d16c69aec459e0a3a6f6c234a407f2f52d',
    'insights_staging_acl': '56504dd9174f66913e7904ad8c8a2f78beecc62a9d330051e1a2bec814b732f7',
}
FILE_PINS = {
    REPO / 'deploy/windows-report-staging-root-canary.txt': 'a761f62a1c3c04ce3a4c7b7f28f40b3164e95bef1a07ddbf1edcb4cdcc393b9d',
    GLOBAL_INSTRUCTIONS: '3527b019d4f65d5ee1502467227a0005c034d710ff5194a2bb878bb4f6f107dc',
    WORKSPACE / 'AGENTS.md': '68a356705be884ddd8f7fdabbe6af2b214d4113789ba718065cc9b7bca63a5b6',
    DUMMY / 'scheduled_dummy.py': 'a76ff81c112810ce18ed2d6ef4c55624f1a5caae619f62c6789a1b5999c94633',
    DUMMY / '.codex/config.toml': '18fd814dcee7b1407bc4c2230d1e0e3393ace5a7224f172999039e13a5cacd21',
    USER_CONFIG: '66eb0479fc51a914aae18782cda79de5d107a57fa7ac88f20d938703f6ac3905',
    CODEX: 'd13914ced6c7af174d8d638db938284231227312104b06a25e9608c632a7daec',
    PYTHON: 'd41bad46673f64e0d02f23797f53f90ccf60f27d6eda8d86dd2d4bbeae5a858c',
}
EXTRA_DISABLED = ('js_repl', 'remote_plugin', 'remote_control', 'auth_elicitation', 'tool_suggest')
RPC_METHODS = frozenset(('initialize', 'config/read', 'thread/start', 'mcpServerStatus/list'))


class GuardFailure(ValueError):
    """Only fixed non-sensitive codes are emitted by the CLI."""


def require(condition, code):
    if not condition:
        raise GuardFailure(code)


def plain_file(path):
    require(path.is_absolute() and path.resolve(strict=True) == path, 'noncanonical_input')
    for item in (path, *path.parents):
        require(not getattr(item.lstat(), 'st_file_attributes', 0)
                & stat.FILE_ATTRIBUTE_REPARSE_POINT, 'reparse_input')
    require(stat.S_ISREG(path.stat().st_mode), 'nonregular_input')


def check_pin(path, expected):
    plain_file(path)
    require(hashlib.sha256(path.read_bytes()).hexdigest() == expected, 'reviewed_input_changed')


def load_adapter():
    # Verify the entire local import closure before importing any project module.
    for name, expected in MODULE_PINS.items():
        check_pin(SOURCE / (name + '.py'), expected)
        loaded = sys.modules.get(name)
        require(loaded is None or Path(loaded.__file__).resolve() == SOURCE / (name + '.py'),
                'unexpected_import_origin')
    sys.path.insert(0, str(SOURCE))
    adapter = importlib.import_module('insights_staging_mcp')
    for name in MODULE_PINS:
        loaded = sys.modules.get(name)
        if loaded is not None:
            require(Path(loaded.__file__).resolve() == SOURCE / (name + '.py'),
                    'unexpected_import_origin')
    return adapter


def load_inputs():
    require(os.name == 'nt' and Path(__file__).resolve() == ENTRY, 'wrong_installation')
    for path, expected in FILE_PINS.items():
        check_pin(path, expected)
    for parent in (WORKSPACE, *WORKSPACE.parents):
        candidate = parent / '.codex/config.toml'
        require(candidate == USER_CONFIG or not candidate.exists(), 'ancestor_config_present')
    require(set(p.name for p in WORKSPACE.iterdir()) == {'AGENTS.md', 'allowed.txt', 'denied.txt'},
            'unexpected_workspace_content')
    for name in ('AGENTS.md', 'allowed.txt', 'denied.txt'):
        plain_file(WORKSPACE / name)
    require((WORKSPACE / 'allowed.txt').read_text().strip() == 'HOME_ENERGY_STAGING_ALLOWED_CANARY',
            'canary_changed')
    require((WORKSPACE / 'denied.txt').read_text().strip() == 'HOME_ENERGY_STAGING_DENIED_CANARY',
            'canary_changed')
    config = tomllib.loads((DUMMY / '.codex/config.toml').read_text(encoding='utf-8'))
    adapter = load_adapter()
    instruction_path = WORKSPACE / 'AGENTS.md'
    instruction_bytes = instruction_path.read_bytes()
    require(hashlib.sha256(instruction_bytes).hexdigest() == FILE_PINS[instruction_path],
            'reviewed_instructions_changed')
    return build_config(config, instruction_bytes.decode('utf-8')), adapter


def load_integration():
    # Only the local --integration-check path imports this prepared connection.
    # Existing preflight/MCP modes never construct or select the real transport.
    for name, expected in INTEGRATION_PINS.items():
        check_pin(SOURCE / (name + '.py'), expected)
        loaded = sys.modules.get(name)
        require(loaded is None or Path(loaded.__file__).resolve() == SOURCE / (name + '.py'),
                'unexpected_import_origin')
    integration = importlib.import_module('insights_staging_integration')
    for name in INTEGRATION_PINS:
        require(Path(sys.modules[name].__file__).resolve() == SOURCE / (name + '.py'),
                'unexpected_import_origin')
    return integration


def build_config(original, instructions):
    """Derive an in-memory candidate, never alter a saved Codex configuration."""
    config = copy.deepcopy(original)
    require(isinstance(instructions, str) and 0 < len(instructions.encode('utf-8')) <= 8192,
            'invalid_candidate_instructions')
    config['developer_instructions'] = instructions
    config.pop('sandbox_mode', None)
    config['approval_policy'] = 'never'
    config['default_permissions'] = PROFILE
    config['permissions'] = {PROFILE: {
        'filesystem': {':root': 'deny', ':minimal': 'read', str(WORKSPACE): 'read',
                       str(WORKSPACE / 'denied.txt'): 'deny', str(SOURCE): 'deny',
                       str(DUMMY): 'deny', str(MATERIAL): 'deny', str(USER_CONFIG.parent): 'deny'},
        'network': {'enabled': False},
    }}
    config['windows'] = {'sandbox': 'mxc'}
    config['web_search'] = 'disabled'
    # Legacy notification commands are independent of the hooks feature flag.
    # Never inherit a host command into the report-only runtime.
    config['notify'] = []
    config['agents'] = {'enabled': False}
    config['model'] = MODEL
    config['model_reasoning_effort'] = 'medium'
    config['projects'] = {str(WORKSPACE): {'trust_level': 'trusted'}}
    for feature in EXTRA_DISABLED:
        config['features'][feature] = False
    command = {'command': str(PYTHON), 'args': ['-I', '-B', str(ENTRY), '--mcp-preflight'],
               'cwd': str(WORKSPACE), 'env': {}, 'env_vars': [], 'enabled': True,
               'required': True, 'enabled_tools': ['prepare', 'publish']}
    config['mcp_servers'] = {SERVER: command,
                             'node_repl': {**command, 'enabled': False, 'required': False}}
    return config


def without_nulls(value):
    if isinstance(value, dict):
        return {k: without_nulls(v) for k, v in value.items() if v is not None}
    return value


def verify_effective(actual, expected):
    require(type(actual.get('notify')) is list and actual['notify'] == [], 'runtime_notify_mismatch')
    require(actual.get('approval_policy') == 'never' and actual.get('default_permissions') == PROFILE
            and actual.get('sandbox_mode') is None, 'runtime_policy_mismatch')
    require(without_nulls(actual.get('permissions', {}).get(PROFILE)) == expected['permissions'][PROFILE],
            'runtime_profile_mismatch')
    require(actual.get('windows', {}).get('sandbox') == 'mxc' and actual.get('web_search') == 'disabled'
            and (actual.get('agents') or {}).get('enabled') is False, 'runtime_capability_mismatch')
    require(all(actual.get('features', {}).get(k) is v for k, v in expected['features'].items()),
            'runtime_features_mismatch')
    require(actual.get('apps', {}).get('_default', {}).get('enabled') is False, 'runtime_apps_mismatch')
    require(actual.get('model') == MODEL and actual.get('model_reasoning_effort') == 'medium',
            'runtime_model_mismatch')
    require(actual.get('developer_instructions') == expected['developer_instructions'],
            'runtime_instructions_mismatch')
    servers = actual.get('mcp_servers', {})
    require(set(servers) == set(expected['mcp_servers']), 'runtime_servers_mismatch')
    for name, wanted in expected['mcp_servers'].items():
        entry = servers[name]
        keys = set(wanted) - {'env_vars'}
        if not wanted['enabled']:
            keys.discard('env')
            # This pinned parser emits optional required=false as null for a
            # disabled server. Neither form can make the disabled server active.
            keys.discard('required')
            require(entry.get('required') in (None, False), 'runtime_server_config_mismatch')
        require(all(entry.get(k) == wanted[k] for k in keys), 'runtime_server_config_mismatch')
        require(not any(entry.get(k) for k in ('url', 'bearer_token_env_var', 'env_vars',
                    'http_headers', 'env_http_headers', 'http_headers_helper', 'experimental_environment')),
                'runtime_server_environment_mismatch')


def verify_thread(started):
    require(started.get('activePermissionProfile') == {'id': PROFILE, 'extends': None},
            'runtime_thread_profile_mismatch')
    require(started.get('sandbox') == {'type': 'readOnly', 'networkAccess': False}
            and started.get('approvalPolicy') == 'never', 'runtime_thread_policy_mismatch')
    require(started.get('cwd') == str(WORKSPACE) and started.get('runtimeWorkspaceRoots') == [],
            'runtime_thread_roots_mismatch')
    require(started.get('model') == MODEL and started.get('modelProvider') == 'openai',
            'runtime_thread_model_mismatch')
    require(started.get('thread', {}).get('ephemeral') is True, 'runtime_thread_not_ephemeral')
    # Instruction discovery still loads only the pinned global file. Candidate
    # instructions are supplied explicitly, then checked in the local probe's
    # actual outgoing developer message (not inferred from this source list).
    require(started.get('instructionSources') == [str(GLOBAL_INSTRUCTIONS)],
            'runtime_instruction_sources_mismatch')


def verify_inventory(status, tools):
    require(status.get('nextCursor') is None, 'runtime_inventory_incomplete')
    rows = status.get('data', [])
    require(len(rows) == 2 and {r.get('name') for r in rows} == {SERVER, 'node_repl'},
            'runtime_inventory_mismatch')
    indexed = {row['name']: row for row in rows}
    require(indexed['node_repl'].get('tools') == {}, 'runtime_repl_exposed')
    actual = indexed[SERVER].get('tools', {})
    require(set(actual) == {tool['name'] for tool in tools}, 'runtime_tools_mismatch')
    for tool in tools:
        entry = actual[tool['name']]
        require(entry.get('name') == tool['name'] and entry.get('inputSchema') == tool['inputSchema'],
                'runtime_tool_schema_mismatch')
    return {SERVER: ['prepare', 'publish'], 'node_repl': []}


class BlockedReports:
    def call(self, *_):
        # No real transport is constructed, regardless of environment or arguments.
        raise ValueError('Preflight report calls are disabled')


def serve_preflight(adapter, source, destination):
    adapter.serve(source, destination, BlockedReports(), lambda event: None)


def toml_value(value):
    if isinstance(value, dict):
        return '{' + ','.join(json.dumps(k) + '=' + toml_value(v) for k, v in value.items()) + '}'
    return json.dumps(value)


def clean_environment(source):
    allowed = {'SYSTEMROOT', 'SYSTEMDRIVE', 'WINDIR', 'COMSPEC', 'TEMP', 'TMP',
               'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'PROGRAMDATA', 'PROGRAMFILES',
               'PROGRAMFILES(X86)', 'COMMONPROGRAMFILES', 'PATHEXT', 'USERNAME',
               'USERDOMAIN', 'OS', 'PROCESSOR_ARCHITECTURE', 'NUMBER_OF_PROCESSORS'}
    result = {k: v for k, v in source.items() if k.upper() in allowed}
    result['PATH'] = str(CODEX.parent) + ';C:\\Windows\\System32;C:\\Windows'
    result['CODEX_HOME'] = str(USER_CONFIG.parent)
    result['PYTHONDONTWRITEBYTECODE'] = '1'
    return result


def runtime_command(config):
    flags = [part for key, value in config.items() for part in ('-c', key + '=' + toml_value(value))]
    return [str(CODEX), '--no-daemon', 'app-server', '--stdio', '--strict-config', *flags]


class OwnedRuntimeTree:
    """Own the suspended runtime and all its descendants, never unrelated PIDs."""

    def __init__(self):
        require(os.name == 'nt', 'windows_job_required')
        import ctypes
        from ctypes import wintypes

        class Limits(ctypes.Structure):
            _fields_ = [('process_time', ctypes.c_longlong), ('job_time', ctypes.c_longlong),
                        ('flags', wintypes.DWORD), ('min_ws', ctypes.c_size_t),
                        ('max_ws', ctypes.c_size_t), ('active', wintypes.DWORD),
                        ('affinity', ctypes.c_size_t), ('priority', wintypes.DWORD),
                        ('scheduling', wintypes.DWORD)]

        class Extended(ctypes.Structure):
            _fields_ = [('basic', Limits), ('io', ctypes.c_ulonglong * 6),
                        ('process_memory', ctypes.c_size_t), ('job_memory', ctypes.c_size_t),
                        ('peak_process', ctypes.c_size_t), ('peak_job', ctypes.c_size_t)]

        self.handle = None
        self.lock = threading.Lock()
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                       ctypes.c_void_p, wintypes.DWORD]
        self.kernel.SetInformationJobObject.restype = wintypes.BOOL
        self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.CloseHandle.restype = wintypes.BOOL
        self.handle = self.kernel.CreateJobObjectW(None, None)
        require(bool(self.handle), 'runtime_job_creation_failed')
        try:
            limits = Extended()
            # No breakaway and no one-process limit: MCP children belong to us too.
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            require(self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits),
                                                       ctypes.sizeof(limits)), 'runtime_job_setup_failed')
        except BaseException:
            self.close()
            raise

    def assign_and_resume(self, process):
        require(self.handle is not None, 'runtime_job_closed')
        require(self.kernel.AssignProcessToJobObject(self.handle, int(process._handle)),
                'runtime_job_assignment_failed')
        self.resume_owned_thread(process.pid)

    def resume_owned_thread(self, pid):
        """Popen closes the primary-thread handle; locate only our suspended PID."""
        import ctypes
        from ctypes import wintypes

        class ThreadEntry(ctypes.Structure):
            _fields_ = [('size', wintypes.DWORD), ('usage', wintypes.DWORD),
                        ('thread_id', wintypes.DWORD), ('process_id', wintypes.DWORD),
                        ('base_priority', wintypes.LONG), ('delta_priority', wintypes.LONG),
                        ('flags', wintypes.DWORD)]

        kernel = self.kernel
        kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(ThreadEntry)]
        kernel.Thread32First.restype = wintypes.BOOL
        kernel.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(ThreadEntry)]
        kernel.Thread32Next.restype = wintypes.BOOL
        kernel.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenThread.restype = wintypes.HANDLE
        kernel.ResumeThread.argtypes = [wintypes.HANDLE]
        kernel.ResumeThread.restype = wintypes.DWORD
        snapshot = kernel.CreateToolhelp32Snapshot(0x00000004, 0)  # TH32CS_SNAPTHREAD
        require(snapshot not in (None, 0, ctypes.c_void_p(-1).value), 'runtime_thread_snapshot_failed')
        owned = []
        try:
            entry = ThreadEntry()
            entry.size = ctypes.sizeof(entry)
            present = kernel.Thread32First(snapshot, ctypes.byref(entry))
            while present:
                if entry.process_id == pid:
                    owned.append(entry.thread_id)
                entry.size = ctypes.sizeof(entry)
                present = kernel.Thread32Next(snapshot, ctypes.byref(entry))
            require(ctypes.get_last_error() == 18, 'runtime_thread_enumeration_failed')  # ERROR_NO_MORE_FILES
        finally:
            kernel.CloseHandle(snapshot)
        require(len(owned) == 1, 'runtime_primary_thread_unverified')
        handle = kernel.OpenThread(0x0002, False, owned[0])  # THREAD_SUSPEND_RESUME
        require(bool(handle), 'runtime_thread_open_failed')
        try:
            require(kernel.ResumeThread(handle) == 1, 'runtime_thread_resume_failed')
        finally:
            kernel.CloseHandle(handle)

    def close(self):
        with self.lock:
            if self.handle is not None:
                require(self.kernel.CloseHandle(self.handle), 'runtime_job_close_failed')
                self.handle = None


class PreflightSession:
    """RPC allowlist deliberately has no generation, shell or filesystem method."""
    def __init__(self, config):
        self.counter = 0
        self.inbox = queue.Queue(maxsize=128)
        self.deadline = time.monotonic() + RUNTIME_SECONDS
        self.process = None
        self.reader = None
        self.reader_failure = None
        self.tree = OwnedRuntimeTree()
        try:
            # Suspended creation closes the spawn-before-job-assignment window.
            # Unbuffered pipes avoid BufferedReader's cross-thread close lock.
            self.process = subprocess.Popen(runtime_command(config), cwd=WORKSPACE,
                env=clean_environment(os.environ), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, bufsize=0, close_fds=True,
                creationflags=subprocess.CREATE_NO_WINDOW | CREATE_SUSPENDED)
            self.tree.assign_and_resume(self.process)
            self.reader = threading.Thread(target=self.collect, daemon=True)
            self.reader.start()
        except BaseException:
            self.close()
            raise

    def collect(self):
        total = 0
        pending = bytearray()
        try:
            while chunk := self.process.stdout.read(16384):
                total += len(chunk)
                require(total <= MAX_OUTPUT, 'runtime_output_limit')
                pending.extend(chunk)
                while (boundary := pending.find(b'\n')) >= 0:
                    require(boundary + 1 <= MAX_FRAME, 'runtime_output_limit')
                    line = bytes(pending[:boundary + 1])
                    del pending[:boundary + 1]
                    event = json.loads(line)
                    require(isinstance(event, dict), 'runtime_invalid_stream')
                    self.inbox.put_nowait(event)
                require(len(pending) < MAX_FRAME, 'runtime_output_limit')
            require(not pending, 'runtime_incomplete_frame')
        except (ValueError, OSError, queue.Full):
            # Always terminate the owned tree, including after parent exit.
            self.reader_failure = 'runtime_stream_failed'
            self.tree.close()
        finally:
            try:
                self.inbox.put_nowait({'error': 'runtime_closed'})
            except queue.Full:
                pass

    def send(self, message):
        allowed = message.get('method') in RPC_METHODS or message == {'method': 'initialized', 'params': {}}
        require(allowed, 'runtime_rpc_not_permitted')
        frame = (json.dumps(message) + '\n').encode()
        require(self.process.stdin.write(frame) == len(frame), 'runtime_short_write')
        self.process.stdin.flush()

    def request(self, method, params):
        require(method in RPC_METHODS, 'runtime_rpc_not_permitted')
        self.counter += 1
        identifier = self.counter
        self.send({'id': identifier, 'method': method, 'params': params})
        while True:
            remaining = self.deadline - time.monotonic()
            require(remaining > 0, 'runtime_timeout')
            try:
                event = self.inbox.get(timeout=remaining)
            except queue.Empty:
                raise GuardFailure('runtime_timeout') from None
            require(not ('method' in event and 'id' in event), 'runtime_client_request_refused')
            require('error' not in event, 'runtime_rpc_failed')
            if event.get('id') == identifier:
                return event['result']

    def check(self, config, tools):
        self.request('initialize', {'clientInfo': {'name': 'home_energy_staging_preflight', 'version': '0.1.0'},
                                  'capabilities': {'experimentalApi': True}})
        self.send({'method': 'initialized', 'params': {}})
        verify_effective(self.request('config/read', {'cwd': str(WORKSPACE)})['config'], config)
        started = self.request('thread/start', {'cwd': str(WORKSPACE), 'ephemeral': True,
            'permissions': PROFILE, 'approvalPolicy': 'never', 'model': MODEL,
            'environments': [], 'runtimeWorkspaceRoots': []})
        verify_thread(started)
        inventory = verify_inventory(self.request('mcpServerStatus/list', {
            'threadId': started['thread']['id'], 'detail': 'toolsAndAuthOnly'}), tools)
        # There are no per-thread config overrides; recheck the same complete config.
        verify_effective(self.request('config/read', {'cwd': str(WORKSPACE)})['config'], config)
        return {'runtime_preflight_passed': True, 'profile': PROFILE, 'mcp_tools': inventory,
                'schemas_verified': True, 'collaboration_enabled': False,
                'model_network': False, 'model_started': False, 'report_calls_enabled': False,
                'candidate_instructions_configured': True,
                'isolation_approved': False, 'schedule_changed': False}

    def close(self):
        failure = None
        try:
            self.tree.close()
        except (GuardFailure, OSError):
            failure = 'runtime_tree_cleanup_failed'
        finally:
            if self.process is not None:
                try:
                    # Also handles assignment failure while still suspended.
                    if self.process.poll() is None:
                        self.process.kill()
                    self.process.wait(timeout=5)
                except (OSError, subprocess.TimeoutExpired):
                    failure = 'runtime_parent_cleanup_failed'
                if self.reader is not None and self.reader.ident is not None:
                    self.reader.join(timeout=3)
                for pipe in (self.process.stdin, self.process.stdout):
                    if pipe is not None:
                        try:
                            pipe.close()
                        except OSError:
                            failure = 'runtime_pipe_cleanup_failed'
                if self.reader is not None and self.reader.ident is not None:
                    self.reader.join(timeout=1)
                    if self.reader.is_alive():
                        failure = 'runtime_reader_cleanup_failed'
                failure = failure or getattr(self, 'reader_failure', None)
        require(failure is None, failure)


def runtime_preflight(config, adapter):
    version = subprocess.run([str(CODEX), '--version'], capture_output=True, timeout=10,
        env=clean_environment(os.environ), creationflags=subprocess.CREATE_NO_WINDOW)
    require(version.returncode == 0 and version.stdout.decode().strip() == VERSION,
            'unreviewed_runtime_version')
    session = PreflightSession(config)
    try:
        return session.check(config, adapter.TOOLS)
    finally:
        session.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check', action='store_true', help='Static checks only; no runtime or network')
    mode.add_argument('--preflight', action='store_true', help='No-generation runtime and schema checks')
    mode.add_argument('--integration-check', action='store_true',
                      help='Local private binding/ACL checks only; no SSH or model')
    mode.add_argument('--mcp-preflight', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    require(sys.flags.isolated == 1 and sys.dont_write_bytecode, 'isolated_interpreter_required')
    config, adapter = load_inputs()
    if args.mcp_preflight:
        serve_preflight(adapter, sys.stdin.buffer, sys.stdout.buffer)
    elif args.integration_check:
        print(json.dumps(load_integration().check_integration(), sort_keys=True))
    elif args.preflight:
        print(json.dumps(runtime_preflight(config, adapter), sort_keys=True))
    else:
        print(json.dumps({'static_checks_passed': True, 'report_calls_enabled': False,
                          'model_started': False, 'isolation_approved': False}))


if __name__ == '__main__':
    try:
        main()
    except (GuardFailure, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print('{"error":"Staging candidate check failed; nothing was activated"}', file=sys.stderr)
        sys.exit(1)
