"""A responsive, immediately-applied settings window."""
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QDialog,
                               QFileDialog, QFrame, QHBoxLayout,
                               QLayout, QLineEdit, QRadioButton, QScrollArea,
                               QSizePolicy, QVBoxLayout, QWidget)
from .components import Dropdown, button, label
from .ai_studio_errors import InvalidBraveExecutableError
from .ai_studio_browser import AIStudioBrowserManager


class SettingsDialog(QDialog):
    settings_changed = Signal(object, object)

    def __init__(self, settings, templates, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Settings')
        self.setModal(False)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.resize(720, 640)
        self.setMinimumSize(560, 520)
        self._settings = dict(settings)
        self._loading = True

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 20)
        outer.setSpacing(20)
        title = label('Settings', 'title')
        outer.addWidget(title)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        body.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        content = QVBoxLayout(body)
        content.setContentsMargins(0, 0, 4, 0)
        content.setSpacing(32)
        content.setSizeConstraint(QLayout.SetMinimumSize)

        files, files_layout = self._section('Files')
        self.output_folder = QLineEdit(settings['output_folder'])
        self.output_folder.setAccessibleName('Output folder')
        browse = button('Choose folder', True)
        browse.clicked.connect(self.choose_folder)
        output_row = QWidget()
        output_layout = QHBoxLayout(output_row)
        output_layout.setContentsMargins(0, 0, 0, 0)
        output_layout.setSpacing(8)
        output_layout.addWidget(self.output_folder, 1)
        output_layout.addWidget(browse)
        self._add_setting(files_layout, 'Output folder', output_row)

        self.default_template = Dropdown()
        for entry in templates:
            if entry.get('valid'):
                self.default_template.addItem(entry['display_name'], entry['id'])
                if entry.get('is_default'):
                    self.default_template.setCurrentIndex(self.default_template.count() - 1)
        self.default_template.setEnabled(self.default_template.count() > 0)
        self._add_setting(files_layout, 'Default template', self.default_template)

        self.open_after = QCheckBox('Open it in my browser')
        self.open_after.setChecked(settings['open_after_manual_generation'])
        self._add_setting(files_layout, 'After generating an exam', self.open_after)

        self.conflicts = Dropdown()
        self.conflicts.addItem('Ask before replacing an existing exam', 'ask')
        self.conflicts.addItem('Always save a numbered copy', 'save_copy')
        self.conflicts.setCurrentIndex(max(0, self.conflicts.findData(settings['manual_conflicts'])))
        self._add_setting(files_layout, 'Existing filename', self.conflicts)

        self.include_subfolders = QCheckBox('Include subfolders')
        self.include_subfolders.setChecked(settings['include_subfolders'])
        self._add_setting(files_layout, 'Folder scan', self.include_subfolders)
        content.addWidget(files)

        background, background_layout = self._section('Clipboard and startup')
        self.clipboard_watcher = QCheckBox('Enable clipboard watching')
        self.clipboard_watcher.setChecked(settings['clipboard_watcher'])
        self._add_setting(background_layout, 'Clipboard watcher', self.clipboard_watcher)

        self.close_behavior = Dropdown()
        self.close_behavior.addItem('Quit MCQ Maker', 'quit')
        self.close_behavior.addItem('Keep running in the system tray', 'tray')
        self.close_behavior.setCurrentIndex(max(0, self.close_behavior.findData(settings['close_behavior'])))
        self._add_setting(background_layout, 'When I close the window', self.close_behavior)

        self.start_windows = QCheckBox('Open MCQ Maker when I sign in')
        self.start_windows.setChecked(settings['start_with_windows'])
        self._add_setting(background_layout, 'Start with Windows', self.start_windows)
        content.addWidget(background)

        ai_studio, ai_studio_layout = self._section('Google AI Studio')
        self.brave_executable = QLineEdit(settings.get('ai_studio_brave_executable') or '')
        self.brave_executable.setAccessibleName('Optional Brave executable override')
        self.brave_executable.setPlaceholderText('Automatic detection (recommended)')
        brave_browse = button('Choose Brave', True)
        brave_browse.clicked.connect(self.choose_brave_executable)
        brave_row = QWidget()
        brave_layout = QHBoxLayout(brave_row)
        brave_layout.setContentsMargins(0, 0, 0, 0)
        brave_layout.setSpacing(8)
        brave_layout.addWidget(self.brave_executable, 1)
        brave_layout.addWidget(brave_browse)
        self._add_setting(ai_studio_layout, 'Optional Brave executable override', brave_row)
        ai_studio_layout.addWidget(label('Leave this blank to find Brave automatically.', 'muted'))
        content.addWidget(ai_studio)

        appearance, appearance_layout = self._section('Appearance and notifications')
        theme_row = QWidget()
        theme_layout = QHBoxLayout(theme_row)
        theme_layout.setContentsMargins(0, 0, 0, 0)
        theme_layout.setSpacing(24)
        self.theme_group = QButtonGroup(self)
        self.theme_buttons = {}
        for title_text, value in [('System', 'system'), ('Light', 'light'), ('Dark', 'dark')]:
            choice = QRadioButton(title_text)
            self.theme_group.addButton(choice)
            self.theme_buttons[value] = choice
            theme_layout.addWidget(choice)
        theme_layout.addStretch()
        self.theme_buttons[settings['theme']].setChecked(True)
        self._add_setting(appearance_layout, 'Theme', theme_row)

        self.notifications = QCheckBox('Show notifications')
        self.notifications.setChecked(settings['notifications'])
        self._add_setting(appearance_layout, 'Notifications', self.notifications)

        self.notification_sounds = QCheckBox('Play notification sounds')
        self.notification_sounds.setChecked(settings['notification_sounds'])
        self.sounds_help = label('Turn on notifications to enable sounds.', 'muted')
        sounds = QWidget()
        sounds_layout = QVBoxLayout(sounds)
        sounds_layout.setContentsMargins(0, 0, 0, 0)
        sounds_layout.setSpacing(4)
        sounds_layout.addWidget(self.notification_sounds)
        sounds_layout.addWidget(self.sounds_help)
        self._add_setting(appearance_layout, 'Notification sounds', sounds)
        content.addWidget(appearance)
        content.addStretch()

        self.scroll.setWidget(body)
        outer.addWidget(self.scroll, 1)
        footer = QHBoxLayout()
        footer.setSpacing(12)
        self.status = label('All settings are up to date.', 'muted')
        footer.addWidget(self.status, 1)
        done = button('Done', True, True)
        done.clicked.connect(self.close)
        footer.addWidget(done)
        outer.addLayout(footer)

        self._connect_changes()
        self._sync_dependencies()
        self._loading = False

    def _section(self, title):
        frame = QFrame()
        frame.setObjectName('settingsSection')
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(14)
        layout.setSizeConstraint(QLayout.SetMinimumSize)
        heading = label(title, 'heading')
        layout.addWidget(heading)
        return frame, layout

    def _add_setting(self, section, title, control):
        row = QWidget()
        row.setObjectName('settingRow')
        row.setMinimumHeight(56)
        layout = QVBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.setSizeConstraint(QLayout.SetMinimumSize)
        field = label(title, 'field')
        layout.addWidget(field)
        layout.addWidget(control)
        section.addWidget(row)

    def _connect_changes(self):
        self.output_folder.editingFinished.connect(self._notify_change)
        self.brave_executable.editingFinished.connect(self._notify_change)
        for combo in (self.default_template, self.conflicts, self.close_behavior):
            combo.currentIndexChanged.connect(self._notify_change)
        for checkbox in (self.open_after, self.include_subfolders, self.clipboard_watcher,
                         self.start_windows, self.notifications, self.notification_sounds):
            checkbox.toggled.connect(self._notify_change)
        self.notifications.toggled.connect(self._sync_dependencies)
        self.theme_group.buttonToggled.connect(self._theme_changed)

    def _theme_changed(self, _button, checked):
        if checked:
            self._notify_change()

    def _sync_dependencies(self, *_):
        enabled = self.notifications.isChecked()
        self.notification_sounds.setEnabled(enabled)
        self.sounds_help.setVisible(not enabled)

    def _notify_change(self, *_):
        if self._loading:
            return
        output = self.output_folder.text().strip()
        if not output or not Path(output).is_absolute():
            self.status.setText('Enter a complete output folder path, such as C:\\Users\\Your name\\Documents\\MCQ Maker Exams.')
            self.status.setProperty('feedback', 'error')
            self._refresh_status_style()
            return
        override = self.brave_executable.text().strip()
        if override:
            try:
                AIStudioBrowserManager(override).discover_brave_executable()
            except InvalidBraveExecutableError as exc:
                self.status.setText(str(exc))
                self.status.setProperty('feedback', 'error')
                self._refresh_status_style()
                return
        values, template_id = self.values()
        self.settings_changed.emit(values, template_id)

    def _refresh_status_style(self):
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)

    def mark_saved(self):
        self.status.setText('All settings are up to date.')
        self.status.setProperty('feedback', 'info')
        self._refresh_status_style()

    def mark_error(self, message):
        self.status.setText(message)
        self.status.setProperty('feedback', 'error')
        self._refresh_status_style()

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, 'Choose output folder', self.output_folder.text())
        if folder:
            self.output_folder.setText(str(Path(folder)))
            self._notify_change()

    def choose_brave_executable(self):
        selected, _ = QFileDialog.getOpenFileName(
            self, 'Choose Brave executable', self.brave_executable.text(),
            'Brave executable (brave.exe);;Executable files (*.exe);;All files (*)'
        )
        if selected:
            self.brave_executable.setText(str(Path(selected)))
            self._notify_change()

    def selected_theme(self):
        return next(value for value, control in self.theme_buttons.items() if control.isChecked())

    def values(self, original=None):
        settings = dict(original or self._settings)
        settings.update({
            'output_folder': self.output_folder.text().strip(),
            'open_after_manual_generation': self.open_after.isChecked(),
            'manual_conflicts': self.conflicts.currentData(),
            'include_subfolders': self.include_subfolders.isChecked(),
            'clipboard_watcher': self.clipboard_watcher.isChecked(),
            'close_behavior': self.close_behavior.currentData(),
            'start_with_windows': self.start_windows.isChecked(),
            'notifications': self.notifications.isChecked(),
            'notification_sounds': self.notification_sounds.isChecked(),
            'theme': self.selected_theme(),
            'ai_studio_brave_executable': self.brave_executable.text().strip() or None,
        })
        return settings, self.default_template.currentData()
