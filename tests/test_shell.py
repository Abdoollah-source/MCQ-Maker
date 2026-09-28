"""Executable layout and shell interaction checks; desktop review is separate."""
import unittest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton
from mcq_maker.shell import MainWindow
from mcq_maker.theme import apply_theme

class ShellTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle('Fusion')

    def setUp(self):
        apply_theme(self.app)
        self.window = MainWindow()
        self.window.show()
        QTest.qWait(100)

    def tearDown(self):
        self.window.close()
        self.app.processEvents()

    def test_initial_size_and_layout(self):
        available = self.window.screen().availableGeometry()
        frame = self.window.frameGeometry()
        target_width = min(1120, available.width()-48)
        target_height = min(780, available.height()-48)
        self.assertLessEqual(abs(frame.width() - target_width), 4)
        self.assertLessEqual(abs(frame.height() - target_height), 4)
        self.assertTrue(available.contains(frame))
        self.assertEqual(self.window.sidebar.width(), 176)
        if self.window.width() >= 960:
            self.assertEqual(self.window.create.details.width(), 288)
        else:
            self.assertGreater(self.window.create.details.width(), 288)
        self.assertGreaterEqual(self.window.create.editor.height(), 280)

    def test_navigation_and_unconfigured_create_state(self):
        for index, button in enumerate(self.window.nav_buttons):
            QTest.mouseClick(button, Qt.LeftButton)
            self.assertEqual(self.window.pages.currentIndex(), index)
            self.assertTrue(button.isChecked())
        self.assertFalse(self.window.create.generate.isEnabled())
        self.assertFalse(self.window.create.template.isEnabled())
        self.assertFalse(self.window.create.editor.isReadOnly())
        self.window.navigate(0)
        self.window.create.editor.setFocus()
        QTest.keyClicks(self.window.create.editor, 'draft input')
        self.assertEqual(self.window.create.editor.toPlainText(), 'draft input')

    def test_responsive_navigation(self):
        self.window.resize(820, 600)
        QTest.qWait(50)
        self.assertFalse(self.window.sidebar.isVisible())
        self.assertTrue(self.window.compact_nav.isVisible())
        self.window.compact_nav.menu().actions()[2].trigger()
        self.assertEqual(self.window.pages.currentIndex(), 2)
        self.window.resize(1120, 780)
        QTest.qWait(50)
        self.assertTrue(self.window.sidebar.isVisible())
        self.assertFalse(self.window.compact_nav.isVisible())

    def test_enter_activates_focused_button(self):
        target = self.window.nav_buttons[2]
        target.setFocus()
        QTest.keyClick(target, Qt.Key_Return)
        self.assertEqual(self.window.pages.currentIndex(), 2)
        back = next(b for b in self.window.pages.currentWidget().findChildren(QPushButton) if b.text() == 'Create exam')
        back.setFocus()
        QTest.keyClick(back, Qt.Key_Return)
        self.assertEqual(self.window.pages.currentIndex(), 0)

    def test_sidebar_focus_does_not_compete_with_selected_page(self):
        selected = self.window.nav_buttons[0]
        focused = self.window.nav_buttons[1]
        focused.setFocus()
        self.app.processEvents()
        self.assertTrue(selected.isChecked())
        self.assertFalse(focused.isChecked())
        self.assertTrue(focused.hasFocus())
        stylesheet = self.app.styleSheet()
        self.assertIn('QPushButton[nav="true"]:focus:unchecked', stylesheet)
        self.assertIn('QPushButton[nav="true"]:checked:focus', stylesheet)

    def test_global_keyboard_shortcuts_reach_their_destinations(self):
        self.window.history_page.search.setEnabled(True)
        self.window.history_shortcut.trigger()
        QTest.qWait(20)
        self.assertEqual(self.window.pages.currentIndex(), 2)
        self.assertTrue(self.window.history_page.search.hasFocus())
        self.assertEqual(self.window.process_clipboard_shortcut.shortcut().toString(), 'Ctrl+Shift+V')
        self.assertEqual(self.window.import_shortcut.shortcut().toString(), 'Ctrl+O')
        self.assertEqual(self.window.settings_shortcut.shortcut().toString(), 'Ctrl+,')

    def test_dark_theme_preserves_screen_and_disabled_states(self):
        self.window.navigate(2)
        apply_theme(self.app, True)
        QTest.qWait(50)
        self.assertEqual(self.window.pages.currentIndex(), 2)
        self.assertEqual(self.app.palette().window().color().name(), '#201e20')
        self.assertFalse(self.window.create.generate.isEnabled())

    def test_window_close(self):
        self.window.close()
        self.assertFalse(self.window.isVisible())

if __name__ == '__main__':
    unittest.main()
