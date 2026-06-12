"""
Unit tests for NpmJournal class.

Tests cover:
- Initialization and empty journal structure
- add() new package entry
- add() deduplication — same (name, version) blocked
- exists() check
- get() latest entry by name
- get_all() returns all entries
- get_stats() counters
- update() existing entry
- mark_failed() adds failed entry
- clear() resets journal
- Corrupted JSON recovery
"""

import json
import os
import pytest
from unittest.mock import patch, MagicMock


class TestNpmJournalInit:
    """Test journal initialization"""

    def test_init_creates_empty(self, tmp_path):
        """Test new journal creates empty structure"""
        from npm_journal import NpmJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        journal = NpmJournal(journal_path)
        assert journal.data == {"packages": []}

    def test_init_loads_existing(self, tmp_path):
        """Test init loads existing journal file"""
        from npm_journal import NpmJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        existing = {"packages": [
            {"name": "express", "version": "4.21.0", "status": "sent"}
        ]}
        with open(journal_path, 'w', encoding='utf-8') as f:
            json.dump(existing, f)

        journal = NpmJournal(journal_path)
        assert len(journal.data["packages"]) == 1
        assert journal.data["packages"][0]["name"] == "express"

    def test_init_handles_corrupted_json(self, tmp_path):
        """Test init handles corrupted JSON by creating backup"""
        from npm_journal import NpmJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        with open(journal_path, 'w', encoding='utf-8') as f:
            f.write("not valid json{{{")

        journal = NpmJournal(journal_path)
        assert journal.data == {"packages": []}
        assert os.path.exists(journal_path + ".backup")

    def test_logger_property(self):
        """Test logger property returns correct logger"""
        from npm_journal import NpmJournal
        journal = NpmJournal("test_logger_journal.json")
        assert journal.logger.name == "gitax"
        journal.clear()
        os.remove("test_logger_journal.json")


class TestNpmJournalAdd:
    """Test add() method"""

    def test_add_new_entry(self, tmp_path):
        """Test adding a new package entry"""
        from npm_journal import NpmJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        journal = NpmJournal(journal_path)

        result = journal.add(
            name="express",
            version="4.21.0",
            description="Fast web framework",
            downloads=12345678,
            files=["express-4.21.0.tgz"]
        )
        assert result is True
        assert len(journal.data["packages"]) == 1
        assert journal.data["packages"][0]["name"] == "express"
        assert journal.data["packages"][0]["version"] == "4.21.0"
        assert journal.data["packages"][0]["status"] == "sent"
        assert "sent_at" in journal.data["packages"][0]

    def test_add_duplicate_blocked(self, tmp_path):
        """Test adding same (name, version) is blocked"""
        from npm_journal import NpmJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        journal = NpmJournal(journal_path)

        journal.add("express", "4.21.0", "desc", 100, ["file.tgz"])
        result = journal.add("express", "4.21.0", "desc", 100, ["file.tgz"])
        assert result is False
        assert len(journal.data["packages"]) == 1

    def test_add_same_name_different_version(self, tmp_path):
        """Test adding same name but different version is allowed"""
        from npm_journal import NpmJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        journal = NpmJournal(journal_path)

        journal.add("express", "4.21.0", "desc", 100, ["file.tgz"])
        result = journal.add("express", "5.0.0", "desc", 200, ["file.tgz"])
        assert result is True
        assert len(journal.data["packages"]) == 2


class TestNpmJournalExists:
    """Test exists() method"""

    def test_exists_returns_true(self, tmp_path):
        """Test exists returns True for existing entry"""
        from npm_journal import NpmJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        journal = NpmJournal(journal_path)
        journal.add("express", "4.21.0", "desc", 100, [])

        assert journal.exists("express", "4.21.0") is True

    def test_exists_returns_false(self, tmp_path):
        """Test exists returns False for missing entry"""
        from npm_journal import NpmJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        journal = NpmJournal(journal_path)

        assert journal.exists("nonexistent", "1.0.0") is False
        assert journal.exists("express", "9.9.9") is False


class TestNpmJournalGet:
    """Test get() method"""

    def test_get_latest_version(self, tmp_path):
        """Test get returns latest version of a package"""
        from npm_journal import NpmJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        journal = NpmJournal(journal_path)
        journal.add("express", "4.20.0", "desc", 100, [])
        journal.add("express", "4.21.0", "desc", 200, [])

        result = journal.get("express")
        assert result is not None
        assert result["version"] == "4.21.0"

    def test_get_returns_none_for_missing(self, tmp_path):
        """Test get returns None for unknown package"""
        from npm_journal import NpmJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        journal = NpmJournal(journal_path)

        assert journal.get("nonexistent") is None


class TestNpmJournalStats:
    """Test get_stats() and get_count() methods"""

    def test_get_stats_empty(self, tmp_path):
        """Test stats for empty journal"""
        from npm_journal import NpmJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        journal = NpmJournal(journal_path)

        stats = journal.get_stats()
        assert stats["total"] == 0
        assert stats["sent"] == 0
        assert stats["failed"] == 0

    def test_get_stats_with_entries(self, tmp_path):
        """Test stats with mixed entries"""
        from npm_journal import NpmJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        journal = NpmJournal(journal_path)
        journal.add("express", "4.21.0", "desc", 100, [])
        journal.mark_failed("bad-pkg", "1.0.0", "error")

        stats = journal.get_stats()
        assert stats["total"] == 2
        assert stats["sent"] == 1
        assert stats["failed"] == 1

    def test_get_count(self, tmp_path):
        """Test get_count returns correct count"""
        from npm_journal import NpmJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        journal = NpmJournal(journal_path)
        assert journal.get_count() == 0
        journal.add("a", "1", "", 0, [])
        assert journal.get_count() == 1
        journal.add("b", "2", "", 0, [])
        assert journal.get_count() == 2

    def test_get_all(self, tmp_path):
        """Test get_all returns all entries"""
        from npm_journal import NpmJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        journal = NpmJournal(journal_path)
        journal.add("a", "1", "", 0, [])
        journal.add("b", "2", "", 0, [])
        all_entries = journal.get_all()
        assert len(all_entries) == 2


class TestNpmJournalUpdate:
    """Test update() method"""

    def test_update_existing(self, tmp_path):
        """Test updating an existing entry"""
        from npm_journal import NpmJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        journal = NpmJournal(journal_path)
        journal.add("express", "4.21.0", "desc", 100, [])

        result = journal.update("express", "4.21.0", {"status": "updated"})
        assert result is True
        entry = journal.get("express")
        assert entry["status"] == "updated"
        assert "updated_at" in entry

    def test_update_missing(self, tmp_path):
        """Test updating a non-existent entry returns False"""
        from npm_journal import NpmJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        journal = NpmJournal(journal_path)

        result = journal.update("nonexistent", "1.0", {"status": "x"})
        assert result is False


class TestNpmJournalClear:
    """Test clear() method"""

    def test_clear(self, tmp_path):
        """Test clear resets journal"""
        from npm_journal import NpmJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        journal = NpmJournal(journal_path)
        journal.add("express", "4.21.0", "desc", 100, [])

        journal.clear()
        assert journal.data == {"packages": []}
        assert journal.get_count() == 0


class TestNpmJournalMarkFailed:
    """Test mark_failed() method"""

    def test_mark_failed(self, tmp_path):
        """Test marking a package as failed"""
        from npm_journal import NpmJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        journal = NpmJournal(journal_path)
        journal.mark_failed("broken-pkg", "0.1", "Download error")

        assert journal.exists("broken-pkg", "0.1")
        entry = journal.get("broken-pkg")
        assert entry["status"] == "failed"
        assert entry["files"] == []
