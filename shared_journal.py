"""
Shared base class for journal modules.

Extracts common boilerplate (locking, loading, atomic save, clear) 
shared across Journal, MediaJournal, DownloadJournal, 
PyPILibsJournal, and BackuperJournal.
"""

import json
import os
import time
import tempfile
import shutil
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
import threading

from logging_config import LogMixin


class BaseJournal(LogMixin):
    """Base journal class with locking, loading, atomic save, and clear."""

    def __init__(self, file_path: str):
        self.file_path = file_path
        self._lock_file = f"{file_path}.lock"
        self.data = self._load()

    _write_lock = threading.Lock()

    # ── Locking ──────────────────────────────────────────────

    def _acquire_lock(self) -> bool:
        """Acquire exclusive lock for safe writes (5 min stale timeout)"""
        try:
            if os.path.exists(self._lock_file):
                lock_age = time.time() - os.path.getmtime(self._lock_file)
                if lock_age > 300:
                    self._release_lock()
                else:
                    return False
            Path(self._lock_file).touch()
            return True
        except Exception:
            return False

    def _release_lock(self):
        """Release lock file"""
        try:
            if os.path.exists(self._lock_file):
                os.remove(self._lock_file)
        except Exception:
            pass

    # ── Loading ──────────────────────────────────────────────

    def _load(self) -> dict:
        """Load journal from file, recover from corruption via backup rename."""
        if os.path.exists(self.file_path):
            try:
                with open(self.file_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                backup_path = f"{self.file_path}.backup"
                if os.path.exists(self.file_path):
                    os.rename(self.file_path, backup_path)
                return self._create_empty()
        return self._create_empty()

    # ── Save (atomic write) ─────────────────────────────────

    def save(self):
        """Save journal to file (atomic write via tempfile → copy2 → os.replace).
        Thread-safe via class-level lock."""
        acquired = self._write_lock.acquire(timeout=5)
        if not acquired:
            self.logger.warning("Journal write lock timeout, skipping save")
            return
        try:
            self._pre_save()
            temp_fd, temp_path = tempfile.mkstemp(
                suffix='.json',
                dir=os.path.dirname(self.file_path) or '.'
            )
            try:
                with os.fdopen(temp_fd, 'w', encoding='utf-8') as f:
                    json.dump(self.data, f, ensure_ascii=False, indent=2)
                if os.path.exists(self.file_path):
                    backup_path = f"{self.file_path}.bak"
                    shutil.copy2(self.file_path, backup_path)
                os.replace(temp_path, self.file_path)
            except Exception:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                raise
        finally:
            self._write_lock.release()

    def _pre_save(self):
        """Hook called before writing data. Subclasses can override for custom logic."""
        pass

    # ── Clear ────────────────────────────────────────────────

    def clear(self, confirm: bool = False):
        """Clear journal — reset all data.

        Args:
            confirm: If True, asks for user confirmation before clearing.
                     Default is False for backward compatibility with tests.
        """
        if confirm:
            try:
                response = input(f"  ⚠ Очистить журнал {self.__class__.__name__}? Все данные будут потеряны. (y/N): ")
                if response.strip().lower() not in ('y', 'yes'):
                    print("  Отмена.")
                    return
            except (EOFError, KeyboardInterrupt):
                print("\n  Отмена.")
                return
        self.data = self._create_empty()
        self.save()
        self.logger.info(f"{self.__class__.__name__} cleared")

    # ── Abstract ─────────────────────────────────────────────

    @abstractmethod
    def _create_empty(self) -> dict:
        """Create empty journal structure. Must be overridden by subclasses."""
        pass


class RuntimeJournalMixin:
    """Mixin providing runtime version tracking for all journals.

    Usage:
        class MyJournal(RuntimeJournalMixin, BaseJournal):
            ...

    Stores runtime info under data["runtime"] key:
    {
        "version": "3.13.2",
        "last_updated": "2026-06-13T10:00:00",
        "entries": [
            {"os": "windows", "filename": "python-3.13.2-amd64.exe", "sent_at": "..."},
            ...
        ]
    }
    """

    def get_runtime_version(self) -> str | None:
        """Get the saved runtime version, or None if not set."""
        runtime = self.data.get("runtime")
        if runtime and isinstance(runtime, dict):
            return runtime.get("version")
        return None

    def set_runtime_version(self, version: str, entries: list[dict]) -> None:
        """Save runtime version and per-OS entries."""
        self.data["runtime"] = {
            "version": version,
            "last_updated": datetime.now().isoformat(),
            "entries": entries,
        }
        self.save()

    def should_update_runtime(self, latest_version: str) -> bool:
        """Check if runtime needs updating by comparing with saved version.

        Returns True if saved version differs from latest, or if no version saved.
        """
        saved = self.get_runtime_version()
        if saved is None:
            return True
        return saved != latest_version

    def get_runtime_entries(self) -> list[dict]:
        """Get saved runtime entries for all OS targets."""
        runtime = self.data.get("runtime")
        if runtime and isinstance(runtime, dict):
            return runtime.get("entries", [])
        return []
