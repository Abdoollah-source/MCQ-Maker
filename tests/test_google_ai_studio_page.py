from pathlib import Path
import tempfile
import unittest
from threading import Event
import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import Qt, QRunnable

from mcq_maker.google_ai_studio_page import (GoogleAIStudioSemiAutomationPage,
                                             GoogleAIStudioSignals, GoogleAIStudioWorker)
from mcq_maker.ai_studio_batch import BatchStateStore, create_manifest
from mcq_maker.settings import SettingsStore
from mcq_maker.template_repository import TemplateRepository


BASE = Path(__file__).resolve().parents[1]


class GoogleAIStudioPageTests(unittest.TestCase):
    def test_worker_pool_diagnostics_are_logged_without_becoming_fake_job_rows(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lectures = root / 'lectures'; lectures.mkdir()
            prompt = root / 'prompt.txt'; prompt.write_text('prompt')
            reference = root / 'reference.txt'; reference.write_text('reference')
            worker = GoogleAIStudioWorker(
                prompt_path=prompt, reference_path=reference,
                lecture_folder=lectures, output_folder=root / 'output',
                model='Gemini 3.8 Flash', thinking='High', brave_executable=None,
                template='', template_entry={}, history=None,
            )
            job_rows = []
            log_events = []
            worker.signals.job_updated.connect(lambda *args: job_rows.append(args))
            worker.signals.event.connect(log_events.append)
            worker._batch_event('Worker Pool', 'Worker started', 'worker_id=worker-test')
            self.assertEqual(job_rows, [])
            self.assertTrue(any('worker_started' in event and 'worker-test' in event for event in log_events))

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_page_uses_the_default_template_and_existing_ai_studio_preferences(self):
        with tempfile.TemporaryDirectory(dir=BASE / 'artifacts') as temporary:
            root = Path(temporary)
            repository = TemplateRepository(root / 'data')
            repository.initialize()
            settings = SettingsStore(repository.root).defaults()
            settings['ai_studio_model'] = 'Gemini 3.8 Flash'
            settings['ai_studio_thinking'] = 'High'
            settings['ai_studio_parallel_tabs'] = 2
            page = GoogleAIStudioSemiAutomationPage(repository, settings, batch_store=BatchStateStore(root / 'batches'))
            self.assertTrue(page.template.currentData())
            self.assertEqual(page.model.currentData(), 'Gemini 3.8 Flash')
            self.assertEqual(page.thinking.currentData(), 'High')
            self.assertEqual(page.parallel_tabs.currentData(), 2)
            self.assertEqual(page.parallel_tabs.itemData(1), 2)
            self.assertTrue(page.scroll.widgetResizable())
            self.assertEqual(page.scroll.horizontalScrollBarPolicy(), Qt.ScrollBarAlwaysOff)
            self.assertGreaterEqual(page.prompt_path.parentWidget().minimumHeight(), 38)
            self.assertEqual(page.run_button.text(), 'Start semi-automation')
            self.assertEqual(page.job_table.columnCount(), 4)
            self.assertEqual(page._friendly_event('[sending_calibration]'), 'Sending calibration…')
            self.assertIn('manual Rerun', page._friendly_event(
                '[manual_rerun_required:1:Google AI Studio displayed Permission denied.]'
            ))
            self.assertEqual(
                page._friendly_event('[completed]: Lecture-A.pdf C:/exams/Lecture-A.html (2048 bytes)'),
                'Lecture Completed; HTML saved and verified: Lecture-A.pdf C:/exams/Lecture-A.html (2048 bytes)',
            )
            page.resize(800, 850)
            page.show()
            self.app.processEvents()
            page.status.setText("Could not open Google AI Studio's Thinking Level control.")
            page.status.setProperty('feedback', 'error')
            page._refresh_status_style()
            self.app.processEvents()
            self.assertLessEqual(page.status.x(), 25)
            self.assertGreaterEqual(page.status.width(), page.activity_panel.width() - 45)
            self.assertGreaterEqual(page.status.contentsMargins().left(), 16)
            page.close()

    def test_run_selection_is_persisted_and_passed_to_the_worker(self):
        with tempfile.TemporaryDirectory(dir=BASE / 'artifacts') as temporary:
            root = Path(temporary)
            repository = TemplateRepository(root / 'data')
            repository.initialize()
            lectures = root / 'lectures'; lectures.mkdir()
            (lectures / 'one.pdf').write_bytes(b'lecture')
            prompt = root / 'prompt.txt'; prompt.write_text('prompt')
            reference = root / 'reference.txt'; reference.write_text('reference')
            output = root / 'output'
            settings = SettingsStore(repository.root).defaults()
            page = GoogleAIStudioSemiAutomationPage(
                repository, settings, batch_store=BatchStateStore(root / 'batches'),
            )
            page.prompt_path.setText(str(prompt))
            page.reference_path.setText(str(reference))
            page.lecture_path.setText(str(lectures))
            page.output_folder.setText(str(output))
            page.parallel_tabs.setCurrentIndex(page.parallel_tabs.findData(2))
            saved_preferences = []
            page.preferences_changed.connect(lambda *values: saved_preferences.append(values))

            class FakeWorker(QRunnable):
                created = None

                def __init__(self, **kwargs):
                    super().__init__()
                    type(self).created = kwargs
                    self.signals = GoogleAIStudioSignals()

                def run(self):
                    pass

            with patch('mcq_maker.google_ai_studio_page.GoogleAIStudioWorker', FakeWorker):
                page.start_run()
            self.assertEqual(saved_preferences, [('Gemini 3.8 Flash', 'High', 2)])
            self.assertEqual(FakeWorker.created['max_workers'], 2)
            self.assertEqual(page.parallel_tabs.currentData(), 2)
            page._set_busy(False)
            page.close()

    def test_worker_runs_the_durable_controller_with_mocked_browser_pipeline(self):
        with tempfile.TemporaryDirectory(dir=BASE / 'artifacts') as temporary:
            root = Path(temporary)
            (root / 'lectures').mkdir()
            (root / 'lectures' / 'one.pdf').write_bytes(b'lecture')
            (root / 'lectures' / 'two.pdf').write_bytes(b'lecture')
            prompt = root / 'prompt.txt'; prompt.write_text('prompt')
            reference = root / 'reference.txt'; reference.write_text('reference')
            store = BatchStateStore(root / 'batches')
            template = (BASE / 'mcq_maker' / 'assets' / 'standard_exam.html').read_text(encoding='utf-8')
            quiz = SimpleNamespace(title='Queue Demo', questions=[1], payload={'title': 'Queue Demo', 'questions': []})
            restored_one_worker_manifest = create_manifest(
                root / 'lectures', prompt, reference, 'Gemini 3.8 Flash', 'High', root / 'output',
                store=store, template_id='default', template=template, max_workers=1,
            )

            class FakeManager:
                async def shutdown(self): pass

            class FakeSession:
                active = maximum = 0
                def __init__(self, manager, *, quiz_validator=None, isolated_page=False): pass
                async def discover_capabilities(self): pass
                async def configure_defaults(self, **kwargs): pass
                async def run_calibration(self, *args): pass
                async def generate_lecture(self, path):
                    type(self).active += 1
                    type(self).maximum = max(type(self).maximum, type(self).active)
                    await asyncio.sleep(0)
                    type(self).active -= 1
                    return quiz
                async def close(self): pass

            worker = GoogleAIStudioWorker(
                prompt_path=prompt, reference_path=reference,
                lecture_folder=root / 'lectures', output_folder=root / 'output',
                model='Gemini 3.8 Flash', thinking='High', brave_executable=None,
                template=template, template_entry={'id': 'default'}, history=None,
                manifest=restored_one_worker_manifest, batch_store=store, max_workers=2,
            )
            with patch('mcq_maker.google_ai_studio_page.AIStudioBrowserManager', return_value=FakeManager()), \
                 patch('mcq_maker.ai_studio_batch.AIStudioConversationSession', FakeSession):
                output, count = asyncio.run(worker._run())
            saved = store.unfinished()
            self.assertFalse(saved)
            manifests = list(store.root.glob('*.json'))
            self.assertEqual(len(manifests), 1)
            manifest = store.load(manifests[0].stem)
            self.assertEqual(manifest.max_workers, 2)
            self.assertTrue(all(item.status == 'Completed' for item in manifest.lectures))
            self.assertTrue(all(item.question_count == 1 for item in manifest.lectures))
            self.assertTrue(output.is_file())
            self.assertEqual(count, 1)
            self.assertEqual(FakeSession.maximum, 2)

    def test_page_restores_saved_queue_settings_and_status_rows(self):
        with tempfile.TemporaryDirectory(dir=BASE / 'artifacts') as temporary:
            root = Path(temporary)
            repository = TemplateRepository(root / 'data')
            repository.initialize()
            snapshot = repository.list_templates()
            entry = next(item for item in snapshot['templates'] if item.get('valid'))
            lecture_folder = root / 'lectures'; lecture_folder.mkdir()
            (lecture_folder / 'A.pdf').write_bytes(b'lecture')
            prompt = root / 'prompt.txt'; prompt.write_text('prompt')
            reference = root / 'reference.txt'; reference.write_text('reference')
            output = root / 'output'
            template = repository.read_template(entry['id'])
            store = BatchStateStore(root / 'batches')
            manifest = create_manifest(
                lecture_folder, prompt, reference, 'Gemini 3.8 Flash', 'High', output,
                store=store, template_id=entry['id'], template=template,
            )
            manifest.lectures[0].status = 'Waiting to retry'
            manifest.lectures[0].retry_count = 1
            manifest.lectures[0].next_retry_at = '2026-09-26T17:00:00+00:00'
            store.save(manifest)
            settings = SettingsStore(repository.root).defaults()
            page = GoogleAIStudioSemiAutomationPage(repository, settings, batch_store=store)
            self.assertEqual(page.run_button.text(), 'Continue saved batch')
            self.assertEqual(page.lecture_path.text(), str(lecture_folder))
            self.assertEqual(page.job_table.topLevelItemCount(), 1)
            self.assertEqual(page.job_table.topLevelItem(0).text(1), 'Waiting to retry')
            self.assertIn('Next retry', page.job_table.topLevelItem(0).text(3))
            page.close()

    def test_paused_batch_controls_and_confirmed_discard_keep_exam_outputs(self):
        with tempfile.TemporaryDirectory(dir=BASE / 'artifacts') as temporary:
            root = Path(temporary)
            repository = TemplateRepository(root / 'data')
            repository.initialize()
            entry = next(item for item in repository.list_templates()['templates'] if item.get('valid'))
            lecture_folder = root / 'lectures'; lecture_folder.mkdir()
            (lecture_folder / 'A.pdf').write_bytes(b'lecture')
            prompt = root / 'prompt.txt'; prompt.write_text('prompt')
            reference = root / 'reference.txt'; reference.write_text('reference')
            store = BatchStateStore(root / 'batches')
            manifest = create_manifest(
                lecture_folder, prompt, reference, 'Gemini 3.8 Flash', 'High', root / 'output',
                store=store, template_id=entry['id'], template=repository.read_template(entry['id']),
            )
            manifest.control_state = 'paused'
            manifest.lectures[0].status = 'Paused'
            output = root / 'output' / 'saved.html'; output.parent.mkdir(); output.write_text('<html>saved</html>')
            manifest.lectures[0].output_path = str(output)
            store.save(manifest)

            page = GoogleAIStudioSemiAutomationPage(
                repository, SettingsStore(repository.root).defaults(), batch_store=store,
            )
            self.assertEqual(page.run_button.text(), 'Resume saved batch')
            self.assertFalse(page.pause_button.isEnabled())
            self.assertFalse(page.discard_button.isHidden())
            with patch('mcq_maker.google_ai_studio_page.QMessageBox.question', return_value=QMessageBox.Yes) as confirm:
                page.discard_saved_batch()
            confirm.assert_called_once()
            self.assertIn('Generated HTML exams', confirm.call_args.args[2])
            self.assertFalse(store.path_for(manifest.batch_id).exists())
            self.assertTrue(output.is_file())
            self.assertEqual(page.run_button.text(), 'Start semi-automation')
            page.close()

    def test_active_buttons_reflect_safe_pause_and_stop_actions(self):
        with tempfile.TemporaryDirectory(dir=BASE / 'artifacts') as temporary:
            repository = TemplateRepository(Path(temporary) / 'data')
            repository.initialize()
            page = GoogleAIStudioSemiAutomationPage(repository, SettingsStore(repository.root).defaults(),
                                                    batch_store=BatchStateStore(Path(temporary) / 'batches'))
            fake_worker = SimpleNamespace(paused=Event(), cancelled=Event(), cancel=lambda: None)
            page.worker = fake_worker
            page._set_busy(True)
            self.assertTrue(page.pause_button.isEnabled())
            self.assertFalse(page.discard_button.isEnabled())
            page.toggle_pause()
            self.assertEqual(page.pause_button.text(), 'Resume now')
            self.assertIn('Pausing after current', page.status.text())
            page.stop_run()
            self.assertEqual(page.run_button.text(), 'Stopping safely…')
            self.assertFalse(page.run_button.isEnabled())
            page.worker = None
            page.close()

    def test_saved_batch_action_reviews_attention_without_calling_it_resume(self):
        manifest = SimpleNamespace(
            control_state='stopped',
            lectures=[SimpleNamespace(status='Interrupted')],
        )
        self.assertEqual(GoogleAIStudioSemiAutomationPage._saved_batch_action(manifest), 'Review saved batch')
        manifest.lectures[0].status = 'Paused'
        self.assertEqual(GoogleAIStudioSemiAutomationPage._saved_batch_action(manifest), 'Resume saved batch')
        manifest.control_state = 'running'
        manifest.lectures[0].status = 'Waiting to retry'
        self.assertEqual(GoogleAIStudioSemiAutomationPage._saved_batch_action(manifest), 'Continue saved batch')


if __name__ == '__main__':
    unittest.main()
