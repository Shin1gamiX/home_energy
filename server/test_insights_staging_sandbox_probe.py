"""Mock-only OS-probe tests: never execute Codex, PowerShell or network calls."""
import copy
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import insights_staging_launcher as launcher
import insights_staging_sandbox_probe as probe


def candidate():
    return launcher.build_config({'features': {'shell_tool': False, 'unified_exec': False},
                                  'apps': {'_default': {'enabled': False}}}, 'Synthetic instructions only.')


def reply(**changes):
    return {'allowed_read': True, 'denied_read': 'access_denied',
            'root_read': 'access_denied',
            'write': 'access_denied', 'network': 'not_tested', **changes}


class CommandTests(unittest.TestCase):
    def test_same_profile_and_no_model_shell_enablement(self):
        config = candidate()
        original = copy.deepcopy(config)
        argv = probe.command(config)
        self.assertEqual(config, original)
        self.assertEqual(argv[0], str(launcher.CODEX))
        self.assertIn('--no-daemon', argv)
        self.assertIn('--include-managed-config', argv)
        self.assertEqual(argv[argv.index('--permission-profile') + 1], launcher.PROFILE)
        self.assertNotIn('--sandbox', argv)
        self.assertNotIn('turn/start', argv)
        self.assertFalse(config['features']['shell_tool'])
        self.assertFalse(config['features']['unified_exec'])
        self.assertEqual(argv[argv.index('--') + 1:argv.index('-Command')],
                         [str(probe.POWERSHELL), '-NoLogo', '-NoProfile', '-NonInteractive'])

    def test_enabled_shell_network_or_legacy_sandbox_is_rejected(self):
        for changed in ('shell_tool', 'unified_exec', 'network', 'legacy', 'profile'):
            config = candidate()
            if changed in ('shell_tool', 'unified_exec'):
                config['features'][changed] = True
            elif changed == 'network':
                config['permissions'][launcher.PROFILE]['network']['enabled'] = True
            elif changed == 'legacy':
                config['sandbox_mode'] = 'read-only'
            else:
                config['default_permissions'] = 'other'
            with self.subTest(changed=changed), self.assertRaises(launcher.GuardFailure):
                probe.command(config)

    def test_script_targets_only_fake_canaries_and_exclusive_create(self):
        script = probe.probe_script()
        for path in (launcher.WORKSPACE / 'allowed.txt', launcher.WORKSPACE / 'denied.txt',
                     probe.TARGET, probe.ROOT_CANARY):
            self.assertIn(str(path), script)
        self.assertIn('[IO.FileMode]::CreateNew', script)
        self.assertNotIn('WriteAllText', script)
        self.assertNotIn('Delete', script)
        self.assertNotIn(str(launcher.MATERIAL), script)
        self.assertNotIn(str(launcher.SOURCE), script)
        self.assertNotIn('id_ed25519', script)
        self.assertNotIn('Remove-Item', script)
        self.assertIn('[System.UnauthorizedAccessException]', script)
        self.assertIn('NativeErrorCode -eq 10013', script)
        self.assertIn("$result.network = 'timeout'", script)

    def test_port_is_bounded_integer_not_arbitrary_script(self):
        for port in (True, 0, -1, 65536, '123; whoami'):
            with self.subTest(port=port), self.assertRaises(launcher.GuardFailure):
                probe.probe_script(port)
        self.assertIn('$port = 12345', probe.probe_script(12345))
        self.assertIn('$port = 0', probe.probe_script())


class ResultTests(unittest.TestCase):
    def test_exact_result_accepted(self):
        self.assertEqual(probe.parse_result(json.dumps(reply()).encode()), reply())

    def test_malformed_or_ambiguous_output_never_certifies_denial(self):
        payloads = [b'', b'not-json', b'[]', b'{"allowed_read":true,"allowed_read":false}',
                    b'x' * (probe.MAX_STDOUT + 1), b'{}\n{}',
                    json.dumps({**reply(), 'extra': 'SECRET'}).encode(),
                    json.dumps(reply(allowed_read=1)).encode(),
                    json.dumps(reply(denied_read='not_found')).encode(),
                    json.dumps(reply(write='process_failed')).encode(),
                    json.dumps(reply(network=['access_denied'])).encode()]
        for payload in payloads:
            with self.subTest(payload=payload[:50]), self.assertRaises((ValueError, TypeError)):
                probe.parse_result(payload)

    def test_unknown_and_nonfinite_result_rejected(self):
        with self.assertRaises(ValueError):
            probe.parse_result(b'{"allowed_read":NaN,"denied_read":"access_denied",'
                               b'"write":"access_denied","network":"not_tested"}')


class WorkflowTests(unittest.TestCase):
    def run_mocked(self, result=None, *, exists=None, loopback=False, controls=None):
        if result is None:
            result = reply()
        if exists is None:
            exists = [False, False]
        target, listener = Mock(), Mock()
        target.exists.side_effect = exists
        listener.getsockname.return_value = ('127.0.0.1', 23456)
        canaries = ['HOME_ENERGY_STAGING_ALLOWED_CANARY', 'HOME_ENERGY_STAGING_DENIED_CANARY',
                    'HOME_ENERGY_STAGING_ROOT_DENIED_CANARY']
        with patch.object(probe, 'TARGET', target), patch.object(launcher, 'plain_file'), \
                patch.object(Path, 'read_text', side_effect=canaries), \
                patch.object(probe, 'command', return_value=['fixed-command']) as command, \
                patch.object(probe, 'bounded_probe', return_value=json.dumps(result).encode()) as process, \
                patch.object(probe.socket, 'socket', return_value=listener), \
                patch.object(probe, 'positive_loopback', side_effect=controls or [True, True]):
            outcome = probe.run_probe(candidate(), with_loopback=loopback)
        return outcome, target, listener, command, process

    def test_positive_and_two_denials_certify_only_separate_filesystem_probe(self):
        outcome, target, listener, command, _ = self.run_mocked()
        self.assertTrue(outcome['filesystem_probe_passed'])
        self.assertEqual(outcome['evidence_boundary'], 'separate_os_profile_probe')
        self.assertFalse(outcome['model_called'])
        self.assertFalse(outcome['ssh_called'])
        self.assertEqual(outcome['report_calls'], 0)
        self.assertFalse(outcome['model_shell_tools_enabled'])
        self.assertEqual(outcome['network']['conclusion'], 'not_tested')
        self.assertEqual(target.exists.call_count, 2)
        listener.bind.assert_not_called()
        self.assertIsNone(command.call_args.args[1])

    def test_missing_positive_or_non_denial_never_passes(self):
        for result in (reply(allowed_read=False), reply(denied_read='inconclusive'),
                       reply(root_read='inconclusive'), reply(root_read='unexpectedly_allowed'),
                       reply(write='inconclusive'), reply(denied_read='unexpectedly_allowed'),
                       reply(write='unexpectedly_allowed')):
            with self.subTest(result=result):
                outcome, *_ = self.run_mocked(result)
                self.assertFalse(outcome['filesystem_probe_passed'])

    def test_unexpected_created_file_is_preserved_and_fails(self):
        outcome, target, *_ = self.run_mocked(exists=[False, True])
        self.assertFalse(outcome['filesystem_probe_passed'])
        self.assertFalse(outcome['filesystem']['unexpected_write_absent'])
        target.unlink.assert_not_called()

    def test_existing_write_target_stops_before_any_probe(self):
        target = Mock()
        target.exists.return_value = True
        with patch.object(probe, 'TARGET', target), patch.object(probe, 'bounded_probe') as process:
            with self.assertRaisesRegex(launcher.GuardFailure, 'unexpected_synthetic_file_present'):
                probe.run_probe(candidate())
        process.assert_not_called()
        target.unlink.assert_not_called()

    def test_changed_canary_stops_before_probe(self):
        with patch.object(probe, 'TARGET', Mock(exists=Mock(return_value=False))), \
                patch.object(launcher, 'plain_file'), patch.object(Path, 'read_text', return_value='WRONG'), \
                patch.object(probe, 'bounded_probe') as process:
            with self.assertRaisesRegex(launcher.GuardFailure, 'canary_changed'):
                probe.run_probe(candidate())
        process.assert_not_called()

    def test_network_timeout_is_separate_inconclusive_not_file_failure(self):
        outcome, _, listener, command, _ = self.run_mocked(reply(network='timeout'), loopback=True)
        self.assertTrue(outcome['filesystem_probe_passed'])
        self.assertEqual(outcome['network']['conclusion'], 'inconclusive')
        self.assertTrue(outcome['network']['host_before'])
        self.assertTrue(outcome['network']['host_after'])
        self.assertEqual(command.call_args.args[1], 23456)
        listener.close.assert_called_once()

    def test_network_denial_requires_both_positive_controls(self):
        for controls in ([True, False], [False]):
            outcome, *_ = self.run_mocked(reply(network='access_denied'), loopback=True, controls=controls)
            self.assertTrue(outcome['filesystem_probe_passed'])
            self.assertEqual(outcome['network']['conclusion'], 'inconclusive')
        outcome, *_ = self.run_mocked(reply(network='access_denied'), loopback=True)
        self.assertEqual(outcome['network']['conclusion'], 'access_denied')

    def test_unexpected_network_access_not_relabelled_as_denial(self):
        outcome, *_ = self.run_mocked(reply(network='unexpectedly_allowed'), loopback=True)
        self.assertEqual(outcome['network']['conclusion'], 'unexpectedly_allowed')


class BoundedProcessTests(unittest.TestCase):
    @staticmethod
    def process(stdout=b'{}', stderr=b''):
        process = Mock()
        process.stdout, process.stderr = io.BytesIO(stdout), io.BytesIO(stderr)
        process.poll.return_value = 0
        process.wait.return_value = 0
        return process

    def test_child_is_suspended_owned_and_output_is_bounded(self):
        process, tree = self.process(b'{}'), Mock()
        with patch.object(launcher, 'OwnedRuntimeTree', return_value=tree), \
                patch.object(probe.subprocess, 'Popen', return_value=process) as popen:
            self.assertEqual(probe.bounded_probe(['fixed-command']), b'{}')
        self.assertEqual(popen.call_args.kwargs['creationflags'],
                         probe.subprocess.CREATE_NO_WINDOW | launcher.CREATE_SUSPENDED)
        self.assertEqual(popen.call_args.kwargs['bufsize'], 0)
        self.assertEqual(popen.call_args.kwargs['stdin'], probe.subprocess.DEVNULL)
        tree.assign_and_resume.assert_called_once_with(process)
        tree.close.assert_called_once()
        self.assertTrue(process.stdout.closed)
        self.assertTrue(process.stderr.closed)

    def test_bounded_stderr_is_discarded_never_returned(self):
        process = self.process(b'{}', b'SECRET')
        with patch.object(launcher, 'OwnedRuntimeTree'), \
                patch.object(probe.subprocess, 'Popen', return_value=process):
            self.assertEqual(probe.bounded_probe(['fixed-command']), b'{}')

    def test_stdout_or_stderr_overflow_kills_tree_and_fails(self):
        for stdout, stderr in ((b'x' * (probe.MAX_STDOUT + 1), b''),
                               (b'{}', b'x' * (probe.MAX_STDERR + 1))):
            process, tree = self.process(stdout, stderr), Mock()
            with patch.object(launcher, 'OwnedRuntimeTree', return_value=tree), \
                    patch.object(probe.subprocess, 'Popen', return_value=process):
                with self.assertRaisesRegex(launcher.GuardFailure, 'probe_output_failed'):
                    probe.bounded_probe(['fixed-command'])
            self.assertGreaterEqual(tree.close.call_count, 2)
            self.assertTrue(process.stdout.closed)

    def test_nonzero_exit_is_not_access_denial(self):
        process = self.process(json.dumps(reply()).encode())
        process.wait.return_value = 1
        with patch.object(launcher, 'OwnedRuntimeTree'), \
                patch.object(probe.subprocess, 'Popen', return_value=process):
            with self.assertRaisesRegex(launcher.GuardFailure, 'probe_process_failed'):
                probe.bounded_probe(['fixed-command'])

    def test_assignment_failure_cleans_suspended_process_and_job(self):
        process, tree = self.process(), Mock()
        process.poll.return_value = None
        tree.assign_and_resume.side_effect = launcher.GuardFailure('synthetic-assignment-failure')
        with patch.object(launcher, 'OwnedRuntimeTree', return_value=tree), \
                patch.object(probe.subprocess, 'Popen', return_value=process):
            with self.assertRaisesRegex(launcher.GuardFailure, 'synthetic-assignment-failure'):
                probe.bounded_probe(['fixed-command'])
        tree.close.assert_called_once()
        process.kill.assert_called_once()
        self.assertTrue(process.stderr.closed)

    def test_deadline_expiry_is_not_access_denial(self):
        process, tree = self.process(), Mock()
        with patch.object(launcher, 'OwnedRuntimeTree', return_value=tree), \
                patch.object(probe.subprocess, 'Popen', return_value=process), \
                patch.object(probe.time, 'monotonic', side_effect=[0, 100]):
            with self.assertRaisesRegex(launcher.GuardFailure, 'probe_timeout'):
                probe.bounded_probe(['fixed-command'])
        tree.close.assert_called_once()


class EntrypointTests(unittest.TestCase):
    def test_no_isolated_interpreter_refuses_before_loading_or_running(self):
        with patch.object(probe.sys, 'argv', ['probe']), \
                patch.object(probe.sys, 'flags', SimpleNamespace(isolated=0)), \
                patch.object(launcher, 'load_inputs') as load, patch.object(probe, 'run_probe') as run, \
                patch('builtins.print') as printed:
            self.assertEqual(probe.main(), 1)
        load.assert_not_called()
        run.assert_not_called()
        self.assertIn('no denial was certified', json.loads(printed.call_args.args[0])['error'])

    def test_unexpected_exception_text_never_reaches_result(self):
        with patch.object(probe.sys, 'argv', ['probe']), \
                patch.object(probe.sys, 'flags', SimpleNamespace(isolated=1)), \
                patch.object(probe.sys, 'dont_write_bytecode', True), \
                patch.object(launcher, 'load_inputs', side_effect=OSError('SECRET')), \
                patch('builtins.print') as printed:
            self.assertEqual(probe.main(), 1)
        self.assertNotIn('SECRET', printed.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
