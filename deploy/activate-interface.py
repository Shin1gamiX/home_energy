"""Installation-specific, backup-first release of the approved Overview/History UI.

Use --check before sudo --activate. Only the listed public files and two exact
Nginx asset routes may change. Reporting, history and control state are read-only.
The private manifest/evidence are staged separately and must never enter Git.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import time

PUBLIC = Path('/var/www/homeenergy/public')
NGINX = Path('/etc/nginx/sites-available/homeenergy.conf')
SOURCE = Path(__file__).resolve().parent
HOSTS = ('home.shin1gamix.com', 'home.shin1gamix.gr')
FILES = ('interface.css', 'assets/solar-home.webp', 'app.js', 'history-cache.js',
         'history.js', 'insights.js', 'index.html', 'history.html')
NEW_FILES = {'interface.css', 'assets/solar-home.webp'}
FEED = Path('/var/lib/homeenergy-reports/public/index.json')
HISTORY = PUBLIC.parent / 'runtime/history'
ROUTE_ANCHOR = '    location = /insights.css { try_files $uri =404; }'
NEW_ROUTES = ('    location = /interface.css { try_files $uri =404; }\n'
              '    location = /assets/solar-home.webp { try_files $uri =404; }')


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def read(path):
    info = path.lstat()
    require(path.resolve() == path and stat.S_ISREG(info.st_mode)
            and info.st_size <= 128 * 1024 * 1024, 'Unexpected file: ' + str(path))
    return path.read_bytes()


def digest(body):
    return hashlib.sha256(body).hexdigest()


def fingerprint(path):
    info = path.stat()
    return dict(sha256=digest(read(path)), uid=info.st_uid, gid=info.st_gid,
                mode=stat.S_IMODE(info.st_mode), mtime_ns=info.st_mtime_ns)


def nginx_candidate(original):
    text = original.decode('utf-8')
    marker = r'|insights\.css):[A-Za-z0-9_-]+$'
    require(text.count(marker) == 1 and text.count(ROUTE_ANCHOR) == 1,
            'Nginx asset anchors differ; inspect before activation')
    require('interface.css' not in text and 'solar-home' not in text,
            'New asset routes already present; inspect before activation')
    text = text.replace(marker, r'|insights\.css|interface\.css|assets/solar-home\.webp):[A-Za-z0-9_-]+$', 1)
    return text.replace(ROUTE_ANCHOR, ROUTE_ANCHOR + '\n' + NEW_ROUTES, 1).encode('utf-8')


def services():
    return subprocess.check_output(['/usr/bin/systemctl', 'show', 'nginx', 'homeenergy',
        'homeenergy-control', '-p', 'Id', '-p', 'MainPID', '-p', 'NRestarts', '-p', 'ActiveState'], text=True)


def protected_state(include_private):
    paths = {p for p in PUBLIC.rglob('*') if p.is_file() and p.relative_to(PUBLIC).as_posix() not in FILES}
    paths.update((PUBLIC.parent / 'server').glob('*.py'))
    paths.add(Path('/opt/homeenergy-control/collector_control.py'))
    for folder in ('/opt/homeenergy-reports', '/opt/homeenergy-reports-staging'):
        paths.update(p for p in Path(folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts)
    for name in ('homeenergy-reporter', 'homeenergy-reporter-staging'):
        paths.add(Path('/etc/ssh/authorized_keys') / name)
    paths.update(Path('/etc/ssh/sshd_config.d').glob('*homeenergy*.conf'))
    paths.add(FEED)
    # Closed daily exports are immutable for this release. Today's exports and
    # the live history database legitimately advance while the exporter runs.
    cutoff = time.strftime('%Y-%m-%d', time.gmtime(time.time() - 86400))
    paths.update(p for p in HISTORY.glob('????-??-??.json') if p.stem < cutoff)
    if include_private:
        for folder in ('/var/lib/homeenergy-reports', '/var/lib/homeenergy-reports-staging'):
            paths.update(p for p in Path(folder).rglob('*') if p.is_file())
    return {str(p): fingerprint(p) for p in sorted(paths)}


def atomic(path, body, meta):
    allowed = {PUBLIC / name for name in FILES} | {NGINX}
    require(path in allowed and path.parent.resolve() == path.parent and not path.is_symlink(),
            'Target outside deployment allowlist')
    fd, pending = tempfile.mkstemp(prefix='.interface-release-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(body)
            output.flush()
            os.fchmod(output.fileno(), meta['mode'])
            os.fchown(output.fileno(), meta['uid'], meta['gid'])
            os.fsync(output.fileno())
        os.replace(pending, path)
    finally:
        if os.path.exists(pending):
            os.unlink(pending)
    require(digest(read(path)) == digest(body), 'Installed bytes differ: ' + str(path))
    info = path.stat()
    require((info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) ==
            (meta['uid'], meta['gid'], meta['mode']), 'Installed metadata differs')


def origin(host, route, etag=None):
    command = ['/usr/bin/curl', '--disable', '--silent', '--show-error', '--http1.1',
        '--noproxy', '*', '--proto', '=https', '--connect-timeout', '5', '--max-time', '15',
        '--max-filesize', '8000000', '--resolve', host + ':443:127.0.0.1', '--include']
    if etag:
        command.extend(['--header', 'If-None-Match: ' + etag])
    result = subprocess.run([*command, 'https://' + host + route], capture_output=True, timeout=20)
    require(result.returncode == 0, 'Origin transport failed: ' + host + route)
    head, separator, body = result.stdout.partition(b'\r\n\r\n')
    require(separator, 'Missing HTTP headers')
    headers = {key.decode().lower(): value.decode().strip() for line in head.split(b'\r\n')[1:]
               if b':' in line for key, value in [line.split(b':', 1)]}
    return int(head.split()[1]), headers, body


def check_headers(headers, immutable=False):
    require(headers.get('x-frame-options') == 'DENY'
            and headers.get('x-content-type-options') == 'nosniff'
            and "script-src 'self'" in headers.get('content-security-policy', '')
            and headers.get('strict-transport-security')
            and headers.get('referrer-policy') == 'no-referrer', 'Security headers differ')
    require(('immutable' if immutable else 'no-store') in headers.get('cache-control', ''),
            'Cache policy differs')


def verify_http(expected, feed_hash):
    routes = {'/': ('index.html', 200), '/history.html': ('history.html', 200)}
    for route in ('/404.html', '/nested/__homeenergy_interface_check__', '/api/collector/status',
                  '/assets/solar-home.provenance.json', '/DESIGN.md', '/.impeccable/design.json'):
        routes[route] = ('404.html', 404)
    for name in ('index.html', 'history.html', '404.html'):
        for asset, version in re.findall(rb'(?:src|href)="/?([a-z0-9/.-]+\.(?:css|js|webp))\?v=([A-Za-z0-9_-]+)"', read(PUBLIC / name)):
            asset, version = asset.decode(), version.decode()
            routes['/' + asset + '?v=' + version] = (asset, 200)
    for host in HOSTS:
        for route, (name, expected_status) in routes.items():
            status, headers, body = origin(host, route)
            require(status == expected_status and digest(body) == expected.get(name, digest(read(PUBLIC / name))),
                    'Origin asset/status mismatch: ' + host + route)
            check_headers(headers, '?v=' in route and name != 'collector-control.js')
            mime = {'.js': 'javascript', '.css': 'text/css', '.webp': 'image/webp', '.html': 'text/html'}[Path(name).suffix]
            require(mime in headers.get('content-type', ''), 'Wrong MIME type: ' + route)
        status, headers, body = origin(host, '/reports/index.json')
        require(status == 200 and digest(body) == feed_hash, 'Public reports changed')
        check_headers(headers)
        status, headers, body = origin(host, '/history/index.json')
        index = json.loads(body)
        require(status == 200 and isinstance(index.get('days'), list) and index.get('energy_summary'), 'History unavailable')
        check_headers(headers)
        cutoff = time.strftime('%Y-%m-%d', time.gmtime(time.time() - 86400))
        days = [day for day in index['days'] if re.fullmatch(r'\d{4}-\d{2}-\d{2}', day) and day < cutoff]
        if days:
            route = '/history/' + max(days) + '.json'
            status, headers, body = origin(host, route)
            require(status == 200 and json.loads(body).get('day') == max(days) and headers.get('etag'), 'Daily history unavailable')
            status, _, body = origin(host, route, headers['etag'])
            require(status == 304 and not body, 'History conditional read failed')
        status, headers, body = origin(host, '/api/energy')
        energy = json.loads(body)
        require(status == 200 and 0 <= time.time() - energy['generated_at'] < 60, 'Exporter snapshot not fresh')
        check_headers(headers)
        print(host + ': assets/MIME/cache, security headers, unchanged reports, private 404 and history/304 verified; telemetry '
              + str(energy.get('status')) + '.', flush=True)


def bundle():
    require(SOURCE.resolve() == SOURCE and SOURCE.parent == Path('/home/shini')
            and SOURCE.name.startswith('homeenergy-interface-release.'), 'Unexpected release directory')
    manifest = json.loads(read(SOURCE / 'interface-manifest.json'))
    require(set(manifest['files']) == set(FILES), 'Unexpected release file allowlist')
    require(digest(read(NGINX)) == manifest['nginx'], 'Nginx baseline changed; inspect first')
    bodies = {name: read(SOURCE / name) for name in FILES}
    for name in FILES:
        target = PUBLIC / name
        require(digest(bodies[name]) == manifest['files'][name]['after'], 'Candidate changed: ' + name)
        if name in NEW_FILES:
            require(manifest['files'][name]['before'] is None and not target.exists() and not target.is_symlink(),
                    'New target already exists: ' + name)
        else:
            require(digest(read(target)) == manifest['files'][name]['before'], 'Baseline changed: ' + name)
    require(not (PUBLIC / 'assets').exists(), 'Assets directory already exists; inspect first')
    nginx_candidate(read(NGINX))
    running = services()
    require(running.count('ActiveState=active') == 3, 'A required service is inactive')
    verify_http({name: meta['before'] for name, meta in manifest['files'].items() if meta['before']}, digest(read(FEED)))
    return manifest, bodies, running


def nginx_reload():
    subprocess.run(['/usr/sbin/nginx', '-t'], check=True)
    subprocess.run(['/usr/bin/systemctl', 'reload', 'nginx'], check=True)


def verify_after_reload(expected, feed_hash):
    # Reload is graceful: a just-accepted connection can briefly use old workers.
    for attempt in range(10):
        try:
            verify_http(expected, feed_hash)
            return
        except RuntimeError:
            if attempt == 9:
                raise
            time.sleep(0.5)


def restore(backup):
    require(backup.resolve() == backup and backup.parent == Path('/home/shini')
            and backup.name.startswith('homeenergy-interface-backup.'), 'Unexpected rollback directory')
    require(backup.stat().st_uid == 0 and stat.S_IMODE(backup.stat().st_mode) == 0o700, 'Backup is not root-private')
    record = json.loads(read(backup / 'record.json'))
    require(set(record['originals']) == set(FILES) - NEW_FILES | {'nginx.conf'}, 'Unexpected rollback allowlist')
    originals = {name: read(backup / name) for name in record['originals']}
    for name, body in originals.items():
        require(digest(body) == record['originals'][name]['sha256'], 'Backup bytes differ')
    for name, body in originals.items():
        path = NGINX if name == 'nginx.conf' else PUBLIC / name
        meta = record['originals'][name]
        atomic(path, body, meta)
        os.utime(path, ns=(meta['mtime_ns'], meta['mtime_ns']))
    nginx_reload()
    verify_after_reload({name: meta['sha256'] for name, meta in record['originals'].items() if name != 'nginx.conf'}, record['feed_hash'])
    print('Previous frontend and Nginx routes restored. Unused new assets, release and private evidence retained; nothing deleted.')


def activate():
    require(os.geteuid() == 0, 'Run activation with sudo')
    manifest, bodies, running = bundle()
    preserved = protected_state(True)
    originals = {name: fingerprint(PUBLIC / name) for name in FILES if name not in NEW_FILES}
    originals['nginx.conf'] = fingerprint(NGINX)
    os.umask(0o077)
    backup = Path(tempfile.mkdtemp(prefix='homeenergy-interface-backup.', dir='/home/shini'))
    for name, meta in originals.items():
        path = NGINX if name == 'nginx.conf' else PUBLIC / name
        body = read(path)
        require(digest(body) == meta['sha256'], 'Source changed during backup')
        (backup / name).write_bytes(body)
        require(digest(read(backup / name)) == meta['sha256'], 'Backup verification failed')
    record = dict(originals=originals, preserved=preserved, services=running, feed_hash=digest(read(FEED)))
    (backup / 'record.json').write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
    print('Private rollback backup: ' + str(backup), flush=True)
    try:
        require(protected_state(True) == preserved and services() == running, 'Protected state changed before activation')
        # Assets first, then route reload, then referencing HTML.
        (PUBLIC / 'assets').mkdir(mode=0o755)
        os.chmod(PUBLIC / 'assets', 0o755)
        for name in FILES:
            if name.endswith('.html'):
                continue
            meta = originals.get(name, dict(uid=0, gid=0, mode=0o644))
            atomic(PUBLIC / name, bodies[name], meta)
        require(fingerprint(NGINX) == originals['nginx.conf'], 'Nginx changed before replacement')
        atomic(NGINX, nginx_candidate(read(NGINX)), originals['nginx.conf'])
        nginx_reload()
        # Confirm new routes before switching the pages.
        for host in HOSTS:
            for name in NEW_FILES:
                for attempt in range(10):
                    status, headers, body = origin(host, '/' + name + '?v=20261018-route-check')
                    if status == 200 and digest(body) == manifest['files'][name]['after']:
                        check_headers(headers, True)
                        break
                    require(attempt < 9, 'New route not ready: ' + name)
                    time.sleep(0.5)
        for name in ('index.html', 'history.html'):
            require(fingerprint(PUBLIC / name) == originals[name], 'HTML changed before replacement')
            atomic(PUBLIC / name, bodies[name], originals[name])
        verify_after_reload({name: meta['after'] for name, meta in manifest['files'].items()}, record['feed_hash'])
        require(protected_state(True) == preserved, 'An unrelated protected file changed')
        require(services() == running, 'Service PID or restart count changed')
    except BaseException:
        restore(backup)
        raise
    print('Approved Overview B, chart-first History and request-limit tuning activated on both HTTPS hostnames.')
    print('Reports/database, closed history exports, reporting/staging access and protected code are unchanged.')
    print('Only Nginx was reloaded. No reporting call, application/device restart or history-data write was requested.')
    print('Verify the public Cloudflare pages separately before pushing the feature commits.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument('--check', action='store_true')
    actions.add_argument('--activate', action='store_true')
    actions.add_argument('--rollback', type=Path)
    args = parser.parse_args()
    if args.rollback:
        require(os.geteuid() == 0, 'Rollback requires sudo')
        restore(args.rollback)
    elif args.activate:
        activate()
    else:
        bundle()
        print('Read-only preflight passed. Frontend, Nginx, reports, history and access are unchanged.')


if __name__ == '__main__':
    main()
