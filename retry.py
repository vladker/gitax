"""Reusable retry decorator with exponential backoff."""

from __future__ import annotations

import functools
import time
import logging

_logger = logging.getLogger("gitax")


def retry(
    max_retries: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: tuple[type[Exception], ...] = (Exception,),
    on_retry: callable | None = None,
) -> callable:
    """
    Retry decorator with exponential backoff.

    Args:
        max_retries: Maximum number of retry attempts (not counting the initial call)
        delay: Initial delay between retries in seconds
        backoff: Multiplier for delay after each retry
        exceptions: Tuple of exception types to catch and retry on
        on_retry: Optional callback(retry_num, exception, delay) called before each retry

    Returns:
        Decorator function
    """
    def decorator(func: callable) -> callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            current_delay = delay
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:
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
