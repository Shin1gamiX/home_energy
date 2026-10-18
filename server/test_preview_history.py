"""Local synthetic preview HTTP contract. Never contacts production."""
import http.client
import json
import re
import shutil
import subprocess
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path

from preview_history import ATHENS, CACHEABLE_ASSETS, Preview, preview_reports


class QuietPreview(Preview):
    def log_message(self, *args):
        pass


class PreviewHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), QuietPreview)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        # A completed day with synthetic readings, unaffected by clock ticks.
        day = datetime.now(ATHENS).date() - timedelta(days=1)
        if day.day % 17 == 0:
            day -= timedelta(days=1)
        cls.day_path = '/history/' + day.isoformat() + '.json'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join()
        cls.server.server_close()

    def request(self, path, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        try:
            connection.request('GET', path, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_day_revalidates_without_body_and_changed_validator_gets_body(self):
        status, headers, body = self.request(self.day_path)
        self.assertEqual(status, 200)
        self.assertIn('no-store', headers['Cache-Control'])
        self.assertGreater(len(body), 1000)
        status, validated, body = self.request(self.day_path, {'If-None-Match': headers['ETag']})
        self.assertEqual(status, 304)
        self.assertEqual(body, b'')
        self.assertEqual(validated['ETag'], headers['ETag'])
        status, _, body = self.request(self.day_path, {'If-None-Match': '"old-version"'})
        self.assertEqual(status, 200)
        self.assertGreater(len(body), 1000)

    def test_only_versioned_assets_can_be_cached(self):
        for name in CACHEABLE_ASSETS:
            status, headers, _ = self.request('/' + name + '?v=test-release')
            self.assertEqual(status, 200)
            self.assertIn('immutable', headers['Cache-Control'])
        for path in ('/history.html', '/history-cache.js', '/history-cache.js?v=bad.value',
                     '/missing.js?v=test', '/collector-control.js?v=test'):
            _, headers, _ = self.request(path)
            self.assertIn('no-store', headers['Cache-Control'])

    def test_comparison_is_opt_in_and_local_only(self):
        status, headers, body = self.request('/history.html?compare=1')
        self.assertEqual(status, 200)
        self.assertIn('no-store', headers['Cache-Control'])
        self.assertIn(b'/preview/history-comparison.js', body)
        self.assertIn(b'/preview/history-comparison.css', body)
        for route in ('/history.html', '/history.html?compare=0', '/'):
            _, _, normal = self.request(route)
            self.assertNotIn(b'/preview/history-comparison.', normal)

    def test_report_fixtures_respect_evidence_and_coverage(self):
        status, headers, body = self.request('/reports/index.json')
        self.assertEqual(status, 200)
        self.assertIn('no-store', headers['Cache-Control'])
        feed = json.loads(body)
        self.assertEqual(feed['schema_version'], 2)
        reports = feed['reports']
        self.assertEqual(len(reports), 5)
        self.assertEqual([r['published_at'] for r in reports],
                         sorted((r['published_at'] for r in reports), reverse=True))
        self.assertNotIn('grid-supply', reports[0]['narrative']['notes'][0]['commentary']['en'])
        warning = next(r['analysis'] for r in reports if r['analysis']['severity'] == 'warning')
        house = next(m for m in warning['metrics'] if m['key'] == 'house_usage')
        self.assertGreaterEqual(house['value'], 2 * house['baseline'])
        self.assertGreaterEqual(house['value'] - house['baseline'], 10)
        self.assertAlmostEqual(house['change_pct'], round((house['value'] / house['baseline'] - 1) * 100, 1))
        incomplete = next(r['analysis'] for r in reports if r['analysis']['severity'] == 'data_issue')
        self.assertEqual(incomplete['coverage_pct'], 73)
        self.assertTrue(all(m['change_pct'] is None for m in incomplete['metrics']))
        self.assertTrue(all(any('Synthetic' in item['en'] for item in r['analysis']['limitations']) for r in reports))
        for report in reports:
            analysis, narrative = report['analysis'], report['narrative']
            self.assertEqual(analysis['schema_version'], 2)
            localized = [m['label'] for m in analysis['metrics']]
            for finding in analysis['findings']:
                localized.extend((finding['title'], finding['observation']))
            localized.extend(analysis['limitations'])
            localized.append(narrative['summary'])
            localized.extend(note['commentary'] for note in narrative['notes'])
            for field in localized:
                self.assertEqual(set(field), {'en', 'el', 'ru'})
                self.assertTrue(all(isinstance(value, str) and value.strip() for value in field.values()))
                self.assertEqual(len(set(field.values())), 3, 'Preview fields must be genuinely translated')

    def assert_browser_feed(self, feed):
        result = subprocess.run([
            'node', '-e',
            "const assert=require('node:assert/strict');"
            "const ui=require('./insights.js'); let raw='';"
            "process.stdin.setEncoding('utf8'); process.stdin.on('data',value=>raw+=value);"
            "process.stdin.on('end',()=>{const reports=ui.validateFeed(JSON.parse(raw));"
            "assert.ok(reports.length); for(const report of reports) for(const lang of ['en','el','ru']) {"
            "const selected=ui.localizeReport(report,lang);"
            "assert.equal(selected.narrative.summary,report.narrative.summary[lang]);"
            "assert.deepEqual(selected.analysis.window,report.analysis.window);"
            "assert.ok(selected.analysis.metrics.every(m=>typeof m.label==='string'));"
            "assert.ok(selected.analysis.findings.every(f=>typeof f.title==='string'&&typeof f.observation==='string'));"
            "assert.ok(selected.analysis.limitations.every(value=>typeof value==='string'));"
            "assert.ok(selected.narrative.notes.every(note=>typeof note.commentary==='string'));}});"
        ], cwd=Path(__file__).resolve().parents[1], input=json.dumps(feed),
            text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which('node'), 'Node is required for the browser contract check')
    def test_localized_preview_matches_browser_schema(self):
        self.assert_browser_feed(preview_reports(1791889200))

    @unittest.skipUnless(shutil.which('node'), 'Node is required for the browser contract check')
    def test_real_analyzer_publisher_feed_matches_browser_schema(self):
        from insights_publish import encoded, prepare, publish
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            state, history = root / 'reports', root / 'history'
            state.mkdir()
            (state / 'public').mkdir()
            history.mkdir()
            snapshot = prepare(state, history, 1791889200)
            payload = {'analysis_id': snapshot['analysis_id'], 'notes': [], 'summary': {
                'en': 'No recorded readings were available for this review.',
                'el': 'Δεν υπήρχαν καταγεγραμμένες μετρήσεις για αυτή την ανασκόπηση.',
                'ru': 'Для этого обзора записанные показания отсутствуют.'}}
            publish(encoded(payload), state, 1791889200)
            self.assert_browser_feed(json.loads((state / 'public/index.json').read_text(encoding='utf-8')))

    def test_nginx_policy_matches_preview_and_preserves_allowlist(self):
        source = (Path(__file__).resolve().parents[1] / 'deploy/nginx.conf.example').read_text()
        pattern = re.search(r'"~([^"]+)" "public, max-age=31536000, immutable"', source).group(1)
        for name in CACHEABLE_ASSETS:
            self.assertRegex('200:/' + name + ':release-1', pattern)
            self.assertRegex('304:/' + name + ':release-1', pattern)
        for key in ('200:/history.html:release-1', '404:/404.html:release-1',
                    '200:/api/collector/status:release-1', '200:/api/energy:release-1',
                    '200:/history/2026-10-05.json:release-1', '200:/styles.css:', '404:/styles.css:release-1'):
            self.assertNotRegex(key, pattern)
        self.assertIn('location = /history-cache.js { try_files $uri =404; }', source)
        self.assertIn('location / { return 404; }', source)
        self.assertEqual(source.count('add_header Cache-Control '), 1, 'Keep headers inherited at server level')


if __name__ == '__main__':
    unittest.main()
