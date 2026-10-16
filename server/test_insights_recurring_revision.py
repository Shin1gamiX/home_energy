"""Synthetic repair/logging regression tests: no live runtime, SSH or model."""
import base64
import hashlib
import json
import os
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import insights_recurring_revision as revision
import insights_recurring_run as runner
import insights_recurring_startup as startup
import insights_recurring_store as storage
from insights_broker import encode
from test_insights_recurring import BUNDLE, Fixture, NOW, context, grant


NEW_BUNDLE = 'e' * 64


class RepairFixture(Fixture):
    def setUp(self):
        super().setUp()
        self.complete()
        tip = storage.digest(self.store.read('terminal'))
        self.store.close()
        for name, value in {'LEGACY_BUNDLE': BUNDLE, 'LEGACY_RUNS': 1,
                            'LEGACY_AUTHORIZATION': storage.digest(grant()), 'LEGACY_TIP': tip}.items():
            self.enterContext(patch.object(revision, name, value))
        self.clock.return_value = NOW + storage.INTERVAL_SECONDS
        self.approval = revision.approval_body(NEW_BUNDLE, int(self.clock()))
        self.store_type = revision.revised_store(storage, NEW_BUNDLE, self.approval)

    def repaired(self, run_id=None):
        store = self.store_type(NEW_BUNDLE, run_id, files=self.files)
        self.addCleanup(store.close)
        return store


class RepairStoreTests(RepairFixture):
    def test_read_only_history_keeps_every_byte_and_original_budget(self):
        before = self.bodies()
        store = self.repaired()
        current_grant = store._grant()
        self.assertEqual(current_grant, grant())
        self.assertEqual(current_grant['max_runs'] - store.history(current_grant)['count'], 119)
        self.assertEqual(before, self.bodies())

    def test_next_run_continues_chain_with_new_bundle_without_rewriting_old_files(self):
        before = self.bodies()
        store = self.repaired()
        reservation = store.reserve()
        self.assertEqual(reservation['run_id'], '000002')
        receipt = store.read('receipt')
        self.assertEqual(receipt['bundle_sha256'], NEW_BUNDLE)
        self.assertEqual(receipt['authorization_sha256'], storage.digest(grant()))
        self.assertEqual(receipt['previous_terminal_sha256'], revision.LEGACY_TIP)
        self.assertEqual({k: self.bodies()[k] for k in before}, before)
        token = reservation['token']
        store.claim_worker(token)
        store.record('runtime_verified')
        store.arm(token)
        store.start_model(token)
        self.complete(store, token)
        store.close()
        self.clock.return_value += storage.INTERVAL_SECONDS
        third = self.repaired()
        self.assertEqual(third.reserve()['run_id'], '000003')
        self.assertEqual({k: self.bodies()[k] for k in before}, before)

    def test_expiry_is_not_extended(self):
        self.clock.return_value = grant()['expires_unix']
        before = self.bodies()
        with self.assertRaisesRegex(ValueError, 'authorization_expired'):
            self.repaired().reserve()
        self.assertEqual(before, self.bodies())

    def test_attempt_limit_cannot_be_reset_by_repair(self):
        with patch.object(storage, 'MAX_RUNS', 1):
            with self.assertRaisesRegex(ValueError, 'authorization_limits'):
                self.repaired().reserve()

    def test_original_grant_cannot_be_replaced_even_with_valid_new_limits(self):
        self.authorize(max_runs=119)
        with self.assertRaisesRegex(ValueError, 'repair_binding_changed'):
            self.repaired().reserve()

    def test_wrong_bundle_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'repair_binding_changed'):
            self.store_type('f' * 64, files=self.files)

    def test_changed_legacy_boundary_is_refused(self):
        with patch.object(revision, 'LEGACY_TIP', 'f' * 64):
            with self.assertRaisesRegex(ValueError, 'approval_changed|history_changed'):
                self.repaired().reserve()

    def test_corrupt_legacy_evidence_still_blocks_new_run(self):
        path = self.root / storage.filename('submission', '000001')
        value = json.loads(path.read_bytes())
        value['summary']['en'] += ' Changed synthetic evidence.'
        path.write_bytes(encode(value) + b'\n')
        with self.assertRaisesRegex(ValueError, 'evidence_changed'):
            self.repaired().reserve()

    def test_uncertain_repaired_run_blocks_next_attempt(self):
        store = self.repaired()
        token = store.reserve()['token']
        with self.assertRaisesRegex(ValueError, 'prior_run_needs_review'):
            store.finish(token, 'uncertain')
        store.close()
        self.clock.return_value += 86400
        with self.assertRaisesRegex(ValueError, 'prior_run_needs_review'):
            self.repaired().reserve()

    def test_forged_old_bundle_in_new_receipt_is_refused(self):
        store = self.repaired()
        token = store.reserve()['token']
        path = self.root / storage.filename('receipt', '000002')
        value = json.loads(path.read_bytes()) | {'bundle_sha256': BUNDLE}
        path.write_bytes(encode(value) + b'\n')
        with self.assertRaisesRegex(ValueError, 'receipt_changed'):
            store.claim_worker(token)


class ApprovalTests(RepairFixture):
    def test_closed_approval_schema(self):
        for field in self.approval:
            bad = dict(self.approval)
            bad.pop(field)
            with self.subTest(missing=field), self.assertRaises(ValueError):
                revision.validate_approval(bad, NEW_BUNDLE, grant(), storage)
        for changes in ({'schema_version': True}, {'legacy_runs': True},
                        {'requires_separate_activation': 1}, {'approved_unix': True},
                        {'approved_unix': int(self.clock()) + 1}, {'bundle_sha256': BUNDLE},
                        {'extra': 'private contents'}, {'legacy_terminal_sha256': '0' * 64}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                revision.validate_approval(self.approval | changes, NEW_BUNDLE, grant(), storage)

    def activation(self):
        return {'schema_version': 1, 'scope': 'recurring_runtime_activation',
                'approval_sha256': storage.digest(self.approval), 'bundle_sha256': NEW_BUNDLE,
                'activated_unix': int(self.clock())}

    def test_approval_alone_never_activates(self):
        self.assertFalse(revision.validate_activation(None, self.approval, NEW_BUNDLE, grant(), storage))

    def test_activation_requires_exact_approval_and_bundle(self):
        valid = self.activation()
        self.assertTrue(revision.validate_activation(valid, self.approval, NEW_BUNDLE, grant(), storage))
        for changes in ({'schema_version': True}, {'scope': 'other'}, {'bundle_sha256': BUNDLE},
                        {'approval_sha256': '0' * 64}, {'activated_unix': True},
                        {'activated_unix': int(self.clock()) + 1}, {'extra': True}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                revision.validate_activation(valid | changes, self.approval, NEW_BUNDLE, grant(), storage)
        with self.assertRaises(ValueError):
            revision.validate_activation(valid, None, NEW_BUNDLE, grant(), storage)


class HandoffTests(RepairFixture):
    def bind(self, run_id=None, token=None, *, armed=True):
        ctx = SimpleNamespace(original_storage=storage, bundle=NEW_BUNDLE)
        activation = {'schema_version': 1, 'scope': 'recurring_runtime_activation',
            'approval_sha256': storage.digest(self.approval), 'bundle_sha256': NEW_BUNDLE,
            'activated_unix': int(self.clock())}
        records = {startup.APPROVAL: self.approval, startup.ACTIVATION: activation}
        reader = SimpleNamespace(APPROVAL=startup.APPROVAL, ACTIVATION=startup.ACTIVATION,
                                 read_record=records.get)
        with patch.object(storage, 'Files', return_value=self.files):
            return revision.bind(ctx, reader, active_run=run_id, token=token, armed=armed)

    def test_worker_and_mcp_handoff_then_completed_history_without_rewriting_evidence(self):
        store = self.repaired()
        reservation = store.reserve()
        run_id, token = reservation['run_id'], reservation['token']
        before_worker = self.bodies()
        self.assertTrue(self.bind(run_id, token, armed=False)['repair_active'])
        self.assertEqual(before_worker, self.bodies())
        store.claim_worker(token)
        store.record('runtime_verified')
        store.arm(token)
        store.start_model(token)
        before_mcp = self.bodies()
        self.assertTrue(self.bind(run_id, token)['repair_active'])
        self.assertEqual(before_mcp, self.bodies())
        self.complete(store, token)
        store.close()
        self.assertEqual(self.bind()['completed_attempts'], 2)

    def test_pending_run_is_never_hidden_from_another_supervisor(self):
        store = self.repaired()
        store.reserve()
        before = self.bodies()
        with self.assertRaisesRegex(ValueError, 'prior_run_needs_review'):
            self.bind()
        self.assertEqual(before, self.bodies())

    def test_bad_handoff_token_and_unarmed_mcp_are_rejected_without_changes(self):
        store = self.repaired()
        reservation = store.reserve()
        before = self.bodies()
        for token, armed in (('f' * 64, False), (reservation['token'], True)):
            with self.subTest(armed=armed), self.assertRaises((ValueError, FileNotFoundError)):
                self.bind(reservation['run_id'], token, armed=armed)
        self.assertEqual(before, self.bodies())

    def test_handoff_rechecks_legacy_evidence(self):
        store = self.repaired()
        reservation = store.reserve()
        path = self.root / storage.filename('submission', '000001')
        value = json.loads(path.read_bytes())
        value['summary']['en'] += ' Synthetic changed content.'
        path.write_bytes(encode(value) + b'\n')
        before = self.bodies()
        with self.assertRaisesRegex(ValueError, 'evidence_changed'):
            self.bind(reservation['run_id'], reservation['token'], armed=False)
        self.assertEqual(before, self.bodies())

    def test_unrelated_pending_run_is_not_filtered_out(self):
        store = self.repaired()
        reservation = store.reserve()
        self.files.create(storage.filename('receipt', '000003'), store.read('receipt'))
        with self.assertRaisesRegex(ValueError, 'prior_run_needs_review'):
            self.bind(reservation['run_id'], reservation['token'], armed=False)

    def test_handoff_receipt_must_continue_exact_chain(self):
        store = self.repaired()
        reservation = store.reserve()
        path = self.root / storage.filename('receipt', reservation['run_id'])
        value = json.loads(path.read_bytes()) | {'previous_terminal_sha256': 'f' * 64}
        path.write_bytes(encode(value) + b'\n')
        with self.assertRaisesRegex(ValueError, 'handoff_changed'):
            self.bind(reservation['run_id'], reservation['token'], armed=False)


class RuntimePinTests(unittest.TestCase):
    def runtime(self):
        old = Path('C:/synthetic-old/codex.exe')
        runtime = SimpleNamespace(CODEX=old, VERSION='old',
            USER_CONFIG=Path('C:/synthetic/config.toml'), PYTHON=Path('C:/synthetic/python.exe'),
            FILE_PINS={old: 'old', Path('C:/synthetic/AGENTS.md'): 'preserved'}, plain_file=Mock())
        body = b'fixed synthetic runtime'
        pin = hashlib.sha256(body).hexdigest()
        for name in ('CONFIG_SHA256', 'PYTHON_SHA256', 'PYTHONW_SHA256'):
            self.enterContext(patch.object(revision, name, pin))
        self.enterContext(patch.object(revision, 'BINARIES', {name: pin for name in revision.BINARIES}))
        self.enterContext(patch.object(Path, 'is_file', return_value=True))
        self.enterContext(patch.object(Path, 'stat', return_value=SimpleNamespace(st_nlink=1)))
        self.enterContext(patch.object(Path, 'read_bytes', return_value=body))
        return runtime

    def test_exact_reviewed_metadata_only_and_all_companions_checked(self):
        runtime = self.runtime()
        old = runtime.CODEX
        revision.configure_runtime(runtime)
        self.assertEqual(runtime.VERSION, revision.VERSION)
        self.assertEqual(runtime.CODEX, revision.CODEX)
        self.assertNotIn(old, runtime.FILE_PINS)
        self.assertEqual(runtime.FILE_PINS[Path('C:/synthetic/AGENTS.md')], 'preserved')
        self.assertEqual(runtime.plain_file.call_count, 7)

    def test_missing_or_multilink_runtime_is_rejected(self):
        runtime = self.runtime()
        with patch.object(Path, 'is_file', return_value=False):
            with self.assertRaisesRegex(ValueError, 'runtime_file_missing'):
                revision.configure_runtime(runtime)
        current = self.runtime()
        with patch.object(Path, 'stat', return_value=SimpleNamespace(st_nlink=2)):
            with self.assertRaises(ValueError):
                revision.configure_runtime(current)

    def test_each_reviewed_file_change_is_refused_with_fixed_code(self):
        runtime = self.runtime()
        paths = {runtime.USER_CONFIG: 'runtime_config_changed',
            runtime.PYTHON: 'runtime_python_changed',
            runtime.PYTHON.with_name('pythonw.exe'): 'runtime_python_changed',
            **{revision.CODEX.parent / name: ('runtime_file_changed' if name == 'codex.exe'
                else 'runtime_companion_changed') for name in revision.BINARIES}}
        for path, code in paths.items():
            current = self.runtime()
            def read_bytes(candidate):
                return b'changed' if candidate == path else b'fixed synthetic runtime'
            with self.subTest(path=path.name), patch.object(Path, 'read_bytes', read_bytes):
                with self.assertRaisesRegex(ValueError, code):
                    revision.configure_runtime(current)


class StartupLoggingTests(unittest.TestCase):
    def test_only_allowlisted_error_codes_are_logged(self):
        for error in (OSError('PRIVATE KEY'), ValueError('password_example'),
                      ValueError('token=secret'), ValueError('private', 'contents')):
            row = startup.error_record(error, 'check', 'bootstrap')
            self.assertEqual(row['code'], 'unexpected_failure')
            self.assertNotIn('secret', json.dumps(row))
            self.assertNotIn('PRIVATE', json.dumps(row))
        row = startup.error_record(ValueError('runtime_config_changed'), 'run-due', 'bootstrap')
        self.assertEqual(row['code'], 'runtime_config_changed')
        self.assertFalse(row['synthetic'])

    def test_arbitrary_mode_and_phase_are_not_logged(self):
        row = startup.error_record(ValueError('SECRET'), 'SECRET', 'SECRET')
        self.assertEqual(row['mode'], 'unknown')
        self.assertEqual(row['phase'], 'bootstrap')
        self.assertNotIn('SECRET', json.dumps(row))

    def test_log_is_bounded_and_create_once_named(self):
        with patch.object(startup, 'private_io', return_value=b'saved') as writer:
            first = startup.record_failure(ValueError('runtime_file_missing'), 'run-due', 'bootstrap')
            second = startup.record_failure(ValueError('synthetic_startup_check'), 'check', 'bootstrap', synthetic=True)
        self.assertNotEqual(first, second)
        for call in writer.call_args_list:
            request = call.args[0]
            self.assertRegex(request['name'], r'^startup-\d{8}T\d{12}Z-[a-f0-9]{16}\.json$')
            self.assertLess(len(request['body']), 2048)
            self.assertEqual(set(json.loads(request['body'])),
                {'schema_version', 'timestamp_utc', 'event', 'mode', 'phase', 'code', 'synthetic'})
        self.assertTrue(json.loads(writer.call_args_list[-1].args[0]['body'])['synthetic'])

    def test_private_io_failure_is_not_hidden(self):
        with patch.object(startup, 'private_io', return_value=b'bad'):
            with self.assertRaisesRegex(ValueError, 'startup_log_unavailable'):
                startup.record_failure(ValueError('runtime_config_changed'), 'check', 'bootstrap')

    def test_read_record_accepts_only_fixed_names_and_canonical_json(self):
        with patch.object(startup, 'private_io') as reader:
            with self.assertRaises(ValueError):
                startup.read_record('id_ed25519')
            reader.assert_not_called()
            reader.return_value = b'missing'
            self.assertIsNone(startup.read_record(startup.APPROVAL))
            for raw in (b'{"a":1,"a":2}\n', b'{ "a": 1 }\n', b'{"a":1}\nmore'):
                reader.return_value = base64.b64encode(raw)
                with self.subTest(raw=raw), self.assertRaises(ValueError):
                    startup.read_record(startup.APPROVAL)
            reader.return_value = base64.b64encode(b'{"a":1}\n')
            self.assertEqual(startup.read_record(startup.APPROVAL), {'a': 1})

    def test_writer_has_no_overwrite_or_delete_path(self):
        script = startup.PRIVATE_IO_SCRIPT
        self.assertIn('[IO.FileMode]::CreateNew', script)
        self.assertIn('$stream.GetAccessControl()', script)
        self.assertIn('-ge 1024', script)
        for unsafe in ('Invoke-Expression', 'Remove-Item', 'Set-Content', 'WriteAllText', '::Create,', '::Truncate'):
            self.assertNotIn(unsafe, script)


class RunnerRepairTests(unittest.TestCase):
    def test_new_sources_and_old_sources_remain_pinned(self):
        for name, pin in (('insights_recurring_revision', runner.REVISION_PIN),
                          ('insights_recurring_startup', runner.STARTUP_PIN),
                          ('insights_recurring_recovery', runner.RECOVERY_PIN),
                          ('insights_recurring_exception', runner.EXCEPTION_PIN),
                          ('insights_recurring_store', runner.STORE_PIN),
                          ('insights_production_trial_run', runner.TRIAL_PIN)):
            with self.subTest(name=name):
                self.assertEqual(hashlib.sha256((runner.SOURCE / (name + '.py')).read_bytes()).hexdigest(), pin)

    def test_no_model_session_explicitly_rejects_generation(self):
        cls = runner.no_model_session_class(context())
        session = cls.__new__(cls)
        with self.assertRaisesRegex(ValueError, 'no_model_only'):
            session.send({'method': 'turn/start', 'id': 1, 'params': {}})

    def test_live_entrypoints_are_held_without_activation(self):
        for mode in ('run-due', 'run-exception', 'worker', 'mcp-report'):
            ctx = context()
            ctx.repair_active = False
            ctx.exception.bind = Mock(return_value={'repair_active': False})
            with self.subTest(mode=mode), patch.object(runner, 'bootstrap', return_value=ctx), \
                    patch.object(runner, 'load_support', return_value=Mock()), \
                    patch.object(runner, 'supervise') as supervisor, patch.object(runner, 'worker') as worker, \
                    patch.object(runner, 'serve') as serve, patch('sys.argv', ['runner', '--' + mode]), \
                    patch.dict(os.environ, {runner.TOKEN_ENV: 'a' * 64}):
                with self.assertRaisesRegex(ValueError, 'not_activated'):
                    runner.main()
                supervisor.assert_not_called()
                worker.assert_not_called()
                serve.assert_not_called()

    def test_static_check_uses_validated_state_without_reserving(self):
        ctx = context()
        ctx.repair_status = {'completed_attempts': 2, 'remaining_attempts': 118, 'repair_active': False}
        with patch.object(ctx.storage, 'Store') as store:
            result = runner.no_model_check(ctx, 'check')
        store.assert_not_called()
        self.assertEqual(result['remaining_attempts'], 118)
        self.assertFalse(result['model_started'])
        self.assertFalse(result['report_calls_enabled'])


if __name__ == '__main__':
    unittest.main()
