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
    DEFAULT_TIMEOUT = 10  # seconds for API requests

    @staticmethod
    def _mask_token(token: Optional[str]) -> str:
        """Return a safely masked version of the token for logging.

        Shows only the first 3 characters and last 2 characters,
        replacing everything in between with asterisks.
        For short tokens (< 6 chars), returns '[token:****]'.
        For None tokens, returns '[no token]'.
        """
        if not token:
            return "[no token]"
        if len(token) < 6:
            return "[token:****]"
        return token[:3] + "*" * (len(token) - 5) + token[-2:]

    def __repr__(self) -> str:
        """Safe representation that never exposes the token."""
        return f"GitHubAPI(token={self._mask_token(self.token)!r}, output_dir={self.output_dir!r})"

    def __init__(self, token: Optional[str] = None, output_dir: str = "./temp"):
        self.token = token
        self.output_dir = output_dir
        if token:
            self.logger.info(f"GitHubAPI initialized with token {self._mask_token(token)}")
        else:
            self.logger.warning("Running without GitHub token. Rate limit: 60 requests/hour (anonymous) vs 5000 requests/hour (authenticated).")
        self.session = self._create_session()
        self._ensure_output_dir()

    def _create_session(self) -> requests.Session:
        """Создать сессию с заголовками"""
        session = requests.Session()
        session.headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"
        }
        if self.token:
            session.headers["Authorization"] = f"Bearer {self.token}"
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
        seen_names = set()

        # GitHub Search API — only endpoint that correctly sorts by stars.
        # Hard cap: 1000 results (documented GitHub limitation).
        # The /repositories endpoint does NOT support sort=stars
        # (valid values: created, updated, pushed, full_name).
        per_page = min(100, limit)
        pages = (limit + per_page - 1) // per_page
        last_progress = 0
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
            if response.status_code == 403:
                self.logger.error("Rate limit исчерпан. Получите токен: https://github.com/settings/tokens")
                break
            if response.status_code != 200:
                self.logger.error(f"API error: {response.status_code}")
                break
            data = response.json()
            for repo in data.get("items", []):
                if repo.get("full_name") not in seen_names:
                    seen_names.add(repo["full_name"])
                    repos.append(repo)
            remaining_calls = response.headers.get('X-RateLimit-Remaining', 'N/A')
            self.logger.info(f"Got {len(data.get('items', []))} repos (remaining: {remaining_calls})")
            # Console progress every 10%
            pct = len(repos) * 100 // limit
            if pct - last_progress >= 10:
                last_progress = pct
                print(f"  ⏳ {len(repos):>6}/{limit} репозиториев ({pct}%)")
            if len(data.get('items', [])) < current_per_page:
                break

        self.logger.info(f"Total repositories loaded: {len(repos)}")
        print(f"  ✓ Получено {len(repos)} репозиториев")
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
            response = self.session.get(url, stream=True, timeout=30)

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

    GRAPHQL_URL = "https://api.github.com/graphql"

    def graphql_query(self, query: str, variables: Optional[dict] = None) -> Optional[dict]:
        """
        Execute a GitHub GraphQL query.

        GraphQL has higher rate limits (5000 points/hour vs 5000 REST req/hour)
        and supports cursor pagination beyond the 1000-result REST search cap.

        Args:
            query: GraphQL query string
            variables: Optional variables dict

        Returns:
            Parsed JSON response or None on error
        """
        payload = {"query": query}
        if variables:
            payload["variables"] = variables

        while True:
            try:
                response = self.session.post(
                    self.GRAPHQL_URL,
                    json=payload,
                    timeout=self.DEFAULT_TIMEOUT,
                )
            except requests.exceptions.ConnectionError as e:
                self.logger.error(f"GraphQL connection error: {e}")
                return None

            if response.status_code == 403:
                body_preview = response.text.lower()[:500]
                if "rate limit" in body_preview:
                    reset_time = response.headers.get("X-RateLimit-Reset")
                    if reset_time:
                        wait = int(reset_time) - int(time.time()) + 5
                        if wait > 0:
                            self.logger.warning(f"GraphQL rate limit. Waiting {min(wait, 60)}s...")
                            time.sleep(min(wait, 60))
                            continue
                return None

            if response.status_code >= 500:
                self.logger.warning(f"GraphQL server error ({response.status_code}). Retrying...")
                time.sleep(5)
                continue

            if response.status_code != 200:
                self.logger.error(f"GraphQL error: {response.status_code} - {response.text[:200]}")
                return None

            data = response.json()
            if "errors" in data:
                self.logger.error(f"GraphQL errors: {data['errors']}")
                return None
            return data.get("data")

    def search_repos_graphql(
        self,
        star_threshold: int = 100,
        max_repos: int = 1000,
        per_page: int = 100,
    ) -> list[dict]:
        """
        Search repositories via GraphQL with cursor pagination.

        Bypasses the REST 1000-result limit by paginating through results.

        Args:
            star_threshold: Minimum stars filter
            max_repos: Maximum repos to fetch
            per_page: Results per page (max 100)

        Returns:
            List of repo dicts (same shape as REST search items)
        """
        query = """
            query SearchRepos($query: String!, $first: Int, $after: String) {
                search(query: $query, type: REPOSITORY, first: $first, after: $after) {
                    pageInfo {
                        hasNextPage
                        endCursor
                    }
                    repositoryCount
                    nodes {
                        ... on Repository {
                            name
                            nameWithOwner
                            url
                            description
                            stargazers { totalCount }
                            forkCount
                            defaultBranchRef { name }
                            primaryLanguage { name }
                            updatedAt
                            pushedAt
                        }
                    }
                }
            }
        """

        repos = []
        seen = set()
        search_query = f"stars:>{star_threshold} sort:stars-desc"
        cursor = None

        self.logger.info(
            f"GraphQL search: stars>{star_threshold}, max={max_repos}, per_page={per_page}"
        )

        while len(repos) < max_repos:
            variables = {
                "query": search_query,
                "first": min(per_page, max_repos - len(repos)),
            }
            if cursor:
                variables["after"] = cursor

            data = self.graphql_query(query, variables)
            if not data:
                break

            search = data.get("search", {})
            nodes = search.get("nodes", [])
            page_info = search.get("pageInfo", {})

            for node in nodes:
                full_name = node.get("nameWithOwner", "")
                if full_name in seen:
                    continue
                seen.add(full_name)

                repos.append({
                    "name": node.get("name", ""),
                    "full_name": full_name,
                    "html_url": node.get("url", ""),
                    "description": node.get("description") or "",
                    "stargazers_count": node.get("stargazers", {}).get("totalCount", 0),
                    "forks_count": node.get("forkCount", 0),
                    "default_branch": node.get("defaultBranchRef", {}).get("name", "main"),
                    "language": node.get("primaryLanguage", {}).get("name"),
                    "updated_at": node.get("updatedAt", ""),
                    "pushed_at": node.get("pushedAt", ""),
                })

            if not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")

            self.logger.info(
                f"GraphQL page: {len(repos)} total, hasNext={page_info.get('hasNextPage', False)}"
            )

        return repos

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