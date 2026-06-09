"""
Журнал отправленных PyPI библиотек

Хранится в pypi_libs_journal.json.
Дедупликация: (name, version) — повторная отправка той же версии блокируется.
Атомарная запись (write+rename), как в journal.py / MediaJournal.
"""

import json
import os
import time
import tempfile
import shutil
from datetime import datetime
from pathlib import Path
from logging_config import LogMixin


class PyPILibsJournal(LogMixin):
    """Журнал отправленных PyPI библиотек"""

    def __init__(self, file_path: str = "pypi_libs_journal.json"):
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
        """Загрузить журнал из файла"""
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
        """Создать пустой журнал"""
        return {"libraries": []}

    def clear(self):
        """Очистить журнал — сбросить все данные"""
        self.data = self._create_empty()
        self.save()
        self.logger.info("PyPI libs journal cleared")

    def save(self):
        """Сохранить журнал в файл (атомарная запись через write+rename)"""
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
                    backup_path = f"{self.file_path}.bak"
                    shutil.copy2(self.file_path, backup_path)
                os.replace(temp_path, self.file_path)
            except Exception:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                raise
        finally:
            self._release_lock()

    def add(self, name: str, version: str, description: str,
            downloads: int, files: list[str]) -> bool:
        """
        Добавить запись о библиотеке в журнал.

        Args:
            name: Имя библиотеки
            version: Версия
            description: Описание
            downloads: Количество загрузок за 365 дней
            files: Список имён отправленных файлов

        Returns:
            True если добавлена, False если (name, version) уже существует
        """
        if self.exists(name, version):
            return False

        self.data.setdefault("libraries", []).append({
            "name": name,
            "version": version,
            "description": description,
            "downloads": downloads,
            "status": "sent",
            "sent_at": datetime.now().isoformat(),
            "files": files,
        })
        self.save()
        return True

    def mark_failed(self, name: str, version: str, description: str = "",
                    downloads: int = 0):
        """Отметить пакет как ошибочный"""
        self.data.setdefault("libraries", []).append({
            "name": name,
            "version": version,
            "description": description,
            "downloads": downloads,
            "status": "failed",
            "sent_at": datetime.now().isoformat(),
            "files": [],
        })
        self.save()

    def update(self, name: str, version: str, updates: dict) -> bool:
        """
        Обновить запись библиотеки (по name + version).

        Args:
            name: Имя библиотеки
            version: Версия
            updates: Словарь с обновлениями

        Returns:
            True если обновлена, False если не найдена
        """
        for entry in self.data.setdefault("libraries", []):
            if entry.get("name") == name and entry.get("version") == version:
                entry.update(updates)
                entry["updated_at"] = datetime.now().isoformat()
                self.save()
                return True
        return False

    def exists(self, name: str, version: str) -> bool:
        """
        Проверить, есть ли библиотека (name, version) в журнале.
        Дедупликация: (name, version) — повторная отправка той же версии блокируется.
        """
        for entry in self.data.get("libraries", []):
            if entry.get("name") == name and entry.get("version") == version:
                return True
        return False

    def exists_by_name(self, name: str) -> bool:
        """
        Проверить, есть ли библиотека в журнале по имени (без версии).
        Используется когда версия не известна (новый формат Hugovk датасета).
        """
        for entry in self.data.get("libraries", []):
            if entry.get("name") == name:
                return True
        return False

    def get(self, name: str) -> dict | None:
        """Получить последнюю запись библиотеки по имени (по latest version)."""
        latest = None
        for entry in self.data.get("libraries", []):
            if entry.get("name") == name:
                if latest is None:
                    latest = entry
                elif (entry.get("sent_at") or "") > (latest.get("sent_at") or ""):
                    latest = entry
        return latest

    def get_all(self) -> list[dict]:
        """Получить все записи"""
        return list(self.data.get("libraries", []))

    def get_count(self) -> int:
        """Получить количество записей"""
        return len(self.data.get("libraries", []))

    def get_stats(self) -> dict:
        """Получить статистику журнала"""
        entries = self.data.get("libraries", [])
        return {
            "total": len(entries),
            "sent": len([e for e in entries if e.get("status") == "sent"]),
            "failed": len([e for e in entries if e.get("status") == "failed"]),
        }
