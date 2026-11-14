"""Update only existing Overview seasonal files; no configuration or service changes."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile

SOURCE = Path(__file__).resolve().parent
# Dependencies first; publish the new entrypoint last.
FILES = ('themes/christmas/theme.css', 'themes/schedule.js',
         'themes/theme-loader.js', 'index.html')
VERSION = '20261114-christmas-2'
VERIFIER = SOURCE / 'activate-interface.py'
SPEC = importlib.util.spec_from_file_location('seasonal_update_checks', VERIFIER)
BASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BASE)
BASE.FILES = FILES
require = BASE.require


def write(name, body, metadata):
    require(name in FILES, 'Target outside seasonal update allowlist')
    BASE.atomic(BASE.PUBLIC / name, body, metadata)


def protected():
    result = BASE.protected_state(True)
    for path in (BASE.NGINX, Path('/etc/systemd/system/homeenergy.service')):
        result[str(path)] = BASE.fingerprint(path)
    return result


def verify(expected, feed_hash):
    for host in BASE.HOSTS:
        for name in FILES[:-1]:
            for suffix in ('', '?v=' + VERSION):
                status, headers, body = BASE.origin(host, '/' + name + suffix)
                require(status == 200 and BASE.digest(body) == expected[name],
                        'Seasonal asset mismatch: ' + host + '/' + name)
                BASE.check_headers(headers, bool(suffix))
                mime = 'text/css' if name.endswith('.css') else 'javascript'
                require(mime in headers.get('content-type', ''), 'Seasonal MIME mismatch')
    BASE.verify_http(expected, feed_hash)


def bundle():
    require(SOURCE.resolve() == SOURCE and SOURCE.parent == Path('/home/shini')
            and SOURCE.name.startswith('homeenergy-seasonal-update.'), 'Unexpected release path')
    manifest = json.loads(BASE.read(SOURCE / 'manifest.json'))
    require(set(manifest['files']) == set(FILES), 'Release allowlist differs')
    require(BASE.digest(BASE.read(Path(__file__))) == manifest['helper_sha256'], 'Helper changed')
    require(BASE.digest(BASE.read(VERIFIER)) == manifest['verifier_sha256'], 'Verifier changed')
    require(BASE.digest(BASE.read(BASE.NGINX)) == manifest['nginx_sha256'], 'Nginx baseline changed')
    bodies, originals = {}, {}
    for name in FILES:
        bodies[name] = BASE.read(SOURCE / name)
        require(BASE.digest(bodies[name]) == manifest['files'][name]['after'], 'Candidate changed: ' + name)
        originals[name] = BASE.fingerprint(BASE.PUBLIC / name)
        require(originals[name]['sha256'] == manifest['files'][name]['before'],
                'Installed baseline changed: ' + name)
    running = BASE.services()
    require(running.count('ActiveState=active') == 3, 'Required service inactive')
    feed_hash = BASE.digest(BASE.read(BASE.FEED))
    verify({name: meta['sha256'] for name, meta in originals.items()}, feed_hash)
    return bodies, originals, running, feed_hash


def restore(backup):
    require(backup.resolve() == backup and backup.parent == Path('/home/shini')
            and backup.name.startswith('homeenergy-seasonal-update-backup.'), 'Unexpected backup path')
    require(backup.stat().st_uid == 0 and stat.S_IMODE(backup.stat().st_mode) == 0o700,
            'Backup must be root-private')
    record = json.loads(BASE.read(backup / 'record.json'))
    require(set(record['files']) == set(FILES), 'Backup allowlist differs')
    bodies = {name: BASE.read(backup / name) for name in FILES}
    for name, body in bodies.items():
        require(BASE.digest(body) == record['files'][name]['sha256'], 'Backup bytes differ')
    # Withdraw the entrypoint before restoring its dependencies.
    for name in reversed(FILES):
        write(name, bodies[name], record['files'][name])
        stamp = record['files'][name]['mtime_ns']
        os.utime(BASE.PUBLIC / name, ns=(stamp, stamp))
    verify({name: meta['sha256'] for name, meta in record['files'].items()}, record['feed_hash'])
    print('Previous seasonal files restored. Backup retained; no service reload or restart.')


def activate():
    require(os.geteuid() == 0, 'Activation requires sudo to preserve root-owned theme files')
    bodies, originals, running, feed_hash = bundle()
    preserved = protected()
    os.umask(0o077)
    backup = Path(tempfile.mkdtemp(prefix='homeenergy-seasonal-update-backup.', dir='/home/shini'))
    for name, metadata in originals.items():
        body = BASE.read(BASE.PUBLIC / name)
        require(BASE.digest(body) == metadata['sha256'], 'Source changed during backup')
        target = backup / name
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        target.write_bytes(body)
        require(BASE.digest(BASE.read(target)) == metadata['sha256'], 'Backup verification failed')
    record = dict(files=originals, protected=preserved, services=running, feed_hash=feed_hash)
    (backup / 'record.json').write_text(json.dumps(record, sort_keys=True) + '\n', encoding='utf-8')
    print('Private rollback backup: ' + str(backup), flush=True)
    try:
        require(protected() == preserved and BASE.services() == running, 'Protected state changed before update')
        for name in FILES:
            require(BASE.fingerprint(BASE.PUBLIC / name) == originals[name], 'Target changed before update: ' + name)
            write(name, bodies[name], originals[name])
        verify({name: BASE.digest(body) for name, body in bodies.items()}, feed_hash)
        require(protected() == preserved, 'History, reports or protected files changed')
        require(BASE.services() == running, 'Service state changed')
    except BaseException:
        restore(backup)
        raise
    print('Christmas mobile overlap fix deployed on both HTTPS hostnames.')
    print('December schedule, History, reports/data, reporting access and controls are unchanged.')
    print('No Nginx/SSH reload, service/device restart, report publication or data write.')
    print('Verify public pages before the separate GitHub commit.')


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
        print('Read-only seasonal update preflight passed. Production is unchanged.')


if __name__ == '__main__':
    main()
