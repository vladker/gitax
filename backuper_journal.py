"""
Journal for backuper module — tracks backups, downloads, and passwords.

Stored in backuper_journal.json.
"""

import json
import os
import time
import tempfile
import shutil
import hashlib
from datetime import datetime
from pathlib import Path
from logging_config import LogMixin


class BackuperJournal(LogMixin):
    """Backup and download journal with password storage"""

    def __init__(self, file_path: str = "backuper_journal.json"):
        self.file_path = file_path
        self._lock_file = f"{file_path}.lock"
        self.data = self._load()

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

    def _load(self) -> dict:
        """Load journal from file, recover from corruption"""
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

    def _create_empty(self) -> dict:
        """Create empty journal structure"""
        return {"backups": [], "downloads": [], "passwords": {}}

    def clear(self):
        """Clear journal — reset all data"""
        self.data = self._create_empty()
        self.save()
        self.logger.info("Backuper journal cleared")

    def save(self):
        """Save journal to file (atomic write via temp+rename)"""
        if not self._acquire_lock():
            self.logger.warning("Journal locked, skipping save")
            return
        try:
            temp_fd, temp_path = tempfile.mkstemp(
                suffix='.json',
                dir=os.path.dirname(self.file_path) or '.'
            )
            try:
                with os.fdopen(temp_fd, 'w', encoding='utf-8') as f:
                    json.dump(self.data, f, ensure_ascii=False, indent=2)
                if os.path.exists(self.file_path):
                    shutil.copy2(self.file_path, f"{self.file_path}.bak")
                os.replace(temp_path, self.file_path)
            except Exception:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                raise
        finally:
            self._release_lock()

    def add_backup(self, backup_data: dict) -> bool:
        """
        Add backup entry to journal.

        Args:
            backup_data: Dict with archive_name (required), source_path,
                volume_count, encrypted, total_size, status, content_hash

        Returns:
            True if added, False if duplicate by content_hash
        """
        archive_name = backup_data.get('archive_name')
        if not archive_name:
            return False

        content_hash = backup_data.get('content_hash')
        # Block duplicate if same hash already uploaded
        if content_hash:
            for entry in self.data.get("backups", []):
                if entry.get('content_hash') == content_hash and entry.get('status') == 'uploaded':
                    return False

        backup_data.setdefault('created_at', datetime.now().isoformat())
        self.data.setdefault("backups", []).append(backup_data)
        self.save()
        return True

    def add_download(self, download_data: dict) -> bool:
        """
        Add download entry to journal.

        Args:
            download_data: Dict with archive_name, volumes_downloaded,
                extracted_to, status

        Returns:
            True if added
        """
        download_data.setdefault('downloaded_at', datetime.now().isoformat())
        self.data.setdefault("downloads", []).append(download_data)
        self.save()
        return True

    def store_password(self, archive_name: str, password: str):
        """Store password for an archive"""
        self.data.setdefault("passwords", {})[archive_name] = password
        self.save()

    def get_password(self, archive_name: str) -> str | None:
        """Retrieve password for an archive"""
        return self.data.get("passwords", {}).get(archive_name)

    def has_password(self, archive_name: str) -> bool:
        """Check if password exists for archive"""
        return archive_name in self.data.get("passwords", {})

    def get_backup(self, archive_name: str) -> dict | None:
        """Get latest backup entry by archive name"""
        latest = None
        for entry in self.data.get("backups", []):
            if entry.get("archive_name") == archive_name:
                if latest is None or (entry.get("created_at") or "") > (latest.get("created_at") or ""):
                    latest = entry
        return latest

    def get_all_backups(self) -> list[dict]:
        """Get all backup entries"""
        return list(self.data.get("backups", []))

    def get_all_downloads(self) -> list[dict]:
        """Get all download entries"""
        return list(self.data.get("downloads", []))

    def is_duplicate_by_hash(self, content_hash: str) -> bool:
        """Check if a backup with this content_hash already exists (uploaded status)"""
        if not content_hash:
            return False
        for entry in self.data.get("backups", []):
            if entry.get('content_hash') == content_hash and entry.get('status') == 'uploaded':
                return True
        return False

    def compute_content_hash(self, source_path: str) -> str:
        """
        Compute quick hash of directory contents.
        Hash is based on file list (relative path + size + mtime), not file contents.

        Args:
            source_path: Path to directory

        Returns:
            SHA256 hash string prefixed with "sha256:"
        """
        hasher = hashlib.sha256()
        if not os.path.isdir(source_path):
            return f"sha256:{hasher.hexdigest()}"

        file_list = []
        for root, dirs, files in os.walk(source_path):
            for f in sorted(files):
                fp = os.path.join(root, f)
                try:
                    rel = os.path.relpath(fp, source_path)
                    stat = os.stat(fp)
                    file_list.append(f"{rel}|{stat.st_size}|{stat.st_mtime}")
                except OSError:
                    pass

        hasher.update("\n".join(sorted(file_list)).encode('utf-8'))
        return f"sha256:{hasher.hexdigest()}"

    def get_stats(self) -> dict:
        """Get journal statistics"""
        backups = self.data.get("backups", [])
        downloads = self.data.get("downloads", [])
        return {
            "total_backups": len(backups),
            "uploaded": len([b for b in backups if b.get("status") == "uploaded"]),
            "failed": len([b for b in backups if b.get("status") == "failed"]),
            "total_downloads": len(downloads),
            "completed_downloads": len([d for d in downloads if d.get("status") == "completed"]),
            "passwords_stored": len(self.data.get("passwords", {})),
        }

    def get_count(self) -> int:
        """Get number of backup entries"""
        return len(self.data.get("backups", []))
