import json
from pathlib import Path
from threading import Event
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from mcq_maker.folder_scan import FolderScanWorker, discover_files, process_quiz_file
from mcq_maker.folder_scan_page import FolderScanPage
from mcq_maker.components import AnimatedProgressBar
from mcq_maker.settings import SettingsStore
from mcq_maker.template_repository import TemplateRepository
from mcq_maker.history import HistoryRepository

BASE = Path(__file__).resolve().parents[1]


def quiz(title):
    return json.dumps([
        {'title': title},
        {'question': 'Which answer is correct?', 'options': ['A', 'B', 'C', 'D'],
         'correct': 1, 'explanation': 'B is correct.'},
    ], ensure_ascii=False)


class FolderScanEngineTests(unittest.TestCase):
    def test_discovery_is_supported_deterministic_and_optionally_recursive(self):
        with tempfile.TemporaryDirectory(dir=BASE/'artifacts') as root:
            folder = Path(root)
            (folder/'B.txt').write_text(quiz('B'), encoding='utf-8')
            (folder/'a.JSON').write_text(quiz('A'), encoding='utf-8')
            (folder/'ignored.csv').write_text('ignored', encoding='utf-8')
            nested = folder/'nested'
            nested.mkdir()
            (nested/'c.md').write_text(quiz('C'), encoding='utf-8')
            self.assertEqual([path.name for path in discover_files(folder)], ['a.JSON', 'B.txt'])
            self.assertEqual(
                [str(path.relative_to(folder)) for path in discover_files(folder, True)],
                ['a.JSON', 'B.txt', str(Path('nested')/'c.md')],
            )

    def test_files_use_shared_validation_and_numbered_saving(self):
        with tempfile.TemporaryDirectory(dir=BASE/'artifacts') as root:
            base = Path(root)
            repository = TemplateRepository(base/'library')
            repository.initialize()
            entry = repository.list_templates()['templates'][0]
            template = repository.read_template(entry['id'])
            source = base/'quiz.md'
            source.write_text(f'```json\n{quiz("Renal review")}\n```', encoding='utf-8')
            first = process_quiz_file(source, base, template, entry, base/'output')
            second = process_quiz_file(source, base, template, entry, base/'output')
            self.assertEqual(first.status, 'created')
            self.assertEqual(first.question_count, 1)
            self.assertEqual(first.output_path.name, 'Renal_review_mcq.html')
            self.assertEqual(second.output_path.name, 'Renal_review_2_mcq.html')

            broken = base/'broken.txt'
            broken.write_bytes(b'\xff\xfe not utf-8')
            failure = process_quiz_file(broken, base, template, entry, base/'output')
            self.assertEqual(failure.status, 'failed')
            self.assertIn('UTF-8', failure.reason)

    def test_pre_cancelled_worker_marks_every_supported_file_skipped(self):
        with tempfile.TemporaryDirectory(dir=BASE/'artifacts') as root:
            base = Path(root)
            repository = TemplateRepository(base/'library')
            repository.initialize()
            entry = repository.list_templates()['templates'][0]
            (base/'one.json').write_text(quiz('One'), encoding='utf-8')
            (base/'two.json').write_text(quiz('Two'), encoding='utf-8')
            cancellation = Event()
            cancellation.set()
            worker = FolderScanWorker(base, False, repository.read_template(entry['id']),
                                      entry, base/'output', cancellation)
            delivered = []
            finished = []
            worker.signals.progress.connect(lambda result, *_: delivered.append(result))
            worker.signals.finished.connect(lambda results, cancelled, error: finished.append((results, cancelled, error)))
            worker.run()
            self.assertEqual([result.status for result in delivered], ['skipped', 'skipped'])
            self.assertTrue(finished[0][1])
            self.assertFalse((base/'output').exists())


class FolderScanPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_page_processes_files_reports_reasons_and_exposes_actions(self):
        with tempfile.TemporaryDirectory(dir=BASE/'artifacts') as root:
            base = Path(root)
            source = base/'input'
            source.mkdir()
            (source/'valid.json').write_text(quiz('Cardiac review'), encoding='utf-8')
            (source/'wrapped.md').write_text(f'Quiz follows:\n{quiz("Pulmonary review")}', encoding='utf-8')
            (source/'invalid.txt').write_text('{not valid JSON', encoding='utf-8')
            nested = source/'nested'
            nested.mkdir()
            (nested/'not-included.json').write_text(quiz('Nested'), encoding='utf-8')

            repository = TemplateRepository(base/'library')
            repository.initialize()
            history = HistoryRepository(repository.root)
            history.initialize()
            store = SettingsStore(repository.root)
            settings = store.defaults()
            settings['output_folder'] = str(base/'output')
            page = FolderScanPage(repository, settings, history)
            notifications = []
            page.notification_requested.connect(lambda *args: notifications.append(args))
            page.set_template_snapshot(repository.list_templates())
            with patch('mcq_maker.folder_scan_page.QFileDialog.getExistingDirectory', return_value=str(source)):
                page.choose_folder()
            self.assertTrue(page.scan_button.isEnabled())
            page.start_scan()
            page.pool.waitForDone(5000)
            self.app.processEvents()

            self.assertFalse(page.busy)
            self.assertEqual(page.counts(), (2, 0, 1))
            self.assertEqual(page.report.topLevelItemCount(), 3)
            self.assertEqual(len(list((base/'output').glob('*_mcq.html'))), 2)
            self.assertEqual(len(history.list_entries()), 3)
            self.assertEqual({entry.outcome for entry in history.list_entries()}, {'success', 'failed'})
            self.assertEqual(notifications, [(
                'batch_complete', 'Folder scan complete',
                '2 created · 0 skipped · 1 failed', 'folder_report')])
            failed = next(page.report.topLevelItem(i) for i in range(3)
                          if 'Failed' in page.report.topLevelItem(i).text(0))
            self.assertTrue(failed.isExpanded())
            self.assertIn('JSON', failed.child(0).text(0))

            page.copy_report()
            copied = QApplication.clipboard().text()
            self.assertIn('2 created, 0 skipped, 1 failed', copied)
            self.assertNotIn('Which answer is correct?', copied)
            changed = dict(settings, output_folder=str(base/'different output'))
            page.apply_settings(changed)
            with patch('mcq_maker.folder_scan_page.QDesktopServices.openUrl', return_value=True) as opened:
                page.open_output_folder()
            self.assertEqual(Path(opened.call_args.args[0].toLocalFile()), base/'output')
            page.close()

    def test_progress_animates_only_reported_work(self):
        progress = AnimatedProgressBar()
        progress.setRange(0, 10)
        progress.setValue(0)
        progress.set_reported_value(6)
        self.assertEqual(progress.animation.duration(), 100)
        QTest.qWait(130)
        self.assertEqual(progress.value(), 6)


if __name__ == '__main__':
    unittest.main()
