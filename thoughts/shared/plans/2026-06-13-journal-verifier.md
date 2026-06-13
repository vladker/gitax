# Journal-Channel Verifier Implementation Plan

**Goal:** Build a `JournalChannelVerifier` system that compares journal entries against actual MAX channel content, supporting all 4 publisher types (GitHub, PyPI, Backuper, Media) with two scan modes (quick/thorough).

**Architecture:** Protocol-based adapter pattern. Core verifier does set-based diff between journal keys and channel keys. Each publisher has a thin adapter (~50-80 lines) that maps journal entries to expected filenames and vice versa. Browser methods are reused via a `BrowserAdapter` wrapper — no modifications to `browser_max.py`.

**Design:** [thoughts/shared/designs/2026-06-13-journal-verifier-design.md](thoughts/shared/designs/2026-06-13-journal-verifier-design.md)

---

## Dependency Graph

```
Batch 1 (parallel): 1.1, 1.2, 1.3               [foundation — no deps]
Batch 2 (parallel): 2.1, 2.2, 2.3, 2.4          [adapters — depends on batch 1]
Batch 3 (parallel): 3.1, 3.2                    [integration + tests — depends on batch 2]
```

---

## Batch 1: Foundation (parallel — 3 implementers)

All tasks in this batch have NO dependencies and run simultaneously.

### Task 1.1: `verifier/__init__.py` — Package init

**File:** `verifier/__init__.py`
**Test:** none
**Depends:** none

```python
"""
Journal-Channel Verifier — compare journal entries against MAX channel content.

Supports all publisher types (GitHub, PyPI, Backuper, Media) with two scan modes:
  - quick:    DOM-only scan, last N messages (~30-60 seconds)
  - thorough: three-source scan (API + page state + DOM scroll)
"""

from verifier.models import ChannelFile, DiffResult, VerifierMode
from verifier.core import JournalChannelVerifier

__all__ = [
    "ChannelFile",
    "DiffResult",
    "VerifierMode",
    "JournalChannelVerifier",
]
```

**Verify:** `python -c "from verifier import JournalChannelVerifier, VerifierMode; print('ok')"`
**Commit:** `feat(verifier): add package init with public exports`

---

### Task 1.2: `verifier/models.py` — Data models

**File:** `verifier/models.py`
**Test:** `tests/test_verifier_models.py`
**Depends:** none

```python
"""
Data models for the Journal-Channel Verifier.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class VerifierMode(Enum):
    """Scan depth for channel verification."""
    QUICK = "quick"
    THOROUGH = "thorough"


@dataclass
class ChannelFile:
    """Unified representation of a file found in a MAX channel."""
    filename: str
    message_text: str = ""
    timestamp: str = ""
    size: str | None = None


@dataclass
class DiffResult:
    """Result of comparing journal entries against channel content."""
    in_journal_not_in_channel: list[str] = field(default_factory=list)
    in_channel_not_in_journal: list[str] = field(default_factory=list)
    version_mismatches: list[dict[str, Any]] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
    incomplete_scan: bool = False

    @property
    def has_issues(self) -> bool:
        """True if there are missing entries or version mismatches."""
        return bool(
            self.in_journal_not_in_channel
            or self.version_mismatches
        )

    @property
    def missing_count(self) -> int:
        """Number of journal entries not found in channel."""
        return len(self.in_journal_not_in_channel)

    @property
    def orphan_count(self) -> int:
        """Number of channel files not found in journal."""
        return len(self.in_channel_not_in_journal)
```

**Test file:** `tests/test_verifier_models.py`

```python
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
        assert dr.has_issues is False
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
```

**Verify:** `pytest tests/test_verifier_models.py -v`
**Commit:** `feat(verifier): add models (ChannelFile, DiffResult, VerifierMode)`

---

### Task 1.3: `verifier/adapters.py` — Protocol definitions

**File:** `verifier/adapters.py`
**Test:** `tests/test_verifier_adapters.py` (protocol structure tests only)
**Depends:** none

```python
"""
Protocol definitions for verifier adapters.

ChannelAdapter wraps BrowserMAX methods for scanning.
JournalAdapter wraps journal classes for entry access.
"""

from __future__ import annotations

from typing import Protocol

from verifier.models import ChannelFile, VerifierMode


class ChannelAdapter(Protocol):
    """Protocol for scanning MAX channel for files.

    Implementations wrap BrowserMAX methods:
      - quick mode  → scan_channel_for_files()
      - thorough mode → audit_channel_completeness()
    """

    def scan_files(self, mode: VerifierMode) -> list[ChannelFile]:
        """Scan channel and return list of files found.

        Args:
            mode: Scan depth — quick or thorough.

        Returns:
            List of ChannelFile objects.
        """
        ...

    def navigate_to_channel(self, channel_url: str) -> None:
        """Navigate browser to the specified channel URL."""
        ...

    @property
    def incomplete(self) -> bool:
        """True if the last scan was incomplete (partial results)."""
        ...


class JournalAdapter(Protocol):
    """Protocol for accessing journal entries.

    Each journal type (GitHub, PyPI, Backuper, Media) has its own
    adapter that maps journal entries to expected filenames.
    """

    def get_entries(self) -> list[dict]:
        """Return all journal entries as list of dicts."""
        ...

    def expected_filename(self, entry: dict) -> str | list[str]:
        """Get expected filename(s) for a journal entry.

        Returns a single filename string or a list for entries
        that produce multiple files (e.g., PyPI .tar.gz + .whl).
        """
        ...

    def entry_key(self, entry: dict) -> str:
        """Get a unique key for a journal entry used for comparison."""
        ...

    def channel_to_key(self, filename: str) -> str | None:
        """Convert a channel filename back to a journal entry key.

        Returns None if the filename doesn't match any known pattern.
        """
        ...

    def remove_entry(self, key: str) -> bool:
        """Remove a journal entry by key. Returns True if removed."""
        ...

    def get_stats(self) -> dict:
        """Get journal statistics."""
        ...
```

**Test file:** `tests/test_verifier_adapters.py`

```python
"""
Unit tests for adapter protocols and concrete adapter implementations.
"""

import pytest
from unittest.mock import MagicMock, patch
from verifier.models import VerifierMode, ChannelFile
from verifier.adapters import ChannelAdapter, JournalAdapter


class TestChannelAdapterProtocol:
    """Test ChannelAdapter protocol structure."""

    def test_protocol_has_scan_files(self):
        """ChannelAdapter defines scan_files method."""
        import inspect
        members = dict(inspect.getmembers(ChannelAdapter))
        assert "scan_files" in members

    def test_protocol_has_navigate(self):
        """ChannelAdapter defines navigate_to_channel method."""
        import inspect
        members = dict(inspect.getmembers(ChannelAdapter))
        assert "navigate_to_channel" in members

    def test_protocol_has_incomplete(self):
        """ChannelAdapter defines incomplete property."""
        import inspect
        members = dict(inspect.getmembers(ChannelAdapter))
        assert "incomplete" in members


class TestJournalAdapterProtocol:
    """Test JournalAdapter protocol structure."""

    def test_protocol_has_get_entries(self):
        import inspect
        members = dict(inspect.getmembers(JournalAdapter))
        assert "get_entries" in members

    def test_protocol_has_expected_filename(self):
        import inspect
        members = dict(inspect.getmembers(JournalAdapter))
        assert "expected_filename" in members

    def test_protocol_has_entry_key(self):
        import inspect
        members = dict(inspect.getmembers(JournalAdapter))
        assert "entry_key" in members

    def test_protocol_has_channel_to_key(self):
        import inspect
        members = dict(inspect.getmembers(JournalAdapter))
        assert "channel_to_key" in members

    def test_protocol_has_remove_entry(self):
        import inspect
        members = dict(inspect.getmembers(JournalAdapter))
        assert "remove_entry" in members

    def test_protocol_has_get_stats(self):
        import inspect
        members = dict(inspect.getmembers(JournalAdapter))
        assert "get_stats" in members
```

**Verify:** `pytest tests/test_verifier_adapters.py -v -k "Protocol"`
**Commit:** `feat(verifier): add ChannelAdapter and JournalAdapter protocols`

---

## Batch 2: Core + Adapters (parallel — 4 implementers)

All tasks in this batch depend on Batch 1 completing.

### Task 2.1: `verifier/core.py` — JournalChannelVerifier

**File:** `verifier/core.py`
**Test:** `tests/test_verifier_core.py`
**Depends:** 1.2 (models), 1.3 (adapters)

```python
"""
Core verifier — compares journal entries against MAX channel content.
"""

from __future__ import annotations

from verifier.models import ChannelFile, DiffResult, VerifierMode
from verifier.adapters import ChannelAdapter, JournalAdapter


class VerifierError(Exception):
    """Raised when verification fails due to infrastructure issues."""
    pass


class JournalChannelVerifier:
    """Compare journal entries against actual MAX channel content.

    Uses set-based comparison:
      journal_keys = {adapter.entry_key(e) for e in journal_entries}
      channel_keys = {adapter.channel_to_key(f.filename) for f in channel_files}
      missing = journal_keys - channel_keys
      orphans = channel_keys - journal_keys

    Args:
        channel_adapter: Implements ChannelAdapter protocol (wraps BrowserMAX).
        journal_adapter: Implements JournalAdapter protocol (wraps a journal).
        publisher_name: Human-readable name for reports (e.g., "GitHub").
    """

    def __init__(
        self,
        channel_adapter: ChannelAdapter,
        journal_adapter: JournalAdapter,
        publisher_name: str = "Unknown",
    ):
        self.channel_adapter = channel_adapter
        self.journal_adapter = journal_adapter
        self.publisher_name = publisher_name

    def verify(self, mode: VerifierMode = VerifierMode.QUICK) -> DiffResult:
        """Run verification and return diff result.

        Args:
            mode: Scan depth — quick (DOM-only) or thorough (3-source).

        Returns:
            DiffResult with missing entries, orphans, and stats.

        Raises:
            VerifierError: If browser connection fails.
        """
        # ── Step 1: Scan channel ──
        channel_files = self.channel_adapter.scan_files(mode)
        incomplete = self.channel_adapter.incomplete

        # ── Step 2: Get journal entries ──
        journal_entries = self.journal_adapter.get_entries()

        # ── Step 3: Build key sets ──
        journal_keys: set[str] = set()
        for entry in journal_entries:
            key = self.journal_adapter.entry_key(entry)
            if key:
                journal_keys.add(key)

        channel_keys: set[str] = set()
        for cf in channel_files:
            key = self.journal_adapter.channel_to_key(cf.filename)
            if key:
                channel_keys.add(key)

        # ── Step 4: Compute diff ──
        missing = sorted(journal_keys - channel_keys)
        orphans = sorted(channel_keys - journal_keys)

        # ── Step 5: Check version mismatches ──
        mismatches = self._check_version_mismatches(
            journal_entries, channel_files
        )

        # ── Step 6: Build result ──
        stats = {
            "publisher": self.publisher_name,
            "mode": mode.value,
            "journal_entries": len(journal_entries),
            "channel_files": len(channel_files),
            "journal_keys": len(journal_keys),
            "channel_keys": len(channel_keys),
            "missing": len(missing),
            "orphans": len(orphans),
            "mismatches": len(mismatches),
            "incomplete_scan": incomplete,
        }

        return DiffResult(
            in_journal_not_in_channel=missing,
            in_channel_not_in_journal=orphans,
            version_mismatches=mismatches,
            stats=stats,
            incomplete_scan=incomplete,
        )

    def fix_journal(self, diff: DiffResult) -> int:
        """Remove missing entries from journal.

        Design requires report-only by default. This method is the
        explicit opt-in for journal modification.

        Args:
            diff: DiffResult from verify().

        Returns:
            Number of entries removed.
        """
        removed = 0
        for key in diff.in_journal_not_in_channel:
            if self.journal_adapter.remove_entry(key):
                removed += 1
        return removed

    def report(self, diff: DiffResult) -> str:
        """Generate a human-readable verification report.

        Args:
            diff: DiffResult from verify().

        Returns:
            Formatted string report.
        """
        lines = []
        lines.append(f"  Verifier report — {self.publisher_name}")
        lines.append(f"  Mode: {diff.stats.get('mode', 'unknown')}")
        lines.append(f"  Journal entries: {diff.stats.get('journal_entries', 0)}")
        lines.append(f"  Channel files:   {diff.stats.get('channel_files', 0)}")
        lines.append("")

        if diff.incomplete_scan:
            lines.append("  ⚠ WARNING: Scan was incomplete (partial results)")
            lines.append("")

        if not diff.has_issues and not diff.in_channel_not_in_journal:
            lines.append("  ✓ All journal entries found in channel. No issues.")
        else:
            if diff.in_journal_not_in_channel:
                lines.append(
                    f"  ✗ Missing from channel ({diff.missing_count}):"
                )
                for key in diff.in_journal_not_in_channel[:20]:
                    lines.append(f"    — {key}")
                if len(diff.in_journal_not_in_channel) > 20:
                    lines.append(
                        f"    ... and {len(diff.in_journal_not_in_channel) - 20} more"
                    )
                lines.append("")

            if diff.version_mismatches:
                lines.append(
                    f"  ⚠ Version mismatches ({len(diff.version_mismatches)}):"
                )
                for mm in diff.version_mismatches[:10]:
                    lines.append(f"    — {mm}")
                lines.append("")

            if diff.in_channel_not_in_journal:
                lines.append(
                    f"  ℹ Orphans in channel ({diff.orphan_count}):"
                )
                for key in diff.in_channel_not_in_journal[:10]:
                    lines.append(f"    — {key}")
                if len(diff.in_channel_not_in_journal) > 10:
                    lines.append(
                        f"    ... and {len(diff.in_channel_not_in_journal) - 10} more"
                    )
                lines.append("")

        return "\n".join(lines)

    def _check_version_mismatches(
        self,
        entries: list[dict],
        files: list[ChannelFile],
    ) -> list[dict]:
        """Check for version mismatches between journal and channel.

        Compares journal entry versions against filenames where possible.
        Returns a list of mismatch dicts with details.
        """
        mismatches = []
        channel_by_key: dict[str, ChannelFile] = {}
        for cf in files:
            key = self.journal_adapter.channel_to_key(cf.filename)
            if key:
                channel_by_key[key] = cf

        for entry in entries:
            key = self.journal_adapter.entry_key(entry)
            journal_ver = entry.get("version", "")
            if not journal_ver:
                continue
            cf = channel_by_key.get(key)
            if cf and journal_ver not in cf.filename:
                mismatches.append({
                    "key": key,
                    "journal_version": journal_ver,
                    "channel_file": cf.filename,
                })
        return mismatches
```

**Test file:** `tests/test_verifier_core.py`

```python
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

    def __init__(self, files: list[ChannelFile], incomplete: bool = False):
        self.files = files
        self._incomplete = incomplete
        self.last_mode = None

    def scan_files(self, mode: VerifierMode) -> list[ChannelFile]:
        self.last_mode = mode
        return self.files

    def navigate_to_channel(self, url: str) -> None:
        pass

    @property
    def incomplete(self) -> bool:
        return self._incomplete


class MockJournalAdapter:
    """Mock journal adapter for testing."""

    def __init__(self, entries: list[dict]):
        self.entries = list(entries)
        self.removed_keys: set[str] = set()

    def get_entries(self) -> list[dict]:
        return self.entries

    def expected_filename(self, entry: dict) -> str | list[str]:
        return entry.get("filename", "unknown.zip")

    def entry_key(self, entry: dict) -> str:
        return entry.get("key", entry.get("full_name", ""))

    def channel_to_key(self, filename: str) -> str | None:
        for entry in self.entries:
            ef = self.expected_filename(entry)
            if isinstance(ef, str):
                if ef == filename:
                    return self.entry_key(entry)
            else:
                if filename in ef:
                    return self.entry_key(entry)
        return None

    def remove_entry(self, key: str) -> bool:
        self.removed_keys.add(key)
        self.entries = [e for e in self.entries if self.entry_key(e) != key]
        return True

    def get_stats(self) -> dict:
        return {"total": len(self.entries)}


class TestVerifyPerfectMatch:
    """Test verification when journal and channel match perfectly."""

    def test_no_issues(self):
        entries = [
            {"key": "owner/repo", "filename": "owner-repo.zip", "version": "1.0"},
            {"key": "owner/repo2", "filename": "owner-repo2.zip", "version": "2.0"},
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
        files: list[ChannelFile] = []
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
        assert "orphan" in result.in_channel_not_in_journal[0]


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
        assert "No issues" in report or "found in channel" in report

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

        assert "Missing from channel" in report
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
```

**Verify:** `pytest tests/test_verifier_core.py -v`
**Commit:** `feat(verifier): add JournalChannelVerifier core with verify/fix/report`

---

### Task 2.2: `verifier/adapters_github.py` — GitHub adapter

**File:** `verifier/adapters_github.py`
**Test:** covered by `tests/test_verifier_adapters.py`
**Depends:** 1.2, 1.3

```python
"""
GitHub journal adapter for the verifier.

Maps journal entries (full_name: "owner/repo") to channel filenames
(owner-repo.zip, owner-repo.7z.001, etc.)
"""

from __future__ import annotations

import re
from verifier.models import ChannelFile, VerifierMode
from verifier.adapters import ChannelAdapter, JournalAdapter


class GitHubChannelAdapter:
    """Channel adapter for GitHub archives.

    Wraps BrowserMAX methods:
      - quick mode  → scan_channel_for_files()
      - thorough mode → audit_channel_completeness()
    """

    def __init__(self, browser, channel_url: str):
        self.browser = browser
        self.channel_url = channel_url
        self._incomplete = False

    def navigate_to_channel(self, channel_url: str | None = None) -> None:
        url = channel_url or self.channel_url
        self.browser.navigate(url)
        self.browser.wait_page_ready()

    def scan_files(self, mode: VerifierMode) -> list[ChannelFile]:
        self.navigate_to_channel()
        if mode == VerifierMode.QUICK:
            raw = self.browser.scan_channel_for_files()
            files = [
                ChannelFile(
                    filename=item.get("filename", ""),
                    size=str(item.get("file_size", 0)),
                )
                for item in raw
                if item.get("filename")
            ]
            self._incomplete = False
        else:
            result = self.browser.audit_channel_completeness()
            messages = result.get("channel_messages", [])
            files = []
            for msg in messages:
                text = msg.get("text", "") or ""
                fn_matches = re.findall(
                    r'(\S+\.(?:zip|7z(?:\.\d{3})?))', text
                )
                for fn in fn_matches:
                    files.append(ChannelFile(filename=fn))
            self._incomplete = len(messages) == 0
        return files

    @property
    def incomplete(self) -> bool:
        return self._incomplete


class GitHubJournalAdapter:
    """Journal adapter for GitHub repositories.

    Key mapping:
      journal → "owner/repo" (full_name)
      channel → "owner-repo.zip" → "owner/repo"
    """

    def __init__(self, journal):
        self.journal = journal

    def get_entries(self) -> list[dict]:
        repos = self.journal.get_all_repositories()
        return [r for r in repos if r.get("status") in ("sent", "restored", "incomplete")]

    def expected_filename(self, entry: dict) -> str | list[str]:
        full_name = entry.get("full_name", "")
        base = full_name.replace("/", "-")
        if entry.get("archive_size", 0) > 50 * 1024 * 1024:
            return [f"{base}.7z.001", f"{base}.7z.002", f"{base}.7z.003"]
        return f"{base}.zip"

    def entry_key(self, entry: dict) -> str:
        return entry.get("full_name", "")

    def channel_to_key(self, filename: str) -> str | None:
        base = re.sub(r'\.(zip|7z(?:\.\d{3})?)$', '', filename, flags=re.IGNORECASE)
        if "-" in base:
            owner, repo = base.split("-", 1)
            return f"{owner}/{repo}"
        return None

    def remove_entry(self, key: str) -> bool:
        return self.journal.remove_repository(key)

    def get_stats(self) -> dict:
        return self.journal.get_stats()
```

**Verify:** `pytest tests/test_verifier_adapters.py -v -k "github"`
**Commit:** `feat(verifier): add GitHub channel and journal adapters`

---

### Task 2.3: `verifier/adapters_pypi.py` — PyPI adapter

**File:** `verifier/adapters_pypi.py`
**Test:** covered by `tests/test_verifier_adapters.py`
**Depends:** 1.2, 1.3

```python
"""
PyPI journal adapter for the verifier.

Maps journal entries (name + version) to channel filenames:
  name-version.tar.gz and name-version-py3-none-any.whl
"""

from __future__ import annotations

import re
from verifier.models import ChannelFile, VerifierMode
from verifier.adapters import ChannelAdapter, JournalAdapter


class PyPIChannelAdapter:
    """Channel adapter for PyPI packages.

    Wraps BrowserMAX methods for scanning PyPI channel.
    """

    def __init__(self, browser, channel_url: str):
        self.browser = browser
        self.channel_url = channel_url
        self._incomplete = False

    def navigate_to_channel(self, channel_url: str | None = None) -> None:
        url = channel_url or self.channel_url
        self.browser.navigate(url)
        self.browser.wait_page_ready()

    def scan_files(self, mode: VerifierMode) -> list[ChannelFile]:
        self.navigate_to_channel()
        if mode == VerifierMode.QUICK:
            raw = self.browser.scan_channel_for_files()
            files = [
                ChannelFile(
                    filename=item.get("filename", ""),
                    size=str(item.get("file_size", 0)),
                )
                for item in raw
                if item.get("filename")
            ]
            self._incomplete = False
        else:
            result = self.browser.audit_channel_completeness()
            messages = result.get("channel_messages", [])
            files = []
            for msg in messages:
                text = msg.get("text", "") or ""
                fn_matches = re.findall(
                    r'(\S+\.(?:tar\.gz|whl))', text
                )
                for fn in fn_matches:
                    files.append(ChannelFile(filename=fn))
            self._incomplete = len(messages) == 0
        return files

    @property
    def incomplete(self) -> bool:
        return self._incomplete


class PyPIJournalAdapter:
    """Journal adapter for PyPI libraries.

    Key mapping:
      journal → "name-version" (e.g., "requests-2.31.0")
      channel → "requests-2.31.0.tar.gz" → "requests-2.31.0"
    """

    def __init__(self, journal):
        self.journal = journal

    def get_entries(self) -> list[dict]:
        return self.journal.get_all()

    def expected_filename(self, entry: dict) -> str | list[str]:
        name = entry.get("name", "")
        version = entry.get("version", "")
        return [
            f"{name}-{version}.tar.gz",
            f"{name}-{version}-py3-none-any.whl",
        ]

    def entry_key(self, entry: dict) -> str:
        name = entry.get("name", "")
        version = entry.get("version", "")
        return f"{name}-{version}"

    def channel_to_key(self, filename: str) -> str | None:
        # requests-2.31.0.tar.gz → requests-2.31.0
        m = re.match(r'^(.+)-(\d[\d.]*)\.(?:tar\.gz|whl)$', filename)
        if m:
            return f"{m.group(1)}-{m.group(2)}"
        # requests-2.31.0-py3-none-any.whl → requests-2.31.0
        m = re.match(r'^(.+)-(\d[\d.]*)-py3-none-any\.whl$', filename)
        if m:
            return f"{m.group(1)}-{m.group(2)}"
        return None

    def remove_entry(self, key: str) -> bool:
        parts = key.rsplit("-", 1)
        if len(parts) != 2:
            return False
        name, version = parts
        for i, entry in enumerate(self.journal.get_all()):
            if entry.get("name") == name and entry.get("version") == version:
                self.journal.data["libraries"].pop(i)
                self.journal.save()
                return True
        return False

    def get_stats(self) -> dict:
        return self.journal.get_stats()
```

**Verify:** `pytest tests/test_verifier_adapters.py -v -k "pypi"`
**Commit:** `feat(verifier): add PyPI channel and journal adapters`

---

### Task 2.4: `verifier/adapters_backuper.py` and `verifier/adapters_media.py` — Backuper + Media adapters

**Files:**
- `verifier/adapters_backuper.py`
- `verifier/adapters_media.py`
**Test:** covered by `tests/test_verifier_adapters.py`
**Depends:** 1.2, 1.3

**File:** `verifier/adapters_backuper.py`

```python
"""
Backuper journal adapter for the verifier.

Maps journal entries (archive_name) to channel filenames:
  archive_name.7z.001, archive_name.7z.002, etc.
"""

from __future__ import annotations

import re
from verifier.models import ChannelFile, VerifierMode
from verifier.adapters import ChannelAdapter, JournalAdapter


class BackuperChannelAdapter:
    """Channel adapter for backup archives."""

    def __init__(self, browser, channel_url: str):
        self.browser = browser
        self.channel_url = channel_url
        self._incomplete = False

    def navigate_to_channel(self, channel_url: str | None = None) -> None:
        url = channel_url or self.channel_url
        self.browser.navigate(url)
        self.browser.wait_page_ready()

    def scan_files(self, mode: VerifierMode) -> list[ChannelFile]:
        self.navigate_to_channel()
        if mode == VerifierMode.QUICK:
            raw = self.browser.scan_channel_for_files()
            files = [
                ChannelFile(
                    filename=item.get("filename", ""),
                    size=str(item.get("file_size", 0)),
                )
                for item in raw
                if item.get("filename")
            ]
            self._incomplete = False
        else:
            result = self.browser.audit_channel_completeness()
            messages = result.get("channel_messages", [])
            files = []
            for msg in messages:
                text = msg.get("text", "") or ""
                fn_matches = re.findall(
                    r'(\S+\.(?:7z(?:\.\d{3})?|zip))', text
                )
                for fn in fn_matches:
                    files.append(ChannelFile(filename=fn))
            self._incomplete = len(messages) == 0
        return files

    @property
    def incomplete(self) -> bool:
        return self._incomplete


class BackuperJournalAdapter:
    """Journal adapter for backups.

    Key mapping:
      journal → archive_name (e.g., "documents")
      channel → "documents.7z.001" → "documents"
    """

    def __init__(self, journal):
        self.journal = journal

    def get_entries(self) -> list[dict]:
        backups = self.journal.get_all_backups()
        return [b for b in backups if b.get("status") == "uploaded"]

    def expected_filename(self, entry: dict) -> str | list[str]:
        name = entry.get("archive_name", "")
        vol_count = entry.get("volume_count", 1)
        if vol_count and vol_count > 1:
            return [f"{name}.7z.{i:03d}" for i in range(1, vol_count + 1)]
        return f"{name}.7z"

    def entry_key(self, entry: dict) -> str:
        return entry.get("archive_name", "")

    def channel_to_key(self, filename: str) -> str | None:
        m = re.match(r'^(.+)\.7z(?:\.\d{3})?$', filename)
        if m:
            return m.group(1)
        m = re.match(r'^(.+)\.zip$', filename)
        if m:
            return m.group(1)
        return None

    def remove_entry(self, key: str) -> bool:
        for i, entry in enumerate(self.journal.get_all_backups()):
            if entry.get("archive_name") == key:
                self.journal.data["backups"].pop(i)
                self.journal.save()
                return True
        return False

    def get_stats(self) -> dict:
        return self.journal.get_stats()
```

**File:** `verifier/adapters_media.py`

```python
"""
Media journal adapter for the verifier.

Maps journal entries (filename) to channel filenames directly.
"""

from __future__ import annotations

import re
from verifier.models import ChannelFile, VerifierMode
from verifier.adapters import ChannelAdapter, JournalAdapter


class MediaChannelAdapter:
    """Channel adapter for media files."""

    def __init__(self, browser, channel_url: str):
        self.browser = browser
        self.channel_url = channel_url
        self._incomplete = False

    def navigate_to_channel(self, channel_url: str | None = None) -> None:
        url = channel_url or self.channel_url
        self.browser.navigate(url)
        self.browser.wait_page_ready()

    def scan_files(self, mode: VerifierMode) -> list[ChannelFile]:
        self.navigate_to_channel()
        if mode == VerifierMode.QUICK:
            raw = self.browser.scan_channel_for_files()
            files = [
                ChannelFile(
                    filename=item.get("filename", ""),
                    size=str(item.get("file_size", 0)),
                )
                for item in raw
                if item.get("filename")
            ]
            self._incomplete = False
        else:
            result = self.browser.audit_channel_completeness()
            messages = result.get("channel_messages", [])
            files = []
            for msg in messages:
                text = msg.get("text", "") or ""
                fn_matches = re.findall(
                    r'(\S+\.(?:jpg|jpeg|png|gif|webp|bmp|tiff|mp4|mov|avi|mkv|webm))',
                    text, flags=re.IGNORECASE
                )
                for fn in fn_matches:
                    files.append(ChannelFile(filename=fn))
            self._incomplete = len(messages) == 0
        return files

    @property
    def incomplete(self) -> bool:
        return self._incomplete


class MediaJournalAdapter:
    """Journal adapter for media files.

    Key mapping:
      journal → filename (exact match)
      channel → filename (exact match)
    """

    def __init__(self, journal):
        self.journal = journal

    def get_entries(self) -> list[dict]:
        return self.journal.data.get("entries", [])

    def expected_filename(self, entry: dict) -> str | list[str]:
        return entry.get("filename", "")

    def entry_key(self, entry: dict) -> str:
        return entry.get("filename", "")

    def channel_to_key(self, filename: str) -> str | None:
        return filename if filename else None

    def remove_entry(self, key: str) -> bool:
        entries = self.journal.data.get("entries", [])
        for i, entry in enumerate(entries):
            if entry.get("filename") == key:
                entries.pop(i)
                self.journal.save()
                return True
        return False

    def get_stats(self) -> dict:
        return self.journal.get_stats()
```

**Verify:** `pytest tests/test_verifier_adapters.py -v -k "backuper or media"`
**Commit:** `feat(verifier): add Backuper and Media adapters`

---

## Batch 3: Integration + Full Tests (parallel — 2 implementers)

All tasks in this batch depend on Batch 2 completing.

### Task 3.1: `github_archiver.py` — Menu integration

**File:** `github_archiver.py` (modify existing)
**Test:** none (UI integration tested manually)
**Depends:** 2.1, 2.2, 2.3, 2.4

**Changes to `_service_menu()` method (around line 532):**

Add a new menu option `[4] Верификация журналов` after the existing `[3] Каналы`:

```python
# In _service_menu(), after line "  [3] Каналы — управление каналами":
        print("  [4] Верификация журналов")
```

**Changes to `_run_service_menu()` method (around line 2892):**

Add option `4` to valid_opts and handler:

```python
# In _run_service_menu(), update valid_opts to include "4":
            if setup_done:
                valid_opts = ["0", "1", "2", "3", "4"]
            else:
                valid_opts = ["0", "1", "3", "4"]

# Add handler after choice == '3':
            elif choice == '4':
                self._run_verifier()
```

**New method `_run_verifier()` — add after `_run_service_menu()`:**

```python
    def _run_verifier(self):
        """Верификация журналов — сравнение с каналом"""
        from verifier import JournalChannelVerifier, VerifierMode
        from verifier.adapters_github import (
            GitHubChannelAdapter, GitHubJournalAdapter
        )
        from verifier.adapters_pypi import (
            PyPIChannelAdapter, PyPIJournalAdapter
        )
        from verifier.adapters_backuper import (
            BackuperChannelAdapter, BackuperJournalAdapter
        )
        from verifier.adapters_media import (
            MediaChannelAdapter, MediaJournalAdapter
        )

        print("\n" + "=" * 60)
        print("  Верификация журналов")
        print("─" * 60)
        print()
        print("  Выберите журнал для проверки:")
        print()

        # Show available journals
        from journal import Journal
        from pypi_libs_journal import PyPILibsJournal
        from backuper_journal import BackuperJournal
        from media_archiver import MediaJournal

        gh_journal = Journal("journal.json")
        pypi_journal = PyPILibsJournal("pypi_libs_journal.json")
        bp_journal = BackuperJournal("backuper_journal.json")
        md_journal = MediaJournal("media_journal.json")

        options = []
        if gh_journal.get_count() > 0:
            options.append(("1", "GitHub", gh_journal))
        if pypi_journal.get_count() > 0:
            options.append(("2", "PyPI", pypi_journal))
        if bp_journal.get_count() > 0:
            options.append(("3", "Backuper", bp_journal))
        if md_journal.get_count() > 0:
            options.append(("4", "Media", md_journal))

        if not options:
            print("  ⚠ Все журналы пусты. Верификация не требуется.")
            input("\n  Нажмите Enter для возврата в меню...")
            return

        for num, name, journal in options:
            stats = journal.get_stats()
            total = stats.get("total", stats.get("total_backups", 0))
            print(f"  [{num}] {name} — {total} записей")
        print()

        choice = input("  Выберите журнал [1-4]: ").strip()
        selected = None
        for num, name, journal in options:
            if choice == num:
                selected = (name, journal)
                break

        if not selected:
            print("  Неверный выбор.")
            input("\n  Нажмите Enter для возврата в меню...")
            return

        pub_name, journal = selected
        print(f"\n  Журнал: {pub_name}")

        # Select mode
        print("\n  Режим проверки:")
        print("  [Q] Quick   — быстрый (DOM-сканирование, ~30-60 сек)")
        print("  [T] Thorough — полный (3 источника, может занять время)")
        mode_choice = input("  Режим [Q/T]: ").strip().lower()
        mode = VerifierMode.THOROUGH if mode_choice == "t" else VerifierMode.QUICK

        # Get channel URL
        from config import get_config
        config = get_config()
        channel_map = {
            "GitHub": "max",
            "PyPI": "pypi",
            "Backuper": "backup",
            "Media": "media",
        }
        channel_key = channel_map.get(pub_name, "max")
        channel_url = config.channels.__getattribute__(channel_key, "")

        if not channel_url:
            # Try channel_registry fallback
            from channel_registry import get_channels_for_function
            func_map = {"GitHub": "github", "PyPI": "pypi", "Backuper": "backup", "Media": "media"}
            func_key = func_map.get(pub_name, "github")
            channels = get_channels_for_function(func_key)
            if channels:
                channel_url = channels[0].url

        if not channel_url:
            print(f"\n  ⚠ URL канала для {pub_name} не настроен.")
            input("\n  Нажмите Enter для возврата в меню...")
            return

        # Init browser
        print(f"\n  Подключение к браузеру...")
        try:
            browser = self.browser
            if not browser or not browser.page:
                from browser_init import BrowserInitMixin
                mixin = BrowserInitMixin()
                browser = mixin.init_browser(channel_url, config)
            else:
                browser.navigate(channel_url)
                browser.wait_page_ready()
        except Exception as e:
            print(f"\n  ✗ Ошибка подключения: {e}")
            print("  Убедитесь, что Chrome запущен с --remote-debugging-port=9222")
            input("\n  Нажмите Enter для возврата в меню...")
            return

        # Create adapters
        adapter_map = {
            "GitHub": (GitHubChannelAdapter, GitHubJournalAdapter),
            "PyPI": (PyPIChannelAdapter, PyPIJournalAdapter),
            "Backuper": (BackuperChannelAdapter, BackuperJournalAdapter),
            "Media": (MediaChannelAdapter, MediaJournalAdapter),
        }
        CA, JA = adapter_map[pub_name]
        channel_adapter = CA(browser, channel_url)
        journal_adapter = JA(journal)

        # Run verification
        verifier = JournalChannelVerifier(
            channel_adapter, journal_adapter, pub_name
        )
        print(f"\n  Запуск проверки ({mode.value})...")
        print("  Пожалуйста, не трогайте браузер\n")

        diff = verifier.verify(mode)

        # Show report
        report = verifier.report(diff)
        print(report)

        # Offer fix
        if diff.has_issues:
            print("\n  Найдены расхождения. Исправить журнал?")
            print("  [Y] Да — удалить записи, отсутствующие в канале")
            print("  [N] Нет — только просмотр")
            fix_choice = input("  Ваш выбор [Y/N]: ").strip().lower()
            if fix_choice == "y":
                removed = verifier.fix_journal(diff)
                print(f"\n  ✓ Удалено {removed} записей из журнала")
            else:
                print("\n  Журнал не изменён.")

        input("\n  Нажмите Enter для возврата в меню...")
```

**Verify:** Run `python github_archiver.py` → navigate to Сервис → option 4
**Commit:** `feat(verifier): integrate verifier into service menu`

---

### Task 3.2: `tests/test_verifier_adapters.py` — Full adapter tests

**File:** `tests/test_verifier_adapters.py` (replace the protocol-only version from batch 1)
**Test:** self
**Depends:** 2.2, 2.3, 2.4

```python
"""
Unit tests for verifier adapters.

Tests cover:
- Protocol structure validation
- GitHub adapter key mapping (full_name ↔ filename)
- PyPI adapter key mapping (name-version ↔ filename)
- Backuper adapter key mapping (archive_name ↔ 7z filename)
- Media adapter key mapping (filename ↔ filename)
"""

import pytest
from unittest.mock import MagicMock, patch
from verifier.models import VerifierMode, ChannelFile

# Protocol tests
from verifier.adapters import ChannelAdapter, JournalAdapter


class TestChannelAdapterProtocol:
    """Test ChannelAdapter protocol structure."""

    def test_protocol_has_scan_files(self):
        import inspect
        members = dict(inspect.getmembers(ChannelAdapter))
        assert "scan_files" in members

    def test_protocol_has_navigate(self):
        import inspect
        members = dict(inspect.getmembers(ChannelAdapter))
        assert "navigate_to_channel" in members

    def test_protocol_has_incomplete(self):
        import inspect
        members = dict(inspect.getmembers(ChannelAdapter))
        assert "incomplete" in members


class TestJournalAdapterProtocol:
    """Test JournalAdapter protocol structure."""

    def test_protocol_has_get_entries(self):
        import inspect
        members = dict(inspect.getmembers(JournalAdapter))
        assert "get_entries" in members

    def test_protocol_has_expected_filename(self):
        import inspect
        members = dict(inspect.getmembers(JournalAdapter))
        assert "expected_filename" in members

    def test_protocol_has_entry_key(self):
        import inspect
        members = dict(inspect.getmembers(JournalAdapter))
        assert "entry_key" in members

    def test_protocol_has_channel_to_key(self):
        import inspect
        members = dict(inspect.getmembers(JournalAdapter))
        assert "channel_to_key" in members

    def test_protocol_has_remove_entry(self):
        import inspect
        members = dict(inspect.getmembers(JournalAdapter))
        assert "remove_entry" in members

    def test_protocol_has_get_stats(self):
        import inspect
        members = dict(inspect.getmembers(JournalAdapter))
        assert "get_stats" in members


# ── GitHub Adapter Tests ──

class TestGitHubJournalAdapter:
    """Test GitHub journal adapter key mapping."""

    def test_entry_key_full_name(self):
        from verifier.adapters_github import GitHubJournalAdapter
        mock_journal = MagicMock()
        adapter = GitHubJournalAdapter(mock_journal)
        entry = {"full_name": "owner/repo"}
        assert adapter.entry_key(entry) == "owner/repo"

    def test_channel_to_key_zip(self):
        from verifier.adapters_github import GitHubJournalAdapter
        mock_journal = MagicMock()
        adapter = GitHubJournalAdapter(mock_journal)
        assert adapter.channel_to_key("owner-repo.zip") == "owner/repo"

    def test_channel_to_key_7z_volume(self):
        from verifier.adapters_github import GitHubJournalAdapter
        mock_journal = MagicMock()
        adapter = GitHubJournalAdapter(mock_journal)
        assert adapter.channel_to_key("owner-repo.7z.001") == "owner/repo"
        assert adapter.channel_to_key("owner-repo.7z.003") == "owner/repo"

    def test_channel_to_key_no_extension(self):
        from verifier.adapters_github import GitHubJournalAdapter
        mock_journal = MagicMock()
        adapter = GitHubJournalAdapter(mock_journal)
        assert adapter.channel_to_key("random-file.txt") is None

    def test_expected_filename_zip(self):
        from verifier.adapters_github import GitHubJournalAdapter
        mock_journal = MagicMock()
        adapter = GitHubJournalAdapter(mock_journal)
        entry = {"full_name": "owner/repo", "archive_size": 1000}
        assert adapter.expected_filename(entry) == "owner-repo.zip"

    def test_expected_filename_7z_volumes(self):
        from verifier.adapters_github import GitHubJournalAdapter
        mock_journal = MagicMock()
        adapter = GitHubJournalAdapter(mock_journal)
        entry = {"full_name": "owner/repo", "archive_size": 100_000_000}
        result = adapter.expected_filename(entry)
        assert isinstance(result, list)
        assert "owner-repo.7z.001" in result

    def test_get_entries_filters_status(self):
        from verifier.adapters_github import GitHubJournalAdapter
        mock_journal = MagicMock()
        mock_journal.get_all_repositories.return_value = [
            {"full_name": "a", "status": "sent"},
            {"full_name": "b", "status": "failed"},
            {"full_name": "c", "status": "restored"},
        ]
        adapter = GitHubJournalAdapter(mock_journal)
        entries = adapter.get_entries()
        assert len(entries) == 2
        assert entries[0]["full_name"] == "a"
        assert entries[1]["full_name"] == "c"


class TestGitHubChannelAdapter:
    """Test GitHub channel adapter."""

    def test_quick_mode_calls_scan_channel_for_files(self):
        from verifier.adapters_github import GitHubChannelAdapter
        mock_browser = MagicMock()
        mock_browser.scan_channel_for_files.return_value = [
            {"filename": "owner-repo.zip", "file_size": 1000}
        ]
        adapter = GitHubChannelAdapter(mock_browser, "https://example.com")
        files = adapter.scan_files(VerifierMode.QUICK)
        assert len(files) == 1
        assert files[0].filename == "owner-repo.zip"
        mock_browser.scan_channel_for_files.assert_called_once()

    def test_thorough_mode_calls_audit(self):
        from verifier.adapters_github import GitHubChannelAdapter
        mock_browser = MagicMock()
        mock_browser.audit_channel_completeness.return_value = {
            "channel_messages": [
                {"text": "📦 owner/repo\nowner-repo.zip"}
            ]
        }
        adapter = GitHubChannelAdapter(mock_browser, "https://example.com")
        files = adapter.scan_files(VerifierMode.THOROUGH)
        mock_browser.audit_channel_completeness.assert_called_once()


# ── PyPI Adapter Tests ──

class TestPyPIJournalAdapter:
    """Test PyPI journal adapter key mapping."""

    def test_entry_key(self):
        from verifier.adapters_pypi import PyPIJournalAdapter
        mock_journal = MagicMock()
        adapter = PyPIJournalAdapter(mock_journal)
        entry = {"name": "requests", "version": "2.31.0"}
        assert adapter.entry_key(entry) == "requests-2.31.0"

    def test_channel_to_key_tar_gz(self):
        from verifier.adapters_pypi import PyPIJournalAdapter
        mock_journal = MagicMock()
        adapter = PyPIJournalAdapter(mock_journal)
        assert adapter.channel_to_key("requests-2.31.0.tar.gz") == "requests-2.31.0"

    def test_channel_to_key_whl(self):
        from verifier.adapters_pypi import PyPIJournalAdapter
        mock_journal = MagicMock()
        adapter = PyPIJournalAdapter(mock_journal)
        assert adapter.channel_to_key("requests-2.31.0-py3-none-any.whl") == "requests-2.31.0"

    def test_expected_filenames(self):
        from verifier.adapters_pypi import PyPIJournalAdapter
        mock_journal = MagicMock()
        adapter = PyPIJournalAdapter(mock_journal)
        entry = {"name": "flask", "version": "3.0.0"}
        result = adapter.expected_filename(entry)
        assert "flask-3.0.0.tar.gz" in result
        assert "flask-3.0.0-py3-none-any.whl" in result


# ── Backuper Adapter Tests ──

class TestBackuperJournalAdapter:
    """Test Backuper journal adapter key mapping."""

    def test_entry_key(self):
        from verifier.adapters_backuper import BackuperJournalAdapter
        mock_journal = MagicMock()
        adapter = BackuperJournalAdapter(mock_journal)
        entry = {"archive_name": "documents"}
        assert adapter.entry_key(entry) == "documents"

    def test_channel_to_key_7z(self):
        from verifier.adapters_backuper import BackuperJournalAdapter
        mock_journal = MagicMock()
        adapter = BackuperJournalAdapter(mock_journal)
        assert adapter.channel_to_key("documents.7z") == "documents"

    def test_channel_to_key_7z_volume(self):
        from verifier.adapters_backuper import BackuperJournalAdapter
        mock_journal = MagicMock()
        adapter = BackuperJournalAdapter(mock_journal)
        assert adapter.channel_to_key("documents.7z.001") == "documents"
        assert adapter.channel_to_key("documents.7z.003") == "documents"

    def test_channel_to_key_zip(self):
        from verifier.adapters_backuper import BackuperJournalAdapter
        mock_journal = MagicMock()
        adapter = BackuperJournalAdapter(mock_journal)
        assert adapter.channel_to_key("photos.zip") == "photos"

    def test_expected_filename_single(self):
        from verifier.adapters_backuper import BackuperJournalAdapter
        mock_journal = MagicMock()
        adapter = BackuperJournalAdapter(mock_journal)
        entry = {"archive_name": "docs", "volume_count": 1}
        assert adapter.expected_filename(entry) == "docs.7z"

    def test_expected_filename_volumes(self):
        from verifier.adapters_backuper import BackuperJournalAdapter
        mock_journal = MagicMock()
        adapter = BackuperJournalAdapter(mock_journal)
        entry = {"archive_name": "docs", "volume_count": 3}
        result = adapter.expected_filename(entry)
        assert isinstance(result, list)
        assert len(result) == 3
        assert "docs.7z.001" in result
        assert "docs.7z.003" in result


# ── Media Adapter Tests ──

class TestMediaJournalAdapter:
    """Test Media journal adapter key mapping."""

    def test_entry_key(self):
        from verifier.adapters_media import MediaJournalAdapter
        mock_journal = MagicMock()
        adapter = MediaJournalAdapter(mock_journal)
        entry = {"filename": "photo.jpg"}
        assert adapter.entry_key(entry) == "photo.jpg"

    def test_channel_to_key(self):
        from verifier.adapters_media import MediaJournalAdapter
        mock_journal = MagicMock()
        adapter = MediaJournalAdapter(mock_journal)
        assert adapter.channel_to_key("photo.jpg") == "photo.jpg"

    def test_channel_to_key_empty(self):
        from verifier.adapters_media import MediaJournalAdapter
        mock_journal = MagicMock()
        adapter = MediaJournalAdapter(mock_journal)
        assert adapter.channel_to_key("") is None

    def test_expected_filename(self):
        from verifier.adapters_media import MediaJournalAdapter
        mock_journal = MagicMock()
        adapter = MediaJournalAdapter(mock_journal)
        entry = {"filename": "vacation.png"}
        assert adapter.expected_filename(entry) == "vacation.png"

    def test_get_entries(self):
        from verifier.adapters_media import MediaJournalAdapter
        mock_journal = MagicMock()
        mock_journal.data = {
            "entries": [
                {"filename": "a.jpg", "status": "sent"},
                {"filename": "b.jpg", "status": "failed"},
            ]
        }
        adapter = MediaJournalAdapter(mock_journal)
        entries = adapter.get_entries()
        assert len(entries) == 2
```

**Verify:** `pytest tests/test_verifier_adapters.py -v`
**Commit:** `test(verifier): add full adapter tests for all publisher types`

---

## Summary

| Task | File | Lines (est.) | Test file |
|------|------|-------------|-----------|
| 1.1 | `verifier/__init__.py` | 15 | none |
| 1.2 | `verifier/models.py` | 55 | `tests/test_verifier_models.py` (95) |
| 1.3 | `verifier/adapters.py` | 60 | `tests/test_verifier_adapters.py` (partial) |
| 2.1 | `verifier/core.py` | 180 | `tests/test_verifier_core.py` (200) |
| 2.2 | `verifier/adapters_github.py` | 80 | `tests/test_verifier_adapters.py` |
| 2.3 | `verifier/adapters_pypi.py` | 80 | `tests/test_verifier_adapters.py` |
| 2.4 | `verifier/adapters_backuper.py` | 80 | `tests/test_verifier_adapters.py` |
| 2.4 | `verifier/adapters_media.py` | 80 | `tests/test_verifier_adapters.py` |
| 3.1 | `github_archiver.py` (modify) | +130 | manual |
| 3.2 | `tests/test_verifier_adapters.py` (final) | 200 | self |

**Total new code:** ~860 lines implementation + ~500 lines tests
**Total files:** 8 new + 1 modified + 3 test files

**Key design decisions documented in plan:**
1. **ChannelFile** is a simple dataclass — no Pydantic needed since it's internal only
2. **JournalAdapter.remove_entry()** is the only mutation — design requires report-only by default
3. **Each channel adapter** is identical in structure (navigate → scan → normalize) — only the regex patterns differ per publisher
4. **Browser integration** reuses existing `self.browser` from `GitHubArchiver` when available, falls back to `BrowserInitMixin`
5. **Channel URL resolution** tries legacy `config.channels.*` first, then `channel_registry` fallback
6. **Thorough mode** extracts filenames from `audit_channel_completeness()`'s `channel_messages` via regex, since the return format is message-based, not file-based
