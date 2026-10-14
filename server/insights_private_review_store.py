"""Fixed, private evidence storage for one explicitly approved real-history trial.

No CLI, network, deletion, overwrite, schedule, or production publication paths.
The existing staging ACL guards are reused without changing their globals or
calling their receipt/canary operations. Same-user programs remain outside this
guard's isolation boundary.
"""
from datetime import datetime, timezone
from functools import wraps
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
from insights_staging_run_audit import _check_handle_acl, _root_check


ROOT = Path(r'C:\ProgramData\HomeEnergyReporterStaging')
FILES = MappingProxyType({
    name: ROOT / ('private-review-01-' + name + ('.jsonl' if name == 'audit' else '.json'))
    for name in ('capture', 'snapshot', 'receipt', 'armed', 'claim', 'terminal', 'audit',
                 'result', 'diagnostic')
})
DATA_KINDS = frozenset(('capture', 'snapshot', 'result', 'diagnostic'))
MARKER_KINDS = frozenset(('receipt', 'armed', 'claim', 'terminal'))
EVENTS = frozenset(('reserved', 'runtime_verified', 'model_started',
                    'model_completed', 'prepared', 'saved', 'failed', 'finished'))
MAX_DATA_BYTES = 65536
MAX_MARKER_BYTES = 1024
MAX_AUDIT_BYTES = 8192
MAX_AUDIT_EVENTS = 32
MAX_SECONDS = 360


class StoreFailure(ValueError):
    """Fixed error only; OS details, credentials, and report values are omitted."""


def _require(value):
    if not value:
        raise StoreFailure('private_review_store_rejected')


def _guard(operation):
    @wraps(operation)
    def guarded(*args, **kwargs):
        try:
            return operation(*args, **kwargs)
        except Exception:
            raise StoreFailure('private_review_store_rejected') from None
    return guarded


def _path(kind):
    _require(type(kind) is str and kind in FILES)
    path = FILES[kind]
    _require(path.parent == ROOT and path.name == str(path.relative_to(ROOT)))
    return path


def _open(kind, *, create=False, write=False):
    """CreateNew/open a fixed direct child without sharing; check its handle."""
    _require(os.name == 'nt')
    _require(ROOT.is_absolute() and ROOT.resolve(strict=True) == ROOT
             and stat.S_ISDIR(ROOT.stat().st_mode))
    for ancestor in (ROOT, *ROOT.parents):
        _require(not getattr(ancestor.lstat(), 'st_file_attributes', 0)
                 & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    path = _path(kind)
    import ctypes
    from ctypes import wintypes as wt
    import msvcrt

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p,
                                  wt.DWORD, wt.DWORD, wt.HANDLE]
    kernel.CreateFileW.restype = wt.HANDLE
    kernel.CloseHandle.argtypes = [wt.HANDLE]
    kernel.GetFinalPathNameByHandleW.argtypes = [wt.HANDLE, wt.LPWSTR, wt.DWORD, wt.DWORD]

    class Info(ctypes.Structure):
        _fields_ = [('attributes', wt.DWORD), ('created', wt.FILETIME),
            ('accessed', wt.FILETIME), ('written', wt.FILETIME), ('volume', wt.DWORD),
            ('size_high', wt.DWORD), ('size_low', wt.DWORD), ('links', wt.DWORD),
            ('index_high', wt.DWORD), ('index_low', wt.DWORD)]

    kernel.GetFileInformationByHandle.argtypes = [wt.HANDLE, ctypes.POINTER(Info)]
    access = 0x80000000 | (0x40000000 if write else 0)
    handle = kernel.CreateFileW(str(path), access, 0, None, 1 if create else 3,
                                0x00200000 | 0x80000000, None)
    _require(handle not in (None, ctypes.c_void_p(-1).value))
    try:
        info = Info()
        _require(bool(kernel.GetFileInformationByHandle(handle, ctypes.byref(info))))
        _require(not info.attributes & (stat.FILE_ATTRIBUTE_REPARSE_POINT | stat.FILE_ATTRIBUTE_DIRECTORY))
        _require(info.links == 1)
        canonical = ctypes.create_unicode_buffer(32768)
        count = kernel.GetFinalPathNameByHandleW(handle, canonical, len(canonical), 0)
        _require(0 < count < len(canonical) and canonical.value == '\\\\?\\' + str(path))
        _check_handle_acl(handle)
        fd = msvcrt.open_osfhandle(handle,
            os.O_BINARY | os.O_NOINHERIT | (os.O_RDWR if write else os.O_RDONLY))
        handle = None
        try:
            return os.fdopen(fd, 'r+b' if write else 'rb', buffering=0)
        except BaseException:
            os.close(fd)
            raise
    finally:
        if handle is not None:
            kernel.CloseHandle(handle)


def _present(kind):
    """Do not convert access errors or reparse entries into an absent file."""
    path = _path(kind)
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    with _open(kind):
        return True


def _persist(stream, body):
    _require(type(body) is bytes and stream.write(body) == len(body))
    stream.flush()
    os.fsync(stream.fileno())


def _hash(value):
    _require(type(value) is str and re.fullmatch('[a-f0-9]{64}', value))
    return value


def _token_hash(token):
    return hashlib.sha256(_hash(token).encode('ascii')).hexdigest()


def _json_bytes(value, limit):
    _require(type(value) is dict)
    body = encode(value) + b'\n'
    _require(len(body) <= limit)
    # Round-trip rejects non-string/coerced object keys and non-finite numbers.
    _require(decode(body, limit) == value)
    return body


def _read_json(kind, limit):
    with _open(kind) as stream:
        body = stream.read(limit + 1)
    _require(0 < len(body) <= limit)
    value = decode(body, limit)
    _json_bytes(value, limit)
    return value


def _data(kind, value):
    _require(type(value) is dict and type(value.get('schema_version')) is int
             and value['schema_version'] == 1 and value.get('scope') == 'private_review'
             and value.get('synthetic') is False)
    if kind == 'capture':
        _require(set(value) == {'schema_version', 'scope', 'synthetic', 'created_at'}
                 and type(value['created_at']) is int and value['created_at'] >= 0)
    elif kind == 'snapshot':
        _require(set(value) == {'schema_version', 'scope', 'synthetic', 'captured_at',
                               'analysis_id', 'analysis'}
                 and type(value['captured_at']) is int and value['captured_at'] >= 0
                 and type(value['analysis']) is dict)
        expected = hashlib.sha256(encode(value['analysis'])).hexdigest()
        _require(hmac.compare_digest(_hash(value['analysis_id']), expected))
    return _json_bytes(value, MAX_DATA_BYTES)


def _marker_shape(value):
    _require(type(value) is dict and set(value) == {'schema_version', 'scope', 'synthetic',
                            'token_sha256', 'snapshot_sha256', 'created_unix'})
    _require(type(value['schema_version']) is int and value['schema_version'] == 1
             and value['scope'] == 'private_review' and value['synthetic'] is False
             and type(value['created_unix']) is int and value['created_unix'] >= 0)
    _hash(value['token_sha256'])
    _hash(value['snapshot_sha256'])


def _event(value):
    _require(type(value) is dict and set(value) == {'timestamp_utc', 'event'})
    _require(type(value['event']) is str and value['event'] in EVENTS)
    stamp = value['timestamp_utc']
    _require(type(stamp) is str and re.fullmatch(
        r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?\+00:00', stamp))
    _require(datetime.fromisoformat(stamp).utcoffset().total_seconds() == 0)


def _audit_read(stream):
    body = stream.read(MAX_AUDIT_BYTES + 1)
    _require(len(body) <= MAX_AUDIT_BYTES and (not body or body.endswith(b'\n')))
    lines = body.splitlines()
    _require(len(lines) <= MAX_AUDIT_EVENTS)
    values = [decode(line, MAX_AUDIT_BYTES) for line in lines]
    for value in values:
        _event(value)
    return body, values


class Store:
    """One fixed evidence slot shared by its supervisor and one MCP process.

    A failed append never retries, truncates, or repairs evidence. Exclusive
    Windows opens serialize writers; overlap fails closed instead of racing.
    Read operations remain available after terminal closure for verification.
    """

    @_guard
    def __init__(self):
        _root_check()
        self.token = None
        self.closed = False
        self.failed = False

    def _ready(self, *, mutation=False):
        _require(not self.closed and (not mutation or not self.failed))

    def _receipt(self, token):
        receipt = self._marker_read('receipt', token)
        digest = hashlib.sha256(encode(self.read('snapshot'))).hexdigest()
        _require(hmac.compare_digest(receipt['snapshot_sha256'], digest))
        return receipt

    def _marker_read(self, kind, token, *, snapshot_sha256=None):
        _require(kind in MARKER_KINDS)
        value = _read_json(kind, MAX_MARKER_BYTES)
        _marker_shape(value)
        _require(hmac.compare_digest(_hash(value['token_sha256']), _token_hash(token)))
        _hash(value['snapshot_sha256'])
        if snapshot_sha256 is not None:
            _require(hmac.compare_digest(value['snapshot_sha256'], snapshot_sha256))
        return value

    def _marker_create(self, kind, token, snapshot_sha256):
        _require(kind in MARKER_KINDS)
        value = {'schema_version': 1, 'scope': 'private_review', 'synthetic': False,
                 'token_sha256': _token_hash(token), 'snapshot_sha256': _hash(snapshot_sha256),
                 'created_unix': int(time.time())}
        body = _json_bytes(value, MAX_MARKER_BYTES)
        with _open(kind, create=True, write=True) as stream:
            _persist(stream, body)

    @_guard
    def exists(self, kind):
        self._ready()
        return _present(kind)

    @_guard
    def read(self, kind):
        self._ready()
        _path(kind)
        if kind == 'audit':
            with _open(kind) as stream:
                return _audit_read(stream)[1]
        if kind in MARKER_KINDS:
            value = _read_json(kind, MAX_MARKER_BYTES)
            _marker_shape(value)
        else:
            value = _read_json(kind, MAX_DATA_BYTES)
            _data(kind, value)
        return value

    @_guard
    def create(self, kind, value):
        self._ready(mutation=True)
        _require(kind in DATA_KINDS)
        body = _data(kind, value)
        if kind == 'capture':
            _require(not _present('snapshot') and not _present('receipt') and not _present('terminal'))
        elif kind == 'snapshot':
            self.read('capture')
            _require(not _present('receipt') and not _present('terminal'))
        elif kind == 'result':
            self.active(self.token)
            receipt = self._receipt(self.token)
            self._marker_read('claim', self.token, snapshot_sha256=receipt['snapshot_sha256'])
        else:
            self._receipt(self.token)
            _require(not _present('terminal'))
        with _open(kind, create=True, write=True) as stream:
            _persist(stream, body)

    @_guard
    def reserve(self, snapshot_sha256):
        self._ready(mutation=True)
        _root_check()
        _hash(snapshot_sha256)
        expected = hashlib.sha256(encode(self.read('snapshot'))).hexdigest()
        _require(hmac.compare_digest(snapshot_sha256, expected) and not _present('terminal'))
        token = secrets.token_hex(32)
        # Receipt first: any later failure consumes the approval permanently.
        self._marker_create('receipt', token, snapshot_sha256)
        self.token = token
        with _open('audit', create=True, write=True) as stream:
            _persist(stream, b'')
        self.record('reserved')
        return token

    @_guard
    def active(self, token, armed=True):
        self._ready(mutation=True)
        _require(type(armed) is bool)
        receipt = self._receipt(token)
        now = time.time()
        _require(0 <= now - receipt['created_unix'] < MAX_SECONDS and not _present('terminal'))
        if armed:
            marker = self._marker_read('armed', token, snapshot_sha256=receipt['snapshot_sha256'])
            _require(receipt['created_unix'] <= marker['created_unix'] <= now
                     and now - marker['created_unix'] < MAX_SECONDS)
        self.token = token
        return True

    @_guard
    def arm(self, token):
        self.active(token, armed=False)
        receipt = self._receipt(token)
        self._marker_create('armed', token, receipt['snapshot_sha256'])

    @_guard
    def claim(self, token):
        _root_check()
        self.active(token)
        receipt = self._receipt(token)
        self._marker_create('claim', token, receipt['snapshot_sha256'])
        self.active(token)

    @_guard
    def record(self, event):
        self._ready(mutation=True)
        try:
            _require(type(event) is str and event in EVENTS)
            self._receipt(self.token)
            _require(not _present('terminal'))
            value = {'timestamp_utc': datetime.now(timezone.utc).isoformat(), 'event': event}
            body = _json_bytes(value, MAX_AUDIT_BYTES)
            with _open('audit', write=True) as stream:
                previous, values = _audit_read(stream)
                _require(len(previous) + len(body) <= MAX_AUDIT_BYTES
                         and len(values) < MAX_AUDIT_EVENTS)
                stream.seek(0, os.SEEK_END)
                _require(stream.tell() == len(previous))
                _persist(stream, body)
        except Exception:
            self.failed = True
            raise

    @_guard
    def finish(self, token):
        self._ready()
        receipt = self._receipt(token)
        if _present('terminal'):
            self._marker_read('terminal', token, snapshot_sha256=receipt['snapshot_sha256'])
            return
        # Closure is attempted even if logging has failed or the receipt expired.
        self.token = token
        try:
            if not self.failed:
                self.record('finished')
        finally:
            self._marker_create('terminal', token, receipt['snapshot_sha256'])

    def close(self):
        self.closed = True
        self.token = None
