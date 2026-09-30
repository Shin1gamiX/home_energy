import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from history_store import record, supply_mode


class HistoryTests(unittest.TestCase):
    def test_supply_classification(self):
        self.assertEqual(supply_mode({'grid': 5, 'pv': 1000, 'battery': 500, 'load': 500}), 'solar')
        self.assertEqual(supply_mode({'grid': 0, 'pv': 0, 'battery': -900, 'load': 900}), 'battery')
        self.assertEqual(supply_mode({'grid': 300, 'pv': 700, 'battery': 0, 'load': 1000}), 'mixed')
        self.assertEqual(supply_mode({'grid': 1000, 'pv': 0, 'battery': 0, 'load': 1000}), 'grid')
        self.assertEqual(supply_mode({'pv': 1000}), 'unknown')

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
