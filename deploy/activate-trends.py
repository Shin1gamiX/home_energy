"""Backup-first daily comparisons release. Run --check before --activate.

Only named frontend files, the summary exporter module and exact asset routes
are changed. Raw history and reports are never replaced, even during rollback.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import tempfile
import time
from contextlib import closing
from datetime import datetime
from zoneinfo import ZoneInfo

SOURCE = Path(__file__).resolve().parent
ROOT = Path('/var/www/homeenergy')
FILES = ('public/trends-data.js', 'public/trends.js', 'public/trends.css',
         'server/energy_summary.py', 'public/index.html')
NEW = set(FILES[:3])
SPEC = importlib.util.spec_from_file_location('trends_release_checks', SOURCE / 'activate-interface.py')
BASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BASE)
BASE.FILES = tuple(name.removeprefix('public/') for name in FILES if name.startswith('public/'))
require = BASE.require


def nginx_candidate(body):
    text = body.decode()
    anchor = '    location = /interface.css { try_files $uri =404; }'
    marker = r'|interface\.css|assets/solar-home\.webp):'
    require(text.count(anchor) == 1 and text.count(marker) == 1 and 'trends.' not in text,
            'Nginx asset anchors differ; inspect before activation')
    text = text.replace(marker, r'|interface\.css|trends\.js|trends-data\.js|trends\.css|assets/solar-home\.webp):', 1)
    routes = '\n'.join('    location = /' + name.removeprefix('public/') + ' { try_files $uri =404; }' for name in FILES[:3])
    return text.replace(anchor, anchor + '\n' + routes, 1).encode()


def protected(private=True):
    result = BASE.protected_state(private)
    result.pop(str(ROOT / 'server/energy_summary.py'), None)
    result['/etc/systemd/system/homeenergy.service'] = BASE.fingerprint(Path('/etc/systemd/system/homeenergy.service'))
    return result


def services():
    return {name: subprocess.check_output(['/usr/bin/systemctl', 'show', name,
            '-p', 'MainPID', '-p', 'NRestarts', '-p', 'ActiveState'], text=True)
            for name in ('nginx', 'homeenergy', 'homeenergy-control')}


def write(name, body, metadata):
    require(name in FILES or name == 'nginx.conf', 'Target outside trends allowlist')
    path = BASE.NGINX if name == 'nginx.conf' else ROOT / name
    require(path.parent.resolve() == path.parent and not path.is_symlink(), 'Unexpected target path')
    fd, pending = tempfile.mkstemp(prefix='.trends-release-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(body)
            os.fchmod(output.fileno(), metadata['mode'])
            os.fchown(output.fileno(), metadata['uid'], metadata['gid'])
            output.flush()
            os.fsync(output.fileno())
        os.replace(pending, path)
    finally:
        if os.path.exists(pending):
            os.unlink(pending)
    require(BASE.digest(BASE.read(path)) == BASE.digest(body), 'Installed bytes differ')


def bundle():
    require(SOURCE.resolve() == SOURCE and SOURCE.parent == Path('/home/shini')
            and SOURCE.name.startswith('homeenergy-trends-release.'), 'Unexpected release path')
    manifest = json.loads(BASE.read(SOURCE / 'manifest.json'))
    require(set(manifest['files']) == set(FILES), 'Release allowlist differs')
    require(BASE.digest(BASE.read(Path(__file__))) == manifest['helper'], 'Activation helper changed')
    require(BASE.digest(BASE.read(SOURCE / 'activate-interface.py')) == manifest['verifier'], 'Verifier changed')
    require(BASE.digest(BASE.read(BASE.NGINX)) == manifest['nginx'], 'Nginx baseline changed')
    bodies = {}
    for name in FILES:
        body = BASE.read(SOURCE / name)
        require(BASE.digest(body) == manifest['files'][name]['after'], 'Candidate changed: ' + name)
        path = ROOT / name
        if name in NEW:
            require(manifest['files'][name]['before'] is None and not path.exists() and not path.is_symlink(), 'New target already exists')
        else:
            require(BASE.digest(BASE.read(path)) == manifest['files'][name]['before'], 'Installed baseline changed: ' + name)
        bodies[name] = body
    nginx_candidate(BASE.read(BASE.NGINX))
    state = services()
    require(all('ActiveState=active' in value for value in state.values()), 'Required service inactive')
    BASE.verify_http({}, BASE.digest(BASE.read(BASE.FEED)))
    return bodies, state


def verify_trends():
    today = datetime.now(ZoneInfo('Europe/Athens')).strftime('%Y-%m-%d')
    for attempt in range(30):
        ready = True
        for host in BASE.HOSTS:
            status, headers, body = BASE.origin(host, '/history/index.json')
            require(status == 200, 'History index unavailable')
            BASE.check_headers(headers)
            trends = json.loads(body).get('energy_summary', {}).get('trends', {})
            ready &= (trends.get('version') == 1 and trends.get('completed_before') == today
                      and len(trends.get('days', [])) == 60)
        if ready:
            print('Both HTTPS hostnames: 60 completed Athens days and cached battery peaks verified.', flush=True)
            return
        if attempt < 29:
            time.sleep(1)
    raise RuntimeError('Completed-day trend index was not generated in time')


def restart_exporter():
    subprocess.run(['/usr/bin/systemctl', 'restart', 'homeenergy.service'], check=True, timeout=30)


def restore(backup):
    require(backup.resolve() == backup and backup.parent == Path('/home/shini')
            and backup.name.startswith('homeenergy-trends-backup.'), 'Unexpected backup path')
    require(backup.stat().st_uid == 0 and stat.S_IMODE(backup.stat().st_mode) == 0o700, 'Backup is not root-private')
    record = json.loads(BASE.read(backup / 'record.json'))
    require(set(record['files']) == set(FILES) - NEW | {'nginx.conf'}, 'Backup allowlist differs')
    bodies = {name: BASE.read(backup / name) for name in record['files']}
    for name, body in bodies.items():
        require(BASE.digest(body) == record['files'][name]['sha256'], 'Backup bytes differ')
    for name, body in bodies.items():
        write(name, body, record['files'][name])
    BASE.nginx_reload()
    restart_exporter()
    BASE.verify_after_reload({}, record['feed_hash'])
    require(protected() == record['protected'], 'Protected files differ after rollback')
    print('Original code/routes restored. New unused assets/cache and all readings, reports and backups retained.')


def activate():
    require(os.geteuid() == 0, 'Activation requires sudo')
    bodies, running = bundle()
    preserved = protected()
    originals = {name: BASE.fingerprint(ROOT / name) for name in FILES if name not in NEW}
    originals['nginx.conf'] = BASE.fingerprint(BASE.NGINX)
    os.umask(0o077)
    backup = Path(tempfile.mkdtemp(prefix='homeenergy-trends-backup.', dir='/home/shini'))
    backup.chmod(0o700)
    for name, meta in originals.items():
        body = BASE.read(BASE.NGINX if name == 'nginx.conf' else ROOT / name)
        require(BASE.digest(body) == meta['sha256'], 'Source changed during backup')
        path = backup / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        require(BASE.digest(BASE.read(path)) == meta['sha256'], 'Backup bytes differ')
    # SQLite's backup API captures a consistent snapshot while recording continues.
    with closing(sqlite3.connect((ROOT / 'runtime/history.sqlite3').as_uri() + '?mode=ro', uri=True)) as source, \
            closing(sqlite3.connect(backup / 'history.sqlite3')) as target:
        source.backup(target)
        require(target.execute('PRAGMA quick_check').fetchone()[0] == 'ok', 'History backup failed integrity check')
    (backup / 'history-index.json').write_bytes(BASE.read(ROOT / 'runtime/history/index.json'))
    record = dict(files=originals, protected=preserved, services=running, feed_hash=BASE.digest(BASE.read(BASE.FEED)))
    (backup / 'record.json').write_text(json.dumps(record, sort_keys=True), encoding='utf-8')
    print('Private rollback backup: ' + str(backup), flush=True)
    try:
        require(protected() == preserved and services() == running, 'Protected state changed before activation')
        for name in FILES:
            if name == 'public/index.html':
                continue
            if name not in NEW:
                require(BASE.fingerprint(ROOT / name) == originals[name], 'Baseline changed before replacement')
            write(name, bodies[name], originals.get(name, dict(uid=0, gid=0, mode=0o644)))
        require(BASE.fingerprint(BASE.NGINX) == originals['nginx.conf'], 'Nginx baseline changed')
        write('nginx.conf', nginx_candidate(BASE.read(BASE.NGINX)), originals['nginx.conf'])
        BASE.nginx_reload()
        restart_exporter()
        verify_trends()
        require(BASE.fingerprint(ROOT / 'public/index.html') == originals['public/index.html'], 'Overview changed before replacement')
        write('public/index.html', bodies['public/index.html'], originals['public/index.html'])
        BASE.verify_after_reload({name.removeprefix('public/'): BASE.digest(body) for name, body in bodies.items() if name.startswith('public/')}, record['feed_hash'])
        require(protected() == preserved, 'Protected files changed')
        after = services()
        for service in ('nginx', 'homeenergy-control'):
            require(after[service] == running[service], 'Unrelated service PID/state changed')
        require('ActiveState=active' in after['homeenergy'], 'Exporter inactive')
    except BaseException:
        print('Activation failed; restoring original code/routes. Evidence retained.', flush=True)
        restore(backup)
        raise
    print('Energy trends activated. Only Nginx reloaded and Home Energy exporter restarted.')
    print('Raw history, existing reports, reporting/staging access and controls preserved. No device restart.')
    print('Verify public Cloudflare pages before pushing the separate feature commits.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--check', action='store_true')
    group.add_argument('--activate', action='store_true')
    group.add_argument('--rollback', type=Path)
    args = parser.parse_args()
    if args.rollback:
        require(os.geteuid() == 0, 'Rollback requires sudo')
        restore(args.rollback)
    elif args.activate:
        activate()
    else:
        bundle()
        print('Read-only trends preflight passed. No files, services or data changed.')


if __name__ == '__main__':
    main()
