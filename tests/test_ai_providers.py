import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mcq_maker.ai_providers import (
    AuthenticationError, GoogleSession, ProviderError, RateLimitError,
    TemporaryProviderError, GOOGLE_INLINE_FILE_BYTES, _file_part, list_models,
)


class CapturingGoogleSession(GoogleSession):
    def __init__(self, responses):
        super().__init__('test-key', 'gemini-3.8-flash', 'high')
        self.calls = []
        self.responses = iter(responses)

    def _google_file_part(self, path):
        return {'inlineData': {'mimeType': 'text/plain', 'data': 'SAFE_TEST_DATA'}}

    def _call(self, contents, structured=False):
        self.calls.append((contents, structured, self.system_prompt))
        response = next(self.responses)
        return ({'role': 'model', 'parts': [{'text': response}]}, response)


class RemoteFileSession(CapturingGoogleSession):
    def __init__(self):
        super().__init__(['CALIBRATION COMPLETE', '[{"title":"T"}]', '[{"title":"T2"}]'])
        self.cleaned = 0

    def _google_file_part(self, path):
        name = f'files/{Path(path).stem}'
        self._uploaded_files.append(name)
        return {'fileData': {'mimeType': 'application/pdf', 'fileUri': f'https://files.invalid/{name}'}}

    def _delete_uploaded_files(self):
        self.cleaned += len(self._uploaded_files)
        self._uploaded_files.clear()


class ProviderContractTests(unittest.TestCase):
    def test_google_model_catalog_prefers_gemini_38_flash(self):
        body = {'models': [
            {'name': 'models/gemini-2.5-flash', 'displayName': 'Gemini 2.5 Flash', 'supportedGenerationMethods': ['generateContent']},
            {'name': 'models/gemini-3.8-flash', 'displayName': 'Gemini 3.8 Flash', 'supportedGenerationMethods': ['generateContent'], 'thinking': True},
        ]}
        with patch('mcq_maker.ai_providers.request_json', return_value=(body, {})):
            models = list_models('google', 'test-key')
        self.assertEqual(models[0][0], 'gemini-3.8-flash')

    def test_calibration_contains_system_instruction_reference_and_exact_confirmation(self):
        session = CapturingGoogleSession(['CALIBRATION COMPLETE'])
        result = session.calibrate('SYSTEM PROMPT', Path('reference.md'))
        self.assertEqual(result, 'CALIBRATION COMPLETE')
        contents, structured, system = session.calls[0]
        self.assertEqual(system, 'SYSTEM PROMPT')
        self.assertFalse(structured)
        self.assertEqual(contents[0]['role'], 'user')
        self.assertEqual(len(contents[0]['parts']), 2)
        self.assertIn('CALIBRATION COMPLETE', contents[0]['parts'][1]['text'])

    def test_calibration_rejects_surrounding_or_wrong_text(self):
        session = CapturingGoogleSession(['Ready — CALIBRATION COMPLETE'])
        with self.assertRaises(ProviderError):
            session.calibrate('SYSTEM', Path('reference.md'))

    def test_lecture_turn_preserves_history_and_contains_pdf_only(self):
        session = CapturingGoogleSession(['CALIBRATION COMPLETE', '[{"title":"T"}]'])
        session.calibrate('SYSTEM', Path('reference.md'))
        session.generate(Path('lecture.pdf'))
        contents, structured, _ = session.calls[1]
        self.assertTrue(structured)
        self.assertEqual(len(contents), 3)
        new_user = contents[-1]
        self.assertEqual(new_user['role'], 'user')
        self.assertEqual(len(new_user['parts']), 1)
        self.assertNotIn('text', new_user['parts'][0])

    def test_remote_files_live_until_conversation_group_closes(self):
        session = RemoteFileSession()
        session.calibrate('SYSTEM', Path('reference.md'))
        self.assertEqual(len(session._uploaded_files), 1)
        session.generate(Path('lecture-1.pdf'))
        self.assertEqual(len(session._uploaded_files), 2)
        session.generate(Path('lecture-2.pdf'))
        self.assertEqual(len(session._uploaded_files), 3)
        session.close()
        self.assertEqual(session.cleaned, 3)
        self.assertEqual(session._uploaded_files, [])

    def test_high_maps_to_api_thinking_level(self):
        session = GoogleSession('test-key', 'gemini-3.8-flash', 'high')
        captured = {}

        def call(contents, *, key, payload=None, headers=None, method=None, timeout=180):
            captured['payload'] = payload
            return ({'candidates': [{'content': {'parts': [{'text': 'ok'}]}}]}, {})

        with patch('mcq_maker.ai_providers.request_json', side_effect=call):
            session._call([{'role': 'user', 'parts': [{'text': 'x'}]}], structured=True)
        config = captured['payload']['generationConfig']
        self.assertEqual(config['thinkingConfig']['thinkingLevel'], 'HIGH')
        self.assertEqual(config['responseMimeType'], 'application/json')

    def test_thought_parts_are_excluded(self):
        body = {'candidates': [{'content': {'parts': [
            {'thought': True, 'text': 'private reasoning'},
            {'text': '[{"title":"T"}]'},
        ]}}]}
        with patch('mcq_maker.ai_providers.request_json', return_value=(body, {})):
            _content, text = GoogleSession('key', 'gemini-3.8-flash', 'high')._call([], True)
        self.assertEqual(text, '[{"title":"T"}]')

    def test_small_file_is_inline(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'small.md'; path.write_text('safe', encoding='utf-8')
            part = _file_part(path, True)
        self.assertIn('inlineData', part)

    def test_large_file_uses_files_api_and_cleans_up(self):
        class Response:
            def __init__(self, data): self.data, self.headers = data, {}
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return json.dumps(self.data).encode()

        responses = [
            Response({'file': {'name': 'files/test', 'uri': 'https://upload.invalid/file', 'state': 'PROCESSING'}}),
            Response({'file': {'name': 'files/test', 'uri': 'https://upload.invalid/file', 'state': 'ACTIVE'}}),
            Response({}),
        ]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'large.pdf'; path.write_bytes(b'x' * (GOOGLE_INLINE_FILE_BYTES + 1))
            session = GoogleSession('key', 'gemini-3.8-flash', 'high')
            with patch('mcq_maker.ai_providers.urlopen', side_effect=responses), patch('mcq_maker.ai_providers.time.sleep'):
                part = session._google_file_part(path)
                session._delete_uploaded_files()
        self.assertIn('fileData', part)

    def test_error_classes_remain_specific(self):
        self.assertTrue(issubclass(AuthenticationError, ProviderError))
        self.assertTrue(issubclass(RateLimitError, ProviderError))
        self.assertTrue(issubclass(TemporaryProviderError, ProviderError))


if __name__ == '__main__':
    unittest.main()
