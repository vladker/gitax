"""
Cargo (crates.io) API wrapper.

Фетчит топ Rust пакеты и их версии через crates.io API.
"""

import requests
from logging_config import LogMixin


class CratesIOAPI(LogMixin):
    """Wrapper для crates.io API (REST)."""

    BASE_URL = "https://crates.io/api/v1"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "gitax-cargo-archiver/1.0",
            "Accept": "application/json",
        })

    def get_top_crates(self, limit: int = 100, per_page: int = 100) -> list[dict]:
        """
        Получить топ пакеты по количеству далайнов.

        Args:
            limit: Максимальное количество пакетов
            per_page: Пакетов на страницу

        Returns:
            Список словарей с информацией о пакетах
        """
        crates = []
        page = 1

        while len(crates) < limit:
            url = f"{self.BASE_URL}/crates"
            params = {
                "per_page": min(per_page, limit - len(crates)),
                "page": page,
                "sort": "downloads",
            }

            try:
                resp = self.session.get(url, params=params, timeout=30)
                resp.raise_for_status()
                data = resp.json()
            except requests.RequestException as e:
                self.logger.error(f"Failed to fetch crates page {page}: {e}")
                break

            page_crates = data.get("crates", [])
            if not page_crates:
                break

            for crate in page_crates:
                if len(crates) >= limit:
                    break
                crates.append({
                    "name": crate.get("name", ""),
                    "description": crate.get("description", "") or "",
                    "downloads": crate.get("downloads", 0),
                    "recent_downloads": crate.get("recent_downloads", 0),
                    "version": crate.get("max_version", ""),
                    "updated_at": crate.get("updated_at", ""),
                    "created_at": crate.get("created_at", ""),
                })

            page += 1

        return crates

    def get_crate_versions(self, name: str) -> list[dict]:
        """
        Получить все версии пакета.

        Args:
            name: Имя пакета

        Returns:
            Список версий
        """
        url = f"{self.BASE_URL}/crates/{name}/versions"
        try:
            resp = self.session.get(url, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            versions = []
            for v in data.get("versions", []):
                versions.append({
                    "num": v.get("num", ""),
                    "dl_path": v.get("dl_path", ""),
                    "created_at": v.get("created_at", ""),
                    "size": v.get("size", 0),
                })
            return versions
        except requests.RequestException as e:
            self.logger.error(f"Failed to fetch versions for {name}: {e}")
            return []

    def get_crate_download_url(self, name: str, version: str) -> str:
        """Получить URL для скачивания .crate файла."""
        return f"{self.BASE_URL}/crates/{name}/{version}/download"

    def get_real_crate_url(self, name: str, version: str) -> str | None:
        """
        Получить реальный URL .crate файла через JSON-ответ API.

        crates.io API больше не делает 302 редирект на .crate файл,
        а возвращает JSON: {"url": "https://static.crates.io/crates/..."}
        Этот метод извлекает реальный URL для скачивания.

        Args:
            name: Имя пакета
            version: Версия

        Returns:
            Реальный URL .crate файла или None при ошибке
        """
        url = self.get_crate_download_url(name, version)
        try:
            resp = self.session.get(url, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            real_url = data.get("url", "")
            if real_url:
                return real_url
            self.logger.error(f"No 'url' field in JSON response for {name} {version}: {data}")
            return None
        except requests.RequestException as e:
            self.logger.error(f"Failed to get real download URL for {name} {version}: {e}")
            return None
        except ValueError as e:
            self.logger.error(f"Invalid JSON response for {name} {version}: {e}")
            return None

    def get_latest_version(self, name: str) -> str | None:
        """Получить последнюю версию пакета."""
        url = f"{self.BASE_URL}/crates/{name}"
        try:
            resp = self.session.get(url, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            return data.get("crate", {}).get("max_version", "")
        except requests.RequestException as e:
            self.logger.error(f"Failed to get latest version for {name}: {e}")
            return None
