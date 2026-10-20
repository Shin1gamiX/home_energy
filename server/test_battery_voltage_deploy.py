"""Synthetic release guard checks; no server, device or service calls."""
import importlib.util
from pathlib import Path
import time
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location('voltage_release',
    Path(__file__).resolve().parents[1] / 'deploy/activate-battery-voltage.py')
RELEASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RELEASE)


class VoltageReleaseTests(unittest.TestCase):
    def test_preserves_installation_database_path_only(self):
        generic = b"'/srv/homeassistant/config/home-assistant_v2.db'"
        existing = b"'/home/shini/docker/homeassistant/config/home-assistant_v2.db'"
        candidate = b'DATABASE = Path(' + generic + b')\nnew_field = 1\n'
        installed = b'DATABASE = Path(' + existing + b')\n'
        self.assertEqual(RELEASE.exporter_for_installation(candidate, installed),
                         candidate.replace(generic, existing))
        for bad_candidate, bad_installed in ((candidate + candidate, installed),
                                             (candidate, candidate), (b'', installed)):
            with self.assertRaises(RuntimeError):
                RELEASE.exporter_for_installation(bad_candidate, bad_installed)

    def test_exact_write_allowlist(self):
        self.assertEqual(set(RELEASE.FILES), {'public/app.js', 'public/history.js', 'public/interface.css',
            'public/index.html', 'public/history.html', 'server/export_energy.py', 'server/history_store.py'})
        for name in ('runtime/history.sqlite3', 'public/../runtime/energy.json', '../reports/index.json',
                     '/etc/nginx/sites-available/homeenergy.conf', 'public/collector-control.js'):
            with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, 'allowlist'):
                RELEASE.write(name, b'no write', {})

    def test_actual_new_recording_required(self):
        now = time.time()
        energy = dict(status='live', updated_at=now, values={'battery_voltage': 51.6})
        point = dict(t=int(now // 60) * 60, values={'battery_voltage': 51.7}, counts={'battery_voltage': 2})
        self.assertTrue(RELEASE.recorded_voltage(energy, {'points': [point]}, now - 5))
        for changed in ({'values': {}}, {'values': {'battery_voltage': None}},
                        {'values': {'battery_voltage': float('nan')}},
                        {'updated_at': now - 60}, {'status': 'stale'}):
            self.assertFalse(RELEASE.recorded_voltage({**energy, **changed}, {'points': [point]}, now - 5))
        self.assertFalse(RELEASE.recorded_voltage(energy, {'points': []}, now - 5))
        self.assertFalse(RELEASE.recorded_voltage(energy, {'points': [{**point, 'counts': {}}]}, now - 5))
        self.assertTrue(RELEASE.recorded_voltage({**energy, 'values': {'battery_voltage': 0}},
                                                {'points': [point]}, now - 5))

    def test_only_exporter_restart(self):
        with mock.patch.object(RELEASE.subprocess, 'run') as run:
            RELEASE.restart_exporter()
            run.assert_called_once_with(['/usr/bin/systemctl', 'restart', 'homeenergy.service'],
                                        check=True, timeout=30)


if __name__ == '__main__':
    unittest.main()
