"""Activate the charging-source estimates against an existing flow-observations installation."""
import argparse
from contextlib import closing
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import tempfile
import time


SOURCE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('flow_release', SOURCE / 'activate-flow-observations.py')
HA = importlib.util.module_from_spec(spec)
spec.loader.exec_module(HA)
GRID, BASE, INSTALL, require = HA.GRID, HA.BASE, HA.INSTALL, HA.require
TAG = 'homeenergy-charging-status'
TEMPLATE = HA.TEMPLATE
BACKEND = ('server/history_store.py', HA.EXPORT)
PUBLIC = tuple('public/' + name for name in ('app.js', 'history.js', 'connection-status.css',
                                            'history.css', 'index.html', 'history.html'))
FILES = (TEMPLATE, *BACKEND, *PUBLIC)
SCRIPTS = ('activate-charging-status.py', *HA.SCRIPTS)
CHARGING = {HA.MAIN_PREFIX + 'power_flow_status': None, HA.MAIN_PREFIX + 'inverter_charging_power': 'W'}


def target(name):
    require(name in FILES, 'Target outside charging release')
    return HA.OBSERVATIONS if name == TEMPLATE else INSTALL.ROOT / name


def staged():
    require(SOURCE.parent == GRID.ARCHIVE and SOURCE.name.startswith(TAG + '-release.'), 'Unexpected release path')
    manifest = json.loads(BASE.read(SOURCE / 'manifest.json'))
    require(set(manifest['staged']) == set((*FILES, *SCRIPTS)) and set(manifest['before']) == set(FILES),
            'Release allowlist differs')
    bodies = {}
    for name, expected in manifest['staged'].items():
        body = BASE.read(SOURCE / name)
        require(BASE.digest(body) == expected, 'Staged file changed: ' + name)
        if name in FILES:
            bodies[name] = body
    return bodies, manifest['before']


def preflight():
    bodies, expected = staged()
    originals = {name: BASE.fingerprint(target(name)) for name in FILES}
    for name, meta in originals.items():
        require(meta['sha256'] == expected[name], 'Installed baseline changed: ' + name)
    bodies[HA.EXPORT] = INSTALL.exporter_for_installation(bodies[HA.EXPORT], BASE.read(target(HA.EXPORT)))
    require(re.search(rb'(?m)^template:[ \t]*!include[ \t]+flow_observations\.yaml[ \t]*$', BASE.read(HA.CONFIG)),
            'Existing template include differs')
    require(all('ActiveState=active' in state for state in INSTALL.service_state().values()), 'Required service inactive')
    cid = HA.container()
    BASE.verify_http({}, BASE.digest(BASE.read(BASE.FEED)))
    return bodies, originals, cid


def backup(bodies, originals, cid):
    directory = Path(tempfile.mkdtemp(prefix=TAG + '-backup.', dir=GRID.ARCHIVE))
    print('Private rollback backup: ' + str(directory), flush=True)
    for name in FILES:
        copy = directory / name
        copy.parent.mkdir(exist_ok=True, parents=True)
        copy.write_bytes(BASE.read(target(name)))
        require(BASE.digest(BASE.read(copy)) == originals[name]['sha256'], 'Inconsistent backup: ' + name)
    day = GRID.backup_history(directory)
    INSTALL.FILES = BACKEND + PUBLIC
    saved = INSTALL.protected()
    saved[str(HA.CONFIG)] = BASE.fingerprint(HA.CONFIG)
    for path in BASE.HISTORY.glob('????-??-??.json'):
        if path.stem < day and str(path) not in saved:
            saved[str(path)] = BASE.fingerprint(path)
    record = dict(before=originals, after={n: BASE.digest(b) for n, b in bodies.items()},
                  services=INSTALL.service_state(), cid=cid, day=day, saved=saved, release=str(SOURCE))
    (directory / 'record.json').write_text(json.dumps(record), encoding='utf-8')
    return directory, record


def write(name, expected, body, meta):
    path = target(name)
    require(BASE.digest(BASE.read(path)) == expected, 'Target changed: ' + name)
    if name == TEMPLATE:
        HA.replace_checked(path, expected, body, meta)
    else:
        INSTALL.FILES = BACKEND + PUBLIC
        INSTALL.write(name, body, meta)


def valid_charging_observation(db, entity, observation, sampled, since, now):
    if not isinstance(observation, list) or len(observation) != 3:
        return False
    state, updated, reported = observation
    if (not isinstance(state, str) or type(updated) not in (int, float) or type(reported) not in (int, float)
            or not (0 < updated <= reported <= sampled + 0.000001 and since <= reported and now - reported <= 90)):
        return False
    try:
        watts_or_status = float(state)
    except ValueError:
        return False
    unit = CHARGING[entity]
    if (not math.isfinite(watts_or_status) or watts_or_status < 0
            or (unit is None and (not watts_or_status.is_integer() or watts_or_status > 0xffff))):
        return False
    row = db.execute("""SELECT a.shared_attrs FROM states s LEFT JOIN state_attributes a
        ON a.attributes_id=s.attributes_id WHERE s.metadata_id=(SELECT metadata_id FROM states_meta
        WHERE entity_id=?) AND s.last_updated_ts BETWEEN ? AND ? AND s.state=? LIMIT 1""",
                     (entity, updated - .000001, updated + .000001, state)).fetchone()
    if not row:
        return False
    attrs = json.loads(row[0] or '{}')
    return isinstance(attrs, dict) and not attrs.get('restored') and attrs.get('unit_of_measurement') == unit


def checked_reports(db, since, now):
    original = HA.checked_reports(db, since, now)
    if not original:
        return {}
    recent = db.execute("""SELECT s.state,s.last_updated_ts,a.shared_attrs FROM states s JOIN state_attributes a
        ON a.attributes_id=s.attributes_id WHERE s.metadata_id=(SELECT metadata_id FROM states_meta WHERE entity_id=?)
        AND s.last_updated_ts BETWEEN ? AND ? ORDER BY s.last_updated_ts DESC LIMIT 4""",
                        (HA.ENTITY, max(since, now - 90), now + 30))
    latest = None
    for state, sampled, raw in recent:
        attrs = json.loads(raw)
        charging = attrs.get('charging_reports')
        if (state != '1' or attrs.get('restored') or not isinstance(charging, dict) or set(charging) != set(CHARGING)
                or not all(valid_charging_observation(db, e, charging[e], sampled, since, now) for e in CHARGING)):
            continue
        times = {e: obs[2] for e, obs in charging.items()}
        if latest is None:
            latest = times
        elif all(latest[e] > times[e] for e in CHARGING):
            return {**original, **latest}
    return {}


def wait_for_reports(since):
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        try:
            with closing(sqlite3.connect(HA.DATABASE.as_uri() + '?mode=ro', uri=True, timeout=4)) as db:
                db.execute('PRAGMA query_only=ON')
                db.execute('BEGIN')
                reports = checked_reports(db, since, time.time())
        except sqlite3.OperationalError as error:
            if getattr(error, 'sqlite_errorcode', 0) & 255 not in (
                    sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED, sqlite3.SQLITE_CANTOPEN):
                raise
            reports = {}
        if reports:
            print('Matched advancing reports from seven power sensors, Power Flow Status and Inverter Charging Power.',
                  flush=True)
            return
        time.sleep(4)
    raise RuntimeError('Timed out waiting for fresh observations from the nine monitored sensors')


def usable_routing(live, since, now):
    routing, values = live.get('charging_routing'), live.get('values', {})
    stamp = live.get('updated_at')
    if (live.get('status') not in ('live', 'partial') or type(stamp) not in (int, float)
            or not max(since, now - 90) <= stamp <= now + 30 or not isinstance(routing, dict)
            or not all(type(routing.get(key)) is bool for key in ('mains_active', 'solar_active', 'persistent'))):
        return False
    keys = ('grid', 'bridge', 'load', 'battery', 'pv', 'grid_house', 'grid_battery', 'solar_battery')
    if not all(type(values.get(k)) in (int, float) and math.isfinite(values[k]) for k in keys):
        return False
    grid, bridge, load, battery, pv, house, ac, solar = (values[k] for k in keys)
    require(0 <= house <= load and 0 <= ac <= max(0, -bridge) + 1e-6 and house + ac <= grid + 1e-6
            and 0 <= solar <= pv + 1e-6 and ac + solar <= max(0, battery) + 1e-6
            and (routing['mains_active'] or ac == 0) and (routing['solar_active'] or solar == 0),
            'Live routes contradict the charging flags or measured power')
    discrepancy = routing.get('discrepancy_w')
    require(type(discrepancy) in (int, float) and math.isfinite(discrepancy) and discrepancy >= 0
            and (not routing['persistent'] or discrepancy >= max(150, battery * .1)), 'Invalid charging discrepancy')
    return True


def verify(began, directory, record):
    # The legacy bridge verifier enforced a solar-first bound that is superseded
    # here. Its unqualified recording check still verifies branch counts and kWh coverage.
    GRID.verify_recording(began)
    for host in BASE.HOSTS:
        deadline = time.monotonic() + 90
        while True:
            status, headers, body = BASE.origin(host, '/api/energy')
            require(status == 200, 'Unavailable live endpoint: ' + host)
            BASE.check_headers(headers)
            live = json.loads(body)
            if usable_routing(live, began, time.time()):
                break
            require(time.monotonic() < deadline, 'Fresh status-backed routes unavailable: ' + host)
            time.sleep(3)
        flags, values = live['charging_routing'], live['values']
        print(f"{host}: mains charging={flags['mains_active']}, solar charging={flags['solar_active']}, "
              f"grid to house={values['grid_house']:g} W, grid to battery={values['grid_battery']:g} W, "
              f"solar to battery={values['solar_battery']:g} W.", flush=True)
    with closing(sqlite3.connect((INSTALL.ROOT / 'runtime/history.sqlite3').as_uri() + '?mode=ro', uri=True)) as db:
        boundary = db.execute("SELECT value FROM metadata WHERE key='charging_routing_from'").fetchone()
        require(boundary and boundary[0] <= time.time(), 'New recording method absent')
    BASE.verify_http({name.removeprefix('public/'): record['after'][name] for name in PUBLIC},
                     record['saved'][str(BASE.FEED)]['sha256'])
    for path, previous in record['saved'].items():
        require(BASE.fingerprint(Path(path)) == previous, 'Protected file changed: ' + path)
    GRID.check_existing_day(json.loads(BASE.read(directory / 'history-day.json')),
                            json.loads(BASE.read(BASE.HISTORY / (record['day'] + '.json'))))
    after = INSTALL.service_state()
    require(all(after[n] == record['services'][n] for n in ('nginx', 'homeenergy-control'))
            and 'ActiveState=active' in after['homeenergy'], 'Unexpected service state')


def load_backup(directory):
    require(directory.resolve() == directory and directory.parent == GRID.ARCHIVE
            and directory.name.startswith(TAG + '-backup.') and directory.stat().st_uid == 0
            and stat.S_IMODE(directory.stat().st_mode) == 0o700, 'Unexpected private backup')
    record = json.loads(BASE.read(directory / 'record.json'))
    require(record['release'] == str(SOURCE) and set(record['before']) == set(record['after']) == set(FILES),
            'Backup belongs to another release')
    for name, meta in record['before'].items():
        require(BASE.digest(BASE.read(directory / name)) == meta['sha256'], 'Backup bytes differ: ' + name)
    return record


def restore(directory, restart_ha):
    record = load_backup(directory)
    changed = []
    for name in FILES:
        actual = BASE.digest(BASE.read(target(name)))
        if actual != record['before'][name]['sha256']:
            require(actual == record['after'][name], 'Unexpected installed file: ' + name)
            changed.append(name)
    for name in reversed(changed):
        write(name, record['after'][name], BASE.read(directory / name), record['before'][name])
    if any(n in BACKEND for n in changed):
        INSTALL.restart_exporter()
    if TEMPLATE in changed and restart_ha:
        HA.restart_ha(record['cid'])
    elif TEMPLATE in changed:
        print('The original template is restored on disk. Reload templates in Home Assistant if already loaded.',
              flush=True)
    print('Previous release restored. Readings collected since activation are retained.', flush=True)


def activate(restart_ha):
    bodies, originals, cid = preflight()
    directory, record = backup(bodies, originals, cid)
    reloaded = False
    try:
        since = time.time()
        write(TEMPLATE, originals[TEMPLATE]['sha256'], bodies[TEMPLATE], originals[TEMPLATE])
        HA.validate_configuration(cid)
        if restart_ha:
            reloaded = True
            HA.restart_ha(cid)
        else:
            print('Reload Template entities in Home Assistant now. Waiting up to four minutes.', flush=True)
        wait_for_reports(since)
        for name in BACKEND:
            write(name, originals[name]['sha256'], bodies[name], originals[name])
        began = time.time()
        INSTALL.restart_exporter()
        for name in PUBLIC:
            write(name, originals[name]['sha256'], bodies[name], originals[name])
        verify(began, directory, record)
    except BaseException:
        restore(directory, reloaded)
        raise
    print('Charging-source estimates active on Overview and History at both origins. '
          'Earlier history and reports verified.', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--check', action='store_true')
    modes.add_argument('--activate', action='store_true')
    modes.add_argument('--rollback', type=Path)
    parser.add_argument('--restart-home-assistant', action='store_true')
    args = parser.parse_args()
    if args.check:
        require(not args.restart_home_assistant, 'Read-only check cannot request a restart')
        preflight()
        print('Read-only preflight passed for the installed template, exporter and both HTTPS origins.')
        return
    require(os.geteuid() == 0, 'Administrator privileges required')
    os.umask(0o077)
    if args.activate:
        activate(args.restart_home_assistant)
    else:
        restore(args.rollback, args.restart_home_assistant)


if __name__ == '__main__':
    main()
