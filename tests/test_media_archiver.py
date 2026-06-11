# -*- coding: utf-8 -*-
"""
Tests for MediaJournal module — clear() method.
"""

import json
import os
import pytest


class TestMediaJournalClear:
    """Tests for MediaJournal.clear()"""

    def test_clear_resets_data(self, tmp_path):
        """clear() resets media journal to empty state"""
        from media_archiver import MediaJournal
        j = MediaJournal(str(tmp_path / "media_journal.json"))
        j.mark_sent("photo.jpg", 1024)
        assert j.get_stats()["total"] == 1
        j.clear()
        assert j.get_stats()["total"] == 0

    def test_clear_empties_file_on_disk(self, tmp_path):
        """clear() writes empty media journal to disk"""
        from media_archiver import MediaJournal
        fp = tmp_path / "media_journal.json"
        j = MediaJournal(str(fp))
        j.mark_sent("photo.jpg", 1024)
        j.clear()
        # Re-read from disk
        j2 = MediaJournal(str(fp))
        assert j2.get_stats()["total"] == 0

    def test_clear_on_empty_journal_does_not_crash(self, tmp_path):
        """clear() on empty media journal does not raise"""
        from media_archiver import MediaJournal
        j = MediaJournal(str(tmp_path / "media_journal.json"))
        j.clear()  # Should not raise


class TestMediaArchiverConfiguration:
    """Tests for MediaArchiver configuration validation"""

    def test_missing_watch_dir_raises_configuration_error(self, tmp_path, monkeypatch):
        """Missing MEDIA_WATCH_DIR raises ConfigurationError instead of sys.exit"""
        monkeypatch.delenv("MEDIA_WATCH_DIR", raising=False)
        monkeypatch.setattr("dotenv.load_dotenv", lambda: None)
        from utils import ConfigurationError
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "archiver:\n  large_file_threshold_mb: 50\n"
            "media_archiver:\n  watch_dir: ''\n"
            "channels:\n  media: ''\n"
            "backuper:\n  seven_zip_exe: ''\n"
            "setup:\n  skipped_channels: []\n"
            "channel_registry:\n  github: []\n  pypi: []\n  media: []\n  backup: []\n"
        )
        with pytest.raises(ConfigurationError, match="MEDIA_WATCH_DIR"):
            from media_archiver import MediaArchiver
            MediaArchiver(str(config_file))

    def test_invalid_watch_dir_raises_configuration_error(self, tmp_path, monkeypatch):
        """Non-existent watch_dir raises ConfigurationError"""
        monkeypatch.delenv("MEDIA_WATCH_DIR", raising=False)
        monkeypatch.setattr("dotenv.load_dotenv", lambda: None)
        from utils import ConfigurationError
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "archiver:\n  large_file_threshold_mb: 50\n"
            "media_archiver:\n  watch_dir: '/nonexistent/path'\n"
            "channels:\n  media: ''\n"
            "backuper:\n  seven_zip_exe: ''\n"
            "setup:\n  skipped_channels: []\n"
            "channel_registry:\n  github: []\n  pypi: []\n  media: []\n  backup: []\n"
        )
        with pytest.raises(ConfigurationError, match="Папка медиа не найдена"):
            from media_archiver import MediaArchiver
            MediaArchiver(str(config_file))

    def test_configuration_error_is_exception(self):
        """ConfigurationError is a proper Exception subclass"""
        from utils import ConfigurationError
        assert issubclass(ConfigurationError, Exception)
        with pytest.raises(ConfigurationError):
            raise ConfigurationError("test error")
