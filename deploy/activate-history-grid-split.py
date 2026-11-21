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
from zoneinfo import ZoneInfo

SOURCE = Path(__file__).resolve().parent
ARCHIVE = Path('/home/shini/homeenergy_lots')
BACKEND = ('server/energy_summary.py', 'server/history_store.py')
PUBLIC = ('public/app.js', 'public/history.css', 'public/history.js',
          'public/index.html', 'public/history.html')
FILES = BACKEND + PUBLIC
BRANCHES = ('grid_house', 'grid_battery')
INSTALLER = SOURCE / 'activate-battery-voltage.py'
SPEC = importlib.util.spec_from_file_location('history_split_installer', INSTALLER)
INSTALL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INSTALL)
INSTALL.FILES = FILES
BASE = INSTALL.BASE
require = BASE.require


def bundle():
    require(SOURCE.parent == ARCHIVE and SOURCE.name.startswith('homeenergy-history-grid-split-release.'),
            'Unexpected release directory')
    manifest = json.loads(BASE.read(SOURCE / 'manifest.json'))
    require(set(manifest['files']) == set(FILES), 'Release allowlist differs')
    for path, key in ((Path(__file__), 'helper_sha256'), (INSTALLER, 'installer_sha256'),
                      (INSTALL.BASE_PATH, 'verifier_sha256'), (BASE.NGINX, 'nginx_sha256'),
                      (INSTALL.UNIT, 'unit_sha256')):
        require(BASE.digest(BASE.read(path)) == manifest[key], 'Baseline changed: ' + path.name)
    bodies, originals = {}, {}
    for name in FILES:
        bodies[name] = BASE.read(SOURCE / name)
        require(BASE.digest(bodies[name]) == manifest['files'][name]['after'], 'Staged bytes changed: ' + name)
        originals[name] = BASE.fingerprint(INSTALL.ROOT / name)
        require(originals[name]['sha256'] == manifest['files'][name]['before'], 'Installed baseline changed: ' + name)
    services = INSTALL.service_state()
    require(all('ActiveState=active' in state for state in services.values()), 'Required service inactive')
    feed_hash = BASE.digest(BASE.read(BASE.FEED))
    BASE.verify_http({}, feed_hash)
    return bodies, originals, services, feed_hash


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


def verify_recording(since):
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
            if split and all(totals.get(key, {}).get('seconds', 0) > 0 for key in BRANCHES):
                print(f"{host}: recorded grid_house={split['grid_house'][0]:g} W "
                      f"({split['grid_house'][1]} samples), grid_battery={split['grid_battery'][0]:g} W "
                      f"({split['grid_battery'][1]} samples), minute {minute}.", flush=True)
                pending.remove(host)
        if pending:
            time.sleep(3)
    require(not pending, 'New minute with both grid destinations not verified on ' + ', '.join(sorted(pending)))


def restore(directory):
    require(directory.resolve() == directory and directory.parent == ARCHIVE
            and directory.name.startswith('homeenergy-history-grid-split-backup.')
            and directory.stat().st_uid == 0 and stat.S_IMODE(directory.stat().st_mode) == 0o700,
            'Invalid backup directory')
    record = json.loads(BASE.read(directory / 'record.json'))
    require(set(record['files']) == set(record['installed']) == set(FILES), 'Backup allowlist differs')
    changed = {}
    for name in FILES:
        old = BASE.read(directory / name)
        require(BASE.digest(old) == record['files'][name]['sha256'], 'Backup changed: ' + name)
        current = BASE.digest(BASE.read(INSTALL.ROOT / name))
        if current != BASE.digest(old):
            require(current == record['installed'][name], 'Unexpected installed bytes: ' + name)
            changed[name] = old
    for name in reversed(changed):
        INSTALL.write(name, changed[name], record['files'][name])
    if changed.keys() & set(BACKEND):
        INSTALL.restart_exporter()
    BASE.verify_http({}, BASE.digest(BASE.read(BASE.FEED)))
    print('Previous code restored. All history collected since activation was retained.', flush=True)


def activate(bodies, originals, services, feed_hash):
    directory = Path(tempfile.mkdtemp(prefix='homeenergy-history-grid-split-backup.', dir=ARCHIVE))
    print('Private rollback backup: ' + str(directory), flush=True)
    for name in FILES:
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
        for name in FILES:
            require(BASE.fingerprint(INSTALL.ROOT / name) == originals[name], 'Target changed: ' + name)
            INSTALL.write(name, bodies[name], originals[name])
            if name == BACKEND[-1]:
                since = time.time()
                INSTALL.restart_exporter()
                verify_recording(since)
        BASE.verify_http({name.removeprefix('public/'): hashes[name] for name in PUBLIC}, feed_hash)
        for path, meta in saved.items():
            require(BASE.fingerprint(Path(path)) == meta, 'Protected file changed: ' + path)
        check_existing_day(json.loads(BASE.read(directory / 'history-day.json')),
                           json.loads(BASE.read(BASE.HISTORY / (day + '.json'))))
        after = INSTALL.service_state()
        require('ActiveState=active' in after['homeenergy'], 'Exporter inactive')
        require(all(after[name] == services[name] for name in ('nginx', 'homeenergy-control')),
                'Unrelated service state changed')
    except BaseException:
        restore(directory)
        raise
    print('History grid split active. Closed history, earlier minutes and reports verified. Only the exporter restarted.', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument('--check', action='store_true')
    actions.add_argument('--activate', action='store_true')
    actions.add_argument('--rollback', type=Path)
    args = parser.parse_args()
    if args.check:
        bundle()
        print('Read-only History grid split preflight passed.', flush=True)
    else:
        require(os.geteuid() == 0, 'Administrator privileges required')
        os.umask(0o077)
        if args.activate:
            activate(*bundle())
        else:
            restore(args.rollback)
