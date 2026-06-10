# -*- coding: utf-8 -*-
"""
Tests for MediaJournal module — clear() method.
"""

import json
import os


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
