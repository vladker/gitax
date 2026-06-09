import logging
import os
import sys
from datetime import datetime


_loggers_initialized = False


class SessionCapture:
    """
    Captures all stdout (print() output, progress bars, etc.) to a timestamped
    session log file, while preserving terminal output.

    Usage:
        capture = SessionCapture()
        capture.start()
        # ... your code ...
        capture.stop()  # optional — closes file

    The log file is created as logs/session_YYYYMMDD_HHMMSS.log
    """

    _LOG_DIR = "logs"

    def __init__(self):
        os.makedirs(self._LOG_DIR, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self._path = os.path.join(self._LOG_DIR, f"session_{timestamp}.log")
        self._file = open(self._path, "w", encoding="utf-8")
        self._old_stdout = None

    @property
    def path(self) -> str:
        return self._path

    def start(self):
        self._old_stdout = sys.stdout
        sys.stdout = self
        self._file.write(f"=== Session started at {datetime.now():%Y-%m-%d %H:%M:%S} ===\n")
        self._file.flush()

    def stop(self):
        if self._old_stdout:
            sys.stdout = self._old_stdout
            self._old_stdout = None
        try:
            self._file.write(f"=== Session ended at {datetime.now():%Y-%m-%d %H:%M:%S} ===\n")
            self._file.close()
        except Exception:
            pass

    def write(self, text: str):
        if self._old_stdout:
            self._old_stdout.write(text)
            self._old_stdout.flush()
        self._file.write(text)
        self._file.flush()

    def flush(self):
        if self._old_stdout:
            self._old_stdout.flush()
        self._file.flush()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.stop()


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
