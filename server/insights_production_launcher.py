"""Production launcher preparation: static, discovery and synthetic OS checks.

No generation RPC, live transport, report call, scheduler mutation or activation
mode exists here. Reuse verified source bytes, never cached project bytecode.
The successful private-review and staging sources/evidence remain unchanged.
"""
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path
import queue
import re
import stat
import subprocess
import sys
import threading
import time
import types


REPO = Path(r'C:\Users\anest\Documents\New project\home-energy-repo')
SOURCE = REPO / 'server'
ENTRY = SOURCE / 'insights_production_launcher.py'
WORKSPACE = REPO / 'deploy/windows-report-production-candidate'
MATERIAL = Path(r'C:\ProgramData\HomeEnergyReporterProduction')
PROFILE = 'home-energy-report-production-preflight'
SERVER = 'home_energy_reports'
HELPER_PIN = 'af65c7429aea1e115b5a9aae0d3c6b86b966c1385ceb25925103edc0fcebe214'
MODULE_PINS = {
    'insights_staging_launcher': '83ac3d40b826d35ab58a33af2ad315089fca6e5743bb631a8ab38ed39d0555a0',
    'insights_staging_mcp': 'e0dd02eb2758e463d4f871351bb62770aa99c70f8208ddcd2d3255b0d4054939',
    'insights_windows_transport': 'c57abeb40cf7be444316677d31fd8cdf86d3317da6d8fabb61bac23a601ac4b4',
    'insights_broker': '91bd9d0be1a8df22e9e972dcc4958ac5ed63066256f8aebe65dddf22a77a73f1',
    'insights_publish': 'c7df78994cdf99a6d0be131f2365e36c37f8a9d9eb9ac83df52ae61995a4c567',
    'insights_analysis': '3c48a5254c025c8d3bfaf190c4d785bfef2ab800015de0e4fdb69f5363e0c70e',
    'insights_i18n': '957e784430904770d291bfaf2c402b513007d7ff953ae8c30a31f96ff11c31c5',
    'insights_private_review_mcp': '0a0ef1216f415c84888fc5ba50787293f4cb09294f2e8f452f03395106a091f4',
    'insights_production_mcp': 'cb42392809b42112c87ca3722c9a0fa4a4a747b55f7960af06b02e62456377d7',
    'insights_staging_acl': '56504dd9174f66913e7904ad8c8a2f78beecc62a9d330051e1a2bec814b732f7',
    'insights_staging_sandbox_probe': '004ddaae5c3bfe1a21fa3e89fff3800b0ac32ff0ae9dfd23429a6cc22b3407ab',
    'insights_production_connection': '425af214b37a661ebdc94202d8fa1e612b69412274ba284fec831365a49dd5aa',
    'insights_production_os_probe': 'a9146223041cf653194160826d3647d4de693b975e1b86cdf8f80b3f9abb7c75',
}
FILE_PINS = {
    WORKSPACE / 'AGENTS.md': '0ab05ac51eb8c10d5edec4cf8b12ebed4bbdfa3096c0dee07081876ea5019f94',
    WORKSPACE / 'allowed.txt': '1413409aa25f3639bb93fbb934bc45629fc4274658a7d27b44fc1443f542633e',
    WORKSPACE / 'denied.txt': '91dd323f68bc1ab96b818ad61aeb8929b6b369f698f667e550eab2ce976847e0',
    REPO / 'deploy/windows-report-production-root-canary.txt': 'f1526cddda08a831632c1b22c23c7177f238c2f2eb64410664f463dd1681de1e',
    REPO / 'docs/REPORTING_SKILL.md': 'ad2b76109fcf48474028a69931541af85c3ca6dd1b36f925913942e5643b200d',
}
FORBIDDEN_EVENTS = frozenset(('turn/started', 'turn/completed', 'item/started', 'item/completed',
                             'hook/started', 'hook/completed', 'model/rerouted'))


def require(condition, code):
    if not condition:
        raise ValueError(code)


def read_pinned(path, pin):
    require(path.is_absolute() and path.resolve(strict=True) == path, 'production_noncanonical_source')
    for item in (path, *path.parents):
        require(not getattr(item.lstat(), 'st_file_attributes', 0)
                & stat.FILE_ATTRIBUTE_REPARSE_POINT, 'production_reparse_source')
    info = path.stat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and 0 < info.st_size <= 512 * 1024,
            'production_source_type')
    with path.open('rb') as source:
        body = source.read(512 * 1024 + 1)
    require(hashlib.sha256(body).hexdigest() == pin, 'production_source_changed')
    return body


def bootstrap():
    require(os.name == 'nt' and Path(__file__).resolve() == ENTRY
            and sys.flags.isolated == 1 and sys.dont_write_bytecode, 'production_installation_rejected')
    require(not any(name.startswith('insights_') for name in sys.modules), 'production_preloaded_module')
    # Load only definitions from the pinned import helper. Its CLI/worker never
    # runs; the source-only loader then blocks all unreviewed insights_* imports.
    path = SOURCE / 'insights_private_review_run.py'
    helper = types.ModuleType('_production_source_helpers')
    helper.__file__ = str(path)
    exec(compile(read_pinned(path, HELPER_PIN), str(path), 'exec', dont_inherit=True), helper.__dict__)
    imports = helper.source_loader(SOURCE, MODULE_PINS)
    sys.meta_path.insert(0, imports)
    runtime = importlib.import_module('insights_staging_launcher')
    base, _ = runtime.load_inputs()  # Original runtime/config/source pins, unchanged.
    adapter = importlib.import_module('insights_production_mcp')
    for candidate, pin in FILE_PINS.items():
        read_pinned(candidate, pin)
    for parent in (WORKSPACE, *WORKSPACE.parents):
        candidate = parent / '.codex/config.toml'
        require(candidate == runtime.USER_CONFIG or not candidate.exists(), 'production_ancestor_config')
    require({item.name for item in WORKSPACE.iterdir()} == {'AGENTS.md', 'allowed.txt', 'denied.txt'},
            'production_workspace_changed')
    instructions = read_pinned(WORKSPACE / 'AGENTS.md', FILE_PINS[WORKSPACE / 'AGENTS.md']).decode('utf-8')
    imports.verify_loaded()
    return runtime, adapter, build_config(runtime, base, instructions), imports


def build_config(runtime, base, instructions):
    require(type(instructions) is str and 0 < len(instructions.encode('utf-8')) <= 8192,
            'production_instructions_rejected')
    config = copy.deepcopy(base)
    config['developer_instructions'] = instructions
    config['default_permissions'] = PROFILE
    config['permissions'] = {PROFILE: {
        'filesystem': {':root': 'deny', ':minimal': 'read', str(WORKSPACE): 'read',
                       str(WORKSPACE / 'denied.txt'): 'deny', str(SOURCE): 'deny',
                       str(runtime.DUMMY): 'deny', str(runtime.MATERIAL): 'deny',
                       str(MATERIAL): 'deny', str(runtime.USER_CONFIG.parent): 'deny'},
        'network': {'enabled': False},
    }}
    config['projects'] = {str(WORKSPACE): {'trust_level': 'trusted'}}
    config['model_provider'] = 'openai'
    config['model_providers'] = {}
    command = {'command': str(runtime.PYTHON), 'args': ['-I', '-B', str(ENTRY), '--mcp-preflight'],
               'cwd': str(WORKSPACE), 'env': {}, 'env_vars': [], 'enabled': True,
               'required': True, 'enabled_tools': ['prepare', 'publish']}
    config['mcp_servers'] = {SERVER: command,
                           'node_repl': {**command, 'enabled': False, 'required': False}}
    return config


def verify_config(runtime, actual, expected):
    require(actual.get('default_permissions') == PROFILE and actual.get('sandbox_mode') is None,
            'production_runtime_policy')
    require(runtime.without_nulls(actual.get('permissions', {}).get(PROFILE)) == expected['permissions'][PROFILE],
            'production_runtime_profile')
    require(actual.get('model_provider') == 'openai' and actual.get('model_providers') in ({}, None),
            'production_runtime_provider')
    # Reuse the established capability/schema/env checks. Normalize only the
    # selected profile's *name* in copies, after verifying our exact profile.
    normalized = copy.deepcopy(actual)
    wanted = copy.deepcopy(expected)
    for value in (normalized, wanted):
        value['default_permissions'] = runtime.PROFILE
        value['permissions'] = {runtime.PROFILE: value['permissions'][PROFILE]}
    runtime.verify_effective(normalized, wanted)
    apps = actual.get('apps', {})
    require(all(not entry.get('enabled') for name, entry in apps.items() if name != '_default'),
            'production_runtime_app_override')


def verify_thread(runtime, started):
    require(started.get('activePermissionProfile') == {'id': PROFILE, 'extends': None}
            and started.get('cwd') == str(WORKSPACE), 'production_thread_profile')
    normalized = copy.deepcopy(started)
    normalized['activePermissionProfile'] = {'id': runtime.PROFILE, 'extends': None}
    normalized['cwd'] = str(runtime.WORKSPACE)
    runtime.verify_thread(normalized)
    identifier = started.get('thread', {}).get('id')
    require(type(identifier) is str and 0 < len(identifier) <= 128, 'production_thread_identifier')
    return identifier


def verify_inventory(status, tools):
    require(type(status) is dict and status.get('nextCursor') is None, 'production_inventory_incomplete')
    rows = status.get('data')
    require(type(rows) is list and len(rows) == 2
            and {row.get('name') for row in rows} == {SERVER, 'node_repl'}, 'production_inventory_servers')
    indexed = {row['name']: row for row in rows}
    require(indexed['node_repl'].get('tools') == {}, 'production_repl_exposed')
    actual = indexed[SERVER].get('tools', {})
    require(set(actual) == {'prepare', 'publish'}, 'production_inventory_tools')
    for tool in tools:
        entry = actual[tool['name']]
        require(all(entry.get(key) == tool[key] for key in ('name', 'description', 'inputSchema')),
                'production_tool_contract')
    return {SERVER: ['prepare', 'publish'], 'node_repl': []}


class BlockedReports:
    """Discovery only. Never construct the production connection or Reports()."""
    def call(self, *args):
        raise ValueError('production_preflight_calls_disabled')

    def reject(self, *args):
        pass

    def close(self):
        pass


def serve_preflight(adapter, source, destination):
    adapter.serve(source, destination, BlockedReports())


def session_class(runtime):
    class Session(runtime.PreflightSession):
        def __init__(self, config):
            # Same reviewed process ownership/reader/cleanup as the prior runner;
            # only the production cwd and fixed RPC contracts change.
            self.counter = 0
            self.inbox = queue.Queue(maxsize=128)
            self.deadline = time.monotonic() + runtime.RUNTIME_SECONDS
            self.process = None
            self.reader = None
            self.reader_failure = None
            self.thread_id = None
            self.tree = runtime.OwnedRuntimeTree()
            try:
                self.process = subprocess.Popen(runtime.runtime_command(config), cwd=WORKSPACE,
                    env=runtime.clean_environment(os.environ), stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0, close_fds=True,
                    creationflags=subprocess.CREATE_NO_WINDOW | runtime.CREATE_SUSPENDED)
                self.tree.assign_and_resume(self.process)
                self.reader = threading.Thread(target=self.collect, daemon=True)
                self.reader.start()
            except BaseException:
                self.close()
                raise

        def send(self, message):
            method = message.get('method')
            allowed = {
                'initialize': {'clientInfo': {'name': 'home_energy_production_preflight', 'version': '0.1.0'},
                               'capabilities': {'experimentalApi': True}},
                'config/read': {'cwd': str(WORKSPACE)},
                'thread/start': {'cwd': str(WORKSPACE), 'ephemeral': True, 'permissions': PROFILE,
                                 'approvalPolicy': 'never', 'model': runtime.MODEL,
                                 'environments': [], 'runtimeWorkspaceRoots': []},
                'mcpServerStatus/list': {'threadId': self.thread_id, 'detail': 'toolsAndAuthOnly'},
            }
            if message != {'method': 'initialized', 'params': {}}:
                require(method in allowed and type(message.get('id')) is int
                        and message.get('params') == allowed[method], 'production_rpc_refused')
            return super().send(message)

        def request(self, method, params):
            require(method in runtime.RPC_METHODS, 'production_rpc_refused')
            self.counter += 1
            identifier = self.counter
            self.send({'id': identifier, 'method': method, 'params': params})
            while True:
                remaining = self.deadline - time.monotonic()
                require(remaining > 0, 'production_runtime_timeout')
                try:
                    event = self.inbox.get(timeout=remaining)
                except queue.Empty:
                    raise ValueError('production_runtime_timeout') from None
                require(not ('method' in event and 'id' in event), 'production_client_request_refused')
                require(event.get('method') not in FORBIDDEN_EVENTS, 'production_unexpected_action')
                require('error' not in event, 'production_runtime_rpc_failed')
                if event.get('id') == identifier:
                    return event['result']

        def check(self, config, tools):
            self.request('initialize', {'clientInfo': {'name': 'home_energy_production_preflight', 'version': '0.1.0'},
                                       'capabilities': {'experimentalApi': True}})
            self.send({'method': 'initialized', 'params': {}})
            verify_config(runtime, self.request('config/read', {'cwd': str(WORKSPACE)})['config'], config)
            started = self.request('thread/start', {'cwd': str(WORKSPACE), 'ephemeral': True,
                'permissions': PROFILE, 'approvalPolicy': 'never', 'model': runtime.MODEL,
                'environments': [], 'runtimeWorkspaceRoots': []})
            self.thread_id = verify_thread(runtime, started)
            inventory = verify_inventory(self.request('mcpServerStatus/list', {
                'threadId': self.thread_id, 'detail': 'toolsAndAuthOnly'}), tools)
            verify_config(runtime, self.request('config/read', {'cwd': str(WORKSPACE)})['config'], config)
            return {'runtime_preflight_passed': True, 'profile': PROFILE, 'mcp_tools': inventory,
                    'schemas_verified': True, 'collaboration_enabled': False,
                    'notification_commands_enabled': False, 'thread_ephemeral': True,
                    'generation_rpc_available': False, 'model_started': False,
                    'report_calls_enabled': False, 'ssh_connected': False, 'schedule_changed': False}
    return Session


def runtime_preflight(runtime, config, adapter):
    result = subprocess.run([str(runtime.CODEX), '--version'], capture_output=True, timeout=10,
                            env=runtime.clean_environment(os.environ),
                            creationflags=subprocess.CREATE_NO_WINDOW)
    require(result.returncode == 0 and result.stdout.decode().strip() == runtime.VERSION,
            'production_runtime_version')
    session = session_class(runtime)(config)
    try:
        return session.check(config, adapter.TOOLS)
    finally:
        session.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    for name in ('check', 'integration-check', 'preflight', 'os-check', 'mcp-preflight'):
        modes.add_argument('--' + name, action='store_true',
                           help=argparse.SUPPRESS if name == 'mcp-preflight' else 'No-model ' + name)
    args = parser.parse_args()
    runtime, adapter, config, imports = bootstrap()
    if args.mcp_preflight:
        serve_preflight(adapter, sys.stdin.buffer, sys.stdout.buffer)
        return
    if args.integration_check:
        result = importlib.import_module('insights_production_connection').check_integration()
    elif args.os_check:
        result = importlib.import_module('insights_production_os_probe').run_probe(runtime, config)
    elif args.preflight:
        importlib.import_module('insights_production_connection').check_integration()
        result = runtime_preflight(runtime, config, adapter)
    else:
        result = {'static_checks_passed': True, 'generation_rpc_available': False,
                  'model_started': False, 'ssh_connected': False, 'report_calls_enabled': False,
                  'schedule_changed': False}
    imports.verify_loaded()
    print(json.dumps({'timestamp_utc': datetime.now(timezone.utc).isoformat(), **result}, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        code = error.args[0] if isinstance(error, ValueError) and len(error.args) == 1 else ''
        code = code if type(code) is str and re.fullmatch('[a-z][a-z0-9_]{0,79}', code) else 'production_preflight_failed'
        print(json.dumps({'error': code, 'model_started': False, 'report_calls_enabled': False,
                          'schedule_changed': False}), file=sys.stderr)
        sys.exit(1)
