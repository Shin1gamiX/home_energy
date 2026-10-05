import json
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path

from energy_summary import ATHENS, ENERGY_KEYS, combine_days, summarize_points, update_summary
from history_store import record


def stamp(year, month, day, hour=0, minute=0, second=0):
    return datetime(year, month, day, hour, minute, second, tzinfo=ATHENS).timestamp()


class EnergySummaryTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node is needed for browser calculation parity')
    def test_browser_energy_parity(self):
        rows = [{'t': i * 60, 'values': {'pv': 1200 if i % 4 else None,
                                       'grid': 0 if i % 3 else 75,
                                       'load': 997.314159, 'battery': -300 if i % 2 else 500}}
                for i in range(200) if i % 7]
        cutoff = 199 * 60 + 37
        # Extract only pure calculation functions, never execute browser/network code.
        script = """
const fs = require('node:fs'), vm = require('node:vm');
const source = fs.readFileSync('history.js', 'utf8');
const context = {};
for (const name of ['energyTotals', 'validateEnergySummary']) {
  const start = source.indexOf(`function ${name}(`);
  const end = source.indexOf('\\nfunction ', start + 1);
  vm.runInNewContext(source.slice(start, end < 0 ? undefined : end), context);
}
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
context.validateEnergySummary(input.summary, input.cutoff);
process.stdout.write(JSON.stringify(context.energyTotals(input.rows, 0, input.cutoff, input.cutoff)));
"""
        calculated = summarize_points(rows, cutoff)
        summary = combine_days([{'day': '1970-01-01', **calculated}], cutoff)
        result = subprocess.run([shutil.which('node'), '-e', script],
                                input=json.dumps({'rows': rows, 'cutoff': cutoff, 'summary': summary}),
                                cwd=Path(__file__).resolve().parents[1], text=True,
                                capture_output=True, check=True, timeout=20)
        browser = json.loads(result.stdout)
        for key in ENERGY_KEYS:
            self.assertAlmostEqual(browser[key]['kwh'], calculated['totals'][key]['kwh'])
            self.assertEqual(browser[key]['seconds'], calculated['totals'][key]['seconds'])

    def test_minutes_gaps_signs_and_partial_cutoff(self):
        rows = [{'t': 0, 'values': {'pv': 1200, 'load': 900, 'grid': 100, 'battery': -200}},
                {'t': 120, 'values': {'pv': 1200, 'load': 900, 'grid': 100, 'battery': 300}}]
        total = summarize_points(rows, 150)['totals']
        self.assertEqual(total['pv'], {'kwh': .03, 'seconds': 90})
        self.assertAlmostEqual(total['battery']['kwh'], 200 / 60_000)
        self.assertAlmostEqual(total['solar_to_house']['kwh'], (600 * 60 + 800 * 30) / 3_600_000)
        self.assertEqual(total['solar_to_house']['seconds'], 90)
        self.assertEqual(summarize_points(rows + [rows[0]], 150)['totals'], total)

    def test_unknown_zero_negative_and_nonfinite(self):
        rows = [{'t': 0, 'values': {'pv': 0, 'load': 500, 'grid': 420, 'battery': 0}},
                {'t': 60, 'values': {'pv': None, 'load': 500, 'grid': -4, 'battery': float('nan')}}]
        result = summarize_points(rows, 120)['totals']
        self.assertEqual(result['pv'], {'kwh': 0, 'seconds': 60})
        self.assertEqual(result['grid']['seconds'], 60)
        self.assertEqual(result['battery'], {'kwh': 0, 'seconds': 60})
        self.assertEqual(result['solar_to_house'], {'kwh': 0, 'seconds': 60})
        self.assertEqual(result['load']['seconds'], 120)
        self.assertEqual(summarize_points([], 0)['from'], None)

    def test_cache_backfills_and_finalizes_previous_month(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            last = stamp(2026, 9, 30, 23, 59, 30)
            record({'status': 'live', 'updated_at': last, 'values': {'pv': 600, 'battery': -300}}, runtime)
            before = json.loads((runtime / 'history/index.json').read_text())['energy_summary']
            self.assertEqual(before['totals']['pv']['seconds'], 30)
            with closing(sqlite3.connect(runtime / 'history.sqlite3')) as db, db:
                db.execute('DELETE FROM energy_daily_summary')
                db.execute('INSERT INTO readings VALUES (?,?,?,?,?)', (stamp(2026, 8, 1), '2026-08-01', 'pv', 1200, 2))
            record({'status': 'live', 'updated_at': last + 60, 'values': {'pv': 0}}, runtime)
            result = json.loads((runtime / 'history/index.json').read_text())['energy_summary']
            self.assertEqual([row['month'] for row in result['months']], ['2026-08', '2026-09', '2026-10'])
            self.assertEqual(result['totals']['pv']['seconds'], 150)
            self.assertAlmostEqual(result['totals']['pv']['kwh'], .02)
            self.assertEqual(result['totals']['battery']['seconds'], 60)
            self.assertEqual(result['months'][-1]['totals']['battery']['seconds'], 0)
            for key in ENERGY_KEYS:
                for field in ('kwh', 'seconds'):
                    self.assertAlmostEqual(result['totals'][key][field], sum(row['totals'][key][field] for row in result['months']))
            duplicate = (runtime / 'history/index.json').read_bytes()
            record({'status': 'live', 'updated_at': last + 60, 'values': {'pv': 9999}}, runtime)
            record({'status': 'stale', 'updated_at': last + 120, 'values': {'pv': 9999}}, runtime)
            self.assertEqual(duplicate, (runtime / 'history/index.json').read_bytes())

    def test_cached_old_days_are_not_rescanned(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            record({'status': 'live', 'updated_at': stamp(2026, 8, 1, 12), 'values': {'pv': 600}}, runtime)
            record({'status': 'live', 'updated_at': stamp(2026, 9, 1, 12), 'values': {'pv': 600}}, runtime)
            with closing(sqlite3.connect(runtime / 'history.sqlite3')) as db, db:
                queries = []
                db.set_trace_callback(queries.append)
                update_summary(db, ['2026-08-01', '2026-09-01'], '2026-09-01', stamp(2026, 9, 1, 12, 1), stamp(2026, 9, 1, 12))
                self.assertFalse(any("FROM readings WHERE day='2026-08-01'" in query for query in queries))
                db.execute('UPDATE energy_daily_summary SET version=0')
                queries.clear()
                update_summary(db, ['2026-08-01', '2026-09-01'], '2026-09-01', stamp(2026, 9, 1, 12, 1))
                self.assertTrue(any("FROM readings WHERE day='2026-08-01'" in query for query in queries))

    def test_missing_month_and_athens_dst_boundaries(self):
        first = stamp(2026, 9, 1)
        days = [{'day': '2026-09-01', **summarize_points([{'t': first, 'values': {'pv': 600}}], first + 60)}]
        result = combine_days(days, stamp(2026, 11, 1))
        october = result['months'][1]
        self.assertEqual(october['month'], '2026-10')
        self.assertEqual((october['to'] - october['from']) / 3600, 31 * 24 + 1)
        self.assertEqual(october['totals']['pv']['seconds'], 0)
        self.assertEqual(result['months'][2]['from'], result['months'][2]['to'])
        march_start = stamp(2026, 3, 1)
        march = combine_days([{'day': '2026-03-01', **summarize_points([{'t': march_start, 'values': {'pv': 0}}], march_start + 60)}], stamp(2026, 4, 1))
        self.assertEqual((march['months'][0]['to'] - march['months'][0]['from']) / 3600, 31 * 24 - 1)
        self.assertEqual(combine_days([], 100)['months'], [])


if __name__ == '__main__':
    unittest.main()
