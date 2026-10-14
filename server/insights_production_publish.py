"""Fixed production report boundary; a root-owned gate defaults to closed.

This file is preparation only until an administrator provisions and enables the
reviewed installation. It never provisions accounts, changes the gate, schedules
work, accepts paths, or controls a device. Importing it does not load the writer.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import types


USER = 'homeenergy-reporter'
HOME = Path('/var/lib/homeenergy-reporter-home')
SHELL = Path('/usr/bin/dash')
MODULES = Path('/opt/homeenergy-reports')
STATE = Path('/var/lib/homeenergy-reports')
HISTORY = Path('/var/www/homeenergy/runtime/history')
GATE = MODULES / 'production-gate.json'
ENTRY = MODULES / 'insights_production_publish.py'
COMMANDS = frozenset(('prepare', 'publish'))
SOURCE_HASHES = {
    'insights_i18n': '957e784430904770d291bfaf2c402b513007d7ff953ae8c30a31f96ff11c31c5',
    'insights_analysis': '3c48a5254c025c8d3bfaf190c4d785bfef2ab800015de0e4fdb69f5363e0c70e',
    'insights_publish': 'c7df78994cdf99a6d0be131f2365e36c37f8a9d9eb9ac83df52ae61995a4c567',
}
MAX_SOURCE = 131072
MAX_INPUT = 32768
MAX_RESPONSE = 65536
ERROR = '{"error":"Report request rejected or temporarily unavailable"}'


class Rejected(ValueError):
    """The CLI deliberately replaces all internal failure details."""


def require(condition):
    if not condition:
        raise Rejected('Production report request rejected')


def encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                      separators=(',', ':')).encode('utf-8')


def _object_pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


def trusted_path(path, owner, mode, *, directory=False, allowed_parent=None):
    """Require fixed ownership, no symlink/hardlink, and trusted ancestors.

    Mode checks are supplemented by a separate administrator POSIX ACL audit.
    The reporter-owned public directory may have only its fixed state parent.
    """
    require(path.is_absolute() and path.resolve(strict=True) == path)
    info = path.lstat()
    require((stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            and info.st_uid == owner and stat.S_IMODE(info.st_mode) == mode)
    if not directory:
        require(info.st_nlink == 1)
    for parent in path.parents:
        if parent == allowed_parent:
            continue
        meta = parent.lstat()
        require(stat.S_ISDIR(meta.st_mode) and meta.st_uid == 0
                and not stat.S_IMODE(meta.st_mode) & 0o022)


def _read_trusted_file(path, maximum):
    trusted_path(path, 0, 0o644)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0
                and stat.S_IMODE(info.st_mode) == 0o644 and info.st_nlink == 1
                and 0 < info.st_size <= maximum)
        body = stream.read(maximum + 1)
    require(0 < len(body) <= maximum)
    return body


def verify_gate():
    """False/missing/invalid gates deny both commands before writer/history work."""
    raw = _read_trusted_file(GATE, 256)
    gate = json.loads(raw.decode('utf-8'), object_pairs_hook=_object_pairs,
                      parse_constant=lambda _: require(False))
    require(type(gate) is dict and set(gate) == {
        'schema_version', 'scope', 'public_publishing_enabled'})
    require(type(gate['schema_version']) is int and gate['schema_version'] == 1
            and gate['scope'] == 'production'
            and type(gate['public_publishing_enabled']) is bool)
    require(gate['public_publishing_enabled'] is True)


def load_publisher():
    """Execute verified source bytes only; never accept importable .pyc files."""
    bodies = {}
    for name, expected in SOURCE_HASHES.items():
        require(name not in sys.modules)
        body = _read_trusted_file(MODULES / (name + '.py'), MAX_SOURCE)
        require(hashlib.sha256(body).hexdigest() == expected)
        bodies[name] = body
    loaded = []
    try:
        # Dependency order is deliberate: imports resolve to these exact bytes.
        for name, body in bodies.items():
            module = types.ModuleType(name)
            module.__file__ = str(MODULES / (name + '.py'))
            module.__package__ = ''
            sys.modules[name] = module
            loaded.append(name)
            exec(compile(body, module.__file__, 'exec'), module.__dict__)
        return sys.modules['insights_publish']
    except BaseException:
        for name in loaded:
            sys.modules.pop(name, None)
        raise


def verify_readonly_history():
    """History/exporter ancestors need not be root-owned, but must be read-only.

    Daily content validation, bounded opens and no-follow checks remain with the
    pinned analyzer. The administrator separately audits the account's ACLs.
    """
    require(HISTORY.resolve(strict=True) == HISTORY)
    for path in (HISTORY, *HISTORY.parents):
        require(stat.S_ISDIR(path.lstat().st_mode)
                and not os.access(path, os.W_OK, effective_ids=True))
    require(os.access(HISTORY, os.R_OK | os.X_OK, effective_ids=True))


def verify_environment():
    """Fail closed outside the dedicated, isolated Linux installation."""
    require(sys.platform == 'linux' and sys.flags.isolated == 1
            and sys.flags.dont_write_bytecode == 1)
    import pwd
    import grp
    account = pwd.getpwnam(USER)
    group = grp.getgrnam(USER)
    require(account.pw_uid > 0 and account.pw_gid > 0 and group.gr_gid == account.pw_gid
            and os.getuid() == os.geteuid() == account.pw_uid
            and os.getgid() == os.getegid() == account.pw_gid
            and set(os.getgroups()) <= {account.pw_gid}
            and account.pw_dir == str(HOME)
            and account.pw_shell in ('/bin/dash', '/usr/bin/dash'))
    require(Path(account.pw_shell).resolve(strict=True) == SHELL)
    require(Path(__file__).resolve() == ENTRY)
    trusted_path(HOME, 0, 0o755, directory=True)
    trusted_path(MODULES, 0, 0o755, directory=True)
    trusted_path(ENTRY, 0, 0o644)
    verify_gate()
    # Do not move any state/history inspection or publisher loading above gate.
    trusted_path(STATE, account.pw_uid, 0o711, directory=True)
    trusted_path(STATE / 'public', account.pw_uid, 0o755, directory=True,
                 allowed_parent=STATE)
    verify_readonly_history()
    return load_publisher()


def validate_result(command, result):
    require(type(result) is dict)
    if command == 'prepare':
        require(set(result) == {'analysis_id', 'analysis'})
        require(type(result['analysis_id']) is str
                and re.fullmatch(r'[a-f0-9]{64}', result['analysis_id']))
        analysis = result['analysis']
        require(type(analysis) is dict and set(analysis) == {
            'schema_version', 'generated_at', 'window', 'baseline', 'data_through',
            'coverage_pct', 'severity', 'metrics', 'findings', 'limitations'})
        require(type(analysis['schema_version']) is int and analysis['schema_version'] == 2)
        body = encoded(analysis)
        require(len(body) <= MAX_INPUT
                and hashlib.sha256(body).hexdigest() == result['analysis_id'])
    else:
        require(set(result) == {'status', 'id'}
                and result['status'] in ('published', 'already_published')
                and type(result['id']) is str and re.fullmatch(r'[a-f0-9]{64}', result['id']))


def dispatch(command, raw, *, state, history, publisher, now=None):
    """Fixture-testable helper; only main supplies fixed production paths.

    The injectable publisher is trusted code, never part of the SSH contract.
    It is responsible for authoritative analysis, translations, idempotence,
    rate limits and immutable report persistence.
    """
    require(type(command) is str and command in COMMANDS)
    require(type(raw) is bytes and len(raw) <= MAX_INPUT)
    require(command != 'prepare' or raw == b'')
    if command == 'prepare':
        result = publisher.prepare(state=state, history=history, now=now)
    else:
        require(bool(raw))
        result = publisher.publish(raw, state=state, now=now)
    validate_result(command, result)
    response = {'scope': 'production', 'synthetic': False, 'result': result}
    require(len(encoded(response)) <= MAX_RESPONSE)
    return response


def main():
    require(len(sys.argv) == 1)
    command = os.environ.get('SSH_ORIGINAL_COMMAND', '')
    require(command in COMMANDS)
    publisher = verify_environment()
    os.umask(0o077)
    raw = b'' if command == 'prepare' else sys.stdin.buffer.read(MAX_INPUT + 1)
    response = dispatch(command, raw, state=STATE, history=HISTORY, publisher=publisher)
    print(encoded(response).decode('utf-8'))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        # No submitted prose, filesystem paths, credentials or underlying errors.
        print(ERROR, file=sys.stderr)
        sys.exit(1)
