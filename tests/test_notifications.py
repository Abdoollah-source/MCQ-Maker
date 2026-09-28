from pathlib import Path
import tempfile
import unittest

from PySide6.QtWidgets import QApplication

from mcq_maker.history import HistoryRepository
from mcq_maker.notifications import NotificationService, WindowsToastBackend
from mcq_maker.settings import SettingsStore
from mcq_maker.shell import MainWindow
from mcq_maker.template_repository import TemplateRepository


BASE = Path(__file__).resolve().parents[1]


class FakeBackend:
    def __init__(self, raises=False):
        self.deliveries = []
        self.raises = raises

    def show(self, notification, sound, activated, failed):
        if self.raises:
            raise RuntimeError('Windows rejected the test toast')
        self.deliveries.append((notification, sound, activated, failed))


class NotificationServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_settings_foreground_suppression_sound_and_activation(self):
        settings = {'notifications': True, 'notification_sounds': False}
        state = {'foreground': True}
        backend = FakeBackend()
        service = NotificationService(
            lambda: settings, lambda: state['foreground'], backend=backend)
        self.assertFalse(service.notify('clipboard_saved', 'Exam saved', 'Cardiology · 12 questions'))
        self.assertEqual(backend.deliveries, [])

        self.assertTrue(service.notify(
            'batch_complete', 'Folder scan complete', '3 created · 0 skipped · 0 failed'))
        self.assertEqual(len(backend.deliveries), 1)

        state['foreground'] = False
        activated = []
        service.activation_requested.connect(lambda target: activated.append(target))
        target = Path('C:/Exams/Cardiology_mcq.html')
        self.assertTrue(service.notify('clipboard_saved', 'Exam saved', 'Cardiology · 12 questions', target))
        notification, sound, callback, _ = backend.deliveries[1]
        self.assertFalse(sound)
        self.assertEqual(notification.target, target)
        callback()
        self.assertEqual(activated, [target])

        settings['notification_sounds'] = True
        service.notify('batch_complete', 'Folder scan complete', '3 created · 0 skipped · 0 failed')
        self.assertTrue(backend.deliveries[-1][1])
        settings['notifications'] = False
        self.assertFalse(service.notify('batch_complete', 'Hidden', 'No delivery'))
        self.assertEqual(len(backend.deliveries), 3)

    def test_repeated_errors_are_throttled_and_summarized(self):
        now = [100.0]
        backend = FakeBackend()
        service = NotificationService(
            lambda: {'notifications': True, 'notification_sounds': False},
            lambda: False,
            backend=backend,
            clock=lambda: now[0],
        )
        self.assertTrue(service.notify('background_error', 'Needs attention', 'Output folder unavailable'))
        self.assertFalse(service.notify('background_error', 'Needs attention', 'Output folder unavailable'))
        self.assertEqual(len(backend.deliveries), 1)
        now[0] += 31
        self.assertTrue(service.notify('background_error', 'Needs attention', 'Output folder unavailable'))
        self.assertIn('Repeated 2 more times.', backend.deliveries[-1][0].body)

    def test_delivery_failure_never_escapes(self):
        backend = FakeBackend(raises=True)
        service = NotificationService(
            lambda: {'notifications': True, 'notification_sounds': False},
            lambda: False,
            backend=backend,
        )
        self.assertFalse(service.notify('clipboard_saved', 'Exam saved', 'Renal review'))
        self.assertIn('Windows rejected', service.last_delivery_error)

    def test_installed_backend_can_initialize(self):
        backend = WindowsToastBackend()
        self.assertEqual(backend.toaster.applicationText, 'MCQ Maker')


class NotificationWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_activation_opens_the_matching_place_in_the_app(self):
        with tempfile.TemporaryDirectory(dir=BASE / 'artifacts') as temporary:
            root = Path(temporary)
            repository = TemplateRepository(root / 'library')
            repository.initialize()
            store = SettingsStore(repository.root)
            settings = store.defaults()
            history = HistoryRepository(repository.root)
            history.initialize()
            output = root / 'Cardiology_mcq.html'
            output.write_text('<html></html>', encoding='utf-8')
            history.record_success(
                title='Cardiology', question_count=4, input_hash='a' * 64,
                output_path=output, template_entry=repository.list_templates()['templates'][0],
                source_mode='clipboard')
            window = MainWindow(repository, settings, store, history=history)
            try:
                window.open_notification_target('folder_report')
                self.assertEqual(window.pages.currentIndex(), 1)
                window.open_notification_target(output)
                self.assertEqual(window.pages.currentIndex(), 2)
                self.assertEqual(window.history_page.search.text(), output.name)
                self.assertEqual(window.history_page.table.topLevelItemCount(), 1)
            finally:
                self.app.processEvents()
                window.template_page.pool.waitForDone(2000)
                self.app.processEvents()
                window.force_quit = True
                window.close()


if __name__ == '__main__':
    unittest.main()
