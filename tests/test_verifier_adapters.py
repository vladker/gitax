"""
Unit tests for verifier adapters.

Tests cover:
- Protocol structure validation
- GitHub adapter key mapping (full_name ↔ filename)
- PyPI adapter key mapping (name-version ↔ filename)
- Backuper adapter key mapping (archive_name ↔ 7z filename)
- Media adapter key mapping (filename ↔ filename)
"""

import pytest
from unittest.mock import MagicMock
from verifier.models import VerifierMode, ChannelFile

# Protocol tests
from verifier.adapters import ChannelAdapter, JournalAdapter

# Concrete adapter tests
from verifier.adapters_github import GitHubChannelAdapter, GitHubJournalAdapter
from verifier.adapters_pypi import PyPIChannelAdapter, PyPIJournalAdapter
from verifier.adapters_backuper import (
    BackuperChannelAdapter, BackuperJournalAdapter
)
from verifier.adapters_media import MediaChannelAdapter, MediaJournalAdapter


class TestChannelAdapterProtocol:
    """Test ChannelAdapter protocol structure."""

    def test_protocol_has_scan_files(self):
        import inspect
        members = dict(inspect.getmembers(ChannelAdapter))
        assert "scan_files" in members

    def test_protocol_has_navigate(self):
        import inspect
        members = dict(inspect.getmembers(ChannelAdapter))
        assert "navigate_to_channel" in members

    def test_protocol_has_incomplete(self):
        import inspect
        members = dict(inspect.getmembers(ChannelAdapter))
        assert "incomplete" in members


class TestJournalAdapterProtocol:
    """Test JournalAdapter protocol structure."""

    def test_protocol_has_get_entries(self):
        import inspect
        members = dict(inspect.getmembers(JournalAdapter))
        assert "get_entries" in members

    def test_protocol_has_expected_filename(self):
        import inspect
        members = dict(inspect.getmembers(JournalAdapter))
        assert "expected_filename" in members

    def test_protocol_has_entry_key(self):
        import inspect
        members = dict(inspect.getmembers(JournalAdapter))
        assert "entry_key" in members

    def test_protocol_has_channel_to_key(self):
        import inspect
        members = dict(inspect.getmembers(JournalAdapter))
        assert "channel_to_key" in members

    def test_protocol_has_remove_entry(self):
        import inspect
        members = dict(inspect.getmembers(JournalAdapter))
        assert "remove_entry" in members

    def test_protocol_has_get_stats(self):
        import inspect
        members = dict(inspect.getmembers(JournalAdapter))
        assert "get_stats" in members


class TestGitHubJournalAdapter:
    """Test GitHub adapter key mapping."""

    def setup_method(self):
        self.mock_journal = MagicMock()
        self.adapter = GitHubJournalAdapter(self.mock_journal)

    def test_entry_key(self):
        entry = {"full_name": "owner/repo"}
        assert self.adapter.entry_key(entry) == "owner/repo"

    def test_channel_to_key_zip(self):
        assert self.adapter.channel_to_key("owner-repo.zip") == "owner/repo"

    def test_channel_to_key_7z(self):
        assert self.adapter.channel_to_key("owner-repo.7z.001") == "owner/repo"

    def test_channel_to_key_7z_plain(self):
        assert self.adapter.channel_to_key("owner-repo.7z") == "owner/repo"

    def test_expected_filename_zip(self):
        entry = {"full_name": "owner/repo"}
        assert self.adapter.expected_filename(entry) == "owner-repo.zip"

    def test_expected_filename_7z_large(self):
        entry = {"full_name": "owner/repo", "archive_size": 100 * 1024 * 1024}
        result = self.adapter.expected_filename(entry)
        assert isinstance(result, list)
        assert "owner-repo.7z.001" in result

    def test_get_entries_filters_status(self):
        self.mock_journal.get_all_repositories.return_value = [
            {"full_name": "a", "status": "sent"},
            {"full_name": "b", "status": "failed"},
            {"full_name": "c", "status": "restored"},
            {"full_name": "d", "status": "incomplete"},
        ]
        entries = self.adapter.get_entries()
        keys = [e["full_name"] for e in entries]
        assert "a" in keys
        assert "b" not in keys
        assert "c" in keys
        assert "d" in keys

    def test_remove_entry(self):
        self.mock_journal.remove_repository.return_value = True
        assert self.adapter.remove_entry("owner/repo") is True
        self.mock_journal.remove_repository.assert_called_once_with("owner/repo")


class TestPyPIJournalAdapter:
    """Test PyPI adapter key mapping."""

    def setup_method(self):
        self.mock_journal = MagicMock()
        self.mock_journal.data = {"libraries": []}
        self.adapter = PyPIJournalAdapter(self.mock_journal)

    def test_entry_key(self):
        entry = {"name": "requests", "version": "2.31.0"}
        assert self.adapter.entry_key(entry) == "requests-2.31.0"

    def test_channel_to_key_tar_gz(self):
        assert (
            self.adapter.channel_to_key("requests-2.31.0.tar.gz")
            == "requests-2.31.0"
        )

    def test_channel_to_key_whl(self):
        assert (
            self.adapter.channel_to_key("requests-2.31.0-py3-none-any.whl")
            == "requests-2.31.0"
        )

    def test_channel_to_key_plain_whl(self):
        assert (
            self.adapter.channel_to_key("flask-3.0.0.whl")
            == "flask-3.0.0"
        )

    def test_expected_filename(self):
        entry = {"name": "requests", "version": "2.31.0"}
        result = self.adapter.expected_filename(entry)
        assert isinstance(result, list)
        assert "requests-2.31.0.tar.gz" in result
        assert "requests-2.31.0-py3-none-any.whl" in result

    def test_get_entries(self):
        self.mock_journal.get_all.return_value = [
            {"name": "a", "version": "1.0"},
            {"name": "b", "version": "2.0"},
        ]
        entries = self.adapter.get_entries()
        assert len(entries) == 2


class TestBackuperJournalAdapter:
    """Test Backuper adapter key mapping."""

    def setup_method(self):
        self.mock_journal = MagicMock()
        self.mock_journal.data = {"backups": []}
        self.adapter = BackuperJournalAdapter(self.mock_journal)

    def test_entry_key(self):
        entry = {"archive_name": "documents"}
        assert self.adapter.entry_key(entry) == "documents"

    def test_channel_to_key_7z(self):
        assert self.adapter.channel_to_key("documents.7z") == "documents"

    def test_channel_to_key_7z_volume(self):
        assert self.adapter.channel_to_key("documents.7z.003") == "documents"

    def test_channel_to_key_zip(self):
        assert self.adapter.channel_to_key("photos.zip") == "photos"

    def test_expected_filename_single(self):
        entry = {"archive_name": "docs"}
        assert self.adapter.expected_filename(entry) == "docs.7z"

    def test_expected_filename_multi_volume(self):
        entry = {"archive_name": "docs", "volume_count": 3}
        result = self.adapter.expected_filename(entry)
        assert isinstance(result, list)
        assert result == ["docs.7z.001", "docs.7z.002", "docs.7z.003"]

    def test_get_entries_filters_uploaded(self):
        self.mock_journal.get_all_backups.return_value = [
            {"archive_name": "a", "status": "uploaded"},
            {"archive_name": "b", "status": "failed"},
        ]
        entries = self.adapter.get_entries()
        assert len(entries) == 1
        assert entries[0]["archive_name"] == "a"


class TestMediaJournalAdapter:
    """Test Media adapter key mapping."""

    def setup_method(self):
        self.mock_journal = MagicMock()
        self.mock_journal.data = {"entries": []}
        self.adapter = MediaJournalAdapter(self.mock_journal)

    def test_entry_key(self):
        entry = {"filename": "photo.jpg"}
        assert self.adapter.entry_key(entry) == "photo.jpg"

    def test_channel_to_key(self):
        assert self.adapter.channel_to_key("photo.jpg") == "photo.jpg"

    def test_channel_to_key_empty(self):
        assert self.adapter.channel_to_key("") is None

    def test_expected_filename(self):
        entry = {"filename": "video.mp4"}
        assert self.adapter.expected_filename(entry) == "video.mp4"

    def test_get_entries(self):
        self.mock_journal.data["entries"] = [
            {"filename": "a.jpg"},
            {"filename": "b.png"},
        ]
        entries = self.adapter.get_entries()
        assert len(entries) == 2


class TestChannelAdapterCommon:
    """Test common channel adapter behavior."""

    def test_github_adapter_incomplete_default(self):
        browser = MagicMock()
        adapter = GitHubChannelAdapter(browser, "http://test")
        assert adapter.incomplete is False

    def test_pypi_adapter_incomplete_default(self):
        browser = MagicMock()
        adapter = PyPIChannelAdapter(browser, "http://test")
        assert adapter.incomplete is False

    def test_backuper_adapter_incomplete_default(self):
        browser = MagicMock()
        adapter = BackuperChannelAdapter(browser, "http://test")
        assert adapter.incomplete is False

    def test_media_adapter_incomplete_default(self):
        browser = MagicMock()
        adapter = MediaChannelAdapter(browser, "http://test")
        assert adapter.incomplete is False

    def test_github_navigate(self):
        browser = MagicMock()
        adapter = GitHubChannelAdapter(browser, "http://default")
        adapter.navigate_to_channel("http://override")
        browser.navigate.assert_called_once_with("http://override")
        browser.wait_page_ready.assert_called_once()

    def test_github_navigate_default(self):
        browser = MagicMock()
        adapter = GitHubChannelAdapter(browser, "http://default")
        adapter.navigate_to_channel()
        browser.navigate.assert_called_once_with("http://default")
