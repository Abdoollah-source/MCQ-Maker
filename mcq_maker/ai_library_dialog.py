"""In-app management for providers, prompt versions, and copied references."""
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (QDialog, QFileDialog, QFormLayout, QHBoxLayout,
                               QInputDialog, QLineEdit, QListWidget, QMessageBox,
                               QPlainTextEdit, QTabWidget, QVBoxLayout, QWidget)

from .ai_library import AILibraryError, PROVIDERS
from .ai_providers import ProviderError, list_models, test_connection
from .components import Dropdown, button, label, panel


GUIDES = {
    'google': 'Sign in to Google AI Studio, choose Get API key, create a key, then paste it here. The key gives MCQ Maker access to Gemini under that Google project.',
    'openai': 'Sign in to the OpenAI Platform and create a secret key on the API keys page. API billing is separate from a ChatGPT subscription.',
    'anthropic': 'Open the Anthropic Console, go to API Keys, and create a key. Make sure the account has API credits before testing it.',
    'openrouter': 'OpenRouter provides hundreds of models through one key. Gemini Flash or Auto are good choices here; free models have limited availability and stricter rate limits.',
}


class _TestSignals(QObject):
    done = Signal(str, object)


class _TestJob(QRunnable):
    def __init__(self, provider, key):
        super().__init__(); self.provider = provider; self.key = key; self.signals = _TestSignals()

    def run(self):
        try:
            test_connection(self.provider, self.key)
            models = list_models(self.provider, self.key)
            self.signals.done.emit(
                f'Connection successful. Found {len(models)} compatible model' + ('s.' if len(models) != 1 else '.'),
                models)
        except ProviderError as exc:
            self.signals.done.emit('Needs attention — ' + str(exc), [])


class AILibraryDialog(QDialog):
    library_changed = Signal()

    def __init__(self, library, parent=None):
        super().__init__(parent)
        self.library = library
        self.pool = QThreadPool(self); self.pool.setMaxThreadCount(1)
        self.job = None
        self.setWindowTitle('AI library')
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.resize(820, 680); self.setMinimumSize(660, 560)
        outer = QVBoxLayout(self); outer.setContentsMargins(24, 24, 24, 20); outer.setSpacing(16)
        outer.addWidget(label('AI library', 'title'))
        outer.addWidget(label('Manage provider accounts, generation prompts, and reference files.'))
        self.tabs = QTabWidget(); outer.addWidget(self.tabs, 1)
        self.tabs.addTab(self._provider_tab(), 'API providers')
        self.tabs.addTab(self._prompt_tab(), 'System prompts')
        self.tabs.addTab(self._reference_tab(), 'Reference files')
        footer = QHBoxLayout(); self.status = label('Changes are saved on this device.', 'muted')
        footer.addWidget(self.status, 1); done = button('Done', True, True); done.clicked.connect(self.close)
        footer.addWidget(done); outer.addLayout(footer)
        self.reload()

    def _provider_tab(self):
        page = QWidget(); layout = QHBoxLayout(page); layout.setContentsMargins(16,16,16,16); layout.setSpacing(20)
        self.provider_list = QListWidget(); self.provider_list.setMinimumWidth(180); self.provider_list.setMaximumWidth(210)
        for key, item in PROVIDERS.items():
            self.provider_list.addItem(item['name']); self.provider_list.item(self.provider_list.count()-1).setData(Qt.UserRole, key)
        self.provider_list.currentRowChanged.connect(self._provider_selected); layout.addWidget(self.provider_list)
        box, form = panel(); layout.addWidget(box, 1)
        self.provider_heading = label('', 'heading'); form.addWidget(self.provider_heading)
        self.provider_state = label('', 'muted'); form.addWidget(self.provider_state)
        self.accounts = Dropdown(); self.accounts.currentIndexChanged.connect(self._account_selected); form.addWidget(self.accounts)
        form.addWidget(label('Account nickname', 'field')); self.key_name = QLineEdit(); self.key_name.setPlaceholderText('Personal account')
        form.addWidget(self.key_name)
        form.addWidget(label('API key', 'field')); key_row = QHBoxLayout(); self.key_value = QLineEdit(); self.key_value.setEchoMode(QLineEdit.Password)
        self.key_value.setPlaceholderText('Paste a new key, or select a saved account')
        key_row.addWidget(self.key_value, 1); self.reveal = button('Reveal', True); self.reveal.setCheckable(True); self.reveal.toggled.connect(self._reveal)
        key_row.addWidget(self.reveal); form.addLayout(key_row)
        actions = QHBoxLayout(); add = button('Save as new account', True, True); add.clicked.connect(self._save_key); actions.addWidget(add)
        self.test = button('Test connection', True); self.test.clicked.connect(self._test_key); actions.addWidget(self.test)
        self.remove_key = button('Remove', True); self.remove_key.clicked.connect(self._remove_key); actions.addWidget(self.remove_key); form.addLayout(actions)
        self.guide = label(''); self.guide.setOpenExternalLinks(False); self.guide.linkActivated.connect(lambda url: QDesktopServices.openUrl(QUrl(url)))
        self.guide.setTextFormat(Qt.RichText); form.addWidget(self.guide); form.addStretch()
        form.addWidget(label('Planned next: Microsoft Azure AI and local models. Provider adapters are isolated so they can be added without changing the generation workflow.', 'muted'))
        return page

    def _prompt_tab(self):
        page = QWidget(); layout = QHBoxLayout(page); layout.setContentsMargins(16,16,16,16); layout.setSpacing(20)
        self.prompt_list = QListWidget(); self.prompt_list.setMinimumWidth(220); self.prompt_list.setMaximumWidth(260); self.prompt_list.currentRowChanged.connect(self._prompt_selected); layout.addWidget(self.prompt_list)
        box, content = panel(); layout.addWidget(box, 1); content.addWidget(label('Prompt version', 'heading'))
        content.addWidget(label('The default is simply the prompt preselected for a new run. You can choose any other prompt in AI generation without changing it.', 'muted'))
        self.prompt_name = QLineEdit(); self.prompt_name.setPlaceholderText('Prompt name'); content.addWidget(self.prompt_name)
        self.prompt_text = QPlainTextEdit(); self.prompt_text.setPlaceholderText('Generation instructions'); content.addWidget(self.prompt_text, 1)
        row = QHBoxLayout(); save = button('Save new version', True, True); save.clicked.connect(self._save_prompt); row.addWidget(save)
        self.activate_prompt = button('Use by default', True); self.activate_prompt.clicked.connect(self._activate_prompt); row.addWidget(self.activate_prompt)
        self.remove_prompt = button('Delete version', True); self.remove_prompt.clicked.connect(self._remove_prompt); row.addWidget(self.remove_prompt); row.addStretch(); content.addLayout(row)
        return page

    def _reference_tab(self):
        page = QWidget(); layout = QHBoxLayout(page); layout.setContentsMargins(16,16,16,16); layout.setSpacing(20)
        self.reference_list = QListWidget(); self.reference_list.setMinimumWidth(220); self.reference_list.setMaximumWidth(260); self.reference_list.currentRowChanged.connect(self._reference_selected); layout.addWidget(self.reference_list)
        box, form_box = panel(); layout.addWidget(box, 1); form_box.addWidget(label('Reference details', 'heading'))
        self.reference_path = QLineEdit(); self.reference_path.setReadOnly(True); self.reference_path.setPlaceholderText('Choose a PDF or text reference')
        choose = button('Choose reference file', True); choose.clicked.connect(self._choose_reference)
        path_row = QHBoxLayout(); path_row.addWidget(self.reference_path, 1); path_row.addWidget(choose); form_box.addLayout(path_row)
        self.reference_subject = QLineEdit(); self.reference_topic = QLineEdit(); self.reference_course = QLineEdit(); self.reference_notes = QPlainTextEdit(); self.reference_notes.setMaximumHeight(100)
        form = QFormLayout(); form.setSpacing(10); form.addRow('Subject', self.reference_subject); form.addRow('Topic', self.reference_topic); form.addRow('Class or course', self.reference_course); form.addRow('Notes', self.reference_notes); form_box.addLayout(form)
        row = QHBoxLayout(); add = button('Add to library', True, True); add.clicked.connect(self._add_reference); row.addWidget(add)
        self.remove_reference = button('Remove', True); self.remove_reference.clicked.connect(self._remove_reference); row.addWidget(self.remove_reference); row.addStretch(); form_box.addLayout(row); form_box.addStretch()
        return page

    def reload(self):
        data = self.library.initialize()
        self._refresh_provider_labels(data)
        provider_row = max(0, self.provider_list.currentRow()); self.provider_list.setCurrentRow(provider_row)
        self.prompt_list.clear()
        for item in sorted(data['prompts'], key=lambda x: (x['name'].casefold(), -x['version'])):
            active = ' · Default' if item['id'] == data['active_prompt_id'] else ''
            self.prompt_list.addItem(f"{item['name']} · v{item['version']}{active}"); self.prompt_list.item(self.prompt_list.count()-1).setData(Qt.UserRole, item)
        if self.prompt_list.count(): self.prompt_list.setCurrentRow(0)
        self.reference_list.clear()
        for item in sorted(data['references'], key=lambda x: x['subject'].casefold()):
            self.reference_list.addItem(f"{item['subject']}\n{item['topic'] or item['name']}"); self.reference_list.item(self.reference_list.count()-1).setData(Qt.UserRole, item)
        if self.reference_list.count(): self.reference_list.setCurrentRow(0)
        self.remove_reference.setEnabled(bool(data['references']))
        self.library_changed.emit()

    @staticmethod
    def _provider_status(provider, data):
        keys = [x for x in data['keys'] if x['provider'] == provider]
        if not keys: return 'Not configured'
        if any(x['status'] == 'connected' for x in keys): return 'Connected'
        if all(x['status'] == 'rate limited' for x in keys): return 'Rate limited'
        return 'Needs attention'

    def _refresh_provider_labels(self, data=None):
        data = data or self.library.load()
        for row in range(self.provider_list.count()):
            item = self.provider_list.item(row); provider = item.data(Qt.UserRole)
            item.setText(f"{PROVIDERS[provider]['name']} · {self._provider_status(provider, data)}")

    def _provider_selected(self, *_):
        item = self.provider_list.currentItem()
        if not item: return
        provider = item.data(Qt.UserRole); info = PROVIDERS[provider]; self.provider_heading.setText(info['name'])
        self.guide.setText(f"{GUIDES[provider]}<br><br><a href=\"{info['url']}\">Open the {info['name']} API key page</a>")
        self.accounts.blockSignals(True); self.accounts.clear(); self.accounts.addItem('Add another account', None)
        data = self.library.load(); keys = [x for x in data['keys'] if x['provider'] == provider]
        for key in keys: self.accounts.addItem(f"{key['nickname']} · {key['status']}", key['id'])
        self.accounts.blockSignals(False); self.accounts.setCurrentIndex(1 if keys else 0)
        status = self._provider_status(provider, data)
        next_account = f' · Next account: {keys[0]["nickname"]}' if keys else ''
        self.provider_state.setText(status + next_account)
        self._account_selected()

    def _account_selected(self, *_):
        key_id = self.accounts.currentData(); self.key_value.clear(); self.key_name.clear()
        if key_id:
            item = next(x for x in self.library.load()['keys'] if x['id'] == key_id)
            self.key_name.setText(item['nickname']); self.key_value.setText(self.library.secret(key_id))
        self.remove_key.setEnabled(bool(key_id))

    def _reveal(self, shown):
        self.key_value.setEchoMode(QLineEdit.Normal if shown else QLineEdit.Password); self.reveal.setText('Hide' if shown else 'Reveal')

    def _save_key(self):
        try: self.library.add_key(self.provider_list.currentItem().data(Qt.UserRole), self.key_name.text(), self.key_value.text())
        except AILibraryError as exc: self._message(str(exc), True); return
        self._message('API key protected by Windows and saved.'); self._refresh_provider_labels(); self._provider_selected(); self.library_changed.emit()

    def _test_key(self):
        key = self.key_value.text().strip()
        if not key: self._message('Enter or select an API key before testing.', True); return
        self.test.setEnabled(False); self._message('Testing the connection…')
        self.job = _TestJob(self.provider_list.currentItem().data(Qt.UserRole), key)
        provider = self.provider_list.currentItem().data(Qt.UserRole)
        key_id = self.accounts.currentData()
        def done(message, models):
            self.test.setEnabled(True)
            if models:
                self.library.set_models(provider, models)
                if key_id: self.library.update_key_status(key_id, 'connected', None)
                self._refresh_provider_labels(); self.library_changed.emit()
            self._message(message, message.startswith('Needs attention'))
        self.job.signals.done.connect(done); self.pool.start(self.job)

    def _remove_key(self):
        if self.accounts.currentData() and QMessageBox.question(self, 'Remove API key', 'Remove this saved API key?', QMessageBox.Yes|QMessageBox.Cancel, QMessageBox.Cancel)==QMessageBox.Yes:
            self.library.remove_key(self.accounts.currentData()); self._refresh_provider_labels(); self._provider_selected(); self.library_changed.emit()

    def _prompt_selected(self, *_):
        item = self.prompt_list.currentItem().data(Qt.UserRole) if self.prompt_list.currentItem() else None
        if item: self.prompt_name.setText(item['name']); self.prompt_text.setPlainText(item['text']); self.activate_prompt.setEnabled(item['id'] != self.library.load()['active_prompt_id'])

    def _save_prompt(self):
        try: self.library.add_prompt(self.prompt_name.text(), self.prompt_text.toPlainText())
        except AILibraryError as exc: self._message(str(exc), True); return
        self._message('New prompt version saved and made active.'); self.reload()

    def _activate_prompt(self):
        item = self.prompt_list.currentItem().data(Qt.UserRole) if self.prompt_list.currentItem() else None
        if item: self.library.set_active_prompt(item['id']); self._message('Default prompt changed.'); self.reload()

    def _remove_prompt(self):
        item = self.prompt_list.currentItem().data(Qt.UserRole) if self.prompt_list.currentItem() else None
        if not item:
            return
        answer = QMessageBox.question(self, 'Delete prompt version',
            f'Delete “{item["name"]} · v{item["version"]}”?',
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel)
        if answer == QMessageBox.Yes:
            try:
                self.library.remove_prompt(item['id'])
            except AILibraryError as exc:
                self._message(str(exc), True); return
            self._message('Prompt version deleted.'); self.reload()

    def _choose_reference(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Choose reference file', '', 'Reference files (*.pdf *.txt *.md);;All files (*)')
        if path: self.reference_path.setText(path)

    def _add_reference(self):
        try: self.library.add_reference(Path(self.reference_path.text()), self.reference_subject.text(), self.reference_topic.text(), self.reference_course.text(), self.reference_notes.toPlainText())
        except AILibraryError as exc: self._message(str(exc), True); return
        self._message('Reference copied into the AI library.'); self.reference_path.clear(); self.reload()

    def _reference_selected(self, *_):
        item = self.reference_list.currentItem().data(Qt.UserRole) if self.reference_list.currentItem() else None
        if item: self.reference_subject.setText(item['subject']); self.reference_topic.setText(item['topic']); self.reference_course.setText(item['course']); self.reference_notes.setPlainText(item['notes'])

    def _remove_reference(self):
        item = self.reference_list.currentItem().data(Qt.UserRole) if self.reference_list.currentItem() else None
        if item and QMessageBox.question(self, 'Remove reference', 'Remove this copied reference file?', QMessageBox.Yes|QMessageBox.Cancel, QMessageBox.Cancel)==QMessageBox.Yes:
            self.library.remove_reference(item['id']); self._message('Reference removed.'); self.reload()

    def _message(self, text, error=False):
        self.status.setText(text); self.status.setProperty('feedback', 'error' if error else 'info'); self.status.style().unpolish(self.status); self.status.style().polish(self.status)
