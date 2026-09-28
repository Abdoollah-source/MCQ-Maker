"""Small local rotating diagnostic log; quiz and clipboard contents are never logged."""
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys


def configure_diagnostics(root):
    log_dir = Path(root) / 'logs'
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        log_dir / 'mcq-maker.log', maxBytes=512 * 1024, backupCount=3, encoding='utf-8'
    )
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s'))
    logger = logging.getLogger('mcq_maker')
    logger.setLevel(logging.INFO)
    for existing in logger.handlers:
        existing.close()
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.propagate = False

    def report_uncaught(exc_type, exc_value, traceback):
        logger.error('Unexpected application error', exc_info=(exc_type, exc_value, traceback))
        sys.__excepthook__(exc_type, exc_value, traceback)

    sys.excepthook = report_uncaught
    return logger
