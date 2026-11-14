"""Seasonal hotfix scope and rollback tests with no production access."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('seasonal_update', ROOT / 'deploy/activate-seasonal-update.py')
RELEASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RELEASE)


class SeasonalUpdateTests(unittest.TestCase):
    def test_unrelated_files_are_rejected_before_writing(self):
        for name in ('history.html', 'history.js', 'app.js', 'themes/christmas/theme.js',
                     'nginx.conf', '../runtime/history.sqlite3', '/var/lib/homeenergy-reports/public/index.json'):
            with self.subTest(name=name), mock.patch.object(RELEASE.BASE, 'atomic') as atomic:
                with self.assertRaisesRegex(RuntimeError, 'allowlist'):
                    RELEASE.write(name, b'not written', {})
                atomic.assert_not_called()

    def test_unprivileged_activation_cannot_start(self):
        with mock.patch.object(RELEASE.os, 'geteuid', return_value=1000, create=True), \
                mock.patch.object(RELEASE, 'bundle') as bundle:
            with self.assertRaisesRegex(RuntimeError, 'requires sudo'):
                RELEASE.activate()
            bundle.assert_not_called()

    def test_failed_verification_restores_backed_up_release(self):
        meta = dict(mode=0o644, uid=0, gid=0, sha256=RELEASE.BASE.digest(b'old'))
        originals = {name: dict(meta) for name in RELEASE.FILES}
        bodies = {name: b'new' for name in RELEASE.FILES}
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(RELEASE.os, 'geteuid', return_value=0, create=True), \
                mock.patch.object(RELEASE.os, 'umask'), \
                mock.patch.object(RELEASE, 'bundle', return_value=(bodies, originals, 'active', 'feed')), \
                mock.patch.object(RELEASE, 'protected', return_value={}), \
                mock.patch.object(RELEASE.BASE, 'services', return_value='active'), \
                mock.patch.object(RELEASE.BASE, 'fingerprint', return_value=meta), \
                mock.patch.object(RELEASE.BASE, 'read', return_value=b'old'), \
                mock.patch.object(RELEASE.tempfile, 'mkdtemp', return_value=folder), \
                mock.patch.object(RELEASE, 'write') as write, \
                mock.patch.object(RELEASE, 'restore') as restore, \
                mock.patch.object(RELEASE, 'verify', side_effect=RuntimeError('HTTP mismatch')):
            with self.assertRaisesRegex(RuntimeError, 'HTTP mismatch'):
                RELEASE.activate()
            self.assertEqual([call.args[0] for call in write.call_args_list], list(RELEASE.FILES))
            self.assertEqual(write.call_args_list[-1].args[0], 'index.html')
            restore.assert_called_once_with(Path(folder))
            for name in RELEASE.FILES:
                self.assertEqual((Path(folder) / name).read_bytes(), b'old')


if __name__ == '__main__':
    unittest.main()
