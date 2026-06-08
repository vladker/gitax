"""
Модуль работы с PyPI API
"""

import os
import time
import requests
from logging_config import LogMixin


class PyPIAPIError(Exception):
    """Base exception for PyPI API errors"""
    pass


class NetworkError(PyPIAPIError):
    """Network-related error"""
    pass


class RateLimitError(PyPIAPIError):
    """Rate limit exceeded"""
    pass


class PyPIAPI(LogMixin):
    """Класс для работы с PyPI API"""

    HUGOVK_URL = "https://hugovk.github.io/top-pypi-packages/top-pypi-packages.min.json"
    DEFAULT_TIMEOUT = 60  # seconds

    def __init__(self):
        self._base_url = "https://pypi.org"
        self.session = requests.Session()
        self._cache: dict[str, dict] = {}

    def _request_with_backoff(self, url: str, timeout: int | None = None) -> requests.Response:
        """
        Make a request with exponential backoff for rate limiting (429) and server errors (5xx).

        Args:
            url: Request URL
            timeout: Request timeout in seconds (default: DEFAULT_TIMEOUT)

        Returns:
            Response object

        Raises:
            RateLimitError: If rate limit cannot be recovered
            NetworkError: On connection failures
        """
        if timeout is None:
            timeout = self.DEFAULT_TIMEOUT

        max_retries = 5
        for attempt in range(max_retries):
            try:
                response = self.session.get(url, timeout=timeout)
            except requests.exceptions.ConnectionError as e:
                self.logger.error(f"Connection error: {e}")
                raise NetworkError(f"Cannot connect to PyPI: {e}")
            except requests.exceptions.Timeout:
                self.logger.warning(f"Request timeout after {timeout}s (attempt {attempt + 1}/{max_retries})")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise NetworkError(f"Request timed out after {max_retries} attempts")

            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", 10))
                self.logger.warning(f"Rate limited. Waiting {retry_after}s...")
                time.sleep(min(retry_after, 60))
                continue

            if response.status_code >= 500:
                self.logger.warning(f"Server error ({response.status_code}). Retrying...")
                time.sleep(2 ** attempt)
                continue

            response.raise_for_status()
            return response

        raise RateLimitError(f"Max retries exceeded for {url}")

    def fetch_top_packages(self, limit: int = 1000) -> list[dict]:
        """
        Получить топ Python-пакетов из датасета Hugovk

        Args:
            limit: Количество пакетов

        Returns:
            Список словарей с данными пакетов
        """
        self.logger.info(f"Fetching top {limit} packages from Hugovk dataset...")
        response = requests.get(self.HUGOVK_URL, timeout=self.DEFAULT_TIMEOUT)
        response.raise_for_status()

        data = response.json()
        raw_packages = data.get("top-packages", [])[:limit]

        packages = []
        for pkg in raw_packages:
            packages.append({
                "name": pkg.get("pypi-name", ""),
                "latest_version": pkg.get("pypi-version", ""),
                "downloads_last_365_days": int(pkg.get("download-simple-365-days", 0)),
            })

        self.logger.info(f"Fetched {len(packages)} packages")
        return packages

    def get_package_info(self, package_name: str) -> dict:
        """
        Получить детальную информацию о пакете.
        Результат кэшируется в памяти — повторные вызовы для одного пакета
        не делают новый HTTP-запрос.

        Args:
            package_name: Имя пакета

        Returns:
            Словарь с данными пакета
        """
        # Return cached result if available
        if package_name in self._cache:
            self.logger.debug(f"Cache hit for package: {package_name}")
            return self._cache[package_name]

        url = _get_package_info_url(package_name)
        self.logger.info(f"Getting info for package: {package_name}")

        response = self._request_with_backoff(url)
        data = response.json()
        info = data.get("info", {})

        # Build releases list from releases dict
        releases_dict = data.get("releases", {})
        releases_list = []
        for version_files in releases_dict.values():
            releases_list.extend(version_files)

        result = {
            "latest_version": info.get("version", ""),
            "info": {
                "summary": info.get("summary", ""),
                "license": info.get("license", ""),
            },
            "releases": releases_list,
        }

        # Cache the raw data for download_package to reuse
        self._cache[package_name] = result
        self._cache[f"{package_name}__raw"] = data

        return result

    def download_package(self, package_name: str) -> list[str]:
        """
        Скачать пакет (.tar.gz и .whl).
        Reuses the cached API response from get_package_info — no duplicate HTTP call.

        Args:
            package_name: Имя пакета

        Returns:
            Список путей к скачанным файлам

        Raises:
            ValueError: Если файлы не найдены
        """
        pkg_info = self.get_package_info(package_name)
        version = pkg_info.get("latest_version", "")

        # Reuse cached raw API data — no second HTTP request
        data = self._cache.get(f"{package_name}__raw", {})

        # Support both 'urls' key and nested 'releases' structure
        urls = data.get("urls", [])
        if not urls:
            releases = data.get("releases", {})
            if isinstance(releases, dict):
                urls = releases.get(version, [])
            elif isinstance(releases, list):
                urls = releases

        if not urls:
            raise ValueError("No files found")

        output_dir = _get_output_dir(package_name)
        os.makedirs(output_dir, exist_ok=True)

        downloaded_files = []
        for file_info in urls:
            filename = file_info.get("filename", "")
            file_url = file_info.get("url", "")

            if not filename or not file_url:
                continue

            # Only download .tar.gz and .whl files
            if not (filename.endswith(".tar.gz") or filename.endswith(".whl")):
                continue

            filepath = os.path.join(output_dir, filename)

            if os.path.exists(filepath):
                self.logger.info(f"File already exists: {filename}")
                downloaded_files.append(filepath)
                continue

            self.logger.info(f"Downloading: {filename}")
            resp = self.session.get(file_url, timeout=120)
            resp.raise_for_status()

            with open(filepath, "wb") as f:
                f.write(resp.content)

            size_mb = _file_size_mb(filepath)
            self.logger.info(f"Downloaded: {filename} ({size_mb:.1f} MB)")
            downloaded_files.append(filepath)

        if not downloaded_files:
            raise ValueError("No files found")

        return downloaded_files

    def clear_cache(self):
        """Clear the in-memory cache."""
        self._cache.clear()
        self.logger.debug("Package info cache cleared")


def _get_package_info_url(package_name: str) -> str:
    """Get the PyPI JSON API URL for a package"""
    return f"https://pypi.org/pypi/{package_name}/json"


def _get_output_dir(package_name: str) -> str:
    """Get the output directory path for a package"""
    return f"./temp_pypi/{package_name}"


def _file_size_mb(filepath: str) -> float:
    """Get file size in megabytes"""
    return os.path.getsize(filepath) / (1024 * 1024)
