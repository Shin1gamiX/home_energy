"""Offline tests for report-only SSH activation; no real account or SSH changes."""
from collections import namedtuple
from contextlib import ExitStack, redirect_stdout
import base64
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch


SOURCE = Path(__file__).resolve().parents[1] / 'deploy/activate-report-access.py'
SPEC = importlib.util.spec_from_file_location('report_production_access', SOURCE)
access = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(access)
Shadow = namedtuple('Shadow', 'sp_namp sp_pwdp sp_lstchg sp_min sp_max sp_warn sp_inact sp_expire sp_flag')
SHADOW = Shadow('homeenergy-reporter', '!SYNTHETIC-NEVER-PRINT', 20739, -1, -1, -1, -1, 1, 0)
ACCOUNT = SimpleNamespace(pw_uid=996, pw_gid=986)


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def digest(body):
    return hashlib.sha256(body).hexdigest()


def result(stdout='', stderr='', returncode=0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


def public_key(raw=None):
    raw = raw or b'\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20' + bytes(range(32))
    return b'ssh-ed25519 ' + base64.b64encode(raw) + b' homeenergy-production-only\n'


class PublicKeyAndPolicyTests(unittest.TestCase):
    def test_valid_key_gets_exact_restrict_and_forced_command(self):
        self.assertEqual(access.authorized_line(public_key()),
                         ('restrict,command="' + access.FORCE + '" ').encode('ascii') + public_key())

    def test_bad_key_fields_are_rejected(self):
        for body in (b'', b'x' * 257, 'not bytes', public_key().replace(b'ssh-ed25519 ', b'ssh-rsa ', 1),
                     public_key().replace(b'homeenergy-production-only', b'other-comment'),
                     public_key() + b'command="/bin/sh"', b'command="/bin/sh" ' + public_key()):
            with self.subTest(body=body), self.assertRaises((RuntimeError, ValueError)):
                access.authorized_line(body)

    def test_bad_wire_key_structure_and_encoding_are_rejected(self):
        for raw in (b'x' * 51, b'\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20' + b'x' * 31,
                    b'\x00\x00\x00\x0bssh-ed25519\x00\x00\x00\x20' + b'x' * 33):
            with self.subTest(raw=raw), self.assertRaises(RuntimeError):
                access.authorized_line(public_key(raw))
        with self.assertRaises(ValueError):
            access.authorized_line(b'ssh-ed25519 !!! homeenergy-production-only')
        with self.assertRaises(UnicodeDecodeError):
            access.authorized_line(b'\xff')

    def test_restrictions_accept_only_expected_settings_and_minimal_environment(self):
        settings = dict(access.EXPECTED_SSH, acceptenv='LANG LC_*', setenv='none')
        access.restricted(settings)
        access.restricted(dict(access.EXPECTED_SSH))

    def test_each_missing_or_changed_required_restriction_is_rejected(self):
        for key in access.EXPECTED_SSH:
            for action in ('missing', 'changed'):
                with self.subTest(key=key, action=action):
                    settings = dict(access.EXPECTED_SSH)
                    if action == 'missing':
                        del settings[key]
                    else:
                        settings[key] = 'unexpected'
                    with self.assertRaisesRegex(RuntimeError, 'SSH restrictions'):
                        access.restricted(settings)

    def test_unreviewed_environment_is_rejected(self):
        for extra in ({'acceptenv': 'LANG LC_* PYTHONPATH'}, {'acceptenv': '*'},
                      {'setenv': 'PYTHONPATH=/tmp'}, {'acceptenv': 'SSH_ORIGINAL_COMMAND'}):
            with self.subTest(extra=extra), self.assertRaisesRegex(RuntimeError, 'SSH environment'):
                access.restricted(dict(access.EXPECTED_SSH, **extra))

    def test_effective_inspection_has_fixed_context_and_optional_config(self):
        checks = {'SSH_CONTEXT': 'user=homeenergy-reporter,host=localhost,addr=127.0.0.1',
                  'run': Mock(return_value=result('permittty no\nacceptenv LANG\nacceptenv LC_*\n'))}
        settings = access.effective(checks, 'shini', '/synthetic/config')
        self.assertEqual(settings, {'permittty': 'no', 'acceptenv': 'LANG LC_*'})
        checks['run'].assert_called_once_with([
            '/usr/sbin/sshd', '-T', '-C', 'user=shini,host=localhost,addr=127.0.0.1',
            '-f', '/synthetic/config'])

    def test_effective_inspection_refuses_diagnostics_or_unparseable_output(self):
        for response in (result('permittty no\n', 'warning'), result('invalid\n')):
            with self.subTest(response=response), self.assertRaises(RuntimeError):
                access.effective({'SSH_CONTEXT': 'user=homeenergy-reporter,',
                                  'run': Mock(return_value=response)}, 'shini')

    def test_candidate_preserves_order_of_actual_include_names(self):
        original = b'# before\nInclude /etc/ssh/sshd_config.d/*.conf\n# after\n'
        names = [Path('/etc/ssh/sshd_config.d/99-last.conf'), Path('/etc/ssh/sshd_config.d/00-first.conf')]
        actual = access.candidate_main(original, '/proc/123/fd/7', names, '50-production.conf')
        self.assertEqual(actual, b'# before\nInclude /etc/ssh/sshd_config.d/00-first.conf '
                         b'/proc/123/fd/7 /etc/ssh/sshd_config.d/99-last.conf\n# after\n')

    def test_candidate_rejects_duplicate_missing_or_ambiguous_include(self):
        line = b'Include /etc/ssh/sshd_config.d/*.conf\n'
        for original in (b'Port 22\n', line + line, b'Include /different/*.conf\n'):
            with self.subTest(original=original), self.assertRaisesRegex(RuntimeError, 'Include structure'):
                access.candidate_main(original, '/proc/1/fd/3', [], '00-production.conf')
        with self.assertRaisesRegex(RuntimeError, 'include inventory'):
            access.candidate_main(line, '/proc/1/fd/3', [Path('/etc/ssh/sshd_config.d/00-production.conf')],
                                  '00-production.conf')
        with self.assertRaisesRegex(RuntimeError, 'include inventory'):
            access.candidate_main(line, '/proc/1/fd/3', [Path('/etc/ssh/sshd_config.d/bad name.conf')],
                                  '00-production.conf')


class AccountAndPreflightTests(unittest.TestCase):
    def setUp(self):
        self.recovery = {'identity': Mock(return_value=ACCOUNT), 'shadow_state': Mock(return_value=SHADOW),
                         'RELEASE': Path('/synthetic/release'),
                         'evidence_digest': Mock(return_value={'baseline': 'unchanged'})}
        self.checks = {'USER': 'homeenergy-reporter', 'run': Mock(), 'read_bundle': Mock(return_value=({}, {})),
                       'verify_disabled': Mock(), 'absent': Mock(return_value=True), 'trusted': Mock(),
                       'KEY': Path('/synthetic/authorized_keys/homeenergy-reporter'),
                       'RECEIPT': Path('/synthetic/receipt'), 'read_file': Mock(return_value=b'receipt'),
                       'decode': Mock(return_value={'before': {'files': {}, 'services': {}}}),
                       'digest': digest, 'preserved_snapshot': Mock(return_value={'files': {}, 'services': {}})}
        self.checks['run'].side_effect = lambda args, **kwargs: result(
            'User homeenergy-reporter is not allowed to run sudo on Synthetic-server.\n')

    def test_account_requires_exact_expiry_and_conclusive_sudo_denial(self):
        self.assertEqual(access.account_state(self.recovery, self.checks, 1), (ACCOUNT, SHADOW))
        self.checks['run'].assert_has_calls([
            call(['/usr/sbin/visudo', '-c']),
            call(['/usr/bin/sudo', '-n', '-l', '-U', 'homeenergy-reporter'], check=False)])
        for status in (0, 1):
            self.checks['run'].side_effect = lambda args, **kwargs: result(
                'User homeenergy-reporter is not allowed to run sudo on Synthetic-server.\n', returncode=status)
            access.account_state(self.recovery, self.checks, 1)

    def test_account_wrong_expiry_stops_before_commands(self):
        with self.assertRaisesRegex(RuntimeError, 'expiry'):
            access.account_state(self.recovery, self.checks, -1)
        self.checks['run'].assert_not_called()

    def test_ambiguous_or_privileged_sudo_results_are_refused(self):
        for denial in (result('ALL'), result('', 'error', 1), result('not allowed', returncode=1),
                       result('User homeenergy-reporter is not allowed to run sudo on Synthetic-server.', returncode=2)):
            with self.subTest(denial=denial):
                self.checks['run'].side_effect = [result(), denial]
                with self.assertRaisesRegex(RuntimeError, 'Sudo denial'):
                    access.account_state(self.recovery, self.checks, 1)

    def test_shadow_metadata_never_contains_hash_or_expiry(self):
        metadata = access.shadow_metadata(SHADOW)
        self.assertNotIn('sp_pwdp', metadata)
        self.assertNotIn('sp_expire', metadata)
        self.assertNotIn(SHADOW.sp_pwdp, json.dumps(metadata))

    def test_preflight_reuses_full_disabled_verification_before_candidate(self):
        events = []
        self.checks['verify_disabled'].side_effect = lambda *args: events.append('disabled')
        with patch.object(access, 'account_state', return_value=(ACCOUNT, SHADOW)) as account, \
                patch.object(access, 'effective', side_effect=lambda checks, user: {'synthetic': user}), \
                patch.object(access, 'candidate_policy', side_effect=lambda *args: events.append('candidate')) as candidate:
            before, shadow = access.preflight(self.recovery, self.checks, b'policy')
        self.assertEqual(events, ['disabled', 'candidate'])
        self.checks['verify_disabled'].assert_called_once_with({}, {})
        account.assert_called_once_with(self.recovery, self.checks, 1)
        candidate.assert_called_once_with(self.checks, b'policy', {
            'shini': {'synthetic': 'shini'}, 'homeenergy-reporter-staging': {'synthetic': 'homeenergy-reporter-staging'}})
        self.assertEqual(before['scope'], 'report-ssh-access-publication-disabled')
        self.assertEqual((before['uid'], before['gid']), (996, 986))
        self.assertEqual(before['preparation_sha256'], digest(b'receipt'))
        self.assertEqual(shadow, SHADOW)
        self.assertNotIn(SHADOW.sp_pwdp, json.dumps(before))

    def test_failed_disabled_verification_stops_before_other_checks(self):
        self.checks['verify_disabled'].side_effect = RuntimeError('closed gate check failed')
        with patch.object(access, 'candidate_policy') as candidate:
            with self.assertRaisesRegex(RuntimeError, 'closed gate check failed'):
                access.preflight(self.recovery, self.checks, b'policy')
        candidate.assert_not_called()
        self.checks['absent'].assert_not_called()

    def test_global_sshrc_blocks_preflight(self):
        self.checks['absent'].return_value = False
        with patch.object(access, 'candidate_policy') as candidate:
            with self.assertRaisesRegex(RuntimeError, 'Global SSH rc'):
                access.preflight(self.recovery, self.checks, b'policy')
        candidate.assert_not_called()

    def test_preflight_never_accepts_a_refreshed_or_changed_original_baseline(self):
        self.checks['preserved_snapshot'].return_value = {'files': {'new': 'value'}, 'services': {}}
        with patch.object(access, 'account_state', return_value=(ACCOUNT, SHADOW)), \
                patch.object(access, 'effective', return_value={}), patch.object(access, 'candidate_policy'):
            with self.assertRaisesRegex(RuntimeError, 'baseline or evidence changed'):
                access.preflight(self.recovery, self.checks, b'policy')

    def test_preflight_refuses_receipt_and_original_evidence_drift(self):
        with patch.object(access, 'account_state', return_value=(ACCOUNT, SHADOW)), \
                patch.object(access, 'effective', return_value={}), patch.object(access, 'candidate_policy'):
            self.checks['read_file'].side_effect = [b'receipt', b'changed']
            with self.assertRaisesRegex(RuntimeError, 'baseline or evidence changed'):
                access.preflight(self.recovery, self.checks, b'policy')
            self.checks['read_file'].side_effect = None
            self.recovery['evidence_digest'].side_effect = [{'same': True}, {'same': False}]
            with self.assertRaisesRegex(RuntimeError, 'baseline or evidence changed'):
                access.preflight(self.recovery, self.checks, b'policy')


class CandidateConfigurationTests(unittest.TestCase):
    def invoke(self, *, altered_other=False, invalid_syntax=False):
        checks = {'USER': 'homeenergy-reporter', 'INCLUDE': Path('/etc/ssh/sshd_config.d/20-report.conf'),
                  'SSH_CONFIG': Path('/etc/ssh/sshd_config'),
                  'read_file': Mock(return_value=b'Include /etc/ssh/sshd_config.d/*.conf\n'), 'run': Mock()}
        if invalid_syntax:
            checks['run'].side_effect = RuntimeError('Invalid candidate syntax')
        written = []

        class CandidateBytes(io.BytesIO):
            def close(self):
                written.append(self.getvalue())
                super().close()

        def effective(_checks, user, config):
            if user == 'homeenergy-reporter':
                return dict(access.EXPECTED_SSH)
            return {'safe': not altered_other}

        with patch.object(access.os, 'memfd_create', side_effect=[301, 302], create=True) as memory, \
                patch.object(access.os, 'MFD_CLOEXEC', 1, create=True), \
                patch.object(access.os, 'dup', side_effect=lambda descriptor: descriptor + 10), \
                patch.object(access.os, 'fdopen', side_effect=lambda descriptor, mode: CandidateBytes()), \
                patch.object(access.os, 'getpid', return_value=999), \
                patch.object(access.os, 'close') as closed, \
                patch.object(Path, 'glob', return_value=iter([Path('/etc/ssh/sshd_config.d/10-existing.conf')])), \
                patch.object(access, 'effective', side_effect=effective) as inspected:
            try:
                access.candidate_policy(checks, b'Match User synthetic\n', {'shini': {'safe': True}})
            finally:
                self.assertEqual(closed.call_args_list, [call(301), call(302)])
        return checks, memory, inspected, written

    def test_candidate_uses_memory_files_and_audits_reporter_and_other_users(self):
        checks, memory, inspected, written = self.invoke()
        self.assertEqual(memory.call_count, 2)
        checks['run'].assert_called_once_with(['/usr/sbin/sshd', '-t', '-f', '/proc/999/fd/302'])
        self.assertEqual(inspected.call_args_list, [
            call(checks, 'homeenergy-reporter', '/proc/999/fd/302'), call(checks, 'shini', '/proc/999/fd/302')])
        self.assertEqual(written, [b'Match User synthetic\n',
                                   b'Include /etc/ssh/sshd_config.d/10-existing.conf /proc/999/fd/301\n'])

    def test_other_user_drift_closes_all_memory_descriptors(self):
        with self.assertRaisesRegex(RuntimeError, 'administrator or staging SSH access'):
            self.invoke(altered_other=True)

    def test_syntax_failure_closes_all_memory_descriptors(self):
        with self.assertRaisesRegex(RuntimeError, 'Invalid candidate syntax'):
            self.invoke(invalid_syntax=True)


class ActivationFlowTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        self.backup = self.root / 'retained-evidence'
        self.output = self.stack.enter_context(redirect_stdout(io.StringIO()))
        self.events = []
        self.files = {}
        self.fail_event = None
        self.expiry = 1
        self.before = {'scope': 'report-ssh-access-publication-disabled', 'preserved': {'unchanged': True}}
        self.recovery = {'shadow_state': Mock(return_value=SHADOW),
                         'SHADOW_UNCHANGED': tuple(name for name in SHADOW._fields if name != 'sp_expire')}
        self.checks = {'ADMIN_HOME': self.root, 'KEY': self.root / 'reporter.key',
                       'INCLUDE': self.root / 'production.conf', 'USER': 'homeenergy-reporter',
                       'trusted': Mock(), 'encoded': encode, 'digest': digest,
                       'preserved_snapshot': Mock(return_value=self.before['preserved']),
                       'run': Mock(side_effect=self.run_command)}
        self.preflight = self.stack.enter_context(patch.object(access, 'preflight', return_value=(self.before, SHADOW)))
        self.create = self.stack.enter_context(patch.object(access, 'create_file', side_effect=self.create_file))
        self.audit = self.stack.enter_context(patch.object(access, 'audit', side_effect=self.audit_state))
        self.effective = self.stack.enter_context(patch.object(access, 'effective', return_value=dict(access.EXPECTED_SSH)))
        self.account = self.stack.enter_context(patch.object(access, 'account_state', side_effect=self.account_state))
        self.rollback = self.stack.enter_context(patch.object(access, 'rollback', side_effect=self.roll_back))
        self.stack.enter_context(patch.object(access.tempfile, 'mkdtemp', return_value=str(self.backup)))
        self.stack.enter_context(patch.object(access.os, 'chown', create=True))
        self.stack.enter_context(patch.object(access.os, 'chmod'))
        self.stack.enter_context(patch.object(access.os, 'umask'))

    def checkpoint(self, event):
        self.events.append(event)
        if self.fail_event == event:
            raise RuntimeError('Synthetic failure at ' + event)

    def create_file(self, checks, path, body, mode, **kwargs):
        self.checkpoint('before-write:' + path.name)
        self.assertNotIn(path, self.files)
        self.files[path] = (body, mode)
        if kwargs.get('created') is not None:
            kwargs['created'][path] = ('synthetic-device', path.name)
        self.checkpoint('after-write:' + path.name)

    def run_command(self, args, **kwargs):
        if args == ['/usr/bin/systemctl', 'reload', 'ssh.service']:
            self.checkpoint('reload')
        elif args == ['/usr/bin/systemctl', 'is-active', 'ssh.service']:
            self.checkpoint('is-active')
        elif args == ['/usr/bin/chage', '--expiredate', '-1', 'homeenergy-reporter']:
            self.checkpoint('before-unexpire')
            self.expiry = -1
            self.checkpoint('after-unexpire')
        else:
            raise AssertionError('Unexpected privileged command: ' + repr(args))
        return result('active\n')

    def account_state(self, recovery, checks, expiry):
        self.checkpoint('account:' + str(expiry))
        self.assertEqual(self.expiry, expiry)
        return ACCOUNT, SHADOW._replace(sp_expire=expiry)

    def audit_state(self, recovery, checks, policy, key, before, *, expiry):
        self.assertEqual(self.files[self.checks['KEY']], (b'key', 0o644))
        self.assertEqual(self.files[self.checks['INCLUDE']], (b'policy', 0o644))
        self.assertEqual(self.expiry, expiry)
        self.checkpoint('audit:' + str(expiry))
        return SHADOW._replace(sp_expire=expiry)

    def roll_back(self, recovery, checks, policy, key, created):
        self.events.append('rollback')
        self.expiry = 1
        self.files.pop(checks['KEY'], None)
        self.files.pop(checks['INCLUDE'], None)

    def invoke(self):
        access.activate(self.recovery, self.checks, b'policy', b'key')

    def test_success_installs_policy_then_key_audits_reloads_and_unexpires_last(self):
        self.invoke()
        self.assertEqual(self.events, [
            'before-write:baseline.json', 'after-write:baseline.json',
            'before-write:production.conf', 'after-write:production.conf', 'account:1',
            'before-write:reporter.key', 'after-write:reporter.key', 'audit:1', 'reload', 'is-active',
            'before-unexpire', 'after-unexpire', 'audit:-1',
            'before-write:completed.json', 'after-write:completed.json'])
        self.assertEqual(set(self.files), {self.backup / 'baseline.json', self.backup / 'completed.json',
                                          self.checks['KEY'], self.checks['INCLUDE']})
        self.assertEqual(self.files[self.backup / 'baseline.json'], (encode(self.before) + b'\n', 0o600))
        completion = json.loads(self.files[self.backup / 'completed.json'][0])
        self.assertFalse(completion['public_publishing_enabled'])
        self.assertFalse(completion['client_authentication_verified'])
        self.assertEqual(completion['baseline_sha256'], digest(encode(self.before) + b'\n'))
        self.assertEqual(self.expiry, -1)
        self.rollback.assert_not_called()
        self.assertNotIn(SHADOW.sp_pwdp, self.output.getvalue())

    def test_failed_preflight_creates_no_resources(self):
        self.preflight.side_effect = RuntimeError('Unverified receipt')
        with self.assertRaisesRegex(RuntimeError, 'Unverified receipt'):
            self.invoke()
        self.assertEqual(self.files, {})
        self.create.assert_not_called()
        self.rollback.assert_not_called()
        self.checks['run'].assert_not_called()

    def test_prewrite_protected_state_drift_rolls_back_without_installing_access(self):
        self.checks['preserved_snapshot'].return_value = {'changed': True}
        with self.assertRaisesRegex(RuntimeError, 'State changed'):
            self.invoke()
        self.assertEqual(set(self.files), {self.backup / 'baseline.json'})
        self.assertEqual(self.expiry, 1)
        self.assertEqual(self.events[-1], 'rollback')

    def test_prewrite_shadow_drift_is_not_accepted(self):
        self.recovery['shadow_state'].return_value = SHADOW._replace(sp_pwdp='!different')
        with self.assertRaisesRegex(RuntimeError, 'State changed'):
            self.invoke()
        self.assertEqual(set(self.files), {self.backup / 'baseline.json'})

    def test_failure_before_or_after_each_access_write_rolls_back_but_retains_evidence(self):
        for event in ('before-write:production.conf', 'after-write:production.conf',
                      'account:1', 'before-write:reporter.key', 'after-write:reporter.key', 'audit:1',
                      'reload', 'is-active', 'before-unexpire', 'after-unexpire', 'audit:-1',
                      'before-write:completed.json'):
            with self.subTest(event=event):
                self.files.clear()
                self.events.clear()
                self.expiry = 1
                self.fail_event = event
                with self.assertRaisesRegex(RuntimeError, 'Synthetic failure'):
                    self.invoke()
                self.assertEqual(set(self.files), {self.backup / 'baseline.json'})
                self.assertEqual(self.expiry, 1)
                self.assertEqual(self.events[-1], 'rollback')

    def test_completed_evidence_is_preserved_even_if_postwrite_check_fails(self):
        self.fail_event = 'after-write:completed.json'
        with self.assertRaises(RuntimeError):
            self.invoke()
        self.assertEqual(set(self.files), {self.backup / 'baseline.json', self.backup / 'completed.json'})
        self.assertEqual(self.expiry, 1)

    def test_unrelated_shadow_change_after_enabling_rolls_back(self):
        self.audit.side_effect = [SHADOW, SHADOW._replace(sp_expire=-1, sp_pwdp='!changed')]
        with self.assertRaisesRegex(RuntimeError, 'Unrelated shadow field'):
            self.invoke()
        self.assertEqual(self.expiry, 1)
        self.assertNotIn(self.checks['KEY'], self.files)

    def test_failed_rollback_is_reported_without_false_success_or_evidence_deletion(self):
        self.fail_event = 'audit:1'
        self.rollback.side_effect = RuntimeError('Rollback failed')
        with self.assertRaisesRegex(RuntimeError, 'Synthetic failure'):
            self.invoke()
        self.assertIn('rollback incomplete', self.output.getvalue())
        self.assertNotIn('Restricted reporting SSH policy installed.', self.output.getvalue())
        self.assertIn(self.backup / 'baseline.json', self.files)

    def test_interrupt_also_rolls_back(self):
        self.audit.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.invoke()
        self.rollback.assert_called_once()
        self.assertEqual(self.expiry, 1)


class RollbackAndFileGuardsTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        self.key, self.policy = self.root / 'key', self.root / 'policy'
        self.checks = {'KEY': self.key, 'INCLUDE': self.policy, 'USER': 'homeenergy-reporter',
                       'absent': Mock(side_effect=lambda path: not os.path.lexists(path)),
                       'trusted': Mock(), 'read_file': Mock(side_effect=lambda path: path.read_bytes()),
                       'run': Mock(return_value=result('active\n'))}

    def test_rollback_expires_then_removes_key_before_policy_and_reloads(self):
        events = []
        self.checks['run'].side_effect = lambda args: events.append(tuple(args)) or result('active\n')
        with patch.object(access, 'account_state', side_effect=lambda *args: events.append('confirm-expired')), \
                patch.object(access, 'remove_exact', side_effect=lambda checks, path, body, created: events.append(path)):
            access.rollback({}, self.checks, b'policy', b'key', {})
        self.assertEqual(events, [('/usr/bin/chage', '--expiredate', '1', 'homeenergy-reporter'),
                                 'confirm-expired', self.key, self.policy,
                                 ('/usr/sbin/sshd', '-t'), ('/usr/bin/systemctl', 'reload', 'ssh.service'),
                                 ('/usr/bin/systemctl', 'is-active', 'ssh.service')])

    def test_failed_expiry_confirmation_still_revokes_key_but_retains_policy(self):
        with patch.object(access, 'account_state', side_effect=RuntimeError('Not expired')), \
                patch.object(access, 'remove_exact') as removal, redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, 'policy retained'):
                access.rollback({}, self.checks, b'policy', b'key', {})
        removal.assert_called_once_with(self.checks, self.key, b'key', {})
        self.checks['run'].assert_called_once_with(['/usr/bin/chage', '--expiredate', '1', 'homeenergy-reporter'])

    def test_key_revocation_failure_retains_restrictive_policy(self):
        with patch.object(access, 'account_state'), \
                patch.object(access, 'remove_exact', side_effect=RuntimeError('Changed key')) as removal, \
                redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, 'policy retained'):
                access.rollback({}, self.checks, b'policy', b'key', {})
        removal.assert_called_once_with(self.checks, self.key, b'key', {})
        self.checks['run'].assert_called_once_with(['/usr/bin/chage', '--expiredate', '1', 'homeenergy-reporter'])

    @staticmethod
    def owned(path):
        info = path.lstat()
        return {path: (info.st_dev, info.st_ino)}

    def test_remove_exact_deletes_only_matching_managed_file(self):
        self.key.write_bytes(b'key')
        created = self.owned(self.key)
        access.remove_exact(self.checks, self.key, b'key', created)
        self.assertFalse(self.key.exists())
        self.checks['trusted'].assert_called_once_with(self.key, mode=0o644, directory=False)
        access.remove_exact(self.checks, self.key, b'key', created)

    def test_remove_exact_preserves_changed_or_unapproved_file(self):
        self.key.write_bytes(b'changed')
        with self.assertRaisesRegex(RuntimeError, 'preserve for review'):
            access.remove_exact(self.checks, self.key, b'key', self.owned(self.key))
        self.assertEqual(self.key.read_bytes(), b'changed')
        other = self.root / 'other'
        other.write_bytes(b'keep')
        with self.assertRaisesRegex(RuntimeError, 'Rollback target refused'):
            access.remove_exact(self.checks, other, b'keep', self.owned(other))
        self.assertEqual(other.read_bytes(), b'keep')

    def test_same_bytes_without_this_invocations_inode_ownership_are_preserved(self):
        self.key.write_bytes(b'key')
        for created in ({}, {self.key: (99, 99)}):
            with self.subTest(created=created), self.assertRaisesRegex(RuntimeError, 'not created by this invocation'):
                access.remove_exact(self.checks, self.key, b'key', created)
        self.assertEqual(self.key.read_bytes(), b'key')

    def test_create_refuses_existing_or_unapproved_target_before_open(self):
        self.key.write_bytes(b'keep')
        for path in (self.key, self.root / 'not-allowed'):
            with self.subTest(path=path), patch.object(access.os, 'open') as opened:
                with self.assertRaisesRegex(RuntimeError, 'New access file target refused'):
                    access.create_file(self.checks, path, b'new', 0o644)
                opened.assert_not_called()
        self.assertEqual(self.key.read_bytes(), b'keep')

    def test_create_uses_exclusive_nofollow_and_only_expected_metadata_calls(self):
        stream = Mock()
        context = Mock(__enter__=Mock(return_value=stream), __exit__=Mock(return_value=False))
        stream.fileno.return_value = 123
        self.checks['read_file'].return_value = b'key'
        self.checks['read_file'].side_effect = None
        with patch.object(access.os, 'O_NOFOLLOW', 0x100000, create=True), \
                patch.object(access.os, 'open', return_value=123) as opened, \
                patch.object(access.os, 'fdopen', return_value=context), \
                patch.object(access.os, 'fsync') as fsync, \
                patch.object(access.os, 'fchown', create=True) as chown, \
                patch.object(access.os, 'fchmod', create=True) as chmod:
            access.create_file(self.checks, self.key, b'key', 0o644)
        opened.assert_called_once_with(self.key, os.O_WRONLY | os.O_CREAT | os.O_EXCL | 0x100000, 0o644)
        fsync.assert_called_once_with(123)
        chown.assert_called_once_with(123, 0, 0)
        chmod.assert_called_once_with(123, 0o644)
        stream.write.assert_called_once_with(b'key')

    def test_create_records_inode_ownership_before_any_failable_write(self):
        stream = Mock()
        context = Mock(__enter__=Mock(return_value=stream), __exit__=Mock(return_value=False))
        stream.fileno.return_value = 123
        stream.write.side_effect = OSError('Synthetic write failure')
        created = {}
        with patch.object(access.os, 'O_NOFOLLOW', 0, create=True), \
                patch.object(access.os, 'open', return_value=123), \
                patch.object(access.os, 'fdopen', return_value=context), \
                patch.object(access.os, 'fstat', return_value=SimpleNamespace(st_dev=77, st_ino=99)):
            with self.assertRaisesRegex(OSError, 'Synthetic write failure'):
                access.create_file(self.checks, self.key, b'key', 0o644, created=created)
        self.assertEqual(created, {self.key: (77, 99)})
        self.checks['trusted'].assert_not_called()


class InstalledAuditTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        modules, state, home = (self.root / name for name in ('modules', 'state', 'home'))
        original_backup = self.root / 'original-backup'
        for path in (modules, state, home, state / 'public', original_backup):
            path.mkdir()
        for name in ('baseline.json', 'identity.json'):
            (original_backup / name).write_bytes(b'original-evidence')
        self.policy, self.key = b'policy', b'key'
        self.gate = {'schema_version': 1, 'scope': 'production', 'public_publishing_enabled': False}
        self.checks = {'USER': 'homeenergy-reporter', 'KEY': self.root / 'key', 'INCLUDE': self.root / 'policy',
                       'MODULES': modules, 'HOME': home, 'STATE': state, 'OLD_MODULES': ('old.py',),
                       'WRAPPER': modules / 'wrapper.py', 'GATE': modules / 'gate.json',
                       'RECEIPT': modules / 'receipt.json', 'ACTIVATION': self.root / 'activation',
                       'DISABLED_GATE': self.gate, 'PROTECTED_TREES': (self.root / 'protected',),
                       'PRIVATE_PATHS': (), 'run': Mock(return_value=result()), 'trusted': Mock(),
                       'absent': Mock(side_effect=lambda path: not path.exists()), 'digest': digest,
                       'read_file': Mock(side_effect=lambda path, *args: path.read_bytes()), 'encoded': encode,
                       'read_bundle': Mock(return_value=({'baseline': True}, {'wrapper.py': b'wrapper'})),
                       'audit_tree': Mock(), 'access_allowed': Mock(return_value=False),
                       'verify_gate_rejection': Mock(), 'compare_baseline': Mock()}
        for path, body in ((self.checks['KEY'], self.key), (self.checks['INCLUDE'], self.policy),
                           (modules / 'old.py', b'old'), (self.checks['WRAPPER'], b'wrapper'),
                           (self.checks['GATE'], encode(self.gate) + b'\n'),
                           (self.checks['RECEIPT'], b'receipt'), (state / 'public/index.json', b'empty')):
            path.write_bytes(body)
        self.before = {'uid': 996, 'gid': 986, 'shadow_metadata': access.shadow_metadata(SHADOW),
                       'other_ssh': {'shini': {'safe': True}}, 'preparation_sha256': digest(b'receipt'),
                       'original_evidence': {'baseline': 'same'}, 'preserved': {'files': {}, 'services': {'same': True}}}
        self.current = copy.deepcopy(self.before['preserved'])
        self.current['files'][str(self.checks['INCLUDE'])] = {
            'sha256': digest(self.policy), 'uid': 0, 'gid': 0, 'mode': 0o644}
        self.checks['preserved_snapshot'] = Mock(side_effect=lambda: copy.deepcopy(self.current))
        self.recovery = {'RELEASE': self.root / 'original-release', 'BACKUP': original_backup,
                         'evidence_digest': Mock(return_value=self.before['original_evidence'])}
        self.account = self.stack.enter_context(patch.object(access, 'account_state', return_value=(ACCOUNT, SHADOW)))
        self.effective = self.stack.enter_context(patch.object(access, 'effective', side_effect=lambda checks, user:
            dict(access.EXPECTED_SSH) if user == checks['USER'] else {'safe': True}))

    def invoke(self):
        return access.audit(self.recovery, self.checks, self.policy, self.key, self.before, expiry=1)

    def test_full_audit_preserves_gate_state_receipt_and_evidence(self):
        self.assertEqual(self.invoke(), SHADOW)
        self.checks['audit_tree'].assert_called_once_with(ACCOUNT, self.root / 'protected')
        self.checks['verify_gate_rejection'].assert_called_once_with(ACCOUNT)
        self.checks['compare_baseline'].assert_called_once_with({'baseline': True}, self.before['preserved'])
        self.assertEqual(self.checks['GATE'].read_bytes(), encode(self.gate) + b'\n')
        self.assertIn(str(self.checks['INCLUDE']), self.current['files'])

    def test_open_gate_or_changed_wrapper_stops_before_probe(self):
        for path in (self.checks['GATE'], self.checks['WRAPPER']):
            original = path.read_bytes()
            with self.subTest(path=path):
                path.write_bytes(b'changed')
                with self.assertRaisesRegex(RuntimeError, 'gate is not closed'):
                    self.invoke()
                self.checks['verify_gate_rejection'].assert_not_called()
                path.write_bytes(original)

    def test_changed_receipt_or_original_evidence_is_rejected(self):
        self.checks['RECEIPT'].write_bytes(b'changed')
        with self.assertRaisesRegex(RuntimeError, 'Original evidence changed'):
            self.invoke()
        self.checks['RECEIPT'].write_bytes(b'receipt')
        self.recovery['evidence_digest'].return_value = {'changed': True}
        with self.assertRaisesRegex(RuntimeError, 'Original evidence changed'):
            self.invoke()

    def test_report_database_or_activation_appearance_is_refused(self):
        for path in (self.checks['ACTIVATION'], self.checks['STATE'] / 'reports.sqlite',
                     self.checks['STATE'] / 'public/unexpected.json'):
            with self.subTest(path=path):
                path.write_bytes(b'new')
                with self.assertRaisesRegex(RuntimeError, 'activation or state appeared'):
                    self.invoke()
                path.unlink()

    def test_probe_side_effect_in_storage_is_detected(self):
        self.checks['verify_gate_rejection'].side_effect = lambda account: (
            self.checks['STATE'] / 'publisher.lock').write_bytes(b'unexpected')
        with self.assertRaisesRegex(RuntimeError, 'probe modified report storage'):
            self.invoke()

    def test_private_directory_access_is_refused(self):
        private = self.root / 'private'
        private.mkdir()
        self.checks['PRIVATE_PATHS'] = (private,)
        self.checks['access_allowed'].return_value = True
        with self.assertRaisesRegex(RuntimeError, 'Private directory access allowed'):
            self.invoke()
        self.checks['verify_gate_rejection'].assert_not_called()

    def test_unrelated_file_or_service_drift_is_refused(self):
        self.current['services']['same'] = False
        with self.assertRaisesRegex(RuntimeError, 'unrelated protected file or service changed'):
            self.invoke()
        self.checks['compare_baseline'].assert_not_called()

    def test_admin_policy_changes_are_refused(self):
        self.effective.side_effect = lambda checks, user: (
            dict(access.EXPECTED_SSH) if user == checks['USER'] else {'safe': False})
        with self.assertRaisesRegex(RuntimeError, 'Administrator or staging SSH settings'):
            self.invoke()

    def test_changed_key_or_policy_bytes_are_refused(self):
        for path in (self.checks['KEY'], self.checks['INCLUDE']):
            original = path.read_bytes()
            with self.subTest(path=path):
                path.write_bytes(b'changed')
                with self.assertRaisesRegex(RuntimeError, 'Installed access file changed'):
                    self.invoke()
                path.write_bytes(original)


class PriorSourceTests(unittest.TestCase):
    def source_setup(self, *, expected_digest=None, file_changes=None, opened_changes=None, parent_mode=0o700):
        body = (b'def load_recovery():\n'
                b'    return {"load_reviewed_checks": lambda: {"synthetic": True}}\n'
                b'if __name__ == "__main__": raise AssertionError("No main")\n')
        stack = ExitStack()
        self.addCleanup(stack.close)
        metadata = dict(st_mode=stat.S_IFREG | 0o600, st_uid=1000, st_gid=1000,
                        st_nlink=1, st_size=len(body), st_dev=1, st_ino=2)
        metadata.update(file_changes or {})

        def lstat(path):
            if path == access.FINALIZER:
                return SimpleNamespace(**metadata)
            return SimpleNamespace(st_mode=stat.S_IFDIR | (parent_mode if path == access.FINALIZER.parent else 0o750),
                                   st_uid=1000 if path in (access.FINALIZER.parent, Path('/home/shini')) else 0)

        class SourceBytes(io.BytesIO):
            def fileno(self):
                return 123

        stack.enter_context(patch.dict('sys.modules', {'pwd': SimpleNamespace(
            getpwnam=lambda name: SimpleNamespace(pw_uid=1000))}))
        stack.enter_context(patch.object(Path, 'resolve', lambda path, **kwargs: path))
        stack.enter_context(patch.object(Path, 'lstat', lstat))
        stack.enter_context(patch.object(Path, 'stat', lstat))
        self.opened = stack.enter_context(patch.object(access.os, 'open', return_value=123))
        stack.enter_context(patch.object(access.os, 'fdopen', return_value=SourceBytes(body)))
        stack.enter_context(patch.object(access.os, 'fstat', return_value=SimpleNamespace(
            **dict(metadata, **(opened_changes or {})))))
        stack.enter_context(patch.object(access.os, 'O_NOFOLLOW', 0, create=True))
        stack.enter_context(patch.object(access.os, 'O_NONBLOCK', 0, create=True))
        stack.enter_context(patch.object(access, 'FINALIZER_SHA256', expected_digest or digest(body)))
        return stack

    def test_exact_source_pin_loads_checks_without_prior_mutation_entrypoint(self):
        self.source_setup()
        recovery, checks = access.load_prior()
        self.assertEqual(checks, {'synthetic': True})
        self.assertEqual(set(recovery), {'load_reviewed_checks'})

    def test_hash_mismatch_is_refused(self):
        self.source_setup(expected_digest='0' * 64)
        with self.assertRaisesRegex(RuntimeError, 'digest changed'):
            access.load_prior()

    def test_metadata_or_descriptor_drift_is_refused(self):
        for changed in ({'st_uid': 99}, {'st_nlink': 2}, {'st_size': 65537},
                        {'st_mode': stat.S_IFLNK | 0o600}, {'st_mode': stat.S_IFREG | 0o644}):
            with self.subTest(changed=changed), self.source_setup(file_changes=changed):
                with self.assertRaisesRegex(RuntimeError, 'metadata changed'):
                    access.load_prior()
                self.opened.assert_not_called()
        with self.source_setup(opened_changes={'st_ino': 99}):
            with self.assertRaisesRegex(RuntimeError, 'changed while opening'):
                access.load_prior()

    def test_nonprivate_or_writable_release_directory_is_refused(self):
        for mode in (0o750, 0o770):
            with self.subTest(mode=mode), self.source_setup(parent_mode=mode):
                with self.assertRaises(RuntimeError):
                    access.load_prior()
                self.opened.assert_not_called()


class EntrypointTests(unittest.TestCase):
    def invoke(self, flag='--check', *, platform='linux', uid=0, isolated=1, bytecode=1):
        recovery = {'RELEASE': Path('/synthetic/release')}
        checks = {'read_bundle': Mock(return_value=({}, {'reports-production-sshd.conf.example': b'policy'}))}
        with patch.object(access.sys, 'argv', [str(SOURCE), flag]), \
                patch.object(access.sys, 'platform', platform), \
                patch.object(access.os, 'geteuid', return_value=uid, create=True), \
                patch.object(access.sys, 'flags', SimpleNamespace(isolated=isolated, dont_write_bytecode=bytecode)), \
                patch.object(access, 'load_prior', return_value=(recovery, checks)), \
                patch.object(access, 'read_bundle', return_value=b'key'), \
                patch.object(access, 'preflight') as preflight, \
                patch.object(access, 'activate') as activate, redirect_stdout(io.StringIO()):
            access.main()
        return preflight, activate, recovery, checks

    def test_check_runs_preflight_without_activation(self):
        preflight, activate, recovery, checks = self.invoke()
        preflight.assert_called_once_with(recovery, checks, b'policy')
        activate.assert_not_called()

    def test_activate_selects_only_restricted_transport_entrypoint(self):
        preflight, activate, recovery, checks = self.invoke('--activate-transport')
        preflight.assert_not_called()
        activate.assert_called_once_with(recovery, checks, b'policy', b'key')

    def test_root_isolation_and_no_bytecode_flags_are_required(self):
        for change in ({'platform': 'win32'}, {'uid': 1000}, {'isolated': 0}, {'bytecode': 0}):
            with self.subTest(change=change), self.assertRaisesRegex(RuntimeError, 'sudo /usr/bin/python3 -I -B'):
                self.invoke(**change)


if __name__ == '__main__':
    unittest.main()
