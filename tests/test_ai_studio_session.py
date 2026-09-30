import asyncio
from pathlib import Path
from threading import Event
import unittest
from unittest.mock import patch

from mcq_maker.ai_studio_browser import AIStudioReadiness
from mcq_maker.ai_studio_errors import (AIStudioBrowserLaunchError, AIStudioCalibrationError, AIStudioCancelledError,
                                        AIStudioControlNotFoundError, AIStudioExtractionError,
                                        AIStudioGenerationError, AIStudioGenerationTimeoutError,
                                        AIStudioProfileInUseError, AIStudioResponseError, AIStudioValidationError)
from mcq_maker.ai_studio_selectors import AttachmentReady, ThinkingDiscovery
from mcq_maker.default_resources import DefaultResourceStore
from mcq_maker.ai_studio_session import (AIStudioConversationSession, AIStudioSessionState,
                                         AIStudioConfiguration, AIStudioDiscovery)


class FakePage:
    def __init__(self, url='https://aistudio.google.com/u/0/prompts/new_chat'):
        self.url = url
        self.closed = False
        self.goto_calls = []

    async def goto(self, url, wait_until, timeout=None):
        self.url = url
        self.goto_calls.append((url, wait_until, timeout))

    async def close(self):
        self.closed = True


class InitializationRacePage(FakePage):
    def __init__(self, url='https://aistudio.google.com/u/0/prompts/new_chat'):
        super().__init__(url)
        self.handlers = {}
        self.startup_responses_seen = False

    def on(self, event, handler):
        self.handlers[event] = handler

    def remove_listener(self, event, _handler):
        self.handlers.pop(event, None)

    async def goto(self, url, wait_until, timeout=None):
        await super().goto(url, wait_until, timeout)
        self.startup_responses_seen = 'response' in self.handlers
        if not self.startup_responses_seen:
            return
        for fragment in ('GenerateAccessToken', 'ListModels', 'GetUserPreferences'):
            self.handlers['response'](type('Response', (), {
                'url': f'https://example.test/{fragment}', 'status': 200
            })())


class FakeContext:
    def __init__(self, page, *, pages=None):
        self.page = page
        self.pages = [] if pages is None else list(pages)
        self.new_page_calls = 0
    async def new_page(self):
        self.new_page_calls += 1
        return self.page


class FakeManager:
    def __init__(self, page): self.context = FakeContext(page)
    async def ensure_connected(self, cancellation=None): return self.context
    def classify_ai_studio_url(self, url):
        return AIStudioReadiness.AI_STUDIO_REACHED if 'aistudio.google.com' in url else AIStudioReadiness.UNKNOWN


class FakeLocators:
    navigation_timeout = 120.0
    def __init__(self, page): self.page = page
    async def wait_for_landing_screen(self): pass
    async def wait_for_initialization(self, cancellation=None, *, listener=None): pass
    def arm_initialization_listener(self): return object()
    def disarm_initialization_listener(self, _listener): pass
    async def wait_for_prompt_input(self, cancellation=None): return object()
    async def prompt_is_interactive(self, prompt): return True
    async def has_existing_conversation_messages(self): return False
    async def landing_screen_visible(self): return False
    async def prompt_text(self, prompt): return ''
    async def open_run_settings_panel(self): pass
    async def close_run_settings_panel(self): pass
    async def open_fresh_playground(self): pass
    async def current_model(self): return 'Gemini Pro'
    async def discover_models(self): return ('Gemini Pro', 'Gemini Flash')
    async def discover_thinking(self): return ThinkingDiscovery(True, 'High', ('Low', 'High'))
    async def current_thinking_level(self): return ThinkingDiscovery(True, 'High', None)


class InitializationRaceLocators(FakeLocators):
    def __init__(self, page):
        super().__init__(page)
        self.observed = set()

    def arm_initialization_listener(self):
        def on_response(response):
            for fragment in ('GenerateAccessToken', 'ListModels', 'GetUserPreferences'):
                if fragment in response.url and response.status == 200:
                    self.observed.add(fragment)
        self.page.on('response', on_response)
        return on_response

    async def wait_for_initialization(self, cancellation=None, *, listener=None):
        required = {'GenerateAccessToken', 'ListModels', 'GetUserPreferences'}
        if self.observed != required:
            raise AssertionError('Initialization responses fired before the observer was armed.')
        return True

    def disarm_initialization_listener(self, listener):
        self.page.remove_listener('response', listener)


class MissingPromptLocators(FakeLocators):
    async def wait_for_prompt_input(self, cancellation=None): return None


class ConfigLocators(FakeLocators):
    def __init__(self, page, *, thinking_changed, model_changed=False):
        super().__init__(page)
        self.thinking_changed = thinking_changed
        self.model_changed = model_changed
        self.calls = []

    async def set_thinking_level(self, value):
        self.calls.append(('thinking', value))
        return self.thinking_changed

    async def set_model(self, value):
        self.calls.append(('model', value))
        return self.model_changed

    async def wait_for_thinking_level_ready(self):
        self.calls.append(('thinking_ready',))
        return 'Medium'


class CalibrationLocators(ConfigLocators):
    def __init__(self, page, response):
        super().__init__(page, thinking_changed=False)
        self.response = response

    async def fill_prompt(self, _text): self.calls.append(('fill',))
    async def attach_reference_file(self, _path): self.calls.append(('attach',)); return 'reference.md'
    async def wait_for_attachment_token_count(self, filename): self.calls.append(('tokens', filename))
    async def send_prompt(self): self.calls.append(('send',))
    async def wait_for_generation_start(self): self.calls.append(('started',))
    async def wait_for_generation_complete(self): self.calls.append(('completed',))
    async def last_assistant_response_text(self): return self.response
    async def has_transient_generation_error(self): return False
    async def regenerate_after_transient_error(self): return False
    async def assistant_response_container_count(self): return 1


class RegeneratingCalibrationLocators(CalibrationLocators):
    def __init__(self, page, responses):
        super().__init__(page, '')
        self.responses = iter(responses)
        self.regenerate_clicks = 0

    async def last_assistant_response_text(self):
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response

    async def has_transient_generation_error(self): return True

    async def regenerate_after_transient_error(self):
        self.regenerate_clicks += 1
        return True

    def arm_generation_request_listener(self):
        return object()

    async def wait_for_generation_request(self, _listener, timeout=10.0):
        return True

    def disarm_generation_request_listener(self, _listener):
        return None

    async def execute_prompt_with_keyboard(self):
        return False


class NoNetworkSignalLocators(RegeneratingCalibrationLocators):
    async def wait_for_generation_request(self, _listener, timeout=10.0):
        return False

    async def execute_prompt_with_keyboard(self):
        return False


class GlobalFallbackLocators(RegeneratingCalibrationLocators):
    def __init__(self, page):
        super().__init__(page, ['CALIBRATION COMPLETE'])
        self.keyboard_executes = 0

    async def regenerate_after_transient_error(self):
        self.regenerate_clicks += 1
        return False

    async def execute_prompt_with_keyboard(self):
        self.keyboard_executes += 1
        return True

    async def wait_for_generation_request(self, _listener, timeout=10.0):
        return True


class GenerationLocators(CalibrationLocators):
    def __init__(self, page, response):
        super().__init__(page, 'CALIBRATION COMPLETE')
        self.generation_response = response
        self.lecture_timeout = False

    async def attach_lecture_file(self, _path): self.calls.append(('lecture_attach',)); return 'lecture.pdf'
    async def wait_for_lecture_generation_complete(self):
        self.calls.append(('lecture_completed',))
        if self.lecture_timeout:
            raise AIStudioGenerationTimeoutError('timed out')
    async def last_assistant_response_text(self):
        if self.calls and self.calls[-1] == ('lecture_completed',):
            return self.generation_response
        return self.response


class CorrectingGenerationLocators(GenerationLocators):
    def __init__(self, page, responses):
        super().__init__(page, 'CALIBRATION COMPLETE')
        self.lecture_responses = iter(responses)
        self.lecture_response = 'CALIBRATION COMPLETE'
        self.lecture_sends = 0
        self.correction_prompts = []

    async def fill_prompt(self, text):
        self.calls.append(('fill',))
        if 'previous response was not valid JSON' in text:
            self.correction_prompts.append(text)

    async def send_prompt(self):
        self.calls.append(('send',))
        self.lecture_sends += 1

    async def wait_for_lecture_generation_complete(self):
        self.calls.append(('lecture_completed',))
        self.lecture_response = next(self.lecture_responses)

    async def last_assistant_response_text(self):
        if self.calls and self.calls[-1] == ('lecture_completed',):
            return self.lecture_response
        return self.response


class SilentResendLocators(GenerationLocators):
    def __init__(self, page, response, presence):
        super().__init__(page, response)
        self.presence = iter(presence)

    async def has_completed_assistant_response(self):
        return next(self.presence)


class SilentProbeFailureLocators(SilentResendLocators):
    async def has_transient_generation_error(self):
        raise RuntimeError('response error probe unavailable')


class EarlyGenerationLocators(GenerationLocators):
    async def wait_for_attachment_token_count(self, filename):
        self.calls.append(('tokens', filename))
        return AttachmentReady.GENERATION_STARTED

    async def wait_for_generation_start(self):
        raise AssertionError('Generation start was already observed during attachment processing.')


class AIStudioSessionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        async def no_send_pause(_session):
            return None
        self._send_pause_patch = patch.object(AIStudioConversationSession, '_wait_before_send', no_send_pause)
        self._send_pause_patch.start()
        self.addCleanup(self._send_pause_patch.stop)

    async def test_discovery_uses_only_legal_phase_three_transitions(self):
        session = AIStudioConversationSession(FakeManager(FakePage()), locator_factory=FakeLocators)
        result = await session.discover_capabilities()
        self.assertEqual(result, AIStudioDiscovery('Gemini Pro', None, True, 'High', None))
        self.assertEqual(session.state, AIStudioSessionState.FRESH_CONVERSATION)
        await session.close()
        self.assertEqual(session.state, AIStudioSessionState.CLOSED)

    async def test_discovery_preserves_specific_browser_manager_failures(self):
        for error_type, message in (
            (AIStudioBrowserLaunchError, 'MCQ Maker could not start its dedicated AI Studio Brave browser.'),
            (AIStudioProfileInUseError, 'The dedicated MCQ Maker Brave window is already open.'),
        ):
            class BrokenManager(FakeManager):
                async def ensure_connected(self, cancellation=None):
                    raise error_type(message)
            session = AIStudioConversationSession(BrokenManager(FakePage()), locator_factory=FakeLocators)
            with self.assertRaises(error_type) as caught:
                await session.discover_capabilities()
            self.assertEqual(str(caught.exception), message)

    def test_navigation_failure_summary_distinguishes_known_network_errors(self):
        self.assertEqual(
            AIStudioConversationSession._navigation_failure_message(
                RuntimeError('Page.goto: net::ERR_CONNECTION_TIMED_OUT')
            ),
            'Google AI Studio navigation failed with ERR_CONNECTION_TIMED_OUT.',
        )
        self.assertIn(
            'document-ready state',
            AIStudioConversationSession._navigation_failure_message(TimeoutError()),
        )

    async def test_initialization_listener_is_armed_before_navigation(self):
        page = InitializationRacePage()
        session = AIStudioConversationSession(
            FakeManager(page), locator_factory=InitializationRaceLocators
        )
        await session.discover_capabilities()
        self.assertTrue(page.startup_responses_seen)
        self.assertEqual(page.goto_calls[0][2], 120_000)

    async def test_discovery_reuses_existing_dedicated_browser_tab(self):
        existing = FakePage('about:blank')
        manager = FakeManager(FakePage())
        manager.context = FakeContext(FakePage(), pages=[existing])
        session = AIStudioConversationSession(manager, locator_factory=FakeLocators)
        await session.discover_capabilities()
        self.assertIs(session.page, existing)
        self.assertEqual(manager.context.new_page_calls, 0)

    async def test_illegal_transition_is_rejected(self):
        session = AIStudioConversationSession(FakeManager(FakePage()), locator_factory=FakeLocators)
        with self.assertRaises(RuntimeError):
            session._transition(AIStudioSessionState.THINKING_DISCOVERED)

    async def test_cancelled_session_never_opens_a_page(self):
        session = AIStudioConversationSession(FakeManager(FakePage()), locator_factory=FakeLocators)
        cancelled = Event(); cancelled.set()
        with self.assertRaises(AIStudioCancelledError):
            await session.discover_capabilities(cancelled)
        self.assertEqual(session.state, AIStudioSessionState.CANCELLED)

    async def test_missing_prompt_is_a_safe_control_error(self):
        session = AIStudioConversationSession(FakeManager(FakePage()), locator_factory=MissingPromptLocators)
        with self.assertRaises(AIStudioControlNotFoundError):
            await session.discover_capabilities()
        self.assertEqual(session.state, AIStudioSessionState.FAILED)

    async def test_successful_thinking_change_reaches_configuration_complete(self):
        locators = ConfigLocators(FakePage(), thinking_changed=True)
        session = AIStudioConversationSession(FakeManager(FakePage()), locator_factory=lambda _page: locators)
        await session.discover_capabilities()
        result = await session.configure_defaults()
        self.assertEqual(session.state, AIStudioSessionState.CONFIGURATION_COMPLETE)
        self.assertEqual(result, AIStudioConfiguration('Gemini 3.8 Flash', 'High', False, True))
        self.assertEqual(locators.calls, [('model', 'Gemini 3.8 Flash'), ('thinking', 'High')])

    async def test_model_change_waits_for_dependent_thinking_control_before_selection(self):
        locators = ConfigLocators(FakePage(), thinking_changed=True, model_changed=True)
        session = AIStudioConversationSession(FakeManager(FakePage()), locator_factory=lambda _page: locators)
        await session.discover_capabilities()
        result = await session.configure_defaults()
        self.assertTrue(result.model_changed)
        self.assertEqual(locators.calls, [
            ('model', 'Gemini 3.8 Flash'), ('thinking_ready',), ('thinking', 'High'),
        ])

    async def test_setup_opens_and_closes_run_settings_once(self):
        locators = ConfigLocators(FakePage(), thinking_changed=True)
        locators.open_calls = 0
        locators.close_calls = 0
        async def open_panel():
            locators.open_calls += 1
        async def close_panel():
            locators.close_calls += 1
        locators.open_run_settings_panel = open_panel
        locators.close_run_settings_panel = close_panel
        session = AIStudioConversationSession(FakeManager(FakePage()), locator_factory=lambda _page: locators)
        await session.discover_capabilities()
        await session.configure_defaults()
        self.assertEqual(locators.open_calls, 1)
        self.assertEqual(locators.close_calls, 1)

    async def test_discovery_reads_selected_values_without_opening_option_pickers(self):
        locators = ConfigLocators(FakePage(), thinking_changed=True)
        locators.calls.clear()
        async def unexpected_model_discovery():
            raise AssertionError('Discovery must not open the model picker just to enumerate options.')
        async def unexpected_thinking_discovery():
            raise AssertionError('Discovery must not open the Thinking Level picker just to enumerate options.')
        locators.discover_models = unexpected_model_discovery
        locators.discover_thinking = unexpected_thinking_discovery
        session = AIStudioConversationSession(FakeManager(FakePage()), locator_factory=lambda _page: locators)
        result = await session.discover_capabilities()
        self.assertEqual(result.available_models, None)
        self.assertEqual(result.available_thinking_levels, None)
        self.assertEqual(locators.calls, [])
        await session.configure_defaults()
        self.assertEqual(locators.calls, [('model', 'Gemini 3.8 Flash'), ('thinking', 'High')])

    async def test_already_correct_thinking_skips_the_change(self):
        locators = ConfigLocators(FakePage(), thinking_changed=False)
        session = AIStudioConversationSession(FakeManager(FakePage()), locator_factory=lambda _page: locators)
        await session.discover_capabilities()
        result = await session.configure_defaults()
        self.assertFalse(result.thinking_changed)
        self.assertEqual(session.state, AIStudioSessionState.CONFIGURATION_COMPLETE)

    async def test_response_preview_uses_validated_assistant_response(self):
        locators = CalibrationLocators(FakePage(), 'CALIBRATION COMPLETE. Ready for lecture files.')
        session = AIStudioConversationSession(FakeManager(FakePage()), locator_factory=lambda _page: locators)
        await session.discover_capabilities()
        await session.configure_defaults()
        await session.run_calibration('instruction', 'reference.md')
        self.assertEqual(session.response_preview(20), 'CALIBRATION COMPLETE')

    async def test_successful_calibration_reaches_complete_state(self):
        locators = CalibrationLocators(FakePage(), 'CALIBRATION COMPLETE')
        session = AIStudioConversationSession(FakeManager(FakePage()), locator_factory=lambda _page: locators)
        await session.discover_capabilities()
        await session.configure_defaults()
        await session.run_calibration('instruction', 'reference.md')
        self.assertEqual(session.state, AIStudioSessionState.CALIBRATION_COMPLETE)
        self.assertEqual(locators.calls[-5:], [('attach',), ('tokens', 'reference.md'), ('send',), ('started',), ('completed',)])

    def test_calibration_confirmation_ignores_ai_studio_header_and_footer(self):
        self.assertTrue(AIStudioConversationSession._has_calibration_confirmation(
            'Model Â· 7:45 AM\nCalibration complete. The model is ready for the lecture.\n'
            'Google AI models may make mistakes, so double-check outputs.'
        ))

    def test_calibration_confirmation_accepts_full_prompt_mandated_response(self):
        full_response = (
            'CALIBRATION COMPLETE.\n'
            'I have analyzed the Reference Style, Topic Frequency Map, and JSON Structure.\n'
            'CURRENT MODE: STANDBY.\n'
            'OUTPUT FORMAT SETTING: Raw JSON Array ONLY.\n'
            'Please upload your Lecture/Content Files to begin the comprehensive extraction.'
        )
        self.assertTrue(AIStudioConversationSession._has_calibration_confirmation(full_response))

    async def test_missing_calibration_confirmation_is_a_safe_error(self):
        locators = CalibrationLocators(FakePage(), 'Calibration is pending')
        session = AIStudioConversationSession(FakeManager(FakePage()), locator_factory=lambda _page: locators)
        await session.discover_capabilities()
        await session.configure_defaults()
        with self.assertRaises(AIStudioCalibrationError):
            await session.run_calibration('instruction', 'reference.md')
        self.assertEqual(session.state, AIStudioSessionState.FAILED)

    async def test_prompt_protocol_with_calibration_phrase_is_not_confirmation(self):
        locators = CalibrationLocators(
            FakePage(), 'CALIBRATION COMPLETE. INTERNAL QA — PRE-OUTPUT CHECKS'
        )
        session = AIStudioConversationSession(FakeManager(FakePage()), locator_factory=lambda _page: locators)
        await session.discover_capabilities()
        await session.configure_defaults()
        with self.assertRaises(AIStudioCalibrationError):
            await session.run_calibration('instruction', 'reference.md')
        self.assertEqual(session.state, AIStudioSessionState.FAILED)

    async def test_ambiguous_calibration_response_does_not_resend_or_open_a_new_chat(self):
        locators = SilentResendLocators(
            FakePage(), 'CALIBRATION COMPLETE', [False, False, True]
        )
        session = AIStudioConversationSession(FakeManager(FakePage()), locator_factory=lambda _page: locators)
        session._wait_before_silent_resend = lambda: asyncio.sleep(0)
        presence = iter([False, False, True])
        session._wait_for_completed_assistant_response = lambda: asyncio.sleep(0, result=next(presence))
        await session.discover_capabilities()
        await session.configure_defaults()
        navigation_count = len(locators.page.goto_calls)
        with self.assertRaises(AIStudioResponseError):
            await session.run_calibration('instruction', 'reference.md')
        self.assertEqual([call for call in locators.calls if call == ('send',)],
                         [('send',)])
        self.assertEqual(len(locators.page.goto_calls), navigation_count)

    async def test_silent_lecture_send_escalates_after_two_resends(self):
        locators = SilentResendLocators(
            FakePage(), '[{"title":"T"},{"question":"Q","options":["A","B"],"correct":0,"explanation":"E"}]',
            [True, False, False, False]
        )
        session = AIStudioConversationSession(FakeManager(FakePage()), locator_factory=lambda _page: locators)
        session._wait_before_silent_resend = lambda: asyncio.sleep(0)
        presence = iter([True, False, False, False])
        session._wait_for_completed_assistant_response = lambda: asyncio.sleep(0, result=next(presence))
        await session.discover_capabilities()
        await session.configure_defaults()
        await session.run_calibration('instruction', 'reference.md')
        with self.assertRaises(AIStudioResponseError):
            await session.generate_lecture('lecture.pdf')
        self.assertEqual([call for call in locators.calls if call == ('lecture_attach',)],
                         [('lecture_attach',)])

    async def test_failed_response_probe_does_not_restart_calibration(self):
        locators = SilentProbeFailureLocators(
            FakePage(), 'CALIBRATION COMPLETE', [False] * 6
        )
        session = AIStudioConversationSession(FakeManager(FakePage()), locator_factory=lambda _page: locators)
        session._wait_before_silent_resend = lambda: asyncio.sleep(0)
        session._wait_for_completed_assistant_response = lambda: asyncio.sleep(0, result=False)
        await session.discover_capabilities()
        await session.configure_defaults()
        navigation_count = len(locators.page.goto_calls)
        with self.assertRaises(AIStudioResponseError):
            await session.run_calibration('instruction', 'reference.md')
        self.assertEqual([call for call in locators.calls if call == ('send',)],
                         [('send',)])
        self.assertEqual(len(locators.page.goto_calls), navigation_count)

    async def test_early_generation_start_skips_send_for_calibration_and_lecture(self):
        response = '[{"title":"T"},{"question":"Q","options":["A","B"],"correct":0,"explanation":"E"}]'
        locators = EarlyGenerationLocators(FakePage(), response)
        session = AIStudioConversationSession(FakeManager(FakePage()), locator_factory=lambda _page: locators)
        await session.discover_capabilities()
        await session.configure_defaults()
        navigation_count = len(locators.page.goto_calls)
        await session.run_calibration('instruction', 'reference.md')
        result = await session.generate_lecture('lecture.pdf')
        self.assertEqual(result.title, 'T')
        self.assertEqual([call for call in locators.calls if call == ('send',)], [])

    async def test_reused_response_wrapper_does_not_hide_current_calibration_response(self):
        page = FakePage()
        locators = CalibrationLocators(page, 'CALIBRATION COMPLETE')
        session = AIStudioConversationSession(FakeManager(page), locator_factory=lambda _page: locators)
        await session.discover_capabilities()
        await session.configure_defaults()
        navigation_count = len(page.goto_calls)
        await session.run_calibration('instruction', 'reference.md')
        self.assertEqual(session.state, AIStudioSessionState.CALIBRATION_COMPLETE)
        self.assertEqual([call for call in locators.calls if call == ('send',)], [('send',)])
        self.assertEqual(len(page.goto_calls), navigation_count)

    def test_extract_quiz_json_uses_outer_array(self):
        response = 'Here is the quiz:\n```json\n[{"title":"T"},{"question":"Q"}]\n```'
        self.assertEqual(AIStudioConversationSession.extract_quiz_json(response), '[{"title":"T"},{"question":"Q"}]')

    def test_extract_quiz_json_without_array_is_safe_error(self):
        with self.assertRaises(AIStudioExtractionError):
            AIStudioConversationSession.extract_quiz_json('No quiz returned.')

    def test_local_quote_repair_escapes_only_unambiguous_interior_quotes(self):
        raw = '[{"title":"T"},{"question":"Q","options":["A","B"],"correct":0,"explanation":"The term "beta" is important."}]'
        repaired = AIStudioConversationSession._repair_unescaped_inner_quotes(raw)
        self.assertEqual(repaired, '[{"title":"T"},{"question":"Q","options":["A","B"],"correct":0,"explanation":"The term \\"beta\\" is important."}]')

    def test_local_quote_repair_never_completes_a_truncated_string(self):
        truncated = '[{"title":"T"},{"question":"Q","options":["A","B"],"correct":0,"explanation":"unfinished'
        self.assertIsNone(AIStudioConversationSession._repair_unescaped_inner_quotes(truncated))

    def test_predictable_local_repair_removes_trailing_comma_and_closes_array(self):
        raw = '[{"title":"T"},{"question":"Q","options":["A","B"],"correct":0,"explanation":"E"},'
        repaired = AIStudioConversationSession._repair_predictable_json_syntax(raw)
        self.assertEqual(repaired, '[{"title":"T"},{"question":"Q","options":["A","B"],"correct":0,"explanation":"E"}]')

    def test_predictable_local_repair_does_not_close_an_unfinished_string(self):
        raw = '[{"title":"T"},{"question":"Q","options":["A","B"],"correct":0,"explanation":"unfinished'
        self.assertIsNone(AIStudioConversationSession._repair_predictable_json_syntax(raw))

    async def test_unescaped_interior_quote_is_repaired_locally_before_validation(self):
        response = '[{"title":"T"},{"question":"Q","options":["A","B"],"correct":0,"explanation":"The term "beta" is important."}]'
        locators = GenerationLocators(FakePage(), response)
        session = AIStudioConversationSession(FakeManager(FakePage()), locator_factory=lambda _page: locators)
        diagnostics = []
        session.diagnostic_callback = lambda stage, metadata: diagnostics.append((stage, metadata))
        await session.discover_capabilities()
        await session.configure_defaults()
        await session.run_calibration('instruction', 'reference.md')
        result = await session.generate_lecture('lecture.pdf')
        self.assertEqual(result.questions[0]['explanation'], 'The term "beta" is important.')
        self.assertIn(('quiz_json_syntax_repaired', {'repair': 'predictable_local_syntax_repair'}), diagnostics)
        self.assertEqual([call for call in locators.calls if call == ('send',)], [('send',), ('send',)])

    async def test_generation_reaches_complete_and_returns_validated_data(self):
        response = '[{"title":"T"},{"question":"Q","options":["A","B"],"correct":0,"explanation":"E"}]'
        locators = GenerationLocators(FakePage(), response)
        session = AIStudioConversationSession(FakeManager(FakePage()), locator_factory=lambda _page: locators)
        diagnostics = []
        session.diagnostic_callback = lambda stage, metadata: diagnostics.append((stage, metadata))
        await session.discover_capabilities()
        await session.configure_defaults()
        await session.run_calibration('instruction', 'reference.md')
        locators.calls.clear()
        result = await session.generate_lecture('lecture.pdf')
        self.assertEqual(result.title, 'T')
        self.assertEqual(session.state, AIStudioSessionState.GENERATION_COMPLETE)
        post_generation = [(stage, metadata) for stage, metadata in diagnostics if stage in {
            'lecture_response_captured', 'json_candidate_extracted',
            'quiz_validation_started', 'quiz_validation_succeeded',
        }]
        self.assertEqual([stage for stage, _ in post_generation], [
            'lecture_response_captured', 'json_candidate_extracted',
            'quiz_validation_started', 'quiz_validation_succeeded',
        ])
        self.assertEqual(post_generation[0][1], {'response_length': len(response)})
        self.assertEqual(post_generation[-1][1], {'question_count': 1})
        self.assertNotIn(response, repr(diagnostics))
        self.assertEqual([call for call in locators.calls if call == ('lecture_attach',)], [('lecture_attach',)])
        self.assertEqual([call for call in locators.calls if call == ('send',)], [('send',)])

    def test_authoritative_prompt_keeps_calibration_and_json_output_rules_separate(self):
        prompt_path = DefaultResourceStore().bundled_path('prompt')
        prompt = prompt_path.read_text(encoding='utf-8')
        self.assertIn('PHASE 1 REQUIRED RESPONSE', prompt)
        self.assertIn('For lecture generation, return exactly one complete JSON array', prompt)
        self.assertIn('parsable by standard JSON.parse or Python json.loads', prompt)
        self.assertIn('valid quoting, escaping, commas, and brackets', prompt)
        self.assertIn('FINAL DELIVERY CHECK', prompt)
        self.assertIn('there is no trailing comma', prompt)

    async def test_session_refuses_a_second_lecture_turn(self):
        response = '[{"title":"T"},{"question":"Q","options":["A","B"],"correct":0,"explanation":"E"}]'
        locators = GenerationLocators(FakePage(), response)
        session = AIStudioConversationSession(FakeManager(FakePage()), locator_factory=lambda _page: locators)
        await session.discover_capabilities()
        await session.configure_defaults()
        await session.run_calibration('instruction', 'reference.md')
        await session.generate_lecture('lecture-1.pdf')
        with self.assertRaises(RuntimeError):
            await session.generate_lecture('lecture-2.pdf')
        self.assertEqual(session.state, AIStudioSessionState.GENERATION_COMPLETE)
        self.assertEqual([call for call in locators.calls if call == ('lecture_attach',)],
                         [('lecture_attach',)])

    async def test_generation_validation_failure_is_wrapped(self):
        locators = GenerationLocators(FakePage(), '[{"title":"T"}]')
        session = AIStudioConversationSession(
            FakeManager(FakePage()), locator_factory=lambda _page: locators,
            quiz_validator=lambda _text: (_ for _ in ()).throw(ValueError('invalid quiz')),
        )
        diagnostics = []
        session.diagnostic_callback = lambda stage, metadata: diagnostics.append((stage, metadata))
        await session.discover_capabilities()
        await session.configure_defaults()
        await session.run_calibration('instruction', 'reference.md')
        with self.assertRaises(AIStudioValidationError):
            await session.generate_lecture('lecture.pdf')
        self.assertEqual(session.state, AIStudioSessionState.FAILED)
        self.assertEqual(diagnostics[-1], ('quiz_validation_failed', {
            'error_class': 'ValueError', 'error_category': 'validation_contract', 'issue_count': 1,
        }))

    async def test_json_syntax_failure_stays_local_and_sends_no_correction(self):
        malformed = '[{"title":"T"},{"question":}]'
        locators = CorrectingGenerationLocators(FakePage(), [malformed])
        session = AIStudioConversationSession(FakeManager(FakePage()), locator_factory=lambda _page: locators)
        diagnostics = []
        session.diagnostic_callback = lambda stage, metadata: diagnostics.append((stage, metadata))
        await session.discover_capabilities()
        await session.configure_defaults()
        await session.run_calibration('instruction', 'reference.md')
        locators.calls.clear()

        with self.assertRaises(AIStudioValidationError):
            await session.generate_lecture('lecture.pdf')

        self.assertEqual(session.state, AIStudioSessionState.FAILED)
        self.assertEqual(locators.lecture_sends, 2)  # calibration and the one PDF-only lecture turn
        self.assertEqual(locators.correction_prompts, [])
        self.assertEqual([call for call in locators.calls if call == ('lecture_attach',)], [('lecture_attach',)])
        syntax = next(metadata for stage, metadata in diagnostics if stage == 'quiz_validation_failed')
        self.assertEqual(syntax['error_category'], 'json_syntax')
        self.assertEqual(syntax['parser_exception_class'], 'JSONDecodeError')
        self.assertGreater(syntax['line'], 0)
        self.assertGreater(syntax['column'], 0)
        self.assertGreater(syntax['position'], 0)
        self.assertEqual(syntax['candidate_length'], len(malformed))
        self.assertEqual(syntax['candidate_count'], 1)
        self.assertNotIn(malformed, repr(diagnostics))
        self.assertEqual(diagnostics[-1][0], 'quiz_validation_failed')

    async def test_schema_failure_does_not_request_json_syntax_correction(self):
        locators = GenerationLocators(FakePage(), '[{"title":"T"}]')
        session = AIStudioConversationSession(FakeManager(FakePage()), locator_factory=lambda _page: locators)
        await session.discover_capabilities()
        await session.configure_defaults()
        await session.run_calibration('instruction', 'reference.md')
        with self.assertRaises(AIStudioValidationError):
            await session.generate_lecture('lecture.pdf')
        self.assertEqual([call for call in locators.calls if call == ('send',)], [('send',), ('send',)])
        self.assertEqual([call for call in locators.calls if call == ('lecture_attach',)], [('lecture_attach',)])

    async def test_missing_json_candidate_does_not_request_correction(self):
        locators = GenerationLocators(FakePage(), 'The answer is not available.')
        session = AIStudioConversationSession(FakeManager(FakePage()), locator_factory=lambda _page: locators)
        await session.discover_capabilities()
        await session.configure_defaults()
        await session.run_calibration('instruction', 'reference.md')
        with self.assertRaises(AIStudioExtractionError):
            await session.generate_lecture('lecture.pdf')
        self.assertEqual([call for call in locators.calls if call == ('send',)], [('send',), ('send',)])
        self.assertEqual([call for call in locators.calls if call == ('lecture_attach',)], [('lecture_attach',)])

    async def test_provider_error_does_not_start_json_correction(self):
        class ProviderFailureLocators(GenerationLocators):
            def __init__(self, page):
                super().__init__(page, '[{"title":"T"}]')
                self.send_count = 0
            async def send_prompt(self):
                self.send_count += 1
                self.calls.append(('send',))
                if self.send_count > 1:
                    raise AIStudioResponseError('HTTP 403')

        locators = ProviderFailureLocators(FakePage())
        session = AIStudioConversationSession(FakeManager(FakePage()), locator_factory=lambda _page: locators)
        await session.discover_capabilities()
        await session.configure_defaults()
        await session.run_calibration('instruction', 'reference.md')
        with self.assertRaises(AIStudioResponseError):
            await session.generate_lecture('lecture.pdf')
        self.assertEqual(locators.send_count, 2)
        self.assertEqual([call for call in locators.calls if call == ('lecture_attach',)], [('lecture_attach',)])

    async def test_generation_timeout_is_reported(self):
        locators = GenerationLocators(FakePage(), '[{"title":"T"}]')
        locators.lecture_timeout = True
        session = AIStudioConversationSession(FakeManager(FakePage()), locator_factory=lambda _page: locators)
        await session.discover_capabilities()
        await session.configure_defaults()
        await session.run_calibration('instruction', 'reference.md')
        with self.assertRaises(AIStudioGenerationTimeoutError):
            await session.generate_lecture('lecture.pdf')
        self.assertEqual(session.state, AIStudioSessionState.FAILED)

    def test_session_workflow_has_no_raw_playwright_selector_calls(self):
        source = Path(__file__).resolve().parents[1] / 'mcq_maker' / 'ai_studio_session.py'
        text = source.read_text(encoding='utf-8')
        self.assertNotIn('.locator(', text)
        self.assertNotIn('.get_by_role(', text)


if __name__ == '__main__':
    unittest.main()
