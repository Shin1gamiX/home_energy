"""Install the Home Assistant flow observation template before switching the read-only collector."""
import argparse
from contextlib import closing
import http.client
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import tempfile
import time


SOURCE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('grid_release', SOURCE / 'activate-history-grid-split.py')
GRID = importlib.util.module_from_spec(spec)
spec.loader.exec_module(GRID)
INSTALL, BASE, require = GRID.INSTALL, GRID.BASE, GRID.require
EXPORT = 'server/export_energy.py'
TEMPLATE = 'server/homeassistant/flow_observations.yaml'
INSTALL.FILES = (EXPORT,)
SCRIPTS = ('activate-flow-observations.py', 'activate-history-grid-split.py',
           'activate-battery-voltage.py', 'activate-interface.py')
CONFIG_DIR = Path('/home/shini/docker/homeassistant/config')
CONFIG = CONFIG_DIR / 'configuration.yaml'
OBSERVATIONS = CONFIG_DIR / 'flow_observations.yaml'
EXPORTER = INSTALL.ROOT / EXPORT
DATABASE = CONFIG_DIR / 'home-assistant_v2.db'
TAG = 'homeenergy-flow-observations'
DOCKER = '/usr/bin/docker'
ENTITY = 'sensor.home_energy_flow_observations'
MAIN_PREFIX = 'sensor.anenji_anj_11kw_48v_wifi_p_'
PV_PREFIX = 'sensor.living_room_anenji_anj_11kw_48v_wifi_p_'
SENSORS = tuple(MAIN_PREFIX + suffix for suffix in
                ('grid_power', 'inverter_power', 'load_power', 'battery_power', 'pv_power')) + (
                    PV_PREFIX + 'pv1_power', PV_PREFIX + 'pv2_power')


def configuration(original):
    require(not re.search(rb'(?m)^template(?:\s+[^:\n]+)?\s*:', original)
            and b'flow_observations.yaml' not in original, 'Existing template configuration requires review')
    return original + (b'' if original.endswith(b'\n') else b'\n') + b'\ntemplate: !include flow_observations.yaml\n'


def staged():
    require(SOURCE.parent == GRID.ARCHIVE and SOURCE.name.startswith(TAG + '-release.'), 'Unexpected release directory')
    manifest = json.loads(BASE.read(SOURCE / 'manifest.json'))
    require(set(manifest['staged']) == set((*SCRIPTS, EXPORT, TEMPLATE)), 'Unexpected staged files')
    bodies = {name: BASE.read(SOURCE / name) for name in manifest['staged']}
    for name, body in bodies.items():
        require(BASE.digest(body) == manifest['staged'][name], 'Staged bytes changed: ' + name)
    return manifest, bodies


def container(expected_id=None, require_running=True):
    # Request only the status, ID and /config mount; container environment may contain credentials.
    fmt = '{{.Id}} {{.State.Running}} {{range .Mounts}}{{if eq .Destination "/config"}}{{.Source}}{{end}}{{end}}'
    fields = subprocess.check_output([DOCKER, 'inspect', '--format', fmt, 'homeassistant'],
                                     text=True, timeout=15).strip().split()
    require(len(fields) == 3 and re.fullmatch(r'[0-9a-f]{64}', fields[0])
            and (not require_running or fields[1] == 'true') and fields[2] == str(CONFIG_DIR)
            and (expected_id is None or fields[0] == expected_id),
            'Home Assistant container or configuration mount differs')
    return fields[0]


def validate_configuration(cid):
    result = subprocess.run([DOCKER, 'exec', container(cid), 'python', '-IB', '-m', 'homeassistant',
                             '--script', 'check_config', '-c', '/config'], capture_output=True, timeout=180)
    require(result.returncode == 0, 'Native Home Assistant configuration check failed')


def restart_ha(cid):
    subprocess.run([DOCKER, 'restart', '--time', '60', container(cid, require_running=False)], check=True,
                   timeout=180, stdout=subprocess.DEVNULL)
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        try:
            with closing(http.client.HTTPConnection('127.0.0.1', 8123, timeout=3)) as conn:
                conn.request('GET', '/api/')
                if conn.getresponse().status == 401:
                    container(cid)
                    return
        except (OSError, http.client.HTTPException):
            pass
        time.sleep(2)
    raise RuntimeError('Home Assistant API did not return after restart')


def targets():
    return {'configuration.yaml': CONFIG, 'flow_observations.yaml': OBSERVATIONS, 'export_energy.py': EXPORTER}


def replace_checked(path, expected, body, meta):
    require(path in targets().values() and path.parent.resolve() == path.parent, 'Unexpected destination')
    fd, tmp = tempfile.mkstemp(prefix='.flow-observations-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(body)
            os.fchown(stream.fileno(), meta['uid'], meta['gid'])
            os.fchmod(stream.fileno(), meta['mode'])
            stream.flush()
            os.fsync(stream.fileno())
        if expected is None:
            os.link(tmp, path)  # Exclusive installation of a genuinely new file.
        else:
            require(BASE.digest(BASE.read(path)) == expected, 'Target changed: ' + path.name)
            os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)
    require(BASE.digest(BASE.read(path)) == BASE.digest(body), 'Installed bytes differ: ' + path.name)


def preflight(manifest, bodies):
    original = BASE.read(CONFIG)
    exporter = BASE.read(EXPORTER)
    require(BASE.digest(original) == manifest['configuration_before'], 'Home Assistant configuration changed')
    require(BASE.digest(exporter) == manifest['exporter_before'], 'Installed exporter changed')
    require(not OBSERVATIONS.exists() and not OBSERVATIONS.is_symlink(), 'Template destination already exists')
    result = {'configuration.yaml': configuration(original), 'flow_observations.yaml': bodies[TEMPLATE],
              'export_energy.py': INSTALL.exporter_for_installation(bodies[EXPORT], exporter)}
    cid = container()
    require(all('ActiveState=active' in s for s in INSTALL.service_state().values()), 'Required service inactive')
    BASE.verify_http({}, BASE.digest(BASE.read(BASE.FEED)))
    return result, cid


def backup(candidates, cid):
    directory = Path(tempfile.mkdtemp(prefix=TAG + '-backup.', dir=GRID.ARCHIVE))
    print('Private rollback backup: ' + str(directory), flush=True)
    before = {}
    for name, path in targets().items():
        if path == OBSERVATIONS:
            continue
        before[name] = BASE.fingerprint(path)
        (directory / name).write_bytes(BASE.read(path))
        require(BASE.digest(BASE.read(directory / name)) == before[name]['sha256'], 'Inconsistent backup: ' + name)
    day = GRID.backup_history(directory)
    saved = INSTALL.protected()
    for path in BASE.HISTORY.glob('????-??-??.json'):
        if path.stem < day and str(path) not in saved:
            saved[str(path)] = BASE.fingerprint(path)
    record = dict(release=str(SOURCE), before=before, after={k: BASE.digest(v) for k, v in candidates.items()},
                  day=day, saved=saved, container=cid, since=time.time())
    (directory / 'record.json').write_text(json.dumps(record), encoding='utf-8')
    return directory, record


def load_backup(directory):
    require(directory.resolve() == directory and directory.parent == GRID.ARCHIVE and
            directory.name.startswith(TAG + '-backup.') and directory.stat().st_uid == 0 and
            stat.S_IMODE(directory.stat().st_mode) == 0o700, 'Unexpected private backup')
    record = json.loads(BASE.read(directory / 'record.json'))
    require(record['release'] == str(SOURCE) and set(record['after']) == set(targets())
            and set(record['before']) == {'export_energy.py', 'configuration.yaml'}, 'Backup belongs to a different release')
    for name, meta in record['before'].items():
        require(BASE.digest(BASE.read(directory / name)) == meta['sha256'], 'Backup bytes differ: ' + name)
    return record


def restore(directory, record, restart_home_assistant=False):
    paths = targets()
    configuration_changed = False
    for name in ('export_energy.py', 'configuration.yaml', 'flow_observations.yaml'):
        path = paths[name]
        original = record['before'].get(name)
        if original is None:
            if path.exists() or path.is_symlink():
                require(BASE.digest(BASE.read(path)) == record['after'][name], 'Unexpected bytes at ' + name)
                path.unlink()
        elif BASE.digest(BASE.read(path)) != original['sha256']:
            replace_checked(path, record['after'][name], BASE.read(directory / name), original)
            if path == EXPORTER:
                INSTALL.restart_exporter()
            elif path == CONFIG:
                configuration_changed = True
    if configuration_changed and restart_home_assistant:
        restart_ha(record['container'])
    print('Previous configuration and collector restored on disk. History and reports were not restored over live data.', flush=True)
    if configuration_changed and not restart_home_assistant:
        print('A loaded HA template is removed on its next reload/restart.', flush=True)


def checked_reports(db, since, now):
    # Compare each source at the revision sampled, not at its newest revision:
    # high-rate power sensors can change between a template tick and this check.
    rows = db.execute("""SELECT s.state,s.last_updated_ts,a.shared_attrs
        FROM states s JOIN state_attributes a ON a.attributes_id=s.attributes_id
        WHERE s.metadata_id=(SELECT metadata_id FROM states_meta WHERE entity_id=?)
          AND s.last_updated_ts>=? ORDER BY s.last_updated_ts DESC LIMIT 4""", (ENTITY, max(since, now - 90)))
    samples = []
    for state, sampled, raw in rows:
        attrs = json.loads(raw)
        reports = attrs.get('reports', {})
        if state != '1' or attrs.get('restored') or not isinstance(reports, dict) or set(reports) != set(SENSORS):
            continue
        valid = {}
        for entity in SENSORS:
            observation = reports[entity]
            if not isinstance(observation, list) or len(observation) != 3:
                break
            value, updated, reported = observation
            if (not isinstance(value, str) or type(updated) not in (int, float) or type(reported) not in (int, float)
                    or not (0 < updated <= reported <= sampled + 0.000001 and since <= reported
                            and sampled <= now + 30 and now - reported <= 90)):
                break
            try:
                if not math.isfinite(float(value)):
                    break
            except ValueError:
                break
            row = db.execute("""SELECT a.shared_attrs FROM states s JOIN state_attributes a ON a.attributes_id=s.attributes_id
                 WHERE s.metadata_id=(SELECT metadata_id FROM states_meta WHERE entity_id=?)
                   AND s.last_updated_ts BETWEEN ? AND ? AND s.state=? LIMIT 1""",
                             (entity, updated - 0.000001, updated + 0.000001, value)).fetchone()
            meta = json.loads(row[0]) if row else {}
            if meta.get('unit_of_measurement') != 'W' or meta.get('restored'):
                break
            valid[entity] = reported
        if len(valid) == len(SENSORS):
            samples.append(valid)
    if samples:
        for older in samples[1:]:
            if all(samples[0][entity] > older[entity] for entity in SENSORS):
                return samples[0]
    return {}


def wait_for_reports(since):
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        try:
            with closing(sqlite3.connect(DATABASE.as_uri() + '?mode=ro', uri=True, timeout=4)) as db:
                db.execute('PRAGMA query_only=ON')
                db.execute('BEGIN')
                reports = checked_reports(db, since, time.time())
        except sqlite3.OperationalError as exc:
            code = getattr(exc, 'sqlite_errorcode', 0) & 255
            if code not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED, sqlite3.SQLITE_CANTOPEN):
                raise
            reports = {}
        if reports:
            for entity, stamp in reports.items():
                print(f'{entity}: observed advancing report time {stamp:.6f} with matching Recorder revision.', flush=True)
            return True
        time.sleep(5)
    return False


def finish(directory, record, bodies):
    for name, path in targets().items():
        expected = record['before'][name]['sha256'] if path == EXPORTER else record['after'][name]
        require(BASE.digest(BASE.read(path)) == expected, 'Installed state changed: ' + name)
    container(record['container'])
    print('Awaiting two fresh template observations for each of the seven power sensors.', flush=True)
    if not wait_for_reports(record['since']):
        print('No collector switch. The checked HA configuration and backup are retained. After checking the template, resume with:\n'
              f'sudo /usr/bin/python3 -I -B {Path(__file__).resolve()} --resume {directory}', flush=True)
        return False
    name = 'export_energy.py'
    candidate = INSTALL.exporter_for_installation(bodies[EXPORT], BASE.read(directory / name))
    require(BASE.digest(candidate) == record['after'][name], 'Exporter candidate changed')
    try:
        since = time.time()
        replace_checked(EXPORTER, record['before'][name]['sha256'], candidate, record['before'][name])
        INSTALL.restart_exporter()
        GRID.verify_recording(since, bridge=True)
        BASE.verify_http({}, record['saved'][str(BASE.FEED)]['sha256'])
        for path, prior in record['saved'].items():
            require(BASE.fingerprint(Path(path)) == prior, 'Protected file changed: ' + path)
        GRID.check_existing_day(json.loads(BASE.read(directory / 'history-day.json')),
                                json.loads(BASE.read(BASE.HISTORY / (record['day'] + '.json'))))
    except BaseException:
        restore(directory, record, (directory / 'restart-requested').is_file())
        raise
    print('Collector active. Both HTTPS origins, new recording, earlier history and existing reports verified.', flush=True)
    return True


def activate(manifest, bodies, restart_home_assistant):
    candidates, cid = preflight(manifest, bodies)
    directory, record = backup(candidates, cid)
    config_meta = record['before']['configuration.yaml']
    require(config_meta['sha256'] == manifest['configuration_before'] and
            record['before']['export_energy.py']['sha256'] == manifest['exporter_before'], 'Baseline changed before backup')
    try:
        replace_checked(OBSERVATIONS, None, candidates['flow_observations.yaml'], config_meta)
        replace_checked(CONFIG, config_meta['sha256'], candidates['configuration.yaml'], config_meta)
        validate_configuration(cid)
        if restart_home_assistant:
            print('Home Assistant configuration passed. Restarting only the existing Home Assistant container.', flush=True)
            (directory / 'restart-requested').touch(exist_ok=False)
            restart_ha(cid)
        else:
            print('Home Assistant configuration passed. Apply the template in Home Assistant to continue.', flush=True)
    except BaseException:
        restore(directory, record, (directory / 'restart-requested').is_file())
        raise
    return finish(directory, record, bodies)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--check', action='store_true')
    modes.add_argument('--activate', action='store_true')
    modes.add_argument('--resume', type=Path)
    modes.add_argument('--rollback', type=Path)
    parser.add_argument('--restart-home-assistant', action='store_true')
    args = parser.parse_args()
    require(not args.restart_home_assistant or args.activate or args.rollback,
            'Use restart option only with --activate or --rollback')
    manifest, bodies = staged()
    if args.check:
        preflight(manifest, bodies)
        print('Read-only preflight passed. No configuration, history or reports changed.')
        return
    require(os.geteuid() == 0, 'Administrator privileges required')
    os.umask(0o077)
    if args.activate:
        success = activate(manifest, bodies, args.restart_home_assistant)
    elif args.resume:
        success = finish(args.resume, load_backup(args.resume), bodies)
    else:
        restore(args.rollback, load_backup(args.rollback), args.restart_home_assistant)
        return
    if not success:
        raise SystemExit(2)


if __name__ == '__main__':
    main()
