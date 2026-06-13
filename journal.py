"""
Модуль работы с журналом (journal.json)
"""

import json
import os
import tempfile
import shutil
from datetime import datetime
from typing import Optional
from pathlib import Path
from enum import Enum
from logging_config import setup_logging

from shared_journal import BaseJournal, RuntimeJournalMixin


class RepoStatus(Enum):
    """Статусы репозиториев в журнале"""
    SENT = "sent"
    FAILED = "failed"
    PENDING = "pending"
    INCOMPLETE = "incomplete"
    RESTORED = "restored"
    CLEANED = "cleaned"


class Journal(RuntimeJournalMixin, BaseJournal):
    """Класс для управления журналом загруженных репозиториев"""

    def __init__(self, file_path: str = "journal.json"):
        super().__init__(file_path)

    def _create_empty(self) -> dict:
        """Создать пустой журнал"""
        return {
            "repositories": [],
            "ignored": [],
            "last_updated": "",
            "total_processed": 0,
            "total_sent": 0,
            "total_failed": 0
        }

    def _pre_save(self):
        """Update timestamp before saving"""
        self.data["last_updated"] = datetime.now().isoformat()

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
        for repo in self.data.get("repositories", []):
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

    def is_version_in_journal(self, full_name: str, version: str) -> bool:
        """
        Проверить, есть ли конкретная версия репозитория в журнале.
        Дублем считается запись с тем же full_name И той же version.

        Args:
            full_name: Полное имя репозитория (owner/repo)
            version: Версия (release tag или commit hash)

        Returns:
            True если версия уже есть в журнале
        """
        for repo in self.data.get("repositories", []):
            if repo.get('full_name') == full_name and repo.get('version') == version:
                return True
        return False

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
        self.data["total_sent"] = len([
            r for r in repos
            if r.get('status') == RepoStatus.SENT.value
        ])
        self.data["total_failed"] = len([
            r for r in repos
            if r.get('status') == RepoStatus.FAILED.value
        ])
        self.data["total_restored"] = len([
            r for r in repos
            if r.get('status') == RepoStatus.RESTORED.value
        ])
        self.data["total_incomplete"] = len([
            r for r in repos
            if r.get('status') == RepoStatus.INCOMPLETE.value
        ])

    def get_stats(self) -> dict:
        """Получить статистику журнала"""
        return {
            "total": self.data.get("total_processed", 0),
            "sent": self.data.get("total_sent", 0),
            "failed": self.data.get("total_failed", 0),
            "restored": self.data.get("total_restored", 0),
            "incomplete": self.data.get("total_incomplete", 0),
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

    def is_ignored(self, full_name: str) -> bool:
        """Проверить, есть ли репозиторий в списке игнорирования"""
        return full_name in self.data.get("ignored", [])

    def add_ignored(self, full_name: str) -> bool:
        """Добавить репозиторий в список игнорирования"""
        if not full_name:
            return False
        if full_name in self.data.get("ignored", []):
            return False
        self.data.setdefault("ignored", []).append(full_name)
        self.save()
        return True

    def add_ignored_batch(self, full_names: list) -> int:
        """Добавить несколько репозиториев в список игнорирования"""
        added = 0
        for name in full_names:
            if name and name not in self.data.setdefault("ignored", []):
                self.data["ignored"].append(name)
                added += 1
        if added:
            self.save()
        return added

    def remove_ignored(self, full_name: str) -> bool:
        """Удалить репозиторий из списка игнорирования"""
        ignored = self.data.get("ignored", [])
        if full_name in ignored:
            self.data["ignored"] = [n for n in ignored if n != full_name]
            self.save()
            return True
        return False

    def get_ignored(self) -> list:
        """Получить список игнорируемых репозиториев"""
        return list(self.data.get("ignored", []))

    def get_ignored_count(self) -> int:
        """Получить количество игнорируемых репозиториев"""
        return len(self.data.get("ignored", []))

    def clear_ignored(self) -> int:
        """Очистить весь список игнорирования"""
        count = len(self.data.get("ignored", []))
        self.data["ignored"] = []
        if count:
            self.save()
        return count


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
