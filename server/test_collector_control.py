import concurrent.futures
import getpass
import http.client
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
from types import SimpleNamespace
import unittest
import warnings
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler

from collector_control import (CollectorControl, ControlServer, NoRedirect, PolicyStore,
                               SYSTEMD_CREDENTIAL_DIRECTORY, password_record, prompt_secret,
                               read_private, verify_password, write_new_private)


@unittest.skipIf(os.name == "nt", "Unix credential permissions; also run on deployment host")
class CredentialPermissionTests(unittest.TestCase):
    def setUp(self):
        self.directory = SYSTEMD_CREDENTIAL_DIRECTORY
        self.path = self.directory / "password.hash"
        self.metadata = {}
        for parent in (self.directory, *self.directory.parents):
            self.metadata[parent] = self.info(stat.S_IFDIR | (0o550 if parent == self.directory else 0o755))
        self.metadata[self.path] = self.info(stat.S_IFREG | 0o440)
        env = patch.dict(os.environ, {"CREDENTIALS_DIRECTORY": str(self.directory)}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        stats = patch.object(Path, "lstat", autospec=True, side_effect=lambda path: self.metadata[path])
        stats.start()
        self.addCleanup(stats.stop)
        reader = patch.object(Path, "read_text", return_value="test-secret\n")
        self.reader = reader.start()
        self.addCleanup(reader.stop)

    @staticmethod
    def info(mode, uid=0, gid=0):
        return SimpleNamespace(st_mode=mode, st_uid=uid, st_gid=gid)

    def assert_rejected(self, path=None):
        self.reader.reset_mock()
        with self.assertRaises((ValueError, OSError)):
            read_private(path or self.path)
        self.reader.assert_not_called()

    def test_systemd_read_only_copies_are_accepted(self):
        for name in ("password.hash", "ha.token"):
            path = self.directory / name
            self.metadata[path] = self.info(stat.S_IFREG | 0o440)
            self.assertEqual(read_private(path), "test-secret")

    def test_ordinary_owner_only_file_still_works(self):
        path = Path("/private/secret")
        self.metadata[path] = self.info(stat.S_IFREG | 0o600, uid=1000, gid=1000)
        self.assertEqual(read_private(path), "test-secret")

    def test_ordinary_group_readable_file_is_rejected(self):
        path = Path("/etc/homeenergy-control/password.hash")
        self.metadata[path] = self.info(stat.S_IFREG | 0o440)
        self.assert_rejected(path)

    def test_missing_or_wrong_systemd_environment_is_rejected(self):
        for value in ("", "/tmp/credentials", str(self.directory) + "/"):
            with self.subTest(value=value), patch.dict(os.environ, {"CREDENTIALS_DIRECTORY": value}):
                self.assert_rejected()

    def test_non_root_file_ownership_is_rejected(self):
        for uid, gid in ((1000, 0), (0, 1000)):
            self.metadata[self.path] = self.info(stat.S_IFREG | 0o440, uid=uid, gid=gid)
            self.assert_rejected()

    def test_unsafe_file_modes_are_rejected(self):
        for mode in (0o444, 0o640, 0o460, 0o450, 0o2440):
            with self.subTest(mode=oct(mode)):
                self.metadata[self.path] = self.info(stat.S_IFREG | mode)
                self.assert_rejected()

    def test_unknown_credential_name_is_rejected(self):
        path = self.directory / "other.secret"
        self.metadata[path] = self.info(stat.S_IFREG | 0o440)
        self.assert_rejected(path)

    def test_symlink_or_nonregular_secret_is_rejected(self):
        for kind in (stat.S_IFLNK, stat.S_IFDIR, stat.S_IFIFO):
            self.metadata[self.path] = self.info(kind | 0o600)
            self.assert_rejected()

    def test_unsafe_credential_directory_is_rejected(self):
        for mode in (0o750, 0o570, 0o555):
            self.metadata[self.directory] = self.info(stat.S_IFDIR | mode)
            self.assert_rejected()

    def test_untrusted_ancestors_are_rejected(self):
        parent = self.directory.parent
        for info in (self.info(stat.S_IFDIR | 0o775), self.info(stat.S_IFDIR | 0o777),
                     self.info(stat.S_IFDIR | 0o755, uid=1000),
                     self.info(stat.S_IFDIR | 0o755, gid=1000), self.info(stat.S_IFLNK | 0o755)):
            self.metadata[parent] = info
            self.assert_rejected()

    def test_unreadable_metadata_fails_closed(self):
        with patch.object(Path, "lstat", side_effect=PermissionError):
            self.assert_rejected()


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = 1000.0
        self.path = Path(self.temp.name) / "state.sqlite3"
        self.store = PolicyStore(self.path, lambda: self.now)

    def record_failure(self, ip="192.0.2.1"):
        ticket, error, _ = self.store.reserve_verification(ip)
        self.assertIsNone(error)
        return self.store.finish_verification(ticket, False)

    def test_two_failures_lock_ip_for_five_minutes(self):
        self.assertEqual(self.record_failure(), ("invalid_password", 0))
        self.assertEqual(self.record_failure(), ("locked", 300))
        self.assertEqual(self.store.reserve_verification("192.0.2.1"), (None, "locked", 300))
        self.assertIsNotNone(self.store.reserve_verification("192.0.2.2")[0])
        self.now += 299
        self.assertEqual(self.store.lockout_seconds("192.0.2.1"), 1)
        self.now += 1
        self.assertIsNotNone(self.store.reserve_verification("192.0.2.1")[0])

    def test_concurrent_attempts_cannot_bypass_reservations(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.store.reserve_verification("192.0.2.1"), range(12)))
        tickets = [result[0] for result in results if result[0]]
        self.assertEqual(len(tickets), 2)
        for ticket in tickets:
            self.store.finish_verification(ticket, False)
        self.assertEqual(self.store.lockout_seconds("192.0.2.1"), 300)

    def test_restart_global_atomic_and_persistent(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.store.reserve_restart(), range(12)))
        self.assertEqual(results.count(0), 1)
        self.assertEqual(results.count(300), 11)
        reopened = PolicyStore(self.path, lambda: self.now)
        self.assertEqual(reopened.reserve_restart(), 300)
        self.now += 300
        self.assertEqual(reopened.reserve_restart(), 0)

    def test_global_verification_budget(self):
        for index in range(30):
            self.assertIsNotNone(self.store.reserve_verification("192.0.2." + str(index))[0])
        self.assertEqual(self.store.reserve_verification("198.51.100.1"), (None, "busy", 300))
        self.now += 300
        self.assertIsNotNone(self.store.reserve_verification("198.51.100.1")[0])

    def test_success_clears_failed_attempt(self):
        self.record_failure()
        ticket, _, _ = self.store.reserve_verification("192.0.2.1")
        self.assertEqual(self.store.finish_verification(ticket, True), (None, 0))
        self.assertEqual(self.record_failure(), ("invalid_password", 0))

    def test_lockout_persists_when_service_reopens_database(self):
        self.record_failure()
        self.record_failure()
        reopened = PolicyStore(self.path, lambda: self.now)
        self.assertEqual(reopened.reserve_verification("192.0.2.1"), (None, "locked", 300))

    def test_missing_secret_configuration_disables_control(self):
        with patch.dict(os.environ, {"CONTROL_STATE_FILE": str(self.path)}, clear=True):
            self.assertFalse(CollectorControl.from_environment().enabled)

    def test_hidden_input_failure_does_not_fall_back_to_echo(self):
        def broken_terminal(*args):
            warnings.warn("hidden input unavailable", getpass.GetPassWarning)
            self.fail("Input must not continue after fallback warning")
        with patch("collector_control.getpass.getpass", side_effect=broken_terminal):
            with self.assertRaises(getpass.GetPassWarning):
                prompt_secret("Password: ")

    def test_private_file_never_overwrites_existing_secret(self):
        target = Path(self.temp.name) / "secret"
        write_new_private(target, "first")
        with self.assertRaises(FileExistsError):
            write_new_private(target, "second")
        self.assertEqual(target.read_text().strip(), "first")
        if os.name != "nt":
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password = "test-password-very-long"
        cls.record = password_record(cls.password)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = PolicyStore(Path(self.temp.name) / "state.sqlite3")
        self.control = CollectorControl(self.store, ["https://energy.example.test"],
                                        self.record, "test-token", "button.example_restart_collector")
        self.dispatch = patch.object(self.control, "dispatch_restart", return_value="requested").start()
        self.addCleanup(patch.stopall)
        self.server = ControlServer(("127.0.0.1", 0), self.control)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, password=None, headers=None, method="POST", path="/api/collector/restart", raw=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        request_headers = {"Origin": "https://energy.example.test", "X-Real-IP": "192.0.2.1", "Content-Type": "application/json"}
        request_headers.update(headers or {})
        request_headers = {key: value for key, value in request_headers.items() if value is not None}
        body = raw if raw is not None else json.dumps({"password": password or self.password})
        connection.request(method, path, body=body if method == "POST" else None, headers=request_headers)
        response = connection.getresponse()
        payload = json.loads(response.read())
        status = response.status
        self.assertEqual(response.getheader("Cache-Control"), "no-store")
        connection.close()
        return status, payload

    def test_password_hash(self):
        self.assertTrue(verify_password(self.password, self.record))
        self.assertFalse(verify_password("wrong", self.record))
        self.assertNotIn(self.password, json.dumps(self.record))

    def test_valid_restart_global_cooldown_across_ips(self):
        status, payload = self.request()
        self.assertEqual(status, 202)
        self.assertEqual(payload["state"], "requested")
        self.assertEqual(self.request(headers={"X-Real-IP": "192.0.2.2"})[1]["error"], "cooldown")
        self.dispatch.assert_called_once()

    def test_wrong_password_then_lock_and_status(self):
        self.assertEqual(self.request("wrong")[0], 401)
        self.assertEqual(self.request("wrong")[1]["error"], "locked")
        self.assertEqual(self.request()[1]["error"], "locked")
        status, payload = self.request(method="GET", path="/api/collector/status")
        self.assertEqual(status, 200)
        self.assertGreater(payload["lockout_seconds"], 0)
        self.dispatch.assert_not_called()

    def test_ipv6_privacy_addresses_share_lockout(self):
        self.request("wrong", headers={"X-Real-IP": "2001:db8:1::1"})
        self.assertEqual(self.request("wrong", headers={"X-Real-IP": "2001:db8:1::2"})[1]["error"], "locked")
        self.assertEqual(self.request(headers={"X-Real-IP": "2001:db8:1::3"})[1]["error"], "locked")

    def test_ipv4_mapped_addresses_share_lockout(self):
        self.request("wrong")
        self.assertEqual(self.request("wrong", headers={"X-Real-IP": "::ffff:192.0.2.1"})[1]["error"], "locked")

    def test_origin_and_fetch_site_rejected_without_action(self):
        self.assertEqual(self.request(headers={"Origin": "https://evil.example"})[0], 403)
        self.assertEqual(self.request(headers={"Origin": "null"})[0], 403)
        self.assertEqual(self.request(headers={"Origin": None})[0], 403)
        self.assertEqual(self.request(headers={"Sec-Fetch-Site": "cross-site"})[0], 403)
        self.dispatch.assert_not_called()

    def test_invalid_body_content_type_and_ip(self):
        self.assertEqual(self.request(raw="x" * 1025)[0], 400)
        self.assertEqual(self.request(raw='{"password":123}')[0], 400)
        self.assertEqual(self.request(raw='{"password":"\\ud800"}')[0], 400)
        self.assertEqual(self.request(raw='{"password":"test", "entity_id":"other"}')[0], 400)
        self.assertEqual(self.request(headers={"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.request(headers={"X-Real-IP": "forged, 192.0.2.1"})[0], 403)
        self.dispatch.assert_not_called()

    def test_disabled_fails_closed(self):
        self.control.enabled = False
        self.assertEqual(self.request()[0], 503)
        status, payload = self.request(method="GET", path="/api/collector/status")
        self.assertEqual(status, 200)
        self.assertFalse(payload["enabled"])

    def test_unknown_result_retains_cooldown(self):
        self.dispatch.return_value = "unknown"
        status, payload = self.request()
        self.assertEqual(status, 202)
        self.assertEqual(payload["state"], "unknown")
        self.assertGreater(payload["cooldown_seconds"], 0)

    def test_ha_failure_retains_cooldown(self):
        self.dispatch.return_value = "failed"
        self.assertEqual(self.request()[0], 502)
        self.assertGreater(self.store.status()["cooldown_seconds"], 0)

    def test_parallel_correct_requests_only_restart_once(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.request(), range(2)))
        self.assertEqual(sum(status == 202 for status, _ in results), 1)
        self.dispatch.assert_called_once()


class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.control = CollectorControl(None, ["https://energy.example.test"],
                                        {"test": "record"}, "test-token", "button.example_restart_collector")

    def test_fixed_endpoint_target_and_no_proxy_or_redirect(self):
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value.status = 200
        with patch("collector_control.build_opener", return_value=opener) as factory:
            self.assertEqual(self.control.dispatch_restart(), "requested")
        proxy, redirect = factory.call_args.args
        self.assertIsInstance(proxy, ProxyHandler)
        self.assertEqual(proxy.proxies, {})
        self.assertIsInstance(redirect, NoRedirect)
        self.assertIsNone(redirect.redirect_request(None, None, 302, "", {}, "https://other.example"))
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:8123/api/services/button/press")
        self.assertEqual(request.method, "POST")
        self.assertEqual(json.loads(request.data), {"entity_id": "button.example_restart_collector"})
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 12)

    def test_transport_uncertainty_preserved(self):
        for error in (TimeoutError(), URLError("unavailable")):
            opener = MagicMock()
            opener.open.side_effect = error
            with patch("collector_control.build_opener", return_value=opener):
                self.assertEqual(self.control.dispatch_restart(), "unknown")

    def test_http_error_is_not_acknowledged_success(self):
        opener = MagicMock()
        opener.open.side_effect = HTTPError("http://127.0.0.1", 500, "error", {}, None)
        with patch("collector_control.build_opener", return_value=opener):
            self.assertEqual(self.control.dispatch_restart(), "failed")


if __name__ == "__main__":
    unittest.main()
