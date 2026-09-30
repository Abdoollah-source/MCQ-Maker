"""Human-assisted Google AI Studio generation inside the MCQ Maker desktop app."""
from __future__ import annotations

import asyncio
from pathlib import Path
from threading import Event

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (QFileDialog, QGridLayout, QHBoxLayout, QLayout,
                               QLabel, QMessageBox, QScrollArea, QSizePolicy,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .ai_studio_browser import AIStudioBrowserManager
from .ai_studio_batch import (BatchStateStore, GoogleAIStudioBatchController,
                               create_manifest,
                               manifest_matches_configuration)
from .components import Dropdown, button, label, panel
from .default_resources import DefaultResourceStore
from .localization import tr


class GoogleAIStudioSignals(QObject):
    event = Signal(str)
    job_updated = Signal(str, str, str, str)
    completed = Signal(str, int)
    failed = Signal(str)
    cancelled = Signal()


class CompactPathField(QWidget):
    """A compact, tooltip-backed path display with the former field interface."""

    def __init__(self, title, selected_description, choose_label, choose, parent=None):
        super().__init__(parent)
        self._value = ''
        self._selected_description = selected_description
        self.setAccessibleName(title)
        self.setMinimumHeight(52)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        details = QWidget(self)
        details_layout = QVBoxLayout(details)
        details_layout.setContentsMargins(0, 0, 0, 0)
        details_layout.setSpacing(2)
        self.name_label = QLabel('Not selected')
        self.name_label.setProperty('role', 'field')
        self.name_label.setTextFormat(Qt.PlainText)
        self.name_label.setWordWrap(False)
        self.name_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.detail_label = QLabel('Choose a file or folder to continue.')
        self.detail_label.setProperty('role', 'muted')
        self.detail_label.setTextFormat(Qt.PlainText)
        self.detail_label.setWordWrap(False)
        self.detail_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        details_layout.addWidget(self.name_label)
        details_layout.addWidget(self.detail_label)
        self.choose_button = button(choose_label, True)
        self.choose_button.setMinimumWidth(132)
        self.choose_button.clicked.connect(choose)
        self.copy_button = button('Copy path')
        self.copy_button.setMinimumWidth(96)
        self.copy_button.clicked.connect(self.copy_path)
        layout.addWidget(details, 1)
        layout.addWidget(self.choose_button)
        layout.addWidget(self.copy_button)

    def text(self):
        return self._value

    def setText(self, value):
        self._value = str(value or '')
        if not self._value:
            self.name_label.setText('Not selected')
            self.detail_label.setText('Choose a file or folder to continue.')
            self.setToolTip('')
            self.name_label.setToolTip('')
            self.detail_label.setToolTip('')
            self.copy_button.setEnabled(False)
            return
        path = Path(self._value)
        self.name_label.setText(path.name or self._value)
        self.detail_label.setText(f'{self._selected_description} · {path.parent.name or path.parent}')
        self.setToolTip(self._value)
        self.name_label.setToolTip(self._value)
        self.detail_label.setToolTip(self._value)
        self.copy_button.setEnabled(True)

    def copy_path(self):
        if self._value:
            QGuiApplication.clipboard().setText(self._value)


class GoogleAIStudioWorker(QRunnable):
    """Run the resumable sequential batch away from the Qt GUI thread."""

    def __init__(self, *, prompt_path, reference_path, lecture_folder, output_folder,
                 model, thinking, brave_executable, template, template_entry, history,
                 manifest=None, batch_store=None, max_workers=1):
        super().__init__()
        self.prompt_path = Path(prompt_path)
        self.reference_path = Path(reference_path)
        self.lecture_folder = Path(lecture_folder)
        self.output_folder = Path(output_folder)
        self.model = model
        self.thinking = thinking
        self.brave_executable = brave_executable
        self.template = template
        self.template_entry = dict(template_entry)
        self.history = history
        self.manifest = manifest
        self.max_workers = int(max_workers)
        if self.max_workers not in {1, 2}:
            raise ValueError('max_workers must be 1 or 2.')
        self.batch_store = batch_store or BatchStateStore()
        self.cancelled = Event()
        self.paused = Event()
        self.signals = GoogleAIStudioSignals()

    def cancel(self):
        self.cancelled.set()

    async def _run(self):
        manager = AIStudioBrowserManager(
            self.brave_executable, launch_mode='cdp'
        )
        manifest = self.manifest or create_manifest(
            self.lecture_folder, self.prompt_path, self.reference_path,
            self.model, self.thinking, self.output_folder,
            conflict_policy='save_copy', store=self.batch_store,
            template_id=self.template_entry.get('id', ''), template=self.template,
            max_workers=self.max_workers,
        )
        # The selected UI limit is authoritative for this Run. Reapply it at
        # the worker boundary so a restored manifest cannot silently force a
        # one-worker run after the user selected the two-tab experiment.
        if manifest.max_workers != self.max_workers:
            manifest.max_workers = self.max_workers
            self.batch_store.save(manifest)
        self.manifest = manifest
        completed_before = {
            (item.source_path, item.output_path) for item in manifest.lectures
            if item.status == 'Completed' and item.output_path
        }
        controller = GoogleAIStudioBatchController(
            manifest, browser_manager=manager, template=self.template,
            template_entry=self.template_entry, event_callback=self._batch_event,
            cancellation=self.cancelled, pause_event=self.paused, store=self.batch_store,
        )
        completed = await controller.run()
        if self.history is not None:
            for item in completed.lectures:
                if item.status == 'Completed' and item.output_path and (item.source_path, item.output_path) not in completed_before:
                    try:
                        self.history.record_success(title=item.title, question_count=item.question_count,
                            input_hash=item.fingerprint.get('sha256', ''), output_path=Path(item.output_path), template_entry=self.template_entry,
                            source_mode='google_ai_studio_semi_automation', source_path=item.source_path)
                    except Exception:
                        self.signals.event.emit('[history_not_saved]')
        saved = [x for x in completed.lectures if x.status == 'Completed' and x.output_path
                 and (x.source_path, x.output_path) not in completed_before]
        last = saved[-1] if saved else None
        return (Path(last.output_path) if last else self.output_folder), (last.question_count if last else 0)

    def _batch_event(self, lecture, status, detail):
        attempts = '0'
        if self.manifest is not None:
            matched = next((item for item in self.manifest.lectures if item.file_name == lecture), None)
            if matched is not None:
                attempts = str(matched.attempt_count)
        if lecture != 'Worker Pool':
            self.signals.job_updated.emit(lecture, status, detail, attempts)
        self.signals.event.emit(f'[{status.lower().replace(" ", "_")}]: {lecture} {detail}'.strip())

    def run(self):
        try:
            output, question_count = asyncio.run(self._run())
        except Exception as exc:
            self.signals.failed.emit(str(exc) or 'AI Studio Automation stopped unexpectedly.')
        else:
            if self.cancelled.is_set():
                self.signals.cancelled.emit()
            else:
                self.signals.completed.emit(str(output), question_count)


class GoogleAIStudioSemiAutomationPage(QWidget):
    """Durable AI Studio queue, with an explicitly enabled two-tab experiment."""

    history_changed = Signal()
    preferences_changed = Signal(str, str, int)
    notification_requested = Signal(str, str, str, object)

    def __init__(self, repository, settings, history=None, parent=None, batch_store=None):
        super().__init__(parent)
        self.repository = repository
        # The production shell always supplies its per-user repository. The
        # lightweight no-repository shell is retained for isolated UI tests and
        # previews, where bundled read-only defaults are sufficient.
        self.default_resources = DefaultResourceStore(repository.root) if repository is not None else None
        self.settings = dict(settings or {})
        self.history = history
        self.batch_store = batch_store or BatchStateStore()
        unfinished = self.batch_store.unfinished()
        self.resume_manifest = unfinished[0] if unfinished else None
        self.template_entries = {}
        self.worker = None
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.setObjectName('page')
        self._build_ui()
        snapshot = {'templates': [], 'default_id': None}
        if self.repository is not None:
            try:
                snapshot = self.repository.list_templates()
            except OSError:
                # MainWindow is also used by lightweight tests before a template
                # repository has been initialized. TemplatePage will refresh us later.
                pass
        self.set_template_snapshot(snapshot)
        self._ready()
        self.discard_button.setVisible(bool(self.resume_manifest))
        if self.resume_manifest:
            self._restore_manifest_fields(self.resume_manifest)
            self._render_manifest_jobs(self.resume_manifest)
            self.run_button.setText(self._saved_batch_action(self.resume_manifest))
            self._show_saved_batch_summary(self.resume_manifest)

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(20)

        header = QHBoxLayout()
        self.title_label = label(tr('page.ai_studio'), 'title')
        header.addWidget(self.title_label, 1)
        outer.addLayout(header)
        outer.addWidget(label(
            'Choose a lecture folder. MCQ Maker preserves completed exams while it processes the queue. '
            'Parallel AI Studio tabs remains experimental.'
        ))

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.body = QWidget()
        self.body.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        body_layout = QVBoxLayout(self.body)
        body_layout.setContentsMargins(0, 0, 4, 0)
        body_layout.setSpacing(16)
        body_layout.setSizeConstraint(QLayout.SetMinimumSize)

        setup, setup_layout = panel()
        setup_layout.setSpacing(14)
        setup_layout.addWidget(label('Files and setup', 'heading'))
        self.prompt_path = self._path_field(
            setup_layout, 'Prompt', 'Prompt file selected', 'Choose prompt',
            self._default_resource_path('prompt'),
            'Text files (*.txt *.md);;All files (*)',
        )
        self.reference_path = self._path_field(
            setup_layout, 'Reference', 'Reference file selected', 'Choose reference',
            self._default_resource_path('reference'),
            'Text files (*.txt *.md);;All files (*)',
        )
        self.lecture_path = self._folder_field(setup_layout, 'Lecture folder', 'Lecture folder selected', '')
        self.output_folder = self._folder_field(
            setup_layout, 'Output folder', 'Output folder selected', self.settings.get('output_folder', ''),
        )
        body_layout.addWidget(setup)

        configuration, configuration_layout = panel()
        configuration_layout.setSpacing(12)
        configuration_layout.addWidget(label('Generation configuration', 'heading'))
        options = QWidget()
        options_layout = QGridLayout(options)
        options_layout.setContentsMargins(0, 0, 0, 0)
        options_layout.setHorizontalSpacing(12)
        options_layout.setVerticalSpacing(6)
        options_layout.addWidget(label('Model', 'field'), 0, 0)
        options_layout.addWidget(label('Thinking level', 'field'), 0, 1)
        options_layout.addWidget(label('Parallel AI Studio tabs', 'field'), 0, 2)
        self.model = Dropdown()
        self._set_model_options(self.settings.get('ai_studio_model'))
        options_layout.addWidget(self.model, 1, 0)
        self.thinking = Dropdown()
        for value in ('High', 'Medium', 'Low'):
            self.thinking.addItem(value, value)
        selected_thinking = self.settings.get('ai_studio_thinking') or 'High'
        index = self.thinking.findData(selected_thinking)
        self.thinking.setCurrentIndex(max(0, index))
        options_layout.addWidget(self.thinking, 1, 1)
        self.parallel_tabs = Dropdown()
        self.parallel_tabs.addItem('1', 1)
        self.parallel_tabs.addItem('2 experimental', 2)
        selected_parallel = self.settings.get('ai_studio_parallel_tabs', 1)
        parallel_index = self.parallel_tabs.findData(selected_parallel if selected_parallel in {1, 2} else 1)
        self.parallel_tabs.setCurrentIndex(max(0, parallel_index))
        options_layout.addWidget(self.parallel_tabs, 1, 2)
        self.template = Dropdown()
        options_layout.addWidget(label('Exam template', 'field'), 2, 0, 1, 2)
        options_layout.addWidget(self.template, 3, 0, 1, 2)
        options_layout.setColumnStretch(0, 3)
        options_layout.setColumnStretch(1, 2)
        options_layout.setColumnStretch(2, 2)
        configuration_layout.addWidget(options)
        body_layout.addWidget(configuration)

        activity, activity_layout = panel()
        self.activity_panel = activity
        activity_layout.setSpacing(10)
        activity_layout.addWidget(label('Batch activity', 'heading'))
        self.batch_state = label('No saved batch', 'field')
        activity_layout.addWidget(self.batch_state)
        self.status = label('Choose a lecture folder to start a new AI Studio automation batch.', 'muted')
        self.status.setMinimumHeight(18)
        self.status.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        activity_layout.addWidget(self.status)
        self.job_table = QTreeWidget()
        self.job_table.setAccessibleName('Lecture queue status')
        self.job_table.setHeaderLabels(['Lecture', 'Status', 'Attempts', 'Details'])
        self.job_table.setRootIsDecorated(False)
        self.job_table.setWordWrap(True)
        self.job_table.setMinimumHeight(110)
        self.job_table.header().setStretchLastSection(True)
        self.job_table.header().resizeSection(0, 190)
        self.job_table.header().resizeSection(1, 165)
        self.job_table.header().resizeSection(2, 70)
        activity_layout.addWidget(self.job_table)
        activity_layout.addWidget(label('Activity log', 'field'))
        self.log = QTreeWidget()
        self.log.setAccessibleName('Google AI Studio activity')
        self.log.setHeaderLabels(['Step', 'Details'])
        self.log.setRootIsDecorated(False)
        self.log.setAlternatingRowColors(False)
        self.log.setWordWrap(True)
        self.log.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.log.header().setStretchLastSection(True)
        self.log.header().resizeSection(0, 190)
        self.log.setMinimumHeight(190)
        activity_layout.addWidget(self.log)
        body_layout.addWidget(activity, 1)
        self.scroll.setWidget(self.body)
        outer.addWidget(self.scroll, 1)

        footer = QHBoxLayout()
        self.open_output_button = button('Open output folder', True)
        self.open_output_button.clicked.connect(self.open_output_folder)
        footer.addWidget(self.open_output_button)
        footer.addStretch()
        self.pause_button = button('Pause after current')
        self.pause_button.clicked.connect(self.toggle_pause)
        footer.addWidget(self.pause_button)
        self.discard_button = button('Discard saved batch')
        self.discard_button.clicked.connect(self.discard_saved_batch)
        self.discard_button.setVisible(bool(getattr(self, 'resume_manifest', None)))
        footer.addWidget(self.discard_button)
        self.run_button = button('Start batch', primary=True)
        self.run_button.clicked.connect(self.toggle_run)
        footer.addWidget(self.run_button)
        outer.addLayout(footer)

    def _path_field(self, layout, title, selected_description, action, initial, file_filter):
        field = CompactPathField(
            title, selected_description, action,
            lambda: self.choose_file(field, title, file_filter),
        )
        field.setText(str(initial) if Path(initial).is_file() else '')
        layout.addWidget(field)
        return field

    def _default_resource_path(self, kind):
        if self.default_resources is not None:
            return self.default_resources.default_path(kind)
        return DefaultResourceStore().bundled_path(kind)

    def _folder_field(self, layout, title, selected_description, initial):
        field = CompactPathField(
            title, selected_description, 'Choose folder',
            lambda: self.choose_folder(field),
        )
        field.setText(str(initial))
        layout.addWidget(field)
        return field

    def _set_model_options(self, preferred):
        values = ['Gemini 3.8 Flash']
        if preferred and preferred not in values:
            values.insert(0, preferred)
        for value in values:
            self.model.addItem(value, value)
        selected = self.model.findData(preferred or 'Gemini 3.8 Flash')
        self.model.setCurrentIndex(max(0, selected))

    def set_template_snapshot(self, snapshot):
        self.template_entries = {item['id']: item for item in snapshot.get('templates', []) if item.get('valid')}
        self.template.blockSignals(True)
        self.template.clear()
        for entry in self.template_entries.values():
            self.template.addItem(entry['display_name'], entry['id'])
        selected = self.template.findData(snapshot.get('default_id'))
        self.template.setCurrentIndex(max(0, selected))
        self.template.setEnabled(bool(self.template_entries))
        self.template.blockSignals(False)

    def apply_settings(self, settings):
        self.settings = dict(settings)
        if not self.worker:
            self.output_folder.setText(self.settings.get('output_folder', ''))
            selected_parallel = self.settings.get('ai_studio_parallel_tabs', 1)
            parallel_index = self.parallel_tabs.findData(selected_parallel if selected_parallel in {1, 2} else 1)
            self.parallel_tabs.setCurrentIndex(max(0, parallel_index))

    def retranslate(self):
        self.title_label.setText(tr('page.ai_studio'))

    def choose_file(self, field, title, file_filter):
        selected, _ = QFileDialog.getOpenFileName(self, f'Choose {title.casefold()}', field.text(), file_filter)
        if selected:
            field.setText(str(Path(selected)))

    def choose_folder(self, field):
        selected = QFileDialog.getExistingDirectory(self, 'Choose output folder', field.text())
        if selected:
            field.setText(str(Path(selected)))

    def open_output_folder(self):
        folder = Path(self.output_folder.text().strip())
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def _validation_error(self):
        inputs = (
            (self.prompt_path.text().strip(), 'Choose the saved prompt file.'),
            (self.reference_path.text().strip(), 'Choose the reference file.'),
            (self.lecture_path.text().strip(), 'Choose the lecture folder.'),
        )
        for value, message in inputs:
            if (message.endswith('folder.') and not Path(value).is_dir()) or (not message.endswith('folder.') and not Path(value).is_file()):
                return message
        output = Path(self.output_folder.text().strip())
        if not output.is_absolute():
            return 'Choose a complete output folder path.'
        if not self.template.currentData():
            return 'Choose a valid exam template.'
        return None

    def start_run(self):
        error = self._validation_error()
        if error:
            self.status.setText(error)
            self.status.setProperty('feedback', 'error')
            self._refresh_status_style()
            return
        template_id = self.template.currentData()
        template_entry = self.template_entries[template_id]
        try:
            template = self.repository.read_template(template_id)
        except Exception:
            self.status.setText('MCQ Maker could not read the selected exam template.')
            self.status.setProperty('feedback', 'error')
            self._refresh_status_style()
            return
        max_workers = int(self.parallel_tabs.currentData())
        self.preferences_changed.emit(
            str(self.model.currentData()), str(self.thinking.currentData()), max_workers,
        )
        self.log.clear()
        resume = self.resume_manifest
        resume_manifest = None
        if resume and Path(resume.folder) == Path(self.lecture_path.text().strip()).resolve():
            if manifest_matches_configuration(
                resume, prompt_path=self.prompt_path.text().strip(),
                reference_path=self.reference_path.text().strip(),
                model=self.model.currentData(), thinking=self.thinking.currentData(),
                output_folder=self.output_folder.text().strip(), conflict_policy='save_copy',
                template_id=template_entry.get('id', ''), template=template,
            ):
                resume_manifest = resume
            else:
                answer = QMessageBox.question(
                    self, 'Queue settings changed',
                    'The saved queue uses different prompt, reference, model, thinking, template, or output settings. '
                    'Start a new queue with the current settings? The saved queue will remain available.',
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
                )
                if answer != QMessageBox.Yes:
                    return
                self.resume_manifest = None
        if resume_manifest and any(item.status == 'Interrupted' for item in resume_manifest.lectures):
            answer = QMessageBox.question(
                self, 'Review interrupted lecture',
                'At least one lecture request may have been submitted before MCQ Maker closed. '
                'Check AI Studio before continuing. Resume will explicitly retry that lecture and may create a numbered copy if an exam was already saved. Continue?',
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
        if resume_manifest and any(item.status == 'Needs attention' for item in resume_manifest.lectures):
            answer = QMessageBox.question(
                self, 'Retry lectures needing attention',
                'Some lectures need attention. Retry them now using a fresh conversation? This may create a numbered copy if an exam was already saved.',
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if answer == QMessageBox.Yes:
                for item in resume_manifest.lectures:
                    if item.status == 'Needs attention':
                        item.status = 'Pending'
                        item.lecture_submission_state = 'not_started'
                        item.next_retry_at = None
                self.batch_store.save(resume_manifest)
        # Worker count is operational configuration, not generation content.  An
        # explicit Run choice updates a saved queue before it is started, so a
        # queue created while the default was one tab can be resumed in the
        # experimental two-tab mode without changing its prompt or protocol.
        if resume_manifest and resume_manifest.max_workers != max_workers:
            resume_manifest.max_workers = max_workers
            self.batch_store.save(resume_manifest)
        self.worker = GoogleAIStudioWorker(
            prompt_path=self.prompt_path.text().strip(),
            reference_path=self.reference_path.text().strip(),
            lecture_folder=self.lecture_path.text().strip(),
            output_folder=self.output_folder.text().strip(),
            model=self.model.currentData(),
            thinking=self.thinking.currentData(),
            brave_executable=self.settings.get('ai_studio_brave_executable'),
            template=template,
            template_entry=template_entry,
            history=self.history,
            manifest=resume_manifest,
            batch_store=self.batch_store,
            max_workers=max_workers,
        )
        self.worker.signals.event.connect(self._event)
        self.worker.signals.job_updated.connect(self._job_updated)
        self.worker.signals.completed.connect(self._completed)
        self.worker.signals.failed.connect(self._failed)
        self.worker.signals.cancelled.connect(self._cancelled)
        self._set_busy(True)
        self._event('[starting]')
        self.pool.start(self.worker)

    def toggle_run(self):
        if self.worker is None:
            self.start_run()
        else:
            self.stop_run()

    def stop_run(self):
        if self.worker is not None:
            self.worker.cancel()
            self.run_button.setEnabled(False)
            self.run_button.setText('Stopping safely…')
            self.pause_button.setEnabled(False)
            self.status.setText('Stopping after the current safe point. Completed exams will be kept.')

    def toggle_pause(self):
        if self.worker is None:
            return
        if self.worker.paused.is_set():
            self.worker.paused.clear()
            self.pause_button.setText('Pause after current')
            self._event('[pause_cancelled]')
        else:
            self.worker.paused.set()
            self.pause_button.setText('Resume batch')
            self.status.setText('Pausing after the current lecture…')
            self._event('[pausing_after_current]')

    def discard_saved_batch(self):
        if self.worker is not None:
            self.status.setText('Stop the active queue before discarding its saved progress.')
            return
        if self.resume_manifest is None:
            return
        answer = QMessageBox.question(
            self, 'Discard saved batch?',
            'Remove this batch and its saved progress from MCQ Maker? Generated HTML exams and source PDFs will be kept.',
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        try:
            self.batch_store.discard(self.resume_manifest.batch_id)
        except RuntimeError as exc:
            self.status.setText(str(exc))
            return
        self.status.setText('Saved batch discarded. Generated HTML exams and source PDFs were kept.')
        self.resume_manifest = None
        self._refresh_resume_manifest()

    def _event(self, event):
        message = self._friendly_event(event)
        step, detail = self._event_parts(message)
        item = QTreeWidgetItem([step, detail])
        item.setToolTip(1, detail)
        self.log.addTopLevelItem(item)
        self.log.scrollToBottom()
        self.status.setText(message)
        self.status.setProperty('feedback', 'info')
        self._refresh_status_style()

    def _job_updated(self, lecture, status, detail, attempts):
        row = next((self.job_table.topLevelItem(i) for i in range(self.job_table.topLevelItemCount())
                    if self.job_table.topLevelItem(i).text(0) == lecture), None)
        if row is None:
            row = QTreeWidgetItem([lecture, status, attempts, detail])
            self.job_table.addTopLevelItem(row)
        else:
            row.setText(1, status)
            row.setText(2, attempts)
            row.setText(3, detail)
        row.setToolTip(3, detail)

    def _render_manifest_jobs(self, manifest):
        self.job_table.clear()
        for item in manifest.lectures:
            detail = item.last_error or (Path(item.output_path).name if item.output_path else '')
            if item.next_retry_at:
                detail = f'{detail} · Next retry: {item.next_retry_at}'.strip(' ·')
            self._job_updated(item.file_name, item.status, detail, str(item.attempt_count))

    def _restore_manifest_fields(self, manifest):
        # Old manifests retain their original paths. If an old checkout-only
        # default has since disappeared, display the new seeded default without
        # mutating the historical manifest.
        resolver = self.default_resources
        prompt = (resolver.resolve_existing_or_default(manifest.prompt_path, 'prompt')
                  if resolver is not None else self._default_resource_path('prompt'))
        reference = (resolver.resolve_existing_or_default(manifest.reference_path, 'reference')
                     if resolver is not None else self._default_resource_path('reference'))
        self.prompt_path.setText(str(prompt))
        self.reference_path.setText(str(reference))
        self.lecture_path.setText(manifest.folder)
        self.output_folder.setText(manifest.output_folder)
        model_index = self.model.findData(manifest.model)
        if model_index < 0:
            self.model.addItem(manifest.model, manifest.model)
            model_index = self.model.findData(manifest.model)
        self.model.setCurrentIndex(model_index)
        self.thinking.setCurrentIndex(max(0, self.thinking.findData(manifest.thinking)))
        self.parallel_tabs.setCurrentIndex(max(0, self.parallel_tabs.findData(manifest.max_workers)))
        if manifest.template_id:
            self.template.setCurrentIndex(max(0, self.template.findData(manifest.template_id)))

    def _show_saved_batch_summary(self, manifest):
        statuses = {item.status for item in manifest.lectures if item.status != 'Completed'}
        if statuses & {'Interrupted', 'Needs attention'}:
            state = 'Batch needs attention'
            detail = 'Review the affected lecture status before starting the saved batch.'
        elif manifest.control_state == 'paused' or 'Paused' in statuses:
            state = 'Batch paused'
            detail = 'Resume batch when you are ready to continue the remaining lectures.'
        elif manifest.control_state == 'stopped' or 'Cancelled' in statuses:
            state = 'Batch stopped safely'
            detail = 'Resume batch to continue eligible lectures. Completed exams are preserved.'
        elif statuses:
            state = 'Saved batch ready'
            detail = 'Review progress, then continue the saved batch or discard its saved progress.'
        else:
            state = 'Saved batch complete'
            detail = 'All lectures in this saved batch are complete.'
        self.batch_state.setText(state)
        self.status.setText(detail)

    def _show_empty_batch_summary(self):
        self.batch_state.setText('No saved batch')
        self.status.setText('Choose a lecture folder to start a new AI Studio automation batch.')

    @staticmethod
    def _event_parts(message):
        if '. ' in message:
            step, detail = message.split('. ', 1)
            return step, detail
        if ':' in message:
            step, detail = message.split(':', 1)
            return step, detail.strip()
        return message.rstrip('….'), ''

    @staticmethod
    def _friendly_event(event):
        text = str(event).strip('[]')
        if text.startswith('completed]:'):
            return f'Lecture Completed; HTML saved and verified: {text.split(":", 1)[1].strip()}'
        if text == 'preparing_playground':
            return 'Opening Google AI Studio…'
        if text == 'uploading_reference':
            return 'Adding the reference file…'
        if text.startswith('reference_ready:'):
            return 'Reference file is ready. Sending calibration…'
        if text == 'sending_calibration':
            return 'Sending calibration…'
        if text == 'calibration_running':
            return 'Waiting for calibration completion…'
        if text == 'calibration_complete':
            return 'Calibration complete.'
        if text == 'uploading_lecture':
            return 'Adding the lecture PDF…'
        if text.startswith('lecture_ready:'):
            return 'Lecture PDF is ready. Sending generation…'
        if text == 'sending_lecture':
            return 'Sending lecture generation…'
        if text == 'lecture_running':
            return 'Waiting for quiz generation…'
        if text == 'validating_json':
            return 'Checking the quiz JSON…'
        if text == 'saving_exam':
            return 'Saving the HTML exam…'
        if text == 'complete':
            return 'Exam complete.'
        if text.startswith('manual_rerun_required:'):
            reason = text.split(':', 2)[-1]
            return f'AI Studio needs a manual Rerun. {reason}'
        if text == 'history_not_saved':
            return 'The exam was saved, but History could not be updated.'
        return text.replace('_', ' ').capitalize()

    def _completed(self, output, question_count):
        manifest = self.worker.manifest if self.worker is not None else None
        complete_count = sum(item.status == 'Completed' for item in manifest.lectures) if manifest else 0
        total_count = len(manifest.lectures) if manifest else 0
        self._set_busy(False)
        self._refresh_resume_manifest()
        self.status.setText(f'Exam saved: {Path(output).name} · {question_count} questions')
        if total_count and complete_count < total_count:
            self.status.setText(
                f'Queue saved with progress: {complete_count} of {total_count} complete. '
                'Review lecture statuses and resume when ready.'
            )
        elif not question_count:
            self.status.setText('Queue complete. Existing completed exams were preserved.')
        self.status.setProperty('feedback', 'info')
        self._refresh_status_style()
        self.history_changed.emit()
        if question_count:
            self.notification_requested.emit('ai_studio_complete', 'AI Studio exam saved', Path(output).name, output)
        if question_count and self.settings.get('open_after_manual_generation'):
            QDesktopServices.openUrl(QUrl.fromLocalFile(output))

    def _failed(self, message):
        self._set_busy(False)
        self._refresh_resume_manifest()
        item = QTreeWidgetItem(['Stopped', message])
        item.setToolTip(1, message)
        self.log.addTopLevelItem(item)
        self.log.scrollToBottom()
        self.status.setText(message)
        self.status.setProperty('feedback', 'error')
        self._refresh_status_style()

    def _cancelled(self):
        self._set_busy(False)
        self._refresh_resume_manifest()
        if self.resume_manifest is None:
            self.status.setText('Queue complete. All generated exams were kept.')
        else:
            self.status.setText('Queue stopped safely. Completed exams were kept; Resume is required to continue.')
        self.status.setProperty('feedback', 'info')
        self._refresh_status_style()
        self._event('[cancelled]')

    def _refresh_resume_manifest(self):
        available = self.batch_store.unfinished()
        self.resume_manifest = available[0] if available else None
        self.discard_button.setVisible(bool(self.resume_manifest))
        self.run_button.setText(
            self._saved_batch_action(self.resume_manifest) if self.resume_manifest else 'Start batch'
        )
        if self.resume_manifest:
            self._render_manifest_jobs(self.resume_manifest)
            self._show_saved_batch_summary(self.resume_manifest)
        else:
            self._show_empty_batch_summary()

    @staticmethod
    def _saved_batch_action(manifest):
        statuses = {item.status for item in manifest.lectures if item.status != 'Completed'}
        if manifest.control_state in {'paused', 'stopped'} and statuses & {
                'Paused', 'Cancelled', 'Pending', 'Waiting to retry', 'Failed temporarily',
        }:
            return 'Resume saved batch'
        if statuses & {'Interrupted', 'Needs attention'}:
            return 'Review saved batch'
        return 'Continue batch'

    def _set_busy(self, busy):
        self.run_button.setEnabled(True)
        self.run_button.setText('Stop safely' if busy else 'Start batch')
        self.pause_button.setEnabled(busy)
        self.discard_button.setEnabled(not busy)
        if busy:
            self.pause_button.setText('Pause after current')
            self.batch_state.setText('Batch running')
        for control in (self.prompt_path, self.reference_path, self.lecture_path,
                        self.output_folder, self.model, self.thinking, self.parallel_tabs, self.template):
            control.setEnabled(not busy)
        if not busy:
            self.worker = None
            self.pause_button.setText('Pause after current')
            self.pause_button.setEnabled(False)

    def _ready(self):
        self._set_busy(False)
        self.status.setProperty('feedback', 'info')
        self._refresh_status_style()

    def _refresh_status_style(self):
        if self.status.property('feedback') == 'error':
            self.status.setContentsMargins(16, 8, 12, 8)
        else:
            self.status.setContentsMargins(0, 0, 0, 0)
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
