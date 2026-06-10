# Journal Clear — Implementation Plan

**Goal:** Add `clear()` method to all 3 journal classes (`Journal`, `MediaJournal`, `DownloadJournal`) and add a `_manage_journals()` sub-menu in `GitHubArchiver` to let users clear journals from the menu.

**Architecture:**
- Each journal class gets `clear()` that resets data via existing `_create_empty()`, calls `self.save()`, and (for `Journal`) logs via `LogMixin`.
- A new `_manage_journals()` method in `GitHubArchiver` (following the `_manage_ignore_list()` pattern at line 912) shows journal stats and offers clear options with confirmation.
- Menu item `[10] Очистить журналы` added between `[9] Выход` and the closing separator.

**Design:** `thoughts/shared/designs/2026-06-09-journal-clear-design.md`

---

## Dependency Graph

```
Batch 1 (parallel — 4 implementers):
  1.1  journal.py + tests/test_journal.py
  1.2  media_archiver.py + tests/test_media_archiver.py
  1.3  channel_downloader.py + tests/test_channel_downloader.py (append)
  1.4  github_archiver.py (no test — UI change)
```

All 4 tasks are independent (no cross-imports between the changes).

---

## Batch 1: All Changes (parallel — 4 implementers)

### Task 1.1: Journal.clear() + tests
**File:** `journal.py`
**Test:** `tests/test_journal.py` (NEW file)
**Depends:** none

**Implementation (edit journal.py):**

Insert `clear()` method after `_create_empty()` (after line 81, before line 82 blank):

```python
    def clear(self):
        """Очистить журнал — сбросить все данные"""
        self.data = self._create_empty()
        self.save()
        self.logger.info("Journal cleared")

```

Uses `edit` with oldString/newString:
- oldString: `        }\n\n    def save(self):`
- newString: `        }\n\n    def clear(self):\n        """Очистить журнал — сбросить все данные"""\n        self.data = self._create_empty()\n        self.save()\n        self.logger.info("Journal cleared")\n\n    def save(self):`

**Test file (NEW — `tests/test_journal.py`):**

```python
# -*- coding: utf-8 -*-
"""
Tests for Journal module — clear() method.
"""

import json
import os


class TestJournalClear:
    """Tests for Journal.clear()"""

    def test_clear_resets_data(self, tmp_path):
        """clear() resets journal to empty state"""
        from journal import Journal
        j = Journal(str(tmp_path / "journal.json"))
        j.add_repository({"full_name": "test/repo", "status": "sent"})
        assert j.get_count() > 0
        j.clear()
        assert j.get_count() == 0
        assert j.get_stats()["total"] == 0

    def test_clear_empties_file_on_disk(self, tmp_path):
        """clear() writes empty journal to disk"""
        from journal import Journal
        fp = tmp_path / "journal.json"
        j = Journal(str(fp))
        j.add_repository({"full_name": "test/repo", "status": "sent"})
        j.clear()
        # Re-read from disk — should be empty
        j2 = Journal(str(fp))
        assert j2.get_count() == 0

    def test_clear_on_empty_journal_does_not_crash(self, tmp_path):
        """clear() on empty journal does not raise"""
        from journal import Journal
        j = Journal(str(tmp_path / "journal.json"))
        j.clear()  # Should not raise
```

**Verify:**
```bash
pytest tests/test_journal.py -v
```

**Commit:** `feat(journal): add clear() method to Journal class`


### Task 1.2: MediaJournal.clear() + tests
**File:** `media_archiver.py`
**Test:** `tests/test_media_archiver.py` (NEW file)
**Depends:** none

**Implementation (edit media_archiver.py):**

Insert `clear()` method after `_create_empty()` (after line 70, before line 72 `save`):

```python
    def clear(self):
        """Очистить журнал — сбросить все данные"""
        self.data = self._create_empty()
        self.save()

```

Uses `edit` with oldString/newString:
- oldString: `return {"entries": []}\n\n    def save(self):`
- newString: `return {"entries": []}\n\n    def clear(self):\n        """Очистить журнал — сбросить все данные"""\n        self.data = self._create_empty()\n        self.save()\n\n    def save(self):`

Note: `MediaJournal` does NOT extend `LogMixin`, so no logger call.

**Test file (NEW — `tests/test_media_archiver.py`):**

```python
# -*- coding: utf-8 -*-
"""
Tests for MediaJournal module — clear() method.
"""

import json
import os


class TestMediaJournalClear:
    """Tests for MediaJournal.clear()"""

    def test_clear_resets_data(self, tmp_path):
        """clear() resets media journal to empty state"""
        from media_archiver import MediaJournal
        j = MediaJournal(str(tmp_path / "media_journal.json"))
        j.mark_sent("photo.jpg", 1024)
        assert j.get_stats()["total"] == 1
        j.clear()
        assert j.get_stats()["total"] == 0

    def test_clear_empties_file_on_disk(self, tmp_path):
        """clear() writes empty media journal to disk"""
        from media_archiver import MediaJournal
        fp = tmp_path / "media_journal.json"
        j = MediaJournal(str(fp))
        j.mark_sent("photo.jpg", 1024)
        j.clear()
        # Re-read from disk
        j2 = MediaJournal(str(fp))
        assert j2.get_stats()["total"] == 0

    def test_clear_on_empty_journal_does_not_crash(self, tmp_path):
        """clear() on empty media journal does not raise"""
        from media_archiver import MediaJournal
        j = MediaJournal(str(tmp_path / "media_journal.json"))
        j.clear()  # Should not raise
```

**Verify:**
```bash
pytest tests/test_media_archiver.py -v
```

**Commit:** `feat(media-archiver): add clear() method to MediaJournal`


### Task 1.3: DownloadJournal.clear() + tests
**File:** `channel_downloader.py`
**Test:** `tests/test_channel_downloader.py` (append to existing)
**Depends:** none

**Implementation (edit channel_downloader.py):**

Insert `clear()` method after `_create_empty()` (after line 101, before line 103 `save`):

```python
    def clear(self):
        """Очистить журнал — сбросить все данные"""
        self.data = self._create_empty()
        self.save()

```

Uses `edit` with oldString/newString:
- oldString: `        }\n\n    def save(self):`
- newString: `        }\n\n    def clear(self):\n        """Очистить журнал — сбросить все данные"""\n        self.data = self._create_empty()\n        self.save()\n\n    def save(self):`

Note: `DownloadJournal` does NOT extend `LogMixin`, so no logger call.

**Test addition (append to end of `tests/test_channel_downloader.py`, before the `if __name__` block or at the end of the file):**

```python

# ── DownloadJournal Clear Tests ──

class TestDownloadJournalClear:
    """Tests for DownloadJournal.clear()"""

    def test_clear_resets_data(self, tmp_path):
        """clear() resets download journal to empty state"""
        from channel_downloader import DownloadJournal
        j = DownloadJournal(str(tmp_path / "journal.json"))
        j.mark_downloaded("file.zip", 100, "/tmp/file.zip")
        assert j.get_stats()["total"] == 1
        j.clear()
        assert j.get_stats()["total"] == 0

    def test_clear_empties_file_on_disk(self, tmp_path):
        """clear() writes empty download journal to disk"""
        from channel_downloader import DownloadJournal
        fp = tmp_path / "journal.json"
        j = DownloadJournal(str(fp))
        j.mark_downloaded("file.zip", 100, "/tmp/file.zip")
        j.clear()
        # Re-read from disk
        j2 = DownloadJournal(str(fp))
        assert j2.get_stats()["total"] == 0

    def test_clear_on_empty_journal_does_not_crash(self, tmp_path):
        """clear() on empty download journal does not raise"""
        from channel_downloader import DownloadJournal
        j = DownloadJournal(str(tmp_path / "journal.json"))
        j.clear()  # Should not raise
```

**Verify:**
```bash
pytest tests/test_channel_downloader.py -v -k "Clear"
```

**Commit:** `feat(channel-downloader): add clear() method to DownloadJournal`


### Task 1.4: github_archiver.py — menu + _manage_journals()
**File:** `github_archiver.py`
**Test:** none (UI-level change, tested manually)
**Depends:** none

This task makes 4 edits to `github_archiver.py`:

**Edit 1 — Add menu item (after line 358, between `[9] Выход` and blank line):**

oldString:
```
        print("  [9] Выход")
        print()
```

newString:
```
        print("  [9] Выход")
        print("  [10] Очистить журналы")
        print()
```

**Edit 2 — Add `_manage_journals()` method (after line 965, before the Audit section header at line 966):**

oldString:
```
            elif choice == '3':
                break

    # ──────────────────────────────────────────────
    # Audit & Restore Publications
    # ──────────────────────────────────────────────
```

newString:
```
            elif choice == '3':
                break

    def _manage_journals(self):
        """Управление очисткой журналов"""
        from media_archiver import MediaJournal
        from channel_downloader import DownloadJournal

        while True:
            os.system('cls' if os.name == 'nt' else 'clear')
            print("\n" + "═" * 60)
            print("Очистка журналов")
            print("═" * 60)

            # Получить статистику каждого журнала
            j_stats = self.journal.get_stats()
            mj = MediaJournal("media_journal.json")
            mj_stats = mj.get_stats()
            dj = DownloadJournal("download_journal.json")
            dj_stats = dj.get_stats()

            print(f"\n  Текущее состояние журналов:")
            print(f"  [1] journal.json — {j_stats['total']} репозиториев "
                  f"({j_stats['sent']} отправлено, {j_stats['failed']} ошибок)")
            print(f"  [2] media_journal.json — {mj_stats['total']} файлов "
                  f"({mj_stats['sent']} отправлено, {mj_stats['failed']} ошибок)")
            print(f"  [3] download_journal.json — {dj_stats['total']} файлов "
                  f"({dj_stats['downloaded']} скачано, {dj_stats['failed']} ошибок)")

            print()
            print("  [1] Очистить journal.json")
            print("  [2] Очистить media_journal.json")
            print("  [3] Очистить download_journal.json")
            print("  [4] Очистить ВСЕ журналы")
            print("  [0] Назад")
            print()

            choice = input("  Ваш выбор [0/1/2/3/4]: ").strip()

            if choice == '0':
                break

            elif choice == '1':
                confirm = input("\n  Очистить journal.json? [y/N]: ").strip().lower()
                if confirm in ('y', 'yes', 'д', 'да'):
                    self.journal.clear()
                    print("  ✓ journal.json очищен")
                else:
                    print("  Отменено")
                input("\n  Нажмите Enter для продолжения...")

            elif choice == '2':
                confirm = input("\n  Очистить media_journal.json? [y/N]: ").strip().lower()
                if confirm in ('y', 'yes', 'д', 'да'):
                    MediaJournal("media_journal.json").clear()
                    print("  ✓ media_journal.json очищен")
                else:
                    print("  Отменено")
                input("\n  Нажмите Enter для продолжения...")

            elif choice == '3':
                confirm = input("\n  Очистить download_journal.json? [y/N]: ").strip().lower()
                if confirm in ('y', 'yes', 'д', 'да'):
                    DownloadJournal("download_journal.json").clear()
                    print("  ✓ download_journal.json очищен")
                else:
                    print("  Отменено")
                input("\n  Нажмите Enter для продолжения...")

            elif choice == '4':
                print("\n  ⚠ ВНИМАНИЕ: Будут очищены ВСЕ журналы!")
                confirm = input("  Введите 'ДА' для подтверждения: ").strip().lower()
                if confirm in ('да', 'yes', 'дa'):
                    self.journal.clear()
                    MediaJournal("media_journal.json").clear()
                    DownloadJournal("download_journal.json").clear()
                    print("  ✓ Все журналы очищены")
                else:
                    print("  Отменено")
                input("\n  Нажмите Enter для продолжения...")

    # ──────────────────────────────────────────────
    # Audit & Restore Publications
    # ──────────────────────────────────────────────
```

**Edit 3 — Update prompt in `run()` (line 1965) and add handler + error message:**

oldString:
```
            choice = input("  Выберите действие [1-9]: ").strip()

            if choice == '1':
                self.sync_repositories()
            elif choice == '2':
                self.load_new_repositories()
            elif choice == '3':
                self._manage_ignore_list()
            elif choice == '4':
                self.audit_and_restore_publications()
            elif choice == '5':
                self.export_messages_to_file()
            elif choice == '6':
                self.run_media_archiver()
            elif choice == '7':
                self.download_channel_files()
            elif choice == '8':
                self.delete_all_messages_in_channel()
            elif choice == '9':
                print("\n  До свидания!\n")
                break
            else:
                print("\n  Неверный выбор. Нажмите 1..9.")
                time.sleep(1)
```

newString:
```
            choice = input("  Выберите действие [1-10]: ").strip()

            if choice == '1':
                self.sync_repositories()
            elif choice == '2':
                self.load_new_repositories()
            elif choice == '3':
                self._manage_ignore_list()
            elif choice == '4':
                self.audit_and_restore_publications()
            elif choice == '5':
                self.export_messages_to_file()
            elif choice == '6':
                self.run_media_archiver()
            elif choice == '7':
                self.download_channel_files()
            elif choice == '8':
                self.delete_all_messages_in_channel()
            elif choice == '9':
                print("\n  До свидания!\n")
                break
            elif choice == '10':
                self._manage_journals()
            else:
                print("\n  Неверный выбор. Нажмите 1..10.")
                time.sleep(1)
```

**Manual verification:** Run `python github_archiver.py`, check that `[10] Очистить журналы` appears in menu, test each option 1-4 to verify clear works, test option 4 double confirmation.

**Commit:** `feat(ui): add journal clear sub-menu to GitHub Archiver`
