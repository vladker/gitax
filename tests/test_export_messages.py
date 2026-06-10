"""
Unit tests for export_messages_to_file feature.

Tests cover:
- _collect_full_batch() JS extraction logic (mocked page.evaluate)
- _deduplicate() signature-based dedup
- _write_json() output format validation
- _write_csv() output format validation
- export_messages_to_file() parameter handling
"""

import json
import csv
import os
import pytest
from unittest.mock import patch, MagicMock, PropertyMock
from io import StringIO
from browser_max import BrowserMAX


# ─── Fixtures ───

class TestExportMessagesInit:
    """Test BrowserMAX initialization for export context"""

    def test_browsermax_init(self):
        """BrowserMAX initializes without errors"""
        bm = BrowserMAX("https://web.max.ru/test-channel")
        assert bm.channel_url == "https://web.max.ru/test-channel"
        assert bm.page is None

    def test_export_method_exists(self):
        """export_messages_to_file method exists on BrowserMAX"""
        bm = BrowserMAX("https://web.max.ru/test-channel")
        assert hasattr(bm, "export_messages_to_file")
        assert callable(bm.export_messages_to_file)

    def test_helper_methods_exist(self):
        """All helper methods exist"""
        bm = BrowserMAX("https://web.max.ru/test-channel")
        assert hasattr(bm, "_collect_full_batch")
        assert hasattr(bm, "_scroll_and_collect_full")
        assert hasattr(bm, "_write_json")
        assert hasattr(bm, "_write_csv")


# ─── _collect_full_batch tests ───

class TestCollectFullBatch:
    """Test _collect_full_batch JS extraction"""

    def _make_mock_page(self, messages_data):
        """Helper to create a mock page that returns messages_data"""
        page = MagicMock()
        page.evaluate.return_value = messages_data
        page.is_closed.return_value = False
        return page

    def test_collect_full_batch_returns_list(self):
        """_collect_full_batch returns a list of dicts"""
        bm = BrowserMAX("https://web.max.ru/test-channel")
        bm.page = self._make_mock_page([
            {
                "text": "Hello world",
                "html": "<div>Hello world</div>",
                "classes": "message message--out",
                "sender": "Test User",
                "timestamp": "2026-06-05T12:00:00",
                "direction": "out",
                "attachments": [],
                "reactions": [],
                "is_reply": False,
            }
        ])

        result = bm._collect_full_batch()
        assert isinstance(result, list)
        assert len(result) == 1

    def test_collect_full_batch_empty_dom(self):
        """_collect_full_batch returns empty list when no messages"""
        bm = BrowserMAX("https://web.max.ru/test-channel")
        bm.page = self._make_mock_page([])

        result = bm._collect_full_batch()
        assert result == []

    def test_collect_full_batch_evaluate_called(self):
        """_collect_full_batch calls page.evaluate"""
        bm = BrowserMAX("https://web.max.ru/test-channel")
        bm.page = self._make_mock_page([])

        bm._collect_full_batch()
        bm.page.evaluate.assert_called()

    def test_collect_full_batch_evaluate_error(self):
        """_collect_full_batch handles evaluate errors gracefully"""
        bm = BrowserMAX("https://web.max.ru/test-channel")
        bm.page = self._make_mock_page(None)
        bm.page.evaluate.side_effect = Exception("JS error")

        result = bm._collect_full_batch()
        assert result == []

    def test_collect_full_batch_message_fields(self):
        """Collected messages have all expected fields"""
        expected_fields = [
            "text", "html", "classes", "sender", "timestamp",
            "direction", "attachments", "reactions", "is_reply"
        ]
        bm = BrowserMAX("https://web.max.ru/test-channel")
        sample_msg = {
            "text": "Test message",
            "html": "<div>Test</div>",
            "classes": "message message--out",
            "sender": "Alice",
            "timestamp": "2026-06-05T10:00:00",
            "direction": "out",
            "attachments": [],
            "reactions": [],
            "is_reply": False,
        }
        bm.page = self._make_mock_page([sample_msg])

        result = bm._collect_full_batch()
        assert len(result) == 1
        for field in expected_fields:
            assert field in result[0], f"Missing field: {field}"


# ─── _write_json tests ───

class TestWriteJson:
    """Test JSON output writing"""

    def test_write_json_creates_file(self, tmp_path):
        """_write_json creates a valid JSON file"""
        bm = BrowserMAX("https://web.max.ru/test-channel")
        messages = [
            {"text": "First message", "sender": "Alice", "timestamp": "2026-06-05T10:00:00", "direction": "out", "classes": "message message--out", "attachments": [], "reactions": [], "is_reply": False},
            {"text": "Second message", "sender": "Bob", "timestamp": "2026-06-05T11:00:00", "direction": "in", "classes": "message message--in", "attachments": [], "reactions": [], "is_reply": False},
        ]
        out_path = str(tmp_path / "export.json")

        bm._write_json(out_path, messages, bm.channel_url)

        assert os.path.exists(out_path)
        with open(out_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        assert "metadata" in data
        assert "messages" in data
        assert data["metadata"]["total_messages"] == 2
        assert data["metadata"]["channel_url"] == bm.channel_url
        assert len(data["messages"]) == 2

    def test_write_json_metadata_format(self, tmp_path):
        """JSON metadata has correct structure"""
        bm = BrowserMAX("https://web.max.ru/test-channel")
        messages = [{"text": "Hello", "sender": "Alice", "timestamp": "2026-06-05T10:00:00", "direction": "out", "classes": "message", "attachments": [], "reactions": [], "is_reply": False}]
        out_path = str(tmp_path / "export.json")

        bm._write_json(out_path, messages, bm.channel_url)

        with open(out_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        metadata = data["metadata"]
        assert "exported_at" in metadata
        assert "channel_url" in metadata
        assert "total_messages" in metadata
        assert "format_version" in metadata
        assert metadata["format_version"] == "1.0"

    def test_write_json_with_attachments(self, tmp_path):
        """JSON includes attachment data correctly"""
        bm = BrowserMAX("https://web.max.ru/test-channel")
        messages = [
            {
                "text": "File upload",
                "sender": "Alice",
                "timestamp": "2026-06-05T10:00:00",
                "direction": "out",
                "classes": "message message--out",
                "attachments": [{"name": "repo.zip", "size": "45 MB"}],
                "reactions": [],
                "is_reply": False,
            }
        ]
        out_path = str(tmp_path / "export.json")

        bm._write_json(out_path, messages, bm.channel_url)

        with open(out_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        assert len(data["messages"][0]["attachments"]) == 1
        assert data["messages"][0]["attachments"][0]["name"] == "repo.zip"

    def test_write_json_empty_messages(self, tmp_path):
        """JSON handles empty message list"""
        bm = BrowserMAX("https://web.max.ru/test-channel")
        out_path = str(tmp_path / "export.json")

        bm._write_json(out_path, [], bm.channel_url)

        with open(out_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        assert data["metadata"]["total_messages"] == 0
        assert data["messages"] == []

    def test_write_json_utf8_encoding(self, tmp_path):
        """JSON file is written with UTF-8 encoding"""
        bm = BrowserMAX("https://web.max.ru/test-channel")
        messages = [
            {"text": "Привет мир 🚀", "sender": "Алиса", "timestamp": "2026-06-05T10:00:00", "direction": "out", "classes": "message", "attachments": [], "reactions": [], "is_reply": False}
        ]
        out_path = str(tmp_path / "export.json")

        bm._write_json(out_path, messages, bm.channel_url)

        with open(out_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        assert data["messages"][0]["text"] == "Привет мир 🚀"
        assert data["messages"][0]["sender"] == "Алиса"


# ─── _write_csv tests ───

class TestWriteCsv:
    """Test CSV output writing"""

    def test_write_csv_creates_file(self, tmp_path):
        """_write_csv creates a valid CSV file"""
        bm = BrowserMAX("https://web.max.ru/test-channel")
        messages = [
            {"text": "First message", "sender": "Alice", "timestamp": "2026-06-05T10:00:00", "direction": "out", "classes": "message message--out", "attachments": [], "reactions": [], "is_reply": False},
            {"text": "Second message", "sender": "Bob", "timestamp": "2026-06-05T11:00:00", "direction": "in", "classes": "message message--in", "attachments": [{"name": "file.zip"}], "reactions": [], "is_reply": False},
        ]
        out_path = str(tmp_path / "export.csv")

        bm._write_csv(out_path, messages)

        assert os.path.exists(out_path)
        with open(out_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        assert len(rows) == 2
        assert rows[0]["sender"] == "Alice"
        assert rows[1]["sender"] == "Bob"

    def test_write_csv_headers(self, tmp_path):
        """CSV has correct column headers"""
        bm = BrowserMAX("https://web.max.ru/test-channel")
        messages = [
            {"text": "Test", "sender": "Alice", "timestamp": "2026-06-05T10:00:00", "direction": "out", "classes": "message", "attachments": [], "reactions": [], "is_reply": False}
        ]
        out_path = str(tmp_path / "export.csv")

        bm._write_csv(out_path, messages)

        with open(out_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            headers = reader.fieldnames

        expected_headers = ["index", "sender", "timestamp", "direction", "text", "type", "attachments"]
        for h in expected_headers:
            assert h in headers, f"Missing header: {h}"

    def test_write_csv_no_html(self, tmp_path):
        """CSV does NOT include html column (too large)"""
        bm = BrowserMAX("https://web.max.ru/test-channel")
        messages = [
            {"text": "Test", "sender": "Alice", "timestamp": "2026-06-05T10:00:00", "direction": "out", "classes": "message", "attachments": [], "reactions": [], "is_reply": False}
        ]
        out_path = str(tmp_path / "export.csv")

        bm._write_csv(out_path, messages)

        with open(out_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            headers = reader.fieldnames

        assert "html" not in headers

    def test_write_csv_attachments_serialized(self, tmp_path):
        """CSV serializes attachments as JSON string"""
        bm = BrowserMAX("https://web.max.ru/test-channel")
        messages = [
            {
                "text": "File msg",
                "sender": "Alice",
                "timestamp": "2026-06-05T10:00:00",
                "direction": "out",
                "classes": "message message--out",
                "attachments": [{"name": "a.zip", "size": "10 MB"}, {"name": "b.zip", "size": "20 MB"}],
                "reactions": [],
                "is_reply": False,
            }
        ]
        out_path = str(tmp_path / "export.csv")

        bm._write_csv(out_path, messages)

        with open(out_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            row = next(reader)

        attach_data = json.loads(row["attachments"])
        assert len(attach_data) == 2
        assert attach_data[0]["name"] == "a.zip"

    def test_write_csv_utf8(self, tmp_path):
        """CSV handles UTF-8 content"""
        bm = BrowserMAX("https://web.max.ru/test-channel")
        messages = [
            {"text": "Русский текст 🇷🇺", "sender": "Иван", "timestamp": "2026-06-05T10:00:00", "direction": "out", "classes": "message", "attachments": [], "reactions": [], "is_reply": False}
        ]
        out_path = str(tmp_path / "export.csv")

        bm._write_csv(out_path, messages)

        with open(out_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            row = next(reader)

        assert row["text"] == "Русский текст 🇷🇺"


# ─── export_messages_to_file integration tests ───

class TestExportMessagesToFile:
    """Test the main export_messages_to_file method"""

    def _make_mock_page(self):
        """Helper to create a mock page"""
        page = MagicMock()
        page.is_closed.return_value = False
        return page

    def test_export_returns_count(self):
        """export_messages_to_file returns total message count"""
        bm = BrowserMAX("https://web.max.ru/test-channel")
        bm.page = self._make_mock_page()

        # Mock all dependencies
        with patch.object(bm, "_scroll_and_collect_full", return_value=[
            {"text": "Msg 1", "sender": "A", "timestamp": "2026-06-05T10:00:00", "direction": "out", "classes": "message", "attachments": [], "reactions": [], "is_reply": False},
        ]):
            with patch.object(bm, "_write_json") as mock_write:
                count = bm.export_messages_to_file(
                    output_path="/tmp/test_export.json",
                    format="json",
                )
                assert count == 1
                mock_write.assert_called_once()

    def test_export_json_format(self, tmp_path):
        """export uses _write_json when format=json"""
        bm = BrowserMAX("https://web.max.ru/test-channel")
        bm.page = self._make_mock_page()
        test_msgs = [
            {"text": "Msg", "sender": "A", "timestamp": "2026-06-05T10:00:00", "direction": "out", "classes": "message", "attachments": [], "reactions": [], "is_reply": False},
        ]

        with patch.object(bm, "_scroll_and_collect_full", return_value=test_msgs):
            with patch.object(bm, "_write_json") as mock_json:
                with patch.object(bm, "_write_csv") as mock_csv:
                    bm.export_messages_to_file(
                        output_path=str(tmp_path / "out.json"),
                        format="json",
                    )
                    mock_json.assert_called_once()
                    mock_csv.assert_not_called()

    def test_export_csv_format(self, tmp_path):
        """export uses _write_csv when format=csv"""
        bm = BrowserMAX("https://web.max.ru/test-channel")
        bm.page = self._make_mock_page()
        test_msgs = [
            {"text": "Msg", "sender": "A", "timestamp": "2026-06-05T10:00:00", "direction": "out", "classes": "message", "attachments": [], "reactions": [], "is_reply": False},
        ]

        with patch.object(bm, "_scroll_and_collect_full", return_value=test_msgs):
            with patch.object(bm, "_write_json") as mock_json:
                with patch.object(bm, "_write_csv") as mock_csv:
                    bm.export_messages_to_file(
                        output_path=str(tmp_path / "out.csv"),
                        format="csv",
                    )
                    mock_csv.assert_called_once()
                    mock_json.assert_not_called()

    def test_export_default_output_path(self):
        """export uses default output path when not specified"""
        bm = BrowserMAX("https://web.max.ru/test-channel")
        bm.page = self._make_mock_page()

        with patch.object(bm, "_scroll_and_collect_full", return_value=[
            {"text": "Msg", "sender": "A", "timestamp": "2026-06-05T10:00:00", "direction": "out", "classes": "message", "attachments": [], "reactions": [], "is_reply": False},
        ]):
            with patch.object(bm, "_write_json") as mock_write:
                bm.export_messages_to_file()
                call_args = mock_write.call_args
                assert call_args[0][0] == "messages_export.json"

    def test_export_empty_messages_returns_zero(self):
        """export returns 0 when no messages collected"""
        bm = BrowserMAX("https://web.max.ru/test-channel")
        bm.page = self._make_mock_page()

        with patch.object(bm, "_scroll_and_collect_full", return_value=[]):
            with patch.object(bm, "_write_json") as mock_write:
                count = bm.export_messages_to_file()
                assert count == 0
                mock_write.assert_not_called()  # Should not write empty file

    def test_export_max_messages_limit(self):
        """export respects max_messages limit"""
        bm = BrowserMAX("https://web.max.ru/test-channel")
        bm.page = self._make_mock_page()
        all_msgs = [
            {"text": f"Msg {i}", "sender": "A", "timestamp": "2026-06-05T10:00:00", "direction": "out", "classes": "message", "attachments": [], "reactions": [], "is_reply": False}
            for i in range(100)
        ]

        with patch.object(bm, "_scroll_and_collect_full", return_value=all_msgs):
            with patch.object(bm, "_write_json") as mock_write:
                bm.export_messages_to_file(max_messages=10)
                # The messages passed to _write_json should be limited
                written_msgs = mock_write.call_args[0][1]
                assert len(written_msgs) == 10

    def test_export_reindexes_messages(self):
        """export re-indexes messages sequentially"""
        bm = BrowserMAX("https://web.max.ru/test-channel")
        bm.page = self._make_mock_page()
        msgs = [
            {"text": "First", "sender": "A", "timestamp": "2026-06-05T10:00:00", "direction": "out", "classes": "message", "attachments": [], "reactions": [], "is_reply": False},
            {"text": "Second", "sender": "B", "timestamp": "2026-06-05T11:00:00", "direction": "in", "classes": "message", "attachments": [], "reactions": [], "is_reply": False},
        ]

        with patch.object(bm, "_scroll_and_collect_full", return_value=msgs):
            with patch.object(bm, "_write_json") as mock_write:
                bm.export_messages_to_file()
                written = mock_write.call_args[0][1]
                assert written[0].get("index") == 0
                assert written[1].get("index") == 1

    def test_export_html_inclusion_flag(self):
        """export strips HTML when include_html=False"""
        bm = BrowserMAX("https://web.max.ru/test-channel")
        bm.page = self._make_mock_page()
        msgs = [
            {"text": "Msg", "html": "<div>large html</div>", "sender": "A", "timestamp": "2026-06-05T10:00:00", "direction": "out", "classes": "message", "attachments": [], "reactions": [], "is_reply": False},
        ]

        with patch.object(bm, "_scroll_and_collect_full", return_value=msgs):
            with patch.object(bm, "_write_json") as mock_write:
                bm.export_messages_to_file(include_html=False)
                written = mock_write.call_args[0][1]
                assert written[0].get("html", "") == ""
