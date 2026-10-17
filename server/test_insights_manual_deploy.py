"""Offline scope/rollback tests. Never invoke sudo, SSH, HTTP or production writes."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

DEPLOY_DIR = Path(__file__).resolve().parents[1] / 'deploy'
if not (DEPLOY_DIR / 'activate-report-manual.py').is_file():
    DEPLOY_DIR = Path(__file__).resolve().parent  # Flat, staged release bundle.
SPEC = importlib.util.spec_from_file_location('manual_deploy', DEPLOY_DIR / 'activate-report-manual.py')
deploy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(deploy)


class DeploymentTests(unittest.TestCase):
    def test_origin_requests_cover_both_hosts_and_exact_content(self):
        bodies = {'index.html': b'<html>overview</html>\n', 'insights.js': b'// renderer\n'}
        feed = b'{"reports": []}\n'
        expected_routes = {
            '/': bodies['index.html'],
            '/insights.js?v=20261017-manual-reviews': bodies['insights.js'],
            '/reports/index.json': feed,
        }
        responses = [Mock(returncode=0, stdout=body + b'\n200')
                     for _ in deploy.HOSTS for body in expected_routes.values()]
        with patch.object(deploy.subprocess, 'run', side_effect=responses) as run, patch('builtins.print'):
            deploy.verify_http(bodies, deploy.digest(feed))
        self.assertEqual(run.call_count, 6)
        expected_urls = ['https://' + host + route for host in deploy.HOSTS for route in expected_routes]
        self.assertEqual([call.args[0][-1] for call in run.call_args_list], expected_urls)
        for call in run.call_args_list:
            command = call.args[0]
            host = command[-1].split('/')[2]
            self.assertEqual(command[:2], ['/usr/bin/curl', '--disable'])
            self.assertEqual(command[command.index('--resolve') + 1], host + ':443:127.0.0.1')
            self.assertEqual(command[command.index('--noproxy') + 1], '*')
            self.assertEqual(command[command.index('--proto') + 1], '=https')
            self.assertEqual(command[command.index('--max-time') + 1], '15')
            self.assertEqual(command[command.index('--max-filesize') + 1], '2000000')
            self.assertEqual(command[command.index('--write-out') + 1], '\n%{http_code}')
            self.assertEqual(call.kwargs, {'capture_output': True, 'timeout': 20, 'check': False})
            for forbidden in ('--insecure', '-k', '--location', '-L', '--config', '-K'):
                self.assertNotIn(forbidden, command)

    def test_http_errors_and_redirects_fail_without_retry(self):
        for status in (b'403', b'301', b'302', b'500'):
            with self.subTest(status=status), \
                    patch.object(deploy.subprocess, 'run', return_value=Mock(returncode=0, stdout=b'error\n' + status)) as run:
                with self.assertRaisesRegex(RuntimeError, 'Local HTTPS returned ' + status.decode() + ': home.shin1gamix.com/'):
                    deploy.verify_http({'index.html': b'index', 'insights.js': b'js'}, deploy.digest(b'feed'))
                run.assert_called_once()

    def test_wrong_content_is_rejected_even_with_status_200(self):
        with patch.object(deploy.subprocess, 'run', return_value=Mock(returncode=0, stdout=b'wrong content\n200')):
            with self.assertRaisesRegex(RuntimeError, 'content mismatch: home.shin1gamix.gr/reports/index.json'):
                deploy.verify_origin_asset('home.shin1gamix.gr', '/reports/index.json', deploy.digest(b'expected'))

    def test_transport_and_certificate_failures_are_rejected(self):
        for exit_code in (7, 28, 60, 63):  # Connection, timeout, certificate, size.
            with self.subTest(exit_code=exit_code), \
                    patch.object(deploy.subprocess, 'run', return_value=Mock(returncode=exit_code, stdout=b'expected\n200')):
                with self.assertRaisesRegex(RuntimeError, r'transport failed: home.shin1gamix.com/ \(curl exit ' + str(exit_code) + r'\)'):
                    deploy.verify_origin_asset('home.shin1gamix.com', '/', deploy.digest(b'expected'))

    def test_missing_curl_and_process_timeout_have_route_context(self):
        for error in (FileNotFoundError(), deploy.subprocess.TimeoutExpired('curl', 20)):
            with self.subTest(error=type(error).__name__), patch.object(deploy.subprocess, 'run', side_effect=error):
                with self.assertRaisesRegex(RuntimeError, 'could not complete: home.shin1gamix.com/'):
                    deploy.verify_origin_asset('home.shin1gamix.com', '/', deploy.digest(b'expected'))

    def test_malformed_or_oversized_output_is_rejected(self):
        for output in (b'200', b'expected\n', b'expected\nnot a status', b'x' * 2_000_001 + b'\n200'):
            with self.subTest(size=len(output)), \
                    patch.object(deploy.subprocess, 'run', return_value=Mock(returncode=0, stdout=output)):
                with self.assertRaises(RuntimeError):
                    deploy.verify_origin_asset('home.shin1gamix.com', '/', deploy.digest(output))

    def test_policy_changes_only_entrypoint(self):
        original = (DEPLOY_DIR / 'reports-production-sshd.conf.example').read_bytes()
        changed = deploy.ssh_config(original)
        self.assertEqual(changed.replace(deploy.NEW_ENTRY.encode(), deploy.OLD_ENTRY.encode()), original)
        self.assertIn(b'DisableForwarding yes', changed)
        self.assertIn(b'PermitTTY no', changed)
        for value in (changed, b'no target', original + original):
            with self.assertRaises(RuntimeError):
                deploy.ssh_config(value)

    def test_no_report_storage_is_a_write_target(self):
        self.assertEqual(set(deploy.TARGETS), {'index.html', 'insights.js', 'ssh.conf'})
        self.assertTrue(all(deploy.STATE not in path.parents for path in deploy.TARGETS.values()))
        self.assertEqual(set(deploy.NEW_MODULES), {'insights_manual.py', 'insights_manual_entry.py'})

    def test_atomic_replacement_keeps_backup_and_unrelated_file(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            destination = root / 'target'
            destination.write_bytes(b'old')
            backup = root / 'backup'
            backup.write_bytes(b'old')
            with patch.object(deploy.os, 'fchown', create=True), patch.object(deploy.os, 'fchmod', create=True):
                deploy.replace_file(destination, b'new', 0, 0, 0o644)
            self.assertEqual(destination.read_bytes(), b'new')
            self.assertEqual(backup.read_bytes(), b'old')

    def test_effective_restrictions_verified(self):
        policy = ('forcecommand /usr/bin/timeout --signal=TERM --kill-after=5s 45s /usr/bin/python3 -I -B '
                  + deploy.NEW_ENTRY + '\ndisableforwarding yes\npermittty no\npermituserrc no\n'
                  'passwordauthentication no\nkbdinteractiveauthentication no\nauthenticationmethods publickey\n')
        with patch.object(deploy.subprocess, 'run', return_value=Mock(stdout=policy)):
            deploy.verify_policy()
        with patch.object(deploy.subprocess, 'run', return_value=Mock(stdout=policy.replace('permittty no', 'permittty yes'))):
            with self.assertRaises(RuntimeError):
                deploy.verify_policy()

    def test_preservation_checks_bytes_and_metadata(self):
        before = {'fixed-path': {'sha256': 'same', 'mtime_ns': 1}}
        with patch.object(deploy, 'fingerprint', return_value={'sha256': 'same', 'mtime_ns': 1}):
            deploy.verify_preserved(before)
        with patch.object(deploy, 'fingerprint', return_value={'sha256': 'same', 'mtime_ns': 2}):
            with self.assertRaises(RuntimeError):
                deploy.verify_preserved(before)


if __name__ == '__main__':
    unittest.main()
