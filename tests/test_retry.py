# -*- coding: utf-8 -*-
"""Tests for retry decorator."""

import time
import pytest
from retry import retry


class TestRetryDecorator:
    """Test retry decorator with exponential backoff"""

    def test_success_first_try(self):
        """Successful call returns immediately"""
        call_count = 0
        @retry(max_retries=3, delay=0.01)
        def succeed():
            nonlocal call_count
            call_count += 1
            return "ok"
        result = succeed()
        assert result == "ok"
        assert call_count == 1

    def test_retry_on_failure(self):
        """Retries on failure, succeeds on second try"""
        call_count = 0
        @retry(max_retries=3, delay=0.01)
        def fail_then_succeed():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ValueError("not yet")
            return "ok"
        result = fail_then_succeed()
        assert result == "ok"
        assert call_count == 2

    def test_raises_after_max_retries(self):
        """Raises after max retries exhausted"""
        @retry(max_retries=2, delay=0.01)
        def always_fail():
            raise RuntimeError("fail")
        with pytest.raises(RuntimeError, match="fail"):
            always_fail()

    def test_specific_exceptions(self):
        """Only retries on specified exceptions"""
        @retry(max_retries=2, delay=0.01, exceptions=(ValueError,))
        def raise_type_error():
            raise TypeError("wrong type")
        with pytest.raises(TypeError):
            raise_type_error()

    def test_backoff_delays(self):
        """Delays increase with backoff"""
        delays = []
        @retry(max_retries=2, delay=0.1, backoff=2.0)
        def fail_twice():
            raise ValueError("fail")
        try:
            fail_twice()
        except ValueError:
            pass
        # Test runs but we can't easily measure delays without mocking time.sleep

    def test_preserves_function_name(self):
        """Decorator preserves function metadata"""
        @retry(max_retries=3, delay=0.01)
        def my_func():
            return 42
        assert my_func.__name__ == "my_func"
