"""Searchable, responsive history of generation metadata."""
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QStandardPaths, Qt, QSize, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QLineEdit, QMenu,
                               QHeaderView, QMessageBox, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from .components import button, label, panel
from .history import HistoryError
from .localization import tr


class HistoryPage(QWidget):
    def __init__(self, history=None, parent=None):
        super().__init__(parent)
        self.history = history
        self.entries = []
        self.compact = False
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(200)
        self.search_timer.timeout.connect(self.reload)
        self.setObjectName('page')

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(20)
        header = QHBoxLayout()
        self.title_label = label(tr('page.history'), 'title')
        header.addWidget(self.title_label, 1)
        self.export_button = button(tr('history.export'))
        self.export_button.clicked.connect(self.export_csv)
        header.addWidget(self.export_button)
        layout.addLayout(header)
        layout.addWidget(label('Find and reopen your saved exams.'))

        self.search = QLineEdit()
        self.search.setAccessibleName('Search history')
        self.search.setPlaceholderText('Search by exam, template, or source file')
        self.search.setToolTip('Search History (Ctrl+F)')
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda: self.search_timer.start())
        self.search.setEnabled(history is not None)
        layout.addWidget(self.search)

        actions = QHBoxLayout()
        self.open_button = button(tr('history.open_exam'))
        self.open_button.clicked.connect(self.open_selected)
        actions.addWidget(self.open_button)
        self.show_button = button(tr('common.reveal_folder'))
        self.show_button.clicked.connect(self.show_selected)
        actions.addWidget(self.show_button)
        self.remove_button = button(tr('history.remove'))
        self.remove_button.clicked.connect(self.remove_selected)
        actions.addWidget(self.remove_button)
        actions.addStretch()
        layout.addLayout(actions)

        box, content = panel()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(0)
        self.table = QTreeWidget()
        self.table.setAccessibleName('Exam history')
        self.table.setColumnCount(6)
        self.table.setHeaderLabels(('Exam', 'Created', 'Template', 'Questions', 'Source', 'Status'))
        self.table.setRootIsDecorated(False)
        self.table.setUniformRowHeights(True)
        self.table.setWordWrap(True)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.table.setSortingEnabled(False)
        self.table.setSelectionMode(QTreeWidget.SingleSelection)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.context_menu)
        self.table.currentItemChanged.connect(self.selection_changed)
        self.table.itemActivated.connect(lambda *_: self.open_selected())
        header_view = self.table.header()
        header_view.setStretchLastSection(False)
        header_view.setSectionResizeMode(0, QHeaderView.Stretch)
        for column, width in enumerate((0, 140, 144, 82, 104, 92)):
            if column:
                header_view.setSectionResizeMode(column, QHeaderView.Fixed)
                self.table.setColumnWidth(column, width)
        content.addWidget(self.table, 1)

        self.empty = QWidget()
        empty_layout = QVBoxLayout(self.empty)
        empty_layout.setContentsMargins(20, 20, 20, 20)
        empty_layout.addStretch()
        self.empty_title = label('No generated exams yet.', 'heading')
        self.empty_title.setAlignment(Qt.AlignCenter)
        empty_layout.addWidget(self.empty_title)
        self.empty_text = label('Generated exams will appear here after validation and saving.')
        self.empty_text.setAlignment(Qt.AlignCenter)
        empty_layout.addWidget(self.empty_text)
        self.create_button = button('Create exam', True)
        empty_layout.addWidget(self.create_button, alignment=Qt.AlignHCenter)
        empty_layout.addStretch()
        content.addWidget(self.empty, 1)
        layout.addWidget(box, 1)

        self.status = label('', 'muted')
        layout.addWidget(self.status)
        self.reload()

    def reload(self):
        if self.history is None:
            self._show_error('History is unavailable.')
            return
        selected_id = self.selected_entry().id if self.selected_entry() else None
        try:
            self.entries = self.history.list_entries(self.search.text())
        except Exception:
            self._show_error('MCQ Maker could not read the history database. Your exam files are unchanged.')
            return
        self.table.blockSignals(True)
        self.table.clear()
        selected_item = None
        for entry in self.entries:
            item = QTreeWidgetItem()
            item.setData(0, Qt.UserRole, entry)
            item.setSizeHint(0, QSize(0, 64))
            item.setToolTip(0, entry.output_path or entry.source_path)
            self.table.addTopLevelItem(item)
            self._render_item(item, entry)
            if entry.id == selected_id:
                selected_item = item
        if selected_item:
            self.table.setCurrentItem(selected_item)
        self.table.blockSignals(False)
        has_rows = bool(self.entries)
        self.table.setVisible(has_rows)
        self.empty.setVisible(not has_rows)
        if not has_rows and self.search.text().strip():
            self.empty_title.setText('No history matches your search.')
            self.empty_text.setText('Try a title, filename, template, or source filename.')
            self.create_button.hide()
        else:
            self.empty_title.setText('No generated exams yet.')
            self.empty_text.setText('Generated exams will appear here after validation and saving.')
            self.create_button.show()
        self.export_button.setEnabled(bool(self.entries))
        self.status.setText(f'{len(self.entries)} entr{"y" if len(self.entries) == 1 else "ies"}')
        self.status.setProperty('feedback', 'info')
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        self.selection_changed()

    def retranslate(self):
        self.title_label.setText(tr('page.history'))
        self.export_button.setText(tr('history.export'))
        self.open_button.setText(tr('history.open_exam'))
        self.remove_button.setText(tr('history.remove'))
        self.selection_changed()

    @staticmethod
    def local_created(value, now=None):
        try:
            moment = datetime.fromisoformat(value).astimezone()
            current = now.astimezone() if now else datetime.now().astimezone()
            time_text = moment.strftime('%I:%M %p').lstrip('0')
            if moment.date() == current.date():
                return f'Today, {time_text}'
            if (current.date() - moment.date()).days == 1:
                return f'Yesterday, {time_text}'
            return f'{moment.strftime("%b %d, %Y").replace(" 0", " ")} · {time_text}'
        except (ValueError, TypeError):
            return 'Unknown time'

    @staticmethod
    def full_created(value):
        try:
            moment = datetime.fromisoformat(value).astimezone()
            return moment.strftime('%A, %B %d, %Y at %I:%M %p').replace(' 0', ' ')
        except (ValueError, TypeError):
            return 'The recorded creation time is unavailable.'

    @staticmethod
    def source_name(mode):
        return {'paste': 'Paste', 'folder': 'Folder scan', 'clipboard': 'Clipboard'}.get(mode, mode.title())

    def _render_item(self, item, entry):
        item.setSizeHint(0, QSize(0, 80 if self.compact else 64))
        output = Path(entry.output_path) if entry.output_path else None
        exists = bool(output and output.is_file())
        status = 'Failed' if entry.outcome == 'failed' else 'Saved' if exists else 'File missing'
        filename = entry.output_filename or (Path(entry.source_path).name if entry.source_path else 'Generation attempt')
        if self.compact:
            metadata = f'{self.local_created(entry.created_utc).replace(chr(10), " · ")} · {entry.template_name} · {entry.question_count} questions · {self.source_name(entry.source_mode)} · {status}'
            item.setText(0, f'{entry.title or filename}\n{metadata}')
        else:
            item.setText(0, f'{entry.title or "Untitled exam"}\n{filename}')
            item.setText(1, self.local_created(entry.created_utc))
            item.setToolTip(1, self.full_created(entry.created_utc))
            item.setText(2, entry.template_name)
            item.setText(3, str(entry.question_count) if entry.question_count else '—')
            item.setText(4, self.source_name(entry.source_mode))
            item.setText(5, status)
            if status == 'File missing':
                item.setToolTip(5, 'This history record exists, but the saved HTML file is no longer at its original path.')
            elif status == 'Saved':
                item.setToolTip(5, 'The saved HTML exam is available.')

    def selected_entry(self):
        item = self.table.currentItem()
        return item.data(0, Qt.UserRole) if item else None

    def selection_changed(self, *_):
        entry = self.selected_entry()
        output = Path(entry.output_path) if entry and entry.output_path else None
        exists = bool(output and output.is_file())
        self.open_button.setEnabled(exists)
        self.open_button.setToolTip('Open the saved HTML exam.' if exists else (
            'This history record exists, but the saved HTML file is no longer at its original path.' if entry else ''
        ))
        self.show_button.setEnabled(bool(output and (exists or entry.outcome == 'success')))
        self.show_button.setText(tr('common.reveal_folder') if exists or not entry else 'Locate output file')
        self.show_button.setToolTip('Open the folder containing the saved HTML exam.' if exists else (
            'Choose the saved HTML exam at its new location to update this history record.' if entry else ''
        ))
        self.remove_button.setEnabled(entry is not None)
        if entry and entry.outcome == 'failed':
            self.status.setText(entry.error_summary or 'This generation did not finish.')
        elif entry and not exists:
            self.status.setText('This history record exists, but the saved HTML file is no longer at its original path.')

    def open_selected(self):
        entry = self.selected_entry()
        if not entry or not entry.output_path:
            return
        path = Path(entry.output_path)
        if not path.is_file():
            self.locate_selected()
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            self._show_error('Windows could not open this exam in your browser.')

    def show_selected(self):
        entry = self.selected_entry()
        if not entry:
            return
        path = Path(entry.output_path) if entry.output_path else None
        if not path or not path.is_file():
            self.locate_selected()
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent))):
            self._show_error('Windows could not open the exam folder.')

    def locate_selected(self):
        entry = self.selected_entry()
        if not entry or self.history is None:
            return
        filename, _ = QFileDialog.getOpenFileName(self, 'Locate generated exam', '', 'HTML exams (*.html *.htm)')
        if filename:
            try:
                self.history.relocate(entry.id, Path(filename))
                self.reload()
            except HistoryError as exc:
                self._show_error(str(exc))

    def remove_selected(self):
        entry = self.selected_entry()
        if not entry or self.history is None:
            return
        answer = QMessageBox.question(
            self, 'Remove history entry',
            'Remove this entry from History?\n\nThe generated exam file will not be deleted.',
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel,
        )
        if answer == QMessageBox.Yes:
            try:
                self.history.remove(entry.id)
                self.reload()
            except Exception:
                self._show_error('MCQ Maker could not remove this history entry.')

    def export_csv(self):
        if not self.entries or self.history is None:
            return
        documents = Path(QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation))
        filename, _ = QFileDialog.getSaveFileName(self, 'Export history', str(documents/'MCQ Maker History.csv'), 'CSV files (*.csv)')
        if filename:
            try:
                self.history.export_csv(Path(filename), self.entries)
                self.status.setText(f'Exported {len(self.entries)} history entries.')
            except Exception:
                self._show_error('Windows could not save the history export. Choose another location and try again.')

    def context_menu(self, point):
        item = self.table.itemAt(point)
        if item is None:
            return
        self.table.setCurrentItem(item)
        entry = self.selected_entry()
        output = Path(entry.output_path) if entry and entry.output_path else None
        menu = QMenu(self)
        open_action = menu.addAction('Open exam')
        open_action.setEnabled(bool(output and output.is_file()))
        open_action.triggered.connect(self.open_selected)
        folder_action = menu.addAction('Reveal in folder' if output and output.is_file() else 'Locate output file')
        folder_action.setEnabled(bool(output and (output.is_file() or entry.outcome == 'success')))
        folder_action.triggered.connect(self.show_selected)
        menu.addSeparator()
        menu.addAction('Remove from history').triggered.connect(self.remove_selected)
        menu.exec(self.table.mapToGlobal(point))

    def set_compact(self, compact):
        if self.compact == compact:
            return
        self.compact = compact
        self.table.setHeaderHidden(compact)
        for column in range(1, 6):
            self.table.setColumnHidden(column, compact)
        for index in range(self.table.topLevelItemCount()):
            item = self.table.topLevelItem(index)
            self._render_item(item, item.data(0, Qt.UserRole))

    def _show_error(self, text):
        self.status.setText(text)
        self.status.setProperty('feedback', 'error')
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
