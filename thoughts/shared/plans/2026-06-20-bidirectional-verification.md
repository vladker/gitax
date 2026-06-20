# Plan: Bidirectional Journal-Channel Verification

## Overview

Extend the existing `verifier/` module to support **bidirectional** comparison between journal and MAX channel. Currently it only detects entries in the journal that are missing from the channel. The extension adds:
1. Detection of entries in the channel that are missing from the journal
2. Detection of version mismatches between journal and channel
3. Bidirectional fix: add missing entries, update versions, remove stale entries

## Context

- Design doc: `thoughts/shared/designs/2026-06-20-bidirectional-verification-design.md`
- Existing verifier: `verifier/` directory (models.py, main.py, adapters.py, adapters_github.py, adapters_pypi.py, adapters_backuper.py, adapters_media.py)
- Journal module: `journal.py` (and analogs for PyPI, Backuper, Media)
- Browser scanning: `browser_max.py` — `group_messages_by_repo()`, `parse_message()`, `scan_channel_for_files()`

## Tasks

### Task 1: New data models in `verifier/models.py`

**File:** `verifier/models.py`

Add:
- `ChannelRepo` dataclass: `full_name: str`, `version: str`, `display_name: str`, `has_file: bool`, `files_complete: bool`
- `VersionMismatch` dataclass: `full_name: str`, `journal_version: str`, `channel_version: str`
- Extend `JournalDiff` with two new fields: `channel_only: list[ChannelRepo] = field(default_factory=list)`, `version_mismatches: list[VersionMismatch] = field(default_factory=list)`
- Add `has_issues` property to include new fields in the check

### Task 2: Extended adapter protocol in `verifier/adapters.py`

**File:** `verifier/adapters.py`

Add abstract methods:
- `ChannelAdapter.get_channel_repos() -> list[ChannelRepo]` — return structured repo data from channel scan
- `JournalAdapter.add_entry(full_name: str, version: str, display_name: str) -> bool` — add a new entry to the journal
- `JournalAdapter.update_version(full_name: str, new_version: str) -> bool` — update version of an existing entry

### Task 3: Implement GitHub adapters

**File:** `verifier/adapters_github.py`

In `GitHubChannelAdapter`:
- `get_channel_repos()`: Call existing `group_messages_by_repo(messages)`, convert each complete/incomplete repo to `ChannelRepo`. For complete repos: `has_file=True, files_complete=True`. For incomplete: set flags based on issue type.

In `GitHubJournalAdapter`:
- `add_entry()`: Call `self.journal.add_repository()` with status `"restored"`, version from channel, and minimal metadata.
- `update_version()`: Call `self.journal.update_repository()` with `{"version": new_version}`.

### Task 4: Implement PyPI adapters

**File:** `verifier/adapters_pypi.py`

Same pattern as Task 3, adapted for PyPI journal structure (package name instead of full_name, etc.).

### Task 5: Implement Backuper adapters

**File:** `verifier/adapters_backuper.py`

Same pattern, adapted for Backuper journal structure.

### Task 6: Implement Media adapters

**File:** `verifier/adapters_media.py`

Same pattern, adapted for Media journal structure.

### Task 7: Bidirectional verification logic in `verifier/main.py`

**File:** `verifier/main.py`

In `verify()`:
1. After existing `J` and `C` sets are built, compute:
   - `C_only = C - J` → populate `diff.channel_only` by looking up `ChannelRepo` for each full_name in `C_only`
   - `J_and_C = J ∩ C` → for each, compare versions. If different and channel version != "unknown", add to `diff.version_mismatches`

New method `fix_journal_bidirectional(diff)`:
1. For each `journal_only` entry: call `journal_adapter.remove_entry()`
2. For each `channel_only` entry: call `journal_adapter.add_entry()` with status `"restored"`
3. For each `version_mismatch`: call `journal_adapter.update_version()`
4. Return dict with counts: `{"removed": N, "added": N, "updated": N}`

Update `report()` to include new sections:
- "Channel-only entries" (missing from journal)
- "Version mismatches" (different versions)

### Task 8: Update menu entry in `github_archiver.py`

**File:** `github_archiver.py` (around line 3848-3857)

Update the fix prompt to reflect bidirectional behavior:
- Change "Исправить журнал?" to explain it will add, remove, AND update entries
- Call `fix_journal_bidirectional()` instead of `fix_journal()`
- Display all three counts in the summary

### Task 9: Tests

**File:** `tests/test_verifier_bidirectional.py` (new)

Test cases:
1. `test_diff_channel_only`: Verify `channel_only` is populated when C has entries not in J
2. `test_diff_version_mismatch`: Verify `version_mismatches` is populated when versions differ
3. `test_fix_adds_missing`: Mock journal adapter, verify `add_entry` is called for `channel_only`
4. `test_fix_updates_versions`: Mock journal adapter, verify `update_version` is called for mismatches
5. `test_fix_removes_stale`: Verify existing `remove_entry` behavior still works
6. `test_unknown_version_skipped`: Verify entries with version="unknown" in channel are not updated

## Dependencies

- Task 1 → Task 2 (models needed for adapter protocol)
- Task 2 → Tasks 3-6 (protocol needed for implementations)
- Tasks 3-6 are independent of each other
- Task 1-6 → Task 7 (all adapters needed before main logic)
- Task 7 → Task 8 (main logic needed before menu update)
- Tasks 1-8 → Task 9 (tests depend on everything)

## Execution Order

1. Task 1 (models)
2. Task 2 (adapter protocol)
3. Tasks 3, 4, 5, 6 (adapter implementations — parallel)
4. Task 7 (main verification logic)
5. Task 8 (menu update)
6. Task 9 (tests)
