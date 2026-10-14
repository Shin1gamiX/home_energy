"""Prepared report-only production adapter; no live launcher or transport.

The future trusted host must supply a bounded transport and enforce runtime,
credential, audit and approval gates. Tool arguments cannot select that transport.
Running this file directly always exits before importing any project module.
"""
import sys

if __name__ == '__main__':
    print('Production reporting integration is not activated.', file=sys.stderr)
    sys.exit(1)

import copy
from datetime import datetime, timezone
import hashlib
import math
import re
import threading
import time

from insights_broker import MAX_REQUEST, MAX_RESPONSE, decode, encode, request, require
from insights_publish import localized_text, narrative
from insights_private_review_mcp import _metadata
from insights_staging_mcp import text_schema


SERVER = 'home_energy_reports'
ERROR = 'Production report request rejected or temporarily unavailable'
MAX_FRAME = MAX_REQUEST + 8192
MAX_ANALYSIS = 32768
MAX_CALLS = 4
MAX_PUBLISH_CALLS = 2
SESSION_SECONDS = 240
SNAPSHOT_SECONDS = 1800
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
     'description': 'Prepare a frozen review of real recorded energy history. This creates a private server-side analysis snapshot but does not publish a report or control devices.',
     'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
     'annotations': {'readOnlyHint': False, 'destructiveHint': False, 'openWorldHint': False}},
    {'name': 'publish',
     'description': 'Publish one translated energy report to the public dashboard. Existing reports cannot be overwritten. Only an identical submission retry is allowed.',
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


def validate_analysis(analysis, identifier, now):
    """Validate the schema-2 aggregate itself without changing its provenance.

    These independent checks intentionally leave the successful private-review
    and synthetic validators untouched. Source hashes pin this contract later.
    Missing values are distinct from zero; labels and findings require all locales.
    """
    require(type(analysis) is dict and set(analysis) == {
        'schema_version', 'generated_at', 'window', 'baseline', 'data_through',
        'coverage_pct', 'severity', 'metrics', 'findings', 'limitations'})
    body = encode(analysis)
    require(len(body) <= MAX_ANALYSIS)
    require(type(identifier) is str and identifier == hashlib.sha256(body).hexdigest())
    require(type(analysis['schema_version']) is int and analysis['schema_version'] == 2)
    end = analysis['generated_at']
    _timestamp(end)
    _number(now, 0, 32_503_680_000)
    require(end >= 8 * 86400 and end % 60 == 0 and -5 <= now - end <= SNAPSHOT_SECONDS)
    start = end - 86400
    require(type(analysis['window']) is dict and set(analysis['window']) == {'start', 'end'})
    require(type(analysis['baseline']) is dict
            and set(analysis['baseline']) == {'start', 'end', 'days'})
    for value in (*analysis['window'].values(), *analysis['baseline'].values()):
        require(type(value) is int)
    require(analysis['window'] == {'start': start, 'end': end})
    require(analysis['baseline'] == {'start': start - 7 * 86400, 'end': start, 'days': 7})
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


def validate_response(raw, method, identifier=None, *, now=None):
    require(method in ('prepare', 'publish'))
    value = decode(raw, MAX_RESPONSE)
    require(type(value) is dict and set(value) == {'scope', 'synthetic', 'result'})
    require(value['scope'] == 'production' and value['synthetic'] is False)
    result = value['result']
    require(type(result) is dict)
    if method == 'prepare':
        require(identifier is None and set(result) == {'analysis_id', 'analysis'})
        validate_analysis(result['analysis'], result['analysis_id'], time.time() if now is None else now)
    else:
        require(type(identifier) is str and re.fullmatch('[a-f0-9]{64}', identifier))
        require(set(result) == {'status', 'id'} and result['id'] == identifier
                and result['status'] in ('published', 'already_published'))
    return value


class Reports:
    """One frozen analysis; bounded sends; never replace an uncertain submission.

    The injected transport owns its deadline, destination, credentials and audit.
    This library alone is not runtime isolation or authorization to publish.
    A protocol failure closes the stream, even where an identical retry would
    be permitted by this lower-level session contract.
    """
    def __init__(self, transport, *, clock=time.time, monotonic=time.monotonic):
        require(callable(transport) and callable(clock) and callable(monotonic))
        self._transport = transport
        self._clock = clock
        self._monotonic = monotonic
        self._started = monotonic()
        _number(self._started, 0, float('inf'))
        self._prepared = None
        self._pending = None
        self._calls = 0
        self._published = 0
        self._failed = False
        self._closed = False
        self._lock = threading.Lock()
        self.rejections = []

    def _gate(self):
        require(not self._closed and not self._failed)
        current = self._monotonic()
        _number(current, 0, float('inf'))
        require(0 <= current - self._started <= SESSION_SECONDS)
        if self._prepared is not None:
            value = self._prepared['result']
            validate_analysis(value['analysis'], value['analysis_id'], self._clock())

    def call(self, name, arguments):
        with self._lock:
            self._gate()
            method, body = request(encode({'method': name, 'payload': arguments}))
            identifier = None
            if method == 'prepare':
                if self._prepared is not None:
                    return copy.deepcopy(self._prepared)
            else:
                require(self._prepared is not None and self._published < MAX_PUBLISH_CALLS)
                value = self._prepared['result']
                identifier = value['analysis_id']
                require(arguments['analysis_id'] == identifier)
                narrative(arguments, value['analysis'])
                require(self._pending is None or self._pending == body)
            require(self._calls < MAX_CALLS)
            self._calls += 1
            if method == 'publish':
                self._published += 1
                # A partial send or lost reply may already have published publicly.
                self._pending = body
            raw = self._transport(method, body)
            try:
                self._gate()
                result = validate_response(raw, method, identifier, now=self._clock())
            except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
                # Invalid provenance/data is not an ambiguous network failure.
                # Never recover by silently preparing or sending another report.
                self._failed = True
                raise
            if method == 'prepare':
                self._prepared = result
            return copy.deepcopy(result)

    def reject(self, code, attempt, method):
        require(code in REJECTION_CODES and type(attempt) is int and 1 <= attempt <= 64
                and method in ('prepare', 'publish', 'unknown'))
        with self._lock:
            if not self._failed:
                self.rejections.append({'timestamp_utc': datetime.now(timezone.utc).isoformat(),
                                        'attempt': attempt, 'method': method, 'code': code})
            self._failed = True

    def close(self):
        with self._lock:
            self._closed = True


def serve(source, destination, reports):
    """Bounded protocol only; discovery never constructs a transport or reads data."""
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
