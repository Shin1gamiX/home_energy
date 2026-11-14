"""Backup-first Overview seasonal release; History, reports and data are read-only."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import time

SOURCE = Path(__file__).resolve().parent
ASSETS = ('themes/theme-loader.js', 'themes/schedule.js',
          'themes/christmas/theme.css', 'themes/christmas/theme.js')
FILES = (*ASSETS, 'index.html')
VERSION = '20261114-christmas-1'
VERIFIER = SOURCE / 'activate-interface.py'
SPEC = importlib.util.spec_from_file_location('seasonal_checks', VERIFIER)
BASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BASE)
BASE.FILES = FILES
require = BASE.require


def nginx_candidate(original):
    text = original.decode('utf-8')
    marker = r'|assets/solar-home\.webp):[A-Za-z0-9_-]+$'
    anchor = '    location = /assets/solar-home.webp { try_files $uri =404; }'
    require(text.count(marker) == 1 and text.count(anchor) == 1,
            'Nginx asset anchors changed; inspect before activation')
    require('/themes/' not in text, 'Seasonal routes already exist; inspect before activation')
    patterns = '|'.join(name.replace('.', r'\.') for name in ASSETS)
    text = text.replace(marker, r'|assets/solar-home\.webp|' + patterns + r'):[A-Za-z0-9_-]+$', 1)
    routes = '\n'.join('    location = /' + name + ' { try_files $uri =404; }' for name in ASSETS)
    return text.replace(anchor, anchor + '\n' + routes, 1).encode('utf-8')


def write(name, body, metadata):
    require(name in FILES or name == 'nginx.conf', 'Target outside seasonal release allowlist')
    BASE.atomic(BASE.NGINX if name == 'nginx.conf' else BASE.PUBLIC / name, body, metadata)


def protected():
    result = BASE.protected_state(True)
    result['exporter-service'] = BASE.fingerprint(Path('/etc/systemd/system/homeenergy.service'))
    return result


def verify_assets(expected):
    for host in BASE.HOSTS:
        for name in ASSETS:
            for suffix in ('', '?v=' + VERSION):
                route = '/' + name + suffix
                status, headers, body = BASE.origin(host, route)
                require(status == 200 and BASE.digest(body) == expected[name], 'Seasonal asset mismatch: ' + host + route)
                BASE.check_headers(headers, bool(suffix))
                mime = 'text/css' if name.endswith('.css') else 'javascript'
                require(mime in headers.get('content-type', ''), 'Seasonal MIME mismatch: ' + route)
        for route in ('/themes/', '/themes/private.json', '/themes/schedule.js.bak'):
            status, headers, body = BASE.origin(host, route)
            require(status == 404 and body == BASE.read(BASE.PUBLIC / '404.html'), 'Unlisted seasonal route exposed')
            BASE.check_headers(headers)


def bundle():
    require(SOURCE.resolve() == SOURCE and SOURCE.parent == Path('/home/shini')
            and SOURCE.name.startswith('homeenergy-seasonal-release.'), 'Unexpected release path')
    manifest = json.loads(BASE.read(SOURCE / 'manifest.json'))
    require(set(manifest['files']) == set(FILES), 'Release allowlist differs')
    require(BASE.digest(BASE.read(Path(__file__))) == manifest['helper_sha256'], 'Helper changed')
    require(BASE.digest(BASE.read(VERIFIER)) == manifest['verifier_sha256'], 'Verifier changed')
    require(BASE.digest(BASE.read(BASE.NGINX)) == manifest['nginx_sha256'], 'Nginx baseline changed')
    require(not (BASE.PUBLIC / 'themes').exists() and not (BASE.PUBLIC / 'themes').is_symlink(),
            'Theme directory already exists; inspect before activation')
    bodies = {}
    for name in FILES:
        body = BASE.read(SOURCE / name)
        require(BASE.digest(body) == manifest['files'][name]['after'], 'Candidate changed: ' + name)
        target = BASE.PUBLIC / name
        if name in ASSETS:
            require(manifest['files'][name]['before'] is None and not target.exists(), 'New theme asset already exists')
        else:
            require(BASE.digest(BASE.read(target)) == manifest['files'][name]['before'], 'Installed baseline changed: ' + name)
        bodies[name] = body
    nginx_candidate(BASE.read(BASE.NGINX))
    running = BASE.services()
    require(running.count('ActiveState=active') == 3, 'Required service inactive')
    feed_hash = BASE.digest(BASE.read(BASE.FEED))
    BASE.verify_http({}, feed_hash)
    return manifest, bodies, running, feed_hash


def restore(backup):
    require(backup.resolve() == backup and backup.parent == Path('/home/shini')
            and backup.name.startswith('homeenergy-seasonal-backup.'), 'Unexpected backup path')
    require(backup.stat().st_uid == 0 and stat.S_IMODE(backup.stat().st_mode) == 0o700,
            'Backup must be root-private')
    record = json.loads(BASE.read(backup / 'record.json'))
    require(set(record['originals']) == {'index.html', 'nginx.conf'}, 'Backup allowlist differs')
    originals = {name: BASE.read(backup / name) for name in record['originals']}
    for name, body in originals.items():
        require(BASE.digest(body) == record['originals'][name]['sha256'], 'Backup bytes differ')
    for name in ('index.html', 'nginx.conf'):
        write(name, originals[name], record['originals'][name])
        path = BASE.NGINX if name == 'nginx.conf' else BASE.PUBLIC / name
        stamp = record['originals'][name]['mtime_ns']
        os.utime(path, ns=(stamp, stamp))
    BASE.nginx_reload()
    BASE.verify_after_reload({'index.html': record['originals']['index.html']['sha256']}, record['feed_hash'])
    print('Original Overview and Nginx restored. Unused theme assets and all evidence retained; nothing deleted.')


def activate():
    require(os.geteuid() == 0, 'Activation requires sudo for Nginx routes and private rollback checks')
    manifest, bodies, running, feed_hash = bundle()
    preserved = protected()
    originals = {'index.html': BASE.fingerprint(BASE.PUBLIC / 'index.html'),
                 'nginx.conf': BASE.fingerprint(BASE.NGINX)}
    nginx_body = nginx_candidate(BASE.read(BASE.NGINX))
    os.umask(0o077)
    backup = Path(tempfile.mkdtemp(prefix='homeenergy-seasonal-backup.', dir='/home/shini'))
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
        for folder in ('themes', 'themes/christmas'):
            path = BASE.PUBLIC / folder
            require(path.parent.resolve() == path.parent, 'Unexpected theme parent')
            path.mkdir(mode=0o755)
            path.chmod(0o755)
        for name in ASSETS:
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
        require(BASE.fingerprint(BASE.PUBLIC / 'index.html') == originals['index.html'], 'Overview changed before installation')
        write('index.html', bodies['index.html'], originals['index.html'])
        BASE.verify_after_reload(expected, feed_hash)
        require(protected() == preserved, 'History, reports or protected files changed')
        require(BASE.services() == running, 'Application/service PID or restart count changed')
    except BaseException:
        restore(backup)
        raise
    print('Overview seasonal themes deployed on both HTTPS hostnames.')
    print('Christmas is scheduled annually for 1 December to 1 January, end exclusive, Europe/Athens.')
    print('History, reports/database, closed history exports, reporting/staging access and controls are unchanged.')
    print('Only Nginx was reloaded. No application/device restart, report publication or data write.')
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
        print('Read-only seasonal preflight passed. Production has not been changed.')


if __name__ == '__main__':
    main()
