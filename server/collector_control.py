"""Narrow, password-protected collector restart API; standard library only.

Run behind a loopback nginx proxy that overwrites X-Real-IP. Secrets are read
from private files, never accepted as command-line arguments or logged.
"""
import argparse
import base64
from contextlib import contextmanager
import getpass
import hashlib
import hmac
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import secrets
import socket
import sqlite3
import stat
import threading
import time
import warnings
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

COOLDOWN = 300
LOCKOUT = 300
ITERATIONS = 600_000
MAX_BODY = 1024
SYSTEMD_CREDENTIAL_DIRECTORY = Path("/run/credentials/homeenergy-control.service")


def is_systemd_credential(path, metadata):
    """Allow systemd's read-only ACL-backed copies, not arbitrary 0440 files."""
    directory = SYSTEMD_CREDENTIAL_DIRECTORY
    if (os.environ.get("CREDENTIALS_DIRECTORY") != str(directory)
            or path.parent != directory or path.name not in {"password.hash", "ha.token"}
            or metadata.st_uid != 0 or metadata.st_gid != 0
            or stat.S_IMODE(metadata.st_mode) != 0o440):
        return False
    # Check every ancestor without following symlinks. Only root may replace them.
    for parent in (directory, *directory.parents):
        info = parent.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_gid != 0
                or stat.S_IMODE(info.st_mode) & 0o022):
            return False
        if parent == directory and stat.S_IMODE(info.st_mode) & 0o227:
            return False
    return True


def read_private(path):
    path = Path(path)
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError("Secret files must be regular files, not symbolic links")
    if os.name != "nt" and stat.S_IMODE(metadata.st_mode) & 0o077:
        if not is_systemd_credential(path, metadata):
            raise ValueError("Secret files must have private permissions")
    return path.read_text(encoding="utf-8").strip()


def password_record(password):
    salt = secrets.token_bytes(24)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return {"algorithm": "pbkdf2_sha256", "iterations": ITERATIONS,
            "salt": base64.b64encode(salt).decode(), "digest": base64.b64encode(digest).decode()}


def validate_record(record):
    if record.get("algorithm") != "pbkdf2_sha256" or record.get("iterations") != ITERATIONS:
        raise ValueError("Unsupported password record")
    if len(base64.b64decode(record["salt"], validate=True)) != 24:
        raise ValueError("Invalid password salt")
    if len(base64.b64decode(record["digest"], validate=True)) != 32:
        raise ValueError("Invalid password digest")


def verify_password(password, record):
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(),
                               base64.b64decode(record["salt"]), record["iterations"])
    return hmac.compare_digest(digest, base64.b64decode(record["digest"]))


class PolicyStore:
    """SQLite transactions serialize limits across threads and service restarts."""
    def __init__(self, path, clock=time.time):
        self.path, self.clock = str(path), clock
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS failures(ip TEXT PRIMARY KEY, count INTEGER,
                    window_start REAL, locked_until REAL);
                CREATE TABLE IF NOT EXISTS attempts(ticket TEXT PRIMARY KEY, ip TEXT, created REAL);
                CREATE TABLE IF NOT EXISTS budget(created REAL);
                CREATE TABLE IF NOT EXISTS restart(id INTEGER PRIMARY KEY CHECK(id=1),
                    requested REAL, result TEXT);
            """)
        if os.name != "nt":
            os.chmod(self.path, 0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            with db:
                yield db
        finally:
            db.close()

    def reserve_verification(self, ip):
        now = self.clock()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM attempts WHERE created <= ?", (now - 30,))
            db.execute("DELETE FROM budget WHERE created <= ?", (now - LOCKOUT,))
            db.execute("DELETE FROM failures WHERE locked_until <= ? AND window_start <= ?",
                       (now, now - LOCKOUT))
            row = db.execute("SELECT count, window_start, locked_until FROM failures WHERE ip=?", (ip,)).fetchone()
            if row and row[2] > now:
                return None, "locked", math.ceil(row[2] - now)
            pending = db.execute("SELECT COUNT(*) FROM attempts WHERE ip=?", (ip,)).fetchone()[0]
            if (row[0] if row else 0) + pending >= 2:
                return None, "busy", 30
            budget = db.execute("SELECT COUNT(*), MIN(created) FROM budget").fetchone()
            if budget[0] >= 30:
                return None, "busy", max(1, math.ceil(budget[1] + LOCKOUT - now))
            ticket = secrets.token_hex(16)
            db.execute("INSERT INTO attempts VALUES(?,?,?)", (ticket, ip, now))
            db.execute("INSERT INTO budget VALUES(?)", (now,))
            return ticket, None, 0

    def finish_verification(self, ticket, valid):
        now = self.clock()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            attempt = db.execute("SELECT ip FROM attempts WHERE ticket=?", (ticket,)).fetchone()
            if not attempt:
                return "busy", 30
            ip = attempt[0]
            db.execute("DELETE FROM attempts WHERE ticket=?", (ticket,))
            if valid:
                db.execute("DELETE FROM failures WHERE ip=?", (ip,))
                return None, 0
            row = db.execute("SELECT count, window_start FROM failures WHERE ip=?", (ip,)).fetchone()
            count, start = (row[0] + 1, row[1]) if row else (1, now)
            locked_until = now + LOCKOUT if count >= 2 else 0
            db.execute("INSERT OR REPLACE INTO failures VALUES(?,?,?,?)", (ip, count, start, locked_until))
            return ("locked", LOCKOUT) if count >= 2 else ("invalid_password", 0)

    def status(self):
        with self.connect() as db:
            row = db.execute("SELECT requested,result FROM restart WHERE id=1").fetchone()
        return {"cooldown_seconds": max(0, math.ceil(row[0] + COOLDOWN - self.clock())) if row else 0,
                "requested_at": row[0] if row else None, "last_result": row[1] if row else None}

    def lockout_seconds(self, ip):
        with self.connect() as db:
            row = db.execute("SELECT locked_until FROM failures WHERE ip=?", (ip,)).fetchone()
        return max(0, math.ceil(row[0] - self.clock())) if row else 0

    def reserve_restart(self):
        now = self.clock()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT requested FROM restart WHERE id=1").fetchone()
            if row and row[0] + COOLDOWN > now:
                return math.ceil(row[0] + COOLDOWN - now)
            db.execute("INSERT OR REPLACE INTO restart VALUES(1,?,?)", (now, "requested"))
            return 0

    def set_result(self, result):
        with self.connect() as db:
            db.execute("UPDATE restart SET result=? WHERE id=1", (result,))


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class CollectorControl:
    def __init__(self, store, origins=(), record=None, token=None, entity=None,
                 ha_url="http://127.0.0.1:8123", snapshot=None):
        self.store, self.origins, self.record = store, set(origins), record
        self.token, self.entity, self.ha_url, self.snapshot = token, entity, ha_url, snapshot
        self.enabled = bool(origins and record and token and entity)

    @classmethod
    def from_environment(cls):
        store = PolicyStore(os.environ.get("CONTROL_STATE_FILE", "/var/lib/homeenergy-control/state.sqlite3"))
        try:
            origins = [value.strip() for value in os.environ["CONTROL_ALLOWED_ORIGINS"].split(",")]
            for origin in origins:
                parsed = urlsplit(origin)
                if parsed.scheme != "https" or not parsed.netloc or parsed.path or parsed.query or parsed.fragment or parsed.username:
                    raise ValueError("Origins must be exact HTTPS origins")
            entity = os.environ["CONTROL_COLLECTOR_ENTITY"]
            if not re.fullmatch(r"button\.[a-z0-9_]+restart_collector", entity):
                raise ValueError("Expected the collector restart button")
            record = json.loads(read_private(os.environ["CONTROL_PASSWORD_FILE"]))
            validate_record(record)
            token = read_private(os.environ["CONTROL_HA_TOKEN_FILE"])
            if not token or any(c.isspace() for c in token):
                raise ValueError("Invalid token")
            return cls(store, origins, record, token, entity,
                       snapshot=os.environ.get("CONTROL_SNAPSHOT_FILE"))
        except (KeyError, OSError, ValueError, TypeError):
            # Fail closed. Never log malformed configuration or secret values.
            return cls(store)

    def baseline(self):
        try:
            value = json.loads(Path(self.snapshot).read_text(encoding="utf-8")).get("updated_at")
            return value if isinstance(value, (int, float)) and math.isfinite(value) else None
        except (OSError, ValueError, TypeError):
            return None

    def dispatch_restart(self):
        request = Request(self.ha_url + "/api/services/button/press",
                          data=json.dumps({"entity_id": self.entity}).encode(),
                          headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"},
                          method="POST")
        try:
            with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=12) as response:
                return "requested" if 200 <= response.status < 300 else "failed"
        except HTTPError:
            return "failed"
        except (TimeoutError, socket.timeout, URLError, OSError):
            # A network error does not prove that HA failed to execute the action.
            return "unknown"

    def restart(self, ip, password):
        if not self.enabled:
            return 503, {"error": "unavailable"}
        cooldown = self.store.status()["cooldown_seconds"]
        if cooldown:
            return 429, {"error": "cooldown", "retry_after_seconds": cooldown}
        ticket, error, retry = self.store.reserve_verification(ip)
        if error:
            return 429, {"error": error, "retry_after_seconds": retry}
        valid = verify_password(password, self.record)
        error, retry = self.store.finish_verification(ticket, valid)
        if error:
            return (401 if error == "invalid_password" else 429), {"error": error, "retry_after_seconds": retry}
        cooldown = self.store.reserve_restart()
        if cooldown:
            return 429, {"error": "cooldown", "retry_after_seconds": cooldown}
        baseline = self.baseline()
        result = self.dispatch_restart()
        self.store.set_result(result)
        if result == "failed":
            return 502, {"error": "restart_failed", **self.store.status()}
        return 202, {"state": result, "baseline_updated_at": baseline, **self.store.status()}


class Handler(BaseHTTPRequestHandler):
    server_version = "CollectorControl"

    def log_message(self, format, *args):
        pass  # Request paths, bodies and headers must never reach logs.

    def client_identity(self):
        peer = ipaddress.ip_address(self.client_address[0])
        addresses = self.headers.get_all("X-Real-IP", [])
        if not peer.is_loopback or len(addresses) != 1:
            raise ValueError()
        ip = ipaddress.ip_address(addresses[0])
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        # Group rotating IPv6 privacy addresses on the same typical LAN.
        return str(ipaddress.ip_network(str(ip) + "/64", strict=False)) if ip.version == 6 else str(ip)

    def reply(self, status, payload):
        body = json.dumps(payload, allow_nan=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Connection", "close")
        if payload.get("retry_after_seconds"):
            self.send_header("Retry-After", str(payload["retry_after_seconds"]))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def do_GET(self):
        if self.path != "/api/collector/status":
            return self.reply(404, {"error": "not_found"})
        control = self.server.control
        try:
            identity = self.client_identity()
            self.reply(200, {"enabled": control.enabled, "lockout_seconds": control.store.lockout_seconds(identity),
                             **control.store.status()})
        except ValueError:
            self.reply(403, {"error": "forbidden"})
        except (sqlite3.Error, OSError):
            self.reply(503, {"error": "unavailable"})

    def do_POST(self):
        if self.path != "/api/collector/restart":
            return self.reply(404, {"error": "not_found"})
        control = self.server.control
        if not control.enabled:
            return self.reply(503, {"error": "unavailable"})
        if len(self.headers.get_all("Origin", [])) != 1 or self.headers["Origin"] not in control.origins:
            return self.reply(403, {"error": "forbidden"})
        if self.headers.get("Sec-Fetch-Site") not in (None, "same-origin"):
            return self.reply(403, {"error": "forbidden"})
        try:
            identity = self.client_identity()
        except ValueError:
            return self.reply(403, {"error": "forbidden"})
        if self.headers.get_content_type() != "application/json" or self.headers.get("Transfer-Encoding"):
            return self.reply(415, {"error": "invalid_request"})
        try:
            lengths = self.headers.get_all("Content-Length", [])
            if len(lengths) != 1 or not lengths[0].isdigit():
                raise ValueError()
            length = int(lengths[0])
            if not 0 < length <= MAX_BODY:
                raise ValueError()
            body = self.rfile.read(length)
            if len(body) != length:
                raise ValueError()
            payload = json.loads(body)
            if not isinstance(payload, dict) or set(payload) != {"password"}:
                raise ValueError()
            password = payload["password"]
            if not isinstance(password, str) or not 1 <= len(password) <= 128:
                raise ValueError()
            password.encode("utf-8")
        except (ValueError, UnicodeError, TimeoutError):
            return self.reply(400, {"error": "invalid_request"})
        try:
            status, result = control.restart(identity, password)
        except (sqlite3.Error, OSError):
            return self.reply(503, {"error": "unavailable"})
        finally:
            password = None
        self.reply(status, result)


class ControlServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 16

    def __init__(self, address, control):
        self.control = control
        self.slots = threading.BoundedSemaphore(8)
        super().__init__(address, Handler)

    def process_request(self, request, client_address):
        request.settimeout(5)
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def handle_error(self, request, client_address):
        pass  # Do not emit stack traces with request context.


def write_new_private(path, content):
    """Create complete private file without ever overwriting an existing secret."""
    path = Path(path)
    temporary = path.with_name("." + path.name + "." + secrets.token_hex(8))
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(content + "\n")
            output.flush()
            os.fsync(output.fileno())
        os.link(temporary, path)  # Atomic no-clobber publication on the same filesystem.
    finally:
        if temporary.exists():
            temporary.unlink()


def prompt_secret(prompt):
    # getpass otherwise falls back to echoed input if its terminal setup fails.
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        return getpass.getpass(prompt)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--port", type=int, default=8767)
    for command in ("setup-password", "setup-token"):
        child = sub.add_parser(command)
        child.add_argument("path", help="New private file path; existing files are never overwritten")
    args = parser.parse_args()
    if args.command == "serve":
        ControlServer(("127.0.0.1", args.port), CollectorControl.from_environment()).serve_forever()
        return
    if not os.isatty(0):
        parser.error("Secret setup requires an interactive terminal")
    if Path(args.path).exists():
        parser.error("Destination already exists; refusing to overwrite")
    try:
        secret = prompt_secret("Password: " if args.command == "setup-password" else "Home Assistant token: ")
    except (getpass.GetPassWarning, EOFError):
        parser.error("A terminal with hidden input is required")
    if args.command == "setup-password":
        if not 15 <= len(secret) <= 128:
            parser.error("Password must contain 15 to 128 characters")
        try:
            confirmation = prompt_secret("Confirm password: ")
        except (getpass.GetPassWarning, EOFError):
            parser.error("A terminal with hidden input is required")
        if not hmac.compare_digest(secret.encode(), confirmation.encode()):
            parser.error("Passwords do not match")
        content = json.dumps(password_record(secret))
    else:
        if not secret or any(c.isspace() for c in secret):
            parser.error("Token must be nonempty and contain no whitespace")
        content = secret
    write_new_private(args.path, content)
    print("Private file created. Restart the control service to load it.")


if __name__ == "__main__":
    main()
