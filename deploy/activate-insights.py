"""Guarded phase-A release: public UI and an empty feed, never an active reporter.

Build a manifest locally; stage only its fixed file list beside this helper.
--check and --verify are read-only. Only explicit --activate can change the server.
No shell commands, package installation, SSH settings, accounts or schedules change.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
from zoneinfo import ZoneInfo


PUBLIC = Path('/var/www/homeenergy/public')
CONFIG = Path('/etc/nginx/sites-available/homeenergy.conf')
MODULES = Path('/opt/homeenergy-reports')
STATE = Path('/var/lib/homeenergy-reports')
HOSTS = ('home.shin1gamix.com', 'home.shin1gamix.gr')
BASELINE = {
    'index.html': '13f973f095c36752a06628c5fd71db7997e70f70cd2cb1446d37c6ead8a9b37c',
    'nginx.conf': '9c3748b27de778b331d72d223c47c04a2845548bab54b0f680afa2ecb1ba40e1',
}
SOURCE_FILES = ('index.html', 'insights.js', 'insights.css', 'server/insights_analysis.py',
                'server/insights_i18n.py', 'server/insights_publish.py')
ASSETS = ('insights.js', 'insights.css')
MODULE_NAMES = ('insights_analysis.py', 'insights_i18n.py', 'insights_publish.py')
EMPTY_FEED = b'{"schema_version":2,"reports":[]}\n'
MAX_SOURCE_BYTES = 2_000_000
SECURITY_HEADERS = ('strict-transport-security', 'content-security-policy',
                    'x-content-type-options', 'x-frame-options', 'referrer-policy')
MAP_END = rb'|power-history\.css):[A-Za-z0-9_-]+$'
NEW_MAP_END = rb'|power-history\.css|insights\.js|insights\.css):[A-Za-z0-9_-]+$'
ANCHOR = b'    location = /power-history.css { try_files $uri =404; }\n'
ROUTES = b'''    location = /insights.js { try_files $uri =404; }
    location = /insights.css { try_files $uri =404; }
    location = /reports/index.json {
        alias /var/lib/homeenergy-reports/public/index.json;
        default_type application/json;
        limit_except GET { deny all; }
    }
'''


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def digest(body):
    return hashlib.sha256(body).hexdigest()


def canonical(path):
    require(path.is_absolute() and path.resolve() == path, 'Noncanonical or symlink path: ' + str(path))
    require(not path.is_symlink(), 'Symlink refused: ' + str(path))
    return path


def read_file(path, limit=MAX_SOURCE_BYTES):
    canonical(path)
    info = path.stat()
    require(stat.S_ISREG(info.st_mode) and info.st_size <= limit, 'Unexpected or oversized file: ' + str(path))
    with path.open('rb') as stream:
        body = stream.read(limit + 1)
    require(len(body) <= limit, 'File grew beyond limit: ' + str(path))
    return body


def absent(path):
    canonical(path)
    return not os.path.lexists(path)


def file_metadata(path):
    info = path.stat()
    return [info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)]


def asset_versions(html):
    versions = {}
    for name in ASSETS:
        matches = re.findall(rb'["\']' + re.escape(name.encode()) + rb'\?v=([A-Za-z0-9_-]+)["\']', html)
        require(len(matches) == 1, 'Expected one versioned Overview reference: ' + name)
        versions[name] = matches[0].decode('ascii')
    return versions


def build_manifest(repository):
    canonical(repository)
    bodies = {name: read_file(repository / name) for name in SOURCE_FILES}
    return {'manifest_version': 1, 'baseline': BASELINE,
            'files': {name: digest(body) for name, body in bodies.items()},
            'helper_sha256': digest(read_file(repository / 'deploy/activate-insights.py')),
            'versions': asset_versions(bodies['index.html'])}


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'Duplicate manifest field')
        result[key] = value
    return result


def read_bundle(source):
    canonical(source)
    require(source.parent == Path('/home/shini') and
            re.fullmatch(r'homeenergy-insights-release\.[A-Za-z0-9_-]+', source.name), 'Unexpected staging directory')
    manifest = json.loads(read_file(source / 'manifest.json', 16384), object_pairs_hook=unique_pairs)
    require(isinstance(manifest, dict) and set(manifest) ==
            {'manifest_version', 'baseline', 'files', 'helper_sha256', 'versions'}, 'Unexpected manifest fields')
    require(manifest['manifest_version'] == 1 and manifest['baseline'] == BASELINE, 'Wrong release baseline')
    require(isinstance(manifest['files'], dict) and set(manifest['files']) == set(SOURCE_FILES), 'Wrong release file list')
    require(all(isinstance(value, str) and re.fullmatch('[a-f0-9]{64}', value)
                for value in manifest['files'].values()), 'Invalid file hash')
    require(digest(read_file(source / 'activate-insights.py')) == manifest['helper_sha256'], 'Deployment helper changed')
    bodies = {name: read_file(source / name) for name in SOURCE_FILES}
    require(all(digest(body) == manifest['files'][name] for name, body in bodies.items()), 'Staged release changed')
    require(manifest['versions'] == asset_versions(bodies['index.html']), 'Asset versions do not match Overview')
    for name in MODULE_NAMES:
        # Compile without importing, executing prepare, or writing __pycache__.
        compile(bodies['server/' + name], name, 'exec', dont_inherit=True)
    return manifest, bodies


def candidate_config(original):
    require(original.count(ANCHOR) == original.count(MAP_END) == 1, 'Unexpected Nginx structure')
    require(b'/insights.js' not in original and b'/insights.css' not in original and
            b'/reports/' not in original, 'Report routes already exist; administrator review needed')
    result = original.replace(MAP_END, NEW_MAP_END, 1).replace(ANCHOR, ANCHOR + ROUTES, 1)
    require(result.replace(NEW_MAP_END, MAP_END, 1).replace(ROUTES, b'', 1) == original,
            'Out-of-scope Nginx change')
    return result


def reporter_absent():
    import pwd  # Linux-only; local manifest generation remains portable.
    try:
        pwd.getpwnam('homeenergy-reporter')
    except KeyError:
        return True
    return False


def services():
    return subprocess.check_output(['systemctl', 'show', 'nginx.service', 'homeenergy.service',
                                    'homeenergy-control.service', '-p', 'Id', '-p', 'MainPID',
                                    '-p', 'NRestarts', '-p', 'ActiveState'], text=True)


def protected_files():
    return {str(path): digest(read_file(path)) for path in PUBLIC.iterdir()
            if path.is_file() and path.name not in ('index.html', *ASSETS)}


def origin(host, route, method='GET'):
    require(host in HOSTS and route.startswith('/'), 'Unexpected verification URL')
    command = ['curl', '--silent', '--show-error', '--http1.1', '--noproxy', '*', '--max-time', '15',
               '--resolve', host + ':443:127.0.0.1', '--include', '--request', method, 'https://' + host + route]
    raw = subprocess.check_output(command)
    head, body = raw.split(b'\r\n\r\n', 1)
    headers = {key.lower().decode(): value.strip().decode() for line in head.split(b'\r\n')[1:]
               if b':' in line for key, value in [line.split(b':', 1)]}
    return int(head.split()[1]), headers, body


def security(headers, immutable=False):
    require(all(headers.get(name) for name in SECURITY_HEADERS), 'Missing security headers')
    require(headers['x-frame-options'] == 'DENY' and headers['x-content-type-options'] == 'nosniff' and
            headers['referrer-policy'] == 'no-referrer' and "script-src 'self'" in headers['content-security-policy'] and
            "frame-ancestors 'none'" in headers['content-security-policy'], 'Security policy changed')
    cache = headers.get('cache-control', '')
    require(('immutable' in cache and 'max-age=31536000' in cache) if immutable else 'no-store' in cache,
            'Unexpected cache policy')


def verify_http(index_hash, manifest, protected, deployed):
    for host in HOSTS:
        targets = {'/': (index_hash, 200), '/history.html': (protected[str(PUBLIC / 'history.html')], 200)}
        for route in ('/api/collector/status', '/nested/__homeenergy_insights_check__',
                      '/reports/reports.sqlite3', '/reports/publisher.lock', '/reports/public/index.json',
                      '/insights_publish.py'):
            targets[route] = (protected[str(PUBLIC / '404.html')], 404)
        if deployed:
            targets['/reports/index.json'] = (digest(EMPTY_FEED), 200)
            for name in ASSETS:
                targets['/' + name + '?v=' + manifest['versions'][name]] = (manifest['files'][name], 200)
        else:
            targets['/reports/index.json'] = (protected[str(PUBLIC / '404.html')], 404)
        for route, (expected_hash, expected_status) in targets.items():
            status, headers, body = origin(host, route)
            require(status == expected_status and digest(body) == expected_hash, 'HTTP mismatch: ' + host + route)
            security(headers, immutable='?v=' in route)
            if route.startswith('/insights.js?'):
                require('javascript' in headers.get('content-type', ''), 'Wrong JavaScript MIME type')
            if route.startswith('/insights.css?'):
                require('text/css' in headers.get('content-type', ''), 'Wrong CSS MIME type')
            if route == '/reports/index.json' and deployed:
                require('application/json' in headers.get('content-type', ''), 'Wrong report MIME type')
        for route, field in (('/history/index.json', 'days'), ('/api/energy', 'generated_at')):
            status, headers, body = origin(host, route)
            require(status == 200 and field in json.loads(body), 'Existing data endpoint unavailable')
            security(headers)
        if deployed:
            status, headers, _ = origin(host, '/reports/index.json', 'POST')
            require(status in (403, 405), 'The public feed must be read-only')
            security(headers)
        print(host + ': Overview, History, security headers, private 404 and report route verified.', flush=True)


def preflight(manifest):
    require(os.name == 'posix', 'Server checks require the reviewed Linux host')
    for path in (PUBLIC, CONFIG, MODULES.parent, STATE.parent):
        canonical(path)
    require(PUBLIC.is_dir() and MODULES.parent.is_dir() and STATE.parent.is_dir(), 'Missing deployment parent')
    require(reporter_absent(), 'Reporter account already exists; phase-A assumptions no longer hold')
    require(absent(MODULES) and absent(STATE), 'Report directories already exist; refusing to overwrite')
    require(all(absent(PUBLIC / name) for name in ASSETS), 'Insights assets already exist')
    original = read_file(CONFIG)
    require(digest(original) == BASELINE['nginx.conf'] and
            digest(read_file(PUBLIC / 'index.html')) == BASELINE['index.html'], 'Production baseline changed')
    ZoneInfo('Europe/Athens')
    candidate = candidate_config(original)
    running = services()
    require(running.count('ActiveState=active') == 3, 'A required existing service is inactive')
    protected = protected_files()
    verify_http(BASELINE['index.html'], manifest, protected, False)
    return original, candidate, running, protected


def atomic(target, body, metadata):
    allowed = {CONFIG, PUBLIC / 'index.html', STATE / 'public/index.json'}
    allowed.update(PUBLIC / name for name in ASSETS)
    allowed.update(MODULES / name for name in MODULE_NAMES)
    require(target in allowed, 'Write outside deployment allowlist refused')
    canonical(target)
    fd, pending = tempfile.mkstemp(prefix='.insights-release-', dir=target.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchown(stream.fileno(), metadata[0], metadata[1])
            os.fchmod(stream.fileno(), metadata[2])
        os.replace(pending, target)
        require(read_file(target) == body and file_metadata(target) == metadata, 'Deployment readback mismatch')
    finally:
        if os.path.exists(pending):
            os.unlink(pending)


def make_directory(path, mode):
    require(path in (MODULES, STATE, STATE / 'public') and absent(path), 'Directory creation refused')
    path.mkdir(mode=mode)
    os.chown(path, 0, 0)
    os.chmod(path, mode)


def restore_original(target, original, replacement, metadata):
    current = read_file(target)
    if current == original:
        return
    require(current == replacement, 'Later change detected; manual rollback required: ' + str(target))
    atomic(target, original, metadata)


def rollback_frontend(original_index, original_config, candidate, bodies, index_meta, config_meta,
                      index_attempted, config_attempted, manifest, protected):
    """Try both restorations even when one detects an unrelated later change."""
    failures = []
    if index_attempted:
        try:
            restore_original(PUBLIC / 'index.html', original_index, bodies['index.html'], index_meta)
        except BaseException as error:
            failures.append('Overview: ' + str(error))
    if config_attempted:
        try:
            restore_original(CONFIG, original_config, candidate, config_meta)
            subprocess.run(['nginx', '-t'], check=True)
            subprocess.run(['systemctl', 'reload', 'nginx'], check=True)
        except BaseException as error:
            failures.append('Nginx: ' + str(error))
    try:
        verify_http(BASELINE['index.html'], manifest, protected, False)
    except BaseException as error:
        failures.append('Previous HTTP routes: ' + str(error))
    require(not failures, 'Automatic rollback incomplete; administrator recovery required. ' + '; '.join(failures))


def activate(manifest, bodies):
    require(os.name == 'posix' and os.geteuid() == 0, 'Activation requires explicit sudo')
    os.umask(0o077)
    original, candidate, running, protected = preflight(manifest)
    subprocess.run(['nginx', '-t'], check=True)
    original_index = read_file(PUBLIC / 'index.html')
    require(digest(original_index) == BASELINE['index.html'] and read_file(CONFIG) == original,
            'Production changed after preflight')
    index_meta, config_meta = file_metadata(PUBLIC / 'index.html'), file_metadata(CONFIG)
    backup = Path(tempfile.mkdtemp(prefix='homeenergy-insights-backup.', dir='/home/shini'))
    os.chown(backup, 0, 0)
    os.chmod(backup, 0o700)
    (backup / 'index.html').write_bytes(original_index)
    (backup / 'homeenergy.conf').write_bytes(original)
    (backup / 'manifest.json').write_text(json.dumps({'release': manifest, 'index_metadata': index_meta,
        'nginx_metadata': config_meta, 'protected': protected, 'services': running}, indent=2), encoding='utf-8')
    require(read_file(backup / 'index.html') == original_index and read_file(backup / 'homeenergy.conf') == original,
            'Rollback backup verification failed')
    print('Private rollback backup: ' + str(backup), flush=True)
    # Mark replacement attempts before atomic(), including possible readback failures.
    config_attempted = index_attempted = False
    try:
        require(reporter_absent() and absent(MODULES) and absent(STATE), 'Provisioning changed during preflight')
        make_directory(MODULES, 0o755)
        make_directory(STATE, 0o711)
        make_directory(STATE / 'public', 0o755)
        for name in MODULE_NAMES:
            atomic(MODULES / name, bodies['server/' + name], [0, 0, 0o644])
        atomic(STATE / 'public/index.json', EMPTY_FEED, [0, 0, 0o644])
        for name in ASSETS:
            require(absent(PUBLIC / name), 'A later frontend asset exists')
            atomic(PUBLIC / name, bodies[name], [index_meta[0], index_meta[1], 0o644])
        require(read_file(CONFIG) == original, 'Nginx changed during preflight')
        config_attempted = True
        atomic(CONFIG, candidate, config_meta)
        subprocess.run(['nginx', '-t'], check=True)
        subprocess.run(['systemctl', 'reload', 'nginx'], check=True)
        verify_http(BASELINE['index.html'], manifest, protected, True)
        require(read_file(PUBLIC / 'index.html') == original_index, 'Overview changed during preflight')
        index_attempted = True
        atomic(PUBLIC / 'index.html', bodies['index.html'], index_meta)
        verify_http(manifest['files']['index.html'], manifest, protected, True)
        require(read_file(CONFIG) == candidate and protected_files() == protected and services() == running,
                'Unrelated configuration, files or service state changed')
        verify_local(manifest)
    except BaseException:
        try:
            rollback_frontend(original_index, original, candidate, bodies, index_meta, config_meta,
                              index_attempted, config_attempted, manifest, protected)
        except BaseException:
            print('Rollback needs administrator attention. Private backup: ' + str(backup), flush=True)
            raise
        print('Previous Overview/Nginx and HTTP routes restored. New inactive assets and report directories are retained for review.', flush=True)
        print('Do not rerun activation until an administrator reviews the retained paths.', flush=True)
        raise
    print('Phase A active on both HTTPS hostnames: translated UI and empty report feed. Only Nginx was reloaded.')
    print('No reporting account, SSH key, broker, schedule, database or live report was created.')
    print('No exporter/control-service, dongle or inverter restart; no history-data changes.')


def verify_local(manifest):
    require(os.name == 'posix' and os.geteuid() == 0, 'Private phase-A verification requires sudo (read-only)')
    require(reporter_absent(), 'Phase-A verification expects no reporting account')
    for path, mode in ((MODULES, 0o755), (STATE, 0o711), (STATE / 'public', 0o755)):
        canonical(path)
        require(path.is_dir() and file_metadata(path) == [0, 0, mode], 'Unexpected report directory ownership')
    require({path.name for path in MODULES.iterdir()} == set(MODULE_NAMES), 'Unexpected files in publisher directory')
    require({path.name for path in STATE.iterdir()} == {'public'}, 'Reporter state was unexpectedly initialized')
    require({path.name for path in (STATE / 'public').iterdir()} == {'index.json'}, 'Unexpected public report files')
    for name in MODULE_NAMES:
        require(digest(read_file(MODULES / name)) == manifest['files']['server/' + name] and
                file_metadata(MODULES / name) == [0, 0, 0o644], 'Publisher module mismatch')
    require(read_file(STATE / 'public/index.json') == EMPTY_FEED and
            file_metadata(STATE / 'public/index.json') == [0, 0, 0o644], 'Expected untouched empty feed')
    for name in ('index.html', *ASSETS):
        require(digest(read_file(PUBLIC / name)) == manifest['files'][name], 'Frontend release mismatch')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    choices = parser.add_mutually_exclusive_group(required=True)
    choices.add_argument('--build-manifest', type=Path, metavar='ABSOLUTE_REPOSITORY')
    choices.add_argument('--check', action='store_true')
    choices.add_argument('--activate', action='store_true')
    choices.add_argument('--verify', action='store_true')
    options = parser.parse_args()
    if options.build_manifest:
        print(json.dumps(build_manifest(options.build_manifest), indent=2))
        return
    require(os.name == 'posix', 'Production operations require Linux')
    manifest, bodies = read_bundle(Path(__file__).resolve().parent)
    if options.activate:
        activate(manifest, bodies)
    elif options.check:
        preflight(manifest)
        print('Read-only preflight passed. No files, accounts, services or devices were changed.')
    else:
        verify_local(manifest)
        verify_http(manifest['files']['index.html'], manifest, protected_files(), True)
        print('Phase-A release verified; publishing remains unprovisioned.')


if __name__ == '__main__':
    main()
