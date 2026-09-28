import asyncio
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock

from mcq_maker.ai_studio_errors import (
    AIStudioExtractionError, AIStudioResponseError, AIStudioValidationError,
)

from experiments.ai_studio_playwright.human_assisted import (
    ALLOWED_TRANSITIONS,
    ExperimentState,
    GENERATION_REQUEST,
    HumanAssistedExperiment,
    HumanAssistedLocators,
    QuizArrayExtractionError,
    QuizCandidateValidationError,
    StagePerformanceTrace,
    TurnObserver,
    TurnObservation,
    classify_calibration_response,
    diagnose_calibration_response,
    extract_json_array,
    select_valid_quiz_array,
    should_pause_for_calibration_diagnostics,
)


class FakeManager:
    async def ensure_connected(self):
        return object()

    async def shutdown(self):
        return None


class FakeResponse:
    url = 'https://aistudio.google.com/MakerSuiteService/GenerateContent'
    status = 200


class FakePage:
    def __init__(self):
        self.handlers = {}
        self.removed = []
        self.keyboard = self

    def on(self, name, handler):
        self.handlers[name] = handler

    def remove_listener(self, name, handler):
        self.removed.append((name, handler))

    async def wait_for_selector(self, selector, state, timeout):
        if state == 'visible' and 'Stop' in selector:
            self.handlers['response'](FakeResponse())
            return object()
        return object()

    async def evaluate(self, expression):
        return False


class StopBeforeResponsePage(FakePage):
    async def wait_for_selector(self, selector, state, timeout):
        if state == 'visible' and 'Stop' in selector:
            asyncio.get_running_loop().call_later(0.01, self.handlers['response'], FakeResponse())
            return object()
        return object()


class PreparationLocators:
    def __init__(self):
        self.page = FakePage()
        self.calls = []

    async def fill_prompt(self, text):
        self.calls.append(('fill_prompt', text))

    async def attach_reference_file(self, path):
        self.calls.append(('attach_reference', Path(path).name))
        return Path(path).name

    async def attachment_acknowledged(self, filename):
        self.calls.append(('attachment_ready', filename))
        from mcq_maker.ai_studio_selectors import AttachmentReady
        return AttachmentReady.TOKEN_COUNT

    async def send_prompt(self):
        self.calls.append(('send_prompt',))


class PreparationSession:
    locators_instance = None

    def __init__(self, manager, locator_factory):
        self.locators = self.locators_instance
        self.page = FakePage()
        self.page.context = object()

    async def discover_capabilities(self):
        return None

    async def configure_defaults(self, model, thinking_level):
        self.locators.calls.append(('configure', model, thinking_level))


class PreparationRecorder:
    def __init__(self, page, launch_mode, attached, action):
        self.installed = False

    async def install_context(self, context, *, read_webdriver=True):
        self.installed = True

    def uninstall(self):
        return None

    def snapshot(self):
        return {'navigator_webdriver': True, 'waa_create_statuses': [], 'records': []}


class FakeLocators:
    def __init__(self, response='CALIBRATION COMPLETE'):
        self.page = FakePage()
        self.response = response

    @staticmethod
    def _stop_selector():
        return "button[aria-label='Stop' i]"

    @staticmethod
    def _run_selector():
        return "button[aria-label='Run' i]"

    async def wait_for_generation_complete(self):
        return None

    async def wait_for_lecture_generation_complete(self):
        return None

    async def visible_generation_error(self):
        return None

    async def wait_for_completed_assistant_response(self, timeout=5.0):
        return True

    async def assistant_response_container_count(self):
        # AI Studio may reuse one wrapper for multiple completed turns.
        return 1

    async def last_assistant_response_text(self):
        return self.response

    async def last_assistant_response_diagnostic(self):
        return {
            'candidate_group': 0,
            'candidate_group_label': 'ms-prompt-response, ms-response',
            'candidate_index': 1,
            'element': {'tagName': 'MS-RESPONSE', 'afterLastUser': True},
            'rendered_text': 'Model · 7:41AM\\n' + self.response,
            'extracted_text': self.response,
        }


class RetryLocators(FakeLocators):
    def __init__(self):
        super().__init__()
        self.response_checks = 0

    async def wait_for_completed_assistant_response(self, timeout=5.0):
        self.response_checks += 1
        return self.response_checks > 1

    async def visible_generation_error(self):
        return 'Google AI Studio displayed Permission denied.'


class NewTurnLocators(FakeLocators):
    def __init__(self, response):
        super().__init__(response)
        self.count = 1

    async def assistant_response_container_count(self):
        self.count += 1
        return self.count


class DuplicateTextLocator:
    def __init__(self, visible):
        self.visible = visible
        self.first = self

    async def wait_for(self, state, timeout):
        return None

    async def count(self):
        return len(self.visible)

    def nth(self, index):
        return DuplicateTextLocator([self.visible[index]])

    async def is_visible(self):
        return self.visible[0]


class AttachmentPage:
    def get_by_text(self, _filename, exact=True):
        return DuplicateTextLocator([True, True])


class DelayedFilenameLocator(DuplicateTextLocator):
    def __init__(self, calls, visible=False):
        super().__init__([visible])
        self.calls = calls

    async def wait_for(self, state, timeout):
        import asyncio
        self.calls.append('filename_wait_started')
        await asyncio.sleep(0.01)
        self.visible[0] = True
        self.calls.append('filename_visible')


class DelayedAttachmentPage:
    def __init__(self):
        self.calls = []
        self.locator = DelayedFilenameLocator(self.calls)

    def get_by_text(self, _filename, exact=True):
        return self.locator


class TextMatchPage:
    def __init__(self, texts):
        self.texts = texts

    def get_by_text(self, pattern):
        return DuplicateTextLocator([
            True for text in self.texts if pattern.search(text)
        ])


class AttachmentLocators(HumanAssistedLocators):
    async def wait_for_attachment_token_count(self, filename, timeout=None):
        calls = getattr(self.page, 'calls', None)
        if calls is not None:
            calls.append('token_wait')
        return 'ready'


class HumanAssistedTests(unittest.IsolatedAsyncioTestCase):
    async def test_performance_trace_records_only_stage_and_elapsed_duration(self):
        trace = StagePerformanceTrace()
        async with trace.measure('initialization'):
            import asyncio
            await asyncio.sleep(0.001)
        self.assertEqual(trace.records[0]['stage'], 'initialization')
        self.assertGreaterEqual(trace.records[0]['elapsed_seconds'], 0)
        self.assertEqual(set(trace.records[0]), {'stage', 'elapsed_seconds'})

    def test_waiting_states_are_explicit_and_guarded(self):
        self.assertIn(
            ExperimentState.CALIBRATION_RUNNING,
            ALLOWED_TRANSITIONS[ExperimentState.WAITING_CALIBRATION],
        )
        self.assertIn(
            ExperimentState.LECTURE_RUNNING,
            ALLOWED_TRANSITIONS[ExperimentState.WAITING_LECTURE],
        )

    def test_illegal_transition_is_rejected(self):
        experiment = HumanAssistedExperiment(
            FakeManager(), model='Gemini', thinking='High'
        )
        with self.assertRaises(RuntimeError):
            experiment.transition(ExperimentState.COMPLETE)

    async def test_observer_is_armed_before_wait_and_associates_response(self):
        locators = FakeLocators()
        observer = TurnObserver(locators, manual_timeout=1)
        observer.arm()
        started = []
        result = await observer.wait(on_started=lambda: started.append(True))
        self.assertEqual(result.response_text, 'CALIBRATION COMPLETE')
        self.assertTrue(result.stop_control_seen)
        self.assertEqual(result.http_status, 200)
        self.assertEqual(started, [True])
        self.assertTrue(locators.page.removed)

    async def test_delayed_final_answer_is_waited_for_after_generation_completes(self):
        import asyncio

        class DelayedFinalLocators(FakeLocators):
            async def wait_for_completed_assistant_response(self, timeout=120.0):
                await asyncio.sleep(0.01)
                return True

        locators = DelayedFinalLocators()
        observer = TurnObserver(locators, manual_timeout=1)
        observer.arm()
        result = await observer.wait()
        self.assertEqual(result.response_text, 'CALIBRATION COMPLETE')

    async def test_completed_generation_without_final_answer_is_reported(self):
        class NoFinalLocators(FakeLocators):
            async def wait_for_completed_assistant_response(self, timeout=120.0):
                return False

            async def visible_generation_error(self):
                return None

        observer = TurnObserver(NoFinalLocators(), manual_timeout=1)
        observer.arm()
        with self.assertRaisesRegex(AIStudioResponseError, 'without a new completed assistant response'):
            await observer.wait()

    async def test_turn_scoped_response_is_accepted_when_wrapper_count_is_reused(self):
        locators = FakeLocators('Model · 7:45 AM\nCALIBRATION COMPLETE')
        observer = TurnObserver(locators, manual_timeout=1)
        await observer.begin_attempt('automated Send')
        observer.arm()
        result = await observer.wait(
            accept_response=lambda text: classify_calibration_response(text) in {
                'calibration_complete_exact', 'calibration_ready_expanded'
            }
        )
        self.assertEqual(result.http_status, 200)
        self.assertEqual(classify_calibration_response(result.response_text), 'calibration_complete_exact')

    async def test_observer_keeps_network_listener_armed_when_stop_precedes_http_response(self):
        locators = FakeLocators()
        locators.page = StopBeforeResponsePage()
        observer = TurnObserver(locators, manual_timeout=1)
        observer.arm()
        result = await observer.wait()
        self.assertEqual(result.http_status, 200)
        self.assertTrue(result.stop_control_seen)
        self.assertTrue(locators.page.removed)

    async def test_observer_prefers_a_completed_response_over_a_stale_visible_error(self):
        locators = FakeLocators()

        async def error():
            return 'Google AI Studio displayed a generation error.'

        locators.visible_generation_error = error
        observer = TurnObserver(locators, manual_timeout=1)
        observer.arm()
        result = await observer.wait()
        self.assertEqual(result.response_text, 'CALIBRATION COMPLETE')

    async def test_observer_stops_after_one_failed_automated_send(self):
        locators = RetryLocators()
        observer = TurnObserver(locators, manual_timeout=1)
        observer.arm()
        with self.assertRaises(AIStudioResponseError):
            await observer.wait()
        self.assertEqual(locators.response_checks, 1)

    async def test_observer_returns_only_a_new_assistant_turn_after_the_baseline(self):
        locators = NewTurnLocators('[{"title":"Lecture quiz"}]')
        observer = TurnObserver(locators, manual_timeout=1)
        await observer.begin_attempt('automated Send')
        observer.arm()
        result = await observer.wait(lecture=True)
        self.assertEqual(result.response_text, '[{"title":"Lecture quiz"}]')

    async def test_opt_in_calibration_diagnostic_prints_stage_comparison_locally(self):
        locators = FakeLocators('CALIBRATION COMPLETE')
        observer = TurnObserver(locators, manual_timeout=1)
        observer.arm()
        output = StringIO()
        with redirect_stdout(output):
            result = await observer.wait(
                accept_response=lambda text: classify_calibration_response(text) == 'calibration_complete_exact',
                calibration_diagnostics=True,
            )
        self.assertEqual(result.response_text, 'CALIBRATION COMPLETE')
        emitted = output.getvalue()
        self.assertIn("[calibration-diagnostic:selected-element]=", emitted)
        self.assertIn("[calibration-diagnostic:current-extraction]='CALIBRATION COMPLETE'", emitted)
        self.assertIn("[calibration-diagnostic:header-normalized]='CALIBRATION COMPLETE'", emitted)
        self.assertIn("[calibration-diagnostic:classifier-input]='calibration complete'", emitted)
        self.assertIn("[calibration-diagnostic:classification]='calibration_complete_exact'", emitted)
        self.assertIn("[calibration-diagnostic:reason]='exact calibration acknowledgement'", emitted)

    def test_classifier_diagnostic_reports_unrecognized_acknowledgement_reason(self):
        details = diagnose_calibration_response('READY FOR LECTURE')
        self.assertEqual(details[1], 'other')
        self.assertIn('does not begin', details[2])

    def test_opt_in_diagnostics_preserve_playground_for_any_calibration_stage_failure(self):
        self.assertTrue(should_pause_for_calibration_diagnostics(
            True, ExperimentState.CALIBRATION_RUNNING
        ))
        self.assertFalse(should_pause_for_calibration_diagnostics(
            False, ExperimentState.CALIBRATION_RUNNING
        ))
        self.assertFalse(should_pause_for_calibration_diagnostics(
            True, ExperimentState.PREPARING
        ))

    def test_extract_json_array_accepts_raw_json(self):
        value = json.dumps(self._valid_quiz())
        self.assertEqual(extract_json_array(value), value)

    def test_extract_json_array_accepts_json_code_fence(self):
        value = json.dumps(self._valid_quiz())
        self.assertEqual(extract_json_array(f'```json\n{value}\n```'), value)

    def test_extract_json_array_accepts_generic_code_fence_and_prose(self):
        value = json.dumps(self._valid_quiz())
        self.assertEqual(extract_json_array(f'Result:\n```\n{value}\n```'), value)

    def test_extract_json_array_rejects_missing_or_incomplete_array(self):
        for text in ('No quiz returned.', '```json\n[{"title":"Quiz"}'):
            with self.subTest(text=text), self.assertRaises(AIStudioExtractionError):
                extract_json_array(text)

    @staticmethod
    def _valid_quiz(title='Quiz', question='Q?'):
        return [
            {'title': title},
            {'question': question, 'options': ['A', 'B'], 'correct': 0, 'explanation': 'Reason.'},
        ]

    def test_selects_valid_quiz_after_unrelated_array_and_surrounding_text(self):
        unrelated = '["not", "a", "quiz"]'
        valid = json.dumps(self._valid_quiz('Middle Quiz'), ensure_ascii=False)
        response = f'Intro {unrelated} unrelated text; answer: ```json\n{valid}\n``` done.'
        selected = select_valid_quiz_array(response)
        self.assertEqual(selected.quiz.title, 'Middle Quiz')
        self.assertEqual(selected.text, valid)
        self.assertEqual([item['status'] for item in selected.diagnostics], ['invalid_schema', 'valid'])

    def test_nested_arrays_and_brackets_in_escaped_strings_are_not_split(self):
        question = 'Which string contains [brackets], a ] mark, a "quoted [label]" phrase, and C:\\tmp?'
        quiz = self._valid_quiz(question=question)
        quiz[1]['options'] = ['value [one]', 'value ] two']
        raw = json.dumps(quiz, ensure_ascii=False)
        selected = select_valid_quiz_array(f'```json\n{raw}\n```')
        self.assertEqual(json.loads(selected.text), quiz)
        self.assertEqual(len(selected.diagnostics), 1)

    def test_balanced_invalid_json_before_valid_quiz_does_not_hide_quiz(self):
        valid = json.dumps(self._valid_quiz('Found Quiz'))
        selected = select_valid_quiz_array('[{"title":}] then ' + valid)
        self.assertEqual(selected.quiz.title, 'Found Quiz')
        self.assertEqual([item['status'] for item in selected.diagnostics], ['invalid_json', 'valid'])

    def test_duplicate_valid_arrays_are_one_distinct_candidate(self):
        raw = json.dumps(self._valid_quiz('Same Quiz'))
        selected = select_valid_quiz_array(raw + '\n' + raw)
        self.assertEqual(selected.quiz.title, 'Same Quiz')
        self.assertEqual(len([d for d in selected.diagnostics if d['status'] == 'valid']), 2)

    def test_distinct_valid_arrays_are_reported_as_ambiguous(self):
        first = json.dumps(self._valid_quiz('First'))
        second = json.dumps(self._valid_quiz('Second'))
        with self.assertRaises(QuizArrayExtractionError) as caught:
            select_valid_quiz_array(first + '\n' + second)
        self.assertEqual(caught.exception.reason, 'ambiguous')
        self.assertEqual(len(caught.exception.diagnostics), 2)

    def test_extraction_distinguishes_missing_incomplete_invalid_json_and_invalid_schema(self):
        cases = (
            ('nothing here', QuizArrayExtractionError, 'no_array'),
            ('prefix [{"title":"Quiz"}', QuizArrayExtractionError, 'incomplete_array'),
            ('[{"title":}]', QuizArrayExtractionError, 'invalid_json'),
            (json.dumps([{'title': 'Only title'}]), QuizCandidateValidationError, 'invalid_schema'),
        )
        for text, error_type, reason in cases:
            with self.subTest(reason=reason), self.assertRaises(error_type) as caught:
                select_valid_quiz_array(text)
            self.assertEqual(caught.exception.reason, reason)

    def test_invalid_candidate_diagnostics_contain_structure_but_no_values(self):
        secret = 'private synthetic question text'
        invalid = json.dumps([{'title': secret, secret: 'hidden'}, secret + ' 2'])
        with self.assertRaises(QuizCandidateValidationError) as caught:
            select_valid_quiz_array(invalid)
        diagnostics = repr(caught.exception.diagnostics)
        self.assertIn("'element_types': {'object': 1, 'string': 1}", diagnostics)
        self.assertIn("'object_keys': ['title']", diagnostics)
        self.assertIn("'extra_key_count': 1", diagnostics)
        self.assertNotIn(secret, diagnostics)

    def test_calibration_classifier_ignores_model_timestamp_chrome(self):
        self.assertEqual(
            classify_calibration_response('Model • 7:41AM\nCalibration complete. The model is ready for the lecture.'),
            'calibration_ready_expanded',
        )

    def test_calibration_classifier_rejects_protocol_copy_and_error_text(self):
        self.assertEqual(
            classify_calibration_response('Calibration complete. Internal QA protocol follows.'),
            'other',
        )
        self.assertEqual(
            classify_calibration_response('Model • 7:41AM\nCalibration complete, but an internal error has occurred.'),
            'internal_error',
        )

    def test_calibration_classifier_accepts_full_prompt_mandated_response(self):
        # The actual multi-line acknowledgement specified in PROMPT(MCQ-MAKER).txt
        # normalizes to 246 characters — previously rejected by an overly narrow len <= 240 cap.
        full_response = (
            'CALIBRATION COMPLETE.\n'
            'I have analyzed the Reference Style, Topic Frequency Map, and JSON Structure.\n'
            'CURRENT MODE: STANDBY.\n'
            'OUTPUT FORMAT SETTING: Raw JSON Array ONLY.\n'
            'Please upload your Lecture/Content Files to begin the comprehensive extraction.'
        )
        result = classify_calibration_response(full_response)
        self.assertEqual(result, 'calibration_ready_expanded',
                         'The 246-char prompt-mandated response must be accepted by the classifier')

    def test_calibration_classifier_still_rejects_long_protocol_text(self):
        # Ensure removing the len cap didn't open the door to protocol bloat
        self.assertEqual(
            classify_calibration_response(
                'Calibration complete. Batching / token limit protocol: if you reach the token limit mid-array, '
                'close the last complete object but do not close the array.'
            ),
            'other',
        )


    async def test_close_releases_browser_manager(self):
        manager = FakeManager()
        manager.closed = False

        async def shutdown():
            manager.closed = True

        manager.shutdown = shutdown
        experiment = HumanAssistedExperiment(manager, model='Gemini', thinking='High')
        await experiment.close()
        self.assertTrue(manager.closed)

    async def test_attachment_acknowledgement_accepts_visible_chip_and_tooltip(self):
        locators = AttachmentLocators(AttachmentPage())
        result = await locators.attachment_acknowledged('reference.txt')
        self.assertEqual(result, 'ready')

    async def test_delayed_attachment_filename_is_waited_for_before_token_check(self):
        page = DelayedAttachmentPage()
        locators = AttachmentLocators(page)
        result = await locators.attachment_acknowledged('lecture.pdf')
        self.assertEqual(result, 'ready')
        self.assertEqual(page.calls, ['filename_wait_started', 'filename_visible', 'token_wait'])

    async def test_quotation_mark_text_is_not_mistaken_for_quota_error(self):
        locators = HumanAssistedLocators(TextMatchPage([
            'QUOTATION MARK SAFETY RULES (CRITICAL)',
            'Illegal quotation marks inside JSON strings are a critical error.',
        ]))
        self.assertIsNone(await locators.visible_generation_error())

    async def test_real_quota_error_is_still_detected(self):
        locators = HumanAssistedLocators(TextMatchPage([
            'Resource exhausted: quota exceeded. Try again later.',
        ]))
        self.assertIn('quota', (await locators.visible_generation_error()).lower())

    async def test_calibration_places_saved_prompt_in_message_and_sends_without_system_field(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prompt = root / 'prompt.txt'
            reference = root / 'reference.md'
            prompt.write_text('THE SAVED PROMPT', encoding='utf-8')
            reference.write_text('reference', encoding='utf-8')
            locators = PreparationLocators()
            PreparationSession.locators_instance = locators
            experiment = HumanAssistedExperiment(
                FakeManager(), model='Gemini', thinking='High',
                session_factory=PreparationSession,
                recorder_factory=PreparationRecorder,
            )
            await experiment.prepare_calibration(prompt, reference)
            self.assertEqual(experiment.state, ExperimentState.CALIBRATION_RUNNING)
            self.assertIn(('fill_prompt', 'THE SAVED PROMPT'), locators.calls)
            self.assertNotIn(('fill_prompt', ''), locators.calls)
            self.assertIn(('send_prompt',), locators.calls)
            self.assertFalse(any(call[0] == 'clear_system_instructions' for call in locators.calls))

    async def test_lecture_turn_uploads_acknowledges_then_fills_instruction_and_sends(self):
        locators = PreparationLocators()
        async def attach_lecture(path):
            locators.calls.append(('attach_lecture', Path(path).name))
            return Path(path).name
        async def acknowledge(filename):
            locators.calls.append(('lecture_acknowledged', filename))
            from mcq_maker.ai_studio_selectors import AttachmentReady
            return AttachmentReady.TOKEN_COUNT
        locators.attach_lecture_file = attach_lecture
        locators.attachment_acknowledged = acknowledge
        experiment = HumanAssistedExperiment(FakeManager(), model='Gemini', thinking='High')
        experiment.state = ExperimentState.CALIBRATION_RUNNING
        experiment.locators = locators
        experiment.calibration_observer = AsyncMock()
        experiment.calibration_observer.wait.return_value = TurnObservation('CALIBRATION COMPLETE', 200, True)
        await experiment.observe_calibration_and_prepare_lecture(Path('lecture.pdf'))
        names = [call[0] for call in locators.calls]
        self.assertLess(names.index('attach_lecture'), names.index('lecture_acknowledged'))
        self.assertLess(names.index('lecture_acknowledged'), names.index('fill_prompt'))
        self.assertLess(names.index('fill_prompt'), names.index('send_prompt'))
        self.assertIn(('fill_prompt', GENERATION_REQUEST), locators.calls)

    async def test_validated_quiz_is_saved_through_existing_exam_pipeline(self):
        from mcq_maker.exam_generator import proposed_filename
        payload = 'Quiz output follows: ["not", "a quiz"]\n' + json.dumps(
            self._valid_quiz('Lecture Title')
        ) + '\nEnd.'
        experiment = HumanAssistedExperiment(FakeManager(), model='Gemini', thinking='High')
        experiment.state = ExperimentState.LECTURE_RUNNING
        experiment.lecture_observer = AsyncMock()
        experiment.lecture_observer.wait.return_value = TurnObservation(payload, 200, True)
        experiment.template = (Path(__file__).resolve().parents[2] / 'mcq_maker' / 'assets' / 'standard_exam.html').read_text(encoding='utf-8')
        with tempfile.TemporaryDirectory() as directory:
            experiment.output_dir = Path(directory)
            output = await experiment.observe_lecture_and_save()
            self.assertEqual(output.name, proposed_filename('Lecture Title', Path(directory)))
            self.assertTrue(output.is_file())
            self.assertEqual(experiment.last_quiz.title, 'Lecture Title')

    async def test_invalid_or_ambiguous_candidates_never_save_an_exam(self):
        from unittest.mock import patch

        responses = (
            json.dumps([{'title': 'Only title'}]),
            json.dumps(self._valid_quiz('First')) + '\n' + json.dumps(self._valid_quiz('Second')),
        )
        for response in responses:
            experiment = HumanAssistedExperiment(FakeManager(), model='Gemini', thinking='High')
            experiment.state = ExperimentState.LECTURE_RUNNING
            experiment.lecture_observer = AsyncMock()
            experiment.lecture_observer.wait.return_value = TurnObservation(response, 200, True)
            with tempfile.TemporaryDirectory() as directory, patch(
                'experiments.ai_studio_playwright.human_assisted.save_exam'
            ) as save:
                experiment.output_dir = Path(directory)
                with self.assertRaises((AIStudioValidationError, AIStudioExtractionError)):
                    await experiment.observe_lecture_and_save()
                save.assert_not_called()
                self.assertEqual(list(Path(directory).iterdir()), [])


if __name__ == '__main__':
    unittest.main()
