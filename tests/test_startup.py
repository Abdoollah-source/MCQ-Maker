from pathlib import Path
import tempfile
import unittest

from PySide6.QtWidgets import QApplication

from mcq_maker.settings import SettingsStore
from mcq_maker.shell import MainWindow
from mcq_maker.startup import RUN_KEY, VALUE_NAME, WindowsStartupManager
from mcq_maker.template_repository import TemplateRepository


BASE = Path(__file__).resolve().parents[1]


class _Key:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class FakeRegistry:
    HKEY_CURRENT_USER = object()
    KEY_SET_VALUE = 1
    REG_SZ = 1

    def __init__(self):
        self.values = {}

    def CreateKeyEx(self, hive, path, reserved, access):
        self.opened = (hive, path, reserved, access)
        return _Key()

    def SetValueEx(self, key, name, reserved, kind, value):
        self.values[name] = value

    def DeleteValue(self, key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        del self.values[name]


class _Dialog:
    def mark_saved(self):
        self.saved = True

    def mark_error(self, message):
        self.error = message


class StartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_source_registration_is_quoted_and_reversible(self):
        registry = FakeRegistry()
        manager = WindowsStartupManager(
            registry=registry,
            executable=BASE / '.venv' / 'Scripts' / 'python.exe',
            source_launcher=BASE / 'launch_mcq_maker.pyw',
        )
        manager.set_enabled(True)
        self.assertEqual(registry.opened[1], RUN_KEY)
        self.assertIn('pythonw.exe', registry.values[VALUE_NAME])
        self.assertIn('launch_mcq_maker.pyw', registry.values[VALUE_NAME])
        manager.set_enabled(False)
        self.assertNotIn(VALUE_NAME, registry.values)

    def test_settings_change_updates_registration(self):
        with tempfile.TemporaryDirectory(dir=BASE / 'artifacts') as root:
            repository = TemplateRepository(Path(root))
            repository.initialize()
            store = SettingsStore(repository.root)
            settings = store.defaults()
            manager = WindowsStartupManager(registry=FakeRegistry())
            window = MainWindow(repository=repository, settings=settings, settings_store=store,
                                startup_manager=manager)
            dialog = _Dialog()
            updated = dict(settings, start_with_windows=True)
            window.apply_settings_change(dialog, updated, None)
            self.assertTrue(dialog.saved)
            self.assertTrue(store.load()['start_with_windows'])
            self.assertIn(VALUE_NAME, manager.registry.values)
            window.close()


if __name__ == '__main__':
    unittest.main()
