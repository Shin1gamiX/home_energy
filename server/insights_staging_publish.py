"""Fixed-command staging publisher; never selects the production report store.

Install only after separate approval, under the dedicated staging SSH account.
The fixed history directory must contain administrator-provisioned synthetic
fixtures only. This module creates no account, directory, credential or schedule.
"""
import os
from pathlib import Path
import re
import stat
import sys

# The isolated interpreter may import only these administrator-owned modules.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import insights_publish as publisher


USER = 'homeenergy-reporter-staging'
MODULES = Path('/opt/homeenergy-reports-staging')
STATE = Path('/var/lib/homeenergy-reports-staging')
HISTORY = Path('/var/lib/homeenergy-report-fixtures/history')
COMMANDS = frozenset(('prepare', 'publish'))
MODULE_NAMES = ('insights_staging_publish.py', 'insights_publish.py',
                'insights_analysis.py', 'insights_i18n.py')
MAX_RESPONSE = 65536
ERROR = '{"error":"Staging report request rejected or temporarily unavailable"}'


def require(condition):
    if not condition:
        raise publisher.Rejected('Staging request rejected')


def trusted_path(path, owner, mode, *, directory=False, allowed_parent=None):
    """Check fixed Linux ownership/modes; this does not replace an ACL audit."""
    require(path.is_absolute() and path.resolve(strict=True) == path)
    info = path.lstat()
    require((stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            and info.st_uid == owner and stat.S_IMODE(info.st_mode) == mode)
    if not directory:
        require(info.st_nlink == 1)
    for parent in path.parents:
        # The private public/ subdirectory has the staging-owned state as parent.
        if parent == allowed_parent:
            continue
        meta = parent.lstat()
        require(stat.S_ISDIR(meta.st_mode) and meta.st_uid == 0
                and not stat.S_IMODE(meta.st_mode) & 0o022)


def verify_environment():
    """Fail closed outside the reviewed, fixed staging Linux installation."""
    require(sys.platform == 'linux')
    import pwd
    account = pwd.getpwnam(USER)
    require(account.pw_uid > 0 and os.geteuid() == account.pw_uid)
    require(Path(__file__).resolve() == MODULES / 'insights_staging_publish.py')
    require(Path(publisher.__file__).resolve() == MODULES / 'insights_publish.py')
    trusted_path(MODULES, 0, 0o755, directory=True)
    for name in MODULE_NAMES:
        trusted_path(MODULES / name, 0, 0o644)
    trusted_path(STATE, account.pw_uid, 0o700, directory=True)
    trusted_path(STATE / 'public', account.pw_uid, 0o700, directory=True,
                 allowed_parent=STATE)
    trusted_path(HISTORY, 0, 0o755, directory=True)
    for index, path in enumerate(HISTORY.iterdir()):
        require(index < 64 and re.fullmatch(r'\d{4}-\d{2}-\d{2}\.json', path.name))
        trusted_path(path, 0, 0o644)


def dispatch(command, raw, *, state, history, now=None):
    """Trusted-code helper; paths are never accepted by the SSH/MCP contract.

    Explicit paths make temporary fixture tests possible without production
    defaults. The CLI below passes only the three fixed staging constants.
    """
    require(isinstance(command, str) and command in COMMANDS)
    require(isinstance(raw, bytes))
    if command == 'prepare':
        require(raw == b'')
        result = publisher.prepare(state=state, history=history, now=now)
    else:
        result = publisher.publish(raw, state=state, now=now)
        require(isinstance(result, dict) and set(result) == {'status', 'id'})
        require(result['status'] in ('published', 'already_published'))
        result = {'status': {'published': 'staged',
                             'already_published': 'already_staged'}[result['status']],
                  'id': result['id']}
    response = {'scope': 'staging', 'synthetic': True, 'result': result}
    require(len(publisher.encoded(response)) <= MAX_RESPONSE)
    return response


def main():
    # Reject arguments/commands before reading input or touching any state.
    require(len(sys.argv) == 1)
    command = os.environ.get('SSH_ORIGINAL_COMMAND', '')
    require(command in COMMANDS)
    verify_environment()
    os.umask(0o077)
    raw = b'' if command == 'prepare' else sys.stdin.buffer.read(publisher.MAX_INPUT + 1)
    result = dispatch(command, raw, state=STATE, history=HISTORY)
    print(publisher.encoded(result).decode('utf-8'))


if __name__ == '__main__':
    try:
        main()
    except (publisher.Rejected, OSError, ValueError, TypeError, KeyError,
            publisher.sqlite3.Error):
        # No path, submitted prose, system error or raw upstream diagnostics.
        print(ERROR, file=sys.stderr)
        sys.exit(1)
