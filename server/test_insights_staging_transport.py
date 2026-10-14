"""No model/SSH calls: protocol fixtures and short-lived local Python children."""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import call, patch

import insights_broker as contract
import insights_staging_mcp as adapter
import insights_staging_publish as staging
import insights_windows_transport as windows
from insights_analysis import analyze
from test_insights_publish import localized


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.history = self.root / 'history'
        self.history.mkdir()
        analysis = analyze(self.history, 1791882000)
        self.snapshot = {'scope': 'staging', 'synthetic': True, 'result': {
            'analysis_id': hashlib.sha256(contract.encode(analysis)).hexdigest(), 'analysis': analysis}}
        self.payload = {'analysis_id': self.snapshot['result']['analysis_id'], 'summary': localized(), 'notes': []}
        self.calls = []

        def transport(method, body):
            self.calls.append((method, body))
            result = self.snapshot if method == 'prepare' else {
                'scope': 'staging', 'synthetic': True, 'result': {'status': 'staged', 'id': self.payload['analysis_id']}}
            return contract.encode(result)

        self.reports = adapter.StagingReports(transport)

    def test_fixed_two_tool_schema(self):
        self.assertEqual([tool['name'] for tool in adapter.TOOLS], ['prepare', 'publish'])
        for tool in adapter.TOOLS:
            self.assertIs(tool['inputSchema']['additionalProperties'], False)
        self.assertEqual(set(adapter.TOOLS[1]['inputSchema']['properties']), {'analysis_id', 'summary', 'notes'})

    def test_one_frozen_analysis_and_defensive_copies(self):
        result = self.reports.call('prepare', {})
        result['result']['analysis']['coverage_pct'] = 100
        self.assertEqual(self.reports.call('prepare', {})['result']['analysis']['coverage_pct'], 0)
        self.assertEqual(len(self.calls), 1)

    def test_success_and_identical_retry_only(self):
        self.reports.call('prepare', {})
        self.reports.call('publish', self.payload)
        self.reports.call('publish', self.payload)
        with self.assertRaises(ValueError):
            self.reports.call('publish', dict(self.payload, summary=localized('Changed content')))
        self.assertEqual(len(self.calls), 3)

    def test_lost_response_requires_identical_retry(self):
        self.reports.call('prepare', {})
        original = self.reports._transport
        self.reports._transport = lambda *_: (_ for _ in ()).throw(OSError('secret upstream detail'))
        with self.assertRaises(OSError):
            self.reports.call('publish', self.payload)
        self.reports._transport = original
        with self.assertRaises(ValueError):
            self.reports.call('publish', dict(self.payload, summary=localized('Different report')))
        self.assertEqual(self.reports.call('publish', self.payload)['result']['status'], 'staged')

    def test_budget_includes_failed_transports(self):
        self.reports._transport = lambda *_: (_ for _ in ()).throw(OSError())
        for _ in range(adapter.MAX_CALLS):
            with self.assertRaises(OSError):
                self.reports.call('prepare', {})
        with self.assertRaises(contract.Rejected):
            self.reports.call('prepare', {})

    def test_rejects_unprepared_changed_unknown_or_out_of_scope_requests(self):
        with self.assertRaises(ValueError):
            self.reports.call('publish', self.payload)
        self.reports.call('prepare', {})
        for name, arguments in [('shell', {}), ('prepare', {'path': '/etc/passwd'}),
                                ('publish', dict(self.payload, command='id')),
                                ('publish', dict(self.payload, analysis_id='a' * 64)),
                                ('publish', dict(self.payload, summary={'en': 'No translations'})),
                                ('publish', dict(self.payload, notes=[{'finding_id': 'unknown', 'commentary': localized()}])),
                                ('publish', dict(self.payload, summary=localized('<script>bad</script>')))]:
            with self.subTest(name=name, arguments=arguments), self.assertRaises(ValueError):
                self.reports.call(name, arguments)
        self.assertEqual(len(self.calls), 1)

    def test_rejects_wrong_scope_digest_schema_or_ack(self):
        invalid = []
        for key, value in [('scope', 'production'), ('synthetic', False), ('synthetic', 1)]:
            invalid.append(dict(self.snapshot, **{key: value}))
        invalid.append(dict(self.snapshot, password='private'))
        for mutate in (lambda s: s['result'].update(analysis_id='a' * 64),
                       lambda s: s['result']['analysis'].update(schema_version=1),
                       lambda s: s['result']['analysis'].update(path='/private')):
            sample = copy.deepcopy(self.snapshot)
            mutate(sample)
            invalid.append(sample)
        for sample in invalid:
            with self.assertRaises(ValueError):
                adapter.validate_response(contract.encode(sample), 'prepare')
        for status, identifier in [('published', self.payload['analysis_id']), ('staged', 'b' * 64)]:
            with self.assertRaises(ValueError):
                adapter.validate_response(contract.encode({'scope': 'staging', 'synthetic': True,
                    'result': {'status': status, 'id': identifier}}), 'publish', self.payload['analysis_id'])

    def protocol(self, requests):
        source = io.BytesIO(b''.join(contract.encode(item) + b'\n' for item in requests))
        output, audit = io.BytesIO(), []
        adapter.serve(source, output, self.reports, audit.append)
        return [json.loads(line) for line in output.getvalue().splitlines()], audit

    @staticmethod
    def initialize():
        return {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                'params': {'protocolVersion': '2025-03-26', 'capabilities': {}, 'clientInfo': {'name': 'local-test'}}}

    def test_protocol_round_trip_and_secret_free_audit(self):
        replies, audit = self.protocol([self.initialize(),
            {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'},
            {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {'name': 'prepare', 'arguments': {}}},
            {'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call', 'params': {'name': 'publish', 'arguments': self.payload}}])
        self.assertEqual(len(replies), 4)
        self.assertEqual(json.loads(replies[-1]['result']['content'][0]['text'])['result']['status'], 'staged')
        self.assertTrue(all(set(event) == {'timestamp_utc', 'event', 'attempt', 'method', 'outcome'} for event in audit))
        self.assertNotIn(self.payload['summary']['en'], json.dumps(audit))

    def test_error_sanitization_and_no_transport_before_initialize(self):
        self.reports._transport = lambda *_: (_ for _ in ()).throw(OSError('SECRET_KEY /private/path'))
        replies, audit = self.protocol([
            {'jsonrpc': '2.0', 'id': 0, 'method': 'tools/call', 'params': {'name': 'prepare', 'arguments': {}}},
            self.initialize(),
            {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {'name': 'prepare', 'arguments': {}}},
            {'jsonrpc': '2.0', 'id': {'secret': 'SECRET_KEY'}, 'method': 'tools/list'}])
        self.assertIn('error', replies[0])
        self.assertIn('error', replies[2])
        self.assertIsNone(replies[3]['id'])
        self.assertNotIn('SECRET_KEY', json.dumps([replies, audit]))
        self.assertEqual(self.reports._calls, 1)

    def test_optional_progress_token_never_changes_tool_arguments_or_audit(self):
        for token in (0, -1, 2**53 - 1, 'PRIVATE_TOKEN_DO_NOT_LOG'):
            with self.subTest(token=token):
                self.reports = adapter.StagingReports(self.reports._transport)
                replies, audit = self.protocol([self.initialize(),
                    {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list',
                     'params': {'_meta': {'progressToken': token}}},
                    {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call',
                     'params': {'name': 'prepare', 'arguments': {}, '_meta': {'progressToken': token}}}])
                self.assertEqual(replies[1]['result']['tools'], adapter.TOOLS)
                self.assertIn('result', replies[2])
                self.assertNotIn('PRIVATE_TOKEN_DO_NOT_LOG', json.dumps([replies, audit]))
        self.assertEqual(self.calls, [('prepare', b'')] * 4)

    def test_progress_tokens_do_not_change_publication_retry_bytes(self):
        replies, audit = self.protocol([self.initialize(),
            {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
             'params': {'name': 'prepare', 'arguments': {}, '_meta': {'progressToken': 1}}},
            *({'jsonrpc': '2.0', 'id': index, 'method': 'tools/call',
               'params': {'name': 'publish', 'arguments': self.payload,
                          '_meta': {'progressToken': token}}}
              for index, token in enumerate(('PRIVATE_TOKEN_FIRST', 'PRIVATE_TOKEN_RETRY'), 3))])
        self.assertTrue(all('result' in reply for reply in replies))
        self.assertEqual(self.calls, [('prepare', b''),
                                     ('publish', contract.encode(self.payload)),
                                     ('publish', contract.encode(self.payload))])
        self.assertNotIn('PRIVATE_TOKEN', json.dumps([replies, audit]))

    def test_progress_token_cannot_bypass_initialization_or_tool_allowlist(self):
        replies, _ = self.protocol([
            {'jsonrpc': '2.0', 'id': 0, 'method': 'tools/call',
             'params': {'name': 'prepare', 'arguments': {}, '_meta': {'progressToken': 1}}},
            self.initialize(),
            {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
             'params': {'name': 'shell', 'arguments': {}, '_meta': {'progressToken': 2}}}])
        self.assertIn('error', replies[0])
        self.assertIn('error', replies[2])
        self.assertEqual(self.calls, [])

    def test_progress_metadata_is_strictly_bounded_and_cannot_smuggle_arguments(self):
        malformed = [None, [], {}, {'path': 'SECRET'},
                     {'progressToken': 1, 'command': 'SECRET'},
                     *({'progressToken': value} for value in
                       (True, 1.5, 2**53, -(2**53), '', 'x' * 129, 'SECRET/path', {}, []))]
        for metadata in malformed:
            with self.subTest(metadata=metadata):
                replies, audit = self.protocol([self.initialize(),
                    {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
                     'params': {'name': 'prepare', 'arguments': {}, '_meta': metadata}}])
                self.assertIn('error', replies[1])
                self.assertNotIn('SECRET', json.dumps([replies, audit]))
        for method, params in (
            ('tools/list', {'cursor': 'SECRET'}),
            ('tools/call', {'name': 'prepare', 'arguments': {'path': 'SECRET'}}),
            ('tools/call', {'name': 'prepare', 'arguments': {}, 'command': 'SECRET'}),
        ):
            replies, _ = self.protocol([self.initialize(),
                {'jsonrpc': '2.0', 'id': 2, 'method': method,
                 'params': {**params, '_meta': {'progressToken': 1}}}])
            self.assertIn('error', replies[1])
        self.assertEqual(self.calls, [])

    def test_bad_json_size_depth_duplicates_constants_and_notifications(self):
        frames = [b'[]\n', b'{"id":1,"id":2}\n', b'{"value":NaN}\n', b'\xff\n',
                  b'[' * 1100 + b']' * 1100 + b'\n', b'x' * (adapter.MAX_FRAME + 1) + b'\n',
                  b'{"jsonrpc":"2.0","method":"tools/call","params":{"name":"prepare","arguments":{}}}\n']
        for frame in frames:
            with self.subTest(frame=frame[:40]):
                output = io.BytesIO()
                adapter.serve(io.BytesIO(frame), output, self.reports, lambda _: None)
                self.assertIn('error', json.loads(output.getvalue()))
        self.assertEqual(self.calls, [])

    def test_entry_point_is_disabled(self):
        result = subprocess.run([sys.executable, '-I', '-B', str(Path(adapter.__file__))],
                                capture_output=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b'')
        self.assertEqual(result.stderr.strip(), b'Staging launcher integration is not activated.')

    def test_error_preserves_only_valid_string_ids(self):
        replies, _ = self.protocol([self.initialize(),
            {'jsonrpc': '2.0', 'id': 'safe_123', 'method': 'tools/call', 'params': {'name': 'wrong', 'arguments': {}}},
            {'jsonrpc': '2.0', 'id': 'invalid/SECRET', 'method': 'tools/list'}])
        self.assertEqual(replies[1]['id'], 'safe_123')
        self.assertIsNone(replies[2]['id'])
        self.assertNotIn('SECRET', json.dumps(replies))

    def test_mcp_to_real_local_process_to_private_staging_store(self):
        state = self.root / 'staging'
        (state / 'public').mkdir(parents=True)
        sentinel = self.root / 'unrelated.txt'
        sentinel.write_text('unchanged', encoding='utf-8')
        methods = []

        def local_transport(method, body):
            self.assertIn(method, ('prepare', 'publish'))
            methods.append(method)
            # Trusted test-only child; no SSH, credentials or production paths.
            program = ('import sys; from pathlib import Path; '
                       f'sys.path.insert(0, {str(Path(staging.__file__).parent)!r}); '
                       'import insights_staging_publish as s; '
                       f'result=s.dispatch({method!r}, sys.stdin.buffer.read(), '
                       f'state=Path({str(state)!r}), history=Path({str(self.history)!r}), now=1791882000); '
                       'sys.stdout.buffer.write(s.publisher.encoded(result))')
            return windows.bounded_process([sys.executable, '-I', '-B', '-c', program], body, env=os.environ.copy())

        self.reports = adapter.StagingReports(local_transport)
        replies, _ = self.protocol([self.initialize(),
            {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {'name': 'prepare', 'arguments': {}}},
            {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {'name': 'publish', 'arguments': self.payload}},
            {'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call', 'params': {'name': 'publish', 'arguments': self.payload}}])
        results = [json.loads(reply['result']['content'][0]['text']) for reply in replies[1:]]
        self.assertEqual(results[1]['result']['status'], 'staged')
        self.assertEqual(results[2]['result']['status'], 'already_staged')
        self.assertEqual(methods, ['prepare', 'publish', 'publish'])
        feed = json.loads((state / 'public/index.json').read_text(encoding='utf-8'))
        self.assertEqual(len(feed['reports']), 1)
        self.assertEqual(feed['reports'][0]['narrative']['summary'], self.payload['summary'])
        self.assertEqual(sentinel.read_text(encoding='utf-8'), 'unchanged')
        self.assertEqual(list(self.history.iterdir()), [])


class WindowsTransportTests(unittest.TestCase):
    def setUp(self):
        self.destination = windows.Destination('192.0.2.10', 22, 'a' * 64, 'b' * 64)

    def test_exact_destination_user_key_and_command(self):
        argv = windows.ssh_command(self.destination, 'prepare')
        self.assertEqual(argv[0], str(windows.SSH))
        self.assertEqual(argv[-2:], ['homeenergy-reporter-staging@192.0.2.10', 'prepare'])
        self.assertEqual(argv[argv.index('-i') + 1], str(windows.KEY))
        self.assertEqual(argv[argv.index('-F') + 1], 'none')
        for option in ['IdentityAgent=none', 'StrictHostKeyChecking=yes', 'ProxyCommand=none',
                       'ProxyJump=none', 'PermitLocalCommand=no', 'ClearAllForwardings=yes',
                       'ControlMaster=no', 'CertificateFile=none', 'GlobalKnownHostsFile=none']:
            self.assertIn(option, argv)
        self.assertNotIn('codex_access_key', ' '.join(argv))

    def test_no_arbitrary_command_host_port_or_pin(self):
        for command in ('sh', 'publish; id', 'prepare /etc/passwd', '', '-V'):
            with self.assertRaises(ValueError):
                windows.ssh_command(self.destination, command)
        for host, port, digest in [('example.com', 22, 'a' * 64), ('-F foo', 22, 'a' * 64),
                                   ('192.0.2.10', True, 'a' * 64), ('192.0.2.10', 0, 'a' * 64),
                                   ('192.0.2.10', 22, 'unknown')]:
            with self.assertRaises(ValueError):
                windows.Destination(host, port, digest, 'b' * 64).validate()

    def test_environment_drops_ambient_credentials(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'secret', 'SSH_AUTH_SOCK': 'agent',
                                     'SSH_ASKPASS': 'program', 'PYTHONPATH': '/inject', 'HTTPS_PROXY': 'proxy'}):
            result = windows.clean_environment()
        self.assertEqual(set(result), {'SystemRoot', 'WINDIR', 'PROGRAMDATA', 'PATH', 'SSH_ASKPASS_REQUIRE'})
        self.assertNotIn('secret', str(result))

    def test_material_checked_each_time_before_process(self):
        with patch.object(windows, 'verify_material', side_effect=ValueError('unprovisioned')), \
                patch.object(windows, 'bounded_process') as execute:
            with self.assertRaises(ValueError):
                windows.StagingSSH(self.destination)('prepare', b'')
            execute.assert_not_called()

    @unittest.skipUnless(os.name == 'nt', 'Windows file metadata validation')
    def test_regular_file_hardlink_exception_is_executable_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            executable = root / 'ssh.exe'
            executable.write_bytes(b'synthetic executable')
            alias = root / 'servicing-link.exe'
            os.link(executable, alias)
            with patch.object(windows, 'SSH', executable):
                self.assertEqual(executable.stat().st_nlink, 2)
                with self.assertRaises(ValueError):
                    windows.regular_file(executable, 1024)
                windows.regular_file(executable, 1024, allow_system_hardlinks=True)
                with self.assertRaises(ValueError):
                    windows.regular_file(alias, 1024, allow_system_hardlinks=True)
                for name in ('id_ed25519', 'known_hosts'):
                    credential = root / name
                    credential.write_bytes(b'synthetic fixture only')
                    windows.regular_file(credential, 1024)
                    os.link(credential, root / (name + '.alias'))
                    with self.subTest(name=name), self.assertRaises(ValueError):
                        windows.regular_file(credential, 1024)
                    with self.subTest(name=name, override=True), self.assertRaises(ValueError):
                        windows.regular_file(credential, 1024, allow_system_hardlinks=True)

    @unittest.skipUnless(os.name == 'nt', 'Windows file metadata validation')
    def test_executable_exception_keeps_path_reparse_size_and_type_checks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            executable = root / 'ssh.exe'
            executable.write_bytes(b'synthetic executable')
            original_lstat = Path.lstat
            with patch.object(windows, 'SSH', executable):
                with self.assertRaises(ValueError):
                    windows.regular_file(executable, 1, allow_system_hardlinks=True)
                with patch.object(Path, 'resolve', return_value=root / 'different.exe'), self.assertRaises(ValueError):
                    windows.regular_file(executable, 1024, allow_system_hardlinks=True)
                for marked in (executable, root):
                    def metadata(path, *, follow_symlinks=False):
                        if path == marked:
                            return SimpleNamespace(st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
                        return original_lstat(path)

                    with self.subTest(reparse=marked), patch.object(Path, 'lstat', metadata), self.assertRaises(ValueError):
                        windows.regular_file(executable, 1024, allow_system_hardlinks=True)
                executable.write_bytes(b'')
                with self.assertRaises(ValueError):
                    windows.regular_file(executable, 1024, allow_system_hardlinks=True)
            with patch.object(windows, 'SSH', root), self.assertRaises(ValueError):
                windows.regular_file(root, 1024, allow_system_hardlinks=True)

    @unittest.skipUnless(os.name == 'nt', 'Windows material validation')
    def test_material_pins_and_host_mismatch_without_reading_a_private_key(self):
        binary = b'synthetic executable bytes'
        pin = b'192.0.2.10 ssh-ed25519 AAAA\n'
        destination = windows.Destination('192.0.2.10', 22, hashlib.sha256(binary).hexdigest(), hashlib.sha256(pin).hexdigest())
        reads = []

        def read(path):
            reads.append(path)
            self.assertNotEqual(path, windows.KEY)
            return binary if path == windows.SSH else pin

        with patch.object(windows, 'regular_file') as check, patch.object(Path, 'read_bytes', read):
            windows.verify_material(destination)
            self.assertEqual(check.call_args_list, [
                call(windows.SSH, 16 * 1024 * 1024, allow_system_hardlinks=True),
                call(windows.KEY, 16384), call(windows.KNOWN_HOSTS, 4096)])
            for changed in [windows.Destination('192.0.2.11', 22, destination.ssh_sha256, destination.known_hosts_sha256),
                            windows.Destination('192.0.2.10', 22, 'a' * 64, destination.known_hosts_sha256),
                            windows.Destination('192.0.2.10', 22, destination.ssh_sha256, 'b' * 64)]:
                with self.assertRaises(ValueError):
                    windows.verify_material(changed)
        self.assertEqual(set(reads), {windows.SSH, windows.KNOWN_HOSTS})

    @unittest.skipUnless(os.name == 'nt', 'Installed Windows SSH option parsing')
    def test_installed_ssh_parses_fixed_options_without_connecting(self):
        argv = windows.ssh_command(self.destination, 'prepare')
        argv.insert(1, '-G')  # Configuration dump only; no connection/authentication.
        result = subprocess.run(argv, capture_output=True, timeout=5, shell=False,
                                env=windows.clean_environment(), creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(result.returncode, 0)
        options = dict(line.split(' ', 1) for line in result.stdout.decode().splitlines() if ' ' in line)
        self.assertEqual(options['user'], windows.STAGING_USER)
        self.assertEqual(options['hostname'], '192.0.2.10')
        self.assertEqual(options['batchmode'], 'yes')
        self.assertEqual(options['stricthostkeychecking'], 'true')
        self.assertEqual(options['permitlocalcommand'], 'no')
        self.assertEqual(options['clearallforwardings'], 'yes')
        self.assertEqual(options['identityagent'], 'none')

    def child(self, program, body=b'', seconds=3):
        return windows.bounded_process([sys.executable, '-I', '-B', '-c', program], body, seconds, env=os.environ.copy())

    def test_real_local_child_round_trip(self):
        self.assertEqual(self.child('import sys; data=sys.stdin.buffer.read(); sys.stdout.buffer.write(data)', b'synthetic'), b'synthetic')

    def test_real_local_child_nonzero_output_overflow_and_stderr_overflow(self):
        for program in ['raise SystemExit(3)',
                        'import sys; sys.stdout.buffer.write(b"x" * 70000)',
                        'import sys; sys.stderr.buffer.write(b"x" * 12000)']:
            with self.subTest(program=program), self.assertRaises(ValueError):
                self.child(program)

    def test_real_local_child_deadline_and_blocked_stdin_cleanup(self):
        for body in (b'', b'x' * contract.MAX_PAYLOAD):
            before = time.monotonic()
            with self.assertRaises(ValueError):
                self.child('import time; time.sleep(20)', body, seconds=.3)
            self.assertLess(time.monotonic() - before, 4)

    def test_timed_out_local_process_is_reaped_and_pipes_closed(self):
        captured = []
        original = subprocess.Popen

        def start(*args, **kwargs):
            process = original(*args, **kwargs)
            captured.append(process)
            return process

        with patch.object(windows.subprocess, 'Popen', side_effect=start), self.assertRaises(ValueError):
            self.child('import time; time.sleep(20)', seconds=.3)
        self.assertEqual(len(captured), 1)
        self.assertIsNotNone(captured[0].poll())
        self.assertTrue(all(pipe.closed for pipe in (captured[0].stdin, captured[0].stdout, captured[0].stderr)))

    @unittest.skipUnless(os.name == 'nt', 'Windows Job Object enforcement')
    def test_real_job_refuses_helper_process(self):
        # CreateProcess can succeed while the job terminates the new process.
        # Test whether its code executes, not merely whether Popen raises.
        program = ('import subprocess,sys\n'
                   'try:\n p=subprocess.Popen([sys.executable,"-I","-B","-c","print(12345)"],stdout=subprocess.PIPE); out=p.communicate(timeout=2)[0]\n'
                   'except OSError:\n print("blocked")\n'
                   'else:\n print("blocked" if p.returncode != 0 and not out else "unexpected")\n')
        self.assertEqual(self.child(program).strip(), b'blocked')


if __name__ == '__main__':
    unittest.main()
