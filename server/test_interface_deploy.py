"""Synthetic checks for the interface-only release. No production/network calls."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('interface_release', ROOT / 'deploy/activate-interface.py')
RELEASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RELEASE)


class InterfaceReleaseTests(unittest.TestCase):
    def original_config(self):
        text = (ROOT / 'deploy/nginx.conf.example').read_text(encoding='utf-8')
        text = text.replace(r'|interface\.css|assets/solar-home\.webp', '')
        return text.replace('\n' + RELEASE.NEW_ROUTES, '').encode()

    def test_transform_is_limited_to_two_routes_and_cache_entries(self):
        before = self.original_config()
        after = RELEASE.nginx_candidate(before)
        self.assertIn(RELEASE.NEW_ROUTES.encode(), after)
        restored = after.decode().replace('\n' + RELEASE.NEW_ROUTES, '')
        restored = restored.replace(r'|interface\.css|assets/solar-home\.webp', '')
        self.assertEqual(restored.encode(), before)
        self.assertEqual(after, (ROOT / 'deploy/nginx.conf.example').read_text(encoding='utf-8').encode())

    def test_transform_rejects_changed_or_repeated_config(self):
        before = self.original_config()
        for candidate in (before + before, before.replace(b'insights.css', b'other.css'),
                          RELEASE.nginx_candidate(before)):
            with self.subTest(candidate=candidate[:20]):
                with self.assertRaises(RuntimeError):
                    RELEASE.nginx_candidate(candidate)

    def test_write_allowlist_excludes_data_controls_and_reporting(self):
        self.assertEqual(set(RELEASE.FILES), {'interface.css', 'assets/solar-home.webp',
            'app.js', 'history-cache.js', 'history.js', 'insights.js', 'index.html', 'history.html'})
        for target in (Path('/var/lib/homeenergy-reports/reports.sqlite3'),
                       RELEASE.PUBLIC.parent / 'runtime/history.sqlite3',
                       RELEASE.PUBLIC / '../runtime/history/index.json',
                       Path('/etc/ssh/sshd_config.d/00-homeenergy-reporter-production.conf'),
                       RELEASE.PUBLIC / 'collector-control.js'):
            with self.subTest(target=target):
                with self.assertRaisesRegex(RuntimeError, 'allowlist'):
                    RELEASE.atomic(target, b'not written', dict(uid=0, gid=0, mode=0o644))

    def test_security_headers_required_for_cached_assets(self):
        headers = {'x-frame-options': 'DENY', 'x-content-type-options': 'nosniff',
                   'content-security-policy': "default-src 'self'; script-src 'self'",
                   'strict-transport-security': 'max-age=31536000', 'referrer-policy': 'no-referrer',
                   'cache-control': 'public, max-age=31536000, immutable'}
        RELEASE.check_headers(headers, True)
        for key in headers:
            invalid = headers.copy()
            del invalid[key]
            with self.subTest(key=key):
                with self.assertRaises(RuntimeError):
                    RELEASE.check_headers(invalid, True)
        with self.assertRaises(RuntimeError):
            RELEASE.check_headers(headers, False)

    def test_reload_retry_is_bounded_and_propagates_failure(self):
        with mock.patch.object(RELEASE, 'verify_http', side_effect=[RuntimeError('old worker'), None]) as verify, \
                mock.patch.object(RELEASE.time, 'sleep') as sleep:
            RELEASE.verify_after_reload({}, 'feed')
            self.assertEqual(verify.call_count, 2)
            sleep.assert_called_once_with(0.5)
        with mock.patch.object(RELEASE, 'verify_http', side_effect=RuntimeError('failed')) as verify, \
                mock.patch.object(RELEASE.time, 'sleep'):
            with self.assertRaises(RuntimeError):
                RELEASE.verify_after_reload({}, 'feed')
            self.assertEqual(verify.call_count, 10)

    def test_file_reads_refuse_directories_and_symlinks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            with self.assertRaises(RuntimeError):
                RELEASE.read(root)
            target = root / 'data.txt'
            target.write_bytes(b'synthetic')
            self.assertEqual(RELEASE.read(target), b'synthetic')
            link = root / 'link.txt'
            try:
                link.symlink_to(target)
            except OSError:
                return  # Windows may require an existing symlink privilege.
            with self.assertRaises(RuntimeError):
                RELEASE.read(link)


if __name__ == '__main__':
    unittest.main()
