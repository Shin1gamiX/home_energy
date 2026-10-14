"""One-account expiry recovery for the retained, disabled production preparation.

Never rerun provisioning. --check is read-only; --recover may change only the
existing reporter's expiry, then runs the original partial verification. No
receipt, key, policy, database, report or schedule is created or enabled.
"""
import argparse
import hashlib
import os
from pathlib import Path
import stat
import sys
import warnings


USER = 'homeenergy-reporter'
UID = 996
GID = 986
RELEASE = Path('/home/shini/homeenergy-production-release.GioFVQ8K')
BACKUP = Path('/home/shini/homeenergy-production-backup.9oe5dgb5')
HELPER = RELEASE / 'prepare-report-production.py'
HELPER_SHA256 = 'd0f3b66b5cdaa2aa80631fb14d8d28b21d1430a69614dac9d812567f6938c1a3'
MANIFEST_SHA256 = '8b2f07e63cd0385841bf42913d06c9345f499d8a085611ea0b402fe0175f0ffa'
SHADOW_UNCHANGED = ('sp_namp', 'sp_pwdp', 'sp_lstchg', 'sp_min', 'sp_max',
                    'sp_warn', 'sp_inact', 'sp_flag')
CHAGE = ['/usr/bin/chage', '--expiredate', '1', USER]


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def load_reviewed_checks():
    """Load only the reviewed source bytes, never adjacent bytecode/imports."""
    import pwd
    admin = pwd.getpwnam('shini').pw_uid
    require(RELEASE.resolve(strict=True) == RELEASE, 'Original release path changed')
    for path in (RELEASE, *RELEASE.parents):
        info = path.lstat()
        expected_owner = admin if path in (RELEASE, Path('/home/shini')) else 0
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == expected_owner
                and not info.st_mode & 0o022, 'Original release ownership changed')
    require(stat.S_IMODE(RELEASE.stat().st_mode) == 0o700, 'Original release is not private')
    contents = {}
    for path, expected in ((HELPER, HELPER_SHA256), (RELEASE / 'manifest.json', MANIFEST_SHA256)):
        info = path.lstat()
        require(stat.S_ISREG(info.st_mode) and info.st_uid in (0, admin)
                and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1
                and 0 < info.st_size <= 262144, 'Original source file metadata changed')
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as stream:
            opened = os.fstat(stream.fileno())
            require((opened.st_dev, opened.st_ino, opened.st_size, opened.st_mode,
                     opened.st_uid, opened.st_gid, opened.st_nlink) ==
                    (info.st_dev, info.st_ino, info.st_size, info.st_mode,
                     info.st_uid, info.st_gid, info.st_nlink), 'Original source changed while opening')
            body = stream.read(262145)
        require(hashlib.sha256(body).hexdigest() == expected, 'Original source digest changed')
        contents[path] = body
    checks = {'__name__': '_reviewed_expiry_recovery_checks', '__file__': str(HELPER)}
    exec(compile(contents[HELPER], str(HELPER), 'exec'), checks)
    return checks


def identity():
    import grp
    import pwd
    account = pwd.getpwnam(USER)
    require(account.pw_name == USER and account.pw_uid == UID and account.pw_gid == GID
            and account.pw_dir == '/var/lib/homeenergy-reporter-home'
            and account.pw_shell == '/bin/dash', 'Existing reporting identity changed')
    require(grp.getgrgid(GID).gr_name == USER and os.getgrouplist(USER, GID) == [GID],
            'Existing reporting groups changed')
    return account


def shadow_state():
    # Python 3.12's warning is not a security failure. Never log this record.
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', DeprecationWarning)
        import spwd
    value = spwd.getspnam(USER)
    require(value.sp_namp == USER and value.sp_pwdp.startswith('!')
            and value.sp_inact == -1 and value.sp_expire in (-1, 1),
            'Account is not in the reviewed locked expiry-recovery state')
    return value


def evidence_digest(checks):
    """Only hashes leave this function; private evidence contents stay local."""
    return {name: checks['digest'](checks['read_file'](BACKUP / name, 65536))
            for name in ('baseline.json', 'identity.json')}


def retained_state(checks):
    manifest, bodies = checks['read_bundle'](RELEASE)
    trusted = checks['trusted']
    trusted(BACKUP, mode=0o700, private_admin_parent=True)
    require({path.name for path in BACKUP.iterdir()} == {'baseline.json', 'identity.json'},
            'Original evidence inventory changed')
    for name in ('baseline.json', 'identity.json'):
        trusted(BACKUP / name, mode=0o600, directory=False, private_admin_parent=True)
    before = checks['decode'](checks['read_file'](BACKUP / 'baseline.json', 65536))
    recorded_identity = checks['decode'](checks['read_file'](BACKUP / 'identity.json', 2048))
    require(recorded_identity == {'schema_version': 1, 'user': USER, 'uid': UID, 'gid': GID},
            'Original identity evidence changed')
    modules, state = checks['MODULES'], checks['STATE']
    for path, mode in ((modules, 0o755), (state, 0o711), (state / 'public', 0o755),
                       (checks['HOME'], 0o755)):
        trusted(path, mode=mode)
    require(not list(checks['HOME'].iterdir()), 'Reporting home is not empty')
    require({path.name for path in modules.iterdir()} ==
            set(checks['OLD_MODULES']) | {checks['WRAPPER'].name, checks['GATE'].name},
            'Partial preparation inventory changed')
    require({path.name for path in state.iterdir()} == {'public'}
            and {path.name for path in (state / 'public').iterdir()} == {'index.json'},
            'Report state is no longer empty')
    for path in (checks['KEY'], checks['INCLUDE'], checks['ACTIVATION'], checks['RECEIPT'],
                 state / 'reports.sqlite3', state / 'publisher.lock'):
        require(checks['absent'](path), 'Unexpected activation or completion resource')
    for path in (checks['WRAPPER'], checks['GATE']):
        trusted(path, mode=0o644, directory=False)
    require(checks['read_file'](checks['WRAPPER']) == bodies[checks['WRAPPER'].name],
            'Installed wrapper changed')
    require(checks['read_file'](checks['GATE']) == checks['encoded'](checks['DISABLED_GATE']) + b'\n',
            'Publishing gate is not exactly closed')
    current = checks['preserved_snapshot']()
    checks['compare_baseline'](manifest, current)
    require(current == before, 'Protected files or services changed since preparation')
    return manifest, bodies, before


def recover(checks, *, apply=False):
    checks['require_root']()
    account_before = identity()
    shadow_before = shadow_state()
    manifest, bodies, before = retained_state(checks)
    evidence_before = evidence_digest(checks)
    if not apply:
        print('Recovery preflight passed. No changes made. Account expiry day: ' + str(shadow_before.sp_expire))
        return
    if shadow_before.sp_expire == -1:
        print('Setting only the existing reporting account expiry to day 1 (02/01/1970).', flush=True)
        # Numeric days are supported by the installed chage and avoid date parsing.
        checks['run'](CHAGE)
    shadow_after = shadow_state()
    require(shadow_after.sp_expire == 1, 'Expiry correction did not take effect')
    require(all(getattr(shadow_before, key) == getattr(shadow_after, key) for key in SHADOW_UNCHANGED)
            and identity() == account_before, 'An unrelated account field changed; administrator review required')
    require(evidence_digest(checks) == evidence_before, 'Original evidence changed')
    print('Expiry verified. Running the original read-only partial verification.', flush=True)
    checks['verify_disabled'](manifest, bodies, before=before, receipt_required=False)
    require(identity() == account_before and shadow_state() == shadow_after
            and evidence_digest(checks) == evidence_before, 'Account or evidence changed during verification')
    # Do not create the missing receipt: that is a separate completion decision.
    require(checks['absent'](checks['RECEIPT']), 'Unexpected completion receipt')
    print('Expiry corrected and disabled-state verification passed. Original account and evidence preserved.')
    print('No completion receipt, key, policy, database, report or schedule was created or enabled.')
    print('No service or device restart. Public publishing remains disabled.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check', action='store_true')
    mode.add_argument('--recover', action='store_true')
    args = parser.parse_args()
    require(sys.platform == 'linux' and os.geteuid() == 0 and sys.flags.isolated == 1
            and sys.flags.dont_write_bytecode == 1, 'Run on the server with sudo /usr/bin/python3 -I -B')
    recover(load_reviewed_checks(), apply=args.recover)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Reviewed checks use static messages; never print subprocess output or
        # shadow/account records. Non-validation exceptions expose only type.
        detail = str(error) if isinstance(error, RuntimeError) else type(error).__name__
        print('Expiry recovery stopped: ' + detail, file=sys.stderr)
        print('The account may already be expired. Keep all evidence; do not rerun provisioning.', file=sys.stderr)
        sys.exit(1)
