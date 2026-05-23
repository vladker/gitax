import logging
import sys


_loggers_initialized = False


def setup_logging(log_file: str = "archiver.log", level: int = logging.DEBUG):
    """Set up logging to both file and console"""
    global _loggers_initialized

    logger = logging.getLogger("gitax")
    logger.setLevel(level)
    logger.handlers.clear()

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.WARNING)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    logger.info(f"Logging initialized: {log_file}")
    return logger


class LogMixin:
    """Mixin that provides a logger property"""

    @property
    def logger(self):
        return logging.getLogger("gitax")
