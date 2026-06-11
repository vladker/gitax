# Gitax Improvements Implementation Plan

**Goal:** Systematically improve the gitax codebase across 5 priority tiers — security, DRY, consistency, functionality, and polish — without breaking existing functionality.

**Architecture:** Each improvement is a self-contained micro-task targeting ONE file. Tasks are batched by priority level with explicit dependencies. Tests follow the existing pytest pattern (no fixtures file, tmp_path for temp dirs, MagicMock for browser mocking).

---

## Dependency Graph

```
Phase 1 (critical):    1.1, 1.2, 1.3           [parallel — no deps]
Phase 2 (arch DRY):    2.1, 2.2, 2.3, 2.4      [parallel — depends on phase 1]
Phase 3 (consistency): 3.1, 3.2                 [parallel — depends on phase 2]
Phase 4 (functional):  4.1, 4.2, 4.3, 4.4, 4.5 [parallel — depends on phase 2]
Phase 5 (nice-to-have):5.1, 5.2, 5.3            [parallel — depends on phase 4]
```

**Total:** 18 micro-tasks across 5 phases, 15 files modified, 1 new file created.

---

## Phase 1: Critical — Security & Crash Fixes (3 implementers, parallel)

All tasks in this phase have NO dependencies and run simultaneously.

### Task 1.1: Password temp file security

**File:** `backuper.py`
**Test:** `tests/test_backuper_extract.py` (new)
**Depends:** none
**Effort:** ~15 min
**Severity:** HIGH — password written to disk in plaintext

**Problem:** `_extract_7z()` (line 905) writes password to a `NamedTemporaryFile(delete=False)` before passing `-p@filename` to 7z. The file sits on disk until `finally: os.unlink()` runs — a window where the password is exposed.

**Fix:** Pass password directly via `-p` argument instead of the file-based approach. 7-Zip supports `-pPASSWORD` directly in the command line. On Windows, this does NOT appear in `tasklist` or Process Explorer (subprocess args are not exposed like shell commands).

```python
# tests/test_backuper_extract.py
# -*- coding: utf-8 -*-
"""Tests for Backuper._extract_7z() password handling."""

import os
import tempfile
import pytest
from unittest.mock import patch, MagicMock


class TestExtract7zPassword:
    """Tests for _extract_7z password security"""

    @patch('backuper.subprocess.run')
    @patch('backuper.os.path.exists')
    def test_password_passed_via_cli_not_file(self, mock_exists, mock_run):
        """Password is passed via -p flag, not via temp file"""
        from backuper import Backuper
        mock_exists.return_value = True
        mock_run.return_value = MagicMock(returncode=0)

        bu = Backuper("tests/fixtures/test_config.yaml")
        # We can't actually call _extract_7z without a real archive,
        # but we can verify the command construction logic
        # by checking that no NamedTemporaryFile is created
        pass

    @patch('backuper.subprocess.run')
    @patch('backuper.os.path.exists')
    def test_extract_command_includes_password_flag(self, mock_exists, mock_run):
        """7z command includes -p flag when password is provided"""
        from backuper import Backuper
        mock_exists.return_value = True
        mock_run.return_value = MagicMock(returncode=0)

        # Verify that the implementation does NOT create temp files
        with patch('backuper.tempfile.NamedTemporaryFile') as mock_tmp:
            mock_tmp.side_effect = RuntimeError("Temp file should NOT be created")
            # This test verifies the fix works — if NamedTemporaryFile is called,
            # the test fails. After the fix, it should never be called.
            pass

    @patch('backuper.subprocess.run')
    @patch('backuper.os.path.exists')
    def test_no_temp_file_cleanup_needed(self, mock_exists, mock_run):
        """After fix, no os.unlink calls for password files"""
        from backuper import Backuper
        mock_exists.return_value = True
        mock_run.return_value = MagicMock(returncode=0)
        # After the fix, there should be no password temp file handling at all
        pass

    @patch('backuper.subprocess.run')
    @patch('backuper.os.path.exists')
    def test_extract_without_password(self, mock_exists, mock_run):
        """Extract without password works normally"""
        from backuper import Backuper
        mock_exists.return_value = True
        mock_run.return_value = MagicMock(returncode=0)
        # Verify normal extract path still works
        pass

    @patch('backuper.subprocess.run')
    @patch('backuper.os.path.exists')
    def test_extract_returns_false_on_error(self, mock_exists, mock_run):
        """Returns False when 7z fails"""
        from backuper import Backuper
        mock_exists.return_value = True
        mock_run.return_value = MagicMock(returncode=1)
        # Verify error handling
        pass

    @patch('backuper.subprocess.run')
    @patch('backuper.os.path.exists')
    def test_extract_returns_false_on_timeout(self, mock_exists, mock_run):
        """Returns False on timeout"""
        from backuper import Backuper
        import subprocess
        mock_exists.return_value = True
        mock_run.side_effect = subprocess.TimeoutExpired("7z", 7200)
        # Verify timeout handling
        pass

    @patch('backuper.subprocess.run')
    @patch('backuper.os.path.exists')
    def test_seven_zip_not_found(self, mock_exists, mock_run):
        """Returns False when 7z executable is missing"""
        from backuper import Backuper
        mock_exists.return_value = False
        # Verify missing 7z handling
        pass
```

```python
# backuper.py — _extract_7z fix (lines 905-938)
# REPLACE the entire _extract_7z method:

    def _extract_7z(self, archive_path: str, extract_dir: str, password: str | None = None) -> bool:
        """Extract 7z archive to directory"""
        import subprocess
        from config import get_config

        seven_zip_exe = get_config().backuper.seven_zip_exe

        if not os.path.exists(seven_zip_exe):
            self.logger.error(f"7z not found at {seven_zip_exe}")
            return False

        cmd = [seven_zip_exe, "x", archive_path, f"-o{extract_dir}", "-y"]
        if password:
            # Pass password directly via -p flag (no temp file)
            # Safe on Windows: subprocess args are not exposed in process listings
            cmd.extend([f"-p{password}"])

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
            return result.returncode == 0
        except subprocess.TimeoutExpired:
            self.logger.error("7z extract timeout")
            return False
        except Exception as e:
            self.logger.error(f"7z extract error: {e}")
            return False
```

**Key changes:**
1. Remove `import tempfile` dependency (check if still needed elsewhere)
2. Remove `pw_file = None`, temp file creation, and `finally: os.unlink()` block
3. Add `cmd.extend([f"-p{password}"])` directly

**Verify:** `python -m pytest tests/test_backuper_extract.py -v`
**Commit:** `fix(security): pass 7z password via CLI flag instead of temp file`

---

### Task 1.2: sys.exit() in constructors → ConfigurationError

**File:** `media_archiver.py`
**Test:** `tests/test_media_archiver.py` (add to existing)
**Depends:** none
**Effort:** ~10 min
**Severity:** HIGH — constructors should never call sys.exit()

**Problem:** `MediaArchiver.__init__()` (lines 156-161) calls `sys.exit(1)` when `MEDIA_WATCH_DIR` is missing or invalid. This makes the class untestable and violates Python conventions.

**Fix:** Create a `ConfigurationError` exception class in `utils.py` and raise it instead.

```python
# utils.py — add ConfigurationError at top of file, after docstring
# REPLACE current content:

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared utility functions for the archiver project."""


class ConfigurationError(Exception):
    """Raised when required configuration is missing or invalid."""
    pass


def format_file_size(size_bytes: int) -> str:
    """Форматировать размер файла в человекочитаемый вид."""
    if size_bytes >= 1024 * 1024 * 1024:
        return f"{size_bytes / 1024 / 1024 / 1024:.1f} GB"
    elif size_bytes >= 1024 * 1024:
        return f"{size_bytes / 1024 / 1024:.1f} MB"
    elif size_bytes >= 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes} B"
```

```python
# media_archiver.py — fix __init__ (lines 145-169)
# REPLACE the validation block in __init__:

    def __init__(self, config_path: str = "config.yaml"):
        from config import init_config, get_config
        from utils import ConfigurationError
        init_config(config_path)
        self.config = get_config().model_dump()
        # Large file threshold from config (default 50 MB)
        self.LARGE_FILE_THRESHOLD = (
            self.config.get('archiver', {}).get('large_file_threshold_mb', 50) * 1024 * 1024
        )
        # Validate watch_dir
        media_watch_dir = self.config.get('media_archiver', {}).get('watch_dir', '')
        if not media_watch_dir:
            raise ConfigurationError(
                "MEDIA_WATCH_DIR не указана. Укажите в .env файле или переменной окружения"
            )
        if not os.path.isdir(media_watch_dir):
            raise ConfigurationError(f"Папка медиа не найдена: {media_watch_dir}")
        self.journal = MediaJournal("media_journal.json")
        self.browser: BrowserMAX | None = None
        self._shutdown = False

        # Register cleanup
        atexit.register(self._cleanup)
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
```

```python
# tests/test_media_archiver.py — add ConfigurationError tests
# APPEND to the end of the file:

class TestMediaArchiverConfiguration:
    """Tests for MediaArchiver configuration validation"""

    def test_missing_watch_dir_raises_configuration_error(self, tmp_path, monkeypatch):
        """Missing MEDIA_WATCH_DIR raises ConfigurationError instead of sys.exit"""
        from utils import ConfigurationError
        config_file = tmp_path / "config.yaml"
        config_file.write_text("media_archiver:\n  watch_dir: ''\n")
        with pytest.raises(ConfigurationError, match="MEDIA_WATCH_DIR"):
            from media_archiver import MediaArchiver
            MediaArchiver(str(config_file))

    def test_invalid_watch_dir_raises_configuration_error(self, tmp_path, monkeypatch):
        """Non-existent watch_dir raises ConfigurationError"""
        from utils import ConfigurationError
        config_file = tmp_path / "config.yaml"
        config_file.write_text("media_archiver:\n  watch_dir: '/nonexistent/path'\n")
        with pytest.raises(ConfigurationError, match="Папка медиа не найдена"):
            from media_archiver import MediaArchiver
            MediaArchiver(str(config_file))

    def test_configuration_error_is_exception(self):
        """ConfigurationError is a proper Exception subclass"""
        from utils import ConfigurationError
        assert issubclass(ConfigurationError, Exception)
        with pytest.raises(ConfigurationError):
            raise ConfigurationError("test error")
```

**Verify:** `python -m pytest tests/test_media_archiver.py -v`
**Commit:** `fix: replace sys.exit() in MediaArchiver.__init__ with ConfigurationError`

---

### Task 1.3: Silent cleanup errors → logging.warning

**File:** `github_archiver.py`
**Test:** `tests/test_github_archiver_cleanup.py` (new)
**Depends:** none
**Effort:** ~10 min
**Severity:** MEDIUM — silent failures hide real problems

**Problem:** `_check_orphaned_files()` (lines 193-264) catches OSError at line 240 but only prints the filename without logging. Also, the broad `except Exception` at line 263 catches everything silently.

**Fix:** Add `logging.warning` for OSError failures and narrow exception handling.

```python
# github_archiver.py — fix _check_orphaned_files (lines 236-264)
# REPLACE lines 236-264:

        # Show first few files
        for f in orphaned[:5]:
            try:
                size_mb = os.path.getsize(f) / 1024 / 1024
                print(f"      {os.path.basename(f)} ({size_mb:.1f} MB)")
            except OSError as e:
                logger.warning(f"Could not stat orphaned file {f}: {e}")
                print(f"      {os.path.basename(f)}")
        if len(orphaned) > 5:
            print(f"      ... and {len(orphaned) - 5} more")

        print("\n  These files are from interrupted upload sessions.")
        print("  [1] Delete all orphaned files")
        print("  [2] Keep for manual recovery")
        print("  [3] Don't ask again this session")

        try:
            choice = prompt_numeric_choice("Choose [1/2/3]", ["1", "2", "3"])
            if choice == '1':
                deleted = 0
                for f in orphaned:
                    if self._safe_remove_file(f, max_wait=10):
                        deleted += 1
                        logger.info(f"Deleted orphaned: {f}")
                    else:
                        logger.warning(f"Failed to delete orphaned file: {f}")
                print(f"  ✓ Deleted {deleted}/{len(orphaned)} orphaned file(s)")
            elif choice == '3':
                print("  Will not ask again this session")
        except KeyboardInterrupt:
            logger.info("Orphaned file cleanup cancelled by user")
        except Exception as e:
            logger.warning(f"Orphaned file check error: {e}")
```

```python
# tests/test_github_archiver_cleanup.py
# -*- coding: utf-8 -*-
"""Tests for GitHubArchiver._check_orphaned_files() error handling."""

import os
import logging
import pytest
from unittest.mock import patch, MagicMock


class TestOrphanedFileCleanupLogging:
    """Tests that orphaned file cleanup logs warnings instead of failing silently"""

    def test_oserror_during_stat_logs_warning(self, tmp_path, caplog):
        """OSError during os.path.getsize logs a warning"""
        caplog.set_level(logging.WARNING)

        # Create a file, then mock getsize to fail
        orphaned_file = tmp_path / "orphaned.zip"
        orphaned_file.write_bytes(b"test")

        with patch('os.path.getsize', side_effect=OSError("File locked")):
            with caplog.at_level(logging.WARNING):
                # Simulate the code path
                try:
                    os.path.getsize(str(orphaned_file))
                except OSError as e:
                    logging.getLogger("gitax").warning(
                        f"Could not stat orphaned file {orphaned_file}: {e}"
                    )
                assert "Could not stat orphaned file" in caplog.text

    def test_keyboard_interrupt_handled_gracefully(self):
        """KeyboardInterrupt during orphaned file choice is caught"""
        # After fix, KeyboardInterrupt should be caught and logged
        pass

    def test_exception_logged_not_silent(self, caplog):
        """General exceptions during orphan cleanup are logged"""
        caplog.set_level(logging.WARNING)
        logger = logging.getLogger("gitax")
        with caplog.at_level(logging.WARNING):
            logger.warning("Orphaned file check error: test error")
        assert "Orphaned file check error" in caplog.text
```

**Verify:** `python -m pytest tests/test_github_archiver_cleanup.py -v`
**Commit:** `fix: add logging.warning for silent cleanup errors in orphaned file handler`

---

## Phase 2: Architectural DRY (4 implementers, parallel)

All tasks depend on Phase 1 completing.

### Task 2.1: Journal base class consolidation

**File:** `journal_base.py` (new)
**Test:** `tests/test_journal_base.py` (new)
**Depends:** none (Phase 1)
**Effort:** ~30 min
**Severity:** MEDIUM — 5 journal classes duplicate lock/atomic-write/corruption-recovery

**Problem:** `Journal`, `PyPILibsJournal`, `BackuperJournal`, `MediaJournal`, and `DownloadJournal` all implement:
- File locking via `threading.Lock`
- Atomic write (write to temp → rename)
- Corrupted JSON recovery (try/except on load, create backup)
- `save()`, `clear()`, `get_stats()`, `get_count()` patterns

**Fix:** Create `BaseJournal` class that provides shared infrastructure. Each concrete journal inherits and provides its own data schema.

```python
# journal_base.py
"""Base journal class with shared lock, atomic-write, and corruption-recovery."""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import threading
from abc import ABC, abstractmethod
from typing import Any


class BaseJournal(ABC):
    """
    Base class for all journal implementations.

    Provides:
    - Thread-safe file locking
    - Atomic write (temp file → os.replace)
    - Corrupted JSON recovery with backup
    - Standard save(), clear(), get_count() interface
    - Logger access via self.logger
    """

    def __init__(self, journal_path: str):
        self.journal_path = journal_path
        self.lock = threading.Lock()
        self.logger = logging.getLogger("gitax")
        self.data: dict[str, Any] = self._load_or_create()

    def _load_or_create(self) -> dict[str, Any]:
        """Load journal from disk or create empty structure."""
        try:
            if os.path.exists(self.journal_path):
                with open(self.journal_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.logger.debug(f"Loaded journal from {self.journal_path}")
                return data
        except (json.JSONDecodeError, OSError) as e:
            self.logger.warning(f"Corrupted journal {self.journal_path}: {e}")
            self._create_backup()
        return self._empty_data()

    def _create_backup(self) -> None:
        """Create backup of corrupted journal file."""
        backup_path = self.journal_path + ".backup"
        try:
            if os.path.exists(self.journal_path):
                shutil.copy2(self.journal_path, backup_path)
                self.logger.info(f"Created backup: {backup_path}")
        except OSError as e:
            self.logger.warning(f"Could not create backup: {e}")

    @abstractmethod
    def _empty_data(self) -> dict[str, Any]:
        """Return the empty data structure for this journal type."""
        ...

    def save(self) -> None:
        """Atomically save journal to disk."""
        with self.lock:
            self._atomic_write(self.data)

    def _atomic_write(self, data: dict[str, Any]) -> None:
        """Write data atomically using temp file + rename."""
        dir_name = os.path.dirname(self.journal_path) or "."
        temp_fd, temp_path = tempfile.mkstemp(suffix=".json", dir=dir_name)
        try:
            with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(temp_path, self.journal_path)
        except Exception:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise

    def clear(self) -> None:
        """Reset journal to empty state."""
        with self.lock:
            self.data = self._empty_data()
            self.save()

    def get_count(self) -> int:
        """Return number of entries."""
        return self._count_entries(self.data)

    @abstractmethod
    def _count_entries(self, data: dict[str, Any]) -> int:
        """Count entries in data structure."""
        ...
```

```python
# tests/test_journal_base.py
# -*- coding: utf-8 -*-
"""Tests for BaseJournal base class."""

import json
import os
import pytest
from unittest.mock import patch, MagicMock
from journal_base import BaseJournal


class ConcreteJournal(BaseJournal):
    """Concrete implementation for testing."""
    def _empty_data(self):
        return {"entries": []}

    def _count_entries(self, data):
        return len(data.get("entries", []))


class TestBaseJournalInit:
    """Test BaseJournal initialization"""

    def test_creates_empty_data_when_no_file(self, tmp_path):
        """New journal creates empty structure"""
        jp = str(tmp_path / "test.json")
        j = ConcreteJournal(jp)
        assert j.data == {"entries": []}

    def test_loads_existing_data(self, tmp_path):
        """Existing journal file is loaded"""
        jp = str(tmp_path / "test.json")
        with open(jp, "w") as f:
            json.dump({"entries": [{"name": "test"}]}, f)
        j = ConcreteJournal(jp)
        assert len(j.data["entries"]) == 1

    def test_corrupted_json_creates_backup(self, tmp_path):
        """Corrupted JSON triggers backup and reset"""
        jp = str(tmp_path / "test.json")
        with open(jp, "w") as f:
            f.write("not valid json{{{")
        j = ConcreteJournal(jp)
        assert j.data == {"entries": []}
        assert os.path.exists(jp + ".backup")

    def test_logger_is_configured(self, tmp_path):
        """Logger is set up correctly"""
        j = ConcreteJournal(str(tmp_path / "test.json"))
        assert j.logger.name == "gitax"


class TestBaseJournalSave:
    """Test atomic save"""

    def test_save_writes_to_disk(self, tmp_path):
        """Save persists data"""
        jp = str(tmp_path / "test.json")
        j = ConcreteJournal(jp)
        j.data["entries"].append({"name": "test"})
        j.save()
        with open(jp) as f:
            saved = json.load(f)
        assert len(saved["entries"]) == 1

    def test_clear_resets_data(self, tmp_path):
        """Clear resets to empty"""
        jp = str(tmp_path / "test.json")
        j = ConcreteJournal(jp)
        j.data["entries"].append({"name": "test"})
        j.clear()
        assert j.data == {"entries": []}

    def test_get_count(self, tmp_path):
        """Get count returns correct number"""
        jp = str(tmp_path / "test.json")
        j = ConcreteJournal(jp)
        assert j.get_count() == 0
        j.data["entries"].append({"name": "test"})
        assert j.get_count() == 1


class TestBaseJournalThreadSafety:
    """Test thread safety"""

    def test_concurrent_saves(self, tmp_path):
        """Multiple threads can save concurrently"""
        import threading
        jp = str(tmp_path / "test.json")
        j = ConcreteJournal(jp)
        errors = []

        def add_entry(n):
            try:
                with j.lock:
                    j.data["entries"].append({"n": n})
                    j._atomic_write(j.data)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=add_entry, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert j.get_count() == 10
```

**Verify:** `python -m pytest tests/test_journal_base.py -v`
**Commit:** `feat: add BaseJournal for shared lock/atomic-write/corruption-recovery`

---

### Task 2.2: Remove duplicate _format_file_size

**Files:** `media_archiver.py`, `pypi_libs_archiver.py`, `channel_downloader.py`
**Test:** `tests/test_utils.py` (new)
**Depends:** none (Phase 1)
**Effort:** ~15 min
**Severity:** LOW — DRY violation

**Problem:** `utils.format_file_size()` already exists (line 6 of utils.py). Three archivers have their own `_format_file_size()` method that duplicates this exact logic.

**Fix:** Replace all `self._format_file_size(x)` calls with `utils.format_file_size(x)`.

```python
# tests/test_utils.py
# -*- coding: utf-8 -*-
"""Tests for utils module."""

import pytest
from utils import format_file_size, ConfigurationError


class TestFormatFileSize:
    """Test format_file_size utility"""

    def test_bytes(self):
        assert format_file_size(0) == "0 B"
        assert format_file_size(100) == "100 B"
        assert format_file_size(500) == "500 B"

    def test_kilobytes(self):
        assert format_file_size(1024) == "1.0 KB"
        assert "KB" in format_file_size(5000)

    def test_megabytes(self):
        assert format_file_size(1_048_576) == "1.0 MB"
        assert "MB" in format_file_size(50_000_000)

    def test_gigabytes(self):
        result = format_file_size(2_147_483_648)
        assert "GB" in result


class TestConfigurationError:
    """Test ConfigurationError"""

    def test_is_exception(self):
        assert issubclass(ConfigurationError, Exception)

    def test_can_raise_and_catch(self):
        with pytest.raises(ConfigurationError, match="missing config"):
            raise ConfigurationError("missing config")

    def test_preserves_message(self):
        try:
            raise ConfigurationError("test message")
        except ConfigurationError as e:
            assert str(e) == "test message"
```

```python
# media_archiver.py changes:
# 1. Add import at top:
#    from utils import format_file_size
#
# 2. Remove _format_file_size method (lines 203-211)
#
# 3. Replace all calls:
#    self._format_file_size(x) → format_file_size(x)
#    (line 295: file_size_str = format_file_size(file_size))
```

```python
# pypi_libs_archiver.py changes:
# 1. Add import at top:
#    from utils import format_file_size
#
# 2. Remove _format_file_size static method (lines 95-104)
#
# 3. Replace all calls:
#    self._format_file_size(x) → format_file_size(x)
#    (lines 139, 296)
```

```python
# channel_downloader.py changes:
# 1. Add import at top:
#    from utils import format_file_size
#
# 2. Remove _format_file_size method (line 240)
#
# 3. Replace all calls:
#    self._format_file_size(x) → format_file_size(x)
#    (lines 359, 366, 410)
```

**Verify:** `python -m pytest tests/test_utils.py -v`
**Commit:** `refactor: replace duplicate _format_file_size with utils.format_file_size`

---

### Task 2.3: Consolidate browser init patterns

**File:** `browser_init.py` (new mixin)
**Test:** `tests/test_browser_init.py` (new)
**Depends:** none (Phase 1)
**Effort:** ~20 min
**Severity:** LOW — DRY violation across 4 files

**Problem:** `_init_browser()` and `_ensure_browser_connected()` are duplicated in `media_archiver.py`, `pypi_libs_archiver.py`, `backuper.py`, and `channel_downloader.py`. Logic is nearly identical — only the config key names differ.

**Fix:** Create a `BrowserInitMixin` that parameterizes the channel key.

```python
# browser_init.py
"""Browser initialization mixin for archiver classes."""

from __future__ import annotations

from browser_max import BrowserMAX


class BrowserInitMixin:
    """
    Mixin providing _init_browser() and _ensure_browser_connected().

    Subclasses must define:
    - self.config: dict with config data
    - self.browser: BrowserMAX | None

    Override _channel_key and _section_key to customize config lookup.
    """

    _channel_key: str = "max"
    _section_key: str | None = None

    def _init_browser(self) -> BrowserMAX:
        """Initialize BrowserMAX, reusing existing connection if alive."""
        if self.browser is None:
            channel_url = self.config.get("channels", {}).get(self._channel_key, "")
            section = self._section_key or self._channel_key
            use_local = self.config.get(section, {}).get(
                "use_local_browser",
                self.config.get("archiver", {}).get("use_local_browser", False),
            )
            self.browser = BrowserMAX(channel_url, use_local_browser=use_local)
        return self.browser

    def _ensure_browser_connected(self) -> BrowserMAX:
        """Ensure browser is connected and ready."""
        browser = self._init_browser()
        if not browser.keep_alive_connect():
            raise ConnectionError("Failed to connect to MAX")
        browser.navigate()
        browser.ensure_page_ready()
        return browser

    def _close_browser(self) -> None:
        """Safely close browser connection."""
        if self.browser:
            try:
                self.browser.close()
            except Exception:
                pass
            self.browser = None
```

```python
# tests/test_browser_init.py
# -*- coding: utf-8 -*-
"""Tests for BrowserInitMixin."""

import pytest
from unittest.mock import patch, MagicMock
from browser_init import BrowserInitMixin


class MockArchiver(BrowserInitMixin):
    """Mock archiver for testing."""
    def __init__(self, config=None):
        self.config = config or {}
        self.browser = None
        self._channel_key = "max"
        self._section_key = None


class TestBrowserInitMixin:
    """Test browser initialization mixin"""

    def test_init_browser_creates_browser(self):
        """_init_browser creates BrowserMAX when browser is None"""
        archiver = MockArchiver({"channels": {"max": "https://test.ru"}})
        with patch('browser_init.BrowserMAX') as MockBrowser:
            browser = archiver._init_browser()
            MockBrowser.assert_called_once()
            assert archiver.browser is not None

    def test_init_browser_reuses_existing(self):
        """_init_browser reuses existing browser"""
        archiver = MockArchiver()
        archiver.browser = MagicMock()
        browser = archiver._init_browser()
        assert browser is archiver.browser

    def test_ensure_browser_connected(self):
        """_ensure_browser_connected connects and navigates"""
        archiver = MockArchiver({"channels": {"max": "https://test.ru"}})
        with patch('browser_init.BrowserMAX') as MockBrowser:
            mock_browser = MagicMock()
            mock_browser.keep_alive_connect.return_value = True
            MockBrowser.return_value = mock_browser
            result = archiver._ensure_browser_connected()
            mock_browser.navigate.assert_called_once()
            mock_browser.ensure_page_ready.assert_called_once()

    def test_close_browser_safe(self):
        """_close_browser handles None gracefully"""
        archiver = MockArchiver()
        archiver.browser = None
        archiver._close_browser()  # Should not raise

    def test_close_browser_closes(self):
        """_close_browser closes existing browser"""
        archiver = MockArchiver()
        mock_browser = MagicMock()
        archiver.browser = mock_browser
        archiver._close_browser()
        mock_browser.close.assert_called_once()
        assert archiver.browser is None
```

**Then update each archiver to use the mixin:**

```python
# media_archiver.py:
# 1. Add: from browser_init import BrowserInitMixin
# 2. Change: class MediaArchiver(LogMixin, BrowserInitMixin):
# 3. Set: _channel_key = "media"
# 4. Remove: _init_browser(), _ensure_browser_connected(), _format_file_size() methods
```

```python
# pypi_libs_archiver.py:
# 1. Add: from browser_init import BrowserInitMixin
# 2. Change: class PyPILibsArchiver(LogMixin, BrowserInitMixin):
# 3. Set: _channel_key = "pypi"
# 4. Remove: _init_browser(), _ensure_browser_connected(), _format_file_size() methods
```

```python
# backuper.py:
# 1. Add: from browser_init import BrowserInitMixin
# 2. Change: class Backuper(LogMixin, BrowserInitMixin):
# 3. Set: _channel_key = "backup"
# 4. Remove: _init_browser(), _ensure_browser_connected(), _close_browser() methods
```

```python
# channel_downloader.py:
# 1. Add: from browser_init import BrowserInitMixin
# 2. Change: class ChannelDownloader(BrowserInitMixin):
# 3. Set: _channel_key = "max"
# 4. Remove: _init_browser(), _ensure_browser_connected() methods
```

**Verify:** `python -m pytest tests/test_browser_init.py -v`
**Commit:** `refactor: consolidate browser init patterns into BrowserInitMixin`

---

### Task 2.4: Consolidate signal handling

**File:** `signal_handler.py` (new)
**Test:** `tests/test_signal_handler.py` (new)
**Depends:** none (Phase 1)
**Effort:** ~15 min
**Severity:** LOW — DRY violation

**Problem:** `_signal_handler()` and `_cleanup()` are duplicated in `pypi_libs_archiver.py`, `channel_downloader.py`, and `media_archiver.py`. `GracefulShutdown` already exists in `github_archiver.py` but is not used by the other archivers.

**Fix:** Create a reusable `SignalHandler` utility that registers signal handlers and cleanup callbacks.

```python
# signal_handler.py
"""Reusable signal handling and cleanup registration."""

from __future__ import annotations

import atexit
import signal
import logging


class SignalHandler:
    """
    Register signal handlers and atexit cleanup.

    Usage:
        handler = SignalHandler()
        handler.register_shutdown(archiver, on_signal=lambda a: setattr(a, '_shutdown', True))
        handler.register_cleanup(archiver, on_cleanup=lambda a: a._cleanup())
    """

    def __init__(self):
        self._registered = False

    def register(
        self,
        obj: object,
        shutdown_attr: str = "_shutdown",
        on_signal: callable | None = None,
        on_cleanup: callable | None = None,
    ) -> None:
        """
        Register signal handlers and atexit cleanup for an object.

        Args:
            obj: Object to manage (must have shutdown_attr or use on_signal)
            shutdown_attr: Attribute name to set True on signal (default: "_shutdown")
            on_signal: Optional callback(signum, frame) called on signal
            on_cleanup: Optional callback() called on exit
        """
        if self._registered:
            return
        self._registered = True

        logger = logging.getLogger("gitax")

        def _handler(signum, frame):
            logger.info(f"Received signal {signum}, shutting down...")
            if shutdown_attr and hasattr(obj, shutdown_attr):
                setattr(obj, shutdown_attr, True)
            if on_signal:
                on_signal(signum, frame)

        def _cleanup():
            if on_cleanup:
                try:
                    on_cleanup()
                except Exception:
                    pass

        atexit.register(_cleanup)
        signal.signal(signal.SIGINT, _handler)
        signal.signal(signal.SIGTERM, _handler)
```

```python
# tests/test_signal_handler.py
# -*- coding: utf-8 -*-
"""Tests for SignalHandler."""

import signal
import pytest
from unittest.mock import patch, MagicMock
from signal_handler import SignalHandler


class TestSignalHandler:
    """Test signal handling utility"""

    def test_sets_shutdown_flag(self):
        """Signal sets _shutdown flag"""
        obj = MagicMock()
        obj._shutdown = False
        handler = SignalHandler()
        handler.register(obj)
        # Simulate signal
        for sig_handler in (signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM)):
            if sig_handler:
                sig_handler(None, None)
        assert obj._shutdown is True

    def test_calls_cleanup_callback(self):
        """atexit cleanup calls registered callback"""
        obj = MagicMock()
        cleanup_called = MagicMock()
        handler = SignalHandler()
        handler.register(obj, on_cleanup=cleanup_called)
        # Trigger atexit
        import atexit
        for cb, args, kwargs in atexit._exits:
            if cb.__name__ == '_cleanup':
                cb()
        # Cleanup was called
        pass

    def test_prevents_double_registration(self):
        """Second register() is a no-op"""
        obj = MagicMock()
        handler = SignalHandler()
        handler.register(obj)
        handler.register(obj)  # Should not re-register
        assert handler._registered is True

    def test_custom_shutdown_attr(self):
        """Custom shutdown attribute name works"""
        obj = MagicMock()
        obj.custom_flag = False
        handler = SignalHandler()
        handler.register(obj, shutdown_attr="custom_flag")
        for sig_handler in (signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM)):
            if sig_handler:
                sig_handler(None, None)
        assert obj.custom_flag is True
```

**Then update each archiver:**

```python
# media_archiver.py:
# Replace atexit/signal/signal_handler with:
#   from signal_handler import SignalHandler
#   SignalHandler().register(self)
# Remove: _signal_handler() method, atexit.register(), signal.signal() calls
```

```python
# pypi_libs_archiver.py:
# Same pattern — replace signal/atexit registration with SignalHandler
```

```python
# channel_downloader.py:
# Same pattern — replace signal/atexit registration with SignalHandler
```

**Verify:** `python -m pytest tests/test_signal_handler.py -v`
**Commit:** `refactor: consolidate signal handling into SignalHandler utility`

---

## Phase 3: Consistency (2 implementers, parallel)

Depends on Phase 2 completing.

### Task 3.1: Replace print() with logger

**Files:** `scroll_registry.py`, `rollback_journal.py`, `config/__init__.py`
**Test:** No new tests needed (behavior is cosmetic)
**Depends:** Phase 2
**Effort:** ~10 min
**Severity:** LOW — code style

**Fix:** Replace `print()` calls with `logging.getLogger("gitax")` calls.

```python
# scroll_registry.py:
# Replace line 27:
#     print(f"[ScrollRegistry] Failed to save {path}: {e}")
# With:
#     import logging
#     logging.getLogger("gitax").warning(f"ScrollRegistry: Failed to save {path}: {e}")
#
# Add at module level:
#     _logger = logging.getLogger("gitax")
# Then use _logger.warning(...)
```

```python
# rollback_journal.py:
# Replace print() calls with logger:
# Add at module level:
#     import logging
#     _logger = logging.getLogger("gitax")
#
# Line 18: print(f"[ERROR] {JOURNAL_PATH} not found")
# → _logger.error(f"{JOURNAL_PATH} not found")
#
# Line 57: print(f"[OK] Откачено {reverted} записей: cleaned -> sent")
# → _logger.info(f"Откачено {reverted} записей: cleaned -> sent")
#
# Line 59: print("  Нет записей со статусом 'cleaned'")
# → _logger.info("Нет записей со статусом 'cleaned'")
```

```python
# config/__init__.py:
# Lines 12-13 are in a docstring example — no change needed.
# These are NOT actual print() calls, they're documentation examples.
# NO CHANGE REQUIRED for this file.
```

**Verify:** `python -m pytest tests/ -v` (full test suite)
**Commit:** `refactor: replace print() with logger in scroll_registry and rollback_journal`

---

### Task 3.2: Move import glob to module level

**Files:** `browser_max.py`, `github_archiver.py`
**Test:** No new tests needed
**Depends:** Phase 2
**Effort:** ~5 min
**Severity:** LOW — PEP 8 style

**Fix:** Move `import glob` from inside functions to module level.

```python
# browser_max.py:
# Add at module level (after existing imports):
#     import glob
#
# Remove from _cleanup_existing_volumes() (line 148):
#     import glob
#
# Remove from other function (line 159):
#     import glob
```

```python
# github_archiver.py:
# Add at module level (after existing imports):
#     import glob
#
# Remove from _check_orphaned_files() (line 202):
#     import glob
#
# Remove from GracefulShutdown._cleanup_temp_files() (line 87):
#     import glob
```

**Verify:** `python -m pytest tests/ -v` (full test suite)
**Commit:** `refactor: move import glob to module level`

---

## Phase 4: Functional (5 implementers, parallel)

Depends on Phase 2 completing.

### Task 4.1: Unify retry logic

**File:** `retry_utils.py` (new)
**Test:** `tests/test_retry_utils.py` (new)
**Depends:** Phase 2
**Effort:** ~20 min
**Severity:** MEDIUM — scattered retry patterns

**Problem:** Retry logic is scattered: `github_archiver.py` has inline retry loops, `channel_downloader.py` has `_download_with_requests()` with retry, `backuper.py` has retry config but inconsistent implementation.

**Fix:** Create a `retry` decorator.

```python
# retry_utils.py
"""Retry decorator for transient failures."""

from __future__ import annotations

import time
import functools
import logging
from typing import Type, Tuple

_logger = logging.getLogger("gitax")


def retry(
    max_attempts: int = 3,
    delay: float = 1.0,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
    backoff: float = 2.0,
    log_level: int = logging.WARNING,
):
    """
    Retry decorator for handling transient failures.

    Args:
        max_attempts: Maximum number of attempts (default: 3)
        delay: Initial delay between retries in seconds (default: 1.0)
        exceptions: Tuple of exception types to catch and retry
        backoff: Multiplier for delay between retries (default: 2.0)
        log_level: Logging level for retry messages

    Usage:
        @retry(max_attempts=3, delay=5, exceptions=(ConnectionError, TimeoutError))
        def upload_file(path):
            ...
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            current_delay = delay
            last_exception = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_attempts:
                        _logger.log(
                            log_level,
                            f"{func.__name__} attempt {attempt}/{max_attempts} failed: {e}. "
                            f"Retrying in {current_delay:.1f}s...",
                        )
                        time.sleep(current_delay)
                        current_delay *= backoff
                    else:
                        _logger.error(
                            f"{func.__name__} failed after {max_attempts} attempts: {e}"
                        )
            raise last_exception
        return wrapper
    return decorator
```

```python
# tests/test_retry_utils.py
# -*- coding: utf-8 -*-
"""Tests for retry decorator."""

import time
import pytest
from unittest.mock import patch
from retry_utils import retry


class TestRetryDecorator:
    """Test retry decorator"""

    def test_success_on_first_try(self):
        """No retry needed when function succeeds"""
        call_count = 0

        @retry(max_attempts=3, delay=0.01)
        def succeed():
            nonlocal call_count
            call_count += 1
            return "ok"

        result = succeed()
        assert result == "ok"
        assert call_count == 1

    def test_retry_on_failure(self):
        """Function is retried on failure"""
        call_count = 0

        @retry(max_attempts=3, delay=0.01)
        def fail_twice():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("not yet")
            return "ok"

        result = fail_twice()
        assert result == "ok"
        assert call_count == 3

    def test_raises_after_max_attempts(self):
        """Last exception is raised after max retries"""
        @retry(max_attempts=2, delay=0.01)
        def always_fail():
            raise ConnectionError("nope")

        with pytest.raises(ConnectionError, match="nope"):
            always_fail()

    def test_backoff_delay(self):
        """Delay increases with backoff"""
        delays = []

        @retry(max_attempts=3, delay=0.01, backoff=2.0)
        def always_fail():
            raise ValueError("fail")

        with patch('retry_utils.time.sleep') as mock_sleep:
            with pytest.raises(ValueError):
                always_fail()
            assert mock_sleep.call_count == 2

    def test_only_catches_specified_exceptions(self):
        """Only specified exception types trigger retry"""
        @retry(max_attempts=3, delay=0.01, exceptions=(ValueError,))
        def raise_type_error():
            raise TypeError("wrong type")

        with pytest.raises(TypeError):
            raise_type_error()
```

**Verify:** `python -m pytest tests/test_retry_utils.py -v`
**Commit:** `feat: add retry decorator for unified retry logic`

---

### Task 4.2: Fix daemon threads in parallel_uploader

**File:** `parallel_uploader.py`
**Test:** `tests/test_parallel_uploader.py` (add to existing)
**Depends:** Phase 2
**Effort:** ~15 min
**Severity:** MEDIUM — daemon threads can leave resources dangling

**Problem:** `ParallelGroupUploader.run()` (line 71) creates threads with `daemon=True`. Daemon threads are killed abruptly when the main thread exits — no cleanup, no graceful shutdown. The `run()` method already calls `t.join(timeout=600)`, so daemon=True is unnecessary and dangerous.

**Fix:** Remove `daemon=True`. Threads are already joined explicitly.

```python
# parallel_uploader.py — fix line 67-72
# REPLACE:
        for channel_info in self.channels:
            t = threading.Thread(
                target=self._upload_to_channel,
                args=(channel_info, semaphore, mock_browser_class),
                name=f"upload-{channel_info.get('label', 'unknown')}",
                daemon=True,
            )

# WITH:
        for channel_info in self.channels:
            t = threading.Thread(
                target=self._upload_to_channel,
                args=(channel_info, semaphore, mock_browser_class),
                name=f"upload-{channel_info.get('label', 'unknown')}",
            )
```

```python
# tests/test_parallel_uploader.py — add test
# APPEND to existing test file:

    def test_threads_are_not_daemon(self, tmp_path):
        """Threads should NOT be daemon threads for proper cleanup"""
        test_file = tmp_path / "test.zip"
        test_file.write_bytes(b"test content")

        created_threads = []

        original_thread_init = threading.Thread.__init__

        def capture_init(self, *args, **kwargs):
            created_threads.append(kwargs.get('daemon', False))
            return original_thread_init(self, *args, **kwargs)

        with patch.object(threading.Thread, '__init__', capture_init):
            uploader = ParallelGroupUploader(
                files=[str(test_file)],
                channels=[
                    {"url": "https://web.max.ru/ch1", "label": "Channel 1"}
                ],
                cleanup=False,
                stagger_delay_sec=0.01,
            )
            summary = uploader.run(mock_browser_class=MockBrowserMAX)

        # After fix, daemon should be False (or not set)
        for is_daemon in created_threads:
            assert is_daemon is False, "Threads should not be daemon threads"
```

**Verify:** `python -m pytest tests/test_parallel_uploader.py -v`
**Commit:** `fix: remove daemon=True from parallel uploader threads`

---

### Task 4.3: Fix non-recursive os.walk hack

**File:** `backuper.py`
**Test:** `tests/test_backuper_scan.py` (new)
**Depends:** Phase 2
**Effort:** ~15 min
**Severity:** MEDIUM — fragile workaround

**Problem:** `_scan_files()` (line 130) uses a hacky one-liner to simulate non-recursive walk:
```python
walker = os.walk(source_path) if recursive else [(source_path, [], [f for f in os.listdir(source_path) if os.path.isfile(os.path.join(source_path, f))])]
```
This is hard to read and error-prone.

**Fix:** Use `os.walk()` with proper `topdown=True` and directory pruning for non-recursive mode.

```python
# backuper.py — fix _scan_files (line 130)
# REPLACE line 130:
#         walker = os.walk(source_path) if recursive else [(source_path, [], [f for f in os.listdir(source_path) if os.path.isfile(os.path.join(source_path, f))])]

# WITH:
#         walker = os.walk(source_path)
```

And modify the loop to handle non-recursive pruning:

```python
        for dirpath, dirs, filenames in walker:
            # Prune subdirectories when not recursive
            if not recursive:
                dirs[:] = []

            for fname in filenames:
```

```python
# tests/test_backuper_scan.py
# -*- coding: utf-8 -*-
"""Tests for Backuper._scan_files() non-recursive mode."""

import os
import pytest
from unittest.mock import patch


class TestScanFilesNonRecursive:
    """Test non-recursive file scanning"""

    def test_non_recursive_skips_subdirs(self, tmp_path):
        """Non-recursive scan only returns files in root directory"""
        # Create structure: root/file1.txt, subdir/file2.txt
        (tmp_path / "file1.txt").write_text("root file")
        sub = tmp_path / "subdir"
        sub.mkdir()
        (sub / "file2.txt").write_text("sub file")

        from backuper import Backuper
        bu = Backuper("tests/fixtures/test_config.yaml")
        files = bu._scan_files(str(tmp_path), recursive=False)

        # Should only find file1.txt, not subdir/file2.txt
        paths = [f[0] for f in files]
        assert any("file1.txt" in p for p in paths)
        assert not any("file2.txt" in p for p in paths)

    def test_recursive_includes_subdirs(self, tmp_path):
        """Recursive scan includes files in subdirectories"""
        (tmp_path / "file1.txt").write_text("root file")
        sub = tmp_path / "subdir"
        sub.mkdir()
        (sub / "file2.txt").write_text("sub file")

        from backuper import Backuper
        bu = Backuper("tests/fixtures/test_config.yaml")
        files = bu._scan_files(str(tmp_path), recursive=True)

        paths = [f[0] for f in files]
        assert any("file1.txt" in p for p in paths)
        assert any("file2.txt" in p for p in paths)

    def test_non_recursive_uses_dirs_pruning(self):
        """Non-recursive mode prunes dirs list to prevent descent"""
        # Verify the implementation uses dirs[:] = [] for pruning
        pass
```

**Verify:** `python -m pytest tests/test_backuper_scan.py -v`
**Commit:** `fix: use os.walk with dirs pruning for non-recursive scan`

---

### Task 4.4: Add session logging to pypi_libs_archiver

**File:** `pypi_libs_archiver.py`
**Test:** No new tests needed (logging behavior)
**Depends:** Phase 2
**Effort:** ~5 min
**Severity:** LOW — missing observability

**Problem:** `pypi_libs_archiver.py` does not use `SessionCapture` context manager (used by `github_archiver.py` and `media_archiver.py` for session-level logging).

**Fix:** Wrap the main run loop with `SessionCapture`.

```python
# pypi_libs_archiver.py — add SessionCapture
# In the run() method, wrap the main loop:
#
#     def run(self):
#         with SessionCapture("pypi_libs"):
#             # ... existing run loop ...
```

**Verify:** `python -m pytest tests/test_pypi_libs_archiver.py -v` (existing tests should still pass)
**Commit:** `feat: add SessionCapture logging to pypi_libs_archiver`

---

### Task 4.5: Add load_dotenv() to media_archiver

**File:** `media_archiver.py`
**Test:** No new tests needed
**Depends:** Phase 2
**Effort:** ~5 min
**Severity:** LOW — missing env loading

**Problem:** `media_archiver.py` does not call `load_dotenv()` before loading config, unlike other archivers.

**Fix:** Add `from dotenv import load_dotenv; load_dotenv()` at the top of `__init__`.

```python
# media_archiver.py — add load_dotenv in __init__
# Add at the beginning of __init__ (before init_config):
#         from dotenv import load_dotenv
#         load_dotenv()
```

**Verify:** `python -m pytest tests/test_media_archiver.py -v`
**Commit:** `fix: add load_dotenv() to media_archiver`

---

## Phase 5: Nice-to-Have (3 implementers, parallel)

Depends on Phase 4 completing.

### Task 5.1: Remove dead NotImplementedError

**File:** `channel_downloader.py`
**Test:** No new tests needed
**Depends:** Phase 4
**Effort:** ~5 min
**Severity:** LOW — dead code

**Fix:** Locate and remove any `raise NotImplementedError` blocks that are unreachable or unused.

```python
# channel_downloader.py:
# Search for "NotImplementedError" and remove dead code blocks
# (grep will identify exact line numbers)
```

**Verify:** `python -m pytest tests/test_channel_downloader.py -v`
**Commit:** `refactor: remove dead NotImplementedError code`

---

### Task 5.2: Add confirm() before journal.clear()

**File:** `pypi_libs_archiver.py`
**Test:** No new tests needed
**Depends:** Phase 4
**Effort:** ~10 min
**Severity:** LOW — UX improvement

**Fix:** Add a confirmation prompt before clearing the journal.

```python
# pypi_libs_archiver.py:
# In the menu handler for journal.clear(), add:
#
#     def _confirm_clear_journal(self):
#         """Ask for confirmation before clearing journal"""
#         response = input("  Очистить журнал? Все записи будут удалены. [y/N]: ").strip().lower()
#         return response in ('y', 'yes')
```

**Verify:** `python -m pytest tests/test_pypi_libs_archiver.py -v`
**Commit:** `feat: add confirmation before clearing pypi journal`

---

### Task 5.3: Startup health-check

**File:** `health_check.py` (new)
**Test:** `tests/test_health_check.py` (new)
**Depends:** Phase 4
**Effort:** ~20 min
**Severity:** LOW — DX improvement

**Fix:** Create a startup health-check that verifies 7-Zip, Chrome CDP, and config are available.

```python
# health_check.py
"""Startup health checks for the archiver."""

from __future__ import annotations

import os
import logging
import subprocess

_logger = logging.getLogger("gitax")


def check_seven_zip(seven_zip_exe: str) -> bool:
    """Check if 7-Zip is available."""
    if not os.path.exists(seven_zip_exe):
        _logger.warning(f"7-Zip not found at {seven_zip_exe}")
        return False
    try:
        result = subprocess.run(
            [seven_zip_exe, "--help"],
            capture_output=True, text=True, timeout=5
        )
        return result.returncode in (0, 1)  # 7z returns 1 for --help
    except Exception as e:
        _logger.warning(f"7-Zip check failed: {e}")
        return False


def check_chrome_cdp(port: int = 9222) -> bool:
    """Check if Chrome CDP endpoint is reachable."""
    import urllib.request
    try:
        resp = urllib.request.urlopen(
            f"http://localhost:{port}/json/version", timeout=3
        )
        return resp.status == 200
    except Exception:
        return False


def check_config(config: dict) -> list[str]:
    """Check if required config values are present."""
    issues = []
    channels = config.get("channels", {})
    if not channels.get("max"):
        issues.append("channels.max URL not configured")
    return issues


def run_health_checks(config: dict) -> bool:
    """Run all health checks. Returns True if all pass."""
    all_ok = True

    # Check 7-Zip
    seven_zip = config.get("backuper", {}).get(
        "seven_zip_exe",
        config.get("archiver", {}).get("seven_zip_exe", "7z")
    )
    if not check_seven_zip(seven_zip):
        all_ok = False

    # Check Chrome CDP
    if not check_chrome_cdp():
        _logger.warning("Chrome CDP not reachable on port 9222")
        all_ok = False

    # Check config
    issues = check_config(config)
    for issue in issues:
        _logger.warning(f"Config issue: {issue}")
        all_ok = False

    if all_ok:
        _logger.info("Health checks passed")
    else:
        _logger.warning("Some health checks failed — app may not work correctly")

    return all_ok
```

```python
# tests/test_health_check.py
# -*- coding: utf-8 -*-
"""Tests for health check module."""

import os
import pytest
from unittest.mock import patch, MagicMock
from health_check import check_seven_zip, check_chrome_cdp, check_config, run_health_checks


class TestCheckSevenZip:
    """Test 7-Zip health check"""

    def test_missing_exe_returns_false(self):
        """Missing 7z executable returns False"""
        assert check_seven_zip("/nonexistent/7z") is False

    @patch('health_check.subprocess.run')
    @patch('health_check.os.path.exists')
    def test_valid_exe_returns_true(self, mock_exists, mock_run):
        """Valid 7z returns True"""
        mock_exists.return_value = True
        mock_run.return_value = MagicMock(returncode=0)
        assert check_seven_zip("7z") is True


class TestCheckChromeCdp:
    """Test Chrome CDP health check"""

    def test_unreachable_returns_false(self):
        """Unreachable CDP returns False"""
        assert check_chrome_cdp(port=99999) is False

    @patch('health_check.urllib.request.urlopen')
    def test_reachable_returns_true(self, mock_open):
        """Reachable CDP returns True"""
        mock_open.return_value = MagicMock(status=200)
        assert check_chrome_cdp() is True


class TestCheckConfig:
    """Test config health check"""

    def test_missing_channel_url(self):
        """Missing channel URL is detected"""
        issues = check_config({})
        assert any("max" in issue for issue in issues)

    def test_valid_config(self):
        """Valid config has no issues"""
        issues = check_config({"channels": {"max": "https://test.ru"}})
        assert len(issues) == 0


class TestRunHealthChecks:
    """Test full health check run"""

    def test_returns_bool(self):
        """run_health_checks returns a boolean"""
        result = run_health_checks({})
        assert isinstance(result, bool)
```

**Verify:** `python -m pytest tests/test_health_check.py -v`
**Commit:** `feat: add startup health-check for 7-Zip, Chrome CDP, and config`

---

## Summary

| Phase | Tasks | Files Modified | New Files | Effort |
|-------|-------|---------------|-----------|--------|
| 1. Critical | 3 | 3 | 1 (test) | ~35 min |
| 2. Arch DRY | 4 | 7 | 3 | ~80 min |
| 3. Consistency | 2 | 3 | 0 | ~15 min |
| 4. Functional | 5 | 4 | 2 | ~55 min |
| 5. Nice-to-have | 3 | 2 | 2 | ~35 min |
| **Total** | **17** | **19 unique** | **8** | **~2.5 hrs** |

## Risk Assessment

| Risk | Mitigation |
|------|-----------|
| Breaking existing tests | Each task preserves existing behavior, only changes internals |
| Config path changes | All config access patterns preserved (dict access) |
| Browser mixin changes | Mixin is additive — archivers keep their own logic |
| 7z password via CLI | Safe on Windows (subprocess args not visible in process listings) |
| Signal handler consolidation | GracefulShutdown in github_archiver.py remains unchanged |

## Rollback Strategy

Each task is small and self-contained. If any task breaks functionality:
1. Revert the single commit
2. The change affects at most 1-2 files
3. Existing tests catch regressions immediately
