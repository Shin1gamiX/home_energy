"""Offline one-shot receipt/audit regressions; no real staging material used."""
import ctypes
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import insights_staging_run_audit as audit


def event(**changes):
    value = {'timestamp_utc': '2026-10-14T12:00:00+00:00',
             'event': 'staging_adapter', 'attempt': 1,
             'method': 'prepare', 'outcome': 'accepted'}
    value.update(changes)
    return value


class LocalFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.paths = {key: Path(self.temporary) / value.name for key, value in audit.FILES.items()}
        self.enterContext(patch.object(audit, 'FILES', self.paths))
        self.root_check = self.enterContext(patch.object(audit, '_root_check'))
        self.clock = self.enterContext(patch.object(audit.time, 'time', return_value=1000))

        def open_fixture(kind, *, create=False, write=False):
            return self.paths[kind].open('x+b' if create else ('r+b' if write else 'rb'), buffering=0)
        self.open = self.enterContext(patch.object(audit, '_open', side_effect=open_fixture))

    def reserve(self):
        reservation = audit.reserve_run()
        self.addCleanup(reservation.close)
        return reservation

    def ready(self):
        reservation = self.reserve()
        audit.arm_run(reservation.token)
        return reservation


class ReservationTests(LocalFixture):
    def test_fourth_slot_has_fixed_names_and_preserves_all_three_prior_attempts(self):
        expected = {
            'receipt': 'one-shot-04-receipt.json', 'armed': 'one-shot-04-armed.json',
            'claim': 'one-shot-04-mcp-claim.json', 'terminal': 'one-shot-04-terminal.json',
            'audit': 'one-shot-04-mcp-audit.jsonl', 'supervisor': 'one-shot-04-supervisor.jsonl',
            'result': 'one-shot-04-result.json', 'diagnostic': 'one-shot-04-diagnostic.json',
            'rejections': 'one-shot-04-rejections.jsonl',
            'probe': 'one-shot-storage-check.json',
        }
        self.assertEqual({key: path.name for key, path in self.paths.items()}, expected)
        originals = {}
        for key, name in expected.items():
            if key != 'probe':
                for prefix in ('one-shot-', 'one-shot-02-', 'one-shot-03-'):
                    path = Path(self.temporary) / name.replace('one-shot-04-', prefix)
                    originals[path] = ('Preserved synthetic evidence: ' + prefix + key).encode('ascii')
                    path.write_bytes(originals[path])
        reservation = self.ready()
        audit.disarm_run(reservation.token)
        self.assertEqual(originals, {path: path.read_bytes() for path in originals})
        current = {path: path.read_bytes() for path in self.paths.values() if path.exists()}
        with self.assertRaises(FileExistsError):
            audit.reserve_run()
        self.assertEqual(current, {path: path.read_bytes() for path in current})
        self.assertEqual(originals, {path: path.read_bytes() for path in originals})

    def test_native_storage_canary_has_no_model_receipt_and_is_retained(self):
        result = audit.check_private_storage()
        self.assertEqual(result, {'private_storage_verified': True, 'receipt_created': False,
                                  'model_started': False})
        self.assertEqual(self.paths['probe'].read_bytes(), audit.STORAGE_PROBE_BODY)
        self.assertEqual([value.name for value in Path(self.temporary).iterdir()], [self.paths['probe'].name])
        self.open.reset_mock()
        audit.check_private_storage()
        self.assertEqual(self.open.call_count, 1)
        self.assertEqual(self.open.call_args.args, ('probe',))
        self.assertEqual(self.open.call_args.kwargs, {})

    def test_existing_invalid_storage_canary_not_overwritten(self):
        self.paths['probe'].write_bytes(b'unexpected')
        with self.assertRaises(audit.AuditFailure):
            audit.check_private_storage()
        self.assertEqual(self.paths['probe'].read_bytes(), b'unexpected')
        self.assertFalse(self.paths['receipt'].exists())

    def test_reserve_creates_only_permanent_receipt_and_empty_logs(self):
        reservation = self.reserve()
        self.root_check.assert_called_once_with()
        self.assertEqual(len(reservation.token), 64)
        receipt = self.paths['receipt'].read_text()
        self.assertNotIn(reservation.token, receipt)
        self.assertTrue(json.loads(receipt)['synthetic'])
        self.assertEqual(self.paths['audit'].stat().st_size, 0)
        self.assertEqual(json.loads(self.paths['supervisor'].read_text())['event'], 'reserved')
        self.assertFalse(self.paths['armed'].exists())
        self.assertFalse(self.paths['claim'].exists())

    def test_second_reservation_never_clears_or_truncates(self):
        self.reserve()
        before = {key: value.read_bytes() for key, value in self.paths.items() if value.exists()}
        with self.assertRaises(FileExistsError):
            audit.reserve_run()
        self.assertEqual(before, {key: value.read_bytes() for key, value in self.paths.items() if value.exists()})

    def test_partial_reservation_remains_consumed(self):
        self.paths['audit'].write_bytes(b'preexisting')
        with self.assertRaises(FileExistsError):
            audit.reserve_run()
        self.assertTrue(self.paths['receipt'].exists())
        self.assertEqual(self.paths['audit'].read_bytes(), b'preexisting')
        with self.assertRaises(FileExistsError):
            audit.reserve_run()

    def test_root_denial_prevents_any_creation(self):
        self.root_check.side_effect = audit.AuditFailure('denied')
        with self.assertRaises(audit.AuditFailure):
            audit.reserve_run()
        self.open.assert_not_called()

    def test_unarmed_rejected_before_claim_or_audit_open(self):
        reservation = self.reserve()
        self.open.reset_mock()
        with self.assertRaisesRegex(audit.AuditFailure, '^gate_armed_rejected$'):
            audit.open_mcp_audit(reservation.token)
        self.assertFalse(self.paths['claim'].exists())
        self.assertNotIn('audit', [call.args[0] for call in self.open.call_args_list])

    def test_wrong_token_and_malformed_token_rejected(self):
        reservation = self.reserve()
        for token in ('a' * 64, reservation.token.upper(), '../path', '', None, 12):
            with self.subTest(token_type=type(token)), self.assertRaises(audit.AuditFailure):
                audit.arm_run(token)
        self.assertFalse(self.paths['armed'].exists())

    def test_expired_or_future_receipt_cannot_arm(self):
        reservation = self.reserve()
        for now in (999, 1360, 2000):
            self.clock.return_value = now
            with self.subTest(now=now), self.assertRaises(audit.AuditFailure):
                audit.arm_run(reservation.token)

    def test_arm_is_exclusive(self):
        reservation = self.ready()
        with self.assertRaises(FileExistsError):
            audit.arm_run(reservation.token)

    def test_exactly_one_mcp_claim_even_when_no_event_written(self):
        reservation = self.ready()
        with audit.open_mcp_audit(reservation.token):
            pass
        with self.assertRaisesRegex(audit.AuditFailure, '^gate_claim_rejected$'):
            audit.open_mcp_audit(reservation.token)
        self.assertTrue(self.paths['claim'].exists())

    def test_nonempty_audit_rejected_and_claim_consumed(self):
        reservation = self.ready()
        self.paths['audit'].write_bytes(b'preexisting')
        with self.assertRaises(audit.AuditFailure):
            audit.open_mcp_audit(reservation.token)
        self.assertTrue(self.paths['claim'].exists())
        self.assertEqual(self.paths['audit'].read_bytes(), b'preexisting')

    def test_terminal_gate_permanent_and_idempotent(self):
        reservation = self.ready()
        audit.disarm_run(reservation.token)
        before = self.paths['terminal'].read_bytes()
        audit.disarm_run(reservation.token)
        self.assertEqual(self.paths['terminal'].read_bytes(), before)
        with self.assertRaises(audit.AuditFailure):
            audit.open_mcp_audit(reservation.token)

    def test_disarm_works_after_receipt_expiry(self):
        reservation = self.ready()
        self.clock.return_value = 2000
        audit.disarm_run(reservation.token)
        self.assertTrue(self.paths['terminal'].exists())

    def test_supervisor_logs_only_fixed_event_and_timestamp(self):
        reservation = self.reserve()
        reservation.record('runtime_verified')
        for line in self.paths['supervisor'].read_bytes().splitlines():
            value = json.loads(line)
            self.assertEqual(set(value), {'event', 'timestamp_utc'})
            self.assertNotIn(reservation.token.encode(), line)
        with self.assertRaises(audit.AuditFailure):
            reservation.record('raw report text or password')
        with self.assertRaises(audit.AuditFailure):
            reservation.record('finished')

    def test_supervisor_count_limit(self):
        reservation = self.reserve()
        for unused in range(audit.MAX_SUPERVISOR_EVENTS - 1):
            reservation.record('runtime_verified')
        with self.assertRaises(audit.AuditFailure):
            reservation.record('finished')

    def test_supervisor_fsync_failure_poisoned(self):
        reservation = self.reserve()
        with patch.object(audit.os, 'fsync', side_effect=OSError('sensitive detail')):
            with self.assertRaisesRegex(audit.AuditFailure, '^private_supervisor_audit_failed$'):
                reservation.record('armed')
        self.assertTrue(reservation.failed)

    def test_marker_duplicate_or_unknown_fields_fail_closed(self):
        reservation = self.reserve()
        original = self.paths['receipt'].read_bytes()
        for body in (b'{"scope":"staging","scope":"staging"}', b'[]',
                     original[:-2] + b',"password":"secret"}\n', b'x' * 1025):
            self.paths['receipt'].write_bytes(body)
            with self.subTest(body=body[:20]), self.assertRaises(audit.AuditFailure):
                audit.arm_run(reservation.token)


class DurableStreamTests(LocalFixture):
    def test_valid_event_durably_written_and_read_as_counters(self):
        reservation = self.ready()
        with audit.open_mcp_audit(reservation.token) as stream:
            body = audit._json(event()) + b'\n'
            self.assertEqual(stream.write(body), len(body))
            with patch.object(audit.os, 'fsync', wraps=os.fsync) as sync:
                stream.flush()
            sync.assert_called_once_with(stream.stream.fileno())
        summary = audit.read_audit_summary(reservation.token)
        self.assertEqual(summary['events'], 1)
        self.assertEqual(summary['prepare_accepted'], 1)
        self.assertEqual(summary['publish_accepted'], 0)
        self.assertEqual(summary['bytes'], len(body))

    def test_transport_and_rejection_summary(self):
        reservation = self.ready()
        values = [event(event='staging_transport', method='publish', outcome=outcome)
                  for outcome in ('started', 'returned', 'failed')]
        values += [event(method='unknown', outcome='rejected'), event(method='publish'),
                   event(method='unknown', outcome='accepted')]
        with audit.open_mcp_audit(reservation.token) as stream:
            for value in values:
                stream.write(audit._json(value) + b'\n')
                stream.flush()
        summary = audit.read_audit_summary(reservation.token)
        for field in ('publish_accepted', 'protocol_accepted', 'adapter_rejected', 'transport_started', 'transport_returned', 'transport_failed'):
            self.assertEqual(summary[field], 1)

    def test_invalid_payload_poisoned_before_any_write(self):
        invalid = [b'raw secret\n', b'{}\n', audit._json(event(password='secret')) + b'\n',
                   audit._json(event(attempt=True)) + b'\n',
                   audit._json(event(timestamp_utc='2026-99-99T00:00:00+00:00')) + b'\n',
                   audit._json(event(method='ssh')) + b'\n',
                   audit._json(event()) + b'\n\n', audit._json(event()), b'\xff\n',
                   b'{"event":NaN}\n', 'not bytes']
        for body in invalid:
            target = io.BytesIO()
            stream = audit.DurableAuditStream(target)
            with self.subTest(body=repr(body)[:30]), self.assertRaises(audit.AuditFailure):
                stream.write(body)
            self.assertEqual(target.getvalue(), b'')
            self.assertTrue(stream.failed)
            with self.assertRaises(audit.AuditFailure):
                stream.write(audit._json(event()) + b'\n')

    def test_count_and_size_bounds(self):
        body = audit._json(event()) + b'\n'
        for changes in ({'events': audit.MAX_AUDIT_EVENTS}, {'size': audit.MAX_AUDIT_BYTES}):
            stream = audit.DurableAuditStream(io.BytesIO())
            for field, value in changes.items():
                setattr(stream, field, value)
            with self.assertRaises(audit.AuditFailure):
                stream.write(body)

    def test_short_write_and_fsync_failure_poison(self):
        target = MagicMock()
        target.write.return_value = 0
        stream = audit.DurableAuditStream(target)
        with self.assertRaises(audit.AuditFailure):
            stream.write(audit._json(event()) + b'\n')
        self.assertTrue(stream.failed)
        target = MagicMock()
        stream = audit.DurableAuditStream(target)
        with patch.object(audit.os, 'fsync', side_effect=OSError('private details')):
            with self.assertRaisesRegex(audit.AuditFailure, '^private_audit_flush_failed$'):
                stream.flush()
        self.assertTrue(stream.failed)

    def test_summary_rejects_truncated_oversized_or_invalid_log(self):
        reservation = self.reserve()
        for body in (b'{}\n', audit._json(event()), b'x' * (audit.MAX_AUDIT_BYTES + 1)):
            self.paths['audit'].write_bytes(body)
            with self.assertRaises(audit.AuditFailure):
                audit.read_audit_summary(reservation.token)


class ResultTests(LocalFixture):
    def test_result_exclusive_private_and_separate_from_audit(self):
        reservation = self.ready()
        with audit.open_mcp_audit(reservation.token):
            pass
        value = {'analysis': {'scope': 'staging'}, 'submission': {'summary': {'el': 'Δοκιμή'}},
                 'acknowledgement': {'synthetic': True}}
        audit.write_result(reservation.token, value)
        self.assertEqual(audit.read_result(reservation.token), value)
        self.assertEqual(self.paths['audit'].read_bytes(), b'')
        with self.assertRaises(FileExistsError):
            audit.write_result(reservation.token, value)

    def test_result_requires_arm_and_claim(self):
        reservation = self.ready()
        with self.assertRaises(FileNotFoundError):
            audit.write_result(reservation.token, {'analysis': {}, 'submission': {}, 'acknowledgement': {}})
        self.assertFalse(self.paths['result'].exists())

    def test_result_finite_bounded_exact_envelope(self):
        for value in ({}, {'analysis': [], 'submission': {}, 'acknowledgement': {}},
                      {'analysis': {'x': float('nan')}, 'submission': {}, 'acknowledgement': {}},
                      {'analysis': {'x': 'x' * audit.MAX_RESULT_BYTES}, 'submission': {}, 'acknowledgement': {}}):
            with self.assertRaises(audit.AuditFailure):
                audit._result(value)


class DiagnosticTests(LocalFixture):
    def value(self, **changes):
        value = {'model_started': True, 'model_completed': True, 'mcp_items': 0,
                 'tool_calls': 0, 'total_tokens': 1234, 'final_message': 'Δοκιμή — проверка'}
        value.update(changes)
        return value

    def test_exclusive_private_diagnostic_after_terminal_and_expiry(self):
        reservation = self.reserve()
        audit.disarm_run(reservation.token)
        self.clock.return_value = 2000
        value = self.value()
        audit.write_diagnostic(reservation.token, value)
        self.assertEqual(audit.read_diagnostic(reservation.token), value)
        self.assertFalse(self.paths['armed'].exists())
        self.assertFalse(self.paths['claim'].exists())
        self.assertEqual(self.paths['audit'].read_bytes(), b'')
        before = self.paths['diagnostic'].read_bytes()
        with self.assertRaises(FileExistsError):
            audit.write_diagnostic(reservation.token, self.value(final_message='replacement'))
        self.assertEqual(self.paths['diagnostic'].read_bytes(), before)

    def test_matching_receipt_required_before_diagnostic_creation(self):
        self.reserve()
        with self.assertRaises(audit.AuditFailure):
            audit.write_diagnostic('a' * 64, self.value())
        self.assertFalse(self.paths['diagnostic'].exists())

    def test_exact_finite_typed_bounded_diagnostic_schema(self):
        invalid = [{}, self.value(reasoning='not permitted'), self.value(model_started=1),
                   self.value(model_completed='true'), self.value(model_started=False),
                   self.value(mcp_items=True), self.value(mcp_items=-1), self.value(mcp_items=1001),
                   self.value(tool_calls=True), self.value(tool_calls=-1), self.value(tool_calls=1001),
                   self.value(total_tokens=True), self.value(total_tokens=-1),
                   self.value(total_tokens=float('nan')), self.value(total_tokens=10000001),
                   self.value(final_message=None), self.value(final_message='x' * 8001),
                   self.value(final_message='\ud800')]
        for value in invalid:
            with self.subTest(value=str(value)[:80]), self.assertRaises(audit.AuditFailure):
                audit._diagnostic(value)
        body = audit._diagnostic(self.value(final_message='😀' * 8000, total_tokens=None))
        self.assertLessEqual(len(body), audit.MAX_DIAGNOSTIC_BYTES)

    def test_diagnostic_durable_flush(self):
        reservation = self.reserve()
        with patch.object(audit.os, 'fsync', wraps=os.fsync) as sync:
            audit.write_diagnostic(reservation.token, self.value())
        sync.assert_called_once()

    def test_diagnostic_read_rejects_unknown_fields_oversize_and_bad_utf8(self):
        reservation = self.reserve()
        for body in (b'{"model_started":true,"model_started":false}', b'\xff',
                     b'x' * (audit.MAX_DIAGNOSTIC_BYTES + 1), b'{}'):
            self.paths['diagnostic'].write_bytes(body)
            with self.subTest(body=body[:30]), self.assertRaises(audit.AuditFailure):
                audit.read_diagnostic(reservation.token)


class RawAclTests(unittest.TestCase):
    """Exercise raw-handle ACE checks against fixed in-memory Win32 responses."""
    def setUp(self):
        self.adv = MagicMock()
        self.kernel = MagicMock()
        self.buffers = []
        self.sid_names = {100: 'current'}
        self.control = 4
        self.owner = 100
        self.count = 3
        self.ace_values = [(0, 16, audit.acl.FULL_CONTROL, 'current'),
                           (0, 16, audit.acl.FULL_CONTROL, 'S-1-5-18'),
                           (0, 16, audit.acl.FULL_CONTROL, 'S-1-5-32-544')]
        self.enterContext(patch.object(ctypes, 'WinDLL', side_effect=lambda name, **kw:
                                      self.adv if name == 'advapi32' else self.kernel))
        self.kernel.GetCurrentProcess.return_value = 123
        self.adv.OpenProcessToken.side_effect = lambda process, flags, out: (setattr(out._obj, 'value', 9) or 1)

        def token_info(token, kind, buffer, size, needed):
            needed._obj.value = 16
            if buffer is not None:
                ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0] = 100
            return 1
        self.adv.GetTokenInformation.side_effect = token_info
        self.adv.IsValidSid.return_value = 1
        self.adv.GetLengthSid.return_value = 12

        def sid_string(sid, out):
            address = sid.value if isinstance(sid, ctypes.c_void_p) else sid
            out._obj.value = self.sid_names[address]
            return 1
        self.adv.ConvertSidToStringSidW.side_effect = sid_string

        def security(handle, kind, flags, owner, group, dacl, sacl, descriptor):
            buffer = ctypes.create_string_buffer(8)
            self.buffers.append(buffer)
            ctypes.c_ushort.from_buffer(buffer, 4).value = self.count
            owner._obj.value = self.owner
            dacl._obj.value = ctypes.addressof(buffer)
            descriptor._obj.value = 99
            return 0
        self.adv.GetSecurityInfo.side_effect = security
        self.adv.GetSecurityDescriptorControl.side_effect = lambda desc, control, revision: (
            setattr(control._obj, 'value', self.control) or 1)

        def get_ace(dacl, index, output):
            ace_type, flags, rights, sid_name = self.ace_values[index]
            buffer = ctypes.create_string_buffer(20)
            self.buffers.append(buffer)
            ctypes.c_ubyte.from_buffer(buffer).value = ace_type
            ctypes.c_ubyte.from_buffer(buffer, 1).value = flags
            ctypes.c_ushort.from_buffer(buffer, 2).value = 20
            ctypes.c_uint32.from_buffer(buffer, 4).value = rights
            address = ctypes.addressof(buffer)
            self.sid_names[address + 8] = sid_name
            output._obj.value = address
            return 1
        self.adv.GetAce.side_effect = get_ace

    def test_exact_inherited_three_principal_acl_accepted(self):
        audit._check_handle_acl(42)
        self.kernel.CloseHandle.assert_called_once_with(unittest.mock.ANY)
        self.assertGreaterEqual(self.kernel.LocalFree.call_count, 5)

    def test_protected_missing_or_extra_acl_rejected(self):
        for control, count in ((0, 3), (0x1004, 3), (4, 4), (4, 2)):
            self.control, self.count = control, count
            with self.subTest(control=control, count=count), self.assertRaises(audit.AuditFailure):
                audit._check_handle_acl(42)

    def test_raw_ace_semantics_rejected(self):
        for value in ((1, 16, audit.acl.FULL_CONTROL, 'current'),
                      (0, 0, audit.acl.FULL_CONTROL, 'current'),
                      (0, 19, audit.acl.FULL_CONTROL, 'current'),
                      (0, 16, 1, 'current'), (0, 16, audit.acl.FULL_CONTROL, 'other')):
            self.ace_values[0] = value
            with self.subTest(value=value), self.assertRaises(audit.AuditFailure):
                audit._check_handle_acl(42)

    def test_duplicate_principal_and_wrong_owner_rejected(self):
        self.ace_values[2] = self.ace_values[1]
        with self.assertRaises(audit.AuditFailure):
            audit._check_handle_acl(42)
        self.owner = 101
        self.sid_names[101] = 'different-owner'
        with self.assertRaises(audit.AuditFailure):
            audit._check_handle_acl(42)


@unittest.skipUnless(os.name == 'nt', 'Windows handle transfer fixture')
class NativeOpenTests(unittest.TestCase):
    def setUp(self):
        import msvcrt
        self.temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.temporary)
        self.path = self.root / 'audit.jsonl'
        self.path.write_bytes(b'')
        self.enterContext(patch.object(audit, 'ROOT', self.root))
        self.enterContext(patch.object(audit, 'FILES', {'audit': self.path}))
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
        self.check = self.enterContext(patch.object(audit, '_check_handle_acl'))
        self.transfer = self.enterContext(patch.object(msvcrt, 'open_osfhandle',
            side_effect=lambda handle, flags: os.open(self.path, os.O_RDWR | os.O_BINARY)))

    def test_exclusive_nofollow_write_through_create_and_handle_transfer(self):
        with audit._open('audit', create=True, write=True) as stream:
            self.assertEqual(stream.read(), b'')
        args = self.kernel.CreateFileW.call_args.args
        self.assertEqual(args[2], 0)  # No read/write/delete sharing.
        self.assertEqual(args[4], 1)  # CREATE_NEW, never truncate/reuse.
        self.assertTrue(args[5] & 0x00200000)  # FILE_FLAG_OPEN_REPARSE_POINT.
        self.assertTrue(args[5] & 0x80000000)  # FILE_FLAG_WRITE_THROUGH.
        self.assertTrue(self.transfer.call_args.args[1] & os.O_NOINHERIT)
        self.check.assert_called_once_with(77)
        self.kernel.CloseHandle.assert_not_called()  # Descriptor owns it now.

    def test_existing_read_open_does_not_request_write_or_truncation(self):
        with audit._open('audit'):
            pass
        args = self.kernel.CreateFileW.call_args.args
        self.assertEqual(args[1], 0x80000000)
        self.assertEqual(args[4], 3)  # OPEN_EXISTING.

    def test_reparse_directory_and_hardlinks_rejected_before_transfer(self):
        for attributes, links in ((0x400, 1), (0x10, 1), (0, 2), (0, 0)):
            self.attributes, self.links = attributes, links
            with self.subTest(attributes=attributes, links=links), self.assertRaises(audit.AuditFailure):
                audit._open('audit')
        self.transfer.assert_not_called()
        self.assertEqual(self.kernel.CloseHandle.call_count, 4)

    def test_mismatched_canonical_path_rejected(self):
        self.canonical = '\\\\?\\C:\\outside\\audit.jsonl'
        with self.assertRaises(audit.AuditFailure):
            audit._open('audit')
        self.transfer.assert_not_called()
        self.kernel.CloseHandle.assert_called_once_with(77)

    def test_acl_rejection_closes_handle(self):
        self.check.side_effect = audit.AuditFailure('acl_rejected')
        with self.assertRaises(audit.AuditFailure):
            audit._open('audit')
        self.transfer.assert_not_called()
        self.kernel.CloseHandle.assert_called_once_with(77)

    def test_native_open_failure_sanitized(self):
        self.kernel.CreateFileW.return_value = ctypes.c_void_p(-1).value
        with self.assertRaisesRegex(audit.AuditFailure, '^private_audit_open_failed$'):
            audit._open('audit')
        self.transfer.assert_not_called()
        self.kernel.CloseHandle.assert_not_called()


if __name__ == '__main__':
    unittest.main()
