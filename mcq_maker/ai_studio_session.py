"""Phase 3 fresh-chat readiness and live capability discovery."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import asyncio
from json import JSONDecodeError
import re

from .ai_studio_browser import AI_STUDIO_NEW_CHAT_URL, AIStudioReadiness
from .ai_studio_errors import (AIStudioAuthenticationRequired, AIStudioBrowserLaunchError,
                               AIStudioCancelledError,
                               AIStudioCalibrationError, AIStudioConnectionError,
                               AIStudioControlNotFoundError, AIStudioExtractionError,
                               AIStudioGenerationError, AIStudioGenerationTimeoutError,
                               AIStudioInitializationError, AIStudioProfileInUseError,
                               AIStudioResponseError, AIStudioUploadError,
                               AIStudioValidationError, BraveNotFoundError,
                               InvalidBraveExecutableError)
from .ai_studio_selectors import (AIStudioLocators, AttachmentReady,
                                 normalize_assistant_response_text)
from .quiz_validation import QuizError, validate_quiz


class AIStudioSessionState(str, Enum):
    NEW = 'new'
    BROWSER_ATTACHED = 'browser_attached'
    AUTHENTICATION_CHECK = 'authentication_check'
    AUTHENTICATED = 'authenticated'
    FRESH_CONVERSATION = 'fresh_conversation'
    MODEL_DISCOVERED = 'model_discovered'
    THINKING_DISCOVERED = 'thinking_discovered'
    CONFIGURATION_COMPLETE = 'configuration_complete'
    CALIBRATION_COMPLETE = 'calibration_complete'
    GENERATION_COMPLETE = 'generation_complete'
    FAILED = 'failed'
    CANCELLED = 'cancelled'
    CLOSED = 'closed'


_ALLOWED = {
    AIStudioSessionState.NEW: {AIStudioSessionState.BROWSER_ATTACHED, AIStudioSessionState.CANCELLED, AIStudioSessionState.FAILED},
    AIStudioSessionState.BROWSER_ATTACHED: {AIStudioSessionState.AUTHENTICATION_CHECK, AIStudioSessionState.CANCELLED, AIStudioSessionState.FAILED},
    AIStudioSessionState.AUTHENTICATION_CHECK: {AIStudioSessionState.AUTHENTICATED, AIStudioSessionState.CANCELLED, AIStudioSessionState.FAILED},
    AIStudioSessionState.AUTHENTICATED: {AIStudioSessionState.MODEL_DISCOVERED, AIStudioSessionState.CANCELLED, AIStudioSessionState.FAILED},
    AIStudioSessionState.FRESH_CONVERSATION: {AIStudioSessionState.CONFIGURATION_COMPLETE, AIStudioSessionState.CLOSED, AIStudioSessionState.CANCELLED, AIStudioSessionState.FAILED},
    AIStudioSessionState.MODEL_DISCOVERED: {AIStudioSessionState.THINKING_DISCOVERED, AIStudioSessionState.CANCELLED, AIStudioSessionState.FAILED},
    AIStudioSessionState.THINKING_DISCOVERED: {AIStudioSessionState.FRESH_CONVERSATION, AIStudioSessionState.CANCELLED, AIStudioSessionState.FAILED},
    AIStudioSessionState.CONFIGURATION_COMPLETE: {AIStudioSessionState.CALIBRATION_COMPLETE, AIStudioSessionState.CLOSED, AIStudioSessionState.CANCELLED, AIStudioSessionState.FAILED},
    AIStudioSessionState.CALIBRATION_COMPLETE: {AIStudioSessionState.GENERATION_COMPLETE, AIStudioSessionState.CLOSED, AIStudioSessionState.CANCELLED, AIStudioSessionState.FAILED},
    AIStudioSessionState.GENERATION_COMPLETE: {AIStudioSessionState.GENERATION_COMPLETE, AIStudioSessionState.CLOSED, AIStudioSessionState.CANCELLED, AIStudioSessionState.FAILED},
    AIStudioSessionState.FAILED: {AIStudioSessionState.CLOSED},
    AIStudioSessionState.CANCELLED: {AIStudioSessionState.CLOSED},
    AIStudioSessionState.CLOSED: set(),
}


@dataclass(frozen=True)
class AIStudioDiscovery:
    current_model: str
    available_models: tuple[str, ...] | None
    thinking_supported: bool
    current_thinking_level: str | None
    available_thinking_levels: tuple[str, ...] | None


@dataclass(frozen=True)
class AIStudioConfiguration:
    model: str
    thinking_level: str
    model_changed: bool
    thinking_changed: bool


class AIStudioConversationSession:
    """Own one fresh chat page through capability discovery only."""

    def __init__(self, browser_manager, *, locator_factory=AIStudioLocators, quiz_validator=validate_quiz,
                 isolated_page=False):
        self.browser_manager = browser_manager
        self.isolated_page = bool(isolated_page)
        self._owns_page = False
        self.locator_factory = locator_factory
        self.quiz_validator = quiz_validator
        self.state = AIStudioSessionState.NEW
        self.page = None
        self.locators = None
        self._configuration = None
        self._run_settings_open = False
        self._last_response_text = None
        self.performance_callback = None
        self.diagnostic_callback = None

    def _diagnostic(self, stage, **metadata):
        """Emit only stage labels and non-content metadata to the owning worker."""
        callback = self.diagnostic_callback
        if callable(callback):
            callback(stage, metadata)

    async def _measure(self, stage, awaitable):
        started = asyncio.get_running_loop().time()
        try:
            return await awaitable
        finally:
            if callable(self.performance_callback):
                self.performance_callback(stage, asyncio.get_running_loop().time() - started)

    def _transition(self, target):
        if target not in _ALLOWED[self.state]:
            raise RuntimeError(f'Invalid AI Studio session transition: {self.state.value} to {target.value}.')
        self.state = target

    def _cancelled(self, cancellation):
        if cancellation is not None and cancellation.is_set():
            self._transition(AIStudioSessionState.CANCELLED)
            raise AIStudioCancelledError('AI Studio capability discovery was cancelled.')

    @staticmethod
    def _navigation_failure_message(error):
        details = str(error).casefold()
        network_failures = (
            ('net::err_connection_timed_out', 'Google AI Studio navigation failed with ERR_CONNECTION_TIMED_OUT.'),
            ('net::err_internet_disconnected', 'Google AI Studio navigation failed because the internet connection was disconnected.'),
            ('net::err_name_not_resolved', 'Google AI Studio navigation failed because its server name could not be resolved.'),
            ('net::err_connection_reset', 'Google AI Studio navigation failed because the network connection was reset.'),
        )
        return next((message for marker, message in network_failures if marker in details),
                    'Google AI Studio navigation did not reach the document-ready state in time.')

    async def _reserved_sidebar_navigation(self):
        """Preserved for a future phase; the current discovery flow does not call it."""
        await self.locators.open_fresh_playground()

    async def _reserved_legacy_workspace_validation(self, prompt):
        """Preserved for a future phase; current readiness requires only an interactive prompt."""
        if await self.locators.landing_screen_visible() or await self.locators.has_existing_conversation_messages():
            raise AIStudioControlNotFoundError('Google AI Studio did not open a fresh Playground workspace.')
        if await self.locators.prompt_text(prompt):
            raise AIStudioControlNotFoundError('Google AI Studio did not open a fresh empty conversation.')

    async def _acquire_page(self, context):
        """Reuse the dedicated profile's existing tab instead of opening a duplicate."""
        if self.isolated_page:
            self._owns_page = True
            return await context.new_page()
        pages = tuple(getattr(context, 'pages', ()) or ())
        if pages:
            for page in reversed(pages):
                if self.browser_manager.classify_ai_studio_url(getattr(page, 'url', '')) is AIStudioReadiness.AI_STUDIO_REACHED:
                    return page
            return pages[-1]
        return await context.new_page()

    async def discover_capabilities(self, cancellation=None):
        try:
            self._cancelled(cancellation)
            context = await self._measure(
                'browser_context_attach', self.browser_manager.ensure_connected(cancellation)
            )
            self._transition(AIStudioSessionState.BROWSER_ATTACHED)
            self._cancelled(cancellation)
            self.page = await self._acquire_page(context)
            self._diagnostic('ai_studio_page_created',
                             page_identity=f'{id(self.page):x}', isolated_page=self.isolated_page)
            self._transition(AIStudioSessionState.AUTHENTICATION_CHECK)
            self.locators = self.locator_factory(self.page)
            self.locators.performance_callback = self.performance_callback
            initialization_listener = self.locators.arm_initialization_listener()
            try:
                try:
                    await self._measure(
                        'ai_studio_navigation',
                        self.page.goto(
                            AI_STUDIO_NEW_CHAT_URL, wait_until='domcontentloaded',
                            timeout=int(self.locators.navigation_timeout * 1000),
                        ),
                    )
                except Exception as exc:
                    raise AIStudioConnectionError(
                        self._navigation_failure_message(exc)
                    ) from exc
                readiness = self.browser_manager.classify_ai_studio_url(self.page.url)
                if readiness is AIStudioReadiness.SIGN_IN_REQUIRED:
                    raise AIStudioAuthenticationRequired('Sign in to Google in MCQ Maker’s dedicated Brave window, then try again.')
                if readiness is not AIStudioReadiness.AI_STUDIO_REACHED:
                    raise AIStudioConnectionError('Google AI Studio did not open in the dedicated browser.')
                await self._measure(
                    'ai_studio_initialization',
                    self.locators.wait_for_initialization(
                        cancellation=cancellation, listener=initialization_listener
                    ),
                )
            finally:
                self.locators.disarm_initialization_listener(initialization_listener)
            await self._measure('playground_readiness', self.locators.wait_for_landing_screen())
            self._transition(AIStudioSessionState.AUTHENTICATED)
            await self._measure('run_settings_readiness', self.locators.open_run_settings_panel())
            self._run_settings_open = True
            current_model = await self._measure('model_value_read', self.locators.current_model())
            self._transition(AIStudioSessionState.MODEL_DISCOVERED)
            # Read visible selections directly. Enumerating either options
            # picker here would close it, only for configure_defaults() to
            # reopen it when a requested value needs changing.
            thinking = await self._measure('thinking_value_read', self.locators.current_thinking_level())
            self._transition(AIStudioSessionState.THINKING_DISCOVERED)
            prompt = await self._measure(
                'prompt_readiness', self.locators.wait_for_prompt_input(cancellation)
            )
            self._cancelled(cancellation)
            if prompt is None:
                raise AIStudioControlNotFoundError("Could not find a usable Google AI Studio prompt input.")
            if not await self.locators.prompt_is_interactive(prompt):
                raise AIStudioControlNotFoundError('Google AI Studio did not open an interactive Playground prompt input.')
            self._transition(AIStudioSessionState.FRESH_CONVERSATION)
            self._diagnostic('fresh_conversation_ready', page_identity=f'{id(self.page):x}')
            return AIStudioDiscovery(current_model, None, thinking.supported, thinking.current, thinking.options)
        except AIStudioCancelledError:
            if self.state not in {AIStudioSessionState.CANCELLED, AIStudioSessionState.CLOSED}:
                self._transition(AIStudioSessionState.CANCELLED)
            raise
        except (AIStudioAuthenticationRequired, AIStudioBrowserLaunchError,
                AIStudioConnectionError, AIStudioInitializationError,
                AIStudioControlNotFoundError, AIStudioProfileInUseError,
                BraveNotFoundError, InvalidBraveExecutableError):
            if self.state not in {AIStudioSessionState.FAILED, AIStudioSessionState.CLOSED}:
                self._transition(AIStudioSessionState.FAILED)
            raise
        except Exception as exc:
            if self.state not in {AIStudioSessionState.FAILED, AIStudioSessionState.CLOSED}:
                self._transition(AIStudioSessionState.FAILED)
            raise AIStudioConnectionError('MCQ Maker could not inspect Google AI Studio capabilities.') from exc

    async def configure_defaults(self, *, model='Gemini 3.8 Flash', thinking_level='High'):
        """Apply configured generation defaults after the Phase 3 discovery flow."""
        if self.state is not AIStudioSessionState.FRESH_CONVERSATION:
            raise RuntimeError('AI Studio configuration requires a ready fresh conversation.')
        try:
            self._diagnostic('configuration_started', page_identity=f'{id(self.page):x}')
            if not self._run_settings_open:
                await self.locators.open_run_settings_panel()
                self._run_settings_open = True
            model_changed = await self._measure('model_configuration', self.locators.set_model(model))
            if model_changed:
                await self._measure(
                    'thinking_level_dependent_readiness',
                    self.locators.wait_for_thinking_level_ready(),
                )
            thinking_changed = await self._measure(
                'thinking_configuration', self.locators.set_thinking_level(thinking_level)
            )
            await self._measure('run_settings_close', self.locators.close_run_settings_panel())
            self._run_settings_open = False
            self._transition(AIStudioSessionState.CONFIGURATION_COMPLETE)
            self._configuration = AIStudioConfiguration(model, thinking_level, model_changed, thinking_changed)
            self._diagnostic('configuration_completed', page_identity=f'{id(self.page):x}')
            return self._configuration
        except AIStudioControlNotFoundError:
            if self._run_settings_open:
                await self.locators.close_run_settings_panel()
                self._run_settings_open = False
            if self.state is not AIStudioSessionState.FAILED:
                self._transition(AIStudioSessionState.FAILED)
            raise
        except Exception as exc:
            if self._run_settings_open:
                await self.locators.close_run_settings_panel()
                self._run_settings_open = False
            if self.state is not AIStudioSessionState.FAILED:
                self._transition(AIStudioSessionState.FAILED)
            raise AIStudioConnectionError('MCQ Maker could not configure Google AI Studio.') from exc

    @staticmethod
    def _has_calibration_confirmation(response):
        text = normalize_assistant_response_text(response).casefold()
        forbidden = (
            'an internal error has occurred',
            'permission denied',
            'internal qa',
            'batching / token limit protocol',
            'final interaction (after all lecture files',
            'calibration incomplete',
        )
        return (text.startswith('calibration complete')
                and not any(marker in text for marker in forbidden))

    def _arm_turn_response(self):
        arm = getattr(self.locators, 'arm_generate_content_status_listener', None)
        return arm() if callable(arm) else None

    async def _confirm_turn_http_success(self, listener):
        if listener is None:
            return 200  # Lightweight offline locator doubles do not expose page events.
        status = await self.locators.wait_for_generate_content_status(listener, timeout=30.0)
        if status is None:
            raise AIStudioResponseError('Google AI Studio did not return a correlated generation status.')
        if status != 200:
            raise AIStudioResponseError(f'Google AI Studio rejected the generation request (HTTP {status}).')
        return status

    def _disarm_turn_response(self, listener):
        disarm = getattr(self.locators, 'disarm_generate_content_status_listener', None)
        if listener is not None and callable(disarm):
            disarm(listener)

    async def _completed_calibration_response(self):
        response = await self.locators.last_assistant_response_text()
        if not self._has_calibration_confirmation(response):
            raise AIStudioCalibrationError('Google AI Studio did not confirm calibration completion.')
        self._last_response_text = response
        return response

    def response_preview(self, limit=200):
        """Return a diagnostic preview of the exact response already validated."""
        return (self._last_response_text or '')[:limit]

    async def _wait_before_send(self):
        await asyncio.sleep(0)

    async def _has_completed_assistant_response(self):
        """Allow older test doubles while requiring the production locator contract."""
        checker = getattr(self.locators, 'has_completed_assistant_response', None)
        if checker is None:
            return True
        return await checker()

    async def _wait_for_completed_assistant_response(self, timeout=5.0):
        waiter = getattr(self.locators, 'wait_for_completed_assistant_response', None)
        if waiter is not None:
            return await waiter(timeout=timeout)
        try:
            return await self._has_completed_assistant_response()
        except Exception:
            return False

    async def _stable_assistant_response(self):
        waiter = getattr(self.locators, 'wait_for_stable_assistant_response', None)
        if callable(waiter):
            return await waiter()
        return await self.locators.last_assistant_response_text()

    async def _run_calibration_turn(self, system_prompt, reference_file):
        """Submit calibration exactly once; ambiguous outcomes are never retried."""
        listener = self._arm_turn_response()
        try:
            self._diagnostic('calibration_submission_started', page_identity=f'{id(self.page):x}')
            await self.locators.fill_prompt(system_prompt)
            filename = await self.locators.attach_reference_file(reference_file)
            attachment_ready = await self.locators.wait_for_attachment_token_count(filename)
            generation_started = (attachment_ready is AttachmentReady.GENERATION_STARTED
                                  or attachment_ready == AttachmentReady.GENERATION_STARTED.value)
            if not generation_started:
                await self._wait_before_send()
                await self.locators.send_prompt()
            if not generation_started:
                await self.locators.wait_for_generation_start()
            await self.locators.wait_for_generation_complete()
            await self._confirm_turn_http_success(listener)
            if not await self._wait_for_completed_assistant_response():
                raise AIStudioResponseError(
                    'Google AI Studio finished without a completed calibration response for the current turn.'
                )
            response = await self._completed_calibration_response()
            self._diagnostic('calibration_completed', page_identity=f'{id(self.page):x}')
            return response
        finally:
            self._disarm_turn_response(listener)

    async def run_calibration(self, system_prompt, reference_file):
        """Send calibration once and require its one expected acknowledgement."""
        if self.state is not AIStudioSessionState.CONFIGURATION_COMPLETE:
            raise RuntimeError('AI Studio calibration requires completed model configuration.')
        try:
            response = await self._run_calibration_turn(system_prompt, reference_file)
            if response is None:
                raise AIStudioCalibrationError(
                    'Google AI Studio calibration response was ambiguous; the current conversation was kept open.'
                )
            self._transition(AIStudioSessionState.CALIBRATION_COMPLETE)
        except (AIStudioCalibrationError, AIStudioControlNotFoundError, AIStudioUploadError,
                AIStudioGenerationTimeoutError, AIStudioResponseError, AIStudioAuthenticationRequired,
                AIStudioConnectionError):
            if self.state is not AIStudioSessionState.FAILED:
                self._transition(AIStudioSessionState.FAILED)
            raise
        except Exception as exc:
            if self.state is not AIStudioSessionState.FAILED:
                self._transition(AIStudioSessionState.FAILED)
            raise AIStudioConnectionError('MCQ Maker could not complete Google AI Studio calibration.') from exc

    @staticmethod
    def extract_quiz_json(response):
        start = response.find('[')
        end = response.rfind(']')
        if start < 0 or end < start:
            raise AIStudioExtractionError('Google AI Studio did not return a JSON quiz array.')
        return response[start:end + 1]

    def _validate_quiz_json(self, quiz_json):
        try:
            return self.quiz_validator(quiz_json)
        except Exception as exc:
            raise AIStudioValidationError('The generated quiz did not pass MCQ Maker validation.') from exc

    @staticmethod
    def _validation_diagnostic(exc):
        """Summarize validator failure shape without retaining any quiz values."""
        message = str(exc)
        if message.startswith('JSON syntax problem'):
            category = 'json_syntax'
        elif message.startswith('No complete quiz array') or message.startswith('The quiz must start'):
            category = 'json_array_shape'
        elif message.startswith('More than one quiz array'):
            category = 'multiple_arrays'
        elif 'Quiz title:' in message:
            category = 'title_fields'
        elif re.search(r'Question\s+\d+', message):
            category = 'question_fields'
        else:
            category = 'validation_contract'
        return category, max(1, len(message.splitlines()))

    @staticmethod
    def _json_syntax_error(validation_error):
        """Return the parser error only for a proven JSON syntax failure."""
        cause = validation_error.__cause__
        if isinstance(cause, QuizError) and isinstance(cause.__cause__, JSONDecodeError):
            return cause.__cause__
        return None

    @staticmethod
    def _safe_parser_message(parser_error):
        # JSONDecodeError.msg is parser-generated and does not contain source text.
        message = str(parser_error.msg)
        message = ''.join(char for char in message if char.isprintable())
        return message[:120] or 'JSON parser reported a syntax error.'

    @staticmethod
    def _repair_unescaped_inner_quotes(quiz_json):
        """Escape only provable interior quotes; never invent missing quiz content."""
        repaired, in_string, escaped, changed = [], False, False, False
        length = len(quiz_json)
        for index, char in enumerate(quiz_json):
            if not in_string:
                repaired.append(char)
                if char == '"':
                    in_string = True
                continue
            if escaped:
                repaired.append(char)
                escaped = False
                continue
            if char == '\\':
                repaired.append(char)
                escaped = True
                continue
            if char != '"':
                repaired.append(char)
                continue

            next_index = index + 1
            while next_index < length and quiz_json[next_index].isspace():
                next_index += 1
            next_char = quiz_json[next_index] if next_index < length else ''
            closes_string = bool(next_char) and next_char in ':}]'
            if next_char == ',':
                after_comma = next_index + 1
                while after_comma < length and quiz_json[after_comma].isspace():
                    after_comma += 1
                closes_string = (after_comma >= length
                                 or quiz_json[after_comma] in '"}]')
            if closes_string:
                repaired.append(char)
                in_string = False
            else:
                repaired.append('\\"')
                changed = True
        return ''.join(repaired) if changed else None

    @staticmethod
    def _escape_raw_string_controls(quiz_json):
        """Make literal control characters in otherwise complete JSON strings explicit."""
        repaired, in_string, escaped, changed = [], False, False, False
        for char in quiz_json:
            if not in_string:
                repaired.append(char)
                if char == '"':
                    in_string = True
                continue
            if escaped:
                repaired.append(char)
                escaped = False
            elif char == '\\':
                repaired.append(char)
                escaped = True
            elif char == '"':
                repaired.append(char)
                in_string = False
            elif char == '\n':
                repaired.append('\\n')
                changed = True
            elif char == '\r':
                repaired.append('\\r')
                changed = True
            elif char == '\t':
                repaired.append('\\t')
                changed = True
            else:
                repaired.append(char)
        return ''.join(repaired) if changed else None

    @staticmethod
    def _remove_trailing_json_commas(quiz_json):
        """Remove a comma only when its next non-space character closes a container."""
        repaired, in_string, escaped, changed = [], False, False, False
        length = len(quiz_json)
        for index, char in enumerate(quiz_json):
            if in_string:
                repaired.append(char)
                if escaped:
                    escaped = False
                elif char == '\\':
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
                repaired.append(char)
                continue
            if char == ',':
                next_index = index + 1
                while next_index < length and quiz_json[next_index].isspace():
                    next_index += 1
                if next_index < length and quiz_json[next_index] in '}]':
                    changed = True
                    continue
            repaired.append(char)
        return ''.join(repaired) if changed else None

    @staticmethod
    def _close_unfinished_json_containers(quiz_json):
        """Append only missing structural closers; never close an unfinished string."""
        stack, in_string, escaped = [], False, False
        for char in quiz_json:
            if in_string:
                if escaped:
                    escaped = False
                elif char == '\\':
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char in '[{':
                stack.append(char)
            elif char in ']}':
                if not stack or (char == ']' and stack[-1] != '[') or (char == '}' and stack[-1] != '{'):
                    return None
                stack.pop()
        if in_string or not stack:
            return None
        return quiz_json + ''.join(']' if opener == '[' else '}' for opener in reversed(stack))

    @classmethod
    def _repair_predictable_json_syntax(cls, quiz_json):
        """Apply only content-preserving, deterministic syntax repairs to an AI response."""
        candidate, changed = quiz_json, False
        for repair in (cls._repair_unescaped_inner_quotes, cls._escape_raw_string_controls,
                       cls._close_unfinished_json_containers, cls._remove_trailing_json_commas,
                       cls._close_unfinished_json_containers):
            repaired = repair(candidate)
            if repaired is not None:
                candidate, changed = repaired, True
        return candidate if changed else None

    async def _completed_lecture_result(self, response=None):
        response = response if response is not None else await self.locators.last_assistant_response_text()
        self._diagnostic('lecture_response_captured', response_length=len(response))
        try:
            quiz_json = self.extract_quiz_json(response)
        except AIStudioExtractionError:
            self._diagnostic('json_candidate_extraction_failed', candidate_count=0)
            raise
        self._diagnostic('json_candidate_extracted', candidate_count=1)
        self._diagnostic('quiz_validation_started')
        try:
            result = self._validate_quiz_json(quiz_json)
        except AIStudioValidationError as exc:
            repaired_json = (self._repair_predictable_json_syntax(quiz_json)
                             if self._json_syntax_error(exc) is not None else None)
            if repaired_json is not None:
                try:
                    result = self._validate_quiz_json(repaired_json)
                except AIStudioValidationError:
                    pass
                else:
                    self._diagnostic('quiz_json_syntax_repaired',
                                     repair='predictable_local_syntax_repair')
                    self._diagnostic('quiz_validation_succeeded', question_count=len(result.questions))
                    self._last_response_text = response
                    return result
            cause = exc.__cause__
            error_class = type(cause).__name__ if cause else type(exc).__name__
            category, issue_count = self._validation_diagnostic(cause or exc)
            parser_error = self._json_syntax_error(exc)
            details = {
                'error_class': error_class,
                'error_category': category,
                'issue_count': issue_count,
            }
            if parser_error is not None:
                details.update({
                    'candidate_count': 1,
                    'candidate_length': len(quiz_json),
                })
            if parser_error is not None:
                details.update({
                    'parser_exception_class': type(parser_error).__name__,
                    'parser_message': self._safe_parser_message(parser_error),
                    'line': parser_error.lineno,
                    'column': parser_error.colno,
                    'position': parser_error.pos,
                })
            self._diagnostic('quiz_validation_failed', **details)
            raise
        self._diagnostic('quiz_validation_succeeded', question_count=len(result.questions))
        self._last_response_text = response
        return result

    async def _run_lecture_turn(self, lecture_path):
        """Submit the lecture attachment once, with an intentionally empty prompt."""
        listener = self._arm_turn_response()
        try:
            self._diagnostic('lecture_upload_started', page_identity=f'{id(self.page):x}')
            filename = await self.locators.attach_lecture_file(lecture_path)
            attachment_ready = await self.locators.wait_for_attachment_token_count(filename)
            self._diagnostic('lecture_upload_completed', page_identity=f'{id(self.page):x}')
            generation_started = (attachment_ready is AttachmentReady.GENERATION_STARTED
                                  or attachment_ready == AttachmentReady.GENERATION_STARTED.value)
            if not generation_started:
                await self._wait_before_send()
                await self.locators.send_prompt()
            if not generation_started:
                await self.locators.wait_for_generation_start()
            await self.locators.wait_for_lecture_generation_complete()
            await self._confirm_turn_http_success(listener)
            if not await self._wait_for_completed_assistant_response():
                raise AIStudioResponseError(
                    'Google AI Studio finished without a completed response for the current lecture turn.'
                )
            return await self._completed_lecture_result(await self._stable_assistant_response())
        finally:
            self._disarm_turn_response(listener)

    async def generate_lecture(self, lecture_path):
        """Generate and validate one PDF-only lecture turn in this calibrated conversation."""
        if self.state is not AIStudioSessionState.CALIBRATION_COMPLETE:
            raise RuntimeError('Lecture generation requires completed calibration.')
        try:
            result = await self._run_lecture_turn(lecture_path)
            self._transition(AIStudioSessionState.GENERATION_COMPLETE)
            return result
        except (AIStudioControlNotFoundError, AIStudioExtractionError, AIStudioGenerationError,
                AIStudioGenerationTimeoutError, AIStudioResponseError, AIStudioUploadError,
                AIStudioValidationError):
            if self.state is not AIStudioSessionState.FAILED:
                self._transition(AIStudioSessionState.FAILED)
            raise
        except Exception as exc:
            if self.state is not AIStudioSessionState.FAILED:
                self._transition(AIStudioSessionState.FAILED)
            raise AIStudioConnectionError('MCQ Maker could not generate the lecture quiz.') from exc

    async def close(self):
        if self.page is not None and self._owns_page:
            try:
                await self.page.close()
            except Exception:
                pass
            self.page = None
        if self.state is not AIStudioSessionState.CLOSED:
            if self.state not in {AIStudioSessionState.FAILED, AIStudioSessionState.CANCELLED,
                                  AIStudioSessionState.FRESH_CONVERSATION,
                                  AIStudioSessionState.CONFIGURATION_COMPLETE,
                                  AIStudioSessionState.CALIBRATION_COMPLETE,
                                  AIStudioSessionState.GENERATION_COMPLETE}:
                self._transition(AIStudioSessionState.FAILED)
            self._transition(AIStudioSessionState.CLOSED)
