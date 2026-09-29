"""Desktop entry point."""
import argparse
import sys
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication
from .theme import apply_theme
from .localization import apply_language
from .shell import MainWindow
from .template_repository import TemplateRepository
from .settings import SettingsStore
from .history import HistoryRepository, HistoryError
from .tray import TrayController
from .clipboard_watcher import ClipboardWatcher
from .notifications import NotificationService
from .startup import WindowsStartupManager
from .single_instance import SingleInstance
from .diagnostics import configure_diagnostics
from .ai_library import AILibrary, AILibraryError

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--theme', choices=['system', 'light', 'dark'], help='Development preview theme')
    parser.add_argument('--compact-preview', action='store_true', help='Open the compact layout for visual verification')
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    app.setApplicationName('MCQ Maker')
    app.setStyle('Fusion')
    repository = TemplateRepository()
    logger = configure_diagnostics(repository.root)
    instance = SingleInstance()
    if not instance.acquire():
        return 0
    settings_store = SettingsStore(repository.root)
    settings = settings_store.load()
    apply_language(app, settings['language'])
    ai_library = AILibrary(repository.root)
    try:
        ai_library.initialize()
    except AILibraryError:
        logger.exception('AI library could not be initialized')
        ai_library = None
    startup = WindowsStartupManager()
    if settings['start_with_windows']:
        try:
            startup.set_enabled(True)
        except OSError:
            logger.exception('Start with Windows registration could not be repaired')
            settings['start_with_windows'] = False
            try:
                settings_store.save(settings)
            except OSError:
                pass
    history = HistoryRepository(repository.root)
    try:
        history.initialize()
    except HistoryError:
        logger.exception('History storage could not be opened')
        history = None
    window = None
    def refresh_theme(preference=None):
        if preference not in {'system', 'light', 'dark'}:
            preference = args.theme or (window.settings['theme'] if window is not None else settings['theme'])
        dark = preference == 'dark' or (preference == 'system' and app.styleHints().colorScheme() == Qt.ColorScheme.Dark)
        colors = apply_theme(app, dark)
        if window is not None:
            window.update_icons(colors['secondary'])
    refresh_theme()
    app.styleHints().colorSchemeChanged.connect(lambda _: refresh_theme())
    window = MainWindow(repository=repository, settings=settings, settings_store=settings_store,
                        apply_theme_callback=refresh_theme, history=history, startup_manager=startup,
                        ai_library=ai_library)
    tray = TrayController(window, window.windowIcon())
    window.set_tray_controller(tray)
    watcher = ClipboardWatcher(
        app.clipboard(), repository, settings, history,
        conflict_resolver=window.resolve_clipboard_conflict,
        parent=window,
    )
    window.set_clipboard_watcher(watcher)
    notifications = NotificationService(
        settings_provider=lambda: window.settings,
        foreground_provider=lambda: window.isVisible() and window.isActiveWindow(),
        parent=window,
    )
    window.set_notification_service(notifications)
    instance.activation_requested.connect(window.restore_window)
    app.aboutToQuit.connect(instance.close)
    refresh_theme()
    window.show()
    if args.compact_preview:
        QTimer.singleShot(100, lambda: window.resize(820, 620))
    return app.exec()
