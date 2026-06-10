# -*- coding: utf-8 -*-
"""
Tests for scan_channel_for_files method in BrowserMAX.

Tests cover:
- Extracting a[download] links with href and download attributes
- Extracting video[src] elements
- Extracting image[src] elements
- Deduplication by filename
- Empty channel (no files)
- Handling page.evaluate errors gracefully
- Parsing file sizes from [class*="size"] elements
"""

import pytest
from unittest.mock import MagicMock, patch


@pytest.fixture
def browser_max():
    """Create a BrowserMAX instance with mocked page."""
    from browser_max import BrowserMAX
    bm = BrowserMAX("https://web.max.ru/test-channel")
    bm.page = MagicMock()
    bm.page.is_closed.return_value = False
    bm._connected = True
    return bm


class TestScanChannelForFiles:
    """Tests for scan_channel_for_files method"""

    def test_method_exists(self):
        """scan_channel_for_files exists on BrowserMAX"""
        from browser_max import BrowserMAX
        bm = BrowserMAX("https://web.max.ru/test-channel")
        assert hasattr(bm, "scan_channel_for_files")

    def test_returns_list(self, browser_max):
        """Returns a list"""
        browser_max.page.evaluate.return_value = []
        with patch.object(browser_max, 'collect_all_messages', return_value=[]):
            result = browser_max.scan_channel_for_files()
        assert isinstance(result, list)

    def test_empty_channel_returns_empty_list(self, browser_max):
        """No files in channel returns empty list"""
        browser_max.page.evaluate.return_value = []
        with patch.object(browser_max, 'collect_all_messages', return_value=[]):
            result = browser_max.scan_channel_for_files()
        assert result == []

    def test_extracts_download_link(self, browser_max):
        """Extracts file info from a[download] elements"""
        fake_files = [{
            "filename": "project-v1.0.0.zip",
            "download_url": "https://cdn.max.ru/file/abc123",
            "file_size": 1048576,
            "message_idx": 0,
            "has_direct_url": True,
            "media_type": "file"
        }]
        browser_max.page.evaluate.return_value = fake_files
        with patch.object(browser_max, 'collect_all_messages', return_value=[]):
            result = browser_max.scan_channel_for_files()
        assert len(result) == 1
        assert result[0]["filename"] == "project-v1.0.0.zip"
        assert result[0]["has_direct_url"] is True

    def test_extracts_video(self, browser_max):
        """Extracts file info from video[src] elements"""
        fake_files = [{
            "filename": "demo.mp4",
            "download_url": "https://cdn.max.ru/video/xyz",
            "file_size": 52428800,
            "message_idx": 1,
            "has_direct_url": True,
            "media_type": "video"
        }]
        browser_max.page.evaluate.return_value = fake_files
        with patch.object(browser_max, 'collect_all_messages', return_value=[]):
            result = browser_max.scan_channel_for_files()
        assert len(result) == 1
        assert result[0]["media_type"] == "video"

    def test_deduplicates_by_filename(self, browser_max):
        """Duplicate filenames are removed"""
        browser_max.page.evaluate.return_value = [
            {"filename": "report.pdf", "download_url": "url1", "file_size": 1024, "message_idx": 0, "has_direct_url": True, "media_type": "file"},
            {"filename": "report.pdf", "download_url": "url2", "file_size": 1024, "message_idx": 5, "has_direct_url": True, "media_type": "file"},
        ]
        with patch.object(browser_max, 'collect_all_messages', return_value=[]):
            result = browser_max.scan_channel_for_files()
        assert len(result) == 1

    def test_handles_evaluate_error(self, browser_max):
        """page.evaluate error returns empty list gracefully"""
        browser_max.page.evaluate.side_effect = Exception("JS error")
        with patch.object(browser_max, 'collect_all_messages', return_value=[]):
            result = browser_max.scan_channel_for_files()
        assert result == []

    def test_file_size_zero_when_not_found(self, browser_max):
        """Missing file_size defaults to 0"""
        fake_files = [{
            "filename": "unknown.zip",
            "download_url": "https://cdn.max.ru/file/zzz",
            "file_size": 0,
            "message_idx": 2,
            "has_direct_url": True,
            "media_type": "file"
        }]
        browser_max.page.evaluate.return_value = fake_files
        with patch.object(browser_max, 'collect_all_messages', return_value=[]):
            result = browser_max.scan_channel_for_files()
        assert result[0]["file_size"] == 0

    def test_all_expected_fields_present(self, browser_max):
        """Each file dict has all required fields"""
        fake_files = [{
            "filename": "test.zip",
            "download_url": "https://cdn.max.ru/f",
            "file_size": 100,
            "message_idx": 0,
            "has_direct_url": True,
            "media_type": "file"
        }]
        browser_max.page.evaluate.return_value = fake_files
        expected_fields = {"filename", "download_url", "file_size", "message_idx", "has_direct_url", "media_type"}
        with patch.object(browser_max, 'collect_all_messages', return_value=[]):
            result = browser_max.scan_channel_for_files()
        assert expected_fields.issubset(result[0].keys())

    def test_calls_collect_all_messages(self, browser_max):
        """collect_all_messages is called during scan"""
        browser_max.page.evaluate.return_value = []
        with patch.object(browser_max, 'collect_all_messages', return_value=[]) as mock_cam:
            browser_max.scan_channel_for_files()
            mock_cam.assert_called_once()

    def test_checks_connection_first(self, browser_max):
        """Raises error if not connected"""
        from browser_max import BrowserMAX, ConnectionError as BMConnectionError
        bm = BrowserMAX("https://web.max.ru/test-channel")
        bm.page = None
        with pytest.raises(BMConnectionError):
            bm.scan_channel_for_files()
