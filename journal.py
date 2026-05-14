"""
Модуль работы с журналом (journal.json)
"""

import json
import os
from datetime import datetime
from typing import Optional
from pathlib import Path


class Journal:
    """Класс для управления журналом загруженных репозиториев"""

    def __init__(self, file_path: str = "journal.json"):
        self.file_path = file_path
        self.data = self._load()

    def _load(self) -> dict:
        """Загрузить журнал из файла"""
        if os.path.exists(self.file_path):
            try:
                with open(self.file_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                # Создать резервную копию повреждённого файла
                backup_path = f"{self.file_path}.backup"
                if os.path.exists(self.file_path):
                    os.rename(self.file_path, backup_path)
                return self._create_empty()

        return self._create_empty()

    def _create_empty(self) -> dict:
        """Создать пустой журнал"""
        return {
            "repositories": [],
            "last_updated": "",
            "total_processed": 0,
            "total_sent": 0,
            "total_failed": 0
        }

    def save(self):
        """Сохранить журнал в файл"""
        self.data["last_updated"] = datetime.now().isoformat()
        with open(self.file_path, 'w', encoding='utf-8') as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    def add_repository(self, repo_data: dict) -> bool:
        """
        Добавить репозиторий в журнал

        Args:
            repo_data: Словарь с данными репозитория
                - full_name: str (обязательно)
                - display_name: str
                - description: str
                - version: str
                - version_type: str
                - stars: int
                - forks: int
                - github_url: str
                - zip_url: str
                - status: str (sent/failed/skipped)

        Returns:
            True если добавлен, False если уже существует
        """
        full_name = repo_data.get('full_name')
        if not full_name:
            return False

        # Проверить, существует ли уже
        for i, repo in enumerate(self.data["repositories"]):
            if repo.get('full_name') == full_name:
                # Обновить существующую запись
                self.data["repositories"][i].update(repo_data)
                self.save()
                return True

        # Добавить новую запись
        repo_data['downloaded_at'] = datetime.now().isoformat()
        self.data["repositories"].append(repo_data)
        self._update_stats()
        self.save()
        return True

    def update_repository(self, full_name: str, updates: dict) -> bool:
        """
        Обновить запись репозитория

        Args:
            full_name: Полное имя репозитория (owner/repo)
            updates: Словарь с обновлениями

        Returns:
            True если обновлён, False если не найден
        """
        for i, repo in enumerate(self.data["repositories"]):
            if repo.get('full_name') == full_name:
                self.data["repositories"][i].update(updates)
                self._update_stats()
                self.save()
                return True
        return False

    def get_repository(self, full_name: str) -> Optional[dict]:
        """
        Получить данные репозитория из журнала

        Args:
            full_name: Полное имя репозитория

        Returns:
            Словарь с данными или None
        """
        for repo in self.data["repositories"]:
            if repo.get('full_name') == full_name:
                return repo
        return None

    def get_all_repositories(self) -> list:
        """Получить все репозитории из журнала"""
        return self.data.get("repositories", [])

    def get_repositories_by_status(self, status: str) -> list:
        """Получить репозитории с определённым статусом"""
        return [r for r in self.data.get("repositories", [])
                if r.get('status') == status]

    def is_in_journal(self, full_name: str) -> bool:
        """Проверить, есть ли репозиторий в журнале"""
        return self.get_repository(full_name) is not None

    def get_processed_names(self) -> set:
        """Получить set имён обработанных репозиториев"""
        return {r['full_name'] for r in self.data.get("repositories", [])}

    def remove_repository(self, full_name: str) -> bool:
        """Удалить репозиторий из журнала"""
        for i, repo in enumerate(self.data["repositories"]):
            if repo.get('full_name') == full_name:
                del self.data["repositories"][i]
                self._update_stats()
                self.save()
                return True
        return False

    def _update_stats(self):
        """Обновить статистику журнала"""
        repos = self.data.get("repositories", [])
        self.data["total_processed"] = len(repos)
        self.data["total_sent"] = len([r for r in repos if r.get('status') == 'sent'])
        self.data["total_failed"] = len([r for r in repos if r.get('status') == 'failed'])

    def get_stats(self) -> dict:
        """Получить статистику журнала"""
        return {
            "total": self.data.get("total_processed", 0),
            "sent": self.data.get("total_sent", 0),
            "failed": self.data.get("total_failed", 0),
            "last_updated": self.data.get("last_updated", "")
        }

    def get_count(self) -> int:
        """Получить количество записей в журнале"""
        return len(self.data.get("repositories", []))

    def clear_failed(self) -> int:
        """Удалить все записи со статусом failed"""
        original_count = len(self.data.get("repositories", []))
        self.data["repositories"] = [
            r for r in self.data.get("repositories", [])
            if r.get('status') != 'failed'
        ]
        removed = original_count - len(self.data["repositories"])
        self._update_stats()
        self.save()
        return removed


if __name__ == "__main__":
    # Тестирование модуля
    journal = Journal("test_journal.json")

    # Добавить тестовый репозиторий
    journal.add_repository({
        "full_name": "test/repo",
        "display_name": "Test Repo",
        "description": "Test description",
        "version": "1.0.0",
        "stars": 1000,
        "forks": 100,
        "github_url": "https://github.com/test/repo",
        "zip_url": "https://github.com/test/repo/archive/refs/heads/main.zip",
        "status": "sent"
    })

    print(f"Журнал: {journal.get_count()} репозиториев")
    print(f"Статистика: {journal.get_stats()}")

    # Очистка тестового файла
    if os.path.exists("test_journal.json"):
        os.remove("test_journal.json")