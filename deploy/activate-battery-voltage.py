"""Backup-first battery-voltage recording and control-spacing release.

Installation-specific. Run as root with --check or --activate from the staged
release. Reuses the existing read-only HTTPS verifier; does not change Nginx.
Rollback restores code only, never removes readings collected since activation.
"""
import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import time
from datetime import datetime
from zoneinfo import ZoneInfo

ROOT = Path('/var/www/homeenergy')
SOURCE = Path(__file__).resolve().parent
FILES = ('public/app.js', 'public/history.js', 'public/interface.css',
         'public/index.html', 'public/history.html',
         'server/export_energy.py', 'server/history_store.py')
HOSTS = ('home.shin1gamix.com', 'home.shin1gamix.gr')
NGINX = Path('/etc/nginx/sites-available/homeenergy.conf')
UNIT = Path('/etc/systemd/system/homeenergy.service')
BASE_PATH = SOURCE / 'activate-interface.py'
if not BASE_PATH.exists():
    BASE_PATH = Path(__file__).with_name('activate-interface.py')
SPEC = importlib.util.spec_from_file_location('interface_checks', BASE_PATH)
BASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BASE)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def digest(body):
    return hashlib.sha256(body).hexdigest()


def exporter_for_installation(candidate, installed):
    generic = b"'/srv/homeassistant/config/home-assistant_v2.db'"
    existing = b"'/home/shini/docker/homeassistant/config/home-assistant_v2.db'"
    require(candidate.count(generic) == 1 and installed.count(existing) == 1,
            'Exporter database-path anchor changed; inspect before activation')
    return candidate.replace(generic, existing, 1)


def protected():
    BASE.FILES = tuple(name.removeprefix('public/') for name in FILES if name.startswith('public/'))
    excluded = {str(ROOT / name) for name in FILES}
    result = {path: value for path, value in BASE.protected_state(True).items() if path not in excluded}
    for path in (NGINX, UNIT):
        result[str(path)] = BASE.fingerprint(path)
    return result


def service_state():
    return {name: subprocess.check_output(['/usr/bin/systemctl', 'show', name,
            '-p', 'MainPID', '-p', 'NRestarts', '-p', 'ActiveState'], text=True)
            for name in ('nginx', 'homeenergy', 'homeenergy-control')}


def write(name, body, metadata):
    require(name in FILES, 'Target outside release allowlist')
    path = ROOT / name
    require(path.resolve() == path and path.parent.resolve() == path.parent,
            'Unexpected target path')
    # Replacing existing regular files only; ownership/modes are preserved.
    BASE.read(path)
    fd, pending = tempfile.mkstemp(prefix='.voltage-release-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(body)
            os.fchmod(stream.fileno(), metadata['mode'])
            os.fchown(stream.fileno(), metadata['uid'], metadata['gid'])
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(pending, path)
    finally:
        if os.path.exists(pending):
            os.unlink(pending)
    require(digest(BASE.read(path)) == digest(body), 'Installed bytes differ: ' + name)
    info = path.stat()
    require((info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) ==
            (metadata['uid'], metadata['gid'], metadata['mode']), 'Installed metadata differs')


def bundle():
    require(os.geteuid() == 0, 'Administrator privileges required')
    require(SOURCE.resolve() == SOURCE and SOURCE.parent == Path('/home/shini')
            and SOURCE.name.startswith('homeenergy-voltage-release.'), 'Unexpected release path')
    manifest = json.loads(BASE.read(SOURCE / 'manifest.json'))
    require(set(manifest['files']) == set(FILES), 'Release allowlist differs')
    require(digest(BASE.read(BASE_PATH)) == manifest['verifier_sha256'], 'HTTP verifier changed')
    require(digest(BASE.read(Path(__file__))) == manifest['helper_sha256'], 'Activation helper changed')
    require(digest(BASE.read(NGINX)) == manifest['nginx_sha256'], 'Nginx baseline changed')
    bodies = {}
    for name in FILES:
        body = BASE.read(SOURCE / name)
        require(digest(body) == manifest['files'][name]['after'], 'Candidate changed: ' + name)
        installed = BASE.read(ROOT / name)
        require(digest(installed) == manifest['files'][name]['before'],
                'Installed baseline changed: ' + name)
        if name == 'server/export_energy.py':
            body = exporter_for_installation(body, installed)
        bodies[name] = body
    state = service_state()
    require(all('ActiveState=active' in value for value in state.values()), 'Required service inactive')
    saved = protected()
    BASE.verify_http({}, saved[str(BASE.FEED)]['sha256'])
    return bodies, state, saved


def recorded_voltage(energy, history, since):
    value = energy.get('values', {}).get('battery_voltage')
    stamp = energy.get('updated_at')
    if not (isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)
            and isinstance(stamp, (float, int)) and since <= stamp <= time.time() + 30
            and energy.get('status') in ('live', 'partial')):
        return False
    minute = int(stamp // 60) * 60
    return any(point.get('t') == minute
               and isinstance(point.get('values', {}).get('battery_voltage'), (float, int))
               and math.isfinite(point['values']['battery_voltage'])
               and point.get('counts', {}).get('battery_voltage', 0) > 0
               for point in history.get('points', []))


def verify_recording(since):
    for attempt in range(25):
        complete = True
        for host in HOSTS:
            status, headers, body = BASE.origin(host, '/api/energy')
            require(status == 200, 'Live endpoint unavailable')
            BASE.check_headers(headers)
            energy = json.loads(body)
            stamp = energy.get('updated_at') or time.time()
            day = datetime.fromtimestamp(stamp, ZoneInfo('Europe/Athens')).strftime('%Y-%m-%d')
            status, headers, body = BASE.origin(host, '/history/' + day + '.json')
            require(status == 200, 'Current daily history unavailable')
            BASE.check_headers(headers)
            complete &= recorded_voltage(energy, json.loads(body), since)
        if complete:
            print('Both HTTPS hostnames: fresh measured battery voltage and minute recording verified.', flush=True)
            return
        if attempt < 24:
            time.sleep(2)
    raise RuntimeError('Fresh battery-voltage recording not verified within the bounded check')


def restart_exporter():
    subprocess.run(['/usr/bin/systemctl', 'restart', 'homeenergy.service'], check=True, timeout=30)


def restore(backup):
    require(backup.resolve() == backup and backup.parent == Path('/home/shini')
            and backup.name.startswith('homeenergy-voltage-backup.'), 'Unexpected backup path')
    require(backup.stat().st_uid == 0 and stat.S_IMODE(backup.stat().st_mode) == 0o700,
            'Backup is not root-private')
    record = json.loads(BASE.read(backup / 'record.json'))
    require(set(record['files']) == set(FILES), 'Backup allowlist differs')
    originals = {name: BASE.read(backup / name) for name in FILES}
    for name, body in originals.items():
        require(digest(body) == record['files'][name]['sha256'], 'Backup bytes differ')
    for name, body in originals.items():
        write(name, body, record['files'][name])
    restart_exporter()
    BASE.verify_http({name.removeprefix('public/'): record['files'][name]['sha256']
                      for name in FILES if name.startswith('public/')},
                     record['protected'][str(BASE.FEED)]['sha256'])
    require(protected() == record['protected'], 'Protected state differs after rollback')
    print('Previous code restored. All collected readings, reports and evidence retained.', flush=True)


def activate(bodies, services_before, preserved):
    backup = Path(tempfile.mkdtemp(prefix='homeenergy-voltage-backup.', dir='/home/shini'))
    backup.chmod(0o700)
    metadata = {name: BASE.fingerprint(ROOT / name) for name in FILES}
    for name in FILES:
        path = backup / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(BASE.read(ROOT / name))
        path.chmod(0o600)
    (backup / 'record.json').write_text(json.dumps(
        dict(files=metadata, protected=preserved, services=services_before), sort_keys=True), encoding='utf-8')
    (backup / 'record.json').chmod(0o600)
    print('Private rollback backup: ' + str(backup), flush=True)
    try:
        require(protected() == preserved, 'Protected state changed before installation')
        for name, body in bodies.items():
            write(name, body, metadata[name])
        since = time.time()
        restart_exporter()
        verify_recording(since)
        expected = {name.removeprefix('public/'): digest(body) for name, body in bodies.items()
                    if name.startswith('public/')}
        BASE.verify_http(expected, preserved[str(BASE.FEED)]['sha256'])
        after = service_state()
        require('ActiveState=active' in after['homeenergy'], 'Exporter is not active')
        for service in ('nginx', 'homeenergy-control'):
            require(after[service] == services_before[service], 'Unrelated service changed: ' + service)
        require(protected() == preserved, 'Protected files changed')
    except BaseException:
        print('Activation failed; restoring previous code. Evidence is retained.', flush=True)
        restore(backup)
        raise
    print('Battery voltage and control spacing deployed. Only the Home Energy exporter was restarted.', flush=True)
    print('No Nginx/SSH reload, device restart, report publication, backfill or history deletion.', flush=True)


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--check', action='store_true')
    group.add_argument('--activate', action='store_true')
    group.add_argument('--rollback', type=Path)
    args = parser.parse_args()
    require(os.geteuid() == 0, 'Administrator privileges required')
    os.umask(0o077)
    if args.rollback:
        restore(args.rollback)
        return
    bodies, services_before, preserved = bundle()
    if args.activate:
        activate(bodies, services_before, preserved)
    else:
        print('Read-only release checks passed. Nothing activated or restarted.', flush=True)


if __name__ == '__main__':
    main()
