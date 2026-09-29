from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from mcq_maker.history import HistoryError, HistoryRepository
from mcq_maker.history_page import HistoryPage
from mcq_maker.settings import SettingsStore
from mcq_maker.shell import MainWindow
from mcq_maker.template_repository import TemplateRepository

BASE = Path(__file__).resolve().parents[1]
TEMPLATE = {
    'id': 'template-id', 'display_name': 'Standard exam', 'version': 1,
    'sha256': 'a' * 64,
}


class HistoryRepositoryTests(unittest.TestCase):
    def test_damaged_database_is_left_untouched_and_reported(self):
        with tempfile.TemporaryDirectory(dir=BASE/'artifacts') as root:
            history = HistoryRepository(Path(root))
            history.path.write_bytes(b'not a sqlite database')
            with self.assertRaises(HistoryError):
                history.initialize()
            self.assertEqual(history.path.read_bytes(), b'not a sqlite database')

    def test_schema_records_searches_relocates_exports_and_removes(self):
        with tempfile.TemporaryDirectory(dir=BASE/'artifacts') as root:
            base = Path(root)
            history = HistoryRepository(base)
            history.initialize()
            exam = base/'Cardiac_review_mcq.html'
            exam.write_text('<html></html>', encoding='utf-8')
            entry_id = history.record_success(
                title='Cardiac review', question_count=24, input_hash='b' * 64,
                output_path=exam, template_entry=TEMPLATE, source_mode='paste')
            history.record_failure(
                title='Renal review', question_count=10, input_hash='c' * 64,
                template_entry=TEMPLATE, source_mode='folder',
                source_path=base/'renal.json', error_summary='Question 3 needs four options.')

            entries = history.list_entries()
            self.assertEqual(len(entries), 2)
            success = next(entry for entry in entries if entry.id == entry_id)
            self.assertEqual(success.output_filename, exam.name)
            self.assertEqual(success.question_count, 24)
            self.assertEqual(len(history.list_entries('Cardiac')), 1)
            self.assertEqual(len(history.list_entries('Standard exam')), 2)
            self.assertEqual(history.list_entries('Renal')[0].outcome, 'failed')

            moved = base/'Moved exam.html'
            moved.write_text('<html></html>', encoding='utf-8')
            history.relocate(entry_id, moved)
            self.assertEqual(next(entry for entry in history.list_entries() if entry.id == entry_id).output_path, str(moved))

            exported = base/'history.csv'
            history.export_csv(exported)
            text = exported.read_text(encoding='utf-8-sig')
            self.assertIn('created_utc,title,output_filename', text)
            self.assertIn('Cardiac review', text)
            self.assertNotIn('<html>', text)
            self.assertTrue(history.remove(entry_id))
            self.assertFalse(history.remove(entry_id))

    def test_writes_are_serialized_across_threads(self):
        with tempfile.TemporaryDirectory(dir=BASE/'artifacts') as root:
            history = HistoryRepository(Path(root))
            history.initialize()
            def record(index):
                history.record_failure(template_entry=TEMPLATE, source_mode='folder',
                                       title=f'Quiz {index}', error_summary='Test failure')
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(record, range(20)))
            self.assertEqual(len(history.list_entries()), 20)
            self.assertEqual(len({entry.id for entry in history.list_entries()}), 20)


class HistoryPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_history_interactions_and_compact_rows(self):
        with tempfile.TemporaryDirectory(dir=BASE/'artifacts') as root:
            base = Path(root)
            history = HistoryRepository(base)
            history.initialize()
            exam = base/'Cardiac_mcq.html'
            exam.write_text('<html></html>', encoding='utf-8')
            success_id = history.record_success(
                title='Cardiac review', question_count=12, input_hash='d' * 64,
                output_path=exam, template_entry=TEMPLATE, source_mode='paste')
            missing_id = history.record_success(
                title='Missing review', question_count=8, input_hash='e' * 64,
                output_path=base/'missing.html', template_entry=TEMPLATE, source_mode='folder')
            history.record_failure(
                title='Failed review', template_entry=TEMPLATE, source_mode='folder',
                error_summary='The JSON is incomplete.')
            page = HistoryPage(history)
            page.show()
            QTest.qWait(50)
            try:
                self.assertEqual(page.table.topLevelItemCount(), 3)
                self.assertTrue(page.export_button.isEnabled())
                success_item = next(page.table.topLevelItem(i) for i in range(3)
                                    if page.table.topLevelItem(i).data(0, 0x0100).id == success_id)
                page.table.setCurrentItem(success_item)
                with patch('mcq_maker.history_page.QDesktopServices.openUrl', return_value=True) as opened:
                    page.open_selected()
                self.assertEqual(Path(opened.call_args.args[0].toLocalFile()), exam)

                page.set_compact(True)
                self.assertTrue(page.table.isColumnHidden(5))
                self.assertIn('Paste', success_item.text(0))
                page.set_compact(False)

                page.search.setText('Cardiac')
                QTest.qWait(250)
                self.assertEqual(page.table.topLevelItemCount(), 1)
                page.search.clear()
                QTest.qWait(250)

                export = base/'export.csv'
                with patch('mcq_maker.history_page.QFileDialog.getSaveFileName', return_value=(str(export), 'CSV')):
                    page.export_csv()
                self.assertTrue(export.is_file())

                missing_item = next(page.table.topLevelItem(i) for i in range(3)
                                    if page.table.topLevelItem(i).data(0, 0x0100).id == missing_id)
                page.table.setCurrentItem(missing_item)
                self.assertEqual(missing_item.text(5), 'File missing')
                self.assertIn('history record exists', missing_item.toolTip(5))
                self.assertFalse(page.open_button.isEnabled())
                self.assertTrue(page.show_button.isEnabled())
                self.assertEqual(page.show_button.text(), 'Locate output file')
                self.assertIn('new location', page.show_button.toolTip())
                relocated = base/'relocated.html'
                relocated.write_text('<html></html>', encoding='utf-8')
                with patch('mcq_maker.history_page.QFileDialog.getOpenFileName', return_value=(str(relocated), 'HTML')):
                    page.open_selected()
                self.assertEqual(next(entry for entry in history.list_entries() if entry.id == missing_id).output_path, str(relocated))

                relocated_item = next(page.table.topLevelItem(i) for i in range(3)
                                      if page.table.topLevelItem(i).data(0, 0x0100).id == missing_id)
                page.table.setCurrentItem(relocated_item)
                with patch('mcq_maker.history_page.QMessageBox.question', return_value=QMessageBox.Yes):
                    page.remove_selected()
                self.assertEqual(len(history.list_entries()), 2)
                self.assertTrue(relocated.is_file())
            finally:
                page.close()

    def test_history_uses_friendly_dates_and_a_helpful_empty_state(self):
        format_created = HistoryPage.local_created
        current = datetime.now().astimezone()
        self.assertTrue(format_created(current.isoformat(), current).startswith('Today,'))
        self.assertTrue(format_created((current - timedelta(days=1)).isoformat(), current).startswith('Yesterday,'))
        self.assertIn(str((current - timedelta(days=7)).year), format_created((current - timedelta(days=7)).isoformat(), current))
        with tempfile.TemporaryDirectory(dir=BASE/'artifacts') as root:
            history = HistoryRepository(Path(root))
            history.initialize()
            page = HistoryPage(history)
            try:
                self.assertFalse(page.empty.isHidden())
                self.assertEqual(page.empty_title.text(), 'No generated exams yet.')
                self.assertEqual(
                    page.empty_text.text(),
                    'Generated exams will appear here after validation and saving.',
                )
            finally:
                page.close()

    def test_main_window_updates_history_immediately_after_generation(self):
        with tempfile.TemporaryDirectory(dir=BASE/'artifacts') as root:
            base = Path(root)
            repository = TemplateRepository(base/'library')
            repository.initialize()
            history = HistoryRepository(repository.root)
            history.initialize()
            settings = SettingsStore(repository.root).defaults()
            settings['output_folder'] = str(base/'output')
            window = MainWindow(repository=repository, settings=settings, history=history)
            try:
                window.show()
                QTest.qWait(100)
                window.template_page.pool.waitForDone(3000)
                self.app.processEvents()
                text = '''[
                    {"title":"Immediate history"},
                    {"question":"Question?","options":["A","B","C","D"],"correct":0,"explanation":"A."}
                ]'''
                window.create.editor.setPlainText(text)
                QTest.qWait(320)
                self.assertTrue(window.create.generate.isEnabled())
                window.create.generate.click()
                self.app.processEvents()
                self.assertEqual(window.history_page.table.topLevelItemCount(), 1)
                self.assertIn('Immediate history', window.history_page.table.topLevelItem(0).text(0))
            finally:
                window.close()
                self.app.processEvents()


if __name__ == '__main__':
    unittest.main()
