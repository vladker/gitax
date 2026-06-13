"""
Модуль работы с GitHub API
"""

import os
import requests
import time
from datetime import datetime
from typing import Optional
from pathlib import Path
from tqdm import tqdm
from logging_config import LogMixin, setup_logging


class GitHubAPIError(Exception):
    """Base exception for GitHub API errors"""
    pass


class RateLimitError(GitHubAPIError):
    """Rate limit exceeded"""
    pass


class NotFoundError(GitHubAPIError):
    """Resource not found"""
    pass


class GitHubAPI(LogMixin):
    """Класс для работы с GitHub API"""

    BASE_URL = "https://api.github.com"
    DEFAULT_TIMEOUT = 30  # seconds for API requests

    @staticmethod
    def _mask_token(token: str) -> str:
        """Return a safely masked version of the token for logging.

        Shows only the first 3 characters and last 2 characters,
        replacing everything in between with asterisks.
        For short tokens (< 6 chars), returns '[token:****]'.
        """
        if len(token) < 6:
            return "[token:****]"
        return token[:3] + "*" * (len(token) - 5) + token[-2:]

    def __repr__(self) -> str:
        """Safe representation that never exposes the token."""
        return f"GitHubAPI(token={self._mask_token(self.token)!r}, output_dir={self.output_dir!r})"

    def __init__(self, token: str, output_dir: str = "./temp"):
        self.token = token
        self.output_dir = output_dir
        self.logger.info(f"GitHubAPI initialized with token {self._mask_token(token)}")
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
            try:
                response = self.session.request(
                    method, url, timeout=self.DEFAULT_TIMEOUT, **kwargs
                )
            except requests.exceptions.ConnectionError as e:
                self.logger.error(f"Connection error: {e}")
                raise GitHubAPIError(f"Cannot connect to GitHub: {e}")

            if response.status_code == 403:
                remaining = response.headers.get('X-RateLimit-Remaining')
                reset_time = response.headers.get('X-RateLimit-Reset')
                body_preview = response.text.lower()[:500] if response.text else ""
                is_rate_limit = (
                    remaining is not None and remaining == "0"
                ) or (
                    "rate limit" in body_preview
                )

                if is_rate_limit and reset_time:
                    wait_time = int(reset_time) - int(time.time()) + 5
                    if wait_time > 0:
                        self.logger.warning(f"Rate limit exceeded. Waiting {wait_time}s...")
                        time.sleep(min(wait_time, 60))
                        continue
                elif is_rate_limit:
                    raise RateLimitError("GitHub rate limit exceeded. Please wait before retrying.")
                else:
                    raise GitHubAPIError(
                        "Permission denied (403). Check that your token is valid and has the required scopes."
                    )

            if response.status_code == 404:
                raise NotFoundError(f"Resource not found: {endpoint}")

            if response.status_code == 401:
                raise GitHubAPIError("Authentication failed. Check your token.")

            if response.status_code >= 500:
                self.logger.warning(f"GitHub server error ({response.status_code}). Retrying...")
                time.sleep(5)
                continue

            if response.status_code >= 400:
                self.logger.error(f"Client error: {response.status_code} - {response.text[:200]}")
                raise GitHubAPIError(f"API error {response.status_code}")

            return response

    def get_top_repositories(self, limit: int = 100) -> list[dict]:
        """
        Получить топ репозиториев по звёздам

        Args:
            limit: Количество репозиториев

        Returns:
            Список словарей с данными репозиториев
        """
        repos = []
        per_page = min(100, limit)
        pages = (limit + per_page - 1) // per_page

        for page in range(1, pages + 1):
            remaining = limit - len(repos)
            if remaining <= 0:
                break

            current_per_page = min(per_page, remaining)

            self.logger.info(f"Loading page {page}/{pages}...")
            try:
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
            except GitHubAPIError as e:
                self.logger.error(f"API request failed: {e}")
                break

            if response.status_code != 200:
                self.logger.error(f"API error: {response.status_code}")
                break

            data = response.json()
            repos.extend(data.get("items", []))

            remaining_calls = response.headers.get('X-RateLimit-Remaining', 'N/A')
            self.logger.info(f"Got {len(data.get('items', []))} repos (remaining: {remaining_calls})")

            if len(data.get('items', [])) < current_per_page:
                break

        self.logger.info(f"Total repositories loaded: {len(repos)}")
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
        try:
            response = self._request("GET", f"/repos/{owner}/{repo}")
        except NotFoundError:
            self.logger.warning(f"Repository not found: {owner}/{repo}")
            return None
        except GitHubAPIError as e:
            self.logger.error(f"Error getting details: {e}")
            return None

        if response.status_code == 200:
            return response.json()
        else:
            self.logger.error(f"Error getting details: {response.status_code}")
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
        try:
            response = self._request("GET", f"/repos/{owner}/{repo}/releases/latest")
        except (NotFoundError, GitHubAPIError):
            return None

        if response.status_code == 200:
            return response.json()
        else:
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
        try:
            response = self._request("GET", f"/repos/{owner}/{repo}/commits/{branch}")
        except (NotFoundError, GitHubAPIError):
            return None

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

    def get_default_branch(self, owner: str, repo: str) -> str:
        """
        Получить имя дефолтной ветки репозитория через GitHub API.

        Args:
            owner: Владелец репозитория
            repo: Название репозитория

        Returns:
            Имя дефолтной ветки (обычно 'main' или 'master')
        """
        try:
            resp = self._request("GET", f"/repos/{owner}/{repo}")
            branch = resp.json().get("default_branch", "main")
            self.logger.info(f"Default branch for {owner}/{repo}: {branch}")
            return branch
        except Exception:
            self.logger.warning(f"Failed to detect default branch for {owner}/{repo}, using 'main'")
            return "main"

    def _try_download_zip(self, owner: str, repo: str, ref: str) -> Optional[str]:
        """
        Внутренний метод: скачать ZIP для конкретной ветки.

        Args:
            owner: Владелец репозитория
            repo: Название репозитория
            ref: Ветка для скачивания

        Returns:
            Путь к скачанному файлу или None
        """
        url = f"https://github.com/{owner}/{repo}/archive/refs/heads/{ref}.zip"
        filename = f"{owner}-{repo}-{ref}.zip"
        filepath = os.path.join(self.output_dir, filename)

        try:
            self.logger.info(f"Downloading: {url}")
            response = self.session.get(url, stream=True, timeout=120)

            if response.status_code == 404:
                return None

            if response.status_code != 200:
                self.logger.error(f"Download error: {response.status_code}")
                return None

            total = int(response.headers.get('content-length', 0))
            desc = filename[:40]
            with open(filepath, 'wb') as f:
                with tqdm(total=total, unit='B', unit_scale=True, desc=desc, leave=False) as pbar:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)
                        pbar.update(len(chunk))

            file_size = os.path.getsize(filepath)
            if file_size < 1000:
                self.logger.warning(f"Downloaded file too small ({file_size} bytes), likely error page")
                os.remove(filepath)
                return None

            self.logger.info(f"Downloaded: {filename} ({file_size / 1024 / 1024:.1f} MB)")
            return filepath

        except requests.exceptions.Timeout:
            self.logger.error("Download timeout")
            return None
        except requests.exceptions.ConnectionError as e:
            self.logger.error(f"Connection error during download: {e}")
            return None
        except Exception as e:
            self.logger.error(f"Download exception: {e}")
            if os.path.exists(filepath):
                try:
                    os.remove(filepath)
                except Exception:
                    pass
            return None

    def download_zip(self, owner: str, repo: str, ref: str = "main") -> Optional[str]:
        """
        Скачать репозиторий в ZIP.

        При неудаче автоматически определяет дефолтную ветку через API
        и повторяет попытку.

        Args:
            owner: Владелец репозитория
            repo: Название репозитория
            ref: Ветка или тег для скачивания

        Returns:
            Путь к скачанному файлу или None
        """
        # Первая попытка с переданной веткой
        result = self._try_download_zip(owner, repo, ref)
        if result:
            return result

        # Авто-детект дефолтной ветки через API
        self.logger.warning(f"Initial download failed for {owner}/{repo}, detecting default branch...")
        detected_branch = self.get_default_branch(owner, repo)

        if detected_branch != ref:
            self.logger.info(f"Detected branch '{detected_branch}' differs from '{ref}', retrying...")
            result = self._try_download_zip(owner, repo, detected_branch)
            if result:
                return result

        # Финальный fallback: попробовать main/master
        for fallback in ["main", "master"]:
            if fallback == ref or fallback == detected_branch:
                continue
            self.logger.info(f"Trying fallback branch: {fallback}")
            result = self._try_download_zip(owner, repo, fallback)
            if result:
                return result

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
            "updated_at": repo_info.get('updated_at', ''),
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