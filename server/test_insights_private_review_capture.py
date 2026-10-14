"""Synthetic in-memory capture tests: no network, model or real evidence writes."""
import ast
import copy
import hashlib
import io
from pathlib import Path
import stat
import subprocess
import types
import unittest
from unittest.mock import MagicMock, patch

import insights_private_review_capture as capture
from insights_broker import encode


class MemoryStore:
    def __init__(self):
        self.values = {}
        self.order = []

    def exists(self, kind):
        return kind in self.values

    def create(self, kind, value):
        if self.exists(kind):
            raise ValueError('already exists')
        self.values[kind] = copy.deepcopy(value)
        self.order.append(kind)


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore()
        self.snapshot = {'schema_version': 1, 'scope': 'private_review',
            'synthetic': False, 'captured_at': 1791964800,
            'analysis_id': 'a' * 64, 'analysis': {'synthetic_fixture': True}}
        self.raw = encode(self.snapshot) + b'\n'

    def test_saves_only_one_snapshot_and_returns_no_analysis(self):
        def read():
            self.assertEqual(self.store.order, ['capture'])
            return self.raw

        with patch.object(capture, 'verify_material') as material, \
                patch.object(capture, '_read_remote', side_effect=read), \
                patch.object(capture, '_validate_snapshot') as validate:
            result = capture.capture_once(self.store)
        material.assert_called_once_with()
        validate.assert_called_once_with(self.snapshot)
        self.assertEqual(self.store.order, ['capture', 'snapshot'])
        self.assertEqual(self.store.values['snapshot'], self.snapshot)
        self.assertFalse(result['synthetic'])
        self.assertFalse(result['model_started'])
        self.assertFalse(result['public_published'])
        self.assertNotIn('analysis', result)

    def test_existing_capture_snapshot_or_model_receipt_prevents_network(self):
        for kind in ('capture', 'snapshot', 'receipt'):
            with self.subTest(kind=kind):
                store = MemoryStore()
                store.values[kind] = {}
                with patch.object(capture, 'verify_material') as material, \
                        patch.object(capture, '_read_remote') as remote, \
                        self.assertRaisesRegex(capture.CaptureFailure, 'already_consumed'):
                    capture.capture_once(store)
                material.assert_not_called()
                remote.assert_not_called()

    def test_bad_material_does_not_consume_capture_or_connect(self):
        with patch.object(capture, 'verify_material', side_effect=ValueError('rejected')), \
                patch.object(capture, '_read_remote') as remote, self.assertRaises(ValueError):
            capture.capture_once(self.store)
        self.assertEqual(self.store.values, {})
        remote.assert_not_called()

    def test_exclusive_capture_receipt_failure_prevents_network(self):
        with patch.object(capture, 'verify_material'), \
                patch.object(self.store, 'create', side_effect=ValueError('collision')), \
                patch.object(capture, '_read_remote') as remote, self.assertRaises(ValueError):
            capture.capture_once(self.store)
        remote.assert_not_called()

    def test_failed_transport_consumes_capture_and_cannot_retry(self):
        with patch.object(capture, 'verify_material'), \
                patch.object(capture, '_read_remote', side_effect=ValueError('failure')) as remote:
            with self.assertRaises(ValueError):
                capture.capture_once(self.store)
            with self.assertRaisesRegex(capture.CaptureFailure, 'already_consumed'):
                capture.capture_once(self.store)
        self.assertEqual(remote.call_count, 1)
        self.assertEqual(self.store.order, ['capture'])

    def test_oversize_invalid_duplicate_and_noncanonical_json_rejected(self):
        for raw in (b'x' * (capture.MAX_RESPONSE + 1), b'not-json', b'{}',
                    b'{"a":1,"a":2}\n', self.raw[:-1], self.raw + b'\n',
                    b'{"a":NaN}\n'):
            with self.subTest(size=len(raw)), patch.object(capture, 'verify_material'), \
                    patch.object(capture, '_read_remote', return_value=raw), \
                    patch.object(capture, '_validate_snapshot') as validate, \
                    self.assertRaisesRegex(capture.CaptureFailure, '^private_capture_snapshot_rejected$'):
                capture.capture_once(MemoryStore())
            validate.assert_not_called()

    def test_schema_validation_failure_never_saves_or_leaks_error(self):
        with patch.object(capture, 'verify_material'), \
                patch.object(capture, '_read_remote', return_value=self.raw), \
                patch.object(capture, '_validate_snapshot', side_effect=ValueError('SECRET')), \
                self.assertRaisesRegex(capture.CaptureFailure, '^private_capture_snapshot_rejected$'):
            capture.capture_once(self.store)
        self.assertEqual(self.store.order, ['capture'])

    def test_fixed_ssh_command_has_no_ambient_configuration_or_forwarding(self):
        command = capture.ssh_command()
        self.assertEqual(command[:3], [str(capture.SSH), '-F', 'none'])
        self.assertEqual(command[-2:], ['shini@91.98.44.219', '/usr/bin/python3 -I -B -'])
        self.assertEqual(command[command.index('-i') + 1], str(capture.KEY))
        for setting in ('BatchMode=yes', 'StrictHostKeyChecking=yes',
                'IdentitiesOnly=yes', 'IdentityAgent=none', 'ClearAllForwardings=yes',
                'ForwardAgent=no', 'ForwardX11=no', 'ControlPath=none',
                'ProxyCommand=none', 'ProxyJump=none', 'PermitLocalCommand=no',
                'RequestTTY=no', 'ConnectTimeout=10', 'ConnectionAttempts=1',
                'GlobalKnownHostsFile=none', 'UpdateHostKeys=no'):
            self.assertIn(setting, command)
        self.assertNotIn('prepare', command)
        self.assertNotIn('publish', command)
        self.assertNotIn('sudo', command)

    def test_material_check_never_reads_private_key_bytes(self):
        executable = b'fixture executable'
        public_pin = b'91.98.44.219 ssh-ed25519 AAAATEST=\n'
        reads = []

        def read(path):
            reads.append(path)
            if path == capture.SSH:
                return executable
            if path == capture.KNOWN_HOSTS:
                return public_pin
            self.fail('Unexpected content read')

        with patch.object(capture.os, 'name', 'nt'), \
                patch.object(capture.windows, 'regular_file') as regular, \
                patch.object(Path, 'read_bytes', read), \
                patch.object(capture, 'SSH_SHA256', hashlib.sha256(executable).hexdigest()), \
                patch.object(capture, 'KNOWN_HOSTS_SHA256', hashlib.sha256(public_pin).hexdigest()):
            capture.verify_material()
        self.assertEqual(reads, [capture.SSH, capture.KNOWN_HOSTS])
        regular.assert_any_call(capture.KEY, 16384)
        regular.assert_any_call(capture.SSH, 16 * 1024 * 1024, allow_system_hardlinks=True)

    def test_unreviewed_binary_or_host_hash_blocks_capture(self):
        with patch.object(capture.os, 'name', 'nt'), \
                patch.object(capture, 'SSH_SHA256', 'unreviewed'), \
                patch.object(capture.windows, 'regular_file') as regular, \
                self.assertRaisesRegex(capture.CaptureFailure, 'pins_unreviewed'):
            capture.verify_material()
        regular.assert_not_called()


class TransportTests(unittest.TestCase):
    def process(self, stdout=b'aggregate', stderr=b'', returncode=0):
        process = MagicMock()
        process.stdin = io.BytesIO()
        process.stdout = io.BytesIO(stdout)
        process.stderr = io.BytesIO(stderr)
        process.wait.return_value = returncode
        process.poll.return_value = returncode
        return process

    def invoke(self, process):
        group = MagicMock()
        with patch.object(capture.subprocess, 'Popen', return_value=process) as spawn, \
                patch.object(capture.windows, 'ProcessGroup', return_value=group):
            try:
                result = capture._read_remote()
            finally:
                group.close.assert_called_once_with()
        arguments, options = spawn.call_args
        self.assertEqual(arguments, (capture.ssh_command(),))
        self.assertFalse(options['shell'])
        self.assertEqual(options['env'], capture.windows.clean_environment())
        self.assertTrue(options['close_fds'])
        self.assertTrue(process.stdin.closed)
        self.assertTrue(process.stdout.closed)
        self.assertTrue(process.stderr.closed)
        return result

    def test_output_is_bounded_and_stderr_is_never_returned(self):
        self.assertEqual(self.invoke(self.process(b'aggregate', b'PRIVATE ERROR')), b'aggregate')
        self.assertEqual(len(self.invoke(self.process(b'x' * capture.MAX_RESPONSE))), capture.MAX_RESPONSE)
        self.assertEqual(self.invoke(self.process(b'aggregate', b'x' * capture.MAX_STDERR)), b'aggregate')

    def test_oversize_stdout_or_stderr_or_nonzero_exit_rejected(self):
        for process in (self.process(b'x' * (capture.MAX_RESPONSE + 1)),
                        self.process(stderr=b'x' * (capture.MAX_STDERR + 1)),
                        self.process(stderr=b'SECRET', returncode=1)):
            with self.subTest(), self.assertRaisesRegex(capture.CaptureFailure,
                    '^private_capture_transport_rejected$'):
                self.invoke(process)

    def test_timeout_cleans_up_owned_process(self):
        process = self.process()
        process.wait.side_effect = [subprocess.TimeoutExpired('hidden', 60), 0]
        process.poll.return_value = None
        with self.assertRaisesRegex(capture.CaptureFailure, '^private_capture_transport_rejected$'):
            self.invoke(process)
        process.kill.assert_called_once_with()

    def test_expired_deadline_rejects_even_if_output_completes(self):
        with patch.object(capture.time, 'monotonic', side_effect=[0, 61]), \
                self.assertRaisesRegex(capture.CaptureFailure, '^private_capture_transport_rejected$'):
            self.invoke(self.process())


class RemoteScriptTests(unittest.TestCase):
    def setUp(self):
        self.tree = ast.parse(capture.REMOTE_SCRIPT)

    def test_remote_source_fixed_hashes_paths_and_no_publication_imports(self):
        source = capture.REMOTE_SCRIPT.decode('ascii')
        self.assertIn('/var/www/homeenergy/runtime/history', source)
        self.assertIn('/opt/homeenergy-reports-staging', source)
        self.assertIn('3c48a5254c025c8d3bfaf190c4d785bfef2ab800015de0e4fdb69f5363e0c70e', source)
        self.assertIn('957e784430904770d291bfaf2c402b513007d7ff953ae8c30a31f96ff11c31c5', source)
        for denied in ('insights_publish', 'sqlite', 'subprocess', 'sudo', 'write_bytes', 'write_text'):
            self.assertNotIn(denied, source)
        self.assertIn('"synthetic":False', source)
        self.assertIn('len(body)<=65536', source)

    def module_loader(self, *, owner=0, links=1, mode=stat.S_IFREG | 0o644,
                      parent_owner=0, parent_mode=stat.S_IFDIR | 0o755,
                      changed_inode=False):
        source = b'sentinel = 17\n'
        before = types.SimpleNamespace(st_uid=owner, st_mode=mode, st_nlink=links,
                                      st_dev=1, st_ino=2, st_size=len(source))
        opened = copy.copy(before)
        if changed_inode:
            opened.st_ino = 99
        parent = MagicMock()
        parent.lstat.return_value = types.SimpleNamespace(st_uid=parent_owner, st_mode=parent_mode)
        path = MagicMock()
        path.resolve.return_value = path
        path.parents = [parent]
        path.lstat.return_value = before
        root = MagicMock()
        root.__truediv__.return_value = path
        operating = MagicMock()
        operating.O_RDONLY = 0
        operating.O_NOFOLLOW = 1
        operating.O_NONBLOCK = 2
        stream = MagicMock()
        stream.__enter__.return_value = stream
        stream.read.return_value = source
        stream.fileno.return_value = 10
        operating.fdopen.return_value = stream
        operating.fstat.return_value = opened
        namespace = {'Path': lambda _: root, 'stat': stat, 'os': operating,
            'hashlib': hashlib, 'types': types, 'sys': types.SimpleNamespace(modules={})}
        functions = ast.Module(body=[node for node in self.tree.body
                                   if isinstance(node, ast.FunctionDef)], type_ignores=[])
        exec(compile(functions, '<fixed-remote-helpers>', 'exec'), namespace)
        return namespace, source

    def test_remote_loader_executes_only_verified_bytes(self):
        namespace, source = self.module_loader()
        result = namespace['load']('fixture', hashlib.sha256(source).hexdigest())
        self.assertEqual(result.sentinel, 17)
        self.assertEqual(namespace['os'].open.call_args.args[1], 3)

    def test_remote_loader_rejects_untrusted_files_and_ancestors(self):
        cases = ({'owner': 1000}, {'links': 2}, {'mode': stat.S_IFREG | 0o664},
            {'mode': stat.S_IFLNK | 0o777}, {'parent_owner': 1000},
            {'parent_mode': stat.S_IFDIR | 0o775}, {'parent_mode': stat.S_IFLNK | 0o777},
            {'changed_inode': True})
        for case in cases:
            with self.subTest(case=case):
                namespace, source = self.module_loader(**case)
                with self.assertRaises(ValueError):
                    namespace['load']('fixture', hashlib.sha256(source).hexdigest())
                self.assertEqual(namespace['sys'].modules, {})

    def test_remote_loader_rejects_wrong_digest_before_execution(self):
        namespace, _ = self.module_loader()
        with self.assertRaises(ValueError):
            namespace['load']('fixture', '0' * 64)
        self.assertEqual(namespace['sys'].modules, {})


if __name__ == '__main__':
    unittest.main()
