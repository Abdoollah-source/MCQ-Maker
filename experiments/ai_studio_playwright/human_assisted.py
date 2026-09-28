"""Single-lecture Google AI Studio workflow experiment.

The experiment uses the dedicated Brave profile, sends each turn through the
normal AI Studio prompt, and saves validated output through MCQ Maker's pipeline.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import json
from pathlib import Path
import re
import sys
import time
from contextlib import asynccontextmanager


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mcq_maker.ai_studio_browser import AI_STUDIO_NEW_CHAT_URL, AIStudioBrowserManager
from mcq_maker.ai_studio_errors import (
    AIStudioAuthenticationRequired,
    AIStudioCalibrationError,
    AIStudioCancelledError,
    AIStudioExtractionError,
    AIStudioGenerationTimeoutError,
    AIStudioResponseError,
    AIStudioUploadError,
    AIStudioValidationError,
)
from mcq_maker.ai_studio_selectors import (
    AIStudioLocators, AttachmentReady, normalize_assistant_response_text,
)
from mcq_maker.ai_studio_session import AIStudioConversationSession
from mcq_maker.exam_generator import save_exam
from mcq_maker.quiz_validation import QuizError, validate_quiz
from mcq_maker.template_validation import TemplateError
from tools.ai_studio_waa_diagnostic import WaaDiagnosticRecorder


DEFAULT_PROMPT = PROJECT_ROOT / 'Pompts' / 'PROMPT(MCQ-MAKER).txt'
DEFAULT_REFERENCE = PROJECT_ROOT / 'References' / 'refrence.txt'
DEFAULT_LECTURE = PROJECT_ROOT / 'References' / '1- Lecture-1.pdf'
OUTPUT_DIR = Path(__file__).resolve().parent / 'human_assisted_output'
DIAGNOSTIC_DIR = Path(__file__).resolve().parent / 'human_assisted_artifacts'
GENERATION_REQUEST = (
    'Generate the MCQ Maker quiz for the attached lecture now. '
    'Return one complete JSON array only.'
)
ERROR_TEXT = re.compile(
    r'permission denied|an internal error has occurred|failed to generate|'
    r'\bquota\b(?:\s+(?:exceeded|exhausted|limit))?|\brate[- ]?limit(?:ed|ing)?\b',
    re.I,
)


class ExperimentState(str, Enum):
    NEW = 'new'
    PREPARING = 'preparing_playground'
    CALIBRATION_PREPARED = 'calibration_prepared'
    WAITING_CALIBRATION = 'waiting_for_calibration_response'
    CALIBRATION_RUNNING = 'calibration_running'
    CALIBRATION_COMPLETE = 'calibration_complete'
    LECTURE_UPLOAD_PENDING = 'lecture_upload_pending'
    LECTURE_UPLOADING = 'lecture_uploading'
    LECTURE_UPLOADED = 'lecture_uploaded'
    LECTURE_READY_TO_SEND = 'lecture_ready_to_send'
    WAITING_LECTURE = 'waiting_for_lecture_response'
    LECTURE_RUNNING = 'lecture_running'
    JSON_RECEIVED = 'json_received'
    VALIDATING = 'validating_json'
    SAVING = 'saving_exam'
    EXAM_SAVED = 'exam_saved'
    COMPLETE = 'complete'
    CANCELLED = 'cancelled'
    FAILED = 'failed'


ALLOWED_TRANSITIONS = {
    ExperimentState.NEW: {ExperimentState.PREPARING, ExperimentState.CANCELLED},
    ExperimentState.PREPARING: {
        ExperimentState.CALIBRATION_PREPARED, ExperimentState.FAILED, ExperimentState.CANCELLED,
    },
    ExperimentState.CALIBRATION_PREPARED: {
        ExperimentState.WAITING_CALIBRATION, ExperimentState.FAILED, ExperimentState.CANCELLED,
    },
    ExperimentState.WAITING_CALIBRATION: {
        ExperimentState.CALIBRATION_RUNNING, ExperimentState.FAILED, ExperimentState.CANCELLED,
    },
    ExperimentState.CALIBRATION_RUNNING: {
        ExperimentState.CALIBRATION_COMPLETE, ExperimentState.FAILED, ExperimentState.CANCELLED,
    },
    ExperimentState.CALIBRATION_COMPLETE: {
        ExperimentState.LECTURE_UPLOAD_PENDING, ExperimentState.FAILED, ExperimentState.CANCELLED,
    },
    ExperimentState.LECTURE_UPLOAD_PENDING: {
        ExperimentState.LECTURE_UPLOADING, ExperimentState.FAILED, ExperimentState.CANCELLED,
    },
    ExperimentState.LECTURE_UPLOADING: {
        ExperimentState.LECTURE_UPLOADED, ExperimentState.FAILED, ExperimentState.CANCELLED,
    },
    ExperimentState.LECTURE_UPLOADED: {
        ExperimentState.LECTURE_READY_TO_SEND, ExperimentState.FAILED, ExperimentState.CANCELLED,
    },
    ExperimentState.LECTURE_READY_TO_SEND: {
        ExperimentState.WAITING_LECTURE, ExperimentState.FAILED, ExperimentState.CANCELLED,
    },
    ExperimentState.WAITING_LECTURE: {
        ExperimentState.LECTURE_RUNNING, ExperimentState.FAILED, ExperimentState.CANCELLED,
    },
    ExperimentState.LECTURE_RUNNING: {
        ExperimentState.JSON_RECEIVED, ExperimentState.FAILED, ExperimentState.CANCELLED,
    },
    ExperimentState.JSON_RECEIVED: {ExperimentState.VALIDATING, ExperimentState.FAILED, ExperimentState.CANCELLED},
    ExperimentState.VALIDATING: {
        ExperimentState.SAVING, ExperimentState.FAILED, ExperimentState.CANCELLED,
    },
    ExperimentState.SAVING: {
        ExperimentState.EXAM_SAVED, ExperimentState.FAILED, ExperimentState.CANCELLED,
    },
    ExperimentState.EXAM_SAVED: {ExperimentState.COMPLETE, ExperimentState.FAILED, ExperimentState.CANCELLED},
    ExperimentState.COMPLETE: set(),
    ExperimentState.CANCELLED: set(),
    ExperimentState.FAILED: set(),
}


@dataclass(frozen=True)
class TurnObservation:
    response_text: str
    http_status: int | None
    stop_control_seen: bool


@dataclass(frozen=True)
class QuizArraySelection:
    text: str
    quiz: object
    diagnostics: tuple[dict, ...]


class QuizArrayExtractionError(AIStudioExtractionError):
    """A safe extraction outcome with content-free candidate diagnostics."""

    def __init__(self, reason, diagnostics=()):
        self.reason = reason
        self.diagnostics = tuple(diagnostics)
        messages = {
            'no_array': 'The final assistant response contained no JSON array.',
            'incomplete_array': 'The final assistant response contained an opening array without a matching closing bracket.',
            'invalid_json': 'A balanced array candidate contained invalid JSON syntax.',
            'invalid_schema': 'No complete array candidate passed MCQ Maker quiz validation.',
            'ambiguous': 'More than one distinct array candidate passed MCQ Maker quiz validation.',
        }
        super().__init__(messages.get(reason, 'The final assistant response could not be safely extracted.'))


class QuizCandidateValidationError(AIStudioValidationError):
    """Balanced JSON candidates were found, but none met the existing quiz schema."""

    def __init__(self, diagnostics=()):
        self.reason = 'invalid_schema'
        self.diagnostics = tuple(diagnostics)
        super().__init__('No complete array candidate passed MCQ Maker quiz validation.')


class ExamSaveError(RuntimeError):
    """The validated quiz could not be saved through MCQ Maker's exam pipeline."""


class StagePerformanceTrace:
    """Keep stage names and elapsed time only; never store page or file content."""

    def __init__(self, callback=None):
        self.records = []
        self.callback = callback

    def record(self, stage, elapsed):
        item = {'stage': str(stage), 'elapsed_seconds': round(max(0.0, float(elapsed)), 3)}
        self.records.append(item)
        if callable(self.callback):
            self.callback(f"[timing:{item['stage']}:{item['elapsed_seconds']:.3f}s]")
        return item

    @asynccontextmanager
    async def measure(self, stage):
        started = time.perf_counter()
        try:
            yield
        finally:
            self.record(stage, time.perf_counter() - started)


def _balanced_array_end(text: str, start: int):
    """Find a matching root bracket while respecting JSON strings and escapes."""
    depth = 0
    quoted = False
    escaped = False
    for index in range(start, len(text)):
        current = text[index]
        if quoted:
            if escaped:
                escaped = False
            elif current == '\\':
                escaped = True
            elif current == '"':
                quoted = False
            continue
        if current == '"':
            quoted = True
        elif current == '[':
            depth += 1
        elif current == ']':
            depth -= 1
            if depth == 0:
                return index + 1
    return None


def _candidate_structure(value):
    """Summarize JSON structure without retaining any question or answer values."""
    if not isinstance(value, list):
        return {'top_level_type': type(value).__name__, 'top_level_count': None,
                'element_types': {}, 'object_keys': [], 'missing_fields': []}
    kinds = {}
    all_keys = set()
    extra_key_count = 0
    missing = []
    field_types = {}
    blank_fields = []
    array_lengths = {}
    correct_index_valid = {}
    required = ('question', 'options', 'correct', 'explanation')
    for index, item in enumerate(value):
        kind = 'null' if item is None else 'boolean' if isinstance(item, bool) else (
            'object' if isinstance(item, dict) else 'array' if isinstance(item, list) else
            'string' if isinstance(item, str) else 'number' if isinstance(item, (int, float)) else 'other'
        )
        kinds[kind] = kinds.get(kind, 0) + 1
        if isinstance(item, dict):
            known_keys = {'title', *required}
            all_keys.update(key for key in item if key in known_keys)
            extra_key_count += sum(key not in known_keys for key in item)
            expected = ('title',) if index == 0 else required
            missing.extend(f'/{index}/{key}' for key in expected if key not in item)
            for key in expected:
                if key in item:
                    field_value = item[key]
                    field_types[f'/{index}/{key}'] = (
                        'null' if field_value is None else 'boolean' if isinstance(field_value, bool) else
                        'object' if isinstance(field_value, dict) else 'array' if isinstance(field_value, list) else
                        'string' if isinstance(field_value, str) else 'number' if isinstance(field_value, (int, float)) else 'other'
                    )
                    if isinstance(field_value, str) and not field_value.strip():
                        blank_fields.append(f'/{index}/{key}')
            if isinstance(item.get('options'), list):
                array_lengths[f'/{index}/options'] = len(item['options'])
                option_types = set()
                for option in item['options']:
                    option_types.add(
                        'null' if option is None else 'boolean' if isinstance(option, bool) else
                        'object' if isinstance(option, dict) else 'array' if isinstance(option, list) else
                        'string' if isinstance(option, str) else 'number' if isinstance(option, (int, float)) else 'other'
                    )
                field_types[f'/{index}/options/item_types'] = sorted(option_types)
            if index > 0 and type(item.get('correct')) is int and isinstance(item.get('options'), list):
                correct_index_valid[f'/{index}/correct'] = 0 <= item['correct'] < len(item['options'])
        elif index == 0:
            missing.append('/0/title')
        else:
            missing.append(f'/{index}/<object>')
    return {
        'top_level_type': 'array', 'top_level_count': len(value),
        'element_types': kinds, 'object_keys': sorted(all_keys),
        'extra_key_count': extra_key_count,
        'missing_fields': missing[:40], 'field_types': field_types,
        'blank_fields': blank_fields[:40], 'array_lengths': array_lengths,
        'correct_index_valid': correct_index_valid,
    }


def _scan_json_array_candidates(response_text: str):
    """Use the standard decoder for valid candidates; balance only failed spans for diagnosis."""
    text = str(response_text or '')
    def reject_non_json_constant(_value):
        raise ValueError('non-standard JSON constant')

    decoder = json.JSONDecoder(parse_constant=reject_non_json_constant)
    candidates = []
    index = 0
    while index < len(text):
        start = text.find('[', index)
        if start < 0:
            break
        try:
            value, end = decoder.raw_decode(text, start)
        except (json.JSONDecodeError, ValueError):
            end = _balanced_array_end(text, start)
            if end is None:
                candidates.append({
                    'start': start, 'end': None, 'text': text[start:],
                    'status': 'incomplete', 'value': None,
                })
                # Continue looking: a later independent complete array may follow
                # an unmatched opening bracket in surrounding prose.
                index = start + 1
            else:
                candidates.append({
                    'start': start, 'end': end, 'text': text[start:end],
                    'status': 'invalid_json', 'value': None,
                })
                # Keep scanning nested opens in malformed text; valid JSON
                # arrays are decoded as complete units and skipped below.
                index = start + 1
        else:
            if isinstance(value, list):
                candidates.append({
                    'start': start, 'end': end, 'text': text[start:end],
                    'status': 'parsed', 'value': value,
                })
                index = end
            else:
                index = start + 1
    return candidates


def select_valid_quiz_array(response_text: str) -> QuizArraySelection:
    """Select the single distinct candidate accepted by the existing quiz validator."""
    candidates = _scan_json_array_candidates(response_text)
    if not candidates:
        raise QuizArrayExtractionError('no_array')
    valid = {}
    summaries = []
    for candidate_number, candidate in enumerate(candidates, 1):
        summary = {
            'candidate': candidate_number,
            'start': candidate['start'],
            'char_count': len(candidate['text']),
            'status': candidate['status'],
        }
        if candidate['status'] == 'parsed':
            summary.update(_candidate_structure(candidate['value']))
            try:
                quiz = validate_quiz(candidate['text'])
            except QuizError:
                summary['status'] = 'invalid_schema'
            else:
                canonical = json.dumps(quiz.payload, ensure_ascii=False, sort_keys=True,
                                       separators=(',', ':'))
                valid.setdefault(canonical, (candidate['text'], quiz))
                summary['status'] = 'valid'
        summaries.append(summary)
    if len(valid) == 1:
        selected_text, quiz = next(iter(valid.values()))
        return QuizArraySelection(selected_text, quiz, tuple(summaries))
    if len(valid) > 1:
        raise QuizArrayExtractionError('ambiguous', summaries)
    statuses = {item['status'] for item in summaries}
    if 'invalid_schema' in statuses:
        reason = 'invalid_schema'
    elif 'invalid_json' in statuses:
        reason = 'invalid_json'
    elif 'incomplete' in statuses:
        reason = 'incomplete_array'
    else:
        reason = 'no_array'
    if reason == 'invalid_schema':
        raise QuizCandidateValidationError(summaries)
    raise QuizArrayExtractionError(reason, summaries)


def extract_json_array(response_text: str) -> str:
    """Return the sole distinct array candidate that passes MCQ Maker validation."""
    return select_valid_quiz_array(response_text).text


class HumanAssistedLocators(AIStudioLocators):
    """Small experiment-only acknowledgements and safe visible-error labels."""

    async def attachment_acknowledged(self, filename: str, timeout: float = 30.0):
        name = self.page.get_by_text(filename, exact=True)
        try:
            await name.first.wait_for(state='visible', timeout=int(timeout * 1000))
            visible = False
            for index in range(await name.count()):
                if await name.nth(index).is_visible():
                    visible = True
                    break
            if not visible:
                raise RuntimeError('attachment filename is not visible')
        except Exception as exc:
            raise AIStudioUploadError(
                f'Google AI Studio did not acknowledge the attached file {filename}.'
            ) from exc
        ready = await self.wait_for_attachment_token_count(filename, timeout=min(timeout, 10.0))
        return ready

    async def visible_generation_error(self):
        categories = (
            (re.compile(r'permission denied', re.I), 'Google AI Studio displayed Permission denied.'),
            (re.compile(r'an internal error has occurred', re.I),
             'Google AI Studio displayed an internal generation error.'),
            (re.compile(
                r'\bquota\b(?:\s+(?:exceeded|exhausted|limit))?|'
                r'\brate[- ]?limit(?:ed|ing)?\b', re.I
             ),
             'Google AI Studio displayed a quota or rate-limit error.'),
            (re.compile(r'failed to generate', re.I),
             'Google AI Studio displayed a generation failure.'),
        )
        for pattern, message in categories:
            errors = self.page.get_by_text(pattern)
            for index in range(await errors.count()):
                if await errors.nth(index).is_visible():
                    return message
        return None


def diagnose_calibration_response(text):
    """Return normalized calibration classification details without persisting content."""
    normalized = normalize_assistant_response_text(text).casefold()
    if not normalized:
        return normalized, 'other', 'response is empty after presentation normalization'
    if any(marker in normalized for marker in (
        'permission denied', 'internal error', 'an internal error',
        'calibration incomplete',
    )):
        if 'permission denied' in normalized:
            return normalized, 'permission_denied', 'response contains permission-denied text'
        if 'error' in normalized:
            return normalized, 'internal_error', 'response contains provider error text'
        return normalized, 'other', 'response explicitly says calibration is incomplete'
    if any(marker in normalized for marker in (
        'internal qa', 'batching / token limit protocol',
        'final interaction (after all lecture files',
    )):
        return normalized, 'other', 'response contains calibration protocol text that should not be present in the acknowledgement'
    if normalized == 'calibration complete':
        return normalized, 'calibration_complete_exact', 'exact calibration acknowledgement'
    if normalized.startswith('calibration complete'):
        return normalized, 'calibration_ready_expanded', 'response begins with calibration acknowledgement'
    return normalized, 'other', 'response does not begin with the required calibration acknowledgement'


def classify_calibration_response(text):
    """Classify a newly rendered calibration response without retaining its text."""
    return diagnose_calibration_response(text)[1]


def should_pause_for_calibration_diagnostics(enabled, stage):
    """Keep the same visible Playground for review after any calibration-stage failure."""
    return bool(enabled) and stage is ExperimentState.CALIBRATION_RUNNING


class TurnObserver:
    """Correlate one automated submission with its newly rendered response."""

    def __init__(self, locators: HumanAssistedLocators, *, manual_timeout=180.0,
                 event_callback=None, recorder=None, performance_trace=None,
                 stage_prefix='turn'):
        self.locators = locators
        self.page = locators.page
        self.manual_timeout = manual_timeout
        self.event_callback = event_callback or print
        self.recorder = recorder
        self.performance_trace = performance_trace
        self.stage_prefix = stage_prefix
        self._signal = None
        self._handler = None
    async def begin_attempt(self, initiating_action):
        if self.recorder is not None:
            begin = getattr(self.recorder, 'begin_attempt', None)
            if callable(begin):
                begin(initiating_action)

    def arm(self):
        loop = asyncio.get_running_loop()
        self._signal = loop.create_future()

        def on_response(response):
            url = str(getattr(response, 'url', '')).casefold()
            if 'generatecontent' not in url or self._signal.done():
                return
            status = getattr(response, 'status', None)
            if callable(status):
                status = status()
            self._signal.set_result(status)

        self._handler = on_response
        self.page.on('response', on_response)

    def disarm(self):
        if self._handler is not None:
            self.page.remove_listener('response', self._handler)
            self._handler = None

    async def _wait_for_submission(self):
        """Wait for the single automated send's generation signal."""
        if self._signal is None:
            self.arm()
        stop_wait = asyncio.create_task(self.page.wait_for_selector(
            self.locators._stop_selector(), state='visible',
            timeout=int(self.manual_timeout * 1000),
        ))
        network_wait = asyncio.ensure_future(asyncio.shield(self._signal))
        try:
            done, pending = await asyncio.wait(
                (stop_wait, network_wait), return_when=asyncio.FIRST_COMPLETED
            )
            stop_seen = stop_wait in done and stop_wait.exception() is None
            status = self._signal.result() if self._signal.done() else None
            if stop_seen and status is None:
                # Stop can render before the browser delivers the matching HTTP
                # response event. Keep the listener armed through this short wait.
                try:
                    status = await asyncio.wait_for(asyncio.shield(self._signal), timeout=30.0)
                except asyncio.TimeoutError:
                    status = None
            if not stop_seen and status is None:
                raise AIStudioGenerationTimeoutError(
                    'No AI Studio submission was detected before the waiting period ended.'
                )
            for task in pending:
                if task is stop_wait:
                    task.cancel()
            return stop_seen, status
        finally:
            if not network_wait.done():
                network_wait.cancel()
            if not stop_wait.done():
                stop_wait.cancel()
            self.disarm()

    async def wait(self, *, lecture=False, on_started=None, accept_response=None,
                   calibration_diagnostics=False):
        """Observe exactly one automated send and fail clearly if it is not accepted."""
        if self.performance_trace is None:
            stop_seen, status = await self._wait_for_submission()
        else:
            async with self.performance_trace.measure(f'{self.stage_prefix}_submission_start'):
                stop_seen, status = await self._wait_for_submission()
        if on_started is not None:
            on_started()
        if status != 200:
            reason = await self.locators.visible_generation_error()
            detail = reason or (f'Google AI Studio returned HTTP {status}.' if status is not None
                                else 'Google AI Studio did not return a generation response.')
            self._finish_attempt(False, detail)
            raise AIStudioResponseError(detail)

        if stop_seen:
            if self.performance_trace is None:
                if lecture:
                    await self.locators.wait_for_lecture_generation_complete()
                else:
                    await self.locators.wait_for_generation_complete()
            else:
                async with self.performance_trace.measure(f'{self.stage_prefix}_generation_completion'):
                    if lecture:
                        await self.locators.wait_for_lecture_generation_complete()
                    else:
                        await self.locators.wait_for_generation_complete()
        else:
            # A fast response may complete before Stop is observable.
            try:
                await self.page.wait_for_selector(
                    self.locators._run_selector(), state='visible',
                    timeout=int(getattr(self.locators, 'run_return_timeout', 120.0) * 1000),
                )
            except Exception as exc:
                self._finish_attempt(False, 'Google AI Studio did not return to the ready state after generation.')
                raise AIStudioGenerationTimeoutError(
                    'Google AI Studio did not return to the ready state after generation.'
                ) from exc

        try:
            if self.performance_trace is None:
                rendered = await self.locators.wait_for_completed_assistant_response(
                    timeout=getattr(self.locators, 'final_answer_timeout', 120.0)
                )
            else:
                async with self.performance_trace.measure(f'{self.stage_prefix}_final_answer_render'):
                    rendered = await self.locators.wait_for_completed_assistant_response(
                        timeout=getattr(self.locators, 'final_answer_timeout', 120.0)
                    )
        except Exception:
            rendered = False
        # The locator contract selects only a completed assistant turn after
        # the latest user message. Container counts are unstable because the
        # page may reuse a response wrapper across turns.
        new_response = bool(rendered)
        if not new_response:
            reason = await self.locators.visible_generation_error()
            reason = reason or 'Google AI Studio finished without a new completed assistant response.'
            self._finish_attempt(False, reason)
            raise AIStudioResponseError(reason)

        selected = None
        if calibration_diagnostics and not lecture:
            selected = await self.locators.last_assistant_response_diagnostic()
            response = selected['extracted_text']
        else:
            response = await self.locators.last_assistant_response_text()
        normalized, classification, reason = diagnose_calibration_response(response)
        if calibration_diagnostics and not lecture:
            print('[calibration-diagnostic:selected-element]=' + repr(selected), flush=True)
            print('[calibration-diagnostic:current-extraction]=' + repr(response), flush=True)
            print('[calibration-diagnostic:header-normalized]=' + repr(normalize_assistant_response_text(response)), flush=True)
            print('[calibration-diagnostic:classifier-input]=' + repr(normalized), flush=True)
            print('[calibration-diagnostic:classification]=' + repr(classification), flush=True)
            print('[calibration-diagnostic:reason]=' + repr(reason), flush=True)
        if accept_response is not None and not accept_response(response):
            reason = 'Google AI Studio did not confirm calibration completion.'
            self._finish_attempt(True, reason, response)
            raise AIStudioCalibrationError(reason)
        self._finish_attempt(True, 'accepted', response, accepted=True)
        return TurnObservation(response, status, stop_seen)

    def _finish_attempt(self, new_response, reason, response='', accepted=False):
        finish = getattr(self.recorder, 'finish_attempt', None) if self.recorder is not None else None
        if not callable(finish):
            return
        lowered = reason.casefold()
        classification = (
            classify_calibration_response(response) if accepted or response else
            'permission denied' if 'permission denied' in lowered else
            'internal error' if 'internal error' in lowered else
            'other'
        )
        finish(new_assistant_response=bool(new_response), response_classification=classification)


class HumanAssistedExperiment:
    def __init__(self, manager: AIStudioBrowserManager, *, model: str, thinking: str,
                 output_dir: Path = OUTPUT_DIR, locator_factory=HumanAssistedLocators,
                 session_factory=AIStudioConversationSession,
                 recorder_factory=WaaDiagnosticRecorder, event_callback=None,
                 template=None, calibration_diagnostics=False):
        self.manager = manager
        self.model = model
        self.thinking = thinking
        self.output_dir = Path(output_dir)
        self.locator_factory = locator_factory
        self.session_factory = session_factory
        self.recorder_factory = recorder_factory
        self.event_callback = event_callback or (lambda message: print(message, flush=True))
        self.template = template
        self.calibration_diagnostics = bool(calibration_diagnostics)
        self.state = ExperimentState.NEW
        self.session = None
        self.locators = None
        self.calibration_observer = None
        self.lecture_observer = None
        self.recorder = None
        self.last_quiz = None
        self.extraction_diagnostics = None
        self.performance_trace = StagePerformanceTrace(self._report)

    def _report(self, message):
        self.event_callback(message)

    def transition(self, target: ExperimentState):
        if target not in ALLOWED_TRANSITIONS[self.state]:
            raise RuntimeError(f'Illegal experiment transition: {self.state.value} to {target.value}.')
        self.state = target
        self._report(f'[{target.value}]')

    async def prepare_calibration(self, prompt_path: Path, reference_path: Path):
        self.transition(ExperimentState.PREPARING)
        async with self.performance_trace.measure('calibration_prompt_read'):
            prompt_text = Path(prompt_path).read_text(encoding='utf-8-sig')
        async with self.performance_trace.measure('browser_attach'):
            context = await self.manager.ensure_connected()
        async with self.performance_trace.measure('diagnostic_observer_install'):
            self.recorder = self.recorder_factory(
                None, 'cdp', True, 'human_assisted_manual_run'
            )
            await self.recorder.install_context(context, read_webdriver=False)
        self.session = self.session_factory(
            self.manager, locator_factory=self.locator_factory
        )
        self.session.performance_callback = self.performance_trace.record
        await self.session.discover_capabilities()
        set_selected_page = getattr(self.recorder, 'set_selected_page', None)
        if callable(set_selected_page):
            set_selected_page(self.session.page)
        record_operation_page = getattr(self.recorder, 'record_operation_page', None)
        if callable(record_operation_page):
            for operation in ('initialization', 'prompt_fill', 'file_attachment', 'send', 'network_observation', 'response_extraction'):
                record_operation_page(operation, self.session.page)
        self.recorder.page = self.session.page
        try:
            self.recorder.navigator_webdriver = await self.session.page.evaluate(
                '() => Boolean(navigator.webdriver)'
            )
        except Exception:
            self.recorder.navigator_webdriver = None
        await self.session.configure_defaults(model=self.model, thinking_level=self.thinking)
        self.locators = self.session.locators
        self._report('[uploading_reference]')
        async with self.performance_trace.measure('calibration_prompt_fill'):
            await self.locators.fill_prompt(prompt_text)
        async with self.performance_trace.measure('reference_upload_and_acknowledgement'):
            filename = await self.locators.attach_reference_file(reference_path)
            attachment = await self.locators.attachment_acknowledged(filename)
        self.calibration_observer = TurnObserver(
            self.locators, event_callback=self._report, recorder=self.recorder,
            performance_trace=self.performance_trace, stage_prefix='calibration',
        )
        await self.calibration_observer.begin_attempt('automated Send')
        self.calibration_observer.arm()
        self.transition(ExperimentState.CALIBRATION_PREPARED)
        self.transition(ExperimentState.WAITING_CALIBRATION)
        self._report(f'[reference_ready:{attachment.value}]')
        self._report('[sending_calibration]')
        async with self.performance_trace.measure('calibration_send_action'):
            await self.locators.send_prompt()
        self.transition(ExperimentState.CALIBRATION_RUNNING)

    async def observe_calibration_and_prepare_lecture(self, lecture_path: Path):
        # Keep the path queued locally while generation runs. The composer and
        # hidden upload input are part of the same live UI, and AI Studio's
        # ability to mutate them during generation is not guaranteed. We only
        # touch them after the calibration Stop-to-Run cycle has completed.
        self.queued_lecture_path = Path(lecture_path)
        observation = await self.calibration_observer.wait(
            lecture=False,
            accept_response=lambda text: classify_calibration_response(text) in {
                'calibration_complete_exact', 'calibration_ready_expanded'
            },
            calibration_diagnostics=self.calibration_diagnostics,
        )
        self.transition(ExperimentState.CALIBRATION_COMPLETE)
        self.transition(ExperimentState.LECTURE_UPLOAD_PENDING)
        self.transition(ExperimentState.LECTURE_UPLOADING)
        self._report('[uploading_lecture]')
        async with self.performance_trace.measure('lecture_upload_and_acknowledgement'):
            filename = await self.locators.attach_lecture_file(self.queued_lecture_path)
            attachment = await self.locators.attachment_acknowledged(filename)
        self.transition(ExperimentState.LECTURE_UPLOADED)
        async with self.performance_trace.measure('lecture_instruction_fill'):
            await self.locators.fill_prompt(GENERATION_REQUEST)
        self.transition(ExperimentState.LECTURE_READY_TO_SEND)
        self.lecture_observer = TurnObserver(
            self.locators, event_callback=self._report, recorder=self.recorder,
            performance_trace=self.performance_trace, stage_prefix='lecture',
        )
        await self.lecture_observer.begin_attempt('automated Send')
        self.lecture_observer.arm()
        self.transition(ExperimentState.WAITING_LECTURE)
        self._report(f'[lecture_ready:{attachment.value}]')
        self._report('[sending_lecture]')
        async with self.performance_trace.measure('lecture_send_action'):
            await self.locators.send_prompt()
        self.transition(ExperimentState.LECTURE_RUNNING)

    async def observe_lecture_and_save(self):
        observation = await self.lecture_observer.wait(
            lecture=True,
        )
        self.transition(ExperimentState.JSON_RECEIVED)
        self.transition(ExperimentState.VALIDATING)
        try:
            async with self.performance_trace.measure('quiz_validation'):
                selection = select_valid_quiz_array(observation.response_text)
        except (QuizArrayExtractionError, QuizCandidateValidationError) as exc:
            self.extraction_diagnostics = {
                'reason': exc.reason,
                'candidates': list(exc.diagnostics),
            }
            raise
        quiz = selection.quiz
        self.last_quiz = quiz
        self.transition(ExperimentState.SAVING)
        template = self.template or (
            PROJECT_ROOT / 'mcq_maker' / 'assets' / 'standard_exam.html'
        ).read_text(encoding='utf-8')
        try:
            async with self.performance_trace.measure('html_exam_save'):
                output = save_exam(template, quiz, self.output_dir)
        except (TemplateError, OSError) as exc:
            raise ExamSaveError(f'MCQ Maker could not save the HTML exam: {exc}') from exc
        self.transition(ExperimentState.EXAM_SAVED)
        self.transition(ExperimentState.COMPLETE)
        self._report(f'[saved:{output.name}]')
        self._report(f'[questions:{len(quiz.questions)}]')
        return output

    async def close(self):
        if self.recorder is not None:
            self.recorder.uninstall()
        await self.manager.shutdown()

    def save_diagnostic(self, directory: Path = DIAGNOSTIC_DIR):
        if self.recorder is None:
            return None
        snapshot = self.recorder.snapshot()
        snapshot['performance_trace'] = list(self.performance_trace.records)
        if self.extraction_diagnostics is not None:
            snapshot['quiz_candidates'] = self.extraction_diagnostics
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        target = directory / f'{stamp}_manual_run.json'
        target.write_text(json.dumps(snapshot, indent=2), encoding='utf-8')
        return target


async def run(args):
    for value, label in (
        (args.prompt, 'prompt'), (args.reference, 'reference'), (args.lecture, 'lecture')
    ):
        if not value.is_file():
            raise FileNotFoundError(f'The selected {label} file is unavailable: {value}')
    manager = AIStudioBrowserManager(launch_mode='cdp')
    experiment = HumanAssistedExperiment(
        manager, model=args.model, thinking=args.thinking, output_dir=args.output,
        calibration_diagnostics=args.calibration_diagnostics,
    )
    failure = None
    keep_browser_open = False
    try:
        await experiment.prepare_calibration(args.prompt, args.reference)
        await experiment.observe_calibration_and_prepare_lecture(args.lecture)
        await experiment.observe_lecture_and_save()
        if getattr(args, 'keep_browser', False):
            keep_browser_open = True
            print('[browser-left-open: run completed; waiting for user review]', flush=True)
            await asyncio.Event().wait()
    except KeyboardInterrupt:
        if experiment.state not in {ExperimentState.COMPLETE, ExperimentState.FAILED}:
            experiment.state = ExperimentState.CANCELLED
        print('[cancelled]', flush=True)
    except (AIStudioAuthenticationRequired, AIStudioCancelledError) as exc:
        experiment.state = ExperimentState.CANCELLED
        print(f'[interrupted:{exc}]', flush=True)
        raise
    except Exception as exc:
        failed_stage = experiment.state
        experiment.state = ExperimentState.FAILED
        failure = exc
        print(f'[failed:{type(exc).__name__}:{exc}]', flush=True)
        diagnostic = experiment.save_diagnostic()
        if diagnostic is not None:
            snapshot = experiment.recorder.snapshot()
            statuses = [
                item.get('status') for item in snapshot.get('records', [])
                if item.get('status') is not None
            ]
            errors = [
                item.get('grpc_code') for item in snapshot.get('records', [])
                if item.get('grpc_code') is not None
            ]
            print(
                f'[diagnostic:navigator_webdriver={snapshot.get("navigator_webdriver")};'
                f'waa_create={snapshot.get("waa_create_statuses")};'
                f'generate_statuses={statuses};grpc_codes={errors}]',
                flush=True,
            )
            print(f'[diagnostic_saved:{diagnostic.name}]', flush=True)
        if getattr(args, 'keep_browser', False) or should_pause_for_calibration_diagnostics(args.calibration_diagnostics, failed_stage):
            keep_browser_open = True
            print('[calibration-diagnostic:browser-left-open; waiting for user review]', flush=True)
            # Keep the same completed turn visible for screenshot-based comparison.
            await asyncio.Event().wait()
    finally:
        if not keep_browser_open:
            await experiment.close()
    if failure is not None:
        raise failure


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description='Run one calibrated AI Studio lecture through MCQ Maker validation and save.'
    )
    parser.add_argument('--prompt', type=Path, default=DEFAULT_PROMPT)
    parser.add_argument('--reference', type=Path, default=DEFAULT_REFERENCE)
    parser.add_argument('--lecture', type=Path, default=DEFAULT_LECTURE)
    parser.add_argument('--output', type=Path, default=OUTPUT_DIR)
    parser.add_argument('--model', default='Gemini 3.8 Flash')
    parser.add_argument('--thinking', default='High')
    parser.add_argument(
        '--calibration-diagnostics', action='store_true',
        help='Temporarily print the selected calibration response and its normalization locally.',
    )
    parser.add_argument(
        '--keep-browser', action='store_true',
        help='Keep the dedicated Brave browser open after completion or failure for manual inspection.',
    )
    return parser.parse_args(argv)


if __name__ == '__main__':
    asyncio.run(run(parse_args()))
