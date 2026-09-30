"""Test-only local smoke check for a frozen Playwright runtime.

This module is intentionally reachable only through the hidden
``--playwright-smoke-test`` command-line switch.  It never opens AI Studio and
requires ``MCQ_MAKER_TEST_DATA_ROOT`` so it cannot use normal user data.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from .ai_studio_browser import AIStudioBrowserManager
from .ai_studio_errors import BraveNotFoundError
from .app_data import TEST_DATA_ROOT_ENV
from .playwright_runtime import PlaywrightRuntimeError, driver_paths, start


def _test_root() -> Path | None:
    value = os.environ.get(TEST_DATA_ROOT_ENV)
    if not value:
        return None
    candidate = Path(value).expanduser()
    return candidate if candidate.is_absolute() else None


def _write_result(root: Path, result: dict) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / 'playwright-smoke-result.json').write_text(
        json.dumps(result, indent=2, sort_keys=True), encoding='utf-8'
    )


async def _run(root: Path) -> dict:
    node_path, cli_path = driver_paths()
    result = {
        'driver_cli': str(cli_path),
        'driver_node': str(node_path),
        'playwright_initialized': False,
        'browser_smoke': 'not_run',
    }
    playwright = await start()
    try:
        result['playwright_initialized'] = True
        manager = AIStudioBrowserManager()
        try:
            brave_path = manager.discover_brave_executable()
        except BraveNotFoundError:
            # This is an expected deployment state, not a missing-driver error.
            result['browser_smoke'] = 'brave_not_found'
            return result
        result['brave_executable'] = str(brave_path)
        try:
            context = await manager.start()
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto('about:blank')
            await page.set_content('<title>MCQ Maker smoke</title><p>Local smoke check</p>')
            if await page.title() != 'MCQ Maker smoke':
                raise PlaywrightRuntimeError('The local browser page did not respond as expected.')
            result['browser_smoke'] = 'passed'
        finally:
            await manager.shutdown()
        return result
    finally:
        await playwright.stop()


def run() -> int:
    """Run the isolated check and leave a small, privacy-safe result file."""
    root = _test_root()
    if root is None:
        return 2
    try:
        result = asyncio.run(_run(root))
    except Exception as exc:
        _write_result(root, {
            'browser_smoke': 'not_run',
            'error_class': type(exc).__name__,
            'error_message': str(exc),
            'playwright_initialized': False,
        })
        return 1
    _write_result(root, result)
    return 0
