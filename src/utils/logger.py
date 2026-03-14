from __future__ import annotations

import logging

try:
    from pythonjsonlogger.json import JsonFormatter
except ImportError:
    try:
        from pythonjsonlogger.jsonlogger import JsonFormatter
    except ImportError:
        # Fallback for environments without pythonjsonlogger
        class JsonFormatter(logging.Formatter):
            def format(self, record):
                return super().format(record)


def configure_logger(level: str) -> None:
    logger = logging.getLogger()
    logger.setLevel(level)

    handler = logging.StreamHandler()
    formatter = JsonFormatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    handler.setFormatter(formatter)

    if not logger.handlers:
        logger.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
