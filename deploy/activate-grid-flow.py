"""Deploy live grid destination readings and Overview animations, with code-only rollback."""
import argparse
import importlib.util
import json
import math
import os
from pathlib import Path
import stat
import tempfile
import time

SOURCE = Path(__file__).resolve().parent
ARCHIVE = Path('/home/shini/homeenergy_lots')
EXPORTER = 'server/export_energy.py'
FILES = (EXPORTER, 'public/app.js', 'public/index.html')
INSTALLER = SOURCE / 'activate-battery-voltage.py'
SPEC = importlib.util.spec_from_file_location('grid_flow_install', INSTALLER)
INSTALL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INSTALL)
INSTALL.FILES = FILES  # The shared atomic writer and protected-state checks use this allowlist.
BASE = INSTALL.BASE
ROOT = INSTALL.ROOT
require = BASE.require


def bundle():
    require(SOURCE.parent == ARCHIVE and SOURCE.name.startswith('homeenergy-grid-flow-release.'),
            'Unexpected release directory')
    manifest = json.loads(BASE.read(SOURCE / 'manifest.json'))
    require(set(manifest['files']) == set(FILES), 'Release allowlist differs')
    for path, key in ((Path(__file__), 'helper_sha256'), (INSTALLER, 'installer_sha256'),
                      (INSTALL.BASE_PATH, 'verifier_sha256'), (BASE.NGINX, 'nginx_sha256')):
        require(BASE.digest(BASE.read(path)) == manifest[key], 'Baseline changed: ' + path.name)
    bodies, originals = {}, {}
    for name in FILES:
        body = BASE.read(SOURCE / name)
        require(BASE.digest(body) == manifest['files'][name]['after'], 'Candidate changed: ' + name)
        originals[name] = BASE.fingerprint(ROOT / name)
        require(originals[name]['sha256'] == manifest['files'][name]['before'],
                'Installed baseline changed: ' + name)
        if name == EXPORTER:
            body = INSTALL.exporter_for_installation(body, BASE.read(ROOT / name))
        bodies[name] = body
    running = INSTALL.service_state()
    require(all('ActiveState=active' in state for state in running.values()), 'Required service inactive')
    feed_hash = BASE.digest(BASE.read(BASE.FEED))
    BASE.verify_http({}, feed_hash)
    return bodies, originals, running, feed_hash


def fresh_split(energy, since, now):
    def number(value):
        return type(value) in (int, float) and math.isfinite(value)

    stamp = energy.get('updated_at')
    generated = energy.get('generated_at')
    values = energy.get('values', {})
    house, charging, grid = (values.get(key) for key in ('grid_house', 'grid_battery', 'grid'))
    return (energy.get('status') in ('live', 'partial')
            and number(stamp) and since <= stamp <= now + 30 and now - stamp <= 90
            and number(generated) and since <= generated <= now + 30 and now - generated <= 60
            and all(number(value) and value >= 0 for value in (house, charging, grid))
            and math.isclose(house + charging, grid))


def verify_split(since):
    pending = set(BASE.HOSTS)
    for attempt in range(45):
        for host in sorted(pending):
            status, headers, body = BASE.origin(host, '/api/energy')
            require(status == 200, 'Energy endpoint unavailable')
            BASE.check_headers(headers)
            energy = json.loads(body)
            if fresh_split(energy, since, time.time()):
                # The recording index advances only after the normal minute recording succeeds.
                status, _, body = BASE.origin(host, '/history/index.json')
                if status == 200 and (json.loads(body).get('updated_at') or 0) >= energy['updated_at']:
                    values = energy['values']
                    print(f"{host}: grid to house {values['grid_house']} W, grid to battery "
                          f"{values['grid_battery']} W. Normal recording verified.", flush=True)
                    pending.remove(host)
        if not pending:
            return
        if attempt < 44:
            time.sleep(2)
    raise RuntimeError('Fresh grid destinations and normal recording not confirmed')


def restore(backup):
    require(backup.resolve() == backup and backup.parent == ARCHIVE
            and backup.name.startswith('homeenergy-grid-flow-backup.') and backup.stat().st_uid == 0
            and stat.S_IMODE(backup.stat().st_mode) == 0o700, 'Invalid private backup directory')
    record = json.loads(BASE.read(backup / 'record.json'))
    require(set(record['files']) == set(record['installed']) == set(FILES), 'Backup allowlist differs')
    bodies = {name: BASE.read(backup / name) for name in FILES}
    changed = []
    for name, body in bodies.items():
        original_hash = record['files'][name]['sha256']
        require(BASE.digest(body) == original_hash, 'Backup bytes differ: ' + name)
        current = BASE.digest(BASE.read(ROOT / name))
        require(current in (original_hash, record['installed'][name]), 'Target changed since release: ' + name)
        if current != original_hash:
            changed.append(name)
    for name in reversed(changed):
        metadata = record['files'][name]
        INSTALL.write(name, bodies[name], metadata)
        os.utime(ROOT / name, ns=(metadata['mtime_ns'], metadata['mtime_ns']))
    if EXPORTER in changed:
        INSTALL.restart_exporter()
    BASE.verify_http({}, BASE.digest(BASE.read(BASE.FEED)))
    print('Previous code verified. Backup retained.', flush=True)


def activate(bodies, originals, running, feed_hash):
    preserved = INSTALL.protected()
    backup = Path(tempfile.mkdtemp(prefix='homeenergy-grid-flow-backup.', dir=ARCHIVE))
    for name, meta in originals.items():
        body = BASE.read(ROOT / name)
        require(BASE.digest(body) == meta['sha256'], 'Source changed before backup: ' + name)
        dest = backup / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(body)
        require(BASE.digest(BASE.read(dest)) == meta['sha256'], 'Backup failed: ' + name)
    hashes = {name: BASE.digest(body) for name, body in bodies.items()}
    (backup / 'record.json').write_text(json.dumps(dict(files=originals, installed=hashes)), encoding='utf-8')
    print('Private rollback backup: ' + str(backup), flush=True)
    require(INSTALL.service_state() == running, 'Service state changed before activation')
    try:
        for name in FILES:
            require(BASE.fingerprint(ROOT / name) == originals[name], 'Target changed before write: ' + name)
            INSTALL.write(name, bodies[name], originals[name])
            if name == EXPORTER:
                since = time.time()
                INSTALL.restart_exporter()
                verify_split(since)
        expected = {name.removeprefix('public/'): hashes[name] for name in FILES if name.startswith('public/')}
        BASE.verify_http(expected, feed_hash)
        require(INSTALL.protected() == preserved, 'Protected files changed')
        after = INSTALL.service_state()
        require('ActiveState=active' in after['homeenergy'], 'Exporter inactive')
        for name in ('nginx', 'homeenergy-control'):
            require(after[name] == running[name], 'Unrelated service changed: ' + name)
    except BaseException:
        restore(backup)
        raise
    print('Grid destinations deployed on both HTTPS hostnames. Only the exporter restarted.', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument('--check', action='store_true')
    actions.add_argument('--activate', action='store_true')
    actions.add_argument('--rollback', type=Path)
    args = parser.parse_args()
    if args.check:
        bundle()
        print('Read-only release checks passed.', flush=True)
    else:
        require(os.geteuid() == 0, 'Administrator privileges required')
        os.umask(0o077)
        if args.activate:
            activate(*bundle())
        else:
            restore(args.rollback)
