import tempfile
import unittest
from pathlib import Path

from mcq_maker.ai_library import AILibrary, unprotect_secret


BASE = Path(__file__).resolve().parents[1]


class AILibraryTests(unittest.TestCase):
    def test_secure_keys_prompt_versions_and_copied_references_persist(self):
        with tempfile.TemporaryDirectory(dir=BASE/'artifacts') as temporary:
            root = Path(temporary)
            library = AILibrary(root)
            data = library.initialize()
            self.assertEqual(len(data['prompts']), 1)
            key_id = library.add_key('google', 'Personal', 'secret-value')
            saved = next(x for x in library.load()['keys'] if x['id'] == key_id)
            self.assertNotIn('secret-value', library.path.read_text(encoding='utf-8'))
            self.assertEqual(unprotect_secret(saved['secret']), 'secret-value')
            second_prompt = library.add_prompt('Medical MCQ generation', 'Second version')
            versions = [x['version'] for x in library.load()['prompts']]
            self.assertEqual(sorted(versions), [1, 2])
            library.remove_prompt(second_prompt)
            prompt_data = library.load()
            self.assertEqual(len(prompt_data['prompts']), 1)
            self.assertEqual(prompt_data['active_prompt_id'], prompt_data['prompts'][0]['id'])
            library.set_models('google', [('gemini-test', 'Gemini Test', True)])
            self.assertEqual(library.load()['models']['google'][0]['id'], 'gemini-test')
            source = root/'reference.pdf'; source.write_bytes(b'%PDF-test')
            reference_id = library.add_reference(source, 'Anesthesia', 'Finals', 'Year 5', 'Core reference')
            source.unlink()
            self.assertTrue(library.reference_path(reference_id).is_file())
            library.remove_key(key_id)
            self.assertEqual(library.load()['keys'], [])
            library.path.write_text('{damaged', encoding='utf-8')
            recovered = library.load()
            self.assertEqual(recovered['version'], 1)


if __name__ == '__main__':
    unittest.main()
