"""Phase-A deployment contract tests. Never connect to or modify a real server."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


HELPER = Path(__file__).resolve().parents[1] / 'deploy/activate-insights.py'
SPEC = importlib.util.spec_from_file_location('insights_deploy', HELPER)
deploy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(deploy)


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        (self.root / 'server').mkdir()
        (self.root / 'deploy').mkdir()
        self.html = b'<link href="insights.css?v=translated2"><script src="insights.js?v=translated2"></script>'
        for name in deploy.SOURCE_FILES:
            (self.root / name).write_bytes(self.html if name == 'index.html' else b'# fixture\n')
        (self.root / 'deploy/activate-insights.py').write_bytes(HELPER.read_bytes())

    @staticmethod
    def config():
        return b'map { "~^(200|304):/(styles\\.css' + deploy.MAP_END + b'" immutable; }\nserver {\n' + deploy.ANCHOR + b'}\n'

    def test_manifest_has_only_explicit_sources_and_pinned_baseline(self):
        manifest = deploy.build_manifest(self.root)
        self.assertEqual(manifest['baseline'], deploy.BASELINE)
        self.assertEqual(set(manifest['files']), set(deploy.SOURCE_FILES))
        self.assertEqual(manifest['versions'], {'insights.js': 'translated2', 'insights.css': 'translated2'})
        self.assertEqual(manifest['helper_sha256'], deploy.digest(HELPER.read_bytes()))
        self.assertEqual(manifest['files']['index.html'], deploy.digest(self.html))

    def test_manifest_generation_does_not_write(self):
        before = {str(path): path.read_bytes() for path in self.root.rglob('*') if path.is_file()}
        deploy.build_manifest(self.root)
        after = {str(path): path.read_bytes() for path in self.root.rglob('*') if path.is_file()}
        self.assertEqual(before, after)

    def test_assets_must_have_one_strict_versioned_reference(self):
        for html in (b'', self.html + self.html, self.html.replace(b'translated2', b'../../key'),
                     self.html.replace(b'insights.js?v=translated2', b'insights.js')):
            with self.subTest(html=html), self.assertRaises(RuntimeError):
                deploy.asset_versions(html)

    def test_configuration_changes_only_three_routes_and_asset_cache_names(self):
        original = self.config()
        candidate = deploy.candidate_config(original)
        self.assertEqual(candidate.count(b'location = /insights.js'), 1)
        self.assertEqual(candidate.count(b'location = /insights.css'), 1)
        self.assertEqual(candidate.count(b'location = /reports/index.json'), 1)
        self.assertIn(b'limit_except GET { deny all; }', candidate)
        self.assertEqual(candidate.replace(deploy.NEW_MAP_END, deploy.MAP_END, 1).replace(deploy.ROUTES, b'', 1), original)

    def test_configuration_refuses_repeat_ambiguous_or_existing_report_routes(self):
        original = self.config()
        for body in (original + deploy.ANCHOR, original.replace(deploy.MAP_END, b'missing'),
                     original + b'location /reports/ {}\n', deploy.candidate_config(original)):
            with self.subTest(body=body), self.assertRaises(RuntimeError):
                deploy.candidate_config(body)

    def test_fixed_empty_feed_uses_translated_schema(self):
        self.assertEqual(json.loads(deploy.EMPTY_FEED), {'schema_version': 2, 'reports': []})

    def test_source_read_is_bounded_and_regular_file_only(self):
        with self.assertRaises(RuntimeError):
            deploy.read_file(self.root / 'index.html', limit=5)
        with self.assertRaises(RuntimeError):
            deploy.read_file(self.root / 'server')

    def test_noncanonical_paths_are_refused(self):
        with self.assertRaises(RuntimeError):
            deploy.canonical(Path('relative'))
        with self.assertRaises(RuntimeError):
            deploy.canonical(self.root / 'server/../index.html')

    def test_symlink_source_and_destination_are_refused(self):
        link = self.root / 'linked.html'
        try:
            link.symlink_to(self.root / 'index.html')
        except OSError:
            self.skipTest('This Windows host does not permit test symlinks')
        with self.assertRaises(RuntimeError):
            deploy.read_file(link)
        with self.assertRaises(RuntimeError):
            deploy.absent(link)

    def test_write_allowlist_refuses_unrelated_file_before_mutation(self):
        sentinel = self.root / 'unrelated.txt'
        sentinel.write_bytes(b'unchanged')
        with self.assertRaises(RuntimeError):
            deploy.atomic(sentinel, b'changed', [0, 0, 0o644])
        self.assertEqual(sentinel.read_bytes(), b'unchanged')

    def test_rollback_never_overwrites_a_later_change(self):
        with patch.object(deploy, 'read_file', return_value=b'later administrator update'), \
                patch.object(deploy, 'atomic') as write, self.assertRaises(RuntimeError):
            deploy.restore_original(deploy.CONFIG, b'old', b'new', [0, 0, 0o644])
        write.assert_not_called()

    def test_rollback_skips_untouched_file_and_restores_exact_release(self):
        with patch.object(deploy, 'read_file', return_value=b'old'), patch.object(deploy, 'atomic') as write:
            deploy.restore_original(deploy.CONFIG, b'old', b'new', [0, 0, 0o644])
            write.assert_not_called()
        with patch.object(deploy, 'read_file', return_value=b'new'), patch.object(deploy, 'atomic') as write:
            deploy.restore_original(deploy.CONFIG, b'old', b'new', [0, 0, 0o644])
            write.assert_called_once_with(deploy.CONFIG, b'old', [0, 0, 0o644])

    def test_failed_overview_rollback_does_not_skip_nginx_or_http_check(self):
        with patch.object(deploy, 'restore_original', side_effect=[RuntimeError('Later Overview change'), None]) as restore, \
                patch.object(deploy.subprocess, 'run') as run, patch.object(deploy, 'verify_http') as verify, \
                self.assertRaisesRegex(RuntimeError, 'administrator recovery required'):
            deploy.rollback_frontend(b'old-index', b'old-nginx', b'new-nginx', {'index.html': b'new-index'},
                                     [1, 1, 0o664], [0, 0, 0o644], True, True, {}, {})
        self.assertEqual(restore.call_count, 2)
        self.assertEqual(restore.call_args_list[1].args[0], deploy.CONFIG)
        self.assertEqual(run.call_count, 2)
        verify.assert_called_once()

    def test_rollback_http_failure_prevents_success_claim(self):
        with patch.object(deploy, 'restore_original'), patch.object(deploy.subprocess, 'run'), \
                patch.object(deploy, 'verify_http', side_effect=RuntimeError('HTTP mismatch')), \
                self.assertRaisesRegex(RuntimeError, 'Previous HTTP routes'):
            deploy.rollback_frontend(b'old-index', b'old-nginx', b'new-nginx', {'index.html': b'new-index'},
                                     [1, 1, 0o664], [0, 0, 0o644], True, True, {}, {})

    def test_staging_path_is_not_caller_selected_or_an_arbitrary_repository(self):
        with self.assertRaisesRegex(RuntimeError, 'Unexpected staging'):
            deploy.read_bundle(self.root)

    def test_security_and_cache_requirements(self):
        headers = {'strict-transport-security': 'max-age=31536000',
                   'content-security-policy': "script-src 'self'; frame-ancestors 'none'",
                   'x-content-type-options': 'nosniff', 'x-frame-options': 'DENY',
                   'referrer-policy': 'no-referrer', 'cache-control': 'no-store, max-age=0'}
        deploy.security(headers)
        for key in deploy.SECURITY_HEADERS:
            broken = dict(headers)
            broken.pop(key)
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                deploy.security(broken)
        with self.assertRaises(RuntimeError):
            deploy.security(headers, immutable=True)
        headers['cache-control'] = 'public, max-age=31536000, immutable'
        deploy.security(headers, immutable=True)
        with self.assertRaises(RuntimeError):
            deploy.security(headers)

    def test_duplicate_manifest_keys_rejected(self):
        with self.assertRaises(RuntimeError):
            json.loads('{"files":{},"files":{}}', object_pairs_hook=deploy.unique_pairs)

    def test_no_action_without_explicit_mode(self):
        with patch.object(deploy.sys, 'argv', ['activate-insights.py']), \
                patch.object(deploy, 'activate') as activate, self.assertRaises(SystemExit):
            deploy.main()
        activate.assert_not_called()


if __name__ == '__main__':
    unittest.main()
