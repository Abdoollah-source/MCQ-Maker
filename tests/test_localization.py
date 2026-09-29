from pathlib import Path
import tempfile
import unittest

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from mcq_maker.localization import active_language, apply_language, set_language, tr
from mcq_maker.settings import SettingsStore
from mcq_maker.settings_dialog import SettingsDialog
from mcq_maker.shell import MainWindow
from mcq_maker.template_repository import TemplateRepository
from mcq_maker.theme import apply_theme


BASE = Path(__file__).resolve().parents[1]


class LocalizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self):
        apply_language(self.app, 'en')

    def test_translation_lookup_falls_back_safely(self):
        set_language('ar')
        self.assertEqual(tr('nav.history'), 'السجل')
        self.assertEqual(tr('missing.key'), 'missing.key')
        set_language('en')
        self.assertEqual(tr('nav.history'), 'History')

    def test_default_language_is_arabic_and_persists(self):
        with tempfile.TemporaryDirectory(dir=BASE / 'artifacts') as temporary:
            store = SettingsStore(Path(temporary) / 'data')
            settings = store.defaults()
            self.assertEqual(settings['language'], 'ar')
            settings['language'] = 'en'
            store.save(settings)
            self.assertEqual(store.load()['language'], 'en')

    def test_arabic_editor_uses_the_arabic_capable_application_font(self):
        """Avoid the expensive Consolas fallback for Arabic editor placeholders."""
        apply_language(self.app, 'ar')
        apply_theme(self.app, False)
        self.assertIn(f'font-family: "{self.app.font().family()}";', self.app.styleSheet())
        self.assertNotIn('font-family: Consolas;', self.app.styleSheet())

        apply_language(self.app, 'en')
        apply_theme(self.app, False)
        self.assertIn('font-family: "Consolas";', self.app.styleSheet())

    def test_settings_language_control_and_sidebar_retranslate(self):
        with tempfile.TemporaryDirectory(dir=BASE / 'artifacts') as temporary:
            repository = TemplateRepository(Path(temporary) / 'library')
            repository.initialize()
            settings = SettingsStore(repository.root).defaults()
            apply_language(self.app, settings['language'])
            dialog = SettingsDialog(settings, repository.list_templates()['templates'])
            window = MainWindow(repository=repository, settings=settings)
            try:
                self.assertEqual(dialog.language.currentData(), 'ar')
                self.assertEqual(dialog.language.itemText(0), 'العربية')
                self.assertIn('إنشاء اختبار', window.nav_titles)
                self.assertEqual(window.create.import_button.text(), 'استيراد ملف')
                self.assertEqual(window.create.paste.text(), 'لصق من الحافظة')
                self.assertEqual(window.create.generate.text(), 'إنشاء الامتحان')
                self.assertEqual(window.create.editor.layoutDirection(), Qt.LeftToRight)
                self.assertEqual(self.app.layoutDirection(), Qt.RightToLeft)
                apply_language(self.app, 'en')
                window.retranslate()
                self.assertEqual(active_language(), 'en')
                self.assertIn('Create exam', window.nav_titles)
                self.assertEqual(window.create.import_button.text(), 'Import file')
                self.assertEqual(window.create.generate.text(), 'Generate exam')
                self.assertEqual(self.app.layoutDirection(), Qt.LeftToRight)
            finally:
                dialog.close()
                window.template_page.pool.waitForDone(2000)
                window.close()


if __name__ == '__main__':
    unittest.main()
