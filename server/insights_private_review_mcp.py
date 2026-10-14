"""Two bounded tools for one frozen real-data review saved privately on this PC.

This adapter has no network transport or caller-selected paths. The trusted
runner supplies a lazy, fixed local store. Discovery never opens that store.
Existing synthetic adapters and their provenance contracts are not reused.
"""
import copy
from datetime import datetime, timezone
import hashlib
import math
import re
import time

from insights_broker import MAX_REQUEST, MAX_RESPONSE, decode, encode, request, require
from insights_publish import localized_text, narrative
from insights_staging_mcp import (
    CONTEXT_METADATA_FIELDS, MAX_CONTEXT_LENGTH, TOOL_METADATA_FIELDS,
    text_schema, validate_turn_metadata,
)


SERVER = 'home_energy_private_review'
CLOSED_TOKEN = '0' * 64
ERROR = 'Private review request rejected or temporarily unavailable'
MAX_FRAME = MAX_REQUEST + 8192  # Payload plus bounded client context, never prose output.
MAX_ANALYSIS = 32768
MAX_SNAPSHOT = 65536
SEVERITIES = {'routine': 0, 'notice': 1, 'warning': 2, 'data_issue': 3}
METRIC_KEYS = ('pv', 'grid', 'load', 'battery_supplied', 'battery_charged', 'soc_max')
FINDINGS = {
    'incomplete_coverage': ('monitoring', 'data_issue', None),
    'stale_history': ('monitoring', 'data_issue', None),
    'recording_gap': ('monitoring', 'data_issue', None),
    'limited_baseline': ('monitoring', 'notice', None),
    'high_load_energy': ('house', None, 'load'),
    'high_grid_energy': ('grid', 'notice', 'grid'),
    'low_solar_energy': ('solar', 'notice', 'pv'),
    'low_battery_maximum': ('battery', 'notice', 'soc_max'),
    'sustained_house_load': ('house', 'notice', 'load'),
    'no_threshold_exceeded': ('monitoring', 'routine', None),
}
REJECTION_CODES = frozenset((
    'protocol_frame', 'protocol_json', 'protocol_envelope', 'protocol_id',
    'protocol_params', 'protocol_notification', 'protocol_metadata',
    'protocol_initialize', 'protocol_discovery', 'protocol_call',
    'tool_execution_rejected', 'protocol_response',
))
TOOLS = [
    {'name': 'prepare',
     'description': 'Read one frozen aggregate of real recorded history for a private review. No live device access.',
     'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
     'annotations': {'readOnlyHint': False, 'destructiveHint': False, 'openWorldHint': False}},
    {'name': 'publish',
     'description': 'Save this review privately on the administrator PC only. Nothing is published to the dashboard. One identical retry is safe.',
     'inputSchema': {'type': 'object', 'properties': {
         'analysis_id': {'type': 'string', 'pattern': '^[a-f0-9]{64}$'},
         'summary': text_schema(600),
         'notes': {'type': 'array', 'maxItems': 6, 'items': {'type': 'object', 'properties': {
             'finding_id': {'type': 'string', 'maxLength': 80}, 'commentary': text_schema(400)},
             'required': ['finding_id', 'commentary'], 'additionalProperties': False}}},
         'required': ['analysis_id', 'summary', 'notes'], 'additionalProperties': False},
     'annotations': {'readOnlyHint': False, 'destructiveHint': False,
                     'idempotentHint': True, 'openWorldHint': False}},
]


def _number(value, minimum, maximum, nullable=False):
    if value is None and nullable:
        return
    require(type(value) in (int, float))
    try:
        require(math.isfinite(value) and minimum <= value <= maximum)
    except OverflowError:
        require(False)


def _timestamp(value):
    require(type(value) is int and 0 <= value <= 32_503_680_000)


def validate_snapshot(envelope, now=None, fresh=False):
    """Reject arbitrary fields, synthetic provenance and malformed aggregates."""
    require(type(envelope) is dict and set(envelope) == {
        'schema_version', 'scope', 'synthetic', 'captured_at', 'analysis_id', 'analysis'})
    require(len(encode(envelope)) <= MAX_SNAPSHOT)
    require(type(envelope['schema_version']) is int and envelope['schema_version'] == 1)
    require(envelope['scope'] == 'private_review' and envelope['synthetic'] is False)
    _timestamp(envelope['captured_at'])
    analysis = envelope['analysis']
    require(type(analysis) is dict and set(analysis) == {
        'schema_version', 'generated_at', 'window', 'baseline', 'data_through',
        'coverage_pct', 'severity', 'metrics', 'findings', 'limitations'})
    body = encode(analysis)
    require(len(body) <= MAX_ANALYSIS)
    require(type(envelope['analysis_id']) is str
            and envelope['analysis_id'] == hashlib.sha256(body).hexdigest())
    require(type(analysis['schema_version']) is int and analysis['schema_version'] == 2)
    end = analysis['generated_at']
    _timestamp(end)
    require(end >= 8 * 86400 and end % 60 == 0)
    start = end - 86400
    require(type(analysis['window']) is dict and set(analysis['window']) == {'start', 'end'})
    require(type(analysis['baseline']) is dict
            and set(analysis['baseline']) == {'start', 'end', 'days'})
    for value in (*analysis['window'].values(), *analysis['baseline'].values()):
        require(type(value) is int)
    require(analysis['window'] == {'start': start, 'end': end})
    require(analysis['baseline'] == {'start': start - 7 * 86400, 'end': start, 'days': 7})
    require(0 <= envelope['captured_at'] - end <= 120)
    through = analysis['data_through']
    if through is not None:
        _timestamp(through)
        require(start - 7 * 86400 < through <= end and through % 60 == 0)
    _number(analysis['coverage_pct'], 0, 100)
    require(type(analysis['severity']) is str and analysis['severity'] in SEVERITIES)
    metrics = analysis['metrics']
    require(type(metrics) is list and len(metrics) == len(METRIC_KEYS))
    for metric, key in zip(metrics, METRIC_KEYS):
        require(type(metric) is dict and set(metric) == {
            'key', 'label', 'unit', 'value', 'baseline', 'change_pct', 'coverage_pct', 'baseline_days'})
        require(metric['key'] == key and metric['unit'] == ('%' if key == 'soc_max' else 'kWh'))
        localized_text(metric['label'], 200)
        maximum = 100 if key == 'soc_max' else 24_000_000
        _number(metric['value'], 0, maximum, nullable=True)
        _number(metric['baseline'], 0, maximum, nullable=True)
        _number(metric['change_pct'], -100, 1e15, nullable=True)
        _number(metric['coverage_pct'], 0, 100)
        require(type(metric['baseline_days']) is int and 0 <= metric['baseline_days'] <= 7)
        require((metric['baseline'] is None) == (metric['baseline_days'] < 4))
        require((metric['value'] is None) == (metric['coverage_pct'] == 0))
        if metric['change_pct'] is not None:
            require(metric['value'] is not None and metric['baseline'] is not None
                    and metric['coverage_pct'] >= 90)
    require(analysis['coverage_pct'] == min(item['coverage_pct'] for item in metrics))
    findings = analysis['findings']
    require(type(findings) is list and 1 <= len(findings) <= 6)
    ids = set()
    for item in findings:
        require(type(item) is dict and set(item) == {
            'id', 'category', 'severity', 'title', 'observation', 'start', 'end', 'metric'})
        key = item['id']
        require(type(key) is str and key in FINDINGS and key not in ids)
        ids.add(key)
        category, severity, metric = FINDINGS[key]
        require(item['category'] == category and item['metric'] == metric)
        require(item['severity'] == severity if severity else item['severity'] in ('notice', 'warning'))
        _timestamp(item['start'])
        _timestamp(item['end'])
        require(start <= item['start'] < item['end'] <= end)
        require(item['start'] % 60 == 0 and item['end'] % 60 == 0)
        localized_text(item['title'], 300)
        localized_text(item['observation'], 1200)
    require(analysis['severity'] == max((item['severity'] for item in findings), key=SEVERITIES.get))
    require(type(analysis['limitations']) is list and 6 <= len(analysis['limitations']) <= 7)
    for item in analysis['limitations']:
        localized_text(item, 1200)
    if fresh:
        current = time.time() if now is None else now
        _number(current, 0, 32_503_680_000)
        # Five seconds of clock skew do not extend the 30-minute lifetime.
        require(-5 <= current - envelope['captured_at'] <= 1800)
    return copy.deepcopy(envelope)


class Reports:
    """One prepare, one immutable private save, at most one identical retry."""
    def __init__(self, store_factory, token):
        self._factory = store_factory
        self._token = token
        self._store = None
        self._prepared = None
        self._snapshot_hash = None
        self._pending = None
        self._published = 0
        self._saved = None
        self._failed = False
        self._closed = False
        self.rejections = []

    def _gate(self):
        require(not self._closed and not self._failed)
        require(type(self._token) is str and self._token != CLOSED_TOKEN
                and re.fullmatch('[a-f0-9]{64}', self._token))
        if self._store is None:
            self._store = self._factory() if callable(self._factory) else self._factory
        self._store.active(self._token, armed=True)

    def call(self, name, arguments):
        self._gate()
        method, body = request(encode({'method': name, 'payload': arguments}))
        if method == 'prepare':
            require(self._prepared is None)
            self._store.claim(self._token)
            snapshot = validate_snapshot(self._store.read('snapshot'), fresh=True)
            self._snapshot_hash = hashlib.sha256(encode(snapshot)).hexdigest()
            require(self._store.read('receipt')['snapshot_sha256'] == self._snapshot_hash)
            self._prepared = {'scope': 'private_review', 'synthetic': False,
                              'result': {'analysis_id': snapshot['analysis_id'], 'analysis': snapshot['analysis']}}
            self._store.record('prepared')
            return copy.deepcopy(self._prepared)
        require(self._prepared is not None and self._published < 2)
        self._published += 1
        identifier = self._prepared['result']['analysis_id']
        require(arguments['analysis_id'] == identifier)
        narrative(arguments, self._prepared['result']['analysis'])
        require(self._pending is None or self._pending == body)
        self._pending = body  # A persistence/acknowledgement failure never authorizes replacement.
        if self._saved is not None:
            require(self._store.read('result') == self._saved)
            return {'scope': 'private_review', 'synthetic': False,
                    'result': {'status': 'already_saved_private', 'id': identifier}}
        acknowledgement = {'scope': 'private_review', 'synthetic': False,
                           'result': {'status': 'saved_private', 'id': identifier}}
        result = {'schema_version': 1, 'scope': 'private_review', 'synthetic': False,
                  'snapshot_sha256': self._snapshot_hash, 'analysis': self._prepared,
                  'submission': decode(body, MAX_REQUEST), 'acknowledgement': acknowledgement}
        require(len(encode(result)) <= MAX_RESPONSE)
        self._store.active(self._token, armed=True)
        self._store.create('result', result)
        require(self._store.read('result') == result)
        self._saved = result
        self._store.record('saved')
        return copy.deepcopy(acknowledgement)

    def reject(self, code, attempt, method):
        """First rejection only; fixed metadata stays in private evidence."""
        require(code in REJECTION_CODES and type(attempt) is int and 1 <= attempt <= 64
                and method in ('prepare', 'publish', 'unknown'))
        first = not self._failed
        self._failed = True
        if first:
            self.rejections.append({
                'timestamp_utc': datetime.now(timezone.utc).isoformat(),
                'attempt': attempt, 'method': method, 'code': code})
            if self._store is not None:
                self._store.record('failed')

    def close(self):
        self._closed = True
        if self._store is not None:
            self._store.close()


def _metadata(params, method):
    if '_meta' not in params:
        return params
    metadata = params['_meta']
    allowed = TOOL_METADATA_FIELDS if method == 'tools/call' else {'progressToken'}
    require(type(metadata) is dict and 'progressToken' in metadata and set(metadata) <= allowed)
    token = metadata['progressToken']
    require(type(token) is int and abs(token) < 2**53 or
            type(token) is str and re.fullmatch('[A-Za-z0-9_-]{1,128}', token))
    if 'threadId' in metadata:
        require(type(metadata['threadId']) is str
                and re.fullmatch('[A-Za-z0-9_-]{1,128}', metadata['threadId']))
    for field in CONTEXT_METADATA_FIELDS & metadata.keys():
        context = metadata[field]
        require(type(context) is str and len(context) <= MAX_CONTEXT_LENGTH
                and all(' ' <= character <= '~' for character in context))
    if 'x-codex-turn-metadata' in metadata:
        validate_turn_metadata(metadata['x-codex-turn-metadata'])
    return {key: value for key, value in params.items() if key != '_meta'}


def serve(source, destination, reports):
    """64 bounded newline JSON-RPC frames; malformed requests fail closed."""
    initialized = False
    for attempt in range(1, 65):
        raw = source.readline(MAX_FRAME + 1)
        if not raw:
            return
        identifier = None
        event_method = 'unknown'
        code = 'protocol_frame'
        rejected = False
        try:
            require(len(raw) <= MAX_FRAME and raw.endswith(b'\n'))
            code = 'protocol_json'
            incoming = decode(raw, MAX_FRAME)
            code = 'protocol_envelope'
            require(type(incoming) is dict and set(incoming) <= {'jsonrpc', 'id', 'method', 'params'}
                    and incoming.get('jsonrpc') == '2.0')
            candidate = incoming.get('id')
            code = 'protocol_id'
            require(type(candidate) is int and 0 <= candidate <= 2**53 or
                    type(candidate) is str and re.fullmatch('[A-Za-z0-9_-]{1,64}', candidate)
                    or candidate is None)
            identifier = candidate
            code = 'protocol_params'
            method, params = incoming.get('method'), incoming.get('params', {})
            require(type(method) is str and type(params) is dict)
            if identifier is None:
                code = 'protocol_notification'
                require(initialized and method == 'notifications/initialized' and not params)
                continue
            code = 'protocol_metadata'
            params = _metadata(params, method)
            if method == 'initialize':
                code = 'protocol_initialize'
                require(not initialized and set(params) <= {'protocolVersion', 'capabilities', 'clientInfo'}
                        and params.get('protocolVersion') in ('2024-11-05', '2025-03-26', '2025-06-18'))
                initialized = True
                result = {'protocolVersion': params['protocolVersion'], 'capabilities': {'tools': {}},
                          'serverInfo': {'name': SERVER, 'version': '0.1.0'}}
            elif method in ('ping', 'tools/list'):
                code = 'protocol_discovery'
                require(initialized and not params)
                result = {} if method == 'ping' else {'tools': copy.deepcopy(TOOLS)}
            else:
                code = 'protocol_call'
                require(initialized and method == 'tools/call' and set(params) == {'name', 'arguments'}
                        and type(params['name']) is str and params['name'] in ('prepare', 'publish'))
                event_method = params['name']
                code = 'tool_execution_rejected'
                payload = reports.call(event_method, params['arguments'])
                code = 'protocol_response'
                require(len(encode(payload)) <= MAX_RESPONSE)
                result = {'content': [{'type': 'text', 'text': encode(payload).decode('utf-8')}]}
            response = {'jsonrpc': '2.0', 'id': identifier, 'result': result}
        except (ValueError, TypeError, KeyError, AttributeError, OSError, RecursionError):
            response = {'jsonrpc': '2.0', 'id': identifier,
                        'error': {'code': -32600, 'message': ERROR}}
            reports.reject(code, attempt, event_method)
            rejected = True
        destination.write(encode(response) + b'\n')
        destination.flush()
        if rejected:
            return
