"""Publisher contract and capability tests, using only isolated temporary state."""
import copy
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import insights_publish as publisher


def localized(en='Recorded house use increased.', el='Η καταγεγραμμένη κατανάλωση αυξήθηκε.',
              ru='Записанное потребление дома выросло.'):
    return {'en': en, 'el': el, 'ru': ru}


def analysis(now):
    return {'schema_version': 2, 'generated_at': now, 'window': {'start': now - 86400, 'end': now},
            'baseline': {'start': now - 8 * 86400, 'end': now - 86400, 'days': 7},
            'data_through': now, 'coverage_pct': 100, 'severity': 'notice', 'metrics': [],
            'findings': [{'id': 'house_high', 'category': 'house', 'severity': 'notice',
                          'title': localized(), 'observation': localized(),
                          'start': now - 86400, 'end': now, 'metric': 'load'}], 'limitations': []}


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.state = self.root / 'reports'
        self.state.mkdir()
        (self.state / 'public').mkdir()
        self.history = self.root / 'history'
        self.history.mkdir()
        self.sentinel = self.root / 'unrelated.txt'
        self.sentinel.write_text('unchanged')
        self.now = 1791800000
        self.mock = patch.object(publisher, 'analyze', side_effect=lambda history, now: analysis(now))
        self.mock.start()
        self.addCleanup(self.mock.stop)
        self.snapshot = publisher.prepare(self.state, self.history, self.now)
        self.payload = {'analysis_id': self.snapshot['analysis_id'], 'summary': localized(),
                        'notes': [{'finding_id': 'house_high', 'commentary': localized(
                            'Review the corresponding History period.', 'Ελέγξτε την αντίστοιχη περίοδο στο Ιστορικό.',
                            'Просмотрите соответствующий период в истории.')}]}

    def submit(self, payload=None, now=None):
        return publisher.publish(publisher.encoded(self.payload if payload is None else payload),
                                 self.state, self.now if now is None else now)

    def test_publish_uses_server_analysis_and_leaves_other_files_untouched(self):
        self.assertEqual(self.submit()['status'], 'published')
        feed = json.loads((self.state / 'public/index.json').read_text(encoding='utf-8'))
        self.assertEqual(feed['reports'][0]['analysis'], self.snapshot['analysis'])
        self.assertEqual(self.sentinel.read_text(encoding='utf-8'), 'unchanged')
        self.assertEqual(list(self.history.iterdir()), [])

    def test_idempotent_retry_and_immutable_report(self):
        self.submit()
        self.assertEqual(self.submit()['status'], 'already_published')
        changed = copy.deepcopy(self.payload)
        changed['summary'] = 'Replacement attempt'
        with self.assertRaises(publisher.Rejected):
            self.submit(changed)
        with closing(sqlite3.connect(self.state / 'reports.sqlite3')) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM reports').fetchone()[0], 1)

    def test_identical_retry_does_not_rewrite_healthy_feed(self):
        self.submit()
        with patch.object(publisher.os, 'replace', side_effect=AssertionError('Unnecessary rewrite')):
            self.assertEqual(self.submit()['status'], 'already_published')

    def test_real_analyzer_snapshot_can_be_published(self):
        # Use the real reader against empty recorded history, not the mocked digest.
        self.mock.stop()
        snapshot = publisher.prepare(self.state, self.history, self.now + 61)
        self.assertEqual(snapshot['analysis']['severity'], 'data_issue')
        self.assertEqual(snapshot['analysis']['coverage_pct'], 0)
        self.submit({'analysis_id': snapshot['analysis_id'], 'summary': localized(
            'No recorded readings were available for this review.', 'Δεν υπήρχαν καταγεγραμμένες μετρήσεις για αυτή την ανασκόπηση.',
            'Для этого обзора записанные показания отсутствуют.'), 'notes': []}, self.now + 61)
        feed = json.loads((self.state / 'public/index.json').read_text(encoding='utf-8'))
        self.assertEqual(feed['reports'][0]['analysis'], snapshot['analysis'])

    def test_concurrent_sessions_cannot_bypass_global_cooldown(self):
        next_snapshot = publisher.prepare(self.state, self.history, self.now + 61)
        payloads = [self.payload, dict(self.payload, analysis_id=next_snapshot['analysis_id'])]

        def attempt(payload):
            try:
                return self.submit(payload, self.now + 61)['status']
            except publisher.Rejected:
                return 'rejected'

        with ThreadPoolExecutor(max_workers=2) as workers:
            self.assertCountEqual(list(workers.map(attempt, payloads)), ['published', 'rejected'])
        feed = json.loads((self.state / 'public/index.json').read_text(encoding='utf-8'))
        self.assertEqual(len(feed['reports']), 1)

    def test_retry_repairs_feed_after_failed_write(self):
        with patch.object(publisher, 'write_feed', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.submit()
        self.assertEqual(self.submit()['status'], 'already_published')
        self.assertEqual(len(json.loads((self.state / 'public/index.json').read_text(encoding='utf-8'))['reports']), 1)

    def test_expired_unknown_and_future_snapshots_rejected(self):
        for timestamp in [self.now - 1, self.now + publisher.SNAPSHOT_LIFETIME + 1]:
            with self.assertRaises(publisher.Rejected):
                self.submit(now=timestamp)
        changed = dict(self.payload, analysis_id='a' * 64)
        with self.assertRaises(publisher.Rejected):
            self.submit(changed)

    def test_rejects_fields_outside_report_contract(self):
        for key in ['path', 'filename', 'command', 'analysis', 'severity', 'published_at', 'url']:
            with self.subTest(key=key), self.assertRaises(publisher.Rejected):
                self.submit(dict(self.payload, **{key: '/etc/passwd'}))

    def test_rejects_script_links_control_characters_and_oversized_text(self):
        for value in ['<script>alert(1)</script>', 'https://example.com', 'javascript:alert(1)',
                      'data:text/plain,abc', 'a\u202eb', 'a\nb', '', ' ' * 10, 'a' * 601]:
            for language in publisher.LANGUAGES:
                with self.subTest(value=value[:20], language=language), self.assertRaises(publisher.Rejected):
                    self.submit(dict(self.payload, summary=dict(localized(), **{language: value})))

    def test_every_language_required_for_summary_and_each_note(self):
        invalid = ['English only', {'en': 'English only'}, dict(localized(), de='Nicht erlaubt'),
                   dict(localized(), ru=''), dict(localized(), el=None)]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(publisher.Rejected):
                self.submit(dict(self.payload, summary=value))
            with self.subTest(note=value), self.assertRaises(publisher.Rejected):
                self.submit(dict(self.payload, notes=[{'finding_id': 'house_high', 'commentary': value}]))

    def test_multibyte_payload_and_all_translations_survive_round_trip(self):
        payload = copy.deepcopy(self.payload)
        payload['summary'] = localized('E' * 600, 'Ω' * 600, 'Я' * 600)
        self.submit(payload)
        feed = json.loads((self.state / 'public/index.json').read_text(encoding='utf-8'))
        self.assertEqual(feed['schema_version'], 2)
        self.assertEqual(feed['reports'][0]['narrative']['summary'], payload['summary'])

    def test_oversized_utf8_is_rejected_by_bytes_not_characters(self):
        payload = dict(self.payload, summary=localized('😀' * 600, '😀' * 600, '😀' * 600),
                       notes=[{'finding_id': str(index), 'commentary': localized('😀' * 400, '😀' * 400, '😀' * 400)}
                              for index in range(6)])
        raw = publisher.encoded(payload)
        self.assertGreater(len(raw), publisher.MAX_INPUT)
        with self.assertRaises(publisher.Rejected):
            publisher.decode(raw)

    def test_byte_bounded_feed_retains_newest_and_preserves_archive(self):
        self.submit()
        next_snapshot = publisher.prepare(self.state, self.history, self.now + 300)
        self.submit(dict(self.payload, analysis_id=next_snapshot['analysis_id']), self.now + 300)
        with publisher.database(self.state) as db:
            stored = json.loads(db.execute('SELECT report FROM reports ORDER BY published DESC LIMIT 1').fetchone()[0])
            one_report_bytes = len(publisher.encoded({'schema_version': 2, 'reports': [stored]}))
            with patch.object(publisher, 'MAX_FEED', one_report_bytes):
                publisher.write_feed(self.state, db)
            self.assertEqual(db.execute('SELECT count(*) FROM reports').fetchone()[0], 2)
        feed = json.loads((self.state / 'public/index.json').read_text(encoding='utf-8'))
        self.assertEqual([r['id'] for r in feed['reports']], [next_snapshot['analysis_id']])

    def test_legacy_snapshot_is_not_published(self):
        with publisher.database(self.state) as db, db:
            legacy = dict(self.snapshot['analysis'], schema_version=1)
            db.execute('UPDATE snapshots SET analysis=?', (publisher.encoded(legacy).decode(),))
        with self.assertRaises(publisher.Rejected):
            self.submit()

    def test_unknown_duplicate_or_malformed_notes_rejected(self):
        for notes in [[{'finding_id': '../x', 'commentary': 'Comment'}], self.payload['notes'] * 2,
                      [{'finding_id': 'house_high', 'commentary': 12}], 'notes']:
            with self.subTest(notes=notes), self.assertRaises(publisher.Rejected):
                self.submit(dict(self.payload, notes=notes))

    def test_invalid_json_and_duplicate_keys(self):
        for raw in [b'{}{}', b'NaN', b'\xff', b'a' * (publisher.MAX_INPUT + 1), b'{"x":1,"x":2}', b'[]']:
            with self.subTest(raw=raw[:20]), self.assertRaises((publisher.Rejected, ValueError)):
                publisher.publish(raw, self.state, self.now)

    def test_prepare_reuses_recent_snapshot_and_rate_limits(self):
        self.assertEqual(publisher.prepare(self.state, self.history, self.now + 10), self.snapshot)
        for offset in range(1, 12):
            publisher.prepare(self.state, self.history, self.now + offset * 61)
        with self.assertRaises(publisher.Rejected):
            publisher.prepare(self.state, self.history, self.now + 12 * 61)

    def test_global_publication_cooldown_and_newest_first(self):
        self.submit()
        next_snapshot = publisher.prepare(self.state, self.history, self.now + 100)
        next_payload = dict(self.payload, analysis_id=next_snapshot['analysis_id'])
        with self.assertRaises(publisher.Rejected):
            self.submit(next_payload, self.now + 100)
        self.submit(next_payload, self.now + 300)
        feed = json.loads((self.state / 'public/index.json').read_text(encoding='utf-8'))
        self.assertEqual(feed['reports'][0]['id'], next_snapshot['analysis_id'])

    def test_global_daily_limit(self):
        for offset in range(12):
            now = self.now + offset * 3600
            snapshot = publisher.prepare(self.state, self.history, now)
            self.submit(dict(self.payload, analysis_id=snapshot['analysis_id']), now)
        snapshot = publisher.prepare(self.state, self.history, self.now + 12 * 3600)
        with self.assertRaises(publisher.Rejected):
            self.submit(dict(self.payload, analysis_id=snapshot['analysis_id']), self.now + 12 * 3600)

    def test_command_dispatch_refuses_shell_and_arguments(self):
        for command in ['', 'sh', 'publish; id', 'prepare /etc/passwd', 'cat /etc/passwd', 'scp -t /tmp/x']:
            with self.subTest(command=command), patch.dict(os.environ, {'SSH_ORIGINAL_COMMAND': command}), \
                    patch.object(publisher.sys, 'argv', ['insights_publish.py']), self.assertRaises(publisher.Rejected):
                publisher.main()

    def test_symlink_feed_rejected_when_supported(self):
        try:
            (self.state / 'public/index.json').symlink_to(self.sentinel)
        except OSError:
            self.skipTest('Symlink creation requires privileges on this host')
        with self.assertRaises(publisher.Rejected):
            self.submit()
        self.assertEqual(self.sentinel.read_text(encoding='utf-8'), 'unchanged')


if __name__ == '__main__':
    unittest.main()
