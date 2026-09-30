import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtTest import QTest
from PySide6.QtCore import QMimeData, QUrl
from PySide6.QtWidgets import QApplication
from mcq_maker.exam_generator import filename_stem, inject_quiz_data, proposed_filename, save_exam
from mcq_maker.quiz_validation import QuizError, validate_quiz
from mcq_maker.shell import CreatePage, MainWindow
from mcq_maker.template_repository import TemplateRepository
from mcq_maker.history import HistoryRepository
from mcq_maker.localization import apply_language

BASE = Path(__file__).resolve().parents[1]
TEMPLATE = (BASE / 'mcq_maker' / 'assets' / 'standard_exam.html').read_text(encoding='utf-8')
QUIZ = json.dumps([
    {'title': 'Cardiology: α & β / Review'},
    {'question': 'What is first?', 'options': ['A', 'B', 'C', 'D'], 'correct': 0, 'explanation': 'A is first.'},
], ensure_ascii=False)


class QuizValidationTests(unittest.TestCase):
    def test_plain_fenced_and_prose_json(self):
        for text in (QUIZ, f'```json\n{QUIZ}\n```', f'Here is the quiz:\n{QUIZ}\nReview it.'):
            with self.subTest(text=text[:20]):
                result = validate_quiz(text)
                self.assertEqual(result.title, 'Cardiology: α & β / Review')
                self.assertEqual(len(result.questions), 1)

    def test_strict_json_and_input_errors(self):
        cases = [
            '[{"title":"x"},{"question":"q","question":"again","options":["A","B"],"correct":0,"explanation":"x"}]',
            '[{"title":"x"},{"question":"q","options":["A","B"],"correct":NaN,"explanation":"x"}]',
            '{"title":"not an array"}',
        ]
        for text in cases:
            with self.subTest(text=text), self.assertRaises(QuizError):
                validate_quiz(text)

    def test_all_structural_errors_are_explained(self):
        bad = json.dumps([{'title': '  '}, {'question': '', 'options': ['A'], 'correct': True, 'explanation': ''}])
        with self.assertRaises(QuizError) as captured:
            validate_quiz(bad)
        message = str(captured.exception)
        self.assertIn('Quiz title', message)
        self.assertIn('Question 1', message)
        self.assertIn('options', message)

    def test_template_option_limit_and_warnings(self):
        with self.assertRaises(QuizError):
            validate_quiz(QUIZ.replace('"C", "D"', '"C", "D", "E"'), 4, 4)
        exact = validate_quiz(QUIZ, 4, 4)
        self.assertEqual(len(exact.questions[0]['options']), 4)
        warnings = validate_quiz(json.dumps([
            {'title': 'x'},
            {'question': 'same', 'options': ['A', 'A'], 'correct': 0, 'explanation': 'x'},
            {'question': 'same', 'options': ['B', 'C'], 'correct': 1, 'explanation': 'x'},
        ])).warnings
        self.assertTrue(any('repeats' in warning for warning in warnings))


class GenerationTests(unittest.TestCase):
    def test_injection_is_safe_and_keeps_markers(self):
        quiz = validate_quiz(QUIZ.replace('What is first?', '</script> & <tag>'))
        output = inject_quiz_data(TEMPLATE, quiz)
        self.assertEqual(output.count('/* MCQ_MAKER_DATA_START */'), 1)
        self.assertEqual(output.count('/* MCQ_MAKER_DATA_END */'), 1)
        self.assertIn('\\/script\\u003e', output)
        self.assertIn('\\u0026', output)

    def test_windows_filename_rules_and_conflict_copies(self):
        self.assertEqual(filename_stem(' CON . '), 'exam_CON')
        self.assertEqual(filename_stem(' A / B:* '), 'A_B')
        with tempfile.TemporaryDirectory(dir=BASE/'artifacts') as root:
            folder = Path(root)
            quiz = validate_quiz(QUIZ)
            first = save_exam(TEMPLATE, quiz, folder)
            second = save_exam(TEMPLATE, quiz, folder)
            self.assertEqual(first.name, 'Cardiology_α_&_β_Review_mcq.html')
            self.assertEqual(second.name, 'Cardiology_α_&_β_Review_2_mcq.html')
            self.assertTrue(first.read_text(encoding='utf-8').startswith('<!doctype html>'))

    def test_explicit_replace_updates_only_the_base_filename(self):
        with tempfile.TemporaryDirectory(dir=BASE/'artifacts') as root:
            folder = Path(root)
            quiz = validate_quiz(QUIZ)
            original = save_exam(TEMPLATE, quiz, folder)
            replacement = save_exam(TEMPLATE.replace('MCQ Maker', 'MCQ Maker updated', 1), quiz, folder, overwrite=True)
            self.assertEqual(replacement, original)
            self.assertIn('MCQ Maker updated', original.read_text(encoding='utf-8'))

    def test_path_budget(self):
        folder = Path('C:/') / ('a' * 190)
        name = proposed_filename('x' * 100, folder)
        self.assertLessEqual(len(str(folder / name).encode('utf-16-le')) // 2, 240)


class CreatePageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        # Create-page copy is asserted in English by this test class.
        apply_language(self.app, 'en')

    def test_live_validation_generation_auto_clear_and_undo(self):
        with tempfile.TemporaryDirectory(dir=BASE/'artifacts') as root:
            repo = TemplateRepository(Path(root)/'library')
            repo.initialize()
            history = HistoryRepository(repo.root)
            history.initialize()
            entry = repo.list_templates()['templates'][0]
            page = CreatePage(repo, history)
            page.template.addItem(entry['display_name'], entry['id'])
            page.template.setCurrentIndex(1)
            page.template.setEnabled(True)
            page.set_template_entries([entry])
            page.output_folder = Path(root)/'output'
            page.editor.setPlainText(QUIZ)
            QTest.qWait(380)
            self.assertTrue(page.generate.isEnabled())
            self.assertEqual(page.detail_values['count'].text(), '1')
            page.generate_exam()
            self.assertEqual(page.editor.toPlainText(), '')
            self.assertEqual(len(list(page.output_folder.glob('*_mcq.html'))), 1)
            self.assertEqual(len(history.list_entries()), 1)
            self.assertEqual(history.list_entries()[0].source_mode, 'paste')
            QTest.qWait(380)
            self.assertIn('Saved', page.validation.text())
            page.editor.undo()
            self.assertEqual(page.editor.toPlainText(), QUIZ)
            page.close()

    def test_example_clear_undo_and_file_import(self):
        with tempfile.TemporaryDirectory(dir=BASE/'artifacts') as root:
            repo = TemplateRepository(Path(root)/'library')
            repo.initialize()
            entry = repo.list_templates()['templates'][0]
            page = CreatePage(repo)
            page.template.addItem(entry['display_name'], entry['id'])
            page.template.setCurrentIndex(1)
            page.template.setEnabled(True)
            page.set_template_entries([entry])

            page.load_example()
            QTest.qWait(380)
            self.assertIn('Sample clinical review', page.editor.toPlainText())
            self.assertTrue(page.generate.isEnabled())
            page.editor_clear()
            self.assertEqual(page.editor.toPlainText(), '')
            self.assertEqual(page.validation.text(), 'Paste questions to begin.')
            page.editor.undo()
            QTest.qWait(380)
            self.assertIn('Sample clinical review', page.editor.toPlainText())

            source = Path(root)/'imported.md'
            source.write_text(QUIZ, encoding='utf-8')
            with patch('mcq_maker.shell.QFileDialog.getOpenFileName', return_value=(str(source), '')):
                page.import_file()
            QTest.qWait(380)
            self.assertEqual(page.validation_result.title, 'Cardiology: α & β / Review')
            self.assertTrue(page.generate.isEnabled())
            page.close()

    def test_invalid_input_marks_editor_without_stealing_text(self):
        page = CreatePage()
        page.template_entries = {'template': {'id': 'template', 'minimum_options': 2, 'maximum_options': 10}}
        page.template.addItem('Template', 'template')
        page.template.setCurrentIndex(1)
        page.editor.setPlainText('[{"title":"Broken"}]')
        QTest.qWait(380)
        self.assertEqual(page.editor.property('validation'), 'error')
        self.assertEqual(page.editor.toPlainText(), '[{"title":"Broken"}]')
        page.close()

    def test_editor_accepts_one_supported_local_file_for_drop(self):
        page = CreatePage()
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(BASE / 'quiz.json'))])
        event = type('DropEvent', (), {'mimeData': lambda self: mime})()
        self.assertTrue(page.editor._supported_file(event))
        mime.setUrls([QUrl.fromLocalFile(str(BASE / 'quiz.csv'))])
        self.assertFalse(page.editor._supported_file(event))
        page.close()

    def test_main_window_connects_template_library_to_create_workflow(self):
        with tempfile.TemporaryDirectory(dir=BASE/'artifacts') as root:
            repo = TemplateRepository(Path(root)/'library')
            window = MainWindow(repository=repo)
            try:
                window.show()
                QTest.qWait(100)
                window.template_page.pool.waitForDone(2000)
                self.app.processEvents()
                self.assertTrue(window.create.template.isEnabled(), window.template_page.status.text())
                self.assertIsNotNone(window.create.template.currentData())
                self.assertIsNotNone(window.folder_scan.selected_template())
                window.create.editor.setPlainText(QUIZ)
                QTest.qWait(320)
                self.assertTrue(window.create.generate.isEnabled())
            finally:
                window.close()
                window.template_page.pool.waitForDone(2000)
                self.app.processEvents()

    def test_open_output_folder_creates_and_targets_the_current_folder(self):
        with tempfile.TemporaryDirectory(dir=BASE/'artifacts') as root:
            page = CreatePage()
            page.output_folder = Path(root)/'new output'
            with patch('mcq_maker.shell.QDesktopServices.openUrl', return_value=True) as opened:
                page.open_output_folder()
            self.assertTrue(page.output_folder.is_dir())
            self.assertEqual(Path(opened.call_args.args[0].toLocalFile()), page.output_folder)
            page.close()


if __name__ == '__main__':
    unittest.main()
