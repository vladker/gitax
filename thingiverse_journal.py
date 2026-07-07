"""
Журнал обработанных Thingiverse things

Хранится в thingiverse_journal.json.
Дедупликация: thing_id — повторная отправка того же thing блокируется.
Атомарная запись (write+rename), как в journal.py / PyPILibsJournal.
"""

from datetime import datetime
from shared_journal import BaseJournal, RuntimeJournalMixin


class ThingiverseJournal(RuntimeJournalMixin, BaseJournal):
    """Журнал обработанных Thingiverse things"""

    def __init__(self, file_path: str = "thingiverse_journal.json"):
        super().__init__(file_path)

    def _create_empty(self) -> dict:
        """Создать пустой журнал"""
        return {"things": []}

    def is_processed(self, thing_id: int) -> bool:
        """
        Проверить, есть ли thing_id в журнале.
        Дедупликация: повторная обработка того же thing блокируется.
        """
        for entry in self.data.get("things", []):
            if entry.get("thing_id") == thing_id:
                return True
        return False

    def mark_sent(self, thing_id: int, name: str, files: list[str],
                  size: int = 0, version: str = "") -> bool:
        """
        Добавить запись об успешно отправленном thing.

        Args:
            thing_id: Идентификатор thing
            name: Название thing
            files: Список имён отправленных файлов
            size: Размер в байтах
            version: Дата загрузки (download_date)

        Returns:
            True если добавлена, False если thing_id уже существует
        """
        if self.is_processed(thing_id):
            return False

        self.data.setdefault("things", []).append({
            "thing_id": thing_id,
            "name": name,
            "version": version,
            "files": files,
            "size": size,
            "status": "sent",
            "timestamp": datetime.now().isoformat(),
        })
        return self.save()

    def mark_failed(self, thing_id: int, name: str, error: str = ""):
        """Отметить thing как ошибочный"""
        self.data.setdefault("things", []).append({
            "thing_id": thing_id,
            "name": name,
            "version": "",
            "files": [],
            "size": 0,
            "status": "failed",
            "error": error,
            "timestamp": datetime.now().isoformat(),
        })
        self.save()

    def get(self, thing_id: int) -> dict | None:
        """Получить запись thing по thing_id."""
        for entry in self.data.get("things", []):
            if entry.get("thing_id") == thing_id:
                return entry
        return None

    def get_all(self) -> list[dict]:
        """Получить все записи"""
        return list(self.data.get("things", []))

    def get_count(self) -> int:
        """Получить количество записей"""
        return len(self.data.get("things", []))

    def get_stats(self) -> dict:
        """Получить статистику журнала"""
        entries = self.data.get("things", [])
        return {
            "total": len(entries),
            "sent": len([e for e in entries if e.get("status") == "sent"]),
            "failed": len([e for e in entries if e.get("status") == "failed"]),
        }

    def clear(self, confirm: bool = False):
        """Очистить журнал"""
        super().clear(confirm)


if __name__ == "__main__":
    # Тестирование модуля
    journal = ThingiverseJournal("test_thingiverse_journal.json")

    # Добавить тестовую запись
    journal.mark_sent(12345, "Test Thing", ["test.zip"], size=1024000,
                      version="2026-07-01")
    journal.mark_failed(67890, "Failed Thing", error="timeout")

    print(f"Журнал: {journal.get_count()} записей")
    print(f"Статистика: {journal.get_stats()}")
    print(f"Обработан 12345: {journal.is_processed(12345)}")
    print(f"Обработан 99999: {journal.is_processed(99999)}")

    # Очистка тестового файла
    import os
    if os.path.exists("test_thingiverse_journal.json"):
        os.remove("test_thingiverse_journal.json")
