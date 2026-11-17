"""Backup-first Custom History release. Reports and history records are read-only."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import time

SOURCE = Path(__file__).resolve().parent
ASSETS = ('history-custom-range.css', 'history-custom-range.js')
EXISTING = ('history-calendar.js', 'history.js', 'history.html')
FILES = (*ASSETS, *EXISTING)
VERIFIER = SOURCE / 'activate-interface.py'
SPEC = importlib.util.spec_from_file_location('custom_range_checks', VERIFIER)
BASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BASE)
BASE.FILES = FILES
require = BASE.require


def nginx_candidate(original):
    text = original.decode('utf-8')
    marker = r'|history-calendar\.js|'
    anchor = '    location = /history-calendar.js { try_files $uri =404; }'
    require(text.count(marker) == 1 and text.count(anchor) == 1
            and 'history-custom-range' not in text, 'Nginx anchors changed; inspect before activation')
    text = text.replace(marker, marker + r'history-custom-range\.css|history-custom-range\.js|', 1)
    routes = '\n'.join('    location = /' + name + ' { try_files $uri =404; }' for name in ASSETS)
    return text.replace(anchor, anchor + '\n' + routes, 1).encode('utf-8')


def write(name, body, metadata):
    require(name in (*FILES, 'nginx.conf'), 'Target outside Custom History allowlist')
    BASE.atomic(BASE.NGINX if name == 'nginx.conf' else BASE.PUBLIC / name, body, metadata)


def protected():
    result = BASE.protected_state(True)
    for path in (Path('/etc/systemd/system/homeenergy.service'),
                 Path('/etc/systemd/system/homeenergy.service.d/30-connection-health.conf')):
        result[str(path)] = BASE.fingerprint(path)
    return result


def verify_assets(expected):
    for host in BASE.HOSTS:
        for name in ASSETS:
            for suffix in ('', '?v=20261117-custom-2'):
                route = '/' + name + suffix
                status, headers, body = BASE.origin(host, route)
                require(status == 200 and BASE.digest(body) == expected[name], 'Custom asset mismatch: ' + host + route)
                BASE.check_headers(headers, bool(suffix))
                mime = 'text/css' if name.endswith('.css') else 'javascript'
                require(mime in headers.get('content-type', ''), 'Custom asset MIME mismatch: ' + route)
        for route in ('/history-custom-range.js.bak', '/docs/HISTORY_CUSTOM_RANGE.md',
                      '/deploy/activate-history-custom-range.py'):
            status, headers, body = BASE.origin(host, route)
            require(status == 404 and body == BASE.read(BASE.PUBLIC / '404.html'), 'Private route exposed: ' + route)
            BASE.check_headers(headers)


def bundle():
    require(SOURCE.resolve() == SOURCE and SOURCE.parent == Path('/home/shini')
            and SOURCE.name.startswith('homeenergy-custom-range-release.'), 'Unexpected release directory')
    manifest = json.loads(BASE.read(SOURCE / 'manifest.json'))
    require(set(manifest['files']) == set(FILES), 'Release allowlist differs')
    require(BASE.digest(BASE.read(Path(__file__))) == manifest['helper_sha256'], 'Helper changed')
    require(BASE.digest(BASE.read(VERIFIER)) == manifest['verifier_sha256'], 'Verifier changed')
    require(BASE.digest(BASE.read(BASE.NGINX)) == manifest['nginx_sha256'], 'Nginx baseline changed')
    bodies = {}
    for name in FILES:
        body = BASE.read(SOURCE / name)
        require(BASE.digest(body) == manifest['files'][name]['after'], 'Candidate changed: ' + name)
        target = BASE.PUBLIC / name
        if name in ASSETS:
            require(manifest['files'][name]['before'] is None and not target.exists() and not target.is_symlink(),
                    'New asset already exists: ' + name)
        else:
            require(BASE.digest(BASE.read(target)) == manifest['files'][name]['before'], 'Installed baseline changed: ' + name)
        bodies[name] = body
    nginx_candidate(BASE.read(BASE.NGINX))
    running = BASE.services()
    require(running.count('ActiveState=active') == 3, 'Required service inactive')
    feed_hash = BASE.digest(BASE.read(BASE.FEED))
    BASE.verify_http({}, feed_hash)
    return bodies, running, feed_hash


def restore(backup):
    require(backup.resolve() == backup and backup.parent == Path('/home/shini')
            and backup.name.startswith('homeenergy-custom-range-backup.'), 'Unexpected backup directory')
    require(backup.stat().st_uid == 0 and stat.S_IMODE(backup.stat().st_mode) == 0o700,
            'Backup must be root-private')
    record = json.loads(BASE.read(backup / 'record.json'))
    require(set(record['originals']) == {*EXISTING, 'nginx.conf'}, 'Backup allowlist differs')
    originals = {name: BASE.read(backup / name) for name in record['originals']}
    for name, body in originals.items():
        require(BASE.digest(body) == record['originals'][name]['sha256'], 'Backup bytes differ')
    # Stop serving the new HTML before removing the asset routes. Retain all evidence.
    for name in ('history.html', 'history.js', 'history-calendar.js', 'nginx.conf'):
        metadata = record['originals'][name]
        write(name, originals[name], metadata)
        path = BASE.NGINX if name == 'nginx.conf' else BASE.PUBLIC / name
        os.utime(path, ns=(metadata['mtime_ns'], metadata['mtime_ns']))
    BASE.nginx_reload()
    BASE.verify_after_reload({name: record['originals'][name]['sha256'] for name in EXISTING}, record['feed_hash'])
    require(protected() == record['protected'], 'Protected files changed during rollback')
    require(BASE.services() == record['services'], 'Service state changed during rollback')
    print('Previous History and Nginx restored. Unused new assets and all backups/evidence retained.')


def activate():
    require(os.geteuid() == 0, 'Activation requires sudo for Nginx routes and private rollback checks')
    bodies, running, feed_hash = bundle()
    preserved = protected()
    originals = {name: BASE.fingerprint(BASE.PUBLIC / name) for name in EXISTING}
    originals['nginx.conf'] = BASE.fingerprint(BASE.NGINX)
    nginx_body = nginx_candidate(BASE.read(BASE.NGINX))
    os.umask(0o077)
    backup = Path(tempfile.mkdtemp(prefix='homeenergy-custom-range-backup.', dir='/home/shini'))
    backup.chmod(0o700)
    for name, metadata in originals.items():
        path = BASE.NGINX if name == 'nginx.conf' else BASE.PUBLIC / name
        body = BASE.read(path)
        require(BASE.digest(body) == metadata['sha256'], 'Original changed before backup')
        (backup / name).write_bytes(body)
        require(BASE.digest(BASE.read(backup / name)) == metadata['sha256'], 'Backup verification failed')
    (backup / 'record.json').write_text(json.dumps(dict(originals=originals, protected=preserved,
        services=running, feed_hash=feed_hash), sort_keys=True) + '\n', encoding='utf-8')
    print('Private rollback backup: ' + str(backup), flush=True)
    try:
        require(protected() == preserved and BASE.services() == running, 'Protected state changed before activation')
        for name in ASSETS:
            target = BASE.PUBLIC / name
            require(not target.exists() and not target.is_symlink(), 'New asset appeared before installation')
            write(name, bodies[name], dict(mode=0o644, uid=0, gid=0))
        require(BASE.fingerprint(BASE.NGINX) == originals['nginx.conf'], 'Nginx changed before installation')
        write('nginx.conf', nginx_body, originals['nginx.conf'])
        BASE.nginx_reload()
        expected = {name: BASE.digest(body) for name, body in bodies.items()}
        for attempt in range(10):
            try:
                verify_assets(expected)
                break
            except RuntimeError:
                if attempt == 9:
                    raise
                time.sleep(.5)
        for name in EXISTING:
            require(BASE.fingerprint(BASE.PUBLIC / name) == originals[name], 'Target changed before installation: ' + name)
            write(name, bodies[name], originals[name])
        BASE.verify_after_reload(expected, feed_hash)
        require(protected() == preserved, 'Reports, history or protected files changed')
        require(BASE.services() == running, 'Application/service PID or restart count changed')
    except BaseException:
        restore(backup)
        raise
    print('Custom date range deployed to History on both HTTPS hostnames.')
    print('Overview, reports/database, closed history, reporting/staging access and controls are unchanged.')
    print('Only Nginx was reloaded. No exporter/device restart, report publication or history-data write.')
    print('Verify the public pages before the separate GitHub commit.')


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
        print('Read-only Custom History preflight passed. No files or service state changed.')


if __name__ == '__main__':
    main()
