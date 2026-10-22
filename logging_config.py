import logging
import logging.handlers
import os
import re
import sys
from datetime import datetime
from urllib.parse import urlsplit, urlunsplit


_loggers_initialized = False

_MASK = "***"


def mask_secret(value, keep: int = 0) -> str:
    """Маскировать секрет: по умолчанию полностью, либо сохранить последние keep символов."""
    if value is None:
        return ""
    text = str(value)
    if not text:
        return ""
    if keep <= 0 or len(text) <= keep:
        return _MASK
    return _MASK + text[-keep:]


def mask_url(url) -> str:
    """Убрать из URL учётные данные и query/fragment — для безопасного логирования."""
    if not url:
        return ""
    try:
        parts = urlsplit(str(url))
    except ValueError:
        return "<unparseable url>"
    netloc = parts.netloc
    if "@" in netloc:
        netloc = _MASK + "@" + netloc.rsplit("@", 1)[1]
    query = _MASK if parts.query else ""
    return urlunsplit((parts.scheme, netloc, parts.path, query, ""))


_KEY_RULE = re.compile(
    r"(?i)\b(access_token|token|client_secret|secret|password|passwd|pwd|api_?key|authorization|bearer|code|state)(\s*[=:]\s*)([^\s&,;\"']+)"
)
_PREFIXED_TOKENS = re.compile(r"\b(github_pat_|gh[pousr]_|npm_[A-Za-z0-9]{10,}|sk-[A-Za-z0-9\-_]{10,})[A-Za-z0-9_\-\.]+")


def _mask_key_match(match: re.Match) -> str:
    key = match.group(1)
    value = match.group(3)
    keep = 4 if re.search(r"(?i)token|key", key) else 0
    return f"{key}{match.group(2)}{mask_secret(value, keep)}"


def scrub_secrets(text: str) -> str:
    """Заменить секреты в произвольном тексте на маски (для логов и session-файлов)."""
    if not text:
        return text
    out = _KEY_RULE.sub(_mask_key_match, text)
    out = _PREFIXED_TOKENS.sub(lambda m: m.group(1) + _MASK, out)
    return out


class SecretScrubber(logging.Filter):
    """logging.Filter, вырезающий секреты и токены из каждого сообщения."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = scrub_secrets(record.getMessage())
            record.args = ()
        except Exception:
            pass
        return True


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
        self._file.write(scrub_secrets(text))
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


def setup_logging(
    log_file: str = "archiver.log",
    level: int = logging.DEBUG,
    max_bytes: int = 10 * 1024 * 1024,   # 10 MB
    backup_count: int = 3,
):
    """Set up logging to both file and console.

    Uses RotatingFileHandler so archiver.log never grows beyond max_bytes.
    Old logs are kept as archiver.log.1, .2, .3 (up to backup_count).
    """
    global _loggers_initialized

    logger = logging.getLogger("gitax")
    logger.setLevel(level)
    logger.handlers.clear()

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    file_handler.addFilter(SecretScrubber())
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(SecretScrubber())
    logger.addHandler(console_handler)

    logger.info(f"Logging initialized: {log_file} (rotation: {max_bytes / 1024 / 1024:.0f}MB x {backup_count})")
    return logger


class LogMixin:
    """Mixin that provides a logger property"""

    @property
    def logger(self):
        return logging.getLogger("gitax")
