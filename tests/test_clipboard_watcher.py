import json
from pathlib import Path
import tempfile
import time
import unittest

from PySide6.QtCore import QObject, Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from mcq_maker.clipboard_watcher import ClipboardWatcher, recognizable_quiz_text
from mcq_maker.history import HistoryRepository
from mcq_maker.localization import apply_language
from mcq_maker.settings import SettingsStore
from mcq_maker.shell import MainWindow, sheet_icon
from mcq_maker.template_repository import TemplateRepository
from mcq_maker.tray import TrayController


BASE = Path(__file__).resolve().parents[1]
QUIZ = json.dumps([
    {'title': 'Clipboard review'},
    {'question': 'Which answer is first?', 'options': ['A', 'B', 'C', 'D'],
     'correct': 0, 'explanation': 'A is first.'},
])


class FakeClipboard(QObject):
    dataChanged = Signal()

    def __init__(self):
        super().__init__()
        self.value = ''

    def text(self):
        return self.value

    def copy(self, text):
        self.value = text
        self.dataChanged.emit()


class BrokenClipboard(FakeClipboard):
    def text(self):
        raise RuntimeError('clipboard unavailable')


class ClipboardWatcherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=BASE / 'artifacts')
        self.root = Path(self.temporary.name)
        self.repository = TemplateRepository(self.root / 'library')
        self.repository.initialize()
        self.history = HistoryRepository(self.repository.root)
        self.history.initialize()
        self.settings = SettingsStore(self.repository.root).defaults()
        self.settings['output_folder'] = str(self.root / 'output')
        self.settings['clipboard_watcher'] = True
        self.clipboard = FakeClipboard()

    def tearDown(self):
        self.temporary.cleanup()

    def wait_until_quiet(self, watcher):
        for _ in range(100):
            self.app.processEvents()
            if not watcher.busy and not watcher.timer.isActive():
                return
            time.sleep(0.025)
        self.fail('Clipboard processing did not finish.')

    def test_recognition_is_conservative(self):
        self.assertFalse(recognizable_quiz_text('Meeting notes [draft]'))
        self.assertFalse(recognizable_quiz_text('[{"title":"Only a title"}]'))
        self.assertTrue(recognizable_quiz_text(QUIZ))

    def test_unexpected_clipboard_failure_stops_watcher_and_requests_attention(self):
        clipboard = BrokenClipboard()
        watcher = ClipboardWatcher(clipboard, self.repository, self.settings, self.history)
        notifications = []
        watcher.notification_requested.connect(lambda *args: notifications.append(args))
        watcher._read_clipboard()
        self.assertEqual(watcher.state, 'off')
        self.assertEqual(notifications[0][0], 'watcher_stopped')
        self.assertNotIn('clipboard unavailable', notifications[0][2])

    def test_each_completed_copy_saves_even_when_the_text_is_identical(self):
        conflict_prompts = []
        watcher = ClipboardWatcher(
            self.clipboard, self.repository, self.settings, self.history,
            conflict_resolver=lambda path: conflict_prompts.append(path) or 'cancel',
        )
        notifications = []
        watcher.notification_requested.connect(lambda *args: notifications.append(args))
        self.clipboard.copy(QUIZ)
        self.wait_until_quiet(watcher)
        files = list((self.root / 'output').glob('*_mcq.html'))
        self.assertEqual(len(files), 1)
        entry = self.history.list_entries()[0]
        self.assertEqual(entry.source_mode, 'clipboard')
        self.assertEqual(entry.question_count, 1)
        self.assertEqual(notifications[0][:3], (
            'clipboard_saved', 'Exam saved', 'Clipboard review · 1 question'))
        self.clipboard.copy(QUIZ)
        QTest.qWait(500)
        self.wait_until_quiet(watcher)
        self.assertEqual(len(list((self.root / 'output').glob('*.html'))), 2)
        self.assertTrue((self.root / 'output' / 'Clipboard_review_2_mcq.html').exists())
        self.assertEqual(len(self.history.list_entries()), 2)
        self.assertEqual(conflict_prompts, [])
        watcher.shutdown()
        watcher.pool.waitForDone(2000)

    def test_multiple_signals_during_one_debounce_window_create_one_exam(self):
        watcher = ClipboardWatcher(self.clipboard, self.repository, self.settings, self.history)
        self.clipboard.copy(QUIZ)
        self.clipboard.dataChanged.emit()
        self.clipboard.dataChanged.emit()
        self.wait_until_quiet(watcher)
        self.assertEqual(len(list((self.root / 'output').glob('*.html'))), 1)
        watcher.shutdown()
        watcher.pool.waitForDone(2000)

    def test_invalid_quiz_is_recorded_without_raw_text_and_unrelated_text_is_ignored(self):
        watcher = ClipboardWatcher(self.clipboard, self.repository, self.settings, self.history)
        errors = []
        watcher.activity.connect(lambda message, error: errors.append((message, error)))
        self.clipboard.copy('A normal copied sentence.')
        self.wait_until_quiet(watcher)
        self.assertEqual(self.history.list_entries(), [])
        invalid = '[{"title":"Private quiz"},{"question":"Secret question","options":["A"],"correct":0,"explanation":"x"}]'
        self.clipboard.copy(invalid)
        self.wait_until_quiet(watcher)
        entries = self.history.list_entries()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].outcome, 'failed')
        self.assertNotIn('Secret question', entries[0].error_summary)
        self.assertTrue(errors[-1][1])
        watcher.shutdown()
        watcher.pool.waitForDone(2000)

    def test_pause_resume_and_conflict_choice(self):
        choices = []
        self.settings['manual_conflicts'] = 'ask'
        watcher = ClipboardWatcher(
            self.clipboard, self.repository, self.settings, self.history,
            conflict_resolver=lambda path: choices.append(path) or 'copy',
        )
        watcher.pause()
        self.clipboard.copy(QUIZ)
        QTest.qWait(500)
        self.assertFalse((self.root / 'output').exists())
        watcher.resume()
        self.clipboard.copy(QUIZ)
        self.wait_until_quiet(watcher)
        changed = QUIZ.replace('A is first.', 'The first answer is A.')
        self.clipboard.copy(changed)
        self.wait_until_quiet(watcher)
        self.assertEqual(len(choices), 1)
        self.assertTrue(choices[0].name.endswith('_mcq.html'))
        self.assertTrue((self.root / 'output' / 'Clipboard_review_2_mcq.html').exists())
        watcher.shutdown()
        watcher.pool.waitForDone(2000)


class ClipboardWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        # These assertions verify the English UI copy, regardless of a prior
        # test having selected the Arabic application language.
        apply_language(self.app, 'en')

    def test_process_clipboard_now_opens_create_preview_without_enabling_watcher(self):
        with tempfile.TemporaryDirectory(dir=BASE / 'artifacts') as temporary:
            root = Path(temporary)
            repository = TemplateRepository(root / 'library')
            repository.initialize()
            store = SettingsStore(repository.root)
            settings = store.defaults()
            window = MainWindow(repository=repository, settings=settings, settings_store=store)
            try:
                QApplication.clipboard().setText(QUIZ)
                window.navigate(2)
                window.process_clipboard_now()
                self.assertEqual(window.pages.currentIndex(), 0)
                self.assertEqual(window.create.editor.toPlainText(), QUIZ)
                self.assertFalse(settings['clipboard_watcher'])
            finally:
                self.app.processEvents()
                window.template_page.pool.waitForDone(2000)
                self.app.processEvents()
                window.template_page.pool.waitForDone(2000)
                window.close()

    def test_tray_toggle_persists_enable_then_pauses_and_resumes(self):
        with tempfile.TemporaryDirectory(dir=BASE / 'artifacts') as temporary:
            root = Path(temporary)
            repository = TemplateRepository(root / 'library')
            repository.initialize()
            store = SettingsStore(repository.root)
            settings = store.defaults()
            settings['language'] = 'en'
            clipboard = FakeClipboard()
            window = MainWindow(repository=repository, settings=settings, settings_store=store)
            tray = TrayController(window, sheet_icon())
            watcher = ClipboardWatcher(clipboard, repository, settings, parent=window)
            try:
                window.set_tray_controller(tray)
                window.set_clipboard_watcher(watcher)
                window.toggle_clipboard_watcher()
                self.assertEqual(watcher.state, 'on')
                self.assertTrue(store.load()['clipboard_watcher'])
                self.assertEqual(window.watcher_status.text(), 'Watching')
                tray.watcher_toggle_requested.emit()
                self.assertEqual(watcher.state, 'paused')
                self.assertEqual(tray.watcher_action.text(), 'Resume Clipboard Watcher')
                tray.watcher_toggle_requested.emit()
                self.assertEqual(watcher.state, 'on')
                self.assertEqual(tray.watcher_action.text(), 'Pause Clipboard Watcher')
            finally:
                watcher.shutdown()
                watcher.pool.waitForDone(2000)
                tray.hide()
                self.app.processEvents()
                window.template_page.pool.waitForDone(2000)
                self.app.processEvents()
                window.force_quit = True
                window.close()
                tray.deleteLater()
                window.deleteLater()
                self.app.processEvents()

    def test_sidebar_watcher_toggle_persists_and_syncs_settings_dialog(self):
        with tempfile.TemporaryDirectory(dir=BASE / 'artifacts') as temporary:
            root = Path(temporary)
            repository = TemplateRepository(root / 'library')
            repository.initialize()
            store = SettingsStore(repository.root)
            settings = store.defaults()
            settings['language'] = 'en'
            clipboard = FakeClipboard()
            window = MainWindow(repository=repository, settings=settings, settings_store=store)
            watcher = ClipboardWatcher(clipboard, repository, settings, parent=window)
            try:
                window.set_clipboard_watcher(watcher)
                window.open_settings()
                dialog = window.settings_dialog

                window.watcher_status.click()
                self.assertEqual(watcher.state, 'on')
                self.assertTrue(store.load()['clipboard_watcher'])
                self.assertTrue(dialog.clipboard_watcher.isChecked())
                self.assertEqual(window.watcher_status.text(), 'Watching')

                window.watcher_status.click()
                self.assertEqual(watcher.state, 'off')
                self.assertFalse(store.load()['clipboard_watcher'])
                self.assertFalse(dialog.clipboard_watcher.isChecked())
                self.assertEqual(window.watcher_status.text(), 'Off')
            finally:
                if window.settings_dialog is not None:
                    window.settings_dialog.close()
                watcher.shutdown()
                watcher.pool.waitForDone(2000)
                self.app.processEvents()
                window.template_page.pool.waitForDone(2000)
                window.force_quit = True
                window.close()
                window.deleteLater()
                self.app.processEvents()


if __name__ == '__main__':
    unittest.main()
