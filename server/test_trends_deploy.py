import importlib.util
from pathlib import Path
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('trends_release', ROOT / 'deploy/activate-trends.py')
RELEASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RELEASE)


class TrendsReleaseTests(unittest.TestCase):
    def test_exact_routes_and_no_controls_or_history_changes(self):
        # Reverse only this feature's example routes to make the prior baseline.
        candidate = (ROOT / 'deploy/nginx.conf.example').read_bytes()
        baseline = candidate.replace(b'|trends\\.js|trends-data\\.js|trends\\.css', b'')
        for name in ('trends.js', 'trends-data.js', 'trends.css'):
            baseline = baseline.replace(('    location = /' + name + ' { try_files $uri =404; }\n').encode(), b'')
        self.assertEqual(RELEASE.nginx_candidate(baseline), candidate)
        with self.assertRaises(RuntimeError):
            RELEASE.nginx_candidate(candidate)
        for name in ('../runtime/history.sqlite3', 'public/history.html', 'server/history_store.py',
                     '/etc/passwd', 'public/collector-control.js'):
            with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, 'allowlist'):
                RELEASE.write(name, b'', {})

    def test_non_root_cannot_activate(self):
        with mock.patch.object(RELEASE.os, 'geteuid', return_value=1000, create=True), \
                mock.patch.object(RELEASE, 'bundle') as bundle:
            with self.assertRaisesRegex(RuntimeError, 'sudo'):
                RELEASE.activate()
            bundle.assert_not_called()

    def test_controls_and_exporter_service_config_are_protected(self):
        with mock.patch.object(RELEASE.BASE, 'protected_state', return_value={
                str(RELEASE.ROOT / 'server/energy_summary.py'): 'changed', 'reports': 'fixed'}), \
                mock.patch.object(RELEASE.BASE, 'fingerprint', return_value='unit'):
            result = RELEASE.protected()
        self.assertEqual(result, {'reports': 'fixed', '/etc/systemd/system/homeenergy.service': 'unit'})

    def test_only_exporter_restarts(self):
        with mock.patch.object(RELEASE.subprocess, 'run') as run:
            RELEASE.restart_exporter()
        run.assert_called_once_with(['/usr/bin/systemctl', 'restart', 'homeenergy.service'], check=True, timeout=30)


if __name__ == '__main__':
    unittest.main()
