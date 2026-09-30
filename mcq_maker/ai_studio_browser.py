"""Dedicated Brave lifecycle for Google AI Studio browser automation."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
import os
from pathlib import Path
import socket
import subprocess
import time
from urllib.error import URLError
from urllib.request import urlopen

from .ai_studio_errors import (AIStudioBrowserLaunchError, AIStudioCancelledError,
                               AIStudioConnectionError,
                               AIStudioProfileInUseError, BraveNotFoundError,
                               InvalidBraveExecutableError)
from .app_data import app_data_root
from .playwright_runtime import PlaywrightRuntimeError, start as start_playwright_runtime


BRAVE_RELATIVE_PATH = Path('BraveSoftware') / 'Brave-Browser' / 'Application' / 'brave.exe'
AI_STUDIO_NEW_CHAT_URL = 'https://aistudio.google.com/u/0/prompts/new_chat'


class AIStudioReadiness(str, Enum):
    SIGN_IN_REQUIRED = 'sign_in_required'
    AI_STUDIO_REACHED = 'ai_studio_reached'
    UNKNOWN = 'unknown'


@dataclass
class BrowserOwnership:
    """In-memory ownership details for the current MCQ Maker process only."""

    launched_by_mcq_maker: bool = False
    process: object | None = None
    cdp_port: int | None = None


class AIStudioBrowserManager:
    """Own one dedicated Brave and CDP connection during an app lifetime."""

    launch_attempts = 2
    cdp_ready_timeout = 12.0
    cdp_poll_interval = 0.2

    def __init__(self, brave_executable=None, *, environment=None, process_factory=None,
                 playwright_factory=None, cdp_probe=None, clock=None, sleep=None,
                 launch_mode='cdp'):
        if launch_mode not in {'cdp', 'persistent'}:
            raise ValueError("AI Studio browser launch mode must be 'cdp' or 'persistent'.")
        self.brave_executable = brave_executable
        self.launch_mode = launch_mode
        self.environment = dict(os.environ if environment is None else environment)
        self.ownership = BrowserOwnership()
        self._process_factory = process_factory or subprocess.Popen
        self._playwright_factory = playwright_factory
        self._cdp_probe = cdp_probe or self._default_cdp_probe
        self._clock = clock or time.monotonic
        self._sleep = sleep or asyncio.sleep
        self._playwright = None
        self._browser = None
        self._context = None
        self._connection_lock = asyncio.Lock()

    @property
    def profile_path(self):
        if not self.environment.get('LOCALAPPDATA') and not self.environment.get('MCQ_MAKER_TEST_DATA_ROOT'):
            raise InvalidBraveExecutableError(
                'Windows Local AppData is unavailable, so MCQ Maker cannot prepare its AI Studio profile.'
            )
        return app_data_root(self.environment) / 'ai_studio' / 'brave-profile'

    def _configured_executable(self):
        value = self.brave_executable
        if value is None:
            return None
        if not isinstance(value, str):
            raise InvalidBraveExecutableError('The configured Brave executable path is invalid.')
        value = value.strip()
        if not value:
            return None
        path = Path(value)
        if not path.is_absolute() or path.name.casefold() != 'brave.exe' or not path.is_file():
            raise InvalidBraveExecutableError(
                'The configured Brave executable is unavailable. Choose the installed brave.exe file or use automatic detection.'
            )
        return path

    def discovery_candidates(self):
        """Return Windows installation candidates without reading machine-specific paths."""
        roots = (
            self.environment.get('ProgramFiles'),
            self.environment.get('ProgramFiles(x86)'),
            self.environment.get('LOCALAPPDATA'),
        )
        return tuple(Path(root) / BRAVE_RELATIVE_PATH for root in roots if root)

    def discover_brave_executable(self):
        configured = self._configured_executable()
        if configured is not None:
            return configured
        for candidate in self.discovery_candidates():
            if candidate.is_file():
                return candidate
        raise BraveNotFoundError(
            'Brave could not be found. Install Brave or choose its brave.exe file in Settings.'
        )

    @staticmethod
    def free_cdp_port():
        """Reserve an ephemeral IPv4 loopback port number for a future CDP launch."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(('127.0.0.1', 0))
            return int(listener.getsockname()[1])

    def launch_command(self, port):
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            raise ValueError('CDP port must be a valid local port number.')
        return [
            str(self.discover_brave_executable()),
            f'--user-data-dir={self.profile_path}',
            '--remote-debugging-address=127.0.0.1',
            f'--remote-debugging-port={port}',
        ]

    def _launch_process(self, port):
        command = self.launch_command(port)
        try:
            process = self._process_factory(
                command,
                shell=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except OSError as exc:
            raise AIStudioBrowserLaunchError(
                'MCQ Maker could not start its dedicated AI Studio Brave browser.'
            ) from exc
        self.record_owned_process(process, port)
        return process

    @staticmethod
    def _default_cdp_probe(port):
        try:
            with urlopen(f'http://127.0.0.1:{port}/json/version', timeout=0.5) as response:
                return 200 <= response.status < 300
        except (OSError, URLError):
            return False

    async def _wait_for_cdp_ready(self, port, cancellation=None):
        deadline = self._clock() + self.cdp_ready_timeout
        while self._clock() < deadline:
            if cancellation is not None and cancellation.is_set():
                raise AIStudioCancelledError('Connecting to the AI Studio browser was cancelled.')
            if await asyncio.to_thread(self._cdp_probe, port):
                return
            process = self.ownership.process
            if process is not None and process.poll() is not None:
                self._raise_if_profile_in_use(process)
                raise AIStudioBrowserLaunchError(
                    'MCQ Maker could not start its dedicated AI Studio Brave browser.'
                )
            await self._sleep(self.cdp_poll_interval)
        raise AIStudioConnectionError(
            'MCQ Maker could not reach its dedicated AI Studio browser in time.'
        )

    def _raise_if_profile_in_use(self, process):
        stream = getattr(process, 'stderr', None)
        if stream is None:
            return
        try:
            detail = stream.read().decode('utf-8', errors='ignore').casefold()
        except (OSError, AttributeError):
            return
        markers = ('processsingleton', 'profile in use', 'user data directory is already in use', 'singletonlock')
        if any(marker in detail for marker in markers):
            raise self.profile_in_use_error()

    async def _start_playwright(self):
        if self._playwright_factory is None:
            try:
                return await start_playwright_runtime()
            except PlaywrightRuntimeError as exc:
                raise AIStudioConnectionError(
                    'The Playwright browser component is unavailable. Reinstall MCQ Maker and try again.'
                ) from exc
        else:
            factory = self._playwright_factory
        return await factory().start()

    async def _connect_once(self, port, cancellation=None):
        self._launch_process(port)
        await self._wait_for_cdp_ready(port, cancellation)
        self._playwright = await self._start_playwright()
        try:
            self._browser = await self._playwright.chromium.connect_over_cdp(f'http://127.0.0.1:{port}')
            contexts = self._browser.contexts
            if not contexts:
                raise AIStudioConnectionError(
                    'The dedicated AI Studio browser opened without a usable browser profile.'
                )
            self._context = contexts[0]
            return self._context
        except AIStudioConnectionError:
            raise
        except Exception as exc:
            raise AIStudioConnectionError(
                'MCQ Maker could not connect to its dedicated AI Studio browser.'
            ) from exc

    async def _launch_persistent_context(self, port, cancellation=None):
        if cancellation is not None and cancellation.is_set():
            raise AIStudioCancelledError('Connecting to the AI Studio browser was cancelled.')
        self._playwright = await self._start_playwright()
        try:
            self._context = await self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(self.profile_path),
                executable_path=str(self.discover_brave_executable()),
                headless=False,
                args=[
                    '--remote-debugging-address=127.0.0.1',
                    f'--remote-debugging-port={port}',
                ],
            )
            self._browser = self._context.browser
            self.ownership = BrowserOwnership(True, None, port)
            return self._context
        except AIStudioCancelledError:
            raise
        except Exception as exc:
            raise AIStudioBrowserLaunchError(
                'MCQ Maker could not start its dedicated AI Studio Brave browser.'
            ) from exc

    def _connection_is_healthy(self):
        if self._context is None:
            return False
        if self.launch_mode == 'persistent':
            return True
        if self._browser is None or self.ownership.process is None:
            return False
        connected = getattr(self._browser, 'is_connected', None)
        return bool(connected()) if callable(connected) else True

    async def start(self, cancellation=None):
        """Launch and attach once, or reuse a healthy manager-owned connection."""
        async with self._connection_lock:
            if self._connection_is_healthy():
                return self._context
            if self.ownership.process is not None or self._browser is not None or self._playwright is not None:
                await self.shutdown()

            last_error = None
            for _attempt in range(self.launch_attempts):
                port = self.free_cdp_port()
                try:
                    if self.launch_mode == 'persistent':
                        return await self._launch_persistent_context(port, cancellation)
                    return await self._connect_once(port, cancellation)
                except AIStudioProfileInUseError:
                    await self.shutdown()
                    raise
                except AIStudioCancelledError:
                    await self.shutdown()
                    raise
                except AIStudioBrowserLaunchError as exc:
                    last_error = exc
                    await self.shutdown()
                except AIStudioConnectionError as exc:
                    last_error = exc
                    await self.shutdown()

            raise last_error or AIStudioConnectionError(
                'MCQ Maker could not connect to its dedicated AI Studio browser.'
            )

    async def ensure_connected(self, cancellation=None):
        return await self.start(cancellation)

    @staticmethod
    def classify_ai_studio_url(url):
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            host = (parsed.hostname or '').casefold()
        except (TypeError, ValueError):
            return AIStudioReadiness.UNKNOWN
        if host in {'accounts.google.com', 'signin.google.com'}:
            return AIStudioReadiness.SIGN_IN_REQUIRED
        if host == 'aistudio.google.com':
            return AIStudioReadiness.AI_STUDIO_REACHED
        return AIStudioReadiness.UNKNOWN

    async def initial_ai_studio_readiness(self):
        """Classify only the destination URL; Phase 3 verifies actual prompt readiness."""
        context = await self.ensure_connected()
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(AI_STUDIO_NEW_CHAT_URL, wait_until='domcontentloaded')
            return self.classify_ai_studio_url(page.url)
        except Exception as exc:
            raise AIStudioConnectionError(
                'MCQ Maker could not open Google AI Studio in its dedicated browser.'
            ) from exc

    async def shutdown(self):
        """Release CDP and close only the Brave process launched by this manager."""
        browser, context, playwright, ownership = self._browser, self._context, self._playwright, self.ownership
        self._browser = None
        self._context = None
        self._playwright = None
        self.clear_ownership()

        # An attached browser may only be closed when this manager started it.
        # Stopping Playwright below detaches from an unowned CDP browser instead.
        if ownership.launched_by_mcq_maker and self.launch_mode == 'persistent' and context is not None:
            try:
                await context.close()
            except Exception:
                pass
        elif ownership.launched_by_mcq_maker and browser is not None:
            try:
                await browser.close()
            except Exception:
                pass
        if playwright is not None:
            try:
                await playwright.stop()
            except Exception:
                pass
        if ownership.launched_by_mcq_maker and ownership.process is not None:
            process = ownership.process
            try:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=3)
            except (OSError, subprocess.TimeoutExpired):
                pass

    def record_owned_process(self, process, port):
        """Record Phase 2 ownership after a successful launch; never starts a process."""
        if process is None:
            raise ValueError('An owned browser process is required.')
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            raise ValueError('CDP port must be a valid local port number.')
        self.ownership = BrowserOwnership(True, process, port)

    def clear_ownership(self):
        self.ownership = BrowserOwnership()

    def profile_in_use_error(self):
        return AIStudioProfileInUseError(
            'The dedicated MCQ Maker Brave window is already open. Close that window, then try again.'
        )
