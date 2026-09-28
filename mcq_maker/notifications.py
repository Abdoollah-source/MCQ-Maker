"""Failure-isolated Windows notification delivery."""
from dataclasses import dataclass
from pathlib import Path
import time

from PySide6.QtCore import QObject, Signal


@dataclass(frozen=True)
class Notification:
    kind: str
    title: str
    body: str
    target: object = None


class WindowsToastBackend:
    """Small adapter around windows-toasts so the rest of the app stays testable."""

    def __init__(self):
        from windows_toasts import WindowsToaster
        self.toaster = WindowsToaster('MCQ Maker')
        self.active = []

    def show(self, notification, sound, activated, failed):
        from windows_toasts import (Toast, ToastAudio, ToastDisplayImage,
                                    ToastImage, ToastImagePosition)
        toast = Toast([notification.title, notification.body])
        icon = Path(__file__).parent / 'assets' / 'app_icon.png'
        toast.AddImage(ToastDisplayImage(
            ToastImage(icon), altText='MCQ Maker', position=ToastImagePosition.AppLogo))
        toast.audio = ToastAudio(silent=not sound)
        toast.on_activated = lambda _event: activated()
        toast.on_failed = lambda event: failed(str(getattr(event, 'error_code', 'unknown error')))
        self.active.append(toast)
        del self.active[:-20]
        self.toaster.show_toast(toast)


class NotificationService(QObject):
    """Apply settings, foreground suppression, and error aggregation in one place."""

    activation_requested = Signal(object)

    def __init__(self, settings_provider, foreground_provider, backend=None,
                 clock=None, parent=None):
        super().__init__(parent)
        self.settings_provider = settings_provider
        self.foreground_provider = foreground_provider
        self.clock = clock or time.monotonic
        self.backend = backend
        self.backend_failed = False
        self.last_delivery_error = ''
        self.error_key = ''
        self.error_time = 0.0
        self.suppressed_error_count = 0

    def _backend(self):
        if self.backend is None and not self.backend_failed:
            try:
                self.backend = WindowsToastBackend()
            except Exception as exc:
                self.backend_failed = True
                self.last_delivery_error = str(exc)
        return self.backend

    def notify(self, kind, title, body, target=None):
        settings = self.settings_provider() or {}
        if not settings.get('notifications', True):
            return False
        # A batch can take long enough that its final result is useful even while
        # the scan page is open. Per-file progress remains in the in-app report.
        if kind != 'batch_complete' and self.foreground_provider():
            return False

        body = str(body).strip()[:300]
        if kind in {'background_error', 'watcher_stopped'}:
            key = f'{kind}\0{title}\0{body}'
            now = self.clock()
            if key == self.error_key and now - self.error_time < 30:
                self.suppressed_error_count += 1
                return False
            if key == self.error_key and self.suppressed_error_count:
                repeated = self.suppressed_error_count + 1
                body = f'{body} Repeated {repeated} more times.'
            self.error_key = key
            self.error_time = now
            self.suppressed_error_count = 0

        notification = Notification(kind, str(title).strip()[:80], body, target)
        backend = self._backend()
        if backend is None:
            return False
        try:
            backend.show(
                notification,
                bool(settings.get('notification_sounds', False)),
                lambda: self.activation_requested.emit(target),
                self._delivery_failed,
            )
            return True
        except Exception as exc:
            self._delivery_failed(str(exc))
            return False

    def _delivery_failed(self, message):
        self.last_delivery_error = str(message)
