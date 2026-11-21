"""Synthetic checks for the grid flow release. No service or external connection required."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

HELPER = Path(__file__).resolve().parents[1] / 'deploy' / 'activate-grid-flow.py'
SPEC = importlib.util.spec_from_file_location('grid_flow_release', HELPER)
RELEASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RELEASE)


def snapshot(house=1800, charging=2500):
    return dict(status='live', generated_at=105, updated_at=104,
                values=dict(grid_house=house, grid_battery=charging, grid=house + charging))


class GridReleaseTests(unittest.TestCase):
    def test_release_scope_and_installed_default(self):
        self.assertEqual(RELEASE.INSTALL.FILES, ('server/export_energy.py', 'public/app.js', 'public/index.html'))
        candidate = b"'/srv/homeassistant/config/home-assistant_v2.db'"
        installed = b"'/home/shini/docker/homeassistant/config/home-assistant_v2.db'"
        self.assertEqual(RELEASE.INSTALL.exporter_for_installation(candidate, installed), installed)

    def test_new_sample_in_any_grid_mode(self):
        for house, charging in ((1800, 2500), (0, 2500), (1800, 0), (0, 0)):
            self.assertTrue(RELEASE.fresh_split(snapshot(house, charging), 100, 105))
        for key, value in (('grid', 20), ('grid_house', -1), ('grid_battery', None),
                           ('grid_battery', float('nan')), ('grid_house', True)):
            with self.subTest(key=key, value=value):
                energy = snapshot()
                energy['values'][key] = value
                self.assertFalse(RELEASE.fresh_split(energy, 100, 105))
        for key, value in (('status', 'offline'), ('updated_at', 99), ('generated_at', 98)):
            energy = {**snapshot(), key: value}
            self.assertFalse(RELEASE.fresh_split(energy, 100, 105))

    def test_requires_both_hosts_and_current_recording(self):
        seen = []

        def origin(host, path):
            already = (host, path) in seen
            seen.append((host, path))
            if path == '/api/energy':
                body = snapshot()
            else:
                self.assertEqual(path, '/history/index.json')
                body = {'updated_at': 104 if already else 99}
            return 200, {}, json.dumps(body)

        with patch.object(RELEASE.BASE, 'origin', origin), patch.object(RELEASE.BASE, 'check_headers'), \
                patch.object(RELEASE.time, 'time', return_value=105), patch.object(RELEASE.time, 'sleep'):
            RELEASE.verify_split(100)
        for host in RELEASE.BASE.HOSTS:
            self.assertEqual(seen.count((host, '/api/energy')), 2)
            self.assertEqual(seen.count((host, '/history/index.json')), 2)

    def test_originals_backed_up_before_any_publish_and_failure_calls_restore(self):
        running = dict.fromkeys(('homeenergy', 'nginx', 'homeenergy-control'), 'ActiveState=active')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            bodies, originals = {}, {}
            for name in RELEASE.FILES:
                target = root / name
                target.parent.mkdir(exist_ok=True)
                target.write_bytes(b'old')
                originals[name] = RELEASE.BASE.fingerprint(target)
                bodies[name] = b'new'
            writes = []

            def writer(name, body, meta):
                backup, = root.glob('homeenergy-grid-flow-backup.*')
                self.assertEqual(set(json.loads((backup / 'record.json').read_text())['files']), set(RELEASE.FILES))
                self.assertTrue(all((backup / item).read_bytes() == b'old' for item in RELEASE.FILES))
                writes.append(name)

            with patch.object(RELEASE, 'ARCHIVE', root), patch.object(RELEASE, 'ROOT', root), \
                    patch.object(RELEASE.INSTALL, 'protected', return_value={}), \
                    patch.object(RELEASE.INSTALL, 'service_state', return_value=running), \
                    patch.object(RELEASE.INSTALL, 'write', writer), patch.object(RELEASE.INSTALL, 'restart_exporter'), \
                    patch.object(RELEASE, 'verify_split', side_effect=RuntimeError('Unavailable snapshot')), \
                    patch.object(RELEASE, 'restore') as restore:
                with self.assertRaisesRegex(RuntimeError, 'Unavailable snapshot'):
                    RELEASE.activate(bodies, originals, running, 'feed')
            self.assertEqual(writes, [RELEASE.EXPORTER])
            restore.assert_called_once_with(next(root.glob('homeenergy-grid-flow-backup.*')))


if __name__ == '__main__':
    unittest.main()
