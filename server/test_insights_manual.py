"""Manual conversion tests: synthetic data only; no SSH or model calls."""
from datetime import datetime
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch, Mock

import insights_analysis as legacy
import insights_manual as manual
import insights_manual_client as client
import insights_manual_entry as entry
import insights_publish as writer


NOW = int(datetime(2026, 10, 17, 6, tzinfo=legacy.ATHENS).timestamp())


def rows_for(start, end, missing=False):
    rows = []
    for stamp in range(start, end, 60):
        hour = datetime.fromtimestamp(stamp, legacy.ATHENS).hour
        values = {'pv': 1000 if 8 <= hour < 18 else 0, 'load': 500,
                  'grid': 100, 'battery': -200, 'soc': 90}
        if missing:
            values.pop('load')
        rows.append({'t': stamp, 'values': values})
    return rows


class ManualTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.history = self.root / 'history'
        self.history.mkdir()
        self.state = self.root / 'reports'
        self.state.mkdir()
        (self.state / 'public').mkdir()

    def review(self, hours=24, days=7, now=NOW, missing=False):
        with patch.object(legacy, '_load_points', side_effect=lambda _, start, end: rows_for(start, end, missing)):
            return manual.analyze(self.history, now, {'hours': hours, 'baseline_days': days, 'save': False})

    def test_requested_periods_and_nonoverlap(self):
        for hours, days in [(6, 7), (12, 14), (24, 30), (50, 7), (50, 30), (168, 90)]:
            with self.subTest(hours=hours, days=days):
                analysis, context = self.review(hours, days)
                self.assertEqual(analysis['window']['end'] - analysis['window']['start'], hours * 3600)
                self.assertEqual(analysis['baseline']['days'], days)
                previous = analysis['window']['start']
                for window in context['comparison_windows']:
                    self.assertLessEqual(window['end'], previous)
                    self.assertGreaterEqual(window['start'], analysis['baseline']['start'])
                    self.assertEqual(window['end'] - window['start'], hours * 3600)
                    previous = window['start']
                self.assertEqual(len(context['hourly']), hours)
                load = next(item for item in analysis['metrics'] if item['key'] == 'load')
                self.assertEqual(load['value'], .5 * hours)
                self.assertEqual(load['baseline'], .5 * hours)
                self.assertEqual(load['change_pct'], 0)
                self.assertLess(len(writer.encoded(analysis)), writer.MAX_ANALYSIS)
                self.assertLess(len(writer.encoded(context)), 220000)

    def test_night_not_compared_with_daylight_solar(self):
        analysis, _ = self.review(6, 14)
        pv = next(item for item in analysis['metrics'] if item['key'] == 'pv')
        self.assertEqual((pv['value'], pv['baseline']), (0, 0))
        self.assertIsNone(pv['change_pct'])
        self.assertNotIn('low_solar_energy', [f['id'] for f in analysis['findings']])

    def test_dst_durations_and_baseline_exclusion(self):
        for month, day in [(3, 30), (10, 26)]:
            for hours in (6, 24, 50):
                end = int(datetime(2026, month, day, 6, tzinfo=legacy.ATHENS).timestamp())
                start = end - hours * 3600
                lower, windows = manual.comparison_windows(start, end, 14)
                previous = start
                for left, right in windows:
                    self.assertGreaterEqual(left, lower)
                    self.assertLessEqual(right, previous)
                    self.assertEqual(right - left, hours * 3600)
                    previous = left

    def test_missing_data_is_not_zero_or_normal(self):
        analysis, _ = self.review(12, 7, missing=True)
        load = next(item for item in analysis['metrics'] if item['key'] == 'load')
        self.assertIsNone(load['value'])
        self.assertIsNone(load['baseline'])
        self.assertEqual(analysis['severity'], 'data_issue')

    def test_insufficient_baseline_withheld(self):
        analysis, _ = self.review(50, 1)
        self.assertEqual(analysis['baseline']['windows'], 0)
        self.assertTrue(all(item['baseline'] is None for item in analysis['metrics']))

    def test_comparison_threshold_scales_with_hours(self):
        def changed(_, start, end):
            rows = rows_for(start, end)
            for row in rows:
                if row['t'] >= end - 6 * 3600:
                    row['values']['load'] = 900
            return rows
        with patch.object(legacy, '_load_points', side_effect=changed):
            analysis, _ = manual.analyze(self.history, NOW, {'hours': 6, 'save': False})
        self.assertIn('high_load_energy', [f['id'] for f in analysis['findings']])

    def test_invalid_options(self):
        for item in [{}, {'save': 0}, {'save': False, 'hours': True}, {'save': False, 'hours': 0},
                     {'save': False, 'hours': 169}, {'save': False, 'baseline_days': 91},
                     {'save': False, 'path': '/etc/passwd'}, {'save': False, 'end': NOW + 60},
                     {'save': False, 'end': NOW - 1}]:
            with self.subTest(item=item), self.assertRaises(writer.Rejected):
                manual.options(item, NOW)

    def test_review_writes_nothing(self):
        sentinel = self.state / 'existing-evidence'
        sentinel.write_bytes(b'untouched')
        before = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        response = manual.prepare(b'{"hours":6,"baseline_days":30,"save":false}', self.state, self.history, NOW)
        self.assertFalse(response['saved'])
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_prepare_publish_and_existing_evidence_preserved(self):
        old = writer.prepare(self.state, self.history, NOW - 7 * 86400)
        prose = {'en': 'No readings.', 'el': 'Δεν υπάρχουν μετρήσεις.', 'ru': 'Показаний нет.'}
        payload = {'analysis_id': old['analysis_id'], 'summary': prose, 'notes': []}
        writer.publish(writer.encoded(payload), self.state, NOW - 7 * 86400)
        with closing(sqlite3.connect(self.state / 'reports.sqlite3')) as db:
            old_reports = db.execute('SELECT * FROM reports').fetchall()
            old_snapshots = db.execute('SELECT * FROM snapshots').fetchall()
        feed_before = (self.state / 'public/index.json').read_bytes()
        response = manual.prepare(b'{"hours":50,"baseline_days":30,"save":true}', self.state, self.history, NOW)
        self.assertTrue(response['saved'])
        self.assertEqual((self.state / 'public/index.json').read_bytes(), feed_before)
        payload['analysis_id'] = response['analysis_id']
        self.assertEqual(writer.publish(writer.encoded(payload), self.state, NOW)['status'], 'published')
        self.assertEqual(writer.publish(writer.encoded(payload), self.state, NOW)['status'], 'already_published')
        with closing(sqlite3.connect(self.state / 'reports.sqlite3')) as db:
            for row in old_reports:
                self.assertEqual(db.execute('SELECT * FROM reports WHERE id=?', (row[0],)).fetchone(), row)
            for row in old_snapshots:
                self.assertEqual(db.execute('SELECT * FROM snapshots WHERE id=?', (row[0],)).fetchone(), row)
        payload['summary'] = dict(prose, en='Changed')
        with self.assertRaises(writer.Rejected):
            writer.publish(writer.encoded(payload), self.state, NOW)

    def test_translations_and_size(self):
        analysis, _ = self.review()
        fields = analysis['limitations'] + [item['label'] for item in analysis['metrics']]
        fields += [item[key] for item in analysis['findings'] for key in ('title', 'observation')]
        for field in fields:
            self.assertEqual(set(field), {'en', 'el', 'ru'})
            self.assertTrue(all(isinstance(value, str) and value for value in field.values()))

    def test_exact_command_allowlist_and_json_required(self):
        stub = Mock()
        for command in ('prepare', 'publish', 'manual-review; id', 'manual-publish --file /etc/passwd', 'sh', ''):
            with self.assertRaises(ValueError):
                entry.dispatch(command, b'{}', stub, stub)
        with self.assertRaises(ValueError):
            entry.dispatch('manual-prepare', b'', stub, stub)
        stub.assert_not_called()
        entry.dispatch('manual-review', b'{"save":false}', stub, writer)
        stub.prepare.assert_called_once_with(b'{"save":false}')
        with self.assertRaises(ValueError):
            entry.dispatch('manual-review', b'{"save":true}', stub, writer)

    def test_client_uses_fixed_ssh_and_no_shell(self):
        args = client.ssh_arguments('prepare')
        self.assertEqual(args[-2:], [client.DESTINATION, 'manual-prepare'])
        self.assertIn('StrictHostKeyChecking=yes', args)
        self.assertIn('ClearAllForwardings=yes', args)
        self.assertNotIn('codex_access_key', ' '.join(args))
        with self.assertRaises(ValueError):
            client.ssh_arguments('prepare; whoami')

    def test_client_timeout_no_retry(self):
        with patch.object(client.subprocess, 'run', side_effect=subprocess.TimeoutExpired('ssh', 55)) as run:
            with self.assertRaisesRegex(RuntimeError, 'uncertain'):
                client.call('publish', {'analysis_id': 'a' * 64})
            self.assertEqual(run.call_count, 1)

    def test_client_rejects_old_server_save_status(self):
        analysis = {'schema_version': 2}
        digest = hashlib.sha256(writer.encoded(analysis)).hexdigest()
        raw = writer.encoded({'scope': 'production', 'synthetic': False,
                              'result': {'analysis_id': digest, 'analysis': analysis}})
        with patch.object(client.subprocess, 'run', return_value=Mock(returncode=0, stdout=raw)):
            with self.assertRaisesRegex(ValueError, 'manual server support'):
                client.call('prepare', {'save': False})


if __name__ == '__main__':
    unittest.main()
