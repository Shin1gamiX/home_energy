"""Synthetic Custom History deployment checks. No server, network or service calls."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('custom_release', ROOT / 'deploy/activate-history-custom-range.py')
RELEASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RELEASE)

NGINX = (b'map "$status:$uri:$arg_v" $cache {\n'
         b'"~^(200|304):/(styles\\.css|history-calendar\\.js|history\\.css):[A-Za-z0-9_-]+$" immutable;\n}\n'
         b'    location = /history-calendar.js { try_files $uri =404; }\n'
         b'    location = /api/energy { proxy_pass http://127.0.0.1:9000; }\n'
         b'    location / { return 404; }\n')


class CustomHistoryReleaseTests(unittest.TestCase):
    def test_allowlist_excludes_reports_history_storage_overview_and_controls(self):
        self.assertEqual(set(RELEASE.FILES), {'history-custom-range.css', 'history-custom-range.js',
                                             'history-calendar.js', 'history.js', 'history.html'})
        self.assertEqual(RELEASE.BASE.FILES, RELEASE.FILES)
        for name in ('index.html', 'app.js', 'history-cache.js', 'insights.js', 'connection-status.js',
                     '../runtime/history.sqlite3', '/etc/ssh/sshd_config',
                     '/var/lib/homeenergy-reports/public/index.json'):
            with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, 'allowlist'):
                RELEASE.write(name, b'never written', {})

    def test_nginx_changes_only_two_exact_routes_and_cache_entries(self):
        updated = RELEASE.nginx_candidate(NGINX)
        restored = updated.replace(b'|history-custom-range\\.css|history-custom-range\\.js|', b'|', 1)
        for name in RELEASE.ASSETS:
            route = ('\n    location = /' + name + ' { try_files $uri =404; }').encode()
            self.assertEqual(restored.count(route), 1)
            restored = restored.replace(route, b'', 1)
        self.assertEqual(restored, NGINX)
        for changed in (updated, b'unknown configuration', NGINX + NGINX):
            with self.assertRaisesRegex(RuntimeError, 'anchors changed'):
                RELEASE.nginx_candidate(changed)

    def test_metadata_is_passed_to_the_existing_atomic_writer(self):
        metadata = dict(mode=0o644, uid=1000, gid=1000)
        with mock.patch.object(RELEASE.BASE, 'atomic') as atomic:
            RELEASE.write('history.js', b'synthetic', metadata)
        atomic.assert_called_once_with(RELEASE.BASE.PUBLIC / 'history.js', b'synthetic', metadata)

    def test_protects_existing_reporting_state_and_health_configuration(self):
        with mock.patch.object(RELEASE.BASE, 'protected_state', return_value={'reports': 'unchanged'}) as saved, \
                mock.patch.object(RELEASE.BASE, 'fingerprint', return_value={'sha256': 'fixed'}):
            result = RELEASE.protected()
        saved.assert_called_once_with(True)
        self.assertEqual(result['reports'], 'unchanged')
        self.assertIn(str(Path('/etc/systemd/system/homeenergy.service')), result)
        self.assertIn(str(Path('/etc/systemd/system/homeenergy.service.d/30-connection-health.conf')), result)

    def test_unprivileged_activation_stops_before_any_changes(self):
        with mock.patch.object(RELEASE.os, 'geteuid', return_value=1000, create=True), \
                mock.patch.object(RELEASE, 'bundle') as bundle:
            with self.assertRaisesRegex(RuntimeError, 'requires sudo'):
                RELEASE.activate()
            bundle.assert_not_called()

    def exercise_activation(self, failure=None):
        metadata = dict(mode=0o644, uid=1000, gid=1000, mtime_ns=123, sha256=RELEASE.BASE.digest(b'old'))
        bodies = {name: b'new' for name in RELEASE.FILES}
        with tempfile.TemporaryDirectory() as folder:
            public, backup = Path(folder) / 'public', Path(folder) / 'backup'
            public.mkdir(); backup.mkdir()
            with mock.patch.object(RELEASE.os, 'geteuid', return_value=0, create=True), \
                    mock.patch.object(RELEASE.os, 'umask'), \
                    mock.patch.object(RELEASE.BASE, 'PUBLIC', public), \
                    mock.patch.object(RELEASE, 'bundle', return_value=(bodies, 'active', 'feed')), \
                    mock.patch.object(RELEASE, 'protected', return_value={'reports': 'unchanged'}), \
                    mock.patch.object(RELEASE.BASE, 'services', return_value='active'), \
                    mock.patch.object(RELEASE.BASE, 'fingerprint', return_value=metadata), \
                    mock.patch.object(RELEASE.BASE, 'read', return_value=b'old'), \
                    mock.patch.object(RELEASE, 'nginx_candidate', return_value=b'config'), \
                    mock.patch.object(RELEASE.tempfile, 'mkdtemp', return_value=str(backup)), \
                    mock.patch.object(RELEASE, 'write') as write, \
                    mock.patch.object(RELEASE, 'restore') as restore, \
                    mock.patch.object(RELEASE.BASE, 'nginx_reload') as reload_nginx, \
                    mock.patch.object(RELEASE.time, 'sleep'), \
                    mock.patch.object(RELEASE, 'verify_assets', side_effect=RuntimeError('routes failed') if failure == 'assets' else None), \
                    mock.patch.object(RELEASE.BASE, 'verify_after_reload', side_effect=RuntimeError('HTTP failed') if failure == 'http' else None):
                if failure:
                    with self.assertRaises(RuntimeError):
                        RELEASE.activate()
                    restore.assert_called_once_with(backup)
                else:
                    RELEASE.activate()
                    restore.assert_not_called()
                reload_nginx.assert_called_once()
                self.assertTrue((backup / 'record.json').is_file())
                for name in (*RELEASE.EXISTING, 'nginx.conf'):
                    self.assertEqual((backup / name).read_bytes(), b'old')
                return [call.args[0] for call in write.call_args_list]

    def test_routes_are_verified_before_replacing_existing_history(self):
        written = self.exercise_activation('assets')
        self.assertEqual(written, [*RELEASE.ASSETS, 'nginx.conf'])

    def test_html_is_last_and_failed_final_verification_rolls_back(self):
        written = self.exercise_activation('http')
        self.assertEqual(written, [*RELEASE.ASSETS, 'nginx.conf', *RELEASE.EXISTING])
        self.assertEqual(written[-1], 'history.html')

    def test_success_preserves_backups_and_only_reloads_nginx(self):
        self.exercise_activation()
        source = (ROOT / 'deploy/activate-history-custom-range.py').read_text(encoding='utf-8')
        self.assertNotIn('subprocess.run(', source)
        self.assertNotIn('restart_exporter(', source)


if __name__ == '__main__':
    unittest.main()
