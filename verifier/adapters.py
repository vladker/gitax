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
