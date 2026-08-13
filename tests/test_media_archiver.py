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
        with pytest.raises(ConfigurationError, match="Папка для медиа не указана"):
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


class TestMediaJournalFileIndex:
    """Tests for new file_index-based journal structure"""

    def test_file_index_replaces_entries(self, tmp_path):
        """file_index dict replaces flat entries list"""
        from media_archiver import MediaJournal
        j = MediaJournal(str(tmp_path / "journal.json"))
        j.mark_sent("photo.jpg", 1024)
        assert "photo.jpg|1024" in j.file_index
        assert j.file_index["photo.jpg|1024"]["status"] == "sent"

    def test_is_sent_by_relative_path_and_size(self, tmp_path):
        """is_sent() checks file_index by rel_path + size"""
        from media_archiver import MediaJournal
        j = MediaJournal(str(tmp_path / "journal.json"))
        j.mark_sent("video.mp4", 5000)
        assert j.is_sent("video.mp4", 5000) is True
        assert j.is_sent("video.mp4", 6000) is False  # different size
        assert j.is_sent("other.mp4", 5000) is False  # different path

    def test_mark_failed_records_status(self, tmp_path):
        """mark_failed() records failed status"""
        from media_archiver import MediaJournal
        j = MediaJournal(str(tmp_path / "journal.json"))
        j.mark_failed("photo.jpg", 2048, "sess_001")
        entry = j.file_index.get("photo.jpg|2048")
        assert entry is not None
        assert entry["status"] == "failed"
        assert "sess_001" in entry["failed_sessions"]


class TestMediaJournalSessionTracking:
    """Tests for session tracking"""

    def test_start_session_creates_session(self, tmp_path):
        """start_session() adds session to sessions dict"""
        from media_archiver import MediaJournal
        j = MediaJournal(str(tmp_path / "journal.json"))
        j.start_session("sess_test")
        assert "sess_test" in j.sessions

    def test_end_session_records_stats(self, tmp_path):
        """end_session() records file count and bytes"""
        from media_archiver import MediaJournal
        j = MediaJournal(str(tmp_path / "journal.json"))
        j.start_session("sess_test")
        j.mark_sent("a.jpg", 1000)
        j.mark_sent("b.jpg", 2000)
        j.end_session("sess_test", "completed", 3000)
        sess = j.sessions["sess_test"]
        assert sess["files_processed"] == 2
        assert sess["bytes_sent"] == 3000
        assert sess["status"] == "completed"

    def test_session_persists_to_disk(self, tmp_path):
        """Session data survives journal reload"""
        from media_archiver import MediaJournal
        fp = tmp_path / "journal.json"
        j = MediaJournal(str(fp))
        j.start_session("sess_001")
        j.mark_sent("x.jpg", 5000)
        j.end_session("sess_001", "completed", 5000)
        j2 = MediaJournal(str(fp))
        assert "sess_001" in j2.sessions
        assert j2.sessions["sess_001"]["bytes_sent"] == 5000


class TestMediaJournalBatchTracking:
    """Tests for batch (subdirectory) tracking"""

    def test_update_batch_tracks_bytes(self, tmp_path):
        """update_batch() accumulates total and sent bytes"""
        from media_archiver import MediaJournal
        j = MediaJournal(str(tmp_path / "journal.json"))
        j.update_batch("Photos/2024", 1000, sent=True)
        j.update_batch("Photos/2024", 2000, sent=True)
        j.update_batch("Photos/2024", 500, sent=False)
        b = j.batches["Photos/2024"]
        assert b["total_bytes"] == 3500
        assert b["sent_bytes"] == 3000

    def test_batch_completion_flag(self, tmp_path):
        """mark_batch_complete() sets completed flag"""
        from media_archiver import MediaJournal
        j = MediaJournal(str(tmp_path / "journal.json"))
        j.update_batch("Videos", 5000, sent=True)
        j.mark_batch_complete("Videos")
        assert j.batches["Videos"]["completed"] is True


class TestMediaJournalProgress:
    """Tests for progress calculation"""

    def test_get_progress_returns_stats(self, tmp_path):
        """get_progress() returns correct byte statistics"""
        from media_archiver import MediaJournal
        j = MediaJournal(str(tmp_path / "journal.json"))
        j.mark_sent("a.jpg", 1000)
        j.mark_sent("b.jpg", 2000)
        j.mark_failed("c.jpg", 500, "s1")
        prog = j.get_progress()
        assert prog["total_files"] == 3
        assert prog["sent_files"] == 2
        assert prog["sent_bytes"] == 3000
        assert prog["failed_files"] == 1

    def test_progress_percentage(self, tmp_path):
        """Progress percentage is calculated correctly"""
        from media_archiver import MediaJournal
        j = MediaJournal(str(tmp_path / "journal.json"))
        j.mark_sent("a.jpg", 1000)
        j.mark_sent("b.jpg", 1000)
        j.mark_failed("c.jpg", 1000, "s1")
        prog = j.get_progress()
        assert prog["percent"] == pytest.approx(33.33, abs=0.1)


class TestMediaJournalLegacyMigration:
    """Tests for old-format to new-format migration"""

    def test_old_entries_migrate_to_file_index(self, tmp_path):
        """Old entries list migrates to file_index dict on load"""
        from media_archiver import MediaJournal
        fp = tmp_path / "journal.json"
        # Write old format
        old_data = {
            "entries": [
                {"path": "old.jpg", "size": 3000, "status": "sent", "timestamp": "2025-01-01"},
                {"path": "old2.jpg", "size": 4000, "status": "sent", "timestamp": "2025-01-02"}
            ],
            "total_processed": 2
        }
        with open(fp, "w") as f:
            json.dump(old_data, f)
        # Load - should migrate
        j = MediaJournal(str(fp))
        assert "old.jpg|3000" in j.file_index
        assert "old2.jpg|4000" in j.file_index
        assert len(j.entries) == 0  # old entries cleared

    def test_migration_preserves_sent_status(self, tmp_path):
        """Migration preserves sent/failed status from old format"""
        from media_archiver import MediaJournal
        fp = tmp_path / "journal.json"
        old_data = {
            "entries": [
                {"path": "ok.jpg", "size": 100, "status": "sent"},
                {"path": "bad.jpg", "size": 200, "status": "failed"}
            ]
        }
        with open(fp, "w") as f:
            json.dump(old_data, f)
        j = MediaJournal(str(fp))
        assert j.is_sent("ok.jpg", 100) is True
        assert j.is_sent("bad.jpg", 200) is False
