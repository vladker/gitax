# -*- coding: utf-8 -*-
"""Tests for SignalHandler."""

import signal
import pytest
from unittest.mock import patch, MagicMock
from signal_handler import SignalHandler


class TestSignalHandler:
    """Test signal handling utility"""

    def test_sets_shutdown_flag(self):
        obj = MagicMock()
        obj._shutdown = False
        handler = SignalHandler()
        handler.register(obj)
        for sig_handler in (signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM)):
            if sig_handler:
                sig_handler(None, None)
        assert obj._shutdown is True

    def test_prevents_double_registration(self):
        obj = MagicMock()
        handler = SignalHandler()
        handler.register(obj)
        handler.register(obj)
        assert handler._registered is True

    def test_custom_shutdown_attr(self):
        obj = MagicMock()
        obj.custom_flag = False
        handler = SignalHandler()
        handler.register(obj, shutdown_attr="custom_flag")
        for sig_handler in (signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM)):
            if sig_handler:
                sig_handler(None, None)
        assert obj.custom_flag is True

    def test_on_cleanup_callback(self):
        obj = MagicMock()
        cleanup_called = MagicMock()
        handler = SignalHandler()
        handler.register(obj, on_cleanup=cleanup_called)
        assert handler._registered is True
