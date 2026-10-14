"""Disabled production preparation tests: temporary fixtures and mocks only."""
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import importlib.util
import json
import io
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


HELPER = Path(__file__).resolve().parents[1] / 'deploy/prepare-report-production.py'
SPEC = importlib.util.spec_from_file_location('production_prepare', HELPER)
prepare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prepare)


def services():
    return {name: {'Id': name, 'ActiveState': 'active', 'MainPID': index + 100, 'NRestarts': 0}
            for index, name in enumerate(prepare.SERVICES)}


def baseline():
    files = {path.as_posix(): 'a' * 64 for path in prepare.PRESERVED_PATHS}
    for name, value in prepare.OLD_MODULES.items():
        files[(prepare.MODULES / name).as_posix()] = value
    files[(prepare.STATE / 'public/index.json').as_posix()] = prepare.EMPTY_FEED_SHA256
    return {'preserved': files, 'services': services()}


def manifest():
    return {'schema_version': 1, 'scope': 'production-preparation',
            'public_publishing_enabled': False,
            'files': {name: 'b' * 64 for name in prepare.SOURCE_FILES}, 'baseline': baseline()}


def account():
    return SimpleNamespace(pw_uid=555, pw_gid=556, pw_dir=str(prepare.HOME), pw_shell='/bin/dash')


def completed(stdout='', stderr='', code=0):
    return subprocess.CompletedProcess([], code, stdout, stderr)


class ManifestTests(unittest.TestCase):
    def test_exact_disabled_manifest_accepted(self):
        prepare.validate_manifest(manifest())

    def test_enablement_or_other_scopes_rejected(self):
        for key, value in [('public_publishing_enabled', True), ('public_publishing_enabled', 0),
                           ('scope', 'production'), ('schema_version', True), ('schema_version', 2)]:
            with self.subTest(key=key, value=value), self.assertRaises(RuntimeError):
                prepare.validate_manifest({**manifest(), key: value})

    def test_extra_or_missing_manifest_fields_rejected(self):
        for value in [dict(manifest(), key='unapproved'), {k: v for k, v in manifest().items() if k != 'baseline'}]:
            with self.assertRaises(RuntimeError):
                prepare.validate_manifest(value)

    def test_source_file_allowlist_cannot_be_extended(self):
        for name in ['key.pub', '../escape', 'activate.py', 'manifest.json']:
            value = manifest()
            value['files'][name] = 'a' * 64
            with self.subTest(name=name), self.assertRaises(RuntimeError):
                prepare.validate_manifest(value)

    def test_invalid_hashes_rejected(self):
        for value in ['A' * 64, 'a' * 63, 'z' * 64, 42, None]:
            candidate = manifest()
            candidate['files'][prepare.SOURCE_FILES[0]] = value
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                prepare.validate_manifest(candidate)

    def test_baseline_cannot_select_other_files(self):
        candidate = baseline()
        candidate['preserved']['/etc/shadow'] = 'a' * 64
        with self.assertRaises(RuntimeError):
            prepare.validate_baseline(candidate)

    def test_existing_modules_and_empty_feed_are_fixed(self):
        for path in [prepare.MODULES / name for name in prepare.OLD_MODULES] + [prepare.STATE / 'public/index.json']:
            candidate = baseline()
            candidate['preserved'][path.as_posix()] = '0' * 64
            with self.subTest(path=path), self.assertRaises(RuntimeError):
                prepare.validate_baseline(candidate)

    def test_service_baseline_is_explicit_and_active(self):
        for key, value in [('Id', 'other.service'), ('ActiveState', 'inactive'), ('MainPID', 0),
                           ('MainPID', True), ('NRestarts', -1), ('NRestarts', '0')]:
            candidate = services()
            candidate[prepare.SERVICES[0]][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(RuntimeError):
                prepare.validate_services(candidate)

    def test_no_extra_or_missing_services(self):
        for candidate in [{}, dict(services(), extra={})]:
            with self.assertRaises(RuntimeError):
                prepare.validate_services(candidate)

    def test_json_rejects_duplicate_nonfinite_and_oversized_values(self):
        for raw in [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}', b'a' * 65537, b'']:
            with self.subTest(raw=raw[:24]), self.assertRaises((RuntimeError, ValueError)):
                prepare.decode(raw)

    def test_manifest_builder_only_reads_sources_and_private_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / 'server').mkdir()
            (root / 'deploy').mkdir()
            for name in prepare.SOURCE_FILES:
                target = root / ('server' if name == prepare.WRAPPER.name else 'deploy') / name
                target.write_bytes(b'# synthetic candidate\n')
            fixture_hashes = {}
            for name in prepare.OLD_MODULES:
                body = ('# ' + name).encode()
                (root / 'server' / name).write_bytes(body)
                fixture_hashes[name] = hashlib.sha256(body).hexdigest()
            with patch.object(prepare, 'OLD_MODULES', fixture_hashes):
                evidence = root / 'baseline.json'
                evidence.write_bytes(prepare.encoded(baseline()))
                with patch.object(prepare, 'run', side_effect=AssertionError('No subprocess during build')):
                    result = prepare.build_manifest(root, evidence)
                self.assertIs(result['public_publishing_enabled'], False)
                self.assertEqual(set(result['files']), set(prepare.SOURCE_FILES))
                self.assertEqual(result['baseline'], baseline())
                self.assertFalse((root / 'manifest.json').exists())


class ReadOnlyAndFileTests(unittest.TestCase):
    def test_bounded_file_reads(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / 'file'
            path.write_bytes(b'1234')
            self.assertEqual(prepare.read_file(path, 4), b'1234')
            with self.assertRaises(RuntimeError):
                prepare.read_file(path, 3)

    def test_hardlinked_file_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / 'file'
            path.write_bytes(b'fixed')
            os.link(path, path.with_name('hardlink'))
            with self.assertRaises(RuntimeError):
                prepare.read_file(path)

    def test_nonregular_file_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(RuntimeError):
                prepare.read_file(Path(directory).resolve())

    def test_new_file_allowlist_prevents_unrelated_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / 'unrelated'
            with self.assertRaises(RuntimeError):
                prepare.create_file(path, b'forbidden', 0o600)
            self.assertFalse(path.exists())

    def test_create_never_overwrites(self):
        with patch.object(prepare, 'absent', return_value=False), patch.object(prepare.os, 'open') as opened:
            with self.assertRaises(RuntimeError):
                prepare.create_file(prepare.WRAPPER, b'new', 0o644)
            opened.assert_not_called()

    def test_service_snapshot_requires_completed_exact_records(self):
        stdout = '\n\n'.join('\n'.join(f'{key}={value}' for key, value in item.items())
                              for item in services().values()) + '\n'
        with patch.object(prepare, 'run', return_value=completed(stdout)):
            self.assertEqual(prepare.service_snapshot(), services())
        with patch.object(prepare, 'run', return_value=completed(stdout, 'warning')):
            with self.assertRaises(RuntimeError):
                prepare.service_snapshot()

    def test_duplicate_service_fields_rejected(self):
        stdout = 'Id=nginx.service\nId=nginx.service\n'
        with patch.object(prepare, 'run', return_value=completed(stdout)), self.assertRaises(RuntimeError):
            prepare.service_snapshot()

    def test_changed_baseline_stops_preparation(self):
        value = manifest()
        snapshot = {'files': {key: {'sha256': val} for key, val in value['baseline']['preserved'].items()},
                    'services': services()}
        prepare.compare_baseline(value, snapshot)
        snapshot['services']['nginx.service']['MainPID'] += 1
        with self.assertRaises(RuntimeError):
            prepare.compare_baseline(value, snapshot)

    def test_readonly_check_calls_no_mutator(self):
        before = {'files': {}, 'services': {}}
        with patch.object(prepare, 'require_root'), patch.object(prepare, 'account_absent'), \
                patch.object(prepare, 'check_storage'), patch.object(prepare, 'absent', return_value=True), \
                patch.object(prepare, 'trusted'), patch.object(prepare.Path, 'resolve', return_value=Path('/usr/bin/dash')), \
                patch.object(prepare, 'preserved_snapshot', return_value=before), \
                patch.object(prepare, 'compare_baseline'), patch.object(prepare, 'run') as run, \
                patch.object(prepare, 'create_file', side_effect=AssertionError('Read-only check wrote')):
            self.assertIs(prepare.check(manifest()), before)
            self.assertEqual([call.args[0] for call in run.call_args_list],
                             [['/usr/sbin/sshd', '-t'], ['/usr/sbin/visudo', '-c']])


class IdentityAndPolicyTests(unittest.TestCase):
    def probe_result(self, *, allowed=False):
        user = account()
        return {'uid': user.pw_uid, 'euid': user.pw_uid, 'gid': user.pw_gid,
                'egid': user.pw_gid, 'groups': [user.pw_gid], 'allowed': allowed}

    def test_access_denial_has_explicit_kernel_identity_and_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            result = completed(json.dumps(self.probe_result()))
            with patch.object(prepare.subprocess, 'run', return_value=result) as run:
                self.assertIs(prepare.access_allowed(account(), '-w', Path(directory).resolve()), False)
            kwargs = run.call_args.kwargs
            self.assertEqual(kwargs['user'], 555)
            self.assertEqual(kwargs['group'], 556)
            self.assertEqual(kwargs['extra_groups'], [556])
            self.assertNotIn('shell', kwargs)
            self.assertIn('-I', run.call_args.args[0])
            self.assertIn('-B', run.call_args.args[0])

    def test_failed_execution_is_not_denial(self):
        with tempfile.TemporaryDirectory() as directory:
            for result in [completed(code=1), completed('{}'), completed(json.dumps(self.probe_result()), 'PAM failed'),
                           completed(json.dumps(dict(self.probe_result(), uid=0)))]:
                with self.subTest(result=result), patch.object(prepare.subprocess, 'run', return_value=result), \
                        self.assertRaises((RuntimeError, ValueError)):
                    prepare.access_allowed(account(), '-w', Path(directory).resolve())

    def test_malformed_probe_values_are_not_access_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            for changes in [{'allowed': 0}, {'allowed': None}, {'groups': [556, 0]}, {'uid': True}, {'extra': 'x'}]:
                result = completed(json.dumps(dict(self.probe_result(), **changes)))
                with self.subTest(changes=changes), patch.object(prepare.subprocess, 'run', return_value=result), \
                        self.assertRaises(RuntimeError):
                    prepare.access_allowed(account(), '-w', Path(directory).resolve())

    def test_invalid_probe_targets_and_root_identity_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            for user, flag, path in [(account(), '-z', root), (account(), '-w', root / 'missing'),
                                     (SimpleNamespace(pw_uid=0, pw_gid=0), '-w', root)]:
                with self.subTest(flag=flag), patch.object(prepare.subprocess, 'run') as run, self.assertRaises(RuntimeError):
                    prepare.access_allowed(user, flag, path)
                run.assert_not_called()

    def verify_account(self, *, expire=1, password='!', inactive=-1, sudo=None, groups=None):
        user = account()
        modules = {'pwd': SimpleNamespace(getpwnam=lambda _: user),
                   'grp': SimpleNamespace(getgrgid=lambda _: SimpleNamespace(gr_name=prepare.USER)),
                   'spwd': SimpleNamespace(getspnam=lambda _: SimpleNamespace(
                       sp_pwdp=password, sp_expire=expire, sp_inact=inactive))}
        sudo = sudo or completed(f'User {prepare.USER} is not allowed to run sudo on Shini-server.\n')
        with patch.dict('sys.modules', modules), patch.object(prepare.os, 'getgrouplist', create=True,
                                                             return_value=groups or [user.pw_gid]), \
                patch.object(prepare, 'run', side_effect=[completed(), sudo]):
            return prepare.account_disabled()

    def test_both_recognized_sudo_denial_exit_codes_accepted(self):
        for code in (0, 1):
            with self.subTest(code=code):
                policy = completed(f'User {prepare.USER} is not allowed to run sudo on Shini-server.\n', code=code)
                self.assertEqual(self.verify_account(sudo=policy).pw_uid, 555)

    def test_ambiguous_sudo_output_is_not_denial(self):
        for policy in [completed(code=1), completed('PAM failed', code=1),
                       completed(f'User {prepare.USER} is not allowed to run sudo on Shini-server.', 'warning'),
                       completed(f'User {prepare.USER} is not allowed to run sudo on Shini-server.', code=2),
                       completed('(ALL) NOPASSWD: ALL')]:
            with self.subTest(policy=policy), self.assertRaises(RuntimeError):
                self.verify_account(sudo=policy)

    def test_account_must_be_locked_expired_and_dedicated(self):
        for kwargs in [{'expire': -1}, {'expire': 0}, {'password': 'hash'}, {'inactive': 30}, {'groups': [556, 27]}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(RuntimeError):
                self.verify_account(**kwargs)

    def test_expired_account_uses_reviewed_pam_and_no_alternate_key_source(self):
        raw = ('usepam yes\npermituserenvironment no\nauthorizedkeyscommand none\n'
               'trustedusercakeys none\nacceptenv LANG\nacceptenv LC_*\n')
        with patch.object(prepare, 'run', return_value=completed(raw)):
            prepare.verify_disabled_authentication()
        for old, new in [('usepam yes', 'usepam no'), ('authorizedkeyscommand none', 'authorizedkeyscommand /bin/example'),
                         ('trustedusercakeys none', 'trustedusercakeys /tmp/ca'), ('permituserenvironment no', 'permituserenvironment yes'),
                         ('acceptenv LC_*', 'acceptenv PYTHONPATH')]:
            with self.subTest(new=new), patch.object(prepare, 'run', return_value=completed(raw.replace(old, new))), \
                    self.assertRaises(RuntimeError):
                prepare.verify_disabled_authentication()

    def test_batch_probe_is_bounded_and_has_no_false_denial_on_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            paths = [root] * 257
            def reply(args, **kwargs):
                values = json.loads(kwargs['input'])
                self.assertLessEqual(len(values), 128)
                self.assertEqual(kwargs['user'], 555)
                self.assertEqual(kwargs['group'], 556)
                self.assertEqual(kwargs['extra_groups'], [556])
                return completed(json.dumps(dict(self.probe_result(), allowed=[False] * len(values))))
            with patch.object(prepare.subprocess, 'run', side_effect=reply) as run:
                prepare.no_write_access(account(), paths)
                self.assertEqual(run.call_count, 3)
            for result in [completed(code=1), completed(''), completed(json.dumps(dict(self.probe_result(), allowed=[False])), 'error')]:
                with self.subTest(result=result), patch.object(prepare.subprocess, 'run', return_value=result), \
                        self.assertRaises((RuntimeError, ValueError)):
                    prepare.no_write_access(account(), [root])

    def test_batch_probe_rejects_write_access_and_malformed_markers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            for updates in [{'allowed': [True]}, {'allowed': [0]}, {'allowed': []}, {'allowed': False},
                            {'allowed': [False, False]}, {'allowed': [False], 'uid': 0},
                            {'allowed': [False], 'groups': [556, 27]}, {'allowed': [False], 'extra': 'x'}]:
                result = completed(json.dumps(dict(self.probe_result(), **updates)))
                with self.subTest(updates=updates), patch.object(prepare.subprocess, 'run', return_value=result), \
                        self.assertRaises(RuntimeError):
                    prepare.no_write_access(account(), [root])

    def test_batch_probe_rejects_missing_paths_and_empty_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            for paths in [[], [root / 'missing'], [root] * 100033]:
                with patch.object(prepare.subprocess, 'run') as run, self.assertRaises(RuntimeError):
                    prepare.no_write_access(account(), paths)
                run.assert_not_called()

    def test_tree_access_audit_batches_all_children_and_ancestors(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / 'sub').mkdir()
            (root / 'sub/file').write_text('fixture')
            with patch.object(prepare, 'no_write_access') as probe:
                prepare.audit_tree(account(), root)
            paths = probe.call_args.args[1]
            self.assertIn(root, paths)
            self.assertIn(root / 'sub', paths)
            self.assertIn(root / 'sub/file', paths)
            self.assertTrue(set(root.parents) <= set(paths))
            self.assertEqual(probe.call_count, 1)

    def gate_result(self):
        value = self.probe_result()
        value.pop('allowed')
        return dict(value, gate_reached=True, forbidden_entered=False, wrapper_exit=1)

    def test_disabled_wrapper_is_executed_and_gate_observed_without_patch(self):
        replies = [completed(stderr=prepare.DISABLED_ERROR, code=1),
                   completed(json.dumps(self.gate_result()), prepare.DISABLED_ERROR)]
        with patch.object(prepare.subprocess, 'run', side_effect=replies) as run:
            prepare.verify_gate_rejection(account())
        direct, probe = run.call_args_list
        self.assertEqual(direct.args[0], ['/usr/bin/python3', '-I', '-B', str(prepare.WRAPPER)])
        self.assertEqual(probe.args[0][-1], prepare.GATE_PROBE)
        for call in (direct, probe):
            self.assertEqual(call.kwargs['env']['SSH_ORIGINAL_COMMAND'], 'prepare')
            self.assertEqual(call.kwargs['user'], 555)
            self.assertEqual(call.kwargs['group'], 556)
            self.assertEqual(call.kwargs['extra_groups'], [556])
        self.assertIn('sys.setprofile', prepare.GATE_PROBE)
        self.assertIn('runpy.run_path(entry,run_name="__main__")', prepare.GATE_PROBE)
        self.assertNotIn('verify_gate=', prepare.GATE_PROBE)

    def test_other_failures_cannot_masquerade_as_disabled_gate(self):
        for update in [{'gate_reached': False}, {'forbidden_entered': True}, {'wrapper_exit': 0},
                       {'uid': 0}, {'gate_reached': 1}, {'wrapper_exit': True}]:
            replies = [completed(stderr=prepare.DISABLED_ERROR, code=1),
                       completed(json.dumps(dict(self.gate_result(), **update)), prepare.DISABLED_ERROR)]
            with self.subTest(update=update), patch.object(prepare.subprocess, 'run', side_effect=replies), \
                    self.assertRaises(RuntimeError):
                prepare.verify_gate_rejection(account())

    def test_direct_wrapper_must_fail_cleanly_without_stdout(self):
        for result in [completed(code=1), completed('unexpected', prepare.DISABLED_ERROR, 1),
                       completed(stderr=prepare.DISABLED_ERROR), completed(stderr='Permission denied', code=1)]:
            with self.subTest(result=result), patch.object(prepare.subprocess, 'run', return_value=result) as run, \
                    self.assertRaises(RuntimeError):
                prepare.verify_gate_rejection(account())
            self.assertEqual(run.call_count, 1)

    def test_probe_must_complete_with_exact_stderr(self):
        for result in [completed(json.dumps(self.gate_result())),
                       completed(json.dumps(self.gate_result()), prepare.DISABLED_ERROR, 1),
                       completed('{}', prepare.DISABLED_ERROR), completed('x' * 2049, prepare.DISABLED_ERROR)]:
            replies = [completed(stderr=prepare.DISABLED_ERROR, code=1), result]
            with self.subTest(result=result), patch.object(prepare.subprocess, 'run', side_effect=replies), \
                    self.assertRaises(RuntimeError):
                prepare.verify_gate_rejection(account())


class DisabledStorageTests(unittest.TestCase):
    def storage(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        modules = root / 'modules'
        state = root / 'state'
        modules.mkdir()
        (state / 'public').mkdir(parents=True)
        hashes = {}
        for name in prepare.OLD_MODULES:
            body = ('fixture ' + name).encode()
            (modules / name).write_bytes(body)
            hashes[name] = prepare.digest(body)
        feed = b'{"schema_version":2,"reports":[]}\n'
        (state / 'public/index.json').write_bytes(feed)
        values = {'MODULES': modules, 'STATE': state, 'OLD_MODULES': hashes,
                  'EMPTY_FEED_SHA256': prepare.digest(feed), 'KEY': root / 'key',
                  'INCLUDE': root / 'include', 'ACTIVATION': modules / 'activation.json',
                  'WRAPPER': modules / prepare.WRAPPER.name, 'GATE': modules / prepare.GATE.name,
                  'RECEIPT': modules / prepare.RECEIPT.name}
        return root, modules, state, values

    def test_disabled_storage_accepts_only_original_root_owned_layout(self):
        root, modules, state, values = self.storage()
        with patch.multiple(prepare, **values), patch.object(prepare, 'trusted') as trusted:
            prepare.check_storage()
            self.assertTrue(any(call.args == (state,) and call.kwargs == {'mode': 0o711}
                                for call in trusted.call_args_list))

    def test_existing_database_lock_key_and_gate_refused(self):
        for target in ['database', 'lock', 'key', 'activation', 'wrapper', 'extra_feed']:
            root, modules, state, values = self.storage()
            paths = {'database': state / 'reports.sqlite3', 'lock': state / 'publisher.lock',
                     'key': values['KEY'], 'activation': values['ACTIVATION'],
                     'wrapper': values['WRAPPER'], 'extra_feed': state / 'public/extra.json'}
            paths[target].write_bytes(b'fixture')
            with self.subTest(target=target), patch.multiple(prepare, **values), \
                    patch.object(prepare, 'trusted'), self.assertRaises(RuntimeError):
                prepare.check_storage()

    def test_installed_disabled_layout_requires_exact_new_files(self):
        root, modules, state, values = self.storage()
        for key in ['WRAPPER', 'GATE', 'RECEIPT']:
            values[key].write_bytes(b'fixture')
        with patch.multiple(prepare, **values), patch.object(prepare, 'trusted'):
            prepare.check_storage(installed=True)
            (modules / 'unexpected.pyc').write_bytes(b'fixture')
            with self.assertRaises(RuntimeError):
                prepare.check_storage(installed=True)


class MutationBoundaryTests(unittest.TestCase):
    def test_initial_check_failure_creates_nothing(self):
        with patch.object(prepare, 'check', side_effect=RuntimeError('stale')), \
                patch.object(prepare.tempfile, 'mkdtemp') as mkdir, patch.object(prepare, 'run') as run:
            with self.assertRaises(RuntimeError):
                prepare.prepare_disabled(manifest(), {})
            mkdir.assert_not_called()
            run.assert_not_called()

    def test_preparation_failure_never_removes_evidence_or_activates(self):
        before = {'files': {}, 'services': {}}
        backup = Path('/home/shini/homeenergy-production-backup.fixture')
        with patch.object(prepare, 'check', return_value=before), patch.object(prepare.os, 'umask'), \
                patch.object(prepare.tempfile, 'mkdtemp', return_value=str(backup)), \
                patch.object(prepare.os, 'chown', create=True), patch.object(prepare.os, 'chmod'), \
                patch.object(prepare, 'trusted'), patch.object(prepare, 'create_file') as create, \
                patch.object(prepare, 'account_absent'), patch.object(prepare, 'preserved_snapshot', return_value=before), \
                patch.object(prepare, 'run', side_effect=RuntimeError('group creation failed')) as run, \
                patch.object(prepare.os, 'unlink') as unlink:
            with redirect_stdout(io.StringIO()), self.assertRaises(RuntimeError):
                prepare.prepare_disabled(manifest(), {})
            unlink.assert_not_called()
            self.assertEqual(create.call_args.args[0], backup / 'baseline.json')
            self.assertEqual(run.call_args.args[0], ['/usr/sbin/groupadd', '--system', prepare.USER])

    def test_cli_has_no_activation_key_model_or_publication_mode(self):
        for flag in ['--activate', '--enable', '--publish', '--recover', '--install-key']:
            with self.subTest(flag=flag), patch.object(prepare.sys, 'argv', [str(HELPER), flag]), \
                    redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                prepare.main()
            self.assertEqual(error.exception.code, 2)

    def test_source_does_not_include_service_reload_or_key_creation(self):
        source = HELPER.read_text(encoding='utf-8')
        self.assertNotIn("'reload'", source)
        self.assertNotIn("'restart'", source)
        self.assertNotIn('create_file(KEY', source)
        self.assertNotIn('create_file(INCLUDE', source)
        self.assertNotIn('sqlite3.connect', source)
        self.assertNotIn("'SSH_ORIGINAL_COMMAND': 'publish'", source)


if __name__ == '__main__':
    unittest.main()
