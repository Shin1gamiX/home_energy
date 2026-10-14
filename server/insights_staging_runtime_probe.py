"""Fixed local-only runtime probes; never a reporting or model-service runner.

The temporary HTTP responder supplies predefined synthetic tool calls. It never
uses a paid model, accepts caller-provided plans, or constructs an SSH transport.
Only the preflight MCP implementation is allowed; its report calls always fail.
"""
import copy
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import queue
import re
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import insights_staging_launcher as launcher


PROVIDER = 'home_energy_fixed_local_probe'
SECONDS = 90
MAX_BODY = 1_000_000
MODEL_CATALOG_ROUTE = '/v1/models?client_version=0.162.0'
MAX_MODEL_REQUESTS = 2
NESTED = sorted(('clock__curr_time', 'list_mcp_resource_templates',
                 'list_mcp_resources', 'read_mcp_resource',
                 'mcp__home_energy_staging__prepare', 'mcp__home_energy_staging__publish'))
TOP_ALLOWED = frozenset(('functions.exec', 'functions.wait',
    'functions.request_user_input', 'functions.request_user_input_async', 'clock.sleep'))
COLLABORATION = ('list_agents', 'spawn_agent', 'send_message',
                 'followup_task', 'interrupt_agent', 'wait_agent')
SHAPES = ('namespace', 'qualified', 'bare')
MISSING_TARGET = '/root/he_synthetic_nonexistent_target_98a1f70d'
SENTINEL = launcher.WORKSPACE / 'unexpected-model-write.txt'
PROMPT = 'Authorized fixed synthetic isolation test only. No live reporting or external services.'
FINAL = 'LOCAL SYNTHETIC ISOLATION PROBE COMPLETE'
BLOCKED = 'Staging request rejected or temporarily unavailable'
EXTRA_RPC = frozenset(('turn/start', 'thread/loaded/list'))


def require(condition, code):
    launcher.require(condition, code)


def failure_code(error):
    """Only fixed guard-code-shaped values may leave the diagnostic boundary."""
    if isinstance(error, launcher.GuardFailure) and len(error.args) == 1:
        code = error.args[0]
        if isinstance(code, str) and re.fullmatch('[a-z][a-z0-9_]{0,79}', code):
            return code
    return 'probe_unexpected_failure'


def encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False,
                      sort_keys=True, separators=(',', ':')).encode()


def decode(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'probe_duplicate_json')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: require(False, 'probe_nonfinite_json'))


def custom(name, code):
    return {'id': 'item_' + name, 'call_id': name, 'type': 'custom_tool_call',
            'name': 'exec', 'namespace': 'functions', 'input': code, 'status': 'completed'}


def plans():
    """No parameters: every target, argument, script and prospective write is fixed."""
    result = [custom('he_catalog', 'text(ALL_TOOLS.map(t => t.name).sort());')]
    arguments = {
        'list_agents': {},
        'spawn_agent': {'task_name': 'he_synthetic_probe_98a1f70d',
                        'message': 'Synthetic local test only. Do nothing.', 'fork_turns': 'none'},
        'send_message': {'target': MISSING_TARGET, 'message': 'SYNTHETIC TEST ONLY'},
        'followup_task': {'target': MISSING_TARGET, 'message': 'SYNTHETIC TEST ONLY'},
        'interrupt_agent': {'target': MISSING_TARGET},
        'wait_agent': {'timeout_ms': 10000},
    }
    for shape in SHAPES:
        for name in COLLABORATION:
            identifier = 'he_' + shape + '_' + name
            item = {'id': 'item_' + identifier, 'call_id': identifier,
                    'type': 'function_call', 'name': name,
                    'arguments': encoded(arguments[name]).decode(), 'status': 'completed'}
            if shape == 'namespace':
                item['namespace'] = 'collaboration'
            elif shape == 'qualified':
                item['name'] = 'collaboration.' + name
            result.append(item)
    patch = ('*** Begin Patch\n*** Add File: ' + SENTINEL.as_posix()
             + '\n+SYNTHETIC UNEXPECTED WRITE\n*** End Patch')
    for name, argument in (
        ('exec_command', {'cmd': 'echo HOME_ENERGY_SYNTHETIC_UNEXPECTED_SHELL'}),
        ('read_file', {'path': str(launcher.WORKSPACE / 'denied.txt')}),
        ('apply_patch', patch),
    ):
        result.append(custom('he_absent_' + name,
                             'text(await tools.' + name + '(' + encoded(argument).decode() + '));'))
    payload = {'analysis_id': 'a' * 64, 'summary': {
        'en': 'Synthetic blocked-call check only.',
        'el': 'Μόνο συνθετικός έλεγχος αποκλεισμένης κλήσης.',
        'ru': 'Только синтетическая проверка блокировки вызова.'}, 'notes': []}
    for name, argument in (('prepare', {}), ('publish', payload)):
        result.append(custom('he_blocked_' + name,
            'text(await tools.mcp__home_energy_staging__' + name
            + '(' + encoded(argument).decode() + '));'))
    return result


def response_events(item, step):
    response = {'id': 'he_local_' + str(step), 'object': 'response', 'created_at': 0,
                'status': 'completed', 'output': [item],
                'usage': {'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0}}
    events = [{'type': 'response.created', 'response': {**response, 'status': 'in_progress', 'output': []}},
              {'type': 'response.output_item.added', 'output_index': 0, 'item': item}]
    if item['type'] in ('function_call', 'custom_tool_call'):
        field = 'arguments' if item['type'] == 'function_call' else 'input'
        stream = 'function_call_arguments' if field == 'arguments' else 'custom_tool_call_input'
        events[1]['item'] = {**item, field: '', 'status': 'in_progress'}
        events.extend(({'type': 'response.' + stream + '.delta', 'item_id': item['id'],
                        'output_index': 0, 'delta': item[field]},
                       {'type': 'response.' + stream + '.done', 'item_id': item['id'],
                        'output_index': 0, field: item[field]}))
    events.extend(({'type': 'response.output_item.done', 'output_index': 0, 'item': item},
                   {'type': 'response.completed', 'response': response}))
    return events


def texts(content):
    if isinstance(content, str):
        return [content]
    if isinstance(content, list):
        return [entry['text'] for entry in content if isinstance(entry, dict)
                and entry.get('type') in ('text', 'input_text', 'output_text')
                and isinstance(entry.get('text'), str)]
    return []


def developer_instructions_present(body, instructions):
    # Deliberately exclude user messages, tool results and arbitrary JSON strings.
    return any(isinstance(item, dict) and item.get('role') == 'developer'
               and item.get('type', 'message') == 'message'
               and any(instructions in text for text in texts(item.get('content')))
               for item in body.get('input', []))


def catalog(body):
    tools = list(body.get('tools', []))
    for item in body.get('input', []):
        if isinstance(item, dict) and item.get('type') == 'additional_tools':
            tools.extend(item.get('tools', []))
    found = []
    for tool in tools:
        require(isinstance(tool, dict), 'probe_invalid_catalog')
        name, kind = tool.get('name'), tool.get('type')
        require(isinstance(name, str), 'probe_invalid_catalog')
        if kind == 'namespace':
            for child in tool.get('tools', []):
                require(isinstance(child, dict) and child.get('type') in ('function', 'custom'),
                        'probe_unreviewed_tool_type')
                found.append((name + '.' + str(child.get('name')), child['type']))
        else:
            require(kind in ('function', 'custom'), 'probe_unreviewed_tool_type')
            found.append((name, kind))
    require(found and len(found) == len(set(found)), 'probe_missing_or_duplicate_catalog')
    require(all(name in TOP_ALLOWED for name, _ in found)
            and ('functions.exec', 'custom') in found, 'probe_unreviewed_tool_catalog')
    return tuple(sorted(found))


def verify_output(wanted, item):
    """Used both on runtime events and outgoing HTTP input before the next call."""
    identifier = wanted['call_id']
    require(item.get('call_id') == identifier and item.get('type') == wanted['type'] + '_output',
            'probe_output_type_mismatch')
    output = '\n'.join(texts(item.get('output'))).strip()
    if identifier == 'he_catalog':
        arrays = []
        for text in texts(item.get('output')):
            try:
                value = decode(text)
            except ValueError:
                continue
            if isinstance(value, list):
                arrays.append(value)
        require(arrays == [NESTED], 'probe_nested_catalog_mismatch')
    elif wanted['type'] == 'function_call':
        require(output == 'unsupported call: ' + wanted.get('namespace', '') + wanted['name'],
                'probe_collaboration_not_rejected')
    elif identifier.startswith('he_absent_'):
        name = identifier.removeprefix('he_absent_')
        require('TypeError: tools.' + name + ' is not a function' in output,
                'probe_absent_tool_not_rejected')
    else:
        require(BLOCKED in output and 'staged' not in output
                and 'analysis_id' not in output, 'probe_report_call_not_blocked')


def verify_previous_output(body, wanted):
    matches = [item for item in body.get('input', []) if isinstance(item, dict)
               and item.get('call_id') == wanted['call_id']
               and item.get('type') in ('function_call_output', 'custom_tool_call_output')]
    require(len(matches) == 1, 'probe_missing_previous_output')
    verify_output(wanted, matches[0])


class Evidence:
    """Retain fixed flags/names only; never raw instructions, tool results or requests."""
    def __init__(self):
        self.expected = {item['call_id']: item for item in plans()}
        self.calls = set()
        self.results = set()
        self.thread_id = None
        self.turn_id = None
        self.started_threads = set()
        self.completed = False
        self.failed = False
        self.nested_verified = False

    def accept(self, event):
        params = event.get('params', {})
        if event.get('method') == 'thread/started':
            identifier = params.get('thread', {}).get('id')
            require(isinstance(identifier, str), 'probe_invalid_thread_event')
            self.started_threads.add(identifier)
            require(len(self.started_threads) <= 1, 'probe_unexpected_child')
        if params.get('threadId') != self.thread_id or self.thread_id is None:
            return
        if self.turn_id is not None:
            require(params.get('turnId', self.turn_id) == self.turn_id, 'probe_unexpected_turn')
        method = event.get('method')
        if method == 'turn/completed':
            self.completed = params.get('turn', {}).get('status') == 'completed'
            self.failed = not self.completed
        if method != 'rawResponseItem/completed':
            return
        item = params.get('item', {})
        kind, identifier = item.get('type'), item.get('call_id')
        if kind not in ('function_call', 'custom_tool_call', 'function_call_output', 'custom_tool_call_output'):
            return
        require(identifier in self.expected, 'probe_unexpected_call')
        wanted = self.expected[identifier]
        if kind in ('function_call', 'custom_tool_call'):
            keys = ('type', 'name', 'namespace', 'arguments', 'input')
            require(identifier not in self.calls and all(item.get(k) == wanted.get(k) for k in keys),
                    'probe_call_mismatch')
            self.calls.add(identifier)
            return
        require(identifier in self.calls and identifier not in self.results, 'probe_unpaired_output')
        verify_output(wanted, item)
        if identifier == 'he_catalog':
            self.nested_verified = True
        self.results.add(identifier)


def provider_settings(port):
    require(type(port) is int and 1 <= port <= 65535, 'probe_bad_port')
    return {'name': 'Fixed local synthetic responder', 'base_url': f'http://127.0.0.1:{port}/v1',
            'wire_api': 'responses', 'requires_openai_auth': False, 'supports_websockets': False,
            'supports_standalone_web_search': False,
            'request_max_retries': 0, 'stream_max_retries': 0}


def probe_config(config, port):
    result = copy.deepcopy(config)
    result['model_provider'] = PROVIDER
    result['model_providers'] = {PROVIDER: provider_settings(port)}
    return result


def verify_config(actual, expected):
    launcher.verify_effective(actual, expected)
    require(actual.get('model_provider') == PROVIDER, 'probe_provider_mismatch')
    providers = launcher.without_nulls(actual.get('model_providers', {}))
    require(providers == expected['model_providers'], 'probe_provider_settings_mismatch')


def validate_headers(method, path, headers, port):
    require(method == 'POST' and path == '/v1/responses', 'probe_unexpected_endpoint')
    indexed = {}
    for name, value in headers:
        key = name.lower()
        require(key not in indexed, 'probe_duplicate_header')
        indexed[key] = value
    require(not any(name in indexed for name in ('authorization', 'proxy-authorization',
             'cookie', 'x-api-key', 'api-key', 'chatgpt-account-id')), 'probe_credentials_header')
    allowed = {'host', 'content-length', 'content-type', 'accept', 'accept-encoding',
               'user-agent', 'connection', 'originator', 'version', 'session_id',
               'session-id', 'thread-id', 'x-codex-window-id',
               'x-openai-internal-codex-responses-lite',
               'x-codex-turn-metadata', 'x-openai-client-user-agent', 'x-client-request-id',
               'x-request-id', 'x-codex-beta-features', 'openai-beta'}
    require(set(indexed) <= allowed, 'probe_unexpected_header')
    require(indexed.get('host') == f'127.0.0.1:{port}', 'probe_host_mismatch')
    require(indexed.get('content-type', '').split(';')[0].strip() == 'application/json',
            'probe_content_type_mismatch')
    length = indexed.get('content-length', '')
    require(re.fullmatch('[0-9]{1,7}', length) is not None and 0 < int(length) <= MAX_BODY,
            'probe_body_limit')
    return int(length)


def validate_model_headers(path, headers, port, count):
    """Only the exact observed version discovery request, never a proxy route."""
    require(path == MODEL_CATALOG_ROUTE, 'probe_unexpected_models_endpoint')
    require(type(count) is int and 0 <= count < MAX_MODEL_REQUESTS, 'probe_models_request_limit')
    indexed = {}
    for name, value in headers:
        key = name.lower()
        require(key not in indexed, 'probe_duplicate_header')
        indexed[key] = value
    require(set(indexed) <= {'accept', 'host', 'originator', 'user-agent'},
            'probe_unexpected_models_header')
    require(indexed.get('host') == f'127.0.0.1:{port}', 'probe_host_mismatch')


class LocalResponder:
    def __init__(self, instructions):
        require(isinstance(instructions, str) and instructions.strip(), 'probe_instructions_missing')
        self.instructions = instructions
        self.count = 0
        self.model_count = 0
        self.catalog = None
        self.instructions_verified = False
        self.failure = None
        self.lock = threading.Lock()
        self.deadline = time.monotonic() + SECONDS
        self.plan = plans()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def setup(self):
                super().setup()
                self.connection.settimeout(5)

            def do_POST(self):
                with owner.lock:
                    try:
                        require(owner.failure is None and time.monotonic() < owner.deadline,
                                'probe_responder_expired')
                        length = validate_headers(self.command, self.path, self.headers.items(), owner.server.server_port)
                        require(owner.count <= len(owner.plan), 'probe_request_limit')
                        body = decode(self.rfile.read(length))
                        require(isinstance(body, dict) and body.get('stream') is True
                                and body.get('model') == launcher.MODEL, 'probe_invalid_request')
                        observed = catalog(body)
                        if owner.catalog is None:
                            owner.catalog = observed
                        require(observed == owner.catalog, 'probe_catalog_changed')
                        require(developer_instructions_present(body, owner.instructions),
                                'probe_developer_instructions_missing')
                        owner.instructions_verified = True
                        step = owner.count
                        # Do not dispatch any further call before the preceding
                        # result is verified, including the initial nested catalog.
                        if step:
                            verify_previous_output(body, owner.plan[step - 1])
                        owner.count += 1
                        item = owner.plan[step] if step < len(owner.plan) else {
                            'id': 'he_done', 'type': 'message', 'role': 'assistant', 'status': 'completed',
                            'content': [{'type': 'output_text', 'text': FINAL, 'annotations': []}]}
                        payload = ''.join('event: ' + event['type'] + '\ndata: '
                            + encoded(event).decode() + '\n\n' for event in response_events(item, step)).encode()
                        self.send_response(200)
                        self.send_header('Content-Type', 'text/event-stream')
                        self.send_header('Content-Length', str(len(payload)))
                        self.end_headers()
                        self.wfile.write(payload)
                    except Exception as error:
                        owner.failure = failure_code(error)
                        self.send_error(400)

            def do_GET(self):
                with owner.lock:
                    try:
                        require(owner.failure is None and time.monotonic() < owner.deadline,
                                'probe_responder_expired')
                        validate_model_headers(self.path, self.headers.items(),
                                               owner.server.server_port, owner.model_count)
                        owner.model_count += 1
                        payload = b'{"models":[]}'
                        self.send_response(200)
                        self.send_header('Content-Type', 'application/json')
                        self.send_header('Content-Length', str(len(payload)))
                        self.end_headers()
                        self.wfile.write(payload)
                    except Exception as error:
                        owner.failure = failure_code(error)
                        self.send_error(400)

            def reject(self):
                owner.failure = 'probe_responder_unexpected_method'
                self.send_error(405)

            do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = reject

        # One owned worker, no detached per-request threads; its socket timeout
        # also bounds shutdown if a client supplies an incomplete HTTP body.
        self.server = HTTPServer(('127.0.0.1', 0), Handler)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self):
        self.worker.start()

    def close(self):
        if self.worker.ident is not None:
            self.server.shutdown()
        self.server.server_close()
        if self.worker.ident is not None:
            self.worker.join(timeout=3)
            require(not self.worker.is_alive(), 'probe_responder_cleanup_failed')


class ProbeSession(launcher.PreflightSession):
    """The production preflight class remains incapable of starting a turn."""
    def __init__(self, config, evidence):
        self.evidence = evidence
        super().__init__(config)
        self.deadline = time.monotonic() + SECONDS

    def send(self, message):
        method = message.get('method')
        if method not in EXTRA_RPC:
            return super().send(message)
        params = message.get('params')
        wanted = ({'threadId': self.evidence.thread_id,
                   'input': [{'type': 'text', 'text': PROMPT}]} if method == 'turn/start' else {})
        require(params == wanted and type(message.get('id')) is int, 'probe_rpc_not_permitted')
        frame = encoded(message) + b'\n'
        require(self.process.stdin.write(frame) == len(frame), 'runtime_short_write')
        self.process.stdin.flush()

    def receive(self):
        remaining = self.deadline - time.monotonic()
        require(remaining > 0, 'probe_runtime_timeout')
        try:
            event = self.inbox.get(timeout=remaining)
        except queue.Empty:
            raise launcher.GuardFailure('probe_runtime_timeout') from None
        require(not ('method' in event and 'id' in event), 'probe_client_request_refused')
        require('error' not in event, 'probe_runtime_failed')
        self.evidence.accept(event)
        observer = getattr(self, 'observer', None)
        if observer is not None:
            observer(event)
        return event

    def request(self, method, params):
        require(method in launcher.RPC_METHODS | EXTRA_RPC, 'probe_rpc_not_permitted')
        self.counter += 1
        identifier = self.counter
        self.send({'id': identifier, 'method': method, 'params': params})
        while True:
            event = self.receive()
            if event.get('id') == identifier:
                return event['result']


def run_probe(*, candidate_config=None, observer=None):
    config, adapter = launcher.load_inputs()
    # Host-only hook for rechecking a reviewed changed MCP entry with its gate
    # closed. CLI invocations still always use the original blocked candidate.
    if candidate_config is not None:
        config = copy.deepcopy(candidate_config)
    require(not SENTINEL.exists(), 'probe_existing_sentinel')
    instructions = config.get('developer_instructions')
    responder = LocalResponder(instructions)
    session = None
    result = None
    try:
        responder.start()
        config = probe_config(config, responder.server.server_port)
        evidence = Evidence()
        session = ProbeSession(config, evidence)
        session.observer = observer
        session.request('initialize', {'clientInfo': {'name': 'home_energy_fixed_probe', 'version': '0.1.0'},
                                      'capabilities': {'experimentalApi': True}})
        session.send({'method': 'initialized', 'params': {}})
        verify_config(session.request('config/read', {'cwd': str(launcher.WORKSPACE)})['config'], config)
        started = session.request('thread/start', {'cwd': str(launcher.WORKSPACE), 'ephemeral': True,
            'permissions': launcher.PROFILE, 'approvalPolicy': 'never', 'model': launcher.MODEL,
            'modelProvider': PROVIDER, 'environments': [], 'runtimeWorkspaceRoots': [],
            'experimentalRawEvents': True})
        require(started.get('modelProvider') == PROVIDER, 'probe_thread_provider_mismatch')
        # Reuse all existing guards; only the separately verified loopback provider differs.
        launcher.verify_thread({**started, 'modelProvider': 'openai'})
        evidence.thread_id = started['thread']['id']
        require(evidence.started_threads <= {evidence.thread_id}, 'probe_unexpected_child')
        launcher.verify_inventory(session.request('mcpServerStatus/list', {
            'threadId': evidence.thread_id, 'detail': 'toolsAndAuthOnly'}), adapter.TOOLS)
        turn = session.request('turn/start', {'threadId': evidence.thread_id,
            'input': [{'type': 'text', 'text': PROMPT}]})
        evidence.turn_id = turn['turn']['id']
        while not evidence.completed and not evidence.failed:
            require(responder.failure is None, 'probe_responder_failed')
            session.receive()
        require(session.request('thread/loaded/list', {}).get('data') == [evidence.thread_id],
                'probe_unexpected_loaded_thread')
        verify_config(session.request('config/read', {'cwd': str(launcher.WORKSPACE)})['config'], config)
        require(evidence.completed and not evidence.failed and evidence.nested_verified,
                'probe_incomplete_evidence')
        require(evidence.calls == evidence.results == set(evidence.expected), 'probe_missing_call_results')
        require(responder.failure is None and responder.count == len(plans()) + 1
                and responder.instructions_verified, 'probe_missing_provider_evidence')
        require(not SENTINEL.exists(), 'probe_unexpected_write')
        result = {'local_synthetic_probe_passed': True, 'external_model_called': False,
                  'top_tool_catalog': responder.catalog, 'nested_tools': NESTED,
                  'synthetic_model_catalog_requests': responder.model_count,
                  'matched_collaboration_rejections': 18, 'matched_absent_tools': 3,
                  'matched_blocked_report_calls': 2, 'candidate_developer_instructions_verified': True,
                  'candidate_instructions_sha256': hashlib.sha256(instructions.encode()).hexdigest(),
                  'only_parent_thread_loaded': True, 'write_tool_unavailable_verified': True,
                  'os_filesystem_denial_proven': False, 'report_transport_enabled': False,
                  'schedule_changed': False, 'isolation_approved': False}
    except Exception:
        if responder.failure is not None:
            raise launcher.GuardFailure(responder.failure) from None
        raise
    finally:
        try:
            if session is not None:
                session.close()
        finally:
            responder.close()
    return result


def main():
    require(sys.flags.isolated == 1 and sys.dont_write_bytecode and len(sys.argv) == 1,
            'probe_isolated_fixed_invocation_required')
    print(json.dumps(run_probe(), sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'error': 'Local synthetic probe failed; no activation was performed',
                          'code': failure_code(error)}), file=sys.stderr)
        sys.exit(1)
