"""Report-only Unix-socket broker; no model-supplied commands or destinations.

Run only through the reviewed socket-activated Linux unit, under its own UID.
The model runner is a different UID and cannot read the SSH credential. This
module does not provision accounts, start services, or schedule model calls.
"""
from dataclasses import dataclass
import ipaddress
import json
import os
from pathlib import Path
import re
import selectors
import socket
import stat
import struct
import subprocess
import sys
import time


CONFIG = Path('/etc/homeenergy-report-broker/config.json')
KNOWN_HOSTS = Path('/etc/homeenergy-report-broker/known_hosts')
RUNNER_USER = 'homeenergy-report-runner'
BROKER_USER = 'homeenergy-report-broker'
SOCKET_PATH = '/run/homeenergy-report-broker.sock'
MAX_PAYLOAD = 32768
MAX_REQUEST = MAX_PAYLOAD + 128
MAX_RESPONSE = 65536
FRAME_SECONDS = 5
SSH_SECONDS = 50
GENERIC_ERROR = {'ok': False, 'error': 'Report request rejected or temporarily unavailable'}


class Rejected(ValueError):
    """No request text or upstream diagnostics should reach the caller or logs."""


def require(condition):
    if not condition:
        raise Rejected('Report request rejected')


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False,
                      sort_keys=True, separators=(',', ':')).encode('utf-8')


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


def decode(body, limit):
    require(isinstance(body, bytes) and 0 < len(body) <= limit)
    try:
        return json.loads(body.decode('utf-8'), object_pairs_hook=unique_pairs,
                          parse_constant=lambda _: require(False))
    except (UnicodeError, ValueError, RecursionError):
        raise Rejected('Report request rejected') from None


def request(body):
    value = decode(body, MAX_REQUEST)
    require(isinstance(value, dict) and set(value) == {'method', 'payload'})
    method, payload = value['method'], value['payload']
    require(isinstance(method, str) and method in ('prepare', 'publish'))
    require(isinstance(payload, dict))
    if method == 'prepare':
        require(payload == {})
        return method, b''
    require(set(payload) == {'analysis_id', 'summary', 'notes'})
    require(isinstance(payload['analysis_id'], str) and
            re.fullmatch(r'[a-f0-9]{64}', payload['analysis_id']))
    require(isinstance(payload['summary'], dict) and set(payload['summary']) == {'en', 'el', 'ru'})
    require(isinstance(payload['notes'], list) and len(payload['notes']) <= 6)
    # The publisher remains authoritative for text, translations and finding IDs.
    raw = encode(payload)
    require(len(raw) <= MAX_PAYLOAD)
    return method, raw


def trusted_file(path, private=False):
    """Reject symlink paths, mutable ancestors and unexpectedly readable keys."""
    require(path.is_absolute() and path.resolve() == path)
    allowed_owners = (0, os.geteuid()) if private else (0,)
    for ancestor in path.parents:
        info = ancestor.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid in allowed_owners and
                not info.st_mode & 0o022)
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid in allowed_owners and info.st_nlink == 1)
    require(not info.st_mode & (0o377 if private else 0o022))
    require(0 < info.st_size <= 16384)
    return path


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    known_hosts: Path
    key: Path


def load_settings():
    raw = trusted_file(CONFIG).read_bytes()
    value = decode(raw, 4096)
    require(isinstance(value, dict) and set(value) == {'host', 'port'})
    require(isinstance(value['host'], str))
    address = ipaddress.ip_address(value['host'])
    require(str(address) == value['host'] and '%' not in value['host'])
    require(type(value['port']) is int and 1 <= value['port'] <= 65535)
    credential_dir = Path(os.environ.get('CREDENTIALS_DIRECTORY', ''))
    require(credential_dir.is_absolute() and credential_dir.parent == Path('/run/credentials'))
    key = trusted_file(credential_dir / 'report-key', private=True)
    pin = trusted_file(KNOWN_HOSTS)
    # One plain Ed25519 host pin, no wildcard hosts or certificate authorities.
    fields = pin.read_text(encoding='ascii').strip().split()
    hostname = value['host'] if value['port'] == 22 else '[%s]:%d' % (value['host'], value['port'])
    require(len(fields) == 3 and fields[0] == hostname and fields[1] == 'ssh-ed25519' and
            re.fullmatch(r'[A-Za-z0-9+/]+={0,2}', fields[2]))
    return Settings(value['host'], value['port'], pin, key)


def ssh_command(settings, method):
    require(method in ('prepare', 'publish'))
    return ['/usr/bin/ssh', '-F', '/dev/null', '-T', '-p', str(settings.port),
            '-i', str(settings.key), '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
            '-o', 'IdentityAgent=none', '-o', 'AddKeysToAgent=no',
            '-o', 'StrictHostKeyChecking=yes', '-o', 'UpdateHostKeys=no',
            '-o', 'UserKnownHostsFile=' + str(settings.known_hosts),
            '-o', 'GlobalKnownHostsFile=/dev/null', '-o', 'HostKeyAlgorithms=ssh-ed25519',
            '-o', 'PasswordAuthentication=no', '-o', 'KbdInteractiveAuthentication=no',
            '-o', 'PreferredAuthentications=publickey', '-o', 'GSSAPIAuthentication=no',
            '-o', 'ForwardAgent=no', '-o', 'ForwardX11=no', '-o', 'ClearAllForwardings=yes',
            '-o', 'ControlMaster=no', '-o', 'ControlPath=none', '-o', 'ProxyCommand=none',
            '-o', 'ProxyJump=none', '-o', 'PermitLocalCommand=no', '-o', 'RequestTTY=no',
            '-o', 'ConnectionAttempts=1', '-o', 'ConnectTimeout=10',
            '-o', 'ServerAliveInterval=10', '-o', 'ServerAliveCountMax=2',
            '-o', 'LogLevel=ERROR', 'homeenergy-reporter@' + settings.host, method]


def bounded_process(argv, body, seconds=SSH_SECONDS):
    """Linux pipe pump: bounded stdout/stderr and one deadline, including stdin."""
    deadline = time.monotonic() + seconds
    process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, shell=False, cwd='/', close_fds=True,
                               env={'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C'})
    output = bytearray()
    errors = 0
    sent = 0
    try:
        with selectors.DefaultSelector() as selector:
            for pipe in (process.stdin, process.stdout, process.stderr):
                os.set_blocking(pipe.fileno(), False)
            selector.register(process.stdout, selectors.EVENT_READ, 'output')
            selector.register(process.stderr, selectors.EVENT_READ, 'error')
            if body:
                selector.register(process.stdin, selectors.EVENT_WRITE, 'input')
            else:
                process.stdin.close()
            while selector.get_map():
                remaining = deadline - time.monotonic()
                require(remaining > 0)
                for key, _ in selector.select(remaining):
                    if key.data == 'input':
                        sent += os.write(key.fd, body[sent:sent + 4096])
                        if sent == len(body):
                            selector.unregister(key.fileobj)
                            key.fileobj.close()
                        continue
                    chunk = os.read(key.fd, 4096)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                    elif key.data == 'output':
                        output.extend(chunk)
                        require(len(output) <= MAX_RESPONSE)
                    else:
                        errors += len(chunk)
                        require(errors <= 8192)
            require(process.wait(timeout=max(0.001, deadline - time.monotonic())) == 0)
        return bytes(output)
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        for pipe in (process.stdin, process.stdout, process.stderr):
            pipe.close()


def transport(settings, method, payload):
    value = decode(bounded_process(ssh_command(settings, method), payload), MAX_RESPONSE)
    require(isinstance(value, dict))
    if method == 'prepare':
        require(set(value) == {'analysis_id', 'analysis'} and isinstance(value['analysis'], dict))
        require(isinstance(value['analysis_id'], str) and re.fullmatch(r'[a-f0-9]{64}', value['analysis_id']))
    else:
        require(set(value) == {'status', 'id'} and value['status'] in ('published', 'already_published'))
        require(value['id'] == decode(payload, MAX_PAYLOAD)['analysis_id'])
    return value


def receive_exact(connection, count, deadline):
    data = bytearray()
    while len(data) < count:
        remaining = deadline - time.monotonic()
        require(remaining > 0)
        connection.settimeout(remaining)
        chunk = connection.recv(count - len(data))
        require(chunk)
        data.extend(chunk)
    return bytes(data)


def receive_frame(connection, limit, seconds):
    deadline = time.monotonic() + seconds
    size = struct.unpack('!I', receive_exact(connection, 4, deadline))[0]
    require(0 < size <= limit)
    body = receive_exact(connection, size, deadline)
    # Each connection carries one request/response. The sender must half-close.
    remaining = deadline - time.monotonic()
    require(remaining > 0)
    connection.settimeout(remaining)
    require(connection.recv(1) == b'')
    return body


def send_frame(connection, body):
    require(0 < len(body) <= MAX_RESPONSE)
    connection.settimeout(FRAME_SECONDS)
    connection.sendall(struct.pack('!I', len(body)) + body)
    connection.shutdown(socket.SHUT_WR)


def peer_uid(connection):
    require(sys.platform == 'linux' and connection.family == socket.AF_UNIX)
    return struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))[1]


def serve_connection(connection, allowed_uid, call, audit):
    """One request per systemd instance; injected transport is for isolated tests."""
    uid, method, outcome = -1, 'unknown', 'rejected'
    response = GENERIC_ERROR
    try:
        uid = peer_uid(connection)
        require(allowed_uid > 0 and uid == allowed_uid)
        method, body = request(receive_frame(connection, MAX_REQUEST, FRAME_SECONDS))
        response = {'ok': True, 'result': call(method, body)}
        require(len(encode(response)) <= MAX_RESPONSE)
        outcome = 'accepted'
    except (Rejected, OSError, ValueError, TypeError, KeyError, RecursionError, subprocess.SubprocessError):
        response = GENERIC_ERROR
    try:
        send_frame(connection, encode(response))
    except OSError:
        outcome = 'delivery_failed'
    finally:
        # No free text, payloads, keys, SSH stderr, addresses or credentials.
        audit({'event': 'report_broker', 'uid': uid, 'method': method if method in ('prepare', 'publish') else 'unknown',
               'outcome': outcome})


def main():
    import pwd
    require(sys.platform == 'linux' and len(sys.argv) == 1)
    require(os.geteuid() == pwd.getpwnam(BROKER_USER).pw_uid and os.geteuid() != 0)
    allowed_uid = pwd.getpwnam(RUNNER_USER).pw_uid
    require(allowed_uid != os.geteuid())
    settings = load_settings()
    with socket.fromfd(0, socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        serve_connection(connection, allowed_uid,
                         lambda method, body: transport(settings, method, body),
                         lambda event: print(encode(event).decode(), file=sys.stderr, flush=True))


if __name__ == '__main__':
    try:
        main()
    except (Rejected, OSError, ValueError, KeyError):
        print('{"event":"report_broker","outcome":"configuration_error"}', file=sys.stderr)
        sys.exit(1)
