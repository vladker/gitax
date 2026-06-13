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
        lines.append(f"  Верификация — {self.publisher_name}")
        lines.append(f"  Режим: {diff.stats.get('mode', 'unknown')}")
        lines.append(
            f"  Записей в журнале: {diff.stats.get('journal_entries', 0)}"
        )
        lines.append(
            f"  Файлов в канале:   {diff.stats.get('channel_files', 0)}"
        )
        lines.append("")

        if diff.incomplete_scan:
            lines.append("  ⚠ ВНИМАНИЕ: Скан неполный (частичные результаты)")
            lines.append("")

        if not diff.has_issues and not diff.in_channel_not_in_journal:
            lines.append("  ✓ Все записи журнала найдены в канале. Расхождений нет.")
        else:
            if diff.in_journal_not_in_channel:
                lines.append(
                    f"  ✗ Отсутствуют в канале ({diff.missing_count}):"
                )
                for key in diff.in_journal_not_in_channel[:20]:
                    lines.append(f"    — {key}")
                if len(diff.in_journal_not_in_channel) > 20:
                    lines.append(
                        f"    ... и ещё {len(diff.in_journal_not_in_channel) - 20}"
                    )
                lines.append("")

            if diff.version_mismatches:
                lines.append(
                    f"  ⚠ Расхождения версий ({len(diff.version_mismatches)}):"
                )
                for mm in diff.version_mismatches[:10]:
                    lines.append(f"    — {mm}")
                lines.append("")

            if diff.in_channel_not_in_journal:
                lines.append(
                    f"  ℹ Орфаны в канале ({diff.orphan_count}):"
                )
                for key in diff.in_channel_not_in_journal[:10]:
                    lines.append(f"    — {key}")
                if len(diff.in_channel_not_in_journal) > 10:
                    lines.append(
                        f"    ... и ещё {len(diff.in_channel_not_in_journal) - 10}"
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
