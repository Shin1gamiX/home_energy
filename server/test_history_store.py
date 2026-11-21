import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from history_store import record, supply_mode


class HistoryTests(unittest.TestCase):
    def test_charging_status_cutover_records_only_new_routes_and_persistent_discrepancies(self):
        minute = 1790100000
        old = dict(grid=1037, bridge=-72, load=992, pv=996, battery=1046,
                   grid_house=965, grid_battery=50, solar_house=0, battery_house=0)
        revised = dict(old, grid_battery=0)
        pv_only = dict(mains_active=False, solar_active=True, discrepancy_w=50, persistent=False)
        flagged = dict(pv_only, persistent=True, discrepancy_w=500)
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)

            def send(stamp, values, routing=None):
                sample = dict(status='live', updated_at=stamp, values=values)
                if routing is not None:
                    sample['charging_routing'] = routing
                record(sample, runtime)

            send(minute - 86400, old)
            send(minute + 4, old)
            closed, current = sorted((runtime / 'history').glob('????-??-??.json'))
            closed_bytes = closed.read_bytes()
            send(minute + 30, dict(revised, grid=1053, soc=37), flagged)
            send(minute + 60, revised, pv_only)
            send(minute + 120, revised, flagged)
            send(minute + 120, revised, flagged)  # Duplicate observations never count twice.
            send(minute + 130, revised, dict(flagged, discrepancy_w=300))
            send(minute + 140, revised, pv_only)
            send(minute + 180, dict(revised, grid_battery=None), dict(flagged, mains_active=None))
            send(minute + 240, old)  # Rolling back a producer must not revive old estimates.
            send(minute + 300, revised, dict(pv_only, discrepancy_w=600))  # Brief mismatch.

            self.assertEqual(closed_bytes, closed.read_bytes())
            first, clean, alert, gap, older_producer, brief = json.loads(current.read_text())['points']
            # Independent measurements keep recording in this open minute; old route estimates stay intact.
            self.assertEqual(first['values']['grid'], 1045)
            self.assertEqual(first['counts']['grid'], 2)
            self.assertEqual(first['values']['soc'], 37)
            for key in ('grid_house', 'grid_battery', 'solar_house'):
                self.assertEqual(first['values'][key], old[key])
                self.assertEqual(first['counts'][key], 1)
            for point in (first, clean, gap, older_producer, brief):
                self.assertNotIn('charging_discrepancy_w', point['values'])
            self.assertEqual(clean['values']['grid_battery'], 0)
            self.assertEqual(alert['counts']['charging_discrepancy_w'], 2)
            self.assertEqual(alert['values']['charging_discrepancy_w'], 400)
            for point in (gap, older_producer):
                self.assertIsNone(point['values']['grid_battery'])
                self.assertNotIn('grid_battery', point['counts'])
                self.assertEqual(point['values']['grid'], old['grid'])
            self.assertIsNone(older_producer['values']['solar_house'])
            self.assertEqual(brief['values']['grid_battery'], 0)

    def test_supply_classification(self):
        self.assertEqual(supply_mode({'grid': 5, 'pv': 1000, 'battery': 500, 'load': 500}), 'solar')
        self.assertEqual(supply_mode({'grid': 0, 'pv': 0, 'battery': -900, 'load': 900}), 'battery')
        self.assertEqual(supply_mode({'grid': 300, 'pv': 700, 'battery': 0, 'load': 1000}), 'mixed')
        self.assertEqual(supply_mode({'grid': 1000, 'pv': 0, 'battery': 0, 'load': 1000}), 'grid')
        self.assertEqual(supply_mode({'pv': 1000}), 'unknown')

    def test_split_supply_and_missing_branches(self):
        sample = dict(grid=1000, grid_house=0, grid_battery=1000, load=800, pv=800, battery=1000)
        self.assertEqual(supply_mode(sample), 'solar')
        self.assertEqual(supply_mode(dict(sample, grid_house=200, grid=1200)), 'mixed')
        self.assertEqual(supply_mode(dict(sample, pv=0, grid_house=800, grid=1800)), 'grid')
        self.assertEqual(supply_mode(dict(sample, grid_house=None)), 'unknown')
        self.assertEqual(supply_mode(dict(sample, grid=None, grid_battery=None)), 'solar')
        self.assertEqual(supply_mode(dict(sample, load=0)), 'standby')

    def test_legacy_day_and_upgrade_minute_are_retained_without_inventing_split(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            minute = 1790100000
            legacy = dict(pv=800, load=800, battery=1000, grid=1000)
            split = dict(legacy, grid_house=0, grid_battery=1000)
            def send(stamp, values):
                record(dict(status='live', updated_at=stamp, values=values), runtime)

            send(minute - 86400, legacy)
            old_day = next(path for path in (runtime / 'history').glob('????-??-??.json'))
            old_bytes = old_day.read_bytes()
            send(minute + 2, legacy)
            send(minute + 22, split)
            send(minute + 60, split)
            send(minute + 65, split)
            send(minute + 65, split)  # Duplicate heartbeat.
            send(minute + 120, dict(legacy, grid_house=None, grid_battery=None))
            send(minute + 180, dict(legacy, grid_house=-4, grid_battery=0))
            index = json.loads((runtime / 'history/index.json').read_text())
            day = json.loads((runtime / 'history' / (index['days'][-1] + '.json')).read_text())
            first, full, missing, invalid = day['points']
            self.assertNotIn('grid_house', first['values'])
            self.assertNotIn('grid_battery', first['values'])
            self.assertEqual(full['values']['grid_house'], 0)
            self.assertEqual(full['values']['grid_battery'], 1000)
            self.assertEqual(full['counts']['grid_house'], 2)
            self.assertIsNone(missing['values']['grid_house'])
            self.assertNotIn('grid_house', missing['counts'])
            self.assertIsNone(invalid['values']['grid_house'])
            self.assertEqual(invalid['values']['grid_battery'], 0)
            self.assertEqual([(mode['start'], mode['state']) for mode in day['modes']],
                             [(minute + 2, 'grid'), (minute + 22, 'solar'), (minute + 120, 'unknown')])
            totals = index['energy_summary']['totals']
            self.assertEqual(totals['grid_house'], {'kwh': 0, 'seconds': 60})
            self.assertEqual(totals['grid_battery'], {'kwh': 1000 / 60000, 'seconds': 60})
            self.assertEqual(totals['solar_to_house'], {'kwh': 800 / 60000, 'seconds': 180})
            self.assertEqual(old_bytes, old_day.read_bytes())

    def test_supply_intervals_transitions_and_outages(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            sample = {'status': 'live', 'updated_at': 1790100000,
                      'values': {'grid': 500, 'pv': 0, 'battery': 0, 'load': 500}}
            record(sample, runtime)
            sample['updated_at'] += 10
            record(sample, runtime)
            sample['updated_at'] += 10
            sample['values'] = {'grid': 0, 'pv': 0, 'battery': -500, 'load': 500}
            record(sample, runtime)
            sample['updated_at'] += 200
            record(sample, runtime)
            with closing(sqlite3.connect(runtime / 'history.sqlite3')) as db:
                rows = db.execute('SELECT start,end,state FROM supply_intervals ORDER BY start').fetchall()
            self.assertEqual(rows, [(1790100000, 1790100020, 'grid'),
                                    (1790100020, 1790100110, 'battery'),
                                    (1790100220, 1790100310, 'battery')])
            index = json.loads((runtime / 'history/index.json').read_text())
            data = json.loads((runtime / 'history' / (index['days'][0] + '.json')).read_text())
            self.assertEqual(len(data['modes']), 3)
            self.assertEqual(index['mode_recorded_from'], 1790100000)

    def test_bridge_upgrade_preserves_old_estimates_and_records_observed_house_flows(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            minute = 1790100000
            def send(stamp, values):
                record(dict(status='partial', updated_at=stamp, values=values), runtime)

            old = dict(grid=720, grid_house=0, grid_battery=720, pv=3125, load=662, battery=2964)
            charging = dict(old, bridge=-68, grid_house=652, grid_battery=0,
                            solar_house=0, battery_house=0)
            mixed = dict(grid=300, bridge=300, grid_house=300, grid_battery=0, pv=200, load=600,
                         battery=-100, solar_house=200, battery_house=100)
            missing = dict(charging, bridge=None, grid_house=None, grid_battery=None,
                           solar_house=None, battery_house=None)
            send(minute - 86400, old)
            send(minute + 5, old)
            old_path, current_path = sorted((runtime / 'history').glob('????-??-??.json'))
            old_bytes = old_path.read_bytes()
            first = json.loads(current_path.read_text())['points'][0]
            with closing(sqlite3.connect(runtime / 'history.sqlite3')) as db:
                closed_cache = db.execute('SELECT * FROM energy_daily_summary WHERE day=?',
                                          (old_path.stem,)).fetchone()

            # This minute already has old grid flows. Even a new house reading
            # cannot be mixed into it without changing its old solar estimate.
            send(minute + 30, dict(mixed, soc=75))
            send(minute + 60, charging)
            send(minute + 65, mixed)
            send(minute + 120, missing)
            send(minute + 180, charging)

            day = json.loads(current_path.read_text())
            transition, measured, gap, resumed = day['points']
            for key in old:
                self.assertEqual(transition['values'][key], first['values'][key])
                self.assertEqual(transition['counts'][key], 1)
            self.assertNotIn('solar_house', transition['values'])
            self.assertEqual(transition['values']['soc'], 75)
            self.assertEqual(measured['values']['grid_house'], 476)
            self.assertEqual(measured['values']['grid_battery'], 0)
            self.assertEqual(measured['values']['solar_house'], 100)
            self.assertEqual(measured['counts']['solar_house'], 2)
            self.assertEqual(gap['values']['grid'], 720)
            self.assertIsNone(gap['values']['grid_house'])
            self.assertIsNone(gap['values']['solar_house'])
            self.assertNotIn('grid_battery', gap['counts'])
            self.assertNotIn('solar_house', gap['counts'])
            self.assertEqual(resumed['values']['solar_house'], 0)
            self.assertEqual([item['state'] for item in day['modes']],
                             ['solar', 'mixed', 'grid', 'mixed', 'unknown', 'grid'])
            totals = json.loads((runtime / 'history/index.json').read_text())['energy_summary']['totals']
            self.assertEqual(totals['grid_battery'], {'kwh': 2 * 720 / 60000, 'seconds': 180})
            self.assertAlmostEqual(totals['solar_to_house']['kwh'], (2 * 662 + 100) / 60000)
            self.assertEqual(totals['solar_to_house']['seconds'], 180)
            self.assertEqual(totals['grid']['seconds'], 240)
            self.assertEqual(old_path.read_bytes(), old_bytes)
            with closing(sqlite3.connect(runtime / 'history.sqlite3')) as db:
                self.assertEqual(db.execute("SELECT value FROM metadata WHERE key='grid_bridge_from'").fetchone(),
                                 (minute + 60,))
                self.assertEqual(db.execute('SELECT * FROM energy_daily_summary WHERE day=?',
                                            (old_path.stem,)).fetchone(), closed_cache)

    def test_supply_interval_crosses_athens_midnight(self):
        from datetime import datetime
        from history_store import ATHENS
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            stamp = datetime(2026, 9, 30, 23, 59, 50, tzinfo=ATHENS).timestamp()
            sample = {'status': 'live', 'updated_at': stamp,
                      'values': {'grid': 500, 'pv': 0, 'battery': 0, 'load': 500}}
            record(sample, runtime)
            sample['updated_at'] += 20
            record(sample, runtime)
            first = json.loads((runtime / 'history/2026-09-30.json').read_text())['modes'][0]
            second = json.loads((runtime / 'history/2026-10-01.json').read_text())['modes'][0]
            self.assertEqual(first['end'], second['start'])
            self.assertEqual(second['end'], stamp + 110)

    def test_legacy_voltage_is_preserved_but_not_recorded(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            sample = {'status': 'live', 'updated_at': 1790100000,
                      'values': {'pv_voltage': 380, 'pv1_voltage': 380, 'pv2_voltage': 240}}
            record(sample, runtime)
            with closing(sqlite3.connect(runtime / 'history.sqlite3')) as db, db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM readings WHERE metric='pv_voltage'").fetchone()[0], 0)
                day = db.execute('SELECT day FROM readings LIMIT 1').fetchone()[0]
                db.execute('INSERT INTO readings VALUES(?,?,?,?,?)', (1790099940, day, 'pv_voltage', 390, 1))
            sample['updated_at'] += 60
            record(sample, runtime)
            data = json.loads((runtime / 'history' / (day + '.json')).read_text())
            legacy = [p['values']['pv_voltage'] for p in data['points'] if 'pv_voltage' in p['values']]
            self.assertEqual(legacy, [390])
            self.assertEqual(data['points'][-1]['values']['pv2_voltage'], 240)

    def test_channel_history_preserves_legacy_and_missing_values(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            sample = {'status': 'live', 'updated_at': 1790100000, 'values': {'pv': 300}}
            record(sample, runtime)
            sample['updated_at'] += 60
            sample['values'] = {'pv': 300, 'pv1_power': 100, 'pv2_power': 200,
                                'pv2_voltage': 237, 'pv2_current': None}
            record(sample, runtime)
            with closing(sqlite3.connect(runtime / 'history.sqlite3')) as db:
                self.assertEqual(db.execute("SELECT SUM(total) FROM readings WHERE metric='pv'").fetchone()[0], 600)
                self.assertEqual(db.execute("SELECT total,count FROM readings WHERE metric='pv2_power'").fetchone(), (200, 1))
                self.assertEqual(db.execute("SELECT COUNT(*) FROM readings WHERE metric='pv2_current'").fetchone()[0], 0)

    def test_current_metrics_keep_sign_and_missing_values(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            sample = {'status': 'live', 'updated_at': 1790100000,
                      'values': {'pv_current': 4.8, 'battery_current': -10.0, 'load_current': 2.0}}
            record(sample, runtime)
            sample['updated_at'] += 5
            sample['values'] = {'pv_current': 5.2, 'battery_current': -6.0, 'load_current': None}
            record(sample, runtime)
            index = json.loads((runtime / 'history/index.json').read_text())
            data = json.loads((runtime / 'history' / (index['days'][0] + '.json')).read_text())
            point = data['points'][0]
            self.assertEqual(point['values']['pv_current'], 5.0)
            self.assertEqual(point['values']['battery_current'], -8.0)
            self.assertEqual(point['values']['load_current'], 2.0)
            self.assertEqual(point['counts']['load_current'], 1)

    def test_battery_voltage_records_forward_without_backfill(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            sample = {'status': 'live', 'updated_at': 1790100000, 'values': {'pv': 100}}
            record(sample, runtime)
            sample['updated_at'] += 60
            sample['values'] = {'pv': 100, 'battery_voltage': 51.6}
            record(sample, runtime)
            record(sample, runtime)  # Same heartbeat must not count twice.
            sample['updated_at'] += 5
            sample['values']['battery_voltage'] = 52.0
            record(sample, runtime)
            sample['updated_at'] += 5
            sample['values']['battery_voltage'] = None
            record(sample, runtime)
            sample['updated_at'] += 60
            sample['status'] = 'stale'
            sample['values']['battery_voltage'] = 55
            record(sample, runtime)
            index = json.loads((runtime / 'history/index.json').read_text())
            data = json.loads((runtime / 'history' / (index['days'][0] + '.json')).read_text())
            self.assertNotIn('battery_voltage', data['points'][0]['values'])
            self.assertAlmostEqual(data['points'][1]['values']['battery_voltage'], 51.8)
            self.assertEqual(data['points'][1]['counts']['battery_voltage'], 2)
            self.assertEqual(len(data['points']), 2)
            self.assertNotIn('battery_voltage', index['energy_summary']['totals'])

    def test_deduplication_averages_and_stale_reports(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            sample = {'status': 'live', 'updated_at': 1790100000, 'values': {'pv': 100, 'soc': 23, 'grid_voltage': 230}}
            record(sample, runtime)
            record(sample, runtime)
            sample['updated_at'] += 5
            sample['values']['pv'] = 300
            sample['values']['grid_voltage'] = 232
            record(sample, runtime)
            sample['status'] = 'stale'
            sample['updated_at'] += 5
            record(sample, runtime)
            with closing(sqlite3.connect(runtime / 'history.sqlite3')) as db:
                self.assertEqual(db.execute("SELECT total,count FROM readings WHERE metric='pv'").fetchone(), (400, 2))
            index = json.loads((runtime / 'history/index.json').read_text())
            data = json.loads((runtime / 'history' / (index['days'][0] + '.json')).read_text())
            self.assertEqual(data['points'][0]['values']['pv'], 200)
            self.assertEqual(data['points'][0]['values']['grid_voltage'], 231)
            self.assertNotIn('battery', data['points'][0]['values'])


if __name__ == '__main__':
    unittest.main()
