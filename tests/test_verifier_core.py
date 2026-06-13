"""
Unit tests for JournalChannelVerifier core logic.
"""

import pytest
from unittest.mock import MagicMock
from verifier.models import VerifierMode, ChannelFile, DiffResult
from verifier.core import JournalChannelVerifier, VerifierError


# ── Mock adapters for testing ──

class MockChannelAdapter:
    """Mock channel adapter for testing."""

    def __init__(self, files, incomplete=False):
        self.files = files
        self._incomplete = incomplete
        self.last_mode = None

    def scan_files(self, mode):
        self.last_mode = mode
        return self.files

    def navigate_to_channel(self, url=None):
        pass

    @property
    def incomplete(self):
        return self._incomplete


class MockJournalAdapter:
    """Mock journal adapter for testing."""

    def __init__(self, entries):
        self.entries = list(entries)
        self.removed_keys = set()

    def get_entries(self):
        return self.entries

    def expected_filename(self, entry):
        return entry.get("filename", "unknown.zip")

    def entry_key(self, entry):
        return entry.get("key", entry.get("full_name", ""))

    def channel_to_key(self, filename):
        for entry in self.entries:
            ef = self.expected_filename(entry)
            if isinstance(ef, str):
                if ef == filename:
                    return self.entry_key(entry)
            else:
                if filename in ef:
                    return self.entry_key(entry)
        # Return filename as key for unmatched files (orphan detection)
        return filename if filename else None

    def remove_entry(self, key):
        self.removed_keys.add(key)
        self.entries = [e for e in self.entries if self.entry_key(e) != key]
        return True

    def get_stats(self):
        return {"total": len(self.entries)}


class TestVerifyPerfectMatch:
    """Test verification when journal and channel match perfectly."""

    def test_no_issues(self):
        entries = [
            {"key": "owner/repo", "filename": "owner-repo.zip"},
            {"key": "owner/repo2", "filename": "owner-repo2.zip"},
        ]
        files = [
            ChannelFile("owner-repo.zip"),
            ChannelFile("owner-repo2.zip"),
        ]
        ca = MockChannelAdapter(files)
        ja = MockJournalAdapter(entries)
        verifier = JournalChannelVerifier(ca, ja, "Test")

        result = verifier.verify(VerifierMode.QUICK)

        assert result.has_issues is False
        assert result.missing_count == 0
        assert result.orphan_count == 0
        assert result.incomplete_scan is False
        assert ca.last_mode == VerifierMode.QUICK


class TestVerifyMissingEntries:
    """Test verification when journal has entries missing from channel."""

    def test_missing_one(self):
        entries = [
            {"key": "a", "filename": "a.zip"},
            {"key": "b", "filename": "b.zip"},
            {"key": "c", "filename": "c.zip"},
        ]
        files = [
            ChannelFile("a.zip"),
            ChannelFile("c.zip"),
        ]
        ca = MockChannelAdapter(files)
        ja = MockJournalAdapter(entries)
        verifier = JournalChannelVerifier(ca, ja, "Test")

        result = verifier.verify()

        assert result.has_issues is True
        assert result.missing_count == 1
        assert "b" in result.in_journal_not_in_channel

    def test_missing_all(self):
        entries = [
            {"key": "x", "filename": "x.zip"},
        ]
        files = []
        ca = MockChannelAdapter(files)
        ja = MockJournalAdapter(entries)
        verifier = JournalChannelVerifier(ca, ja, "Test")

        result = verifier.verify()

        assert result.missing_count == 1
        assert "x" in result.in_journal_not_in_channel


class TestVerifyOrphans:
    """Test verification when channel has files not in journal."""

    def test_orphan_files(self):
        entries = [
            {"key": "a", "filename": "a.zip"},
        ]
        files = [
            ChannelFile("a.zip"),
            ChannelFile("orphan.zip"),
            ChannelFile("another.zip"),
        ]
        ca = MockChannelAdapter(files)
        ja = MockJournalAdapter(entries)
        verifier = JournalChannelVerifier(ca, ja, "Test")

        result = verifier.verify()

        assert result.orphan_count == 2
        orphan_set = set(result.in_channel_not_in_journal)
        assert "orphan.zip" in orphan_set
        assert "another.zip" in orphan_set


class TestVerifyIncompleteScan:
    """Test verification with incomplete scan flag."""

    def test_incomplete_scan_propagates(self):
        entries = [
            {"key": "a", "filename": "a.zip"},
        ]
        files = [ChannelFile("a.zip")]
        ca = MockChannelAdapter(files, incomplete=True)
        ja = MockJournalAdapter(entries)
        verifier = JournalChannelVerifier(ca, ja, "Test")

        result = verifier.verify()

        assert result.incomplete_scan is True
        assert result.stats["incomplete_scan"] is True


class TestFixJournal:
    """Test fix_journal removes missing entries."""

    def test_fix_removes_missing(self):
        entries = [
            {"key": "a", "filename": "a.zip"},
            {"key": "b", "filename": "b.zip"},
            {"key": "c", "filename": "c.zip"},
        ]
        files = [ChannelFile("a.zip"), ChannelFile("c.zip")]
        ca = MockChannelAdapter(files)
        ja = MockJournalAdapter(entries)
        verifier = JournalChannelVerifier(ca, ja, "Test")

        diff = verifier.verify()
        removed = verifier.fix_journal(diff)

        assert removed == 1
        assert "b" in ja.removed_keys
        assert len(ja.entries) == 2

    def test_fix_no_missing(self):
        entries = [
            {"key": "a", "filename": "a.zip"},
        ]
        files = [ChannelFile("a.zip")]
        ca = MockChannelAdapter(files)
        ja = MockJournalAdapter(entries)
        verifier = JournalChannelVerifier(ca, ja, "Test")

        diff = verifier.verify()
        removed = verifier.fix_journal(diff)

        assert removed == 0
        assert len(ja.entries) == 1


class TestReport:
    """Test report generation."""

    def test_report_no_issues(self):
        entries = [{"key": "a", "filename": "a.zip"}]
        files = [ChannelFile("a.zip")]
        ca = MockChannelAdapter(files)
        ja = MockJournalAdapter(entries)
        verifier = JournalChannelVerifier(ca, ja, "GitHub")

        diff = verifier.verify()
        report = verifier.report(diff)

        assert "GitHub" in report
        assert "Расхождений нет" in report or "найдены в канале" in report

    def test_report_missing(self):
        entries = [
            {"key": "a", "filename": "a.zip"},
            {"key": "b", "filename": "b.zip"},
        ]
        files = [ChannelFile("a.zip")]
        ca = MockChannelAdapter(files)
        ja = MockJournalAdapter(entries)
        verifier = JournalChannelVerifier(ca, ja, "Test")

        diff = verifier.verify()
        report = verifier.report(diff)

        assert "Отсутствуют в канале" in report
        assert "b" in report

    def test_report_incomplete_warning(self):
        entries = [{"key": "a", "filename": "a.zip"}]
        files = [ChannelFile("a.zip")]
        ca = MockChannelAdapter(files, incomplete=True)
        ja = MockJournalAdapter(entries)
        verifier = JournalChannelVerifier(ca, ja, "Test")

        diff = verifier.verify()
        report = verifier.report(diff)

        assert "incomplete" in report.lower() or "⚠" in report


class TestStats:
    """Test stats in diff result."""

    def test_stats_populated(self):
        entries = [
            {"key": "a", "filename": "a.zip", "version": "1"},
            {"key": "b", "filename": "b.zip", "version": "2"},
        ]
        files = [ChannelFile("a.zip")]
        ca = MockChannelAdapter(files)
        ja = MockJournalAdapter(entries)
        verifier = JournalChannelVerifier(ca, ja, "GitHub")

        diff = verifier.verify(VerifierMode.THOROUGH)

        assert diff.stats["publisher"] == "GitHub"
        assert diff.stats["mode"] == "thorough"
        assert diff.stats["journal_entries"] == 2
        assert diff.stats["channel_files"] == 1
        assert diff.stats["missing"] == 1


class TestVerifierError:
    """Test VerifierError exception."""

    def test_verifier_error_is_exception(self):
        assert issubclass(VerifierError, Exception)

    def test_verifier_error_message(self):
        err = VerifierError("CDP not available")
        assert "CDP not available" in str(err)
