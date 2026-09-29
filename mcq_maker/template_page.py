"""Template library UI. File work stays in a serialized background queue."""
from datetime import datetime
from pathlib import Path
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Qt, QSize, QUrl
from PySide6.QtGui import QDesktopServices, QIcon, QPixmap, QPainter, QColor, QPolygon
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QTreeWidget, QTreeWidgetItem,
                              QFileDialog, QInputDialog, QMessageBox, QMenu)
from .template_validation import TemplateError
from .components import button, label
from .localization import tr

class JobSignals(QObject):
    finished = Signal(object, str)

class TemplateJob(QRunnable):
    def __init__(self, operation):
        super().__init__()
        self.operation = operation
        self.signals = JobSignals()

    def run(self):
        try:
            self.signals.finished.emit(self.operation(), '')
        except TemplateError as exc:
            self.signals.finished.emit(None, str(exc))
        except OSError:
            self.signals.finished.emit(None, 'Windows could not read or save the template files. Check that the file is available and the folder is writable, then try again.')
        except Exception:
            self.signals.finished.emit(None, 'The template library could not be loaded. Your original files have not been changed. Close and reopen MCQ Maker, then try again.')

def warning_icon():
    pixmap = QPixmap(18, 18)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor('#C23B48'))
    painter.setPen(Qt.NoPen)
    painter.drawPolygon(QPolygon([QPoint(9, 2), QPoint(17, 16), QPoint(1, 16)]))
    painter.setPen(QColor('#FFFFFF'))
    painter.drawText(pixmap.rect(), Qt.AlignCenter, '!')
    painter.end()
    return QIcon(pixmap)

class TemplatePage(QWidget):
    library_changed = Signal(object)

    def __init__(self, repository, parent=None):
        super().__init__(parent)
        self.make_label = label
        self.make_button = button
        self.repo = repository
        self.entries = {}
        self.busy = False
        self.job = None
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.setObjectName('page')
        self.setAcceptDrops(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        top = QHBoxLayout()
        self.title_label = label(tr('page.templates'), 'title')
        top.addWidget(self.title_label, 1)
        self.refresh_button = button(tr('common.refresh'), True)
        self.refresh_button.clicked.connect(self.reload)
        top.addWidget(self.refresh_button)
        self.import_button = button(tr('common.import'), True, True)
        self.import_button.clicked.connect(self.import_template)
        top.addWidget(self.import_button)
        layout.addLayout(top)
        layout.addWidget(label('Your own copies, kept together and ready to use.'))
        self.status = label('Loading your template library…')
        self.status.setAccessibleName('Template status')
        layout.addWidget(self.status)
        self.tree = QTreeWidget()
        self.tree.setAccessibleName('Template library')
        self.tree.setHeaderHidden(True)
        self.tree.setRootIsDecorated(True)
        self.tree.setIndentation(18)
        self.tree.setAnimated(False)
        self.tree.currentItemChanged.connect(self.selection_changed)
        self.tree.itemClicked.connect(self.toggle_details)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.context_menu)
        layout.addWidget(self.tree, 1)
        self.empty = label('No templates in your library. Import an HTML file to begin.', 'heading')
        self.empty.hide()
        layout.addWidget(self.empty)
        layout.addWidget(label('Templates run JavaScript when previewed. Only import HTML you trust.', 'muted'))
        self.selection_changed()

    def selected_id(self):
        item = self.tree.currentItem()
        if item is not None and item.parent() is not None:
            item = item.parent()
        return item.data(0, Qt.UserRole) if item else None

    def retranslate(self):
        self.title_label.setText(tr('page.templates'))
        self.refresh_button.setText(tr('common.refresh'))
        self.import_button.setText(tr('common.import'))

    def selected_entry(self):
        return self.entries.get(self.selected_id())

    def message(self, text, error=False):
        self.status.setText(('Needs attention — ' if error else '') + text)
        self.status.setProperty('feedback', 'error' if error else 'info')
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)

    def submit(self, operation, message='', after=None, initial=False):
        if self.busy:
            return
        self.busy = True
        previous = self.selected_id()
        self.import_button.setEnabled(False)
        self.refresh_button.setEnabled(False)
        self.tree.setEnabled(False)
        self.selection_changed()
        self.message('Checking and saving template…' if not initial else 'Loading your template library…')
        def work():
            result = operation()
            return result, self.repo.list_templates()
        self.job = TemplateJob(work)
        def completed(result, error):
            self.busy = False
            self.import_button.setEnabled(True)
            self.refresh_button.setEnabled(True)
            self.tree.setEnabled(True)
            if error:
                self.message(error, True)
                self.selection_changed()
                return
            value, snapshot = result
            selection = value if isinstance(value, str) else previous
            self.populate(snapshot, selection)
            self.message(message or f'{len(self.entries)} templates in your library.')
            if after:
                after(value)
        self.job.signals.finished.connect(completed)
        self.pool.start(self.job)

    def reload(self):
        self.submit(self.repo.initialize, initial=True)

    def populate(self, snapshot, selected=None):
        self.entries = {entry['id']: entry for entry in snapshot['templates']}
        self.tree.blockSignals(True)
        self.tree.clear()
        selected_item = None
        for entry in snapshot['templates']:
            parts = []
            if entry['is_default']:
                parts.append('Default template')
            parts.append(f"{entry['minimum_options']}–{entry['maximum_options']} options")
            parts.append(self.template_status(entry))
            item = QTreeWidgetItem([entry['display_name'] + '\n' + ' · '.join(parts)])
            item.setData(0, Qt.UserRole, entry['id'])
            item.setSizeHint(0, QSize(0, 64))
            item.setToolTip(0, self.template_status_tooltip(entry))
            if entry.get('warnings') and not entry.get('warnings_acknowledged'):
                item.setIcon(0, warning_icon())
                item.setToolTip(0, 'Link warnings: expand this template to review the affected lines.')
            detail = QTreeWidgetItem(item)
            detail.setFlags(Qt.NoItemFlags)
            detail.setSizeHint(0, QSize(0, self.detail_height(entry)))
            self.tree.addTopLevelItem(item)
            self.tree.setItemWidget(detail, 0, self.detail_widget(entry))
            if entry['id'] == selected:
                selected_item = item
        self.tree.blockSignals(False)
        if selected_item:
            self.tree.setCurrentItem(selected_item)
        elif self.tree.topLevelItemCount():
            self.tree.setCurrentItem(self.tree.topLevelItem(0))
        self.empty.setVisible(not self.entries)
        self.selection_changed()
        self.library_changed.emit(snapshot)

    @staticmethod
    def template_status(entry):
        if not entry['valid']:
            return 'Missing template' if 'missing' in entry.get('problem', '').casefold() else 'Invalid template'
        if entry.get('warnings') and not entry.get('warnings_acknowledged'):
            return 'Valid · links need review'
        if entry['source_changed']:
            return 'Valid · source changed'
        return 'Valid'

    @classmethod
    def template_status_tooltip(cls, entry):
        status = cls.template_status(entry)
        if entry['is_default']:
            status = f'Default template · {status}'
        if not entry['valid'] and entry.get('problem'):
            return f'{status}. {entry["problem"]}'
        return status

    def selection_changed(self, *_):
        # Buttons live in the expanded template row and are rebuilt after every operation.
        pass

    def toggle_details(self, item, _column):
        if item.parent() is not None:
            return
        item.setExpanded(not item.isExpanded())

    def detail_height(self, entry):
        warnings = entry.get('warnings') or []
        if warnings and not entry.get('warnings_acknowledged', False):
            return 188 + min(len(warnings), 6) * 42
        return 188

    def detail_widget(self, entry):
        detail = QWidget()
        layout = QVBoxLayout(detail)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)
        source = entry.get('source_path') or 'Included with MCQ Maker'
        info = self.make_label(f"Format {entry.get('version', 1)} · Revision {entry.get('revision', 1)}\nOriginal: {source}\nStored copy: {self.repo.templates / entry['id'] / 'template.html'}", 'muted')
        info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(info)
        state = self.make_label(self.template_status_tooltip(entry), 'field')
        if not entry['valid']:
            state.setProperty('feedback', 'error')
        elif entry.get('warnings') and not entry.get('warnings_acknowledged', False):
            state.setProperty('feedback', 'warning')
        else:
            state.setProperty('feedback', 'success')
        layout.addWidget(state)
        if not entry['valid']:
            problem = self.make_label('Needs attention: ' + entry['problem'])
            problem.setProperty('feedback', 'error')
            layout.addWidget(problem)
        elif entry['source_changed']:
            layout.addWidget(self.make_label('The original file has changed. Re-import or Recheck to update the stored copy.'))
        warnings = entry.get('warnings') or []
        if warnings and not entry.get('warnings_acknowledged', False):
            lines = ['Linked resources may not work after MCQ Maker copies this template. You can still use it.']
            lines.extend(f"Line {warning['line']}: {warning['reference']} — {warning['message']}" for warning in warnings)
            note = self.make_label('\n'.join(lines))
            note.setProperty('feedback', 'error')
            note.setTextInteractionFlags(Qt.TextSelectableByMouse)
            layout.addWidget(note)
        first = QHBoxLayout()
        for title, callback, enabled in [
            ('Rename', self.rename, True),
            ('Set default', self.set_default, entry['valid'] and not entry['is_default']),
            ('Preview', self.preview, entry['valid']),
            ('Re-import', self.reimport, True),
            ('Remove', self.remove, True),
        ]:
            control = self.make_button(title, enabled and not self.busy)
            control.clicked.connect(lambda checked=False, template_id=entry['id'], action=callback: self.run_action(template_id, action))
            first.addWidget(control)
        first.addStretch()
        layout.addLayout(first)
        second = QHBoxLayout()
        for title, callback, enabled in [
            ('Reveal in folder', self.reveal, True),
            ('Ignore', self.ignore_warnings, bool(warnings) and not entry.get('warnings_acknowledged', False)),
            ('Recheck source', self.recheck, bool(entry.get('source_path'))),
        ]:
            control = self.make_button(title, enabled and not self.busy)
            control.clicked.connect(lambda checked=False, template_id=entry['id'], action=callback: self.run_action(template_id, action))
            second.addWidget(control)
        second.addStretch()
        layout.addLayout(second)
        return detail

    def run_action(self, template_id, action):
        for index in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(index)
            if item.data(0, Qt.UserRole) == template_id:
                self.tree.setCurrentItem(item)
                break
        action()

    def choose_file(self, title):
        filename, _ = QFileDialog.getOpenFileName(self, title, '', 'HTML templates (*.html *.htm)')
        return Path(filename) if filename else None

    def import_template(self, checked=False, path=None):
        path = path or self.choose_file('Import HTML template')
        if path is None:
            return
        name, accepted = QInputDialog.getText(self, 'Name template', 'Display name', text=path.stem)
        if accepted:
            self.submit(lambda: self.repo.import_template(path, name), f'Added “{name.strip()}”. Your original file is unchanged.')

    def rename(self):
        entry = self.selected_entry()
        if not entry:
            return
        name, accepted = QInputDialog.getText(self, 'Rename template', 'Display name', text=entry['display_name'])
        if accepted:
            self.submit(lambda: self.repo.rename(entry['id'], name), 'Template renamed. Its stored filename is unchanged.')

    def set_default(self):
        entry = self.selected_entry()
        if entry:
            self.submit(lambda: self.repo.set_default(entry['id']), f'“{entry["display_name"]}” is now the default template.')

    def reimport(self):
        entry = self.selected_entry()
        if not entry:
            return
        path = self.choose_file('Replace template from HTML file')
        if path:
            self.submit(lambda: self.repo.replace(entry['id'], path), 'Template updated. The previous revision is kept in the library’s revisions folder.')

    def ignore_warnings(self):
        entry = self.selected_entry()
        if entry:
            self.submit(lambda: self.repo.acknowledge_warnings(entry['id']), 'Link warnings ignored. This template remains available to use.')

    def recheck(self):
        entry = self.selected_entry()
        if entry:
            self.submit(lambda: self.repo.recheck(entry['id']), 'Rechecked the original file and updated the stored copy.')

    def remove(self):
        entry = self.selected_entry()
        if not entry:
            return
        message = f'Remove “{entry["display_name"]}” from your template library?\n\nYour original HTML and generated exams stay untouched.'
        if entry['is_default']:
            message += '\nThe next available valid template will become the default.'
        answer = QMessageBox.question(self, 'Remove template', message, QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
        if answer == QMessageBox.Yes:
            self.submit(lambda: self.repo.remove(entry['id']), 'Template removed from the library.')

    def preview(self):
        entry = self.selected_entry()
        if not entry or not entry['valid']:
            return
        def opened(path):
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
                self.message('The preview was saved, but Windows could not open a browser. Use Reveal in Explorer to inspect the stored template.', True)
        self.submit(lambda: self.repo.preview(entry['id']), 'Opened a preview with two sample questions. No exam was added to history.', after=opened)

    def reveal(self):
        template_id = self.selected_id()
        if template_id and not QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.repo.templates / template_id))):
            self.message('Windows could not open the template folder.', True)

    def context_menu(self, point):
        item = self.tree.itemAt(point)
        if item is None or self.busy:
            return
        if item.parent() is not None:
            item = item.parent()
        self.tree.setCurrentItem(item)
        entry = self.selected_entry()
        if not entry:
            return
        menu = QMenu(self)
        actions = [
            ('Rename', self.rename, True),
            ('Set default', self.set_default, entry['valid'] and not entry['is_default']),
            ('Preview', self.preview, entry['valid']),
            ('Re-import', self.reimport, True),
            ('Remove', self.remove, True),
            ('Reveal in folder', self.reveal, True),
            ('Ignore warnings', self.ignore_warnings, bool(entry.get('warnings')) and not entry.get('warnings_acknowledged', False)),
            ('Recheck source', self.recheck, bool(entry.get('source_path'))),
        ]
        for name, callback, enabled in actions:
            action = menu.addAction(name)
            action.setEnabled(enabled)
            action.triggered.connect(callback)
        menu.exec(self.tree.mapToGlobal(point))

    def dragEnterEvent(self, event):
        urls = event.mimeData().urls()
        if not self.busy and len(urls) == 1 and urls[0].isLocalFile() and Path(urls[0].toLocalFile()).suffix.lower() in ('.html', '.htm'):
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if not self.busy and len(urls) == 1 and urls[0].isLocalFile():
            event.acceptProposedAction()
            self.import_template(path=Path(urls[0].toLocalFile()))
