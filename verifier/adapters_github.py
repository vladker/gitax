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
        return [
            r for r in repos
            if r.get("status") in ("sent", "restored", "incomplete")
        ]

    def expected_filename(self, entry: dict) -> str | list[str]:
        full_name = entry.get("full_name", "")
        base = full_name.replace("/", "-")
        archive_size = entry.get("archive_size", 0)
        if archive_size and isinstance(archive_size, (int, float)):
            threshold = 50 * 1024 * 1024  # 50 MB
            if archive_size > threshold:
                return [
                    f"{base}.7z.001",
                    f"{base}.7z.002",
                    f"{base}.7z.003",
                ]
        return f"{base}.zip"

    def entry_key(self, entry: dict) -> str:
        return entry.get("full_name", "")

    def channel_to_key(self, filename: str) -> str | None:
        base = re.sub(
            r'\.(zip|7z(?:\.\d{3})?)$', '', filename, flags=re.IGNORECASE
        )
        if "-" in base:
            owner, repo = base.split("-", 1)
            return f"{owner}/{repo}"
        return None

    def remove_entry(self, key: str) -> bool:
        return self.journal.remove_repository(key)

    def get_stats(self) -> dict:
        return self.journal.get_stats()
