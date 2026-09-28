import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PySide6.QtWidgets import QApplication
from mcq_maker.template_repository import TemplateRepository, atomic_write, write_json
from mcq_maker.template_validation import TemplateError, validate_template, decode_template, render_preview, START, END
from mcq_maker.template_page import TemplatePage

BASE = Path(__file__).resolve().parents[1]
HTML = (BASE / 'mcq_maker/assets/standard_exam.html').read_text(encoding='utf-8')
(BASE / 'artifacts').mkdir(exist_ok=True)

class ValidationTests(unittest.TestCase):
    def test_bundled_contract_and_bom(self):
        result = validate_template(decode_template(b'\xef\xbb\xbf' + HTML.encode()))
        self.assertEqual((result.version, result.minimum_options, result.maximum_options), (1, 2, 10))

    def test_missing_duplicate_reversed_markers(self):
        cases = [HTML.replace(START, ''), HTML + START,
                 HTML.replace(START, 'TEMP').replace(END, START).replace('TEMP', END)]
        for text in cases:
            with self.subTest(text=text[:30]), self.assertRaises(TemplateError):
                validate_template(text)

    def test_markers_must_share_executable_inline_script(self):
        for text in (HTML.replace(START, '</script>'+START+'<script>'),
                     HTML.replace('<script>', '<script type="application/json">'),
                     HTML.replace(END, '</script><script>'+END)):
            with self.subTest(text=text[:20]), self.assertRaises(TemplateError):
                validate_template(text)

    def test_strict_placeholder(self):
        for content in ('const quizData = {};', 'const quizData = []; alert(1);',
                        'const quizData = [NaN];', 'const quizData = [{"a":1,"a":2}];',
                        'let quizData = [];', 'const quizData = [1,];'):
            with self.subTest(content=content), self.assertRaises(TemplateError):
                validate_template(HTML.replace('const quizData = [];', content))

    def test_encoding_and_size(self):
        for data in (b'\xff\xfe', b'x' * (5*1024*1024+1)):
            with self.assertRaises(TemplateError):
                decode_template(data)

    def test_windows_line_endings_are_normalized(self):
        self.assertNotIn('\r', decode_template(HTML.replace('\n', '\r\n').encode('utf-8')))

    def test_metadata_and_document(self):
        changes = [('<meta charset="UTF-8">', ''), ('</html>', ''),
                   ('name="mcq-maker-template-version" content="1"', 'name="mcq-maker-template-version" content="2"'),
                   ('name="mcq-maker-min-options" content="2"', 'name="mcq-maker-min-options" content="11"')]
        for old, new in changes:
            with self.subTest(old=old), self.assertRaises(TemplateError):
                validate_template(HTML.replace(old, new))

    def test_external_resources_become_line_numbered_warnings(self):
        linked = HTML.replace('</head>', '\n<link rel="stylesheet" href="local.css">\n<style>body { background: url(images/paper.png); }</style>\n</head>')
        warnings = validate_template(linked).warnings
        self.assertEqual([warning.line for warning in warnings], [
            linked.splitlines().index('<link rel="stylesheet" href="local.css">') + 1,
            linked.splitlines().index('<style>body { background: url(images/paper.png); }</style>') + 1,
        ])
        self.assertEqual([warning.reference for warning in warnings], ['local.css', 'images/paper.png'])

    def test_embedded_resources_do_not_warn(self):
        embedded = HTML.replace('</head>', '<img src="data:image/png;base64,AA=="><style>body { background: url(#paper); }</style></head>')
        self.assertFalse(validate_template(embedded).warnings)

    def test_safe_preview_and_option_range(self):
        output = render_preview(HTML)
        self.assertEqual(output.count(START), 1)
        self.assertEqual(output.count(END), 1)
        payload = output.split(START)[1].split(END)[0].strip().removeprefix('const quizData = ').removesuffix(';')
        parsed = json.loads(payload)
        self.assertEqual([len(q['options']) for q in parsed[1:]], [2, 10])
        self.assertNotIn('<script>', payload)
        self.assertIn('اختبار', parsed[2]['question'])

class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=BASE/'artifacts')
        self.root = Path(self.temp.name)
        self.repo = TemplateRepository(self.root/'library')
        self.repo.initialize()
        self.source = self.root/'source.html'
        self.source.write_text(HTML, encoding='utf-8')

    def tearDown(self):
        self.temp.cleanup()

    def test_first_run_and_restart(self):
        before = self.repo.list_templates()
        self.repo.initialize()
        after = TemplateRepository(self.repo.root).list_templates()
        self.assertEqual(before['default_id'], after['default_id'])
        self.assertEqual(len(after['templates']), 1)

    def test_import_is_independent_and_unicode_name(self):
        template_id = self.repo.import_template(self.source, 'اختبار القلب: Part / 1')
        self.source.unlink()
        self.assertEqual(self.repo.read_template(template_id), HTML)
        entry = self.repo._metadata(template_id)
        self.assertEqual(entry['display_name'], 'اختبار القلب: Part / 1')
        self.assertTrue((self.repo.templates/template_id/'template.html').exists())

    def test_bad_import_does_not_change_library_or_source(self):
        invalid = HTML.replace(END, '')
        self.source.write_text(invalid, encoding='utf-8')
        before = self.repo.list_templates()
        with self.assertRaises(TemplateError):
            self.repo.import_template(self.source)
        self.assertEqual(self.repo.list_templates(), before)
        self.assertEqual(self.source.read_text(encoding='utf-8'), invalid)

    def test_names_and_physical_identity(self):
        template_id = self.repo.import_template(self.source, 'Custom')
        self.repo.rename(template_id, 'New display name')
        self.assertTrue((self.repo.templates/template_id/'template.html').exists())
        for name in ('', '   ', 'Standard exam', 'x'*101, 'line\nbreak'):
            with self.subTest(name=name), self.assertRaises(TemplateError):
                self.repo.rename(template_id, name)

    def test_default_remove_and_empty_library(self):
        new_id = self.repo.import_template(self.source, 'Custom')
        self.repo.set_default(new_id)
        self.assertEqual(TemplateRepository(self.repo.root).list_templates()['default_id'], new_id)
        self.repo.remove(new_id)
        self.assertNotEqual(self.repo.list_templates()['default_id'], new_id)
        self.assertTrue(self.source.exists())
        remaining = self.repo.list_templates()['templates'][0]['id']
        self.repo.remove(remaining)
        self.repo.initialize()
        self.assertEqual(self.repo.list_templates()['templates'], [])
        self.assertIsNone(self.repo.list_templates()['default_id'])

    def test_invalid_replacement_keeps_old_revision(self):
        template_id = self.repo.import_template(self.source, 'Custom')
        self.source.write_text('not html', encoding='utf-8')
        with self.assertRaises(TemplateError):
            self.repo.replace(template_id, self.source)
        self.assertEqual(self.repo.read_template(template_id), HTML)
        self.assertEqual(self.repo._metadata(template_id)['revision'], 1)

    def test_source_change_and_reimport(self):
        template_id = self.repo.import_template(self.source, 'Custom')
        changed = HTML + '\n<!-- revision two -->'
        self.source.write_text(changed, encoding='utf-8')
        entry = next(t for t in self.repo.list_templates()['templates'] if t['id'] == template_id)
        self.assertTrue(entry['source_changed'])
        self.repo.replace(template_id, self.source)
        self.assertEqual(self.repo.read_template(template_id), changed)
        self.assertEqual(self.repo._metadata(template_id)['revision'], 2)
        self.assertEqual(len(list((self.repo.templates/template_id/'revisions').glob('*.html'))), 1)

    def test_link_warnings_can_be_acknowledged_and_rechecked(self):
        linked = HTML.replace('</head>', '<link rel="stylesheet" href="local.css">\n</head>')
        self.source.write_text(linked, encoding='utf-8')
        template_id = self.repo.import_template(self.source, 'Linked template')
        entry = next(t for t in self.repo.list_templates()['templates'] if t['id'] == template_id)
        self.assertTrue(entry['valid'])
        self.assertEqual(entry['warnings'][0]['line'], linked.splitlines().index('<link rel="stylesheet" href="local.css">') + 1)
        self.assertFalse(entry['warnings_acknowledged'])
        self.repo.acknowledge_warnings(template_id)
        self.assertTrue(self.repo._metadata(template_id)['warnings_acknowledged'])
        self.source.write_text(HTML, encoding='utf-8')
        self.repo.recheck(template_id)
        refreshed = self.repo._metadata(template_id)
        self.assertEqual(refreshed['warnings'], [])
        self.assertFalse(refreshed['warnings_acknowledged'])
        self.assertEqual(refreshed['revision'], 2)

    def test_interrupted_update_recovers_old_version(self):
        template_id = self.repo.import_template(self.source, 'Custom')
        folder = self.repo.templates/template_id
        old = self.repo._metadata(template_id)
        backup_id = 'a'*32
        atomic_write(folder/'revisions'/f'{backup_id}.html', HTML.encode())
        write_json(folder/'update.json', {'backup_id': backup_id, 'old': old, 'new': dict(old, sha256='not-committed')})
        atomic_write(folder/'template.html', b'interrupted')
        self.assertEqual(self.repo.read_template(template_id), HTML)
        self.assertFalse((folder/'update.json').exists())

    def test_tampering_and_path_escape(self):
        template_id = self.repo.import_template(self.source, 'Custom')
        (self.repo.templates/template_id/'template.html').write_text('tampered', encoding='utf-8')
        with self.assertRaises(TemplateError):
            self.repo.set_default(template_id)
        for value in ('../elsewhere', '/absolute', '', '123'):
            with self.subTest(value=value), self.assertRaises(TemplateError):
                self.repo.remove(value)

    def test_preview_is_separate_from_stored_template(self):
        template_id = self.repo.import_template(self.source, 'Custom')
        preview = self.repo.preview(template_id)
        self.assertEqual(preview.parent, self.repo.root/'previews')
        self.assertIn('Template preview', preview.read_text(encoding='utf-8'))
        self.assertEqual(self.repo.read_template(template_id), HTML)

class TemplatePageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_warning_is_kept_in_an_expandable_template_row(self):
        with tempfile.TemporaryDirectory(dir=BASE/'artifacts') as root:
            repo = TemplateRepository(Path(root)/'library')
            repo.initialize()
            page = TemplatePage(repo)
            snapshot = repo.list_templates()
            entry = snapshot['templates'][0]
            entry = dict(entry, warnings=[{'line': 23, 'reference': 'theme.css',
                                           'message': 'The linked stylesheet may not work after copying.'}],
                         warnings_acknowledged=False)
            page.populate({'templates': [entry], 'default_id': entry['id']})
            row = page.tree.topLevelItem(0)
            self.assertFalse(row.isExpanded())
            self.assertFalse(row.icon(0).isNull())
            self.assertEqual(row.childCount(), 1)
            detail = page.tree.itemWidget(row.child(0), 0)
            self.assertIn('Line 23: theme.css', detail.findChildren(type(page.status))[-1].text())
            page.toggle_details(row, 0)
            self.assertTrue(row.isExpanded())
            page.close()

if __name__ == '__main__':
    unittest.main()
