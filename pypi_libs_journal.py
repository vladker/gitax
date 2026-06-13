"""
Журнал отправленных PyPI библиотек

Хранится в pypi_libs_journal.json.
Дедупликация: (name, version) — повторная отправка той же версии блокируется.
Атомарная запись (write+rename), как в journal.py / MediaJournal.
"""

from datetime import datetime
from shared_journal import BaseJournal, RuntimeJournalMixin


class PyPILibsJournal(RuntimeJournalMixin, BaseJournal):
    """Журнал отправленных PyPI библиотек"""

    def _create_empty(self) -> dict:
        """Создать пустой журнал"""
        return {"libraries": []}

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
