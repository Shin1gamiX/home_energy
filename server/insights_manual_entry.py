"""Manual report-only SSH entrypoint. No Codex executable, settings or hash pins."""
import json
import os
from pathlib import Path
import stat
import sys

MODULES = Path('/opt/homeenergy-reports')
USER = 'homeenergy-reporter'
MAX_INPUT = 32768
COMMANDS = ('manual-review', 'manual-prepare', 'manual-publish')


def trusted_code(path):
    info = path.lstat()
    if path.resolve() != path or not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError('Report code must be root-owned and not writable by the reporting account')


def load_modules():
    import pwd
    account = pwd.getpwnam(USER)
    if not (sys.flags.isolated and sys.flags.dont_write_bytecode and
            os.getuid() == os.geteuid() == account.pw_uid != 0 and
            set(os.getgroups()) <= {account.pw_gid}):
        raise ValueError('Dedicated reporting account required')
    for directory in (MODULES, *MODULES.parents):
        info = directory.lstat()
        if directory.resolve() != directory or not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('Untrusted report code directory')
    for name in ('insights_manual_entry.py', 'insights_manual.py', 'insights_analysis.py',
                 'insights_i18n.py', 'insights_publish.py', 'production-gate.json'):
        trusted_code(MODULES / name)
    gate = json.loads((MODULES / 'production-gate.json').read_text(encoding='utf-8'))
    if gate != {'schema_version': 1, 'scope': 'production', 'public_publishing_enabled': True}:
        raise ValueError('Reporting is disabled')
    sys.path.insert(0, str(MODULES))
    import insights_manual
    import insights_publish
    return insights_manual, insights_publish


def dispatch(command, raw, manual, publisher):
    if command not in COMMANDS or not 0 < len(raw) <= MAX_INPUT:
        raise ValueError('Only fixed manual report commands with JSON are accepted')
    if command == 'manual-publish':
        return publisher.publish(raw)
    request = publisher.decode(raw)
    if type(request) is not dict or request.get('save') is not (command == 'manual-prepare'):
        raise ValueError('Save flag does not match the operation')
    return manual.prepare(raw)


def main():
    if len(sys.argv) != 1 or os.environ.get('SSH_ORIGINAL_COMMAND') not in COMMANDS:
        raise ValueError('Unsupported report command')
    manual, publisher = load_modules()
    os.umask(0o077)
    result = dispatch(os.environ['SSH_ORIGINAL_COMMAND'], sys.stdin.buffer.read(MAX_INPUT + 1), manual, publisher)
    print(json.dumps({'scope': 'production', 'synthetic': False, 'result': result}, ensure_ascii=False, allow_nan=False))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('{"error":"Manual report request rejected or unavailable"}', file=sys.stderr)
        sys.exit(1)
