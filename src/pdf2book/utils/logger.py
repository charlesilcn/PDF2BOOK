"""Rich-based logging setup for pdf2book."""

import logging

from rich.logging import RichHandler

_LOGGER_NAME = "pdf2book"


def setup_logger(level: str = "INFO") -> logging.Logger:
    """Configure and return the pdf2book logger."""
    logger = logging.getLogger(_LOGGER_NAME)
    if logger.handlers:
        logger.setLevel(level)
        return logger
    handler = RichHandler(rich_tracebacks=True, show_path=False, markup=True)
    handler.setFormatter(logging.Formatter("%(message)s", datefmt="[%X]"))
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    return logger


def get_logger() -> logging.Logger:
    """Return the pdf2book logger."""
    return logging.getLogger(_LOGGER_NAME)
