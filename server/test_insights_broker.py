"""Report-only broker boundaries; never contact SSH or the production report store.

Portable tests use real socketpairs with a mocked peer identity. Linux tests also
exercise kernel peer credentials, bounded child processes and a Unix listener.
"""
from contextlib import ExitStack
import copy
import io
import json
import os
from pathlib import Path, PurePosixPath
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import insights_broker as broker
import insights_report_client as client
import insights_publish as publisher


LINUX = sys.platform == 'linux'
ANALYSIS_ID = 'a' * 64
TEST_UID = 12345


def localized(text='No recorded readings were available for this review.'):
    return {'en': text, 'el': 'Δεν υπήρχαν καταγεγραμμένες μετρήσεις για αυτή την ανασκόπηση.',
            'ru': 'Для этого обзора записанные показания отсутствуют.'}


def publish_payload(identifier=ANALYSIS_ID):
    return {'analysis_id': identifier, 'summary': localized(), 'notes': []}


def frame(body):
    return struct.pack('!I', len(body)) + body


class SocketHarness(unittest.TestCase):
    def exchange(self, raw=None, *, method='prepare', payload=None, wire=None,
                 call=None, peer=TEST_UID, allowed=TEST_UID):
        if raw is None:
            raw = broker.encode({'method': method, 'payload': {} if payload is None else payload})
        if wire is None:
            wire = frame(raw)
        call = call or Mock(return_value={'analysis_id': ANALYSIS_ID, 'analysis': {}})
        audit, errors = [], []
        with ExitStack() as stack:
            sender, receiver = socket.socketpair()
            stack.enter_context(sender)
            stack.enter_context(receiver)
            stack.enter_context(patch.object(broker, 'peer_uid', return_value=peer))

            def serve():
                try:
                    broker.serve_connection(receiver, allowed, call, audit.append)
                except BaseException as error:
                    errors.append(error)

            worker = threading.Thread(target=serve, daemon=True)
            worker.start()
            try:
                # A denied UID must be rejected without reading any request.
                if peer == allowed and allowed > 0:
                    sender.sendall(wire)
                sender.shutdown(socket.SHUT_WR)
                result = broker.decode(broker.receive_frame(sender, broker.MAX_RESPONSE, 3), broker.MAX_RESPONSE)
            finally:
                sender.close()
                receiver.close()
                worker.join(3)
            self.assertFalse(worker.is_alive(), 'Broker worker did not terminate')
            self.assertEqual(errors, [])
        return result, audit, call


class RequestContractTests(unittest.TestCase):
    def test_prepare_has_no_payload_or_command(self):
        self.assertEqual(broker.request(b'{"method":"prepare","payload":{}}'), ('prepare', b''))

    def test_publish_preserves_all_translations_and_has_fixed_fields(self):
        payload = publish_payload()
        method, raw = broker.request(broker.encode({'method': 'publish', 'payload': payload}))
        self.assertEqual(method, 'publish')
        self.assertEqual(broker.decode(raw, broker.MAX_PAYLOAD), payload)

    def test_json_rejects_duplicates_invalid_utf8_numbers_and_trailing_objects(self):
        for raw in [b'', b'{}{}', b'\xff', b'NaN', b'Infinity', b'{"a":1,"a":2}',
                    b'{"method":"prepare","payload":{"x":NaN}}', b'[' * 1200]:
            with self.subTest(raw=raw[:40]), self.assertRaises(ValueError):
                broker.request(raw)

    def test_only_two_exact_methods_are_accepted(self):
        for method in ['', 'PREPARE', 'prepare ', 'prepare /etc/passwd', 'publish; id',
                       'scp -t /tmp/x', 'sh', ['prepare'], None]:
            with self.subTest(method=method), self.assertRaises(ValueError):
                broker.request(broker.encode({'method': method, 'payload': {}}))

    def test_envelope_and_prepare_payload_are_closed_contracts(self):
        values = [[], None, {'method': 'prepare'}, {'payload': {}},
                  {'method': 'prepare', 'payload': [], 'url': 'https://example.invalid'},
                  {'method': 'prepare', 'payload': {}, 'command': 'id'},
                  {'method': 'prepare', 'payload': {'path': '/etc/passwd'}}]
        for value in values:
            with self.subTest(value=value), self.assertRaises(ValueError):
                broker.request(broker.encode(value))

    def test_publish_rejects_extra_fields_bad_id_missing_languages_and_notes_type(self):
        base = publish_payload()
        values = [dict(base, path='/tmp/report'), dict(base, analysis_id='../report'),
                  dict(base, analysis_id='A' * 64), dict(base, summary={'en': 'Only English'}),
                  dict(base, summary=dict(localized(), de='Extra')), dict(base, notes='notes'),
                  dict(base, notes=[{}] * 7)]
        for payload in values:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                broker.request(broker.encode({'method': 'publish', 'payload': payload}))

    def test_request_and_payload_limits_count_utf8_bytes(self):
        with self.assertRaises(ValueError):
            broker.request(b' ' * (broker.MAX_REQUEST + 1))
        payload = dict(publish_payload(), summary=localized('😀' * 9000))
        with self.assertRaises(ValueError):
            broker.request(broker.encode({'method': 'publish', 'payload': payload}))
        raw = broker.encode({'method': 'publish', 'payload': publish_payload()})
        with patch.object(broker, 'MAX_PAYLOAD', 10), self.assertRaises(ValueError):
            broker.request(raw)

    def test_ssh_command_disables_ambient_configuration_agents_and_forwarding(self):
        settings = broker.Settings('192.0.2.10', 2222, PurePosixPath('/etc/pinned_hosts'),
                                   PurePosixPath('/run/credentials/test/report-key'))
        command = broker.ssh_command(settings, 'prepare')
        self.assertEqual(command[:4], ['/usr/bin/ssh', '-F', '/dev/null', '-T'])
        self.assertEqual(command[-2:], ['homeenergy-reporter@192.0.2.10', 'prepare'])
        self.assertEqual(command[command.index('-p') + 1], '2222')
        self.assertEqual(command[command.index('-i') + 1], str(settings.key))
        options = {command[index + 1] for index, value in enumerate(command) if value == '-o'}
        expected = {'BatchMode=yes', 'IdentitiesOnly=yes', 'IdentityAgent=none',
                    'StrictHostKeyChecking=yes', 'UpdateHostKeys=no', 'GlobalKnownHostsFile=/dev/null',
                    'HostKeyAlgorithms=ssh-ed25519', 'PasswordAuthentication=no',
                    'KbdInteractiveAuthentication=no', 'ForwardAgent=no', 'ForwardX11=no',
                    'ClearAllForwardings=yes', 'ControlMaster=no', 'ControlPath=none',
                    'ProxyCommand=none', 'ProxyJump=none', 'PermitLocalCommand=no', 'RequestTTY=no',
                    'UserKnownHostsFile=/etc/pinned_hosts'}
        self.assertTrue(expected <= options)
        for method in ['prepare; id', 'publish /tmp/x', 'sh']:
            with self.subTest(method=method), self.assertRaises(broker.Rejected):
                broker.ssh_command(settings, method)

    def test_transport_only_accepts_matching_fixed_result_shapes(self):
        settings = broker.Settings('192.0.2.10', 22, Path('hosts'), Path('key'))
        payload = broker.encode(publish_payload())
        valid = [('prepare', b'', {'analysis_id': ANALYSIS_ID, 'analysis': {}}),
                 ('publish', payload, {'status': 'published', 'id': ANALYSIS_ID}),
                 ('publish', payload, {'status': 'already_published', 'id': ANALYSIS_ID})]
        for method, body, value in valid:
            with self.subTest(method=method, value=value), patch.object(broker, 'bounded_process', return_value=broker.encode(value)) as run:
                self.assertEqual(broker.transport(settings, method, body), value)
                self.assertEqual(run.call_args.args[0][-1], method)
                self.assertEqual(run.call_args.args[1], body)
        invalid = [('prepare', {'analysis_id': '../x', 'analysis': {}}),
                   ('prepare', {'analysis_id': ANALYSIS_ID, 'analysis': []}),
                   ('prepare', {'analysis_id': ANALYSIS_ID, 'analysis': {}, 'secret': 'no'}),
                   ('publish', {'status': 'published', 'id': 'b' * 64}),
                   ('publish', {'status': 'replaced', 'id': ANALYSIS_ID}), ('publish', [])]
        for method, value in invalid:
            with self.subTest(method=method, value=value), patch.object(broker, 'bounded_process', return_value=broker.encode(value)), self.assertRaises(broker.Rejected):
                broker.transport(settings, method, payload)


class TrustedConfigurationTests(unittest.TestCase):
    def make_path(self, *, mode=0o644, owner=0, links=1, size=30, directory_mode=0o755):
        directory = Mock()
        directory.lstat.return_value = SimpleNamespace(st_mode=stat.S_IFDIR | directory_mode, st_uid=0)
        path = Mock()
        path.is_absolute.return_value = True
        path.resolve.return_value = path
        path.parents = [directory]
        path.lstat.return_value = SimpleNamespace(st_mode=stat.S_IFREG | mode, st_uid=owner,
                                                st_nlink=links, st_size=size)
        return path, directory

    def test_root_owned_regular_file_and_read_only_service_credential_are_accepted(self):
        public, _ = self.make_path()
        private, _ = self.make_path(mode=0o400, owner=4321)
        with patch.object(broker.os, 'geteuid', return_value=4321, create=True):
            self.assertIs(broker.trusted_file(public), public)
            self.assertIs(broker.trusted_file(private, private=True), private)

    def test_symlinks_relative_paths_mutable_ancestors_and_other_owners_are_rejected(self):
        for change in ['relative', 'symlink', 'ancestor_write', 'ancestor_owner', 'ancestor_file', 'file_owner']:
            path, directory = self.make_path()
            if change == 'relative':
                path.is_absolute.return_value = False
            elif change == 'symlink':
                path.resolve.return_value = Mock()
            elif change == 'ancestor_write':
                directory.lstat.return_value.st_mode |= 0o020
            elif change == 'ancestor_owner':
                directory.lstat.return_value.st_uid = 999
            elif change == 'ancestor_file':
                directory.lstat.return_value.st_mode = stat.S_IFREG | 0o644
            else:
                path.lstat.return_value.st_uid = 999
            with self.subTest(change=change), self.assertRaises(broker.Rejected):
                broker.trusted_file(path)

    def test_hardlinks_special_files_mutable_files_and_unbounded_sizes_are_rejected(self):
        for values in [dict(links=2), dict(mode=0o666), dict(size=0), dict(size=16385)]:
            path, _ = self.make_path(**values)
            with self.subTest(values=values), self.assertRaises(broker.Rejected):
                broker.trusted_file(path)
        path, _ = self.make_path()
        path.lstat.return_value.st_mode = stat.S_IFIFO | 0o600
        with self.assertRaises(broker.Rejected):
            broker.trusted_file(path)

    def test_private_key_rejects_readable_executable_and_writable_permissions(self):
        for mode in [0o440, 0o404, 0o500, 0o600, 0o777]:
            path, _ = self.make_path(mode=mode, owner=4321)
            with self.subTest(mode=oct(mode)), patch.object(broker.os, 'geteuid', return_value=4321, create=True), self.assertRaises(broker.Rejected):
                broker.trusted_file(path, private=True)

    def settings(self, value=None, pin=None, credential='/run/credentials/test.service'):
        value = {'host': '192.0.2.10', 'port': 22} if value is None else value
        config_path = PurePosixPath('/etc/homeenergy-report-broker/config.json')
        pin_path = PurePosixPath('/etc/homeenergy-report-broker/known_hosts')
        config_file = Mock()
        config_file.read_bytes.return_value = broker.encode(value)
        pin_file = Mock()
        pin_file.read_text.return_value = '192.0.2.10 ssh-ed25519 AAAA' if pin is None else pin

        def trusted(path, private=False):
            if path == config_path:
                return config_file
            if path == pin_path:
                return pin_file
            self.assertTrue(private)
            return path

        with patch.object(broker, 'Path', PurePosixPath), patch.object(broker, 'CONFIG', config_path), \
                patch.object(broker, 'KNOWN_HOSTS', pin_path), patch.object(broker, 'trusted_file', side_effect=trusted), \
                patch.dict(os.environ, {'CREDENTIALS_DIRECTORY': credential}):
            return broker.load_settings()

    def test_settings_accept_only_canonical_ip_port_and_pinned_host(self):
        value = self.settings()
        self.assertEqual((value.host, value.port), ('192.0.2.10', 22))
        self.assertEqual(value.key, PurePosixPath('/run/credentials/test.service/report-key'))
        value = self.settings({'host': '2001:db8::10', 'port': 2222},
                              '[2001:db8::10]:2222 ssh-ed25519 AAAA')
        self.assertEqual(value.port, 2222)

    def test_settings_reject_paths_hostnames_commands_extra_keys_and_invalid_ports(self):
        for value in [{'host': 'example.invalid', 'port': 22}, {'host': '192.0.2.10;id', 'port': 22},
                      {'host': 'fe80::1%eth0', 'port': 22}, {'host': '2001:0db8::10', 'port': 22},
                      {'host': '192.0.2.10', 'port': True}, {'host': '192.0.2.10', 'port': 0},
                      {'host': '192.0.2.10', 'port': 65536}, {'host': '192.0.2.10', 'port': '22'},
                      {'host': '192.0.2.10', 'port': 22, 'command': 'id'}]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.settings(value)

    def test_settings_reject_wrong_host_wildcard_ca_or_multiple_pins(self):
        for pin in ['* ssh-ed25519 AAAA', '192.0.2.11 ssh-ed25519 AAAA',
                    '@cert-authority 192.0.2.10 ssh-ed25519 AAAA',
                    '192.0.2.10 ssh-rsa AAAA', '192.0.2.10 ssh-ed25519 ***',
                    '192.0.2.10 ssh-ed25519 AAAA\n192.0.2.11 ssh-ed25519 AAAA']:
            with self.subTest(pin=pin), self.assertRaises(broker.Rejected):
                self.settings(pin=pin)

    def test_settings_require_the_systemd_credentials_directory(self):
        for value in ['', '.', '/tmp/credentials', '/run/credentials', '/run/credentials/nested/child']:
            with self.subTest(value=value), self.assertRaises(broker.Rejected):
                self.settings(credential=value)


class FramingAndAuditTests(SocketHarness):
    def test_allowed_peer_dispatches_one_request_and_audits_metadata_only(self):
        result, events, call = self.exchange()
        self.assertTrue(result['ok'])
        call.assert_called_once_with('prepare', b'')
        self.assertEqual(events, [{'event': 'report_broker', 'uid': TEST_UID,
                                   'method': 'prepare', 'outcome': 'accepted'}])

    def test_wrong_or_root_peer_cannot_reach_transport(self):
        for peer, allowed in [(TEST_UID + 1, TEST_UID), (0, 0), (0, TEST_UID)]:
            with self.subTest(peer=peer, allowed=allowed):
                result, events, call = self.exchange(peer=peer, allowed=allowed)
                self.assertEqual(result, broker.GENERIC_ERROR)
                call.assert_not_called()
                self.assertEqual(events[0]['outcome'], 'rejected')

    def test_zero_oversize_truncated_header_and_body_are_rejected(self):
        for wire in [b'\x00\x00', struct.pack('!I', 0), struct.pack('!I', broker.MAX_REQUEST + 1),
                     struct.pack('!I', 100) + b'{}']:
            with self.subTest(wire=wire):
                result, _, call = self.exchange(wire=wire)
                self.assertEqual(result, broker.GENERIC_ERROR)
                call.assert_not_called()

    def test_trailing_byte_or_second_frame_is_rejected(self):
        raw = broker.encode({'method': 'prepare', 'payload': {}})
        for suffix in [b'X', frame(b'{}')]:
            with self.subTest(suffix=suffix):
                result, _, call = self.exchange(wire=frame(raw) + suffix)
                self.assertEqual(result, broker.GENERIC_ERROR)
                call.assert_not_called()

    def test_payload_and_upstream_errors_never_enter_response_or_audit(self):
        secret = 'DO-NOT-LOG-THIS-SUBMITTED-TEXT'
        result, events, _ = self.exchange(method='publish', payload=publish_payload(),
                                          call=Mock(side_effect=OSError(secret)))
        self.assertEqual(result, broker.GENERIC_ERROR)
        self.assertNotIn(secret, json.dumps([result, events]))
        self.assertEqual(set(events[0]), {'event', 'uid', 'method', 'outcome'})
        self.assertNotIn('summary', json.dumps(events))
        result, events, call = self.exchange(method=secret)
        self.assertEqual(result, broker.GENERIC_ERROR)
        self.assertEqual(events[0]['method'], 'unknown')
        self.assertNotIn(secret, json.dumps(events))
        call.assert_not_called()

    def test_oversized_transport_output_is_not_delivered(self):
        result, events, _ = self.exchange(call=Mock(return_value={'data': 'x' * broker.MAX_RESPONSE}))
        self.assertEqual(result, broker.GENERIC_ERROR)
        self.assertEqual(events[0]['outcome'], 'rejected')

    def test_absolute_deadline_is_reused_across_partial_reads(self):
        connection = Mock()
        connection.recv.side_effect = [b'a', b'b', b'c']
        with patch.object(broker.time, 'monotonic', side_effect=[10.0, 10.6, 11.1]), self.assertRaises(broker.Rejected):
            broker.receive_exact(connection, 3, 11.0)
        self.assertEqual(connection.recv.call_count, 2)
        self.assertAlmostEqual(connection.settimeout.call_args_list[0].args[0], 1.0)
        self.assertAlmostEqual(connection.settimeout.call_args_list[1].args[0], 0.4)

    def test_client_helper_uses_one_absolute_response_deadline(self):
        connection = Mock()
        connection.recv.side_effect = [b'a', b'b']
        with patch.object(client.time, 'monotonic', side_effect=[10.0, 10.9, 11.1]), self.assertRaises(TimeoutError):
            client._receive(connection, 3, 11.0)
        self.assertEqual(connection.recv.call_count, 2)

    def test_client_only_exposes_fixed_operations(self):
        with patch.object(client, '_call', return_value={}) as call:
            self.assertEqual(client.prepare(), {})
            call.assert_called_once_with('prepare', {})
            call.reset_mock()
            payload = publish_payload()
            client.publish(**payload)
            call.assert_called_once_with('publish', payload)

    def test_client_rejects_oversize_request_before_opening_socket(self):
        with patch.object(client.socket, 'socket') as connection, self.assertRaises(ValueError):
            client.publish(ANALYSIS_ID, localized('x' * client.MAX_REQUEST), [])
        connection.assert_not_called()

    def client_response(self, wire):
        stream = io.BytesIO(wire)
        connection = Mock()
        connection.recv.side_effect = stream.read
        context = Mock()
        context.__enter__ = Mock(return_value=connection)
        context.__exit__ = Mock(return_value=False)
        with patch.object(client.socket, 'socket', return_value=context), \
                patch.object(client.socket, 'AF_UNIX', 1, create=True):
            return client.prepare()

    def test_client_strict_json_rejects_duplicate_keys_constants_and_invalid_utf8(self):
        for body in [b'{"ok":true,"ok":true,"result":{}}', b'{"ok":true,"result":{"x":NaN}}',
                     b'{"ok":true,"result":{"x":Infinity}}', b'\xff', b'{}{}']:
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.client_response(frame(body))

    def test_client_rejects_any_trailing_bytes_after_one_response(self):
        response = frame(b'{"ok":true,"result":{}}')
        for suffix in [b'X', frame(b'{"ok":true,"result":{}}')]:
            with self.subTest(suffix=suffix), self.assertRaises(ValueError):
                self.client_response(response + suffix)

    def test_client_accepts_one_complete_success_response(self):
        result = {'analysis_id': ANALYSIS_ID, 'analysis': {}}
        self.assertEqual(self.client_response(frame(broker.encode({'ok': True, 'result': result}))), result)


class PublisherIntegrationTests(SocketHarness):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='he-broker-test-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.state = self.root / 'reports'
        (self.state / 'public').mkdir(parents=True)
        self.history = self.root / 'history'
        self.history.mkdir()
        self.sentinel = self.root / 'unrelated.txt'
        self.sentinel.write_text('unchanged', encoding='utf-8')
        self.now = 1791800000

    def trusted_adapter(self, method, body):
        if method == 'prepare':
            return publisher.prepare(self.state, self.history, self.now)
        if method == 'publish':
            return publisher.publish(body, self.state, self.now)
        raise AssertionError('Unallowlisted method reached trusted transport')

    def prepare(self):
        response, _, _ = self.exchange(call=self.trusted_adapter)
        self.assertTrue(response['ok'])
        self.assertEqual(response['result']['analysis']['coverage_pct'], 0)
        self.assertEqual(response['result']['analysis']['severity'], 'data_issue')
        return response['result']

    def test_real_empty_history_roundtrip_duplicate_and_replacement_protection(self):
        snapshot = self.prepare()
        payload = publish_payload(snapshot['analysis_id'])
        response, _, _ = self.exchange(method='publish', payload=payload, call=self.trusted_adapter)
        self.assertEqual(response['result']['status'], 'published')
        original = (self.state / 'public/index.json').read_bytes()
        response, _, _ = self.exchange(method='publish', payload=payload, call=self.trusted_adapter)
        self.assertEqual(response['result']['status'], 'already_published')
        changed = copy.deepcopy(payload)
        changed['summary']['en'] = 'Attempt to replace a published report.'
        response, _, _ = self.exchange(method='publish', payload=changed, call=self.trusted_adapter)
        self.assertEqual(response, broker.GENERIC_ERROR)
        self.assertEqual((self.state / 'public/index.json').read_bytes(), original)
        feed = json.loads(original)
        self.assertEqual(len(feed['reports']), 1)
        self.assertEqual(feed['reports'][0]['analysis'], snapshot['analysis'])
        self.assertEqual(feed['reports'][0]['narrative']['summary'], payload['summary'])
        self.assertEqual(self.sentinel.read_text(encoding='utf-8'), 'unchanged')
        self.assertEqual(list(self.history.iterdir()), [])

    def test_paths_commands_markup_and_unknown_findings_cannot_publish(self):
        snapshot = self.prepare()
        payload = publish_payload(snapshot['analysis_id'])
        cases = [dict(payload, path=str(self.sentinel)), dict(payload, command='id'),
                 dict(payload, summary=localized('<script>alert(1)</script>')),
                 dict(payload, summary=localized('https://example.invalid')),
                 dict(payload, notes=[{'finding_id': '../unrelated.txt', 'commentary': localized()}])]
        for submitted in cases:
            with self.subTest(submitted=submitted):
                response, _, _ = self.exchange(method='publish', payload=submitted, call=self.trusted_adapter)
                self.assertEqual(response, broker.GENERIC_ERROR)
        self.assertFalse((self.state / 'public/index.json').exists())
        self.assertEqual(self.sentinel.read_text(encoding='utf-8'), 'unchanged')
        self.assertEqual(list(self.history.iterdir()), [])


@unittest.skipUnless(LINUX, 'Requires Linux Unix peer credentials and nonblocking pipe selector')
class LinuxBoundaryTests(unittest.TestCase):
    def test_kernel_peer_uid_matches_socket_creator(self):
        with ExitStack() as stack:
            left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
            stack.enter_context(left)
            stack.enter_context(right)
            self.assertEqual(broker.peer_uid(right), os.geteuid())

    def test_real_peer_acceptance_and_rejection(self):
        if os.geteuid() == 0:
            self.skipTest('Production refuses UID 0; acceptance requires a non-root test user')
        for allowed, expected in [(os.geteuid(), True), (os.geteuid() + 1, False)]:
            with self.subTest(allowed=allowed), ExitStack() as stack:
                left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
                stack.enter_context(left)
                stack.enter_context(right)
                call = Mock(return_value={'test': True})
                audit = []
                worker = threading.Thread(target=broker.serve_connection,
                                          args=(right, allowed, call, audit.append), daemon=True)
                worker.start()
                if expected:
                    left.sendall(frame(broker.encode({'method': 'prepare', 'payload': {}})))
                left.shutdown(socket.SHUT_WR)
                response = broker.decode(broker.receive_frame(left, broker.MAX_RESPONSE, 2), broker.MAX_RESPONSE)
                worker.join(2)
                self.assertFalse(worker.is_alive())
                self.assertEqual(response['ok'], expected)
                self.assertEqual(call.call_count, int(expected))

    def test_slow_dribble_cannot_extend_the_total_frame_deadline(self):
        with ExitStack() as stack:
            left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
            stack.enter_context(left)
            stack.enter_context(right)

            def dribble():
                try:
                    for byte in frame(b'{}'):
                        left.sendall(bytes([byte]))
                        time.sleep(0.05)
                    left.shutdown(socket.SHUT_WR)
                except OSError:
                    pass

            worker = threading.Thread(target=dribble, daemon=True)
            worker.start()
            started = time.monotonic()
            try:
                with self.assertRaises((broker.Rejected, OSError)):
                    broker.receive_frame(right, 100, 0.12)
                self.assertLess(time.monotonic() - started, 0.8)
            finally:
                left.close()
                right.close()
                worker.join(1)
            self.assertFalse(worker.is_alive())

    def test_complete_request_without_half_close_times_out(self):
        with ExitStack() as stack:
            left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
            stack.enter_context(left)
            stack.enter_context(right)
            left.sendall(frame(b'{}'))
            with self.assertRaises((broker.Rejected, OSError)):
                broker.receive_frame(right, 100, 0.1)

    def run_child(self, script, body=b'', seconds=2):
        children = []
        original = subprocess.Popen

        def tracked(*args, **kwargs):
            process = original(*args, **kwargs)
            children.append(process)
            return process

        try:
            with patch.object(broker.subprocess, 'Popen', side_effect=tracked):
                return broker.bounded_process([sys.executable, '-I', '-c', script], body, seconds)
        finally:
            self.assertEqual(len(children), 1)
            process = children[0]
            self.assertIsNotNone(process.poll(), 'Child was not reaped')
            self.assertTrue(all(pipe.closed for pipe in (process.stdin, process.stdout, process.stderr)))

    def test_process_roundtrip_accepts_bounded_stdout_and_stderr(self):
        output = self.run_child('import sys; data=sys.stdin.buffer.read(); sys.stderr.write("safe"); sys.stdout.buffer.write(data)',
                                b'accepted input')
        self.assertEqual(output, b'accepted input')

    def test_stdout_flood_is_bounded_and_child_reaped(self):
        with self.assertRaises(broker.Rejected):
            self.run_child('import os,time; os.write(1,b"x"*70000); time.sleep(10)')

    def test_stderr_flood_is_bounded_and_child_reaped(self):
        with self.assertRaises(broker.Rejected):
            self.run_child('import os,time; os.write(2,b"x"*9000); time.sleep(10)')

    def test_stalled_stdin_is_included_in_timeout_and_child_reaped(self):
        started = time.monotonic()
        with self.assertRaises(broker.Rejected):
            self.run_child('import time; time.sleep(10)', b'x' * 1048576, seconds=0.2)
        self.assertLess(time.monotonic() - started, 1.5)

    def test_closed_streams_do_not_bypass_process_exit_deadline(self):
        with self.assertRaises((broker.Rejected, subprocess.TimeoutExpired)):
            self.run_child('import os,time; os.close(0); os.close(1); os.close(2); time.sleep(10)', seconds=0.2)

    def test_nonzero_exit_is_rejected_and_child_reaped(self):
        with self.assertRaises(broker.Rejected):
            self.run_child('raise SystemExit(3)')


@unittest.skipUnless(LINUX, 'Requires a filesystem Unix-domain socket')
class LinuxClientTests(unittest.TestCase):
    def call_server(self, handler, invoke=client.prepare):
        with tempfile.TemporaryDirectory(prefix='he-client-') as directory:
            path = str(Path(directory) / 'broker.sock')
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                listener.bind(path)
                listener.listen(1)
                listener.settimeout(3)
                errors = []

                def serve():
                    try:
                        connection, _ = listener.accept()
                        with connection:
                            handler(connection)
                    except BaseException as error:
                        errors.append(error)

                worker = threading.Thread(target=serve, daemon=True)
                worker.start()
                try:
                    with patch.object(client, 'SOCKET_PATH', path):
                        return invoke()
                finally:
                    worker.join(4)
                    self.assertFalse(worker.is_alive())
                    self.assertEqual(errors, [])

    def test_real_client_and_broker_end_to_end(self):
        result = {'analysis_id': ANALYSIS_ID, 'analysis': {'schema_version': 2}}
        audit = []

        def serve(connection):
            with patch.object(broker, 'peer_uid', return_value=TEST_UID):
                broker.serve_connection(connection, TEST_UID, lambda method, body: result, audit.append)

        self.assertEqual(self.call_server(serve), result)
        self.assertEqual(audit[0]['outcome'], 'accepted')

    def test_client_rejects_invalid_lengths_truncated_responses_and_error_envelopes(self):
        cases = [struct.pack('!I', 0), struct.pack('!I', client.MAX_RESPONSE + 1),
                 struct.pack('!I', 100) + b'{}', b'\x00', frame(b'[]'),
                 frame(broker.encode(broker.GENERIC_ERROR)),
                 frame(broker.encode({'ok': True, 'result': {}, 'extra': 'not allowed'})),
                 frame(b'{"ok":true,"ok":true,"result":{}}'),
                 frame(b'{"ok":true,"result":NaN}'),
                 frame(b'{"ok":true,"result":{}}') + b'X']
        for wire in cases:
            with self.subTest(wire=wire[:30]):
                def serve(connection):
                    broker.receive_frame(connection, broker.MAX_REQUEST, 2)
                    connection.sendall(wire)
                    connection.shutdown(socket.SHUT_WR)

                with self.assertRaises(ValueError):
                    self.call_server(serve)


if __name__ == '__main__':
    unittest.main()
