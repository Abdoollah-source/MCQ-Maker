import json
from pathlib import Path
import tempfile
import unittest

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication

from mcq_maker.settings import SettingsStore
from mcq_maker.settings_dialog import SettingsDialog
from mcq_maker.shell import MainWindow
from mcq_maker.template_repository import TemplateRepository
from mcq_maker.theme import apply_theme
from mcq_maker.components import Dropdown
from PySide6.QtTest import QTest

BASE = Path(__file__).resolve().parents[1]


class SettingsStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=BASE/'artifacts')
        self.root = Path(self.temporary.name)/'appdata'
        self.store = SettingsStore(self.root)

    def tearDown(self):
        self.temporary.cleanup()

    def test_defaults_save_backup_and_reload(self):
        settings = self.store.load()
        self.assertEqual(settings['theme'], 'system')
        self.assertTrue(Path(settings['output_folder']).is_absolute())
        settings['theme'] = 'dark'
        settings['manual_conflicts'] = 'ask'
        self.store.save(settings)
        settings['theme'] = 'light'
        self.store.save(settings)
        self.assertTrue(self.store.backup.exists())
        restored = self.store.load()
        self.assertEqual(restored['theme'], 'light')
        self.assertEqual(restored['manual_conflicts'], 'ask')

    def test_corrupt_settings_are_preserved_and_defaults_recovered(self):
        self.root.mkdir(parents=True)
        self.store.path.write_text('{broken', encoding='utf-8')
        recovered = self.store.load()
        self.assertEqual(recovered['manual_conflicts'], 'save_copy')
        self.assertFalse(self.store.path.exists())
        self.assertEqual(len(list(self.root.glob('settings.corrupt.*.json'))), 1)

    def test_invalid_values_are_rejected(self):
        settings = self.store.defaults()
        settings['theme'] = 'purple'
        with self.assertRaises(Exception):
            self.store.save(settings)

    def test_ai_studio_preferences_survive_save_and_reload(self):
        settings = self.store.defaults()
        settings['ai_studio_brave_executable'] = r'C:\\Program Files\\BraveSoftware\\Brave-Browser\\Application\\brave.exe'
        settings['ai_studio_model'] = 'Gemini available later'
        settings['ai_studio_thinking'] = 'High'
        self.store.save(settings)
        restored = self.store.load()
        self.assertEqual(restored['ai_studio_brave_executable'], settings['ai_studio_brave_executable'])
        self.assertEqual(restored['ai_studio_model'], 'Gemini available later')
        self.assertEqual(restored['ai_studio_thinking'], 'High')


class SettingsDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_dialog_exposes_all_persisted_options(self):
        with tempfile.TemporaryDirectory(dir=BASE/'artifacts') as root:
            repository = TemplateRepository(Path(root)/'library')
            repository.initialize()
            store = SettingsStore(repository.root)
            settings = store.defaults()
            templates = repository.list_templates()['templates']
            dialog = SettingsDialog(settings, templates)
            self.assertTrue(dialog.output_folder.text())
            self.assertEqual(dialog.conflicts.count(), 2)
            self.assertEqual(dialog.close_behavior.count(), 2)
            self.assertEqual(len(dialog.theme_group.buttons()), 3)
            self.assertEqual(dialog.brave_executable.text(), '')
            values, template_id = dialog.values(settings)
            self.assertEqual(values['output_folder'], settings['output_folder'])
            self.assertIsNone(values['ai_studio_brave_executable'])
            self.assertEqual(template_id, templates[0]['id'])
            dialog.close()

    def test_dialog_never_scrolls_sideways_and_controls_have_readable_height(self):
        with tempfile.TemporaryDirectory(dir=BASE/'artifacts') as root:
            repository = TemplateRepository(Path(root)/'library')
            repository.initialize()
            settings = SettingsStore(repository.root).defaults()
            apply_theme(self.app, False)
            dialog = SettingsDialog(settings, repository.list_templates()['templates'])
            try:
                dialog.show()
                QTest.qWait(100)
                self.app.processEvents()
                self.assertEqual(dialog.scroll.horizontalScrollBar().maximum(), 0)
                for combo in (dialog.default_template, dialog.conflicts, dialog.close_behavior):
                    self.assertGreaterEqual(combo.height(), 36)
                    self.assertGreaterEqual(combo.view().sizeHintForRow(0), 36)
                    self.assertLessEqual(combo.geometry().bottom(), combo.parentWidget().rect().bottom())
                dialog.default_template.setFocus()
                self.app.processEvents()
                focused = dialog.default_template.grab().toImage()
                self.assertEqual(
                    focused.pixelColor(focused.width() // 2, focused.height() - 1).name(),
                    '#79506e',
                )
            finally:
                dialog.close()

    def test_theme_choice_applies_and_saves_immediately(self):
        with tempfile.TemporaryDirectory(dir=BASE/'artifacts') as root:
            repository = TemplateRepository(Path(root)/'library')
            repository.initialize()
            store = SettingsStore(repository.root)
            settings = store.defaults()
            apply_theme(self.app, False)
            window = MainWindow(
                repository=repository,
                settings=settings,
                settings_store=store,
                apply_theme_callback=lambda preference: apply_theme(self.app, preference == 'dark'),
            )
            try:
                window.show()
                window.open_settings()
                QTest.qWait(100)
                dialog = window.settings_dialog
                dialog.theme_buttons['dark'].click()
                self.app.processEvents()
                self.assertEqual(self.app.palette().window().color().name(), '#201e20')
                self.assertEqual(store.load()['theme'], 'dark')
                dialog.theme_buttons['light'].click()
                self.app.processEvents()
                self.assertEqual(self.app.palette().window().color().name(), '#f5f3ef')
                self.assertEqual(store.load()['theme'], 'light')
            finally:
                if window.settings_dialog is not None:
                    window.settings_dialog.close()
                window.close()
                self.app.processEvents()

    def test_dropdown_direction_depends_on_space_not_selected_row(self):
        apply_theme(self.app, False)
        dropdown = Dropdown()
        dropdown.addItems(['First option', 'Second option'])
        dropdown.resize(320, dropdown.sizeHint().height())
        dropdown.show()
        QTest.qWait(50)
        tops = []
        try:
            for index in (0, 1):
                dropdown.setCurrentIndex(index)
                dropdown.showPopup()
                QTest.qWait(50)
                tops.append(dropdown.view().window().geometry().top())
                self.assertEqual(tops[-1], dropdown.mapToGlobal(QPoint(0, dropdown.height())).y())
                dropdown.hidePopup()
            self.assertEqual(tops[0], tops[1])
        finally:
            dropdown.hidePopup()
            dropdown.close()

    def test_main_window_applies_saved_output_and_persists_geometry(self):
        with tempfile.TemporaryDirectory(dir=BASE/'artifacts') as root:
            repository = TemplateRepository(Path(root)/'library')
            store = SettingsStore(repository.root)
            settings = store.defaults()
            settings['output_folder'] = str(Path(root)/'chosen output')
            store.save(settings)
            window = MainWindow(repository=repository, settings=settings, settings_store=store)
            try:
                window.show()
                QTest.qWait(100)
                window.template_page.pool.waitForDone(2000)
                self.app.processEvents()
                self.assertEqual(window.create.output_folder, Path(root)/'chosen output')
                window.resize(900, 650)
            finally:
                window.close()
                self.app.processEvents()
            saved = store.load()
            self.assertEqual(saved['window']['width'], window.frameGeometry().width())


if __name__ == '__main__':
    unittest.main()
