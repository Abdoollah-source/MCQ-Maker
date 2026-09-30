"""Durable, single-worker Google AI Studio lecture queue."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone, timedelta
import asyncio
import hashlib
import json
import os
from pathlib import Path
import random
import re
import tempfile
from threading import Lock
import uuid

from .ai_studio_errors import (
    AIStudioAuthenticationRequired, AIStudioCalibrationError,
    AIStudioConnectionError, AIStudioExtractionError,
    AIStudioInitializationError, AIStudioUploadError, AIStudioValidationError,
    AIStudioResponseError,
)
from .app_data import app_data_root
from .ai_studio_session import AIStudioConversationSession
from .exam_generator import proposed_filename, save_exam, serialize_payload


class BatchStatus:
    PENDING = 'Pending'
    RETRY = 'Waiting to retry'
    OPENING = 'Opening new conversation'
    CONFIGURING = 'Configuring AI Studio'
    CALIBRATING = 'Calibrating'
    READY = 'Calibration ready'
    UPLOADING = 'Uploading lecture'
    GENERATING = 'Generating questions'
    VALIDATING = 'Validating JSON'
    SAVING = 'Saving HTML'
    COMPLETED = 'Completed'
    FAILED_TEMP = 'Failed temporarily'
    ATTENTION = 'Needs attention'
    INTERRUPTED = 'Interrupted'
    PAUSED = 'Paused'
    CANCELLED = 'Cancelled'


ACTIVE_STATUSES = {
    BatchStatus.OPENING, BatchStatus.CONFIGURING, BatchStatus.CALIBRATING,
    BatchStatus.READY, BatchStatus.UPLOADING, BatchStatus.GENERATING,
    BatchStatus.VALIDATING, BatchStatus.SAVING,
}
MAX_PRE_SUBMISSION_ATTEMPTS = 3
MAX_PREPARED_WORKERS = 2
_worker_locks_guard = Lock()
_worker_locks: dict[str, Lock] = {}
_job_claim_locks: dict[str, Lock] = {}
_manifest_write_locks: dict[str, Lock] = {}


def _manifest_write_lock(path):
    key = str(Path(path).resolve())
    with _worker_locks_guard:
        return _manifest_write_locks.setdefault(key, Lock())


def _job_claim_lock(batch_id):
    with _worker_locks_guard:
        return _job_claim_locks.setdefault(str(batch_id), Lock())


def discover_lecture_pdfs(folder):
    folder = Path(folder)
    if not folder.is_dir():
        raise OSError('The lecture folder is no longer available.')
    return sorted((p.resolve() for p in folder.iterdir() if p.is_file() and p.suffix.casefold() == '.pdf'),
                  key=lambda p: (p.name.casefold(), p.name))


def input_fingerprint(path):
    path = Path(path)
    stat = path.stat()
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return {'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns, 'sha256': digest.hexdigest()}


def _utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _path_fingerprint(path):
    """Return a content hash when available; never retain the file contents."""
    path = Path(path)
    try:
        return input_fingerprint(path)['sha256']
    except OSError:
        return 'missing'


def configuration_fingerprint(*, prompt_path, reference_path, model, thinking,
                              output_folder, conflict_policy='save_copy',
                              template_id='', template=''):
    material = {
        'prompt_path': str(Path(prompt_path).resolve()),
        'prompt_sha256': _path_fingerprint(prompt_path),
        'reference_path': str(Path(reference_path).resolve()),
        'reference_sha256': _path_fingerprint(reference_path),
        'model': str(model), 'thinking': str(thinking),
        'output_folder': str(Path(output_folder).resolve()),
        'conflict_policy': str(conflict_policy), 'template_id': str(template_id or ''),
        'template_sha256': hashlib.sha256(str(template).encode('utf-8')).hexdigest(),
    }
    encoded = json.dumps(material, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def default_batch_root():
    return app_data_root() / 'ai_studio' / 'batches'


@dataclass
class LectureState:
    source_path: str
    file_name: str
    fingerprint: dict
    status: str = BatchStatus.PENDING
    attempt_count: int = 0
    retry_count: int = 0
    last_error: str = ''
    last_attempted_at: str | None = None
    next_retry_at: str | None = None
    output_path: str | None = None
    question_count: int | None = None
    title: str = ''
    stage: str = 'not_started'
    lecture_submission_state: str = 'not_started'
    template_id: str = ''
    validation_diagnostics: dict = field(default_factory=dict)
    job_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    worker_id: str | None = None
    claimed_at: str | None = None


@dataclass
class BatchManifest:
    batch_id: str
    folder: str
    prompt_path: str
    reference_path: str
    model: str
    thinking: str
    output_folder: str
    conflict_policy: str
    created_at: str
    updated_at: str
    lectures: list[LectureState] = field(default_factory=list)
    configuration_fingerprint: str = ''
    template_id: str = ''
    schema_version: int = 3
    control_state: str = 'idle'
    max_workers: int = 1


class BatchStateStore:
    """Atomic JSON manifests in MCQ Maker's per-user application data folder."""

    def __init__(self, root=None):
        self.root = Path(root or default_batch_root())

    def path_for(self, batch_id):
        return self.root / f'{batch_id}.json'

    def save(self, manifest):
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.path_for(manifest.batch_id)
        with _manifest_write_lock(target):
            manifest.updated_at = _utc_now()
            payload = asdict(manifest)
            fd, temporary = tempfile.mkstemp(prefix='.batch-', suffix='.tmp', dir=self.root)
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                    json.dump(payload, stream, ensure_ascii=False, indent=2)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, target)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        return target

    def load(self, batch_id):
        payload = json.loads(self.path_for(batch_id).read_text(encoding='utf-8'))
        payload.setdefault('max_workers', 1)
        prior_version = int(payload.get('schema_version', 1))
        payload['schema_version'] = max(prior_version, 3)
        lectures = payload.get('lectures', [])
        for item in lectures:
            item.setdefault('worker_id', None)
            item.setdefault('claimed_at', None)
            if not item.get('job_id'):
                identity = f"{batch_id}\0{item.get('source_path', '')}".encode('utf-8')
                item['job_id'] = hashlib.sha256(identity).hexdigest()[:24]
        payload['lectures'] = [LectureState(**item) for item in lectures]
        return BatchManifest(**payload)

    def discard(self, batch_id):
        with _worker_locks_guard:
            lock = _worker_locks.setdefault(batch_id, Lock())
            if not lock.acquire(blocking=False):
                raise RuntimeError('Stop the active queue before discarding it.')
            try:
                self.path_for(batch_id).unlink(missing_ok=True)
            finally:
                lock.release()

    def unfinished(self):
        if not self.root.is_dir():
            return []
        manifests = []
        for path in self.root.glob('*.json'):
            try:
                item = self.load(path.stem)
                changed = False
                if item.control_state == 'running':
                    # A previous process exited before the queue returned to an
                    # idle state. Recovery is always explicit from the UI.
                    item.control_state = 'stopped'
                    changed = True
                for lecture in item.lectures:
                    if lecture.status in ACTIVE_STATUSES:
                        item.control_state = 'stopped'
                        if lecture.lecture_submission_state in {'unknown', 'response_validated'}:
                            lecture.status = BatchStatus.INTERRUPTED
                            lecture.last_error = 'The app closed after lecture submission or during exam saving. Review before retrying.'
                        else:
                            lecture.status = BatchStatus.PENDING
                            lecture.stage = 'not_started'
                        changed = True
                    try:
                        current = input_fingerprint(lecture.source_path)
                    except OSError:
                        if lecture.status != BatchStatus.ATTENTION:
                            lecture.status = BatchStatus.ATTENTION
                            lecture.last_error = 'The source lecture is missing or unreadable.'
                            changed = True
                        continue
                    if current != lecture.fingerprint:
                        lecture.fingerprint = current
                        lecture.job_id = uuid.uuid4().hex
                        lecture.worker_id = None
                        lecture.claimed_at = None
                        lecture.validation_diagnostics = {}
                        lecture.status = BatchStatus.PENDING
                        lecture.attempt_count = 0
                        lecture.retry_count = 0
                        lecture.last_error = 'The source lecture changed; it is queued for a fresh attempt.'
                        lecture.last_attempted_at = None
                        lecture.next_retry_at = None
                        lecture.output_path = None
                        lecture.question_count = None
                        lecture.title = ''
                        lecture.stage = 'not_started'
                        lecture.lecture_submission_state = 'not_started'
                        changed = True
                    elif lecture.status == BatchStatus.COMPLETED:
                        if not lecture.output_path or not Path(lecture.output_path).is_file():
                            lecture.status = BatchStatus.ATTENTION
                            lecture.last_error = 'The saved exam is missing; explicitly start a new attempt to regenerate it.'
                            changed = True
                if changed:
                    self.save(item)
                if any(x.status != BatchStatus.COMPLETED for x in item.lectures):
                    manifests.append(item)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                continue
        return sorted(manifests, key=lambda x: x.updated_at, reverse=True)


class BatchJobClaimer:
    """Serialize durable job claims for workers sharing one in-process batch."""

    ELIGIBLE = {BatchStatus.PENDING, BatchStatus.RETRY, BatchStatus.FAILED_TEMP}

    def __init__(self, manifest, store):
        self.manifest = manifest
        self.store = store

    def claim_next(self, worker_id, *, candidates=None):
        """Atomically claim the first eligible job, persisting ownership before return."""
        with _job_claim_lock(self.manifest.batch_id):
            if self.manifest.control_state != 'running':
                return None
            limit = _bounded_worker_count(self.manifest.max_workers)
            active = sum(item.status in ACTIVE_STATUSES for item in self.manifest.lectures)
            if active >= limit:
                return None
            pool = self.manifest.lectures if candidates is None else candidates
            for item in pool:
                if item not in self.manifest.lectures or item.status not in self.ELIGIBLE:
                    continue
                if item.next_retry_at and not _is_due(item.next_retry_at):
                    continue
                item.attempt_count += 1
                item.last_attempted_at = _utc_now()
                item.next_retry_at = None
                item.last_error = ''
                item.status = BatchStatus.OPENING
                item.stage = 'opening'
                item.lecture_submission_state = 'not_started'
                item.worker_id = str(worker_id)
                item.claimed_at = _utc_now()
                self.store.save(self.manifest)
                return item
            return None


def _bounded_worker_count(value):
    try:
        count = int(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError('max_workers must be 1 or 2.')
    if count < 1 or count > MAX_PREPARED_WORKERS:
        raise ValueError('max_workers must be 1 or 2.')
    return count


def create_manifest(folder, prompt_path, reference_path, model, thinking, output_folder,
                    conflict_policy='save_copy', store=None, *, template_id='', template='',
                    max_workers=1):
    paths = discover_lecture_pdfs(folder)
    if not paths:
        raise ValueError('The selected folder contains no lecture PDFs.')
    lectures = []
    for path in paths:
        try:
            fingerprint = input_fingerprint(path)
            status, error = BatchStatus.PENDING, ''
        except OSError:
            fingerprint = {'size': None, 'mtime_ns': None, 'sha256': ''}
            status, error = BatchStatus.ATTENTION, 'The source lecture is unreadable.'
        lectures.append(LectureState(str(path), path.name, fingerprint, status=status, last_error=error))
    now = _utc_now()
    prompt_path = str(Path(prompt_path).resolve())
    reference_path = str(Path(reference_path).resolve())
    output_folder = str(Path(output_folder).resolve())
    manifest = BatchManifest(
        batch_id=datetime.now().strftime('%Y%m%d_%H%M%S_%f'),
        folder=str(Path(folder).resolve()), prompt_path=prompt_path,
        reference_path=reference_path, model=model, thinking=thinking,
        output_folder=output_folder, conflict_policy=conflict_policy,
        created_at=now, updated_at=now, lectures=lectures,
        max_workers=_bounded_worker_count(max_workers),
        template_id=str(template_id or ''),
        configuration_fingerprint=configuration_fingerprint(
            prompt_path=prompt_path, reference_path=reference_path, model=model,
            thinking=thinking, output_folder=output_folder,
            conflict_policy=conflict_policy, template_id=template_id, template=template,
        ),
    )
    (store or BatchStateStore()).save(manifest)
    return manifest


def manifest_matches_configuration(manifest, *, prompt_path, reference_path, model,
                                  thinking, output_folder, conflict_policy='save_copy',
                                  template_id='', template=''):
    current = configuration_fingerprint(
        prompt_path=prompt_path, reference_path=reference_path, model=model,
        thinking=thinking, output_folder=output_folder,
        conflict_policy=conflict_policy, template_id=template_id, template=template,
    )
    return bool(manifest.configuration_fingerprint) and current == manifest.configuration_fingerprint


class GoogleAIStudioBatchController:
    """One browser process, one page and one lecture request at a time."""

    def __init__(self, manifest, *, browser_manager, template, template_entry, store=None,
                 event_callback=None, cancellation=None, pause_event=None):
        self.manifest = manifest
        self.browser_manager = browser_manager
        self.template = template
        self.template_entry = dict(template_entry)
        self.store = store or BatchStateStore()
        self.event_callback = event_callback or (lambda *args: None)
        self.cancellation = cancellation
        self.pause_event = pause_event
        self._lease = None
        self.worker_id = f'worker-{uuid.uuid4().hex[:12]}'
        self.max_workers = _bounded_worker_count(getattr(manifest, 'max_workers', 1))
        self.job_claimer = BatchJobClaimer(manifest, self.store)

    def _pool_event(self, status, detail=''):
        """Emit safe worker identity and pool lifecycle messages through existing logs."""
        self.event_callback('Worker Pool', status, detail)

    def _event(self, lecture, status, detail=''):
        self.event_callback(lecture, status, detail)

    def _record_post_generation_stage(self, item, stage, metadata):
        """Persist safe response-processing milestones without retaining quiz text."""
        item.stage = stage
        if stage == 'lecture_response_captured':
            item.lecture_submission_state = 'response_received'
        if stage == 'lecture_response_captured':
            detail = f"Final answer captured ({int(metadata.get('response_length', 0))} characters)."
        elif stage == 'json_candidate_extracted':
            detail = f"JSON array extracted ({int(metadata.get('candidate_count', 0))} candidate)."
        elif stage == 'json_candidate_extraction_failed':
            detail = 'No complete JSON array was extracted.'
        elif stage == 'quiz_validation_started':
            detail = 'Checking the response with MCQ Maker validation.'
        elif stage == 'quiz_json_syntax_repaired':
            detail = 'A safe local JSON quote escape was applied; validating the repaired response.'
        elif stage == 'quiz_validation_failed':
            error_class = str(metadata.get('error_class', 'ValidationError'))
            if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,79}', error_class):
                error_class = 'ValidationError'
            category = str(metadata.get('error_category', 'validation_contract'))
            if not re.fullmatch(r'[a-z_]{1,40}', category):
                category = 'validation_contract'
            issue_count = max(1, min(100, int(metadata.get('issue_count', 1))))
            detail = f'Quiz validation failed ({error_class}; {category}; {issue_count} issue(s)).'
            if category == 'json_syntax':
                parser_class = str(metadata.get('parser_exception_class', 'JSONDecodeError'))
                if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,79}', parser_class):
                    parser_class = 'JSONDecodeError'
                parser_message = str(metadata.get('parser_message', 'JSON parser reported a syntax error.'))
                parser_message = ''.join(char for char in parser_message if char.isprintable())[:120]
                if not parser_message:
                    parser_message = 'JSON parser reported a syntax error.'
                safe = {'failure_category': category, 'parser_exception_class': parser_class,
                        'parser_message': parser_message if category == 'json_syntax' else '',
                        'issue_count': issue_count,
                        'candidate_length': self._bounded_diagnostic_integer(metadata, 'candidate_length'),
                        'candidate_count': self._bounded_diagnostic_integer(metadata, 'candidate_count', maximum=20)}
                for key in ('line', 'column', 'position') if category == 'json_syntax' else ():
                    if metadata.get(key) is not None:
                        safe[key] = self._bounded_diagnostic_integer(metadata, key)
                item.validation_diagnostics = safe
                location = ''
                if safe.get('line') is not None and safe.get('column') is not None:
                    location = f"line {safe['line']}, column {safe['column']} — "
                if category == 'json_syntax':
                    detail = f'Invalid JSON returned by AI Studio: {location}{parser_message}.'
        elif stage == 'quiz_validation_succeeded':
            count = int(metadata.get('question_count', 0))
            noun = 'question' if count == 1 else 'questions'
            detail = f'Quiz validation passed ({count} {noun}).'
        elif stage in {'quiz_json_correction_started', 'quiz_json_correction_succeeded',
                       'quiz_json_correction_failed'}:
            attempt = self._bounded_diagnostic_integer(metadata, 'correction_attempt', maximum=1)
            result = str(metadata.get('correction_result', 'in_progress'))
            if stage == 'quiz_json_correction_started':
                result = 'in_progress'
                detail = f'JSON syntax correction requested ({attempt} of 1).'
            elif stage == 'quiz_json_correction_succeeded':
                result = 'succeeded'
                detail = 'JSON syntax correction was validated.'
            else:
                result = 'failed'
                detail = 'The single JSON syntax correction did not produce a usable quiz.'
            diagnostics = dict(item.validation_diagnostics or {})
            if diagnostics.get('failure_category') == 'json_syntax':
                diagnostics.update({'correction_attempt': attempt,
                                    'correction_attempted': True,
                                    'correction_result': result})
                item.validation_diagnostics = diagnostics
            if stage == 'quiz_json_correction_started':
                original = item.last_error
                item.last_error = (f'{original} Correction attempt {attempt} of 1 is in progress.'
                                   if original else detail)[:240]
            elif stage == 'quiz_json_correction_failed' and item.validation_diagnostics.get('failure_category') == 'json_syntax':
                safe = item.validation_diagnostics
                location = (f"line {safe['line']}, column {safe['column']} — "
                            if safe.get('line') is not None and safe.get('column') is not None else '')
                item.last_error = (f"Invalid JSON returned by AI Studio: {location}{safe.get('parser_message', 'JSON parser reported a syntax error.')}. "
                                   'The single correction attempt failed.')[:240]
        else:
            detail = 'Post-generation processing continued.'
        if stage in {'json_candidate_extraction_failed', 'quiz_validation_failed'}:
            item.last_error = f'{stage}: {detail}'[:240]
        self.store.save(self.manifest)
        self._event(item.file_name, item.status, detail)

    @staticmethod
    def _bounded_diagnostic_integer(metadata, key, maximum=5 * 1024 * 1024):
        try:
            return max(0, min(maximum, int(metadata.get(key, 0))))
        except (TypeError, ValueError, OverflowError):
            return 0

    async def _wait_until_due(self, timestamp):
        while timestamp and not _is_due(timestamp):
            if self.cancellation and self.cancellation.is_set():
                return False
            if self.pause_event and self.pause_event.is_set():
                return False
            try:
                due = datetime.fromisoformat(timestamp)
                delay = max(0.1, min(1.0, (due - datetime.now(timezone.utc)).total_seconds()))
            except (TypeError, ValueError):
                return True
            await asyncio.sleep(delay)
        return True

    def _pause_queued_work(self, items):
        """Persist a pause after the current job, without claiming another."""
        queued_ids = {id(item) for item in items}
        changed = []
        for item in self.manifest.lectures:
            if (id(item) in queued_ids or item.status in {
                    BatchStatus.PENDING, BatchStatus.RETRY, BatchStatus.FAILED_TEMP,
                    BatchStatus.CANCELLED, BatchStatus.PAUSED,
            }) and item.status not in {
                    BatchStatus.COMPLETED, BatchStatus.ATTENTION, BatchStatus.INTERRUPTED,
            }:
                if item.status != BatchStatus.PAUSED:
                    item.status = BatchStatus.PAUSED
                    changed.append(item)
        self.manifest.control_state = 'paused'
        self.store.save(self.manifest)
        for item in changed:
            self._event(item.file_name, item.status, 'Paused after the current lecture; progress is saved.')

    def _stop_queued_work(self):
        """Persist Stop without retrying work whose send outcome is uncertain."""
        for item in self.manifest.lectures:
            if item.status not in {BatchStatus.COMPLETED, BatchStatus.ATTENTION, BatchStatus.INTERRUPTED}:
                item.status = BatchStatus.CANCELLED
        self.manifest.control_state = 'stopped'
        self.store.save(self.manifest)

    def _changed(self, item):
        try:
            return input_fingerprint(item.source_path) != item.fingerprint
        except OSError:
            return True

    def _acquire_lease(self):
        with _worker_locks_guard:
            lock = _worker_locks.setdefault(self.manifest.batch_id, Lock())
            if not lock.acquire(blocking=False):
                raise RuntimeError('This saved queue is already being processed.')
        self._lease = lock

    def _release_lease(self):
        if self._lease:
            self._lease.release()
            self._lease = None

    @staticmethod
    def _safe_error(exc):
        # Keep diagnostics useful without ever persisting prompts or responses.
        message = str(exc).strip()
        if not message or len(message) > 240:
            return type(exc).__name__
        return message.replace('\r', ' ').replace('\n', ' ')

    def _next_delay(self, retry_count):
        base = min(300, 15 * (2 ** max(0, retry_count - 1)))
        return random.uniform(base, min(300, base * 1.25))

    def _schedule_safe_retry(self, item, reason):
        item.retry_count += 1
        item.last_error = reason
        if item.retry_count >= MAX_PRE_SUBMISSION_ATTEMPTS:
            item.status = BatchStatus.ATTENTION
            item.last_error = f'Safe retry limit reached: {reason}'[:240]
            item.next_retry_at = None
            return
        delay = self._next_delay(item.retry_count)
        item.status = BatchStatus.FAILED_TEMP
        item.next_retry_at = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat(timespec='seconds')

    def _handle_provider_status(self, item, exc):
        """A response status follows a submission; never automatically resend it."""
        match = re.search(r'HTTP\s+(\d{3})', str(exc), flags=re.IGNORECASE)
        if not match:
            if item.lecture_submission_state == 'unknown':
                item.status = BatchStatus.INTERRUPTED
                item.last_error = 'AI Studio response status was not correlated; verify the conversation before retrying.'
            else:
                item.status = BatchStatus.ATTENTION
                item.last_error = 'Calibration response status was not correlated. Review this conversation before retrying.'
            item.next_retry_at = None
            return
        status = int(match.group(1))
        item.status = BatchStatus.ATTENTION
        item.lecture_submission_state = 'response_rejected'
        item.last_error = (
            f'AI Studio rejected the submitted request with HTTP {status}. '
            'No automatic resend was attempted; review before starting a new attempt.'
        )
        item.next_retry_at = None

    def _prepare_for_explicit_resume(self):
        for item in self.manifest.lectures:
            if item.status in {BatchStatus.CANCELLED, BatchStatus.PAUSED}:
                item.status = BatchStatus.PENDING
            elif item.status == BatchStatus.INTERRUPTED:
                # Calling Resume after reviewing the warning is the explicit
                # recovery decision for a request with unknown submission outcome.
                item.status = BatchStatus.PENDING
                item.lecture_submission_state = 'not_started'
                item.last_error = 'Retry explicitly requested after an interrupted attempt.'
            elif item.status == BatchStatus.FAILED_TEMP and item.retry_count >= MAX_PRE_SUBMISSION_ATTEMPTS:
                item.status = BatchStatus.ATTENTION
                item.last_error = 'The safe retry limit was reached. Review the local or network issue before starting a new batch.'
        self.manifest.control_state = 'running'

    async def run(self):
        self._acquire_lease()
        try:
            return await self._run_owned()
        finally:
            self._release_lease()

    async def _run_owned(self):
        if self.max_workers == 2:
            return await self._run_parallel_owned()
        manager = self.browser_manager
        try:
            prompt_path = Path(self.manifest.prompt_path)
            reference = Path(self.manifest.reference_path)
            if not prompt_path.is_file():
                self._mark_unfinished_attention('The saved prompt file is no longer available.')
                return self.manifest
            if not reference.is_file():
                self._mark_unfinished_attention('The reference file is no longer available.')
                return self.manifest
            try:
                prompt = prompt_path.read_text(encoding='utf-8-sig')
            except (OSError, UnicodeError):
                self._mark_unfinished_attention('The saved prompt file cannot be read as text.')
                return self.manifest
            self._prepare_for_explicit_resume()
            self.store.save(self.manifest)
            self._pool_event('Worker started', f'worker_id={self.worker_id}; max_workers={self.max_workers}')
            items = list(self.manifest.lectures)
            while items and not (self.cancellation and self.cancellation.is_set()):
                if self.pause_event and self.pause_event.is_set():
                    self._pause_queued_work(items)
                    items.clear()
                    break
                item = items.pop(0)
                if item.status == BatchStatus.COMPLETED:
                    if not self._changed(item) and item.output_path and Path(item.output_path).is_file():
                        continue
                    item.status = BatchStatus.ATTENTION
                    item.last_error = 'Completed output or source no longer matches. Review before regeneration.'
                    self.store.save(self.manifest)
                    continue
                if self._changed(item):
                    try:
                        item.fingerprint = input_fingerprint(item.source_path)
                    except OSError:
                        item.status = BatchStatus.ATTENTION
                        item.last_error = 'The source lecture is missing or unreadable.'
                        self.store.save(self.manifest)
                        self._event(item.file_name, item.status, item.last_error)
                        continue
                    item.status = BatchStatus.PENDING
                    item.job_id = uuid.uuid4().hex
                    item.worker_id = None
                    item.claimed_at = None
                    item.validation_diagnostics = {}
                    item.attempt_count = 0
                    item.retry_count = 0
                    item.output_path = None
                    item.question_count = None
                    item.title = ''
                if item.status == BatchStatus.ATTENTION:
                    continue
                if item.status == BatchStatus.INTERRUPTED:
                    continue
                if item.next_retry_at and not await self._wait_until_due(item.next_retry_at):
                    if self.cancellation and self.cancellation.is_set():
                        item.status = BatchStatus.CANCELLED
                        self.store.save(self.manifest)
                    elif self.pause_event and self.pause_event.is_set():
                        self._pause_queued_work([item, *items])
                        items.clear()
                    break
                if self.pause_event and self.pause_event.is_set():
                    self._pause_queued_work([item, *items])
                    items.clear()
                    break
                if self.cancellation and self.cancellation.is_set():
                    item.status = BatchStatus.CANCELLED
                    self.store.save(self.manifest)
                    break
                claimed = self.job_claimer.claim_next(self.worker_id, candidates=(item,))
                if claimed is None:
                    continue
                self._pool_event('Job claimed', f'worker_id={self.worker_id}; job_id={claimed.job_id}; lecture={claimed.file_name}')
                await self._process_one(claimed, manager, prompt, reference)
                if claimed.status == BatchStatus.COMPLETED:
                    self._pool_event('Job completed', f'worker_id={self.worker_id}; job_id={claimed.job_id}')
                elif claimed.status == BatchStatus.INTERRUPTED:
                    self._pool_event('Job interrupted', f'worker_id={self.worker_id}; job_id={claimed.job_id}')
                else:
                    self._pool_event('Job failed', f'worker_id={self.worker_id}; job_id={claimed.job_id}; status={claimed.status}')
                if item.status == BatchStatus.FAILED_TEMP and item.retry_count < MAX_PRE_SUBMISSION_ATTEMPTS:
                    items.append(item)
        finally:
            try:
                await manager.shutdown()
            finally:
                self._pool_event('Worker stopped', f'worker_id={self.worker_id}')
        if self.cancellation and self.cancellation.is_set():
            self._pool_event('Stop requested', f'worker_id={self.worker_id}')
        elif self.pause_event and self.pause_event.is_set():
            self._pool_event('Pause requested', f'worker_id={self.worker_id}; current job finished safely')
        else:
            self._pool_event('No eligible jobs remaining', f'worker_id={self.worker_id}')
        if all(item.status == BatchStatus.COMPLETED for item in self.manifest.lectures):
            self.manifest.control_state = 'completed'
            self.store.save(self.manifest)
        elif self.cancellation and self.cancellation.is_set():
            self._stop_queued_work()
        elif self.pause_event and self.pause_event.is_set():
            self._pause_queued_work([])
        else:
            self.manifest.control_state = 'idle'
            self.store.save(self.manifest)
        return self.manifest

    async def _run_parallel_owned(self):
        """Experimental two-tab execution; each task owns one claimed job page."""
        manager = self.browser_manager
        try:
            prompt_path, reference = Path(self.manifest.prompt_path), Path(self.manifest.reference_path)
            if not prompt_path.is_file() or not reference.is_file():
                self._mark_unfinished_attention('The saved prompt or reference file is no longer available.')
                return self.manifest
            try:
                prompt = prompt_path.read_text(encoding='utf-8-sig')
            except (OSError, UnicodeError):
                self._mark_unfinished_attention('The saved prompt file cannot be read as text.')
                return self.manifest
            self._prepare_for_explicit_resume()
            self.store.save(self.manifest)
            self._pool_event('Parallel pool starting',
                             f'batch_id={self.manifest.batch_id}; max_workers=2; workers=2')
            tasks = [asyncio.create_task(self._parallel_worker(index + 1, manager, prompt, reference))
                     for index in range(2)]
            await asyncio.gather(*tasks)
        finally:
            await manager.shutdown()
        if self.cancellation and self.cancellation.is_set():
            self._stop_queued_work()
        elif self.pause_event and self.pause_event.is_set():
            self._pause_queued_work([])
        elif all(item.status == BatchStatus.COMPLETED for item in self.manifest.lectures):
            self.manifest.control_state = 'completed'
            self.store.save(self.manifest)
        else:
            self.manifest.control_state = 'idle'
            self.store.save(self.manifest)
        return self.manifest

    async def _parallel_worker(self, slot, manager, prompt, reference):
        worker_id = f'{self.worker_id}-tab-{slot}'
        self._pool_event('Worker task started',
                         f'batch_id={self.manifest.batch_id}; worker_id={worker_id}; max_workers=2')
        try:
            while not (self.cancellation and self.cancellation.is_set()) and not (self.pause_event and self.pause_event.is_set()):
                self._pool_event('Claim attempt', f'worker_id={worker_id}')
                claimed = self.job_claimer.claim_next(worker_id)
                if claimed is None:
                    self._pool_event('No job claimed', f'worker_id={worker_id}; no eligible job remains')
                    return
                self._pool_event('Job claimed', f'worker_id={worker_id}; job_id={claimed.job_id}; lecture={claimed.file_name}')
                await self._process_one(claimed, manager, prompt, reference,
                                        isolated_page=True, worker_id=worker_id)
                outcome = 'Job completed' if claimed.status == BatchStatus.COMPLETED else 'Job failed'
                self._pool_event(outcome, f'worker_id={worker_id}; job_id={claimed.job_id}; status={claimed.status}')
        except Exception as exc:
            self._pool_event('Worker exception',
                             f'worker_id={worker_id}; {type(exc).__name__}: {self._safe_error(exc)}')
            raise
        finally:
            self._pool_event('Worker task ended', f'worker_id={worker_id}')

    async def _process_one(self, item, manager, prompt, reference, *, isolated_page=False, worker_id=None):
        # BatchJobClaimer durably records the lease before this worker opens a session.
        self._event(item.file_name, item.status)
        session = (AIStudioConversationSession(manager, isolated_page=True)
                   if isolated_page else AIStudioConversationSession(manager))
        session.diagnostic_callback = lambda stage, metadata: self._record_session_stage(
            item, worker_id or self.worker_id, stage, metadata
        )
        try:
            await session.discover_capabilities()
            item.status = BatchStatus.CONFIGURING; item.stage = 'configuring'
            self.store.save(self.manifest); self._event(item.file_name, item.status)
            await session.configure_defaults(model=self.manifest.model, thinking_level=self.manifest.thinking)
            item.status = BatchStatus.CALIBRATING; item.stage = 'calibrating'
            self.store.save(self.manifest); self._event(item.file_name, item.status)
            await session.run_calibration(prompt, reference)
            item.status = BatchStatus.READY; item.stage = 'calibration_ready'
            self.store.save(self.manifest); self._event(item.file_name, item.status)
            item.status = BatchStatus.UPLOADING; item.stage = 'uploading_lecture'
            self.store.save(self.manifest); self._event(item.file_name, item.status)
            item.status = BatchStatus.GENERATING; item.stage = 'lecture_submission_unknown'
            item.lecture_submission_state = 'unknown'
            self.store.save(self.manifest); self._event(item.file_name, item.status)
            quiz = await session.generate_lecture(item.source_path)
            item.lecture_submission_state = 'response_validated'
            item.stage = 'saving'
            item.status = BatchStatus.VALIDATING
            self.store.save(self.manifest); self._event(item.file_name, item.status)
            item.status = BatchStatus.SAVING
            self.store.save(self.manifest); self._event(item.file_name, item.status)
            output = save_exam(self.template, quiz, Path(self.manifest.output_folder),
                               overwrite=self.manifest.conflict_policy == 'overwrite')
            item.stage = 'verifying_saved_html'
            self.store.save(self.manifest)
            try:
                output_size = output.stat().st_size if output.is_file() else 0
                saved_html = output.read_text(encoding='utf-8') if output_size else ''
            except (OSError, UnicodeError) as exc:
                raise OSError('The saved HTML exam could not be read back from disk.') from exc
            expected_payload = f'const quizData = {serialize_payload(quiz.payload)};'
            if output_size == 0 or expected_payload not in saved_html:
                raise OSError('The saved HTML exam did not contain the validated quiz data.')
            item.output_path = str(output.resolve())
            item.question_count = len(quiz.questions)
            item.title = quiz.title
            item.status = BatchStatus.COMPLETED
            item.stage = 'completed'
            item.lecture_submission_state = 'saved'
            item.last_error = ''
            item.next_retry_at = None
            self.store.save(self.manifest)
            self._event(item.file_name, item.status, f'{output.resolve()} ({output_size} bytes)')
        except (FileNotFoundError, PermissionError, IsADirectoryError) as exc:
            item.status = BatchStatus.ATTENTION
            item.last_error = f'{item.stage}: {self._safe_error(exc)}'[:240]
            self.store.save(self.manifest)
            self._event(item.file_name, item.status, item.last_error)
        except (AIStudioExtractionError, AIStudioValidationError, AIStudioCalibrationError,
                AIStudioAuthenticationRequired, AIStudioUploadError) as exc:
            item.status = BatchStatus.ATTENTION
            if not item.last_error:
                item.last_error = f'{item.stage}: {type(exc).__name__}: {self._safe_error(exc)}'[:240]
            elif item.stage not in {'json_candidate_extraction_failed', 'quiz_validation_failed'}:
                item.last_error = f'{item.stage}: {type(exc).__name__}: {item.last_error}'[:240]
            self.store.save(self.manifest)
            self._event(item.file_name, item.status, item.last_error)
        except AIStudioResponseError as exc:
            self._handle_provider_status(item, exc)
            self.store.save(self.manifest)
            detail = item.last_error
            if item.next_retry_at:
                detail = f'{detail} · Next eligible retry: {item.next_retry_at}'
            self._event(item.file_name, item.status, detail)
        except Exception as exc:
            item.last_error = f'{item.stage}: {type(exc).__name__}: {self._safe_error(exc)}'[:240]
            if item.lecture_submission_state == 'unknown':
                item.status = BatchStatus.INTERRUPTED
                item.last_error = 'The lecture request outcome is unknown. Review the AI Studio conversation before choosing Resume.'
                item.next_retry_at = None
            elif isinstance(exc, (AIStudioConnectionError, AIStudioInitializationError, TimeoutError, ConnectionError)):
                self._schedule_safe_retry(item, item.last_error)
            else:
                item.status = BatchStatus.ATTENTION
                item.next_retry_at = None
            self.store.save(self.manifest)
            detail = item.last_error
            if item.next_retry_at:
                detail = f'{detail} · Next eligible retry: {item.next_retry_at}'
            self._event(item.file_name, item.status, detail)
        finally:
            if isolated_page:
                await session.close()

    def _record_session_stage(self, item, worker_id, stage, metadata):
        """Surface safe per-worker milestones without storing request or quiz text."""
        if stage in {
                'lecture_response_captured', 'json_candidate_extracted',
                'json_candidate_extraction_failed', 'quiz_validation_started',
                'quiz_json_syntax_repaired', 'quiz_validation_failed',
                'quiz_validation_succeeded',
        }:
            self._record_post_generation_stage(item, stage, metadata)
            return
        page_identity = metadata.get('page_identity')
        page_detail = f'; page={page_identity}' if page_identity else ''
        self._pool_event('AI Studio stage',
                         f'worker_id={worker_id}; job_id={item.job_id}; stage={stage}{page_detail}')

    def _mark_unfinished_attention(self, message):
        for item in self.manifest.lectures:
            if item.status != BatchStatus.COMPLETED:
                item.status = BatchStatus.ATTENTION
                item.last_error = message
                item.next_retry_at = None
        self.store.save(self.manifest)
        for item in self.manifest.lectures:
            if item.status == BatchStatus.ATTENTION:
                self._event(item.file_name, item.status, message)


def _is_due(timestamp):
    try:
        return datetime.fromisoformat(timestamp) <= datetime.now(timezone.utc)
    except (TypeError, ValueError):
        return True
