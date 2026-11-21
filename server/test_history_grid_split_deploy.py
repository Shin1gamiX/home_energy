"""Exercise the grid split release with synthetic HTTP responses and a private test database."""
from contextlib import closing
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

HELPER = Path(__file__).resolve().parents[1] / 'deploy' / 'activate-history-grid-split.py'
SPEC = importlib.util.spec_from_file_location('history_grid_split_release', HELPER)
RELEASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RELEASE)


def point(stamp, house=0, battery=0):
    return {'t': stamp, 'values': {'grid_house': house, 'grid_battery': battery},
            'counts': {'grid_house': 1, 'grid_battery': 2}}


def initialize_history(root):
    history = root / 'runtime' / 'history'
    history.mkdir(parents=True)
    (history / 'index.json').write_text(json.dumps({'updated_at': 60}))
    (history / '1970-01-01.json').write_text(json.dumps({'day': '1970-01-01', 'points': [], 'modes': []}))
    database = root / 'runtime' / 'history.sqlite3'
    with closing(sqlite3.connect(database)) as conn, conn:
        conn.execute('CREATE TABLE readings (value REAL)')
        conn.execute('INSERT INTO readings VALUES (3.5)')
    return history, database


class HistoryGridReleaseTests(unittest.TestCase):
    def test_bridge_release_limits_and_live_evidence(self):
        self.assertEqual(set(RELEASE.BRIDGE.files), {
            'server/export_energy.py', 'server/history_store.py', 'server/energy_summary.py',
            'public/app.js', 'public/history.js', 'public/power-history.js', 'public/index.html', 'public/history.html'})
        def readings(**changes):
            values = dict(grid=720, bridge=-68, load=662, battery=2964, pv=3125, grid_house=652, grid_battery=0)
            values.update(changes)
            return dict(status='partial', updated_at=125, values=values)
        self.assertTrue(RELEASE.usable_bridge(readings(), 120, 140))
        self.assertTrue(RELEASE.usable_bridge(readings(grid=4650, bridge=-2900, load=1675, battery=5480,
                                                     pv=2960, grid_house=1675, grid_battery=2520), 120, 140))
        self.assertFalse(RELEASE.usable_bridge(readings(bridge=None), 120, 140))
        self.assertFalse(RELEASE.usable_bridge(readings(), 130, 140))
        with self.assertRaisesRegex(RuntimeError, 'exceed'):
            RELEASE.usable_bridge(readings(grid_battery=68), 120, 140)

    def test_bridge_verifier_requires_new_method_minute_and_fresh_live_split(self):
        seen = []
        def origin(host, route):
            nth = seen.count((host, route))
            seen.append((host, route))
            if route == '/history/index.json':
                doc = {'updated_at': 183, 'energy_summary': {'totals': {
                    branch: {'seconds': 10} for branch in RELEASE.BRANCHES}}}
            elif route == '/api/energy':
                doc = dict(updated_at=183, status='live', values=dict(
                    grid=720, bridge=-68 if nth >= 2 else None, load=662, battery=2964, pv=3125,
                    grid_house=652, grid_battery=0))
            else:
                p = point(180, 652, 0)
                if nth >= 1:
                    p['values']['solar_house'] = 0
                doc = dict(day='1970-01-01', points=[p])
            return 200, {'etag':'x', 'content-type':'application/json'}, json.dumps(doc)
        with patch.object(RELEASE.BASE, 'origin', origin), patch.object(RELEASE.BASE, 'check_headers'), \
                patch.object(RELEASE.time, 'time', return_value=184), patch.object(RELEASE.time, 'sleep'):
            RELEASE.verify_recording(121, bridge=True)
        for host in RELEASE.BASE.HOSTS:
            self.assertEqual(seen.count((host, '/api/energy')), 3)

    def test_only_recorded_finite_watts_qualify(self):
        for house, battery in ((0, 0), (15, 0), (0, 24), (8.5, 12.5)):
            day = {'points': [point(60, house, battery)]}
            self.assertEqual(RELEASE.recorded_split(day, 60), {'grid_house': (house, 1), 'grid_battery': (battery, 2)})
            self.assertIsNone(RELEASE.recorded_split(day, 120))
        for branch in RELEASE.BRANCHES:
            for group, values in (('values', [None, -1, True, float('nan'), float('inf')]),
                                  ('counts', [0, None, -1, False, 1.5])):
                for value in values:
                    with self.subTest(branch=branch, group=group, value=value):
                        p = point(60)
                        p[group][branch] = value
                        self.assertIsNone(RELEASE.recorded_split({'points': [p]}, 60))
                p = point(60)
                del p[group][branch]
                self.assertIsNone(RELEASE.recorded_split({'points': [p]}, 60))

    def test_recording_waits_for_next_minute_and_handles_athens_midnight(self):
        # 21 Nov 2026, 22:00 UTC is 22 Nov 00:00 in Athens.
        from datetime import datetime, timezone
        midnight = int(datetime(2026, 11, 21, 22, tzinfo=timezone.utc).timestamp())
        seen = []

        def origin(host, path):
            nth = seen.count((host, path))
            seen.append((host, path))
            if path == '/history/index.json':
                stamp = midnight - 1 if nth == 0 else midnight + 5
                doc = {'updated_at': stamp, 'energy_summary': {'totals': {
                    branch: {'seconds': 5} for branch in RELEASE.BRANCHES}}}
            else:
                self.assertEqual(path, '/history/2026-11-22.json')
                latest = point(midnight, 0, 370)
                if nth == 0:
                    latest['counts']['grid_battery'] = 0
                doc = {'day': '2026-11-22', 'points': [latest]}
            return 200, {'etag': 'abc', 'content-type': 'application/json'}, json.dumps(doc)

        with patch.object(RELEASE.BASE, 'origin', origin), patch.object(RELEASE.BASE, 'check_headers'), \
                patch.object(RELEASE.time, 'time', return_value=midnight + 12), patch.object(RELEASE.time, 'sleep'):
            RELEASE.verify_recording(midnight - 18)
        for host in RELEASE.BASE.HOSTS:
            self.assertEqual(seen.count((host, '/history/index.json')), 3)
            self.assertEqual(seen.count((host, '/history/2026-11-22.json')), 2)

    def test_stale_index_stops_verification_without_accepting_old_samples(self):
        with patch.object(RELEASE.BASE, 'origin', return_value=(200, {}, '{"updated_at":125}')) as origin, \
                patch.object(RELEASE.BASE, 'check_headers'), patch.object(RELEASE.time, 'time', return_value=230), \
                patch.object(RELEASE.time, 'monotonic', side_effect=[0, 1, 161]), patch.object(RELEASE.time, 'sleep'):
            with self.assertRaisesRegex(RuntimeError, 'New minute'):
                RELEASE.verify_recording(60)
        self.assertEqual(origin.call_count, len(RELEASE.BASE.HOSTS))

    def test_existing_day_accepts_only_advancing_tail_and_new_rows(self):
        day = {'day': '2026-11-21', 'points': [point(60), point(120)],
               'modes': [{'start': 60, 'end': 120, 'state': 'grid'},
                         {'start': 120, 'end': 200, 'state': 'solar'}]}
        current = deepcopy(day)
        current['points'][1]['values']['grid_house'] = 100
        current['points'].append(point(180))
        current['modes'][1]['end'] = 180
        current['modes'].append({'start': 180, 'end': 200, 'state': 'grid'})
        RELEASE.check_existing_day(day, current)
        for key, index, field, value in (('points', 0, 't', 20), ('points', 1, 't', 180),
                ('modes', 0, 'end', 119), ('modes', 1, 'state', 'battery')):
            broken = deepcopy(current)
            broken[key][index][field] = value
            with self.subTest(key=key, field=field), self.assertRaises(RuntimeError):
                RELEASE.check_existing_day(day, broken)

    def test_consistent_history_backup_precedes_writes_and_failure_restores_only_code(self):
        services = dict.fromkeys(('homeenergy', 'nginx', 'homeenergy-control'), 'ActiveState=active')
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            history, database = initialize_history(root)
            bodies, originals, writes = {}, {}, []
            for name in RELEASE.FILES:
                target = root / name
                target.parent.mkdir(exist_ok=True)
                target.write_bytes(b'old')
                originals[name] = RELEASE.BASE.fingerprint(target)
                bodies[name] = b'new'

            def write(name, body, metadata):
                backup, = root.glob('homeenergy-history-grid-split-backup.*')
                self.assertTrue(all((backup / target).read_bytes() == b'old' for target in RELEASE.FILES))
                with closing(sqlite3.connect(backup / 'history.sqlite3')) as conn:
                    self.assertEqual(conn.execute('SELECT value FROM readings').fetchall(), [(3.5,)])
                self.assertEqual(json.loads((backup / 'history-index.json').read_text()), {'updated_at': 60})
                writes.append((name, body))
                (root / name).write_bytes(body)

            def verification_failure(since):
                with closing(sqlite3.connect(database)) as conn, conn:
                    conn.execute('INSERT INTO readings VALUES (4.5)')
                raise RuntimeError('Recording unavailable')

            original_stat = Path.stat

            def private_backup_mode(path, *args, **kwargs):
                result = original_stat(path, *args, **kwargs)
                if path.parent == root and path.name.startswith('homeenergy-history-grid-split-backup.'):
                    fields = list(result)
                    fields[0], fields[4] = RELEASE.stat.S_IFDIR | 0o700, 0
                    return os.stat_result(fields)
                return result

            with patch.object(RELEASE, 'ARCHIVE', root), patch.object(RELEASE.INSTALL, 'ROOT', root), \
                    patch.object(RELEASE.BASE, 'HISTORY', history), patch.object(RELEASE.BASE, 'FEED', history / 'index.json'), \
                    patch.object(RELEASE.INSTALL, 'protected', return_value={}), \
                    patch.object(RELEASE.INSTALL, 'service_state', return_value=services), \
                    patch.object(RELEASE.INSTALL, 'write', side_effect=write), \
                    patch.object(RELEASE.INSTALL, 'restart_exporter') as restart, \
                    patch.object(RELEASE, 'verify_recording', side_effect=verification_failure), \
                    patch.object(RELEASE.BASE, 'verify_http'), \
                    patch.object(Path, 'stat', private_backup_mode):
                # Windows lacks POSIX mode bits; the production ownership/mode gate is emulated here.
                with self.assertRaisesRegex(RuntimeError, 'Recording unavailable'):
                    RELEASE.activate(bodies, originals, services, 'feed')
            self.assertEqual(writes[:2], [(name, b'new') for name in RELEASE.BACKEND])
            self.assertEqual(writes[2:], [(name, b'old') for name in reversed(RELEASE.BACKEND)])
            self.assertEqual(restart.call_count, 2)
            with closing(sqlite3.connect(database)) as conn:
                self.assertEqual(conn.execute('SELECT value FROM readings ORDER BY value').fetchall(), [(3.5,), (4.5,)])


if __name__ == '__main__':
    unittest.main()
