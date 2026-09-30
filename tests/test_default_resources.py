from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch

from mcq_maker.app_data import TEST_DATA_ROOT_ENV, app_data_root
from mcq_maker.ai_studio_batch import default_batch_root
from mcq_maker import default_resources
from mcq_maker.default_resources import DEFAULT_RESOURCE_VERSION, DefaultResourceStore


BASE = Path(__file__).resolve().parents[1]


class DefaultResourceStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=BASE / 'artifacts')
        self.root = Path(self.temporary.name) / 'data'
        self.store = DefaultResourceStore(self.root)

    def tearDown(self):
        self.temporary.cleanup()

    def test_packaged_defaults_are_available_and_seeded_per_user(self):
        for kind in ('prompt', 'reference', 'template'):
            bundled = self.store.bundled_path(kind)
            seeded = self.store.default_path(kind)
            self.assertTrue(bundled.is_file())
            self.assertTrue(seeded.is_file())
            self.assertEqual(seeded.read_bytes(), bundled.read_bytes())
            self.assertEqual(seeded.parent, self.root / 'defaults' / DEFAULT_RESOURCE_VERSION / {
                'prompt': 'prompt', 'reference': 'reference', 'template': 'templates',
            }[kind])

    def test_seeding_is_idempotent_and_never_replaces_user_edits(self):
        prompt = self.store.default_path('prompt')
        prompt.write_text('user-owned prompt', encoding='utf-8')
        self.store.ensure_seeded()
        self.assertEqual(prompt.read_text(encoding='utf-8'), 'user-owned prompt')

    def test_early_batch_a_seed_is_migrated_without_losing_its_contents(self):
        legacy = self.store._legacy_seeded_path('prompt')
        legacy.parent.mkdir(parents=True)
        legacy.write_text('edited early seed', encoding='utf-8')
        prompt = self.store.default_path('prompt')
        self.assertEqual(prompt.read_text(encoding='utf-8'), 'edited early seed')
        self.assertEqual(legacy.read_text(encoding='utf-8'), 'edited early seed')

    def test_valid_custom_selection_is_preserved_and_missing_legacy_path_falls_back(self):
        custom = self.root / 'custom.txt'
        custom.parent.mkdir(parents=True)
        custom.write_text('custom prompt', encoding='utf-8')
        self.assertEqual(self.store.resolve_existing_or_default(custom, 'prompt'), custom)
        fallback = self.store.resolve_existing_or_default(
            self.root / 'Pompts' / 'PROMPT(MCQ-MAKER).txt', 'prompt',
        )
        self.assertEqual(fallback, self.store.default_path('prompt'))

    def test_frozen_build_resolver_uses_pyinstaller_resource_root(self):
        frozen_root = self.root / 'frozen'
        expected = frozen_root / 'mcq_maker' / 'resources'
        with patch.object(default_resources.sys, 'frozen', True, create=True), \
             patch.object(default_resources.sys, '_MEIPASS', str(frozen_root), create=True):
            self.assertEqual(default_resources.bundled_resource_root(), expected)

    def test_test_root_override_is_centralized_and_normal_root_is_unchanged(self):
        with patch.dict(os.environ, {TEST_DATA_ROOT_ENV: str(self.root / 'override')}):
            override = self.root / 'override'
            self.assertEqual(app_data_root(), override)
            self.assertEqual(DefaultResourceStore().default_path('prompt'),
                             override / 'defaults' / DEFAULT_RESOURCE_VERSION / 'prompt' / 'PROMPT(MCQ-MAKER).txt')
            self.assertEqual(default_batch_root(), override / 'ai_studio' / 'batches')
        with patch.dict(os.environ, {}, clear=True), \
             patch('mcq_maker.app_data.user_data_path', return_value=self.root / 'normal'):
            self.assertEqual(app_data_root(), self.root / 'normal')


if __name__ == '__main__':
    unittest.main()
