"""Synthetic in-memory contract tests; no SSH, model, live data or publication."""
import copy
import hashlib
import io
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

import insights_analysis as analyzer
from insights_broker import decode, encode
import insights_production_mcp as adapter


NOW = 1791800040


def fixture(rows=None):
    with patch.object(analyzer, '_load_points', return_value=[] if rows is None else rows):
        analysis = analyzer.analyze('not-accessed', NOW)
    return {'scope': 'production', 'synthetic': False,
            'result': {'analysis_id': hashlib.sha256(encode(analysis)).hexdigest(), 'analysis': analysis}}


def rehash(value):
    value['result']['analysis_id'] = hashlib.sha256(encode(value['result']['analysis'])).hexdigest()
    return value


def prose():
    return {'en': 'Coverage is incomplete; missing readings are not zero.',
            'el': 'Η κάλυψη είναι ελλιπής. Οι ελλείπουσες μετρήσεις δεν είναι μηδενικές.',
            'ru': 'Охват неполный. Отсутствующие показания не равны нулю.'}


class FixtureTransport:
    def __init__(self):
        self.prepared = fixture()
        self.calls = []
        self.sent = None

    def __call__(self, method, body):
        self.calls.append((method, body))
        if method == 'prepare':
            return encode(self.prepared)
        payload = decode(body, adapter.MAX_REQUEST)
        status = 'already_published' if self.sent is not None else 'published'
        if self.sent is not None and self.sent != body:
            raise ValueError('synthetic immutable fixture')
        self.sent = body
        return encode({'scope': 'production', 'synthetic': False,
                       'result': {'status': status, 'id': payload['analysis_id']}})


class ResponseTests(unittest.TestCase):
    def test_empty_and_complete_analysis_accepted_without_provenance_change(self):
        for rows in ([], [{'t': stamp, 'values': {'pv': 500, 'grid': 100, 'load': 600,
                                                'battery': -200, 'soc': 90}}
                         for stamp in range(NOW - 8 * 86400, NOW, 60)]):
            value = fixture(rows)
            self.assertEqual(adapter.validate_response(encode(value), 'prepare', now=NOW), value)

    def test_analyzer_finding_variants_accepted(self):
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
                adapter.validate_response(encode(fixture(rows)), 'prepare', now=NOW)

    def test_wrong_provenance_or_envelope_is_rejected(self):
        for operation in (
                lambda x: x.update(scope='staging'), lambda x: x.update(scope='private_review'),
                lambda x: x.update(synthetic=True), lambda x: x.update(synthetic=0),
                lambda x: x.update(path='private'), lambda x: x['result'].update(analysis_id='0' * 64),
                lambda x: x['result'].update(extra='private')):
            value = fixture()
            operation(value)
            with self.assertRaises(ValueError):
                adapter.validate_response(encode(value), 'prepare', now=NOW)
        with self.assertRaises(ValueError):
            adapter.validate_response(encode(fixture()['result']), 'prepare', now=NOW)

    def test_freshness_and_clock_values_are_bounded(self):
        raw = encode(fixture())
        for current in (NOW - 5, NOW, NOW + 1800):
            adapter.validate_response(raw, 'prepare', now=current)
        for current in (NOW - 6, NOW + 1801, True, float('inf'), float('nan')):
            with self.subTest(current=current), self.assertRaises(ValueError):
                adapter.validate_response(raw, 'prepare', now=current)

    def test_closed_window_baseline_schema(self):
        for operation in (
                lambda a: a.update(schema_version=True), lambda a: a.update(raw_history=[]),
                lambda a: a['window'].update(start=NOW - 1), lambda a: a['window'].update(extra=0),
                lambda a: a['baseline'].update(days=True), lambda a: a['baseline'].update(end=NOW),
                lambda a: a.update(generated_at=NOW + 1), lambda a: a.update(data_through=NOW + 60),
                lambda a: a.update(data_through=NOW - 8 * 86400)):
            value = fixture()
            operation(value['result']['analysis'])
            with self.assertRaises(ValueError):
                adapter.validate_response(encode(rehash(value)), 'prepare', now=NOW)

    def test_metric_structure_bounds_translation_and_missingness(self):
        for key, replacement in (
                ('value', -1), ('value', True), ('value', 24_000_001), ('value', 0),
                ('baseline', 10), ('change_pct', 100), ('coverage_pct', 101),
                ('baseline_days', 8), ('baseline_days', True), ('key', 'unknown'),
                ('unit', 'W'), ('extra', 'private'), ('label', {'en': 'Only English'}),
                ('label', dict.fromkeys(('en', 'el', 'ru'), '<script>'))):
            value = fixture()
            value['result']['analysis']['metrics'][0][key] = replacement
            with self.subTest(key=key, replacement=replacement), self.assertRaises(ValueError):
                adapter.validate_response(encode(rehash(value)), 'prepare', now=NOW)

    def test_findings_severity_and_limitations_closed(self):
        for operation in (
                lambda a: a.update(findings=[]),
                lambda a: a['findings'].append(copy.deepcopy(a['findings'][0])),
                lambda a: a['findings'][0].update(id='unknown'),
                lambda a: a['findings'][0].update(category='solar'),
                lambda a: a['findings'][0].update(metric='pv'),
                lambda a: a['findings'][0].update(start=NOW),
                lambda a: a['findings'][0].update(end=NOW + 1),
                lambda a: a['findings'][0].update(extra='x'),
                lambda a: a.update(severity='routine'), lambda a: a.update(limitations=[]),
                lambda a: a['limitations'].append(dict.fromkeys(('en', 'el', 'ru'), 'x' * 1201))):
            value = fixture()
            operation(value['result']['analysis'])
            with self.assertRaises(ValueError):
                adapter.validate_response(encode(rehash(value)), 'prepare', now=NOW)

    def test_publication_acknowledgement_is_exact_and_id_bound(self):
        identifier = 'a' * 64
        for status in ('published', 'already_published'):
            value = {'scope': 'production', 'synthetic': False,
                     'result': {'status': status, 'id': identifier}}
            self.assertEqual(adapter.validate_response(encode(value), 'publish', identifier), value)
        for status in ('staged', 'already_staged', 'saved_private', 'already_saved_private', 'ok'):
            value = {'scope': 'production', 'synthetic': False,
                     'result': {'status': status, 'id': identifier}}
            with self.subTest(status=status), self.assertRaises(ValueError):
                adapter.validate_response(encode(value), 'publish', identifier)
        for result in ({'status': 'published', 'id': 'b' * 64},
                       {'status': 'published', 'id': identifier, 'path': 'private'}):
            with self.assertRaises(ValueError):
                adapter.validate_response(encode({'scope': 'production', 'synthetic': False,
                                                  'result': result}), 'publish', identifier)

    def test_raw_response_encoding_bounds_and_duplicate_keys(self):
        for raw in (b'', b'x' * (adapter.MAX_RESPONSE + 1), b'{}{}',
                    b'{"scope":"production","scope":"production"}', b'{"result":NaN}'):
            with self.subTest(raw=raw[:60]), self.assertRaises(ValueError):
                adapter.validate_response(raw, 'prepare', now=NOW)


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.transport = FixtureTransport()
        self.now = NOW
        self.elapsed = 0
        self.reports = adapter.Reports(self.transport, clock=lambda: self.now,
                                       monotonic=lambda: self.elapsed)

    def payload(self):
        return {'analysis_id': self.transport.prepared['result']['analysis_id'],
                'summary': prose(), 'notes': []}

    def test_construct_and_discovery_never_call_transport(self):
        self.assertEqual(self.transport.calls, [])

    def test_prepare_is_frozen_and_copies_cannot_mutate_it(self):
        first = self.reports.call('prepare', {})
        first['result']['analysis']['severity'] = 'warning'
        second = self.reports.call('prepare', {})
        self.assertEqual(second, self.transport.prepared)
        self.assertEqual(self.transport.calls, [('prepare', b'')])

    def test_publish_accepts_only_identical_canonical_retry_twice(self):
        self.reports.call('prepare', {})
        payload = self.payload()
        first = self.reports.call('publish', payload)
        self.assertEqual(first['result']['status'], 'published')
        reversed_keys = {key: payload[key] for key in reversed(list(payload))}
        second = self.reports.call('publish', reversed_keys)
        self.assertEqual(second['result']['status'], 'already_published')
        self.assertEqual(self.transport.calls[1], self.transport.calls[2])
        with self.assertRaises(ValueError):
            self.reports.call('publish', payload)
        self.assertEqual(len(self.transport.calls), 3)

    def test_lost_ack_locks_pending_content_before_transport(self):
        self.reports.call('prepare', {})
        saved = self.reports._transport
        self.reports._transport = Mock(side_effect=OSError('private transport detail'))
        with self.assertRaises(OSError):
            self.reports.call('publish', self.payload())
        changed = self.payload()
        changed['summary']['en'] = 'Different report.'
        with self.assertRaises(ValueError):
            self.reports.call('publish', changed)
        self.reports._transport = saved
        self.assertEqual(self.reports.call('publish', self.payload())['result']['status'], 'published')
        with self.assertRaises(ValueError):
            self.reports.call('publish', self.payload())

    def test_total_upstream_attempts_bounded_including_failed_prepare(self):
        transport = Mock(side_effect=OSError('private'))
        reports = adapter.Reports(transport, clock=lambda: NOW, monotonic=lambda: 0)
        for _ in range(adapter.MAX_CALLS):
            with self.assertRaises(OSError):
                reports.call('prepare', {})
        with self.assertRaises(ValueError):
            reports.call('prepare', {})
        self.assertEqual(transport.call_count, adapter.MAX_CALLS)

    def test_malformed_response_permanently_closes_session(self):
        wrong = fixture()
        wrong['scope'] = 'staging'
        transport = Mock(return_value=encode(wrong))
        reports = adapter.Reports(transport, clock=lambda: NOW, monotonic=lambda: 0)
        with self.assertRaises(ValueError):
            reports.call('prepare', {})
        transport.return_value = encode(fixture())
        with self.assertRaises(ValueError):
            reports.call('prepare', {})
        self.assertEqual(transport.call_count, 1)

    def test_malformed_publish_ack_cannot_be_retried(self):
        self.reports.call('prepare', {})
        transport = Mock(return_value=encode({'scope': 'private_review', 'synthetic': False,
                                              'result': {'status': 'saved_private',
                                                         'id': self.payload()['analysis_id']}}))
        self.reports._transport = transport
        with self.assertRaises(ValueError):
            self.reports.call('publish', self.payload())
        with self.assertRaises(ValueError):
            self.reports.call('publish', self.payload())
        self.assertEqual(transport.call_count, 1)

    def test_publish_before_prepare_and_invalid_payloads_never_send(self):
        with self.assertRaises(ValueError):
            self.reports.call('publish', self.payload())
        self.reports.call('prepare', {})
        for change in ({'analysis_id': '0' * 64}, {'summary': {'en': 'Only English'}},
                       {'summary': dict(prose(), en='x' * 601)},
                       {'summary': dict(prose(), en='https://example.invalid')},
                       {'notes': [{'finding_id': 'fake', 'commentary': prose()}]},
                       {'severity': 'warning'}, {'path': 'unrelated'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.reports.call('publish', dict(self.payload(), **change))
        self.assertEqual(self.transport.calls, [('prepare', b'')])

    def test_notes_require_known_unique_ids_and_all_translations(self):
        self.reports.call('prepare', {})
        identifier = self.transport.prepared['result']['analysis']['findings'][0]['id']
        valid = {'finding_id': identifier, 'commentary': prose()}
        for notes in ([valid, valid], [{'finding_id': identifier, 'commentary': {'en': 'Only English'}}]):
            with self.assertRaises(ValueError):
                self.reports.call('publish', dict(self.payload(), notes=notes))
        self.assertEqual(self.reports.call('publish', dict(self.payload(), notes=[valid]))['result']['status'],
                         'published')

    def test_expired_or_backwards_session_and_stale_analysis_fail_closed(self):
        self.reports.call('prepare', {})
        for elapsed in (-1, 241, float('nan'), float('inf')):
            self.elapsed = elapsed
            with self.subTest(elapsed=elapsed), self.assertRaises(ValueError):
                self.reports.call('prepare', {})
        self.elapsed = 10
        self.now = NOW + 1801
        with self.assertRaises(ValueError):
            self.reports.call('publish', self.payload())
        self.assertEqual(self.transport.calls, [('prepare', b'')])

    def test_transport_overrun_is_not_reported_as_success(self):
        def transport(method, body):
            self.elapsed = 241
            return self.transport(method, body)
        reports = adapter.Reports(transport, clock=lambda: NOW, monotonic=lambda: self.elapsed)
        with self.assertRaises(ValueError):
            reports.call('prepare', {})

    def test_reject_records_first_fixed_code_only_and_permanently_closes_session(self):
        self.reports.reject('protocol_call', 2, 'unknown')
        self.reports.reject('protocol_frame', 3, 'unknown')
        self.assertEqual(len(self.reports.rejections), 1)
        self.assertEqual(set(self.reports.rejections[0]), {'timestamp_utc', 'attempt', 'method', 'code'})
        with self.assertRaises(ValueError):
            self.reports.call('prepare', {})
        self.assertEqual(self.transport.calls, [])

    def test_close_prevents_further_calls(self):
        self.reports.close()
        with self.assertRaises(ValueError):
            self.reports.call('prepare', {})
        self.assertEqual(self.transport.calls, [])


def message(method, params=None, identifier=1):
    return {'jsonrpc': '2.0', 'id': identifier, 'method': method, 'params': params or {}}


INITIALIZE = message('initialize', {'protocolVersion': '2025-06-18'})


class ProtocolTests(unittest.TestCase):
    def run_messages(self, items, reports=None):
        reports = reports or Mock()
        source = io.BytesIO(b''.join(encode(item) + b'\n' for item in items))
        destination = io.BytesIO()
        adapter.serve(source, destination, reports)
        return [decode(row, adapter.MAX_RESPONSE * 2) for row in destination.getvalue().splitlines()], reports

    def test_discovery_has_honest_public_effects_and_no_network(self):
        transport = Mock()
        reports = adapter.Reports(transport)
        responses, _ = self.run_messages([INITIALIZE, message('tools/list'), message('ping')], reports)
        transport.assert_not_called()
        self.assertEqual(responses[0]['result']['serverInfo']['name'], 'home_energy_reports')
        self.assertEqual(responses[1]['result']['tools'], adapter.TOOLS)
        self.assertFalse(adapter.TOOLS[0]['annotations']['readOnlyHint'])
        self.assertIn('public dashboard', adapter.TOOLS[1]['description'])
        self.assertEqual([tool['name'] for tool in adapter.TOOLS], ['prepare', 'publish'])

    def test_observed_metadata_is_bounded_and_stripped(self):
        metadata = {'progressToken': 1, 'threadId': 'thread-1', 'callId': '', 'itemId': 'item',
                    'sessionId': 'session', 'windowId': 'window',
                    'x-codex-turn-metadata': {'nested': [{'ok': True}]}}
        reports = Mock()
        reports.call.return_value = fixture()
        responses, _ = self.run_messages([INITIALIZE, message('tools/call', {
            'name': 'prepare', 'arguments': {}, '_meta': metadata})], reports)
        self.assertIn('result', responses[-1])
        reports.call.assert_called_once_with('prepare', {})

    def test_invalid_metadata_stops_before_dispatch(self):
        for metadata in ({}, {'progressToken': True}, {'progressToken': 1, 'command': 'x'},
                         {'progressToken': 1, 'threadId': 'x' * 129},
                         {'progressToken': 1, 'callId': 'line\nbreak'},
                         {'progressToken': 1, 'windowId': 'x' * 257},
                         {'progressToken': 1, 'x-codex-turn-metadata': 'not an object'},
                         {'progressToken': 1, 'x-codex-turn-metadata': {'deep': {'a': {'b': {'c': {}}}}}}):
            with self.subTest(metadata=metadata):
                responses, reports = self.run_messages([INITIALIZE, message('tools/call', {
                    'name': 'prepare', 'arguments': {}, '_meta': metadata}), message('tools/list')])
                self.assertEqual(len(responses), 2)
                self.assertEqual(responses[-1]['error']['message'], adapter.ERROR)
                reports.call.assert_not_called()
                reports.reject.assert_called_once()

    def test_unknown_routes_fields_or_missing_initialization_rejected(self):
        for method, params in [('resources/list', {}), ('tools/call', {'name': 'exec', 'arguments': {}}),
                               ('tools/call', {'name': 'prepare', 'arguments': {}, 'path': 'x'}),
                               ('tools/list', {'cursor': 'x'}), ('initialize', {'protocolVersion': 'invalid'})]:
            with self.subTest(method=method, params=params):
                responses, reports = self.run_messages([INITIALIZE, message(method, params)])
                self.assertIn('error', responses[-1])
                reports.call.assert_not_called()
        responses, _ = self.run_messages([message('tools/list')])
        self.assertIn('error', responses[-1])

    def test_bad_frames_and_ids_never_echo_private_content(self):
        for raw in (b'{bad-json}\n', b'{"jsonrpc":"2.0","jsonrpc":"2.0"}\n',
                    b'{}', b'x' * (adapter.MAX_FRAME + 1),
                    encode(message('ping', identifier={'private': 'details'})) + b'\n'):
            destination = io.BytesIO()
            reports = Mock()
            adapter.serve(io.BytesIO(raw), destination, reports)
            response = decode(destination.getvalue(), 1024)
            self.assertIsNone(response['id'])
            self.assertEqual(response['error']['message'], adapter.ERROR)
            reports.call.assert_not_called()

    def test_tool_failures_are_redacted_and_stop_protocol(self):
        reports = Mock()
        reports.call.side_effect = OSError('credential detail and path')
        responses, _ = self.run_messages([INITIALIZE, message('tools/call', {
            'name': 'prepare', 'arguments': {}}), message('tools/list')], reports)
        self.assertEqual(len(responses), 2)
        self.assertEqual(responses[-1]['error']['message'], adapter.ERROR)
        self.assertNotIn('credential', str(responses))
        reports.reject.assert_called_once_with('tool_execution_rejected', 2, 'prepare')

    def test_frame_count_bounded_and_notifications_have_no_response(self):
        notification = {'jsonrpc': '2.0', 'method': 'notifications/initialized', 'params': {}}
        responses, _ = self.run_messages([INITIALIZE, notification] + [message('ping')] * 70)
        self.assertEqual(len(responses), 63)

    def test_direct_cli_rejects_before_project_imports(self):
        result = subprocess.run([sys.executable, '-I', '-B', str(Path(adapter.__file__))],
                                capture_output=True, timeout=5, check=False)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, b'')
        self.assertEqual(result.stderr.splitlines(), [b'Production reporting integration is not activated.'])


if __name__ == '__main__':
    unittest.main()
