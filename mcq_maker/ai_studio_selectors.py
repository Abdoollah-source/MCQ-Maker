"""The single, narrow DOM boundary for Google AI Studio Phase 3 controls."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import re
import random
import time

from .ai_studio_errors import (AIStudioConnectionError, AIStudioControlNotFoundError, AIStudioGenerationTimeoutError,
                               AIStudioCancelledError,
                               AIStudioInitializationError, AIStudioInitializationRejectedError,
                               AIStudioInitializationTimeoutError, AIStudioResponseError, AIStudioUploadError)


def normalize_option(value):
    return ' '.join(str(value or '').split())


def first_line_option(value):
    return normalize_option(str(value or '').splitlines()[0] if str(value or '').splitlines() else '')


TOKEN_COUNT_PATTERN = re.compile(r'\b\d[\d,]*(?:\.\d+)?\s*tokens?\b', re.I)
ASSISTANT_META_HEADER = re.compile(r'^model\s*[·•]\s*\d{1,2}:\d{2}\s*(?:am|pm)?$', re.I)
ASSISTANT_META_HEADER = re.compile(
    r'^model\b.*\b\d{1,2}:\d{2}\s*(?:am|pm)$', re.I
)
ASSISTANT_META_HEADER_PREFIX = re.compile(
    r'^model\b.*?\b\d{1,2}:\d{2}\s*(?:am|pm)\s*', re.I
)
AI_STUDIO_DISCLAIMER = re.compile(r'^Google AI models may make mistakes\b.*$', re.I)
TRANSIENT_GENERATION_ERROR_PATTERN = re.compile(
    r'(?:failed to generate content|permission denied|an internal error has occurred)', re.I
)
AI_STUDIO_INITIALIZATION_FRAGMENTS = (
    'GenerateAccessToken',
    'ListModels',
    'GetUserPreferences',
)
CALIBRATION_PROTOCOL_MARKERS = (
    'internal qa',
    'batching / token limit protocol',
    'final interaction (after all lecture files',
)


def has_token_count_text(value):
    return bool(TOKEN_COUNT_PATTERN.search(normalize_option(value)))


def has_transient_generation_error_text(value):
    """Recognize the small set of Google AI Studio errors safe to retry."""
    return bool(TRANSIENT_GENERATION_ERROR_PATTERN.search(normalize_option(value)))


def normalize_assistant_response_text(value):
    """Remove visible AI Studio turn chrome without changing generated content."""
    lines = [line.strip() for line in str(value or '').splitlines()]
    if lines:
        # Some rendered layouts flatten the metadata and first response line
        # together; strip only the leading model/time header in that case.
        lines[0] = ASSISTANT_META_HEADER_PREFIX.sub('', lines[0], count=1).strip()
    while lines and (not lines[0] or ASSISTANT_META_HEADER.fullmatch(lines[0])):
        lines.pop(0)
    disclaimer_index = next((index for index, line in enumerate(lines)
                             if AI_STUDIO_DISCLAIMER.fullmatch(line)), None)
    if disclaimer_index is not None:
        lines = lines[:disclaimer_index]
    return normalize_option('\n'.join(lines))


def is_generated_response_text(value):
    """Reject empty, error, and copied protocol text from a response candidate."""
    text = normalize_option(value).casefold()
    return bool(text) and not has_transient_generation_error_text(text) and not any(
        marker in text for marker in CALIBRATION_PROTOCOL_MARKERS
    )


def normalize_options(rows, allowed_roles=('option', 'menuitemradio', 'radio')):
    """Keep visible selectable labels in UI order without making catalog assumptions."""
    values, seen = [], set()
    for row in rows:
        if row.get('disabled') or row.get('role') not in allowed_roles:
            continue
        value = normalize_option(row.get('text'))
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            values.append(value)
    return tuple(values)


@dataclass(frozen=True)
class ThinkingDiscovery:
    supported: bool
    current: str | None = None
    options: tuple[str, ...] | None = ()


class AttachmentReady(str, Enum):
    TOKEN_COUNT = 'token_count'
    GENERATION_STARTED = 'generation_started'
    TOKEN_TIMEOUT = 'token_timeout'


class AIStudioLocators:
    """Targeted Playwright locators; workflow code must not contain page selectors."""

    readiness_timeout = 45.0
    poll_interval = 0.2
    run_settings_ready_timeout = 20.0
    model_selector_timeout = 15.0
    model_selector_stable_for = 0.5
    attachment_timeout = 10.0
    generation_start_timeout = 90.0
    run_return_timeout = 120.0
    final_answer_timeout = 120.0
    generation_timeout = 300.0
    lecture_generation_timeout = 600.0
    generation_poll_interval = 0.5
    navigation_timeout = 120.0
    initialization_timeout = 180.0

    def __init__(self, page, *, clock=None, sleep=None):
        self.page = page
        self._clock = clock or time.monotonic
        self._sleep = sleep or asyncio.sleep
        self._run_settings_open = False
        self.performance_callback = None
        self.last_initialization_diagnostics = None

    async def _first_visible(self, candidates):
        for locator in candidates:
            count = await locator.count()
            for index in range(count):
                candidate = locator.nth(index)
                if await candidate.is_visible():
                    return candidate
        return None

    async def _first_usable(self, candidates):
        for locator in candidates:
            count = await locator.count()
            for index in range(count):
                candidate = locator.nth(index)
                if await candidate.is_visible() and await candidate.is_enabled():
                    return candidate
        return None

    async def _wait_for_any_visible(self, candidates, timeout):
        """Wait on browser visibility events for a set of candidate locators."""
        waiters = []
        for locator in candidates:
            wait_for = getattr(locator, 'wait_for', None)
            if callable(wait_for):
                waiters.append(asyncio.create_task(wait_for(state='visible', timeout=int(timeout * 1000))))
        if not waiters:
            return None
        pending = set(waiters)
        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                if not task.cancelled() and task.exception() is None:
                    for other in pending:
                        other.cancel()
                    return await self._first_visible(candidates)
        return None

    async def _wait_for_all_hidden(self, candidates, timeout):
        waiters = []
        for locator in candidates:
            wait_for = getattr(locator, 'wait_for', None)
            if callable(wait_for):
                waiters.append(asyncio.create_task(wait_for(state='hidden', timeout=int(timeout * 1000))))
        if not waiters:
            return False
        results = await asyncio.gather(*waiters, return_exceptions=True)
        return all(not isinstance(result, Exception) for result in results)

    async def _human_pause(self):
        await asyncio.sleep(random.uniform(1.0, 1.5))

    def _prompt_candidates(self):
        return (
            self.page.get_by_role('textbox', name=re.compile(r'(prompt|message|ask)', re.I)),
            self.page.locator("textarea[aria-label]"),
            self.page.locator("[contenteditable='true'][role='textbox']"),
            self.page.locator('textarea'),
            self.page.locator("[contenteditable='true']"),
        )

    async def wait_for_prompt_input(self, cancellation=None):
        if cancellation is not None and cancellation.is_set():
            return None
        await self._wait_for_any_visible(self._prompt_candidates(), self.readiness_timeout)
        return await self._first_usable(self._prompt_candidates())

    async def wait_for_workspace_load(self):
        """Wait only for document readiness; navigation already waits for DOMContentLoaded."""
        wait = getattr(self.page, 'wait_for_load_state', None)
        if not callable(wait):
            return
        try:
            await wait('domcontentloaded', timeout=int(self.navigation_timeout * 1000))
        except Exception as exc:
            if getattr(self.page, 'is_closed', lambda: False)():
                raise AIStudioConnectionError('Google AI Studio browser page closed while loading.') from exc
            raise AIStudioConnectionError(
                'Google AI Studio did not reach the document-ready state in time.'
            ) from exc

    def arm_initialization_listener(self):
        """Start observing required startup responses before navigation begins."""
        required_names = {fragment.casefold(): fragment for fragment in AI_STUDIO_INITIALIZATION_FRAGMENTS}
        required = set(required_names)
        loop = asyncio.get_running_loop()
        state = {
            'required': required,
            'required_names': required_names,
            'observed': set(),
            'failures': {},
            'complete': loop.create_future(),
            'failed': loop.create_future(),
            'closed': loop.create_future(),
            'started': loop.time(),
        }

        def numeric_rpc_code(response):
            """Read only documented numeric status headers; never response bodies."""
            try:
                headers = response.headers
                if callable(headers):
                    headers = headers()
                if not isinstance(headers, dict):
                    return None
                for key in ('grpc-status', 'x-google-rpc-code'):
                    value = headers.get(key) or headers.get(key.title())
                    if value is not None and str(value).strip().isdigit():
                        return int(str(value).strip())
            except Exception:
                pass
            return None

        def diagnostics():
            entries = []
            for fragment in AI_STUDIO_INITIALIZATION_FRAGMENTS:
                key = fragment.casefold()
                name = required_names[key]
                result = state['results'][key]
                if key in state['observed']:
                    completion = 'succeeded'
                elif key in state['failures']:
                    completion = 'rejected'
                else:
                    completion = 'pending'
                entries.append({
                    'request': name,
                    'completion': completion,
                    'http_status': result['http_status'],
                    'rpc_status': result['rpc_status'],
                })
            return tuple(entries)

        state['results'] = {
            key: {'http_status': None, 'rpc_status': None} for key in required
        }
        state['diagnostics'] = diagnostics
        self.last_initialization_diagnostics = None

        def on_response(response):
            url = str(getattr(response, 'url', '')).casefold()
            status = getattr(response, 'status', None)
            if callable(status):
                status = status()
            for fragment in required:
                if fragment not in url:
                    continue
                if status is not None and 300 <= status < 400:
                    # Redirect responses are not the RPC's terminal result.
                    continue
                result = state['results'][fragment]
                result['http_status'] = status
                result['rpc_status'] = numeric_rpc_code(response)
                if status is not None and (200 <= status < 300 or status == 304):
                    if fragment not in state['observed']:
                        state['observed'].add(fragment)
                        if callable(self.performance_callback):
                            self.performance_callback(
                                f'initialization_{fragment}_ready', loop.time() - state['started']
                            )
                else:
                    state['failures'][fragment] = status
                    if not state['failed'].done():
                        state['failed'].set_result(True)
            if state['observed'] == required and not state['complete'].done():
                state['complete'].set_result(True)

        def on_close(*_args):
            if not state['closed'].done():
                state['closed'].set_result(True)

        state['response_handler'] = on_response
        state['close_handler'] = on_close
        self.page.on('response', on_response)
        self.page.on('close', on_close)
        return state

    def disarm_initialization_listener(self, state):
        if state is None:
            return
        self.page.remove_listener('response', state['response_handler'])
        self.page.remove_listener('close', state['close_handler'])
        for key in ('complete', 'failed', 'closed'):
            future = state[key]
            if not future.done():
                future.cancel()

    async def _wait_for_cancellation(self, cancellation):
        if isinstance(cancellation, asyncio.Event):
            await cancellation.wait()
            return
        wait = getattr(cancellation, 'wait', None)
        if callable(wait):
            while not cancellation.is_set():
                if await asyncio.to_thread(wait, 0.1):
                    return
            return
        while not cancellation.is_set():
            await asyncio.sleep(0.1)

    async def wait_for_initialization(self, timeout=None, cancellation=None, *, listener=None):
        """Await required startup responses, including responses captured during navigation."""
        owned_listener = listener is None
        state = listener or self.arm_initialization_listener()
        cancel_task = None
        try:
            if cancellation is not None and cancellation.is_set():
                raise AIStudioCancelledError('Waiting for Google AI Studio initialization was cancelled.')
            if state['observed'] == state['required']:
                self.last_initialization_diagnostics = state['diagnostics']()
                return True
            wait_seconds = self.initialization_timeout if timeout is None else timeout
            waiters = {state['complete'], state['failed'], state['closed']}
            if cancellation is not None:
                cancel_task = asyncio.create_task(self._wait_for_cancellation(cancellation))
                waiters.add(cancel_task)
            try:
                done, _pending = await asyncio.wait(
                    waiters, timeout=wait_seconds, return_when=asyncio.FIRST_COMPLETED
                )
            except Exception:
                done = set()
            if state['complete'] in done or state['observed'] == state['required']:
                self.last_initialization_diagnostics = state['diagnostics']()
                return True
            if cancel_task is not None and cancel_task in done:
                raise AIStudioCancelledError('Waiting for Google AI Studio initialization was cancelled.')
            if state['closed'] in done:
                raise AIStudioConnectionError('Google AI Studio browser page closed during initialization.')
            if state['failures']:
                details = state['diagnostics']()
                summary = ', '.join(
                    f"{item['request']}=HTTP {item['http_status']}"
                    + (f"/RPC {item['rpc_status']}" if item['rpc_status'] is not None else '')
                    for item in details if item['completion'] == 'rejected'
                )
                raise AIStudioInitializationRejectedError(
                    f'Google AI Studio rejected required initialization request(s): {summary}.',
                    details,
                )
            progress = len(state['observed'])
            details = state['diagnostics']()
            if progress:
                raise AIStudioInitializationTimeoutError(
                    f'Google AI Studio initialization timed out after partial progress ({progress} of {len(state["required"])} required responses).',
                    details,
                )
            raise AIStudioInitializationTimeoutError(
                'Google AI Studio initialization timed out without observing any required startup responses.',
                details,
            )
        finally:
            if cancel_task is not None and not cancel_task.done():
                cancel_task.cancel()
                try:
                    await cancel_task
                except asyncio.CancelledError:
                    pass
            if owned_listener:
                self.disarm_initialization_listener(state)

    def _landing_heading(self):
        return self.page.get_by_text('Explore Google models', exact=True)

    async def landing_screen_visible(self):
        return await self._first_visible((self._landing_heading(),)) is not None

    async def wait_for_landing_screen(self):
        heading = self._landing_heading()
        if hasattr(heading, 'wait_for'):
            try:
                await heading.wait_for(state='visible', timeout=int(self.readiness_timeout * 1000))
                return
            except Exception:
                pass
        if await self.landing_screen_visible():
            return
        raise AIStudioControlNotFoundError('Google AI Studio did not load its Playground landing screen.')

    async def prompt_is_interactive(self, prompt):
        try:
            return await prompt.is_editable()
        except Exception:
            return False

    async def prompt_text(self, prompt):
        value = await prompt.evaluate('(element) => element.value ?? element.textContent ?? ""')
        return normalize_option(value)

    def _existing_message_candidates(self):
        return (
            self.page.locator("[data-testid*='conversation-turn' i]"),
            self.page.locator("[data-testid*='message' i]"),
            self.page.locator("[data-message-id], [data-turn-id]"),
        )

    async def has_existing_conversation_messages(self):
        return await self._first_visible(self._existing_message_candidates()) is not None

    def _run_settings_candidates(self):
        return (
            self.page.locator("[data-testid*='run-settings' i]"),
            self.page.get_by_role('button', name=re.compile(r'run settings', re.I)),
            self.page.locator("button[aria-label*='run settings' i]"),
        )

    def _settings_panel_toggle_candidates(self):
        return (
            self.page.locator("button[aria-label*='run settings' i]"),
            self.page.locator(
                "button[data-testid*='run-settings' i], [role='button'][data-testid*='run-settings' i], "
                "button[data-test-id*='run-settings' i], [role='button'][data-test-id*='run-settings' i]"
            ),
            self.page.locator(
                "header button:last-child:has(svg), [role='banner'] button:last-child:has(svg)"
            ),
        )

    async def _run_settings_panel_visible(self):
        return await self._first_visible((
            *self._model_picker_candidates(),
            self._thinking_label(),
        )) is not None

    async def _run_settings_panel_ready(self):
        """Return true only when both primary controls have rendered visibly."""
        model = await self._first_visible(self._model_picker_candidates())
        thinking = await self._first_visible((self._thinking_label(),))
        return model is not None and thinking is not None

    async def _wait_for_run_settings_ready(self):
        model_wait = self._wait_for_any_visible(self._model_picker_candidates(), self.run_settings_ready_timeout)
        thinking_wait = self._wait_for_any_visible((self._thinking_label(),), self.run_settings_ready_timeout)
        results = await asyncio.gather(model_wait, thinking_wait)
        return bool(results[0] is not None and results[1] is not None and await self._run_settings_panel_ready())

    async def _reopen_run_settings_panel(self):
        self._run_settings_open = True
        await self.close_run_settings_panel()
        toggle = await self._first_usable(self._settings_panel_toggle_candidates())
        if toggle is None:
            raise AIStudioControlNotFoundError("Could not find Google AI Studio's Run Settings panel control.")
        await toggle.click()

    async def open_run_settings_panel(self):
        """Open the panel and wait for both primary controls to render."""
        if await self._run_settings_panel_ready():
            self._run_settings_open = True
            return
        self._run_settings_open = True
        if not await self._run_settings_panel_visible():
            toggle = await self._first_usable(self._settings_panel_toggle_candidates())
            if toggle is None:
                raise AIStudioControlNotFoundError("Could not find Google AI Studio's Run Settings panel control.")
            await toggle.click()
        if await self._wait_for_run_settings_ready():
            return
        await self._reopen_run_settings_panel()
        if await self._wait_for_run_settings_ready():
            return
        raise AIStudioControlNotFoundError("Google AI Studio's Run Settings panel did not finish rendering.")

    async def open_run_settings(self):
        await self.open_run_settings_panel()

    async def close_run_settings_panel(self):
        if not self._run_settings_open:
            return
        close = await self._first_usable((
            self.page.get_by_role('button', name=re.compile(r'close', re.I)),
            self.page.locator("ms-right-side-panel button[aria-label*='close' i], ms-right-side-panel button[title*='close' i]"),
        ))
        if close is not None:
            await close.click()
        else:
            await self.page.keyboard.press('Escape')
        hidden = await asyncio.gather(
            self._wait_for_all_hidden(self._model_picker_candidates(), self.readiness_timeout),
            self._wait_for_all_hidden((self._thinking_label(),), self.readiness_timeout),
        )
        if all(hidden) or not await self._run_settings_panel_visible():
            self._run_settings_open = False
            return
        raise AIStudioControlNotFoundError("Google AI Studio's Run Settings panel did not close.")

    # Reserved for future phases. AI Studio's active Playground sidebar item does
    # not navigate to a different workspace in the current production workflow.
    def _sidebar_toggle_candidates(self):
        return (
            self.page.get_by_role('button', name=re.compile(r'open navigation menu', re.I)),
            self.page.locator("button[aria-label*='navigation menu' i], button[data-testid*='navigation' i]"),
            self.page.locator("header button:first-child:has(svg), [role='banner'] button:first-child:has(svg)"),
        )

    def _playground_sidebar_candidates(self):
        return (
            self.page.get_by_role('link', name='Playground', exact=True),
            self.page.get_by_role('button', name='Playground', exact=True),
            self.page.get_by_text('Playground', exact=True).locator(
                'xpath=ancestor-or-self::*[self::a or self::button][1]'
            ),
        )

    async def open_fresh_playground(self):
        sidebar = await self._first_usable(self._sidebar_toggle_candidates())
        if sidebar is None:
            raise AIStudioControlNotFoundError("Could not find Google AI Studio's navigation menu control.")
        await sidebar.click()
        playground = await self._first_usable(self._playground_sidebar_candidates())
        if playground is None:
            raise AIStudioControlNotFoundError("Could not find Google AI Studio's Playground navigation item.")
        await playground.click()
        await self.wait_for_workspace_load()

    def _model_picker_candidates(self):
        return (
            self.page.locator("[data-testid*='model' i][role='button']"),
            self.page.locator("button[aria-label*='model' i]"),
            self.page.get_by_role('button', name=re.compile(r'^(model|gemini)', re.I)),
        )

    async def model_picker(self):
        await self._wait_for_any_visible(self._model_picker_candidates(), self.model_selector_timeout)
        picker = await self._first_usable(self._model_picker_candidates())
        if picker is not None:
            await asyncio.sleep(self.model_selector_stable_for)
            if await self._first_usable(self._model_picker_candidates()) is not None:
                return picker
        if self._run_settings_open:
            await self._reopen_run_settings_panel()
            if await self._wait_for_run_settings_ready():
                picker = await self._first_usable(self._model_picker_candidates())
                if picker is not None:
                    return picker
        raise AIStudioControlNotFoundError("Could not find Google AI Studio's model selector.")

    async def current_model(self):
        picker = await self.model_picker()
        return first_line_option(await picker.inner_text())

    async def _visible_model_selector(self):
        return await self._first_visible((self.page.locator('ms-model-selector'),))

    async def _visible_overlay(self):
        return await self._first_visible((
            self.page.locator("[role='listbox']"),
            self.page.locator("[role='menu']"),
            self.page.locator("[role='dialog']"),
        ))

    async def _overlay_rows(self, overlay):
        model_cards = overlay.locator('button.model-selector-card')
        if await model_cards.count():
            details = []
            for index in range(await model_cards.count()):
                card = model_cards.nth(index)
                if await card.is_visible():
                    details.append({
                        'text': first_line_option(await card.inner_text()),
                        'role': 'option',
                        'disabled': (await card.get_attribute('aria-disabled')) == 'true' or not await card.is_enabled(),
                    })
            return details
        rows = overlay.locator("[role='option'], [role='menuitemradio'], [role='radio']")
        details = []
        for index in range(await rows.count()):
            row = rows.nth(index)
            if not await row.is_visible():
                continue
            details.append({
                'text': await row.inner_text(),
                'role': await row.get_attribute('role'),
                'disabled': (await row.get_attribute('aria-disabled')) == 'true' or not await row.is_enabled(),
            })
        return details

    async def discover_models(self):
        picker = await self.model_picker()
        await picker.click()
        await self._human_pause()
        overlay = await self._visible_model_selector() or await self._visible_overlay()
        if overlay is None:
            raise AIStudioControlNotFoundError("Could not open Google AI Studio's model selector.")
        try:
            models = normalize_options(await self._overlay_rows(overlay))
            if not models:
                raise AIStudioControlNotFoundError(
                    "Google AI Studio's model selector did not contain usable models."
                )
            return models
        finally:
            await self.page.keyboard.press('Escape')

    def _thinking_label(self):
        return self.page.get_by_text(re.compile(r'^Thinking level$', re.I))

    def _thinking_control_candidates(self, label=None):
        """Return the live Thinking Level combobox before structural fallbacks.

        AI Studio currently renders this control as a ``mat-select`` with an
        accessible ``Thinking Level`` name inside ``ms-thinking-level-setting``.
        Keeping that semantic contract first avoids accidentally resolving a
        different button that merely follows the label in the settings panel.
        """
        candidates = [
            self.page.locator(
                "ms-thinking-level-setting mat-select[role='combobox'], "
                "mat-select[aria-label*='thinking level' i], "
                "[role='combobox'][aria-label*='thinking level' i]"
            ),
            self.page.locator("[data-testid*='thinking' i][role='combobox']"),
            self.page.locator("[data-testid*='thinking' i][role='button']"),
            self.page.locator("button[aria-label*='thinking level' i]"),
        ]
        if label is not None:
            candidates.extend((
                label.locator("xpath=following::*[@role='combobox'][1]"),
                label.locator("xpath=following::mat-select[1]"),
                label.locator("xpath=..//*[@role='combobox' or self::mat-select][1]"),
                label.locator("xpath=following::*[@role='button'][1]"),
            ))
        return tuple(candidates)

    async def _wait_for_thinking_label(self):
        return await self._first_visible((self._thinking_label(),))

    async def current_thinking_level(self):
        """Read the selected value without opening the options picker."""
        current = await self._selected_thinking_level_text()
        return ThinkingDiscovery(current is not None, current, None)

    async def _selected_thinking_level_text(self):
        """Read the current selection from the live control, without opening it.

        Angular may replace the ``mat-select`` host after an option is chosen.
        Re-resolving it here avoids verifying a detached pre-selection element.
        """
        await self.open_run_settings()
        label = await self._wait_for_thinking_label()
        if label is None:
            return None
        control = await self._first_usable(self._thinking_control_candidates(label))
        if control is None:
            raise AIStudioControlNotFoundError("Could not interpret Google AI Studio's Thinking Level control.")
        return normalize_option(await control.inner_text())

    async def wait_for_thinking_level_ready(self):
        """Let the dependent Thinking Level control settle after a model change.

        This waits only for the already-visible live control and two browser
        animation frames; it neither opens the Thinking picker nor adds a
        fixed delay to ordinary configuration.
        """
        await self.open_run_settings()
        label = await self._wait_for_thinking_label()
        if label is None:
            raise AIStudioControlNotFoundError("Could not find Google AI Studio's Thinking Level control.")
        control = await self._first_usable(self._thinking_control_candidates(label))
        if control is None:
            raise AIStudioControlNotFoundError("Could not interpret Google AI Studio's Thinking Level control.")
        wait_for = getattr(control, 'wait_for', None)
        if callable(wait_for):
            await wait_for(state='visible', timeout=int(self.readiness_timeout * 1000))
        evaluate = getattr(control, 'evaluate', None)
        if callable(evaluate):
            await evaluate('''element => new Promise(resolve => {
                requestAnimationFrame(() => requestAnimationFrame(resolve));
            })''')
        # Return the live selected value for diagnostics without opening a menu.
        return await self._selected_thinking_level_text()

    async def _open_thinking_overlay(self, control):
        """Open Angular Material's select without relying on a CDP mouse click."""
        scroll = getattr(control, 'scroll_into_view_if_needed', None)
        if callable(scroll):
            await scroll()
        focus = getattr(control, 'focus', None)
        if callable(focus):
            await focus()
        await self.page.keyboard.press('Enter')
        overlay_candidates = (
            self.page.locator("[role='listbox']"),
            self.page.locator("[role='menu']"),
            self.page.locator("[role='dialog']"),
        )
        await self._wait_for_any_visible(overlay_candidates, self.run_settings_ready_timeout)
        overlay = await self._visible_overlay()
        if overlay is not None:
            return overlay

        # CDP mouse clicks can be acknowledged without delivering Angular's
        # final click event. A native DOM click is a narrow fallback for the
        # already identified mat-select host.
        evaluate = getattr(control, 'evaluate', None)
        if callable(evaluate):
            await evaluate('element => element.click()')
            await self._wait_for_any_visible(overlay_candidates, self.run_settings_ready_timeout)
            return await self._visible_overlay()
        return None

    async def _activate_select_option(self, option):
        """Select an already resolved option without the unreliable mouse path."""
        scroll = getattr(option, 'scroll_into_view_if_needed', None)
        if callable(scroll):
            await scroll()
        evaluate = getattr(option, 'evaluate', None)
        if callable(evaluate):
            await evaluate('element => element.click()')
        else:
            focus = getattr(option, 'focus', None)
            if callable(focus):
                await focus()
            await self.page.keyboard.press('Enter')

    async def discover_thinking(self):
        await self.open_run_settings()
        label = await self._wait_for_thinking_label()
        if label is None:
            return ThinkingDiscovery(False)
        control = await self._first_usable(self._thinking_control_candidates(label))
        if control is None:
            raise AIStudioControlNotFoundError("Could not interpret Google AI Studio's Thinking Level control.")
        current = normalize_option(await control.inner_text()) or None
        overlay = await self._open_thinking_overlay(control)
        if overlay is None:
            raise AIStudioControlNotFoundError("Could not open Google AI Studio's Thinking Level control.")
        try:
            options = normalize_options(await self._overlay_rows(overlay))
            if not options:
                raise AIStudioControlNotFoundError(
                    "Google AI Studio's Thinking Level control did not contain usable choices."
                )
            return ThinkingDiscovery(True, current, options)
        finally:
            await self.page.keyboard.press('Escape')

    @staticmethod
    def _target_value(value, control_name):
        target = normalize_option(value)
        if not target:
            raise AIStudioControlNotFoundError(f'A {control_name} value is required.')
        return target

    async def _matching_option(self, overlay, target):
        target_key = target.casefold()
        model_cards = overlay.locator('button.model-selector-card')
        if await model_cards.count():
            for index in range(await model_cards.count()):
                card = model_cards.nth(index)
                if (await card.is_visible() and await card.is_enabled()
                        and first_line_option(await card.inner_text()).casefold() == target_key):
                    return card
            return None
        rows = overlay.locator("[role='option'], [role='menuitemradio'], [role='radio']")
        for index in range(await rows.count()):
            row = rows.nth(index)
            if (await row.is_visible() and await row.is_enabled()
                    and normalize_option(await row.inner_text()).casefold() == target_key):
                return row
        return None

    async def _wait_for_value(self, read_value, target, control_name):
        if normalize_option(await read_value()).casefold() == target.casefold():
            return
        wait_for_function = getattr(self.page, 'wait_for_function', None)
        if callable(wait_for_function):
            try:
                await wait_for_function(
                    "([target]) => document.body.innerText.toLowerCase().includes(target.toLowerCase())",
                    arg=[target], timeout=int(self.readiness_timeout * 1000),
                )
            except Exception:
                pass
            if normalize_option(await read_value()).casefold() == target.casefold():
                return
        raise AIStudioControlNotFoundError(
            f"Google AI Studio did not update its {control_name} to {target}."
        )

    async def _wait_for_thinking_level_value(self, target):
        """Wait for the closed, live Thinking Level control to show ``target``.

        Waiting for text anywhere in the document is unsafe here: an open
        option list already contains the word "High" before Angular has copied
        it into the select trigger. Scope the browser-side wait to the trigger
        and require that the picker is no longer expanded.
        """
        wait_for_function = getattr(self.page, 'wait_for_function', None)
        if callable(wait_for_function):
            try:
                await wait_for_function(
                    '''target => {
                        const selector = [
                            'ms-thinking-level-setting mat-select[role="combobox"]',
                            'mat-select[aria-label*="thinking level" i]',
                            '[role="combobox"][aria-label*="thinking level" i]'
                        ].join(', ');
                        const normalize = value => (value || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                        return [...document.querySelectorAll(selector)].some(control => {
                            const style = window.getComputedStyle(control);
                            const visible = style.display !== 'none' && style.visibility !== 'hidden'
                                && control.getClientRects().length > 0;
                            return visible && control.getAttribute('aria-expanded') !== 'true'
                                && normalize(control.innerText || control.textContent) === normalize(target);
                        });
                    }''',
                    arg=target, timeout=int(self.readiness_timeout * 1000),
                )
            except Exception:
                pass
        current = await self._selected_thinking_level_text()
        if normalize_option(current).casefold() == target.casefold():
            return
        raise AIStudioControlNotFoundError(
            f"Google AI Studio did not update its Thinking Level to {target}."
        )

    async def set_thinking_level(self, value):
        """Select a Thinking Level and confirm the visible control updated."""
        target = self._target_value(value, 'Thinking Level')
        await self.open_run_settings()
        label = await self._wait_for_thinking_label()
        if label is None:
            raise AIStudioControlNotFoundError("Could not find Google AI Studio's Thinking Level control.")
        control = await self._first_usable(self._thinking_control_candidates(label))
        if control is None:
            raise AIStudioControlNotFoundError("Could not interpret Google AI Studio's Thinking Level control.")
        if normalize_option(await control.inner_text()).casefold() == target.casefold():
            return False
        overlay = await self._open_thinking_overlay(control)
        if overlay is None:
            raise AIStudioControlNotFoundError("Could not open Google AI Studio's Thinking Level control.")
        option = await self._matching_option(overlay, target)
        if option is None:
            await self.page.keyboard.press('Escape')
            raise AIStudioControlNotFoundError(f"Google AI Studio does not offer Thinking Level {target}.")
        await self._activate_select_option(option)
        # Require the replacement control itself, not open-overlay text, to
        # show the selected value.
        await self._wait_for_thinking_level_value(target)
        return True

    async def set_model(self, value):
        """Select a model and confirm the model card in Run Settings updated."""
        target = self._target_value(value, 'model')
        if (await self.current_model()).casefold() == target.casefold():
            return False
        picker = await self.model_picker()
        await picker.click()
        await self._wait_for_any_visible((
            self.page.locator('ms-model-selector'),
            self.page.locator("[role='listbox']"),
            self.page.locator("[role='menu']"),
            self.page.locator("[role='dialog']"),
        ), self.run_settings_ready_timeout)
        overlay = await self._visible_model_selector() or await self._visible_overlay()
        if overlay is None:
            raise AIStudioControlNotFoundError("Could not open Google AI Studio's model selector.")
        option = await self._matching_option(overlay, target)
        if option is None:
            await self.page.keyboard.press('Escape')
            raise AIStudioControlNotFoundError(f"Google AI Studio does not offer model {target}.")
        await option.click()
        await self._wait_for_value(self.current_model, target, 'model')
        return True

    async def fill_prompt(self, text):
        prompt = await self.wait_for_prompt_input()
        if prompt is None or not await self.prompt_is_interactive(prompt):
            raise AIStudioControlNotFoundError('Could not find an interactive Google AI Studio prompt input.')
        await prompt.click()
        await self._human_pause()
        await prompt.fill(text)
        await prompt.press('Space')
        await self._human_pause()
        return prompt

    def _file_input_candidates(self):
        return (self.page.locator("input[type='file']"),)

    async def reference_file_input(self):
        for locator in self._file_input_candidates():
            if await locator.count():
                return locator.nth(0)
        raise AIStudioUploadError('Could not find Google AI Studio\'s file attachment control.')

    async def attach_reference_file(self, file_path):
        path = Path(file_path)
        if not path.is_file():
            raise AIStudioUploadError('The selected calibration reference file is unavailable.')
        file_input = await self.reference_file_input()
        await file_input.set_input_files(str(path))
        await self._human_pause()
        return path.name

    async def attach_lecture_file(self, file_path):
        """Attach a lecture through the same hidden input used by calibration."""
        return await self.attach_reference_file(file_path)

    def _attachment_token_candidates(self, filename):
        attachment = self.page.get_by_text(filename, exact=True)
        containers = (
            attachment.locator("xpath=ancestor::*[@role='listitem' or self::li or contains(@class, 'attachment') or contains(@class, 'file')][1]"),
            attachment.locator('xpath=..'),
        )
        return tuple(container.get_by_text(TOKEN_COUNT_PATTERN) for container in containers)

    async def attachment_has_token_count(self, filename):
        for candidate in self._attachment_token_candidates(filename):
            count = await candidate.count()
            for index in range(count):
                item = candidate.nth(index)
                if await item.is_visible() and has_token_count_text(await item.inner_text()):
                    return True
        return False

    async def wait_for_attachment_token_count(self, filename, timeout=None):
        total_timeout = self.attachment_timeout if timeout is None else timeout
        wait_for_function = getattr(self.page, 'wait_for_function', None)
        wait_for_selector = getattr(self.page, 'wait_for_selector', None)
        if wait_for_function is None or wait_for_selector is None:
            return AttachmentReady.TOKEN_TIMEOUT
        token_wait = asyncio.create_task(wait_for_function(
            "([name]) => [...document.querySelectorAll('*')].some(el => "
            "el.textContent.includes(name) && /\\b\\d[\\d,]*(?:\\.\\d+)?\\s*tokens?\\b/i.test(el.textContent))",
            arg=[filename], timeout=int(total_timeout * 1000)))
        stop_wait = asyncio.create_task(wait_for_selector(self._stop_selector(), state='visible', timeout=int(total_timeout * 1000)))
        done, pending = await asyncio.wait((token_wait, stop_wait), timeout=total_timeout,
                                            return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in done:
            if task is stop_wait and task.exception() is None:
                return AttachmentReady.GENERATION_STARTED
            if task is token_wait and task.exception() is None:
                return AttachmentReady.TOKEN_COUNT
        return AttachmentReady.TOKEN_TIMEOUT

    def _send_button_candidates(self):
        return (
            self.page.get_by_role('button', name=re.compile(r'^send$', re.I)),
            self.page.locator("button[aria-label*='send' i], button[data-testid*='send' i]"),
            self.page.get_by_role('button', name=re.compile(r'^run\b', re.I)),
        )

    def _stop_button_candidates(self):
        return (
            self.page.get_by_role('button', name=re.compile(r'^stop$', re.I)),
            self.page.locator("button[aria-label*='stop' i], button[data-testid*='stop' i]"),
        )

    def _run_button_candidates(self):
        return (
            self.page.get_by_role('button', name=re.compile(r'^run$', re.I)),
            self.page.locator("button[aria-label*='run' i], button[data-testid*='run' i]"),
        )

    async def send_button(self):
        return await self._first_usable(self._send_button_candidates())

    async def stop_button(self):
        return await self._first_visible(self._stop_button_candidates())

    async def run_button(self):
        return await self._first_visible(self._run_button_candidates())

    @staticmethod
    def _stop_selector():
        return "button[aria-label='Stop' i], button[data-testid*='stop' i], button:has-text('Stop')"

    @staticmethod
    def _run_selector():
        return "button[aria-label='Run' i], button[data-testid*='run' i], button:has-text('Run')"

    async def send_prompt(self):
        send = await self.send_button()
        if send is None:
            raise AIStudioControlNotFoundError("Could not find Google AI Studio's Send button.")
        await send.click()
        await self._human_pause()

    async def generation_has_started(self):
        return await self.stop_button() is not None

    async def generation_has_completed(self):
        return await self.stop_button() is None and await self.run_button() is not None

    async def wait_for_generation_start(self):
        try:
            await self.page.wait_for_selector(self._stop_selector(), state='visible',
                                               timeout=int(self.generation_start_timeout * 1000))
            return
        except Exception:
            pass
        raise AIStudioGenerationTimeoutError('Google AI Studio did not start generation in time.')

    async def wait_for_generation_complete(self, timeout=None):
        try:
            await self.page.wait_for_selector(self._stop_selector(), state='hidden',
                                               timeout=int((self.generation_timeout if timeout is None else timeout) * 1000))
            await self.page.wait_for_selector(self._run_selector(), state='visible',
                                              timeout=int(self.run_return_timeout * 1000))
            return
        except Exception:
            pass
        raise AIStudioGenerationTimeoutError('Google AI Studio did not finish calibration generation in time.')

    async def wait_for_lecture_generation_complete(self, timeout=None):
        try:
            await self.page.wait_for_selector(self._stop_selector(), state='hidden',
                                               timeout=int((self.lecture_generation_timeout if timeout is None else timeout) * 1000))
            await self.page.wait_for_selector(self._run_selector(), state='visible',
                                              timeout=int(self.run_return_timeout * 1000))
            return
        except Exception:
            pass
        raise AIStudioGenerationTimeoutError('Google AI Studio did not finish lecture generation in time.')

    def _assistant_response_candidates(self):
        return (
            # Prefer the turn host: Thoughts and final answer are separate hosts.
            self.page.locator('ms-chat-turn'),
            self.page.locator('ms-prompt-response, ms-response'),
            self.page.locator("[data-testid*='response' i], [data-test-id*='response' i]"),
            self.page.locator("[data-testid*='assistant' i], [data-test-id*='assistant' i]"),
            self.page.locator("[data-message-author='assistant'], [data-role='assistant']"),
        )

    @staticmethod
    async def _response_details(candidate):
        return await candidate.evaluate('''element => {
            const copy = element.cloneNode(true);
            for (const node of copy.querySelectorAll(
                'ms-thought-chunk, mat-expansion-panel.thought-panel, [data-testid*="thought" i], [data-test-id*="thought" i], .thoughts, details'
            )) node.remove();
            const body = copy.matches('.turn-content') ? copy : copy.querySelector('.turn-content');
            const hasFinalChunk = Boolean(body && body.querySelector(
                'ms-prompt-chunk, ms-text-chunk, [data-testid*="response-text" i]'
            ));
            const thoughtOnly = element.classList.contains('thought-activity-host')
                || (!hasFinalChunk && Boolean(element.querySelector(
                    'ms-thought-chunk, mat-expansion-panel.thought-panel'
                )));
            const bodyText = body ? (body.innerText || body.textContent || '') : '';
            const attributes = [
                element.getAttribute('data-message-author'),
                element.getAttribute('data-role'),
                element.getAttribute('aria-label'),
                element.className,
                element.tagName,
            ].filter(Boolean).join(' ').toLowerCase();
            const turns = [...document.querySelectorAll(
                'ms-chat-turn, [data-message-author], [data-role], [data-testid*="message" i]'
            )];
            const userTurns = turns.filter(node => {
                const marker = [node.getAttribute('data-message-author'), node.getAttribute('data-role'),
                    node.getAttribute('aria-label'), node.className, node.tagName]
                    .filter(Boolean).join(' ').toLowerCase();
                return /(^|[\\s_-])(user|human|you)([\\s_-]|$)/.test(marker);
            });
            const lastUser = userTurns[userTurns.length - 1];
            const afterLastUser = !lastUser || Boolean(lastUser.compareDocumentPosition(element) & Node.DOCUMENT_POSITION_FOLLOWING);
            const modelHeader = /\\bmodel\\b/.test((copy.innerText || '').toLowerCase())
                && /\\b\\d{1,2}:\\d{2}\\b/.test(copy.innerText || '');
            const innerAssistantMarker = Boolean(copy.querySelector(
                '.chat-turn-container.model, .model, [data-message-author="assistant" i], [data-role="assistant" i]'
            ));
            return {
                text: bodyText,
                assistantMarker: /assistant|model|response/.test(attributes) || innerAssistantMarker,
                userMarker: /(^|[\\s_-])(user|human|you)([\\s_-]|$)/.test(attributes),
                modelHeader,
                afterLastUser,
                thoughtOnly,
                bodyFound: Boolean(body),
                hasFinalChunk,
                tagName: element.tagName,
                id: element.id || '',
                testId: element.getAttribute('data-testid') || element.getAttribute('data-test-id') || '',
                ariaLabel: element.getAttribute('aria-label') || '',
                messageAuthor: element.getAttribute('data-message-author') || '',
                role: element.getAttribute('data-role') || '',
            };
        }''')

    async def last_assistant_response_diagnostic(self):
        """Return the exact current response match plus local-only element metadata."""
        group_labels = (
            'ms-chat-turn elements',
            'ms-prompt-response, ms-response',
            'response test-id elements',
            'assistant test-id elements',
            'assistant data-message-author/data-role elements',
        )
        for group_index, locator in enumerate(self._assistant_response_candidates()):
            responses = []
            for index in range(await locator.count()):
                candidate = locator.nth(index)
                if not await candidate.is_visible():
                    continue
                details = await self._response_details(candidate)
                text = normalize_assistant_response_text(details['text'])
                if (not details['thoughtOnly'] and details['bodyFound']
                        and details['hasFinalChunk'] and is_generated_response_text(text)
                        and not details['userMarker']
                        and details['afterLastUser']
                        and (details['assistantMarker'] or details['modelHeader'])):
                    responses.append({
                        'candidate_group': group_index,
                        'candidate_group_label': group_labels[group_index],
                        'candidate_index': index,
                        'element': {
                            key: details[key] for key in (
                                'tagName', 'id', 'testId', 'ariaLabel',
                                'messageAuthor', 'role', 'assistantMarker',
                                'userMarker', 'afterLastUser', 'modelHeader',
                                'thoughtOnly', 'bodyFound', 'hasFinalChunk',
                            )
                        },
                        'rendered_text': details['text'],
                        'extracted_text': text,
                    })
            if responses:
                return responses[-1]
        raise AIStudioResponseError('Could not find Google AI Studio\'s completed calibration response.')

    async def last_assistant_response_text(self):
        return (await self.last_assistant_response_diagnostic())['extracted_text']

    async def has_completed_assistant_response(self):
        """Return whether a completed assistant turn exists after the latest user turn."""
        for locator in self._assistant_response_candidates():
            for index in range(await locator.count()):
                candidate = locator.nth(index)
                if not await candidate.is_visible():
                    continue
                details = await self._response_details(candidate)
                text = normalize_assistant_response_text(details['text'])
                if (not details['thoughtOnly'] and details['bodyFound']
                        and details['hasFinalChunk'] and is_generated_response_text(text)
                        and not details['userMarker']
                        and details['afterLastUser']
                        and (details['assistantMarker'] or details['modelHeader'])):
                    return True
        return False

    async def assistant_response_container_count(self):
        """Count visible response containers without retaining their content."""
        for locator in self._assistant_response_candidates():
            count = 0
            for index in range(await locator.count()):
                if await locator.nth(index).is_visible():
                    count += 1
            if count:
                return count
        return 0

    async def wait_for_completed_assistant_response(self, timeout=5.0):
        """Wait for a final answer body after the latest user turn, not a Thoughts preview."""
        try:
            await self.page.wait_for_function('''() => {
                const turns = [...document.querySelectorAll('ms-chat-turn')];
                const users = turns.filter(node => {
                    const marker = [node.getAttribute('data-message-author'), node.getAttribute('data-role'),
                        node.getAttribute('aria-label'), node.className, node.tagName]
                        .filter(Boolean).join(' ').toLowerCase();
                    return /(^|[\\s_-])(user|human|you)([\\s_-]|$)/.test(marker);
                });
                const lastUser = users[users.length - 1];
                return turns.some(turn => {
                    if (turn.classList.contains('thought-activity-host')) return false;
                    const assistant = turn.querySelector('.chat-turn-container.model, .model, [data-role="assistant" i]');
                    if (!assistant) return false;
                    if (lastUser && !(lastUser.compareDocumentPosition(turn) & Node.DOCUMENT_POSITION_FOLLOWING)) return false;
                    const body = turn.querySelector('.turn-content');
                    if (!body) return false;
                    const text = (body.innerText || body.textContent || '').trim();
                    const finalChunk = body.querySelector('ms-prompt-chunk, ms-text-chunk, [data-testid*="response-text" i]');
                    return Boolean(text && (finalChunk || /permission denied|internal error|failed to generate/i.test(text)));
                });
            }''', timeout=int(timeout * 1000))
        except Exception:
            return False
        return await self.has_completed_assistant_response()

    async def wait_for_stable_assistant_response(self, timeout=8.0, stable_for=1.0):
        """Require the final answer text to stop changing before local processing."""
        deadline = self._clock() + timeout
        previous, stable_since = None, None
        while self._clock() < deadline:
            try:
                current = await self.last_assistant_response_text()
            except AIStudioResponseError:
                current = ''
            now = self._clock()
            if current and current == previous:
                if stable_since is not None and now - stable_since >= stable_for:
                    return current
            else:
                stable_since = now
                previous = current
            await self._sleep(self.generation_poll_interval)
        raise AIStudioResponseError(
            'Google AI Studio final answer kept changing after generation appeared complete.'
        )

    def _generation_error_candidates(self):
        return (self.page.get_by_text(TRANSIENT_GENERATION_ERROR_PATTERN),)

    async def has_transient_generation_error(self):
        for locator in self._generation_error_candidates():
            for index in range(await locator.count()):
                candidate = locator.nth(index)
                if (await candidate.is_visible()
                        and has_transient_generation_error_text(await candidate.inner_text())):
                    return True
        return False

    def arm_generate_content_status_listener(self):
        """Observe only the HTTP status of a GenerateContent response on this page."""
        loop = asyncio.get_running_loop()
        signal = loop.create_future()

        def on_response(response):
            if signal.done():
                return
            url = str(getattr(response, 'url', '')).casefold()
            if 'generatecontent' not in url:
                return
            signal.set_result(getattr(response, 'status', None))

        self.page.on('response', on_response)
        return signal, on_response

    async def wait_for_generate_content_status(self, listener, timeout=30.0):
        signal, _handler = listener
        try:
            return await asyncio.wait_for(asyncio.shield(signal), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    def disarm_generate_content_status_listener(self, listener):
        _signal, handler = listener
        remove_listener = getattr(self.page, 'remove_listener', None)
        if callable(remove_listener):
            remove_listener('response', handler)

    async def prompt_has_default_placeholder(self, prompt):
        placeholder = await prompt.get_attribute('placeholder')
        return normalize_option(placeholder).casefold() == 'start typing a prompt to see what our models can do.'.casefold()
