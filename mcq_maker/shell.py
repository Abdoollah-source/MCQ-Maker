"""Application shell and navigation between independently verified modules."""
from pathlib import Path
from PySide6.QtCore import Qt, QTimer, QSize, QByteArray, QStandardPaths, QUrl, Signal
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtGui import (QAction, QGuiApplication, QIcon, QPainter, QPen, QColor,
                           QPixmap, QTextCursor, QDesktopServices)
from PySide6.QtWidgets import (QMainWindow, QWidget, QFrame, QVBoxLayout,
    QHBoxLayout, QBoxLayout, QStackedWidget, QScrollArea, QPlainTextEdit,
    QMenu, QApplication, QFileDialog, QMessageBox)
from .exam_generator import proposed_filename, save_exam
from .quiz_validation import MAX_INPUT_BYTES, QuizError, validate_quiz
from .template_validation import TemplateError, validate_template
from .components import Dropdown, button, label, panel
from .folder_scan_page import FolderScanPage
from .history_page import HistoryPage
from .history import payload_hash
from .ai_generation_page import AIGenerationPage
from .google_ai_studio_page import GoogleAIStudioSemiAutomationPage


class QuizEditor(QPlainTextEdit):
    file_dropped = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._drop_active = False

    @staticmethod
    def _supported_file(event):
        urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
        return (len(urls) == 1 and urls[0].isLocalFile()
                and Path(urls[0].toLocalFile()).suffix.casefold() in {'.json', '.txt', '.md'})

    def dragEnterEvent(self, event):
        if self._supported_file(event):
            event.acceptProposedAction()
            self._drop_active = True
            self.viewport().update()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        event.acceptProposedAction() if self._supported_file(event) else event.ignore()

    def dragLeaveEvent(self, event):
        self._drop_active = False
        self.viewport().update()
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        self._drop_active = False
        self.viewport().update()
        if not self._supported_file(event):
            event.ignore()
            return
        event.acceptProposedAction()
        self.file_dropped.emit(event.mimeData().urls()[0].toLocalFile())

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self._drop_active:
            return
        painter = QPainter(self.viewport())
        accent = self.palette().highlight().color()
        wash = QColor(accent)
        wash.setAlpha(42)
        painter.fillRect(self.viewport().rect(), wash)
        painter.setPen(QPen(accent, 2))
        painter.drawRect(self.viewport().rect().adjusted(1, 1, -2, -2))
        painter.setPen(self.palette().text().color())
        font = painter.font()
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(self.viewport().rect(), Qt.AlignCenter, 'Drop to review this file')

def sheet_icon(color='#68445F'):
    pix = QPixmap(32, 32)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(QColor(color), 1.6))
    painter.drawRoundedRect(6, 3, 20, 26, 3, 3)
    for y in (10, 16, 22):
        painter.setBrush(QColor(color) if y == 16 else Qt.NoBrush)
        painter.drawEllipse(10, y-1, 3, 3)
        painter.drawLine(17, y+1, 22, y+1)
    painter.end()
    return QIcon(pix)

def fluent_icon(name, color):
    data = (Path(__file__).parent / 'assets' / f'{name}.svg').read_text(encoding='utf-8')
    data = data.replace('fill="#212121"', f'fill="{color}"')
    renderer = QSvgRenderer(QByteArray(data.encode()))
    pix = QPixmap(40, 40)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    renderer.render(painter)
    painter.end()
    pix.setDevicePixelRatio(2)
    return QIcon(pix)

class CreatePage(QWidget):
    history_changed = Signal()

    def __init__(self, repository=None, history=None):
        super().__init__()
        self.repository = repository
        self.history = history
        self.template_entries = {}
        self.validation_result = None
        self.output_folder = Path(QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)) / 'MCQ Maker Exams'
        self.validation_timer = QTimer(self)
        self.validation_timer.setSingleShot(True)
        # Keep the pause long enough to avoid validating every keystroke while
        # making pasted or imported quizzes ready without a perceptible lag.
        self.validation_timer.setInterval(220)
        self.validation_timer.timeout.connect(self.validate_input)
        self.setObjectName('page')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(24)
        header = QHBoxLayout()
        header.addWidget(label('Create exam', 'title'), 1)
        self.import_button = button('Import file', True)
        self.import_button.setToolTip('Import a .json, .txt, or .md quiz file (Ctrl+O)')
        self.import_button.clicked.connect(self.import_file)
        header.addWidget(self.import_button)
        layout.addLayout(header)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.body = QWidget()
        self.columns = QBoxLayout(QBoxLayout.LeftToRight, self.body)
        self.columns.setContentsMargins(0, 0, 0, 0)
        self.columns.setSpacing(20)
        self.left = QWidget()
        left = QVBoxLayout(self.left)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(12)
        left.addWidget(label('Questions', 'field'))
        toolbar = QHBoxLayout()
        self.paste = button('Paste from clipboard', True)
        self.paste.clicked.connect(self.paste_from_clipboard)
        toolbar.addWidget(self.paste)
        self.example = button('Try an example', True)
        self.example.clicked.connect(self.load_example)
        toolbar.addWidget(self.example)
        toolbar.addStretch()
        self.clear = button('Clear', True)
        self.clear.clicked.connect(self.editor_clear)
        self.clear.setEnabled(False)
        toolbar.addWidget(self.clear)
        left.addLayout(toolbar)
        self.editor = QuizEditor()
        self.editor.setAccessibleName('Questions')
        self.editor.setPlaceholderText('Paste your quiz JSON here.')
        self.editor.setReadOnly(False)
        self.editor.setMinimumHeight(280)
        self.editor.textChanged.connect(self.schedule_validate)
        self.editor.file_dropped.connect(self.load_file)
        left.addWidget(self.editor, 1)
        left.addWidget(label('You can also import a .json, .txt, or .md file.', 'muted'))
        self.validation = label('Paste questions to begin.')
        left.addWidget(self.validation)
        self.error_details = QPlainTextEdit()
        self.error_details.setReadOnly(True)
        self.error_details.setAccessibleName('Validation details')
        self.error_details.setMaximumHeight(130)
        self.error_details.hide()
        left.addWidget(self.error_details)
        self.details, fields = panel()
        fields.addWidget(label('Save details', 'heading'))
        template_group = QVBoxLayout()
        template_group.setSpacing(8)
        template_group.addWidget(label('Template', 'field'))
        self.template = Dropdown()
        self.template.addItem('No template selected')
        self.template.setEnabled(False)
        template_group.addWidget(self.template)
        fields.addLayout(template_group)
        output_group = QVBoxLayout()
        output_group.setSpacing(8)
        output_group.addWidget(label('Output folder', 'field'))
        self.output_label = label(str(self.output_folder), 'muted')
        self.output_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        output_group.addWidget(self.output_label)
        self.open_output = button('Open output folder', True)
        self.open_output.clicked.connect(self.open_output_folder)
        output_group.addWidget(self.open_output)
        fields.addLayout(output_group)
        self.detail_values = {}
        for title, value, key in [('Quiz title', 'Waiting for questions', 'title'), ('Questions', '—', 'count'), ('Proposed filename', 'Appears after validation', 'filename')]:
            group = QVBoxLayout()
            group.setSpacing(8)
            group.addWidget(label(title, 'field'))
            value_label = label(value, 'muted')
            value_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            group.addWidget(value_label)
            self.detail_values[key] = value_label
            fields.addLayout(group)
        fields.addStretch()
        self.columns.addWidget(self.left, 1)
        self.columns.addWidget(self.details)
        self.scroll.setWidget(self.body)
        layout.addWidget(self.scroll, 1)
        footer = QHBoxLayout()
        footer.addWidget(label('Your questions stay on this device until you generate an exam.', 'muted'), 1)
        self.generate = button('Generate exam', primary=True)
        self.generate.setMinimumSize(156, 40)
        self.generate.setToolTip('Generate this exam (Ctrl+Enter)')
        self.generate.clicked.connect(self.generate_exam)
        footer.addWidget(self.generate)
        layout.addLayout(footer)
        self.generate_shortcut = QAction('Generate exam', self)
        self.generate_shortcut.setShortcut('Ctrl+Return')
        self.generate_shortcut.triggered.connect(self.generate_exam)
        self.addAction(self.generate_shortcut)

    def set_template_entries(self, entries):
        self.template_entries = {entry['id']: entry for entry in entries if entry.get('valid')}
        self.schedule_validate()

    def apply_settings(self, settings):
        self.output_folder = Path(settings['output_folder'])
        self.output_label.setText(str(self.output_folder))
        self.open_after_manual_generation = settings['open_after_manual_generation']
        self.manual_conflicts = settings['manual_conflicts']
        self.schedule_validate()

    def schedule_validate(self, *_):
        if self.editor.toPlainText().strip():
            self.message('Checking questions…')
        self.validation_timer.start()

    def selected_template(self):
        return self.template_entries.get(self.template.currentData())

    def message(self, text, error=False):
        self.validation.setText(text)
        self.validation.setProperty('feedback', 'error' if error else 'info')
        self.validation.style().unpolish(self.validation)
        self.validation.style().polish(self.validation)

    def show_details(self, text=''):
        self.error_details.setPlainText(text)
        self.error_details.setVisible(bool(text))

    def validate_input(self):
        self.clear.setEnabled(bool(self.editor.toPlainText()))
        self.validation_result = None
        self.generate.setEnabled(False)
        self.show_details()
        self.editor.setProperty('validation', '')
        self.editor.style().unpolish(self.editor)
        self.editor.style().polish(self.editor)
        text = self.editor.toPlainText()
        entry = self.selected_template()
        if not text.strip():
            self.message('Paste questions to begin.')
            self.detail_values['title'].setText('Waiting for questions')
            self.detail_values['count'].setText('—')
            self.detail_values['filename'].setText('Appears after validation')
            return
        if not entry:
            self.message('Choose a valid template before generating.', True)
            return
        try:
            result = validate_quiz(text, entry['minimum_options'], entry['maximum_options'])
        except QuizError as exc:
            lines = str(exc).splitlines()
            noun = 'item' if len(lines) == 1 else 'items'
            self.message(f'Fix {len(lines)} {noun} before generating.', True)
            self.show_details(str(exc))
            self.editor.setProperty('validation', 'error')
            self.editor.style().unpolish(self.editor)
            self.editor.style().polish(self.editor)
            return
        self.validation_result = result
        self.detail_values['title'].setText(result.title)
        self.detail_values['count'].setText(str(len(result.questions)))
        self.detail_values['filename'].setText(proposed_filename(result.title, self.output_folder))
        if result.warnings:
            question_word = 'question' if len(result.questions) == 1 else 'questions'
            self.message(f'Ready · {len(result.questions)} {question_word} · {len(result.warnings)} warning{'s' if len(result.warnings) != 1 else ''} — review recommended.')
            self.show_details('\n'.join(result.warnings))
        else:
            question_word = 'question' if len(result.questions) == 1 else 'questions'
            self.message(f'Ready · {len(result.questions)} {question_word}.')
        self.generate.setEnabled(True)

    def paste_from_clipboard(self):
        text = QApplication.clipboard().text()
        if not text.strip():
            self.message('The clipboard does not contain text to paste.', True)
            return
        self.editor.insertPlainText(text)
        self.editor.setFocus()

    def load_example(self):
        self.editor.setPlainText('''[
  {"title": "Sample clinical review"},
  {"question": "Which option is correct?", "options": ["Option A", "Option B", "Option C", "Option D"], "correct": 0, "explanation": "Option A is the sample answer."}
]''')
        self.message('Sample loaded. Review it before generating an exam.')
        self.editor.setFocus()

    def import_file(self):
        filename, _ = QFileDialog.getOpenFileName(
            self, 'Import quiz file', '', 'Quiz files (*.json *.txt *.md)')
        if not filename:
            return
        self.load_file(filename)

    def load_file(self, filename):
        try:
            source = Path(filename)
            if source.suffix.casefold() not in {'.json', '.txt', '.md'}:
                raise QuizError('Choose a .json, .txt, or .md quiz file.')
            data = source.read_bytes()
            if len(data) > MAX_INPUT_BYTES:
                raise QuizError('This file is larger than 5 MiB. Split it into a smaller quiz and try again.')
            text = data.decode('utf-8-sig')
        except (OSError, UnicodeDecodeError, QuizError):
            self.message('MCQ Maker could not read that file as UTF-8 quiz text. Choose a .json, .txt, or .md file and try again.', True)
            return
        self.editor.setPlainText(text)
        self.editor.setFocus()

    def editor_clear(self, _checked=False, announce=True):
        cursor = self.editor.textCursor()
        cursor.select(QTextCursor.Document)
        cursor.removeSelectedText()
        self.editor.setTextCursor(cursor)
        if announce:
            self.validation_timer.stop()
            self.validation_result = None
            self.generate.setEnabled(False)
            self.clear.setEnabled(False)
            self.show_details()
            self.detail_values['title'].setText('Waiting for questions')
            self.detail_values['count'].setText('—')
            self.detail_values['filename'].setText('Appears after validation')
            self.message('Questions cleared. Press Ctrl+Z to restore them.')

    def open_output_folder(self):
        self.output_folder.mkdir(parents=True, exist_ok=True)
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.output_folder))):
            self.message('Windows could not open the output folder.', True)

    def clear_after_save(self):
        """Clear with an undo command but keep the saved-result message visible."""
        self.editor.blockSignals(True)
        self.editor_clear(announce=False)
        self.editor.blockSignals(False)
        self.validation_timer.stop()
        self.clear.setEnabled(False)
        self.validation_result = None
        self.generate.setEnabled(False)

    def generate_exam(self):
        if self.validation_result is None or self.repository is None:
            return
        entry = self.selected_template()
        if not entry:
            self.schedule_validate()
            return
        self.generate.setEnabled(False)
        result = None
        try:
            template = self.repository.read_template(entry['id'])
            contract = validate_template(template)
            result = validate_quiz(self.editor.toPlainText(), contract.minimum_options, contract.maximum_options)
            overwrite = False
            target = self.output_folder / proposed_filename(result.title, self.output_folder)
            if target.exists() and getattr(self, 'manual_conflicts', 'save_copy') == 'ask':
                choice = QMessageBox(self)
                choice.setWindowTitle('Existing exam file')
                choice.setText(f'“{target.name}” already exists in this folder.')
                choice.setInformativeText('Replace it, save a numbered copy, or cancel this generation?')
                replace = choice.addButton('Replace', QMessageBox.AcceptRole)
                copy = choice.addButton('Save copy', QMessageBox.ActionRole)
                cancel = choice.addButton('Cancel', QMessageBox.RejectRole)
                choice.exec()
                if choice.clickedButton() is cancel:
                    self.message('Generation cancelled. Your questions are still in the editor.')
                    self.schedule_validate()
                    return
                overwrite = choice.clickedButton() is replace
            saved = save_exam(template, result, self.output_folder, overwrite=overwrite)
        except (QuizError, TemplateError, OSError) as exc:
            if self.history is not None:
                known = result or self.validation_result
                try:
                    self.history.record_failure(
                        title=known.title if known else '',
                        question_count=len(known.questions) if known else 0,
                        input_hash='' if known is None else payload_hash(known.payload),
                        template_entry=entry,
                        source_mode='paste',
                        error_summary=str(exc),
                    )
                    self.history_changed.emit()
                except Exception:
                    pass
            self.message(str(exc), True)
            self.show_details(str(exc))
            self.schedule_validate()
            return
        history_note = ''
        if self.history is not None:
            try:
                self.history.record_success(quiz=result, output_path=saved, template_entry=entry, source_mode='paste')
                self.history_changed.emit()
            except Exception:
                history_note = ' The exam was saved, but History could not be updated.'
        self.clear_after_save()
        question_word = 'question' if len(result.questions) == 1 else 'questions'
        self.message(f'Saved “{saved.name}”. {len(result.questions)} {question_word} ready to open. Press Ctrl+Z in the questions box to restore the input.{history_note}')
        self.detail_values['filename'].setText(saved.name)
        if getattr(self, 'open_after_manual_generation', False):
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(saved)))

    def set_compact(self, compact):
        self.columns.setDirection(QBoxLayout.TopToBottom if compact else QBoxLayout.LeftToRight)
        self.details.setMinimumWidth(0 if compact else 288)
        self.details.setMaximumWidth(16777215 if compact else 288)

def empty_page(title, subtitle, actions, empty_title, empty_text):
    page = QWidget()
    page.setObjectName('page')
    layout = QVBoxLayout(page)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(24)
    top = QHBoxLayout()
    top.addWidget(label(title, 'title'), 1)
    for text in actions:
        top.addWidget(button(text))
    layout.addLayout(top)
    layout.addWidget(label(subtitle))
    box, content = panel()
    content.addStretch()
    heading = label(empty_title, 'heading')
    heading.setAlignment(Qt.AlignCenter)
    content.addWidget(heading)
    message = label(empty_text)
    message.setAlignment(Qt.AlignCenter)
    content.addWidget(message)
    content.addStretch()
    layout.addWidget(box, 1)
    return page, layout, content

class MainWindow(QMainWindow):
    def __init__(self, repository=None, settings=None, settings_store=None, apply_theme_callback=None,
                 history=None, startup_manager=None, ai_library=None):
        super().__init__()
        self.repository = repository
        self.settings = settings
        self.settings_store = settings_store
        self.apply_theme_callback = apply_theme_callback
        self.history = history
        self.startup_manager = startup_manager
        self.ai_library = ai_library
        self.ai_library_dialog = None
        self.settings_dialog = None
        self.tray_controller = None
        self.clipboard_watcher = None
        self.notification_service = None
        self.force_quit = False
        self.pending_quit = False
        self.setWindowTitle('MCQ Maker')
        self.setWindowIcon(sheet_icon())
        available = self.screen().availableGeometry()
        self.setMinimumSize(min(800, available.width()-48), min(560, available.height()-48))
        root = QWidget()
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.sidebar = QFrame()
        self.sidebar.setObjectName('sidebar')
        self.sidebar.setFixedWidth(176)
        nav = QVBoxLayout(self.sidebar)
        nav.setContentsMargins(12, 24, 12, 16)
        nav.setSpacing(4)
        nav.addWidget(label('MCQ Maker', 'heading'))
        nav.addSpacing(24)
        self.nav_buttons = []
        self.nav_titles = (
            'Create exam', 'Folder scan', 'History', 'Templates', 'AI generation',
            'AI Studio semi-automation',
        )
        for i, title in enumerate(self.nav_titles):
            b = button(title, True)
            b.setProperty('nav', True)
            b.setCheckable(True)
            b.setAutoExclusive(True)
            b.clicked.connect(lambda checked=False, index=i: self.navigate(index))
            nav.addWidget(b)
            self.nav_buttons.append(b)
        nav.addStretch()
        nav.addWidget(label('Clipboard watcher', 'muted'))
        self.watcher_status = button('Off', True)
        self.watcher_status.setObjectName('watcherStatus')
        self.watcher_status.setToolTip('Turn clipboard watching on or off.')
        self.watcher_status.clicked.connect(self.toggle_sidebar_clipboard_watcher)
        nav.addWidget(self.watcher_status)
        nav.addSpacing(12)
        self.settings_button = button('Settings', settings is not None)
        self.settings_button.setToolTip('Open Settings (Ctrl+,)')
        self.settings_button.clicked.connect(self.open_settings)
        nav.addWidget(self.settings_button)
        outer.addWidget(self.sidebar)
        self.content = QWidget()
        self.content.setObjectName('content')
        content = QVBoxLayout(self.content)
        content.setContentsMargins(24, 24, 24, 24)
        content.setSpacing(16)
        self.compact_nav = button('Navigate', True)
        menu = QMenu(self.compact_nav)
        for i, title in enumerate(self.nav_titles):
            action = menu.addAction(title)
            action.triggered.connect(lambda checked=False, index=i: self.navigate(index))
        menu.addSeparator()
        self.compact_watcher_status = menu.addAction('Clipboard watcher: Off')
        self.compact_watcher_status.setEnabled(False)
        self.compact_settings = menu.addAction('Settings')
        self.compact_settings.setEnabled(settings is not None)
        self.compact_settings.triggered.connect(self.open_settings)
        self.compact_nav.setMenu(menu)
        content.addWidget(self.compact_nav, alignment=Qt.AlignLeft)
        self.pages = QStackedWidget()
        self.create = CreatePage(repository, history)
        if settings is not None:
            self.create.apply_settings(settings)
        self.pages.addWidget(self.create)
        self.folder_scan = FolderScanPage(repository, settings, history)
        self.pages.addWidget(self.folder_scan)
        self.ai_generation = AIGenerationPage(repository, ai_library, settings or {}, history)
        self.ai_generation.manage_requested.connect(self.open_ai_library)
        self.google_ai_studio = GoogleAIStudioSemiAutomationPage(repository, settings or {}, history)
        self.google_ai_studio.preferences_changed.connect(self.save_ai_studio_preferences)
        self.history_page = HistoryPage(history)
        self.history_page.create_button.clicked.connect(lambda: self.navigate(0))
        self.create.history_changed.connect(self.history_page.reload)
        self.folder_scan.history_changed.connect(self.history_page.reload)
        self.ai_generation.history_changed.connect(self.history_page.reload)
        self.google_ai_studio.history_changed.connect(self.history_page.reload)
        self.pages.addWidget(self.history_page)
        self.template_page = None
        if repository is not None:
            from .template_page import TemplatePage
            self.template_page = TemplatePage(repository)
            self.template_page.library_changed.connect(self.update_templates)
            self.pages.addWidget(self.template_page)
            self.create.template.activated.connect(self.select_template)
            self.create.template.currentIndexChanged.connect(self.create.schedule_validate)
            QTimer.singleShot(0, self.template_page.reload)
        else:
            templates, template_layout, _ = empty_page('Templates', 'Your own copies, kept together and ready to use.', ['Import template'], 'No templates added yet', 'Add an HTML template to begin your library.')
            self.pages.addWidget(templates)
        self.pages.addWidget(self.ai_generation)
        self.pages.addWidget(self.google_ai_studio)
        content.addWidget(self.pages, 1)
        outer.addWidget(self.content, 1)
        self.navigate(0)
        self.process_clipboard_shortcut = QAction('Process Clipboard Now', self)
        self.process_clipboard_shortcut.setShortcut('Ctrl+Shift+V')
        self.process_clipboard_shortcut.triggered.connect(self.process_clipboard_now)
        self.addAction(self.process_clipboard_shortcut)
        self.import_shortcut = QAction('Import quiz file', self)
        self.import_shortcut.setShortcut('Ctrl+O')
        self.import_shortcut.triggered.connect(self.create.import_file)
        self.addAction(self.import_shortcut)
        self.settings_shortcut = QAction('Open Settings', self)
        self.settings_shortcut.setShortcut('Ctrl+,')
        self.settings_shortcut.triggered.connect(self.open_settings)
        self.addAction(self.settings_shortcut)
        self.history_shortcut = QAction('Search History', self)
        self.history_shortcut.setShortcut('Ctrl+F')
        self.history_shortcut.triggered.connect(self.open_history_search)
        self.addAction(self.history_shortcut)
        self.resize(min(1120, available.width()-48), min(780, available.height()-48))
        QTimer.singleShot(0, self.restore_or_fit)

    def restore_or_fit(self):
        available = self.screen().availableGeometry()
        saved = self.settings.get('window') if self.settings else None
        if saved:
            width = min(max(saved['width'], self.minimumWidth()), available.width()-48)
            height = min(max(saved['height'], self.minimumHeight()), available.height()-48)
            self.resize(width, height)
            candidate = self.frameGeometry()
            candidate.moveTo(saved['x'], saved['y'])
            if self._saved_geometry_is_visible(candidate):
                self.move(candidate.topLeft())
                self._restore_navigation_page(saved)
                if saved.get('maximized', False):
                    self.showMaximized()
                return
        frame = self.frameGeometry()
        dw, dh = frame.width()-self.width(), frame.height()-self.height()
        self.resize(min(1120, available.width()-48)-dw, min(780, available.height()-48)-dh)
        frame = self.frameGeometry()
        frame.moveCenter(available.center())
        self.move(frame.topLeft())
        if saved:
            self._restore_navigation_page(saved)
            if saved.get('maximized', False):
                self.showMaximized()

    @staticmethod
    def _saved_geometry_is_visible(candidate):
        """Accept a saved position if a meaningful part remains on any screen."""
        for screen in QGuiApplication.screens():
            visible = candidate.intersected(screen.availableGeometry())
            if visible.width() >= 64 and visible.height() >= 64:
                return True
        return False

    def _restore_navigation_page(self, saved):
        page = saved.get('page', 0)
        if isinstance(page, int) and 0 <= page < self.pages.count():
            self.navigate(page)

    def open_settings(self):
        if self.settings is None or self.settings_store is None or self.repository is None:
            return
        if self.settings_dialog is not None:
            self.settings_dialog.show()
            self.settings_dialog.raise_()
            self.settings_dialog.activateWindow()
            return
        from .settings_dialog import SettingsDialog
        snapshot = self.repository.list_templates()
        dialog = SettingsDialog(self.settings, snapshot['templates'], self)
        self.settings_dialog = dialog
        dialog.settings_changed.connect(lambda values, default_id: self.apply_settings_change(dialog, values, default_id))
        dialog.destroyed.connect(lambda: setattr(self, 'settings_dialog', None))
        available = self.screen().availableGeometry()
        dialog.resize(min(720, available.width() - 48), min(640, available.height() - 48))
        dialog.show()

    def open_ai_library(self):
        if self.ai_library is None:
            return
        if self.ai_library_dialog is not None:
            self.ai_library_dialog.show(); self.ai_library_dialog.raise_(); self.ai_library_dialog.activateWindow(); return
        from .ai_library_dialog import AILibraryDialog
        dialog = AILibraryDialog(self.ai_library, self)
        self.ai_library_dialog = dialog
        dialog.library_changed.connect(self.ai_generation.reload_library)
        dialog.destroyed.connect(lambda: setattr(self, 'ai_library_dialog', None))
        dialog.show()

    def apply_settings_change(self, dialog, new_settings, default_id):
        previous_startup = bool(self.settings and self.settings.get('start_with_windows'))
        requested_startup = bool(new_settings.get('start_with_windows'))
        try:
            if self.startup_manager is not None and requested_startup != previous_startup:
                self.startup_manager.set_enabled(requested_startup)
            self.settings = self.settings_store.save(new_settings)
            snapshot = self.repository.list_templates()
            default_changed = bool(default_id and default_id != snapshot['default_id'])
            if default_changed:
                self.repository.set_default(default_id)
        except Exception:
            if self.startup_manager is not None and requested_startup != previous_startup:
                try:
                    self.startup_manager.set_enabled(previous_startup)
                except Exception:
                    pass
            dialog.mark_error('These settings could not be applied. Check the output folder or Start with Windows and try again.')
            return
        self.create.apply_settings(self.settings)
        self.folder_scan.apply_settings(self.settings)
        self.ai_generation.apply_settings(self.settings)
        self.google_ai_studio.apply_settings(self.settings)
        if self.clipboard_watcher is not None:
            self.clipboard_watcher.apply_settings(self.settings)
        if self.tray_controller is not None:
            self.tray_controller.sync(self.settings)
        if self.apply_theme_callback is not None:
            self.apply_theme_callback(self.settings['theme'])
        if default_changed and self.template_page is not None:
            self.template_page.reload()
        dialog.mark_saved()

    def save_ai_studio_preferences(self, model, thinking, parallel_tabs):
        """Persist the AI Studio choices selected on the generation panel."""
        if self.settings is None or self.settings_store is None:
            return
        updated = dict(self.settings)
        updated['ai_studio_model'] = model
        updated['ai_studio_thinking'] = thinking
        updated['ai_studio_parallel_tabs'] = parallel_tabs
        try:
            self.settings = self.settings_store.save(updated)
        except Exception:
            return
        self.google_ai_studio.apply_settings(self.settings)

    def set_tray_controller(self, controller):
        self.tray_controller = controller
        controller.quit_requested.connect(self.request_quit)
        controller.process_clipboard_requested.connect(self.process_clipboard_now)
        controller.open_output_requested.connect(self.create.open_output_folder)
        controller.settings_requested.connect(self.open_settings)
        if self.settings is not None:
            controller.sync(self.settings)

    def set_clipboard_watcher(self, watcher):
        self.clipboard_watcher = watcher
        watcher.state_changed.connect(self.update_watcher_state)
        watcher.activity.connect(self.watcher_activity)
        watcher.history_changed.connect(self.history_page.reload)
        if self.tray_controller is not None:
            self.tray_controller.watcher_toggle_requested.connect(self.toggle_clipboard_watcher)
        self.update_watcher_state(watcher.state)

    def set_notification_service(self, service):
        self.notification_service = service
        service.activation_requested.connect(self.open_notification_target)
        self.folder_scan.notification_requested.connect(self.forward_notification)
        self.ai_generation.notification_requested.connect(self.forward_notification)
        self.google_ai_studio.notification_requested.connect(self.forward_notification)
        if self.clipboard_watcher is not None:
            self.clipboard_watcher.notification_requested.connect(self.forward_notification)

    def forward_notification(self, kind, title, body, target):
        if self.notification_service is not None:
            self.notification_service.notify(kind, title, body, target)

    def restore_window(self):
        if self.tray_controller is not None:
            self.tray_controller.open_window()
        else:
            self.showNormal()
            self.raise_()
            self.activateWindow()

    def open_notification_target(self, target):
        self.restore_window()
        if target == 'folder_report':
            self.navigate(1)
        elif target == 'ai_generation':
            self.navigate(4)
        elif target == 'google_ai_studio':
            self.navigate(5)
        elif target == 'settings':
            self.open_settings()
        elif target:
            path = Path(target)
            self.navigate(2)
            self.history_page.search.setText(path.name)
            self.history_page.search_timer.stop()
            self.history_page.reload()
            if self.history_page.table.topLevelItemCount():
                self.history_page.table.setCurrentItem(self.history_page.table.topLevelItem(0))

    def update_watcher_state(self, state):
        display = {'off': 'Off', 'on': 'Watching', 'paused': 'Paused'}[state]
        self.watcher_status.setText(display)
        self.watcher_status.setProperty('watcherState', state)
        self.watcher_status.setToolTip(
            'Turn clipboard watching on or off.' if state != 'on'
            else 'Clipboard watching is on. Click to turn it off.'
        )
        self.watcher_status.style().unpolish(self.watcher_status)
        self.watcher_status.style().polish(self.watcher_status)
        self.compact_watcher_status.setText(f'Clipboard watcher: {display}')
        if self.tray_controller is not None:
            self.tray_controller.set_watcher_state(state)

    def watcher_activity(self, message, error=False):
        self.watcher_status.setToolTip(message)
        self.compact_watcher_status.setToolTip(message)
        if self.isVisible() and not self.create.editor.toPlainText().strip():
            self.create.message(message, error)

    def toggle_clipboard_watcher(self):
        if self.clipboard_watcher is None or self.settings is None:
            return
        if self.clipboard_watcher.state == 'on':
            self.clipboard_watcher.pause()
            return
        if self.clipboard_watcher.state == 'paused':
            self.clipboard_watcher.resume()
            return
        updated = dict(self.settings)
        updated['clipboard_watcher'] = True
        try:
            self.settings = self.settings_store.save(updated)
        except Exception:
            self.watcher_activity('Clipboard watching could not be enabled because Settings could not be saved.', True)
            return
        self.clipboard_watcher.apply_settings(self.settings)
        if self.tray_controller is not None:
            self.tray_controller.sync(self.settings)
        self.watcher_activity('Clipboard watcher is on. Valid quiz text will be saved automatically. Use Pause in the tray menu to stop watching temporarily.')

    def toggle_sidebar_clipboard_watcher(self):
        """Persist an explicit on/off choice from the main sidebar control."""
        if self.clipboard_watcher is None or self.settings is None or self.settings_store is None:
            return
        self._set_clipboard_watcher_enabled(self.clipboard_watcher.state != 'on')

    def _set_clipboard_watcher_enabled(self, enabled):
        updated = dict(self.settings)
        updated['clipboard_watcher'] = bool(enabled)
        try:
            self.settings = self.settings_store.save(updated)
        except Exception:
            self.watcher_activity('Clipboard watching could not be changed because Settings could not be saved.', True)
            return False
        self.clipboard_watcher.apply_settings(self.settings)
        if self.settings_dialog is not None:
            self.settings_dialog.set_clipboard_watcher_enabled(enabled)
        if self.tray_controller is not None:
            self.tray_controller.sync(self.settings)
        return True

    def process_clipboard_now(self):
        text = QApplication.clipboard().text()
        if self.tray_controller is not None:
            self.tray_controller.open_window()
        self.navigate(0)
        if not text.strip():
            self.create.message('The clipboard does not contain text to review.', True)
            return
        self.create.editor.selectAll()
        self.create.editor.insertPlainText(text)
        self.create.editor.setFocus()

    def resolve_clipboard_conflict(self, target):
        if self.tray_controller is not None and not self.isVisible():
            self.tray_controller.open_window()
        choice = QMessageBox(self)
        choice.setWindowTitle('Existing exam file')
        choice.setText(f'“{target.name}” already exists in the output folder.')
        choice.setInformativeText('Choose how MCQ Maker should handle this clipboard exam.')
        replace = choice.addButton('Replace', QMessageBox.AcceptRole)
        copy = choice.addButton('Save numbered copy', QMessageBox.ActionRole)
        skip = choice.addButton('Skip', QMessageBox.RejectRole)
        choice.setDefaultButton(copy)
        choice.setEscapeButton(skip)
        choice.exec()
        if choice.clickedButton() is replace:
            return 'replace'
        if choice.clickedButton() is copy:
            return 'copy'
        return 'cancel'

    def persist_window(self):
        if self.settings is None or self.settings_store is None:
            return
        frame = self.normalGeometry() if self.isMaximized() else self.frameGeometry()
        self.settings['window'] = {
            'x': frame.x(), 'y': frame.y(), 'width': frame.width(), 'height': frame.height(),
            'maximized': self.isMaximized(), 'page': self.pages.currentIndex(),
        }
        try:
            self.settings = self.settings_store.save(self.settings)
        except Exception:
            pass

    def navigate(self, index):
        self.pages.setCurrentIndex(index)
        self.nav_buttons[index].setChecked(True)
        if index == 2:
            self.history_page.reload()
        if index == 3 and self.template_page is not None:
            self.template_page.reload()

    def open_history_search(self):
        self.navigate(2)
        QTimer.singleShot(0, self.history_page.search.setFocus)

    def update_templates(self, snapshot):
        combo = self.create.template
        combo.blockSignals(True)
        combo.clear()
        valid = [entry for entry in snapshot['templates'] if entry['valid']]
        for entry in valid:
            combo.addItem(entry['display_name'], entry['id'])
        if not valid:
            combo.addItem('No valid template selected', None)
        index = combo.findData(snapshot['default_id'])
        self.selected_template_index = max(0, index)
        combo.insertSeparator(combo.count())
        combo.addItem('Manage templates…', '__manage__')
        combo.setCurrentIndex(self.selected_template_index)
        combo.setEnabled(True)
        combo.blockSignals(False)
        self.create.set_template_entries(snapshot['templates'])
        self.folder_scan.set_template_snapshot(snapshot)
        self.ai_generation.set_template_snapshot(snapshot)
        self.google_ai_studio.set_template_snapshot(snapshot)

    def select_template(self, index):
        if self.create.template.itemData(index) == '__manage__':
            self.create.template.setCurrentIndex(self.selected_template_index)
            self.navigate(3)
        else:
            self.selected_template_index = index

    def closeEvent(self, event):
        if (not self.force_quit and self.settings is not None
                and self.settings.get('close_behavior') == 'tray'):
            if self.tray_controller is None or not self.tray_controller.available:
                QMessageBox.warning(
                    self,
                    'System tray unavailable',
                    'Windows is not making the system tray available right now, so MCQ Maker will stay open.',
                )
                event.ignore()
                return
            self.persist_window()
            if not self.settings.get('tray_explanation_shown', False):
                QMessageBox.information(
                    self,
                    'Still running in the system tray',
                    'MCQ Maker will keep running after its window closes. Use its tray icon to open it again or quit.',
                )
                self.settings['tray_explanation_shown'] = True
                try:
                    self.settings = self.settings_store.save(self.settings)
                except Exception:
                    pass
            if self.settings_dialog is not None:
                self.settings_dialog.close()
            event.ignore()
            self.hide()
            self.tray_controller.show()
            return
        if self.template_page is not None and self.template_page.busy:
            self.template_page.message('Please wait for the current template operation to finish before closing.')
            event.ignore()
            return
        if self.folder_scan.busy:
            self.folder_scan.cancel_scan()
            self.folder_scan._message('Please wait for the current folder scan to stop before closing.', error=True)
            event.ignore()
            return
        if self.ai_generation.busy:
            self.ai_generation.cancel_run()
            event.ignore()
            return
        if self.google_ai_studio.worker is not None:
            self.google_ai_studio.stop_run()
            event.ignore()
            return
        if self.clipboard_watcher is not None:
            if self.clipboard_watcher.busy:
                event.ignore()
                self.request_quit()
                return
            self.clipboard_watcher.shutdown()
        self.persist_window()
        super().closeEvent(event)

    def request_quit(self):
        """Stop new work, cancel the cancellable scan, then exit when workers are quiet."""
        if self.pending_quit:
            return
        self.pending_quit = True
        self.force_quit = True
        if self.clipboard_watcher is not None:
            self.clipboard_watcher.shutdown()
        if self.folder_scan.busy:
            self.folder_scan.cancel_scan()
        if self.ai_generation.busy:
            self.ai_generation.cancel_run()
        if self.google_ai_studio.worker is not None:
            self.google_ai_studio.stop_run()
        self._finish_quit()

    def _finish_quit(self):
        template_busy = self.template_page is not None and self.template_page.busy
        watcher_busy = self.clipboard_watcher is not None and self.clipboard_watcher.busy
        if (template_busy or self.folder_scan.busy or self.ai_generation.busy
                or self.google_ai_studio.worker is not None or watcher_busy):
            QTimer.singleShot(100, self._finish_quit)
            return
        if self.settings_dialog is not None:
            self.settings_dialog.close()
        self.persist_window()
        if self.tray_controller is not None:
            self.tray_controller.hide()
        self.close()
        QApplication.quit()

    def update_icons(self, color):
        for b, name in zip(self.nav_buttons, ('create', 'folder', 'history', 'templates', 'ai', 'ai')):
            b.setIcon(fluent_icon(name, color))
            b.setIconSize(QSize(20, 20))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'create'):
            compact = self.frameGeometry().width() < 960
            self.sidebar.setVisible(not compact)
            self.compact_nav.setVisible(compact)
            self.create.set_compact(compact)
            self.history_page.set_compact(self.frameGeometry().width() < 1000)
