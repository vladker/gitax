"""
RubyGems API wrapper.

Фетчит топ Ruby пакеты и их версии через rubygems.org API.
"""

import requests
from logging_config import LogMixin


class RubyGemsAPI(LogMixin):
    """Wrapper для rubygems.org API (REST)."""

    BASE_URL = "https://rubygems.org/api/v1"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "gitax-rubygems-archiver/1.0",
            "Accept": "application/json",
        })

    def get_top_gems(self, limit: int = 100) -> list[dict]:
        """
        Получить топ гемы по количеству загрузок.

        Args:
            limit: Максимальное количество гемов

        Returns:
            Список словарей с информацией о гемах
        """
        gems = []
        page = 0

        while len(gems) < limit:
            url = f"{self.BASE_URL}/reverse_dependencies/rails"
            # rubygems.org не имеет прямого endpoint для "top gems"
            # используем поиск с сортировкой по популярности
            url = f"{self.BASE_URL}/search"
            params = {
                "query": "*",
                "page": page,
                "per_page": min(50, limit - len(gems)),
                "sort": "downloads",
            }

            try:
                resp = self.session.get(url, params=params, timeout=30)
                resp.raise_for_status()
                data = resp.json()
            except requests.RequestException as e:
                self.logger.error(f"Failed to fetch rubygems page {page}: {e}")
                break

            page_gems = data if isinstance(data, list) else []
            if not page_gems:
                break

            for gem in page_gems:
                if len(gems) >= limit:
                    break
                gems.append({
                    "name": gem.get("name", ""),
                    "description": gem.get("description", "") or "",
                    "downloads": gem.get("downloads_count", 0),
                    "version": gem.get("version", ""),
                    "created_at": gem.get("created_at", ""),
                    "homepage_uri": gem.get("homepage_uri", ""),
                })

            page += 1

        return gems

    def get_gem_versions(self, name: str) -> list[dict]:
        """
        Получить все версии гема.

        Args:
            name: Имя гема

        Returns:
            Список версий
        """
        url = f"{self.BASE_URL}/versions/{name}"
        try:
            resp = self.session.get(url, timeout=30)
            resp.raise_for_status()
            versions = resp.json()
            result = []
            for v in versions:
                result.append({
                    "number": v.get("number", ""),
                    "created_at": v.get("built_at", ""),
                    "platform": v.get("platform", ""),
                })
            return result
        except requests.RequestException as e:
            self.logger.error(f"Failed to fetch versions for {name}: {e}")
            return []

    def get_gem_download_url(self, name: str, version: str) -> str:
        """Получить URL для скачивания .gem файла."""
        return f"https://rubygems.org/gems/{name}-{version}.gem"

    def get_latest_version(self, name: str) -> str | None:
        """Получить последнюю версию гема."""
        url = f"{self.BASE_URL}/gems/{name}.json"
        try:
            resp = self.session.get(url, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            return data.get("version", "")
        except requests.RequestException as e:
            self.logger.error(f"Failed to get latest version for {name}: {e}")
        return None
