"""Explicit root provisioning of a private, synthetic-only report SSH account.

--check is read-only. --activate creates only the fixed staging resources and
reloads SSH; it never contacts a model/device or changes public reporting.
--verify rechecks the installed policy and the private activation receipt.
--check-recovery validates retained resources without changing them; --recover
requires an explicit recovery plan in the release manifest and never deletes data.
"""
import argparse
import base64
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time
from zoneinfo import ZoneInfo


USER = 'homeenergy-reporter-staging'
HOME = Path('/var/lib/homeenergy-reporter-staging-home')
MODULES = Path('/opt/homeenergy-reports-staging')
STATE = Path('/var/lib/homeenergy-reports-staging')
FIXTURES = Path('/var/lib/homeenergy-report-fixtures')
HISTORY = FIXTURES / 'history'
KEY_DIRECTORY = Path('/etc/ssh/authorized_keys')
KEY = KEY_DIRECTORY / USER
INCLUDE = Path('/etc/ssh/sshd_config.d/00-homeenergy-reporter-staging.conf')
SSHD_CONFIG = Path('/etc/ssh/sshd_config')
RECEIPT = MODULES / 'activation-receipt.json'
FORCED_COMMAND = ('/usr/bin/timeout --signal=TERM --kill-after=5s 45s '
                  '/usr/bin/python3 -I /opt/homeenergy-reports-staging/insights_staging_publish.py')
MODULE_NAMES = ('insights_staging_publish.py', 'insights_publish.py',
                'insights_analysis.py', 'insights_i18n.py')
SOURCE_FILES = (*MODULE_NAMES, 'provision-report-staging.py',
                'reports-staging-sshd.conf.example', 'staging-key.pub')
CONNECTION = {'peer_ip': '193.38.254.225', 'peer_host': '193.38.254.225',
              'local_ip': '91.98.44.219', 'local_port': 22}
BASELINE_SSHD = '307d75fc24117b92869aa5ad8af6df0f643946a68c9d8b24322d92a3b290848a'
ENVIRONMENT = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'}
ACCESS_PROBE = (
    'import json,os,sys; '
    'print(json.dumps({"uid":os.getuid(),"euid":os.geteuid(),'
    '"gid":os.getgid(),"egid":os.getegid(),"groups":sorted(os.getgroups()),'
    '"allowed":os.access(sys.argv[2],{"-r":os.R_OK,"-w":os.W_OK,"-x":os.X_OK}[sys.argv[1]],'
    'effective_ids=True)},sort_keys=True))'
)
EXPECTED_SSH = {
    'forcecommand': FORCED_COMMAND, 'authenticationmethods': 'publickey',
    'pubkeyauthentication': 'yes', 'passwordauthentication': 'no',
    'kbdinteractiveauthentication': 'no', 'authorizedkeysfile': KEY.as_posix(),
    'authorizedkeyscommand': 'none', 'trustedusercakeys': 'none',
    'disableforwarding': 'yes', 'permittty': 'no', 'permittunnel': 'no',
    'permituserrc': 'no', 'permituserenvironment': 'no', 'maxsessions': '1',
}
PROTECTED_TREES = (Path('/var/www/homeenergy'), Path('/etc/ssh'),
                   Path('/etc/nginx'), Path('/opt/homeenergy-control'),
                   Path('/var/lib/homeenergy-reports'))


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def digest(body):
    return hashlib.sha256(body).hexdigest()


def canonical(path):
    require(path.is_absolute() and path.resolve() == path and not path.is_symlink(),
            'Noncanonical or linked path refused: ' + str(path))
    return path


def absent(path):
    canonical(path)
    return not os.path.lexists(path)


def read_file(path, limit=4_000_000):
    canonical(path)
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= limit,
            'Unexpected file: ' + str(path))
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    with os.fdopen(os.open(path, flags), 'rb') as stream:
        opened = os.fstat(stream.fileno())
        require(stat.S_ISREG(opened.st_mode) and opened.st_ino == info.st_ino
                and opened.st_dev == info.st_dev, 'File changed while opening')
        body = stream.read(limit + 1)
    require(len(body) <= limit, 'Oversized file')
    return body


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'Duplicate manifest field')
        result[key] = value
    return result


def authorized_line(body):
    parts = body.decode('ascii').strip().split()
    require(len(parts) in (2, 3) and parts[0] == 'ssh-ed25519'
            and b'\n' not in body.strip() and b'\r' not in body.strip(), 'Expected one Ed25519 public key')
    try:
        blob = base64.b64decode(parts[1], validate=True)
    except ValueError as error:
        raise RuntimeError('Invalid public-key encoding') from error
    require(len(blob) == 51 and blob[:19] == b'\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20',
            'Invalid Ed25519 public-key structure')
    return ('restrict,command="' + FORCED_COMMAND + '" ssh-ed25519 ' + parts[1] + '\n').encode('ascii')


def validate_template(body):
    lines = [line.strip() for line in body.decode('ascii').splitlines()
             if line.strip() and not line.lstrip().startswith('#')]
    require(lines[0:1] == ['Match User ' + USER] and lines[-1:] == ['Match all'],
            'SSH template must affect only the staging account')
    settings = {}
    optional = {'allowagentforwarding': 'no', 'allowtcpforwarding': 'no',
                'allowstreamlocalforwarding': 'no', 'x11forwarding': 'no',
                'pubkeyacceptedalgorithms': 'ssh-ed25519'}
    allowed = {**EXPECTED_SSH, **optional}
    for line in lines[1:-1]:
        fields = line.split(None, 1)
        require(len(fields) == 2, 'Invalid staging SSH directive')
        key, value = fields[0].lower(), fields[1]
        require(key not in settings and allowed.get(key) == value,
                'Unreviewed staging SSH directive')
        settings[key] = value
    required = set(EXPECTED_SSH) - {'permituserenvironment', 'pubkeyauthentication',
                                  'authorizedkeyscommand', 'trustedusercakeys'}
    require(required <= set(settings), 'Missing staging SSH restrictions')


def read_bundle(source):
    canonical(source)
    require(source.parent == Path('/home/shini') and
            re.fullmatch(r'homeenergy-staging-release\.[A-Za-z0-9_-]+', source.name),
            'Unexpected release directory')
    manifest = json.loads(read_file(source / 'manifest.json', 16384), object_pairs_hook=unique_pairs)
    require(isinstance(manifest, dict), 'Expected release manifest')
    fields = {'manifest_version', 'files', 'connection', 'baseline_sshd'}
    require(type(manifest.get('manifest_version')) is int and
            ((manifest['manifest_version'] == 1 and set(manifest) == fields) or
             (manifest['manifest_version'] == 2 and set(manifest) == fields | {'recovery'})),
            'Unexpected manifest fields/version')
    if manifest['manifest_version'] == 2:
        validate_recovery_plan(manifest['recovery'])
    require(manifest['connection'] == CONNECTION
            and manifest['baseline_sshd'] == BASELINE_SSHD, 'Unreviewed release target')
    require(isinstance(manifest['files'], dict) and set(manifest['files']) == set(SOURCE_FILES),
            'Unexpected release file list')
    bodies = {name: read_file(source / name) for name in SOURCE_FILES}
    require(all(isinstance(value, str) and re.fullmatch(r'[a-f0-9]{64}', value)
                and digest(bodies[name]) == value for name, value in manifest['files'].items()),
            'Release hash mismatch')
    for name in MODULE_NAMES:
        compile(bodies[name], name, 'exec', dont_inherit=True)
    authorized_line(bodies['staging-key.pub'])
    validate_template(bodies['reports-staging-sshd.conf.example'])
    return manifest, bodies


def run(args, *, check=True, timeout=30):
    result = subprocess.run(args, capture_output=True, text=True, env=ENVIRONMENT,
                            timeout=timeout, check=False)
    require(not check or result.returncode == 0, 'Command failed: ' + args[0])
    return result


def require_root():
    require(sys.platform == 'linux' and os.geteuid() == 0, 'Run explicitly with sudo on the reviewed Linux server')


def trusted(path, *, mode=None, owner=0, directory=True):
    canonical(path)
    info = path.lstat()
    require((stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            and info.st_uid == owner and not info.st_mode & 0o022
            and (mode is None or stat.S_IMODE(info.st_mode) == mode),
            'Untrusted ownership/mode: ' + str(path))
    if not directory:
        require(info.st_nlink == 1, 'Hardlinked file refused')
    for parent in path.parents:
        if parent == STATE and path == STATE / 'public':
            continue
        meta = parent.lstat()
        require(stat.S_ISDIR(meta.st_mode) and meta.st_uid == 0 and not meta.st_mode & 0o022,
                'Untrusted ancestor: ' + str(parent))


def parse_settings(raw):
    settings = {}
    for line in raw.splitlines():
        if ' ' in line:
            key, value = line.split(' ', 1)
            settings[key] = (settings[key] + ' ' if key in settings else '') + value
    return settings


def effective_settings(user, config_path=None):
    context = (f'user={user},host={CONNECTION["peer_host"]},addr={CONNECTION["peer_ip"]},'
               f'laddr={CONNECTION["local_ip"]},lport={CONNECTION["local_port"]}')
    command = ['/usr/sbin/sshd', '-T', '-C', context]
    if config_path is not None:
        command.extend(['-f', config_path])
    return parse_settings(run(command).stdout)


def safe_authentication(settings, *, installed):
    require(settings.get('authorizedkeyscommand') == 'none' and
            settings.get('trustedusercakeys') == 'none' and
            settings.get('permituserenvironment') == 'no', 'Unreviewed SSH authentication/environment source')
    require(set(settings.get('acceptenv', '').split()) <= {'LANG', 'LC_*'}, 'Unreviewed accepted SSH environment')
    # Reviewed Ubuntu policy uses PAM account checks with a locked password.
    # Never alter global PAM/password settings to make authentication work.
    require(settings.get('usepam') == 'yes', 'Locked-account SSH/PAM behavior needs administrator review')
    if installed:
        require(all(settings.get(key) == value for key, value in EXPECTED_SSH.items()),
                'Effective staging SSH restrictions do not match')


def services():
    result = run(['/usr/bin/systemctl', 'show', 'nginx.service', 'homeenergy.service',
                  'homeenergy-control.service', '-p', 'Id', '-p', 'MainPID', '-p', 'NRestarts', '-p', 'ActiveState']).stdout
    require(result.count('ActiveState=active') == 3 and 'MainPID=0\n' not in result,
            'An existing Home Energy service is not active')
    return result


def protected_snapshot():
    # Only code/config/public assets: do not read keys, tokens, account hashes or
    # changing history/telemetry. Effective-access checks below inspect metadata.
    paths = [SSHD_CONFIG, Path('/etc/nginx/sites-available/homeenergy.conf'),
             Path('/var/lib/homeenergy-reports/public/index.json')]
    paths += list(Path('/etc/ssh/sshd_config.d').glob('*.conf'))
    paths += [p for p in Path('/var/www/homeenergy/public').rglob('*') if p.is_file()]
    paths += [p for p in Path('/opt/homeenergy-control').rglob('*')
              if p.is_file() and p.suffix in ('.py', '.service')]
    result = {}
    for path in paths:
        if path != INCLUDE:
            result[str(path)] = digest(read_file(path))
    return result


def account_absent():
    import grp
    import pwd
    for lookup in (pwd.getpwnam, grp.getgrnam):
        try:
            lookup(USER)
        except KeyError:
            continue
        raise RuntimeError('Staging account/group already exists; administrator review required')


def verify_dash():
    require(Path('/bin/dash').is_file() and
            Path('/bin/dash').resolve() == Path('/usr/bin/dash').resolve(), 'Reviewed dash shell unavailable')


def candidate_main(original, candidate_path, includes):
    """Keep real Include boundaries/order; do not flatten Match directives."""
    pattern = rb'(?m)^[ \t]*Include[ \t]+/etc/ssh/sshd_config\.d/\*\.conf[ \t]*(?:#[^\r\n]*)?\r?$'
    require(len(re.findall(pattern, original)) == 1, 'Unexpected SSH Include structure')
    entries = [(path.name, path.as_posix()) for path in includes]
    require(all(re.fullmatch(r'[A-Za-z0-9_.-]+\.conf', name) for name, _ in entries),
            'Unreviewed SSH include filename')
    entries.append((INCLUDE.name, candidate_path))
    replacement = ('Include ' + ' '.join(value for _, value in sorted(entries))).encode('ascii')
    return re.sub(pattern, lambda _: replacement, original, count=1)


def candidate_policy(bodies):
    # Linux anonymous memory files preserve actual Include semantics without
    # writing a candidate file or exposing it to the running SSH listener.
    # Reference this parent process's descriptors: sshd may close inherited FDs.
    descriptors = []
    def memory_file(body):
        descriptor = os.memfd_create('homeenergy-staging-check', flags=os.MFD_CLOEXEC)
        descriptors.append(descriptor)
        with os.fdopen(os.dup(descriptor), 'wb') as stream:
            stream.write(body)
            stream.flush()
        return f'/proc/{os.getpid()}/fd/{descriptor}'
    try:
        candidate = memory_file(bodies['reports-staging-sshd.conf.example'])
        config = memory_file(candidate_main(read_file(SSHD_CONFIG), candidate,
                                           list(INCLUDE.parent.glob('*.conf'))))
        run(['/usr/sbin/sshd', '-t', '-f', config])
        safe_authentication(effective_settings(USER, config), installed=True)
        require(effective_settings('shini', config) == effective_settings('shini'),
                'Candidate SSH policy changes administrator access')
    finally:
        for descriptor in descriptors:
            os.close(descriptor)


def preflight(bodies):
    require_root()
    account_absent()
    for path in (HOME, MODULES, STATE, FIXTURES, KEY, INCLUDE):
        require(absent(path), 'Refusing pre-existing staging resource: ' + str(path))
    for path in (HOME.parent, MODULES.parent, KEY_DIRECTORY.parent, INCLUDE.parent):
        trusted(path)
    if KEY_DIRECTORY.exists():
        trusted(KEY_DIRECTORY, mode=0o755)
    require(digest(read_file(SSHD_CONFIG)) == BASELINE_SSHD, 'Global SSH configuration baseline changed')
    trusted(SSHD_CONFIG, directory=False)
    for path in INCLUDE.parent.glob('*.conf'):
        trusted(path, directory=False)
    for path in (Path('/etc/ssh/sshrc'),):
        if path.exists():
            trusted(path, directory=False)
    verify_dash()
    ZoneInfo('Europe/Athens')
    run(['/usr/sbin/sshd', '-t'])
    run(['/usr/sbin/visudo', '-c'])
    safe_authentication(effective_settings(USER), installed=False)
    candidate_policy(bodies)
    return {'protected': protected_snapshot(), 'services': services(),
            'administrator_ssh': effective_settings('shini')}


def synthetic_fixtures(now):
    """Eight elapsed days, generated values only; never reads live history."""
    stop = int(now) // 60 * 60
    days = {}
    zone = ZoneInfo('Europe/Athens')
    for stamp in range(stop - 8 * 86400, stop, 60):
        local = datetime.fromtimestamp(stamp, zone)
        minute = local.hour * 60 + local.minute
        solar = max(0, 1800 - abs(minute - 720) * 6)
        load = 550 + (150 if 18 <= local.hour < 22 else 0)
        battery = min(700, max(-400, solar - load))
        values = {'pv': solar, 'grid': max(0, load + battery - solar),
                  'load': load, 'battery': battery,
                  'soc': min(95, max(30, 45 + (minute - 360) // 12))}
        day = local.date().isoformat()
        days.setdefault(day, []).append({'t': stamp, 'values': values,
                                        'counts': {key: 1 for key in values}})
    return {day + '.json': json.dumps({'day': day, 'synthetic_fixture': True, 'points': rows},
            separators=(',', ':'), allow_nan=False).encode() for day, rows in days.items()}


def allowed_new_file(path):
    return path in {KEY, INCLUDE, RECEIPT, *(MODULES / name for name in MODULE_NAMES)} or (
        path.parent == HISTORY and re.fullmatch(r'\d{4}-\d{2}-\d{2}\.json', path.name))


def create_file(path, body, mode=0o644):
    require(allowed_new_file(path) and absent(path), 'New-file target refused: ' + str(path))
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
    with os.fdopen(os.open(path, flags, mode), 'wb') as stream:
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())
        os.fchown(stream.fileno(), 0, 0)
        os.fchmod(stream.fileno(), mode)
    trusted(path, mode=mode, directory=False)
    require(read_file(path) == body, 'New-file readback mismatch')


def create_directory(path, mode=0o755, uid=0, gid=0):
    allowed = {HOME, MODULES, STATE, STATE / 'public', FIXTURES, HISTORY, KEY_DIRECTORY}
    require(path in allowed and absent(path), 'New-directory target refused: ' + str(path))
    path.mkdir(mode=mode)
    os.chown(path, uid, gid)
    os.chmod(path, mode)
    trusted(path, mode=mode, owner=uid)


def permitted(user, flag, path):
    """Prove both the dropped kernel identity and a completed access check.

    Unlike runuser/test, a failed launch cannot be mistaken for access denial.
    Root drops all UID/GID/group credentials without PAM, so an expired account
    can be audited before recovery re-enables its SSH login.
    """
    import pwd
    require(flag in ('-r', '-w', '-x') and path.is_absolute() and path.exists(),
            'Invalid or missing access-probe target')
    account = pwd.getpwnam(user)
    require(account.pw_uid > 0, 'Access probes must not run as root')
    groups = sorted(os.getgrouplist(user, account.pw_gid))
    result = subprocess.run(['/usr/bin/python3', '-I', '-B', '-c', ACCESS_PROBE, flag, str(path)],
                            capture_output=True, text=True, env=ENVIRONMENT, timeout=10,
                            check=False, user=account.pw_uid, group=account.pw_gid,
                            extra_groups=groups)
    require(result.returncode == 0 and result.stderr == '' and len(result.stdout) <= 2048,
            'Access probe did not complete; denial is not established')
    answer = json.loads(result.stdout, object_pairs_hook=unique_pairs)
    expected = {'uid': account.pw_uid, 'euid': account.pw_uid,
                'gid': account.pw_gid, 'egid': account.pw_gid, 'groups': groups}
    require(isinstance(answer, dict) and set(answer) == set(expected) | {'allowed'}
            and all(answer[key] == value for key, value in expected.items())
            and all(type(answer[key]) is int for key in ('uid', 'euid', 'gid', 'egid'))
            and isinstance(answer['groups'], list)
            and all(type(group) is int for group in answer['groups'])
            and type(answer['allowed']) is bool, 'Access probe identity/result mismatch')
    return answer['allowed']


def assert_no_sudo():
    denial = run(['/usr/bin/sudo', '-n', '-l', '-U', USER], check=False)
    # sudo 1.9.15p5 returns success for a completed listing of no privileges.
    # Password/PAM/policy failures and command listings are NOT denial evidence.
    require(denial.returncode == 0 and denial.stderr == '' and re.fullmatch(
        r'User ' + re.escape(USER) + r' is not allowed to run sudo on [A-Za-z0-9_.-]+\.',
        denial.stdout.strip()), 'Sudo denial not conclusively verified')


def audit_account(*, expired=False):
    import grp
    import pwd
    account = pwd.getpwnam(USER)
    require(account.pw_uid > 0 and account.pw_dir == str(HOME)
            and account.pw_shell == '/bin/dash', 'Unexpected staging account')
    require(os.getgrouplist(USER, account.pw_gid) == [account.pw_gid]
            and grp.getgrgid(account.pw_gid).gr_name == USER, 'Unexpected staging groups')
    password_state = run(['/usr/bin/passwd', '-S', USER]).stdout.split()
    require(len(password_state) >= 2 and password_state[:2] == [USER, 'L'], 'Staging password is not locked')
    aging = run(['/usr/bin/chage', '-l', USER])
    fields = {}
    for line in aging.stdout.splitlines():
        require(':' in line, 'Unrecognized account aging output')
        key, value = (part.strip() for part in line.split(':', 1))
        require(key not in fields, 'Duplicate account aging field')
        fields[key] = value
    require(aging.stderr == '' and fields.get('Account expires') ==
            ('Jan 02, 1970' if expired else 'never'), 'Unexpected staging account expiry')
    require(fields.get('Password expires') == 'never' and fields.get('Password inactive') == 'never',
            'Unexpected password expiration/inactivity policy')
    return account


def audit_tree(root):
    require(root.is_dir() and root.resolve() == root, 'Missing or linked protected tree')
    count = 0
    def walk_error(error):
        raise error
    for path in (root, *root.parents):
        require(not permitted(USER, '-w', path), 'Reporter can modify a protected ancestor')
    for directory, children, files in os.walk(root, followlinks=False, onerror=walk_error):
        for name in children + files:
            count += 1
            require(count <= 100000, 'Protected tree too large for reviewed audit')
            path = Path(directory) / name
            if path.is_symlink():
                resolved = path.resolve(strict=True)
                # Existing Nginx module links may point outside /etc/nginx.
                # Review root-owned files and ancestors, but never recursively
                # follow an external directory or accept a writable target.
                if not resolved.is_relative_to(root):
                    require(resolved.is_file(), 'Protected tree has external directory link')
                    trusted(resolved, directory=False)
                    for target in (resolved, *resolved.parents):
                        require(not permitted(USER, '-w', target), 'Reporter can modify linked protected path')
            require(not permitted(USER, '-w', path), 'Reporter can modify a protected path')


def audit(manifest, bodies, *, key_present, expired=False, policy_installed=True):
    account = audit_account(expired=expired)
    run(['/usr/sbin/visudo', '-c'])
    assert_no_sudo()
    for path in (HOME, MODULES, FIXTURES, HISTORY, KEY_DIRECTORY):
        trusted(path, mode=0o755)
    for path in (STATE, STATE / 'public'):
        trusted(path, mode=0o700, owner=account.pw_uid)
        require(permitted(USER, '-w', path), 'Staging identity cannot write its private state')
        require(not permitted('www-data', '-x', path), 'Nginx can traverse private staging state')
    for name in MODULE_NAMES:
        trusted(MODULES / name, mode=0o644, directory=False)
        require(digest(read_file(MODULES / name)) == manifest['files'][name], 'Installed module changed')
    for path in HISTORY.iterdir():
        require(re.fullmatch(r'\d{4}-\d{2}-\d{2}\.json', path.name), 'Unexpected fixture file')
        trusted(path, mode=0o644, directory=False)
    if policy_installed:
        trusted(INCLUDE, mode=0o644, directory=False)
        require(read_file(INCLUDE) == bodies['reports-staging-sshd.conf.example'], 'Managed SSH include changed')
    else:
        require(not key_present and expired and absent(INCLUDE), 'Recovery policy is not inactive')
    if key_present:
        trusted(KEY, mode=0o644, directory=False)
        require(read_file(KEY) == authorized_line(bodies['staging-key.pub']), 'Staging authorized key changed')
    else:
        require(absent(KEY), 'Key became usable before policy audit')
    safe_authentication(effective_settings(USER), installed=policy_installed)
    for path in (*PROTECTED_TREES, MODULES, HOME, FIXTURES):
        audit_tree(path)
    for path in (Path('/root'), Path('/home/shini/.ssh'), Path('/etc/homeenergy-control'),
                 Path('/etc/homeenergy-report-credentials'), Path('/run/credentials/homeenergy-control.service')):
        if path.exists():
            require(not permitted(USER, '-x', path), 'Reporter can traverse a private credential directory')
    return account


def validate_recovery_plan(plan):
    require(isinstance(plan, dict) and set(plan) ==
            {'backup', 'prior_release', 'prior_manifest_sha256', 'uid', 'gid'}, 'Invalid recovery plan')
    for key, prefix in (('backup', 'homeenergy-staging-backup.'),
                        ('prior_release', 'homeenergy-staging-release.')):
        require(isinstance(plan[key], str), 'Invalid recovery path')
        path = Path(plan[key])
        require(path.parent == Path('/home/shini') and
                re.fullmatch(re.escape(prefix) + r'[A-Za-z0-9_-]+', path.name), 'Unreviewed recovery path')
    require(isinstance(plan['prior_manifest_sha256'], str) and
            re.fullmatch('[a-f0-9]{64}', plan['prior_manifest_sha256']), 'Invalid original-manifest pin')
    require(all(type(plan[key]) is int and 0 < plan[key] < 1000 for key in ('uid', 'gid')),
            'Recovery requires the reviewed system identity')


def recovery_evidence(manifest):
    """Read original root-owned evidence, not a newly invented baseline."""
    import pwd
    plan = manifest.get('recovery')
    validate_recovery_plan(plan)
    backup = Path(plan['backup'])
    prior_release = Path(plan['prior_release'])
    canonical(backup)
    # Backups deliberately live below the trusted administrator's home rather
    # than below an all-root-owned ancestor chain used for installed resources.
    admin_home = backup.parent
    admin = pwd.getpwnam('shini')
    home_info = admin_home.lstat()
    require(home_info.st_uid == admin.pw_uid and not home_info.st_mode & 0o022,
            'Untrusted administrator home')
    for parent in admin_home.parents:
        trusted(parent)
    info = backup.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and info.st_gid == 0
            and stat.S_IMODE(info.st_mode) == 0o700, 'Untrusted rollback backup')
    require({p.name for p in backup.iterdir()} == {'preflight.json', 'sshd_config'},
            'Unexpected rollback evidence files')
    saved = {}
    for name in ('preflight.json', 'sshd_config'):
        path = backup / name
        meta = path.lstat()
        require(meta.st_uid == 0 and meta.st_gid == 0 and stat.S_IMODE(meta.st_mode) == 0o600,
                'Untrusted rollback evidence')
        saved[name] = read_file(path)
    prior_bytes = read_file(prior_release / 'manifest.json', 16384)
    require(digest(prior_bytes) == plan['prior_manifest_sha256'], 'Original manifest pin mismatch')
    previous, _ = read_bundle(prior_release)
    require(previous['manifest_version'] == 1, 'Recovery requires the original provisioning release')
    for name in SOURCE_FILES:
        if name != 'provision-report-staging.py':
            require(previous['files'][name] == manifest['files'][name],
                    'Recovery cannot replace staging code, key or policy')
    evidence = json.loads(saved['preflight.json'], object_pairs_hook=unique_pairs)
    require(isinstance(evidence, dict) and set(evidence) == {'manifest', 'before', 'fixtures', 'backup'}
            and evidence['manifest'] == previous and evidence['backup'] == str(backup)
            and evidence['fixtures'] == {}, 'Original rollback evidence mismatch')
    require(digest(saved['sshd_config']) == BASELINE_SSHD, 'Saved SSH baseline changed')
    before = evidence['before']
    require(isinstance(before, dict) and set(before) == {'protected', 'services', 'administrator_ssh'},
            'Incomplete original baseline')
    verify_invariants(before)
    return before


def check_recovery_fixtures():
    """Recover hashes by proving the entire retained fixture set byte-for-byte.

    The first release saved preflight before generating fixtures, so its receipt
    has no fixture hashes. Do not trust a synthetic label or merely its filenames.
    """
    files = list(HISTORY.iterdir())
    require(1 <= len(files) <= 10, 'Unexpected synthetic fixture file count')
    actual = {}
    stamps = []
    for path in files:
        require(re.fullmatch(r'\d{4}-\d{2}-\d{2}\.json', path.name), 'Unexpected fixture filename')
        trusted(path, mode=0o644, directory=False)
        body = read_file(path)
        document = json.loads(body, object_pairs_hook=unique_pairs)
        require(isinstance(document, dict) and isinstance(document.get('points'), list)
                and 1 <= len(document['points']) <= 1500, 'Invalid retained fixture')
        for point in document['points']:
            require(isinstance(point, dict) and type(point.get('t')) is int, 'Invalid fixture timestamp')
            stamps.append(point['t'])
        actual[path.name] = body
    require(len(stamps) == 8 * 1440, 'Incomplete synthetic history')
    stop = max(stamps) + 60
    require(0 < stop <= int(time.time()) // 60 * 60 + 60, 'Invalid retained fixture period')
    expected = synthetic_fixtures(stop)
    require(actual == expected, 'Retained fixtures differ from the deterministic synthetic dataset')
    return {name: digest(body) for name, body in actual.items()}


def recovery_preflight(manifest, bodies):
    """No mutations: keep the account expired and key absent during every audit."""
    require_root()
    before = recovery_evidence(manifest)
    for path in (KEY, INCLUDE, RECEIPT):
        require(absent(path), 'Recovery requires an inactive, incomplete installation: ' + str(path))
    account = audit_account(expired=True)
    require((account.pw_uid, account.pw_gid) == (manifest['recovery']['uid'], manifest['recovery']['gid']),
            'Retained staging identity changed')
    processes = run(['/usr/bin/pgrep', '-u', str(account.pw_uid)], check=False)
    require(processes.returncode == 1 and processes.stdout == '' and processes.stderr == '',
            'Recovery requires an idle staging identity')
    for path in (HOME, MODULES, FIXTURES, HISTORY, KEY_DIRECTORY):
        trusted(path, mode=0o755)
    for path in (STATE, STATE / 'public'):
        trusted(path, mode=0o700, owner=account.pw_uid)
    require(not list(HOME.iterdir()) and {p.name for p in MODULES.iterdir()} == set(MODULE_NAMES)
            and {p.name for p in FIXTURES.iterdir()} == {'history'}
            and {p.name for p in STATE.iterdir()} == {'public'}
            and not list((STATE / 'public').iterdir()), 'Retained staging resources are not pristine')
    fixtures = check_recovery_fixtures()
    run(['/usr/sbin/sshd', '-t'])
    candidate_policy(bodies)
    audit(manifest, bodies, key_present=False, expired=True, policy_installed=False)
    verify_invariants(before)
    return {'manifest': manifest, 'before': before, 'fixtures': fixtures,
            'backup': manifest['recovery']['backup']}


def recover(manifest, bodies):
    receipt = recovery_preflight(manifest, bodies)
    os.umask(0o077)
    include_attempted = key_attempted = expiry_attempted = False
    try:
        include_attempted = True
        create_file(INCLUDE, bodies['reports-staging-sshd.conf.example'])
        run(['/usr/sbin/sshd', '-t'])
        audit(manifest, bodies, key_present=False, expired=True)
        verify_invariants(receipt['before'])
        run(['/usr/bin/systemctl', 'reload', 'ssh.service'])
        require(run(['/usr/bin/systemctl', 'is-active', 'ssh.service']).stdout.strip() == 'active',
                'SSH is not active after reload')
        # Locked password remains locked. Expiry changes only after the expired
        # identity and its candidate/installed access policy have passed audits.
        expiry_attempted = True
        run(['/usr/sbin/usermod', '--expiredate', '-1', USER])
        audit_account(expired=False)
        verify_invariants(receipt['before'])
        require(check_recovery_fixtures() == receipt['fixtures'], 'Fixtures changed during recovery')
        key_attempted = True
        create_file(KEY, authorized_line(bodies['staging-key.pub']))
        audit(manifest, bodies, key_present=True)
        verify_invariants(receipt['before'])
        create_file(RECEIPT, json.dumps(receipt, indent=2).encode(), 0o600)
        verify(manifest, bodies)
    except BaseException:
        rollback(bodies, user_created=expiry_attempted, include_attempted=include_attempted,
                 key_attempted=key_attempted)
        print('Recovery rolled back. Key revoked if created; account remains expired; retained data preserved.', flush=True)
        raise
    print('Private staging recovery completed. Only SSH was reloaded; retained UID and synthetic data preserved.')
    print('No model call, public report, schedule change, application/device restart or live-history change.')
    print('Keep this administrator session open. Client authentication/denial tests are still required.')


def verify_invariants(before):
    require(protected_snapshot() == before['protected'], 'Protected static files changed')
    require(services() == before['services'], 'Existing service state or PIDs changed')
    require(effective_settings('shini') == before['administrator_ssh'], 'Administrator SSH policy changed')


def remove_owned_file(path, expected):
    require(path in (KEY, INCLUDE), 'Rollback target refused')
    if absent(path):
        return
    trusted(path, mode=0o644, directory=False)
    require(read_file(path) == expected, 'Later change detected; manual rollback required: ' + str(path))
    path.unlink()


def rollback(bodies, *, user_created, include_attempted, key_attempted):
    failures = []
    actions = []
    if key_attempted:
        actions.append(('key revocation', lambda: remove_owned_file(KEY, authorized_line(bodies['staging-key.pub']))))
    if user_created:
        actions.append(('account expiry', lambda: run(['/usr/sbin/usermod', '--expiredate', '1', '--lock', USER])))
    if include_attempted:
        actions.append(('managed include removal', lambda: remove_owned_file(INCLUDE, bodies['reports-staging-sshd.conf.example'])))
        actions.append(('SSH syntax check', lambda: run(['/usr/sbin/sshd', '-t'])))
    for label, action in actions:
        try:
            action()
        except BaseException:
            failures.append(label)
    if include_attempted and 'SSH syntax check' not in failures:
        try:
            run(['/usr/bin/systemctl', 'reload', 'ssh.service'])
        except BaseException:
            failures.append('SSH reload')
    require(not failures, 'Rollback incomplete; administrator action required: ' + ', '.join(failures))


def activate(manifest, bodies):
    before = preflight(bodies)
    os.umask(0o077)
    backup = Path(tempfile.mkdtemp(prefix='homeenergy-staging-backup.', dir='/home/shini'))
    os.chown(backup, 0, 0)
    os.chmod(backup, 0o700)
    receipt = {'manifest': manifest, 'before': before, 'fixtures': {}, 'backup': str(backup)}
    # Private review evidence only; rollback touches no existing SSH configuration.
    (backup / 'preflight.json').write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    (backup / 'sshd_config').write_bytes(read_file(SSHD_CONFIG))
    print('Private rollback evidence: ' + str(backup), flush=True)
    user_created = include_attempted = key_attempted = False
    try:
        import pwd
        account_absent()
        verify_invariants(before)
        run(['/usr/sbin/groupadd', '--system', USER])
        run(['/usr/sbin/useradd', '--system', '--gid', USER, '--no-create-home',
             '--home-dir', str(HOME), '--shell', '/bin/dash', USER])
        user_created = True
        account = pwd.getpwnam(USER)
        for path in (HOME, MODULES, FIXTURES, HISTORY):
            create_directory(path)
        for path in (STATE, STATE / 'public'):
            create_directory(path, 0o700, account.pw_uid, account.pw_gid)
        if not KEY_DIRECTORY.exists():
            create_directory(KEY_DIRECTORY)
        for name in MODULE_NAMES:
            create_file(MODULES / name, bodies[name])
        fixtures = synthetic_fixtures(time.time())
        for name, body in fixtures.items():
            create_file(HISTORY / name, body)
        receipt['fixtures'] = {name: digest(body) for name, body in fixtures.items()}
        include_attempted = True
        create_file(INCLUDE, bodies['reports-staging-sshd.conf.example'])
        run(['/usr/sbin/sshd', '-t'])
        audit(manifest, bodies, key_present=False)
        verify_invariants(before)
        run(['/usr/bin/systemctl', 'reload', 'ssh.service'])
        require(run(['/usr/bin/systemctl', 'is-active', 'ssh.service']).stdout.strip() == 'active',
                'SSH is not active after reload')
        # Only now authorize authentication. The key also carries ForceCommand
        # defensively, independent of the account-bound Match rule.
        key_attempted = True
        create_file(KEY, authorized_line(bodies['staging-key.pub']))
        audit(manifest, bodies, key_present=True)
        verify_invariants(before)
        create_file(RECEIPT, json.dumps(receipt, indent=2).encode(), 0o600)
        verify(manifest, bodies)
    except BaseException:
        rollback(bodies, user_created=user_created, include_attempted=include_attempted,
                 key_attempted=key_attempted)
        print('Rollback completed for the attempted staging key/account/include steps; SSH reloaded if needed.', flush=True)
        print('Private staging resources retained for administrator review; do not rerun activation blindly.', flush=True)
        raise
    print('Private staging account and synthetic fixtures provisioned; only SSH was reloaded.')
    print('No public report, model call, schedule change, device operation or live-history change occurred.')
    print('Keep this administrator session open. Verify a fresh administrator SSH connection, then run the separately reviewed restricted-key tests.')


def verify(manifest, bodies):
    require_root()
    trusted(RECEIPT, mode=0o600, directory=False)
    receipt = json.loads(read_file(RECEIPT), object_pairs_hook=unique_pairs)
    require(set(receipt) == {'manifest', 'before', 'fixtures', 'backup'} and receipt['manifest'] == manifest,
            'Activation receipt mismatch')
    audit(manifest, bodies, key_present=True)
    actual = {path.name: digest(read_file(path)) for path in HISTORY.iterdir()}
    require(actual == receipt['fixtures'], 'Synthetic fixtures changed')
    verify_invariants(receipt['before'])
    print('Staging filesystem/account/effective SSH policy verified; real client authentication is not yet certified.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    choices = parser.add_mutually_exclusive_group(required=True)
    for option in ('check', 'activate', 'verify', 'check-recovery', 'recover'):
        choices.add_argument('--' + option, action='store_true')
    args = parser.parse_args()
    require_root()
    manifest, bodies = read_bundle(Path(__file__).resolve().parent)
    if args.check_recovery:
        recovery_preflight(manifest, bodies)
        print('Read-only recovery preflight passed. Account remains expired and key absent.')
    elif args.recover:
        recover(manifest, bodies)
    elif args.check:
        preflight(bodies)
        print('Read-only staging preflight passed. No account, key, file, service or schedule changed.')
    elif args.activate:
        activate(manifest, bodies)
    else:
        verify(manifest, bodies)


if __name__ == '__main__':
    main()
