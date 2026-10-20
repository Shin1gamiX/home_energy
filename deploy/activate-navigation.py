"""Backup-first navigation-only release. No configuration or service changes."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile

SOURCE = Path(__file__).resolve().parent
FILES = ('interface.css', 'index.html', 'history.html')
VERIFIER = SOURCE / 'activate-interface.py'
SPEC = importlib.util.spec_from_file_location('navigation_checks', VERIFIER)
BASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BASE)
BASE.FILES = FILES
require = BASE.require


def write(name, body, metadata):
    require(name in FILES, 'Target outside navigation allowlist')
    BASE.atomic(BASE.PUBLIC / name, body, metadata)


def protected():
    result = BASE.protected_state(True)
    for path in (BASE.NGINX, Path('/etc/systemd/system/homeenergy.service')):
        result[str(path)] = BASE.fingerprint(path)
    return result


def bundle():
    require(SOURCE.resolve() == SOURCE and SOURCE.parent == Path('/home/shini')
            and SOURCE.name.startswith('homeenergy-navigation-release.'), 'Unexpected release path')
    manifest = json.loads(BASE.read(SOURCE / 'manifest.json'))
    require(set(manifest['files']) == set(FILES), 'Release allowlist differs')
    require(BASE.digest(BASE.read(Path(__file__))) == manifest['helper_sha256'], 'Helper changed')
    require(BASE.digest(BASE.read(VERIFIER)) == manifest['verifier_sha256'], 'Verifier changed')
    require(BASE.digest(BASE.read(BASE.NGINX)) == manifest['nginx_sha256'], 'Nginx baseline changed')
    bodies = {}
    originals = {}
    for name in FILES:
        body = BASE.read(SOURCE / name)
        require(BASE.digest(body) == manifest['files'][name]['after'], 'Candidate changed: ' + name)
        originals[name] = BASE.fingerprint(BASE.PUBLIC / name)
        require(originals[name]['sha256'] == manifest['files'][name]['before'],
                'Installed baseline changed: ' + name)
        bodies[name] = body
    running = BASE.services()
    require(running.count('ActiveState=active') == 3, 'Required service inactive')
    feed_hash = BASE.digest(BASE.read(BASE.FEED))
    BASE.verify_http({}, feed_hash)
    return bodies, originals, running, feed_hash


def restore(backup):
    require(backup.resolve() == backup and backup.parent == Path('/home/shini')
            and backup.name.startswith('homeenergy-navigation-backup.'), 'Unexpected backup path')
    require(backup.stat().st_uid == 0 and stat.S_IMODE(backup.stat().st_mode) == 0o700,
            'Backup must be root-private')
    record = json.loads(BASE.read(backup / 'record.json'))
    require(set(record['files']) == set(FILES), 'Backup allowlist differs')
    bodies = {name: BASE.read(backup / name) for name in FILES}
    for name, body in bodies.items():
        require(BASE.digest(body) == record['files'][name]['sha256'], 'Backup bytes differ')
    for name, body in bodies.items():
        write(name, body, record['files'][name])
        stamp = record['files'][name]['mtime_ns']
        os.utime(BASE.PUBLIC / name, ns=(stamp, stamp))
    BASE.verify_http({name: meta['sha256'] for name, meta in record['files'].items()},
                     record['feed_hash'])
    print('Previous navigation restored. Backup and release retained; no service reload or restart.')


def activate():
    require(os.geteuid() == 0, 'Activation requires sudo to preserve file ownership')
    bodies, originals, running, feed_hash = bundle()
    preserved = protected()
    os.umask(0o077)
    backup = Path(tempfile.mkdtemp(prefix='homeenergy-navigation-backup.', dir='/home/shini'))
    backup.chmod(0o700)
    for name, metadata in originals.items():
        body = BASE.read(BASE.PUBLIC / name)
        require(BASE.digest(body) == metadata['sha256'], 'Source changed during backup')
        (backup / name).write_bytes(body)
        require(BASE.digest(BASE.read(backup / name)) == metadata['sha256'], 'Backup verification failed')
    record = dict(files=originals, protected=preserved, services=running, feed_hash=feed_hash)
    (backup / 'record.json').write_text(json.dumps(record, sort_keys=True) + '\n', encoding='utf-8')
    print('Private rollback backup: ' + str(backup), flush=True)
    try:
        require(protected() == preserved and BASE.services() == running,
                'Protected state changed before activation')
        # The shared stylesheet is replaced before the HTML that references it.
        for name, body in bodies.items():
            require(BASE.fingerprint(BASE.PUBLIC / name) == originals[name],
                    'Target changed before installation: ' + name)
            write(name, body, originals[name])
        BASE.verify_http({name: BASE.digest(body) for name, body in bodies.items()}, feed_hash)
        require(protected() == preserved, 'Protected files changed')
        require(BASE.services() == running, 'Service state changed')
    except BaseException:
        restore(backup)
        raise
    print('Overview/History navigation deployed on both HTTPS hostnames.', flush=True)
    print('Reports, history, reporting/staging access and controls are unchanged.', flush=True)
    print('No Nginx/SSH reload, service/device restart or report publication.', flush=True)
    print('Metric icons and battery-status changes are not included. Check public pages before GitHub push.',
          flush=True)


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
        print('Read-only navigation preflight passed. No files or service state changed.')


if __name__ == '__main__':
    main()
