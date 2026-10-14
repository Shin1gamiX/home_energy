"""Two fixed functions for a future isolated runner's tool adapter.

No credentials, server address, shell, configurable paths, model API or schedule.
The adapter must expose only these operations, not Python execution/imports.
"""
import json
import socket
import struct
import time


SOCKET_PATH = '/run/homeenergy-report-broker.sock'
MAX_REQUEST = 32896
MAX_RESPONSE = 65536


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Invalid report broker response')
        result[key] = value
    return result


def _invalid_number(value):
    raise ValueError('Invalid report broker response')


def _receive(connection, size, deadline):
    result = bytearray()
    while len(result) < size:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Report broker timed out')
        connection.settimeout(remaining)
        chunk = connection.recv(size - len(result))
        if not chunk:
            raise ValueError('Incomplete report broker response')
        result.extend(chunk)
    return bytes(result)


def _call(method, payload):
    raw = json.dumps({'method': method, 'payload': payload}, ensure_ascii=False,
                     allow_nan=False, separators=(',', ':')).encode('utf-8')
    if not 0 < len(raw) <= MAX_REQUEST:
        raise ValueError('Report request too large')
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(5)
        connection.connect(SOCKET_PATH)
        connection.sendall(struct.pack('!I', len(raw)) + raw)
        connection.shutdown(socket.SHUT_WR)
        deadline = time.monotonic() + 60
        size = struct.unpack('!I', _receive(connection, 4, deadline))[0]
        if not 0 < size <= MAX_RESPONSE:
            raise ValueError('Invalid report broker response')
        body = _receive(connection, size, deadline)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Report broker timed out')
        connection.settimeout(remaining)
        if connection.recv(1) != b'':
            raise ValueError('Unexpected extra broker response')
        response = json.loads(body.decode('utf-8'), object_pairs_hook=_unique_pairs,
                              parse_constant=_invalid_number)
        if not isinstance(response, dict) or response.get('ok') is not True or set(response) != {'ok', 'result'}:
            raise ValueError('Report request rejected or temporarily unavailable')
        return response['result']


def prepare():
    return _call('prepare', {})


def publish(analysis_id, summary, notes):
    return _call('publish', {'analysis_id': analysis_id, 'summary': summary, 'notes': notes})
