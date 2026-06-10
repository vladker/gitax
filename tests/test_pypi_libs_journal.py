"""
Unit tests for PyPILibsJournal class.

Tests cover:
- Initialization and empty journal structure
- add() new library entry
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
import time
import pytest
from datetime import datetime
from unittest.mock import patch, MagicMock


class TestPyPILibsJournalInit:
    """Test journal initialization"""

    def test_init_creates_empty(self, tmp_path):
        """Test new journal creates empty structure"""
        from pypi_libs_journal import PyPILibsJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        journal = PyPILibsJournal(journal_path)
        assert journal.data == {"libraries": []}

    def test_init_loads_existing(self, tmp_path):
        """Test init loads existing journal file"""
        from pypi_libs_journal import PyPILibsJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        # Pre-create journal
        existing = {"libraries": [
            {"name": "requests", "version": "2.31.0", "status": "sent"}
        ]}
        with open(journal_path, 'w', encoding='utf-8') as f:
            json.dump(existing, f)

        journal = PyPILibsJournal(journal_path)
        assert len(journal.data["libraries"]) == 1
        assert journal.data["libraries"][0]["name"] == "requests"

    def test_init_handles_corrupted_json(self, tmp_path):
        """Test init handles corrupted JSON by creating backup"""
        from pypi_libs_journal import PyPILibsJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        with open(journal_path, 'w', encoding='utf-8') as f:
            f.write("not valid json{{{")

        journal = PyPILibsJournal(journal_path)
        assert journal.data == {"libraries": []}
        # Backup should exist
        assert os.path.exists(journal_path + ".backup")

    def test_logger_property(self):
        """Test logger property returns correct logger"""
        from pypi_libs_journal import PyPILibsJournal
        journal = PyPILibsJournal("test_logger_journal.json")
        assert journal.logger.name == "gitax"
        journal.clear()
        os.remove("test_logger_journal.json")


class TestPyPILibsJournalAdd:
    """Test add() method"""

    def test_add_new_entry(self, tmp_path):
        """Test adding a new library entry"""
        from pypi_libs_journal import PyPILibsJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        journal = PyPILibsJournal(journal_path)

        result = journal.add(
            name="requests",
            version="2.31.0",
            description="HTTP for humans",
            downloads=982742658,
            files=["requests-2.31.0.tar.gz", "requests-2.31.0-py3-none-any.whl"]
        )
        assert result is True
        assert len(journal.data["libraries"]) == 1
        assert journal.data["libraries"][0]["name"] == "requests"
        assert journal.data["libraries"][0]["version"] == "2.31.0"
        assert journal.data["libraries"][0]["status"] == "sent"
        assert "sent_at" in journal.data["libraries"][0]

    def test_add_duplicate_blocked(self, tmp_path):
        """Test adding same (name, version) is blocked"""
        from pypi_libs_journal import PyPILibsJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        journal = PyPILibsJournal(journal_path)

        journal.add("requests", "2.31.0", "desc", 100, ["file.tar.gz"])
        result = journal.add("requests", "2.31.0", "desc", 100, ["file.tar.gz"])
        assert result is False
        assert len(journal.data["libraries"]) == 1

    def test_add_same_name_different_version(self, tmp_path):
        """Test adding same name but different version is allowed"""
        from pypi_libs_journal import PyPILibsJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        journal = PyPILibsJournal(journal_path)

        journal.add("requests", "2.31.0", "desc", 100, ["file.tar.gz"])
        result = journal.add("requests", "3.0.0", "desc", 200, ["file.tar.gz"])
        assert result is True
        assert len(journal.data["libraries"]) == 2


class TestPyPILibsJournalExists:
    """Test exists() method"""

    def test_exists_returns_true(self, tmp_path):
        """Test exists returns True for existing entry"""
        from pypi_libs_journal import PyPILibsJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        journal = PyPILibsJournal(journal_path)
        journal.add("requests", "2.31.0", "desc", 100, [])

        assert journal.exists("requests", "2.31.0") is True

    def test_exists_returns_false(self, tmp_path):
        """Test exists returns False for missing entry"""
        from pypi_libs_journal import PyPILibsJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        journal = PyPILibsJournal(journal_path)

        assert journal.exists("nonexistent", "1.0.0") is False
        assert journal.exists("requests", "9.9.9") is False


class TestPyPILibsJournalGet:
    """Test get() method"""

    def test_get_latest_version(self, tmp_path):
        """Test get returns latest version of a library"""
        from pypi_libs_journal import PyPILibsJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        journal = PyPILibsJournal(journal_path)
        journal.add("requests", "2.30.0", "desc", 100, [])
        journal.add("requests", "2.31.0", "desc", 200, [])

        result = journal.get("requests")
        assert result is not None
        assert result["version"] == "2.31.0"

    def test_get_returns_none_for_missing(self, tmp_path):
        """Test get returns None for unknown library"""
        from pypi_libs_journal import PyPILibsJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        journal = PyPILibsJournal(journal_path)

        assert journal.get("nonexistent") is None


class TestPyPILibsJournalStats:
    """Test get_stats() and get_count() methods"""

    def test_get_stats_empty(self, tmp_path):
        """Test stats for empty journal"""
        from pypi_libs_journal import PyPILibsJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        journal = PyPILibsJournal(journal_path)

        stats = journal.get_stats()
        assert stats["total"] == 0
        assert stats["sent"] == 0
        assert stats["failed"] == 0

    def test_get_stats_with_entries(self, tmp_path):
        """Test stats with mixed entries"""
        from pypi_libs_journal import PyPILibsJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        journal = PyPILibsJournal(journal_path)
        journal.add("requests", "2.31.0", "desc", 100, [])
        journal.mark_failed("bad-pkg", "1.0.0", "error")

        stats = journal.get_stats()
        assert stats["total"] == 2
        assert stats["sent"] == 1
        assert stats["failed"] == 1

    def test_get_count(self, tmp_path):
        """Test get_count returns correct count"""
        from pypi_libs_journal import PyPILibsJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        journal = PyPILibsJournal(journal_path)
        assert journal.get_count() == 0
        journal.add("a", "1", "", 0, [])
        assert journal.get_count() == 1
        journal.add("b", "2", "", 0, [])
        assert journal.get_count() == 2

    def test_get_all(self, tmp_path):
        """Test get_all returns all entries"""
        from pypi_libs_journal import PyPILibsJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        journal = PyPILibsJournal(journal_path)
        journal.add("a", "1", "", 0, [])
        journal.add("b", "2", "", 0, [])
        all_entries = journal.get_all()
        assert len(all_entries) == 2


class TestPyPILibsJournalUpdate:
    """Test update() method"""

    def test_update_existing(self, tmp_path):
        """Test updating an existing entry"""
        from pypi_libs_journal import PyPILibsJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        journal = PyPILibsJournal(journal_path)
        journal.add("requests", "2.31.0", "desc", 100, [])

        result = journal.update("requests", "2.31.0", {"status": "updated"})
        assert result is True
        entry = journal.get("requests")
        assert entry["status"] == "updated"
        assert "updated_at" in entry

    def test_update_missing(self, tmp_path):
        """Test updating a non-existent entry returns False"""
        from pypi_libs_journal import PyPILibsJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        journal = PyPILibsJournal(journal_path)

        result = journal.update("nonexistent", "1.0", {"status": "x"})
        assert result is False


class TestPyPILibsJournalClear:
    """Test clear() method"""

    def test_clear(self, tmp_path):
        """Test clear resets journal"""
        from pypi_libs_journal import PyPILibsJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        journal = PyPILibsJournal(journal_path)
        journal.add("requests", "2.31.0", "desc", 100, [])

        journal.clear()
        assert journal.data == {"libraries": []}
        assert journal.get_count() == 0


class TestPyPILibsJournalMarkFailed:
    """Test mark_failed() method"""

    def test_mark_failed(self, tmp_path):
        """Test marking a package as failed"""
        from pypi_libs_journal import PyPILibsJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        journal = PyPILibsJournal(journal_path)
        journal.mark_failed("broken-pkg", "0.1", "Download error")

        assert journal.exists("broken-pkg", "0.1")
        entry = journal.get("broken-pkg")
        assert entry["status"] == "failed"
        assert entry["files"] == []
