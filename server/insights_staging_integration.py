"""Prepared host-side staging connection; no executable reporting entrypoint.

Only trusted launcher code may construct this integration after its runtime
checks. This module never starts a model or activates a schedule. The current
launcher uses check_integration() only, which cannot construct an SSH client.
"""
from datetime import datetime, timezone
import hashlib
import os
import re
import sys
import threading
import time

if __name__ == '__main__':
    # Deliberately before project imports, including isolated Python invocation.
    print('Staging execution is not activated.', file=sys.stderr)
    sys.exit(1)

from insights_broker import decode, encode, require, Rejected
import insights_staging_acl as acl
import insights_staging_mcp as adapter
import insights_windows_transport as transport


BINDING = transport.PRIVATE_ROOT / 'integration-binding.json'
BINDING_SHA256 = 'a0cd2b2268d95ea20d01a2b5718619c8fa2e562b080c972cdb16aaea44153639'
BINDING_LIMIT = 1024
SESSION_SECONDS = 240
CHECK_RESERVE_SECONDS = 15
MAX_AUDIT_BYTES = 32768
MAX_AUDIT_EVENTS = 64 + adapter.MAX_CALLS * 2


def parse_binding(raw):
    """Exact private deployment schema, never populated from tool arguments."""
    value = decode(raw, BINDING_LIMIT)
    require(isinstance(value, dict) and set(value) == {
        'schema_version', 'scope', 'synthetic', 'host', 'port',
        'ssh_sha256', 'known_hosts_sha256'})
    require(type(value['schema_version']) is int and value['schema_version'] == 1
            and value['scope'] == 'staging' and value['synthetic'] is True)
    destination = transport.Destination(value['host'], value['port'],
        value['ssh_sha256'], value['known_hosts_sha256'])
    destination.validate()
    return destination


def load_destination():
    """Local metadata/pin checks only; private-key bytes are never read here."""
    require(os.name == 'nt')
    acl.verify_permissions()
    transport.regular_file(BINDING, BINDING_LIMIT)
    with BINDING.open('rb') as source:
        raw = source.read(BINDING_LIMIT + 1)
    require(hashlib.sha256(raw).hexdigest() == BINDING_SHA256)
    destination = parse_binding(raw)
    transport.verify_material(destination)
    return destination


def check_integration():
    load_destination()
    return {'integration_prepared': True, 'private_binding_verified': True,
            'material_acl_verified': True, 'material_pins_verified': True,
            'ssh_connected': False, 'model_started': False,
            'report_calls_enabled': False, 'schedule_changed': False}


class AuditLog:
    """Bounded secret-free JSON lines to a caller-owned private binary sink.

    A future run supervisor must provide a private persistent sink, separate from
    model-visible stdio and logs. This class does not create a file or establish
    its ACLs. Write/flush failure permanently disables the sink and its session.
    """
    def __init__(self, stream):
        require(stream is not None and stream is not getattr(sys.stdout, 'buffer', None))
        require(callable(getattr(stream, 'write', None))
                and callable(getattr(stream, 'flush', None)))
        self._stream = stream
        self._bytes = 0
        self._events = 0
        self.failed = False

    def record(self, event):
        try:
            require(not self.failed and isinstance(event, dict) and set(event) == {
                'timestamp_utc', 'event', 'attempt', 'method', 'outcome'})
            timestamp = event['timestamp_utc']
            require(isinstance(timestamp, str) and re.fullmatch(
                r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?\+00:00', timestamp))
            require(type(event['attempt']) is int)
            if event['event'] == 'staging_adapter':
                require(1 <= event['attempt'] <= 64
                        and event['method'] in ('unknown', 'prepare', 'publish')
                        and event['outcome'] in ('accepted', 'rejected'))
            else:
                require(event['event'] == 'staging_transport'
                        and 1 <= event['attempt'] <= adapter.MAX_CALLS
                        and event['method'] in ('prepare', 'publish')
                        and event['outcome'] in ('started', 'returned', 'failed'))
            line = encode(event) + b'\n'
            require(self._events < MAX_AUDIT_EVENTS
                    and self._bytes + len(line) <= MAX_AUDIT_BYTES)
            require(self._stream.write(line) == len(line))
            self._stream.flush()
            self._events += 1
            self._bytes += len(line)
        except (OSError, ValueError, TypeError, AttributeError):
            self.failed = True
            raise Rejected(adapter.ERROR) from None


class _SessionTransport:
    """Fresh checks before every request; one serialized, bounded session."""
    def __init__(self, destination, audit):
        self.destination = destination
        self.audit = audit
        self.deadline = time.monotonic() + SESSION_SECONDS
        self.attempt = 0
        self.closed = False
        self.lock = threading.Lock()

    def _audit(self, method, outcome):
        self.audit.record({'timestamp_utc': datetime.now(timezone.utc).isoformat(),
            'event': 'staging_transport', 'attempt': self.attempt,
            'method': method, 'outcome': outcome})

    def __call__(self, method, body):
        with self.lock:
            require(not self.closed and not self.audit.failed
                    and self.attempt < adapter.MAX_CALLS)
            require(method in ('prepare', 'publish') and isinstance(body, bytes))
            require(len(body) <= transport.MAX_PAYLOAD)
            self.attempt += 1
            self._audit(method, 'started')  # No upstream action unless logging succeeds.
            try:
                require(time.monotonic() + transport.SSH_SECONDS + CHECK_RESERVE_SECONDS
                        <= self.deadline)
                require(load_destination() == self.destination)
                require(time.monotonic() + transport.SSH_SECONDS <= self.deadline)
                result = transport.StagingSSH(self.destination)(method, body)
                require(time.monotonic() <= self.deadline)
            except (ValueError, OSError, TypeError, KeyError):
                self._audit(method, 'failed')
                raise Rejected(adapter.ERROR) from None
            self._audit(method, 'returned')
            # "returned" describes transport completion, not server acknowledgement;
            # the existing adapter still validates staging scope, digest and result.
            return result

    def close(self):
        with self.lock:
            self.closed = True


class StagingSession:
    """One frozen analysis and identical-retry state for a future approved run."""
    def __init__(self, audit):
        require(type(audit) is AuditLog and not audit.failed)
        destination = load_destination()
        self.audit = audit
        self.transport = _SessionTransport(destination, audit)
        self.reports = adapter.StagingReports(self.transport)
        self.closed = False
        self.lock = threading.Lock()

    def call(self, name, arguments):
        with self.lock:
            require(not self.closed and not self.audit.failed
                    and time.monotonic() < self.transport.deadline)
            return self.reports.call(name, arguments)

    def close(self):
        with self.lock:
            self.closed = True
            self.transport.close()


def serve_staging(source, destination, audit):
    """Library hook only; no current launcher mode selects this real connection.

    The future supervisor owns the total runtime deadline (including an idle
    stdio read), private audit sink, runtime checks and explicit run approval.
    """
    session = StagingSession(audit)
    try:
        adapter.serve(source, destination, session, audit.record)
    finally:
        session.close()
