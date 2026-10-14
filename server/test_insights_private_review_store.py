"""Offline private-review storage tests; only disposable fixtures are written."""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import insights_private_review_store as storage
from insights_broker import encode


def snapshot(**changes):
    analysis = {'schema_version': 2, 'example': 'Only synthetic test data'}
    value = {'schema_version': 1, 'scope': 'private_review', 'synthetic': False,
             'captured_at': 1000, 'analysis_id': hashlib.sha256(encode(analysis)).hexdigest(),
             'analysis': analysis}
    value.update(changes)
    return value


def artifact(**changes):
    value = {'schema_version': 1, 'scope': 'private_review', 'synthetic': False,
             'example': 'Τοπικό · Частный · Synthetic fixture'}
    value.update(changes)
    return value


class LocalFixture(unittest.TestCase):
    def setUp(self):
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(temporary)
        self.paths = {key: self.root / path.name for key, path in storage.FILES.items()}
        self.enterContext(patch.object(storage, 'ROOT', self.root))
        self.enterContext(patch.object(storage, 'FILES', self.paths))
        self.root_check = self.enterContext(patch.object(storage, '_root_check'))
        self.clock = self.enterContext(patch.object(storage.time, 'time', return_value=1000))

        def open_fixture(kind, *, create=False, write=False):
            return self.paths[kind].open('x+b' if create else ('r+b' if write else 'rb'), buffering=0)
        self.open = self.enterContext(patch.object(storage, '_open', side_effect=open_fixture))
        self.store = storage.Store()
        self.addCleanup(self.store.close)

    def reserve(self):
        value = snapshot()
        self.capture()
        self.store.create('snapshot', value)
        digest = hashlib.sha256(encode(value)).hexdigest()
        return self.store.reserve(digest)

    def capture(self):
        self.store.create('capture', {'schema_version': 1, 'scope': 'private_review',
                                     'synthetic': False, 'created_at': 1000})

    def ready(self):
        token = self.reserve()
        self.store.arm(token)
        self.store.claim(token)
        return token

    def bodies(self):
        return {key: path.read_bytes() for key, path in self.paths.items() if path.exists()}


class StoreTests(LocalFixture):
    def test_all_paths_have_new_fixed_prefix_only(self):
        self.assertEqual(set(self.paths), {'capture', 'snapshot', 'receipt', 'armed', 'claim',
                                         'terminal', 'audit', 'result', 'diagnostic'})
        for kind, path in self.paths.items():
            extension = '.jsonl' if kind == 'audit' else '.json'
            self.assertEqual(path.name, 'private-review-01-' + kind + extension)

    def test_old_staging_evidence_and_canary_remain_untouched(self):
        old = {self.root / name: b'preserved-evidence' for name in (
            'one-shot-receipt.json', 'one-shot-02-receipt.json',
            'one-shot-03-receipt.json', 'one-shot-04-receipt.json',
            'one-shot-storage-check.json')}
        for path, body in old.items():
            path.write_bytes(body)
        token = self.ready()
        self.store.finish(token)
        self.assertEqual(old, {path: path.read_bytes() for path in old})

    def test_snapshot_is_exact_canonical_utf8_and_hash_bound(self):
        token = self.reserve()
        body = self.paths['snapshot'].read_bytes()
        self.assertEqual(body, encode(snapshot()) + b'\n')
        receipt = self.store.read('receipt')
        self.assertEqual(receipt['snapshot_sha256'], hashlib.sha256(encode(snapshot())).hexdigest())
        self.assertEqual(receipt['scope'], 'private_review')
        self.assertIs(receipt['synthetic'], False)
        self.assertEqual(receipt['created_unix'], 1000)
        self.assertEqual(receipt['token_sha256'], hashlib.sha256(token.encode('ascii')).hexdigest())
        self.assertNotIn(token.encode('ascii'), self.paths['receipt'].read_bytes())

    def test_result_requires_claim_and_armed_receipt(self):
        token = self.reserve()
        with self.assertRaises(storage.StoreFailure):
            self.store.create('result', artifact())
        self.store.arm(token)
        with self.assertRaises(storage.StoreFailure):
            self.store.create('result', artifact())
        self.store.claim(token)
        self.store.create('result', artifact())
        self.assertEqual(self.store.read('result'), artifact())
        self.assertIn('Τοπικό'.encode('utf-8'), self.paths['result'].read_bytes())

    def test_snapshot_cannot_be_replaced_before_or_after_reserve(self):
        token = self.reserve()
        before = self.bodies()
        with self.assertRaises(storage.StoreFailure):
            self.store.create('snapshot', snapshot(captured_at=1001))
        self.assertEqual(before, self.bodies())
        self.assertTrue(self.store.active(token, armed=False))

    def test_result_and_diagnostic_are_create_once(self):
        self.ready()
        for kind in ('result', 'diagnostic'):
            self.store.create(kind, artifact())
            before = self.paths[kind].read_bytes()
            with self.assertRaises(storage.StoreFailure):
                self.store.create(kind, artifact(example='replacement'))
            self.assertEqual(self.paths[kind].read_bytes(), before)

    def test_repeated_reservation_keeps_original_evidence(self):
        self.reserve()
        before = self.bodies()
        with self.assertRaises(storage.StoreFailure):
            self.store.reserve(hashlib.sha256(encode(snapshot())).hexdigest())
        self.assertEqual(before, self.bodies())

    def test_partial_reservation_consumes_slot_before_audit_creation(self):
        self.paths['audit'].write_bytes(b'pre-existing data')
        with self.assertRaises(storage.StoreFailure):
            self.reserve()
        self.assertTrue(self.paths['receipt'].exists())
        self.assertEqual(self.paths['audit'].read_bytes(), b'pre-existing data')
        before = self.bodies()
        with self.assertRaises(storage.StoreFailure):
            self.store.reserve(hashlib.sha256(encode(snapshot())).hexdigest())
        self.assertEqual(before, self.bodies())

    def test_mismatched_snapshot_hash_never_consumes_receipt(self):
        self.capture()
        self.store.create('snapshot', snapshot())
        with self.assertRaises(storage.StoreFailure):
            self.store.reserve('0' * 64)
        self.assertFalse(self.paths['receipt'].exists())
        self.assertFalse(self.paths['audit'].exists())

    def test_invalid_tokens_fail_without_arming(self):
        self.reserve()
        for value in ('a' * 64, 'INVALID', '', None, True, 4, '../path'):
            with self.subTest(value_type=type(value)), self.assertRaises(storage.StoreFailure):
                self.store.arm(value)
        self.assertFalse(self.paths['armed'].exists())

    def test_expiry_and_future_times_rejected_at_exact_boundary(self):
        token = self.reserve()
        for now in (999, 1360, 2000):
            self.clock.return_value = now
            with self.subTest(now=now), self.assertRaises(storage.StoreFailure):
                self.store.active(token, armed=False)
        self.clock.return_value = 1359.99
        self.assertTrue(self.store.active(token, armed=False))

    def test_arm_and_claim_cannot_repeat(self):
        token = self.reserve()
        self.store.arm(token)
        before = self.paths['armed'].read_bytes()
        with self.assertRaises(storage.StoreFailure):
            self.store.arm(token)
        self.assertEqual(self.paths['armed'].read_bytes(), before)
        self.store.claim(token)
        before = self.paths['claim'].read_bytes()
        with self.assertRaises(storage.StoreFailure):
            self.store.claim(token)
        self.assertEqual(self.paths['claim'].read_bytes(), before)

    def test_fresh_process_can_claim_same_bound_slot_once(self):
        token = self.reserve()
        self.store.arm(token)
        child = storage.Store()
        self.addCleanup(child.close)
        child.claim(token)
        child.record('prepared')
        child.create('result', artifact())
        self.assertEqual(self.store.read('result'), artifact())
        with self.assertRaises(storage.StoreFailure):
            self.store.claim(token)

    def test_snapshot_tampering_prevents_activation(self):
        token = self.reserve()
        self.paths['snapshot'].write_bytes(encode(snapshot(captured_at=1001)))
        with self.assertRaises(storage.StoreFailure):
            self.store.active(token, armed=False)

    def test_synthetic_wrong_scope_or_invalid_analysis_hash_rejected(self):
        for value in (snapshot(synthetic=True), snapshot(scope='staging'),
                      snapshot(analysis_id='0' * 64), snapshot(captured_at=True),
                      snapshot(schema_version=True), snapshot(extra='unexpected')):
            with self.subTest(keys=set(value)), self.assertRaises(storage.StoreFailure):
                self.store.create('snapshot', value)
        self.assertFalse(self.paths['snapshot'].exists())

    def test_all_data_artifacts_require_real_review_markers(self):
        self.ready()
        for kind in ('result', 'diagnostic'):
            for changes in ({'synthetic': True}, {'scope': 'staging'}, {'schema_version': True}):
                with self.subTest(kind=kind, changes=changes), self.assertRaises(storage.StoreFailure):
                    self.store.create(kind, artifact(**changes))
            self.assertFalse(self.paths[kind].exists())

    def test_oversized_nonfinite_nonjson_data_rejected(self):
        self.ready()
        for value in (artifact(example='x' * 65536), artifact(example=float('nan')),
                      artifact(example=float('inf')), artifact(example={3: 'coerced key'}),
                      artifact(example=object())):
            with self.subTest(value_type=type(value['example'])), self.assertRaises(storage.StoreFailure):
                self.store.create('result', value)
        self.assertFalse(self.paths['result'].exists())

    def test_data_read_rejects_duplicate_keys_nan_and_trailing_content(self):
        self.ready()
        for body in (b'{"scope":"private_review","scope":"private_review"}',
                     b'{"v":NaN}', b'{} {}', b'x' * 65537):
            self.paths['diagnostic'].write_bytes(body)
            with self.assertRaises(storage.StoreFailure):
                self.store.read('diagnostic')

    def test_marker_validation_rejects_shape_identity_and_scope_changes(self):
        token = self.reserve()
        original = json.loads(self.paths['receipt'].read_bytes())
        for changes in ({'synthetic': True}, {'scope': 'staging'}, {'schema_version': True},
                        {'created_unix': True}, {'created_unix': -1}, {'extra': 'text'},
                        {'token_sha256': 'a' * 64}, {'snapshot_sha256': 'b' * 64}):
            value = dict(original, **changes)
            self.paths['receipt'].write_bytes(encode(value))
            with self.subTest(changes=changes), self.assertRaises(storage.StoreFailure):
                self.store.active(token, armed=False)

    def test_marker_binding_prevents_other_snapshot_or_token_claim(self):
        token = self.ready()
        marker = self.store.read('claim')
        for changes in ({'token_sha256': 'a' * 64}, {'snapshot_sha256': 'b' * 64}):
            self.paths['claim'].write_bytes(encode(dict(marker, **changes)))
            with self.subTest(changes=changes), self.assertRaises(storage.StoreFailure):
                self.store.create('result', artifact())

    def test_unknown_paths_and_direct_marker_creation_rejected(self):
        for kind in ('../receipt', 'one-shot-04-receipt', '', None, 7):
            for operation in (self.store.exists, self.store.read):
                with self.subTest(kind=kind), self.assertRaises(storage.StoreFailure):
                    operation(kind)
        for kind in ('receipt', 'armed', 'claim', 'terminal', 'audit'):
            with self.subTest(kind=kind), self.assertRaises(storage.StoreFailure):
                self.store.create(kind, artifact())
        self.assertEqual(self.bodies(), {})

    def test_terminal_is_permanent_idempotent_and_preserves_read_access(self):
        token = self.ready()
        self.store.create('result', artifact())
        self.store.finish(token)
        before = self.bodies()
        self.store.finish(token)
        self.assertEqual(self.bodies(), before)
        self.assertEqual(self.store.read('result'), artifact())
        self.assertTrue(self.store.exists('terminal'))
        for operation in (lambda: self.store.active(token), lambda: self.store.claim(token),
                          lambda: self.store.arm(token), lambda: self.store.record('prepared'),
                          lambda: self.store.create('diagnostic', artifact())):
            with self.assertRaises(storage.StoreFailure):
                operation()
        self.assertEqual(self.bodies(), before)

    def test_diagnostic_and_finish_allowed_after_expiry(self):
        token = self.reserve()
        self.clock.return_value = 2000
        self.store.create('diagnostic', artifact())
        self.store.finish(token)
        self.assertEqual(self.store.read('diagnostic'), artifact())
        self.assertTrue(self.store.exists('terminal'))

    def test_close_disables_all_subsequent_io(self):
        self.store.close()
        self.store.close()
        for operation in (lambda: self.store.exists('snapshot'), lambda: self.store.read('snapshot'),
                          lambda: self.store.create('snapshot', snapshot()),
                          lambda: self.store.reserve('a' * 64),
                          lambda: self.store.finish('a' * 64)):
            with self.assertRaises(storage.StoreFailure):
                operation()
        self.open.assert_not_called()

    def test_root_denial_does_not_open_files(self):
        self.root_check.side_effect = OSError('sensitive directory information')
        with self.assertRaisesRegex(storage.StoreFailure, '^private_review_store_rejected$'):
            storage.Store()
        self.open.assert_not_called()

    def test_error_output_never_includes_os_details(self):
        self.open.side_effect = OSError('secret token and private path')
        with self.assertRaisesRegex(storage.StoreFailure, '^private_review_store_rejected$'):
            self.store.create('snapshot', snapshot())

    def test_each_write_flushes_and_fsyncs_before_return(self):
        with patch.object(storage.os, 'fsync', wraps=os.fsync) as sync:
            self.ready()
            self.assertGreaterEqual(sync.call_count, 6)

    def test_capture_is_exact_private_envelope_and_create_once(self):
        self.capture()
        value = self.store.read('capture')
        self.assertEqual(value, {'schema_version': 1, 'scope': 'private_review',
                                 'synthetic': False, 'created_at': 1000})
        before = self.paths['capture'].read_bytes()
        with self.assertRaises(storage.StoreFailure):
            self.capture()
        self.assertEqual(self.paths['capture'].read_bytes(), before)

    def test_snapshot_requires_prior_valid_capture(self):
        with self.assertRaises(storage.StoreFailure):
            self.store.create('snapshot', snapshot())
        self.assertFalse(self.paths['snapshot'].exists())
        self.capture()
        self.store.create('snapshot', snapshot())
        self.assertEqual(self.store.read('snapshot'), snapshot())

    def test_capture_rejects_wrong_shape_and_timestamp(self):
        value = {'schema_version': 1, 'scope': 'private_review', 'synthetic': False,
                 'created_at': 1000}
        for changes in ({'created_at': True}, {'created_at': -1}, {'extra': 'text'},
                        {'synthetic': True}, {'scope': 'staging'}):
            with self.subTest(changes=changes), self.assertRaises(storage.StoreFailure):
                self.store.create('capture', dict(value, **changes))
        self.assertFalse(self.paths['capture'].exists())

    def test_capture_rejected_after_snapshot_or_reservation_even_when_missing(self):
        self.reserve()
        # Fixture-only removal models an incomplete/tampered slot, never repair it.
        self.paths['capture'].unlink()
        with self.assertRaises(storage.StoreFailure):
            self.capture()
        self.assertFalse(self.paths['capture'].exists())


class AuditTests(LocalFixture):
    def test_only_fixed_events_and_timestamps_are_persisted(self):
        token = self.reserve()
        self.store.record('runtime_verified')
        rows = self.store.read('audit')
        self.assertEqual([row['event'] for row in rows], ['reserved', 'runtime_verified'])
        for row in rows:
            self.assertEqual(set(row), {'timestamp_utc', 'event'})
            self.assertNotIn(token, encode(row).decode('utf-8'))

    def test_unknown_event_poisoned_but_terminal_still_written(self):
        token = self.reserve()
        before = self.paths['audit'].read_bytes()
        with self.assertRaises(storage.StoreFailure):
            self.store.record('arbitrary sensitive text')
        with self.assertRaises(storage.StoreFailure):
            self.store.record('prepared')
        self.store.finish(token)
        self.assertEqual(self.paths['audit'].read_bytes(), before)
        self.assertTrue(self.paths['terminal'].exists())

    def test_count_limit_stops_append_without_truncation(self):
        token = self.reserve()
        for unused in range(storage.MAX_AUDIT_EVENTS - 1):
            self.store.record('runtime_verified')
        before = self.paths['audit'].read_bytes()
        with self.assertRaises(storage.StoreFailure):
            self.store.record('prepared')
        self.assertEqual(self.paths['audit'].read_bytes(), before)
        self.store.finish(token)
        self.assertTrue(self.paths['terminal'].exists())

    def test_malformed_existing_event_never_repaired(self):
        token = self.reserve()
        self.paths['audit'].write_bytes(b'{"event":"unexpected","timestamp_utc":"secret"}\n')
        before = self.paths['audit'].read_bytes()
        with self.assertRaises(storage.StoreFailure):
            self.store.record('prepared')
        self.assertEqual(self.paths['audit'].read_bytes(), before)
        self.store.finish(token)
        self.assertTrue(self.paths['terminal'].exists())

    def test_missing_newline_oversize_and_extra_keys_rejected_on_read(self):
        self.reserve()
        for body in (b'{}', b'x' * 8193,
                     b'{"event":"prepared","timestamp_utc":"2026-10-14T01:00:00+00:00","raw":"secret"}\n'):
            self.paths['audit'].write_bytes(body)
            with self.assertRaises(storage.StoreFailure):
                self.store.read('audit')

    def test_two_sequential_processes_preserve_each_others_append(self):
        token = self.reserve()
        self.store.arm(token)
        child = storage.Store()
        self.addCleanup(child.close)
        child.claim(token)
        child.record('prepared')
        self.store.record('model_completed')
        self.assertEqual([row['event'] for row in self.store.read('audit')],
                         ['reserved', 'prepared', 'model_completed'])

    def test_concurrent_sharing_error_fails_closed_without_retry(self):
        token = self.reserve()
        original_open = self.open.side_effect
        calls = []

        def sharing_error(kind, **kwargs):
            if kind == 'audit':
                calls.append(kind)
                raise PermissionError('sharing violation private filename')
            return original_open(kind, **kwargs)
        self.open.side_effect = sharing_error
        before = self.paths['audit'].read_bytes()
        with self.assertRaisesRegex(storage.StoreFailure, '^private_review_store_rejected$'):
            self.store.record('prepared')
        self.assertEqual(calls, ['audit'])
        self.assertEqual(self.paths['audit'].read_bytes(), before)
        self.store.finish(token)
        self.assertTrue(self.paths['terminal'].exists())

    def test_fsync_failure_poisoned_and_no_log_repair(self):
        token = self.reserve()
        with patch.object(storage.os, 'fsync', side_effect=OSError('private filesystem detail')):
            with self.assertRaisesRegex(storage.StoreFailure, '^private_review_store_rejected$'):
                self.store.record('prepared')
        self.assertTrue(self.store.failed)
        before = self.paths['audit'].read_bytes()
        self.store.finish(token)
        self.assertEqual(self.paths['audit'].read_bytes(), before)


@unittest.skipUnless(os.name == 'nt', 'Windows handle fixture')
class NativeOpenTests(unittest.TestCase):
    def setUp(self):
        import msvcrt
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(temporary)
        self.path = self.root / 'private-review-01-audit.jsonl'
        self.path.write_bytes(b'')
        self.enterContext(patch.object(storage, 'ROOT', self.root))
        self.enterContext(patch.object(storage, 'FILES', {'audit': self.path}))
        self.kernel = MagicMock()
        self.enterContext(patch.object(ctypes, 'WinDLL', return_value=self.kernel))
        self.kernel.CreateFileW.return_value = 77
        self.attributes, self.links = 0, 1
        self.canonical = '\\\\?\\' + str(self.path)

        def info(handle, output):
            output._obj.attributes = self.attributes
            output._obj.links = self.links
            return 1
        self.kernel.GetFileInformationByHandle.side_effect = info

        def canonical(handle, output, length, flags):
            output.value = self.canonical
            return len(self.canonical)
        self.kernel.GetFinalPathNameByHandleW.side_effect = canonical
        self.check = self.enterContext(patch.object(storage, '_check_handle_acl'))
        self.transfer = self.enterContext(patch.object(msvcrt, 'open_osfhandle',
            side_effect=lambda handle, flags: os.open(self.path, os.O_RDWR | os.O_BINARY)))

    def test_create_new_exclusive_nofollow_write_through_and_noinherit(self):
        with storage._open('audit', create=True, write=True):
            pass
        args = self.kernel.CreateFileW.call_args.args
        self.assertEqual(args[2], 0)
        self.assertEqual(args[4], 1)
        self.assertEqual(args[5], 0x00200000 | 0x80000000)
        self.assertTrue(self.transfer.call_args.args[1] & os.O_NOINHERIT)
        self.check.assert_called_once_with(77)
        self.kernel.CloseHandle.assert_not_called()

    def test_existing_read_never_requests_write_or_truncation(self):
        with storage._open('audit'):
            pass
        args = self.kernel.CreateFileW.call_args.args
        self.assertEqual(args[1], 0x80000000)
        self.assertEqual(args[4], 3)

    def test_reparse_directory_hardlink_reject_before_handle_transfer(self):
        for attributes, links in ((0x400, 1), (0x10, 1), (0, 2), (0, 0)):
            self.attributes, self.links = attributes, links
            with self.subTest(attributes=attributes, links=links), self.assertRaises(storage.StoreFailure):
                storage._open('audit')
        self.transfer.assert_not_called()
        self.assertEqual(self.kernel.CloseHandle.call_count, 4)

    def test_mismatched_canonical_path_rejected(self):
        self.canonical = '\\\\?\\C:\\outside\\private-review-01-audit.jsonl'
        with self.assertRaises(storage.StoreFailure):
            storage._open('audit')
        self.transfer.assert_not_called()
        self.kernel.CloseHandle.assert_called_once_with(77)

    def test_acl_denial_closes_handle_without_transfer(self):
        self.check.side_effect = ValueError('test ACL denial')
        with self.assertRaises(ValueError):
            storage._open('audit')
        self.transfer.assert_not_called()
        self.kernel.CloseHandle.assert_called_once_with(77)

    def test_failed_native_open_is_fixed_error(self):
        self.kernel.CreateFileW.return_value = ctypes.c_void_p(-1).value
        with self.assertRaisesRegex(storage.StoreFailure, '^private_review_store_rejected$'):
            storage._open('audit')
        self.transfer.assert_not_called()
        self.kernel.CloseHandle.assert_not_called()

    def test_unknown_file_kind_cannot_reach_windows_api(self):
        with self.assertRaises(storage.StoreFailure):
            storage._open('../outside')
        self.kernel.CreateFileW.assert_not_called()


if __name__ == '__main__':
    unittest.main()
