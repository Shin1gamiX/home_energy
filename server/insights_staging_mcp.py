"""Two-operation stdio adapter, staging only; never a generic SSH or file tool.

Trusted launcher integration is deliberately not installed. The executable entry
point fails closed. Tests inject a synthetic transport into serve(); model tool
requests cannot select that transport or any destination, credential or path.
"""
import copy
from datetime import datetime, timezone
import hashlib
import re
import sys

if __name__ == '__main__':
    # Deliberately before project imports, including when invoked with python -I.
    print('Staging launcher integration is not activated.', file=sys.stderr)
    sys.exit(1)

from insights_broker import decode, encode, request, require, MAX_REQUEST, MAX_RESPONSE
from insights_publish import narrative


ERROR = 'Staging request rejected or temporarily unavailable'
MAX_FRAME = MAX_REQUEST + 1024
MAX_CALLS = 4
LANGUAGES = ('en', 'el', 'ru')
CONTEXT_METADATA_FIELDS = frozenset(('callId', 'itemId', 'sessionId', 'windowId'))
TOOL_METADATA_FIELDS = CONTEXT_METADATA_FIELDS | {'progressToken', 'threadId', 'x-codex-turn-metadata'}
MAX_CONTEXT_LENGTH = 256
MAX_TURN_METADATA_BYTES = 4096
MAX_TURN_METADATA_DEPTH = 4
MAX_TURN_METADATA_NODES = 64
MAX_TURN_METADATA_ITEMS = 16
MAX_TURN_METADATA_KEY = 128
MAX_TURN_METADATA_STRING = 1024
GATE_REJECTION_CODES = frozenset((
    'report_gate_closed', 'gate_root_rejected', 'gate_receipt_rejected',
    'gate_expired', 'gate_terminal', 'gate_armed_rejected', 'gate_claim_rejected',
    'gate_audit_rejected', 'gate_recheck_rejected', 'gate_open_rejected',
    'gate_audit_setup_rejected', 'gate_session_setup_rejected',
    'report_session_failed', 'publication_attempts_exhausted',
    'tool_execution_rejected', 'private_result_persistence_failed',
))
REJECTION_CODES = GATE_REJECTION_CODES | frozenset((
    'protocol_frame', 'protocol_json', 'protocol_envelope', 'protocol_id',
    'protocol_params', 'protocol_notification', 'protocol_metadata_shape',
    'protocol_metadata_token', 'protocol_metadata_thread', 'protocol_metadata_context',
    'protocol_metadata_turn', 'protocol_initialize', 'protocol_version',
    'protocol_discovery', 'protocol_call_shape', 'protocol_tool_name',
    'protocol_response',
))


def validate_turn_metadata(value):
    """Bound opaque client context; nested fields have no trusted meaning."""
    require(type(value) is dict and len(encode(value)) <= MAX_TURN_METADATA_BYTES)
    pending = [(value, 1)]
    nodes = 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        require(nodes <= MAX_TURN_METADATA_NODES)
        if type(item) in (dict, list):
            require(depth <= MAX_TURN_METADATA_DEPTH and len(item) <= MAX_TURN_METADATA_ITEMS)
            if type(item) is dict:
                require(all(type(key) is str and len(key) <= MAX_TURN_METADATA_KEY for key in item))
                children = item.values()
            else:
                children = item
            pending.extend((child, depth + 1) for child in children)
        elif type(item) is str:
            require(len(item) <= MAX_TURN_METADATA_STRING)
        else:
            # encode() above also rejects non-finite floats and invalid Unicode.
            require(item is None or type(item) in (bool, int, float))


def text_schema(limit):
    return {'type': 'object', 'properties': {language: {'type': 'string', 'minLength': 1, 'maxLength': limit}
                                            for language in LANGUAGES},
            'required': list(LANGUAGES), 'additionalProperties': False}


TOOLS = [
    {'name': 'prepare', 'description': 'Prepare one synthetic staging review. No live history or public publishing.',
     'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
     'annotations': {'readOnlyHint': False, 'destructiveHint': False, 'openWorldHint': False}},
    {'name': 'publish', 'description': 'Append this review to the private staging feed only. Identical retries are safe.',
     'inputSchema': {'type': 'object', 'properties': {
         'analysis_id': {'type': 'string', 'pattern': '^[a-f0-9]{64}$'},
         'summary': text_schema(600),
         'notes': {'type': 'array', 'maxItems': 6, 'items': {'type': 'object', 'properties': {
             'finding_id': {'type': 'string', 'maxLength': 80}, 'commentary': text_schema(400)},
             'required': ['finding_id', 'commentary'], 'additionalProperties': False}}},
         'required': ['analysis_id', 'summary', 'notes'], 'additionalProperties': False},
     'annotations': {'readOnlyHint': False, 'destructiveHint': False, 'idempotentHint': True, 'openWorldHint': False}},
]


def validate_response(raw, method, identifier=None):
    value = decode(raw, MAX_RESPONSE)
    require(isinstance(value, dict) and set(value) == {'scope', 'synthetic', 'result'})
    require(value['scope'] == 'staging' and value['synthetic'] is True)
    result = value['result']
    require(isinstance(result, dict))
    if method == 'prepare':
        require(set(result) == {'analysis_id', 'analysis'})
        analysis = result['analysis']
        require(isinstance(analysis, dict) and set(analysis) == {
            'schema_version', 'generated_at', 'window', 'baseline', 'data_through',
            'coverage_pct', 'severity', 'metrics', 'findings', 'limitations'})
        require(type(analysis['schema_version']) is int and analysis['schema_version'] == 2)
        require(len(encode(analysis)) <= 32768)
        require(result['analysis_id'] == hashlib.sha256(encode(analysis)).hexdigest())
        require(isinstance(analysis['findings'], list) and len(analysis['findings']) <= 6)
        ids = []
        for finding in analysis['findings']:
            require(isinstance(finding, dict) and isinstance(finding.get('id'), str)
                    and re.fullmatch('[a-z][a-z0-9_]{0,79}', finding['id']))
            ids.append(finding['id'])
        require(len(set(ids)) == len(ids))
    else:
        require(method == 'publish' and set(result) == {'status', 'id'})
        require(result['status'] in ('staged', 'already_staged') and result['id'] == identifier)
    return value


class StagingReports:
    """One frozen analysis/session; only identical submissions after a send."""
    def __init__(self, transport):
        self._transport = transport
        self._prepared = None
        self._pending = None
        self._calls = 0

    def call(self, name, arguments):
        method, body = request(encode({'method': name, 'payload': arguments}))
        identifier = None
        if method == 'prepare':
            if self._prepared is not None:
                return copy.deepcopy(self._prepared)
        else:
            require(self._prepared is not None)
            snapshot = self._prepared['result']
            identifier = arguments['analysis_id']
            require(identifier == snapshot['analysis_id'])
            narrative(arguments, snapshot['analysis'])
            require(self._pending is None or body == self._pending)
        require(self._calls < MAX_CALLS)
        self._calls += 1
        if method == 'publish':
            # Even a lost acknowledgement may mean publication succeeded.
            self._pending = body
        value = validate_response(self._transport(method, body), method, identifier)
        if method == 'prepare':
            self._prepared = value
        return copy.deepcopy(value)


def serve(source, destination, reports, audit, rejection=None):
    """Bounded newline JSON-RPC; audit contains only fixed fields, never prose."""
    initialized = False
    for attempt in range(1, 65):
        raw = source.readline(MAX_FRAME + 1)
        if not raw:
            return
        identifier = None
        valid_identifier = None
        event_method = 'unknown'
        outcome = 'rejected'
        rejection_code = 'protocol_frame'
        fatal = len(raw) > MAX_FRAME or not raw.endswith(b'\n')
        try:
            require(not fatal)
            rejection_code = 'protocol_json'
            incoming = decode(raw, MAX_FRAME)
            rejection_code = 'protocol_envelope'
            require(isinstance(incoming, dict) and set(incoming) <= {'jsonrpc', 'id', 'method', 'params'})
            require(incoming.get('jsonrpc') == '2.0')
            identifier = incoming.get('id')
            rejection_code = 'protocol_id'
            require(type(identifier) is int and 0 <= identifier <= 2**53 or
                    isinstance(identifier, str) and re.fullmatch('[A-Za-z0-9_-]{1,64}', identifier) or
                    identifier is None)
            valid_identifier = identifier
            method, params = incoming.get('method'), incoming.get('params', {})
            rejection_code = 'protocol_params'
            require(isinstance(method, str) and isinstance(params, dict))
            if identifier is None:
                rejection_code = 'protocol_notification'
                require(initialized and method == 'notifications/initialized' and not params)
                continue
            if '_meta' in params:
                # Only observed top-level client context is accepted. It is
                # never identity, authority or a gate token. Discard all of it
                # before dispatch; opaque nested fields have no semantics here.
                metadata = params['_meta']
                rejection_code = 'protocol_metadata_shape'
                allowed_metadata = TOOL_METADATA_FIELDS if method == 'tools/call' else {'progressToken'}
                require(isinstance(metadata, dict) and 'progressToken' in metadata
                        and set(metadata) <= allowed_metadata)
                token = metadata['progressToken']
                rejection_code = 'protocol_metadata_token'
                require(type(token) is int and abs(token) < 2**53 or
                        isinstance(token, str) and re.fullmatch('[A-Za-z0-9_-]{1,128}', token))
                if 'threadId' in metadata:
                    rejection_code = 'protocol_metadata_thread'
                    require(type(metadata['threadId']) is str
                            and re.fullmatch('[A-Za-z0-9_-]{1,128}', metadata['threadId']))
                rejection_code = 'protocol_metadata_context'
                for field in CONTEXT_METADATA_FIELDS & metadata.keys():
                    context = metadata[field]
                    require(type(context) is str and len(context) <= MAX_CONTEXT_LENGTH
                            and all(' ' <= character <= '~' for character in context))
                if 'x-codex-turn-metadata' in metadata:
                    rejection_code = 'protocol_metadata_turn'
                    validate_turn_metadata(metadata['x-codex-turn-metadata'])
                params = {key: value for key, value in params.items() if key != '_meta'}
            if method == 'initialize':
                rejection_code = 'protocol_initialize'
                require(not initialized and set(params) <= {'protocolVersion', 'capabilities', 'clientInfo'})
                rejection_code = 'protocol_version'
                require(params.get('protocolVersion') in ('2024-11-05', '2025-03-26', '2025-06-18'))
                initialized = True
                result = {'protocolVersion': params['protocolVersion'], 'capabilities': {'tools': {}},
                          'serverInfo': {'name': 'home-energy-staging', 'version': '0.1.0'}}
            elif method in ('ping', 'tools/list'):
                rejection_code = 'protocol_discovery'
                require(initialized and not params)
                result = {} if method == 'ping' else {'tools': TOOLS}
            else:
                rejection_code = 'protocol_call_shape'
                require(initialized and method == 'tools/call' and set(params) == {'name', 'arguments'})
                rejection_code = 'protocol_tool_name'
                require(isinstance(params['name'], str) and params['name'] in ('prepare', 'publish'))
                event_method = params['name']
                rejection_code = 'tool_execution_rejected'
                payload = reports.call(event_method, params['arguments'])
                rejection_code = 'protocol_response'
                result = {'content': [{'type': 'text', 'text': encode(payload).decode('utf-8')}]}
            response = {'jsonrpc': '2.0', 'id': identifier, 'result': result}
            outcome = 'accepted'
        except (ValueError, TypeError, KeyError, AttributeError, OSError, RecursionError):
            # Never reflect malformed IDs, SSH stderr, submitted prose or paths.
            response = {'jsonrpc': '2.0', 'id': valid_identifier, 'error': {'code': -32600, 'message': ERROR}}
            if rejection_code == 'tool_execution_rejected':
                internal_code = getattr(reports, 'rejection_code', None)
                if type(internal_code) is str and internal_code in GATE_REJECTION_CODES:
                    rejection_code = internal_code
            if rejection is not None:
                # This is a trusted host callback, never MCP output. A failed
                # diagnostic sink stops the adapter; no fallback/retry occurs.
                rejection({'timestamp_utc': datetime.now(timezone.utc).isoformat(),
                           'attempt': attempt, 'method': event_method, 'code': rejection_code})
        audit({'timestamp_utc': datetime.now(timezone.utc).isoformat(), 'event': 'staging_adapter',
               'attempt': attempt, 'method': event_method, 'outcome': outcome})
        destination.write(encode(response) + b'\n')
        destination.flush()
        if fatal:
            return

