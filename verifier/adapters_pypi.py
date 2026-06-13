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
            if (entry.get("name") == name
                    and entry.get("version") == version):
                self.journal.data["libraries"].pop(i)
                self.journal.save()
                return True
        return False

    def get_stats(self) -> dict:
        return self.journal.get_stats()
