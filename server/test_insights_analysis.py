"""Deterministic report checks; all history fixtures are temporary and local."""

import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from string import Formatter
from unittest.mock import patch

from insights_analysis import ATHENS, DAY_SECONDS, MAX_FILE_BYTES, analyze, summarize_window
from insights_i18n import LANGUAGES, TEXT, localized


def stamp(year=2026, month=10, day=13, hour=12, minute=0, second=0):
    return int(datetime(year, month, day, hour, minute, second, tzinfo=ATHENS).timestamp())


class InsightsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.now = stamp()

    def write(self, points):
        days = {}
        for point in points:
            day = datetime.fromtimestamp(point['t'], ATHENS).date().isoformat()
            days.setdefault(day, []).append(point)
        for day, rows in days.items():
            (self.root / (day + '.json')).write_text(json.dumps({'day': day, 'points': rows}), encoding='utf-8')

    def points(self, days=8, transform=None):
        points = []
        for value in range(self.now - days * DAY_SECONDS, self.now, 60):
            values = {'pv': 500, 'grid': 100, 'load': 600, 'battery': -200, 'soc': 90}
            row = {'t': value, 'values': values, 'counts': dict.fromkeys(values, 1)}
            if transform:
                transform(row)
            points.append(row)
        return points

    def report(self, transform=None, days=8):
        self.write(self.points(days, transform))
        return analyze(self.root, self.now)

    @staticmethod
    def metrics(report):
        return {metric['key']: metric for metric in report['metrics']}

    @staticmethod
    def ids(report):
        return {finding['id'] for finding in report['findings']}

    def assert_localized_report(self, report):
        self.assertEqual(report['schema_version'], 2)
        fields = [metric['label'] for metric in report['metrics']]
        fields.extend(report['limitations'])
        for finding in report['findings']:
            fields.extend((finding['title'], finding['observation']))
        for field in fields:
            self.assertIsInstance(field, dict)
            self.assertEqual(set(field), set(LANGUAGES))
            for language, value in field.items():
                with self.subTest(language=language, value=value):
                    self.assertIsInstance(value, str)
                    self.assertTrue(value.strip())
                    self.assertNotIn('{', value)
                    if language == 'el':
                        self.assertRegex(value, '[\u0370-\u03ff]')
                    elif language == 'ru':
                        self.assertRegex(value, '[\u0400-\u04ff]')
                    if language != 'en':
                        self.assertNotEqual(value, field['en'])

    def test_normal_complete_history_and_contract(self):
        report = self.report()
        self.assert_localized_report(report)
        self.assertEqual(report['severity'], 'routine')
        self.assertEqual(report['coverage_pct'], 100)
        self.assertEqual(report['data_through'], self.now)
        self.assertEqual(report['window'], {'start': self.now - DAY_SECONDS, 'end': self.now})
        self.assertEqual(report['baseline'], {'start': self.now - 8 * DAY_SECONDS, 'end': self.now - DAY_SECONDS, 'days': 7})
        metrics = self.metrics(report)
        self.assertEqual(metrics['pv']['value'], 12)
        self.assertEqual(metrics['load']['baseline'], 14.4)
        self.assertEqual(metrics['battery_supplied']['value'], 4.8)
        self.assertEqual(metrics['battery_charged']['value'], 0)
        self.assertEqual(metrics['soc_max']['value'], 90)
        self.assertTrue(all(metric['baseline_days'] == 7 for metric in report['metrics']))
        json.dumps(report, allow_nan=False)

    def test_observed_zero_is_not_missing_and_low_solar_never_claims_weather(self):
        def transform(row):
            if row['t'] >= self.now - DAY_SECONDS:
                row['values'].update(pv=0, soc=60)
        report = self.report(transform)
        metrics = self.metrics(report)
        self.assertEqual(metrics['pv']['value'], 0)
        self.assertEqual(metrics['pv']['coverage_pct'], 100)
        self.assertEqual(metrics['pv']['change_pct'], -100)
        self.assertIn('low_solar_energy', self.ids(report))
        self.assertIn('low_battery_maximum', self.ids(report))
        text = next(item['observation'] for item in report['findings'] if item['id'] == 'low_solar_energy')
        self.assertIn('cannot be distinguished', text['en'])

    def test_missing_current_window_is_not_zero_or_routine(self):
        points = [row for row in self.points() if row['t'] < self.now - DAY_SECONDS]
        self.write(points)
        report = analyze(self.root, self.now)
        self.assertEqual(report['severity'], 'data_issue')
        self.assertEqual(report['coverage_pct'], 0)
        self.assertTrue(all(metric['value'] is None for metric in report['metrics']))
        self.assertNotIn('low_solar_energy', self.ids(report))
        self.assertEqual(report['data_through'], self.now - DAY_SECONDS)
        self.assertIn('stale_history', self.ids(report))

    def test_empty_history(self):
        report = analyze(self.root, self.now)
        self.assertIsNone(report['data_through'])
        self.assertEqual(report['severity'], 'data_issue')
        self.assertIn('limited_baseline', self.ids(report))

    def test_battery_directions_share_signed_reading_coverage(self):
        def transform(row):
            if row['t'] >= self.now - DAY_SECONDS:
                row['values']['battery'] = 500 if row['t'] < self.now - DAY_SECONDS / 2 else -250
        metrics = self.metrics(self.report(transform))
        self.assertEqual(metrics['battery_charged']['value'], 6)
        self.assertEqual(metrics['battery_supplied']['value'], 3)
        self.assertEqual(metrics['battery_charged']['coverage_pct'], 100)
        self.assertEqual(metrics['battery_supplied']['coverage_pct'], 100)

    def test_missing_counts_zero_counts_bool_negative_power_and_soc_are_excluded(self):
        def transform(row):
            if row['t'] >= self.now - DAY_SECONDS:
                row['counts']['pv'] = 0
                row['counts'].pop('grid')
                row['values']['load'] = -100
                row['values']['soc'] = 101
                row['values']['battery'] = True
        report = self.report(transform)
        self.assertTrue(all(metric['value'] is None for metric in report['metrics']))
        self.assertEqual(report['coverage_pct'], 0)

    def test_negative_battery_is_valid_not_negative_solar(self):
        def transform(row):
            row['values']['pv'] = -1
        metrics = self.metrics(self.report(transform))
        self.assertIsNone(metrics['pv']['value'])
        self.assertEqual(metrics['battery_supplied']['value'], 4.8)

    def test_insufficient_baseline_requires_four_complete_windows(self):
        report = self.report(days=4)
        self.assertTrue(all(metric['baseline_days'] == 3 for metric in report['metrics']))
        self.assertTrue(all(metric['baseline'] is None for metric in report['metrics']))
        self.assertTrue(all(metric['change_pct'] is None for metric in report['metrics']))
        self.assertEqual(report['severity'], 'notice')
        self.assertIn('limited_baseline', self.ids(report))

    def test_current_window_never_leaks_into_baseline(self):
        def transform(row):
            if row['t'] >= self.now - DAY_SECONDS:
                row['values']['load'] = 2000
        report = self.report(transform)
        self.assertEqual(self.metrics(report)['load']['baseline'], 14.4)
        self.assertEqual(self.metrics(report)['load']['value'], 48)
        self.assertIn('high_load_energy', self.ids(report))
        self.assertEqual(report['severity'], 'warning')

    def test_median_not_mean_and_zero_baseline_has_no_infinite_percentage(self):
        def transform(row):
            row['values']['grid'] = 0
            if row['t'] < self.now - 7 * DAY_SECONDS:
                row['values']['load'] = 9000
            if row['t'] >= self.now - DAY_SECONDS:
                row['values']['grid'] = 500
        report = self.report(transform)
        metrics = self.metrics(report)
        self.assertEqual(metrics['load']['baseline'], 14.4)
        self.assertEqual(metrics['grid']['baseline'], 0)
        self.assertIsNone(metrics['grid']['change_pct'])
        self.assertIn('high_grid_energy', self.ids(report))

    def test_quality_filter_is_per_metric_and_suppresses_partial_current_comparison(self):
        def transform(row):
            if row['t'] < self.now - DAY_SECONDS:
                # Each comparison window loses 12h of PV only.
                if (self.now - row['t']) % DAY_SECONDS < DAY_SECONDS / 2:
                    row['counts']['pv'] = 0
            elif row['t'] < self.now - DAY_SECONDS / 2:
                row['counts']['load'] = 0
        report = self.report(transform)
        metrics = self.metrics(report)
        self.assertIsNone(metrics['pv']['baseline'])
        self.assertEqual(metrics['pv']['baseline_days'], 0)
        self.assertEqual(metrics['load']['baseline_days'], 7)
        self.assertEqual(metrics['load']['coverage_pct'], 50)
        self.assertIsNone(metrics['load']['change_pct'])

    def test_spike_is_not_sustained_high_demand(self):
        def transform(row):
            if row['t'] == self.now - 3600:
                row['values']['load'] = 10_000
        report = self.report(transform)
        self.assertNotIn('sustained_house_load', self.ids(report))

    def test_contiguous_half_hour_high_load_and_gap_breaks_the_run(self):
        def transform(row):
            if self.now - 3600 <= row['t'] < self.now - 1800:
                row['values']['load'] = 3000
        report = self.report(transform)
        finding = next(item for item in report['findings'] if item['id'] == 'sustained_house_load')
        self.assertEqual(finding['end'] - finding['start'], 1800)
        self.assertIn('fixed 3 kW/30-minute threshold', finding['observation']['en'])
        points = self.points(transform=transform)
        for row in points:
            if row['t'] == self.now - 2700:
                row['counts']['load'] = 0
        self.write(points)
        self.assertNotIn('sustained_house_load', self.ids(analyze(self.root, self.now)))

    def test_recording_gap_even_when_overall_coverage_is_above_90(self):
        def transform(row):
            if self.now - 7200 <= row['t'] < self.now - 6300:
                row['counts']['soc'] = 0
        report = self.report(transform)
        self.assertGreater(report['coverage_pct'], 90)
        self.assertIn('recording_gap', self.ids(report))
        self.assertEqual(report['severity'], 'data_issue')

    def test_stale_latest_minute_and_time_floor_determinism(self):
        self.write([row for row in self.points() if row['t'] < self.now - 600])
        first = analyze(self.root, self.now + 1)
        second = analyze(self.root, self.now + 59.99)
        self.assertEqual(first, second)
        self.assertEqual(first['data_through'], self.now - 600)
        self.assertIn('stale_history', self.ids(first))

    def test_midnight_and_both_daylight_saving_boundaries_are_elapsed_windows(self):
        for month, day, hour in ((10, 13, 0), (10, 26, 0), (3, 30, 0)):
            with self.subTest(month=month, day=day):
                self.now = stamp(month=month, day=day, hour=hour)
                self.write(self.points())
                report = analyze(self.root, self.now)
                self.assertEqual(report['window']['end'] - report['window']['start'], DAY_SECONDS)
                self.assertEqual(report['coverage_pct'], 100)
                self.assertEqual(self.metrics(report)['load']['value'], 14.4)

    def test_integration_clips_both_ends_and_deduplicates_minutes(self):
        rows = [{'t': 0, 'values': {'pv': 1200, 'battery': -600, 'soc': 50}},
                {'t': 60, 'values': {'pv': 1200, 'battery': 600, 'soc': 60}},
                {'t': 120, 'values': {'pv': 9000}}]
        summary = summarize_window(rows + [rows[0]], 30, 90)
        self.assertEqual(summary['seconds']['pv'], 60)
        self.assertEqual(summary['values']['pv'], .02)
        self.assertEqual(summary['values']['battery_supplied'], .005)
        self.assertEqual(summary['values']['battery_charged'], .005)
        self.assertEqual(summary['values']['soc_max'], 60)

    def test_full_valid_minute_energy_matches_existing_summary_rules(self):
        from energy_summary import summarize_points
        rows = [{'t': value * 60,
                 'values': {'pv': 800.25, 'grid': 100, 'load': 730, 'battery': -320 if value % 2 else 450, 'soc': 75}}
                for value in range(100) if value % 7]
        expected = summarize_points(rows, 5940)['totals']
        result = summarize_window(rows, 0, 5940)
        for key, existing in (('pv', 'pv'), ('grid', 'grid'), ('load', 'load'),
                              ('battery_supplied', 'battery'), ('battery_charged', 'battery_charged')):
            self.assertAlmostEqual(result['values'][key], expected[existing]['kwh'])

    def test_same_minute_duplicate_not_counted_and_conflicts_rejected(self):
        points = self.points(days=1)
        self.write(points + [points[0]])
        self.assertEqual(self.metrics(analyze(self.root, self.now))['load']['value'], 14.4)
        conflicting = {**points[0], 'values': {**points[0]['values'], 'load': 900}}
        self.write(points + [conflicting])
        with self.assertRaisesRegex(ValueError, 'Conflicting'):
            analyze(self.root, self.now)

    def test_invalid_existing_json_is_error_not_zero(self):
        day = datetime.fromtimestamp(self.now - 60, ATHENS).date().isoformat()
        (self.root / (day + '.json')).write_text('{broken', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Invalid JSON'):
            analyze(self.root, self.now)

    def test_nonfinite_json_is_rejected(self):
        day = datetime.fromtimestamp(self.now - 60, ATHENS).date().isoformat()
        (self.root / (day + '.json')).write_text('{"day": "' + day + '", "points": [], "extra": NaN}', encoding='utf-8')
        with self.assertRaises(ValueError):
            analyze(self.root, self.now)

    def test_wrong_day_and_nonminute_timestamp_rejected(self):
        self.write(self.points(days=1))
        path = next(self.root.glob('*.json'))
        payload = json.loads(path.read_text())
        payload['points'][0]['t'] += 1
        path.write_text(json.dumps(payload))
        with self.assertRaisesRegex(ValueError, 'minute timestamp'):
            analyze(self.root, self.now)
        payload['points'][0]['t'] += DAY_SECONDS - 1
        path.write_text(json.dumps(payload))
        with self.assertRaisesRegex(ValueError, 'different local date'):
            analyze(self.root, self.now)

    def test_index_cannot_introduce_arbitrary_paths(self):
        (self.root / 'index.json').write_text('{"days": ["../../sensitive"]}', encoding='utf-8')
        self.assertEqual(analyze(self.root, self.now)['coverage_pct'], 0)

    def test_invalid_now_is_rejected(self):
        for value in (None, True, float('nan'), float('inf'), 0, 'today', 10 ** 400):
            with self.subTest(value=value), self.assertRaises(ValueError):
                analyze(self.root, value)

    def test_oversized_daily_file_and_directory_are_rejected(self):
        day = datetime.fromtimestamp(self.now - 60, ATHENS).date().isoformat()
        path = self.root / (day + '.json')
        path.mkdir()
        with self.assertRaisesRegex(ValueError, 'regular file'):
            analyze(self.root, self.now)
        path.rmdir()
        path.write_bytes(b' ' * (MAX_FILE_BYTES + 1))
        with self.assertRaisesRegex(ValueError, 'bounded regular file'):
            analyze(self.root, self.now)

    def test_daily_symlink_is_rejected(self):
        day = datetime.fromtimestamp(self.now - 60, ATHENS).date().isoformat()
        target = self.root / 'not-history.json'
        target.write_text('{}')
        try:
            (self.root / (day + '.json')).symlink_to(target)
        except OSError:
            self.skipTest('This system does not permit test symlinks')
        with self.assertRaisesRegex(ValueError, 'symlink'):
            analyze(self.root, self.now)

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'Named FIFO test requires POSIX')
    def test_fifo_is_rejected_without_opening_it(self):
        day = datetime.fromtimestamp(self.now - 60, ATHENS).date().isoformat()
        os.mkfifo(self.root / (day + '.json'))
        with self.assertRaisesRegex(ValueError, 'regular file'):
            analyze(self.root, self.now)

    def test_no_file_changes_during_analysis_and_bounded_findings(self):
        self.write(self.points())
        before = {path.name: path.read_bytes() for path in self.root.iterdir()}
        report = analyze(self.root, self.now)
        after = {path.name: path.read_bytes() for path in self.root.iterdir()}
        self.assertEqual(before, after)
        self.assertLessEqual(len(report['findings']), 6)
        self.assertTrue(all(item['start'] >= report['window']['start'] and item['end'] <= report['window']['end']
                            for item in report['findings']))

    def test_every_template_has_complete_equivalent_placeholders(self):
        evidence = {'coverage': 95.6, 'minutes': 45, 'value': 1.234, 'baseline': 2.345,
                    'baseline_days': 7, 'average': 4.321}
        formatter = Formatter()
        for key, translations in TEXT.items():
            with self.subTest(key=key):
                self.assertEqual(set(translations), set(LANGUAGES))
                expected = [(field, spec, conversion) for _, field, spec, conversion in formatter.parse(translations['en'])
                            if field is not None]
                for language in LANGUAGES:
                    actual = [(field, spec, conversion) for _, field, spec, conversion in formatter.parse(translations[language])
                              if field is not None]
                    self.assertEqual(sorted(expected), sorted(actual))
                result = localized(key, **evidence)
                self.assertEqual(set(result), set(LANGUAGES))
                self.assertTrue(all(isinstance(text, str) and text.strip() for text in result.values()))
                self.assertNotEqual(result['en'], result['el'])
                self.assertNotEqual(result['en'], result['ru'])

    def test_missing_or_empty_translation_fails_without_english_fallback(self):
        for invalid in ({'en': 'English', 'el': 'Ελληνικά'},
                        {'en': 'English', 'el': ' ', 'ru': 'Русский'},
                        {'en': 'English', 'el': 'Ελληνικά', 'ru': 'Русский', 'de': 'Deutsch'}):
            with self.subTest(invalid=invalid), patch.dict(TEXT, {'test.invalid': invalid}):
                with self.assertRaises(ValueError):
                    localized('test.invalid')
        with self.assertRaises(KeyError):
            localized('test.unknown')

    def test_numeric_evidence_uses_matching_precision_and_local_decimal_separator(self):
        result = localized('observation.high_energy', value=12.345, baseline=9.876, baseline_days=5)
        self.assertIn('12.35 kWh', result['en'])
        self.assertIn('9.88 kWh', result['en'])
        self.assertIn('12,35 kWh', result['el'])
        self.assertIn('9,88 kWh', result['el'])
        self.assertIn('12,35 кВт·ч', result['ru'])
        self.assertIn('9,88 кВт·ч', result['ru'])
        self.assertTrue(all('5' in value for value in result.values()))

    def test_all_findings_branches_are_fully_translated(self):
        seen = set()

        def inspect(report):
            self.assert_localized_report(report)
            seen.update(self.ids(report))

        inspect(analyze(self.root, self.now))  # Incomplete, stale and limited.
        inspect(self.report())  # Routine.

        def deviations(row):
            if row['t'] >= self.now - DAY_SECONDS:
                row['values'].update(load=4000, grid=1000, pv=0, soc=60)
        inspect(self.report(deviations))

        def gap(row):
            if self.now - 7200 <= row['t'] < self.now - 6300:
                row['counts']['soc'] = 0
        inspect(self.report(gap))
        expected = {key.split('.', 1)[1] for key in TEXT if key.startswith('title.')}
        self.assertEqual(seen, expected)

    def test_finding_limit_caveat_is_translated_and_evidence_fields_unchanged(self):
        def deviations(row):
            row['counts']['battery'] = 0
            if row['t'] >= self.now - DAY_SECONDS:
                row['values'].update(load=4000, grid=1000, pv=0, soc=60)
        report = self.report(deviations)
        self.assert_localized_report(report)
        self.assertEqual(len(report['findings']), 6)
        self.assertEqual(report['limitations'][-1], localized('limitation.finding_limit'))
        self.assertEqual(report['coverage_pct'], 0)
        self.assertEqual(report['window'], {'start': self.now - DAY_SECONDS, 'end': self.now})
        metrics = self.metrics(report)
        self.assertEqual(metrics['load']['value'], 96)
        self.assertEqual(metrics['load']['baseline'], 14.4)
        self.assertEqual(metrics['load']['baseline_days'], 7)
        self.assertEqual(metrics['load']['unit'], 'kWh')
        self.assertIsNone(metrics['battery_charged']['value'])
        self.assertEqual(metrics['soc_max']['value'], 60)
        self.assertEqual(metrics['soc_max']['unit'], '%')
        self.assertEqual(report, analyze(self.root, self.now + 59))

    def test_largest_localized_analysis_payload_fits_publisher_limit(self):
        # Conservative envelope: combine the six largest formatted findings,
        # all seven limitations, and maximal-width bounded evidence fields,
        # even when those individual maxima cannot occur in a single report.
        # 1e9 W * 24h = 24,000,000 kWh; the longest three-decimal number below
        # that ceiling is longer than the exact ceiling itself. The largest
        # finite float covers every serializable percentage's exponent width.
        evidence = {'coverage': 100.0, 'minutes': 1440, 'value': 24_000_000.0,
                    'baseline': 24_000_000.0, 'baseline_days': 7, 'average': 1_000_000.0}

        def encoded(value):
            return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                              separators=(',', ':')).encode('utf-8')

        candidates = []
        for key in TEXT:
            if not key.startswith('title.'):
                continue
            code = key.split('.', 1)[1]
            observation = 'high_energy' if code in ('high_load_energy', 'high_grid_energy') else code
            candidates.append({'id': code, 'category': 'monitoring', 'severity': 'data_issue',
                               'title': localized(key), 'observation': localized('observation.' + observation, **evidence),
                               'start': 32_503_680_000, 'end': 32_503_680_000, 'metric': 'battery_supplied'})
        report = analyze(self.root, self.now)
        report['generated_at'] = report['data_through'] = 32_503_680_000
        report['window'] = {'start': 32_503_680_000, 'end': 32_503_680_000}
        report['baseline'] = {'start': 32_503_680_000, 'end': 32_503_680_000, 'days': 7}
        report['coverage_pct'] = 99.99
        for metric in report['metrics']:
            metric.update(value=23_999_999.999, baseline=23_999_999.999, coverage_pct=99.99,
                          baseline_days=7, change_pct=-1.7976931348623157e308)
        report['findings'] = sorted(candidates, key=lambda finding: len(encoded(finding)), reverse=True)[:6]
        report['limitations'] = [localized(key) for key in TEXT if key.startswith('limitation.')]
        self.assert_localized_report(report)
        size = len(encoded(report))
        self.assertLess(size, 32_768, f'Conservative localized report size: {size} bytes')
        # Retained for a focused verification command without writing fixtures.
        self.largest_payload_bytes = size


if __name__ == '__main__':
    unittest.main()
