"""Private-review contracts with in-memory fixtures only; no server/model calls."""
import copy
import hashlib
import io
import unittest
from unittest.mock import Mock, patch

import insights_analysis as analyzer
from insights_broker import decode, encode
import insights_private_review_mcp as adapter


NOW = 1791800040
TOKEN = '1' * 64


def fixture(rows=None):
    with patch.object(analyzer, '_load_points', return_value=[] if rows is None else rows):
        analysis = analyzer.analyze('not-accessed', NOW)
    return {'schema_version': 1, 'scope': 'private_review', 'synthetic': False,
            'captured_at': NOW, 'analysis_id': hashlib.sha256(encode(analysis)).hexdigest(),
            'analysis': analysis}


def rehash(value):
    value['analysis_id'] = hashlib.sha256(encode(value['analysis'])).hexdigest()
    return value


def prose():
    return {'en': 'Coverage is incomplete; missing readings are not zero.',
            'el': 'Η κάλυψη είναι ελλιπής. Οι ελλείπουσες μετρήσεις δεν είναι μηδενικές.',
            'ru': 'Охват неполный. Отсутствующие показания не равны нулю.'}


class MemoryStore:
    def __init__(self):
        snapshot = fixture()
        self.data = {'snapshot': snapshot, 'receipt': {
            'snapshot_sha256': hashlib.sha256(encode(snapshot)).hexdigest()}}
        self.calls = []
        self.claimed = False
        self.closed = False
        self.events = []

    def active(self, token, armed=True):
        if token != TOKEN or self.closed or armed is not True:
            raise ValueError('closed fixture')
        self.calls.append(('active',))

    def claim(self, token):
        self.active(token)
        if self.claimed:
            raise ValueError('already claimed')
        self.claimed = True
        self.calls.append(('claim',))

    def read(self, kind):
        self.calls.append(('read', kind))
        return copy.deepcopy(self.data[kind])

    def create(self, kind, value):
        if kind in self.data or kind != 'result' or not self.claimed:
            raise ValueError('immutable result')
        self.data[kind] = copy.deepcopy(value)
        self.calls.append(('create', kind))

    def record(self, event):
        self.events.append(event)

    def close(self):
        self.closed = True


class SnapshotTests(unittest.TestCase):
    def test_empty_analyzer_snapshot_has_honest_private_provenance(self):
        value = fixture()
        verified = adapter.validate_snapshot(value, now=NOW, fresh=True)
        self.assertEqual(verified, value)
        self.assertIsNot(verified, value)
        self.assertEqual(verified['analysis']['coverage_pct'], 0)

    def test_complete_analyzer_snapshot_accepted(self):
        rows = [{'t': stamp, 'values': {'pv': 500, 'grid': 100, 'load': 600, 'battery': -200, 'soc': 90}}
                for stamp in range(NOW - 8 * 86400, NOW, 60)]
        value = fixture(rows)
        self.assertEqual(adapter.validate_snapshot(value)['analysis']['severity'], 'routine')

    def test_finding_variants_from_real_analyzer(self):
        for change in ('high_load', 'high_grid', 'low_solar', 'low_soc', 'recording_gap'):
            rows = []
            for stamp in range(NOW - 8 * 86400, NOW, 60):
                values = {'pv': 500, 'grid': 100, 'load': 600, 'battery': -200, 'soc': 99}
                if stamp >= NOW - 86400:
                    if change == 'high_load':
                        values['load'] = 4000
                    elif change == 'high_grid':
                        values['grid'] = 1000
                    elif change == 'low_solar':
                        values['pv'] = 0
                    elif change == 'low_soc':
                        values['soc'] = 60
                    elif NOW - 1800 <= stamp < NOW - 600:
                        continue
                rows.append({'t': stamp, 'values': values})
            with self.subTest(change=change):
                adapter.validate_snapshot(fixture(rows))

    def test_scope_synthetic_hash_and_extra_fields_rejected(self):
        for field, value in [('synthetic', True), ('synthetic', 0), ('scope', 'staging'),
                             ('schema_version', True), ('captured_at', True),
                             ('analysis_id', '0' * 64), ('path', 'unrelated')]:
            invalid = fixture()
            invalid[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                adapter.validate_snapshot(invalid)

    def test_freshness_bounds_and_clock_skew(self):
        value = fixture()
        for current in (NOW - 5, NOW, NOW + 1800):
            adapter.validate_snapshot(value, now=current, fresh=True)
        for current in (NOW - 6, NOW + 1801, True, float('inf')):
            with self.subTest(current=current), self.assertRaises(ValueError):
                adapter.validate_snapshot(value, now=current, fresh=True)
        for captured in (NOW - 1, NOW + 121):
            with self.subTest(captured=captured), self.assertRaises(ValueError):
                adapter.validate_snapshot(dict(value, captured_at=captured))

    def test_window_baseline_and_schema_closed(self):
        for operation in (
                lambda a: a.update(schema_version=True),
                lambda a: a.update(raw_history=[]),
                lambda a: a['window'].update(start=NOW - 1),
                lambda a: a['window'].update(extra=0),
                lambda a: a['baseline'].update(days=True),
                lambda a: a['baseline'].update(end=NOW),
                lambda a: a.update(generated_at=NOW + 1),
                lambda a: a.update(data_through=NOW + 60),
                lambda a: a.update(data_through=NOW - 8 * 86400)):
            value = fixture()
            operation(value['analysis'])
            with self.assertRaises(ValueError):
                adapter.validate_snapshot(rehash(value))

    def test_metric_structure_bounds_translations_and_missingness(self):
        changes = [('value', -1), ('value', True), ('value', 24_000_001),
                   ('value', 0), ('baseline', 10), ('change_pct', 100),
                   ('coverage_pct', 101), ('baseline_days', 8), ('baseline_days', True),
                   ('key', 'unknown'), ('unit', 'W'), ('extra', 'private'),
                   ('label', {'en': 'Only English'}),
                   ('label', dict.fromkeys(('en', 'el', 'ru'), '<script>'))]
        for key, replacement in changes:
            value = fixture()
            value['analysis']['metrics'][0][key] = replacement
            with self.subTest(key=key, replacement=replacement), self.assertRaises(ValueError):
                adapter.validate_snapshot(rehash(value))

    def test_findings_and_limitations_are_bounded_and_closed(self):
        for operation in (
                lambda a: a.update(findings=[]),
                lambda a: a['findings'].append(copy.deepcopy(a['findings'][0])),
                lambda a: a['findings'][0].update(id='unknown'),
                lambda a: a['findings'][0].update(category='solar'),
                lambda a: a['findings'][0].update(metric='pv'),
                lambda a: a['findings'][0].update(start=NOW),
                lambda a: a['findings'][0].update(end=NOW + 1),
                lambda a: a['findings'][0].update(extra='x'),
                lambda a: a.update(severity='routine'),
                lambda a: a.update(limitations=[]),
                lambda a: a['limitations'].append(dict.fromkeys(('en', 'el', 'ru'), 'x' * 1201))):
            value = fixture()
            operation(value['analysis'])
            with self.assertRaises(ValueError):
                adapter.validate_snapshot(rehash(value))


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore()
        self.factory = Mock(return_value=self.store)
        self.reports = adapter.Reports(self.factory, TOKEN)
        self.clock = patch.object(adapter.time, 'time', return_value=NOW)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def payload(self):
        return {'analysis_id': self.store.data['snapshot']['analysis_id'], 'summary': prose(), 'notes': []}

    def test_closed_token_never_constructs_store(self):
        for token in (adapter.CLOSED_TOKEN, '', 'x' * 64, None):
            reports = adapter.Reports(self.factory, token)
            with self.subTest(token=token), self.assertRaises(ValueError):
                reports.call('prepare', {})
        self.factory.assert_not_called()

    def test_prepare_claims_once_and_does_not_leak_capture_metadata(self):
        prepared = self.reports.call('prepare', {})
        self.assertEqual(set(prepared), {'scope', 'synthetic', 'result'})
        self.assertEqual(set(prepared['result']), {'analysis_id', 'analysis'})
        self.assertEqual(self.store.events, ['prepared'])
        with self.assertRaises(ValueError):
            self.reports.call('prepare', {})
        self.assertEqual(self.store.calls.count(('claim',)), 1)

    def test_receipt_snapshot_binding_rejected(self):
        self.store.data['receipt']['snapshot_sha256'] = '0' * 64
        with self.assertRaises(ValueError):
            self.reports.call('prepare', {})
        self.assertNotIn('result', self.store.data)

    def test_publish_is_private_immutable_and_identical_retry_only(self):
        prepared = self.reports.call('prepare', {})
        payload = self.payload()
        ack = self.reports.call('publish', payload)
        self.assertEqual(ack, {'scope': 'private_review', 'synthetic': False,
                             'result': {'status': 'saved_private', 'id': payload['analysis_id']}})
        result = copy.deepcopy(self.store.data['result'])
        self.assertEqual(result['schema_version'], 1)
        self.assertEqual(result['analysis'], prepared)
        self.assertEqual(result['submission'], payload)
        self.assertEqual(result['acknowledgement'], ack)
        self.assertEqual(self.reports.call('publish', payload)['result']['status'], 'already_saved_private')
        self.assertEqual(self.store.data['result'], result)
        self.assertEqual(self.store.events, ['prepared', 'saved'])
        with self.assertRaises(ValueError):
            self.reports.call('publish', payload)

    def test_changed_retry_cannot_overwrite(self):
        self.reports.call('prepare', {})
        self.reports.call('publish', self.payload())
        before = encode(self.store.data['result'])
        changed = self.payload()
        changed['summary']['en'] = 'Different report.'
        with self.assertRaises(ValueError):
            self.reports.call('publish', changed)
        self.assertEqual(encode(self.store.data['result']), before)

    def test_publish_before_prepare_wrong_id_extra_fields_and_bad_prose_rejected(self):
        with self.assertRaises(ValueError):
            self.reports.call('publish', self.payload())
        self.reports.call('prepare', {})
        invalid = self.payload()
        invalid['analysis_id'] = '0' * 64
        with self.assertRaises(ValueError):
            self.reports.call('publish', invalid)
        with self.assertRaises(ValueError):
            self.reports.call('publish', dict(self.payload(), summary={'en': 'Only English'}))
        self.assertNotIn('result', self.store.data)

    def test_bad_notes_and_forbidden_content_rejected(self):
        for changes in ({'notes': [{'finding_id': 'fake', 'commentary': prose()}]},
                        {'path': 'unrelated'}, {'summary': dict(prose(), en='https://example.invalid/')},
                        {'summary': dict(prose(), en='x' * 601)}):
            with self.subTest(changes=changes):
                store = MemoryStore()
                reports = adapter.Reports(lambda: store, TOKEN)
                reports.call('prepare', {})
                with self.assertRaises(ValueError):
                    reports.call('publish', dict(self.payload(), **changes))
                self.assertNotIn('result', store.data)

    def test_result_create_failure_preserves_pending_payload(self):
        self.reports.call('prepare', {})
        with patch.object(self.store, 'create', side_effect=OSError('private host detail')):
            with self.assertRaises(OSError):
                self.reports.call('publish', self.payload())
        changed = self.payload()
        changed['summary']['en'] = 'Replacement.'
        with self.assertRaises(ValueError):
            self.reports.call('publish', changed)
        self.assertNotIn('result', self.store.data)

    def test_rejection_and_close_fail_closed_without_diagnostic_overwrite(self):
        self.reports.call('prepare', {})
        self.reports.reject('protocol_call', 2, 'unknown')
        self.reports.reject('protocol_frame', 3, 'unknown')
        self.assertEqual(self.store.events, ['prepared', 'failed'])
        self.assertEqual(len(self.reports.rejections), 1)
        self.assertNotIn('diagnostic', self.store.data)
        with self.assertRaises(ValueError):
            self.reports.call('publish', self.payload())
        self.reports.close()
        self.assertTrue(self.store.closed)


def message(method, params=None, identifier=1):
    return {'jsonrpc': '2.0', 'id': identifier, 'method': method, 'params': params or {}}


INITIALIZE = message('initialize', {'protocolVersion': '2025-06-18'})


class ProtocolTests(unittest.TestCase):
    def run_messages(self, items, reports=None):
        reports = reports or Mock()
        source = io.BytesIO(b''.join(encode(item) + b'\n' for item in items))
        destination = io.BytesIO()
        adapter.serve(source, destination, reports)
        return [decode(row, adapter.MAX_FRAME * 2) for row in destination.getvalue().splitlines()], reports

    def test_discovery_is_honest_and_never_opens_store(self):
        factory = Mock()
        reports = adapter.Reports(factory, adapter.CLOSED_TOKEN)
        responses, _ = self.run_messages([INITIALIZE, message('tools/list'), message('ping')], reports)
        factory.assert_not_called()
        self.assertEqual(responses[0]['result']['serverInfo']['name'], adapter.SERVER)
        self.assertEqual(responses[1]['result']['tools'], adapter.TOOLS)
        self.assertEqual([tool['name'] for tool in adapter.TOOLS], ['prepare', 'publish'])
        self.assertNotIn('synthetic', str(adapter.TOOLS))

    def test_known_metadata_is_bounded_and_stripped(self):
        metadata = {'progressToken': 1, 'threadId': 'thread-1', 'callId': '', 'itemId': 'item',
                    'sessionId': 'session', 'windowId': 'window',
                    'x-codex-turn-metadata': {'nested': [{'ok': True}]}}
        reports = Mock()
        reports.call.return_value = {'scope': 'private_review', 'synthetic': False, 'result': {}}
        responses, _ = self.run_messages([INITIALIZE, message('tools/call', {
            'name': 'prepare', 'arguments': {}, '_meta': metadata})], reports)
        self.assertIn('result', responses[-1])
        reports.call.assert_called_once_with('prepare', {})

    def test_invalid_metadata_and_unknown_routes_stop_before_dispatch(self):
        invalid = [{}, {'progressToken': True}, {'progressToken': 1, 'command': 'x'},
                   {'progressToken': 1, 'threadId': 'x' * 129},
                   {'progressToken': 1, 'callId': 'line\nbreak'},
                   {'progressToken': 1, 'windowId': 'x' * 257},
                   {'progressToken': 1, 'x-codex-turn-metadata': 'not an object'},
                   {'progressToken': 1, 'x-codex-turn-metadata': {'deep': {'a': {'b': {'c': {}}}}}}]
        for metadata in invalid:
            with self.subTest(metadata=metadata):
                responses, reports = self.run_messages([INITIALIZE, message('tools/call', {
                    'name': 'prepare', 'arguments': {}, '_meta': metadata}), message('tools/list')])
                self.assertEqual(len(responses), 2)
                self.assertEqual(responses[-1]['error']['message'], adapter.ERROR)
                reports.call.assert_not_called()
                reports.reject.assert_called_once()
        for method, params in [('resources/list', {}), ('tools/call', {'name': 'exec', 'arguments': {}}),
                               ('tools/call', {'name': 'prepare', 'arguments': {}, 'path': 'x'})]:
            with self.subTest(method=method, params=params):
                responses, reports = self.run_messages([INITIALIZE, message(method, params)])
                self.assertIn('error', responses[-1])
                reports.call.assert_not_called()

    def test_malformed_json_duplicate_keys_and_frame_bounds_are_redacted(self):
        raws = [b'{bad-json}\n', b'{"jsonrpc":"2.0","jsonrpc":"2.0"}\n',
                b'{}', b'x' * (adapter.MAX_FRAME + 1),
                encode(message('ping', identifier={'private': 'details'})) + b'\n']
        for raw in raws:
            with self.subTest(raw=raw[:60]):
                destination = io.BytesIO()
                reports = Mock()
                adapter.serve(io.BytesIO(raw), destination, reports)
                response = decode(destination.getvalue(), 1024)
                self.assertIsNone(response['id'])
                self.assertEqual(response['error']['message'], adapter.ERROR)
                reports.call.assert_not_called()

    def test_tool_error_is_redacted_and_records_only_code(self):
        reports = Mock()
        reports.call.side_effect = OSError('credential detail and path')
        responses, _ = self.run_messages([INITIALIZE, message('tools/call', {
            'name': 'prepare', 'arguments': {}})], reports)
        self.assertEqual(responses[-1]['error']['message'], adapter.ERROR)
        self.assertNotIn('credential', str(responses))
        reports.reject.assert_called_once_with('tool_execution_rejected', 2, 'prepare')

    def test_frame_count_bounded_and_notifications_have_no_response(self):
        notification = {'jsonrpc': '2.0', 'method': 'notifications/initialized', 'params': {}}
        responses, _ = self.run_messages([INITIALIZE, notification] + [message('ping')] * 70)
        self.assertEqual(len(responses), 63)


if __name__ == '__main__':
    unittest.main()
