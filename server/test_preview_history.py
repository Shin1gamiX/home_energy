"""Local synthetic preview HTTP contract. Never contacts production."""
import http.client
import re
import threading
import unittest
from datetime import datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path

from preview_history import ATHENS, CACHEABLE_ASSETS, Preview


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
