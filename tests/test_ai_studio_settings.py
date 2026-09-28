import tempfile
import unittest
from pathlib import Path

from mcq_maker.settings import SettingsError, SettingsStore


class AIStudioSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.store = SettingsStore(Path(self.temporary.name) / 'data')

    def tearDown(self):
        self.temporary.cleanup()

    def test_defaults_keep_ai_studio_preferences_empty(self):
        settings = self.store.defaults()
        self.assertIsNone(settings['ai_studio_brave_executable'])
        self.assertIsNone(settings['ai_studio_model'])
        self.assertIsNone(settings['ai_studio_thinking'])
        self.assertEqual(settings['ai_studio_parallel_tabs'], 1)

    def test_old_settings_load_with_new_defaults(self):
        old = self.store.defaults()
        old.pop('ai_studio_brave_executable')
        old.pop('ai_studio_model')
        old.pop('ai_studio_thinking')
        old.pop('ai_studio_parallel_tabs')
        self.store.root.mkdir(parents=True)
        self.store.path.write_text(__import__('json').dumps(old), encoding='utf-8')
        loaded = self.store.load()
        self.assertIsNone(loaded['ai_studio_brave_executable'])
        self.assertIsNone(loaded['ai_studio_model'])
        self.assertIsNone(loaded['ai_studio_thinking'])
        self.assertEqual(loaded['ai_studio_parallel_tabs'], 1)

    def test_preferences_persist_without_catalog_validation(self):
        settings = self.store.defaults()
        settings.update({
            'ai_studio_brave_executable': r'C:\\Tools\\Brave\\brave.exe',
            'ai_studio_model': 'A future model from AI Studio',
            'ai_studio_thinking': 'Very thorough',
            'ai_studio_parallel_tabs': 2,
        })
        self.store.save(settings)
        loaded = self.store.load()
        self.assertEqual(loaded['ai_studio_brave_executable'], r'C:\\Tools\\Brave\\brave.exe')
        self.assertEqual(loaded['ai_studio_model'], 'A future model from AI Studio')
        self.assertEqual(loaded['ai_studio_thinking'], 'Very thorough')
        self.assertEqual(loaded['ai_studio_parallel_tabs'], 2)

    def test_empty_brave_override_normalizes_to_none(self):
        settings = self.store.defaults()
        settings['ai_studio_brave_executable'] = '   '
        self.assertIsNone(self.store.save(settings)['ai_studio_brave_executable'])

    def test_malformed_preferences_are_rejected(self):
        settings = self.store.defaults()
        settings['ai_studio_brave_executable'] = 'relative\\brave.exe'
        with self.assertRaises(SettingsError):
            self.store.save(settings)
        settings = self.store.defaults()
        settings['ai_studio_model'] = ['not', 'a', 'string']
        with self.assertRaises(SettingsError):
            self.store.save(settings)
        settings = self.store.defaults()
        settings['ai_studio_thinking'] = 3
        with self.assertRaises(SettingsError):
            self.store.save(settings)
        settings = self.store.defaults()
        settings['ai_studio_parallel_tabs'] = 3
        with self.assertRaises(SettingsError):
            self.store.save(settings)


if __name__ == '__main__':
    unittest.main()
