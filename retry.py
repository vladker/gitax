"""Reusable retry decorator with exponential backoff."""

from __future__ import annotations

import functools
import time
import logging
from typing import Callable, TypeVar

_logger = logging.getLogger("gitax")

T = TypeVar("T")

# Default exceptions: retry on transient failures, not logic errors.
_DEFAULT_EXCEPTIONS = (OSError, ConnectionError, TimeoutError)


def retry(
    max_retries: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: tuple[type[Exception], ...] | None = None,
    on_retry: Callable[[int, Exception, float], None] | None = None,
) -> Callable[..., Callable[..., T]]:
    """
    Retry decorator with exponential backoff.

    Args:
        max_retries: Maximum number of retry attempts (not counting the initial call)
        delay: Initial delay between retries in seconds
        backoff: Multiplier for delay after each retry
        exceptions: Tuple of exception types to catch and retry on.
                    Defaults to (OSError, ConnectionError, TimeoutError).
                    Pass an explicit tuple to override.
        on_retry: Optional callback(retry_num, exception, delay) called before each retry

    Returns:
        Decorator function
    """
    effective_exceptions = exceptions if exceptions is not None else _DEFAULT_EXCEPTIONS

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> T:
            current_delay = delay
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except effective_exceptions as exc:
                    if attempt == max_retries:
                        _logger.error(
                            f"{func.__name__} failed after {max_retries} retries: {exc}"
                        )
                        raise
                    _logger.warning(
                        f"{func.__name__} attempt {attempt + 1}/{max_retries} failed: {exc}. "
                        f"Retrying in {current_delay:.1f}s..."
                    )
                    if on_retry:
                        on_retry(attempt + 1, exc, current_delay)
                    time.sleep(current_delay)
                    current_delay *= backoff
        return wrapper
    return decorator
