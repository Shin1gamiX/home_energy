"""Offline ACL policy tests: no credentials, shell invocation or ACL mutation."""

import copy
import json
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import insights_staging_acl as acl


def snapshot():
    result = {}
    for name in ('root', 'private_key', 'host_pin', 'binding'):
        protected = name in ('root', 'private_key')
        result[name] = {
            'plain': True, 'owner_is_current': True, 'dacl_present': True,
            'protected': protected, 'raw_ace_count': 3, 'raw_aces_supported': True,
            'rules': [
                {'principal': principal, 'access': 'Allow', 'rights': acl.FULL_CONTROL,
                 'inherited': not protected, 'inheritance': 3 if name == 'root' else 0,
                 'propagation': 0}
                for principal in ('current', 'system', 'administrators')
            ],
        }
    return result


class PolicyTests(unittest.TestCase):
    def test_exact_private_policy_accepts_inherited_host_pin_and_binding(self):
        original = snapshot()
        before = copy.deepcopy(original)
        self.assertEqual(acl.validate_snapshot(original), {'acl_verified': True})
        self.assertEqual(original, before)

    def test_protected_host_pin_and_binding_also_accept_exact_explicit_rules(self):
        value = snapshot()
        for name in ('host_pin', 'binding'):
            value[name]['protected'] = True
            for rule in value[name]['rules']:
                rule['inherited'] = False
        self.assertEqual(acl.validate_snapshot(value), {'acl_verified': True})

    def test_missing_or_additional_paths_rejected(self):
        for extra in (False, True):
            value = snapshot()
            if extra:
                value['other'] = value['root']
            else:
                del value['binding']
            with self.subTest(extra=extra), self.assertRaisesRegex(acl.AclFailure, 'acl_metadata_shape'):
                acl.validate_snapshot(value)

    def test_nonplain_or_noncurrent_owner_or_null_dacl_rejected_everywhere(self):
        for name in acl.ENTRIES:
            for flag in ('plain', 'owner_is_current', 'dacl_present'):
                value = snapshot()
                value[name][flag] = False
                with self.subTest(name=name, flag=flag), self.assertRaises(acl.AclFailure):
                    acl.validate_snapshot(value)

    def test_unprotected_root_and_private_key_rejected(self):
        for name in ('root', 'private_key'):
            value = snapshot()
            value[name]['protected'] = False
            with self.subTest(name=name), self.assertRaisesRegex(acl.AclFailure, 'acl_unprotected_private_material'):
                acl.validate_snapshot(value)

    def test_raw_count_cannot_hide_omitted_aces_or_coerce_types(self):
        for name in acl.ENTRIES:
            for count in (0, 4, True, 3.0, '3'):
                value = snapshot()
                value[name]['raw_ace_count'] = count
                with self.subTest(name=name, count=count), self.assertRaisesRegex(acl.AclFailure, 'acl_raw_ace_count'):
                    acl.validate_snapshot(value)

    def test_raw_unsupported_aces_rejected_despite_valid_projected_rules(self):
        for name in acl.ENTRIES:
            value = snapshot()
            value[name]['raw_aces_supported'] = False
            with self.subTest(name=name), self.assertRaisesRegex(acl.AclFailure, 'acl_raw_ace_unsupported'):
                acl.validate_snapshot(value)

    def test_raw_ace_supported_flag_requires_real_boolean(self):
        for flag in (1, 'true', None):
            value = snapshot()
            value['root']['raw_aces_supported'] = flag
            with self.subTest(flag=flag), self.assertRaisesRegex(acl.AclFailure, 'acl_metadata_shape'):
                acl.validate_snapshot(value)

    def test_exactly_three_unique_principals_required(self):
        for mutation in ('few', 'extra', 'duplicate', 'other'):
            value = snapshot()
            rules = value['binding']['rules']
            if mutation == 'few':
                rules.pop()
            elif mutation == 'extra':
                rules.append(copy.deepcopy(rules[0]))
            else:
                rules[0]['principal'] = 'system' if mutation == 'duplicate' else 'other'
            with self.subTest(mutation=mutation), self.assertRaises(acl.AclFailure):
                acl.validate_snapshot(value)

    def test_deny_reduced_or_expanded_rights_rejected(self):
        for field, bad in (('access', 'Deny'), ('rights', 1), ('rights', acl.FULL_CONTROL + 1), ('rights', True)):
            value = snapshot()
            value['private_key']['rules'][0][field] = bad
            with self.subTest(field=field, bad=bad), self.assertRaises(acl.AclFailure):
                acl.validate_snapshot(value)

    def test_inherit_only_no_propagate_or_unknown_propagation_rejected(self):
        for name in acl.ENTRIES:
            for flags in (1, 2, 3, 4):
                value = snapshot()
                value[name]['rules'][0]['propagation'] = flags
                with self.subTest(name=name, flags=flags), self.assertRaisesRegex(acl.AclFailure, 'acl_propagation_rejected'):
                    acl.validate_snapshot(value)

    def test_directory_must_propagate_both_types_and_files_neither(self):
        for name in acl.ENTRIES:
            for flags in (0, 1, 2, 3, 4):
                if flags == (3 if name == 'root' else 0):
                    continue
                value = snapshot()
                value[name]['rules'][0]['inheritance'] = flags
                with self.subTest(name=name, flags=flags), self.assertRaisesRegex(acl.AclFailure, 'acl_inheritance_rejected'):
                    acl.validate_snapshot(value)

    def test_inherited_rules_in_protected_dacl_or_explicit_in_inherited_dacl_rejected(self):
        for name in acl.ENTRIES:
            value = snapshot()
            value[name]['rules'][0]['inherited'] = not value[name]['rules'][0]['inherited']
            with self.subTest(name=name), self.assertRaisesRegex(acl.AclFailure, 'acl_inherited_rule_rejected'):
                acl.validate_snapshot(value)

    def test_exact_schema_and_types(self):
        mutations = [
            lambda value: value['root'].update(extra=True),
            lambda value: value['root'].update(plain=1),
            lambda value: value['root'].update(protected=1),
            lambda value: value['root'].update(rules=tuple(value['root']['rules'])),
            lambda value: value['root']['rules'][0].update(extra=True),
            lambda value: value['root']['rules'][0].update(inherited=0),
            lambda value: value['root']['rules'][0].update(propagation=False),
            lambda value: value['root']['rules'][0].update(inheritance=True),
            lambda value: value['root']['rules'][0].update(principal=[]),
        ]
        for index, mutate in enumerate(mutations):
            value = snapshot()
            mutate(value)
            with self.subTest(index=index), self.assertRaises(acl.AclFailure):
                acl.validate_snapshot(value)


class ParsingTests(unittest.TestCase):
    def test_roundtrip_anonymous_metadata(self):
        body = json.dumps(snapshot()).encode('ascii')
        self.assertEqual(acl.validate_snapshot(acl.parse_snapshot(body)), {'acl_verified': True})

    def test_empty_oversized_nonbytes_and_invalid_encoding_rejected(self):
        for body in (b'', b'x' * (acl.MAX_METADATA_BYTES + 1), '{}', b'\xff', b'{} trailing'):
            with self.subTest(kind=type(body).__name__), self.assertRaises(acl.AclFailure):
                acl.parse_snapshot(body)

    def test_duplicate_keys_rejected_not_last_value_wins(self):
        for body in (b'{"root":{},"root":{}}', b'{"root":{"plain":false,"plain":true}}'):
            with self.assertRaisesRegex(acl.AclFailure, 'acl_metadata_duplicate_key'):
                acl.parse_snapshot(body)

    def test_raw_sids_paths_and_stdout_never_returned(self):
        value = snapshot()
        value['binding']['rules'][0]['principal'] = 'S-1-5-21-SYNTHETIC'
        with self.assertRaises(acl.AclFailure) as error:
            acl.validate_snapshot(value)
        self.assertEqual(str(error.exception), 'acl_principal_rejected')


class InvocationTests(unittest.TestCase):
    def invoke(self, result):
        with patch.object(acl.os, 'name', 'nt'), patch.object(acl, '_verify_powershell') as pin, patch.object(acl.subprocess, 'run', return_value=result) as run:
            outcome = acl.verify_permissions()
        return outcome, pin, run

    def test_fixed_command_private_environment_deadline_and_fixed_result(self):
        outcome, pin, run = self.invoke(SimpleNamespace(returncode=0, stdout=json.dumps(snapshot()).encode(), stderr=b''))
        self.assertEqual(outcome, {'acl_verified': True})
        pin.assert_called_once_with()
        args, kwargs = run.call_args
        self.assertEqual(args[0], [str(acl.POWERSHELL), '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', acl.ACL_METADATA_SCRIPT])
        self.assertEqual(kwargs['env'], acl.SAFE_ENV)
        self.assertIsNot(kwargs['env'], acl.SAFE_ENV)
        self.assertEqual(kwargs['timeout'], 10)
        self.assertEqual(kwargs['stdin'], subprocess.DEVNULL)
        self.assertFalse(kwargs['check'])
        self.assertNotIn('shell', kwargs)

    def test_nonwindows_rejected_before_subprocess(self):
        with patch.object(acl.os, 'name', 'posix'), patch.object(acl.subprocess, 'run') as run:
            with self.assertRaisesRegex(acl.AclFailure, 'acl_windows_required'):
                acl.verify_permissions()
            run.assert_not_called()

    def test_executable_failure_prevents_invocation(self):
        with patch.object(acl.os, 'name', 'nt'), patch.object(acl, '_verify_powershell', side_effect=acl.AclFailure('acl_probe_executable_rejected')), patch.object(acl.subprocess, 'run') as run:
            with self.assertRaisesRegex(acl.AclFailure, 'acl_probe_executable_rejected'):
                acl.verify_permissions()
            run.assert_not_called()

    def test_nonzero_or_stderr_fail_closed_without_diagnostics(self):
        for code, stderr in ((1, b'synthetic private path'), (0, b'synthetic private identity')):
            with self.subTest(code=code), self.assertRaisesRegex(acl.AclFailure, '^acl_probe_failed$'):
                self.invoke(SimpleNamespace(returncode=code, stdout=b'private content', stderr=stderr))

    def test_timeout_or_spawn_failure_sanitized(self):
        for error in (OSError('synthetic path'), subprocess.TimeoutExpired('synthetic command', 10)):
            with patch.object(acl.os, 'name', 'nt'), patch.object(acl, '_verify_powershell'), patch.object(acl.subprocess, 'run', side_effect=error):
                with self.assertRaisesRegex(acl.AclFailure, '^acl_probe_failed$'):
                    acl.verify_permissions()

    def test_script_has_fixed_paths_and_no_file_content_or_mutating_commands(self):
        script = acl.ACL_METADATA_SCRIPT
        for name in ('id_ed25519', 'known_hosts', 'integration-binding.json'):
            self.assertIn('C:\\ProgramData\\HomeEnergyReporterStaging\\' + name, script)
        for prohibited in ('Get-Content', 'ReadAll', 'Set-Acl', 'SetAccessControl', 'Remove-', 'New-Item', 'Invoke-Expression'):
            self.assertNotIn(prohibited, script)
        self.assertIn('GetSecurityDescriptorBinaryForm', script)
        self.assertIn('$heRaw.DiscretionaryAcl.Count', script)
        self.assertIn('$heRawAce.GetType() -ne [System.Security.AccessControl.CommonAce]', script)
        self.assertIn('$heRawAce.AceType -ne [System.Security.AccessControl.AceType]::AccessAllowed', script)
        self.assertIn('$heRawAce.IsCallback -or $heRawAce.OpaqueLength -ne 0', script)
        self.assertIn('$heRawAce.AceFlags -band (-bnot 19)', script)
        self.assertIn("[Console]::Error.Write('acl_metadata_unavailable')", script)


if __name__ == '__main__':
    unittest.main()
