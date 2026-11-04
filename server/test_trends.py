import json
import sqlite3
import unittest
from datetime import datetime, timedelta
from energy_summary import ATHENS, daily_trends, summarize_points, update_summary


class TrendsTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.execute('CREATE TABLE readings (minute INTEGER, day TEXT, metric TEXT, total REAL, count INTEGER)')
        self.addCleanup(self.db.close)

    def test_dst_peaks_coverage_missing_and_zero(self):
        start = datetime(2026, 10, 25, tzinfo=ATHENS)
        day = start.strftime('%Y-%m-%d')
        for index, value in enumerate([0, 45, 92, 101, -1]):
            self.db.execute('INSERT INTO readings VALUES (?,?,?,?,?)', (start.timestamp()+index*60, day, 'soc', value*2, 2))
        summaries = [{'day': day, **summarize_points([], start.timestamp())}]
        before = self.db.execute('SELECT * FROM readings').fetchall()
        result = daily_trends(self.db, summaries, (start + timedelta(days=1)).timestamp())
        self.assertEqual(len(result['days']), 60)
        self.assertEqual(result['days'][-1]['seconds'], 90000)
        self.assertEqual(result['days'][-1]['soc'], dict(minimum=0, maximum=92, seconds=180))
        self.assertEqual(result['days'][-2]['soc']['maximum'], None)
        self.assertEqual(before, self.db.execute('SELECT * FROM readings').fetchall())
        trace=[]
        self.db.set_trace_callback(trace.append)
        self.assertEqual(result, daily_trends(self.db, summaries, (start + timedelta(days=1)).timestamp()))
        self.assertFalse(any('FROM readings' in sql for sql in trace))
        spring = daily_trends(self.db, [], datetime(2026, 3, 30, tzinfo=ATHENS).timestamp())
        self.assertEqual(spring['days'][-1]['seconds'], 82800)

    def test_recording_integration_preserves_energy_version_and_raw(self):
        stamp = datetime(2026, 11, 1, 12, tzinfo=ATHENS).timestamp()
        for metric, total in [('soc', 180), ('load', 1200), ('battery', -600)]:
            self.db.execute('INSERT INTO readings VALUES (?,?,?,?,?)', (stamp, '2026-11-01', metric, total, 2))
        before = self.db.execute('SELECT * FROM readings').fetchall()
        cutoff = datetime(2026, 11, 2, tzinfo=ATHENS).timestamp()
        result = update_summary(self.db, ['2026-11-01'], '2026-11-02', cutoff)
        self.assertEqual(result['version'], 2)
        day = result['trends']['days'][-1]
        self.assertEqual(day['soc']['maximum'], 90)
        self.assertAlmostEqual(day['totals']['load']['kwh'], .01)
        self.assertEqual(day['totals']['load']['seconds'], 60)
        self.assertEqual(before, self.db.execute('SELECT * FROM readings').fetchall())
        json.dumps(result, allow_nan=False)


if __name__ == '__main__':
    unittest.main()
