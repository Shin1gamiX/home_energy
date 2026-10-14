"""Read-only phase-B server checks. Does not provision or repair anything.

Run on the publishing server as root AFTER a separately approved account setup:
  python3 -B check-report-isolation.py --peer-ip BROKER_IP --peer-host BROKER_HOST \
      --local-ip SERVER_IP --local-port SSH_PORT
This is one gate, not a substitute for real restricted-key or runner tests.
"""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys


USER = 'homeenergy-reporter'
HOME = Path('/var/lib/homeenergy-reporter-home')
MODULES = Path('/opt/homeenergy-reports')
STATE = Path('/var/lib/homeenergy-reports')
KEYS = Path('/etc/ssh/authorized_keys/homeenergy-reporter')
HISTORY = Path('/var/www/homeenergy/runtime/history')
FORCED_COMMAND = ('/usr/bin/timeout --signal=TERM --kill-after=5s 45s '
                  '/usr/bin/python3 -I /opt/homeenergy-reports/insights_publish.py')


def secure_path(path, owner, mode, directory=False):
    if not path.is_absolute() or path.resolve() != path:
        return False
    info = path.lstat()
    kind = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if not kind or info.st_uid != owner or stat.S_IMODE(info.st_mode) != mode:
        return False
    for parent in path.parents:
        meta = parent.lstat()
        # Reporter-owned state ancestors are checked independently below.
        if parent == STATE or parent == STATE / 'public':
            continue
        if not stat.S_ISDIR(meta.st_mode) or meta.st_uid != 0 or meta.st_mode & 0o022:
            return False
    return True


def effective_settings(peer_ip, peer_host, local_ip, local_port):
    raw = subprocess.check_output(['/usr/sbin/sshd', '-T', '-C',
                                   f'user={USER},host={peer_host},addr={peer_ip},'
                                   f'laddr={local_ip},lport={local_port}'], text=True)
    return parse_settings(raw)


def parse_settings(raw):
    settings = {}
    for line in raw.splitlines():
        if ' ' not in line:
            continue
        key, value = line.split(' ', 1)
        # sshd can print multiple AcceptEnv lines. Never drop an earlier pattern.
        if key in settings:
            settings[key] += ' ' + value
        else:
            settings[key] = value
    return settings


def sudo_denied(config_result, policy_result):
    return config_result.returncode == 0 and policy_result.returncode == 1 and bool(re.fullmatch(
        r'User ' + USER + r' is not allowed to run sudo on [A-Za-z0-9_.-]+\.',
        (policy_result.stdout + policy_result.stderr).strip()))


def protected_tree_readonly(root, permitted):
    if not root.is_dir() or root.resolve() != root:
        return False
    if any(permitted('-w', path) for path in (root, *root.parents)):
        return False
    count = 0

    def fail_walk(error):
        raise error

    try:
        for directory, children, files in os.walk(root, followlinks=False, onerror=fail_walk):
            for name in children + files:
                count += 1
                if count > 100000:
                    return False
                path = Path(directory) / name
                # In-root links are covered by the same scan. Out-of-scope links,
                # loops and unreadable directories require administrator review.
                if path.is_symlink() and not path.resolve(strict=True).is_relative_to(root):
                    return False
                if permitted('-w', path):
                    return False
    except (OSError, RuntimeError):
        return False
    return True


def checks(peer_ip, peer_host, local_ip, local_port):
    import grp
    import pwd
    results = []

    def record(name, passed):
        results.append({'check': name, 'passed': bool(passed)})

    try:
        account = pwd.getpwnam(USER)
    except KeyError:
        record('dedicated reporting account exists', False)
        return results
    record('non-root account with dedicated home', account.pw_uid != 0 and account.pw_dir == str(HOME))
    record('dash login shell', account.pw_shell in ('/bin/dash', '/bin/sh') and
           Path(account.pw_shell).resolve() == Path('/usr/bin/dash').resolve())
    groups = os.getgrouplist(USER, account.pw_gid)
    record('only its dedicated group', groups == [account.pw_gid] and grp.getgrgid(account.pw_gid).gr_name == USER)
    # Exit 1 alone can also mean a broken policy/plugin; require a recognized
    # C-locale denial and successful sudoers validation. Never print policy text.
    environment = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C'}
    config = subprocess.run(['/usr/sbin/visudo', '-c'], stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, env=environment, timeout=20)
    policy = subprocess.run(['/usr/bin/sudo', '-n', '-l', '-U', USER],
                            capture_output=True, text=True, env=environment, timeout=20)
    record('recognized sudo denial (otherwise manual review)', sudo_denied(config, policy))
    for path, owner, mode, directory in (
            (HOME, 0, 0o755, True), (MODULES, 0, 0o755, True), (KEYS, 0, 0o644, False),
            (STATE, account.pw_uid, 0o711, True), (STATE / 'public', account.pw_uid, 0o755, True)):
        try:
            record('ownership and mode: ' + str(path), secure_path(path, owner, mode, directory))
        except OSError:
            record('ownership and mode: ' + str(path), False)
    for name in ('insights_publish.py', 'insights_analysis.py', 'insights_i18n.py'):
        try:
            record('root-owned module: ' + name, secure_path(MODULES / name, 0, 0o644))
        except OSError:
            record('root-owned module: ' + name, False)
    try:
        # Root owns the file: never print the public key or any submitted values.
        lines = [line for line in KEYS.read_text().splitlines() if line.strip() and not line.startswith('#')]
        record('one restricted Ed25519 reporting key', len(lines) == 1 and
               len(lines[0].split()) == 3 and lines[0].split()[:2] == ['restrict', 'ssh-ed25519'])
    except OSError:
        record('one restricted Ed25519 reporting key', False)
    settings = effective_settings(peer_ip, peer_host, local_ip, local_port)
    expected = {'forcecommand': FORCED_COMMAND, 'authenticationmethods': 'publickey',
                'pubkeyauthentication': 'yes', 'passwordauthentication': 'no',
                'kbdinteractiveauthentication': 'no', 'disableforwarding': 'yes',
                'permittty': 'no', 'permittunnel': 'no', 'permituserrc': 'no',
                'permituserenvironment': 'no', 'authorizedkeysfile': str(KEYS),
                'maxsessions': '1'}
    for key, value in expected.items():
        record('effective sshd: ' + key, settings.get(key) == value)
    record('only reviewed locale environment variables',
           set(settings.get('acceptenv', '').split()) <= {'LANG', 'LC_*'})
    # Effective access checks include POSIX ACLs; they do not attempt any writes.
    def permitted(flag, path):
        return subprocess.run(['/usr/sbin/runuser', '-u', USER, '--', '/usr/bin/test', flag, str(path)],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    record('history readable and traversable', permitted('-r', HISTORY) and permitted('-x', HISTORY))
    roots = [Path('/var/www/homeenergy'), Path('/etc/ssh'), Path('/etc/nginx'),
             MODULES, HOME, Path('/opt/homeenergy-control')]
    for root in roots:
        record('no reporter write access: ' + str(root),
               protected_tree_readonly(root, permitted))
    for path in (Path('/root'), Path('/home/shini'), Path('/etc/homeenergy-report-credentials')):
        if path.exists():
            record('private directory inaccessible: ' + str(path), not permitted('-x', path))
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--peer-ip', required=True)
    parser.add_argument('--peer-host', required=True)
    parser.add_argument('--local-ip', required=True)
    parser.add_argument('--local-port', required=True, type=int)
    args = parser.parse_args()
    ipaddress.ip_address(args.peer_ip)
    ipaddress.ip_address(args.local_ip)
    if not re.fullmatch(r'[A-Za-z0-9_.:-]+', args.peer_host) or not 1 <= args.local_port <= 65535:
        raise ValueError('Invalid connection context')
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise ValueError('Root is required for read-only effective permission checks')
    results = checks(args.peer_ip, args.peer_host, args.local_ip, args.local_port)
    print(json.dumps({'ready_for_restricted_key_tests': all(item['passed'] for item in results),
                      'checks': results}, indent=2))
    return 0 if results and all(item['passed'] for item in results) else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError, KeyError, subprocess.SubprocessError):
        print('{"ready_for_restricted_key_tests":false,"error":"Isolation audit could not complete"}')
        sys.exit(1)
