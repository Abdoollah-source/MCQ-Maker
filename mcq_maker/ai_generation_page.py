"""AI Generation screen built from the shared MCQ Maker controls."""
from pathlib import Path
from threading import Event

from PySide6.QtCore import QStandardPaths, QThreadPool, Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QFileDialog, QHBoxLayout, QHeaderView,
                               QSpinBox, QSplitter, QTableWidget, QTableWidgetItem,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .ai_generation import AIGenerationWorker, AIRun, discover_pdfs
from .ai_providers import MODELS
from .components import Dropdown, button, label, panel


class AIGenerationPage(QWidget):
    manage_requested = Signal()
    history_changed = Signal()
    notification_requested = Signal(str, str, str, object)

    def __init__(self, repository, ai_library, settings, history=None, parent=None):
        super().__init__(parent)
        self.repository = repository
        self.library = ai_library
        self.settings = dict(settings)
        self.history = history

        self.folder = None
        self.lectures = []
        self.template_entries = {}
        self.default_template_id = None

        self.worker = None
        self.cancellation = None
        self.busy = False

        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)

        self._compact = False
        self._compact_breakpoint = 720

        self.setObjectName('page')
        self._build_ui()
        self.reload_library()
        self._ready()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(16)

        self._build_header(outer)
        outer.addWidget(label('Turn lecture PDFs into validated exams while you watch each result.'))

        self.setup_panel, self.setup_layout = panel()
        self.setup_layout.setSpacing(12)
        self._build_setup_fields()
        outer.addWidget(self.setup_panel)

        self._build_splitter(outer)

        footer = QHBoxLayout()
        self.summary = label('Total 0 · Completed 0 · Failed 0 · Remaining 0', 'muted')
        footer.addWidget(self.summary, 1)
        self.run_button = button('Run generation', primary=True)
        self.run_button.clicked.connect(self.toggle_run)
        footer.addWidget(self.run_button)
        outer.addLayout(footer)

    def _build_header(self, outer_layout):
        self.header_container = QWidget()
        self.header_layout = QHBoxLayout(self.header_container)
        self.header_layout.setContentsMargins(0, 0, 0, 0)
        self.header_layout.setSpacing(12)

        self.header_layout.addWidget(label('AI generation', 'title'), 1)

        self.library_button = button('Manage AI library', True)
        self.library_button.clicked.connect(self.manage_requested)
        self.library_button.setEnabled(self.library is not None)
        self.header_layout.addWidget(self.library_button)

        self.folder_button = button('Choose lecture folder', True)
        self.folder_button.clicked.connect(self.choose_folder)
        self.header_layout.addWidget(self.folder_button)

        outer_layout.addWidget(self.header_container)

    def _build_setup_fields(self):
        self.prompt_box, self.prompt = self._make_field('Prompt')
        self.provider_box, self.provider = self._make_field('Provider')
        self.model_box, self.model = self._make_field('Model')
        self.thinking_box, self.thinking = self._make_field('Thinking')
        for value in ('Low', 'Medium', 'High'):
            self.thinking.addItem(value, value.casefold())
        self.thinking.setCurrentIndex(2)

        self.per_conversation_box, self.per_conversation = self._make_spin_field('Lectures per conversation', 1, 20, 3)

        self.reference = Dropdown()
        self.reference.currentIndexChanged.connect(self._reference_changed)

        self.apply_all = QCheckBox('Apply to all lectures')
        self.apply_all.setChecked(True)
        self.apply_all.toggled.connect(self._reference_changed)

        self.folder_label = label('No lecture folder selected.', 'muted')
        self.folder_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self._apply_wide_layout()

        self.prompt.currentIndexChanged.connect(self._ready)
        self.provider.currentIndexChanged.connect(self._provider_changed)
        self.model.currentIndexChanged.connect(self._model_changed)

    def _make_field(self, title):
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(label(title, 'field'))
        control = Dropdown()
        layout.addWidget(control)
        return box, control

    def _make_spin_field(self, title, minimum, maximum, value):
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(label(title, 'field'))
        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        spin.setMinimumHeight(38)
        layout.addWidget(spin)
        return box, spin

    def _apply_wide_layout(self):
        self._clear_setup_layout()

        row1 = QHBoxLayout()
        row1.setSpacing(12)
        row1.addWidget(self.prompt_box, 3)
        row1.addWidget(self.provider_box, 1)
        row1.addWidget(self.model_box, 2)
        self.setup_layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(12)
        row2.addWidget(self.thinking_box, 1)
        row2.addWidget(self.per_conversation_box, 1)
        row2.addStretch(1)
        self.setup_layout.addLayout(row2)

        self.setup_layout.addWidget(label('Reference', 'field'))

        ref_row = QHBoxLayout()
        ref_row.setSpacing(12)
        ref_row.addWidget(self.reference, 1)
        ref_row.addWidget(self.apply_all)
        self.setup_layout.addLayout(ref_row)

        self.setup_layout.addWidget(self.folder_label)

    def _apply_compact_layout(self):
        self._clear_setup_layout()

        self.setup_layout.addWidget(self.prompt_box)
        self.setup_layout.addWidget(self.provider_box)
        self.setup_layout.addWidget(self.model_box)
        self.setup_layout.addWidget(self.thinking_box)
        self.setup_layout.addWidget(self.per_conversation_box)

        self.setup_layout.addWidget(label('Reference', 'field'))
        self.setup_layout.addWidget(self.reference)
        self.setup_layout.addWidget(self.apply_all)
        self.setup_layout.addWidget(self.folder_label)

    def _clear_setup_layout(self):
        while self.setup_layout.count():
            item = self.setup_layout.takeAt(0)
            if item.widget():
                item.widget().setParent(None)
            elif item.layout():
                self._clear_layout(item.layout())

    def _clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().setParent(None)
            elif item.layout():
                self._clear_layout(item.layout())

    def _build_splitter(self, outer_layout):
        self.splitter = QSplitter(Qt.Horizontal)

        lecture_panel, lecture_layout = panel()
        lecture_layout.setSpacing(8)

        lecture_header = QHBoxLayout()
        lecture_header.addWidget(label('Lectures', 'heading'), 1)
        self.lecture_count = label('0 PDFs', 'muted')
        lecture_header.addWidget(self.lecture_count)
        lecture_layout.addLayout(lecture_header)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(['Lecture', 'Reference', 'Status'])
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setMinimumHeight(150)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Interactive)
        self.table.setColumnWidth(1, 170)
        self.table.setColumnWidth(2, 90)
        lecture_layout.addWidget(self.table)

        self.splitter.addWidget(lecture_panel)

        log_panel, log_layout = panel()
        log_layout.setSpacing(8)
        log_layout.addWidget(label('Live log', 'heading'))

        self.log = QTreeWidget()
        self.log.setMinimumHeight(140)
        self.log.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.log.setWordWrap(True)
        self.log.setHeaderLabels(['Time', 'Step', 'Details'])
        self.log.setRootIsDecorated(False)
        self.log.setAlternatingRowColors(False)
        self.log.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.log.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.log.header().setSectionResizeMode(2, QHeaderView.Stretch)
        log_layout.addWidget(self.log)

        self.splitter.addWidget(log_panel)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setSizes([520, 480])

        outer_layout.addWidget(self.splitter, 1)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        available_width = self.width()
        should_be_compact = available_width < self._compact_breakpoint
        if should_be_compact != self._compact:
            self._compact = should_be_compact
            self._update_responsive_layout()

    def _update_responsive_layout(self):
        if self._compact:
            self._apply_compact_layout()
            self.header_layout.setDirection(QHBoxLayout.TopToBottom)
            self.splitter.setOrientation(Qt.Vertical)
        else:
            self._apply_wide_layout()
            self.header_layout.setDirection(QHBoxLayout.LeftToRight)
            self.splitter.setOrientation(Qt.Horizontal)

    def apply_settings(self, settings):
        self.settings = dict(settings)

    def set_template_snapshot(self, snapshot):
        self.template_entries = {x['id']: x for x in snapshot['templates']}
        self.default_template_id = snapshot.get('default_id')
        self._ready()

    def reload_library(self):
        if self.library is None:
            self.prompt.clear()
            self.reference.clear()
            self.provider.clear()
            self.model.clear()
            self._ready()
            return

        data = self.library.initialize()

        current_prompt = self.prompt.currentData()
        self.prompt.blockSignals(True)
        self.prompt.clear()
        for item in data['prompts']:
            self.prompt.addItem(
                f"{item['name']} · v{item['version']}" + (' · Default' if item['id'] == data['active_prompt_id'] else ''),
                item['id']
            )
        index = self.prompt.findData(current_prompt or data['active_prompt_id'])
        self.prompt.setCurrentIndex(max(0, index))
        self.prompt.blockSignals(False)

        current_ref = self.reference.currentData()
        self.reference.blockSignals(True)
        self.reference.clear()
        for item in data['references']:
            self.reference.addItem(f"{item['subject']} · {item['topic'] or item['name']}", item['id'])
        self.reference.setCurrentIndex(max(0, self.reference.findData(current_ref)))
        self.reference.blockSignals(False)

        current_provider = self.provider.currentData()
        self.provider.blockSignals(True)
        self.provider.clear()
        configured = sorted({x['provider'] for x in data['keys'] if x.get('status') != 'needs attention'})
        names = {'google': 'Google Gemini', 'openai': 'OpenAI', 'anthropic': 'Anthropic', 'openrouter': 'OpenRouter'}
        for provider in configured:
            self.provider.addItem(names[provider], provider)
        self.provider.setCurrentIndex(max(0, self.provider.findData(current_provider)))
        self.provider.blockSignals(False)

        self._provider_changed()
        self._populate_reference_cells()
        self._ready()

    def _provider_changed(self, *_):
        provider = self.provider.currentData()
        previous_model = self.model.currentData()
        self.model.clear()

        cached = self.library.load().get('models', {}).get(provider, []) if self.library else []
        if cached:
            for item in cached:
                self.model.addItem(item['name'], item['id'])
                self.model.setItemData(self.model.count() - 1, bool(item.get('thinking', True)), Qt.UserRole + 1)
        else:
            for item in MODELS.get(provider, []):
                model_id, display = item[:2]
                supports_thinking = bool(item[2]) if len(item) > 2 else True
                self.model.addItem(display, model_id)
                self.model.setItemData(self.model.count() - 1, supports_thinking, Qt.UserRole + 1)

        selected = self.model.findData(previous_model)
        if selected < 0 and provider == 'google':
            preferred = self.model.findData('gemini-3.8-flash')
            # A previously chosen Google model that disappeared from the live
            # catalog must not be silently replaced. Leave the field empty so
            # the user can choose an available model deliberately.
            selected = -1 if previous_model and cached else preferred
        self.model.setCurrentIndex(max(0, selected))
        self._model_changed()

    def _model_changed(self, *_):
        supports_thinking = bool(self.model.currentData(Qt.UserRole + 1)) if self.model.currentIndex() >= 0 else False
        self.thinking.setEnabled(supports_thinking)
        self.thinking.setToolTip('' if supports_thinking else 'This model does not offer a thinking-level control.')
        self._ready()

    def choose_folder(self):
        start = str(self.folder or Path(QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)))
        selected = QFileDialog.getExistingDirectory(self, 'Choose lecture folder', start)
        if selected:
            self.set_folder(Path(selected))

    def set_folder(self, folder):
        self.folder = Path(folder).resolve()
        try:
            self.lectures = discover_pdfs(self.folder)
        except OSError as exc:
            self.lectures = []
            self._add_log('', 'Error', str(exc))

        self.folder_label.setText(str(self.folder))
        self.lecture_count.setText(f'{len(self.lectures)} PDF' + ('' if len(self.lectures) == 1 else 's'))

        self.table.setRowCount(len(self.lectures))
        for row, lecture in enumerate(self.lectures):
            self.table.setRowHeight(row, 46)
            self.table.setItem(row, 0, QTableWidgetItem(lecture.name))
            self.table.setItem(row, 2, QTableWidgetItem('○ Pending'))

        self._populate_reference_cells()
        self.summary.setText(f'Total {len(self.lectures)} · Completed 0 · Failed 0 · Remaining {len(self.lectures)}')
        self._ready()

    def _populate_reference_cells(self):
        if not hasattr(self, 'table') or self.library is None:
            return
        data = self.library.load()
        selected = self.reference.currentData()
        for row in range(self.table.rowCount()):
            combo = Dropdown()
            for item in data['references']:
                combo.addItem(item['subject'] + (' · ' + item['topic'] if item['topic'] else ''), item['id'])
            combo.setCurrentIndex(max(0, combo.findData(selected)))
            combo.setEnabled(not self.apply_all.isChecked())
            combo.currentIndexChanged.connect(self._ready)
            self.table.setCellWidget(row, 1, combo)

    def _reference_changed(self, *_):
        selected = self.reference.currentData()
        for row in range(self.table.rowCount()):
            combo = self.table.cellWidget(row, 1)
            if combo:
                combo.setEnabled(not self.apply_all.isChecked())
                if self.apply_all.isChecked():
                    combo.setCurrentIndex(max(0, combo.findData(selected)))
        self._ready()

    def _references(self):
        return {
            str(lecture): self.table.cellWidget(row, 1).currentData()
            for row, lecture in enumerate(self.lectures)
            if self.table.cellWidget(row, 1) and self.table.cellWidget(row, 1).currentData()
        }

    def _ready(self, *_):
        if self.busy:
            return
        refs = self._references() if hasattr(self, 'table') else {}
        valid_template = self.template_entries.get(self.default_template_id, {}).get('valid', False)
        ready = bool(
            self.lectures
            and self.prompt.currentData()
            and self.provider.currentData()
            and self.model.currentData()
            and len(refs) == len(self.lectures)
            and valid_template
        )
        self.run_button.setEnabled(ready)
        self.run_button.setText('Run generation')
        if not ready:
            self.run_button.setToolTip('Choose a PDF folder, configured provider, prompt, reference for every lecture, and a valid default template.')
        else:
            self.run_button.setToolTip('')

    def toggle_run(self):
        if self.busy:
            self.cancel_run()
        else:
            self.start_run()

    def start_run(self):
        entry = self.template_entries.get(self.default_template_id)
        data = self.library.load()
        prompt = next((x for x in data['prompts'] if x['id'] == self.prompt.currentData()), None)
        if not entry or not prompt:
            self._ready()
            return
        try:
            template = self.repository.read_template(entry['id'])
        except Exception:
            self._add_log('', 'Error', 'The default template could not be read. Recheck it in Templates.')
            return

        thinking = self.thinking.currentData() if self.thinking.isEnabled() else None
        run = AIRun(
            tuple(self.lectures),
            self._references(),
            prompt,
            self.provider.currentData(),
            self.model.currentData(),
            thinking,
            self.per_conversation.value(),
            template,
            dict(entry),
            Path(self.settings['output_folder'])
        )
        self.busy = True
        self.log.clear()
        self.folder_button.setEnabled(False)
        self.library_button.setEnabled(False)
        self.run_button.setEnabled(True)
        self.run_button.setText('Cancel')
        self.run_button.setToolTip('Stop after the current API request finishes.')

        self.cancellation = Event()
        self.worker = AIGenerationWorker(run, self.library, self.history, self.cancellation)
        self.worker.signals.lecture_status.connect(self._lecture_status)
        self.worker.signals.log.connect(self._add_log)
        self.worker.signals.summary.connect(self._summary)
        self.worker.signals.finished.connect(self._finished)
        self.pool.start(self.worker)

    def cancel_run(self):
        if self.worker:
            self.worker.cancel()
            self.run_button.setEnabled(False)
            self.run_button.setText('Stopping…')

    def _lecture_status(self, path, status, detail):
        row = next((i for i, p in enumerate(self.lectures) if str(p) == path), -1)
        if row >= 0:
            symbols = {'pending': '○', 'processing': '◐', 'done': '✓', 'failed': '×'}
            item = self.table.item(row, 2)
            item.setText(f'{symbols.get(status, "○")} {status.title()}')
            item.setToolTip(detail)

    def _add_log(self, timestamp, kind, message):
        bar = self.log.verticalScrollBar()
        follow = bar.value() >= bar.maximum() - 4
        item = QTreeWidgetItem([timestamp, kind.title(), message])
        item.setToolTip(2, message)
        self.log.addTopLevelItem(item)
        if follow:
            self.log.scrollToBottom()

    def _summary(self, total, done, failed, remaining):
        self.summary.setText(f'Total {total} · Completed {done} · Failed {failed} · Remaining {remaining}')

    def _finished(self, results, cancelled):
        self.busy = False
        self.folder_button.setEnabled(True)
        self.library_button.setEnabled(True)
        self.worker = None
        self.cancellation = None
        self.history_changed.emit()
        self._ready()

        done = sum(x.status == 'done' for x in results)
        failed = sum(x.status == 'failed' for x in results)
        if not cancelled:
            self.notification_requested.emit('batch_complete', 'AI generation complete', f'{done} exams saved · {failed} failed', 'ai_generation')
