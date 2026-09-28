"""Folder scan workflow and actionable processing report."""
from pathlib import Path
from threading import Event

from PySide6.QtCore import QStandardPaths, Qt, QSize, QThreadPool, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QApplication, QFileDialog, QHBoxLayout,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .components import AnimatedProgressBar, button, label, panel
from .folder_scan import FolderScanWorker, ScanResult


class FolderScanPage(QWidget):
    history_changed = Signal()
    notification_requested = Signal(str, str, str, object)

    def __init__(self, repository=None, settings=None, history=None, parent=None):
        super().__init__(parent)
        self.repository = repository
        self.history = history
        self.settings = settings or {}
        self.folder = None
        self.template_entries = {}
        self.default_template_id = None
        self.results = []
        self.last_folder = None
        self.last_output_folder = None
        self.last_template_name = ''
        self.active_template_entry = None
        self.history_warnings = 0
        self.busy = False
        self.worker = None
        self.cancellation = None
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.setObjectName('page')

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(20)
        header = QHBoxLayout()
        header.addWidget(label('Folder scan', 'title'), 1)
        self.choose_button = button('Choose folder', True)
        self.choose_button.clicked.connect(self.choose_folder)
        header.addWidget(self.choose_button)
        layout.addLayout(header)
        layout.addWidget(label('Create exams from a folder of .json, .txt, and .md files.'))

        source_panel, source_layout = panel()
        source_layout.setSpacing(8)
        source_layout.addWidget(label('Selected folder', 'field'))
        self.folder_label = label('No folder selected.', 'muted')
        self.folder_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        source_layout.addWidget(self.folder_label)
        self.scan_detail = label('Choose a folder. MCQ Maker will use your default template.', 'muted')
        source_layout.addWidget(self.scan_detail)
        layout.addWidget(source_panel)

        report_panel, report_layout = panel()
        report_header = QHBoxLayout()
        report_header.addWidget(label('Processing report', 'heading'), 1)
        self.summary = label('0 created · 0 skipped · 0 failed', 'muted')
        report_header.addWidget(self.summary)
        report_layout.addLayout(report_header)
        self.progress = AnimatedProgressBar()
        self.progress.setAccessibleName('Folder scan progress')
        self.progress.setTextVisible(False)
        self.progress.hide()
        report_layout.addWidget(self.progress)
        self.current = label('Choose a folder to begin.', 'muted')
        report_layout.addWidget(self.current)
        self.report = QTreeWidget()
        self.report.setAccessibleName('Processing results')
        self.report.setHeaderHidden(True)
        self.report.setRootIsDecorated(True)
        self.report.setIndentation(18)
        self.report.setAnimated(False)
        self.report.setWordWrap(True)
        self.report.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.report.hide()
        report_layout.addWidget(self.report, 1)
        self.empty = QWidget()
        empty_layout = QVBoxLayout(self.empty)
        empty_layout.setContentsMargins(20, 20, 20, 20)
        empty_layout.addStretch()
        empty_heading = label('No files processed yet', 'heading')
        empty_heading.setAlignment(Qt.AlignCenter)
        empty_layout.addWidget(empty_heading)
        empty_message = label('Choose a folder to see each file’s result here.')
        empty_message.setAlignment(Qt.AlignCenter)
        empty_layout.addWidget(empty_message)
        empty_layout.addStretch()
        report_layout.addWidget(self.empty, 1)
        layout.addWidget(report_panel, 1)

        footer = QHBoxLayout()
        self.open_output_button = button('Open output folder', True)
        self.open_output_button.clicked.connect(self.open_output_folder)
        footer.addWidget(self.open_output_button)
        self.copy_button = button('Copy report')
        self.copy_button.clicked.connect(self.copy_report)
        footer.addWidget(self.copy_button)
        footer.addStretch()
        self.scan_button = button('Start scan', primary=True)
        self.scan_button.clicked.connect(self.toggle_scan)
        footer.addWidget(self.scan_button)
        layout.addLayout(footer)
        self._update_ready_state()

    @property
    def output_folder(self):
        configured = self.settings.get('output_folder')
        if configured:
            return Path(configured)
        return Path(QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)) / 'MCQ Maker Exams'

    def apply_settings(self, settings):
        self.settings = dict(settings)
        self._update_ready_state()

    def set_template_snapshot(self, snapshot):
        self.template_entries = {entry['id']: entry for entry in snapshot['templates']}
        self.default_template_id = snapshot.get('default_id')
        self._update_ready_state()

    def selected_template(self):
        entry = self.template_entries.get(self.default_template_id)
        return entry if entry and entry.get('valid') else None

    def choose_folder(self):
        start = str(self.folder or Path(QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)))
        selected = QFileDialog.getExistingDirectory(self, 'Choose folder to scan', start)
        if selected:
            self.set_folder(Path(selected))

    def set_folder(self, folder):
        self.folder = Path(folder).resolve()
        self.folder_label.setText(str(self.folder))
        self.results = []
        self.last_folder = None
        self.last_output_folder = None
        self.last_template_name = ''
        self.report.clear()
        self.report.hide()
        self.empty.show()
        self.summary.setText('0 created · 0 skipped · 0 failed')
        self.current.setText('Ready to scan.')
        self.current.setProperty('feedback', 'info')
        self._refresh_style(self.current)
        self.copy_button.setEnabled(False)
        self._update_ready_state()

    def _update_ready_state(self):
        if self.busy:
            return
        template = self.selected_template()
        recursive = bool(self.settings.get('include_subfolders', False))
        details = []
        if template:
            details.append(f"Template: {template['display_name']}")
        else:
            details.append('Choose a valid default template in Settings')
        details.append('Subfolders included' if recursive else 'Top-level files only')
        details.append(f'Output: {self.output_folder}')
        self.scan_detail.setText(' · '.join(details))
        ready = bool(self.folder and self.folder.is_dir() and template and self.repository)
        self.scan_button.setEnabled(ready)
        self.scan_button.setText('Start scan')
        if not ready:
            self.scan_button.setToolTip('Choose an available folder and a valid default template first.')
        else:
            self.scan_button.setToolTip('')

    def toggle_scan(self):
        if self.busy:
            self.cancel_scan()
        else:
            self.start_scan()

    def start_scan(self):
        template_entry = self.selected_template()
        if not self.folder or not self.folder.is_dir():
            self._message('Choose an available folder before starting the scan.', error=True)
            return
        if not template_entry or self.repository is None:
            self._message('Choose a valid default template in Settings before starting the scan.', error=True)
            return
        try:
            template = self.repository.read_template(template_entry['id'])
        except Exception:
            self._message('The default template could not be read. Recheck it in Templates and try again.', error=True)
            return

        self.results = []
        self.last_folder = self.folder
        self.last_output_folder = self.output_folder
        self.last_template_name = template_entry['display_name']
        self.active_template_entry = dict(template_entry)
        self.history_warnings = 0
        self.report.clear()
        self.report.hide()
        self.empty.show()
        self.summary.setText('0 created · 0 skipped · 0 failed')
        self._message('Finding supported files…')
        self.progress.setRange(0, 0)
        self.progress.show()
        self.busy = True
        self.choose_button.setEnabled(False)
        self.scan_button.setEnabled(True)
        self.scan_button.setText('Cancel')
        self.scan_button.setToolTip('Stop after the current file finishes.')
        self.copy_button.setEnabled(False)
        self.cancellation = Event()
        self.worker = FolderScanWorker(
            self.folder,
            bool(self.settings.get('include_subfolders', False)),
            template,
            template_entry,
            self.last_output_folder,
            self.cancellation,
        )
        self.worker.signals.progress.connect(self._file_finished)
        self.worker.signals.finished.connect(self._scan_finished)
        self.pool.start(self.worker)

    def cancel_scan(self):
        if self.busy and self.worker is not None:
            self.worker.cancel()
            self.scan_button.setEnabled(False)
            self.scan_button.setText('Cancelling…')
            self.current.setText('Finishing the current file, then stopping…')

    def _file_finished(self, result, completed, total):
        if self.progress.maximum() == 0:
            self.progress.setRange(0, max(1, total))
        self.progress.set_reported_value(completed)
        self.results.append(result)
        self._record_history(result)
        self._add_result(result)
        self._set_summary()
        self.current.setText(f'{completed} of {total} · {result.relative_path}')

    def _scan_finished(self, results, cancelled, error):
        self.busy = False
        self.choose_button.setEnabled(True)
        self.progress.hide()
        if error:
            self._message(error, error=True)
            self.notification_requested.emit(
                'background_error', 'Folder scan could not finish', error, 'folder_report')
        elif not results:
            self.current.setText('No supported .json, .txt, or .md files were found in this folder.')
            self.current.setProperty('feedback', 'warning')
            self._refresh_style(self.current)
        elif cancelled:
            self.current.setText('Scan cancelled. Exams created before cancellation were kept.')
            self.current.setProperty('feedback', 'warning')
            self._refresh_style(self.current)
        else:
            created, skipped, failed = self.counts()
            history_note = f' History could not record {self.history_warnings} item(s).' if self.history_warnings else ''
            self.current.setText(f'Folder scan complete: {created} created, {skipped} skipped, {failed} failed.{history_note}')
            self.current.setProperty('feedback', 'success' if failed == 0 else 'warning')
            self._refresh_style(self.current)
            self.notification_requested.emit(
                'batch_complete', 'Folder scan complete',
                f'{created} created · {skipped} skipped · {failed} failed', 'folder_report')
        self.copy_button.setEnabled(bool(self.results))
        self._update_ready_state()
        self.worker = None
        self.cancellation = None

    def _record_history(self, result):
        if self.history is None or self.active_template_entry is None or result.status == 'skipped':
            return
        try:
            if result.status == 'created':
                self.history.record_success(
                    title=result.title,
                    question_count=result.question_count,
                    input_hash=result.input_hash,
                    output_path=result.output_path,
                    template_entry=self.active_template_entry,
                    source_mode='folder',
                    source_path=result.source_path,
                )
            else:
                self.history.record_failure(
                    template_entry=self.active_template_entry,
                    source_mode='folder',
                    source_path=result.source_path,
                    error_summary=result.reason,
                )
            self.history_changed.emit()
        except Exception:
            self.history_warnings += 1

    def _add_result(self, result: ScanResult):
        self.empty.hide()
        self.report.show()
        symbols = {'created': '✓ Created', 'failed': '× Failed', 'skipped': '– Skipped'}
        summary = result.output_path.name if result.output_path else result.reason.splitlines()[0]
        item = QTreeWidgetItem([f'{symbols[result.status]} — {result.relative_path}\n{summary}'])
        item.setData(0, Qt.UserRole, result)
        item.setSizeHint(0, QSize(0, 64))
        item.setToolTip(0, result.relative_path)
        detail = QTreeWidgetItem(item)
        detail.setFlags(Qt.NoItemFlags)
        lines = []
        if result.status == 'created':
            lines.extend((f'Title: {result.title}', f'Questions: {result.question_count}',
                          f'Saved to: {result.output_path}'))
            lines.extend(f'Warning: {warning}' for warning in result.warnings)
        else:
            lines.extend(result.reason.splitlines())
        detail.setText(0, '\n'.join(lines))
        detail.setSizeHint(0, QSize(0, max(64, 28 + len(lines) * 24)))
        self.report.addTopLevelItem(item)
        item.setExpanded(result.status == 'failed')

    def counts(self):
        return tuple(sum(result.status == status for result in self.results)
                     for status in ('created', 'skipped', 'failed'))

    def _set_summary(self):
        created, skipped, failed = self.counts()
        self.summary.setText(f'{created} created · {skipped} skipped · {failed} failed')

    def _message(self, text, error=False):
        self.current.setText(text)
        self.current.setProperty('feedback', 'error' if error else 'info')
        self._refresh_style(self.current)

    @staticmethod
    def _refresh_style(widget):
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    def report_text(self):
        created, skipped, failed = self.counts()
        lines = [
            'MCQ Maker folder scan',
            f'Folder: {self.last_folder or self.folder}',
            f'Template: {self.last_template_name or "Unavailable"}',
            f'Output: {self.last_output_folder or self.output_folder}',
            f'Result: {created} created, {skipped} skipped, {failed} failed',
            '',
        ]
        for result in self.results:
            detail = str(result.output_path) if result.output_path else result.reason.replace('\n', ' ')
            lines.append(f'[{result.status.upper()}] {result.relative_path} — {detail}')
        return '\n'.join(lines)

    def copy_report(self):
        if self.results:
            QApplication.clipboard().setText(self.report_text())
            self._message('Processing report copied to the clipboard.')

    def open_output_folder(self):
        target = self.last_output_folder or self.output_folder
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError:
            self._message('Windows could not create the output folder. Choose another folder in Settings.', error=True)
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(target))):
            self._message('Windows could not open the output folder.', error=True)
