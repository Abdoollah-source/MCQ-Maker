from pathlib import Path
import logging
import tempfile
import unittest

from mcq_maker.diagnostics import configure_diagnostics


BASE = Path(__file__).resolve().parents[1]


class DiagnosticsTests(unittest.TestCase):
    def test_local_log_is_bounded_and_contains_no_implicit_payload(self):
        with tempfile.TemporaryDirectory(dir=BASE / 'artifacts') as root:
            logger = configure_diagnostics(root)
            logger.info('Application started')
            for handler in logger.handlers:
                handler.flush()
            text = (Path(root) / 'logs' / 'mcq-maker.log').read_text(encoding='utf-8')
            self.assertIn('Application started', text)
            self.assertEqual(logger.handlers[0].maxBytes, 512 * 1024)
            for handler in logger.handlers:
                handler.close()
            logger.handlers.clear()


if __name__ == '__main__':
    unittest.main()
