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
                    r'(\S+\.(?:jpg|jpeg|png|gif|webp|bmp|tiff|mp4|mov|'
                    r'avi|mkv|webm))', text, flags=re.IGNORECASE
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

    def add_entry(self, entry_data: dict) -> bool:
        """Add a media file to the journal.

        Used for bidirectional verification when a file exists in the
        channel but is missing from the journal.
        """
        entry_data.setdefault("status", "sent")
        self.journal.data.setdefault("entries", []).append(entry_data)
        self.journal.save()
        return True

    def update_version(self, key: str, version: str) -> bool:
        """Update version of a media entry.

        Media journal doesn't track versions, so this is a no-op.
        """
        return True

    def get_stats(self) -> dict:
        return self.journal.get_stats()
