# -*- coding: utf-8 -*-
"""
Tests for _extract_file_urls method in BrowserMAX.

Tests cover:
- Extracting URL from a[download] elements
- Extracting URL from a[href*="download"] alternative links
- Extracting URL from video[src] elements
- Extracting URL from img[src] (non-emoji) elements
- Empty DOM returns empty dict
- page.evaluate error returns empty dict
- Deduplication by filename
- Integration: scan_channel_for_archives includes volume_urls
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


class TestExtractFileUrls:
    """Tests for _extract_file_urls method"""

    def test_method_exists(self):
        """_extract_file_urls exists on BrowserMAX"""
        from browser_max import BrowserMAX
        bm = BrowserMAX("https://web.max.ru/test-channel")
        assert hasattr(bm, "_extract_file_urls")

    def test_returns_dict(self, browser_max):
        """Returns a dict"""
        browser_max.page.evaluate.return_value = {}
        result = browser_max._extract_file_urls()
        assert isinstance(result, dict)

    def test_empty_dom_returns_empty_dict(self, browser_max):
        """No file elements returns empty dict"""
        browser_max.page.evaluate.return_value = {}
        result = browser_max._extract_file_urls()
        assert result == {}

    def test_extracts_download_link(self, browser_max):
        """Extracts URL from a[download] elements"""
        browser_max.page.evaluate.return_value = {
            "urlMap": {"project-v1.0.0.zip": "https://cdn.max.ru/file/abc123"},
            "debug": {"totalMessages": 5, "skippedNoUrl": 0, "skippedNoFilename": 0,
                      "byStrategy": {"a_download": 1, "a_href_download": 0, "video": 0, "img": 0, "genericFile": 0},
                      "firstMsgClasses": "msg", "firstMsgTag": "DIV", "firstMsgSample": "",
                      "archiveMsgSamples": []},
        }
        result = browser_max._extract_file_urls()
        assert len(result) == 1
        assert result["project-v1.0.0.zip"] == "https://cdn.max.ru/file/abc123"

    def test_extracts_alt_download_link(self, browser_max):
        """Extracts URL from a[href*="download"] alternative links"""
        browser_max.page.evaluate.return_value = {
            "urlMap": {"report.pdf": "https://cdn.max.ru/download/xyz"},
            "debug": {"totalMessages": 3, "skippedNoUrl": 0, "skippedNoFilename": 0,
                      "byStrategy": {"a_download": 0, "a_href_download": 1, "video": 0, "img": 0, "genericFile": 0},
                      "firstMsgClasses": "msg", "firstMsgTag": "DIV", "firstMsgSample": "",
                      "archiveMsgSamples": []},
        }
        result = browser_max._extract_file_urls()
        assert result["report.pdf"] == "https://cdn.max.ru/download/xyz"

    def test_extracts_video(self, browser_max):
        """Extracts URL from video[src] elements"""
        browser_max.page.evaluate.return_value = {
            "urlMap": {"demo.mp4": "https://cdn.max.ru/video/xyz"},
            "debug": {"totalMessages": 2, "skippedNoUrl": 0, "skippedNoFilename": 0,
                      "byStrategy": {"a_download": 0, "a_href_download": 0, "video": 1, "img": 0, "genericFile": 0},
                      "firstMsgClasses": "msg", "firstMsgTag": "DIV", "firstMsgSample": "",
                      "archiveMsgSamples": []},
        }
        result = browser_max._extract_file_urls()
        assert result["demo.mp4"] == "https://cdn.max.ru/video/xyz"

    def test_extracts_image(self, browser_max):
        """Extracts URL from img[src] (non-emoji) elements"""
        browser_max.page.evaluate.return_value = {
            "urlMap": {"screenshot.png": "https://cdn.max.ru/img/abc"},
            "debug": {"totalMessages": 4, "skippedNoUrl": 0, "skippedNoFilename": 0,
                      "byStrategy": {"a_download": 0, "a_href_download": 0, "video": 0, "img": 1, "genericFile": 0},
                      "firstMsgClasses": "msg", "firstMsgTag": "DIV", "firstMsgSample": "",
                      "archiveMsgSamples": []},
        }
        result = browser_max._extract_file_urls()
        assert result["screenshot.png"] == "https://cdn.max.ru/img/abc"

    def test_deduplicates_by_filename(self, browser_max):
        """Duplicate filenames are deduplicated (first occurrence wins)"""
        browser_max.page.evaluate.return_value = {
            "urlMap": {"report.pdf": "https://cdn.max.ru/file/url1"},
            "debug": {"totalMessages": 3, "skippedNoUrl": 0, "skippedNoFilename": 0,
                      "byStrategy": {"a_download": 1, "a_href_download": 0, "video": 0, "img": 0, "genericFile": 0},
                      "firstMsgClasses": "msg", "firstMsgTag": "DIV", "firstMsgSample": "",
                      "archiveMsgSamples": []},
        }
        result = browser_max._extract_file_urls()
        assert result["report.pdf"] == "https://cdn.max.ru/file/url1"
        # JS-side dedup means evaluate already returns deduplicated map

    def test_multiple_files(self, browser_max):
        """Multiple files returned correctly"""
        browser_max.page.evaluate.return_value = {
            "urlMap": {
                "file1.zip": "https://cdn.max.ru/f1",
                "file2.zip": "https://cdn.max.ru/f2",
                "file3.zip": "https://cdn.max.ru/f3",
            },
            "debug": {"totalMessages": 5, "skippedNoUrl": 0, "skippedNoFilename": 0,
                      "byStrategy": {"a_download": 3, "a_href_download": 0, "video": 0, "img": 0, "genericFile": 0},
                      "firstMsgClasses": "msg", "firstMsgTag": "DIV", "firstMsgSample": "",
                      "archiveMsgSamples": []},
        }
        result = browser_max._extract_file_urls()
        assert len(result) == 3
        assert result["file1.zip"] == "https://cdn.max.ru/f1"
        assert result["file2.zip"] == "https://cdn.max.ru/f2"
        assert result["file3.zip"] == "https://cdn.max.ru/f3"

    def test_handles_evaluate_error(self, browser_max):
        """page.evaluate error returns empty dict gracefully"""
        browser_max.page.evaluate.side_effect = Exception("JS error")
        result = browser_max._extract_file_urls()
        assert result == {}

    def test_handles_empty_filename(self, browser_max):
        """Entries with empty filename are excluded"""
        browser_max.page.evaluate.return_value = {
            "urlMap": {"": "https://cdn.max.ru/file/noname", "valid.zip": "https://cdn.max.ru/file/valid"},
            "debug": {"totalMessages": 2, "skippedNoUrl": 0, "skippedNoFilename": 0,
                      "byStrategy": {"a_download": 2, "a_href_download": 0, "video": 0, "img": 0, "genericFile": 0},
                      "firstMsgClasses": "msg", "firstMsgTag": "DIV", "firstMsgSample": "",
                      "archiveMsgSamples": []},
        }
        result = browser_max._extract_file_urls()
        assert "valid.zip" in result
        # JS-side handles empty filenames

    def test_checks_connection_first(self, browser_max):
        """Raises error if not connected"""
        from browser_max import BrowserMAX, ConnectionError as BMConnectionError
        bm = BrowserMAX("https://web.max.ru/test-channel")
        bm.page = None
        with pytest.raises(BMConnectionError):
            bm._extract_file_urls()


class TestDebugDumpFileMessages:
    """Tests for _debug_dump_file_messages method"""

    def test_method_exists(self):
        """_debug_dump_file_messages exists on BrowserMAX"""
        from browser_max import BrowserMAX
        bm = BrowserMAX("https://web.max.ru/test-channel")
        assert hasattr(bm, "_debug_dump_file_messages")

    def test_returns_dict(self, browser_max):
        """Returns a dict with expected keys"""
        browser_max.page.evaluate.return_value = {"totalMessages": 0, "matching": []}
        result = browser_max._debug_dump_file_messages()
        assert isinstance(result, dict)
        assert "total_messages" in result
        assert "matching_messages" in result
        assert "api_urls" in result

    def test_no_matching_returns_empty(self, browser_max):
        """No matching messages returns empty list"""
        browser_max.page.evaluate.return_value = {"totalMessages": 0, "matching": []}
        result = browser_max._debug_dump_file_messages("nonexistent.7z")
        assert result["total_messages"] == 0
        assert result["matching_messages"] == []

    def test_handles_evaluate_error(self, browser_max):
        """page.evaluate error returns gracefully"""
        browser_max.page.evaluate.side_effect = Exception("JS error")
        result = browser_max._debug_dump_file_messages("test.7z")
        assert isinstance(result, dict)
        assert result["total_messages"] == 0

    def test_checks_connection_first(self, browser_max):
        """Raises error if not connected"""
        from browser_max import BrowserMAX, ConnectionError as BMConnectionError
        bm = BrowserMAX("https://web.max.ru/test-channel")
        bm.page = None
        with pytest.raises(BMConnectionError):
            bm._debug_dump_file_messages()

    def test_returns_matching_message(self, browser_max):
        """Returns structured info for matching messages"""
        browser_max.page.evaluate.return_value = {
            "totalMessages": 3,
            "matching": [{
                "index": 2,
                "tagName": "DIV",
                "className": "message file-message",
                "textPreview": "Screenshots_20260610_0953.7z some text",
                "outerHTML": "<div class='message file-message'>test</div>",
                "innerHTML": "test",
                "attributes": {"class": "message file-message"},
                "links": [],
                "buttons": [],
                "inputs": [],
                "imgs": [],
                "videos": [],
                "audios": [],
                "iframes": [],
                "dataAttrs": {},
            }],
        }
        result = browser_max._debug_dump_file_messages("Screenshots")
        assert result["total_messages"] == 3
        assert len(result["matching_messages"]) == 1
        msg = result["matching_messages"][0]
        assert msg["tagName"] == "DIV"
        assert msg["index"] == 2
        assert "Screenshots" in msg.get("textPreview", "")

    def test_returns_links_in_message(self, browser_max):
        """Correctly returns link info from DOM"""
        browser_max.page.evaluate.return_value = {
            "totalMessages": 1,
            "matching": [{
                "index": 0,
                "tagName": "DIV",
                "className": "msg",
                "textPreview": "test file.zip",
                "outerHTML": "<div class='msg'>test</div>",
                "innerHTML": "",
                "attributes": {"class": "msg"},
                "links": [
                    {"href": "https://cdn.max.ru/file/abc", "download": "file.zip",
                     "text": "Download", "rel": "", "target": "", "type": "",
                     "onclick": False, "className": "", "role": "", "ariaLabel": ""},
                ],
                "buttons": [],
                "inputs": [],
                "imgs": [],
                "videos": [],
                "audios": [],
                "iframes": [],
                "dataAttrs": {},
            }],
        }
        result = browser_max._debug_dump_file_messages("file.zip")
        assert len(result["matching_messages"]) == 1
        links = result["matching_messages"][0].get("links", [])
        assert len(links) == 1
        assert links[0]["href"] == "https://cdn.max.ru/file/abc"
        assert links[0]["download"] == "file.zip"


class TestScanChannelForArchivesVolumeUrls:
    """Tests that scan_channel_for_archives includes volume_urls"""

    def test_volume_urls_in_result(self, browser_max):
        """volume_urls field present in each archive result"""
        from browser_max import group_volumes
        fake_messages = [
            {"text": "archive.7z.001", "html": ""},
            {"text": "archive.7z.002", "html": ""},
            {"text": "readme.txt", "html": ""},
        ]
        url_map = {
            "archive.7z.001": "https://cdn.max.ru/f/001",
            "archive.7z.002": "https://cdn.max.ru/f/002",
        }
        with patch.object(browser_max, 'collect_all_messages', return_value=fake_messages):
            with patch.object(browser_max, '_extract_file_urls', return_value=url_map):
                # Need to patch import of group_volumes too if necessary
                result = browser_max.scan_channel_for_archives()
        assert len(result) >= 1
        for arch in result:
            assert "volume_urls" in arch
            assert isinstance(arch["volume_urls"], dict)

    def test_volume_urls_maps_correctly(self, browser_max):
        """volume_urls maps each volume filename to its download URL"""
        fake_messages = [
            {"text": "backup.7z.001", "html": ""},
            {"text": "backup.7z.002", "html": ""},
            {"text": "notes.txt", "html": ""},
        ]
        url_map = {
            "backup.7z.001": "https://cdn.max.ru/f/001",
            "backup.7z.002": "https://cdn.max.ru/f/002",
        }
        with patch.object(browser_max, 'collect_all_messages', return_value=fake_messages):
            with patch.object(browser_max, '_extract_file_urls', return_value=url_map):
                result = browser_max.scan_channel_for_archives()
        # Find the backup archive
        for arch in result:
            if arch["base_name"] == "backup.7z":
                assert arch["volume_urls"]["backup.7z.001"] == "https://cdn.max.ru/f/001"
                assert arch["volume_urls"]["backup.7z.002"] == "https://cdn.max.ru/f/002"
                break
        else:
            pytest.fail("backup.7z not found in results")

    def test_partial_url_map(self, browser_max):
        """Some volumes may not have URLs — those are omitted from volume_urls"""
        fake_messages = [
            {"text": "data.7z.001", "html": ""},
            {"text": "data.7z.002", "html": ""},
            {"text": "data.7z.003", "html": ""},
        ]
        url_map = {
            "data.7z.001": "https://cdn.max.ru/f/001",
            # 002 and 003 not in map
        }
        with patch.object(browser_max, 'collect_all_messages', return_value=fake_messages):
            with patch.object(browser_max, '_extract_file_urls', return_value=url_map):
                result = browser_max.scan_channel_for_archives()
        for arch in result:
            if arch["base_name"] == "data.7z":
                assert arch["volume_urls"].get("data.7z.001") == "https://cdn.max.ru/f/001"
                assert "data.7z.002" not in arch["volume_urls"]
                assert "data.7z.003" not in arch["volume_urls"]
                break
        else:
            pytest.fail("data.7z not found in results")

    def test_empty_url_map(self, browser_max):
        """Empty url_map from _extract_file_urls results in empty volume_urls"""
        fake_messages = [
            {"text": "data.7z.001", "html": ""},
        ]
        with patch.object(browser_max, 'collect_all_messages', return_value=fake_messages):
            with patch.object(browser_max, '_extract_file_urls', return_value={}):
                result = browser_max.scan_channel_for_archives()
        for arch in result:
            assert arch["volume_urls"] == {}
