"""
Unit tests for verifier models.
"""

import pytest
from verifier.models import ChannelFile, DiffResult, VerifierMode


class TestVerifierMode:
    """Test VerifierMode enum."""

    def test_quick_value(self):
        assert VerifierMode.QUICK.value == "quick"

    def test_thorough_value(self):
        assert VerifierMode.THOROUGH.value == "thorough"

    def test_all_modes(self):
        assert len(VerifierMode) == 2
        modes = {m.value for m in VerifierMode}
        assert modes == {"quick", "thorough"}


class TestChannelFile:
    """Test ChannelFile dataclass."""

    def test_create_required(self):
        cf = ChannelFile(filename="test.zip")
        assert cf.filename == "test.zip"
        assert cf.message_text == ""
        assert cf.timestamp == ""
        assert cf.size is None

    def test_create_full(self):
        cf = ChannelFile(
            filename="repo.zip",
            message_text="Some text",
            timestamp="2024-01-01",
            size="1024",
        )
        assert cf.filename == "repo.zip"
        assert cf.message_text == "Some text"
        assert cf.timestamp == "2024-01-01"
        assert cf.size == "1024"


class TestDiffResult:
    """Test DiffResult dataclass."""

    def test_empty_defaults(self):
        dr = DiffResult()
        assert dr.in_journal_not_in_channel == []
        assert dr.in_channel_not_in_journal == []
        assert dr.version_mismatches == []
        assert dr.stats == {}
        assert dr.incomplete_scan is False

    def test_has_issues_missing(self):
        dr = DiffResult(in_journal_not_in_channel=["a", "b"])
        assert dr.has_issues is True
        assert dr.missing_count == 2

    def test_has_issues_orphans_only(self):
        dr = DiffResult(in_channel_not_in_journal=["x"])
        assert dr.has_issues is True
        assert dr.orphan_count == 1

    def test_has_issues_mismatches(self):
        dr = DiffResult(version_mismatches=[{"key": "a"}])
        assert dr.has_issues is True

    def test_no_issues(self):
        dr = DiffResult()
        assert dr.has_issues is False
        assert dr.missing_count == 0
        assert dr.orphan_count == 0

    def test_incomplete_scan(self):
        dr = DiffResult(incomplete_scan=True)
        assert dr.incomplete_scan is True
