"""
Модуль работы с GitHub API
"""

import os
import requests
import time
from datetime import datetime
from typing import Optional
from pathlib import Path


class GitHubAPI:
    """Класс для работы с GitHub API"""

    BASE_URL = "https://api.github.com"

    def __init__(self, token: str, output_dir: str = "./temp"):
        self.token = token
        self.output_dir = output_dir
        self.session = self._create_session()
        self._ensure_output_dir()

    def _create_session(self) -> requests.Session:
        """Создать сессию с заголовками"""
        session = requests.Session()
        session.headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28"
        }
        return session

    def _ensure_output_dir(self):
        """Создать папку для загрузок если её нет"""
        Path(self.output_dir).mkdir(parents=True, exist_ok=True)

    def _request(self, method: str, endpoint: str, **kwargs) -> requests.Response:
        """
        Выполнить HTTP запрос с обработкой ошибок и rate limiting

        Args:
            method: GET, POST, etc.
            endpoint: API эндпоинт (без базового URL)
            **kwargs: Дополнительные параметры requests

        Returns:
            Response объект

        Raises:
            Exception при критических ошибках
        """
        url = f"{self.BASE_URL}{endpoint}"

        while True:
            response = self.session.request(method, url, **kwargs)

            # Rate limit exceeded
            if response.status_code == 403:
                reset_time = response.headers.get('X-RateLimit-Reset')
                if reset_time:
                    wait_time = int(reset_time) - int(time.time()) + 5
                    if wait_time > 0:
                        print(f"⚠ Rate limit. Жду {wait_time} сек...")
                        time.sleep(min(wait_time, 60))
                        continue

            # Success or client error
            if response.status_code < 500:
                return response

            # Server error - retry
            print(f"⚠ Сервер GitHub недоступен ({response.status_code}). Повторяю...")
            time.sleep(5)

    def get_top_repositories(self, limit: int = 100) -> list[dict]:
        """
        Получить топ репозиториев по звёздам

        Args:
            limit: Количество репозиториев

        Returns:
            Список словарей с данными репозиториев
        """
        repos = []
        per_page = min(100, limit)  # GitHub max per_page = 100
        pages = (limit + per_page - 1) // per_page

        for page in range(1, pages + 1):
            remaining = limit - len(repos)
            if remaining <= 0:
                break

            current_per_page = min(per_page, remaining)

            print(f"  Загружаю страницу {page}/{pages}...")
            response = self._request(
                "GET",
                "/search/repositories",
                params={
                    "q": "stars:>1000",
                    "sort": "stars",
                    "order": "desc",
                    "per_page": current_per_page,
                    "page": page
                }
            )

            if response.status_code != 200:
                print(f"✗ Ошибка API: {response.status_code}")
                break

            data = response.json()
            repos.extend(data.get("items", []))

            # Rate limit info
            remaining_calls = response.headers.get('X-RateLimit-Remaining', 'N/A')
            print(f"  → Получено {len(data.get('items', []))} репозиториев (осталось запросов: {remaining_calls})")

            if len(data.get('items', [])) < current_per_page:
                break

        return repos

    def get_repository_details(self, owner: str, repo: str) -> Optional[dict]:
        """
        Получить детальную информацию о репозитории

        Args:
            owner: Владелец репозитория
            repo: Название репозитория

        Returns:
            Словарь с данными или None
        """
        response = self._request("GET", f"/repos/{owner}/{repo}")

        if response.status_code == 200:
            return response.json()
        elif response.status_code == 404:
            return None
        else:
            print(f"✗ Ошибка получения деталей: {response.status_code}")
            return None

    def get_latest_release(self, owner: str, repo: str) -> Optional[dict]:
        """
        Получить информацию о последнем релизе

        Args:
            owner: Владелец репозитория
            repo: Название репозитория

        Returns:
            Словарь с данными релиза или None
        """
        response = self._request("GET", f"/repos/{owner}/{repo}/releases/latest")

        if response.status_code == 200:
            return response.json()
        else:
            # Нет релиза или другие ошибки
            return None

    def get_latest_commit(self, owner: str, repo: str, branch: str) -> Optional[str]:
        """
        Получить хеш последнего коммита на ветке

        Args:
            owner: Владелец репозитория
            repo: Название репозитория
            branch: Название ветки

        Returns:
            Хеш коммита (7 символов) или None
        """
        response = self._request("GET", f"/repos/{owner}/{repo}/commits/{branch}")

        if response.status_code == 200:
            data = response.json()
            return data.get('sha', '')[:7]
        else:
            return None

    def get_version_info(self, owner: str, repo: str, default_branch: str) -> tuple[str, str]:
        """
        Получить информацию о версии репозитория

        Приоритет:
        1. Последний релиз (тег)
        2. Последний коммит на default_branch

        Args:
            owner: Владелец репозитория
            repo: Название репозитория
            default_branch: Ветка по умолчанию

        Returns:
            (version, version_type) - версия и тип версии
        """
        # Попытка получить релиз
        release = self.get_latest_release(owner, repo)
        if release:
            tag_name = release.get('tag_name', '')
            if tag_name:
                return tag_name, "release"

        # Попытка получить последний коммит
        commit_hash = self.get_latest_commit(owner, repo, default_branch)
        if commit_hash:
            return commit_hash, "branch"

        return "unknown", "unknown"

    def download_zip(self, owner: str, repo: str, ref: str = "main") -> Optional[str]:
        """
        Скачать репозиторий в ZIP

        Args:
            owner: Владелец репозитория
            repo: Название репозитория
            ref: Ветка или тег для скачивания

        Returns:
            Путь к скачанному файлу или None
        """
        url = f"https://github.com/{owner}/{repo}/archive/refs/heads/{ref}.zip"

        # Если указана ветка, скачать с неё
        if ref not in ['main', 'master']:
            url = f"https://github.com/{owner}/{repo}/archive/refs/heads/{ref}.zip"

        filename = f"{owner}-{repo}-{ref}.zip"
        filepath = os.path.join(self.output_dir, filename)

        try:
            print(f"  ↓ Скачиваю: {url}")
            response = self.session.get(url, stream=True, timeout=120)

            if response.status_code == 200:
                with open(filepath, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)

                file_size = os.path.getsize(filepath)
                print(f"  ✓ Скачано: {filename} ({file_size / 1024 / 1024:.1f} MB)")
                return filepath
            else:
                print(f"  ✗ Ошибка скачивания: {response.status_code}")
                return None

        except Exception as e:
            print(f"  ✗ Исключение при скачивании: {e}")
            return None

    def build_repo_data(self, repo_info: dict) -> dict:
        """
        Построить словарь с данными репозитория для журнала

        Args:
            repo_info: Словарь с информацией от GitHub API

        Returns:
            Словарь с обработанными данными
        """
        owner = repo_info.get('owner', {}).get('login', '')
        repo_name = repo_info.get('name', '')
        full_name = repo_info.get('full_name', '')
        default_branch = repo_info.get('default_branch', 'main')

        # Получить версию
        version, version_type = self.get_version_info(owner, repo_name, default_branch)

        # URL для скачивания zip
        zip_url = f"https://github.com/{owner}/{repo_name}/archive/refs/heads/{default_branch}.zip"

        return {
            "full_name": full_name,
            "display_name": repo_name,
            "description": repo_info.get('description', '') or 'Без описания',
            "version": version,
            "version_type": version_type,
            "stars": repo_info.get('stargazers_count', 0),
            "forks": repo_info.get('forks_count', 0),
            "github_url": repo_info.get('html_url', ''),
            "zip_url": zip_url,
            "default_branch": default_branch,
            "language": repo_info.get('language', 'N/A'),
            "status": "pending"
        }

    def check_new_version(self, owner: str, repo: str, default_branch: str,
                         current_version: str) -> tuple[bool, str]:
        """
        Проверить, есть ли новая версия репозитория

        Args:
            owner: Владелец репозитория
            repo: Название репозитория
            default_branch: Ветка по умолчанию
            current_version: Текущая сохранённая версия

        Returns:
            (has_new_version, latest_version)
        """
        latest_version, _ = self.get_version_info(owner, repo, default_branch)

        has_new = latest_version != current_version
        return has_new, latest_version


if __name__ == "__main__":
    # Тестирование (нужен токен в переменной окружения или config)
    print("GitHub API модуль")
    print("Используйте: from github_api import GitHubAPI")