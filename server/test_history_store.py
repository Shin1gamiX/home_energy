import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from history_store import record


class HistoryTests(unittest.TestCase):
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
            with sqlite3.connect(runtime / 'history.sqlite3') as db:
                self.assertEqual(db.execute("SELECT total,count FROM readings WHERE metric='pv'").fetchone(), (400, 2))
            index = json.loads((runtime / 'history/index.json').read_text())
            data = json.loads((runtime / 'history' / (index['days'][0] + '.json')).read_text())
            self.assertEqual(data['points'][0]['values']['pv'], 200)
            self.assertEqual(data['points'][0]['values']['grid_voltage'], 231)
            self.assertNotIn('battery', data['points'][0]['values'])


if __name__ == '__main__':
    unittest.main()
