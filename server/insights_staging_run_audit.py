"""Private, single-use approval receipt and bounded durable staging audit.

No CLI, networking, model calls, deletion, truncation, or arbitrary file paths.
The supervisor must terminate its owned process tree within 360 seconds. These
guards do not isolate the user from other programs running as that same user.
"""
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import stat
import time

import insights_staging_acl as acl
from insights_staging_mcp import REJECTION_CODES


ROOT = Path(r'C:\ProgramData\HomeEnergyReporterStaging')
FILES = {
    'receipt': ROOT / 'one-shot-04-receipt.json',
    'armed': ROOT / 'one-shot-04-armed.json',
    'claim': ROOT / 'one-shot-04-mcp-claim.json',
    'terminal': ROOT / 'one-shot-04-terminal.json',
    'audit': ROOT / 'one-shot-04-mcp-audit.jsonl',
    'supervisor': ROOT / 'one-shot-04-supervisor.jsonl',
    'result': ROOT / 'one-shot-04-result.json',
    'diagnostic': ROOT / 'one-shot-04-diagnostic.json',
    'rejections': ROOT / 'one-shot-04-rejections.jsonl',
    'probe': ROOT / 'one-shot-storage-check.json',
}
MAX_SECONDS = 360
MAX_AUDIT_BYTES = 32768
MAX_AUDIT_EVENTS = 72
MAX_SUPERVISOR_BYTES = 8192
MAX_SUPERVISOR_EVENTS = 20
MAX_RESULT_BYTES = 65536
MAX_DIAGNOSTIC_BYTES = 32768
SUPERVISOR_EVENTS = frozenset(('reserved', 'runtime_verified', 'armed',
    'model_started', 'model_completed', 'model_failed', 'verification_failed',
    'timeout', 'cleanup_started', 'cleanup_completed', 'finished'))
STORAGE_PROBE_BODY = b'{"schema_version":1,"scope":"staging","synthetic":true,"storage_check":true}\n'


class AuditFailure(ValueError):
    """Fixed non-sensitive error code; never includes OS messages or payloads."""


def require(value, code='private_audit_rejected'):
    if not value:
        raise AuditFailure(code)


def _json(value):
    return json.dumps(value, ensure_ascii=True, separators=(',', ':'), sort_keys=True).encode('ascii')


def _unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


def _decode(body):
    try:
        return json.loads(body.decode('ascii'), object_pairs_hook=_unique,
                          parse_constant=lambda value: require(False))
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise AuditFailure('private_audit_rejected') from None


def _root_check():
    require(os.name == 'nt', 'private_audit_windows_required')
    acl.verify_permissions()
    require(ROOT.is_absolute() and ROOT.resolve(strict=True) == ROOT)
    require(stat.S_ISDIR(ROOT.stat().st_mode))
    for path in (ROOT, *ROOT.parents):
        require(not getattr(path.lstat(), 'st_file_attributes', 0)
                & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _check_handle_acl(handle):
    """Validate the opened file's raw DACL, not a path-based ACL projection."""
    import ctypes
    from ctypes import wintypes as wt
    adv = ctypes.WinDLL('advapi32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    pointer = ctypes.c_void_p
    adv.GetSecurityInfo.argtypes = [wt.HANDLE, ctypes.c_int, wt.DWORD,
        ctypes.POINTER(pointer), pointer, ctypes.POINTER(pointer), pointer,
        ctypes.POINTER(pointer)]
    adv.GetSecurityInfo.restype = wt.DWORD
    adv.GetSecurityDescriptorControl.argtypes = [pointer, ctypes.POINTER(wt.WORD), ctypes.POINTER(wt.DWORD)]
    adv.GetAce.argtypes = [pointer, wt.DWORD, ctypes.POINTER(pointer)]
    adv.IsValidSid.argtypes = [pointer]
    adv.GetLengthSid.argtypes = [pointer]
    adv.ConvertSidToStringSidW.argtypes = [pointer, ctypes.POINTER(wt.LPWSTR)]
    adv.OpenProcessToken.argtypes = [wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE)]
    adv.GetTokenInformation.argtypes = [wt.HANDLE, ctypes.c_int, pointer, wt.DWORD, ctypes.POINTER(wt.DWORD)]
    kernel.GetCurrentProcess.restype = wt.HANDLE
    kernel.CloseHandle.argtypes = [wt.HANDLE]
    kernel.LocalFree.argtypes = [pointer]
    kernel.LocalFree.restype = pointer

    def sid_text(sid):
        require(bool(adv.IsValidSid(sid)))
        text = wt.LPWSTR()
        require(bool(adv.ConvertSidToStringSidW(sid, ctypes.byref(text))))
        try:
            return text.value
        finally:
            kernel.LocalFree(ctypes.cast(text, pointer))

    token = wt.HANDLE()
    require(bool(adv.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token))))
    try:
        needed = wt.DWORD()
        adv.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
        require(0 < needed.value <= 4096)
        buffer = ctypes.create_string_buffer(needed.value)
        require(bool(adv.GetTokenInformation(token, 1, buffer, needed, ctypes.byref(needed))))
        current_sid = sid_text(ctypes.cast(buffer, ctypes.POINTER(pointer))[0])
    finally:
        kernel.CloseHandle(token)
    owner, dacl, descriptor = pointer(), pointer(), pointer()
    require(adv.GetSecurityInfo(handle, 1, 1 | 4, ctypes.byref(owner), None,
        ctypes.byref(dacl), None, ctypes.byref(descriptor)) == 0)
    try:
        control, revision = wt.WORD(), wt.DWORD()
        require(bool(adv.GetSecurityDescriptorControl(descriptor, ctypes.byref(control), ctypes.byref(revision))))
        require(bool(control.value & 4) and not control.value & 0x1000 and bool(dacl))
        require(sid_text(owner) == current_sid)
        # ACL header is revision/reserved/size/ace-count/reserved (eight bytes).
        require(ctypes.c_ushort.from_address(dacl.value + 4).value == 3)
        expected = {current_sid, 'S-1-5-18', 'S-1-5-32-544'}
        require(len(expected) == 3)
        actual = set()
        for index in range(3):
            ace = pointer()
            require(bool(adv.GetAce(dacl, index, ctypes.byref(ace))))
            address = ace.value
            # ACCESS_ALLOWED_ACE only, exactly inherited, no callback/object ACE.
            require(ctypes.c_ubyte.from_address(address).value == 0)
            require(ctypes.c_ubyte.from_address(address + 1).value == 16)
            require(ctypes.c_uint32.from_address(address + 4).value == acl.FULL_CONTROL)
            sid = pointer(address + 8)
            principal = sid_text(sid)
            require(ctypes.c_ushort.from_address(address + 2).value == 8 + adv.GetLengthSid(sid))
            require(principal in expected and principal not in actual)
            actual.add(principal)
        require(actual == expected)
    finally:
        kernel.LocalFree(descriptor)


def _open(kind, *, create=False, write=False):
    """Open a fixed direct child exclusively; inspect its actual Windows handle."""
    require(kind in FILES and os.name == 'nt')
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
    path = FILES[kind]
    require(path.parent == ROOT and path.name == str(path.relative_to(ROOT)))
    access = 0x80000000 | (0x40000000 if write else 0)  # Read plus optional write.
    handle = kernel.CreateFileW(str(path), access, 0, None, 1 if create else 3,
                                0x00200000 | 0x80000000, None)  # OPEN_REPARSE_POINT, WRITE_THROUGH.
    require(handle not in (None, ctypes.c_void_p(-1).value), 'private_audit_open_failed')
    try:
        info = Info()
        require(bool(kernel.GetFileInformationByHandle(handle, ctypes.byref(info))))
        require(not info.attributes & (stat.FILE_ATTRIBUTE_REPARSE_POINT | stat.FILE_ATTRIBUTE_DIRECTORY))
        require(info.links == 1)
        canonical = ctypes.create_unicode_buffer(32768)
        count = kernel.GetFinalPathNameByHandleW(handle, canonical, len(canonical), 0)
        require(0 < count < len(canonical)
                and canonical.value == '\\\\?\\' + str(path))
        _check_handle_acl(handle)
        fd = msvcrt.open_osfhandle(handle, os.O_BINARY | os.O_NOINHERIT | (os.O_RDWR if write else os.O_RDONLY))
        handle = None  # File descriptor now owns this handle.
        try:
            return os.fdopen(fd, 'r+b' if write else 'rb', buffering=0)
        except BaseException:
            os.close(fd)
            raise
    finally:
        if handle is not None:
            kernel.CloseHandle(handle)


def _digest(token):
    require(type(token) is str and re.fullmatch('[a-f0-9]{64}', token))
    return hashlib.sha256(token.encode('ascii')).hexdigest()


def _marker(kind, token):
    value = {'schema_version': 1, 'scope': 'staging', 'synthetic': True,
             'token_sha256': _digest(token), 'created_unix': int(time.time())}
    with _open(kind, create=True, write=True) as stream:
        body = _json(value) + b'\n'
        require(stream.write(body) == len(body))
        stream.flush()
        os.fsync(stream.fileno())


def _read_marker(kind, token):
    with _open(kind) as stream:
        body = stream.read(1025)
    require(len(body) <= 1024)
    value = _decode(body)
    require(type(value) is dict and set(value) == {
        'schema_version', 'scope', 'synthetic', 'token_sha256', 'created_unix'})
    require(type(value['schema_version']) is int and value['schema_version'] == 1
            and value['scope'] == 'staging' and value['synthetic'] is True)
    require(type(value['token_sha256']) is str
            and re.fullmatch('[a-f0-9]{64}', value['token_sha256'])
            and hmac.compare_digest(value['token_sha256'], _digest(token)))
    require(type(value['created_unix']) is int)
    return value['created_unix']


def _active(token, *, armed=False):
    started = _read_marker('receipt', token)
    now = time.time()
    require(0 <= now - started < MAX_SECONDS, 'private_audit_expired')
    require(not os.path.lexists(FILES['terminal']), 'private_audit_terminal')
    if armed:
        when = _read_marker('armed', token)
        require(started <= when <= now and now - when < MAX_SECONDS)


def _timestamp(value):
    require(type(value) is str and re.fullmatch(
        r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?\+00:00', value))
    try:
        datetime.fromisoformat(value)
    except ValueError:
        raise AuditFailure('private_audit_rejected') from None


def _audit_event(event):
    require(type(event) is dict and set(event) == {'timestamp_utc', 'event', 'attempt', 'method', 'outcome'})
    _timestamp(event['timestamp_utc'])
    require(type(event['attempt']) is int)
    if event['event'] == 'staging_adapter':
        require(1 <= event['attempt'] <= 64 and event['method'] in ('unknown', 'prepare', 'publish')
                and event['outcome'] in ('accepted', 'rejected'))
    else:
        require(event['event'] == 'staging_transport' and 1 <= event['attempt'] <= 4
                and event['method'] in ('prepare', 'publish')
                and event['outcome'] in ('started', 'returned', 'failed'))


class DurableAuditStream:
    """A single MCP writer; every flush commits OS file buffers to storage."""
    def __init__(self, stream):
        self.stream = stream
        self.size = 0
        self.events = 0
        self.failed = False

    def write(self, body):
        try:
            require(not self.failed and type(body) is bytes and body.endswith(b'\n')
                    and body.count(b'\n') == 1)
            require(self.size + len(body) <= MAX_AUDIT_BYTES and self.events < MAX_AUDIT_EVENTS)
            _audit_event(_decode(body))
            self.size += len(body)
            self.events += 1
            require(self.stream.write(body) == len(body))
            return len(body)
        except (ValueError, OSError, TypeError):
            self.failed = True
            raise AuditFailure('private_audit_write_failed') from None

    def flush(self):
        try:
            require(not self.failed)
            self.stream.flush()
            os.fsync(self.stream.fileno())
        except (ValueError, OSError, TypeError):
            self.failed = True
            raise AuditFailure('private_audit_flush_failed') from None

    def close(self):
        self.stream.close()

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        self.close()


class Reservation:
    def __init__(self, token, stream):
        self.token = token
        self.stream = stream
        self.events = 0
        self.size = 0
        self.failed = False

    def record(self, event):
        try:
            require(not self.failed and type(event) is str and event in SUPERVISOR_EVENTS)
            require(self.events < MAX_SUPERVISOR_EVENTS)
            body = _json({'timestamp_utc': datetime.now(timezone.utc).isoformat(),
                          'event': event}) + b'\n'
            require(self.size + len(body) <= MAX_SUPERVISOR_BYTES)
            self.events += 1
            self.size += len(body)
            require(self.stream.write(body) == len(body))
            self.stream.flush()
            os.fsync(self.stream.fileno())
        except (ValueError, OSError, TypeError):
            self.failed = True
            raise AuditFailure('private_supervisor_audit_failed') from None

    def close(self):
        self.stream.close()


def reserve_run():
    """Consume this approval once, before starting the single model attempt.

    A partial failure deliberately leaves its receipt/files behind and cannot be
    retried automatically. Only a future explicitly approved administrative
    change can provision another approval; no reset operation exists here.
    """
    _root_check()
    token = secrets.token_hex(32)
    _marker('receipt', token)
    for kind in ('audit', 'rejections'):
        with _open(kind, create=True, write=True) as stream:
            stream.flush()
            os.fsync(stream.fileno())
    stream = _open('supervisor', create=True, write=True)
    reservation = Reservation(token, stream)
    try:
        reservation.record('reserved')
        return reservation
    except BaseException:
        reservation.close()
        raise


def check_private_storage():
    """Verify native creation/durability/ACLs without consuming a model approval.

    The fixed synthetic canary is retained. An existing canary is only read and
    verified; it is never repaired, overwritten, or removed automatically.
    """
    _root_check()
    if not os.path.lexists(FILES['probe']):
        with _open('probe', create=True, write=True) as stream:
            require(stream.write(STORAGE_PROBE_BODY) == len(STORAGE_PROBE_BODY))
            stream.flush()
            os.fsync(stream.fileno())
    with _open('probe') as stream:
        require(stream.read(len(STORAGE_PROBE_BODY) + 1) == STORAGE_PROBE_BODY)
    return {'private_storage_verified': True, 'receipt_created': False,
            'model_started': False}


def arm_run(token):
    """Only the verified supervisor calls this, immediately before turn/start."""
    _active(token)
    _marker('armed', token)


def disarm_run(token):
    """Permanently close the gate, including after expiry; never remove files."""
    _read_marker('receipt', token)
    if os.path.lexists(FILES['terminal']):
        _read_marker('terminal', token)
        return
    _marker('terminal', token)


def open_mcp_audit(token):
    """Claim exactly one MCP session; reconnects fail even if no call was sent."""
    gate_step('gate_root_rejected', _root_check)
    started = gate_step('gate_receipt_rejected', _read_marker, 'receipt', token)
    now = time.time()
    require(0 <= now - started < MAX_SECONDS, 'gate_expired')
    require(not os.path.lexists(FILES['terminal']), 'gate_terminal')
    when = gate_step('gate_armed_rejected', _read_marker, 'armed', token)
    require(started <= when <= now and now - when < MAX_SECONDS, 'gate_armed_rejected')
    gate_step('gate_claim_rejected', _marker, 'claim', token)
    stream = gate_step('gate_audit_rejected', _open, 'audit', write=True)
    try:
        require(os.fstat(stream.fileno()).st_size == 0, 'gate_audit_rejected')
        gate_step('gate_recheck_rejected', _active, token, armed=True)
        return DurableAuditStream(stream)
    except BaseException:
        stream.close()
        raise


def gate_step(code, operation, *args, **kwargs):
    """Replace all lower-level details with the caller's fixed stage code."""
    require(code in REJECTION_CODES)
    try:
        return operation(*args, **kwargs)
    except Exception:
        raise AuditFailure(code) from None


def validate_rejection(event):
    require(type(event) is dict and set(event) == {'timestamp_utc', 'attempt', 'method', 'code'},
            'private_rejection_invalid')
    _timestamp(event['timestamp_utc'])
    require(type(event['attempt']) is int and 1 <= event['attempt'] <= 64,
            'private_rejection_invalid')
    require(type(event['method']) is str and event['method'] in ('unknown', 'prepare', 'publish'),
            'private_rejection_invalid')
    require(type(event['code']) is str and event['code'] in REJECTION_CODES,
            'private_rejection_invalid')


class RejectionLog:
    """Bounded, secret-free host diagnostics; never grants reporting access."""
    def __init__(self, stream):
        self.stream = stream
        self.size = 0
        self.events = 0
        self.failed = False

    def record(self, event):
        try:
            require(not self.failed, 'private_rejection_failed')
            validate_rejection(event)
            body = _json(event) + b'\n'
            require(self.events < 64 and self.size + len(body) <= MAX_AUDIT_BYTES,
                    'private_rejection_limit')
            self.events += 1
            self.size += len(body)
            require(self.stream.write(body) == len(body), 'private_rejection_write_failed')
            self.stream.flush()
            os.fsync(self.stream.fileno())
        except Exception:
            self.failed = True
            raise AuditFailure('private_rejection_write_failed') from None

    def close(self):
        self.stream.close()


def open_rejection_log(token):
    """Open only a pre-reserved sink, before arming; never create or repair it.

    File handles validate their own ACLs. The reporting gate separately validates
    the entire private-material root so its failures can be logged here. Receipt,
    handle or sink failures remain fail-closed even when diagnostics cannot open.
    An expired receipt can log its rejection, but terminal receipts cannot open
    this sink. Neither case acquires a claim or grants reporting authority.
    """
    _read_marker('receipt', token)
    require(not os.path.lexists(FILES['terminal']), 'private_audit_terminal')
    stream = _open('rejections', write=True)
    try:
        require(os.fstat(stream.fileno()).st_size == 0, 'private_rejection_not_empty')
        return RejectionLog(stream)
    except BaseException:
        stream.close()
        raise


class LazyRejectionLog:
    """Discovery may start multiple MCP clients; only a rejection opens a sink."""
    def __init__(self, token):
        self.token = token
        self.log = None
        self.failed = False

    def record(self, event):
        try:
            require(not self.failed, 'private_rejection_failed')
            validate_rejection(event)
            if self.log is None:
                self.log = open_rejection_log(self.token)
            self.log.record(event)
        except Exception:
            self.failed = True
            raise AuditFailure('private_rejection_write_failed') from None

    def close(self):
        if self.log is not None:
            self.log.close()


def read_audit_summary(token):
    """After the MCP process closes: validated counters, never report content."""
    _read_marker('receipt', token)
    with _open('audit') as stream:
        body = stream.read(MAX_AUDIT_BYTES + 1)
    require(len(body) <= MAX_AUDIT_BYTES and (not body or body.endswith(b'\n')))
    lines = body.splitlines()
    require(len(lines) <= MAX_AUDIT_EVENTS)
    counts = {'events': len(lines), 'bytes': len(body), 'prepare_accepted': 0,
              'publish_accepted': 0, 'protocol_accepted': 0, 'adapter_rejected': 0, 'transport_started': 0,
              'transport_returned': 0, 'transport_failed': 0}
    for line in lines:
        event = _decode(line)
        _audit_event(event)
        if event['event'] == 'staging_transport':
            counts['transport_' + event['outcome']] += 1
        elif event['outcome'] == 'rejected':
            counts['adapter_rejected'] += 1
        elif event['method'] == 'unknown':
            counts['protocol_accepted'] += 1
        else:
            require(event['method'] in ('prepare', 'publish'))
            counts[event['method'] + '_accepted'] += 1
    return counts


def _result(value):
    require(type(value) is dict and set(value) == {'analysis', 'submission', 'acknowledgement'})
    require(all(type(item) is dict for item in value.values()))
    # The caller validates the report contract and matching acknowledgement.
    # This sink independently enforces exact top-level fields and finite JSON.
    try:
        body = json.dumps(value, ensure_ascii=True, allow_nan=False,
                          separators=(',', ':'), sort_keys=True).encode('ascii') + b'\n'
    except (ValueError, TypeError, RecursionError):
        raise AuditFailure('private_result_rejected') from None
    require(len(body) <= MAX_RESULT_BYTES, 'private_result_rejected')
    return body


def write_result(token, value):
    """One private synthetic evidence artifact, separate from secret-free logs.

    Only the trusted caller supplies an already validated analysis, exact
    submission and acknowledged staging result. This is not a model file tool.
    """
    _active(token, armed=True)
    _read_marker('claim', token)
    body = _result(value)
    with _open('result', create=True, write=True) as stream:
        require(stream.write(body) == len(body))
        stream.flush()
        os.fsync(stream.fileno())


def read_result(token):
    _read_marker('receipt', token)
    with _open('result') as stream:
        body = stream.read(MAX_RESULT_BYTES + 1)
    require(len(body) <= MAX_RESULT_BYTES)
    value = _decode(body)
    _result(value)
    return value


def _diagnostic(value):
    require(type(value) is dict and set(value) == {
        'model_started', 'model_completed', 'mcp_items', 'tool_calls', 'total_tokens', 'final_message'},
        'private_diagnostic_rejected')
    require(type(value['model_started']) is bool and type(value['model_completed']) is bool,
            'private_diagnostic_rejected')
    require(not value['model_completed'] or value['model_started'], 'private_diagnostic_rejected')
    require(type(value['mcp_items']) is int and 0 <= value['mcp_items'] <= 1000,
            'private_diagnostic_rejected')
    require(type(value['tool_calls']) is int and 0 <= value['tool_calls'] <= 1000,
            'private_diagnostic_rejected')
    require(value['total_tokens'] is None or (type(value['total_tokens']) is int
            and 0 <= value['total_tokens'] <= 10000000), 'private_diagnostic_rejected')
    require(type(value['final_message']) is str and len(value['final_message']) <= 8000,
            'private_diagnostic_rejected')
    try:
        body = json.dumps(value, ensure_ascii=False, allow_nan=False,
                          separators=(',', ':'), sort_keys=True).encode('utf-8') + b'\n'
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise AuditFailure('private_diagnostic_rejected') from None
    require(len(body) <= MAX_DIAGNOSTIC_BYTES, 'private_diagnostic_rejected')
    return body


def write_diagnostic(token, value):
    """Persist one private postmortem, not a security log or publication proof.

    Only the final model message and bounded counters are allowed. No reasoning,
    runtime configuration, request bodies or credentials belong here. Matching
    the consumed receipt permits diagnostics after expiry or gate closure; it
    does not reopen the gate, permit a second model run, or overwrite evidence.
    An absent MCP claim or empty audit alone cannot prove no tool was attempted;
    tool_calls counts observed raw function/custom calls without their contents.
    """
    _read_marker('receipt', token)
    body = _diagnostic(value)
    with _open('diagnostic', create=True, write=True) as stream:
        require(stream.write(body) == len(body))
        stream.flush()
        os.fsync(stream.fileno())


def read_diagnostic(token):
    _read_marker('receipt', token)
    with _open('diagnostic') as stream:
        body = stream.read(MAX_DIAGNOSTIC_BYTES + 1)
    require(len(body) <= MAX_DIAGNOSTIC_BYTES, 'private_diagnostic_rejected')
    try:
        value = json.loads(body.decode('utf-8'), object_pairs_hook=_unique,
                           parse_constant=lambda unused: require(False))
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise AuditFailure('private_diagnostic_rejected') from None
    _diagnostic(value)
    return value
