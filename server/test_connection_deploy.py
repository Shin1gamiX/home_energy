"""Synthetic connection release tests; no network, equipment or service actions."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('connection_release', ROOT / 'deploy/activate-connection-status.py')
RELEASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RELEASE)
CONFIG = dict(container='homeassistant', router='10.8.0.5', interface='wg0', port=8899)
NGINX = (b'map original { "~(styles\\.css|app\\.js|history\\.js)" immutable; }\n'
         b'    location = /app.js { try_files $uri =404; }\n'
         b'    location = /history.js { try_files $uri =404; }\n')


class ConnectionDeploymentTests(unittest.TestCase):
    def test_narrow_write_allowlist(self):
        self.assertEqual(set(RELEASE.FILES), {
            'public/connection-status.css', 'public/connection-status.js', 'public/app.js',
            'public/index.html', 'server/export_energy.py', 'server/connection_health.py',
            'server/connection_probe.py'})
        for name in ('public/history.html', 'server/history_store.py', 'runtime/history.sqlite3',
                     'public/../runtime/energy.json', '/etc/ssh/sshd_config', 'reports/index.json'):
            with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, 'allowlist'):
                RELEASE.write(name, b'not written', {})

    def test_adds_only_two_asset_routes_and_cache_patterns(self):
        updated = RELEASE.nginx_candidate(NGINX)
        self.assertIn(b'|app\\.js|connection-status\\.js|connection-status\\.css|history\\.js', updated)
        for name in ('connection-status.js', 'connection-status.css'):
            route = ('    location = /' + name + ' { try_files $uri =404; }').encode()
            self.assertEqual(updated.count(route), 1)
            updated = updated.replace(b'\n' + route, b'')
        updated = updated.replace(b'|connection-status\\.js|connection-status\\.css', b'')
        self.assertEqual(updated, NGINX)
        for invalid in (NGINX + NGINX, NGINX.replace(b'/app.js', b'/renamed.js'),
                        RELEASE.nginx_candidate(NGINX)):
            with self.assertRaises(RuntimeError):
                RELEASE.nginx_candidate(invalid)

    def test_dropin_sets_only_four_fixed_environment_values(self):
        lines = RELEASE.dropin_candidate(CONFIG).decode().splitlines()
        self.assertEqual(lines[0], '[Service]')
        self.assertEqual(len(lines), 5)
        self.assertTrue(all(line.startswith('Environment="ENERGY_HEALTH_') for line in lines[1:]))
        for field, bad in (('container', 'ha\nExecStart=/bin/sh'), ('interface', 'wg0;reboot'),
                           ('router', '10.8.0.5;reboot'), ('port', True), ('port', 65536)):
            with self.subTest(field=field), self.assertRaises((RuntimeError, ValueError)):
                RELEASE.dropin_candidate({**CONFIG, field: bad})
        with self.assertRaises(RuntimeError):
            RELEASE.dropin_candidate({**CONFIG, 'extra': 'unexpected'})

    def test_preserves_existing_database_path(self):
        generic = b"'/srv/homeassistant/config/home-assistant_v2.db'"
        existing = b"'/home/shini/docker/homeassistant/config/home-assistant_v2.db'"
        candidate, installed = b'DATABASE=' + generic, b'DATABASE=' + existing
        self.assertEqual(RELEASE.exporter_for_installation(candidate, installed), installed)
        for bad in (b'', candidate * 2, installed):
            with self.assertRaises(RuntimeError):
                RELEASE.exporter_for_installation(bad, installed)

    def test_requires_new_healthy_observations_and_reading_after_restart(self):
        now = 1800000000
        checks = {key: dict(state='up', checked_at=now) for key in ('vpn', 'dongle', 'collector', 'inverter')}
        energy = dict(health=dict(version=1, checks=checks), status='live', updated_at=now)
        self.assertTrue(RELEASE.health_ready(energy, now - 10, now))
        for key in checks:
            for invalid in ({'state': 'unknown'}, {'checked_at': now - 100},
                            {'checked_at': now + 31}, {'checked_at': True}):
                altered = {**energy, 'health': dict(version=1, checks={**checks, key: {**checks[key], **invalid}})}
                self.assertFalse(RELEASE.health_ready(altered, now - 10, now))
        for invalid in ({'updated_at': now - 20}, {'updated_at': float('nan')}, {'status': 'stale'}):
            self.assertFalse(RELEASE.health_ready({**energy, **invalid}, now - 10, now))

    def test_restart_is_limited_to_exporter_and_unit_reload(self):
        with mock.patch.object(RELEASE.subprocess, 'run') as run:
            RELEASE.restart_exporter()
        self.assertEqual([call.args[0] for call in run.call_args_list], [
            ['/usr/bin/systemctl', 'daemon-reload'],
            ['/usr/bin/systemctl', 'restart', 'homeenergy.service']])

    def test_reports_history_and_service_restrictions_remain_protected(self):
        paths = {str(RELEASE.ROOT / 'server/export_energy.py'): 'old',
                 str(RELEASE.ROOT / 'server/history_store.py'): 'recorder',
                 str(RELEASE.BASE.FEED): 'reports', 'report-database': 'unchanged'}
        with mock.patch.object(RELEASE.BASE, 'protected_state', return_value=paths), \
                mock.patch.object(RELEASE.BASE, 'fingerprint', return_value='unit'), \
                tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(RELEASE, 'DROPIN', Path(folder) / 'health.conf'):
            saved = RELEASE.protected(True)
        self.assertNotIn(str(RELEASE.ROOT / 'server/export_energy.py'), saved)
        self.assertEqual(saved[str(RELEASE.ROOT / 'server/history_store.py')], 'recorder')
        self.assertEqual(saved[str(RELEASE.BASE.FEED)], 'reports')
        self.assertEqual(saved[str(RELEASE.UNIT)], 'unit')

    def test_failed_health_check_rolls_back_before_switching_html(self):
        old = b'original'
        meta = dict(uid=0, gid=0, mode=0o644, sha256=RELEASE.BASE.digest(old), mtime_ns=1)
        originals = {name: meta for name in (*RELEASE.FILES, 'nginx.conf') if name not in RELEASE.NEW_FILES}
        bodies = {name: b'candidate' for name in (*RELEASE.FILES, *RELEASE.CONFIG_NAMES)}
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(RELEASE.os, 'geteuid', return_value=0, create=True), \
                mock.patch.object(RELEASE.os, 'umask'), \
                mock.patch.object(RELEASE, 'bundle', return_value=(bodies, originals, {}, 'feed')), \
                mock.patch.object(RELEASE, 'protected', return_value={'reports': 'same'}), \
                mock.patch.object(RELEASE, 'service_state', return_value={}), \
                mock.patch.object(RELEASE.BASE, 'read', return_value=old), \
                mock.patch.object(RELEASE.BASE, 'fingerprint', return_value=meta), \
                mock.patch.object(RELEASE.tempfile, 'mkdtemp', return_value=folder), \
                mock.patch.object(RELEASE, 'DROPIN', Path(folder) / 'unit/30-connection-health.conf'), \
                mock.patch.object(RELEASE, 'ROOT', Path(folder) / 'installation'), \
                mock.patch.object(RELEASE, 'write') as write, \
                mock.patch.object(RELEASE.BASE, 'nginx_reload'), \
                mock.patch.object(RELEASE, 'verify_assets'), \
                mock.patch.object(RELEASE, 'restart_exporter') as restart, \
                mock.patch.object(RELEASE, 'verify_health', side_effect=RuntimeError('health not ready')), \
                mock.patch.object(RELEASE, 'restore') as restore:
            with self.assertRaisesRegex(RuntimeError, 'health not ready'):
                RELEASE.activate()
            restore.assert_called_once_with(Path(folder))
            restart.assert_called_once()
            self.assertNotIn('public/index.html', [call.args[0] for call in write.call_args_list])
            self.assertNotIn('public/app.js', [call.args[0] for call in write.call_args_list])
            self.assertEqual((Path(folder) / 'public/index.html').read_bytes(), old)


if __name__ == '__main__':
    unittest.main()
