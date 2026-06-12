"""Unit tests for BackuperJournal class."""

import json
import os
import pytest


class TestBackuperJournalInit:
    """Test journal initialization"""

    def test_init_creates_empty(self, tmp_path):
        from backuper_journal import BackuperJournal
        j = BackuperJournal(str(tmp_path / "j.json"))
        assert j.data == {"backups": [], "downloads": [], "password_protected": [], "password_hints": {}, "files": {}}

    def test_init_loads_existing(self, tmp_path):
        from backuper_journal import BackuperJournal
        jp = str(tmp_path / "j.json")
        with open(jp, 'w', encoding='utf-8') as f:
            json.dump({
                "backups": [{"archive_name": "docs", "status": "uploaded"}],
                "downloads": [],
                "password_protected": ["docs"],
                "password_hints": {"docs": "my hint"},
                "files": {}
            }, f)
        j = BackuperJournal(jp)
        assert len(j.data["backups"]) == 1
        assert j.has_password("docs") is True
        assert j.get_password_hint("docs") == "my hint"

    def test_init_handles_corrupted_json(self, tmp_path):
        from backuper_journal import BackuperJournal
        jp = str(tmp_path / "j.json")
        with open(jp, 'w') as f:
            f.write("invalid{{{")
        j = BackuperJournal(jp)
        assert j.data == {"backups": [], "downloads": [], "password_protected": [], "password_hints": {}, "files": {}}
        assert os.path.exists(jp + ".backup")

    def test_logger_property(self):
        from backuper_journal import BackuperJournal
        j = BackuperJournal("test_logger_journal.json")
        assert j.logger.name == "gitax"
        j.clear()
        os.remove("test_logger_journal.json")


class TestBackuperJournalAddBackup:
    """Test add_backup() method"""

    def test_add_new_backup(self, tmp_path):
        from backuper_journal import BackuperJournal
        j = BackuperJournal(str(tmp_path / "j.json"))
        assert j.add_backup({
            "archive_name": "docs",
            "content_hash": "sha256:abc",
            "status": "uploaded"
        }) is True
        assert len(j.data["backups"]) == 1
        assert "created_at" in j.data["backups"][0]

    def test_add_no_name_returns_false(self, tmp_path):
        from backuper_journal import BackuperJournal
        j = BackuperJournal(str(tmp_path / "j.json"))
        assert j.add_backup({"status": "uploaded"}) is False

    def test_add_duplicate_hash_returns_false(self, tmp_path):
        from backuper_journal import BackuperJournal
        j = BackuperJournal(str(tmp_path / "j.json"))
        j.add_backup({"archive_name": "docs", "content_hash": "sha256:abc", "status": "uploaded"})
        assert j.add_backup({
            "archive_name": "docs2",
            "content_hash": "sha256:abc",
            "status": "uploaded"
        }) is False
        assert len(j.data["backups"]) == 1

    def test_add_failed_backup(self, tmp_path):
        from backuper_journal import BackuperJournal
        j = BackuperJournal(str(tmp_path / "j.json"))
        j.add_backup({"archive_name": "broken", "status": "failed"})
        assert j.data["backups"][0]["status"] == "failed"


class TestBackuperJournalPasswords:
    """Test password protection flags and hints (passwords are NOT stored)"""

    def test_mark_and_check(self, tmp_path):
        from backuper_journal import BackuperJournal
        j = BackuperJournal(str(tmp_path / "j.json"))
        j.mark_password_protected("docs")
        assert j.has_password("docs") is True
        assert j.has_password("unknown") is False

    def test_hint_storage(self, tmp_path):
        from backuper_journal import BackuperJournal
        j = BackuperJournal(str(tmp_path / "j.json"))
        j.store_password_hint("docs", "my email")
        assert j.get_password_hint("docs") == "my email"
        assert j.get_password_hint("unknown") is None

    def test_mark_idempotent(self, tmp_path):
        from backuper_journal import BackuperJournal
        j = BackuperJournal(str(tmp_path / "j.json"))
        j.mark_password_protected("docs")
        j.mark_password_protected("docs")
        protected = j.data.get("password_protected", [])
        assert protected.count("docs") == 1

    def test_hint_survives_save_load(self, tmp_path):
        from backuper_journal import BackuperJournal
        jp = str(tmp_path / "j.json")
        j = BackuperJournal(jp)
        j.mark_password_protected("docs")
        j.store_password_hint("docs", "my email")
        j2 = BackuperJournal(jp)
        assert j2.has_password("docs") is True
        assert j2.get_password_hint("docs") == "my email"


class TestBackuperJournalHash:
    """Test content hashing"""

    def test_compute_hash_same_content(self, tmp_path):
        from backuper_journal import BackuperJournal
        j = BackuperJournal(str(tmp_path / "j.json"))
        d = tmp_path / "dir"
        d.mkdir()
        (d / "f.txt").write_text("hello")
        assert j.compute_content_hash(str(d)) == j.compute_content_hash(str(d))
        assert j.compute_content_hash(str(d)).startswith("sha256:")

    def test_compute_hash_different_content(self, tmp_path):
        from backuper_journal import BackuperJournal
        j = BackuperJournal(str(tmp_path / "j.json"))
        d1 = tmp_path / "d1"
        d1.mkdir()
        (d1 / "a.txt").write_text("a")
        d2 = tmp_path / "d2"
        d2.mkdir()
        (d2 / "b.txt").write_text("b")
        assert j.compute_content_hash(str(d1)) != j.compute_content_hash(str(d2))

    def test_compute_hash_nonexistent(self, tmp_path):
        from backuper_journal import BackuperJournal
        j = BackuperJournal(str(tmp_path / "j.json"))
        h = j.compute_content_hash("/nonexistent/path")
        assert h.startswith("sha256:")

    def test_is_duplicate_by_hash(self, tmp_path):
        from backuper_journal import BackuperJournal
        j = BackuperJournal(str(tmp_path / "j.json"))
        j.add_backup({"archive_name": "docs", "content_hash": "sha256:abc", "status": "uploaded"})
        assert j.is_duplicate_by_hash("sha256:abc") is True
        assert j.is_duplicate_by_hash("sha256:xyz") is False
        assert j.is_duplicate_by_hash("") is False

    def test_failed_not_duplicate(self, tmp_path):
        """Failed backup should not block re-upload"""
        from backuper_journal import BackuperJournal
        j = BackuperJournal(str(tmp_path / "j.json"))
        j.add_backup({"archive_name": "docs", "content_hash": "sha256:abc", "status": "failed"})
        assert j.is_duplicate_by_hash("sha256:abc") is False


class TestBackuperJournalQuery:
    """Test query methods"""

    def test_get_backup_latest(self, tmp_path):
        from backuper_journal import BackuperJournal
        j = BackuperJournal(str(tmp_path / "j.json"))
        j.add_backup({"archive_name": "docs", "created_at": "2026-01-01T00:00:00", "status": "uploaded"})
        j.add_backup({"archive_name": "docs", "created_at": "2026-06-01T00:00:00", "status": "uploaded"})
        b = j.get_backup("docs")
        assert b["created_at"] == "2026-06-01T00:00:00"

    def test_get_backup_none(self, tmp_path):
        from backuper_journal import BackuperJournal
        j = BackuperJournal(str(tmp_path / "j.json"))
        assert j.get_backup("unknown") is None

    def test_get_all_backups(self, tmp_path):
        from backuper_journal import BackuperJournal
        j = BackuperJournal(str(tmp_path / "j.json"))
        j.add_backup({"archive_name": "docs", "status": "uploaded"})
        j.add_backup({"archive_name": "photos", "status": "uploaded"})
        assert len(j.get_all_backups()) == 2

    def test_get_count(self, tmp_path):
        from backuper_journal import BackuperJournal
        j = BackuperJournal(str(tmp_path / "j.json"))
        assert j.get_count() == 0
        j.add_backup({"archive_name": "docs", "status": "uploaded"})
        assert j.get_count() == 1


class TestBackuperJournalStats:
    """Test statistics"""

    def test_stats(self, tmp_path):
        from backuper_journal import BackuperJournal
        j = BackuperJournal(str(tmp_path / "j.json"))
        j.add_backup({"archive_name": "docs", "status": "uploaded"})
        j.add_backup({"archive_name": "photos", "status": "failed"})
        j.add_download({"archive_name": "docs", "status": "completed"})
        j.mark_password_protected("docs")
        s = j.get_stats()
        assert s["total_backups"] == 2
        assert s["uploaded"] == 1
        assert s["failed"] == 1
        assert s["total_downloads"] == 1
        assert s["completed_downloads"] == 1
        assert s["password_protected"] == 1


class TestBackuperJournalClear:
    """Test clear()"""

    def test_clear_resets(self, tmp_path):
        from backuper_journal import BackuperJournal
        j = BackuperJournal(str(tmp_path / "j.json"))
        j.add_backup({"archive_name": "docs", "status": "uploaded"})
        j.mark_password_protected("docs")
        j.clear()
        assert j.data == {"backups": [], "downloads": [], "password_protected": [], "password_hints": {}, "files": {}}

    def test_clear_persists(self, tmp_path):
        from backuper_journal import BackuperJournal
        jp = str(tmp_path / "j.json")
        j = BackuperJournal(jp)
        j.add_backup({"archive_name": "docs", "status": "uploaded"})
        j.clear()
        j2 = BackuperJournal(jp)
        assert j2.get_count() == 0
