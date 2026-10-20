"""Synthetic navigation deployment tests; no production or network calls."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('navigation_release', ROOT / 'deploy/activate-navigation.py')
RELEASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RELEASE)


class NavigationReleaseTests(unittest.TestCase):
    def test_only_three_frontend_files_are_writable(self):
        self.assertEqual(RELEASE.FILES, ('interface.css', 'index.html', 'history.html'))
        self.assertEqual(RELEASE.BASE.FILES, RELEASE.FILES)
        for name in ('app.js', 'history.js', 'collector-control.js', '../runtime/history.sqlite3',
                     '/etc/nginx/sites-available/homeenergy.conf', '../reports/index.json'):
            with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, 'allowlist'):
                RELEASE.write(name, b'not written', {})

    def test_preserves_metadata_via_existing_atomic_writer(self):
        metadata = dict(mode=0o644, uid=0, gid=0)
        with mock.patch.object(RELEASE.BASE, 'atomic') as atomic:
            RELEASE.write('interface.css', b'synthetic', metadata)
        atomic.assert_called_once_with(RELEASE.BASE.PUBLIC / 'interface.css', b'synthetic', metadata)

    def test_protects_nginx_and_exporter_unit(self):
        with mock.patch.object(RELEASE.BASE, 'protected_state', return_value={'reports': 'unchanged'}) as saved, \
                mock.patch.object(RELEASE.BASE, 'fingerprint', return_value={'sha256': 'synthetic'}):
            result = RELEASE.protected()
        saved.assert_called_once_with(True)
        self.assertIn(str(RELEASE.BASE.NGINX), result)
        self.assertIn(str(Path('/etc/systemd/system/homeenergy.service')), result)
        self.assertEqual(result['reports'], 'unchanged')

    def test_failed_verification_restores_without_service_actions(self):
        metadata = dict(mode=0o644, uid=0, gid=0, sha256=RELEASE.BASE.digest(b'old'))
        originals = {name: metadata for name in RELEASE.FILES}
        bodies = {name: b'new' for name in RELEASE.FILES}
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(RELEASE.os, 'geteuid', return_value=0, create=True), \
                mock.patch.object(RELEASE.os, 'umask'), \
                mock.patch.object(RELEASE, 'bundle', return_value=(bodies, originals, 'active', 'feed')), \
                mock.patch.object(RELEASE, 'protected', return_value={}), \
                mock.patch.object(RELEASE.BASE, 'services', return_value='active'), \
                mock.patch.object(RELEASE.BASE, 'fingerprint', return_value=metadata), \
                mock.patch.object(RELEASE.BASE, 'read', return_value=b'old'), \
                mock.patch.object(RELEASE.tempfile, 'mkdtemp', return_value=folder), \
                mock.patch.object(RELEASE, 'write') as write, \
                mock.patch.object(RELEASE, 'restore') as restore, \
                mock.patch.object(RELEASE.BASE, 'verify_http', side_effect=RuntimeError('HTTP mismatch')):
            with self.assertRaisesRegex(RuntimeError, 'HTTP mismatch'):
                RELEASE.activate()
            self.assertEqual([call.args[0] for call in write.call_args_list], list(RELEASE.FILES))
            restore.assert_called_once_with(Path(folder))

    def test_no_mutating_service_or_configuration_calls(self):
        source = (ROOT / 'deploy/activate-navigation.py').read_text(encoding='utf-8')
        for forbidden in ('nginx_reload(', 'restart_exporter(', 'subprocess.run(', 'nginx_candidate('):
            self.assertNotIn(forbidden, source)


if __name__ == '__main__':
    unittest.main()
