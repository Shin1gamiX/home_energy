"""Back up and activate forward-only recording of the two grid destinations."""
import argparse
from contextlib import closing
from datetime import datetime
import importlib.util
import json
import math
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
import time
import sys
from typing import NamedTuple
from zoneinfo import ZoneInfo

SOURCE = Path(__file__).resolve().parent
ARCHIVE = Path('/home/shini/homeenergy_lots')
BACKEND = ('server/energy_summary.py', 'server/history_store.py')
PUBLIC = ('public/app.js', 'public/history.css', 'public/history.js',
          'public/index.html', 'public/history.html')
FILES = BACKEND + PUBLIC


class Release(NamedTuple):
    tag: str
    backend: tuple
    public: tuple

    @property
    def files(self):
        return self.backend + self.public


SPLIT = Release('homeenergy-history-grid-split', BACKEND, PUBLIC)
BRIDGE = Release('homeenergy-grid-bridge',
                 (*BACKEND, 'server/export_energy.py'),
                 ('public/app.js', 'public/history.js', 'public/power-history.js',
                  'public/index.html', 'public/history.html'))
BRANCHES = ('grid_house', 'grid_battery')
INSTALLER = SOURCE / 'activate-battery-voltage.py'
SPEC = importlib.util.spec_from_file_location('history_split_installer', INSTALLER)
INSTALL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INSTALL)
INSTALL.FILES = FILES
BASE = INSTALL.BASE
require = BASE.require


def bundle(release=SPLIT):
    require(SOURCE.parent == ARCHIVE and SOURCE.name.startswith(release.tag + '-release.'),
            'Unexpected release directory')
    manifest = json.loads(BASE.read(SOURCE / 'manifest.json'))
    require(set(manifest['files']) == set(release.files), 'Release allowlist differs')
    for path, key in ((Path(__file__), 'helper_sha256'), (INSTALLER, 'installer_sha256'),
                      (INSTALL.BASE_PATH, 'verifier_sha256'), (BASE.NGINX, 'nginx_sha256'),
                      (INSTALL.UNIT, 'unit_sha256')):
        require(BASE.digest(BASE.read(path)) == manifest[key], 'Baseline changed: ' + path.name)
    bodies, originals = {}, {}
    for name in release.files:
        bodies[name] = BASE.read(SOURCE / name)
        require(BASE.digest(bodies[name]) == manifest['files'][name]['after'], 'Staged bytes changed: ' + name)
        originals[name] = BASE.fingerprint(INSTALL.ROOT / name)
        require(originals[name]['sha256'] == manifest['files'][name]['before'], 'Installed baseline changed: ' + name)
    services = INSTALL.service_state()
    require(all('ActiveState=active' in state for state in services.values()), 'Required service inactive')
    feed_hash = BASE.digest(BASE.read(BASE.FEED))
    BASE.verify_http({}, feed_hash)
    if release == BRIDGE:
        history_db = INSTALL.ROOT / 'runtime/history.sqlite3'
        with closing(sqlite3.connect(history_db.as_uri() + '?mode=ro', uri=True)) as connection:
            prior = connection.execute("SELECT value FROM metadata WHERE key='grid_bridge_from'").fetchone()
        require(prior is None, 'Previously recorded bridge cutover detected; inspect the existing history first')
        check_recorder()
    return bodies, originals, services, feed_hash


def usable_bridge(payload, since, now):
    stamp = payload.get('updated_at')
    if (payload.get('status') not in ('live', 'partial') or not isinstance(stamp, (int, float))
            or not max(since, now - 90) <= stamp <= now + 30):
        return False
    values = payload.get('values', {})
    inputs = ('grid', 'bridge', 'load', 'battery', 'pv', 'grid_house', 'grid_battery')
    if not all(type(values.get(key)) in (float, int) and math.isfinite(values[key]) for key in inputs):
        return False
    grid, bridge, load, battery, pv, house, charging = (values[key] for key in inputs)
    require(grid >= 0 and 0 <= house <= load and charging >= 0 and house + charging <= grid + 1e-6
            and charging <= min(max(0, -bridge), max(0, battery - pv)) + 1e-6,
            'Grid routes exceed the simultaneous source/destination power')
    return True


def check_recorder():
    # Isolated -I/-B interpreter. Candidate reads the actual Recorder in one
    # read-only transaction. No candidate main loop or recording call runs.
    sys.path[:0] = [str(SOURCE / 'server'), str(INSTALL.ROOT / 'server')]
    try:
        import export_energy
    finally:
        del sys.path[:2]
    require(Path(export_energy.__file__).resolve() == SOURCE / 'server/export_energy.py', 'Unexpected exporter')
    with closing(sqlite3.connect(export_energy.DATABASE.as_uri() + '?mode=ro', uri=True, timeout=3)) as db:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        now = time.time()
        snapshot = export_energy.snapshot(db, now)
    require(usable_bridge(snapshot, now - 90, now), 'Awaiting simultaneous fresh signed inverter power and grid readings')
    values = snapshot['values']
    print(f"Read-only Recorder: signed inverter={values['bridge']:g} W, grid={values['grid']:g} W, "
          f"grid to house={values['grid_house']:g} W, grid to battery={values['grid_battery']:g} W.", flush=True)


def local_day(stamp):
    return datetime.fromtimestamp(stamp, ZoneInfo('Europe/Athens')).date().isoformat()


def backup_history(directory):
    database = INSTALL.ROOT / 'runtime/history.sqlite3'
    require(database.resolve() == database and database.is_file(), 'Unexpected history database')
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as source, \
            closing(sqlite3.connect(directory / 'history.sqlite3')) as target:
        source.backup(target, pages=512)
        require(target.execute('PRAGMA quick_check').fetchall() == [('ok',)], 'History backup integrity check failed')
    index = BASE.read(BASE.HISTORY / 'index.json')
    (directory / 'history-index.json').write_bytes(index)
    day = local_day(json.loads(index)['updated_at'])
    data = BASE.read(BASE.HISTORY / (day + '.json'))
    (directory / 'history-day.json').write_bytes(data)
    return day


def check_existing_day(original, current):
    """The existing minute and last supply interval may legitimately advance."""
    require(original['day'] == current['day'], 'History day changed')
    for key in ('points', 'modes'):
        fixed = original[key][:-1]
        require(current[key][:len(fixed)] == fixed, 'Existing recorded ' + key + ' changed')
        if original[key]:
            index = len(fixed)
            anchor = ('t',) if key == 'points' else ('start', 'state')
            require(len(current[key]) > index and all(original[key][index][field] == current[key][index][field]
                    for field in anchor), 'Last pre-deployment ' + key + ' missing')


def recorded_split(day, minute):
    for point in reversed(day['points']):
        if point['t'] == minute:
            values, counts = point['values'], point['counts']
            if all(type(values.get(key)) in (int, float) and math.isfinite(values[key]) and values[key] >= 0
                   and type(counts.get(key)) is int and counts[key] > 0 for key in BRANCHES):
                return {key: (values[key], counts[key]) for key in BRANCHES}
            break
    return None


def verify_recording(since, bridge=False):
    first_minute = (int(since) // 60 + 1) * 60
    deadline = time.monotonic() + 160
    pending = set(BASE.HOSTS)
    while pending and time.monotonic() < deadline:
        for host in sorted(pending):
            status, headers, body = BASE.origin(host, '/history/index.json')
            require(status == 200, 'History index unavailable: ' + host)
            BASE.check_headers(headers)
            index = json.loads(body)
            stamp = index['updated_at']
            minute = int(stamp // 60) * 60
            if minute < first_minute or not -30 <= time.time() - stamp <= 90:
                continue
            day = local_day(stamp)
            status, headers, body = BASE.origin(host, '/history/' + day + '.json')
            require(status == 200 and headers.get('etag') and 'application/json' in headers.get('content-type', ''),
                    'Daily history unavailable: ' + host)
            document = json.loads(body)
            require(document['day'] == day, 'Unexpected daily history')
            split = recorded_split(document, minute)
            totals = index['energy_summary']['totals']
            if split and bridge:
                point = next(point for point in document['points'] if point['t'] == minute)
                status, live_headers, live_body = BASE.origin(host, '/api/energy')
                require(status == 200, 'Live power unavailable: ' + host)
                BASE.check_headers(live_headers)
                live = json.loads(live_body)
                if 'solar_house' not in point['values'] or not usable_bridge(live, since, time.time()):
                    continue
                print(f"{host}: signed inverter power={live['values']['bridge']:g} W.", flush=True)
            if split and all(totals.get(key, {}).get('seconds', 0) > 0 for key in BRANCHES):
                print(f"{host}: recorded grid_house={split['grid_house'][0]:g} W "
                      f"({split['grid_house'][1]} samples), grid_battery={split['grid_battery'][0]:g} W "
                      f"({split['grid_battery'][1]} samples), minute {minute}.", flush=True)
                pending.remove(host)
        if pending:
            time.sleep(3)
    require(not pending, 'New minute with both grid destinations not verified on ' + ', '.join(sorted(pending)))


def restore(directory, release=SPLIT):
    require(directory.resolve() == directory and directory.parent == ARCHIVE
            and directory.name.startswith(release.tag + '-backup.')
            and directory.stat().st_uid == 0 and stat.S_IMODE(directory.stat().st_mode) == 0o700,
            'Invalid backup directory')
    record = json.loads(BASE.read(directory / 'record.json'))
    require(set(record['files']) == set(record['installed']) == set(release.files), 'Backup allowlist differs')
    changed = {}
    for name in release.files:
        old = BASE.read(directory / name)
        require(BASE.digest(old) == record['files'][name]['sha256'], 'Backup changed: ' + name)
        current = BASE.digest(BASE.read(INSTALL.ROOT / name))
        if current != BASE.digest(old):
            require(current == record['installed'][name], 'Unexpected installed bytes: ' + name)
            changed[name] = old
    for name in reversed(changed):
        INSTALL.write(name, changed[name], record['files'][name])
    if changed.keys() & set(release.backend):
        INSTALL.restart_exporter()
    BASE.verify_http({}, BASE.digest(BASE.read(BASE.FEED)))
    print('Previous code restored. All history collected since activation was retained.', flush=True)


def activate(bodies, originals, services, feed_hash, release=SPLIT):
    directory = Path(tempfile.mkdtemp(prefix=release.tag + '-backup.', dir=ARCHIVE))
    print('Private rollback backup: ' + str(directory), flush=True)
    for name in release.files:
        dest = directory / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(BASE.read(INSTALL.ROOT / name))
        require(BASE.digest(BASE.read(dest)) == originals[name]['sha256'], 'Backup differs: ' + name)
    day = backup_history(directory)
    saved = INSTALL.protected()
    for path in BASE.HISTORY.glob('????-??-??.json'):
        if path.stem < day and str(path) not in saved:
            saved[str(path)] = BASE.fingerprint(path)
    hashes = {name: BASE.digest(body) for name, body in bodies.items()}
    (directory / 'record.json').write_text(json.dumps(dict(files=originals, installed=hashes,
                                                       preserved=saved), sort_keys=True), encoding='utf-8')
    require(INSTALL.service_state() == services, 'Service state changed before activation')
    try:
        for name in release.files:
            require(BASE.fingerprint(INSTALL.ROOT / name) == originals[name], 'Target changed: ' + name)
            INSTALL.write(name, bodies[name], originals[name])
            if name == release.backend[-1]:
                since = time.time()
                INSTALL.restart_exporter()
                if release == BRIDGE:
                    verify_recording(since, bridge=True)
                else:
                    verify_recording(since)
        BASE.verify_http({name.removeprefix('public/'): hashes[name] for name in release.public}, feed_hash)
        for path, meta in saved.items():
            require(BASE.fingerprint(Path(path)) == meta, 'Protected file changed: ' + path)
        check_existing_day(json.loads(BASE.read(directory / 'history-day.json')),
                           json.loads(BASE.read(BASE.HISTORY / (day + '.json'))))
        after = INSTALL.service_state()
        require('ActiveState=active' in after['homeenergy'], 'Exporter inactive')
        require(all(after[name] == services[name] for name in ('nginx', 'homeenergy-control')),
                'Unrelated service state changed')
    except BaseException:
        restore(directory, release)
        raise
    print('History grid split active. Closed history, earlier minutes and reports verified. Only the exporter restarted.', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument('--check', action='store_true')
    actions.add_argument('--activate', action='store_true')
    actions.add_argument('--rollback', type=Path)
    parser.add_argument('--bridge', action='store_true', help='Deploy signed inverter power routing')
    args = parser.parse_args()
    release = BRIDGE if args.bridge else SPLIT
    INSTALL.FILES = release.files
    if args.check:
        bundle(release)
        print('Read-only History grid split preflight passed.', flush=True)
    else:
        require(os.geteuid() == 0, 'Administrator privileges required')
        os.umask(0o077)
        if args.activate:
            activate(*bundle(release), release)
        else:
            restore(args.rollback, release)
