"""
Модуль работы с Thingiverse API
"""

import os
import re
import zipfile
import requests
from logging_config import LogMixin
from retry import retry


class ThingiverseAPIError(Exception):
    """Base exception for Thingiverse API errors"""
    pass


class NetworkError(ThingiverseAPIError):
    """Network-related error"""
    pass


class AuthError(ThingiverseAPIError):
    """Authentication error"""
    pass


class ThingiverseAPI(LogMixin):
    """Класс для работы с Thingiverse API"""

    BASE_URL = "https://api.thingiverse.com"
    DEFAULT_TIMEOUT = 60  # seconds
    MAX_PAGE_SIZE = 100  # Thingiverse API max per-page limit

    def __init__(self, token: str | None = None):
        self.token = token or os.environ.get("THINGIVERSE_TOKEN", "")
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "ThingiverseArchiver/1.0 (https://github.com/vldkr/gitax; contact: archiver@local)",
            "Accept": "application/json",
        })
        if self.token:
            self.session.headers["Authorization"] = f"Bearer {self.token}"

    def _request(self, path: str, params: dict | None = None) -> requests.Response:
        """
        Make an authenticated GET request to the Thingiverse API.

        Args:
            path: API path (e.g. '/popular', '/things/123')
            params: Query parameters

        Returns:
            Response object

        Raises:
            AuthError: On 401 responses
            NetworkError: On connection failures
        """
        url = f"{self.BASE_URL}{path}"
        try:
            response = self.session.get(url, params=params, timeout=self.DEFAULT_TIMEOUT)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            raise NetworkError(f"Cannot connect to Thingiverse: {e}")

        if response.status_code == 401:
            raise AuthError("Invalid or missing Thingiverse token")

        response.raise_for_status()
        return response

    def _paginate(self, path: str, params: dict, limit: int) -> list[dict]:
        """
        Fetch paginated results up to `limit` items.

        Args:
            path: API path
            params: Base query parameters (must not include 'limit'/'offset')
            limit: Maximum number of results to return

        Returns:
            List of result dictionaries
        """
        all_results: list[dict] = []
        offset = 0

        while limit > 0:
            page_size = min(limit, self.MAX_PAGE_SIZE)
            page_params = {**params, "limit": page_size, "offset": offset}
            response = self._request(path, page_params)
            data = response.json()

            results = data if isinstance(data, list) else []
            if not results:
                break

            all_results.extend(results)
            limit -= len(results)
            offset += len(results)

            # If we got fewer items than requested, we've reached the end
            if len(results) < page_size:
                break

        return all_results[:offset]

    def get_popular(self, weeks: int = 4, limit: int = 100) -> list[dict]:
        """
        Получить популярные вещи за указанный период.

        Args:
            weeks: Количество недель (по умолчанию 4)
            limit: Максимальное количество результатов

        Returns:
            Список словарей с данными вещей
        """
        self.logger.info(f"Fetching {limit} popular things (last {weeks} weeks)...")
        params = {"weeks": weeks}
        results = self._paginate("/popular", params, limit)
        self.logger.info(f"Got {len(results)} popular things")
        return results

    def get_by_tag(self, tag: str, limit: int = 100) -> list[dict]:
        """
        Поиск вещей по тегу.

        Args:
            tag: Тег для поиска
            limit: Максимальное количество результатов

        Returns:
            Список словарей с данными вещей
        """
        self.logger.info(f"Searching things by tag '{tag}' (limit {limit})...")
        params = {"tag": tag}
        results = self._paginate("/things", params, limit)
        self.logger.info(f"Got {len(results)} things for tag '{tag}'")
        return results

    def get_by_category(self, category: str, limit: int = 100) -> list[dict]:
        """
        Поиск вещей по категории.

        Args:
            category: Категория для поиска
            limit: Максимальное количество результатов

        Returns:
            Список словарей с данными вещей
        """
        self.logger.info(f"Searching things by category '{category}' (limit {limit})...")
        params = {"category": category}
        results = self._paginate("/things", params, limit)
        self.logger.info(f"Got {len(results)} things for category '{category}'")
        return results

    def get_by_author(self, author: str, limit: int = 100) -> list[dict]:
        """
        Получить вещи конкретного автора.

        Args:
            author: Имя пользователя автора
            limit: Максимальное количество результатов

        Returns:
            Список словарей с данными вещей
        """
        self.logger.info(f"Fetching things by author '{author}' (limit {limit})...")
        params = {}
        results = self._paginate(f"/users/{author}/things", params, limit)
        self.logger.info(f"Got {len(results)} things by '{author}'")
        return results

    def get_thing(self, thing_id: int) -> dict:
        """
        Получить детальную информацию о вещи.

        Args:
            thing_id: ID вещи

        Returns:
            Словарь с данными вещи
        """
        self.logger.info(f"Getting thing details: {thing_id}")
        response = self._request(f"/things/{thing_id}")
        return response.json()

    def download_thing(self, thing_id: int, output_dir: str = None) -> str:
        """
        Скачать все файлы вещи и упаковать в ZIP-архив.

        Args:
            thing_id: ID вещи

        Returns:
            Путь к созданному ZIP-файлу

        Raises:
            ValueError: Если файлы не найдены
        """
        thing = self.get_thing(thing_id)
        name = thing.get("name", f"thing-{thing_id}")
        safe_name = sanitize_filename(name) or f"thing-{thing_id}"

        if output_dir is None:
            output_dir = f"./temp_thingiverse/{thing_id}"
        zip_path = os.path.join(output_dir, f"{safe_name}.zip")
        os.makedirs(output_dir, exist_ok=True)

        if os.path.exists(zip_path):
            self.logger.info(f"ZIP already exists: {zip_path}")
            return zip_path

        self.logger.info(f"Downloading thing {thing_id}: {name}")

        # Fetch file list
        response = self._request(f"/things/{thing_id}/files")
        data = response.json()
        files = data if isinstance(data, list) else []

        if not files:
            self.logger.warning(f"No files found for thing {thing_id}")
            raise ValueError(f"No files found for thing {thing_id}")

        downloaded = 0
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for file_info in files:
                file_url = file_info.get("download_url")
                file_name = file_info.get("name", f"file-{file_info.get('id', 'unknown')}")

                if not file_url:
                    continue

                try:
                    content = self._download_file(file_url)
                    zf.writestr(file_name, content)
                    downloaded += 1
                except Exception as e:
                    self.logger.warning(f"Failed to download file '{file_name}': {e}")

        if downloaded == 0:
            raise ValueError(f"No downloadable files for thing {thing_id}")

        size_mb = os.path.getsize(zip_path) / (1024 * 1024)
        self.logger.info(f"Downloaded: {safe_name}.zip ({downloaded} files, {size_mb:.1f} MB)")
        return zip_path

    @retry(max_retries=3, delay=2.0, backoff=2.0,
           exceptions=(requests.exceptions.ConnectionError, requests.exceptions.Timeout))
    def _download_file(self, url: str) -> bytes:
        """Download a single file and return its content."""
        resp = self.session.get(url, timeout=120)
        resp.raise_for_status()
        return resp.content


def sanitize_filename(name: str) -> str:
    """
    Sanitize a string for safe use as a filename.

    Strips special characters, replaces spaces with underscores,
    and collapses consecutive underscores.

    Args:
        name: Original name string

    Returns:
        Sanitized filename-safe string
    """
    # Keep only alphanumeric, spaces, underscores, hyphens, dots
    safe = re.sub(r"[^\w\s.-]", "", name)
    # Replace spaces with underscores
    safe = safe.replace(" ", "_")
    # Collapse consecutive underscores
    safe = re.sub(r"_+", "_", safe)
    # Strip leading/trailing underscores and dots
    safe = safe.strip("_.")
    return safe
