import concurrent.futures
import getpass
import http.client
import io
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

from collector_control import (CollectorControl, ControlServer, NoRedirect, PolicyStore, SecurityAudit,
                               SYSTEMD_CREDENTIAL_DIRECTORY, password_record, prompt_secret,
                               read_private, verify_password, write_new_private)


class SecurityAuditTests(unittest.TestCase):
    def setUp(self):
        self.output = io.StringIO()
        self.now = 0.0
        self.audit = SecurityAudit(self.output, lambda: self.now)

    def events(self):
        return [json.loads(line) for line in self.output.getvalue().splitlines()]

    def test_only_allowlisted_fields_and_values_are_serialized(self):
        secret = "DO-NOT-LOG-THIS\nforged-event"
        self.audit.emit("request_rejected", reason=secret, request_id=secret,
                        client_ip=secret, client_bucket=secret, password=secret,
                        token=secret, headers={"Authorization": secret}, body=secret,
                        http_status=secret, attempt_number=True, blocked=secret)
        self.audit.emit(secret, password=secret)
        self.audit.emit([], reason=[])
        self.audit.emit("control_error", reason=[])
        self.assertNotIn("DO-NOT-LOG", self.output.getvalue())
        self.assertEqual(len(self.events()), 2)
        event = self.events()[0]
        self.assertEqual(set(event), {"timestamp", "component", "event", "client_ip", "client_bucket"})
        self.assertIsNone(event["client_ip"])
        self.assertTrue(event["timestamp"].endswith("+00:00"))

    def test_scope_id_cannot_smuggle_text_into_an_ipv6_address(self):
        self.audit.emit("request_rejected", client_ip="fe80::1%secret", client_bucket="fe80::1%secret/64")
        self.assertIsNone(self.events()[0]["client_ip"])
        self.assertIsNone(self.events()[0]["client_bucket"])
        self.assertNotIn("secret", self.output.getvalue())

    def test_noise_is_bounded_but_password_checks_and_actions_are_retained(self):
        for _ in range(100):
            self.audit.emit("request_rejected", reason="invalid_origin", client_ip="192.0.2.1")
        self.audit.emit("password_rejected", failed_attempts=2, lockout_started=True)
        self.audit.emit("restart_accepted")
        self.assertEqual(len(self.events()), 62)
        self.now = 60
        self.audit.flush_suppressed()
        self.assertEqual(self.events()[-1]["event"], "audit_suppressed")
        self.assertEqual(self.events()[-1]["suppressed_events"], 40)
        self.audit.flush_suppressed()
        self.audit.emit("request_rejected", reason="invalid_origin")
        self.assertEqual(len(self.events()), 64)

    def test_concurrent_noise_keeps_exact_budget_and_valid_json(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _: self.audit.emit("restart_blocked", reason="lockout"), range(100)))
        self.assertEqual(len(self.events()), 60)
        self.now = 60
        self.audit.flush_suppressed()
        self.assertEqual(self.events()[-1]["suppressed_events"], 40)

    def test_logging_failure_is_contained_and_reported_after_recovery(self):
        with patch.object(self.output, "write", side_effect=OSError("secret error detail")):
            self.audit.emit("authentication_accepted")
        self.audit.emit("restart_accepted")
        self.assertEqual(self.events()[0]["prior_log_write_failures"], 1)
        self.assertNotIn("secret", self.output.getvalue())


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
        self.audit_output = io.StringIO()
        self.control = CollectorControl(self.store, ["https://energy.example.test"],
                                        self.record, "test-token", "button.example_restart_collector",
                                        audit=SecurityAudit(self.audit_output))
        self.dispatch = patch.object(self.control, "dispatch_restart", return_value="requested").start()
        self.addCleanup(patch.stopall)
        self.server = ControlServer(("127.0.0.1", 0), self.control)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)

    def audit_events(self):
        return [json.loads(line) for line in self.audit_output.getvalue().splitlines()]

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
        self.assertEqual(status, 404)
        self.assertEqual(payload, {"error": "not_found"})
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
        status, payload = self.request(method="GET", path="/api/collector/status", headers={"X-Real-IP": "127.0.0.1"})
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

    def test_audit_failure_attempts_lockout_and_no_password_check_while_blocked(self):
        self.request("wrong-first")
        self.request("wrong-second")
        self.request()
        first, second, blocked = self.audit_events()
        self.assertEqual((first["attempt_number"], first["failed_attempts"]), (1, 1))
        self.assertFalse(first["lockout_started"])
        self.assertEqual((second["attempt_number"], second["failed_attempts"]), (2, 2))
        self.assertTrue(second["lockout_started"])
        self.assertEqual(second["retry_after_seconds"], 300)
        self.assertEqual(blocked["event"], "restart_blocked")
        self.assertEqual(blocked["reason"], "lockout")
        self.assertEqual(blocked["failed_attempts"], 2)
        self.assertFalse(blocked["password_checked"])
        self.assertNotIn("attempt_number", blocked)
        self.assertEqual(len({event["request_id"] for event in self.audit_events()}), 3)
        self.assertNotIn("wrong-first", self.audit_output.getvalue())
        self.assertNotIn("wrong-second", self.audit_output.getvalue())

    def test_audit_success_links_authentication_dispatch_and_result(self):
        _, payload = self.request()
        events = self.audit_events()
        self.assertEqual([event["event"] for event in events],
                         ["authentication_accepted", "restart_requested", "restart_accepted"])
        self.assertEqual(len({event["request_id"] for event in events}), 1)
        self.assertTrue(events[0]["password_checked"])
        self.assertEqual(events[0]["attempt_number"], 1)
        self.assertNotIn("request_id", payload)  # No new public status fields.
        for secret in (self.password, "test-token", self.record["digest"], self.record["salt"]):
            self.assertNotIn(secret, self.audit_output.getvalue())

    def test_audit_cooldown_is_not_a_failed_password_attempt(self):
        self.request()
        self.request(headers={"X-Real-IP": "192.0.2.2"})
        event = self.audit_events()[-1]
        self.assertEqual(event["reason"], "cooldown")
        self.assertEqual(event["client_ip"], "192.0.2.2")
        self.assertTrue(event["password_checked"])
        self.assertEqual(event["attempt_number"], 1)
        self.assertEqual(event["failed_attempts"], 0)
        self.dispatch.assert_called_once()

    def test_audit_keeps_full_ipv6_ip_separate_from_shared_bucket(self):
        self.request("wrong", headers={"X-Real-IP": "2001:db8:1::abcd"})
        event = self.audit_events()[0]
        self.assertEqual(event["client_ip"], "2001:db8:1::abcd")
        self.assertEqual(event["client_bucket"], "2001:db8:1::/64")

    def test_audit_bad_origin_does_not_log_headers_body_or_query(self):
        marker = "private-marker-not-for-logs"
        self.request(marker, headers={"Origin": "https://" + marker,
                     "Authorization": "Bearer " + marker, "Cookie": marker, "User-Agent": marker})
        self.request(raw=marker)
        self.request(path="/api/collector/restart?password=" + marker)
        self.request(headers={"X-Real-IP": marker})
        events = self.audit_events()
        self.assertEqual([event["reason"] for event in events],
                         ["invalid_origin", "invalid_body", "not_found", "invalid_client_identity"])
        self.assertEqual(events[0]["client_ip"], "192.0.2.1")
        self.assertIsNone(events[-1]["client_ip"])
        self.assertNotIn(marker, self.audit_output.getvalue())
        self.dispatch.assert_not_called()

    def test_audit_ignores_normal_status_polling(self):
        self.request(method="GET", path="/api/collector/status", headers={"X-Real-IP": "127.0.0.1"})
        self.assertEqual(self.audit_events(), [])

    def test_audit_upstream_failure_and_uncertainty_are_distinct(self):
        self.dispatch.return_value = "unknown"
        self.request()
        self.assertEqual(self.audit_events()[-1]["event"], "restart_unknown")

    def test_audit_upstream_rejection_is_not_success(self):
        self.dispatch.return_value = "failed"
        self.request()
        self.assertEqual(self.audit_events()[-1]["event"], "restart_failed")

    def test_audit_distinguishes_global_budget_from_ip_lockout(self):
        for index in range(30):
            self.store.reserve_verification("198.51.100." + str(index))
        self.request()
        event = self.audit_events()[0]
        self.assertEqual(event["reason"], "global_budget")
        self.assertFalse(event["password_checked"])
        self.assertNotIn("attempt_number", event)
        self.dispatch.assert_not_called()

    def test_audit_distinguishes_pending_checks_from_global_budget(self):
        self.store.reserve_verification("192.0.2.1")
        self.store.reserve_verification("192.0.2.1")
        self.request()
        self.assertEqual(self.audit_events()[0]["reason"], "concurrent_verification")
        self.dispatch.assert_not_called()

    def test_audit_unexpected_exception_never_logs_exception_text(self):
        with patch.object(self.control, "restart", side_effect=RuntimeError("secret-exception-marker")):
            with self.assertRaises(http.client.RemoteDisconnected):
                self.request()
        self.assertEqual(self.audit_events()[-1]["reason"], "internal_error")
        self.assertEqual(self.audit_events()[-1]["client_ip"], "192.0.2.1")
        self.assertRegex(self.audit_events()[-1]["request_id"], r"^[0-9a-f]{32}$")
        self.assertNotIn("secret-exception-marker", self.audit_output.getvalue())

    def test_audit_storage_failure_never_logs_exception_text(self):
        with patch.object(self.store, "reserve_verification", side_effect=OSError("secret-storage-marker")):
            self.assertEqual(self.request()[0], 503)
        self.assertEqual(self.audit_events()[-1]["reason"], "storage_error")
        self.assertNotIn("secret-storage-marker", self.audit_output.getvalue())
        self.dispatch.assert_not_called()

    def test_status_is_internal_only_even_if_proxy_route_is_misconfigured(self):
        for ip in ("192.0.2.1", "2001:db8::1", "::ffff:192.0.2.1"):
            status, payload = self.request(method="GET", path="/api/collector/status", headers={"X-Real-IP": ip})
            self.assertEqual((status, payload), (404, {"error": "not_found"}))
        status, payload = self.request(method="GET", path="/api/collector/status", headers={"X-Real-IP": "127.0.0.1"})
        self.assertEqual(status, 200)
        self.assertTrue(payload["enabled"])
        self.assertEqual(self.request(method="GET", path="/api/collector/status?test=1")[0], 404)

    def test_wrong_password_cannot_discover_active_cooldown_or_last_restart(self):
        self.request()
        status, payload = self.request("wrong", headers={"X-Real-IP": "192.0.2.2"})
        self.assertEqual(status, 401)
        self.assertEqual(payload, {"error": "invalid_password", "retry_after_seconds": 0})
        status, payload = self.request("wrong", headers={"X-Real-IP": "192.0.2.2"})
        self.assertEqual(status, 429)
        self.assertEqual(payload["error"], "locked")
        self.assertNotIn("cooldown_seconds", payload)
        self.assertNotIn("requested_at", payload)
        self.assertNotIn("last_result", payload)
        self.dispatch.assert_called_once()

    def test_repeated_correct_password_never_restarts_or_extends_cooldown(self):
        now = [1000.0]
        self.store.clock = lambda: now[0]
        self.assertEqual(self.request()[0], 202)
        for seconds, ip in ((1, "192.0.2.1"), (50, "192.0.2.2"), (150, "192.0.2.3"), (299, "192.0.2.1")):
            now[0] = 1000 + seconds
            status, payload = self.request(headers={"X-Real-IP": ip})
            self.assertEqual(status, 429)
            self.assertEqual(payload, {"error": "cooldown", "retry_after_seconds": 300 - seconds})
            self.assertEqual(self.store.status()["requested_at"], 1000)
            self.dispatch.assert_called_once()
        now[0] = 1300
        self.assertEqual(self.request()[0], 202)
        self.assertEqual(self.dispatch.call_count, 2)

    def test_reopened_service_still_rejects_correct_password_in_cooldown(self):
        self.assertEqual(self.request()[0], 202)
        self.control.store = PolicyStore(self.store.path)
        status, payload = self.request(headers={"X-Real-IP": "192.0.2.2"})
        self.assertEqual((status, payload["error"]), (429, "cooldown"))
        self.dispatch.assert_called_once()

    def test_correct_password_during_failed_or_uncertain_dispatch_cooldown_is_blocked(self):
        for outcome, status in (("failed", 502), ("unknown", 202)):
            with self.subTest(outcome=outcome):
                with self.store.connect() as db:
                    db.execute("DELETE FROM restart")
                self.dispatch.reset_mock()
                self.dispatch.return_value = outcome
                self.assertEqual(self.request()[0], status)
                self.assertEqual(self.request()[1]["error"], "cooldown")
                self.dispatch.assert_called_once()

    def test_parallel_correct_passwords_from_distinct_ips_only_dispatch_once(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda ip: self.request(headers={"X-Real-IP": ip}),
                                    ["192.0.2.1", "192.0.2.2", "192.0.2.3", "192.0.2.4"]))
        self.assertEqual(sum(status == 202 for status, _ in results), 1)
        self.assertEqual(sum(payload.get("error") == "cooldown" for _, payload in results), 3)
        self.dispatch.assert_called_once()

    def test_audit_concurrent_errors_keep_request_identity_separate(self):
        with patch.object(self.control, "restart", side_effect=RuntimeError("private error")):
            def failed_request(ip):
                with self.assertRaises(http.client.RemoteDisconnected):
                    self.request(headers={"X-Real-IP": ip})

            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                list(pool.map(failed_request, ["192.0.2.1", "192.0.2.2"]))
        events = self.audit_events()
        self.assertEqual({event["client_ip"] for event in events}, {"192.0.2.1", "192.0.2.2"})
        self.assertEqual(len({event["request_id"] for event in events}), 2)

    def test_audit_stream_failure_does_not_repeat_or_prevent_restart(self):
        with patch.object(self.audit_output, "write", side_effect=OSError("stream unavailable")):
            self.assertEqual(self.request()[0], 202)
        self.dispatch.assert_called_once()

    def test_audit_parallel_attempt_numbers_and_failed_counts_are_atomic(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _: self.request("wrong"), range(2)))
        events = self.audit_events()
        self.assertEqual(sorted(event["attempt_number"] for event in events), [1, 2])
        self.assertEqual(sorted(event["failed_attempts"] for event in events), [1, 2])
        self.assertEqual(sum(event["lockout_started"] for event in events), 1)

    def test_audit_attempt_count_starts_again_after_lockout_expiry(self):
        self.request("wrong")
        self.request("wrong")
        with patch.object(self.store, "clock", return_value=self.store.clock() + 301):
            self.request("wrong")
        self.assertEqual(self.audit_events()[-1]["attempt_number"], 1)

    def test_audit_expired_ticket_never_reports_accepted_authentication(self):
        original_finish = self.store.finish_verification

        def expired(ticket, valid, audit):
            with self.store.connect() as db:
                db.execute("DELETE FROM attempts")
            return original_finish(ticket, valid, audit)

        with patch.object(self.store, "finish_verification", side_effect=expired):
            self.assertEqual(self.request()[0], 429)
        event = self.audit_events()[0]
        self.assertEqual(event["reason"], "verification_expired")
        self.assertTrue(event["password_checked"])
        self.dispatch.assert_not_called()


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
