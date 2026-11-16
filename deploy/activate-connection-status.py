"""Backed-up connection-status release; no report or history-data mutations.

The existing exporter gets fixed, read-only diagnostics. Its service restrictions
are retained. Only Nginx is reloaded and the exporter restarted.
"""
import argparse
from datetime import datetime
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import time
from zoneinfo import ZoneInfo

SOURCE = Path(__file__).resolve().parent
ROOT = Path('/var/www/homeenergy')
NGINX = Path('/etc/nginx/sites-available/homeenergy.conf')
UNIT = Path('/etc/systemd/system/homeenergy.service')
DROPIN = Path('/etc/systemd/system/homeenergy.service.d/30-connection-health.conf')
FILES = ('public/connection-status.css', 'public/connection-status.js',
         'server/connection_probe.py', 'server/connection_health.py',
         'server/export_energy.py', 'public/app.js', 'public/index.html')
NEW_FILES = frozenset(FILES[:4])
CONFIG_NAMES = ('nginx.conf', 'health.conf')
VERSION = '20261116-connection'
VERIFIER = SOURCE / 'activate-interface.py'
SPEC = importlib.util.spec_from_file_location('connection_release_checks', VERIFIER)
BASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BASE)
BASE.FILES = tuple(name.removeprefix('public/') for name in FILES if name.startswith('public/'))
require = BASE.require


def target(name):
    require(name in (*FILES, *CONFIG_NAMES), 'Target outside connection release allowlist')
    return NGINX if name == 'nginx.conf' else DROPIN if name == 'health.conf' else ROOT / name


def exporter_for_installation(candidate, installed):
    generic = b"'/srv/homeassistant/config/home-assistant_v2.db'"
    existing = b"'/home/shini/docker/homeassistant/config/home-assistant_v2.db'"
    require(candidate.count(generic) == 1 and installed.count(existing) == 1,
            'Exporter database path changed; inspect before activation')
    return candidate.replace(generic, existing, 1)


def nginx_candidate(original):
    text = original.decode('utf-8')
    marker = r'|app\.js|'
    anchor = '    location = /app.js { try_files $uri =404; }'
    require(text.count(marker) == 1 and text.count(anchor) == 1
            and 'connection-status' not in text, 'Nginx anchors changed; inspect first')
    text = text.replace(marker, r'|app\.js|connection-status\.js|connection-status\.css|', 1)
    routes = '\n'.join('    location = /' + name + ' { try_files $uri =404; }'
                       for name in ('connection-status.js', 'connection-status.css'))
    return text.replace(anchor, anchor + '\n' + routes, 1).encode('utf-8')


def environment(config):
    require(isinstance(config, dict) and set(config) == {'container', 'router', 'interface', 'port'},
            'Unexpected health configuration')
    require(isinstance(config['container'], str)
            and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,62}', config['container']), 'Invalid container')
    require(isinstance(config['interface'], str)
            and re.fullmatch(r'[A-Za-z0-9_.-]{1,15}', config['interface']), 'Invalid interface')
    router = str(ipaddress.IPv4Address(config['router']))
    require(isinstance(config['port'], int) and not isinstance(config['port'], bool)
            and 1 <= config['port'] <= 65535, 'Invalid listener port')
    return dict(ENERGY_HEALTH_CONTAINER=config['container'], ENERGY_HEALTH_ROUTER=router,
                ENERGY_HEALTH_INTERFACE=config['interface'], ENERGY_HEALTH_LISTENER_PORT=str(config['port']))


def dropin_candidate(config):
    return ('[Service]\n' + ''.join('Environment="' + key + '=' + value + '"\n'
            for key, value in environment(config).items())).encode('ascii')


def write(name, body, metadata):
    path = target(name)
    require(path.resolve() == path and path.parent.resolve() == path.parent and not path.is_symlink(),
            'Unexpected target path')
    if path.exists():
        BASE.read(path)
    fd, pending = tempfile.mkstemp(prefix='.connection-release-', dir=path.parent)
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
    info = path.stat()
    require(BASE.digest(BASE.read(path)) == BASE.digest(body), 'Installed bytes differ: ' + name)
    require((info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) ==
            (metadata['uid'], metadata['gid'], metadata['mode']), 'Installed metadata differs: ' + name)


def protected(include_private):
    excluded = {str(ROOT / name) for name in FILES}
    saved = {path: value for path, value in BASE.protected_state(include_private).items()
             if path not in excluded}
    saved[str(UNIT)] = BASE.fingerprint(UNIT)
    for path in DROPIN.parent.glob('*.conf'):
        if path != DROPIN:
            saved[str(path)] = BASE.fingerprint(path)
    return saved


def service_state():
    return {name: subprocess.check_output(['/usr/bin/systemctl', 'show', name,
            '-p', 'MainPID', '-p', 'NRestarts', '-p', 'ActiveState'], text=True)
            for name in ('nginx', 'homeenergy', 'homeenergy-control', 'ssh')}


def probe(config):
    env = {**os.environ, **environment(config)}
    # Uses the exact candidate probe and the existing user's Docker access.
    command = ['/usr/bin/python3', '-I', '-B', '-c',
               'import json,runpy,sys; m=runpy.run_path(sys.argv[1]); '
               'print(json.dumps(m["HealthMonitor"].from_environment().probe()))',
               str(SOURCE / 'server/connection_health.py')]
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=12, check=True)
    require(json.loads(result.stdout) == dict(vpn='up', dongle='up', collector='up'),
            'Healthy route/listener/session not confirmed; inspect before activation')


def bundle():
    require(SOURCE.resolve() == SOURCE and SOURCE.parent == Path('/home/shini')
            and SOURCE.name.startswith('homeenergy-connection-release.'), 'Unexpected release path')
    manifest = json.loads(BASE.read(SOURCE / 'manifest.json'))
    require(set(manifest['files']) == set(FILES), 'Release allowlist differs')
    for path, key in ((Path(__file__), 'helper_sha256'), (VERIFIER, 'verifier_sha256'),
                      (NGINX, 'nginx_sha256'), (UNIT, 'unit_sha256')):
        require(BASE.digest(BASE.read(path)) == manifest[key], 'Reviewed baseline changed: ' + path.name)
    require(not DROPIN.exists() and not DROPIN.is_symlink(), 'Health configuration already exists')
    require(not DROPIN.parent.is_symlink(), 'Unexpected service drop-in directory')
    bodies, originals = {}, {}
    for name in FILES:
        body = BASE.read(SOURCE / name)
        require(BASE.digest(body) == manifest['files'][name]['after'], 'Candidate changed: ' + name)
        path = target(name)
        if name in NEW_FILES:
            require(manifest['files'][name]['before'] is None and not path.exists() and not path.is_symlink(),
                    'New target already exists: ' + name)
        else:
            originals[name] = BASE.fingerprint(path)
            require(originals[name]['sha256'] == manifest['files'][name]['before'],
                    'Installed baseline changed: ' + name)
        if name == 'server/export_energy.py':
            body = exporter_for_installation(body, BASE.read(path))
        bodies[name] = body
    bodies['nginx.conf'] = nginx_candidate(BASE.read(NGINX))
    bodies['health.conf'] = dropin_candidate(manifest['health'])
    originals['nginx.conf'] = BASE.fingerprint(NGINX)
    state = service_state()
    require(all('ActiveState=active' in value for value in state.values()), 'Required service inactive')
    feed_hash = BASE.digest(BASE.read(BASE.FEED))
    BASE.verify_http({}, feed_hash)
    probe(manifest['health'])
    return bodies, originals, state, feed_hash


def restart_exporter():
    subprocess.run(['/usr/bin/systemctl', 'daemon-reload'], check=True, timeout=30)
    subprocess.run(['/usr/bin/systemctl', 'restart', 'homeenergy.service'], check=True, timeout=30)


def health_ready(energy, since, now):
    checks = energy.get('health', {}).get('checks', {})
    if set(checks) != {'vpn', 'dongle', 'collector', 'inverter'}:
        return False
    for item in checks.values():
        stamp = item.get('checked_at')
        if (item.get('state') != 'up' or not isinstance(stamp, (int, float))
                or isinstance(stamp, bool) or not since <= stamp <= now + 30 or now - stamp > 90):
            return False
    stamp = energy.get('updated_at')
    return (energy.get('health', {}).get('version') == 1 and energy.get('status') in ('live', 'partial')
            and isinstance(stamp, (int, float)) and not isinstance(stamp, bool) and since <= stamp <= now + 30)


def verify_health(since):
    for attempt in range(25):
        complete = True
        for host in BASE.HOSTS:
            status, headers, body = BASE.origin(host, '/api/energy')
            require(status == 200, 'Energy endpoint unavailable')
            BASE.check_headers(headers)
            energy = json.loads(body)
            ready = health_ready(energy, since, time.time())
            complete &= ready
            if ready:
                stamp = energy['updated_at']
                day = datetime.fromtimestamp(stamp, ZoneInfo('Europe/Athens')).strftime('%Y-%m-%d')
                status, _, body = BASE.origin(host, '/history/' + day + '.json')
                require(status == 200, 'Current recorded history unavailable')
                points = json.loads(body).get('points', [])
                complete &= any(point.get('t') == int(stamp // 60) * 60
                                and point.get('counts', {}).get('battery_voltage', 0) > 0 for point in points)
        if complete:
            print('Both HTTPS hostnames: fresh health checks, telemetry and normal minute recording verified.', flush=True)
            return
        if attempt < 24:
            time.sleep(2)
    raise RuntimeError('Health checks and recording were not confirmed; rolling back')


def verify_assets(bodies):
    for host in BASE.HOSTS:
        for name in ('connection-status.js', 'connection-status.css'):
            for suffix in ('', '?v=' + VERSION):
                for attempt in range(10):
                    status, headers, body = BASE.origin(host, '/' + name + suffix)
                    if status == 200 and BASE.digest(body) == BASE.digest(bodies['public/' + name]):
                        BASE.check_headers(headers, bool(suffix))
                        require(('javascript' if name.endswith('.js') else 'text/css') in
                                headers.get('content-type', ''), 'Wrong connection asset MIME')
                        break
                    require(attempt < 9, 'Connection asset route unavailable')
                    time.sleep(.5)


def restore(backup):
    require(backup.resolve() == backup and backup.parent == Path('/home/shini')
            and backup.name.startswith('homeenergy-connection-backup.'), 'Unexpected backup path')
    require(backup.stat().st_uid == 0 and stat.S_IMODE(backup.stat().st_mode) == 0o700,
            'Backup is not root-private')
    record = json.loads(BASE.read(backup / 'record.json'))
    require(set(record['files']) == set(FILES) - NEW_FILES | {'nginx.conf'}, 'Backup allowlist differs')
    originals = {name: BASE.read(backup / name) for name in record['files']}
    for name, body in originals.items():
        require(BASE.digest(body) == record['files'][name]['sha256'], 'Backup bytes differ')
    if DROPIN.exists() or DROPIN.is_symlink():
        require(BASE.digest(BASE.read(DROPIN)) == record['installed']['health.conf'],
                'Health configuration changed; manual rollback review required')
    for name, body in originals.items():
        write(name, body, record['files'][name])
        stamp = record['files'][name]['mtime_ns']
        os.utime(target(name), ns=(stamp, stamp))
    if DROPIN.exists():
        DROPIN.unlink()  # Only this release's exact configuration; keep all evidence/new code.
    BASE.nginx_reload()
    restart_exporter()
    expected = {name.removeprefix('public/'): meta['sha256'] for name, meta in record['files'].items()
                if name.startswith('public/')}
    BASE.verify_after_reload(expected, record['feed_hash'])
    require(protected(True) == record['protected'], 'Protected state differs after rollback')
    print('Previous frontend/exporter/routes restored; new unused modules and all evidence retained.', flush=True)
    print('Reports and history data were not restored, deleted or modified.', flush=True)


def activate():
    require(os.geteuid() == 0, 'Activation requires sudo')
    bodies, originals, running, feed_hash = bundle()
    preserved = protected(True)
    os.umask(0o077)
    backup = Path(tempfile.mkdtemp(prefix='homeenergy-connection-backup.', dir='/home/shini'))
    backup.chmod(0o700)
    for name, metadata in originals.items():
        body = BASE.read(target(name))
        require(BASE.digest(body) == metadata['sha256'], 'Source changed before backup')
        path = backup / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        require(BASE.digest(BASE.read(path)) == metadata['sha256'], 'Backup verification failed')
    record = dict(files=originals, protected=preserved, services=running, feed_hash=feed_hash,
                  installed={name: BASE.digest(body) for name, body in bodies.items()})
    (backup / 'record.json').write_text(json.dumps(record, sort_keys=True) + '\n', encoding='utf-8')
    print('Private rollback backup: ' + str(backup), flush=True)
    try:
        require(protected(True) == preserved and service_state() == running, 'Baseline changed before activation')
        for name, meta in originals.items():
            require(BASE.fingerprint(target(name)) == meta, 'Target changed before installation: ' + name)
        for name in NEW_FILES:
            require(not target(name).exists() and not target(name).is_symlink(), 'New target appeared')
        require(not DROPIN.exists() and not DROPIN.is_symlink(), 'Health configuration appeared')
        if not DROPIN.parent.exists():
            DROPIN.parent.mkdir(mode=0o755)
            DROPIN.parent.chmod(0o755)
        require(DROPIN.parent.resolve() == DROPIN.parent, 'Unexpected drop-in path')
        for name, body in bodies.items():
            if name not in ('public/app.js', 'public/index.html'):
                write(name, body, originals.get(name, dict(uid=0, gid=0, mode=0o644)))
        BASE.nginx_reload()
        verify_assets(bodies)
        since = time.time()
        restart_exporter()
        verify_health(since)
        # Keep the previous app active while verifying the backend and routes.
        for name in ('public/app.js', 'public/index.html'):
            write(name, bodies[name], originals[name])
        expected = {name.removeprefix('public/'): BASE.digest(body) for name, body in bodies.items()
                    if name.startswith('public/')}
        BASE.verify_after_reload(expected, feed_hash)
        require(protected(True) == preserved, 'Protected files changed')
        after = service_state()
        require('ActiveState=active' in after['homeenergy'] and 'NRestarts=0' in after['homeenergy'],
                'Exporter is not stable')
        require(all(after[name] == running[name] for name in ('nginx', 'homeenergy-control', 'ssh')),
                'Unrelated service changed')
        (backup / 'verified.json').write_text(json.dumps(dict(verified_at=time.time(), services=after)) + '\n', encoding='utf-8')
    except BaseException:
        print('Activation failed; restoring the backed-up release.', flush=True)
        restore(backup)
        raise
    print('Connection-status redesign and health checks deployed on both HTTPS hostnames.', flush=True)
    print('Reports, closed history, History UI, reporting/staging access and controls are unchanged.', flush=True)
    print('Only Nginx was reloaded and the exporter restarted. No Home Assistant, VPN or device restart.', flush=True)
    print('No report publication, history backfill or deletion. Verify public pages before the GitHub commit.', flush=True)


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
        print('Read-only connection release preflight passed; nothing activated or restarted.', flush=True)


if __name__ == '__main__':
    main()
