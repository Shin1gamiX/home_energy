"""Prepare a disabled production report identity; never enable publication.

--check and --verify-disabled are read-only. --prepare-disabled creates only a
locked, expired account, its root-owned home and three new root-owned files.
It does not install keys/SSH policy, reload services, create a report database,
change existing report storage, call a model or enable a schedule.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile


USER = 'homeenergy-reporter'
HOME = Path('/var/lib/homeenergy-reporter-home')
MODULES = Path('/opt/homeenergy-reports')
STATE = Path('/var/lib/homeenergy-reports')
WRAPPER = MODULES / 'insights_production_publish.py'
GATE = MODULES / 'production-gate.json'
RECEIPT = MODULES / 'production-preparation-receipt.json'
KEY = Path('/etc/ssh/authorized_keys/homeenergy-reporter')
INCLUDE = Path('/etc/ssh/sshd_config.d/00-homeenergy-reporter-production.conf')
ACTIVATION = MODULES / 'activation.json'
ADMIN_HOME = Path('/home/shini')
SSH_CONFIG = Path('/etc/ssh/sshd_config')
SOURCE_FILES = ('prepare-report-production.py', 'insights_production_publish.py',
                'reports-production-sshd.conf.example')
OLD_MODULES = {
    'insights_publish.py': 'c7df78994cdf99a6d0be131f2365e36c37f8a9d9eb9ac83df52ae61995a4c567',
    'insights_analysis.py': '3c48a5254c025c8d3bfaf190c4d785bfef2ab800015de0e4fdb69f5363e0c70e',
    'insights_i18n.py': '957e784430904770d291bfaf2c402b513007d7ff953ae8c30a31f96ff11c31c5',
}
EMPTY_FEED_SHA256 = 'dd5905995b96e949e5617526861c930068373d9b85a18a5befc90fc9bc73e724'
PRESERVED_PATHS = tuple(MODULES / name for name in OLD_MODULES) + (
    STATE / 'public/index.json', Path('/var/www/homeenergy/public/index.html'),
    Path('/var/www/homeenergy/public/history.html'),
    Path('/etc/nginx/sites-available/homeenergy.conf'), SSH_CONFIG,
)
SERVICES = ('nginx.service', 'homeenergy.service', 'homeenergy-control.service')
DISABLED_GATE = {'schema_version': 1, 'scope': 'production', 'public_publishing_enabled': False}
ENVIRONMENT = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'}
SSH_CONTEXT = ('user=homeenergy-reporter,host=193.38.254.225,addr=193.38.254.225,'
               'laddr=91.98.44.219,lport=22')
PROTECTED_TREES = (Path('/var/www/homeenergy'), Path('/etc/ssh'), Path('/etc/nginx'),
                   Path('/opt/homeenergy-control'), MODULES, STATE, HOME)
PRIVATE_PATHS = (Path('/root'), Path('/home/shini/.ssh'), Path('/etc/homeenergy-control'),
                 Path('/etc/homeenergy-report-credentials'),
                 Path('/run/credentials/homeenergy-control.service'),
                 Path('/var/lib/homeenergy-reports-staging'))
ACCESS_PROBE = (
    'import json,os,sys; '
    'print(json.dumps({"uid":os.getuid(),"euid":os.geteuid(),'
    '"gid":os.getgid(),"egid":os.getegid(),"groups":sorted(os.getgroups()),'
    '"allowed":os.access(sys.argv[2],{"-r":os.R_OK,"-w":os.W_OK,"-x":os.X_OK}[sys.argv[1]],'
    'effective_ids=True)},sort_keys=True))'
)
ACCESS_BATCH_PROBE = (
    'import json,os,sys; paths=json.load(sys.stdin); '
    'print(json.dumps({"uid":os.getuid(),"euid":os.geteuid(),'
    '"gid":os.getgid(),"egid":os.getegid(),"groups":sorted(os.getgroups()),'
    '"allowed":[os.access(path,os.W_OK,effective_ids=True) for path in paths]},sort_keys=True))'
)
DISABLED_ERROR = '{"error":"Report request rejected or temporarily unavailable"}\n'
# Root-only verification source. Profiling observes the real gate call without
# replacing it, enabling it, or changing the wrapper or any imported function.
GATE_PROBE = '''import json,os,runpy,sys
entry="/opt/homeenergy-reports/insights_production_publish.py"
reached=False
forbidden=False
status=None
def profile(frame,event,arg):
    global reached,forbidden
    if event=="call" and frame.f_code.co_filename==entry:
        if frame.f_code.co_name=="verify_gate":
            reached=True
        if frame.f_code.co_name in ("load_publisher","verify_readonly_history","dispatch"):
            forbidden=True
sys.argv=[entry]
sys.setprofile(profile)
try:
    runpy.run_path(entry,run_name="__main__")
except SystemExit as error:
    status=error.code
finally:
    sys.setprofile(None)
print(json.dumps({"uid":os.getuid(),"euid":os.geteuid(),"gid":os.getgid(),"egid":os.getegid(),"groups":sorted(os.getgroups()),"gate_reached":reached,"forbidden_entered":forbidden,"wrapper_exit":status},sort_keys=True))
'''


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def digest(body):
    return hashlib.sha256(body).hexdigest()


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'Duplicate JSON field')
        result[key] = value
    return result


def decode(body):
    require(isinstance(body, bytes) and 0 < len(body) <= 65536, 'JSON size refused')
    return json.loads(body.decode('utf-8'), object_pairs_hook=unique_pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(RuntimeError('Nonfinite JSON')))


def canonical(path):
    require(path.is_absolute() and path.resolve() == path and not path.is_symlink(),
            'Noncanonical or linked path refused')
    return path


def absent(path):
    canonical(path)
    return not os.path.lexists(path)


def read_file(path, limit=2_000_000):
    canonical(path)
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= limit,
            'Unbounded, linked or nonregular file refused')
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    with os.fdopen(os.open(path, flags), 'rb') as stream:
        opened = os.fstat(stream.fileno())
        require(stat.S_ISREG(opened.st_mode) and opened.st_nlink == 1 and
                (opened.st_dev, opened.st_ino) == (before.st_dev, before.st_ino),
                'Opened file changed')
        body = stream.read(limit + 1)
    require(len(body) <= limit, 'File exceeds size bound')
    return body


def require_root():
    require(sys.platform == 'linux' and os.geteuid() == 0,
            'Run the reviewed server modes explicitly with sudo on Linux')


def trusted(path, *, mode=None, directory=True, private_admin_parent=False):
    canonical(path)
    info = path.lstat()
    require((stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            and info.st_uid == 0 and info.st_gid == 0 and not info.st_mode & 0o022
            and (mode is None or stat.S_IMODE(info.st_mode) == mode), 'Untrusted ownership or mode')
    if not directory:
        require(info.st_nlink == 1, 'Hardlinked file refused')
    for parent in path.parents:
        meta = parent.lstat()
        owner = 0
        if private_admin_parent and parent == ADMIN_HOME:
            import pwd
            owner = pwd.getpwnam('shini').pw_uid
        require(stat.S_ISDIR(meta.st_mode) and meta.st_uid == owner and not meta.st_mode & 0o022,
                'Untrusted parent directory')


def run(args, *, check=True, timeout=30):
    result = subprocess.run(args, capture_output=True, text=True, env=ENVIRONMENT,
                            timeout=timeout, check=False)
    require(not check or result.returncode == 0, 'Required local check failed')
    return result


def valid_hash(value):
    return isinstance(value, str) and bool(re.fullmatch(r'[a-f0-9]{64}', value))


def validate_services(value):
    require(isinstance(value, dict) and set(value) == set(SERVICES), 'Unexpected service inventory')
    for name, service in value.items():
        require(isinstance(service, dict) and set(service) == {'Id', 'ActiveState', 'MainPID', 'NRestarts'}
                and service['Id'] == name and service['ActiveState'] == 'active'
                and type(service['MainPID']) is int and service['MainPID'] > 0
                and type(service['NRestarts']) is int and service['NRestarts'] >= 0,
                'Invalid service baseline')


def validate_baseline(value):
    require(isinstance(value, dict) and set(value) == {'preserved', 'services'}, 'Unexpected baseline fields')
    expected = {path.as_posix() for path in PRESERVED_PATHS}
    require(isinstance(value['preserved'], dict) and set(value['preserved']) == expected
            and all(valid_hash(item) for item in value['preserved'].values()), 'Unexpected protected-file pins')
    for name, expected_hash in OLD_MODULES.items():
        require(value['preserved'][(MODULES / name).as_posix()] == expected_hash, 'Unreviewed existing module pin')
    require(value['preserved'][(STATE / 'public/index.json').as_posix()] == EMPTY_FEED_SHA256,
            'Public report feed is not the reviewed empty feed')
    validate_services(value['services'])


def validate_manifest(value):
    require(isinstance(value, dict) and set(value) == {
        'schema_version', 'scope', 'public_publishing_enabled', 'files', 'baseline'}, 'Unexpected manifest fields')
    require(type(value['schema_version']) is int and value['schema_version'] == 1
            and value['scope'] == 'production-preparation' and value['public_publishing_enabled'] is False,
            'Manifest is not disabled production preparation')
    require(isinstance(value['files'], dict) and set(value['files']) == set(SOURCE_FILES)
            and all(valid_hash(item) for item in value['files'].values()), 'Unexpected candidate file pins')
    validate_baseline(value['baseline'])


def build_manifest(repo, baseline_path):
    require(repo.is_absolute() and baseline_path.is_absolute(), 'Absolute build inputs required')
    baseline = decode(read_file(baseline_path, 16384))
    validate_baseline(baseline)
    locations = {'prepare-report-production.py': repo / 'deploy/prepare-report-production.py',
                 'insights_production_publish.py': repo / 'server/insights_production_publish.py',
                 'reports-production-sshd.conf.example': repo / 'deploy/reports-production-sshd.conf.example'}
    files = {name: digest(read_file(path)) for name, path in locations.items()}
    for name, expected_hash in OLD_MODULES.items():
        require(digest(read_file(repo / 'server' / name)) == expected_hash, 'Existing local module drift')
    result = {'schema_version': 1, 'scope': 'production-preparation',
              'public_publishing_enabled': False, 'files': files, 'baseline': baseline}
    validate_manifest(result)
    return result


def read_bundle(source):
    import pwd
    canonical(source)
    require(source.parent == ADMIN_HOME and re.fullmatch(r'homeenergy-production-release\.[A-Za-z0-9_-]+', source.name),
            'Unexpected private release directory')
    admin_uid = pwd.getpwnam('shini').pw_uid
    meta = source.lstat()
    require(stat.S_ISDIR(meta.st_mode) and meta.st_uid in (0, admin_uid)
            and stat.S_IMODE(meta.st_mode) == 0o700, 'Release must remain private')
    for parent in source.parents:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == (admin_uid if parent == ADMIN_HOME else 0)
                and not info.st_mode & 0o022, 'Untrusted release ancestor')
    require({p.name for p in source.iterdir()} == set(SOURCE_FILES) | {'manifest.json'}, 'Unexpected release contents')
    for name in (*SOURCE_FILES, 'manifest.json'):
        info = (source / name).lstat()
        require(info.st_uid in (0, admin_uid) and stat.S_IMODE(info.st_mode) in (0o600, 0o644),
                'Untrusted release file permissions')
    manifest = decode(read_file(source / 'manifest.json', 16384))
    validate_manifest(manifest)
    bodies = {name: read_file(source / name, 262144) for name in SOURCE_FILES}
    require(all(digest(bodies[name]) == manifest['files'][name] for name in SOURCE_FILES), 'Candidate checksum mismatch')
    require(Path(__file__).resolve() == source / 'prepare-report-production.py', 'Wrong preparation entrypoint')
    for name in ('prepare-report-production.py', 'insights_production_publish.py'):
        compile(bodies[name], name, 'exec', dont_inherit=True)
    return manifest, bodies


def service_snapshot():
    result = run(['/usr/bin/systemctl', 'show', *SERVICES, '-p', 'Id', '-p', 'ActiveState',
                  '-p', 'MainPID', '-p', 'NRestarts'])
    require(result.stderr == '', 'Service inspection produced diagnostic output')
    answer = {}
    for block in result.stdout.strip().split('\n\n'):
        fields = {}
        for line in block.splitlines():
            key, separator, value = line.partition('=')
            require(separator and key not in fields, 'Unrecognized service result')
            fields[key] = int(value) if key in ('MainPID', 'NRestarts') else value
        require(fields.get('Id') not in answer, 'Duplicate service record')
        answer[fields.get('Id')] = fields
    validate_services(answer)
    return answer


def preserved_snapshot():
    paths = [*PRESERVED_PATHS, *SSH_CONFIG.parent.joinpath('sshd_config.d').glob('*.conf')]
    result = {}
    for path in paths:
        body = read_file(path)
        info = path.lstat()
        result[path.as_posix()] = {'sha256': digest(body), 'uid': info.st_uid,
                                 'gid': info.st_gid, 'mode': stat.S_IMODE(info.st_mode)}
    for path in (MODULES, STATE, STATE / 'public'):
        info = path.lstat()
        result[path.as_posix()] = {'uid': info.st_uid, 'gid': info.st_gid, 'mode': stat.S_IMODE(info.st_mode)}
    return {'files': result, 'services': service_snapshot()}


def compare_baseline(manifest, snapshot):
    for path, expected in manifest['baseline']['preserved'].items():
        require(snapshot['files'][path]['sha256'] == expected, 'A protected file changed from the reviewed baseline')
    require(snapshot['services'] == manifest['baseline']['services'], 'A protected service changed from the reviewed baseline')


def account_absent():
    import grp
    import pwd
    for lookup in (pwd.getpwnam, grp.getgrnam):
        try:
            lookup(USER)
        except KeyError:
            continue
        raise RuntimeError('Production identity already exists; use its verification or request manual review')


def check_storage(*, installed=False):
    for path, mode in ((MODULES, 0o755), (STATE, 0o711), (STATE / 'public', 0o755)):
        trusted(path, mode=mode)
    expected = set(OLD_MODULES) | ({WRAPPER.name, GATE.name, RECEIPT.name} if installed else set())
    require({p.name for p in MODULES.iterdir()} == expected, 'Unreviewed publisher-directory contents')
    require({p.name for p in STATE.iterdir()} == {'public'}
            and {p.name for p in (STATE / 'public').iterdir()} == {'index.json'}, 'Production state is not empty and disabled')
    for name, expected_hash in OLD_MODULES.items():
        trusted(MODULES / name, mode=0o644, directory=False)
        require(digest(read_file(MODULES / name)) == expected_hash, 'Existing publisher source changed')
    trusted(STATE / 'public/index.json', mode=0o644, directory=False)
    require(digest(read_file(STATE / 'public/index.json')) == EMPTY_FEED_SHA256, 'Empty public feed changed')
    for path in (KEY, INCLUDE, ACTIVATION, STATE / 'reports.sqlite3', STATE / 'publisher.lock'):
        require(absent(path), 'An activation or production-state artifact already exists')


def check(manifest):
    require_root()
    account_absent()
    check_storage()
    for path in (HOME, WRAPPER, GATE, RECEIPT):
        require(absent(path), 'A preparation resource already exists; do not rerun blindly')
    for path in (HOME.parent, MODULES.parent, Path('/etc/ssh/sshd_config.d')):
        trusted(path)
    require(Path('/bin/dash').resolve(strict=True) == Path('/usr/bin/dash').resolve(strict=True),
            'Reviewed dash shell unavailable')
    run(['/usr/sbin/sshd', '-t'])
    run(['/usr/sbin/visudo', '-c'])
    before = preserved_snapshot()
    compare_baseline(manifest, before)
    return before


def access_allowed(account, flag, path):
    require(flag in ('-r', '-w', '-x') and path.is_absolute() and path.exists(), 'Invalid access-probe target')
    require(account.pw_uid > 0 and account.pw_gid > 0, 'Access probe cannot run as root')
    result = subprocess.run(['/usr/bin/python3', '-I', '-B', '-c', ACCESS_PROBE, flag, str(path)],
                            capture_output=True, text=True, env=ENVIRONMENT, timeout=10,
                            check=False, user=account.pw_uid, group=account.pw_gid,
                            extra_groups=[account.pw_gid])
    require(result.returncode == 0 and result.stderr == '' and len(result.stdout) <= 2048,
            'Access probe failed to run; denial is not established')
    answer = decode(result.stdout.encode('utf-8'))
    expected = {'uid': account.pw_uid, 'euid': account.pw_uid, 'gid': account.pw_gid,
                'egid': account.pw_gid, 'groups': [account.pw_gid]}
    require(isinstance(answer, dict) and set(answer) == set(expected) | {'allowed'}
            and all(answer[key] == value for key, value in expected.items())
            and all(type(answer[key]) is int for key in ('uid', 'euid', 'gid', 'egid'))
            and type(answer['allowed']) is bool and answer['groups'] == [account.pw_gid]
            and all(type(value) is int for value in answer['groups']), 'Access probe identity/result mismatch')
    return answer['allowed']


def account_disabled():
    import grp
    import pwd
    import spwd
    account = pwd.getpwnam(USER)
    require(account.pw_uid > 0 and account.pw_gid > 0 and account.pw_dir == str(HOME)
            and account.pw_shell == '/bin/dash', 'Unexpected reporting identity')
    require(os.getgrouplist(USER, account.pw_gid) == [account.pw_gid]
            and grp.getgrgid(account.pw_gid).gr_name == USER, 'Unexpected supplementary groups')
    shadow = spwd.getspnam(USER)
    require(shadow.sp_pwdp.startswith('!') and shadow.sp_expire == 1
            and shadow.sp_inact == -1, 'Reporter must remain password-locked and expired')
    run(['/usr/sbin/visudo', '-c'])
    policy = run(['/usr/bin/sudo', '-n', '-l', '-U', USER], check=False)
    require(policy.returncode in (0, 1) and policy.stderr == '' and re.fullmatch(
        r'User ' + re.escape(USER) + r' is not allowed to run sudo on [A-Za-z0-9_.-]+\.',
        policy.stdout.strip()), 'Sudo denial is not conclusively established')
    return account


def verify_disabled_authentication():
    """Check expiry enforcement, not the future uninstalled publishing policy."""
    result = run(['/usr/sbin/sshd', '-T', '-C', SSH_CONTEXT])
    require(result.stderr == '', 'Effective SSH inspection produced diagnostic output')
    settings = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition(' ')
        require(separator and key, 'Unrecognized effective SSH output')
        settings[key] = (settings[key] + ' ' if key in settings else '') + value
    # PAM is needed for the reviewed Ubuntu account-expiry enforcement. The
    # account has no password or key; no future publishing policy is installed.
    require(settings.get('usepam') == 'yes' and settings.get('permituserenvironment') == 'no'
            and settings.get('authorizedkeyscommand') == 'none'
            and settings.get('trustedusercakeys') == 'none',
            'Disabled-account SSH policy requires administrator review')
    require(set(settings.get('acceptenv', '').split()) <= {'LANG', 'LC_*'},
            'Unexpected inherited SSH environment')


def no_write_access(account, paths):
    """Bounded batches avoid launching one interpreter for every history file."""
    require(account.pw_uid > 0 and account.pw_gid > 0, 'Access probe cannot run as root')
    paths = list(paths)
    require(0 < len(paths) <= 100032 and all(path.is_absolute() and path.exists() for path in paths),
            'Invalid protected access targets')
    for offset in range(0, len(paths), 128):
        batch = paths[offset:offset + 128]
        body = encoded([str(path) for path in batch]).decode('utf-8')
        require(len(body.encode('utf-8')) <= 65536, 'Access probe request too large')
        result = subprocess.run(['/usr/bin/python3', '-I', '-B', '-c', ACCESS_BATCH_PROBE],
                                input=body, capture_output=True, text=True, env=ENVIRONMENT,
                                timeout=10, check=False, user=account.pw_uid,
                                group=account.pw_gid, extra_groups=[account.pw_gid])
        require(result.returncode == 0 and result.stderr == '' and len(result.stdout) <= 16384,
                'Batch access probe failed; denial is not established')
        answer = decode(result.stdout.encode('utf-8'))
        expected = {'uid': account.pw_uid, 'euid': account.pw_uid,
                    'gid': account.pw_gid, 'egid': account.pw_gid, 'groups': [account.pw_gid]}
        require(isinstance(answer, dict) and set(answer) == set(expected) | {'allowed'}
                and all(answer[key] == value for key, value in expected.items())
                and all(type(answer[key]) is int for key in ('uid', 'euid', 'gid', 'egid'))
                and isinstance(answer['groups'], list) and all(type(value) is int for value in answer['groups'])
                and isinstance(answer['allowed'], list) and len(answer['allowed']) == len(batch)
                and all(type(value) is bool for value in answer['allowed']),
                'Batch access probe identity/result mismatch')
        require(not any(answer['allowed']), 'Reporter can modify a protected path')


def verify_gate_rejection(account):
    """Execute the actual wrapper with no write capability and prove gate entry."""
    require(account.pw_uid > 0 and account.pw_gid > 0, 'Gate probe cannot run as root')
    environment = {**ENVIRONMENT, 'SSH_ORIGINAL_COMMAND': 'prepare', 'PYTHONDONTWRITEBYTECODE': '1'}
    options = {'capture_output': True, 'text': True, 'env': environment, 'timeout': 10, 'check': False,
               'user': account.pw_uid, 'group': account.pw_gid, 'extra_groups': [account.pw_gid]}
    direct = subprocess.run(['/usr/bin/python3', '-I', '-B', str(WRAPPER)], **options)
    require(direct.returncode == 1 and direct.stdout == '' and direct.stderr == DISABLED_ERROR,
            'Disabled wrapper did not return the exact expected refusal')
    probe = subprocess.run(['/usr/bin/python3', '-I', '-B', '-c', GATE_PROBE], **options)
    require(probe.returncode == 0 and probe.stderr == DISABLED_ERROR and len(probe.stdout) <= 2048,
            'Disabled gate observation did not complete')
    answer = decode(probe.stdout.encode('utf-8'))
    expected = {'uid': account.pw_uid, 'euid': account.pw_uid, 'gid': account.pw_gid,
                'egid': account.pw_gid, 'groups': [account.pw_gid], 'gate_reached': True,
                'forbidden_entered': False, 'wrapper_exit': 1}
    require(isinstance(answer, dict) and set(answer) == set(expected) and answer == expected
            and all(type(answer[key]) is int for key in ('uid', 'euid', 'gid', 'egid', 'wrapper_exit'))
            and type(answer['gate_reached']) is bool and type(answer['forbidden_entered']) is bool
            and all(type(value) is int for value in answer['groups']),
            'Gate denial was not observed under the expected reporting identity')


def audit_tree(account, root):
    canonical(root)
    require(root.is_dir(), 'Protected tree is missing')
    paths = {root, *root.parents}
    count = 0
    def fail_walk(error):
        raise error
    for directory, children, files in os.walk(root, followlinks=False, onerror=fail_walk):
        for name in children + files:
            count += 1
            require(count <= 100000, 'Protected tree exceeds reviewed audit bound')
            path = Path(directory) / name
            if path.is_symlink():
                target = path.resolve(strict=True)
                if not target.is_relative_to(root):
                    require(target.is_file(), 'External directory link requires review')
                    trusted(target, directory=False)
                    paths.update((target, *target.parents))
            paths.add(path)
    no_write_access(account, sorted(paths))


def verify_disabled(manifest, bodies, *, before=None, receipt_required=True):
    require_root()
    account = account_disabled()
    verify_disabled_authentication()
    if receipt_required:
        check_storage(installed=True)
    else:
        # Receipt is written only after every effective-access test passes.
        with_receipt = {p.name for p in MODULES.iterdir()}
        require(with_receipt == set(OLD_MODULES) | {WRAPPER.name, GATE.name}, 'Unexpected partial preparation contents')
        for path, mode in ((MODULES, 0o755), (STATE, 0o711), (STATE / 'public', 0o755)):
            trusted(path, mode=mode)
        require({p.name for p in STATE.iterdir()} == {'public'}
                and {p.name for p in (STATE / 'public').iterdir()} == {'index.json'}, 'Production state changed')
        for path in (KEY, INCLUDE, ACTIVATION, STATE / 'reports.sqlite3', STATE / 'publisher.lock'):
            require(absent(path), 'Production must remain disabled')
    trusted(HOME, mode=0o755)
    require(not list(HOME.iterdir()), 'Reporting home must remain empty')
    trusted(WRAPPER, mode=0o644, directory=False)
    require(read_file(WRAPPER) == bodies[WRAPPER.name], 'Installed wrapper mismatch')
    trusted(GATE, mode=0o644, directory=False)
    require(read_file(GATE) == encoded(DISABLED_GATE) + b'\n', 'Publication gate is not disabled')
    if receipt_required:
        trusted(RECEIPT, mode=0o600, directory=False)
        receipt = decode(read_file(RECEIPT, 65536))
        require(set(receipt) == {'schema_version', 'scope', 'manifest', 'before', 'backup', 'uid', 'gid'}
                and type(receipt['schema_version']) is int and receipt['schema_version'] == 1
                and receipt['scope'] == 'disabled-production-preparation' and receipt['manifest'] == manifest
                and receipt['uid'] == account.pw_uid and receipt['gid'] == account.pw_gid,
                'Private preparation receipt mismatch')
        backup = Path(receipt['backup'])
        require(backup.parent == ADMIN_HOME and re.fullmatch(r'homeenergy-production-backup\.[A-Za-z0-9_-]+', backup.name),
                'Unexpected backup reference')
        trusted(backup, mode=0o700, private_admin_parent=True)
        require({path.name for path in backup.iterdir()} == {'baseline.json', 'identity.json'},
                'Unexpected private evidence files')
        trusted(backup / 'baseline.json', mode=0o600, directory=False, private_admin_parent=True)
        require(decode(read_file(backup / 'baseline.json', 65536)) == receipt['before'], 'Private evidence changed')
        trusted(backup / 'identity.json', mode=0o600, directory=False, private_admin_parent=True)
        require(decode(read_file(backup / 'identity.json', 2048)) == {
            'schema_version': 1, 'user': USER, 'uid': account.pw_uid, 'gid': account.pw_gid},
            'Private identity evidence changed')
        before = receipt['before']
    require(isinstance(before, dict), 'Missing preservation evidence')
    for path in PROTECTED_TREES:
        audit_tree(account, path)
    for path in PRIVATE_PATHS:
        if path.exists():
            require(not access_allowed(account, '-x', path), 'Reporter can traverse a private credential/state directory')
    require(not access_allowed(account, '-w', STATE) and not access_allowed(account, '-w', STATE / 'public'),
            'Disabled reporter can modify public report storage')
    verify_gate_rejection(account)
    require(absent(STATE / 'reports.sqlite3') and absent(STATE / 'publisher.lock')
            and {p.name for p in STATE.iterdir()} == {'public'}
            and {p.name for p in (STATE / 'public').iterdir()} == {'index.json'},
            'Disabled wrapper changed report-state contents')
    after = preserved_snapshot()
    compare_baseline(manifest, after)
    require(after == before, 'Protected bytes, ownership or services changed during preparation')
    return account


def create_file(path, body, mode, *, backup=None):
    require(path in (WRAPPER, GATE, RECEIPT) or (backup is not None and
            path in (backup / 'baseline.json', backup / 'identity.json')),
            'New-file target is not allowlisted')
    require(absent(path), 'Refusing to overwrite a preparation file')
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
    with os.fdopen(os.open(path, flags, mode), 'wb') as stream:
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())
        os.fchown(stream.fileno(), 0, 0)
        os.fchmod(stream.fileno(), mode)
    trusted(path, mode=mode, directory=False, private_admin_parent=backup is not None)
    require(read_file(path) == body, 'New-file readback mismatch')


def prepare_disabled(manifest, bodies):
    before = check(manifest)
    os.umask(0o077)
    backup = Path(tempfile.mkdtemp(prefix='homeenergy-production-backup.', dir=ADMIN_HOME))
    os.chown(backup, 0, 0)
    os.chmod(backup, 0o700)
    trusted(backup, mode=0o700, private_admin_parent=True)
    create_file(backup / 'baseline.json', encoded(before) + b'\n', 0o600, backup=backup)
    print('Private preparation evidence: ' + str(backup), flush=True)
    try:
        account_absent()
        require(preserved_snapshot() == before, 'Protected state changed before preparation')
        run(['/usr/sbin/groupadd', '--system', USER])
        # The identity is expired and password-locked at creation, never active.
        run(['/usr/sbin/useradd', '--system', '--gid', USER, '--no-create-home', '--home-dir', str(HOME),
             '--shell', '/bin/dash', '--expiredate', '1970-01-02', '--inactive', '-1', '--password', '!', USER])
        import pwd
        created_account = pwd.getpwnam(USER)
        require(created_account.pw_uid > 0 and created_account.pw_gid > 0, 'Unexpected new account identity')
        create_file(backup / 'identity.json', encoded({'schema_version': 1, 'user': USER,
                    'uid': created_account.pw_uid, 'gid': created_account.pw_gid}) + b'\n', 0o600, backup=backup)
        require(absent(HOME), 'Refusing an existing reporting home')
        HOME.mkdir(mode=0o755)
        os.chown(HOME, 0, 0)
        os.chmod(HOME, 0o755)
        trusted(HOME, mode=0o755)
        create_file(WRAPPER, bodies[WRAPPER.name], 0o644)
        create_file(GATE, encoded(DISABLED_GATE) + b'\n', 0o644)
        account = verify_disabled(manifest, bodies, before=before, receipt_required=False)
        receipt = {'schema_version': 1, 'scope': 'disabled-production-preparation', 'manifest': manifest,
                   'before': before, 'backup': str(backup), 'uid': account.pw_uid, 'gid': account.pw_gid}
        create_file(RECEIPT, encoded(receipt) + b'\n', 0o600)
        verify_disabled(manifest, bodies)
    except BaseException:
        print('Preparation did not complete. Retained resources require administrator review; do not rerun blindly.', flush=True)
        print('No key, active gate, SSH policy, public report or schedule was installed by this helper.', flush=True)
        raise
    print('Disabled production preparation verified. Account expired; passwords locked; public storage unchanged.')
    print('No key, SSH policy, database, public report, model call or schedule activation; no service/device restart.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--build-manifest', type=Path, metavar='ABS_REPO')
    modes.add_argument('--check', action='store_true')
    modes.add_argument('--prepare-disabled', action='store_true')
    modes.add_argument('--verify-disabled', action='store_true')
    parser.add_argument('--baseline', type=Path, help='Private, freshly inspected JSON baseline; build mode only')
    args = parser.parse_args()
    if args.build_manifest is not None:
        require(args.baseline is not None, 'Build mode requires the reviewed private baseline JSON')
        print(encoded(build_manifest(args.build_manifest, args.baseline)).decode('utf-8'))
        return
    require(args.baseline is None, 'Baseline is accepted only when building a manifest')
    require_root()
    manifest, bodies = read_bundle(Path(__file__).resolve().parent)
    if args.check:
        check(manifest)
        print('Read-only production-preparation check passed. No account or runtime was changed.')
    elif args.prepare_disabled:
        prepare_disabled(manifest, bodies)
    else:
        verify_disabled(manifest, bodies)
        print('Disabled production preparation verified; publication and scheduling remain disabled.')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError, KeyError, subprocess.SubprocessError):
        print('Production preparation check failed. Preserve existing evidence and request administrator review.', file=sys.stderr)
        sys.exit(1)
