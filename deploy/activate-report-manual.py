"""One-time manual-report conversion. No report calls or evidence cleanup.

Run --check, then --activate as root. Backups include the unchanged database/feed.
On failure restore changed files, retaining new modules and all backup evidence.
HTTPS verification targets local Nginx; public Cloudflare access is checked separately.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile

MODULES = Path('/opt/homeenergy-reports')
PUBLIC = Path('/var/www/homeenergy/public')
STATE = Path('/var/lib/homeenergy-reports')
POLICY = Path('/etc/ssh/sshd_config.d/00-homeenergy-reporter-production.conf')
HOSTS = ('home.shin1gamix.com', 'home.shin1gamix.gr')
OLD_ENTRY = '/opt/homeenergy-reports/insights_production_publish.py'
NEW_ENTRY = '/opt/homeenergy-reports/insights_manual_entry.py'
OLD_VERSION = b'insights.js?v=20261013-insights-i18n'
NEW_VERSION = b'insights.js?v=20261017-manual-reviews'
OLD_DESCRIPTION = b'24-hour reviews, compared with the preceding week.'
NEW_DESCRIPTION = b'On-demand reviews of recorded energy and recent patterns.'
EXPECTED = {
    'index.html': '0004de70eb7207539c0e159163eff297c13e0a7c40ee786c6f69f9d5b4957265',
    'insights.js': 'a6a74382a20c10da0a3553c3706ed1023cd89cee3d74a5b6943ef54d262c5c0d',
    'ssh.conf': '84941a0f4ef89d7caf5f786b729c5cb82dda3407853d7a2293db3ecf17196b01',
}
TARGETS = {'index.html': PUBLIC / 'index.html', 'insights.js': PUBLIC / 'insights.js', 'ssh.conf': POLICY}
NEW_MODULES = ('insights_manual.py', 'insights_manual_entry.py')
PRESERVE = (
    STATE / 'reports.sqlite3', STATE / 'public/index.json',
    MODULES / 'insights_analysis.py', MODULES / 'insights_i18n.py', MODULES / 'insights_publish.py',
    MODULES / 'insights_production_publish.py', MODULES / 'production-gate.json',
    Path('/etc/ssh/authorized_keys/homeenergy-reporter'),
    Path('/etc/nginx/sites-available/homeenergy.conf'),
)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def read(path):
    info = path.lstat()
    require(path.resolve() == path and stat.S_ISREG(info.st_mode) and info.st_size <= 64 * 1024 * 1024,
            'Unexpected file: ' + str(path))
    return path.read_bytes()


def digest(body):
    return hashlib.sha256(body).hexdigest()


def fingerprint(path):
    info = path.stat()
    return {'sha256': digest(read(path)), 'uid': info.st_uid, 'gid': info.st_gid,
            'mode': stat.S_IMODE(info.st_mode), 'mtime_ns': info.st_mtime_ns}


def ssh_config(original):
    require(original.count(OLD_ENTRY.encode()) == 1 and NEW_ENTRY.encode() not in original,
            'Reporting SSH entrypoint is not the expected original')
    return original.replace(OLD_ENTRY.encode(), NEW_ENTRY.encode(), 1)


def check(source):
    require(os.geteuid() == 0, 'Run this helper with sudo')
    require(source.resolve() == source and source.parent == Path('/home/shini') and
            source.name.startswith('homeenergy-manual-release.'), 'Unexpected release location')
    bodies = {name: read(source / name) for name in (*NEW_MODULES, 'index.html', 'insights.js')}
    for name in NEW_MODULES:
        compile(bodies[name], name, 'exec', dont_inherit=True)
        destination = MODULES / name
        require(not destination.exists() or read(destination) == bodies[name], 'Different manual module already exists')
    originals = {name: read(path) for name, path in TARGETS.items()}
    for name, original in originals.items():
        require(digest(original) == EXPECTED[name], 'Existing file changed; inspect before activation: ' + name)
    candidate = bodies['index.html']
    require(candidate.count(NEW_VERSION) == 1 and candidate.count(NEW_DESCRIPTION) == 1 and
            candidate.replace(NEW_VERSION, OLD_VERSION).replace(NEW_DESCRIPTION, OLD_DESCRIPTION) == originals['index.html'],
            'Overview contains changes outside the manual-report text and asset version')
    bodies['ssh.conf'] = ssh_config(originals['ssh.conf'])
    subprocess.run(['/usr/sbin/sshd', '-t'], check=True)
    preserved = {str(path): fingerprint(path) for path in PRESERVE}
    require(json.loads(read(MODULES / 'production-gate.json')) == {
        'schema_version': 1, 'scope': 'production', 'public_publishing_enabled': True}, 'Unexpected reporting gate')
    return bodies, originals, preserved


def replace_file(path, body, uid, gid, mode):
    # Atomic same-directory replacement; no shell expansion or caller paths.
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '.manual-', dir=path.parent)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())
        os.fchown(stream.fileno(), uid, gid)
        os.fchmod(stream.fileno(), mode)
    os.replace(temporary, path)


def verify_preserved(before):
    require(all(fingerprint(Path(path)) == value for path, value in before.items()),
            'A preserved file changed; retain the backup and inspect')


def reload_ssh():
    subprocess.run(['/usr/sbin/sshd', '-t'], check=True)
    subprocess.run(['/usr/bin/systemctl', 'reload', 'ssh'], check=True)


def verify_policy():
    result = subprocess.run(['/usr/sbin/sshd', '-T', '-C',
                             'user=homeenergy-reporter,host=localhost,addr=127.0.0.1'],
                            capture_output=True, text=True, check=True)
    policy = dict(line.split(' ', 1) for line in result.stdout.splitlines())
    require(policy.get('forcecommand', '').endswith(' /usr/bin/python3 -I -B ' + NEW_ENTRY),
            'Effective SSH entrypoint does not match')
    for name, value in {'disableforwarding': 'yes', 'permittty': 'no', 'permituserrc': 'no',
                        'passwordauthentication': 'no', 'kbdinteractiveauthentication': 'no',
                        'authenticationmethods': 'publickey'}.items():
        require(policy.get(name) == value, 'Effective SSH restriction changed: ' + name)


def verify_origin_asset(host, route, expected):
    target = host + route
    # Keep the hostname for SNI/certificate checks, but never go through Cloudflare,
    # environment proxies, curl config files, or redirects during activation.
    command = [
        '/usr/bin/curl', '--disable', '--silent', '--show-error',
        '--noproxy', '*', '--proto', '=https',
        '--resolve', host + ':443:127.0.0.1',
        '--connect-timeout', '5', '--max-time', '15', '--max-filesize', '2000000',
        '--header', 'Cache-Control: no-cache', '--write-out', '\n%{http_code}',
        'https://' + target,
    ]
    try:
        result = subprocess.run(command, capture_output=True, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError('Local HTTPS check could not complete: ' + target) from exc
    require(result.returncode == 0,
            'Local HTTPS transport failed: ' + target + ' (curl exit ' + str(result.returncode) + ')')
    body, separator, status = result.stdout.rpartition(b'\n')
    status_label = status.decode('ascii') if len(status) == 3 and status.isdigit() else 'invalid status'
    require(separator and status == b'200', 'Local HTTPS returned ' + status_label + ': ' + target)
    require(len(body) <= 2_000_000, 'Local HTTPS response too large: ' + target)
    require(digest(body) == expected, 'Local HTTPS content mismatch: ' + target)


def verify_http(bodies, feed_hash):
    for host in HOSTS:
        for route, expected in (('/', digest(bodies['index.html'])),
                                ('/insights.js?v=20261017-manual-reviews', digest(bodies['insights.js'])),
                                ('/reports/index.json', feed_hash)):
            verify_origin_asset(host, route, expected)
        print(host + ': Overview, insights renderer and unchanged reports verified over local HTTPS.')
    print('Public Cloudflare access is not checked here; verify it separately.')


def restore(backup):
    require(backup.resolve() == backup and backup.parent == Path('/home/shini') and
            backup.name.startswith('homeenergy-manual-backup.'), 'Unexpected backup path')
    require(backup.stat().st_uid == 0 and stat.S_IMODE(backup.stat().st_mode) == 0o700,
            'Backup must be private and root-owned')
    record = json.loads(read(backup / 'record.json'))
    for name, path in TARGETS.items():
        meta = record['targets'][name]
        body = read(backup / name)
        require(digest(body) == meta['sha256'], 'Backup bytes differ: ' + name)
        replace_file(path, body, meta['uid'], meta['gid'], meta['mode'])
        os.utime(path, ns=(meta['mtime_ns'], meta['mtime_ns']))
    reload_ssh()
    verify_preserved(record['preserved'])
    print('Original SSH entrypoint and frontend restored. Backups and new unused modules retained; nothing deleted.')


def activate(source):
    bodies, originals, preserved = check(source)
    os.umask(0o077)
    backup = Path(tempfile.mkdtemp(prefix='homeenergy-manual-backup.', dir='/home/shini'))
    record = {'targets': {name: fingerprint(path) for name, path in TARGETS.items()}, 'preserved': preserved}
    for name, body in originals.items():
        (backup / name).write_bytes(body)
    shutil.copyfile(STATE / 'reports.sqlite3', backup / 'reports.sqlite3')
    shutil.copyfile(STATE / 'public/index.json', backup / 'reports-index.json')
    require(digest(read(backup / 'reports.sqlite3')) == preserved[str(STATE / 'reports.sqlite3')]['sha256']
            and digest(read(backup / 'reports-index.json')) == preserved[str(STATE / 'public/index.json')]['sha256'],
            'Report backup verification failed')
    (backup / 'record.json').write_text(json.dumps(record, sort_keys=True, indent=2) + '\n', encoding='utf-8')
    print('Private rollback backup: ' + str(backup), flush=True)
    try:
        for name in NEW_MODULES:
            replace_file(MODULES / name, bodies[name], 0, 0, 0o644)
        # Renderer first, then its HTML version; report storage is never written.
        for name in ('insights.js', 'index.html', 'ssh.conf'):
            meta = record['targets'][name]
            replace_file(TARGETS[name], bodies[name], meta['uid'], meta['gid'], meta['mode'])
        verify_policy()
        reload_ssh()
        verify_preserved(preserved)
        verify_http(bodies, preserved[str(STATE / 'public/index.json')]['sha256'])
        verify_preserved(preserved)
    except Exception:
        restore(backup)
        raise
    print('Manual reporting activated. Existing reports, snapshots and evidence unchanged.')
    print('Only SSH was reloaded. No report/model run, schedule change, Nginx/application/device restart or deletion.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--check', action='store_true')
    group.add_argument('--activate', action='store_true')
    group.add_argument('--rollback', type=Path)
    args = parser.parse_args()
    require(os.geteuid() == 0, 'Run with sudo')
    if args.rollback:
        restore(args.rollback)
    elif args.activate:
        activate(Path(__file__).resolve().parent)
    else:
        check(Path(__file__).resolve().parent)
        print('Read-only checks passed. No report, snapshot, schedule or file was changed.')


if __name__ == '__main__':
    main()
