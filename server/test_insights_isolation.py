"""Preparation-only configuration contracts; does not install or start services."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('isolation_audit', ROOT / 'deploy/check-report-isolation.py')
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def directives(name):
    text = (ROOT / 'deploy' / name).read_text(encoding='utf-8')
    return dict(line.split('=', 1) for line in text.splitlines() if '=' in line and not line.startswith('#'))


class IsolationPreparationTests(unittest.TestCase):
    def test_effective_environment_never_drops_earlier_unsafe_rules(self):
        settings = audit.parse_settings('acceptenv BASH_ENV\nacceptenv LANG\nacceptenv LC_*\n')
        self.assertEqual(settings['acceptenv'], 'BASH_ENV LANG LC_*')
        self.assertFalse(set(settings['acceptenv'].split()) <= {'LANG', 'LC_*'})

    def test_multiple_hostkeys_supported_without_losing_scalar_duplicates(self):
        settings = audit.parse_settings('hostkey /key1\nhostkey /key2\npermittty yes\npermittty no\n')
        self.assertEqual(settings['hostkey'], '/key1 /key2')
        self.assertNotEqual(settings['permittty'], 'no')

    def test_effective_sshd_query_is_read_only_and_scoped(self):
        with patch.object(audit.subprocess, 'check_output', return_value='permittty no\n') as query:
            self.assertEqual(audit.effective_settings('192.0.2.10', 'broker.example', '192.0.2.20', 22), {'permittty': 'no'})
        self.assertEqual(query.call_args.args[0], ['/usr/sbin/sshd', '-T', '-C',
                         'user=homeenergy-reporter,host=broker.example,addr=192.0.2.10,laddr=192.0.2.20,lport=22'])

    def test_sudo_failure_is_not_mistaken_for_policy_denial(self):
        good = subprocess.CompletedProcess([], 0)
        denial = subprocess.CompletedProcess([], 1, '', 'User homeenergy-reporter is not allowed to run sudo on host.\n')
        self.assertTrue(audit.sudo_denied(good, denial))
        self.assertFalse(audit.sudo_denied(subprocess.CompletedProcess([], 1), denial))
        for message in ('', 'sudo: policy plugin failed', denial.stderr + 'Unexpected error'):
            self.assertFalse(audit.sudo_denied(good, subprocess.CompletedProcess([], 1, '', message)))

    def test_parent_write_access_and_walk_errors_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            self.assertFalse(audit.protected_tree_readonly(root, lambda flag, path: path == root.parent))
            self.assertTrue(audit.protected_tree_readonly(root, lambda flag, path: False))
            with patch.object(audit.os, 'walk', side_effect=PermissionError):
                self.assertFalse(audit.protected_tree_readonly(root, lambda flag, path: False))

    @unittest.skipUnless(sys.platform == 'linux', 'Linux symlink fixture')
    def test_out_of_scope_links_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / 'protected'
            root.mkdir()
            (root / 'external').symlink_to(root.parent, target_is_directory=True)
            self.assertFalse(audit.protected_tree_readonly(root, lambda flag, path: False))

    def test_socket_has_uid_boundary_and_aggregate_limits(self):
        unit = directives('homeenergy-report-broker.socket.example')
        expected = {'SocketUser': 'root', 'SocketGroup': 'homeenergy-report-runner',
                    'SocketMode': '0660', 'Accept': 'yes', 'MaxConnections': '4',
                    'TriggerLimitIntervalSec': '60', 'TriggerLimitBurst': '24',
                    'ListenStream': '/run/homeenergy-report-broker.sock'}
        self.assertEqual({key: unit[key] for key in expected}, expected)

    def test_service_separates_credentials_and_limits_scope(self):
        unit = directives('homeenergy-report-broker@.service.example')
        expected = {'User': 'homeenergy-report-broker', 'StandardInput': 'socket',
                    'StandardOutput': 'null', 'StandardError': 'journal', 'ProtectSystem': 'strict',
                    'NoNewPrivileges': 'yes', 'KillMode': 'control-group', 'RuntimeMaxSec': '65',
                    'TasksMax': '8', 'MemoryMax': '96M', 'ProtectHome': 'yes', 'IPAddressDeny': 'any',
                    'LoadCredential': 'report-key:/etc/homeenergy-report-credentials/report-key'}
        self.assertEqual({key: unit[key] for key in expected}, expected)
        self.assertNotIn('ReadWritePaths', unit)
        self.assertTrue(unit['ExecStart'].startswith('/usr/bin/python3 -I -B '))

    def test_documentation_address_consistent_and_not_a_real_deployment(self):
        config = json.loads((ROOT / 'deploy/report-broker-config.json.example').read_text())
        unit = directives('homeenergy-report-broker@.service.example')
        self.assertEqual(config, {'host': '192.0.2.10', 'port': 22})
        self.assertEqual(unit['IPAddressAllow'], config['host'])

    def test_server_match_forces_fixed_dispatcher_and_no_session_features(self):
        content = (ROOT / 'deploy/reports-sshd.conf.example').read_text()
        for rule in ('ForceCommand ' + audit.FORCED_COMMAND, 'DisableForwarding yes',
                     'PermitTTY no', 'PermitTunnel no', 'PermitUserRC no',
                     'AuthenticationMethods publickey', 'PasswordAuthentication no'):
            self.assertIn(rule, content)

    @unittest.skipUnless(sys.platform == 'linux', 'Linux account database required')
    def test_missing_account_fails_closed_without_subprocess_or_mutation(self):
        with patch('pwd.getpwnam', side_effect=KeyError), patch.object(audit.subprocess, 'run') as command:
            results = audit.checks('192.0.2.10', 'broker.example', '192.0.2.20', 22)
        self.assertEqual(results, [{'check': 'dedicated reporting account exists', 'passed': False}])
        command.assert_not_called()


if __name__ == '__main__':
    unittest.main()
