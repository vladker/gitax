# -*- coding: utf-8 -*-
"""
Tests for Journal module — clear() method.
"""

import json
import os


class TestJournalClear:
    """Tests for Journal.clear()"""

    def test_clear_resets_data(self, tmp_path):
        """clear() resets journal to empty state"""
        from journal import Journal
        j = Journal(str(tmp_path / "journal.json"))
        j.add_repository({"full_name": "test/repo", "status": "sent"})
        assert j.get_count() > 0
        j.clear()
        assert j.get_count() == 0
        assert j.get_stats()["total"] == 0

    def test_clear_empties_file_on_disk(self, tmp_path):
        """clear() writes empty journal to disk"""
        from journal import Journal
        fp = tmp_path / "journal.json"
        j = Journal(str(fp))
        j.add_repository({"full_name": "test/repo", "status": "sent"})
        j.clear()
        # Re-read from disk — should be empty
        j2 = Journal(str(fp))
        assert j2.get_count() == 0

    def test_clear_on_empty_journal_does_not_crash(self, tmp_path):
        """clear() on empty journal does not raise"""
        from journal import Journal
        j = Journal(str(tmp_path / "journal.json"))
        j.clear()  # Should not raise
