"""Local synthetic preview HTTP contract. Never contacts production."""
import http.client
import threading
import unittest
from datetime import datetime, timedelta
from http.server import ThreadingHTTPServer

from preview_history import ATHENS, Preview


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


if __name__ == '__main__':
    unittest.main()
