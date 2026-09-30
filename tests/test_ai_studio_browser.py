import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
import asyncio
from threading import Event

from mcq_maker.ai_studio_browser import AIStudioBrowserManager, AIStudioReadiness
from mcq_maker.ai_studio_errors import (AIStudioCancelledError, AIStudioConnectionError,
                                        AIStudioProfileInUseError, BraveNotFoundError,
                                        InvalidBraveExecutableError)


class FakeProcess:
    def __init__(self, return_code=None, stderr=None):
        self.return_code = return_code
        self.stderr = stderr
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.return_code

    def terminate(self):
        self.terminated = True
        self.return_code = 0

    def kill(self):
        self.killed = True
        self.return_code = 0

    def wait(self, timeout=None):
        return self.return_code


class FakeBrowser:
    def __init__(self, contexts, connected=True):
        self.contexts = contexts
        self.connected = connected
        self.closed = False

    def is_connected(self):
        return self.connected

    async def close(self):
        self.closed = True


class FakeRuntime:
    def __init__(self, browser):
        self.browser = browser
        self.chromium = self
        self.endpoint = None
        self.stopped = False

    async def connect_over_cdp(self, endpoint):
        self.endpoint = endpoint
        return self.browser

    async def stop(self):
        self.stopped = True


class FakePlaywrightFactory:
    def __init__(self, runtime):
        self.runtime = runtime

    def __call__(self):
        return self

    async def start(self):
        return self.runtime


class AIStudioBrowserManagerTests(unittest.TestCase):
    def _environment(self, root):
        return {
            'ProgramFiles': str(root / 'Program Files'),
            'ProgramFiles(x86)': str(root / 'Program Files (x86)'),
            'LOCALAPPDATA': str(root / 'Local AppData'),
        }

    def _brave_path(self, root):
        return root / 'BraveSoftware' / 'Brave-Browser' / 'Application' / 'brave.exe'

    def test_valid_configured_executable_takes_precedence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            configured = root / 'custom' / 'brave.exe'
            configured.parent.mkdir()
            configured.touch()
            discovered = self._brave_path(root / 'Program Files')
            discovered.parent.mkdir(parents=True)
            discovered.touch()
            manager = AIStudioBrowserManager(str(configured), environment=self._environment(root))
            self.assertEqual(manager.discover_brave_executable(), configured)

    def test_invalid_configured_executable_is_reported_without_falling_back(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manager = AIStudioBrowserManager(str(root / 'missing' / 'brave.exe'), environment=self._environment(root))
            with self.assertRaises(InvalidBraveExecutableError):
                manager.discover_brave_executable()

    def test_discovery_checks_standard_windows_locations(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate = self._brave_path(root / 'Local AppData')
            candidate.parent.mkdir(parents=True)
            candidate.touch()
            manager = AIStudioBrowserManager(environment=self._environment(root))
            self.assertIn(self._brave_path(root / 'Program Files'), manager.discovery_candidates())
            self.assertIn(self._brave_path(root / 'Program Files (x86)'), manager.discovery_candidates())
            self.assertEqual(manager.discover_brave_executable(), candidate)

    def test_missing_brave_is_reported_cleanly(self):
        with tempfile.TemporaryDirectory() as temporary:
            manager = AIStudioBrowserManager(environment=self._environment(Path(temporary)))
            with self.assertRaises(BraveNotFoundError):
                manager.discover_brave_executable()

    def test_profile_path_uses_local_appdata_without_machine_specific_name(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manager = AIStudioBrowserManager(environment=self._environment(root))
            expected = root / 'Local AppData' / 'MCQ Maker' / 'ai_studio' / 'brave-profile'
            self.assertEqual(manager.profile_path, expected)
            self.assertNotIn('Abdoollah', str(manager.profile_path))

    def test_profile_path_uses_explicit_test_root_override(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            test_root = root / 'isolated'
            environment = self._environment(root)
            environment['MCQ_MAKER_TEST_DATA_ROOT'] = str(test_root)
            manager = AIStudioBrowserManager(environment=environment)
            self.assertEqual(manager.profile_path, test_root / 'ai_studio' / 'brave-profile')

    def test_launch_command_is_an_argument_list_with_loopback_cdp(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            executable = self._brave_path(root / 'Program Files')
            executable.parent.mkdir(parents=True)
            executable.touch()
            manager = AIStudioBrowserManager(environment=self._environment(root))
            command = manager.launch_command(43123)
            self.assertIsInstance(command, list)
            self.assertEqual(command[0], str(executable))
            self.assertIn(f'--user-data-dir={manager.profile_path}', command)
            self.assertIn('--remote-debugging-address=127.0.0.1', command)
            self.assertIn('--remote-debugging-port=43123', command)
            self.assertFalse(any('0.0.0.0' in argument for argument in command))

    def test_free_port_binds_ipv4_loopback_only(self):
        listener = MagicMock()
        listener.__enter__.return_value = listener
        listener.getsockname.return_value = ('127.0.0.1', 45678)
        with patch('mcq_maker.ai_studio_browser.socket.socket', return_value=listener) as factory:
            self.assertEqual(AIStudioBrowserManager.free_cdp_port(), 45678)
        factory.assert_called_once_with(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind.assert_called_once_with(('127.0.0.1', 0))

    def test_ownership_is_empty_until_phase_two_records_a_process(self):
        manager = AIStudioBrowserManager(environment={'LOCALAPPDATA': r'C:\\Data'})
        self.assertFalse(manager.ownership.launched_by_mcq_maker)
        self.assertIsNone(manager.ownership.process)
        self.assertIsNone(manager.ownership.cdp_port)
        process = object()
        manager.record_owned_process(process, 45123)
        self.assertTrue(manager.ownership.launched_by_mcq_maker)
        self.assertIs(manager.ownership.process, process)
        self.assertEqual(manager.ownership.cdp_port, 45123)
        manager.clear_ownership()
        self.assertFalse(manager.ownership.launched_by_mcq_maker)

    def test_profile_in_use_boundary_returns_safe_error(self):
        manager = AIStudioBrowserManager(environment={'LOCALAPPDATA': r'C:\\Data'})
        self.assertIsInstance(manager.profile_in_use_error(), AIStudioProfileInUseError)

    def _async_manager(self, root, process_factory, runtime, probe=lambda _port: True):
        executable = self._brave_path(root / 'Program Files')
        executable.parent.mkdir(parents=True, exist_ok=True)
        executable.touch(exist_ok=True)
        return AIStudioBrowserManager(
            environment=self._environment(root), process_factory=process_factory,
            playwright_factory=FakePlaywrightFactory(runtime), cdp_probe=probe,
        )

    def test_start_launches_with_safe_arguments_and_connects_to_default_context(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            process = FakeProcess()
            first, second = object(), object()
            browser = FakeBrowser([first, second])
            runtime = FakeRuntime(browser)
            factory = MagicMock(return_value=process)
            manager = self._async_manager(root, factory, runtime)
            with patch.object(manager, 'free_cdp_port', return_value=43123):
                context = asyncio.run(manager.start())
            self.assertIs(context, first)
            self.assertEqual(runtime.endpoint, 'http://127.0.0.1:43123')
            command, kwargs = factory.call_args
            self.assertIsInstance(command[0], list)
            self.assertIn('--remote-debugging-address=127.0.0.1', command[0])
            self.assertFalse(kwargs['shell'])
            self.assertTrue(manager.ownership.launched_by_mcq_maker)
            self.assertIs(manager.ownership.process, process)

    def test_start_reuses_a_healthy_connection(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            process = FakeProcess()
            browser = FakeBrowser([object()])
            runtime = FakeRuntime(browser)
            factory = MagicMock(return_value=process)
            manager = self._async_manager(root, factory, runtime)
            first = asyncio.run(manager.start())
            second = asyncio.run(manager.start())
            self.assertIs(first, second)
            self.assertEqual(factory.call_count, 1)

    def test_stale_connection_is_closed_before_controlled_relaunch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first_process, second_process = FakeProcess(), FakeProcess()
            first_browser = FakeBrowser([object()])
            second_browser = FakeBrowser([object()])
            runtimes = iter([FakeRuntime(first_browser), FakeRuntime(second_browser)])
            factory = MagicMock(side_effect=[first_process, second_process])
            manager = self._async_manager(root, factory, next(runtimes))
            original_factory = manager._playwright_factory
            manager._playwright_factory = lambda: next(runtimes) if False else original_factory()
            # Replace the factory with a predictable runtime sequence after first connection.
            manager._playwright_factory = type('Factory', (), {
                '__init__': lambda self: setattr(self, 'runs', iter([FakeRuntime(first_browser), FakeRuntime(second_browser)])),
                '__call__': lambda self: self,
                'start': lambda self: _async_next(self.runs),
            })()
            asyncio.run(manager.start())
            first_browser.connected = False
            asyncio.run(manager.start())
            self.assertTrue(first_process.terminated)
            self.assertEqual(factory.call_count, 2)

    def test_failed_first_start_retries_with_a_new_port(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            failed, successful = FakeProcess(return_code=1), FakeProcess()
            browser = FakeBrowser([object()])
            runtime = FakeRuntime(browser)
            factory = MagicMock(side_effect=[failed, successful])
            manager = self._async_manager(root, factory, runtime, probe=lambda port: port == 45124)
            with patch.object(manager, 'free_cdp_port', side_effect=[45123, 45124]):
                asyncio.run(manager.start())
            self.assertEqual(factory.call_count, 2)
            self.assertEqual(manager.ownership.cdp_port, 45124)

    def test_exhausted_readiness_retries_raise_safe_connection_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            factory = MagicMock(side_effect=[FakeProcess(), FakeProcess()])
            manager = self._async_manager(root, factory, FakeRuntime(FakeBrowser([object()])), probe=lambda _port: False)
            manager.cdp_ready_timeout = 0
            with self.assertRaises(AIStudioConnectionError):
                asyncio.run(manager.start())
            self.assertEqual(factory.call_count, manager.launch_attempts)

    def test_no_default_context_is_a_safe_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manager = self._async_manager(root, lambda *_args, **_kwargs: FakeProcess(),
                                          FakeRuntime(FakeBrowser([])))
            manager.launch_attempts = 1
            with self.assertRaises(AIStudioConnectionError):
                asyncio.run(manager.start())

    def test_cancelled_readiness_does_not_retry_or_connect(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            factory = MagicMock(return_value=FakeProcess())
            manager = self._async_manager(root, factory, FakeRuntime(FakeBrowser([object()])))
            cancellation = Event()
            cancellation.set()
            with self.assertRaises(AIStudioCancelledError):
                asyncio.run(manager.start(cancellation))
            self.assertEqual(factory.call_count, 1)

    def test_shutdown_is_idempotent_and_never_terminates_an_unowned_process(self):
        unowned = FakeProcess()
        browser = FakeBrowser([object()])
        runtime = FakeRuntime(browser)
        manager = AIStudioBrowserManager(environment={'LOCALAPPDATA': r'C:\\Data'})
        manager.ownership.process = unowned
        manager._browser = browser
        manager._playwright = runtime
        asyncio.run(manager.shutdown())
        asyncio.run(manager.shutdown())
        self.assertFalse(unowned.terminated)
        self.assertFalse(browser.closed)
        self.assertTrue(runtime.stopped)

    def test_shutdown_closes_only_the_owned_process(self):
        process = FakeProcess()
        browser = FakeBrowser([object()])
        runtime = FakeRuntime(browser)
        manager = AIStudioBrowserManager(environment={'LOCALAPPDATA': r'C:\\Data'})
        manager.record_owned_process(process, 45123)
        manager._browser = browser
        manager._playwright = runtime
        asyncio.run(manager.shutdown())
        self.assertTrue(browser.closed)
        self.assertTrue(runtime.stopped)
        self.assertTrue(process.terminated)
        self.assertFalse(manager.ownership.launched_by_mcq_maker)

    def test_url_classification_is_conservative(self):
        self.assertEqual(
            AIStudioBrowserManager.classify_ai_studio_url('https://accounts.google.com/ServiceLogin'),
            AIStudioReadiness.SIGN_IN_REQUIRED,
        )
        self.assertEqual(
            AIStudioBrowserManager.classify_ai_studio_url('https://aistudio.google.com/u/0/prompts/new_chat'),
            AIStudioReadiness.AI_STUDIO_REACHED,
        )
        self.assertEqual(
            AIStudioBrowserManager.classify_ai_studio_url('https://example.invalid/'),
            AIStudioReadiness.UNKNOWN,
        )


async def _async_next(values):
    return next(values)


if __name__ == '__main__':
    unittest.main()
