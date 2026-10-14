"""Install report-only SSH transport while publication remains disabled.

Creates one dedicated authorized-key file, one Match include and private audit
evidence. Only account expiry is removed, last; passwords stay locked. Does not
change the gate, storage, application code, history, model runtime or schedule.
"""
import argparse
import base64
import hashlib
import os
from pathlib import Path
import re
import stat
import sys
import tempfile


FINALIZER = Path('/home/shini/homeenergy-preparation-finalize.JFDO36Uw/finalize-report-preparation.py')
FINALIZER_SHA256 = 'a21a7ad2d5d1a078eea57842703bbe476b796c1a710ba69cfc5dbbf2c3e311f5'
# Dedicated production public key; the private key stays on the Windows host.
PUBLIC_KEY_SHA256 = '54f417401aa4cda931ece3cc30173787b268cab7e420168a2beb761b6200926d'
FORCE = ('/usr/bin/timeout --signal=TERM --kill-after=5s 45s '
         '/usr/bin/python3 -I -B /opt/homeenergy-reports/insights_production_publish.py')
OTHER_USERS = ('shini', 'homeenergy-reporter-staging')
EXPECTED_SSH = {
    'authenticationmethods': 'publickey', 'pubkeyauthentication': 'yes',
    'passwordauthentication': 'no', 'kbdinteractiveauthentication': 'no',
    'authorizedkeysfile': '/etc/ssh/authorized_keys/homeenergy-reporter',
    'authorizedkeyscommand': 'none', 'trustedusercakeys': 'none',
    'pubkeyacceptedalgorithms': 'ssh-ed25519', 'forcecommand': FORCE,
    'disableforwarding': 'yes', 'allowagentforwarding': 'no',
    'allowtcpforwarding': 'no', 'x11forwarding': 'no', 'permittty': 'no',
    'permittunnel': 'no', 'permituserrc': 'no', 'maxsessions': '1',
    'permituserenvironment': 'no', 'usepam': 'yes',
}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def load_prior():
    """Execute pinned source only; never call any earlier mutation entrypoint."""
    import pwd
    admin = pwd.getpwnam('shini').pw_uid
    require(FINALIZER.resolve(strict=True) == FINALIZER, 'Prior finalizer path changed')
    for path in FINALIZER.parents:
        info = path.lstat()
        owner = admin if path in (FINALIZER.parent, Path('/home/shini')) else 0
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == owner
                and not info.st_mode & 0o022, 'Prior finalizer parent changed')
    require(stat.S_IMODE(FINALIZER.parent.stat().st_mode) == 0o700, 'Prior release is not private')
    info = FINALIZER.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid in (0, admin)
            and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1
            and 0 < info.st_size <= 65536, 'Prior finalizer metadata changed')
    with os.fdopen(os.open(FINALIZER, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as stream:
        opened = os.fstat(stream.fileno())
        require((opened.st_dev, opened.st_ino, opened.st_mode, opened.st_uid,
                 opened.st_gid, opened.st_nlink, opened.st_size) ==
                (info.st_dev, info.st_ino, info.st_mode, info.st_uid,
                 info.st_gid, info.st_nlink, info.st_size), 'Prior source changed while opening')
        body = stream.read(65537)
    require(hashlib.sha256(body).hexdigest() == FINALIZER_SHA256, 'Prior finalizer digest changed')
    namespace = {'__name__': '_reviewed_access_checks', '__file__': str(FINALIZER)}
    exec(compile(body, str(FINALIZER), 'exec'), namespace)
    recovery = namespace['load_recovery']()
    return recovery, recovery['load_reviewed_checks']()


def authorized_line(body):
    require(isinstance(body, bytes) and 0 < len(body) <= 256, 'Public key size refused')
    fields = body.decode('ascii').strip().split()
    require(len(fields) == 3 and fields[0] == 'ssh-ed25519'
            and fields[2] == 'homeenergy-production-only', 'Dedicated public key format refused')
    raw = base64.b64decode(fields[1], validate=True)
    require(len(raw) == 51 and raw[:19] == b'\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20'
            and base64.b64encode(raw).decode('ascii') == fields[1], 'Invalid Ed25519 public key')
    return ('restrict,command="' + FORCE + '" ' + ' '.join(fields) + '\n').encode('ascii')


def read_bundle(checks):
    import pwd
    source = Path(__file__).resolve().parent
    require(source.parent == Path('/home/shini') and re.fullmatch(
        r'homeenergy-report-access\.[A-Za-z0-9_-]+', source.name), 'Unexpected access release')
    admin = pwd.getpwnam('shini').pw_uid
    for path in (source, *source.parents):
        info = path.lstat()
        owner = admin if path in (source, Path('/home/shini')) else 0
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == owner
                and not info.st_mode & 0o022, 'Untrusted access release parent')
    require(stat.S_IMODE(source.stat().st_mode) == 0o700
            and {p.name for p in source.iterdir()} == {'activate-report-access.py', 'reporter-key.pub'},
            'Access release is not private or contains unexpected files')
    for name in ('activate-report-access.py', 'reporter-key.pub'):
        info = (source / name).lstat()
        require(info.st_uid in (0, admin) and stat.S_IMODE(info.st_mode) == 0o600,
                'Untrusted access release file')
    key = checks['read_file'](source / 'reporter-key.pub', 256)
    require(PUBLIC_KEY_SHA256 != '0' * 64 and checks['digest'](key) == PUBLIC_KEY_SHA256,
            'Dedicated public key digest mismatch')
    return authorized_line(key)


def effective(checks, user, config=None):
    context = checks['SSH_CONTEXT'].replace('user=homeenergy-reporter,', 'user=' + user + ',', 1)
    args = ['/usr/sbin/sshd', '-T', '-C', context]
    if config is not None:
        args.extend(('-f', config))
    result = checks['run'](args)
    require(result.stderr == '', 'Effective SSH inspection produced diagnostics')
    settings = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition(' ')
        require(separator and key, 'Unrecognized effective SSH setting')
        settings[key] = (settings[key] + ' ' if key in settings else '') + value
    return settings


def restricted(settings):
    require(all(settings.get(key) == value for key, value in EXPECTED_SSH.items()),
            'Effective reporting SSH restrictions do not match')
    require(set(settings.get('acceptenv', '').split()) <= {'LANG', 'LC_*'}
            and settings.get('setenv', 'none') == 'none', 'Unexpected SSH environment')


def candidate_main(original, candidate_path, includes, include_name):
    pattern = rb'(?m)^[ \t]*Include[ \t]+/etc/ssh/sshd_config\.d/\*\.conf[ \t]*(?:#[^\r\n]*)?\r?$'
    require(len(re.findall(pattern, original)) == 1, 'Unexpected SSH Include structure')
    entries = [(path.name, path.as_posix()) for path in includes]
    require(all(re.fullmatch(r'[A-Za-z0-9_.-]+\.conf', name) for name, _ in entries)
            and include_name not in {name for name, _ in entries}, 'Unexpected SSH include inventory')
    entries.append((include_name, candidate_path))
    return re.sub(pattern, lambda _: ('Include ' + ' '.join(value for _, value in sorted(entries))).encode('ascii'),
                  original, count=1)


def candidate_policy(checks, policy, other):
    descriptors = []
    def memory_file(body):
        descriptor = os.memfd_create('homeenergy-report-access-check', flags=os.MFD_CLOEXEC)
        descriptors.append(descriptor)
        with os.fdopen(os.dup(descriptor), 'wb') as stream:
            stream.write(body)
            stream.flush()
        return f'/proc/{os.getpid()}/fd/{descriptor}'
    try:
        candidate = memory_file(policy)
        config = memory_file(candidate_main(checks['read_file'](checks['SSH_CONFIG']), candidate,
                             list(checks['INCLUDE'].parent.glob('*.conf')), checks['INCLUDE'].name))
        checks['run'](['/usr/sbin/sshd', '-t', '-f', config])
        restricted(effective(checks, checks['USER'], config))
        require(all(effective(checks, user, config) == settings for user, settings in other.items()),
                'Candidate changes administrator or staging SSH access')
    finally:
        for descriptor in descriptors:
            os.close(descriptor)


def shadow_metadata(shadow):
    # No password hash is persisted or printed. Full in-memory comparison during
    # activation also ensures the locked hash itself is not modified.
    return {name: getattr(shadow, name) for name in (
        'sp_namp', 'sp_lstchg', 'sp_min', 'sp_max', 'sp_warn', 'sp_inact', 'sp_flag')}


def account_state(recovery, checks, expiry):
    account = recovery['identity']()
    shadow = recovery['shadow_state']()
    require(shadow.sp_expire == expiry, 'Unexpected reporting account expiry')
    checks['run'](['/usr/sbin/visudo', '-c'])
    denial = checks['run'](['/usr/bin/sudo', '-n', '-l', '-U', checks['USER']], check=False)
    require(denial.returncode in (0, 1) and denial.stderr == '' and re.fullmatch(
        r'User homeenergy-reporter is not allowed to run sudo on [A-Za-z0-9_.-]+\.',
        denial.stdout.strip()), 'Sudo denial is not conclusively established')
    return account, shadow


def preflight(recovery, checks, policy):
    manifest, bodies = checks['read_bundle'](recovery['RELEASE'])
    receipt_bytes = checks['read_file'](checks['RECEIPT'], 65536)
    evidence = recovery['evidence_digest'](checks)
    checks['verify_disabled'](manifest, bodies)
    receipt = checks['decode'](receipt_bytes)
    require(checks['absent'](Path('/etc/ssh/sshrc')), 'Global SSH rc requires separate review')
    checks['trusted'](checks['KEY'].parent, mode=0o755)
    account, shadow = account_state(recovery, checks, 1)
    other = {user: effective(checks, user) for user in OTHER_USERS}
    candidate_policy(checks, policy, other)
    before = {'schema_version': 1, 'scope': 'report-ssh-access-publication-disabled',
              'preserved': checks['preserved_snapshot'](),
              'preparation_sha256': checks['digest'](receipt_bytes),
              'original_evidence': evidence,
              'other_ssh': other, 'shadow_metadata': shadow_metadata(shadow),
              'key_sha256': PUBLIC_KEY_SHA256, 'policy_sha256': checks['digest'](policy),
              'uid': account.pw_uid, 'gid': account.pw_gid}
    require(before['preserved'] == receipt['before']
            and checks['read_file'](checks['RECEIPT'], 65536) == receipt_bytes
            and recovery['evidence_digest'](checks) == evidence,
            'Preparation baseline or evidence changed during preflight')
    return before, shadow


def audit(recovery, checks, policy, key, before, *, expiry):
    """Post-install audit; intentionally distinct from disabled-preparation checks."""
    account, shadow = account_state(recovery, checks, expiry)
    require((account.pw_uid, account.pw_gid) == (before['uid'], before['gid'])
            and shadow_metadata(shadow) == before['shadow_metadata'], 'Account fields changed')
    require(checks['absent'](Path('/etc/ssh/sshrc')), 'Global SSH rc appeared')
    checks['run'](['/usr/sbin/sshd', '-t'])
    restricted(effective(checks, checks['USER']))
    require(all(effective(checks, user) == settings for user, settings in before['other_ssh'].items()),
            'Administrator or staging SSH settings changed')
    for path, body in ((checks['KEY'], key), (checks['INCLUDE'], policy)):
        checks['trusted'](path, mode=0o644, directory=False)
        require(checks['read_file'](path) == body, 'Installed access file changed')
    checks['trusted'](checks['KEY'].parent, mode=0o755)
    for path, mode in ((checks['MODULES'], 0o755), (checks['HOME'], 0o755),
                       (checks['STATE'], 0o711), (checks['STATE'] / 'public', 0o755)):
        checks['trusted'](path, mode=mode)
    require(not list(checks['HOME'].iterdir()), 'Reporting home is not empty')
    require({p.name for p in checks['MODULES'].iterdir()} == set(checks['OLD_MODULES']) |
            {checks['WRAPPER'].name, checks['GATE'].name, checks['RECEIPT'].name},
            'Reporting module inventory changed')
    for path in (checks['WRAPPER'], checks['GATE']):
        checks['trusted'](path, mode=0o644, directory=False)
    manifest, bodies = checks['read_bundle'](recovery['RELEASE'])
    require(checks['read_file'](checks['WRAPPER']) == bodies[checks['WRAPPER'].name]
            and checks['read_file'](checks['GATE']) == checks['encoded'](checks['DISABLED_GATE']) + b'\n',
            'Wrapper changed or public publishing gate is not closed')
    checks['trusted'](checks['RECEIPT'], mode=0o600, directory=False)
    checks['trusted'](recovery['BACKUP'], mode=0o700, private_admin_parent=True)
    require({p.name for p in recovery['BACKUP'].iterdir()} == {'baseline.json', 'identity.json'},
            'Original evidence inventory changed')
    for name in ('baseline.json', 'identity.json'):
        checks['trusted'](recovery['BACKUP'] / name, mode=0o600, directory=False, private_admin_parent=True)
    require(checks['digest'](checks['read_file'](checks['RECEIPT'], 65536)) == before['preparation_sha256']
            and recovery['evidence_digest'](checks) == before['original_evidence'], 'Original evidence changed')
    require(checks['absent'](checks['ACTIVATION']) and
            {p.name for p in checks['STATE'].iterdir()} == {'public'} and
            {p.name for p in (checks['STATE'] / 'public').iterdir()} == {'index.json'},
            'Report activation or state appeared')
    for root in checks['PROTECTED_TREES']:
        checks['audit_tree'](account, root)
    for path in checks['PRIVATE_PATHS']:
        if path.exists():
            require(not checks['access_allowed'](account, '-x', path), 'Private directory access allowed')
    checks['verify_gate_rejection'](account)
    require({p.name for p in checks['STATE'].iterdir()} == {'public'} and
            {p.name for p in (checks['STATE'] / 'public').iterdir()} == {'index.json'},
            'Closed-gate probe modified report storage')
    current = checks['preserved_snapshot']()
    added = current['files'].pop(str(checks['INCLUDE']), None)
    require(added == {'sha256': checks['digest'](policy), 'uid': 0, 'gid': 0, 'mode': 0o644},
            'Unexpected managed SSH include metadata')
    require(current == before['preserved'], 'An unrelated protected file or service changed')
    checks['compare_baseline'](manifest, current)
    return shadow


def create_file(checks, path, body, mode, *, backup=None, created=None):
    allowed = {checks['KEY'], checks['INCLUDE']}
    if backup is not None:
        allowed |= {backup / 'baseline.json', backup / 'completed.json'}
    require(path in allowed and checks['absent'](path), 'New access file target refused')
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode), 'wb') as stream:
        if created is not None:
            info = os.fstat(stream.fileno())
            created[path] = (info.st_dev, info.st_ino)
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())
        os.fchown(stream.fileno(), 0, 0)
        os.fchmod(stream.fileno(), mode)
    checks['trusted'](path, mode=mode, directory=False, private_admin_parent=backup is not None)
    require(checks['read_file'](path) == body, 'Access file read-back mismatch')


def remove_exact(checks, path, body, created):
    require(path in (checks['KEY'], checks['INCLUDE']), 'Rollback target refused')
    if checks['absent'](path):
        return
    info = path.lstat()
    require(created.get(path) == (info.st_dev, info.st_ino),
            'Rollback file was not created by this invocation; preserve for review')
    checks['trusted'](path, mode=0o644, directory=False)
    require(checks['read_file'](path) == body, 'Rollback file changed; preserve for review')
    path.unlink()
    require(checks['absent'](path), 'Rollback removal not verified')


def rollback(recovery, checks, policy, key, created):
    # Expire before removing policy; never leave an authorized key with an
    # unrestricted account. Existing bounded sessions are not killed by expiry.
    expired = False
    revoked = False
    try:
        checks['run'](['/usr/bin/chage', '--expiredate', '1', checks['USER']])
        account_state(recovery, checks, 1)
        expired = True
    except BaseException:
        print('Rollback expiry verification failed; attempting owned-key revocation.', flush=True)
    try:
        remove_exact(checks, checks['KEY'], key, created)
        revoked = checks['absent'](checks['KEY'])
    except BaseException:
        print('Rollback key revocation needs review; restrictive policy retained.', flush=True)
    require(expired and revoked, 'Rollback could not certify expiry and key revocation; policy retained')
    remove_exact(checks, checks['INCLUDE'], policy, created)
    checks['run'](['/usr/sbin/sshd', '-t'])
    checks['run'](['/usr/bin/systemctl', 'reload', 'ssh.service'])
    require(checks['run'](['/usr/bin/systemctl', 'is-active', 'ssh.service']).stdout.strip() == 'active',
            'SSH is not active after rollback reload')


def activate(recovery, checks, policy, key):
    before, shadow_before = preflight(recovery, checks, policy)
    os.umask(0o077)
    backup = Path(tempfile.mkdtemp(prefix='homeenergy-report-access-backup.', dir=checks['ADMIN_HOME']))
    os.chown(backup, 0, 0)
    os.chmod(backup, 0o700)
    checks['trusted'](backup, mode=0o700, private_admin_parent=True)
    create_file(checks, backup / 'baseline.json', checks['encoded'](before) + b'\n', 0o600, backup=backup)
    print('Private access evidence: ' + str(backup), flush=True)
    created = {}
    try:
        # Compare again immediately before writes, without refreshing baseline.
        require(checks['preserved_snapshot']() == before['preserved']
                and recovery['shadow_state']() == shadow_before, 'State changed before SSH installation')
        create_file(checks, checks['INCLUDE'], policy, 0o644, created=created)
        restricted(effective(checks, checks['USER']))
        account_state(recovery, checks, 1)
        create_file(checks, checks['KEY'], key, 0o644, created=created)
        audit(recovery, checks, policy, key, before, expiry=1)
        checks['run'](['/usr/bin/systemctl', 'reload', 'ssh.service'])
        require(checks['run'](['/usr/bin/systemctl', 'is-active', 'ssh.service']).stdout.strip() == 'active',
                'SSH is not active after reload')
        # This is the only enabling mutation, after installation and verification.
        checks['run'](['/usr/bin/chage', '--expiredate', '-1', checks['USER']])
        shadow_after = audit(recovery, checks, policy, key, before, expiry=-1)
        require(all(getattr(shadow_before, field) == getattr(shadow_after, field)
                    for field in recovery['SHADOW_UNCHANGED']), 'Unrelated shadow field changed')
        completion = {'schema_version': 1, 'scope': before['scope'],
                      'baseline_sha256': checks['digest'](checks['encoded'](before) + b'\n'),
                      'public_publishing_enabled': False, 'client_authentication_verified': False}
        create_file(checks, backup / 'completed.json', checks['encoded'](completion) + b'\n', 0o600, backup=backup)
    except BaseException:
        try:
            rollback(recovery, checks, policy, key, created)
            print('Access rollback completed: account expired and exact new key/policy removed.', flush=True)
        except BaseException:
            print('Access rollback incomplete. Keep this administrator session open and request review.', flush=True)
        print('All private evidence retained; do not rerun activation blindly.', flush=True)
        raise
    print('Restricted reporting SSH policy installed. Only SSH was reloaded.')
    print('Password remains locked. Public publishing gate and report storage remain disabled/unchanged.')
    print('No model call, public report, schedule change or application/device restart.')
    print('Keep this administrator session open. Real client authentication/denial tests are still required.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--check', action='store_true')
    modes.add_argument('--activate-transport', action='store_true')
    args = parser.parse_args()
    require(sys.platform == 'linux' and os.geteuid() == 0 and sys.flags.isolated == 1
            and sys.flags.dont_write_bytecode == 1, 'Run with sudo /usr/bin/python3 -I -B')
    recovery, checks = load_prior()
    key = read_bundle(checks)
    _, bodies = checks['read_bundle'](recovery['RELEASE'])
    policy = bodies['reports-production-sshd.conf.example']
    if args.check:
        preflight(recovery, checks, policy)
        print('Read-only report SSH access check passed. No changes made.')
    else:
        activate(recovery, checks, policy, key)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        detail = str(error) if isinstance(error, RuntimeError) else type(error).__name__
        print('Reporting SSH access stopped: ' + detail, file=sys.stderr)
        print('Preserve all evidence. Keep the administrator session open; do not rerun blindly.', file=sys.stderr)
        sys.exit(1)
