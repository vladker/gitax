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

from logging_config import LogMixin


class BaseJournal(LogMixin):
    """Base journal class with locking, loading, atomic save, and clear."""

    def __init__(self, file_path: str):
        self.file_path = file_path
        self._lock_file = f"{file_path}.lock"
        self.data = self._load()

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
        """Save journal to file (atomic write via tempfile → copy2 → os.replace)"""
        if not self._acquire_lock():
            self.logger.warning("Journal locked, skipping save")
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
            self._release_lock()

    def _pre_save(self):
        """Hook called before writing data. Subclasses can override for custom logic."""
        pass

    # ── Clear ────────────────────────────────────────────────

    def clear(self):
        """Clear journal — reset all data"""
        self.data = self._create_empty()
        self.save()
        self.logger.info(f"{self.__class__.__name__} cleared")

    # ── Abstract ─────────────────────────────────────────────

    @abstractmethod
    def _create_empty(self) -> dict:
        """Create empty journal structure. Must be overridden by subclasses."""
        pass
