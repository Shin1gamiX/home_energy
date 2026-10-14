"""Exclusive, bounded evidence for one future authorized public-report trial.

The authorization is read-only here and must be installed separately. No old
evidence is overwritten, no secrets are logged, and no receipt can be reset.
"""
from datetime import datetime, timezone
import hashlib
import hmac
import os
from pathlib import Path
import re
import secrets
import stat
import time
from types import MappingProxyType

from insights_broker import decode, encode
from insights_staging_run_audit import _check_handle_acl
import insights_production_connection as connection


ROOT = connection.ROOT
FILES = MappingProxyType({name: ROOT / ('public-trial-01-' + name + ('.jsonl' if name == 'audit' else '.json'))
    for name in ('authorization', 'receipt', 'worker', 'armed', 'model', 'claim', 'terminal', 'audit',
                 'analysis', 'submission', 'result', 'diagnostic', 'storage-probe')})
MARKERS = frozenset(('receipt', 'worker', 'armed', 'model', 'claim', 'terminal'))
ARTIFACTS = frozenset(('analysis', 'submission', 'result', 'diagnostic'))
EVENTS = frozenset(('reserved', 'worker_claimed', 'runtime_verified', 'armed', 'model_started', 'model_completed',
    'mcp_claimed', 'transport_started', 'transport_returned', 'transport_failed',
    'analysis_saved', 'submission_saved', 'publication_acknowledged', 'protocol_rejected',
    'failed', 'closed'))
CODES = frozenset(('none', 'failed', 'timeout', 'uncertain')) | frozenset((
    'protocol_frame', 'protocol_json', 'protocol_envelope', 'protocol_id', 'protocol_params',
    'protocol_notification', 'protocol_metadata', 'protocol_initialize', 'protocol_discovery',
    'protocol_call', 'tool_execution_rejected', 'protocol_response'))
MAX_SECONDS = 360
MAX_DATA = 65536
MAX_AUDIT = 32768
MAX_EVENTS = 96
PROBE_BODY = {'schema_version': 1, 'scope': 'synthetic_storage_probe', 'model_started': False,
              'public_publishing_enabled': False}


def require(condition, code='production_trial_store_rejected'):
    if not condition:
        raise ValueError(code)


def digest(value):
    return hashlib.sha256(encode(value)).hexdigest()


def hash_value(value):
    require(type(value) is str and re.fullmatch('[a-f0-9]{64}', value))
    return value


def token_hash(token):
    require(hash_value(token) != '0' * 64, 'production_trial_not_authorized')
    return hashlib.sha256(token.encode('ascii')).hexdigest()


def _root_check():
    connection.verify_permissions()
    require(ROOT.is_absolute() and ROOT.resolve(strict=True) == ROOT and stat.S_ISDIR(ROOT.stat().st_mode))
    for item in (ROOT, *ROOT.parents):
        require(not getattr(item.lstat(), 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _path(kind):
    require(type(kind) is str and kind in FILES)
    path = FILES[kind]
    require(path.parent == ROOT and path.name == str(path.relative_to(ROOT)))
    return path


def _open(kind, *, create=False, append=False):
    """Fixed child, exclusive handle, CreateNew writes, canonical path and DACL."""
    require(os.name == 'nt' and not (create and append))
    require(not create or kind != 'authorization')
    require(not append or kind == 'audit')
    path = _path(kind)
    require(ROOT.resolve(strict=True) == ROOT)
    for item in (ROOT, *ROOT.parents):
        require(not getattr(item.lstat(), 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    import ctypes
    from ctypes import wintypes as wt
    import msvcrt
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p, wt.DWORD, wt.DWORD, wt.HANDLE]
    kernel.CreateFileW.restype = wt.HANDLE
    kernel.CloseHandle.argtypes = [wt.HANDLE]
    kernel.GetFinalPathNameByHandleW.argtypes = [wt.HANDLE, wt.LPWSTR, wt.DWORD, wt.DWORD]

    class Info(ctypes.Structure):
        _fields_ = [('attributes', wt.DWORD), ('created', wt.FILETIME), ('accessed', wt.FILETIME),
            ('written', wt.FILETIME), ('volume', wt.DWORD), ('size_high', wt.DWORD), ('size_low', wt.DWORD),
            ('links', wt.DWORD), ('index_high', wt.DWORD), ('index_low', wt.DWORD)]

    kernel.GetFileInformationByHandle.argtypes = [wt.HANDLE, ctypes.POINTER(Info)]
    write = create or append
    handle = kernel.CreateFileW(str(path), 0x80000000 | (0x40000000 if write else 0), 0, None,
                                1 if create else 3, 0x00200000 | 0x80000000, None)
    require(handle not in (None, ctypes.c_void_p(-1).value))
    try:
        info = Info()
        require(bool(kernel.GetFileInformationByHandle(handle, ctypes.byref(info))))
        require(not info.attributes & (stat.FILE_ATTRIBUTE_REPARSE_POINT | stat.FILE_ATTRIBUTE_DIRECTORY)
                and info.links == 1)
        canonical = ctypes.create_unicode_buffer(32768)
        count = kernel.GetFinalPathNameByHandleW(handle, canonical, len(canonical), 0)
        require(0 < count < len(canonical) and canonical.value == '\\\\?\\' + str(path))
        _check_handle_acl(handle)
        fd = msvcrt.open_osfhandle(handle, os.O_BINARY | os.O_NOINHERIT | (os.O_RDWR if write else os.O_RDONLY))
        handle = None
        try:
            return os.fdopen(fd, 'r+b' if write else 'rb', buffering=0)
        except BaseException:
            os.close(fd)
            raise
    finally:
        if handle is not None:
            kernel.CloseHandle(handle)


def _persist(stream, body):
    require(type(body) is bytes and stream.write(body) == len(body))
    stream.flush()
    os.fsync(stream.fileno())


def _body(value, limit=MAX_DATA):
    require(type(value) is dict)
    body = encode(value) + b'\n'
    require(len(body) <= limit and decode(body, limit) == value)
    return body


def _present(kind):
    try:
        _path(kind).lstat()
    except FileNotFoundError:
        return False
    with _open(kind):
        return True


def _read(kind):
    require(kind != 'audit')
    limit = 2048 if kind == 'authorization' or kind in MARKERS else MAX_DATA
    with _open(kind) as stream:
        raw = stream.read(limit + 1)
    require(len(raw) <= limit)
    value = decode(raw, limit)
    require(_body(value, limit) == raw)
    return value


def _create(kind, value):
    require(kind != 'authorization', 'production_authorization_is_read_only')
    with _open(kind, create=True) as stream:
        _persist(stream, _body(value))


def validate_authorization(value, bundle, now):
    require(type(value) is dict and set(value) == {'schema_version', 'scope', 'purpose',
            'bundle_sha256', 'issued_unix', 'expires_unix', 'max_model_turns', 'max_reports',
            'server_activation_verified'}, 'production_authorization_invalid')
    require(type(value['schema_version']) is int and value['schema_version'] == 1
            and value['scope'] == 'production' and value['purpose'] == 'single_public_report'
            and value['server_activation_verified'] is True, 'production_authorization_invalid')
    require(hash_value(value['bundle_sha256']) == hash_value(bundle), 'production_authorization_bundle')
    require(type(value['max_model_turns']) is int and value['max_model_turns'] == 1
            and type(value['max_reports']) is int and value['max_reports'] == 1,
            'production_authorization_limits')
    require(type(value['issued_unix']) is int and type(value['expires_unix']) is int
            and 0 <= value['issued_unix'] <= now < value['expires_unix']
            and 0 < value['expires_unix'] - value['issued_unix'] <= 900,
            'production_authorization_expired')


class Store:
    def __init__(self, bundle):
        self.bundle = hash_value(bundle)
        _root_check()
        self.token = None
        self.failed = False
        self.closed = False

    def _ready(self):
        require(not self.closed and not self.failed)

    def read(self, kind):
        require(not self.closed)
        return _read(kind)

    def available(self):
        """Read-only check; does not certify the remote gate or authorize a run."""
        require(not self.closed)
        return {'authorization_present': _present('authorization'),
                'attempt_already_consumed': _present('receipt'),
                'terminal_present': _present('terminal')}

    def _marker(self, kind, token):
        value = _read(kind)
        require(type(value) is dict and set(value) == {'schema_version', 'scope', 'bundle_sha256',
                'authorization_sha256', 'token_sha256', 'created_unix', 'outcome'})
        require(type(value['schema_version']) is int and value['schema_version'] == 1
                and value['scope'] == 'production' and value['bundle_sha256'] == self.bundle
                and type(value['created_unix']) is int and value['created_unix'] >= 0
                and value['outcome'] in ('pending', 'completed', 'uncertain'))
        require(hmac.compare_digest(hash_value(value['token_sha256']), token_hash(token)))
        require(hmac.compare_digest(hash_value(value['authorization_sha256']), digest(_read('authorization'))))
        return value

    def reserve(self):
        self._ready()
        require(_present('authorization'), 'production_trial_not_authorized')
        authorization = _read('authorization')
        validate_authorization(authorization, self.bundle, time.time())
        # Any retained artifact blocks reuse, including partial failed runs.
        require(not any(_present(k) for k in FILES if k not in ('authorization', 'storage-probe')),
                'production_trial_already_consumed')
        token = secrets.token_hex(32)
        receipt = {'schema_version': 1, 'scope': 'production', 'bundle_sha256': self.bundle,
                   'authorization_sha256': digest(authorization), 'token_sha256': token_hash(token),
                   'created_unix': int(time.time()), 'outcome': 'pending'}
        _create('receipt', receipt)  # CreateNew wins one race; failures consume this attempt.
        self.token = token
        with _open('audit', create=True) as stream:
            _persist(stream, b'')
        self.record('reserved')
        return token

    def active(self, token, *, armed=True):
        self._ready()
        receipt = self._marker('receipt', token)
        validate_authorization(_read('authorization'), self.bundle, time.time())
        require(receipt['outcome'] == 'pending' and 0 <= time.time() - receipt['created_unix'] < MAX_SECONDS
                and not _present('terminal'), 'production_trial_inactive')
        if armed:
            require(self._marker('armed', token) == receipt, 'production_trial_not_armed')
        self.token = token
        return receipt

    def arm(self, token):
        receipt = self.active(token, armed=False)
        require(self._marker('worker', token) == receipt)
        _create('armed', receipt)
        self.record('armed')

    def claim_worker(self, token):
        receipt = self.active(token, armed=False)
        _create('worker', receipt)
        self.record('worker_claimed')

    def start_model(self, token):
        receipt = self.active(token)
        require(self._marker('worker', token) == receipt)
        _create('model', receipt)
        self.record('model_started')

    def claim(self, token):
        receipt = self.active(token)
        require(self._marker('model', token) == receipt)
        _create('claim', receipt)
        self.record('mcp_claimed')

    def save(self, kind, value):
        self._ready()
        require(kind in ARTIFACTS)
        receipt = self.active(self.token)
        if kind != 'diagnostic':
            require(self._marker('claim', self.token) == receipt)
        _create(kind, value)
        require(_read(kind) == value)

    def record(self, event, *, method='none', attempt=0, code='none'):
        self._ready()
        try:
            self._marker('receipt', self.token)
            require(not _present('terminal') and event in EVENTS and method in ('none', 'prepare', 'publish', 'unknown')
                    and type(attempt) is int and 0 <= attempt <= 64 and code in CODES)
            value = {'timestamp_utc': datetime.now(timezone.utc).isoformat(), 'event': event,
                     'method': method, 'attempt': attempt, 'code': code}
            body = _body(value, MAX_AUDIT)
            with _open('audit', append=True) as stream:
                old = stream.read(MAX_AUDIT + 1)
                require(len(old) + len(body) <= MAX_AUDIT and (not old or old.endswith(b'\n'))
                        and len(old.splitlines()) < MAX_EVENTS)
                for line in old.splitlines():
                    prior = decode(line, MAX_AUDIT)
                    require(type(prior) is dict and set(prior) == set(value)
                            and prior['event'] in EVENTS and prior['code'] in CODES
                            and prior['method'] in ('none', 'prepare', 'publish', 'unknown')
                            and type(prior['attempt']) is int and 0 <= prior['attempt'] <= 64
                            and type(prior['timestamp_utc']) is str
                            and re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?\+00:00', prior['timestamp_utc']))
                stream.seek(0, os.SEEK_END)
                require(stream.tell() == len(old))
                _persist(stream, body)
        except Exception:
            self.failed = True
            raise ValueError('production_trial_audit_failed') from None

    def finish(self, token, outcome):
        """Best effort closure even after expiry/log failure; never repair or retry."""
        require(not self.closed and outcome in ('completed', 'uncertain'))
        receipt = self._marker('receipt', token)
        if _present('terminal'):
            require(self._marker('terminal', token) == receipt | {'outcome': outcome})
            return
        self.token = token
        try:
            if not self.failed:
                self.record('closed', code='none' if outcome == 'completed' else 'uncertain')
        finally:
            _create('terminal', receipt | {'outcome': outcome})

    def close(self):
        self.token = None
        self.closed = True


def storage_check():
    """One fixed harmless probe, retained as evidence; no authorization created."""
    _root_check()
    require(not _present('authorization') and not _present('receipt'), 'production_storage_probe_inactive_only')
    created = not _present('storage-probe')
    if created:
        _create('storage-probe', PROBE_BODY)
    require(_read('storage-probe') == PROBE_BODY)
    return {'private_storage_verified': True, 'synthetic_probe_created': created,
            'authorization_created': False, 'model_started': False, 'ssh_connected': False}
