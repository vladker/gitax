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
