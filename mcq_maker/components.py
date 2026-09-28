"""Shared visual and interactive primitives for every MCQ Maker window."""
from PySide6.QtCore import (QEasingCurve, QPropertyAnimation, QPoint, QSize,
                            QTimer, Qt)
from PySide6.QtWidgets import (QAbstractScrollArea, QComboBox, QFrame, QLabel, QPushButton,
                               QApplication, QProgressBar, QSizePolicy,
                               QStyledItemDelegate, QVBoxLayout)


class _DropdownItemDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        return QSize(size.width(), max(36, size.height()))


class Dropdown(QComboBox):
    """A selector with consistent control and menu sizing on Windows."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(38)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.setItemDelegate(_DropdownItemDelegate(self))

    def showPopup(self):
        super().showPopup()
        QTimer.singleShot(0, self._place_popup)

    def _place_popup(self):
        popup = self.view().window()
        if not popup.isVisible():
            return
        available = self.screen().availableGeometry()
        top_left = self.mapToGlobal(QPoint(0, 0))
        below = self.mapToGlobal(QPoint(0, self.height()))
        popup_width = max(self.width(), popup.width())
        x = min(max(below.x(), available.left()), available.right() - popup_width + 1)
        if below.y() + popup.height() <= available.bottom() + 1:
            y = below.y()
        else:
            y = max(available.top(), top_left.y() - popup.height())
        popup.resize(popup_width, popup.height())
        popup.move(x, y)

    def wheelEvent(self, event):
        """Let a surrounding scroll form handle wheel input until the menu is open."""
        if self.view().window().isVisible() or not self._has_scrollable_ancestor():
            super().wheelEvent(event)
            return
        event.ignore()

    def _has_scrollable_ancestor(self):
        parent = self.parentWidget()
        while parent is not None:
            if isinstance(parent, QAbstractScrollArea):
                return True
            parent = parent.parentWidget()
        return False


class AppButton(QPushButton):
    unavailable_tooltip = 'Available when this module is added.'

    def setEnabled(self, enabled):
        super().setEnabled(enabled)
        if enabled and self.toolTip() == self.unavailable_tooltip:
            self.setToolTip('')

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and self.isEnabled():
            self.showMenu() if self.menu() is not None else self.click()
            event.accept()
            return
        super().keyPressEvent(event)


class AnimatedProgressBar(QProgressBar):
    """Animate only real progress updates, respecting Windows reduced motion."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.animation = QPropertyAnimation(self, b'value', self)
        self.animation.setDuration(100)
        self.animation.setEasingCurve(QEasingCurve.Linear)

    @staticmethod
    def reduced_motion():
        hints = QApplication.styleHints()
        value = getattr(hints, 'reduceMotion', None)
        return bool(value()) if callable(value) else False

    def set_reported_value(self, value):
        value = int(value)
        if self.maximum() <= 0 or self.reduced_motion():
            self.setValue(value)
            return
        self.animation.stop()
        self.animation.setStartValue(self.value())
        self.animation.setEndValue(value)
        self.animation.start()


def label(text, role='secondary'):
    control = QLabel(text)
    control.setProperty('role', role)
    control.setWordWrap(True)
    control.setTextFormat(Qt.PlainText)
    control.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    return control


def button(text, enabled=False, primary=False):
    control = AppButton(text)
    control.setEnabled(enabled)
    control.setProperty('primary', primary)
    if not enabled:
        control.setToolTip(control.unavailable_tooltip)
    return control


def panel():
    frame = QFrame()
    frame.setObjectName('panel')
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(20, 20, 20, 20)
    layout.setSpacing(20)
    return frame, layout
