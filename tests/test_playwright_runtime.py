import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from mcq_maker.playwright_runtime import PlaywrightRuntimeError, driver_paths, start
from mcq_maker import playwright_smoke


class PlaywrightRuntimeTests(unittest.TestCase):
    def test_driver_paths_are_packaged_with_playwright(self):
        node_path, cli_path = driver_paths()
        self.assertTrue(node_path.is_file())
        self.assertTrue(cli_path.is_file())
        self.assertEqual(node_path.name.casefold(), 'node.exe')
        self.assertEqual(cli_path.name, 'cli.js')

    def test_missing_driver_is_a_clean_runtime_error(self):
        with patch('playwright._impl._driver.compute_driver_executable', return_value=('missing-node.exe', 'missing-cli.js')):
            with self.assertRaises(PlaywrightRuntimeError):
                driver_paths()

    def test_playwright_runtime_can_start_without_a_managed_browser(self):
        async def lifecycle():
            runtime = await start()
            try:
                return runtime.chromium
            finally:
                await runtime.stop()

        self.assertIsNotNone(asyncio.run(lifecycle()))

    def test_playwright_distribution_does_not_contain_browser_binaries(self):
        node_path, _ = driver_paths()
        driver_root = node_path.parent
        browser_executables = {'chrome.exe', 'chromium.exe', 'firefox.exe', 'minibrowser.exe'}
        packaged = {path.name.casefold() for path in driver_root.rglob('*') if path.is_file()}
        self.assertTrue(browser_executables.isdisjoint(packaged))

    def test_smoke_command_requires_an_explicit_isolated_root(self):
        with patch.dict('os.environ', {}, clear=True):
            self.assertEqual(playwright_smoke.run(), 2)

    def test_smoke_reports_missing_brave_as_an_expected_clean_result(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(playwright_smoke, 'driver_paths', return_value=(root / 'node.exe', root / 'cli.js')), \
                 patch.object(playwright_smoke, 'start', new=AsyncMock(return_value=_FakePlaywright())), \
                 patch('mcq_maker.playwright_smoke.AIStudioBrowserManager', _NoBraveManager):
                result = asyncio.run(playwright_smoke._run(root))
        self.assertTrue(result['playwright_initialized'])
        self.assertEqual(result['browser_smoke'], 'brave_not_found')


class _FakePlaywright:
    async def stop(self):
        return None


class _NoBraveManager:
    def discover_brave_executable(self):
        from mcq_maker.ai_studio_errors import BraveNotFoundError
        raise BraveNotFoundError('Brave could not be found.')


if __name__ == '__main__':
    unittest.main()
