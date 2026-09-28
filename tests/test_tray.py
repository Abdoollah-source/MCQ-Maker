import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QObject, Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from mcq_maker.settings import SettingsStore
from mcq_maker.shell import MainWindow, sheet_icon
from mcq_maker.template_repository import TemplateRepository
from mcq_maker.tray import TrayController


BASE = Path(__file__).resolve().parents[1]


class FakeTray(QObject):
    quit_requested = Signal()
    watcher_toggle_requested = Signal()
    process_clipboard_requested = Signal()
    open_output_requested = Signal()
    settings_requested = Signal()

    def __init__(self, available=True):
        super().__init__()
        self.available = available
        self.shown = 0
        self.hidden = 0
        self.synced = []
        self.watcher_states = []

    def sync(self, settings):
        self.synced.append(settings['close_behavior'])

    def show(self):
        self.shown += 1

    def hide(self):
        self.hidden += 1

    def set_watcher_state(self, state):
        self.watcher_states.append(state)


class TrayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle('Fusion')

    def test_tray_menu_and_open_action(self):
        window = QWidget()
        tray = TrayController(window, sheet_icon())
        try:
            quit_requests = []
            tray.quit_requested.connect(lambda: quit_requests.append(True))
            actions = [action for action in tray.menu.actions() if not action.isSeparator()]
            self.assertEqual([action.text() for action in actions], [
                'Open MCQ Maker', 'Enable Clipboard Watcher', 'Process Clipboard Now',
                'Open Output Folder', 'Settings', 'Quit'])
            tray.set_watcher_state('on')
            self.assertEqual(tray.watcher_action.text(), 'Pause Clipboard Watcher')
            self.assertTrue(tray.watcher_action.isEnabled())
            tray.open_action.trigger()
            self.app.processEvents()
            self.assertTrue(window.isVisible())
            tray.quit_action.trigger()
            self.assertEqual(quit_requests, [True])
        finally:
            tray.hide()
            window.close()

    def _window(self, root, close_behavior='tray'):
        repository = TemplateRepository(root / 'library')
        repository.initialize()
        store = SettingsStore(repository.root)
        settings = store.defaults()
        settings['close_behavior'] = close_behavior
        store.save(settings)
        window = MainWindow(repository=repository, settings=settings, settings_store=store)
        return window, store

    def _settle_template_worker(self, window):
        if window.template_page is not None:
            window.template_page.pool.waitForDone(2000)
        self.app.processEvents()

    def test_close_to_tray_hides_window_and_explains_only_once(self):
        with tempfile.TemporaryDirectory(dir=BASE / 'artifacts') as temporary:
            window, store = self._window(Path(temporary))
            tray = FakeTray()
            window.set_tray_controller(tray)
            try:
                window.show()
                QTest.qWait(30)
                with patch('mcq_maker.shell.QMessageBox.information', return_value=None) as explanation:
                    window.close()
                    self.app.processEvents()
                    self.assertFalse(window.isVisible())
                    self.assertEqual(tray.shown, 1)
                    self.assertTrue(store.load()['tray_explanation_shown'])
                    window.show()
                    window.close()
                    self.app.processEvents()
                    self.assertEqual(explanation.call_count, 1)
            finally:
                self._settle_template_worker(window)
                window.force_quit = True
                window.close()

    def test_unavailable_tray_keeps_window_accessible(self):
        with tempfile.TemporaryDirectory(dir=BASE / 'artifacts') as temporary:
            window, _ = self._window(Path(temporary))
            window.set_tray_controller(FakeTray(available=False))
            try:
                window.show()
                QTest.qWait(30)
                with patch('mcq_maker.shell.QMessageBox.warning', return_value=None) as warning:
                    window.close()
                    self.app.processEvents()
                    self.assertTrue(window.isVisible())
                    warning.assert_called_once()
            finally:
                self._settle_template_worker(window)
                window.force_quit = True
                window.close()


if __name__ == '__main__':
    unittest.main()
