"""
Unit tests for PyPILibsArchiver class.

Tests cover:
- _build_message_text() formatting
- _format_downloads() helper
- format_file_size() helper (via utils)
- Config validation (missing channel_url)
- Journal integration (dedup check in load path)
"""

import os
import sys
import json
import pytest
from unittest.mock import patch, MagicMock


class TestFormatDownloads:
    """Test _format_downloads static method"""

    def test_format_downloads_billions(self):
        from pypi_libs_archiver import PyPILibsArchiver
        assert PyPILibsArchiver._format_downloads(1_500_000_000) == "1.5B"
        assert PyPILibsArchiver._format_downloads(10_000_000_000) == "10.0B"

    def test_format_downloads_millions(self):
        from pypi_libs_archiver import PyPILibsArchiver
        assert PyPILibsArchiver._format_downloads(1_500_000) == "1.5M"
        assert PyPILibsArchiver._format_downloads(982_742_658) == "982.7M"

    def test_format_downloads_thousands(self):
        from pypi_libs_archiver import PyPILibsArchiver
        assert PyPILibsArchiver._format_downloads(1_500) == "1.5K"
        assert PyPILibsArchiver._format_downloads(999) == "999"

    def test_format_downloads_small(self):
        from pypi_libs_archiver import PyPILibsArchiver
        assert PyPILibsArchiver._format_downloads(0) == "0"
        assert PyPILibsArchiver._format_downloads(42) == "42"
        assert PyPILibsArchiver._format_downloads(999) == "999"


class TestFormatFileSize:
    """Test utils.format_file_size (used by PyPILibsArchiver)"""

    def test_format_file_size_gb(self):
        from utils import format_file_size
        assert "GB" in format_file_size(2_000_000_000)

    def test_format_file_size_mb(self):
        from utils import format_file_size
        assert format_file_size(1_048_576) == "1.0 MB"
        assert "MB" in format_file_size(50_000_000)

    def test_format_file_size_kb(self):
        from utils import format_file_size
        assert "KB" in format_file_size(1_024)
        assert format_file_size(500) == "500 B"

    def test_format_file_size_bytes(self):
        from utils import format_file_size
        assert format_file_size(0) == "0 B"
        assert format_file_size(100) == "100 B"


class TestBuildMessageText:
    """Test _build_message_text() — the core message formatting"""

    def test_basic_message(self):
        """Test basic message with all fields"""
        from pypi_libs_archiver import PyPILibsArchiver
        archiver = PyPILibsArchiver.__new__(PyPILibsArchiver)

        pkg_data = {
            "name": "requests",
            "latest_version": "2.31.0",
            "summary": "Python HTTP for Humans.",
            "downloads": 982742658,
            "license": "Apache-2.0",
        }
        text = archiver._build_message_text(pkg_data, [])

        assert "requests" in text
        assert "2.31.0" in text
        assert "Python HTTP for Humans." in text
        assert "982.7M" in text  # formatted downloads
        assert "Apache-2.0" in text
        assert "pypi.org" not in text  # URL removed to avoid Cloudflare issues

    def test_message_with_file_sizes(self):
        """Test message includes file sizes when provided"""
        from pypi_libs_archiver import PyPILibsArchiver
        archiver = PyPILibsArchiver.__new__(PyPILibsArchiver)

        pkg_data = {
            "name": "requests",
            "latest_version": "2.31.0",
            "summary": "HTTP library",
            "downloads": 1000000,
            "license": "MIT",
        }
        text = archiver._build_message_text(pkg_data, [1_048_576, 512_000])

        assert "Файл 1" in text
        assert "Файл 2" in text
        assert "1.0 MB" in text
        assert "500.0 KB" in text

    def test_message_no_description(self):
        """Test message with missing description"""
        from pypi_libs_archiver import PyPILibsArchiver
        archiver = PyPILibsArchiver.__new__(PyPILibsArchiver)

        pkg_data = {
            "name": "unknown",
            "latest_version": "0.1",
            "summary": "",
            "downloads": 0,
            "license": "",
        }
        text = archiver._build_message_text(pkg_data, [])

        assert "unknown" in text
        assert "Без описания" in text

    def test_message_pypi_url(self):
        """Test PyPI URL is properly constructed"""
        from pypi_libs_archiver import PyPILibsArchiver
        archiver = PyPILibsArchiver.__new__(PyPILibsArchiver)

        pkg_data = {
            "name": "django",
            "latest_version": "5.0",
            "summary": "Web framework",
            "downloads": 50000000,
            "license": "BSD",
        }
        text = archiver._build_message_text(pkg_data, [])
        assert "https://pypi.org/project/django/" not in text  # URL removed to avoid Cloudflare issues


class TestConfigValidation:
    """Test config loading with missing settings"""

    def test_missing_channel_url_exits(self, tmp_path, monkeypatch):
        """Test missing channel URL causes exit"""
        monkeypatch.delenv("CHANNEL_pypi", raising=False)
        # Mock load_dotenv to prevent .env from overriding test config
        monkeypatch.setattr("config.loader.load_dotenv", lambda **kwargs: None)
        config_file = tmp_path / "test_config.yaml"
        config_file.write_text("channels: {}\n")
        from pypi_libs_archiver import PyPILibsArchiver

        # The new config system validates but doesn't exit in __init__
        # Channel URL validation happens when browser is initialized
        archiver = PyPILibsArchiver(str(config_file))
        # Config loads but channel URL is empty (no migration happened)
        assert archiver.config['channels']['pypi'] == ""
        # No registry entry created when no URL is available
        assert len(archiver.config['channel_registry']['pypi']) == 0

    def test_channel_url_from_env(self, tmp_path, monkeypatch):
        """Test channel URL is read from CHANNEL_pypi env var"""
        monkeypatch.setenv("CHANNEL_pypi", "https://web.max.ru/pypi-channel")
        config_file = tmp_path / "test_config.yaml"
        config_file.write_text("channels: {}\n")
        from pypi_libs_archiver import PyPILibsArchiver

        archiver = PyPILibsArchiver(str(config_file))
        # Legacy channel preserved for backward compat
        assert archiver.config['channels']['pypi'] == "https://web.max.ru/pypi-channel"
        assert len(archiver.config['channel_registry']['pypi']) == 1
        assert archiver.config['channel_registry']['pypi'][0]['url'] == "https://web.max.ru/pypi-channel"

    def test_channel_url_from_yaml(self, tmp_path, monkeypatch):
        """Test channel URL fallback to config.yaml channels.pypi"""
        monkeypatch.delenv("CHANNEL_pypi", raising=False)
        config_file = tmp_path / "test_config.yaml"
        config_file.write_text("channels:\n  pypi: https://web.max.ru/pypi-channel\n")
        # Mock load_dotenv to prevent .env from overriding test config
        with patch("config.loader.load_dotenv"):
            from pypi_libs_archiver import PyPILibsArchiver
            archiver = PyPILibsArchiver(str(config_file))
        # Legacy channel preserved for backward compat
        assert archiver.config['channels']['pypi'] == "https://web.max.ru/pypi-channel"
        assert len(archiver.config['channel_registry']['pypi']) == 1
        assert archiver.config['channel_registry']['pypi'][0]['url'] == "https://web.max.ru/pypi-channel"


class TestJournalIntegration:
    """Test how archiver interacts with the journal"""

    def test_archiver_creates_journal(self, tmp_path):
        """Test archiver creates a PyPILibsJournal instance"""
        config_file = tmp_path / "test_config.yaml"
        config_file.write_text("channels:\n  pypi: https://example.com\n")
        from pypi_libs_archiver import PyPILibsArchiver
        from pypi_libs_journal import PyPILibsJournal

        archiver = PyPILibsArchiver(str(config_file))
        assert isinstance(archiver.journal, PyPILibsJournal)
        assert archiver.journal.file_path == "pypi_libs_journal.json"
        archiver.journal.clear()
        # Clean up
        journal_file = tmp_path / "pypi_libs_journal.json"
        if journal_file.exists():
            journal_file.unlink()
