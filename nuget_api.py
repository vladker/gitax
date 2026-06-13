"""
NuGet API wrapper.

Фетчит топ .NET пакеты и их версии через nuget.org API.
"""

import requests
from logging_config import LogMixin


class NuGetAPI(LogMixin):
    """Wrapper для nuget.org API (REST OData)."""

    BASE_URL = "https://api.nuget.org/v3"
    ODATA_URL = "https://api.nuget.org/v3-flatcontainer"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "gitax-nuget-archiver/1.0",
            "Accept": "application/json",
        })

    def get_top_packages(self, limit: int = 100) -> list[dict]:
        """
        Получить топ пакеты по количеству загрузок.

        Args:
            limit: Максимальное количество пакетов

        Returns:
            Список словарей с информацией о пакетах
        """
        packages = []
        skip = 0

        while len(packages) < limit:
            url = f"{self.BASE_URL}/registration/search"
            params = {
                "q": "*",
                "semVerLevel": "2.0.0",
                "$skip": skip,
                "$top": min(50, limit - len(packages)),
                "$orderby": "downloadCount desc",
            }

            try:
                resp = self.session.get(url, params=params, timeout=30)
                resp.raise_for_status()
                data = resp.json()
            except requests.RequestException as e:
                self.logger.error(f"Failed to fetch nuget packages: {e}")
                break

            items = data.get("@graph", [])
            if not items:
                break

            for item in items:
                if len(packages) >= limit:
                    break
                package_data = item.get("packageDetails", {})
                packages.append({
                    "id": item.get("id", ""),
                    "version": package_data.get("version", ""),
                    "description": item.get("description", "") or "",
                    "total_downloads": package_data.get("totalDownloads", 0),
                    "version_downloads": package_data.get("downloadCount", 0),
                    "license_url": package_data.get("licenseUrl", ""),
                    "project_url": package_data.get("projectUrl", ""),
                })

            skip += 50

        return packages

    def get_latest_version(self, package_id: str) -> str | None:
        """Получить последнюю версию пакета."""
        url = f"{self.BASE_URL}/registration5-semver1/{package_id.lower()}/index.json"
        try:
            resp = self.session.get(url, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            items = data.get("items", [])
            if items:
                return items[-1].get("catalogEntry", {}).get("package", {}).get("version", "")
        except requests.RequestException as e:
            self.logger.error(f"Failed to get latest version for {package_id}: {e}")
        return None

    def get_package_download_url(self, package_id: str, version: str) -> str:
        """Получить URL для скачивания .nupkg файла."""
        return f"{self.ODATA_URL}/{package_id.lower()}/{version}/{package_id}.{version}.nupkg"
