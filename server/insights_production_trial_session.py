"""Fixed report-only SSH session and durable evidence, for a later armed trial.

No executable main; discovery is lazy and the closed token never opens storage.
No automatic transport retry: ambiguous results require administrator review.
"""
import copy
import threading
import time

from insights_broker import MAX_PAYLOAD, decode, encode, request
import insights_production_mcp as adapter
import insights_production_connection as connection
import insights_windows_transport as transport


CLOSED_TOKEN = '0' * 64


def require(condition, code='production_trial_session_rejected'):
    if not condition:
        raise ValueError(code)


def ssh_command(destination, method):
    require(method in ('prepare', 'publish'))
    require(destination.host == connection.EXPECTED_BINDING['host'] and destination.port == 22)
    destination.validate()
    options = (
        'BatchMode=yes', 'IdentitiesOnly=yes', 'IdentityAgent=none', 'AddKeysToAgent=no',
        'CertificateFile=none', 'PKCS11Provider=none', 'SecurityKeyProvider=none',
        'StrictHostKeyChecking=yes', 'UpdateHostKeys=no', 'HostKeyAlgorithms=ssh-ed25519',
        'VerifyHostKeyDNS=no', 'CanonicalizeHostname=no', 'PubkeyAcceptedAlgorithms=ssh-ed25519',
        'UserKnownHostsFile=' + str(connection.HOST_PIN), 'GlobalKnownHostsFile=none',
        'PasswordAuthentication=no', 'KbdInteractiveAuthentication=no',
        'PreferredAuthentications=publickey', 'GSSAPIAuthentication=no',
        'ForwardAgent=no', 'ForwardX11=no', 'ClearAllForwardings=yes',
        'ControlMaster=no', 'ControlPath=none', 'ProxyCommand=none', 'ProxyJump=none',
        'PermitLocalCommand=no', 'RequestTTY=no', 'ConnectionAttempts=1',
        'ConnectTimeout=10', 'ServerAliveInterval=10', 'ServerAliveCountMax=2', 'LogLevel=ERROR',
    )
    return [str(transport.SSH), '-F', 'none', '-T', '-p', '22', '-i', str(connection.KEY),
            *[part for option in options for part in ('-o', option)],
            'homeenergy-reporter@' + destination.host, method]


class Reports:
    def __init__(self, store_factory, token):
        self.factory = store_factory
        self.token = token
        self.store = None
        self.reports = None
        self.deadline = None
        self.calls = 0
        self.prepared = None
        self.submission = None
        self.result = None
        self.failed = False
        self.closed = False
        self.lock = threading.Lock()

    def _gate(self):
        require(not self.closed and not self.failed and self.token != CLOSED_TOKEN,
                'production_trial_not_authorized')
        if self.store is None:
            store = self.factory()
            self.store = store
            store.claim(self.token)
            self.deadline = time.monotonic() + adapter.SESSION_SECONDS
            self.reports = adapter.Reports(self._send)
        self.store.active(self.token)
        require(time.monotonic() < self.deadline)

    def _send(self, method, body):
        require(method in ('prepare', 'publish') and type(body) is bytes and len(body) <= MAX_PAYLOAD
                and self.calls < adapter.MAX_CALLS)
        self.store.active(self.token)
        self.calls += 1
        self.store.record('transport_started', method=method, attempt=self.calls)
        try:
            require(time.monotonic() + transport.SSH_SECONDS + 15 <= self.deadline)
            destination = connection.load_destination()  # Fresh ACL/pin checks before each send.
            argv = ssh_command(destination, method)
            require(time.monotonic() + transport.SSH_SECONDS <= self.deadline)
            result = transport.bounded_process(argv, body, env=transport.clean_environment())
            require(time.monotonic() <= self.deadline)
            self.store.active(self.token)
        except Exception:
            self.store.record('transport_failed', method=method, attempt=self.calls, code='uncertain')
            raise ValueError('production_transport_uncertain') from None
        self.store.record('transport_returned', method=method, attempt=self.calls)
        return result

    def call(self, name, arguments):
        with self.lock:
            self._gate()
            try:
                method, body = request(encode({'method': name, 'payload': arguments}))
                if method == 'prepare':
                    if self.prepared is not None:
                        return copy.deepcopy(self.prepared)
                    result = self.reports.call(name, arguments)
                    self.store.save('analysis', result)
                    self.store.record('analysis_saved', method='prepare', attempt=self.calls)
                    self.prepared = result
                else:
                    require(self.prepared is not None)
                    expected = self.prepared['result']
                    require(arguments['analysis_id'] == expected['analysis_id'])
                    adapter.narrative(arguments, expected['analysis'])
                    require(self.submission is None or self.submission == body)
                    if self.result is not None:
                        require(self.store.read('submission') == decode(body, MAX_PAYLOAD)
                                and self.store.read('result') == self.result)
                        return copy.deepcopy(self.result)  # Same acknowledgement; no second network send.
                    self.store.save('submission', decode(body, MAX_PAYLOAD))
                    self.submission = body  # Durable before sending; never replace after uncertainty.
                    self.store.record('submission_saved', method='publish', attempt=self.calls)
                    result = self.reports.call(name, arguments)
                    self.store.save('result', result)
                    self.store.record('publication_acknowledged', method='publish', attempt=self.calls)
                    self.result = result
                return copy.deepcopy(result)
            except Exception:
                self.failed = True
                if self.store is not None:
                    self.store.record('failed', code='uncertain')
                raise ValueError('production_trial_call_failed_review_evidence') from None

    def reject(self, code, attempt, method):
        require(code in adapter.REJECTION_CODES and type(attempt) is int and 1 <= attempt <= 64
                and method in ('prepare', 'publish', 'unknown'))
        self.failed = True
        if self.store is not None:
            self.store.record('protocol_rejected', method=method, attempt=attempt, code=code)

    def close(self):
        self.closed = True
        if self.reports is not None:
            self.reports.close()
        if self.store is not None:
            self.store.close()
