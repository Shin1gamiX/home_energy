"""Prepared administrator procedure; no action occurs on import.

--check is read-only. A later, explicitly approved --activate opens the public
gate last. --rollback closes it first and freezes report storage without erasing
reports. --close-gate is an independent emergency stop, not a full rollback.
This helper never calls prepare/publish, starts a model, or changes a schedule.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace


ADMIN_HOME = Path('/home/shini')
MODULES = Path('/opt/homeenergy-reports')
STATE = Path('/var/lib/homeenergy-reports')
PUBLIC = STATE / 'public'
GATE = MODULES / 'production-gate.json'
ACTIVATION = MODULES / 'activation.json'
ACCESS = ADMIN_HOME / 'homeenergy-report-access.zduokPzZ/activate-report-access.py'
ACCESS_SHA256 = '850600758a3ef63c290fb42aea5360d6000257f5600294c9dacfb7ee2a536cef'
ACCESS_BACKUP = ADMIN_HOME / 'homeenergy-report-access-backup.miikacxu'
SCOPE = 'report-publication-activation-v1'
UID, GID = 996, 986
DIRECTORIES = ((STATE, 0o711), (PUBLIC, 0o755))
PRIVATE_FILES = frozenset(('reports.sqlite3', 'publisher.lock', 'reports.sqlite3-journal',
                           'reports.sqlite3-wal', 'reports.sqlite3-shm'))
ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'}
MAX_FILE = 32 * 1024 * 1024  # First controlled trial, not an unbounded archive migration.
DRAIN_SECONDS = 55  # Installed forced command: timeout 45s plus kill-after 5s.
READY_PROBE = '''import json,os,runpy
checks=runpy.run_path("/opt/homeenergy-reports/insights_production_publish.py")
publisher=checks["verify_environment"]()
print(json.dumps({"uid":os.getuid(),"euid":os.geteuid(),"gid":os.getgid(),"egid":os.getegid(),"groups":sorted(os.getgroups()),"publisher_loaded":publisher.__name__=="insights_publish"},sort_keys=True))
'''


class ProcedureError(RuntimeError):
    """A fixed, secret-free administrator diagnostic from this helper."""


def require(condition, message):
    if not condition:
        raise ProcedureError(message)


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode() + b'\n'


def digest(body):
    return hashlib.sha256(body).hexdigest()


def decode(body):
    def pairs(values):
        answer = {}
        for key, value in values:
            require(key not in answer, 'Duplicate evidence field')
            answer[key] = value
        return answer
    require(type(body) is bytes and 0 < len(body) <= 65536, 'Evidence size refused')
    value = json.loads(body, object_pairs_hook=pairs,
        parse_constant=lambda _: require(False, 'Nonfinite evidence value'))
    require(type(value) is dict and encoded(value) == body, 'Noncanonical evidence')
    return value


def canonical(path):
    require(path.is_absolute() and path.resolve(strict=True) == path, 'Linked or noncanonical path')
    return path.lstat()


def no_acl(path):
    require(not any(name.startswith('system.posix_acl_') for name in
                    os.listxattr(path, follow_symlinks=False)), 'Unexpected POSIX ACL; separate review required')


def identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid, info.st_nlink)


def root_path(path, mode, *, directory=False, admin_parent=False):
    info = canonical(path)
    require((stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            and info.st_uid == info.st_gid == 0 and stat.S_IMODE(info.st_mode) == mode
            and (directory or info.st_nlink == 1), 'Unexpected root-owned path metadata')
    import pwd
    for parent in path.parents:
        item = canonical(parent)
        owner = pwd.getpwnam('shini').pw_uid if admin_parent and parent == ADMIN_HOME else 0
        require(stat.S_ISDIR(item.st_mode) and item.st_uid == owner and not item.st_mode & 0o022,
                'Untrusted parent directory')
    no_acl(path)
    return info


def read_file(path, maximum=65536):
    before = canonical(path)
    require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= maximum,
            'Linked, special or oversized file')
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as stream:
        opened = os.fstat(stream.fileno())
        require(identity(opened) == identity(before), 'File changed while opening')
        body = stream.read(maximum + 1)
        require(identity(os.fstat(stream.fileno())) == identity(before), 'File changed while reading')
    require(len(body) == before.st_size and len(body) <= maximum, 'File size changed')
    return body


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def validate_release():
    """Fixed private administrator release; no caller-selected paths or sources."""
    import pwd
    entry = Path(__file__).absolute()
    source = entry.parent
    admin = pwd.getpwnam('shini').pw_uid
    require(source.parent == ADMIN_HOME and re.fullmatch(r'homeenergy-report-activation\.[A-Za-z0-9_-]+', source.name)
            and entry.name == 'activate-report-production.py', 'Unexpected activation release')
    for path in (entry, source, *source.parents):
        info = canonical(path)
        require(info.st_uid == (admin if path in (entry, source, ADMIN_HOME) else 0)
                and not info.st_mode & 0o022, 'Untrusted activation release')
        no_acl(path)
    require(stat.S_IMODE(source.stat().st_mode) == 0o700
            and stat.S_IMODE(entry.stat().st_mode) == 0o600
            and {p.name for p in source.iterdir()} == {entry.name}, 'Unexpected release contents/modes')
    return digest(read_file(entry, 131072))


def load_context(helper_hash):
    """Reuse frozen access checks; never execute their CLI/mutation functions."""
    import pwd
    admin = pwd.getpwnam('shini').pw_uid
    for path in (ACCESS, ACCESS.parent, *ACCESS.parent.parents):
        info = canonical(path)
        require(info.st_uid == (admin if path in (ACCESS, ACCESS.parent, ADMIN_HOME) else 0)
                and not info.st_mode & 0o022, 'Previous access release metadata changed')
    require(stat.S_IMODE(ACCESS.parent.stat().st_mode) == 0o700
            and stat.S_IMODE(ACCESS.stat().st_mode) == 0o600, 'Previous access release is not private')
    body = read_file(ACCESS, 65536)
    require(digest(body) == ACCESS_SHA256, 'Previous access source changed')
    access = {'__name__': '_activation_access_checks', '__file__': str(ACCESS)}
    exec(compile(body, str(ACCESS), 'exec', dont_inherit=True), access)
    recovery, checks = access['load_prior']()
    key = access['read_bundle'](checks)
    _, bodies = checks['read_bundle'](recovery['RELEASE'])
    root_path(ACCESS_BACKUP, 0o700, directory=True, admin_parent=True)
    for name in ('baseline.json', 'completed.json'):
        root_path(ACCESS_BACKUP / name, 0o600, admin_parent=True)
    raw = read_file(ACCESS_BACKUP / 'baseline.json')
    before = decode(raw)
    completion = decode(read_file(ACCESS_BACKUP / 'completed.json'))
    require(completion == {'schema_version': 1, 'scope': before['scope'],
            'baseline_sha256': digest(raw), 'public_publishing_enabled': False,
            'client_authentication_verified': False}, 'Prior access evidence mismatch')
    require(before['uid'] == UID and before['gid'] == GID, 'Prior identity changed')
    return SimpleNamespace(access=access, recovery=recovery, checks=checks, key=key,
        policy=bodies['reports-production-sshd.conf.example'], access_before=before,
        access_hash=digest(raw), helper_hash=helper_hash)


def gate_body(enabled):
    require(type(enabled) is bool, 'Gate state must be boolean')
    return encoded({'schema_version': 1, 'scope': 'production', 'public_publishing_enabled': enabled})


def read_gate():
    root_path(MODULES, 0o755, directory=True)
    root_path(GATE, 0o644)
    body = read_file(GATE, 256)
    require(body in (gate_body(False), gate_body(True)), 'Unexpected gate contents; preserve for review')
    return body == gate_body(True)


def write_gate(enabled):
    """Atomic fixed-file replacement; old bytes are a known exact gate state."""
    require(type(enabled) is bool, 'Gate state must be boolean')
    current = read_gate()
    if current == enabled:
        return
    before = identity(GATE.lstat())
    descriptor, temporary = tempfile.mkstemp(prefix='.production-gate-', suffix='.tmp', dir=MODULES)
    # A failed temporary file is deliberately retained, never blindly removed.
    with os.fdopen(descriptor, 'wb') as stream:
        require(stream.write(gate_body(enabled)) == len(gate_body(enabled)), 'Short gate write')
        os.fchown(stream.fileno(), 0, 0)
        os.fchmod(stream.fileno(), 0o644)
        stream.flush()
        os.fsync(stream.fileno())
    require(identity(GATE.lstat()) == before and read_gate() == current, 'Gate changed before replacement')
    root_path(Path(temporary), 0o644)
    require(read_file(Path(temporary), 256) == gate_body(enabled), 'Gate temporary read-back failed')
    os.replace(temporary, GATE)
    sync_directory(MODULES)
    require(read_gate() == enabled, 'Gate replacement did not verify')


def directory_metadata():
    result = {}
    for path, mode in DIRECTORIES:
        info = canonical(path)
        require(stat.S_ISDIR(info.st_mode) and stat.S_IMODE(info.st_mode) == mode
                and (info.st_uid, info.st_gid) in ((0, 0), (UID, GID)), 'Unexpected report directory metadata')
        no_acl(path)
        result[str(path)] = {'device': info.st_dev, 'inode': info.st_ino,
                            'uid': info.st_uid, 'gid': info.st_gid, 'mode': mode}
    return result


def empty_state(ctx):
    require({p.name for p in STATE.iterdir()} == {'public'}
            and {p.name for p in PUBLIC.iterdir()} == {'index.json'}, 'Existing report data; first activation refused')
    if STATE.stat().st_uid == 0:
        root_path(PUBLIC / 'index.json', 0o644)
    else:
        no_acl(PUBLIC / 'index.json')
    require(digest(read_file(PUBLIC / 'index.json')) == ctx.checks['EMPTY_FEED_SHA256'], 'Feed is not the original empty feed')


def preflight(ctx):
    require(not os.path.lexists(ACTIVATION), 'Activation intent already exists; do not rerun')
    ctx.access['audit'](ctx.recovery, ctx.checks, ctx.policy, ctx.key, ctx.access_before, expiry=-1)
    require(read_gate() is False, 'Public gate must remain closed')
    directories = directory_metadata()
    require(all(row['uid'] == row['gid'] == 0 for row in directories.values()), 'Storage is not disabled/root-owned')
    empty_state(ctx)
    return {'schema_version': 1, 'scope': SCOPE, 'helper_sha256': ctx.helper_hash,
            'access_baseline_sha256': ctx.access_hash, 'uid': UID, 'gid': GID,
            'directories': directories, 'preserved': ctx.checks['preserved_snapshot']()}


def backup_path(path):
    require(path.parent == ADMIN_HOME and re.fullmatch(r'homeenergy-report-activation-backup\.[A-Za-z0-9_-]+', path.name),
            'Unexpected activation backup path')
    root_path(path, 0o700, directory=True, admin_parent=True)


def new_evidence(backup, name, value):
    backup_path(backup)
    require(name == 'baseline.json' or re.fullmatch(r'(activated|rollback-before|rollback-complete|failed)-[a-f0-9]{16}\.json', name),
            'Evidence name refused')
    body = encoded(value)
    require(len(body) <= 65536, 'Evidence exceeds bound')
    path = backup / name
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), 'wb') as stream:
        require(stream.write(body) == len(body), 'Short evidence write')
        os.fchown(stream.fileno(), 0, 0)
        os.fchmod(stream.fileno(), 0o600)
        stream.flush()
        os.fsync(stream.fileno())
    sync_directory(backup)
    root_path(path, 0o600, admin_parent=True)
    require(read_file(path) == body, 'Evidence read-back mismatch')


def record(backup, event, **values):
    require(event in ('activated', 'rollback-before', 'rollback-complete', 'failed'), 'Evidence event refused')
    new_evidence(backup, event + '-' + secrets.token_hex(8) + '.json', {
        'schema_version': 1, 'scope': SCOPE, 'event': event,
        'timestamp_utc': datetime.now(timezone.utc).isoformat(), **values})


@contextmanager
def procedure_lock():
    """Serialize all modes on a root-owned directory; no lock file is created."""
    import fcntl
    before = root_path(MODULES, 0o755, directory=True)
    descriptor = os.open(MODULES, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        require(identity(os.fstat(descriptor)) == identity(before), 'Procedure lock directory changed')
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(descriptor)


@contextmanager
def activation_lock(intent=None):
    """One immutable intent is also the serialization lock; never delete/reset."""
    import fcntl
    root_path(MODULES, 0o755, directory=True)
    if intent is None:
        root_path(ACTIVATION, 0o600)
    flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
    if intent is not None:
        flags |= os.O_CREAT | os.O_EXCL
    descriptor = os.open(ACTIVATION, flags, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        info = os.fstat(descriptor)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == info.st_gid == 0
                and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1
                and identity(info) == identity(canonical(ACTIVATION)), 'Activation intent handle changed')
        no_acl(ACTIVATION)
        if intent is not None:
            body = encoded(intent)
            require(os.write(descriptor, body) == len(body), 'Short activation intent write')
            os.fsync(descriptor)
            sync_directory(MODULES)
            require(read_file(ACTIVATION, 2048) == body, 'Intent read-back mismatch')
        yield decode(read_file(ACTIVATION, 2048))
    finally:
        os.close(descriptor)


def load_baseline(ctx, intent):
    require(set(intent) == {'schema_version', 'scope', 'backup', 'baseline_sha256', 'helper_sha256'}
            and type(intent['schema_version']) is int and intent['schema_version'] == 1
            and intent['scope'] == SCOPE and intent['helper_sha256'] == ctx.helper_hash,
            'Unexpected activation intent')
    backup = Path(intent['backup'])
    backup_path(backup)
    root_path(backup / 'baseline.json', 0o600, admin_parent=True)
    raw = read_file(backup / 'baseline.json')
    require(digest(raw) == intent['baseline_sha256'], 'Activation baseline changed')
    before = decode(raw)
    require(set(before) == {'schema_version', 'scope', 'helper_sha256', 'access_baseline_sha256',
                            'uid', 'gid', 'directories', 'preserved'}
            and type(before['schema_version']) is int and before['schema_version'] == 1
            and before['scope'] == SCOPE and before['helper_sha256'] == ctx.helper_hash
            and before['access_baseline_sha256'] == ctx.access_hash
            and type(before['uid']) is int and before['uid'] == UID
            and type(before['gid']) is int and before['gid'] == GID, 'Unreviewed activation baseline')
    ctx.checks['validate_services'](before['preserved']['services'])
    require(set(before['directories']) == {str(path) for path, _ in DIRECTORIES}, 'Unexpected directory baseline')
    for path, mode in DIRECTORIES:
        row = before['directories'][str(path)]
        require(set(row) == {'device', 'inode', 'uid', 'gid', 'mode'}
                and all(type(value) is int and value >= 0 for value in row.values())
                and row['uid'] == row['gid'] == 0 and row['mode'] == mode, 'Invalid original directory metadata')
    return backup, before


def set_directories(before, *, enabled):
    """Only the two recorded directory inodes; no recursive chown/chmod."""
    require(read_gate() is False, 'Ownership may change only with the gate closed')
    for path, mode in DIRECTORIES:
        info = canonical(path)
        row = before['directories'][str(path)]
        require(stat.S_ISDIR(info.st_mode) and (info.st_dev, info.st_ino) == (row['device'], row['inode'])
                and (info.st_uid, info.st_gid) in ((0, 0), (UID, GID))
                and stat.S_IMODE(info.st_mode) == mode, 'Report directory replaced or metadata changed')
        no_acl(path)
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            require(identity(os.fstat(descriptor)) == identity(info), 'Report directory changed while opening')
            os.fchown(descriptor, UID if enabled else 0, GID if enabled else 0)
            os.fchmod(descriptor, mode)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        after = canonical(path)
        require((after.st_dev, after.st_ino, after.st_uid, after.st_gid, stat.S_IMODE(after.st_mode)) ==
                (row['device'], row['inode'], UID if enabled else 0, GID if enabled else 0, mode),
                'Directory ownership read-back mismatch')


def verify_common(ctx, before, *, enabled, allow_report_changes=False):
    checks, access = ctx.checks, ctx.access
    account, shadow = access['account_state'](ctx.recovery, checks, -1)
    require((account.pw_uid, account.pw_gid) == (UID, GID)
            and access['shadow_metadata'](shadow) == ctx.access_before['shadow_metadata'], 'Identity changed')
    require(checks['absent'](Path('/etc/ssh/sshrc')), 'Global SSH rc appeared')
    checks['run'](['/usr/sbin/sshd', '-t'])
    access['restricted'](access['effective'](checks, checks['USER']))
    require(all(access['effective'](checks, user) == settings for user, settings in ctx.access_before['other_ssh'].items()),
            'Unrelated effective SSH policy changed')
    for path, body in ((checks['KEY'], ctx.key), (checks['INCLUDE'], ctx.policy)):
        checks['trusted'](path, mode=0o644, directory=False)
        require(checks['read_file'](path) == body, 'SSH access file changed')
    require(checks['digest'](checks['read_file'](checks['RECEIPT'])) == ctx.access_before['preparation_sha256']
            and ctx.recovery['evidence_digest'](checks) == ctx.access_before['original_evidence'], 'Original preparation evidence changed')
    _, bodies = checks['read_bundle'](ctx.recovery['RELEASE'])
    root_path(checks['WRAPPER'], 0o644)
    require(checks['read_file'](checks['WRAPPER']) == bodies[checks['WRAPPER'].name], 'Reporting wrapper changed')
    require({p.name for p in MODULES.iterdir()} == set(checks['OLD_MODULES']) |
            {checks['WRAPPER'].name, GATE.name, checks['RECEIPT'].name, ACTIVATION.name}, 'Module inventory changed')
    root_path(ACTIVATION, 0o600)
    directories = directory_metadata()
    for path, mode in DIRECTORIES:
        require(directories[str(path)] == before['directories'][str(path)] |
                {'uid': UID if enabled else 0, 'gid': GID if enabled else 0}, 'Directory state mismatch')
    current = checks['preserved_snapshot']()
    expected = before['preserved']
    require(set(current['files']) == set(expected['files']) and current['services'] == expected['services'],
            'Protected file inventory or service state changed')
    exceptions = {str(path) for path, _ in DIRECTORIES}
    if allow_report_changes:
        exceptions.add(str(PUBLIC / 'index.json'))
    require(all(value == expected['files'][name] for name, value in current['files'].items() if name not in exceptions),
            'An unrelated protected file changed')
    for root in checks['PROTECTED_TREES']:
        if root != STATE:
            checks['audit_tree'](account, root)
    checks['no_write_access'](account, list(STATE.parents))
    for path in checks['PRIVATE_PATHS']:
        if path.exists():
            require(not checks['access_allowed'](account, '-x', path), 'Private directory traversal allowed')
    for path, _ in DIRECTORIES:
        require(checks['access_allowed'](account, '-w', path) is enabled, 'Report directory access mismatch')
    if not allow_report_changes:
        empty_state(ctx)
    return account


def verify_ready(ctx, before, *, gate_enabled):
    require(read_gate() is gate_enabled, 'Unexpected publication gate state')
    account = verify_common(ctx, before, enabled=True)
    if not gate_enabled:
        ctx.checks['verify_gate_rejection'](account)
        return
    result = subprocess.run(['/usr/bin/python3', '-I', '-B', '-c', READY_PROBE],
        capture_output=True, text=True, timeout=10, check=False, env=ENV,
        user=UID, group=GID, extra_groups=[GID])
    require(result.returncode == 0 and result.stderr == '' and len(result.stdout) <= 2048,
            'Readiness probe failed; no report was requested')
    require(json.loads(result.stdout) == {'uid': UID, 'euid': UID, 'gid': GID, 'egid': GID,
            'groups': [GID], 'publisher_loaded': True}, 'Readiness probe identity mismatch')
    empty_state(ctx)  # Import/verification must not create a snapshot, lock or database.


def drain_reporter():
    """No process kill or broad service restart; wait only for the fixed UID."""
    require(read_gate() is False, 'Drain requires a closed gate')
    deadline = time.monotonic() + DRAIN_SECONDS
    while True:
        result = subprocess.run(['/usr/bin/ps', '-o', 'pid=', '-u', str(UID)],
                                capture_output=True, text=True, timeout=5, check=False, env=ENV)
        require(result.stderr == '' and len(result.stdout) <= 4096 and result.returncode in (0, 1), 'Process inspection failed')
        rows = result.stdout.split()
        require(all(re.fullmatch(r'[1-9][0-9]*', row) for row in rows)
                and ((not rows and result.returncode == 1) or (bool(rows) and result.returncode == 0)),
                'Process inspection was not conclusive')
        if not rows:
            return
        require(time.monotonic() < deadline, 'Reporter did not drain; gate closed, ownership/evidence retained for review')
        time.sleep(0.25)


def report_files():
    """Strict first-trial inventory; unexpected entries are retained, not followed."""
    require({p.name for p in STATE.iterdir()} <= PRIVATE_FILES | {'public'}, 'Unexpected report-state entry')
    paths = [path for path in STATE.iterdir() if path.name != 'public']
    for path in PUBLIC.iterdir():
        require(path.name == 'index.json' or re.fullmatch(r'\.feed-[A-Za-z0-9_-]{1,64}\.tmp', path.name),
                'Unexpected public-state entry')
        paths.append(path)
    require(0 < len(paths) <= 16 and (PUBLIC / 'index.json') in paths, 'Report-state inventory bound')
    answer = {}
    for path in sorted(paths):
        info = canonical(path)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                and (info.st_uid, info.st_gid) in ((0, 0), (UID, GID))
                and stat.S_IMODE(info.st_mode) in (0o600, 0o644), 'Unexpected state-file metadata')
        no_acl(path)
        answer[str(path)] = {'device': info.st_dev, 'inode': info.st_ino, 'uid': info.st_uid,
            'gid': info.st_gid, 'mode': stat.S_IMODE(info.st_mode), 'sha256': digest(read_file(path, MAX_FILE))}
    return answer


def freeze_files(before):
    """Freeze exact already-inventoried files; never restore earlier contents."""
    require(read_gate() is False, 'Freeze requires a closed gate')
    require(report_files() == before, 'Report files changed before freeze')
    for name, row in before.items():
        path = Path(name)
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(descriptor)
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                    and (info.st_dev, info.st_ino, info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) ==
                    (row['device'], row['inode'], row['uid'], row['gid'], row['mode']), 'Freeze handle mismatch')
            os.fchown(descriptor, 0, 0)
            os.fchmod(descriptor, 0o644 if path == PUBLIC / 'index.json' else 0o600)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    after = report_files()
    expected = {name: row | {'uid': 0, 'gid': 0, 'mode': 0o644 if Path(name) == PUBLIC / 'index.json' else 0o600}
                for name, row in before.items()}
    require(after == expected, 'Freeze changed bytes/inodes or metadata did not verify')
    return after


def rollback(ctx, backup, before):
    # Do not make restoration conditional on unrelated services/files still
    # matching: closing the gate and draining come before those later audits.
    write_gate(False)
    drain_reporter()
    set_directories(before, enabled=False)
    files = report_files()
    record(backup, 'rollback-before', files=files, public_publishing_enabled=False)
    frozen = freeze_files(files)
    account = verify_common(ctx, before, enabled=False, allow_report_changes=True)
    ctx.checks['no_write_access'](account, [STATE, PUBLIC, *map(Path, frozen)])
    ctx.checks['verify_gate_rejection'](account)
    require(report_files() == frozen and read_gate() is False, 'Rollback verification changed report data')
    record(backup, 'rollback-complete', files=frozen, public_publishing_enabled=False)
    print('Public gate closed and report storage root-owned. Existing reports and all evidence retained.')
    print('No schedule change, model call, report request, account change or service/device restart.')


def activate(ctx):
    before = preflight(ctx)
    backup = Path(tempfile.mkdtemp(prefix='homeenergy-report-activation-backup.', dir=ADMIN_HOME))
    os.chown(backup, 0, 0)
    os.chmod(backup, 0o700)
    new_evidence(backup, 'baseline.json', before)
    intent = {'schema_version': 1, 'scope': SCOPE, 'backup': str(backup),
              'baseline_sha256': digest(encoded(before)), 'helper_sha256': ctx.helper_hash}
    print('Private activation/rollback evidence: ' + str(backup), flush=True)
    require(preflight(ctx) == before, 'State changed before activation; no changes adopted')
    # A failed/losing intent creation cannot roll back a different invocation.
    with activation_lock(intent):
        try:
            require(read_gate() is False and ctx.checks['preserved_snapshot']() == before['preserved'], 'State changed after intent')
            set_directories(before, enabled=True)
            verify_ready(ctx, before, gate_enabled=False)
            write_gate(True)  # Last enabling mutation, after closed-gate verification.
            verify_ready(ctx, before, gate_enabled=True)
            record(backup, 'activated', public_publishing_enabled=True,
                   baseline_sha256=intent['baseline_sha256'], model_started=False, report_requested=False)
        except BaseException:
            restored = False
            try:
                rollback(ctx, backup, before)
                restored = True
            except BaseException:
                print('Rollback incomplete. Use the separate gate-only stop if needed and request administrator review.', flush=True)
            try:
                record(backup, 'failed', rollback_verified=restored)
            except BaseException:
                print('Failure receipt could not be saved; retain the intent and existing evidence.', flush=True)
            print('Activation failed. Preserve all evidence; never rerun or remove the intent blindly.', flush=True)
            raise
    print('Public report endpoint enabled; no report requested. Scheduling and local run authorization are unchanged.')
    print('No account/SSH policy change, reload, application/device restart or history modification.')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    for name in ('check', 'activate', 'rollback', 'close-gate'):
        modes.add_argument('--' + name, action='store_true')
    parser.add_argument('--approve-public-publishing', action='store_true')
    args = parser.parse_args(argv)
    require(args.approve_public_publishing == args.activate,
            'Only --activate requires --approve-public-publishing; checks cannot enable publication')
    require(sys.platform == 'linux' and os.geteuid() == 0 and sys.flags.isolated == 1
            and sys.dont_write_bytecode, 'Use sudo /usr/bin/python3 -I -B on the reviewed server')
    helper_hash = validate_release()
    os.umask(0o077)
    with procedure_lock():
        if args.close_gate:
            # Independent of old receipts/dependencies. This is not a full rollback
            # or cancellation of a command which has already passed the gate.
            write_gate(False)
            print('Public gate is closed. In-flight work may finish; reports/evidence/ownership were not reverted.')
            return
        ctx = load_context(helper_hash)
        if args.check:
            preflight(ctx)
            print('Read-only activation preflight passed. Public gate and schedule were not changed.')
        elif args.activate:
            activate(ctx)
        else:
            with activation_lock() as intent:
                backup, before = load_baseline(ctx, intent)
                rollback(ctx, backup, before)


if __name__ == '__main__':
    try:
        main()
    except ProcedureError as error:
        print('Production activation procedure stopped: ' + str(error) + '. Preserve evidence and request review.', file=sys.stderr)
        sys.exit(1)
    except Exception:
        # No credential values, submitted prose or raw subprocess output.
        print('Production activation procedure stopped. Preserve evidence and request administrator review.', file=sys.stderr)
        sys.exit(1)
