"""Cancellable AI batch engine with key rotation, validation, and immediate saving."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
import time

from PySide6.QtCore import QObject, QRunnable, Signal

from .ai_providers import (AuthenticationError, ProviderError, RateLimitError,
                           TemporaryProviderError,
                           create_session)
from .exam_generator import save_exam
from .history import payload_hash
from .quiz_validation import QuizError, validate_quiz


def discover_pdfs(folder):
    folder = Path(folder)
    if not folder.is_dir():
        raise OSError('The lecture folder is no longer available.')
    return sorted((p for p in folder.iterdir() if p.is_file() and p.suffix.casefold() == '.pdf'),
                  key=lambda p: p.name.casefold())


@dataclass(frozen=True)
class AIRun:
    lectures: tuple[Path, ...]
    reference_ids: dict[str, str]
    prompt: dict
    provider: str
    model: str
    thinking: str
    lectures_per_conversation: int
    template: str
    template_entry: dict
    output_folder: Path


@dataclass(frozen=True)
class AIResult:
    lecture: Path
    status: str
    reason: str = ''
    output_path: Path | None = None
    title: str = ''
    question_count: int = 0
    input_hash: str = ''


class AISignals(QObject):
    lecture_status = Signal(str, str, str)
    log = Signal(str, str, str)
    summary = Signal(int, int, int, int)
    finished = Signal(object, bool)


class AIGenerationWorker(QRunnable):
    def __init__(self, run, library, history=None, cancellation=None):
        super().__init__()
        self.config = run
        self.library = library
        self.history = history
        self.cancellation = cancellation or Event()
        self.signals = AISignals()
        self.results = []
        self._key_cursor = 0

    def cancel(self):
        self.cancellation.set()

    def _log(self, kind, message):
        self.signals.log.emit(datetime.now().strftime('%H:%M:%S'), kind, message)

    def _keys(self):
        return [x for x in self.library.load()['keys'] if x['provider'] == self.config.provider]

    def _available_key(self):
        keys = self._keys()
        if not keys:
            raise ProviderError('No API key is configured for this provider.')
        now = datetime.now(timezone.utc)
        available, waits = [], []
        for item in keys:
            until = item.get('rate_limited_until')
            try:
                remaining = (datetime.fromisoformat(until) - now).total_seconds() if until else 0
            except (ValueError, TypeError):
                remaining = 0
            if remaining <= 0:
                available.append(item)
            else:
                waits.append(remaining)
        if available:
            item = available[self._key_cursor % len(available)]
            self._key_cursor += 1
            return item
        wait = max(1, int(min(waits, default=60)))
        self._log('wait', f'All {self.config.provider.title()} accounts are resting. Resuming in {wait // 60:02d}:{wait % 60:02d}.')
        while wait and not self.cancellation.wait(1):
            wait -= 1
            if wait < 10 or wait % 30 == 0:
                self._log('wait', f'Resuming automatically in {wait // 60:02d}:{wait % 60:02d}.')
        if self.cancellation.is_set():
            raise ProviderError('Generation was cancelled.')
        return self._available_key()

    def _session(self):
        key_item = self._available_key()
        secret = self.library.secret(key_item['id'])
        self._log('account', f'Using {key_item["nickname"]} ({self.config.provider.title()}).')
        return key_item, create_session(self.config.provider, secret, self.config.model, self.config.thinking)

    def _rate_limited(self, key, exc):
        until = datetime.fromtimestamp(time.time() + exc.retry_after, timezone.utc).isoformat(timespec='seconds')
        self.library.update_key_status(key['id'], 'rate limited', until)
        self._log('wait', f'{key["nickname"]} reached its limit. Trying the next saved account.')

    def _wait_for_provider(self, seconds, lecture_name):
        remaining = max(1, int(seconds))
        self._log('wait', f'{lecture_name}: Google is temporarily busy. Retrying automatically in {remaining} seconds.')
        while remaining and not self.cancellation.wait(1):
            remaining -= 1
            if remaining and (remaining <= 5 or remaining % 15 == 0):
                self._log('wait', f'{lecture_name}: retrying in {remaining} seconds.')

    def _record(self, result):
        if self.history is None:
            return
        try:
            if result.status == 'done':
                self.history.record_success(title=result.title, question_count=result.question_count,
                    input_hash=result.input_hash, output_path=result.output_path,
                    template_entry=self.config.template_entry, source_mode='ai', source_path=result.lecture)
            else:
                self.history.record_failure(template_entry=self.config.template_entry, source_mode='ai',
                    source_path=result.lecture, error_summary=result.reason)
        except Exception:
            self._log('warning', f'{result.lecture.name}: the result was kept, but History could not be updated.')

    @staticmethod
    def _close_session(session):
        close = getattr(session, 'close', None)
        if callable(close):
            try:
                close()
            except Exception:
                pass

    def run(self):
        completed = failed = 0
        total = len(self.config.lectures)
        index = 0
        while index < total and not self.cancellation.is_set():
            reference_id = self.config.reference_ids.get(str(self.config.lectures[index]))
            group = []
            while (index < total and len(group) < self.config.lectures_per_conversation
                   and self.config.reference_ids.get(str(self.config.lectures[index])) == reference_id):
                group.append(self.config.lectures[index])
                index += 1
            try:
                reference = self.library.reference_path(reference_id)
            except Exception as exc:
                for lecture in group:
                    result = AIResult(lecture, 'failed', str(exc))
                    self.results.append(result); failed += 1; self._record(result)
                    self.signals.lecture_status.emit(str(lecture), 'failed', str(exc))
                continue

            session = key_item = None
            calibrated = False
            for lecture in group:
                if self.cancellation.is_set():
                    break
                self.signals.lecture_status.emit(str(lecture), 'processing', 'Turn 1 Â· calibrating')
                attempts = 0
                while not self.cancellation.is_set():
                    attempts += 1
                    try:
                        if session is None or not calibrated:
                            key_item, session = self._session()
                            self._log('turn', f'{lecture.name}: turn 1 â€” calibrating with {Path(reference).name}.')
                            confirmation = session.calibrate(self.config.prompt['text'], reference)
                            self.library.update_key_status(key_item['id'], 'connected', None)
                            self._log('reply', f'Calibration confirmed: {confirmation.strip()[:240]}')
                            calibrated = True
                        self.signals.lecture_status.emit(str(lecture), 'processing', 'Turn 2 Â· generating')
                        self._log('turn', f'{lecture.name}: turn 2 â€” generating questions.')
                        response = session.generate(lecture)
                        self._log('reply', f'{lecture.name}: response received; checking its structure.')
                        quiz = validate_quiz(response, self.config.template_entry['minimum_options'],
                                             self.config.template_entry['maximum_options'])
                        output = save_exam(self.config.template, quiz, self.config.output_folder)
                        result = AIResult(lecture, 'done', output_path=output, title=quiz.title,
                                          question_count=len(quiz.questions), input_hash=payload_hash(quiz.payload))
                        self.results.append(result); completed += 1; self._record(result)
                        self.signals.lecture_status.emit(str(lecture), 'done', output.name)
                        self._log('saved', f'{lecture.name}: saved â€œ{output.name}â€ with {len(quiz.questions)} questions.')
                        break
                    except RateLimitError as exc:
                        self._rate_limited(key_item, exc)
                        self._close_session(session)
                        session = None; calibrated = False
                    except TemporaryProviderError as exc:
                        self._wait_for_provider(exc.retry_after, lecture.name)
                        if self.cancellation.is_set():
                            break
                    except AuthenticationError as exc:
                        if key_item:
                            self.library.update_key_status(key_item['id'], 'needs attention', None)
                        self._log('error', f'{lecture.name}: {exc}')
                        self._close_session(session)
                        session = None; calibrated = False
                        if attempts >= max(1, len(self._keys())):
                            result = AIResult(lecture, 'failed', str(exc)); failed += 1
                            self.results.append(result); self._record(result)
                            self.signals.lecture_status.emit(str(lecture), 'failed', str(exc)); break
                    except (ProviderError, QuizError, OSError) as exc:
                        result = AIResult(lecture, 'failed', str(exc)); failed += 1
                        self.results.append(result); self._record(result)
                        self.signals.lecture_status.emit(str(lecture), 'failed', str(exc))
                        self._log('error', f'{lecture.name}: {exc}')
                        self._close_session(session)
                        session = None; calibrated = False
                        break
                self.signals.summary.emit(total, completed, failed, total - completed - failed)
            self._close_session(session)
        if self.cancellation.is_set():
            self._log('warning', 'Generation stopped. Finished exams were kept.')
        else:
            self._log('complete', f'Batch complete: {completed} saved, {failed} failed.')
        self.signals.finished.emit(tuple(self.results), self.cancellation.is_set())

