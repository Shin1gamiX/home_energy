"""Source-only loader fixtures; no private files, model, server or subprocess."""
import hashlib
import importlib
import importlib.machinery
import importlib.util
import marshal
from pathlib import Path
import struct
import sys
import tempfile
from types import ModuleType
import unittest
from unittest.mock import Mock, patch

import insights_private_review_run as run


class SourceOnlyImportTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(directory).resolve()
        self.names = []
        self.loaders = []
        self.addCleanup(self.clean_imports)

    def clean_imports(self):
        for loader in self.loaders:
            if loader in sys.meta_path:
                sys.meta_path.remove(loader)
        for name in self.names:
            sys.modules.pop(name, None)

    def fixture(self, name='insights_source_only_fixture', body=b'value = "approved"\n'):
        self.names.append(name)
        path = self.root / (name + '.py')
        path.write_bytes(body)
        return name, path, {name: hashlib.sha256(body).hexdigest()}

    def install(self, pins):
        loader = run.source_loader(self.root, pins)
        sys.meta_path.insert(0, loader)
        self.loaders.append(loader)
        return loader

    def test_eligible_poisoned_bytecode_is_ignored_and_preserved(self):
        name, path, pins = self.fixture()
        info = path.stat()
        # A valid timestamp/size header makes this altered body eligible to the
        # ordinary source loader even though the .py file contains approved code.
        poison = compile('value = "poisoned"\n', str(path), 'exec')
        body = (importlib.util.MAGIC_NUMBER + struct.pack('<III', 0, int(info.st_mtime), info.st_size)
                + marshal.dumps(poison))
        cache = Path(importlib.util.cache_from_source(str(path)))
        cache.parent.mkdir()
        cache.write_bytes(body)
        ordinary = importlib.machinery.SourceFileLoader(name, str(path)).get_code(name)
        self.assertIn('poisoned', ordinary.co_consts)
        loader = self.install(pins)
        module = importlib.import_module(name)
        self.assertEqual(module.value, 'approved')
        self.assertIs(module.__loader__, loader)
        self.assertEqual(module.__file__, str(path))
        self.assertIsNone(module.__cached__)
        self.assertEqual(cache.read_bytes(), body)
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), pins[name])

    def test_pinned_source_is_frozen_before_import_without_cache_writes(self):
        name, path, pins = self.fixture()
        self.install(pins)
        path.write_bytes(b'value = "later_changed"\n')
        module = importlib.import_module(name)
        self.assertEqual(module.value, 'approved')
        self.assertFalse(Path(importlib.util.cache_from_source(str(path))).exists())

    def test_wrong_digest_refused_before_import(self):
        name, _, _ = self.fixture()
        with self.assertRaisesRegex(ValueError, '^private_review_source_pin_changed$'):
            self.install({name: '0' * 64})
        self.assertNotIn(name, sys.modules)

    def test_preloaded_module_refused_even_with_expected_origin(self):
        name, path, pins = self.fixture()
        existing = ModuleType(name)
        existing.__file__ = str(path)
        sys.modules[name] = existing
        with self.assertRaisesRegex(ValueError, '^preloaded_private_review_module$'):
            self.install(pins)

    def test_unknown_project_module_cannot_fall_back_to_path_imports(self):
        name, _, pins = self.fixture()
        self.install(pins)
        unknown, _, _ = self.fixture('insights_unreviewed_fixture')
        with patch.object(sys, 'path', [str(self.root), *sys.path]), \
                self.assertRaisesRegex(ValueError, '^unreviewed_private_review_import$'):
            importlib.import_module(unknown)
        self.assertNotIn(unknown, sys.modules)
        self.assertNotIn(name, sys.modules)

    def test_standard_library_imports_are_not_intercepted(self):
        _, _, pins = self.fixture()
        loader = self.install(pins)
        self.assertIsNone(loader.find_spec('json'))
        self.assertIsNone(loader.find_spec('xml.etree.ElementTree'))

    def test_dependency_loaded_only_from_complete_verified_closure(self):
        parent, _, pins = self.fixture(body=b'import insights_dependency_fixture\nvalue = insights_dependency_fixture.value\n')
        child, _, child_pins = self.fixture('insights_dependency_fixture')
        loader = self.install(pins)
        checker = Mock()
        verified = run.pinned_sources(self.root, child_pins, checker)
        checker.assert_called_once_with(self.root / (child + '.py'), child_pins[child])
        loader.extend(verified)
        module = importlib.import_module(parent)
        self.assertEqual(module.value, 'approved')
        self.assertIs(sys.modules[child].__loader__, loader)

    def test_reload_and_duplicate_extension_refused(self):
        name, _, pins = self.fixture()
        loader = self.install(pins)
        module = importlib.import_module(name)
        with self.assertRaisesRegex(ValueError, '^unreviewed_private_review_import$'):
            importlib.reload(module)
        with self.assertRaisesRegex(ValueError, '^duplicate_private_review_source$'):
            loader.extend(dict(loader.sources))

    def test_invalid_names_pins_and_oversized_source_rejected(self):
        name, path, pins = self.fixture()
        for invalid in ({'../outside': 'a' * 64}, {name: 'invalid'}, {'json': 'a' * 64}):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError,
                    '^invalid_private_review_source_pin$'):
                self.install(invalid)
        path.write_bytes(b'x' * (512 * 1024 + 1))
        with self.assertRaisesRegex(ValueError, '^invalid_private_review_source$'):
            self.install(pins)

    def test_path_pin_checker_failure_cannot_be_bypassed_by_matching_bytes(self):
        _, _, pins = self.fixture()
        checker = Mock(side_effect=ValueError('fixture_path_rejected'))
        with self.assertRaisesRegex(ValueError, '^fixture_path_rejected$'):
            run.pinned_sources(self.root, pins, checker)


if __name__ == '__main__':
    unittest.main()
