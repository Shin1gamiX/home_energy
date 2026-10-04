"""Exercise the shipped 404 block with isolated, loopback-only Nginx.

No production configuration, credentials, telemetry or device actions are used.
"""

import http.client
import pathlib
import shutil
import socket
import subprocess
import tempfile
import time
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
NGINX = shutil.which("nginx")
if not NGINX and pathlib.Path("/usr/sbin/nginx").is_file():
    NGINX = "/usr/sbin/nginx"


def not_found_block():
    source = (ROOT / "deploy/nginx.conf.example").read_text(encoding="utf-8")
    start = "    # BEGIN Home Energy not found"
    end = "    # END Home Energy not found"
    if source.count(start) != 1 or source.count(end) != 1:
        raise AssertionError("Expected exactly one authoritative 404 block")
    return source[source.index(start):source.index(end) + len(end)]


@unittest.skipUnless(NGINX, "An existing Nginx installation is required")
class NotFoundNginxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="homeenergy-404-test-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.directory = pathlib.Path(cls.temp.name)
        public = cls.directory / "public"
        public.mkdir()
        for name in ("404.html", "404.css", "404.js", "styles.css"):
            shutil.copyfile(ROOT / name, public / name)
        cls.page = (public / "404.html").read_bytes()
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            cls.port = probe.getsockname()[1]
        cls.access_log = cls.directory / "access.log"
        configuration = cls.directory / "nginx.conf"
        configuration.write_text(
            'daemon off;\nmaster_process off;\n'
            f'pid "{cls.directory / "nginx.pid"}";\n'
            f'error_log "{cls.directory / "error.log"}" notice;\n'
            'events { worker_connections 32; }\nhttp {\n'
            'types { text/html html; text/css css; application/javascript js; }\n'
            f'access_log "{cls.access_log}";\n'
            'server {\n'
            f'listen 127.0.0.1:{cls.port};\nroot "{public}";\n'
            'server_tokens off;\n'
            'add_header X-Content-Type-Options nosniff always;\n'
            'add_header Cache-Control "no-store, max-age=0" always;\n'
            'add_header Content-Security-Policy "default-src \'self\'; '
            'script-src \'self\'; object-src \'none\'; base-uri \'none\'" always;\n'
            + not_found_block() + '\n'
            'location = / { return 200 "overview fixture"; }\n'
            'location = /history.html { return 200 "history fixture"; }\n'
            'location = /styles.css { try_files $uri =404; }\n'
            'location = /api/collector/status { access_log off; return 404; }\n'
            '# A sentinel, never a real proxy or device action.\n'
            'location = /api/collector/restart { return 418 "control sentinel"; }\n'
            'location / { return 404; }\n}\n}\n', encoding="utf-8",
        )
        subprocess.run([NGINX, "-t", "-p", str(cls.directory), "-c", str(configuration)],
                       check=True, capture_output=True)
        cls.process = subprocess.Popen(
            [NGINX, "-p", str(cls.directory), "-c", str(configuration)],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        cls.addClassCleanup(cls.stop_nginx)
        for _ in range(100):
            if cls.process.poll() is not None:
                raise RuntimeError(cls.process.stderr.read().decode("utf-8", "replace"))
            try:
                cls.request("/")
                return
            except OSError:
                time.sleep(0.05)
        raise RuntimeError("Isolated Nginx did not start")

    @classmethod
    def stop_nginx(cls):
        if cls.process.poll() is None:
            cls.process.terminate()
            try:
                cls.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                cls.process.kill()
                cls.process.wait(timeout=5)
        cls.process.stderr.close()

    @classmethod
    def request(cls, path, method="GET"):
        connection = http.client.HTTPConnection("127.0.0.1", cls.port, timeout=3)
        try:
            connection.request(method, path)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_missing_nested_and_private_paths_keep_real_404(self):
        for path in ("/missing-page", "/nested/missing/page?secret-probe=not-reflected",
                     "/server/collector_control.py", "/.git/config", "/404.html"):
            with self.subTest(path=path):
                status, headers, body = self.request(path)
                self.assertEqual(status, 404)
                self.assertEqual(body, self.page)
                self.assertIn("text/html", headers["Content-Type"])
                self.assertNotIn("Location", headers)
                self.assertNotIn(b"secret-probe", body)

    def test_status_remains_blocked_for_all_methods(self):
        for method in ("GET", "HEAD", "POST"):
            with self.subTest(method=method):
                status, _, body = self.request("/api/collector/status?privacy-probe=hidden", method)
                self.assertEqual(status, 404)
                self.assertEqual(body, b"" if method == "HEAD" else self.page)

    def test_head_is_404_without_body(self):
        status, headers, body = self.request("/nested/missing", "HEAD")
        self.assertEqual(status, 404)
        self.assertEqual(int(headers["Content-Length"]), len(self.page))
        self.assertEqual(body, b"")

    def test_assets_are_allowlisted_and_serve_correct_content_types(self):
        for name, content_type in (("404.css", "text/css"), ("404.js", "application/javascript"),
                                   ("styles.css", "text/css")):
            with self.subTest(name=name):
                status, headers, body = self.request("/" + name)
                self.assertEqual(status, 200)
                self.assertIn(content_type, headers["Content-Type"])
                self.assertEqual(body, (ROOT / name).read_bytes())

    def test_security_headers_survive_error_redirect(self):
        for path in ("/missing", "/api/collector/status"):
            status, headers, _ = self.request(path)
            self.assertEqual(status, 404)
            self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
            self.assertIn("no-store", headers["Cache-Control"])
            self.assertIn("script-src 'self'", headers["Content-Security-Policy"])

    def test_normal_and_control_routes_are_not_replaced(self):
        self.assertEqual(self.request("/")[0], 200)
        self.assertEqual(self.request("/history.html")[0], 200)
        self.assertEqual(self.request("/api/collector/restart", "POST")[0], 418)

    def test_error_redirect_does_not_reintroduce_status_access_logging(self):
        self.request("/api/collector/status?unique-private-status-marker=not-logged")
        self.request("/missing?unique-private-error-marker=not-logged")
        self.request("/history.html?public-log-marker=logged")
        log = self.access_log.read_text(encoding="utf-8")
        self.assertIn("public-log-marker", log)
        self.assertNotIn("unique-private-status-marker", log)
        self.assertNotIn("unique-private-error-marker", log)


if __name__ == "__main__":
    unittest.main()
