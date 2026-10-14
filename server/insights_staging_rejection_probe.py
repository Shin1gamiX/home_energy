"""Direct, local MCP rejection check: no turn, model provider, or SSH transport.

The pinned app-server is used only as an MCP client. Its two fixed requests go
to an always-closed fixture. A fresh temporary directory holds allowlisted
diagnostic events; existing approval receipts and evidence are never accessed.
"""
import copy
from datetime import datetime
import json
import os
from pathlib import Path
import queue
import re
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import insights_staging_launcher as launcher


ENTRY = launcher.SOURCE / 'insights_staging_rejection_probe.py'
DIRECTORY_ENV = 'HOME_ENERGY_REJECTION_PROBE_DIRECTORY'
PREFIX = 'homeenergy-rejection-probe-'
EVIDENCE_NAME = 'rejections.jsonl'
METADATA_NAME = 'metadata-{tool}.json'
MAX_EVIDENCE_BYTES = 8192
MAX_EVENTS = 16
METHOD = 'mcpServer/tool/call'
GATE_CODE = 'report_gate_closed'
# Administrator-approved review applies to this always-closed diagnostic only.
# Never copy this pin into the real reporting launcher without separate review.
DIAGNOSTIC_CONFIG_SHA256 = '66eb0479fc51a914aae18782cda79de5d107a57fa7ac88f20d938703f6ac3905'


def require(condition, code):
    launcher.require(condition, code)


def failure_code(error):
    if type(error) is launcher.GuardFailure and len(error.args) == 1:
        value = error.args[0]
        if type(value) is str and value.startswith('rejection_probe_') and re.fullmatch('[a-z_]{1,80}', value):
            return value
    return 'rejection_probe_failed'


def encoded(value):
    return json.dumps(value, ensure_ascii=True, allow_nan=False,
                      sort_keys=True, separators=(',', ':')).encode('ascii')


def evidence_path(directory):
    """Only a fresh direct child of this user's temporary directory is allowed."""
    require(type(directory) is str, 'rejection_probe_directory_invalid')
    path = Path(directory)
    parent = Path(tempfile.gettempdir()).resolve(strict=True)
    require(path.is_absolute() and path.parent == parent
            and re.fullmatch(PREFIX + '[a-z0-9_]{8}', path.name) is not None
            and path.resolve(strict=True) == path and path.is_dir(),
            'rejection_probe_directory_invalid')
    for item in (path, *path.parents):
        require(not item.is_symlink() and not getattr(item.lstat(), 'st_file_attributes', 0) & 1024,
                'rejection_probe_directory_invalid')
    return path / EVIDENCE_NAME


def validate_event(event, codes):
    require(type(event) is dict and set(event) == {'timestamp_utc', 'attempt', 'method', 'code'},
            'rejection_probe_event_invalid')
    require(type(event['timestamp_utc']) is str and re.fullmatch(
        r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?\+00:00', event['timestamp_utc']),
        'rejection_probe_event_invalid')
    try:
        datetime.fromisoformat(event['timestamp_utc'])
    except ValueError:
        raise launcher.GuardFailure('rejection_probe_event_invalid') from None
    require(type(event['attempt']) is int and 1 <= event['attempt'] <= 64
            and type(event['method']) is str and event['method'] in ('unknown', 'prepare', 'publish')
            and type(event['code']) is str and event['code'] in codes,
            'rejection_probe_event_invalid')
    return event


class EvidenceSink:
    def __init__(self, stream, codes):
        self.stream, self.codes = stream, codes
        self.count = self.size = 0
        self.failed = False

    def record(self, event):
        try:
            require(not self.failed, 'rejection_probe_sink_failed')
            body = encoded(validate_event(event, self.codes)) + b'\n'
            require(self.count < MAX_EVENTS and self.size + len(body) <= MAX_EVIDENCE_BYTES,
                    'rejection_probe_evidence_limit')
            require(self.stream.write(body) == len(body), 'rejection_probe_sink_failed')
            self.stream.flush()
            os.fsync(self.stream.fileno())
            self.count += 1
            self.size += len(body)
        except Exception:
            self.failed = True
            raise launcher.GuardFailure('rejection_probe_sink_failed') from None


class ClosedReports:
    rejection_code = GATE_CODE

    def call(self, *_):
        raise ValueError('Local fixture is permanently closed')


def json_type(value):
    return {type(None): 'null', bool: 'boolean', int: 'integer', float: 'number',
            str: 'string', dict: 'object', list: 'array'}.get(type(value), 'unknown')


class MetadataSource:
    """Observe only fixed synthetic calls, without changing their wire bytes."""
    def __init__(self, source, record=None):
        self.source = source
        self.record = record
        self.observations = []

    def expected_calls(self):
        """Fixed plan; a separate local fixture may supply its own fixed plan."""
        return fixed_calls()

    def readline(self, limit):
        line = self.source.readline(limit)
        if not line or len(line) > limit or not line.endswith(b'\n'):
            return line
        try:
            message = json.loads(line)
        except (ValueError, UnicodeError, RecursionError):
            return line
        if type(message) is not dict or message.get('method') != 'tools/call':
            return line
        params = message.get('params')
        if type(params) is not dict:
            return line
        calls = self.expected_calls()
        require(len(self.observations) < len(calls), 'rejection_probe_metadata_limit')
        name, arguments = calls[len(self.observations)]
        require(params.get('name') == name and params.get('arguments') == arguments,
                'rejection_probe_metadata_call_invalid')
        metadata = params.get('_meta')
        fields = []
        if type(metadata) is dict:
            require(len(metadata) <= 16, 'rejection_probe_metadata_limit')
            for key, value in sorted(metadata.items()):
                # Protocol field names only; never identifiers or metadata values.
                safe_name = key if re.fullmatch(r'[A-Za-z][A-Za-z_./:-]{0,95}', key) else '[redacted-key]'
                fields.append({'name': safe_name, 'type': json_type(value)})
        self.observations.append({'tool': name, 'metadata_present': '_meta' in params,
                                  'metadata_type': json_type(metadata), 'fields': fields})
        if self.record is not None:
            self.record(self.observations[-1])
        return line


def retain_metadata(path, observations):
    for observation in observations:
        require(observation.get('tool') in ('prepare', 'publish'), 'rejection_probe_metadata_call_invalid')
        body = encoded(observation) + b'\n'
        require(len(body) <= MAX_EVIDENCE_BYTES, 'rejection_probe_metadata_limit')
        target = path.with_name(METADATA_NAME.format(tool=observation['tool']))
        with target.open('xb') as stream:
            require(stream.write(body) == len(body), 'rejection_probe_metadata_write_failed')
            stream.flush()
            os.fsync(stream.fileno())


def serve_fixture(adapter, source, destination, stream):
    sink = EvidenceSink(stream, adapter.REJECTION_CODES)
    adapter.serve(source, destination, ClosedReports(), lambda event: None, rejection=sink.record)


def serve_file_fixture(adapter, source, destination, path, *, source_type=MetadataSource):
    # App-server may construct separate discovery and per-thread MCP clients.
    # Discovery alone must not consume the exclusive diagnostic file.
    stream = None
    sink = None
    # Persist before replying: Job Object cleanup can kill the fixture without
    # running Python's finally handlers. Each fixed call has its own new file.
    observed_source = source_type(source, lambda observation: retain_metadata(path, [observation]))

    def record(event):
        nonlocal stream, sink
        if stream is None:
            stream = path.open('xb')
            sink = EvidenceSink(stream, adapter.REJECTION_CODES)
        sink.record(event)

    try:
        adapter.serve(observed_source, destination, ClosedReports(), lambda event: None, rejection=record)
    finally:
        if stream is not None:
            stream.close()


def fixture_config(config, directory):
    evidence_path(directory)
    result = copy.deepcopy(config)
    active = result['mcp_servers'][launcher.SERVER]
    active['args'] = ['-I', '-B', str(ENTRY), '--mcp-fixture']
    active['env'] = {DIRECTORY_ENV: directory}
    return result


def fixed_calls():
    return [('prepare', {}), ('publish', {
        'analysis_id': 'a' * 64,
        'summary': {'en': 'Local closed-gate fixture.', 'el': 'Τοπικός έλεγχος αποκλεισμού.',
                    'ru': 'Локальная проверка блокировки.'}, 'notes': []})]


def load_probe_inputs():
    original_pins = launcher.FILE_PINS
    try:
        launcher.FILE_PINS = {**original_pins, launcher.USER_CONFIG: DIAGNOSTIC_CONFIG_SHA256}
        return launcher.load_inputs()
    finally:
        launcher.FILE_PINS = original_pins


class DirectSession(launcher.PreflightSession):
    """No generation method, even when called directly through send/request."""
    def __init__(self, config):
        self.thread_id = None
        self.call_index = 0
        super().__init__(config)

    def send(self, message):
        if message.get('method') != METHOD:
            return super().send(message)
        require(type(message.get('id')) is int and self.thread_id is not None
                and self.call_index < len(fixed_calls()), 'rejection_probe_rpc_refused')
        name, arguments = fixed_calls()[self.call_index]
        wanted = {'threadId': self.thread_id, 'server': launcher.SERVER,
                  'tool': name, 'arguments': arguments}
        require(message.get('params') == wanted, 'rejection_probe_rpc_refused')
        self.call_index += 1  # A partial send is consumed, never automatically retried.
        frame = encoded(message) + b'\n'
        require(self.process.stdin.write(frame) == len(frame), 'rejection_probe_short_write')
        self.process.stdin.flush()

    def call_fixture(self, name, arguments, blocked_message):
        self.counter += 1
        identifier = self.counter
        self.send({'id': identifier, 'method': METHOD, 'params': {
            'threadId': self.thread_id, 'server': launcher.SERVER, 'tool': name, 'arguments': arguments}})
        while True:
            remaining = self.deadline - time.monotonic()
            require(remaining > 0, 'rejection_probe_timeout')
            try:
                event = self.inbox.get(timeout=remaining)
            except queue.Empty:
                raise launcher.GuardFailure('rejection_probe_timeout') from None
            require(not ('method' in event and 'id' in event), 'rejection_probe_client_request_refused')
            require(not str(event.get('method', '')).startswith('turn/'), 'rejection_probe_unexpected_turn')
            if event.get('id') == identifier:
                # The app-server may wrap the MCP error as an RPC error. Never
                # retain its text: only establish the fixed generic boundary.
                require('error' in event and blocked_message in json.dumps(event['error']),
                        'rejection_probe_call_not_blocked')
                return
            require('error' not in event, 'rejection_probe_runtime_failed')


def read_evidence(path, codes):
    with path.open('rb') as source:
        body = source.read(MAX_EVIDENCE_BYTES + 1)
    require(len(body) <= MAX_EVIDENCE_BYTES and body.endswith(b'\n'), 'rejection_probe_evidence_invalid')
    lines = body.splitlines()
    require(len(lines) == len(fixed_calls()), 'rejection_probe_evidence_incomplete')
    events = [validate_event(json.loads(line), codes) for line in lines]
    require([event['attempt'] for event in events] == sorted(set(event['attempt'] for event in events)),
            'rejection_probe_evidence_invalid')
    return events


def run_probe():
    config, adapter = load_probe_inputs()
    launcher.plain_file(ENTRY)
    require(Path(__file__).resolve() == ENTRY, 'rejection_probe_installation_invalid')
    # Retain this synthetic directory for diagnosis; do not touch any prior run.
    directory = tempfile.mkdtemp(prefix=PREFIX)
    path = evidence_path(directory)
    actual = fixture_config(config, directory)
    session = None
    try:
        session = DirectSession(actual)
        session.request('initialize', {'clientInfo': {'name': 'home_energy_rejection_probe', 'version': '0.1.0'},
                                      'capabilities': {'experimentalApi': True}})
        session.send({'method': 'initialized', 'params': {}})
        launcher.verify_effective(session.request('config/read', {'cwd': str(launcher.WORKSPACE)})['config'], actual)
        started = session.request('thread/start', {'cwd': str(launcher.WORKSPACE), 'ephemeral': True,
            'permissions': launcher.PROFILE, 'approvalPolicy': 'never', 'model': launcher.MODEL,
            'environments': [], 'runtimeWorkspaceRoots': []})
        launcher.verify_thread(started)
        session.thread_id = started['thread']['id']
        launcher.verify_inventory(session.request('mcpServerStatus/list', {
            'threadId': session.thread_id, 'detail': 'toolsAndAuthOnly'}), adapter.TOOLS)
        for name, arguments in fixed_calls():
            session.call_fixture(name, arguments, adapter.ERROR)
        launcher.verify_effective(session.request('config/read', {'cwd': str(launcher.WORKSPACE)})['config'], actual)
        launcher.check_pin(launcher.USER_CONFIG, DIAGNOSTIC_CONFIG_SHA256)
    finally:
        if session is not None:
            session.close()
    events = read_evidence(path, adapter.REJECTION_CODES)
    metadata_shapes = []
    for name, _ in fixed_calls():
        with path.with_name(METADATA_NAME.format(tool=name)).open('rb') as source:
            metadata_body = source.read(MAX_EVIDENCE_BYTES + 1)
        require(len(metadata_body) <= MAX_EVIDENCE_BYTES, 'rejection_probe_metadata_limit')
        metadata_shapes.append(json.loads(metadata_body))
    return {'local_boundary_probe_completed': True,
            'rejection_codes': [event['code'] for event in events],
            'metadata_shapes': metadata_shapes,
            'closed_gate_reached': all(event['code'] == GATE_CODE for event in events),
            'client_path': 'direct_mcp_rpc', 'original_model_failure_reproduced': False,
            'model_started': False, 'turn_started': False, 'ssh_connected': False,
            'public_publishing_enabled': False, 'schedule_changed': False,
            'existing_evidence_changed': False, 'synthetic_evidence_directory': directory}


def main():
    require(sys.flags.isolated == 1 and sys.dont_write_bytecode, 'rejection_probe_isolated_invocation_required')
    require(Path(__file__).resolve() == ENTRY, 'rejection_probe_installation_invalid')
    if sys.argv[1:] == ['--mcp-fixture']:
        # This branch does not import the integration, inspect material, or run
        # load_inputs. Its only writes are fixed allowlisted synthetic events.
        adapter = launcher.load_adapter()
        path = evidence_path(os.environ.pop(DIRECTORY_ENV, None))
        serve_file_fixture(adapter, sys.stdin.buffer, sys.stdout.buffer, path)
    else:
        require(len(sys.argv) == 1, 'rejection_probe_arguments_refused')
        print(json.dumps(run_probe(), sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'error': 'Local rejection probe failed', 'code': failure_code(error)}), file=sys.stderr)
        sys.exit(1)
