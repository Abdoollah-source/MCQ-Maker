"""System-tray ownership and the Module 7 tray menu."""
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPen
from PySide6.QtWidgets import QMenu, QSystemTrayIcon


class TrayController(QObject):
    """Keep tray behavior separate from the main-window layout."""

    open_requested = Signal()
    watcher_toggle_requested = Signal()
    process_clipboard_requested = Signal()
    open_output_requested = Signal()
    settings_requested = Signal()
    quit_requested = Signal()

    def __init__(self, window, icon, parent=None):
        super().__init__(parent or window)
        self.window = window
        self.base_icon = icon
        self.icon = QSystemTrayIcon(icon, self)
        self.icon.setToolTip('MCQ Maker — Clipboard watcher off')

        self.menu = QMenu(window)
        self.open_action = self.menu.addAction('Open MCQ Maker')
        self.watcher_action = self.menu.addAction('Enable Clipboard Watcher')
        self.process_action = self.menu.addAction('Process Clipboard Now')
        self.menu.addSeparator()
        self.output_action = self.menu.addAction('Open Output Folder')
        self.settings_action = self.menu.addAction('Settings')
        self.menu.addSeparator()
        self.quit_action = self.menu.addAction('Quit')
        self.icon.setContextMenu(self.menu)

        self.open_action.triggered.connect(self.open_window)
        self.watcher_action.triggered.connect(self.watcher_toggle_requested)
        self.process_action.triggered.connect(self.process_clipboard_requested)
        self.output_action.triggered.connect(self.open_output_requested)
        self.settings_action.triggered.connect(self.settings_requested)
        self.quit_action.triggered.connect(self.quit_requested)
        self.icon.activated.connect(self._activated)

    @property
    def available(self):
        return QSystemTrayIcon.isSystemTrayAvailable()

    def sync(self, settings):
        if ((settings.get('close_behavior') == 'tray' or settings.get('clipboard_watcher'))
                and self.available):
            self.show()
        else:
            self.hide()

    def show(self):
        if self.available:
            self.icon.show()

    def hide(self):
        self.icon.hide()

    def open_window(self):
        self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()
        self.open_requested.emit()

    def set_watcher_state(self, state):
        names = {'off': 'Enable Clipboard Watcher', 'on': 'Pause Clipboard Watcher',
                 'paused': 'Resume Clipboard Watcher'}
        self.watcher_action.setText(names[state])
        self.watcher_action.setEnabled(True)
        self.icon.setToolTip(f'MCQ Maker — Clipboard watcher {state}')
        self.icon.setIcon(self._state_icon(state))

    def _state_icon(self, state):
        pixmap = self.base_icon.pixmap(32, 32)
        if state == 'off':
            return QIcon(pixmap)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        badge = QColor('#276343' if state == 'on' else '#805400')
        painter.setPen(QPen(QColor('#F5F3EF'), 1))
        painter.setBrush(badge)
        painter.drawEllipse(20, 20, 11, 11)
        pen = QPen(QColor('#FFFFFF'), 1.7)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        if state == 'on':
            painter.drawLine(23, 25, 25, 27)
            painter.drawLine(25, 27, 29, 23)
        else:
            painter.drawLine(24, 23, 24, 28)
            painter.drawLine(28, 23, 28, 28)
        painter.end()
        return QIcon(pixmap)

    def _activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self.open_window()
