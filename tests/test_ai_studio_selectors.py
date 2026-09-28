import asyncio
import unittest
from unittest.mock import AsyncMock

from mcq_maker.ai_studio_errors import (AIStudioCancelledError, AIStudioConnectionError,
                                        AIStudioControlNotFoundError, AIStudioGenerationTimeoutError,
                                        AIStudioInitializationError,
                                        AIStudioInitializationRejectedError,
                                        AIStudioInitializationTimeoutError)
from mcq_maker.ai_studio_selectors import (AIStudioLocators, AttachmentReady, ThinkingDiscovery, first_line_option,
                                            has_token_count_text, has_transient_generation_error_text,
                                            is_generated_response_text, normalize_assistant_response_text,
                                            normalize_option, normalize_options)


class FakeLabel:
    def locator(self, _selector):
        return object()


class FakePage:
    class Keyboard:
        async def press(self, key, delay=None):
            self.last = (key, delay)

    def __init__(self):
        self.keyboard = self.Keyboard()
        self.events = {}
        self.mouse = type('Mouse', (), {'move': self._mouse_move})()

    async def _mouse_move(self, *_args, **_kwargs):
        return None

    def locator(self, _selector):
        return object()

    def on(self, event, handler):
        self.events[event] = handler

    def remove_listener(self, event, handler):
        self.events.pop(event, None)

    async def bring_to_front(self):
        return None

    async def wait_for_function(self, expression, *, arg=None, timeout=None):
        self.wait_expression = expression
        self.wait_argument = arg
        self.wait_timeout = timeout
        return True


class FakeRegenerateButton:
    def __init__(self):
        self.actions = []

    async def evaluate(self, expression):
        self.actions.append(('evaluate', expression))


class FakePrompt:
    def __init__(self):
        self.actions = []

    async def click(self):
        self.actions.append('click')

    async def fill(self, text):
        self.actions.append(('fill', text))

    async def press(self, key):
        self.actions.append(('press', key))


class ResponseCandidate:
    def __init__(self, details):
        self.details = details

    async def is_visible(self):
        return True

    async def evaluate(self, _expression):
        return self.details


class ResponseGroup:
    def __init__(self, candidates):
        self.candidates = candidates

    async def count(self):
        return len(self.candidates)

    def nth(self, index):
        return self.candidates[index]


class ResponsePage(FakePage):
    def __init__(self, groups):
        super().__init__()
        self.groups = groups
        self.wait_expression = None

    def locator(self, selector):
        if selector == 'ms-chat-turn':
            return ResponseGroup(self.groups)
        return ResponseGroup([])

    async def wait_for_function(self, expression, *, timeout):
        self.wait_expression = expression
        self.wait_timeout = timeout
        return True


class FakeSelectControl:
    def __init__(self):
        self.actions = []

    async def scroll_into_view_if_needed(self):
        self.actions.append('scroll')

    async def focus(self):
        self.actions.append('focus')

    async def evaluate(self, expression):
        self.actions.append(('evaluate', expression))

    async def inner_text(self):
        return 'High'


class ValueSelectControl(FakeSelectControl):
    def __init__(self, value):
        super().__init__()
        self.value = value

    async def inner_text(self):
        return self.value


class AIStudioSelectorUtilityTests(unittest.TestCase):
    def test_option_normalization_deduplicates_in_ui_order(self):
        rows = [
            {'text': ' Gemini   Pro ', 'role': 'option', 'disabled': False},
            {'text': 'Gemini Pro', 'role': 'option', 'disabled': False},
            {'text': 'Gemini Flash', 'role': 'option', 'disabled': False},
        ]
        self.assertEqual(normalize_options(rows), ('Gemini Pro', 'Gemini Flash'))

    def test_option_normalization_excludes_headings_and_disabled_rows(self):
        rows = [
            {'text': 'Models', 'role': 'heading', 'disabled': False},
            {'text': 'Search', 'role': 'textbox', 'disabled': False},
            {'text': 'Unavailable model', 'role': 'option', 'disabled': True},
            {'text': 'Available model', 'role': 'option', 'disabled': False},
        ]
        self.assertEqual(normalize_options(rows), ('Available model',))
        self.assertEqual(normalize_option('  High\n thinking '), 'High thinking')
        self.assertEqual(first_line_option('Gemini 3.8 Flash\ngemini-3.8-flash\nDescription'), 'Gemini 3.8 Flash')


class AIStudioLocatorTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _turn(text, *, thought=False, has_final_chunk=True):
        return {
            'text': text,
            'assistantMarker': True,
            'userMarker': False,
            'modelHeader': False,
            'afterLastUser': True,
            'thoughtOnly': thought,
            'bodyFound': True,
            'hasFinalChunk': has_final_chunk,
            'tagName': 'MS-CHAT-TURN',
            'id': '', 'testId': '', 'ariaLabel': '',
            'messageAuthor': '', 'role': '',
        }

    async def test_thought_preview_is_not_accepted_as_a_completed_answer(self):
        thought = self._turn(
            'Model 3:37 PM Thoughts Expand to view model thoughts 2.2s',
            thought=True, has_final_chunk=False,
        )
        page = ResponsePage([ResponseCandidate(thought)])
        locators = AIStudioLocators(page)
        self.assertFalse(await locators.has_completed_assistant_response())
        with self.assertRaises(Exception):
            await locators.last_assistant_response_text()

    async def test_final_answer_body_is_extracted_without_turn_chrome(self):
        thought = self._turn(
            'Model 3:37 PM Thoughts Expand to view model thoughts 2.2s',
            thought=True, has_final_chunk=False,
        )
        answer = self._turn(
            'CALIBRATION COMPLETE.\\nCURRENT MODE: STANDBY.',
            has_final_chunk=True,
        )
        page = ResponsePage([ResponseCandidate(thought), ResponseCandidate(answer)])
        locators = AIStudioLocators(page)
        details = await locators.last_assistant_response_diagnostic()
        self.assertEqual(details['extracted_text'], 'CALIBRATION COMPLETE.\\nCURRENT MODE: STANDBY.')
        self.assertEqual(details['candidate_index'], 1)
        self.assertFalse(details['element']['thoughtOnly'])

    async def test_response_wait_targets_final_answer_not_thought_preview(self):
        thought = self._turn('Thoughts: internal reasoning', thought=True, has_final_chunk=False)
        answer = self._turn('CALIBRATION COMPLETE', has_final_chunk=True)
        page = ResponsePage([ResponseCandidate(thought), ResponseCandidate(answer)])
        locators = AIStudioLocators(page)
        self.assertTrue(await locators.wait_for_completed_assistant_response(timeout=17))
        self.assertIn('thought-activity-host', page.wait_expression)
        self.assertIn('.turn-content', page.wait_expression)
        self.assertEqual(page.wait_timeout, 17000)

    async def test_thinking_control_prefers_live_accessible_mat_select(self):
        page = FakePage()
        selectors = []
        page.locator = lambda selector: selectors.append(selector) or object()
        candidates = AIStudioLocators(page)._thinking_control_candidates(FakeLabel())
        self.assertTrue(candidates)
        self.assertIn("ms-thinking-level-setting mat-select[role='combobox']", selectors[0])
        self.assertIn("aria-label*='thinking level'", selectors[0])

    async def test_thinking_control_opens_with_keyboard_instead_of_mouse_click(self):
        locators = AIStudioLocators(FakePage())
        control = FakeSelectControl()
        overlay = object()
        locators._human_pause = lambda: asyncio.sleep(0)
        locators._visible_overlay = lambda: asyncio.sleep(0, result=overlay)
        self.assertIs(await locators._open_thinking_overlay(control), overlay)
        self.assertEqual(control.actions, ['scroll', 'focus'])
        self.assertEqual(locators.page.keyboard.last, ('Enter', None))

    async def test_thinking_control_uses_native_dom_click_when_keyboard_does_not_open_it(self):
        locators = AIStudioLocators(FakePage())
        control = FakeSelectControl()
        overlay = object()
        results = iter([None, overlay])
        locators._human_pause = lambda: asyncio.sleep(0)
        locators._visible_overlay = lambda: asyncio.sleep(0, result=next(results))
        self.assertIs(await locators._open_thinking_overlay(control), overlay)
        self.assertEqual(control.actions, [
            'scroll', 'focus', ('evaluate', 'element => element.click()')
        ])

    async def test_thinking_verification_rereads_replaced_combobox_after_selection(self):
        """A selected Angular mat-select can replace its old host element."""
        locators = AIStudioLocators(FakePage())
        stale_control = ValueSelectControl('Medium')
        live_control = ValueSelectControl('High')
        option = object()
        locators.open_run_settings = AsyncMock()
        locators._wait_for_thinking_label = AsyncMock(return_value=FakeLabel())
        locators._first_usable = AsyncMock(side_effect=[stale_control, live_control])
        locators._open_thinking_overlay = AsyncMock(return_value=object())
        locators._matching_option = AsyncMock(return_value=option)
        locators._activate_select_option = AsyncMock()

        self.assertTrue(await locators.set_thinking_level('High'))
        locators._activate_select_option.assert_awaited_once_with(option)
        self.assertEqual(locators._first_usable.await_count, 2)

    async def test_thinking_readiness_waits_for_live_control_without_opening_picker(self):
        locators = AIStudioLocators(FakePage())
        control = ValueSelectControl('Medium')
        locators.open_run_settings = AsyncMock()
        locators._wait_for_thinking_label = AsyncMock(return_value=FakeLabel())
        locators._first_usable = AsyncMock(side_effect=[control, control])

        self.assertEqual(await locators.wait_for_thinking_level_ready(), 'Medium')
        self.assertEqual(control.actions, [('evaluate', '''element => new Promise(resolve => {
                requestAnimationFrame(() => requestAnimationFrame(resolve));
            })''')])

    async def test_thinking_value_wait_targets_closed_combobox_not_page_text(self):
        locators = AIStudioLocators(FakePage())
        locators._selected_thinking_level_text = AsyncMock(return_value='High')

        await locators._wait_for_thinking_level_value('High')

        self.assertEqual(locators.page.wait_argument, 'High')
        self.assertIn('aria-expanded', locators.page.wait_expression)
        self.assertIn('ms-thinking-level-setting', locators.page.wait_expression)
        self.assertNotIn('document.body.innerText', locators.page.wait_expression)

    async def test_initialization_wait_accepts_required_successful_responses(self):
        page = FakePage()
        locators = AIStudioLocators(page)
        task = asyncio.create_task(locators.wait_for_initialization(timeout=1.0))
        await asyncio.sleep(0)
        for fragment in ('GenerateAccessToken', 'ListModels', 'GetUserPreferences'):
            page.events['response'](type('Response', (), {
                'url': f'https://example.test/{fragment}', 'status': 200
            })())
        self.assertTrue(await task)

    async def test_initialization_listener_captures_fast_responses_before_wait_starts(self):
        page = FakePage()
        locators = AIStudioLocators(page)
        listener = locators.arm_initialization_listener()
        for fragment in ('GenerateAccessToken', 'ListModels', 'GetUserPreferences'):
            page.events['response'](type('Response', (), {
                'url': f'https://example.test/{fragment}', 'status': 200, 'headers': {}
            })())
        self.assertTrue(await locators.wait_for_initialization(listener=listener, timeout=0.01))
        self.assertEqual(
            {item['request']: item['http_status']
             for item in locators.last_initialization_diagnostics},
            {'GenerateAccessToken': 200, 'ListModels': 200, 'GetUserPreferences': 200},
        )
        locators.disarm_initialization_listener(listener)
        self.assertEqual(page.events, {})

    async def test_initialization_wait_accepts_slow_progress_until_its_bounded_deadline(self):
        page = FakePage()
        locators = AIStudioLocators(page)
        listener = locators.arm_initialization_listener()
        task = asyncio.create_task(locators.wait_for_initialization(listener=listener, timeout=0.5))
        for fragment in ('GenerateAccessToken', 'ListModels', 'GetUserPreferences'):
            await asyncio.sleep(0.01)
            page.events['response'](type('Response', (), {
                'url': f'https://example.test/{fragment}', 'status': 200
            })())
        self.assertTrue(await task)
        locators.disarm_initialization_listener(listener)

    async def test_initialization_wait_times_out_with_safe_error(self):
        locators = AIStudioLocators(FakePage())
        with self.assertRaises(AIStudioInitializationTimeoutError) as raised:
            await locators.wait_for_initialization(timeout=0.01)
        self.assertEqual([item['completion'] for item in raised.exception.diagnostics],
                         ['pending', 'pending', 'pending'])
        self.assertTrue(all(item['http_status'] is None for item in raised.exception.diagnostics))

    async def test_initialization_wait_reports_rejected_rpc_immediately_and_safely(self):
        page = FakePage()
        locators = AIStudioLocators(page)
        listener = locators.arm_initialization_listener()
        page.events['response'](type('Response', (), {
            'url': 'https://example.test/GenerateAccessToken?private=query', 'status': 403,
            'headers': {'grpc-status': '7', 'authorization': 'private-token'},
        })())
        started = asyncio.get_running_loop().time()
        with self.assertRaises(AIStudioInitializationRejectedError) as raised:
            await locators.wait_for_initialization(listener=listener, timeout=1.0)
        self.assertLess(asyncio.get_running_loop().time() - started, 0.1)
        self.assertEqual(next(item for item in raised.exception.diagnostics
                              if item['request'] == 'GenerateAccessToken'), {
            'request': 'GenerateAccessToken', 'completion': 'rejected',
            'http_status': 403, 'rpc_status': 7,
        })
        self.assertNotIn('private', str(raised.exception.diagnostics))
        locators.disarm_initialization_listener(listener)

    async def test_initialization_timeout_reports_partial_progress(self):
        page = FakePage()
        locators = AIStudioLocators(page)
        listener = locators.arm_initialization_listener()
        page.events['response'](type('Response', (), {
            'url': 'https://example.test/GenerateAccessToken', 'status': 200
        })())
        with self.assertRaisesRegex(AIStudioInitializationTimeoutError, r'partial progress \(1 of 3') as raised:
            await locators.wait_for_initialization(listener=listener, timeout=0.01)
        progress = {item['request']: item['completion'] for item in raised.exception.diagnostics}
        self.assertEqual(progress['GenerateAccessToken'], 'succeeded')
        self.assertEqual(progress['ListModels'], 'pending')
        self.assertEqual(progress['GetUserPreferences'], 'pending')
        locators.disarm_initialization_listener(listener)

    async def test_initialization_redirect_is_not_treated_as_rpc_rejection(self):
        page = FakePage()
        locators = AIStudioLocators(page)
        listener = locators.arm_initialization_listener()
        response = page.events['response']
        response(type('Response', (), {
            'url': 'https://example.test/ListModels', 'status': 302, 'headers': {},
        })())
        response(type('Response', (), {
            'url': 'https://example.test/ListModels', 'status': 200, 'headers': {},
        })())
        for fragment in ('GenerateAccessToken', 'GetUserPreferences'):
            response(type('Response', (), {
                'url': f'https://example.test/{fragment}', 'status': 200, 'headers': {},
            })())
        self.assertTrue(await locators.wait_for_initialization(listener=listener, timeout=0.01))
        locators.disarm_initialization_listener(listener)

    async def test_initialization_wait_stops_promptly_on_cancellation(self):
        page = FakePage()
        locators = AIStudioLocators(page)
        listener = locators.arm_initialization_listener()
        cancelled = asyncio.Event()
        task = asyncio.create_task(locators.wait_for_initialization(
            listener=listener, cancellation=cancelled, timeout=2.0
        ))
        await asyncio.sleep(0)
        cancelled.set()
        with self.assertRaises(AIStudioCancelledError):
            await task
        locators.disarm_initialization_listener(listener)

    async def test_page_close_during_initialization_is_connection_error(self):
        page = FakePage()
        locators = AIStudioLocators(page)
        listener = locators.arm_initialization_listener()
        task = asyncio.create_task(locators.wait_for_initialization(listener=listener, timeout=1.0))
        await asyncio.sleep(0)
        page.events['close']()
        with self.assertRaises(AIStudioConnectionError):
            await task
        locators.disarm_initialization_listener(listener)

    async def test_thinking_without_label_is_supported_as_unavailable(self):
        locators = AIStudioLocators(FakePage())
        locators.open_run_settings = lambda: asyncio.sleep(0)
        locators._wait_for_thinking_label = lambda: asyncio.sleep(0, result=None)
        result = await locators.discover_thinking()
        self.assertEqual(result, ThinkingDiscovery(False))

    async def test_visible_thinking_label_without_control_is_safe_error(self):
        locators = AIStudioLocators(FakePage())
        locators.open_run_settings = lambda: asyncio.sleep(0)
        locators._wait_for_thinking_label = lambda: asyncio.sleep(0, result=FakeLabel())
        locators._first_usable = lambda _candidates: asyncio.sleep(0, result=None)
        with self.assertRaises(AIStudioControlNotFoundError):
            await locators.discover_thinking()

    async def test_panel_readiness_reopens_once_when_controls_render_late(self):
        locators = AIStudioLocators(FakePage())
        readiness = iter([False, True])
        reopened = []
        locators._run_settings_panel_ready = lambda: asyncio.sleep(0, result=next(readiness))
        locators._run_settings_panel_visible = lambda: asyncio.sleep(0, result=False)
        locators._wait_for_run_settings_ready = lambda: asyncio.sleep(0, result=False if not reopened else True)
        locators._reopen_run_settings_panel = lambda: asyncio.sleep(0, result=reopened.append(True))
        class Toggle:
            async def click(self):
                return None
        locators._first_usable = lambda _candidates: asyncio.sleep(0, result=Toggle())
        locators._human_pause = lambda: asyncio.sleep(0)
        await locators.open_run_settings_panel()
        self.assertEqual(reopened, [True])

    async def test_fill_prompt_press_space_after_inserting_text(self):
        locators = AIStudioLocators(FakePage())
        prompt = FakePrompt()
        locators.wait_for_prompt_input = lambda: asyncio.sleep(0, result=prompt)
        locators.prompt_is_interactive = lambda _prompt: asyncio.sleep(0, result=True)
        locators._human_pause = lambda: asyncio.sleep(0)
        await locators.fill_prompt('instruction')
        self.assertEqual(prompt.actions, ['click', ('fill', 'instruction'), ('press', 'Space')])

    async def test_invalid_thinking_level_value_is_safe_error(self):
        with self.assertRaises(AIStudioControlNotFoundError):
            await AIStudioLocators(FakePage()).set_thinking_level('   ')

    async def test_model_already_correct_skips_picker(self):
        locators = AIStudioLocators(FakePage())
        locators.current_model = lambda: asyncio.sleep(0, result='Gemini 3.8 Flash')

        async def picker_should_not_run():
            raise AssertionError('The model picker should not open when the model already matches.')

        locators.model_picker = picker_should_not_run
        self.assertFalse(await locators.set_model('Gemini 3.8 Flash'))

    async def test_thinking_level_already_correct_skips_picker(self):
        locators = AIStudioLocators(FakePage())
        control = FakeSelectControl()
        locators.open_run_settings = lambda: asyncio.sleep(0)
        locators._wait_for_thinking_label = lambda: asyncio.sleep(0, result=FakeLabel())
        locators._first_usable = lambda _candidates: asyncio.sleep(0, result=control)

        async def should_not_open(_control):
            raise AssertionError('The Thinking Level picker should stay closed when the value already matches.')

        locators._open_thinking_overlay = should_not_open
        self.assertFalse(await locators.set_thinking_level('High'))

    async def test_current_thinking_level_reads_without_opening_picker(self):
        locators = AIStudioLocators(FakePage())
        locators.open_run_settings = lambda: asyncio.sleep(0)
        locators._wait_for_thinking_label = lambda: asyncio.sleep(0, result=FakeLabel())
        locators._first_usable = lambda _candidates: asyncio.sleep(0, result=FakeSelectControl())

        async def should_not_open(_control):
            raise AssertionError('Reading the selected level must not open the picker.')

        locators._open_thinking_overlay = should_not_open
        result = await locators.current_thinking_level()
        self.assertEqual(result, ThinkingDiscovery(True, 'High', None))

    def test_token_count_detection_requires_visible_numeric_token_text(self):
        self.assertTrue(has_token_count_text('Reference.md · 12,480 tokens'))
        self.assertFalse(has_token_count_text('Reference.md · Processing'))

    def test_transient_generation_error_detection_uses_only_known_google_errors(self):
        self.assertTrue(has_transient_generation_error_text('Failed to generate content: permission denied.'))
        self.assertTrue(has_transient_generation_error_text('An internal error has occurred.'))
        self.assertFalse(has_transient_generation_error_text('CALIBRATION COMPLETE.'))

    def test_error_and_protocol_text_are_not_generated_responses(self):
        self.assertFalse(is_generated_response_text('An internal error has occurred.'))
        self.assertFalse(is_generated_response_text('CALIBRATION COMPLETE. INTERNAL QA — PRE-OUTPUT CHECKS'))
        self.assertTrue(is_generated_response_text('CALIBRATION COMPLETE. Ready for lecture files.'))

    def test_assistant_response_normalization_strips_only_ai_studio_turn_chrome(self):
        self.assertEqual(
            normalize_assistant_response_text(
                'Model Â· 7:45 AM\nCalibration complete. The model is ready.\n'
                'Google AI models may make mistakes, so double-check outputs.'
            ),
            'Calibration complete. The model is ready.',
        )
        self.assertEqual(normalize_assistant_response_text('Calibration complete.'), 'Calibration complete.')
        self.assertEqual(
            normalize_assistant_response_text('Model · 7:45 AM CALIBRATION COMPLETE'),
            'CALIBRATION COMPLETE',
        )

    async def test_generation_start_timeout_reports_its_stage(self):
        class TimeoutPage(FakePage):
            async def wait_for_selector(self, *_args, **_kwargs):
                raise TimeoutError('deliberate test timeout')

        with self.assertRaisesRegex(AIStudioGenerationTimeoutError, 'did not start generation'):
            await AIStudioLocators(TimeoutPage()).wait_for_generation_start()

    async def test_send_stop_and_run_state_detection(self):
        locators = AIStudioLocators(FakePage())
        locators.stop_button = lambda: asyncio.sleep(0, result=object())
        locators.run_button = lambda: asyncio.sleep(0, result=None)
        self.assertTrue(await locators.generation_has_started())
        self.assertFalse(await locators.generation_has_completed())
        locators.stop_button = lambda: asyncio.sleep(0, result=None)
        locators.run_button = lambda: asyncio.sleep(0, result=object())
        self.assertTrue(await locators.generation_has_completed())

    async def test_attachment_wait_soft_times_out_after_ten_seconds(self):
        locators = AIStudioLocators(FakePage())
        ticks = iter(range(20))
        locators._clock = lambda: next(ticks)
        locators.attachment_has_token_count = lambda _filename: asyncio.sleep(0, result=False)
        locators.stop_button = lambda: asyncio.sleep(0, result=None)
        locators._sleep = lambda _seconds: asyncio.sleep(0)
        result = await locators.wait_for_attachment_token_count('reference.txt')
        self.assertEqual(result, AttachmentReady.TOKEN_TIMEOUT)


if __name__ == '__main__':
    unittest.main()
