"""Small, packaging-aware boundary around Playwright's bundled driver."""
from __future__ import annotations

from pathlib import Path


class PlaywrightRuntimeError(RuntimeError):
    """Raised when MCQ Maker cannot start its bundled Playwright runtime."""


def driver_paths() -> tuple[Path, Path]:
    """Return the Node executable and CLI entry point shipped by Playwright.

    Playwright resolves these beneath ``sys._MEIPASS`` in a PyInstaller build,
    so validating them here catches a missing driver before browser work starts.
    """
    try:
        from playwright._impl._driver import compute_driver_executable
        node_path, cli_path = compute_driver_executable()
    except (ImportError, OSError, ValueError) as exc:
        raise PlaywrightRuntimeError('The bundled Playwright runtime is unavailable.') from exc
    paths = Path(node_path), Path(cli_path)
    if not all(path.is_file() for path in paths):
        raise PlaywrightRuntimeError('The bundled Playwright runtime is incomplete.')
    return paths


async def start() -> object:
    """Start Playwright without downloading or launching a managed browser."""
    driver_paths()
    try:
        from playwright.async_api import async_playwright
        return await async_playwright().start()
    except (ImportError, OSError, RuntimeError) as exc:
        raise PlaywrightRuntimeError('The bundled Playwright runtime could not start.') from exc
