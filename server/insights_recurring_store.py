"""Create-once recurring report evidence in a separate, fixed private namespace.

No authorization writer, reset, deletion, scheduler, network or model operation.
An exclusive Windows handle serializes supervisors. A retained incomplete run
blocks all subsequent runs, including after process death or a PC restart.
"""
from datetime import datetime, timezone
import hashlib
import hmac
import os
import re
import secrets
import stat
import time

from insights_broker import decode, encode
import insights_production_mcp as adapter
import insights_production_trial_store as trial


ROOT = trial.ROOT
PREFIX = 'recurring-v1-'
INTERVAL_SECONDS = 6 * 3600
MAX_RUNS = 120
MAX_GRANT_SECONDS = 30 * 86400
MAX_SECONDS = trial.MAX_SECONDS
KINDS = trial.MARKERS | trial.ARTIFACTS | {'audit'}
EVENTS = trial.EVENTS | {'armed'}
ZERO_HASH = '0' * 64
PROBE = {'schema_version': 1, 'scope': 'synthetic_recurring_storage_probe',
         'model_started': False, 'report_calls_enabled': False}
digest = trial.digest


def require(condition, code='recurring_evidence_rejected'):
    if not condition:
        raise ValueError(code)


def run_identifier(value):
    require(type(value) is str and re.fullmatch(r'[0-9]{6}', value)
            and 1 <= int(value) <= MAX_RUNS, 'recurring_run_identifier')
    return value


def filename(kind, run_id=None):
    if run_id is None:
        require(kind in ('authorization', 'lock', 'storage-probe'), 'recurring_path_refused')
        return PREFIX + kind + ('.lock' if kind == 'lock' else '.json')
    run_identifier(run_id)
    require(kind in KINDS, 'recurring_path_refused')
    return PREFIX + run_id + '-' + kind + ('.jsonl' if kind == 'audit' else '.json')


def validate_name(name):
    require(type(name) is str, 'recurring_path_refused')
    if name in {filename(k) for k in ('authorization', 'lock', 'storage-probe')}:
        return None
    match = re.fullmatch(r'recurring-v1-([0-9]{6})-([a-z]+)\.(jsonl?)', name)
    require(match is not None, 'recurring_unknown_evidence')
    run_id, kind, _ = match.groups()
    require(name == filename(kind, run_id), 'recurring_path_refused')
    return run_id, kind


class Files:
    """Fixed direct children only; inspect the opened handle, not just its path."""
    def __init__(self):
        trial._root_check()

    def names(self):
        names = []
        for path in ROOT.iterdir():
            if path.name.startswith(PREFIX):
                validate_name(path.name)
                names.append(path.name)
                require(len(names) <= MAX_RUNS * len(KINDS) + 3, 'recurring_inventory_limit')
        return set(names)

    def open(self, name, mode='read'):
        validate_name(name)
        require(os.name == 'nt' and mode in ('read', 'create', 'append', 'lock'), 'recurring_open_refused')
        require(mode == 'read' or name != filename('authorization'), 'recurring_authorization_read_only')
        require(mode != 'append' or name.endswith('-audit.jsonl'), 'recurring_append_refused')
        require((mode == 'lock') == (name == filename('lock')), 'recurring_lock_refused')
        path = ROOT / name
        require(ROOT.resolve(strict=True) == ROOT and path.parent == ROOT, 'recurring_path_refused')
        for parent in (ROOT, *ROOT.parents):
            require(not getattr(parent.lstat(), 'st_file_attributes', 0)
                    & stat.FILE_ATTRIBUTE_REPARSE_POINT, 'recurring_reparse_path')
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
            _fields_ = [('attributes', wt.DWORD), ('created', wt.FILETIME), ('accessed', wt.FILETIME),
                       ('written', wt.FILETIME), ('volume', wt.DWORD), ('size_high', wt.DWORD),
                       ('size_low', wt.DWORD), ('links', wt.DWORD), ('index_high', wt.DWORD),
                       ('index_low', wt.DWORD)]

        kernel.GetFileInformationByHandle.argtypes = [wt.HANDLE, ctypes.POINTER(Info)]
        writable = mode != 'read'
        # OPEN_ALWAYS is permitted only for the empty interprocess lock. Nothing
        # uses CREATE_ALWAYS or truncates an existing file, even on failure.
        disposition = 1 if mode == 'create' else (4 if mode == 'lock' else 3)
        handle = kernel.CreateFileW(str(path), 0x80000000 | (0x40000000 if writable else 0),
                                   0, None, disposition, 0x00200000 | 0x80000000, None)
        if handle in (None, ctypes.c_void_p(-1).value):
            error = ctypes.get_last_error()
            raise ValueError('recurring_busy' if mode == 'lock' and error == 32
                             else 'recurring_file_open_refused')
        try:
            info = Info()
            require(bool(kernel.GetFileInformationByHandle(handle, ctypes.byref(info))))
            require(not info.attributes & (stat.FILE_ATTRIBUTE_REPARSE_POINT | stat.FILE_ATTRIBUTE_DIRECTORY)
                    and info.links == 1, 'recurring_file_type')
            require(mode != 'lock' or info.size_high == info.size_low == 0, 'recurring_lock_changed')
            canonical = ctypes.create_unicode_buffer(32768)
            count = kernel.GetFinalPathNameByHandleW(handle, canonical, len(canonical), 0)
            require(0 < count < len(canonical) and canonical.value == '\\\\?\\' + str(path),
                    'recurring_file_path')
            trial._check_handle_acl(handle)
            fd = msvcrt.open_osfhandle(handle, os.O_BINARY | os.O_NOINHERIT
                                      | (os.O_RDWR if writable else os.O_RDONLY))
            handle = None
            try:
                return os.fdopen(fd, 'r+b' if writable else 'rb', buffering=0)
            except BaseException:
                os.close(fd)
                raise
        finally:
            if handle is not None:
                kernel.CloseHandle(handle)

    def read(self, name, limit=trial.MAX_DATA):
        with self.open(name) as stream:
            raw = stream.read(limit + 1)
        require(len(raw) <= limit)
        value = decode(raw, limit)
        require(trial._body(value, limit) == raw, 'recurring_noncanonical_evidence')
        return value

    def create(self, name, value):
        body = trial._body(value)
        with self.open(name, 'create') as stream:
            trial._persist(stream, body)


def validate_authorization(value, bundle, now):
    require(type(value) is dict and set(value) == {'schema_version', 'scope', 'purpose',
        'bundle_sha256', 'issued_unix', 'not_before_unix', 'expires_unix', 'interval_seconds',
        'max_runs', 'max_model_turns_per_run', 'max_reports_per_run', 'server_activation_verified'},
        'recurring_authorization_invalid')
    require(type(value['schema_version']) is int and value['schema_version'] == 1
            and value['scope'] == 'production' and value['purpose'] == 'recurring_public_reports'
            and value['server_activation_verified'] is True, 'recurring_authorization_invalid')
    require(trial.hash_value(value['bundle_sha256']) == trial.hash_value(bundle), 'recurring_bundle_changed')
    for field in ('issued_unix', 'not_before_unix', 'expires_unix', 'interval_seconds', 'max_runs',
                  'max_model_turns_per_run', 'max_reports_per_run'):
        require(type(value[field]) is int, 'recurring_authorization_invalid')
    require(value['interval_seconds'] == INTERVAL_SECONDS and 1 <= value['max_runs'] <= MAX_RUNS
            and value['max_model_turns_per_run'] == value['max_reports_per_run'] == 1,
            'recurring_authorization_limits')
    require(0 <= value['issued_unix'] <= now < value['expires_unix']
            and value['issued_unix'] <= value['not_before_unix'] < value['expires_unix']
            and value['expires_unix'] - value['issued_unix'] <= MAX_GRANT_SECONDS,
            'recurring_authorization_expired')
    return value


def validate_audit(raw):
    require(type(raw) is bytes and len(raw) <= trial.MAX_AUDIT
            and (not raw or raw.endswith(b'\n')), 'recurring_audit_invalid')
    rows = []
    for line in raw.splitlines():
        row = decode(line, trial.MAX_AUDIT)
        require(type(row) is dict and set(row) == {'timestamp_utc', 'event', 'method', 'attempt', 'code'}
                and row['event'] in EVENTS and row['code'] in trial.CODES
                and row['method'] in ('none', 'prepare', 'publish', 'unknown')
                and type(row['attempt']) is int and 0 <= row['attempt'] <= 64
                and type(row['timestamp_utc']) is str
                and re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?\+00:00', row['timestamp_utc'])
                and encode(row) == line, 'recurring_audit_invalid')
        rows.append(row)
        require(len(rows) <= trial.MAX_EVENTS, 'recurring_audit_limit')
    return rows


class Store:
    def __init__(self, bundle, run_id=None, *, files=None):
        self.bundle = trial.hash_value(bundle)
        self.files = Files() if files is None else files
        self.run_id = None if run_id is None else run_identifier(run_id)
        self.token = None
        self.failed = self.closed = False
        self.lock = None

    def _ready(self):
        require(not self.closed and not self.failed, 'recurring_store_closed')

    def _name(self, kind):
        require(self.run_id is not None, 'recurring_run_not_reserved')
        return filename(kind, self.run_id)

    def _grant(self):
        require(filename('authorization') in self.files.names(), 'recurring_not_authorized')
        return validate_authorization(self.files.read(filename('authorization'), 4096), self.bundle, time.time())

    def read(self, kind):
        require(not self.closed)
        return self.files.read(self._name(kind))

    def _receipt(self, run_id, grant):
        receipt = self.files.read(filename('receipt', run_id), 4096)
        require(set(receipt) == {'schema_version', 'scope', 'run_id', 'bundle_sha256',
            'authorization_sha256', 'token_sha256', 'created_unix', 'previous_terminal_sha256', 'outcome'}
            and type(receipt['schema_version']) is int and receipt['schema_version'] == 1
            and receipt['scope'] == 'production' and receipt['run_id'] == run_id
            and receipt['bundle_sha256'] == self.bundle and receipt['authorization_sha256'] == digest(grant)
            and type(receipt['created_unix']) is int
            and grant['not_before_unix'] <= receipt['created_unix'] < grant['expires_unix']
            and receipt['outcome'] == 'pending', 'recurring_receipt_changed')
        trial.hash_value(receipt['token_sha256'])
        trial.hash_value(receipt['previous_terminal_sha256'])
        return receipt

    def _audit_bytes(self, run_id):
        with self.files.open(filename('audit', run_id)) as stream:
            raw = stream.read(trial.MAX_AUDIT + 1)
        validate_audit(raw)
        return raw

    def _completed_evidence(self, run_id, receipt):
        for kind in ('worker', 'armed', 'model', 'claim'):
            require(self.files.read(filename(kind, run_id)) == receipt, 'recurring_marker_changed')
        artifacts = {kind: self.files.read(filename(kind, run_id)) for kind in trial.ARTIFACTS}
        # Validate historical snapshots at the original run's deadline, not now:
        # old evidence is supposed to be old, but its schema/provenance must hold.
        analysis = adapter.validate_response(encode(artifacts['analysis']), 'prepare',
                                             now=receipt['created_unix'] + MAX_SECONDS - 1)
        identifier = analysis['result']['analysis_id']
        submission = artifacts['submission']
        adapter.request(encode({'method': 'publish', 'payload': submission}))
        require(submission['analysis_id'] == identifier, 'recurring_result_mismatch')
        adapter.narrative(submission, analysis['result']['analysis'])
        adapter.validate_response(encode(artifacts['result']), 'publish', identifier)
        hashes = {kind: digest(value) for kind, value in artifacts.items()}
        diagnostic = artifacts['diagnostic']
        require(set(diagnostic) == {'schema_version', 'scope', 'model_started', 'model_completed',
                                    'mcp_items', 'total_tokens'}
                and type(diagnostic['schema_version']) is int and diagnostic['schema_version'] == 1
                and diagnostic['scope'] == 'production'
                and diagnostic.get('model_started') is True and diagnostic.get('model_completed') is True
                and type(diagnostic.get('mcp_items')) is int and 2 <= diagnostic['mcp_items'] <= 64,
                'recurring_completion_missing')
        require(diagnostic['total_tokens'] is None or (type(diagnostic['total_tokens']) is int
                and 0 <= diagnostic['total_tokens'] < 1_000_000), 'recurring_completion_missing')
        raw = self._audit_bytes(run_id)
        rows = validate_audit(raw)
        require(rows and rows[-1]['event'] == 'closed' and all(row['code'] == 'none' for row in rows),
                'recurring_completion_missing')
        events = [row['event'] for row in rows]
        expected = ['reserved', 'worker_claimed', 'runtime_verified', 'armed', 'model_started',
                    'mcp_claimed', 'transport_started', 'transport_returned', 'analysis_saved',
                    'submission_saved', 'transport_started', 'transport_returned',
                    'publication_acknowledged', 'model_completed', 'closed']
        require(events == expected, 'recurring_completion_missing')
        require([(row['method'], row['attempt']) for row in rows if row['event'] == 'transport_started']
                == [('prepare', 1), ('publish', 2)], 'recurring_transport_count')
        hashes['audit'] = hashlib.sha256(raw).hexdigest()
        return hashes

    def history(self, grant):
        inventory = {}
        for name in self.files.names():
            parsed = validate_name(name)
            if parsed:
                run_id, kind = parsed
                inventory.setdefault(run_id, set()).add(kind)
        require(len(inventory) <= grant['max_runs'], 'recurring_run_limit')
        previous = ZERO_HASH
        latest_created = latest_completed = None
        for index, (run_id, kinds) in enumerate(sorted(inventory.items()), 1):
            require(run_id == f'{index:06d}' and kinds == KINDS, 'recurring_prior_run_needs_review')
            receipt = self._receipt(run_id, grant)
            require(receipt['previous_terminal_sha256'] == previous, 'recurring_evidence_chain_changed')
            require(latest_created is None or receipt['created_unix'] - latest_created >= INTERVAL_SECONDS,
                    'recurring_evidence_spacing')
            terminal = self.files.read(filename('terminal', run_id))
            require(terminal.get('outcome') == 'completed', 'recurring_prior_run_needs_review')
            completed = terminal.get('completed_unix')
            require(type(completed) is int and receipt['created_unix'] <= completed
                    < receipt['created_unix'] + MAX_SECONDS, 'recurring_completion_time')
            require(terminal == receipt | {'outcome': 'completed', 'completed_unix': completed,
                    'evidence_sha256': self._completed_evidence(run_id, receipt)}, 'recurring_evidence_changed')
            previous, latest_created, latest_completed = digest(terminal), receipt['created_unix'], completed
        return {'count': len(inventory), 'previous': previous, 'created': latest_created,
                'completed': latest_completed}

    def available(self):
        """Read-only inventory; no local grant means no run, irrespective of the server gate."""
        self._ready()
        names = self.files.names()
        return {'authorization_present': filename('authorization') in names,
                'run_evidence_present': any(validate_name(name) is not None for name in names)}

    def reserve(self):
        self._ready()
        require(self.run_id is None and self.lock is None, 'recurring_supervisor_only')
        self._grant()  # No grant: do not even create the lock file.
        self.lock = self.files.open(filename('lock'), 'lock')
        grant = self._grant()
        history = self.history(grant)
        now = int(time.time())
        require(history['completed'] is None or now >= history['completed'], 'recurring_clock_reversed')
        require(history['count'] < grant['max_runs'], 'recurring_run_limit')
        due = max(grant['not_before_unix'], (history['created'] + INTERVAL_SECONDS)
                  if history['created'] is not None else 0)
        if now < due:
            return {'status': 'not_due', 'next_due_unix': due}
        require(now + MAX_SECONDS < grant['expires_unix'], 'recurring_authorization_ending')
        self.run_id = f"{history['count'] + 1:06d}"
        token = secrets.token_hex(32)
        receipt = {'schema_version': 1, 'scope': 'production', 'run_id': self.run_id,
            'bundle_sha256': self.bundle, 'authorization_sha256': digest(grant),
            'token_sha256': trial.token_hash(token), 'created_unix': now,
            'previous_terminal_sha256': history['previous'], 'outcome': 'pending'}
        self.files.create(self._name('receipt'), receipt)
        self.token = token
        with self.files.open(self._name('audit'), 'create') as stream:
            trial._persist(stream, b'')
        self.record('reserved')
        return {'status': 'reserved', 'run_id': self.run_id, 'token': token}

    def active(self, token, *, armed=True):
        self._ready()
        receipt = self._receipt(self.run_id, self._grant())
        require(hmac.compare_digest(receipt['token_sha256'], trial.token_hash(token)), 'recurring_token_refused')
        require(0 <= time.time() - receipt['created_unix'] < MAX_SECONDS
                and self._name('terminal') not in self.files.names(), 'recurring_run_inactive')
        if armed:
            require(self.read('armed') == receipt, 'recurring_run_not_armed')
        self.token = token
        return receipt

    def _mark(self, kind, token, *, prerequisite=None, armed=True, event):
        receipt = self.active(token, armed=armed)
        if prerequisite:
            require(self.read(prerequisite) == receipt, 'recurring_marker_missing')
        self.files.create(self._name(kind), receipt)
        self.record(event)

    def claim_worker(self, token):
        self._mark('worker', token, armed=False, event='worker_claimed')

    def arm(self, token):
        self._mark('armed', token, prerequisite='worker', armed=False, event='armed')

    def start_model(self, token):
        self._mark('model', token, prerequisite='worker', event='model_started')

    def claim(self, token):
        self._mark('claim', token, prerequisite='model', event='mcp_claimed')

    def save(self, kind, value):
        require(kind in trial.ARTIFACTS, 'recurring_artifact_refused')
        receipt = self.active(self.token)
        if kind != 'diagnostic':
            require(self.read('claim') == receipt, 'recurring_marker_missing')
        self.files.create(self._name(kind), value)
        require(self.read(kind) == value)

    def record(self, event, *, method='none', attempt=0, code='none'):
        self._ready()
        try:
            require(self.token is not None and self._name('terminal') not in self.files.names())
            row = {'timestamp_utc': datetime.now(timezone.utc).isoformat(), 'event': event,
                   'method': method, 'attempt': attempt, 'code': code}
            body = trial._body(row, trial.MAX_AUDIT)
            validate_audit(body)
            with self.files.open(self._name('audit'), 'append') as stream:
                old = stream.read(trial.MAX_AUDIT + 1)
                validate_audit(old + body)
                stream.seek(0, os.SEEK_END)
                require(stream.tell() == len(old))
                trial._persist(stream, body)
        except Exception:
            self.failed = True
            raise ValueError('recurring_audit_failed') from None

    def finish(self, token, outcome):
        require(not self.closed and outcome in ('completed', 'uncertain'))
        # Expiry does not prevent closure. A removed/changed grant can instead
        # leave a pending receipt, which also blocks every subsequent attempt.
        grant = self.files.read(filename('authorization'), 4096)
        receipt = self._receipt(self.run_id, grant)
        require(hmac.compare_digest(receipt['token_sha256'], trial.token_hash(token)))
        require(self._name('terminal') not in self.files.names(), 'recurring_terminal_exists')
        self.token = token
        completed = int(time.time())
        if self.failed or not receipt['created_unix'] <= completed < receipt['created_unix'] + MAX_SECONDS:
            outcome = 'uncertain'
        hashes = {}
        try:
            if not self.failed:
                self.record('closed', code='none' if outcome == 'completed' else 'uncertain')
            if outcome == 'completed':
                hashes = self._completed_evidence(self.run_id, receipt)
        except BaseException:
            outcome = 'uncertain'
            raise
        finally:
            self.files.create(self._name('terminal'), receipt | {'outcome': outcome,
                              'completed_unix': completed, 'evidence_sha256': hashes})
        require(outcome == 'completed', 'recurring_prior_run_needs_review')

    def close(self):
        self.token = None
        self.closed = True
        if self.lock is not None:
            lock, self.lock = self.lock, None
            lock.close()


def storage_check():
    """Retained harmless file: verify actual private ACLs and exclusive handles."""
    files = Files()
    names = files.names()
    require(filename('authorization') not in names and not any(validate_name(n) for n in names),
            'recurring_storage_check_disabled_only')
    name = filename('storage-probe')
    created = name not in names
    if created:
        files.create(name, PROBE)
    require(files.read(name) == PROBE, 'recurring_probe_changed')
    with files.open(name):
        try:
            second = files.open(name)
        except ValueError as error:
            require(str(error) == 'recurring_file_open_refused', 'recurring_exclusive_check_failed')
        else:
            second.close()
            raise ValueError('recurring_exclusive_check_failed')
    require(files.read(name) == PROBE)
    with files.open(filename('lock'), 'lock'):
        try:
            second = files.open(filename('lock'), 'lock')
        except ValueError as error:
            require(str(error) == 'recurring_busy', 'recurring_exclusive_check_failed')
        else:
            second.close()
            raise ValueError('recurring_exclusive_check_failed')
    # Closing the owning handle releases the lock without deleting its file.
    with files.open(filename('lock'), 'lock'):
        pass
    return {'private_storage_verified': True, 'exclusive_handle_verified': True,
            'supervisor_lock_verified': True, 'synthetic_probe_created': created, 'authorization_created': False,
            'model_started': False, 'ssh_connected': False, 'report_calls_enabled': False}
