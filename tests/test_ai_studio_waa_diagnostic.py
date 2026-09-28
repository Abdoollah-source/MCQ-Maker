import asyncio
import json
import unittest

from tools.ai_studio_waa_diagnostic import (
    WaaDiagnosticRecorder,
    method_name_from_url,
    parse_grpc_error_body,
    redact_command_line,
    sanitize_generate_content_body,
)
from experiments.ai_studio_playwright.human_assisted import classify_calibration_response


class DiagnosticSanitizerTests(unittest.TestCase):
    def test_calibration_classifier_distinguishes_exact_and_expanded_readiness(self):
        self.assertEqual(classify_calibration_response('CALIBRATION COMPLETE'), 'calibration_complete_exact')
        self.assertEqual(
            classify_calibration_response('Calibration complete. The model is ready for your lecture.'),
            'calibration_ready_expanded',
        )

    def test_calibration_classifier_rejects_prompt_like_incomplete_text(self):
        self.assertEqual(classify_calibration_response('Calibration incomplete; continue setup.'), 'other')
        self.assertEqual(classify_calibration_response('Please reply CALIBRATION COMPLETE'), 'other')

    def test_calibration_classifier_rejects_errors_even_if_they_contain_confirmation(self):
        self.assertEqual(
            classify_calibration_response('CALIBRATION COMPLETE, but an internal error has occurred.'),
            'internal_error',
        )

    def test_calibration_classifier_rejects_unrelated_long_response(self):
        self.assertEqual(classify_calibration_response('The model is ready. ' * 100), 'other')
    def test_sanitizes_sparse_body_without_retaining_sensitive_scalars(self):
        body = [
            "models/gemini-test",
            [[[[None, "PROMPT SECRET"]], "user"]],
            None,
            [None, 1, None, "generation"],
            "!PRIVATE_WAA_PROOF",
            [[[None, "SYSTEM SECRET"]], "user"],
            [[[None, "TOOL SECRET"]]],
            None,
            None,
            None,
            None,
            None,
            None,
            [[None, None, "Asia/Baghdad"]],
            None,
        ]
        sanitized = sanitize_generate_content_body(json.dumps(body))
        rendered = repr(sanitized)
        self.assertTrue(sanitized["parseable_json_array"])
        self.assertEqual(sanitized["model"], "models/gemini-test")
        self.assertTrue(sanitized["index_4"]["is_string"])
        self.assertTrue(sanitized["index_4"]["begins_with_exclamation"])
        self.assertEqual(sanitized["index_4"]["length"], len("!PRIVATE_WAA_PROOF"))
        for secret in ("PROMPT SECRET", "SYSTEM SECRET", "TOOL SECRET", "PRIVATE_WAA_PROOF"):
            self.assertNotIn(secret, rendered)

    def test_proof_hash_or_value_is_never_retained(self):
        sanitized = sanitize_generate_content_body(json.dumps(["models/x", [], None, None, "!proof-value"]))
        self.assertEqual(set(sanitized["index_4"]), {"exists", "is_string", "begins_with_exclamation", "length"})
        self.assertNotIn("hash", repr(sanitized).casefold())
        self.assertNotIn("proof-value", repr(sanitized))

    def test_attachment_and_mime_are_structural_only(self):
        body = ["models/x", [[[[None, None, ["application/pdf", "FILE-ID-SECRET"]]], "user"]]]
        sanitized = sanitize_generate_content_body(json.dumps(body))
        self.assertEqual(sanitized["turns"][0]["part_count"], 1)
        self.assertIn("application/pdf", repr(sanitized))
        self.assertNotIn("FILE-ID-SECRET", repr(sanitized))

    def test_handles_empty_and_non_json_bodies(self):
        for body in (None, "", b"not-json", {}, 7):
            sanitized = sanitize_generate_content_body(body)
            self.assertFalse(sanitized["parseable_json_array"])
            self.assertNotIn("not-json", repr(sanitized))

    def test_handles_sparse_and_nested_shapes(self):
        sanitized = sanitize_generate_content_body(json.dumps([[None, [None, "x"]], None, [None, None, 1]]))
        self.assertTrue(sanitized["parseable_json_array"])
        self.assertEqual(sanitized["top_level_non_null_bitmap"], "101")
        self.assertEqual(sanitized["top_level_types"], ["array", "null", "array"])

    def test_parses_protobuf_json_permission_error_only(self):
        result = parse_grpc_error_body('[,[7,"The caller does not have permission"]]')
        self.assertEqual(result, {"grpc_code": 7, "grpc_message": "The caller does not have permission"})

    def test_parses_json_error_shape(self):
        result = parse_grpc_error_body('{"error":{"code":7,"message":"permission denied"}}')
        self.assertEqual(result["grpc_code"], 7)
        self.assertEqual(result["grpc_message"], "permission denied")

    def test_non_error_response_has_no_error_details(self):
        self.assertEqual(parse_grpc_error_body('{"candidates":[]}'), {"grpc_code": None, "grpc_message": None})

    def test_url_reduces_to_method_name(self):
        self.assertEqual(method_name_from_url("https://host/$rpc/MakerSuiteService/GenerateContent?x=1"), "GenerateContent")

    def test_command_line_redacts_profile_paths(self):
        redacted = redact_command_line(r'brave.exe --user-data-dir="C:\Users\Alice\profile" --remote-debugging-port=1234')
        self.assertNotIn("Alice", redacted)
        self.assertIn("<PATH>", redacted)


class RecorderTests(unittest.IsolatedAsyncioTestCase):
    class FakeRequest:
        method = "POST"
        url = "https://host/$rpc/MakerSuiteService/GenerateContent"
        post_data = json.dumps(["models/x", [], None, None, "!secret-proof"])

    class FakeResponse:
        url = "https://host/$rpc/MakerSuiteService/GenerateContent"
        status = 403

        async def body(self):
            return b'[,[7,"The caller does not have permission"]]'

    class FakePage:
        def __init__(self):
            self.handlers = {}

        async def evaluate(self, _expression):
            return True

        def on(self, event, handler):
            self.handlers[event] = handler

        def remove_listener(self, event, handler):
            if self.handlers.get(event) is handler:
                del self.handlers[event]

    class FakeContext(FakePage):
        def __init__(self, page):
            super().__init__()
            self.pages = [page]

    async def test_recorder_keeps_only_sanitized_request_and_error_metadata(self):
        page = self.FakePage()
        recorder = WaaDiagnosticRecorder(page, "persistent", False, "automated", r"--user-data-dir=C:\Users\Secret\profile")
        await recorder.install()
        page.handlers["request"](self.FakeRequest())
        await page.handlers["response"](self.FakeResponse())
        snapshot = recorder.snapshot()
        rendered = repr(snapshot)
        self.assertEqual(snapshot["navigator_webdriver"], True)
        self.assertEqual(snapshot["records"][1]["grpc_code"], 7)
        self.assertNotIn("secret-proof", rendered)
        self.assertNotIn("Secret", rendered)
        recorder.uninstall()
        self.assertEqual(page.handlers, {})

    async def test_context_installation_captures_all_pages_and_uninstalls(self):
        page = self.FakePage()
        page.url = "https://aistudio.google.com/u/0/prompts/new_chat?private=redact"
        context = self.FakeContext(page)
        recorder = WaaDiagnosticRecorder(page, "cdp", True, "manual")
        inventory = await recorder.install_context(context, read_webdriver=False)
        self.assertEqual(inventory, [{"page": 1, "location": "https://aistudio.google.com/u/0/prompts/new_chat"}])
        context.handlers["request"](self.FakeRequest())
        self.assertEqual(len(recorder.records), 1)
        recorder.uninstall()
        self.assertEqual(context.handlers, {})

    async def test_correlation_metadata_uses_local_labels_only(self):
        page = self.FakePage()
        page.url = "https://aistudio.google.com/u/0/prompts/new_chat?private=redact"
        context = self.FakeContext(page)
        recorder = WaaDiagnosticRecorder(page, "cdp", True, "manual")
        await recorder.install_context(context, read_webdriver=False)
        recorder.set_selected_page(page)
        recorder.record_operation_page('send', page)
        recorder.begin_attempt('automated Send')
        recorder.finish_attempt(new_assistant_response=True, response_classification='calibration complete')
        snapshot = recorder.snapshot()
        self.assertEqual(snapshot['context_label'], 'context-1')
        self.assertEqual(snapshot['page_inventory'][0]['page'], 'page-1')
        self.assertEqual(snapshot['selected_page_label'], 'page-1')
        self.assertEqual(snapshot['operation_pages'], {'send': 'page-1'})
        self.assertEqual(snapshot['attempts'][0]['attempt'], 'attempt-1')
        self.assertEqual(snapshot['attempts'][0]['initiating_action'], 'automated Send')
        self.assertTrue(snapshot['attempts'][0]['new_assistant_response'])
        self.assertEqual(snapshot['attempts'][0]['response_classification'], 'calibration complete')
        self.assertNotIn('private=redact', repr(snapshot))


if __name__ == "__main__":
    unittest.main()
