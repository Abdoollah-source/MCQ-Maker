"""Privacy-safe structural diagnostics for AI Studio GenerateContent requests.

This module is opt-in tooling. It never writes request bodies, prompt text,
file identifiers, cookies, authorization data, or WAA proof contents.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from typing import Any


_GENERATE_MARKER = "generatecontent"
_WAA_CREATE_MARKER = "waa/create"
_MIME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*/[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*$")
_ERROR_RE = re.compile(
    r"(?:\[\s*,\s*\[\s*(?P<code>\d+)\s*,\s*\"(?P<message>[^\"]*)\"\s*\]\s*\]"
    r"|(?P<json_code>\"code\"\s*:\s*(?P<json_code_value>\d+))[^{}]{0,160}?"
    r"\"message\"\s*:\s*\"(?P<json_message>[^\"]*)\")",
    re.I | re.S,
)


def _parse_body(body: Any) -> Any | None:
    """Parse JSON in memory only; return None for any unsupported body."""
    if isinstance(body, bytes):
        try:
            body = body.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if not isinstance(body, str):
        return None
    try:
        return json.loads(body)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "other"


def _non_null_bitmap(value: Any) -> str:
    if not isinstance(value, list):
        return ""
    return "".join("1" if item is not None else "0" for item in value)


def _shape(value: Any, depth: int = 0) -> Any:
    """Return shape only, with no scalar values retained."""
    if depth > 5:
        return _type_name(value)
    if isinstance(value, list):
        return {
            "type": "array",
            "length": len(value),
            "non_null_bitmap": _non_null_bitmap(value),
            "items": [_shape(item, depth + 1) for item in value[:32]],
        }
    if isinstance(value, dict):
        return {"type": "object", "keys": sorted(str(key) for key in value)[:64]}
    if isinstance(value, str):
        match = _MIME_RE.fullmatch(value)
        return {"type": "string", "mime_type": value} if match else "string"
    return _type_name(value)


def _part_layout(turns: Any) -> list[dict[str, Any]]:
    if not isinstance(turns, list):
        return []
    result = []
    for turn in turns[:64]:
        parts = turn[0] if isinstance(turn, list) and turn and isinstance(turn[0], list) else []
        layout = []
        for part in parts[:64]:
            if isinstance(part, list):
                fields = [index for index, item in enumerate(part) if item is not None]
                mime_types = []
                stack = list(part)
                while stack:
                    item = stack.pop()
                    if isinstance(item, str) and _MIME_RE.fullmatch(item):
                        mime_types.append(item)
                    elif isinstance(item, list):
                        stack.extend(item[:32])
                layout.append({
                    "field_positions": fields,
                    "mime_types": sorted(set(mime_types)),
                    "has_attachment_identifier": bool(len(fields) > 2 or (len(part) > 2 and part[2] is not None)),
                })
            else:
                layout.append({"field_positions": [], "mime_types": [], "has_attachment_identifier": False})
        result.append({"part_count": len(parts), "parts": layout})
    return result


def sanitize_generate_content_body(body: Any) -> dict[str, Any]:
    """Return a structural record without retaining any sensitive scalar data."""
    parsed = _parse_body(body)
    if not isinstance(parsed, list):
        return {
            "parseable_json_array": False,
            "body_type": _type_name(parsed) if parsed is not None else "unavailable_or_non_json",
        }

    proof = parsed[4] if len(parsed) > 4 else None
    model = parsed[0] if parsed and isinstance(parsed[0], str) else None
    model_value = model if model and model.startswith("models/") else None
    contents = parsed[1] if len(parsed) > 1 else None
    return {
        "parseable_json_array": True,
        "top_level_length": len(parsed),
        "top_level_non_null_bitmap": _non_null_bitmap(parsed),
        "top_level_types": [_type_name(item) for item in parsed],
        "model": model_value,
        "index_4": {
            "exists": len(parsed) > 4,
            "is_string": isinstance(proof, str),
            "begins_with_exclamation": isinstance(proof, str) and proof.startswith("!"),
            "length": len(proof) if isinstance(proof, str) else None,
        },
        "contents_turn_count": len(contents) if isinstance(contents, list) else 0,
        "turns": _part_layout(contents),
        "generation_config_shape": _shape(parsed[3]) if len(parsed) > 3 else None,
        "system_instruction_shape": _shape(parsed[5]) if len(parsed) > 5 else None,
        "system_instruction_present": len(parsed) > 5 and parsed[5] is not None,
        "tool_shape": _shape(parsed[6]) if len(parsed) > 6 else None,
        "tool_present": len(parsed) > 6 and parsed[6] is not None,
        "timezone_present": len(parsed) > 13 and parsed[13] is not None,
    }


def parse_grpc_error_body(body: Any) -> dict[str, Any]:
    """Extract only the generic numeric code and message from an error body."""
    if isinstance(body, bytes):
        body = body.decode("utf-8", errors="replace")
    if not isinstance(body, str):
        return {"grpc_code": None, "grpc_message": None}
    match = _ERROR_RE.search(body)
    if not match:
        return {"grpc_code": None, "grpc_message": None}
    code = match.group("code") or match.group("json_code_value")
    message = match.group("message") or match.group("json_message")
    return {"grpc_code": int(code) if code is not None else None, "grpc_message": message}


def method_name_from_url(url: str) -> str:
    value = str(url).split("?", 1)[0].rstrip("/")
    return value.rsplit("/", 1)[-1] or "unknown"


def redact_command_line(command_line: Any) -> str:
    value = str(command_line or "")
    value = re.sub(r"[A-Za-z]:\\[^\s\"]+", "<PATH>", value)
    value = re.sub(r"(--user-data-dir=)(?:\"[^\"]+\"|\S+)", r"\1<PATH>", value, flags=re.I)
    return value


@dataclass
class WaaDiagnosticRecorder:
    """Capture sanitized request/response metadata from a Playwright page."""

    page: Any
    browser_launch_mode: str
    attached: bool
    action: str
    browser_command_line: str = ""
    records: list[dict[str, Any]] = field(default_factory=list)
    waa_create_statuses: list[int] = field(default_factory=list)
    _handlers: list[tuple[str, Any]] = field(default_factory=list, init=False)
    _listener_source: Any = field(default=None, init=False, repr=False)
    navigator_webdriver: bool | None = None
    context_label: str = "context-1"
    page_labels: dict[int, str] = field(default_factory=dict, init=False)
    page_inventory: list[dict[str, Any]] = field(default_factory=list, init=False)
    selected_page_label: str | None = field(default=None, init=False)
    operation_pages: dict[str, str] = field(default_factory=dict, init=False)
    attempts: list[dict[str, Any]] = field(default_factory=list, init=False)
    _active_attempt: dict[str, Any] | None = field(default=None, init=False, repr=False)

    async def install_context(self, context: Any, *, read_webdriver: bool = True) -> list[dict[str, Any]]:
        """Install privacy-safe listeners at BrowserContext scope.

        Context listeners see requests from every page in the attached browser
        context, which avoids missing a generation request when AI Studio
        changes targets or opens a related page. Only origin/path metadata is
        returned for the page inventory; query parameters are discarded.
        """
        pages = list(getattr(context, "pages", []) or [])
        inventory = []
        for index, page in enumerate(pages, start=1):
            url = str(getattr(page, "url", "") or "")
            try:
                from urllib.parse import urlsplit
                parsed = urlsplit(url)
                location = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
            except Exception:
                location = ""
            label = self.register_page(page, location=location)
            inventory.append({"page": index, "location": location})
        if read_webdriver and self.page is not None:
            try:
                self.navigator_webdriver = await self.page.evaluate("() => Boolean(navigator.webdriver)")
            except Exception:
                self.navigator_webdriver = None
        self._install_handlers(context)
        self._handlers.append(("context", context))
        return inventory

    def register_page(self, page: Any, *, location: str | None = None) -> str:
        """Register a page with a local label and sanitized location only."""
        existing = self.page_labels.get(id(page))
        if existing is not None:
            return existing
        label = f"page-{len(self.page_labels) + 1}"
        self.page_labels[id(page)] = label
        if location is None:
            try:
                from urllib.parse import urlsplit
                parsed = urlsplit(str(getattr(page, "url", "") or ""))
                location = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
            except Exception:
                location = ""
        self.page_inventory.append({"page": label, "location": location})
        return label

    def _page_label(self, source: Any) -> str | None:
        page = source
        try:
            frame = getattr(source, "frame", None)
            if callable(frame):
                frame = frame()
            candidate = getattr(frame, "page", None) if frame is not None else None
            if callable(candidate):
                candidate = candidate()
            if candidate is not None:
                page = candidate
        except Exception:
            pass
        return self.page_labels.get(id(page))

    def set_selected_page(self, page: Any) -> str:
        label = self.register_page(page)
        self.selected_page_label = label
        return label

    def record_operation_page(self, operation: str, page: Any) -> None:
        self.operation_pages[operation] = self.set_selected_page(page)

    def begin_attempt(self, initiating_action: str) -> str:
        attempt = {
            "attempt": f"attempt-{len(self.attempts) + 1}",
            "initiating_action": initiating_action,
            "selected_page_label": self.selected_page_label,
            "request_page_label": None,
            "response_page_label": None,
            "http_status": None,
            "grpc_code": None,
            "new_assistant_response": False,
            "response_classification": None,
        }
        self.attempts.append(attempt)
        self._active_attempt = attempt
        return attempt["attempt"]

    def finish_attempt(self, *, new_assistant_response: bool, response_classification: str) -> None:
        if self._active_attempt is None:
            return
        self._active_attempt["new_assistant_response"] = bool(new_assistant_response)
        self._active_attempt["response_classification"] = response_classification
        self._active_attempt = None

    def _install_handlers(self, source: Any) -> None:
        self._listener_source = source
        def on_request(request: Any) -> None:
            url = str(getattr(request, "url", ""))
            lowered = url.casefold()
            if _GENERATE_MARKER not in lowered:
                return
            request_page_label = self._page_label(request)
            if self._active_attempt is not None:
                self._active_attempt["request_page_label"] = request_page_label
            post_data = getattr(request, "post_data", None)
            self.records.append({
                "timestamp": __import__("time").time(),
                "launch_mode": self.browser_launch_mode,
                "playwright_attached": self.attached,
                "action": self.action,
                "method": str(getattr(request, "method", "")),
                "method_name": method_name_from_url(url),
                "status": None,
                "request_page_label": request_page_label,
                "body_shape": sanitize_generate_content_body(post_data),
                "navigator_webdriver": self.navigator_webdriver,
                "browser_command_line": redact_command_line(self.browser_command_line),
            })

        async def on_response(response: Any) -> None:
            url = str(getattr(response, "url", ""))
            lowered = url.casefold()
            status = int(getattr(response, "status", 0) or 0)
            if _WAA_CREATE_MARKER in lowered:
                self.waa_create_statuses.append(status)
            if _GENERATE_MARKER not in lowered:
                return
            error = {"grpc_code": None, "grpc_message": None}
            if status == 403:
                try:
                    error = parse_grpc_error_body(await response.body())
                except Exception:
                    pass
            response_page_label = self._page_label(response)
            if self._active_attempt is not None:
                self._active_attempt["response_page_label"] = response_page_label
                self._active_attempt["http_status"] = status
                self._active_attempt["grpc_code"] = error["grpc_code"]
            self.records.append({
                "timestamp": __import__("time").time(),
                "method_name": method_name_from_url(url),
                "status": status,
                "response_page_label": response_page_label,
                **error,
            })

        source.on("request", on_request)
        source.on("response", on_response)
        self._handlers.extend([("request", on_request), ("response", on_response)])

    async def install(self) -> None:
        self.navigator_webdriver = await self.page.evaluate("() => Boolean(navigator.webdriver)")

        self._install_handlers(self.page)

    def uninstall(self) -> None:
        remove_listener = getattr(self._listener_source or self.page, "remove_listener", None)
        if not callable(remove_listener):
            return
        for event, handler in self._handlers:
            if event == "context":
                continue
            remove_listener(event, handler)
        self._handlers.clear()
        self._listener_source = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "navigator_webdriver": self.navigator_webdriver,
            "waa_create_statuses": list(self.waa_create_statuses),
            "context_label": self.context_label,
            "page_inventory": list(self.page_inventory),
            "selected_page_label": self.selected_page_label,
            "operation_pages": dict(self.operation_pages),
            "attempts": list(self.attempts),
            "records": list(self.records),
        }
