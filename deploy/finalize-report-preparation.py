"""Finalize only the missing receipt for the verified disabled preparation.

No account modification, provisioning, SSH installation, activation, publication,
model run, schedule change or service restart is supported. Existing receipts
are never overwritten or deleted. --check repeats read-only prerequisite checks.
"""
import argparse
import hashlib
import os
from pathlib import Path
import stat
import sys


RECOVERY = Path('/home/shini/homeenergy-expiry-recovery.fBPvTfCy/recover-report-expiry.py')
RECOVERY_SHA256 = 'fa1defe9bf19058bfa1b135b59463b6ba4557f37957cdb1ea7e15016a35b6851'
RECEIPT = Path('/opt/homeenergy-reports/production-preparation-receipt.json')


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def load_recovery():
    """Import definitions from verified bytes, with neither main nor .pyc use."""
    import pwd
    admin = pwd.getpwnam('shini').pw_uid
    require(RECOVERY.resolve(strict=True) == RECOVERY, 'Reviewed recovery path changed')
    for path in RECOVERY.parents:
        info = path.lstat()
        expected_owner = admin if path in (RECOVERY.parent, Path('/home/shini')) else 0
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == expected_owner
                and not info.st_mode & 0o022, 'Reviewed recovery parent changed')
    require(stat.S_IMODE(RECOVERY.parent.stat().st_mode) == 0o700, 'Recovery directory is not private')
    info = RECOVERY.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid in (0, admin)
            and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1
            and 0 < info.st_size <= 65536, 'Reviewed recovery file metadata changed')
    with os.fdopen(os.open(RECOVERY, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as stream:
        opened = os.fstat(stream.fileno())
        require((opened.st_dev, opened.st_ino, opened.st_size, opened.st_mode,
                 opened.st_uid, opened.st_gid, opened.st_nlink) ==
                (info.st_dev, info.st_ino, info.st_size, info.st_mode,
                 info.st_uid, info.st_gid, info.st_nlink), 'Recovery changed while opening')
        body = stream.read(65537)
    require(hashlib.sha256(body).hexdigest() == RECOVERY_SHA256, 'Reviewed recovery digest changed')
    namespace = {'__name__': '_reviewed_preparation_finalization', '__file__': str(RECOVERY)}
    exec(compile(body, str(RECOVERY), 'exec'), namespace)
    return namespace


def unchanged(recovery, checks, account, shadow, evidence):
    # Shadow records are compared in memory only; never output or persist them.
    require(recovery['identity']() == account and recovery['shadow_state']() == shadow
            and recovery['evidence_digest'](checks) == evidence,
            'Account or original evidence changed during finalization')


def sync_receipt_parent(checks):
    checks['trusted'](RECEIPT.parent, mode=0o755)
    descriptor = os.open(RECEIPT.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def finalize(recovery, checks, *, apply=False):
    checks['require_root']()
    require(checks['RECEIPT'] == RECEIPT, 'Unexpected receipt destination')
    require(checks['absent'](RECEIPT),
            'Receipt already exists; preserve it and use the original read-only --verify-disabled command')
    account = recovery['identity']()
    shadow = recovery['shadow_state']()
    require(shadow.sp_expire == 1, 'Account expiry must already be corrected before finalization')
    manifest, bodies, before = recovery['retained_state'](checks)
    evidence = recovery['evidence_digest'](checks)
    print('Repeating the original disabled-state checks before receipt creation.', flush=True)
    checks['verify_disabled'](manifest, bodies, before=before, receipt_required=False)
    unchanged(recovery, checks, account, shadow, evidence)
    require(recovery['retained_state'](checks) == (manifest, bodies, before),
            'Retained preparation changed during verification')
    if not apply:
        print('Read-only finalization check passed. No receipt or other resource was created.')
        return
    receipt = {'schema_version': 1, 'scope': 'disabled-production-preparation',
               'manifest': manifest, 'before': before, 'backup': str(recovery['BACKUP']),
               'uid': account.pw_uid, 'gid': account.pw_gid}
    receipt_bytes = checks['encoded'](receipt) + b'\n'
    require(len(receipt_bytes) <= 65536, 'Completion receipt exceeds the reviewed read-back limit')
    print('Creating only the root-owned mode-0600 completion receipt.', flush=True)
    # The reviewed writer uses O_EXCL, refuses existing paths, fsyncs and verifies
    # read-back. No overwrite, cleanup, account change or provisioning is called.
    checks['create_file'](RECEIPT, receipt_bytes, 0o600)
    sync_receipt_parent(checks)
    print('Receipt created. Verifying the complete disabled preparation.', flush=True)
    checks['verify_disabled'](manifest, bodies)
    unchanged(recovery, checks, account, shadow, evidence)
    require(checks['read_file'](RECEIPT, 65536) == receipt_bytes,
            'Completion receipt changed after verification')
    print('Disabled production preparation finalized and verified. Only the completion receipt was created.')
    print('Original account, evidence, protected files and service state are unchanged.')
    print('No key, SSH policy, report, database, model run, schedule activation or service/device restart.')
    print('Public publishing remains disabled.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check', action='store_true')
    mode.add_argument('--finalize', action='store_true')
    args = parser.parse_args()
    require(sys.platform == 'linux' and os.geteuid() == 0 and sys.flags.isolated == 1
            and sys.flags.dont_write_bytecode == 1, 'Run on the server with sudo /usr/bin/python3 -I -B')
    recovery = load_recovery()
    checks = recovery['load_reviewed_checks']()
    finalize(recovery, checks, apply=args.finalize)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # All validation messages come from pinned code; subprocess output,
        # shadow records and private evidence contents are never printed.
        detail = str(error) if isinstance(error, RuntimeError) else type(error).__name__
        print('Preparation finalization stopped: ' + detail, file=sys.stderr)
        print('A receipt may exist. Preserve it and all evidence; do not rerun provisioning or delete anything.', file=sys.stderr)
        sys.exit(1)
