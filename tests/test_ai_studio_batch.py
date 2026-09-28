import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from threading import Event

from mcq_maker.ai_studio_batch import (
    BatchJobClaimer, BatchStateStore, BatchStatus, create_manifest, discover_lecture_pdfs,
    input_fingerprint,
)


class BatchStateTests(unittest.TestCase):
    def test_worker_configuration_defaults_to_one_and_new_jobs_have_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / 'one.pdf').write_bytes(b'a')
            store = BatchStateStore(root / 'state')
            manifest = create_manifest(root, root / 'p', root / 'r', 'm', 'High', root / 'out', store=store)
            item = store.load(manifest.batch_id).lectures[0]
            self.assertEqual(manifest.max_workers, 1)
            self.assertEqual(manifest.schema_version, 3)
            self.assertTrue(item.job_id)
            self.assertIsNone(item.worker_id)
            self.assertIsNone(item.claimed_at)

    def test_atomic_claim_allows_only_one_worker_to_claim_the_same_job(self):
        from concurrent.futures import ThreadPoolExecutor
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / 'one.pdf').write_bytes(b'a')
            store = BatchStateStore(root / 'state')
            manifest = create_manifest(root, root / 'p', root / 'r', 'm', 'High', root / 'out', store=store)
            manifest.max_workers = 2
            manifest.control_state = 'running'
            store.save(manifest)
            first = BatchJobClaimer(manifest, store)
            second = BatchJobClaimer(manifest, store)
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda worker: (worker[0].claim_next(worker[1])),
                                        ((first, 'worker-a'), (second, 'worker-b'))))
            claims = [item for item in results if item is not None]
            self.assertEqual(len(claims), 1)
            persisted = store.load(manifest.batch_id).lectures[0]
            self.assertEqual(persisted.status, BatchStatus.OPENING)
            self.assertIn(persisted.worker_id, {'worker-a', 'worker-b'})
            self.assertTrue(persisted.claimed_at)
            self.assertEqual(persisted.job_id, claims[0].job_id)

    def test_claims_skip_completed_attention_and_interrupted_jobs(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ('a.pdf', 'b.pdf', 'c.pdf', 'd.pdf'):
                (root / name).write_bytes(name.encode())
            store = BatchStateStore(root / 'state')
            manifest = create_manifest(root, root / 'p', root / 'r', 'm', 'High', root / 'out', store=store)
            for item, status in zip(manifest.lectures, (BatchStatus.COMPLETED, BatchStatus.ATTENTION,
                                                        BatchStatus.INTERRUPTED, BatchStatus.PENDING)):
                item.status = status
            manifest.max_workers = 2
            manifest.control_state = 'running'
            store.save(manifest)
            claimed = BatchJobClaimer(manifest, store).claim_next('worker-x')
            self.assertIsNotNone(claimed)
            self.assertEqual(claimed.file_name, 'd.pdf')
            self.assertEqual([item.status for item in manifest.lectures[:3]], [
                BatchStatus.COMPLETED, BatchStatus.ATTENTION, BatchStatus.INTERRUPTED,
            ])

    def test_paused_or_stopped_manifest_cannot_issue_new_claims(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / 'one.pdf').write_bytes(b'a')
            store = BatchStateStore(root / 'state')
            manifest = create_manifest(root, root / 'p', root / 'r', 'm', 'High', root / 'out', store=store)
            claimer = BatchJobClaimer(manifest, store)
            for state in ('paused', 'stopped'):
                manifest.control_state = state
                store.save(manifest)
                self.assertIsNone(claimer.claim_next('worker-x'))

    def test_worker_claims_are_isolated_and_persisted_per_job(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ('a.pdf', 'b.pdf'):
                (root / name).write_bytes(name.encode())
            store = BatchStateStore(root / 'state')
            manifest = create_manifest(root, root / 'p', root / 'r', 'm', 'High', root / 'out', store=store)
            manifest.max_workers = 2
            manifest.control_state = 'running'
            store.save(manifest)
            claimer = BatchJobClaimer(manifest, store)
            worker_a = claimer.claim_next('worker-a')
            worker_b = claimer.claim_next('worker-b')
            self.assertNotEqual(worker_a.job_id, worker_b.job_id)
            worker_a.status = BatchStatus.ATTENTION
            worker_b.status = BatchStatus.COMPLETED
            worker_b.output_path = str(root / 'b.html')
            store.save(manifest)
            saved = store.load(manifest.batch_id).lectures
            self.assertEqual((saved[0].worker_id, saved[0].status), ('worker-a', BatchStatus.ATTENTION))
            self.assertEqual((saved[1].worker_id, saved[1].status), ('worker-b', BatchStatus.COMPLETED))
            self.assertEqual(saved[1].output_path, str(root / 'b.html'))

    def test_legacy_manifest_migrates_with_single_worker_and_keeps_job_states(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / 'one.pdf').write_bytes(b'a')
            store = BatchStateStore(root / 'state')
            manifest = create_manifest(root, root / 'p', root / 'r', 'm', 'High', root / 'out', store=store)
            manifest.lectures[0].status = BatchStatus.COMPLETED
            manifest.lectures[0].output_path = str(root / 'exam.html')
            store.save(manifest)
            path = store.path_for(manifest.batch_id)
            legacy = json.loads(path.read_text(encoding='utf-8'))
            legacy['schema_version'] = 2
            legacy.pop('max_workers')
            legacy['lectures'][0].pop('job_id')
            legacy['lectures'][0].pop('worker_id')
            legacy['lectures'][0].pop('claimed_at')
            path.write_text(json.dumps(legacy), encoding='utf-8')
            migrated = store.load(manifest.batch_id)
            self.assertEqual(migrated.max_workers, 1)
            self.assertEqual(migrated.schema_version, 3)
            self.assertEqual(migrated.lectures[0].status, BatchStatus.COMPLETED)
            self.assertEqual(migrated.lectures[0].output_path, str(root / 'exam.html'))
            self.assertTrue(migrated.lectures[0].job_id)
            self.assertIsNone(migrated.lectures[0].worker_id)

    def test_validated_quiz_is_saved_and_completed_through_production_handoff(self):
        import asyncio
        from mcq_maker.ai_studio_batch import GoogleAIStudioBatchController
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'one.pdf').write_bytes(b'lecture')
            prompt = root / 'prompt.txt'; prompt.write_text('prompt')
            reference = root / 'reference.txt'; reference.write_text('reference')
            store = BatchStateStore(root / 'state')
            manifest = create_manifest(root, prompt, reference, 'm', 'High', root / 'out', store=store)
            quiz = SimpleNamespace(title='Recovered quiz', questions=[object()],
                                   payload=[{'title': 'Recovered quiz'}, {'question': 'Q'}])
            events = []

            class FakeSession:
                diagnostic_callback = None
                def __init__(self, manager, *, quiz_validator=None): pass
                async def discover_capabilities(self): pass
                async def configure_defaults(self, **kwargs): pass
                async def run_calibration(self, *args): pass
                async def generate_lecture(self, path):
                    self.diagnostic_callback('quiz_validation_succeeded', {'question_count': 1})
                    return quiz
            class Manager:
                async def shutdown(self): pass

            template = (Path(__file__).resolve().parents[1] / 'mcq_maker' / 'assets' / 'standard_exam.html').read_text(encoding='utf-8')
            with patch('mcq_maker.ai_studio_batch.AIStudioConversationSession', FakeSession):
                result = asyncio.run(GoogleAIStudioBatchController(
                    manifest, browser_manager=Manager(), template=template,
                    template_entry={}, store=store,
                    event_callback=lambda name, status, detail: events.append((name, status, detail)),
                ).run())
            saved = store.load(manifest.batch_id).lectures[0]
            self.assertEqual(result.lectures[0].status, BatchStatus.COMPLETED)
            self.assertEqual(saved.status, BatchStatus.COMPLETED)
            self.assertTrue(Path(saved.output_path).is_file())
            self.assertGreater(Path(saved.output_path).stat().st_size, 0)
            self.assertEqual(saved.title, 'Recovered quiz')
            self.assertEqual(saved.validation_diagnostics, {})

    def test_real_session_uses_exactly_two_submissions_and_saves_first_valid_response(self):
        import asyncio
        from mcq_maker.ai_studio_browser import AIStudioReadiness
        from mcq_maker.ai_studio_batch import GoogleAIStudioBatchController
        from mcq_maker.ai_studio_session import AIStudioConversationSession
        from mcq_maker.ai_studio_selectors import AttachmentReady
        corrected = '[{"title":"Recovered quiz"},{"question":"Q","options":["A","B"],"correct":0,"explanation":"E"}]'
        malformed = corrected
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'one.pdf').write_bytes(b'lecture')
            prompt = root / 'prompt.txt'; prompt.write_text('prompt')
            reference = root / 'reference.txt'; reference.write_text('reference')
            store = BatchStateStore(root / 'state')
            manifest = create_manifest(root, prompt, reference, 'Gemini 3.8 Flash', 'High', root / 'out', store=store)
            pool_events = []
            class Page:
                url = 'about:blank'
                closed = False
                async def goto(self, url, wait_until, timeout=None): self.url = url
                async def close(self): self.closed = True
            page = Page()
            class Context:
                pages = [page]
            class Manager:
                context = Context()
                shutdown_observation = None
                async def ensure_connected(self, cancellation=None): return self.context
                def classify_ai_studio_url(self, url):
                    return AIStudioReadiness.AI_STUDIO_REACHED if 'aistudio.google.com' in url else AIStudioReadiness.UNKNOWN
                async def shutdown(self):
                    saved = store.load(manifest.batch_id).lectures[0]
                    self.shutdown_observation = (
                        saved.status,
                        bool(saved.output_path and Path(saved.output_path).is_file()),
                    )
            manager = Manager()
            class Locators:
                navigation_timeout = 10
                instances = []
                def __init__(self, _page):
                    self.page = _page
                    self.current_response = ''
                    self.lecture_turn = 0
                    self.last_filled = ''
                    self.reference_uploads = 0
                    self.lecture_uploads = 0
                    self.sends = 0
                    self.correction_sent = False
                    type(self).instances.append(self)
                def arm_initialization_listener(self): return object()
                def disarm_initialization_listener(self, listener): pass
                async def wait_for_initialization(self, cancellation=None, *, listener=None): pass
                async def wait_for_landing_screen(self): pass
                async def open_run_settings_panel(self): pass
                async def close_run_settings_panel(self): pass
                async def current_model(self): return 'Gemini 3.8 Flash'
                async def current_thinking_level(self): return SimpleNamespace(supported=True, current='High', options=None)
                async def wait_for_prompt_input(self, cancellation=None): return object()
                async def prompt_is_interactive(self, prompt): return True
                async def set_model(self, value): return False
                async def set_thinking_level(self, value): return False
                async def fill_prompt(self, text):
                    self.last_filled = text
                    self.correction_sent = self.correction_sent or 'previous response was not valid JSON' in text
                async def attach_reference_file(self, _path): self.reference_uploads += 1; return 'reference.txt'
                async def attach_lecture_file(self, _path): self.lecture_uploads += 1; return 'one.pdf'
                async def wait_for_attachment_token_count(self, filename): return AttachmentReady.TOKEN_COUNT
                async def send_prompt(self): self.sends += 1
                async def wait_for_generation_start(self): pass
                async def wait_for_generation_complete(self): self.current_response = 'CALIBRATION COMPLETE'
                async def wait_for_lecture_generation_complete(self):
                    self.lecture_turn += 1
                    self.current_response = malformed if self.lecture_turn == 1 else corrected
                async def wait_for_completed_assistant_response(self, timeout=5.0): return True
                async def last_assistant_response_text(self): return self.current_response
                async def has_transient_generation_error(self): return False
            template = (Path(__file__).resolve().parents[1] / 'mcq_maker' / 'assets' / 'standard_exam.html').read_text(encoding='utf-8')
            async def no_wait(_session): pass
            with patch('mcq_maker.ai_studio_session.AIStudioConversationSession._wait_before_send', no_wait), \
                 patch('mcq_maker.ai_studio_batch.AIStudioConversationSession',
                       side_effect=lambda browser: AIStudioConversationSession(browser, locator_factory=Locators)):
                result = asyncio.run(GoogleAIStudioBatchController(
                    manifest, browser_manager=manager, template=template,
                    template_entry={}, store=store,
                    event_callback=lambda name, status, detail: pool_events.append((name, status, detail)),
                ).run())
            item = store.load(manifest.batch_id).lectures[0]
            self.assertEqual(result.lectures[0].status, BatchStatus.COMPLETED)
            self.assertEqual(item.status, BatchStatus.COMPLETED)
            self.assertEqual(item.validation_diagnostics, {})
            self.assertTrue(Path(item.output_path).is_file())
            output = Path(item.output_path).read_text(encoding='utf-8')
            self.assertIn('const quizData = ', output)
            self.assertEqual(Path(item.output_path).stat().st_size, len(output.encode('utf-8')))
            self.assertEqual(Locators.instances[0].reference_uploads, 1)
            self.assertEqual(Locators.instances[0].lecture_uploads, 1)
            self.assertEqual(Locators.instances[0].sends, 2)
            self.assertFalse(Locators.instances[0].correction_sent)
            self.assertEqual(manager.context.pages, [page])
            self.assertEqual(manager.shutdown_observation, (BatchStatus.COMPLETED, True))
            self.assertTrue(item.worker_id.startswith('worker-'))
            self.assertTrue(item.claimed_at)
            self.assertIn(('Worker Pool', 'Worker started'), [(name, status) for name, status, _ in pool_events])
            self.assertIn(('Worker Pool', 'Job claimed'), [(name, status) for name, status, _ in pool_events])
            self.assertIn(('Worker Pool', 'Job completed'), [(name, status) for name, status, _ in pool_events])
            self.assertIn(('Worker Pool', 'Worker stopped'), [(name, status) for name, status, _ in pool_events])

    def test_json_syntax_failure_is_needs_attention_and_never_saves_html(self):
        import asyncio
        from mcq_maker.ai_studio_batch import GoogleAIStudioBatchController
        from mcq_maker.ai_studio_errors import AIStudioValidationError
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'one.pdf').write_bytes(b'lecture')
            prompt = root / 'prompt.txt'; prompt.write_text('prompt')
            reference = root / 'reference.txt'; reference.write_text('reference')
            store = BatchStateStore(root / 'state')
            manifest = create_manifest(root, prompt, reference, 'm', 'High', root / 'out', store=store)

            class FakeSession:
                diagnostic_callback = None
                def __init__(self, manager, *, quiz_validator=None): pass
                async def discover_capabilities(self): pass
                async def configure_defaults(self, **kwargs): pass
                async def run_calibration(self, *args): pass
                async def generate_lecture(self, path):
                    self.diagnostic_callback('quiz_validation_failed', {
                        'error_class': 'QuizError', 'error_category': 'json_syntax', 'issue_count': 1,
                        'parser_exception_class': 'JSONDecodeError', 'parser_message': 'Expecting value',
                        'line': 1, 'column': 20, 'position': 19, 'candidate_length': 31,
                        'candidate_count': 1,
                    })
                    raise AIStudioValidationError('The generated quiz did not pass MCQ Maker validation.')
            class Manager:
                async def shutdown(self): pass

            template = (Path(__file__).resolve().parents[1] / 'mcq_maker' / 'assets' / 'standard_exam.html').read_text(encoding='utf-8')
            with patch('mcq_maker.ai_studio_batch.AIStudioConversationSession', FakeSession):
                result = asyncio.run(GoogleAIStudioBatchController(
                    manifest, browser_manager=Manager(), template=template,
                    template_entry={}, store=store,
                ).run())
            saved = store.load(manifest.batch_id).lectures[0]
            self.assertEqual(result.lectures[0].status, BatchStatus.ATTENTION)
            self.assertEqual(saved.status, BatchStatus.ATTENTION)
            self.assertIsNone(saved.output_path)
            self.assertEqual(saved.validation_diagnostics['failure_category'], 'json_syntax')
            self.assertIn('Invalid JSON returned by AI Studio', saved.last_error)
            self.assertFalse((root / 'out').exists())

    def test_json_syntax_diagnostic_is_persisted_without_response_content(self):
        from mcq_maker.ai_studio_batch import GoogleAIStudioBatchController
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / 'one.pdf').write_bytes(b'a')
            store = BatchStateStore(root / 'state')
            manifest = create_manifest(root, root / 'p', root / 'r', 'm', 'High', root / 'out', store=store)
            class Manager: pass
            controller = GoogleAIStudioBatchController(
                manifest, browser_manager=Manager(), template='', template_entry={}, store=store,
            )
            item = manifest.lectures[0]
            item.status = BatchStatus.ATTENTION
            controller._record_post_generation_stage(item, 'quiz_validation_failed', {
                'error_class': 'QuizError', 'error_category': 'json_syntax', 'issue_count': 1,
                'candidate_count': 1, 'candidate_length': 87,
                'parser_exception_class': 'JSONDecodeError', 'parser_message': 'Expecting value',
                'line': 3, 'column': 18, 'position': 64,
            })
            recovered = store.load(manifest.batch_id).lectures[0]
            self.assertEqual(recovered.status, BatchStatus.ATTENTION)
            self.assertIsNone(recovered.output_path)
            self.assertEqual(recovered.validation_diagnostics, {
                'failure_category': 'json_syntax', 'parser_exception_class': 'JSONDecodeError',
                'parser_message': 'Expecting value', 'issue_count': 1,
                'candidate_length': 87, 'candidate_count': 1,
                'line': 3, 'column': 18, 'position': 64,
            })
            self.assertIn('line 3, column 18', recovered.last_error)

    def test_discovery_is_stable_and_pdf_only(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'b.PDF').write_bytes(b'b')
            (root / 'a.pdf').write_bytes(b'a')
            (root / 'ignore.txt').write_text('x')
            self.assertEqual([p.name for p in discover_lecture_pdfs(root)], ['a.pdf', 'b.PDF'])

    def test_manifest_round_trip_and_atomic_update(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source = root / 'one.pdf'; source.write_bytes(b'lecture')
            store = BatchStateStore(root / 'state')
            manifest = create_manifest(root, root / 'prompt.txt', root / 'reference.txt',
                                       'Gemini 3.8 Flash', 'High', root / 'out', store=store)
            loaded = store.load(manifest.batch_id)
            self.assertEqual(loaded.lectures[0].fingerprint, input_fingerprint(source))
            loaded.lectures[0].status = BatchStatus.COMPLETED
            target = store.save(loaded)
            self.assertTrue(target.is_file())
            self.assertEqual(store.load(manifest.batch_id).lectures[0].status, BatchStatus.COMPLETED)
            raw = json.loads(target.read_text(encoding='utf-8'))
            self.assertEqual(raw['schema_version'], 3)
            self.assertTrue(raw['configuration_fingerprint'])

    def test_changed_input_is_detectable(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source = root / 'one.pdf'; source.write_bytes(b'a')
            store = BatchStateStore(root / 'state')
            manifest = create_manifest(root, root / 'p', root / 'r', 'm', 'High', root / 'o', store=store)
            source.write_bytes(b'changed')
            self.assertNotEqual(manifest.lectures[0].fingerprint, input_fingerprint(source))

    def test_startup_reconciles_changed_sources_and_interrupted_submissions(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / 'one.pdf').write_bytes(b'old')
            store = BatchStateStore(root / 'state')
            manifest = create_manifest(root, root / 'p', root / 'r', 'm', 'High', root / 'out', store=store)
            item = manifest.lectures[0]
            item.status = BatchStatus.GENERATING
            item.lecture_submission_state = 'unknown'
            item.worker_id = 'worker-crashed'
            item.claimed_at = '2026-09-27T00:00:00+00:00'
            store.save(manifest)
            recovered = store.unfinished()[0]
            self.assertEqual(recovered.lectures[0].status, BatchStatus.INTERRUPTED)
            self.assertEqual(recovered.lectures[0].worker_id, 'worker-crashed')
            self.assertEqual(recovered.lectures[0].claimed_at, '2026-09-27T00:00:00+00:00')
            (root / 'one.pdf').write_bytes(b'new')
            recovered = store.unfinished()[0]
            self.assertEqual(recovered.lectures[0].status, BatchStatus.PENDING)
            self.assertEqual(recovered.lectures[0].fingerprint, input_fingerprint(root / 'one.pdf'))
            self.assertIsNone(recovered.lectures[0].worker_id)
            self.assertIsNone(recovered.lectures[0].claimed_at)

    def test_completed_job_with_missing_output_needs_attention(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / 'one.pdf').write_bytes(b'a')
            store = BatchStateStore(root / 'state')
            manifest = create_manifest(root, root / 'p', root / 'r', 'm', 'High', root / 'out', store=store)
            item = manifest.lectures[0]
            item.status = BatchStatus.COMPLETED
            item.output_path = str(root / 'gone.html')
            store.save(manifest)
            recovered = store.unfinished()[0]
            self.assertEqual(recovered.lectures[0].status, BatchStatus.ATTENTION)

    def test_discard_removes_only_selected_manifest_and_preserves_html_outputs(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'one.pdf').write_bytes(b'a')
            store = BatchStateStore(root / 'state')
            first = create_manifest(root, root / 'p', root / 'r', 'm', 'High', root / 'out', store=store)
            second_folder = root / 'second'; second_folder.mkdir(); (second_folder / 'two.pdf').write_bytes(b'b')
            second = create_manifest(second_folder, root / 'p', root / 'r', 'm', 'High', root / 'out', store=store)
            output = root / 'out' / 'kept.html'; output.parent.mkdir(); output.write_text('<html>exam</html>')
            first.lectures[0].status = BatchStatus.COMPLETED
            first.lectures[0].output_path = str(output)
            store.save(first)

            store.discard(first.batch_id)

            self.assertFalse(store.path_for(first.batch_id).exists())
            self.assertTrue(store.path_for(second.batch_id).exists())
            self.assertTrue(output.is_file())

    def test_discard_is_blocked_while_controller_holds_batch_lease(self):
        import asyncio
        from mcq_maker.ai_studio_batch import GoogleAIStudioBatchController
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / 'one.pdf').write_bytes(b'a')
            store = BatchStateStore(root / 'state')
            manifest = create_manifest(root, root / 'p', root / 'r', 'm', 'High', root / 'out', store=store)
            controller = GoogleAIStudioBatchController(
                manifest, browser_manager=None, template='', template_entry={}, store=store,
            )
            controller._acquire_lease()
            try:
                with self.assertRaisesRegex(RuntimeError, 'active queue'):
                    store.discard(manifest.batch_id)
                self.assertTrue(store.path_for(manifest.batch_id).is_file())
            finally:
                controller._release_lease()

    def test_configuration_fingerprint_includes_prompt_reference_and_settings(self):
        from mcq_maker.ai_studio_batch import manifest_matches_configuration
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / 'one.pdf').write_bytes(b'a')
            prompt = root / 'p'; prompt.write_text('prompt a')
            reference = root / 'r'; reference.write_text('reference')
            store = BatchStateStore(root / 'state')
            manifest = create_manifest(root, prompt, reference, 'm', 'High', root / 'out',
                                       store=store, template_id='t', template='template')
            common = dict(prompt_path=prompt, reference_path=reference, model='m', thinking='High',
                          output_folder=root / 'out', template_id='t', template='template')
            self.assertTrue(manifest_matches_configuration(manifest, **common))
            prompt.write_text('prompt b')
            self.assertFalse(manifest_matches_configuration(manifest, **common))

    def test_unfinished_returns_only_incomplete_batches(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); source = root / 'one.pdf'; source.write_bytes(b'a')
            store = BatchStateStore(root / 'state')
            manifest = create_manifest(root, root / 'p', root / 'r', 'm', 'High', root / 'o', store=store)
            self.assertEqual(len(store.unfinished()), 1)
            loaded = store.load(manifest.batch_id)
            output = root / 'out' / 'saved.html'; output.parent.mkdir(); output.write_text('exam')
            loaded.lectures[0].status = BatchStatus.COMPLETED
            loaded.lectures[0].output_path = str(output)
            store.save(loaded)
            self.assertEqual(store.unfinished(), [])

    def test_status_vocabulary_covers_resumable_lifecycle(self):
        for value in ('Pending', 'Waiting to retry', 'Opening new conversation',
                      'Configuring AI Studio', 'Calibrating', 'Calibration ready',
                      'Uploading lecture', 'Generating questions', 'Validating JSON',
                      'Saving HTML', 'Completed', 'Failed temporarily', 'Needs attention',
                      'Interrupted', 'Paused', 'Cancelled'):
            self.assertTrue(value)

    def test_controller_processes_lectures_sequentially_and_persists_outputs(self):
        import asyncio
        from mcq_maker.ai_studio_batch import GoogleAIStudioBatchController
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / 'b.pdf').write_bytes(b'b'); (root / 'a.pdf').write_bytes(b'a')
            prompt = root / 'prompt.txt'; prompt.write_text('prompt')
            reference = root / 'reference.txt'; reference.write_text('reference')
            store = BatchStateStore(root / 'state')
            manifest = create_manifest(root, prompt, reference, 'Gemini 3.8 Flash', 'High', root / 'out', store=store)
            quiz = SimpleNamespace(title='Demo', questions=[1], payload={'title': 'Demo', 'questions': []})
            completion_snapshots = []

            def observe_event(name, status, detail):
                if status == BatchStatus.COMPLETED:
                    saved = store.load(manifest.batch_id)
                    item = next(job for job in saved.lectures if job.file_name == name)
                    completion_snapshots.append((item.status, item.output_path, Path(item.output_path).is_file()))

            class FakeSession:
                active = 0; maximum = 0
                def __init__(self, manager, *, quiz_validator=None): self.manager = manager
                async def discover_capabilities(self): pass
                async def configure_defaults(self, **kwargs): pass
                async def run_calibration(self, *args): pass
                async def generate_lecture(self, path):
                    if Path(path).name == 'b.pdf':
                        first = store.load(manifest.batch_id).lectures[0]
                        self_check = Path(first.output_path) if first.output_path else None
                        if first.status != BatchStatus.COMPLETED or not self_check or not self_check.is_file():
                            raise AssertionError('Lecture A must be saved and Completed before Lecture B starts.')
                    FakeSession.active += 1; FakeSession.maximum = max(FakeSession.maximum, FakeSession.active)
                    await asyncio.sleep(0)
                    FakeSession.active -= 1
                    return quiz

            class FakeManager:
                async def shutdown(self): pass

            template = (Path(__file__).resolve().parents[1] / 'mcq_maker' / 'assets' / 'standard_exam.html').read_text(encoding='utf-8')
            with patch('mcq_maker.ai_studio_batch.AIStudioConversationSession', FakeSession):
                result = asyncio.run(GoogleAIStudioBatchController(
                    manifest, browser_manager=FakeManager(), template=template,
                    template_entry={'minimum_options': 2, 'maximum_options': 4}, store=store,
                    event_callback=observe_event,
                ).run())
            self.assertEqual([x.status for x in result.lectures], ['Completed', 'Completed'])
            self.assertEqual(FakeSession.maximum, 1)
            self.assertTrue(all(Path(x.output_path).is_file() for x in result.lectures))
            self.assertEqual(len({x.output_path for x in result.lectures}), 2)
            self.assertEqual(len(completion_snapshots), 2)
            self.assertTrue(all(status == BatchStatus.COMPLETED and exists for status, _, exists in completion_snapshots))

    def test_later_lecture_failure_does_not_undo_earlier_saved_output(self):
        import asyncio
        from mcq_maker.ai_studio_batch import GoogleAIStudioBatchController
        from mcq_maker.ai_studio_errors import AIStudioResponseError
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / 'a.pdf').write_bytes(b'a'); (root / 'b.pdf').write_bytes(b'b')
            prompt = root / 'prompt.txt'; prompt.write_text('prompt')
            reference = root / 'reference.txt'; reference.write_text('reference')
            store = BatchStateStore(root / 'state')
            manifest = create_manifest(root, prompt, reference, 'Gemini 3.8 Flash', 'High', root / 'out', store=store)
            quiz = SimpleNamespace(title='Demo', questions=[1], payload={'title': 'Demo', 'questions': []})

            class FakeSession:
                def __init__(self, manager, *, quiz_validator=None): pass
                async def discover_capabilities(self): pass
                async def configure_defaults(self, **kwargs): pass
                async def run_calibration(self, *args): pass
                async def generate_lecture(self, path):
                    if Path(path).name == 'b.pdf':
                        raise AIStudioResponseError('HTTP 403')
                    return quiz

            class FakeManager:
                async def shutdown(self): pass

            template = (Path(__file__).resolve().parents[1] / 'mcq_maker' / 'assets' / 'standard_exam.html').read_text(encoding='utf-8')
            events = []
            with patch('mcq_maker.ai_studio_batch.AIStudioConversationSession', FakeSession):
                result = asyncio.run(GoogleAIStudioBatchController(
                    manifest, browser_manager=FakeManager(), template=template,
                    template_entry={}, store=store,
                    event_callback=lambda lecture, status, detail: events.append((lecture, status, detail)),
                ).run())
            saved = store.load(manifest.batch_id)
            first, second = saved.lectures
            self.assertEqual(first.status, BatchStatus.COMPLETED)
            self.assertTrue(Path(first.output_path).is_file())
            self.assertEqual(second.status, BatchStatus.ATTENTION)
            self.assertIsNone(second.output_path)
            self.assertEqual([job.status for job in result.lectures], [BatchStatus.COMPLETED, BatchStatus.ATTENTION])

    def test_final_json_is_validated_saved_verified_before_browser_cleanup(self):
        import asyncio
        from mcq_maker.ai_studio_batch import GoogleAIStudioBatchController
        from mcq_maker.ai_studio_session import AIStudioConversationSession
        from mcq_maker.quiz_validation import validate_quiz
        response = '[{"title":"Acceptance Exam"},{"question":"Sample?","options":["A","B"],"correct":0,"explanation":"Fixture."}]'
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / 'one.pdf').write_bytes(b'lecture fixture')
            prompt = root / 'prompt.txt'; prompt.write_text('prompt')
            reference = root / 'reference.txt'; reference.write_text('reference')
            store = BatchStateStore(root / 'state')
            manifest = create_manifest(root, prompt, reference, 'Gemini 3.8 Flash', 'High', root / 'out', store=store)
            template = (Path(__file__).resolve().parents[1] / 'mcq_maker' / 'assets' / 'standard_exam.html').read_text(encoding='utf-8')
            events = []

            class PipelineSession(AIStudioConversationSession):
                def __init__(self, manager, *, quiz_validator=None):
                    self.locators = SimpleNamespace(last_assistant_response_text=self._final_answer)
                    self.quiz_validator = quiz_validator or validate_quiz
                    self.diagnostic_callback = None
                    self._last_response_text = None
                async def _final_answer(self): return response
                async def discover_capabilities(self): pass
                async def configure_defaults(self, **kwargs): pass
                async def run_calibration(self, *args): pass
                async def generate_lecture(self, path): return await self._completed_lecture_result()

            class Manager:
                def __init__(self): self.shutdown_snapshot = None
                async def shutdown(self):
                    saved = store.load(manifest.batch_id)
                    item = saved.lectures[0]
                    self.shutdown_snapshot = (item.status, item.output_path, item.question_count)

            manager = Manager()
            with patch('mcq_maker.ai_studio_batch.AIStudioConversationSession', PipelineSession):
                result = asyncio.run(GoogleAIStudioBatchController(
                    manifest, browser_manager=manager, template=template,
                    template_entry={}, store=store,
                    event_callback=lambda *event: events.append(event),
                ).run())
            item = store.load(manifest.batch_id).lectures[0]
            output = Path(item.output_path)
            self.assertEqual(item.status, BatchStatus.COMPLETED)
            self.assertEqual(item.question_count, 1)
            self.assertTrue(output.is_file())
            self.assertGreater(output.stat().st_size, 0)
            self.assertIn('const quizData = ', output.read_text(encoding='utf-8'))
            self.assertEqual(manager.shutdown_snapshot, ('Completed', item.output_path, 1))
            self.assertEqual([detail for _, _, detail in events if 'captured' in detail.casefold() or 'validation passed' in detail.casefold()], [
                f'Final answer captured ({len(response)} characters).',
                'Quiz validation passed (1 question).',
            ])
            self.assertNotIn(response, store.path_for(manifest.batch_id).read_text(encoding='utf-8'))

    def test_failed_exam_save_never_marks_lecture_completed(self):
        import asyncio
        from mcq_maker.ai_studio_batch import GoogleAIStudioBatchController
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / 'one.pdf').write_bytes(b'a')
            prompt = root / 'p'; prompt.write_text('p'); reference = root / 'r'; reference.write_text('r')
            store = BatchStateStore(root / 'state')
            manifest = create_manifest(root, prompt, reference, 'm', 'High', root / 'out', store=store)
            quiz = SimpleNamespace(title='Demo', questions=[1], payload={'title': 'Demo', 'questions': []})
            class FakeSession:
                def __init__(self, manager, *, quiz_validator=None): pass
                async def discover_capabilities(self): pass
                async def configure_defaults(self, **kwargs): pass
                async def run_calibration(self, *args): pass
                async def generate_lecture(self, path): return quiz
            class FakeManager:
                async def shutdown(self): pass
            with patch('mcq_maker.ai_studio_batch.AIStudioConversationSession', FakeSession), \
                 patch('mcq_maker.ai_studio_batch.save_exam', side_effect=OSError('output unavailable')):
                result = asyncio.run(GoogleAIStudioBatchController(
                    manifest, browser_manager=FakeManager(), template='', template_entry={}, store=store,
                ).run())
            self.assertEqual(result.lectures[0].status, BatchStatus.ATTENTION)
            self.assertIsNone(result.lectures[0].output_path)
            saved = store.load(manifest.batch_id).lectures[0]
            self.assertNotEqual(saved.status, BatchStatus.COMPLETED)
            self.assertEqual(saved.stage, 'saving')
            self.assertIn('saving: OSError: output unavailable', saved.last_error)

    def test_html_without_the_validated_quiz_payload_is_not_completed(self):
        import asyncio
        from mcq_maker.ai_studio_batch import GoogleAIStudioBatchController
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / 'one.pdf').write_bytes(b'a')
            prompt = root / 'p'; prompt.write_text('p'); reference = root / 'r'; reference.write_text('r')
            store = BatchStateStore(root / 'state')
            manifest = create_manifest(root, prompt, reference, 'm', 'High', root / 'out', store=store)
            quiz = SimpleNamespace(title='Demo', questions=[1], payload={'title': 'Demo', 'questions': [1]})
            wrong_output = root / 'out' / 'wrong.html'
            wrong_output.parent.mkdir()
            wrong_output.write_text('<!doctype html><title>wrong</title>', encoding='utf-8')

            class FakeSession:
                diagnostic_callback = None
                def __init__(self, manager, *, quiz_validator=None): pass
                async def discover_capabilities(self): pass
                async def configure_defaults(self, **kwargs): pass
                async def run_calibration(self, *args): pass
                async def generate_lecture(self, path): return quiz
            class Manager:
                async def shutdown(self): pass

            with patch('mcq_maker.ai_studio_batch.AIStudioConversationSession', FakeSession), \
                 patch('mcq_maker.ai_studio_batch.save_exam', return_value=wrong_output):
                result = asyncio.run(GoogleAIStudioBatchController(
                    manifest, browser_manager=Manager(), template='', template_entry={}, store=store,
                ).run())
            item = result.lectures[0]
            self.assertEqual(item.status, BatchStatus.ATTENTION)
            self.assertIsNone(item.output_path)
            self.assertEqual(item.stage, 'verifying_saved_html')
            self.assertIn('validated quiz data', item.last_error)

    def test_validation_failure_persists_known_response_and_safe_stage(self):
        import asyncio
        from mcq_maker.ai_studio_batch import GoogleAIStudioBatchController
        from mcq_maker.ai_studio_errors import AIStudioValidationError
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / 'one.pdf').write_bytes(b'a')
            prompt = root / 'p'; prompt.write_text('p'); reference = root / 'r'; reference.write_text('r')
            store = BatchStateStore(root / 'state')
            manifest = create_manifest(root, prompt, reference, 'm', 'High', root / 'out', store=store)

            class FakeSession:
                diagnostic_callback = None
                def __init__(self, manager, *, quiz_validator=None): pass
                async def discover_capabilities(self): pass
                async def configure_defaults(self, **kwargs): pass
                async def run_calibration(self, *args): pass
                async def generate_lecture(self, path):
                    self.diagnostic_callback('lecture_response_captured', {'response_length': 250})
                    self.diagnostic_callback('quiz_validation_failed', {
                        'error_class': 'QuizError', 'error_category': 'question_fields', 'issue_count': 1,
                    })
                    raise AIStudioValidationError('The generated quiz did not pass MCQ Maker validation.')
            class Manager:
                async def shutdown(self): pass

            with patch('mcq_maker.ai_studio_batch.AIStudioConversationSession', FakeSession):
                result = asyncio.run(GoogleAIStudioBatchController(
                    manifest, browser_manager=Manager(), template='', template_entry={}, store=store,
                ).run())
            item = store.load(manifest.batch_id).lectures[0]
            self.assertEqual(result.lectures[0].status, BatchStatus.ATTENTION)
            self.assertEqual(item.lecture_submission_state, 'response_received')
            self.assertEqual(item.stage, 'quiz_validation_failed')
            self.assertEqual(item.last_error, 'quiz_validation_failed: Quiz validation failed (QuizError; question_fields; 1 issue(s)).')
            self.assertIsNone(item.output_path)

    def test_temporary_failure_is_deferred_until_next_lecture_is_attempted(self):
        import asyncio
        from mcq_maker.ai_studio_batch import GoogleAIStudioBatchController
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / 'a.pdf').write_bytes(b'a'); (root / 'b.pdf').write_bytes(b'b')
            prompt = root / 'p'; prompt.write_text('p'); reference = root / 'r'; reference.write_text('r')
            store = BatchStateStore(root / 'state')
            manifest = create_manifest(root, prompt, reference, 'm', 'High', root / 'o', store=store)
            quiz = SimpleNamespace(title='Demo', questions=[1], payload={'title': 'Demo', 'questions': []})
            events = []
            class FakeSession:
                opened = 0
                def __init__(self, manager, *, quiz_validator=None):
                    type(self).opened += 1
                    self.first = type(self).opened == 1
                async def discover_capabilities(self):
                    if self.first:
                        from mcq_maker.ai_studio_errors import AIStudioConnectionError
                        raise AIStudioConnectionError('offline before a lecture is submitted')
                async def configure_defaults(self, **kwargs): pass
                async def run_calibration(self, *args): pass
                async def generate_lecture(self, path): return quiz
            class FakeManager:
                async def shutdown(self): pass
            template = (Path(__file__).resolve().parents[1] / 'mcq_maker' / 'assets' / 'standard_exam.html').read_text(encoding='utf-8')
            with patch('mcq_maker.ai_studio_batch.AIStudioConversationSession', FakeSession), \
                 patch.object(GoogleAIStudioBatchController, '_next_delay', return_value=0):
                result = asyncio.run(GoogleAIStudioBatchController(
                    manifest, browser_manager=FakeManager(), template=template,
                    template_entry={'minimum_options': 2, 'maximum_options': 4}, store=store,
                    event_callback=lambda name, status, detail: events.append((name, status)),
                ).run())
            self.assertEqual([x.status for x in result.lectures], ['Completed', 'Completed'])
            self.assertLess(events.index(('b.pdf', 'Opening new conversation')), events.index(('a.pdf', 'Completed')))

    def test_uncertain_generation_is_not_retried_automatically(self):
        import asyncio
        from mcq_maker.ai_studio_batch import GoogleAIStudioBatchController
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / 'one.pdf').write_bytes(b'a')
            prompt = root / 'p'; prompt.write_text('p'); reference = root / 'r'; reference.write_text('r')
            store = BatchStateStore(root / 'state')
            manifest = create_manifest(root, prompt, reference, 'm', 'High', root / 'out', store=store)
            class FakeSession:
                calls = 0
                def __init__(self, manager, *, quiz_validator=None): pass
                async def discover_capabilities(self): pass
                async def configure_defaults(self, **kwargs): pass
                async def run_calibration(self, *args): pass
                async def generate_lecture(self, path):
                    type(self).calls += 1
                    raise TimeoutError('generation completion unknown')
            class FakeManager:
                async def shutdown(self): pass
            with patch('mcq_maker.ai_studio_batch.AIStudioConversationSession', FakeSession):
                result = asyncio.run(GoogleAIStudioBatchController(
                    manifest, browser_manager=FakeManager(), template='', template_entry={}, store=store,
                ).run())
            self.assertEqual(FakeSession.calls, 1)
            self.assertEqual(result.lectures[0].status, BatchStatus.INTERRUPTED)
            self.assertEqual(store.load(manifest.batch_id).lectures[0].lecture_submission_state, 'unknown')

    def test_duplicate_worker_lease_is_rejected(self):
        from mcq_maker.ai_studio_batch import GoogleAIStudioBatchController
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / 'one.pdf').write_bytes(b'a')
            store = BatchStateStore(root / 'state')
            manifest = create_manifest(root, root / 'p', root / 'r', 'm', 'High', root / 'out', store=store)
            class Manager: pass
            first = GoogleAIStudioBatchController(manifest, browser_manager=Manager(), template='', template_entry={}, store=store)
            second = GoogleAIStudioBatchController(manifest, browser_manager=Manager(), template='', template_entry={}, store=store)
            first._acquire_lease()
            try:
                with self.assertRaisesRegex(RuntimeError, 'already being processed'):
                    second._acquire_lease()
            finally:
                first._release_lease()

    def test_explicit_http_rejection_is_not_confused_with_unknown_submission(self):
        import asyncio
        from mcq_maker.ai_studio_batch import GoogleAIStudioBatchController
        from mcq_maker.ai_studio_errors import AIStudioResponseError
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / 'one.pdf').write_bytes(b'a')
            prompt = root / 'p'; prompt.write_text('p'); reference = root / 'r'; reference.write_text('r')
            store = BatchStateStore(root / 'state')
            manifest = create_manifest(root, prompt, reference, 'm', 'High', root / 'out', store=store)
            class FakeSession:
                def __init__(self, manager, *, quiz_validator=None): pass
                async def discover_capabilities(self): pass
                async def configure_defaults(self, **kwargs): pass
                async def run_calibration(self, *args): pass
                async def generate_lecture(self, path): raise AIStudioResponseError('HTTP 403')
            class FakeManager:
                async def shutdown(self): pass
            with patch('mcq_maker.ai_studio_batch.AIStudioConversationSession', FakeSession):
                result = asyncio.run(GoogleAIStudioBatchController(
                    manifest, browser_manager=FakeManager(), template='', template_entry={}, store=store,
                ).run())
            item = result.lectures[0]
            self.assertEqual(item.status, BatchStatus.ATTENTION)
            self.assertEqual(item.retry_count, 0)
            self.assertEqual(item.lecture_submission_state, 'response_rejected')
            self.assertIn('HTTP 403', item.last_error)

    def test_transient_http_status_after_submission_needs_attention_without_resend(self):
        import asyncio
        from mcq_maker.ai_studio_batch import GoogleAIStudioBatchController
        from mcq_maker.ai_studio_errors import AIStudioResponseError
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / 'one.pdf').write_bytes(b'a')
            prompt = root / 'p'; prompt.write_text('p'); reference = root / 'r'; reference.write_text('r')
            store = BatchStateStore(root / 'state')
            manifest = create_manifest(root, prompt, reference, 'm', 'High', root / 'out', store=store)
            quiz = SimpleNamespace(title='Demo', questions=[1], payload={'title': 'Demo', 'questions': []})
            class FakeSession:
                calls = 0
                def __init__(self, manager, *, quiz_validator=None): pass
                async def discover_capabilities(self): pass
                async def configure_defaults(self, **kwargs): pass
                async def run_calibration(self, *args): pass
                async def generate_lecture(self, path):
                    type(self).calls += 1
                    if type(self).calls == 1:
                        raise AIStudioResponseError('HTTP 503')
                    return quiz
            class FakeManager:
                async def shutdown(self): pass
            template = (Path(__file__).resolve().parents[1] / 'mcq_maker' / 'assets' / 'standard_exam.html').read_text(encoding='utf-8')
            with patch('mcq_maker.ai_studio_batch.AIStudioConversationSession', FakeSession):
                result = asyncio.run(GoogleAIStudioBatchController(
                    manifest, browser_manager=FakeManager(), template=template,
                    template_entry={}, store=store,
                ).run())
            self.assertEqual(FakeSession.calls, 1)
            self.assertEqual(result.lectures[0].status, BatchStatus.ATTENTION)
            self.assertEqual(result.lectures[0].retry_count, 0)
            self.assertIsNone(result.lectures[0].output_path)

    def test_pause_after_running_lecture_persists_and_requires_explicit_resume(self):
        import asyncio
        from mcq_maker.ai_studio_batch import GoogleAIStudioBatchController
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / 'a.pdf').write_bytes(b'a'); (root / 'b.pdf').write_bytes(b'b')
            prompt = root / 'p'; prompt.write_text('p'); reference = root / 'r'; reference.write_text('r')
            store = BatchStateStore(root / 'state')
            manifest = create_manifest(root, prompt, reference, 'm', 'High', root / 'out', store=store)
            pause = Event(); events = []; calls = []
            quiz = SimpleNamespace(title='Demo', questions=[1], payload={'title': 'Demo', 'questions': []})
            class FakeSession:
                def __init__(self, manager, *, quiz_validator=None): pass
                async def discover_capabilities(self): pass
                async def configure_defaults(self, **kwargs): pass
                async def run_calibration(self, *args): pass
                async def generate_lecture(self, path):
                    calls.append(Path(path).name)
                    if Path(path).name == 'a.pdf': pause.set()
                    return quiz
            class FakeManager:
                async def shutdown(self): pass
            template = (Path(__file__).resolve().parents[1] / 'mcq_maker' / 'assets' / 'standard_exam.html').read_text(encoding='utf-8')
            with patch('mcq_maker.ai_studio_batch.AIStudioConversationSession', FakeSession):
                controller = GoogleAIStudioBatchController(
                    manifest, browser_manager=FakeManager(), template=template, template_entry={},
                    store=store, pause_event=pause,
                    event_callback=lambda name, status, detail: events.append((name, status)),
                )
                result = asyncio.run(controller.run())
            self.assertEqual([item.status for item in result.lectures], [BatchStatus.COMPLETED, BatchStatus.PAUSED])
            self.assertEqual(calls, ['a.pdf'])
            recovered = store.load(manifest.batch_id)
            self.assertEqual(recovered.control_state, 'paused')
            self.assertEqual(recovered.lectures[0].status, BatchStatus.COMPLETED)
            self.assertTrue(Path(recovered.lectures[0].output_path).is_file())
            self.assertEqual(recovered.lectures[1].status, BatchStatus.PAUSED)
            self.assertIsNone(recovered.lectures[1].next_retry_at)
            self.assertIn(('b.pdf', BatchStatus.PAUSED), events)

            pause.clear()
            with patch('mcq_maker.ai_studio_batch.AIStudioConversationSession', FakeSession):
                resumed = asyncio.run(GoogleAIStudioBatchController(
                    recovered, browser_manager=FakeManager(), template=template, template_entry={}, store=store,
                ).run())
            self.assertEqual(calls, ['a.pdf', 'b.pdf'])
            self.assertEqual([item.status for item in resumed.lectures], [BatchStatus.COMPLETED, BatchStatus.COMPLETED])

    def test_cancel_preserves_completed_and_marks_later_lecture_cancelled(self):
        import asyncio
        from mcq_maker.ai_studio_batch import GoogleAIStudioBatchController
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / 'a.pdf').write_bytes(b'a'); (root / 'b.pdf').write_bytes(b'b')
            prompt = root / 'p'; prompt.write_text('p'); reference = root / 'r'; reference.write_text('r')
            store = BatchStateStore(root / 'state')
            manifest = create_manifest(root, prompt, reference, 'm', 'High', root / 'out', store=store)
            cancelled = Event()
            quiz = SimpleNamespace(title='Demo', questions=[1], payload={'title': 'Demo', 'questions': []})
            class FakeSession:
                def __init__(self, manager, *, quiz_validator=None): pass
                async def discover_capabilities(self): pass
                async def configure_defaults(self, **kwargs): pass
                async def run_calibration(self, *args): pass
                async def generate_lecture(self, path):
                    if Path(path).name == 'a.pdf': cancelled.set()
                    return quiz
            class FakeManager:
                async def shutdown(self): pass
            template = (Path(__file__).resolve().parents[1] / 'mcq_maker' / 'assets' / 'standard_exam.html').read_text(encoding='utf-8')
            with patch('mcq_maker.ai_studio_batch.AIStudioConversationSession', FakeSession):
                result = asyncio.run(GoogleAIStudioBatchController(
                    manifest, browser_manager=FakeManager(), template=template,
                    template_entry={}, store=store, cancellation=cancelled,
                ).run())
            self.assertEqual(result.lectures[0].status, BatchStatus.COMPLETED)
            self.assertEqual(result.lectures[1].status, BatchStatus.CANCELLED)
            self.assertEqual(result.control_state, 'stopped')
            persisted = store.load(manifest.batch_id)
            self.assertEqual(persisted.lectures[0].status, BatchStatus.COMPLETED)
            self.assertEqual(persisted.lectures[1].status, BatchStatus.CANCELLED)

    def test_stop_preserves_uncertain_submission_as_interrupted(self):
        import asyncio
        from mcq_maker.ai_studio_batch import GoogleAIStudioBatchController
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / 'one.pdf').write_bytes(b'a')
            prompt = root / 'p'; prompt.write_text('p'); reference = root / 'r'; reference.write_text('r')
            store = BatchStateStore(root / 'state')
            manifest = create_manifest(root, prompt, reference, 'm', 'High', root / 'out', store=store)
            cancelled = Event()

            class FakeSession:
                def __init__(self, manager, *, quiz_validator=None): pass
                async def discover_capabilities(self): pass
                async def configure_defaults(self, **kwargs): pass
                async def run_calibration(self, *args): pass
                async def generate_lecture(self, path):
                    cancelled.set()
                    raise RuntimeError('submission outcome not observed')

            class FakeManager:
                async def shutdown(self): pass

            with patch('mcq_maker.ai_studio_batch.AIStudioConversationSession', FakeSession):
                result = asyncio.run(GoogleAIStudioBatchController(
                    manifest, browser_manager=FakeManager(), template='', template_entry={},
                    store=store, cancellation=cancelled,
                ).run())
            persisted = store.load(manifest.batch_id)
            self.assertEqual(result.control_state, 'stopped')
            self.assertEqual(result.lectures[0].status, BatchStatus.INTERRUPTED)
            self.assertEqual(persisted.lectures[0].status, BatchStatus.INTERRUPTED)
            self.assertNotEqual(persisted.lectures[0].status, BatchStatus.COMPLETED)

    def test_ambiguous_calibration_is_reported_without_restarting_in_a_new_conversation(self):
        import asyncio
        from mcq_maker.ai_studio_batch import GoogleAIStudioBatchController
        from mcq_maker.ai_studio_errors import AIStudioCalibrationError
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / 'one.pdf').write_bytes(b'lecture')
            prompt = root / 'prompt'; prompt.write_text('prompt')
            reference = root / 'reference'; reference.write_text('reference')
            store = BatchStateStore(root / 'state')
            manifest = create_manifest(root, prompt, reference, 'm', 'High', root / 'out', store=store)
            events = []

            class FakeSession:
                created = 0
                def __init__(self, manager, *, quiz_validator=None):
                    type(self).created += 1
                async def discover_capabilities(self): pass
                async def configure_defaults(self, **kwargs): pass
                async def run_calibration(self, *args):
                    raise AIStudioCalibrationError('Calibration response was ambiguous.')

            class FakeManager:
                async def shutdown(self): pass

            with patch('mcq_maker.ai_studio_batch.AIStudioConversationSession', FakeSession):
                result = asyncio.run(GoogleAIStudioBatchController(
                    manifest, browser_manager=FakeManager(), template='',
                    template_entry={}, store=store,
                    event_callback=lambda name, status, detail: events.append((name, status)),
                ).run())
            self.assertEqual(result.lectures[0].status, BatchStatus.ATTENTION)
            self.assertEqual(result.lectures[0].attempt_count, 1)
            self.assertEqual(FakeSession.created, 1)
            self.assertNotIn(('one.pdf', BatchStatus.RETRY), events)

    def test_experimental_two_workers_claim_isolate_and_save_independently(self):
        import asyncio
        from mcq_maker.ai_studio_batch import GoogleAIStudioBatchController
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ('a.pdf', 'b.pdf', 'c.pdf'):
                (root / name).write_bytes(b'lecture')
            prompt = root / 'prompt'; prompt.write_text('prompt')
            reference = root / 'reference'; reference.write_text('reference')
            store = BatchStateStore(root / 'state')
            manifest = create_manifest(root, prompt, reference, 'm', 'High', root / 'out',
                                       store=store, max_workers=2)
            quiz = SimpleNamespace(title='Parallel', questions=[1],
                                   payload={'title': 'Parallel', 'questions': []})

            class FakeSession:
                active = maximum = 0
                instances = []
                def __init__(self, manager, *, isolated_page=False, quiz_validator=None):
                    self.isolated_page = isolated_page
                    self.diagnostic_callback = None
                    self.closed = False
                    self.calls = []
                    type(self).instances.append(self)
                async def discover_capabilities(self):
                    self.calls.append('discover')
                    self.diagnostic_callback('ai_studio_page_created', {'page_identity': f'fake-{id(self):x}'})
                async def configure_defaults(self, **kwargs): self.calls.append('configure')
                async def run_calibration(self, *_args):
                    self.calls.append('calibration')
                    self.diagnostic_callback('calibration_submission_started', {'page_identity': f'fake-{id(self):x}'})
                async def generate_lecture(self, path):
                    self.calls.append(('lecture_only', Path(path).name))
                    type(self).active += 1
                    type(self).maximum = max(type(self).maximum, type(self).active)
                    await asyncio.sleep(0.01)
                    type(self).active -= 1
                    return quiz
                async def close(self): self.closed = True

            class FakeManager:
                shutdowns = 0
                async def shutdown(self): type(self).shutdowns += 1

            template = (Path(__file__).resolve().parents[1] / 'mcq_maker' / 'assets' / 'standard_exam.html').read_text(encoding='utf-8')
            events = []
            with patch('mcq_maker.ai_studio_batch.AIStudioConversationSession', FakeSession):
                result = asyncio.run(GoogleAIStudioBatchController(
                    manifest, browser_manager=FakeManager(), template=template,
                    template_entry={}, store=store,
                    event_callback=lambda lecture, status, detail: events.append((lecture, status, detail)),
                ).run())
            self.assertEqual(FakeSession.maximum, 2)
            self.assertEqual(FakeManager.shutdowns, 1)
            self.assertTrue(all(session.isolated_page and session.closed for session in FakeSession.instances))
            self.assertEqual([item.status for item in result.lectures], [BatchStatus.COMPLETED] * 3)
            self.assertEqual(len({item.worker_id for item in result.lectures}), 2)
            self.assertTrue(all(item.claimed_at and item.job_id and Path(item.output_path).is_file()
                                for item in result.lectures))
            self.assertTrue(all(session.calls.count('calibration') == 1 for session in FakeSession.instances))
            self.assertTrue(all(sum(isinstance(call, tuple) for call in session.calls) == 1
                                for session in FakeSession.instances))
            claimed = [detail for _lecture, status, detail in events if status == 'Job claimed']
            self.assertGreaterEqual(len(claimed), 2)
            self.assertIn('-tab-1', claimed[0])
            self.assertIn('-tab-2', claimed[1])
            calibration_workers = {
                detail.split(';', 1)[0] for _lecture, status, detail in events
                if status == 'AI Studio stage' and 'stage=calibration_submission_started' in detail
            }
            self.assertEqual(len(calibration_workers), 2)

    def test_two_worker_failure_isolated_and_pause_stops_the_next_claim(self):
        import asyncio
        from mcq_maker.ai_studio_batch import GoogleAIStudioBatchController
        from mcq_maker.ai_studio_errors import AIStudioValidationError
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ('a.pdf', 'b.pdf', 'c.pdf'):
                (root / name).write_bytes(b'lecture')
            prompt = root / 'prompt'; prompt.write_text('prompt')
            reference = root / 'reference'; reference.write_text('reference')
            pause = Event(); store = BatchStateStore(root / 'state')
            manifest = create_manifest(root, prompt, reference, 'm', 'High', root / 'out',
                                       store=store, max_workers=2)
            quiz = SimpleNamespace(title='Parallel', questions=[1],
                                   payload={'title': 'Parallel', 'questions': []})

            class FakeSession:
                started = 0
                def __init__(self, manager, *, isolated_page=False, quiz_validator=None):
                    self.diagnostic_callback = None
                async def discover_capabilities(self): pass
                async def configure_defaults(self, **kwargs): pass
                async def run_calibration(self, *_args): pass
                async def generate_lecture(self, path):
                    type(self).started += 1
                    while type(self).started < 2:
                        await asyncio.sleep(0)
                    if Path(path).name == 'a.pdf':
                        pause.set()
                        raise AIStudioValidationError('invalid quiz')
                    await asyncio.sleep(0.01)
                    return quiz
                async def close(self): pass
            class FakeManager:
                async def shutdown(self): pass

            template = (Path(__file__).resolve().parents[1] / 'mcq_maker' / 'assets' / 'standard_exam.html').read_text(encoding='utf-8')
            with patch('mcq_maker.ai_studio_batch.AIStudioConversationSession', FakeSession):
                result = asyncio.run(GoogleAIStudioBatchController(
                    manifest, browser_manager=FakeManager(), template=template,
                    template_entry={}, store=store, pause_event=pause,
                ).run())
            states = {item.file_name: item.status for item in result.lectures}
            self.assertEqual(states['a.pdf'], BatchStatus.ATTENTION)
            self.assertEqual(states['b.pdf'], BatchStatus.COMPLETED)
            self.assertEqual(states['c.pdf'], BatchStatus.PAUSED)
            self.assertEqual(result.control_state, 'paused')


if __name__ == '__main__':
    unittest.main()
