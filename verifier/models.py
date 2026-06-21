"""
Data models for the Journal-Channel Verifier.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
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
class ChannelRepo:
    """Representation of a repository/package found in the MAX channel.

    Used for bidirectional verification: entries present in the channel
    but missing from the journal.
    """
    key: str
    filename: str
    message_text: str = ""
    timestamp: str = ""
    size: str | None = None


@dataclass
class VersionMismatch:
    """Represents a version discrepancy between journal and channel."""
    key: str
    journal_version: str
    channel_version: str
    filename: str = ""


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
        """True if there are missing entries, orphans, or version mismatches."""
        return bool(
            self.in_journal_not_in_channel
            or self.in_channel_not_in_journal
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

    @property
    def mismatch_count(self) -> int:
        """Number of version mismatches."""
        return len(self.version_mismatches)
