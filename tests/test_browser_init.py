# -*- coding: utf-8 -*-
"""Tests for BrowserInitMixin."""

import pytest
from unittest.mock import patch, MagicMock
from browser_init import BrowserInitMixin


class MockArchiver(BrowserInitMixin):
    """Mock archiver for testing."""
    def __init__(self, config=None):
        self.config = config or {}
        self.browser = None
        self._channel_key = "max"
        self._section_key = None


class TestBrowserInitMixin:
    """Test browser initialization mixin"""

    def test_init_browser_creates_browser(self):
        archiver = MockArchiver({"channels": {"max": "https://test.ru"}})
        with patch('browser_init.BrowserMAX') as MockBrowser:
            browser = archiver._init_browser()
            MockBrowser.assert_called_once()
            assert archiver.browser is not None

    def test_init_browser_reuses_existing(self):
        archiver = MockArchiver()
        archiver.browser = MagicMock()
        browser = archiver._init_browser()
        assert browser is archiver.browser

    def test_ensure_browser_connected(self):
        archiver = MockArchiver({"channels": {"max": "https://test.ru"}})
        with patch('browser_init.BrowserMAX') as MockBrowser:
            mock_browser = MagicMock()
            mock_browser.keep_alive_connect.return_value = True
            MockBrowser.return_value = mock_browser
            result = archiver._ensure_browser_connected()
            mock_browser.navigate.assert_called_once()
            mock_browser.ensure_page_ready.assert_called_once()

    def test_close_browser_safe(self):
        archiver = MockArchiver()
        archiver.browser = None
        archiver._close_browser()

    def test_close_browser_closes(self):
        archiver = MockArchiver()
        mock_browser = MagicMock()
        archiver.browser = mock_browser
        archiver._close_browser()
        mock_browser.close.assert_called_once()
        assert archiver.browser is None
