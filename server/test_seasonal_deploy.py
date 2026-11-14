"""Synthetic deployment scope, routing and rollback checks; no network calls."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('seasonal_release', ROOT / 'deploy/activate-seasonal-theme.py')
RELEASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RELEASE)


class SeasonalReleaseTests(unittest.TestCase):
    def test_cannot_write_history_reports_controls_or_other_configuration(self):
        for name in ('history.html', 'history.js', 'app.js', 'theme.js', 'insights.js',
                     '../runtime/history.sqlite3', '/etc/ssh/sshd_config',
                     '/var/lib/homeenergy-reports/public/index.json'):
            with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, 'allowlist'):
                RELEASE.write(name, b'not written', {})
        self.assertEqual(set(RELEASE.FILES) - set(RELEASE.ASSETS), {'index.html'})

    def test_routes_are_exact_and_unrelated_configuration_is_preserved(self):
        original = (b'map "$status:$uri:$arg_v" $cache {\n'
                    b'"~^(200|304):/(styles\\.css|assets/solar-home\\.webp):[A-Za-z0-9_-]+$" immutable;\n}\n'
                    b'    location = /assets/solar-home.webp { try_files $uri =404; }\n'
                    b'    location / { return 404; }\n')
        changed = RELEASE.nginx_candidate(original).decode()
        for name in RELEASE.ASSETS:
            self.assertEqual(changed.count('location = /' + name + ' { try_files $uri =404; }'), 1)
        self.assertIn('    location / { return 404; }', changed)
        self.assertNotIn('location /themes', changed)
        with self.assertRaises(RuntimeError):
            RELEASE.nginx_candidate(changed.encode())
        with self.assertRaises(RuntimeError):
            RELEASE.nginx_candidate(b'unrecognized configuration')

    def test_unprivileged_activation_stops_before_reads_or_writes(self):
        with mock.patch.object(RELEASE.os, 'geteuid', return_value=1000, create=True), \
                mock.patch.object(RELEASE, 'bundle') as bundle:
            with self.assertRaisesRegex(RuntimeError, 'requires sudo'):
                RELEASE.activate()
            bundle.assert_not_called()

    def test_failed_route_verification_rolls_back_before_new_html_is_published(self):
        metadata = dict(mode=0o644, uid=0, gid=0, sha256=RELEASE.BASE.digest(b'old'))
        bodies = {name: b'new' for name in RELEASE.FILES}
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(RELEASE.os, 'geteuid', return_value=0, create=True), \
                mock.patch.object(RELEASE.os, 'umask'), \
                mock.patch.object(RELEASE.BASE, 'PUBLIC', Path(folder)), \
                mock.patch.object(RELEASE, 'bundle', return_value=({}, bodies, 'active', 'feed')), \
                mock.patch.object(RELEASE, 'protected', return_value={}), \
                mock.patch.object(RELEASE.BASE, 'services', return_value='active'), \
                mock.patch.object(RELEASE.BASE, 'fingerprint', return_value=metadata), \
                mock.patch.object(RELEASE.BASE, 'read', return_value=b'old'), \
                mock.patch.object(RELEASE, 'nginx_candidate', return_value=b'config'), \
                mock.patch.object(RELEASE.tempfile, 'mkdtemp', return_value=folder), \
                mock.patch.object(RELEASE, 'write') as write, \
                mock.patch.object(RELEASE, 'restore') as restore, \
                mock.patch.object(RELEASE.BASE, 'nginx_reload'), \
                mock.patch.object(RELEASE.time, 'sleep'), \
                mock.patch.object(RELEASE, 'verify_assets', side_effect=RuntimeError('route mismatch')):
            with self.assertRaisesRegex(RuntimeError, 'route mismatch'):
                RELEASE.activate()
            self.assertNotIn('index.html', [call.args[0] for call in write.call_args_list])
            restore.assert_called_once_with(Path(folder))


if __name__ == '__main__':
    unittest.main()
