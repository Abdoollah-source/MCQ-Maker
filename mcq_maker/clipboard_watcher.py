"""Opt-in clipboard intake using the shared validation and generation pipeline."""
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import re

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal, Slot

from .exam_generator import proposed_filename, save_exam
from .history import payload_hash
from .quiz_validation import QuizError, QuizValidation, validate_quiz
from .template_validation import TemplateError, validate_template


def recognizable_quiz_text(text: str) -> bool:
    """Conservatively distinguish quiz-like clipboard text from everyday copying."""
    sample = text.lstrip('\ufeff \t\r\n')
    if not sample or '[' not in sample:
        return False
    return bool(re.search(r'["\']title["\']\s*:', sample, re.IGNORECASE)
                and re.search(r'["\'](?:question|options|correct)["\']\s*:', sample, re.IGNORECASE))


@dataclass(frozen=True)
class ClipboardJob:
    quiz: QuizValidation
    template: str
    template_entry: dict
    output_folder: Path
    content_hash: str


def prepare_clipboard_job(text, repository, settings):
    if not recognizable_quiz_text(text):
        return None
    snapshot = repository.list_templates()
    entry = next((item for item in snapshot['templates']
                  if item['id'] == snapshot['default_id'] and item.get('valid')), None)
    if entry is None:
        raise TemplateError('Choose a valid default template before using the clipboard watcher.')
    template = repository.read_template(entry['id'])
    contract = validate_template(template)
    quiz = validate_quiz(text, contract.minimum_options, contract.maximum_options)
    return ClipboardJob(
        quiz=quiz,
        template=template,
        template_entry=dict(entry),
        output_folder=Path(settings['output_folder']),
        content_hash=sha256(text.encode('utf-8')).hexdigest(),
    )


class _WorkerSignals(QObject):
    prepared = Signal(object)
    ignored = Signal()
    failed = Signal(str, str, object)
    saved = Signal(object, object)


class _PrepareWorker(QRunnable):
    def __init__(self, text, repository, settings, content_hash):
        super().__init__()
        self.text = text
        self.repository = repository
        self.settings = dict(settings)
        self.content_hash = content_hash
        self.signals = _WorkerSignals()

    @Slot()
    def run(self):
        try:
            job = prepare_clipboard_job(self.text, self.repository, self.settings)
        except (QuizError, TemplateError, OSError) as exc:
            try:
                snapshot = self.repository.list_templates()
                entry = next((item for item in snapshot['templates']
                              if item['id'] == snapshot['default_id']), {})
            except Exception:
                entry = {}
            self.signals.failed.emit(str(exc), self.content_hash, entry)
            return
        if job is None:
            self.signals.ignored.emit()
        else:
            self.signals.prepared.emit(job)


class _SaveWorker(QRunnable):
    def __init__(self, job, overwrite):
        super().__init__()
        self.job = job
        self.overwrite = overwrite
        self.signals = _WorkerSignals()

    @Slot()
    def run(self):
        try:
            path = save_exam(self.job.template, self.job.quiz, self.job.output_folder,
                             overwrite=self.overwrite)
        except (QuizError, TemplateError, OSError) as exc:
            self.signals.failed.emit(str(exc), self.job.content_hash,
                                     self.job.template_entry)
            return
        self.signals.saved.emit(self.job, path)


class ClipboardWatcher(QObject):
    state_changed = Signal(str)
    activity = Signal(str, bool)
    history_changed = Signal()
    notification_requested = Signal(str, str, str, object)

    def __init__(self, clipboard, repository, settings, history=None,
                 conflict_resolver=None, parent=None):
        super().__init__(parent)
        self.clipboard = clipboard
        self.repository = repository
        self.settings = dict(settings)
        self.history = history
        self.conflict_resolver = conflict_resolver or (lambda _path: 'copy')
        self.state = 'off'
        self.busy = False
        self.pending_text = None
        self.worker = None
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(450)
        self.timer.timeout.connect(self._read_clipboard)
        self.clipboard.dataChanged.connect(self._clipboard_changed)
        self.apply_settings(settings)

    def apply_settings(self, settings):
        self.settings = dict(settings)
        enabled = bool(settings.get('clipboard_watcher'))
        if not enabled:
            self.timer.stop()
            self.pending_text = None
            self._set_state('off')
        elif self.state == 'off':
            self._set_state('on')

    def pause(self):
        if self.state == 'on':
            self.timer.stop()
            self.pending_text = None
            self._set_state('paused')

    def resume(self):
        if self.state == 'paused':
            self._set_state('on')

    def shutdown(self):
        self.timer.stop()
        self.pending_text = None
        self._set_state('off')

    def _set_state(self, state):
        if self.state != state:
            self.state = state
            self.state_changed.emit(state)

    def _clipboard_changed(self):
        if self.state == 'on':
            self.timer.start()

    def _read_clipboard(self):
        if self.state == 'on':
            try:
                text = self.clipboard.text()
            except Exception:
                self.shutdown()
                message = 'Clipboard watching stopped unexpectedly. Open Settings to turn it on again.'
                self.activity.emit(message, True)
                self.notification_requested.emit('watcher_stopped', 'Clipboard watcher stopped', message, 'settings')
                return
            self.queue_text(text)

    def queue_text(self, text):
        if self.state != 'on' or not text.strip():
            return False
        content_hash = sha256(text.encode('utf-8')).hexdigest()
        if self.busy:
            self.pending_text = text
            return True
        self._prepare(text, content_hash)
        return True

    def _prepare(self, text, content_hash):
        self.busy = True
        worker = _PrepareWorker(text, self.repository, self.settings, content_hash)
        worker.signals.prepared.connect(self._prepared)
        worker.signals.ignored.connect(self._ignored)
        worker.signals.failed.connect(self._failed)
        self.worker = worker
        self.pool.start(worker)

    def _prepared(self, job):
        target = job.output_folder / proposed_filename(job.quiz.title, job.output_folder)
        choice = 'copy'
        if target.exists() and self.settings.get('manual_conflicts') == 'ask':
            choice = self.conflict_resolver(target)
        if choice == 'cancel':
            self.activity.emit('The clipboard quiz was not saved.', False)
            self._complete()
            return
        worker = _SaveWorker(job, overwrite=choice == 'replace')
        worker.signals.saved.connect(self._saved)
        worker.signals.failed.connect(self._failed)
        self.worker = worker
        self.pool.start(worker)

    def _ignored(self):
        self._complete()

    def _failed(self, message, content_hash, template_entry):
        if self.history is not None:
            try:
                self.history.record_failure(
                    template_entry=template_entry,
                    source_mode='clipboard',
                    error_summary=message,
                    input_hash=content_hash,
                )
                self.history_changed.emit()
            except Exception:
                pass
        self.activity.emit(message, True)
        self.notification_requested.emit(
            'background_error', 'Clipboard exam could not be saved', message, None)
        self._complete()

    def _saved(self, job, path):
        history_note = ''
        if self.history is not None:
            try:
                self.history.record_success(
                    quiz=job.quiz,
                    output_path=path,
                    template_entry=job.template_entry,
                    source_mode='clipboard',
                )
                self.history_changed.emit()
            except Exception:
                history_note = ' History could not be updated.'
        message = f'Saved “{path.name}” from the clipboard.{history_note}'
        self.activity.emit(message, False)
        question_word = 'question' if len(job.quiz.questions) == 1 else 'questions'
        self.notification_requested.emit(
            'clipboard_saved', 'Exam saved',
            f'{job.quiz.title} · {len(job.quiz.questions)} {question_word}', path)
        self._complete()

    def _complete(self):
        self.busy = False
        self.worker = None
        if self.pending_text is not None and self.state == 'on':
            text, self.pending_text = self.pending_text, None
            content_hash = sha256(text.encode('utf-8')).hexdigest()
            self._prepare(text, content_hash)
