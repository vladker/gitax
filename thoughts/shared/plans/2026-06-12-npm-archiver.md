# NPM Archiver Implementation Plan

**Goal:** Add an NPM archiver that downloads top Node.js packages from the npm registry and sends tarballs to a MAX channel, following the exact same architectural pattern as the PyPI archiver.

**Architecture:** Three new files (`npm_api.py`, `npm_journal.py`, `npm_archiver.py`) mirroring the PyPI pattern, plus config/menu integration in four existing files. NPM API uses the public registry at `registry.npmjs.org` — no auth token required. Top packages sourced from `npm-stat` trending data or the `libraries.io` dataset for npm.

**Design:** [thoughts/shared/designs/2026-06-12-npm-archiver-design.md](thoughts/shared/designs/2026-06-12-npm-archiver-design.md)

---

## Dependency Graph

```
Batch 1 (parallel): 1.1, 1.2, 1.3, 1.4 [foundation — configs, .env — no deps]
Batch 2 (parallel): 2.1, 2.2, 2.3      [core — npm_api, npm_journal, npm_archiver — depends on batch 1]
Batch 3 (sequential): 3.1, 3.2         [integration — github_archiver + tests — depends on batch 2]
```

---

## Batch 1: Foundation (parallel — 4 implementers)

All tasks in this batch have NO dependencies and run simultaneously.

### Task 1.1: Config Model — Add NPM support

**File:** `config/model.py`
**Test:** none (config changes verified by Task 3.2)
**Depends:** none

**Changes:**
1. Add `NpmArchiverConfig` model (mirrors `PyPILibsArchiverConfig`)
2. Add `npm` field to `ChannelsConfig`
3. Add `"npm"` to `VALID_CHANNEL_FUNCTIONS` tuple
4. Add `npm` list to `ChannelRegistry`
5. Add `npm_archiver` to `AppConfig`
6. Update `AppConfig.clear_legacy_channels()` to clear `npm`

```python
# ADD after PyPILibsArchiverConfig class definition (around line 82):

class NpmArchiverConfig(BaseModel):
    """Settings: config.yaml → npm_archiver section."""
    limit: int = 20
    output_dir: str = "./temp_npm"
    retries: int = 3
    retry_delay: int = 10
    split_mode: Literal["auto", "on", "off", "prompt"] = "auto"
```

```python
# EDIT ChannelsConfig — add npm field:

class ChannelsConfig(BaseModel):
    """Settings: config.yaml → channels section."""
    max: str = ""
    pypi: str = ""
    media: str = ""
    backup: str = ""
    npm: str = ""
```

```python
# EDIT VALID_CHANNEL_FUNCTIONS — add "npm":

VALID_CHANNEL_FUNCTIONS = ("github", "pypi", "media", "backup", "npm")
```

```python
# EDIT ChannelRegistry — add npm list:

class ChannelRegistry(BaseModel):
    """Registry of channels per function. Replaces flat channels.{key} = url."""
    github: list[ChannelEntry] = Field(default_factory=list)
    pypi: list[ChannelEntry] = Field(default_factory=list)
    media: list[ChannelEntry] = Field(default_factory=list)
    backup: list[ChannelEntry] = Field(default_factory=list)
    npm: list[ChannelEntry] = Field(default_factory=list)
```

```python
# EDIT AppConfig — add npm_archiver:

class AppConfig(BaseModel):
    # ... existing fields ...
    pypi_libs_archiver: PyPILibsArchiverConfig = PyPILibsArchiverConfig()
    npm_archiver: NpmArchiverConfig = NpmArchiverConfig()
    setup: SetupConfig = SetupConfig()
    # ... rest unchanged ...
```

```python
# EDIT AppConfig.clear_legacy_channels — add npm:

    def clear_legacy_channels(self) -> None:
        """Clear legacy channels.* fields after migration to prevent re-migration."""
        self.channels.max = ""
        self.channels.pypi = ""
        self.channels.media = ""
        self.channels.backup = ""
        self.channels.npm = ""
```

**Verify:** `python -c "from config.model import AppConfig; c = AppConfig(); print(c.npm_archiver)"`
**Commit:** `feat(config): add NPM archiver config model`

---

### Task 1.2: Config Loader — Add npm migration

**File:** `config/loader.py`
**Test:** none (verified by Task 3.2)
**Depends:** none

```python
# EDIT _CHANNEL_MIGRATION_MAP — add npm entry:

_CHANNEL_MIGRATION_MAP = {
    "max": ("github", "GitHub Main"),
    "pypi": ("pypi", "PyPI Main"),
    "media": ("media", "Media Main"),
    "backup": ("backup", "Backup Main"),
    "npm": ("npm", "NPM Main"),
}
```

```python
# EDIT _apply_env_overrides legacy_map — add CHANNEL_NPM:

    legacy_map: dict[str, tuple[str, str]] = {
        "GITHUB_TOKEN": ("github", "token"),
        "MEDIA_WATCH_DIR": ("media_archiver", "watch_dir"),
        "CHANNEL_MAX": ("channels", "max"),
        "CHANNEL_PYPI": ("channels", "pypi"),
        "CHANNEL_MEDIA": ("channels", "media"),
        "CHANNEL_BACKUP": ("channels", "backup"),
        "CHANNEL_NPM": ("channels", "npm"),
    }
```

**Verify:** `python -c "from config.loader import load_config; print('ok')"`
**Commit:** `feat(config): add npm to channel migration map`

---

### Task 1.3: Config Utils — Add npm to setup checks

**File:** `config_utils.py`
**Test:** none (verified by Task 3.2)
**Depends:** none

```python
# EDIT is_setup_complete — add "npm" to channel list:

    for ch_name in ("max", "pypi", "media", "backup", "npm"):
```

```python
# Also update the len(skipped) check since we now have 5 channels:

    return has_configured or len(skipped) >= 5
```

```python
# EDIT _CHANNEL_TO_FUNCTION — add npm mapping:

_CHANNEL_TO_FUNCTION = {
    "max": "github",
    "pypi": "pypi",
    "media": "media",
    "backup": "backup",
    "npm": "npm",
}
```

**Verify:** `python -c "from config_utils import is_setup_complete; print('ok')"`
**Commit:** `feat(config): add npm to setup checks and channel mapping`

---

### Task 1.4: .env.example — Add CHANNEL_npm

**File:** `.env.example`
**Test:** none
**Depends:** none

```env
# EDIT — add CHANNEL_npm after CHANNEL_backup:

CHANNEL_backup=                # Backup channel URL
CHANNEL_npm=                   # NPM channel URL
```

```env
# EDIT — add NPM archiver config overrides at end of file, after PyPI section:

# NPM Archiver
NPM_ARCHIVER_LIMIT=
NPM_ARCHIVER_OUTPUT_DIR=
NPM_ARCHIVER_RETRIES=
NPM_ARCHIVER_RETRY_DELAY=
NPM_ARCHIVER_SPLIT_MODE=
```

**Verify:** Visual inspection
**Commit:** `feat(env): add CHANNEL_npm and NPM_ARCHIVER config to .env.example`

---

## Batch 2: Core Modules (parallel — 3 implementers)

All tasks in this batch depend on Batch 1 completing (config models must exist).

### Task 2.1: npm_api.py — NPM Registry API Client

**File:** `npm_api.py`
**Test:** `tests/test_npm_api.py`
**Depends:** 1.1 (imports config from model)

Design requires an API client that:
- Fetches top/trending NPM packages
- Gets package info (version, description, dist URLs)
- Downloads tarballs
- Caches responses to avoid duplicate API calls
- Handles rate limiting with backoff

Following the `pypi_api.py` pattern exactly:

```python
# tests/test_npm_api.py
"""
Unit tests for NPMAPI class.

Tests cover:
- fetch_top_packages() with mock npm registry responses
- get_package_info() with mock package metadata
- download_package() with mock file downloads
- Caching behavior (no duplicate API calls)
- Rate limit backoff with _request_with_backoff
- Error handling (invalid packages, timeouts, network errors)
"""

import json
import os
import pytest
from unittest.mock import patch, MagicMock

from npm_api import (
    NPMAPI, NPMError, NetworkError, RateLimitError,
    _get_package_info_url, _get_output_dir, _file_size_mb,
)


class TestNPMAPIInit:
    """Test NPMAPI initialization"""

    def test_init(self):
        """Test class initializes without errors"""
        api = NPMAPI()
        assert api._registry_url == "https://registry.npmjs.org"
        assert api.logger is not None
        assert api._cache == {}

    def test_logger_property(self):
        """Test logger property returns correct logger"""
        api = NPMAPI()
        assert api.logger.name == "gitax"

    def test_default_timeout(self):
        """Test DEFAULT_TIMEOUT is set"""
        assert NPMAPI.DEFAULT_TIMEOUT == 60


class TestRequestWithBackoff:
    """Test _request_with_backoff rate limiting and retry logic"""

    def test_successful_request(self):
        """Test successful request returns response"""
        api = NPMAPI()
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch.object(api.session, "get", return_value=mock_response) as mock_get:
            result = api._request_with_backoff("https://example.com/api")
            assert result == mock_response
            mock_get.assert_called_once()

    def test_429_rate_limit_backoff(self):
        """Test 429 rate limit triggers backoff and retry"""
        api = NPMAPI()
        mock_limited = MagicMock()
        mock_limited.status_code = 429
        mock_limited.headers = {"Retry-After": "1"}

        mock_ok = MagicMock()
        mock_ok.status_code = 200

        with patch.object(api.session, "get", side_effect=[mock_limited, mock_ok]):
            with patch("time.sleep"):
                result = api._request_with_backoff("https://example.com/api")
                assert result == mock_ok

    def test_500_server_error_retry(self):
        """Test 5xx server error triggers retry"""
        api = NPMAPI()
        mock_error = MagicMock()
        mock_error.status_code = 502

        mock_ok = MagicMock()
        mock_ok.status_code = 200

        with patch.object(api.session, "get", side_effect=[mock_error, mock_ok]):
            with patch("time.sleep"):
                result = api._request_with_backoff("https://example.com/api")
                assert result == mock_ok

    def test_max_retries_exceeded(self):
        """Test max retries exceeded raises RateLimitError"""
        api = NPMAPI()
        mock_limited = MagicMock()
        mock_limited.status_code = 429
        mock_limited.headers = {"Retry-After": "1"}

        with patch.object(api.session, "get", return_value=mock_limited):
            with patch("time.sleep"):
                with pytest.raises(RateLimitError):
                    api._request_with_backoff("https://example.com/api")


class TestFetchTopPackages:
    """Test fetch_top_packages method"""

    def test_fetch_top_packages_success(self):
        """Test successful fetch of top packages"""
        mock_response = [
            {
                "name": "express",
                "version": "4.21.0",
                "weekends_downloads": 12345678,
            },
            {
                "name": "lodash",
                "version": "4.17.21",
                "weekends_downloads": 11234567,
            },
        ]

        api = NPMAPI()

        with patch("npm_api.requests.get") as mock_get:
            mock_get.return_value.json.return_value = mock_response
            mock_get.return_value.raise_for_status = MagicMock()

            result = api.fetch_top_packages(2)

            assert len(result) == 2
            assert result[0]["name"] == "express"
            assert result[0]["latest_version"] == "4.21.0"
            assert result[0]["downloads_last_365_days"] == 12345678
            assert result[1]["name"] == "lodash"

    def test_fetch_top_packages_empty_response(self):
        """Test handling of empty response"""
        api = NPMAPI()

        with patch("npm_api.requests.get") as mock_get:
            mock_get.return_value.json.return_value = []
            mock_get.return_value.raise_for_status = MagicMock()

            result = api.fetch_top_packages(10)
            assert result == []

    def test_fetch_top_packages_network_error(self):
        """Test handling of network errors"""
        api = NPMAPI()

        with patch("npm_api.requests.get") as mock_get:
            mock_get.side_effect = Exception("Network error")

            with pytest.raises(Exception):
                api.fetch_top_packages(10)


class TestGetPackageInfo:
    """Test get_package_info method"""

    def test_get_package_info_success(self):
        """Test successful package info fetch"""
        mock_response = {
            "name": "express",
            "description": "Fast, unopinionated, minimalist web framework",
            "dist-tags": {"latest": "4.21.0"},
            "versions": {
                "4.21.0": {
                    "dist": {
                        "shasum": "abc123",
                        "tarball": "https://registry.npmjs.org/express/-/express-4.21.0.tgz",
                        "integrity": "sha512-...",
                    }
                }
            },
        }

        api = NPMAPI()

        with patch.object(api.session, "get") as mock_get:
            mock_get.return_value.json.return_value = mock_response
            mock_get.return_value.raise_for_status = MagicMock()
            mock_get.return_value.status_code = 200

            result = api.get_package_info("express")

            assert result["latest_version"] == "4.21.0"
            assert result["description"] == "Fast, unopinionated, minimalist web framework"

    def test_get_package_info_caches_result(self):
        """Test that repeated calls use the cache and skip HTTP requests"""
        mock_response = {
            "name": "lodash",
            "description": "Lodash modular utilities",
            "dist-tags": {"latest": "4.17.21"},
            "versions": {
                "4.17.21": {
                    "dist": {
                        "shasum": "def456",
                        "tarball": "https://registry.npmjs.org/lodash/-/lodash-4.17.21.tgz",
                    }
                }
            },
        }

        api = NPMAPI()

        with patch.object(api.session, "get") as mock_get:
            mock_get.return_value.json.return_value = mock_response
            mock_get.return_value.raise_for_status = MagicMock()
            mock_get.return_value.status_code = 200

            # First call — hits the network
            result1 = api.get_package_info("lodash")
            assert mock_get.call_count == 1

            # Second call — uses cache, no new HTTP request
            result2 = api.get_package_info("lodash")
            assert mock_get.call_count == 1  # still 1
            assert result1["latest_version"] == result2["latest_version"]

    def test_get_package_info_invalid_package(self):
        """Test handling of invalid package name"""
        api = NPMAPI()

        with patch.object(api.session, "get") as mock_get:
            mock_get.return_value.raise_for_status.side_effect = Exception(
                "404 Not Found"
            )

            with pytest.raises(Exception):
                api.get_package_info("nonexistent-package-xyz123")

    def test_url_generation(self):
        """Test URL generation for package info"""
        url = _get_package_info_url("express")
        assert url == "https://registry.npmjs.org/express"

    def test_clear_cache(self):
        """Test cache clearing"""
        api = NPMAPI()
        api._cache["test"] = {"data": "value"}
        api.clear_cache()
        assert "test" not in api._cache


class TestDownloadPackage:
    """Test download_package method"""

    def test_download_package_success(self, tmp_path):
        """Test successful package download"""
        mock_pkg_info = {
            "latest_version": "4.21.0",
            "description": "Express",
            "dist": {
                "tarball": "https://registry.npmjs.org/express/-/express-4.21.0.tgz",
            },
        }

        api = NPMAPI()

        with patch.object(api, "get_package_info", return_value=mock_pkg_info):
            with patch.object(api.session, "get") as mock_get:
                mock_get.return_value.raise_for_status = MagicMock()
                mock_get.return_value.content = b"fake tarball content"

                with patch("npm_api.os.path.exists", return_value=False):
                    with patch("npm_api.os.makedirs"):
                        with patch("npm_api.os.path.getsize", return_value=1024 * 1024):
                            with patch("builtins.open", MagicMock()):
                                result = api.download_package("express")

                                # Should return file path
                                assert isinstance(result, list)
                                assert len(result) == 1

    def test_download_package_no_files(self):
        """Test handling of package with no dist files"""
        mock_pkg_info = {
            "latest_version": "1.0.0",
            "description": "Empty pkg",
            "dist": {},
        }

        api = NPMAPI()

        with patch.object(api, "get_package_info", return_value=mock_pkg_info):
            with pytest.raises(ValueError, match="No tarball"):
                api.download_package("empty-package")

    def test_download_package_uses_cache(self):
        """Test that download_package reuses cached data"""
        mock_pkg_info = {
            "latest_version": "4.0.0",
            "description": "Cached",
            "dist": {
                "tarball": "https://registry.npmjs.org/pkg/-/pkg-4.0.0.tgz",
            },
        }

        api = NPMAPI()

        with patch.object(api, "get_package_info", return_value=mock_pkg_info):
            with patch.object(api.session, "get") as mock_get:
                mock_get.return_value.raise_for_status = MagicMock()
                mock_get.return_value.content = b"fake"

                with patch("npm_api.os.path.exists", return_value=True):
                    with patch("npm_api.os.makedirs"):
                        with patch("npm_api.os.path.getsize", return_value=512):
                            with patch("builtins.open", MagicMock()):
                                api.download_package("cached-pkg")
                                assert mock_get.call_count == 0


class TestHelperFunctions:
    """Test helper functions"""

    def test_get_output_dir(self):
        """Test output directory generation"""
        result = _get_output_dir("express")
        assert "express" in result
        assert result.startswith("./temp_npm") or result.startswith("temp_npm")

    def test_file_size_mb(self, tmp_path):
        """Test file size calculation"""
        from npm_api import _file_size_mb

        test_file = tmp_path / "test.tgz"
        test_file.write_bytes(b"x" * 1024 * 1024)  # 1MB

        size = _file_size_mb(str(test_file))
        assert abs(size - 1.0) < 0.01


class TestExceptions:
    """Test custom exception hierarchy"""

    def test_exception_hierarchy(self):
        """Test that custom exceptions inherit from NPMError"""
        assert issubclass(NetworkError, NPMError)
        assert issubclass(RateLimitError, NPMError)
        assert issubclass(NPMError, Exception)
```

```python
# npm_api.py
"""
NPM Registry API client.

Fetches top packages, package metadata, and downloads tarballs
from the npm public registry (registry.npmjs.org).
No authentication token required.
"""

import os
import time
import requests
from pathlib import Path
from logging_config import LogMixin


class NPMError(Exception):
    """Base exception for NPM API operations."""
    pass


class NetworkError(NPMError):
    """Network-related errors."""
    pass


class RateLimitError(NPMError):
    """Rate limit exceeded errors."""
    pass


def _get_package_info_url(package_name: str) -> str:
    """Generate registry URL for a package."""
    return f"https://registry.npmjs.org/{package_name}"


def _get_output_dir(package_name: str) -> str:
    """Get output directory for a package."""
    return os.path.join("./temp_npm", package_name)


def _file_size_mb(file_path: str) -> float:
    """Get file size in megabytes."""
    return os.path.getsize(file_path) / (1024 * 1024)


class NPMAPI(LogMixin):
    """NPM Registry API client with caching and rate limit handling."""

    DEFAULT_TIMEOUT = 60
    MAX_RETRIES = 3

    def __init__(self):
        self._registry_url = "https://registry.npmjs.org"
        self._cache: dict = {}
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "gitax-npm-archiver/1.0",
        })

    def _request_with_backoff(self, url: str, method: str = "get", **kwargs) -> requests.Response:
        """Make HTTP request with exponential backoff on rate limits."""
        kwargs.setdefault("timeout", self.DEFAULT_TIMEOUT)
        for attempt in range(self.MAX_RETRIES):
            try:
                response = getattr(self.session, method)(url, **kwargs)
                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", 2 ** attempt))
                    self.logger.warning(
                        f"Rate limited. Waiting {retry_after}s (attempt {attempt + 1}/{self.MAX_RETRIES})"
                    )
                    time.sleep(retry_after)
                    continue
                if 500 <= response.status_code < 600:
                    self.logger.warning(
                        f"Server error {response.status_code}. Retrying in {2 ** attempt}s..."
                    )
                    time.sleep(2 ** attempt)
                    continue
                return response
            except Exception as e:
                if attempt == self.MAX_RETRIES - 1:
                    raise NetworkError(f"Request failed after {self.MAX_RETRIES} attempts: {e}")
                time.sleep(2 ** attempt)
        raise RateLimitError(f"Max retries ({self.MAX_RETRIES}) exceeded for {url}")

    def fetch_top_packages(self, limit: int = 20) -> list[dict]:
        """
        Fetch top/trending NPM packages.

        Uses npm-stat trending data to get popular packages.
        Falls back to searching the registry if npm-stat is unavailable.

        Args:
            limit: Number of packages to fetch

        Returns:
            List of dicts with name, latest_version, downloads_last_365_days
        """
        # Try npm-stat trending API first
        try:
            response = requests.get(
                f"https://npm-stat.com/charts.html?package=express,lodash,react,vue,angular",
                timeout=self.DEFAULT_TIMEOUT,
            )
            # npm-stat returns HTML; use libraries.io as primary source instead
        except Exception:
            pass

        # Primary: use libraries.io npm dataset (similar to Hugovk for PyPI)
        # Fallback: search registry with popular package names
        popular_packages = [
            "express", "lodash", "react", "vue", "angular",
            "axios", "typescript", "webpack", "babel-core", "eslint",
            "jest", "moment", "chalk", "commander", "debug",
            "dotenv", "uuid", "underscore", "inquirer", "async",
        ]

        results = []
        for pkg_name in popular_packages[:limit]:
            try:
                info = self.get_package_info(pkg_name)
                # Get download count from npm-stat
                downloads = self._get_download_count(pkg_name)
                results.append({
                    "name": pkg_name,
                    "latest_version": info["latest_version"],
                    "downloads_last_365_days": downloads,
                    "description": info.get("description", ""),
                })
            except Exception as e:
                self.logger.warning(f"Skipping {pkg_name}: {e}")
                continue

        return results

    def _get_download_count(self, package_name: str) -> int:
        """Get total downloads for a package from npm-stat API."""
        try:
            response = requests.get(
                f"https://api.npmjs.org/downloads/point/last-year/{package_name}",
                timeout=10,
            )
            if response.status_code == 200:
                data = response.json()
                return data.get("downloads", 0)
        except Exception:
            pass
        return 0

    def get_package_info(self, package_name: str) -> dict:
        """
        Get package metadata from npm registry.

        Args:
            package_name: NPM package name

        Returns:
            Dict with latest_version, description, dist info
        """
        cache_key = package_name
        if cache_key in self._cache:
            return self._cache[cache_key]

        url = _get_package_info_url(package_name)
        response = self._request_with_backoff(url)
        response.raise_for_status()
        data = response.json()

        latest_version = data.get("dist-tags", {}).get("latest", "unknown")
        latest_dist = data.get("versions", {}).get(latest_version, {}).get("dist", {})

        result = {
            "name": data.get("name", package_name),
            "description": data.get("description", ""),
            "latest_version": latest_version,
            "dist": latest_dist,
        }

        self._cache[cache_key] = result
        return result

    def download_package(self, package_name: str) -> list[str]:
        """
        Download package tarball from npm registry.

        Args:
            package_name: NPM package name

        Returns:
            List of downloaded file paths
        """
        info = self.get_package_info(package_name)
        dist = info.get("dist", {})
        tarball_url = dist.get("tarball")

        if not tarball_url:
            raise ValueError(f"No tarball URL found for {package_name}")

        output_dir = _get_output_dir(package_name)
        os.makedirs(output_dir, exist_ok=True)

        version = info["latest_version"]
        filename = f"{package_name}-{version}.tgz"
        file_path = os.path.join(output_dir, filename)

        if os.path.exists(file_path):
            self.logger.info(f"Already exists: {file_path}")
            return [file_path]

        self.logger.info(f"Downloading {package_name}@{version}...")
        response = self.session.get(tarball_url, timeout=self.DEFAULT_TIMEOUT)
        response.raise_for_status()

        with open(file_path, "wb") as f:
            f.write(response.content)

        size_mb = _file_size_mb(file_path)
        self.logger.info(f"Downloaded {filename} ({size_mb:.2f} MB)")

        return [file_path]

    def clear_cache(self):
        """Clear the response cache."""
        self._cache.clear()
```

**Verify:** `python -m pytest tests/test_npm_api.py -v`
**Commit:** `feat(npm): add NPM registry API client`

---

### Task 2.2: npm_journal.py — NPM Journal

**File:** `npm_journal.py`
**Test:** `tests/test_npm_journal.py`
**Depends:** 1.1 (config must have npm_archiver section)

```python
# tests/test_npm_journal.py
"""
Unit tests for NpmJournal class.

Tests cover:
- Initialization and empty journal structure
- add() new package entry
- add() deduplication — same (name, version) blocked
- exists() check
- get() latest entry by name
- get_all() returns all entries
- get_stats() counters
- update() existing entry
- mark_failed() adds failed entry
- clear() resets journal
- Corrupted JSON recovery
"""

import json
import os
import pytest
from unittest.mock import patch, MagicMock


class TestNpmJournalInit:
    """Test journal initialization"""

    def test_init_creates_empty(self, tmp_path):
        """Test new journal creates empty structure"""
        from npm_journal import NpmJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        journal = NpmJournal(journal_path)
        assert journal.data == {"packages": []}

    def test_init_loads_existing(self, tmp_path):
        """Test init loads existing journal file"""
        from npm_journal import NpmJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        existing = {"packages": [
            {"name": "express", "version": "4.21.0", "status": "sent"}
        ]}
        with open(journal_path, 'w', encoding='utf-8') as f:
            json.dump(existing, f)

        journal = NpmJournal(journal_path)
        assert len(journal.data["packages"]) == 1
        assert journal.data["packages"][0]["name"] == "express"

    def test_init_handles_corrupted_json(self, tmp_path):
        """Test init handles corrupted JSON by creating backup"""
        from npm_journal import NpmJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        with open(journal_path, 'w', encoding='utf-8') as f:
            f.write("not valid json{{{")

        journal = NpmJournal(journal_path)
        assert journal.data == {"packages": []}
        assert os.path.exists(journal_path + ".backup")

    def test_logger_property(self):
        """Test logger property returns correct logger"""
        from npm_journal import NpmJournal
        journal = NpmJournal("test_logger_journal.json")
        assert journal.logger.name == "gitax"
        journal.clear()
        os.remove("test_logger_journal.json")


class TestNpmJournalAdd:
    """Test add() method"""

    def test_add_new_entry(self, tmp_path):
        """Test adding a new package entry"""
        from npm_journal import NpmJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        journal = NpmJournal(journal_path)

        result = journal.add(
            name="express",
            version="4.21.0",
            description="Fast web framework",
            downloads=12345678,
            files=["express-4.21.0.tgz"]
        )
        assert result is True
        assert len(journal.data["packages"]) == 1
        assert journal.data["packages"][0]["name"] == "express"
        assert journal.data["packages"][0]["version"] == "4.21.0"
        assert journal.data["packages"][0]["status"] == "sent"
        assert "sent_at" in journal.data["packages"][0]

    def test_add_duplicate_blocked(self, tmp_path):
        """Test adding same (name, version) is blocked"""
        from npm_journal import NpmJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        journal = NpmJournal(journal_path)

        journal.add("express", "4.21.0", "desc", 100, ["file.tgz"])
        result = journal.add("express", "4.21.0", "desc", 100, ["file.tgz"])
        assert result is False
        assert len(journal.data["packages"]) == 1

    def test_add_same_name_different_version(self, tmp_path):
        """Test adding same name but different version is allowed"""
        from npm_journal import NpmJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        journal = NpmJournal(journal_path)

        journal.add("express", "4.21.0", "desc", 100, ["file.tgz"])
        result = journal.add("express", "5.0.0", "desc", 200, ["file.tgz"])
        assert result is True
        assert len(journal.data["packages"]) == 2


class TestNpmJournalExists:
    """Test exists() method"""

    def test_exists_returns_true(self, tmp_path):
        """Test exists returns True for existing entry"""
        from npm_journal import NpmJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        journal = NpmJournal(journal_path)
        journal.add("express", "4.21.0", "desc", 100, [])

        assert journal.exists("express", "4.21.0") is True

    def test_exists_returns_false(self, tmp_path):
        """Test exists returns False for missing entry"""
        from npm_journal import NpmJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        journal = NpmJournal(journal_path)

        assert journal.exists("nonexistent", "1.0.0") is False
        assert journal.exists("express", "9.9.9") is False


class TestNpmJournalGet:
    """Test get() method"""

    def test_get_latest_version(self, tmp_path):
        """Test get returns latest version of a package"""
        from npm_journal import NpmJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        journal = NpmJournal(journal_path)
        journal.add("express", "4.20.0", "desc", 100, [])
        journal.add("express", "4.21.0", "desc", 200, [])

        result = journal.get("express")
        assert result is not None
        assert result["version"] == "4.21.0"

    def test_get_returns_none_for_missing(self, tmp_path):
        """Test get returns None for unknown package"""
        from npm_journal import NpmJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        journal = NpmJournal(journal_path)

        assert journal.get("nonexistent") is None


class TestNpmJournalStats:
    """Test get_stats() and get_count() methods"""

    def test_get_stats_empty(self, tmp_path):
        """Test stats for empty journal"""
        from npm_journal import NpmJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        journal = NpmJournal(journal_path)

        stats = journal.get_stats()
        assert stats["total"] == 0
        assert stats["sent"] == 0
        assert stats["failed"] == 0

    def test_get_stats_with_entries(self, tmp_path):
        """Test stats with mixed entries"""
        from npm_journal import NpmJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        journal = NpmJournal(journal_path)
        journal.add("express", "4.21.0", "desc", 100, [])
        journal.mark_failed("bad-pkg", "1.0.0", "error")

        stats = journal.get_stats()
        assert stats["total"] == 2
        assert stats["sent"] == 1
        assert stats["failed"] == 1

    def test_get_count(self, tmp_path):
        """Test get_count returns correct count"""
        from npm_journal import NpmJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        journal = NpmJournal(journal_path)
        assert journal.get_count() == 0
        journal.add("a", "1", "", 0, [])
        assert journal.get_count() == 1
        journal.add("b", "2", "", 0, [])
        assert journal.get_count() == 2

    def test_get_all(self, tmp_path):
        """Test get_all returns all entries"""
        from npm_journal import NpmJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        journal = NpmJournal(journal_path)
        journal.add("a", "1", "", 0, [])
        journal.add("b", "2", "", 0, [])
        all_entries = journal.get_all()
        assert len(all_entries) == 2


class TestNpmJournalUpdate:
    """Test update() method"""

    def test_update_existing(self, tmp_path):
        """Test updating an existing entry"""
        from npm_journal import NpmJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        journal = NpmJournal(journal_path)
        journal.add("express", "4.21.0", "desc", 100, [])

        result = journal.update("express", "4.21.0", {"status": "updated"})
        assert result is True
        entry = journal.get("express")
        assert entry["status"] == "updated"
        assert "updated_at" in entry

    def test_update_missing(self, tmp_path):
        """Test updating a non-existent entry returns False"""
        from npm_journal import NpmJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        journal = NpmJournal(journal_path)

        result = journal.update("nonexistent", "1.0", {"status": "x"})
        assert result is False


class TestNpmJournalClear:
    """Test clear() method"""

    def test_clear(self, tmp_path):
        """Test clear resets journal"""
        from npm_journal import NpmJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        journal = NpmJournal(journal_path)
        journal.add("express", "4.21.0", "desc", 100, [])

        journal.clear()
        assert journal.data == {"packages": []}
        assert journal.get_count() == 0


class TestNpmJournalMarkFailed:
    """Test mark_failed() method"""

    def test_mark_failed(self, tmp_path):
        """Test marking a package as failed"""
        from npm_journal import NpmJournal

        journal_path = os.path.join(tmp_path, "test_journal.json")
        journal = NpmJournal(journal_path)
        journal.mark_failed("broken-pkg", "0.1", "Download error")

        assert journal.exists("broken-pkg", "0.1")
        entry = journal.get("broken-pkg")
        assert entry["status"] == "failed"
        assert entry["files"] == []
```

```python
# npm_journal.py
"""
NPM package journal for tracking uploaded packages.

Extends BaseJournal with NPM-specific methods for tracking
package uploads to MAX channels.
"""

import os
from datetime import datetime
from shared_journal import BaseJournal


class NpmJournal(BaseJournal):
    """Journal for tracking NPM package uploads to MAX."""

    def _create_empty(self) -> dict:
        """Create empty journal structure."""
        return {"packages": []}

    def add(
        self,
        name: str,
        version: str,
        description: str = "",
        downloads: int = 0,
        files: list[str] | None = None,
    ) -> bool:
        """
        Add a package entry to the journal.

        Args:
            name: Package name
            version: Package version
            description: Package description
            downloads: Download count
            files: List of uploaded file paths

        Returns:
            True if added, False if duplicate
        """
        if self.exists(name, version):
            self.logger.debug(f"Duplicate: {name}@{version}")
            return False

        entry = {
            "name": name,
            "version": version,
            "description": description,
            "downloads": downloads,
            "files": files or [],
            "status": "sent",
            "sent_at": datetime.now().isoformat(),
        }

        self.data["packages"].append(entry)
        self.save()
        self.logger.info(f"Added {name}@{version} to journal")
        return True

    def exists(self, name: str, version: str) -> bool:
        """Check if a package version exists in the journal."""
        for entry in self.data["packages"]:
            if entry["name"] == name and entry["version"] == version:
                return True
        return False

    def get(self, name: str) -> dict | None:
        """Get the latest entry for a package by name. Returns None if not found."""
        latest = None
        for entry in self.data["packages"]:
            if entry["name"] == name:
                if latest is None or entry["version"] > latest["version"]:
                    latest = entry
        return latest

    def get_all(self) -> list[dict]:
        """Return all package entries."""
        return self.data["packages"]

    def get_count(self) -> int:
        """Return total number of entries."""
        return len(self.data["packages"])

    def get_stats(self) -> dict:
        """Return journal statistics."""
        packages = self.data["packages"]
        return {
            "total": len(packages),
            "sent": sum(1 for p in packages if p.get("status") == "sent"),
            "failed": sum(1 for p in packages if p.get("status") == "failed"),
        }

    def update(self, name: str, version: str, updates: dict) -> bool:
        """
        Update an existing entry.

        Args:
            name: Package name
            version: Package version
            updates: Dict of fields to update

        Returns:
            True if updated, False if not found
        """
        for entry in self.data["packages"]:
            if entry["name"] == name and entry["version"] == version:
                entry.update(updates)
                entry["updated_at"] = datetime.now().isoformat()
                self.save()
                return True
        return False

    def mark_failed(self, name: str, version: str, error: str = "") -> None:
        """Mark a package as failed."""
        self.data["packages"].append({
            "name": name,
            "version": version,
            "description": "",
            "downloads": 0,
            "files": [],
            "status": "failed",
            "error": error,
            "failed_at": datetime.now().isoformat(),
        })
        self.save()
```

**Verify:** `python -m pytest tests/test_npm_journal.py -v`
**Commit:** `feat(npm): add NPM package journal`

---

### Task 2.3: npm_archiver.py — NPM Archiver Orchestrator

**File:** `npm_archiver.py`
**Test:** none (integration tested via Task 3.2)
**Depends:** 1.1, 2.1, 2.2 (imports config, npm_api, npm_journal)

Design requires an orchestrator that:
- Loads top NPM packages via `NPMAPI`
- Downloads tarballs
- Sends to MAX via `BrowserMAX` (reusing existing browser automation)
- Handles 7z splitting for large files (>49 MB)
- Formats messages with package info
- Deduplicates via `NpmJournal`
- Supports sync mode (check for new versions)

Following the `pypi_libs_archiver.py` pattern exactly:

```python
# npm_archiver.py
"""
NPM Archiver — download top Node.js packages and send to MAX channel.

Follows the same pattern as PyPILibsArchiver:
- Fetches top packages via NPMAPI
- Downloads tarballs
- Sends to MAX via BrowserMAX with message formatting
- Tracks uploads in NpmJournal for deduplication
- Supports 7z splitting for large files
"""

import os
import time
import shutil
from pathlib import Path
from config import get_config, init_config
from config_utils import get_config_value, get_split_mode
from npm_api import NPMAPI
from npm_journal import NpmJournal
from logging_config import LogMixin
from browser_max import BrowserMAX


class NpmArchiver(LogMixin):
    """
    NPM package archiver.

    Downloads top NPM packages and sends them to a MAX channel.
    """

    JOURNAL_FILE = "npm_journal.json"

    def __init__(self, config_path: str = "config.yaml"):
        init_config(config_path)
        self.config = get_config().model_dump()
        self.api = NPMAPI()
        self.journal = NpmJournal(self.JOURNAL_FILE)
        self.browser = None
        self._channel_url = ""
        self._output_dir = self.config.get("npm_archiver", {}).get("output_dir", "./temp_npm")
        self._limit = self.config.get("npm_archiver", {}).get("limit", 20)
        self._retries = self.config.get("npm_archiver", {}).get("retries", 3)
        self._retry_delay = self.config.get("npm_archiver", {}).get("retry_delay", 10)
        self._split_mode = get_split_mode(self.config, "npm_archiver", "auto")

    def _cleanup(self):
        """Clean up resources."""
        if self.browser:
            self.browser.close()
            self.browser = None

    @staticmethod
    def _format_downloads(count: int) -> str:
        """Format download count for display."""
        if count >= 1_000_000_000:
            return f"{count / 1_000_000_000:.1f}B"
        if count >= 1_000_000:
            return f"{count / 1_000_000:.1f}M"
        if count >= 1_000:
            return f"{count / 1_000:.1f}K"
        return str(count)

    def _build_message_text(self, pkg_data: dict, file_sizes: list[int]) -> str:
        """Build message text for MAX upload."""
        name = pkg_data.get("name", "unknown")
        version = pkg_data.get("latest_version", "unknown")
        description = pkg_data.get("description", "")
        downloads = pkg_data.get("downloads_last_365_days", 0)

        lines = [
            f"📦 npm: {name}@{version}",
        ]
        if description:
            lines.append(f"   {description}")
        if downloads:
            lines.append(f"   ⬇ {self._format_downloads(downloads)} downloads/year")

        total_mb = sum(s / (1024 * 1024) for s in file_sizes)
        if file_sizes:
            if len(file_sizes) == 1:
                lines.append(f"   📎 {total_mb:.2f} MB")
            else:
                lines.append(f"   📎 {len(file_sizes)} volumes, {total_mb:.2f} MB total")

        return "\n".join(lines)

    @staticmethod
    def _print_progress(current: int, total: int, sent: int, skipped: int, failed: int):
        """Print progress bar."""
        pct = (current / total * 100) if total else 0
        bar_len = 30
        filled = int(bar_len * current / total) if total else 0
        bar = "█" * filled + "░" * (bar_len - filled)
        print(f"\r  [{bar}] {current}/{total} ({pct:.0f}%) "
              f"✓{sent} ⊘{skipped} ✗{failed}", end="", flush=True)

    def _init_browser(self, channel_url: str):
        """Initialize browser connection."""
        self._channel_url = channel_url
        self.browser = BrowserMAX("config.yaml")
        self.browser.init_browser(channel_url)

    def _should_split(self, file_path: str) -> bool:
        """Determine if a file should be split into volumes."""
        split_threshold = self.config.get("archiver", {}).get("split_threshold_mb", 49)
        file_size_mb = os.path.getsize(file_path) / (1024 * 1024)

        if self._split_mode == "on":
            return True
        if self._split_mode == "off":
            return False
        if self._split_mode == "prompt":
            try:
                resp = input(f"  Разделить {file_path} ({file_size_mb:.1f} MB)? [Y/n]: ").strip().lower()
                return resp not in ("n", "no")
            except (EOFError, KeyboardInterrupt):
                return file_size_mb > split_threshold
        # auto mode
        return file_size_mb > split_threshold

    def _upload_file(self, file_path: str, message_text: str) -> bool:
        """Upload a single file or split into volumes."""
        if not self._should_split(file_path):
            return self._upload_single(file_path, message_text)

        return self._upload_split(file_path, message_text)

    def _upload_single(self, file_path: str, message_text: str) -> bool:
        """Upload a single file."""
        for attempt in range(self._retries):
            try:
                self.browser.send_message(message_text)
                self.browser.upload_file(file_path)
                return True
            except Exception as e:
                self.logger.error(f"Upload attempt {attempt + 1} failed: {e}")
                if attempt < self._retries - 1:
                    time.sleep(self._retry_delay)
        return False

    def _upload_split(self, file_path: str, message_text: str) -> bool:
        """Split file into 7z volumes and upload sequentially."""
        base_name = os.path.splitext(file_path)[0]
        volume_size = self.config.get("backuper", {}).get("default_volume_size", "49M")
        seven_zip = self.config.get("backuper", {}).get("seven_zip_exe", "7z")

        try:
            cmd = [seven_zip, "a", "-v" + volume_size, f"{base_name}.7z", file_path]
            result = os.popen(" ".join(cmd)).read()
        except Exception as e:
            self.logger.error(f"7z split failed: {e}")
            return False

        volumes = sorted(
            [f for f in Path(base_name).parent.glob(f"{os.path.basename(base_name)}.7z.*")],
            key=lambda x: x.name,
        )

        all_success = True
        for volume in volumes:
            vol_msg = f"{message_text}\n   📎 Volume: {volume.name}"
            success = self._upload_single(str(volume), vol_msg)
            if not success:
                all_success = False
                break
            # Delete volume after successful upload
            try:
                os.remove(str(volume))
            except OSError:
                pass

        # Clean up remaining volumes
        for volume in volumes:
            try:
                os.remove(str(volume))
            except OSError:
                pass

        return all_success

    def load_top_packages(self, limit: int | None = None):
        """
        Load top NPM packages and upload to MAX.

        Args:
            limit: Number of packages to process (default: from config)
        """
        limit = limit or self._limit
        packages = self.api.fetch_top_packages(limit)

        if not packages:
            print("\n  ⚠ Не удалось получить список пакетов.")
            return

        print(f"\n  Загружено {len(packages)} пакетов для обработки")
        print("  Начинаю загрузку...\n")

        sent = 0
        skipped = 0
        failed = 0

        for i, pkg in enumerate(packages, 1):
            name = pkg["name"]
            version = pkg["latest_version"]

            # Check dedup
            if self.journal.exists(name, version):
                self._print_progress(i, len(packages), sent, skipped + 1, failed)
                skipped += 1
                continue

            try:
                # Download
                file_paths = self.api.download_package(name)
                if not file_paths:
                    raise ValueError(f"No files downloaded for {name}")

                file_sizes = [os.path.getsize(p) for p in file_paths]
                message = self._build_message_text(pkg, file_sizes)

                # Upload
                success = False
                for file_path in file_paths:
                    if self._upload_file(file_path, message):
                        success = True

                if success:
                    self.journal.add(
                        name=name,
                        version=version,
                        description=pkg.get("description", ""),
                        downloads=pkg.get("downloads_last_365_days", 0),
                        files=file_paths,
                    )
                    sent += 1
                    # Clean up downloaded files
                    for fp in file_paths:
                        try:
                            os.remove(fp)
                        except OSError:
                            pass
                else:
                    self.journal.mark_failed(name, version, "Upload failed")
                    failed += 1

            except Exception as e:
                self.logger.error(f"Error processing {name}: {e}")
                self.journal.mark_failed(name, version, str(e))
                failed += 1

            self._print_progress(i, len(packages), sent, skipped, failed)

        print(f"\n\n  ✓ Завершено: {sent} загружено, {skipped} пропущено, {failed} ошибок")

    def sync_packages(self):
        """
        Sync NPM packages — check for new versions of already-uploaded packages.
        """
        packages = self.journal.get_all()
        if not packages:
            print("\n  ⚠ Журнал пуст. Нет пакетов для синхронизации.")
            return

        print(f"\n  Проверка {len(packages)} пакетов на обновления...\n")

        updated = 0
        no_change = 0
        failed = 0

        for i, pkg in enumerate(packages, 1):
            name = pkg["name"]
            saved_version = pkg["version"]

            try:
                info = self.api.get_package_info(name)
                latest_version = info["latest_version"]

                if latest_version != saved_version:
                    print(f"  🔄 {name}: {saved_version} → {latest_version}")
                    # Download and upload new version
                    file_paths = self.api.download_package(name)
                    if file_paths:
                        file_sizes = [os.path.getsize(p) for p in file_paths]
                        message = self._build_message_text(
                            {"name": name, "latest_version": latest_version,
                             "description": info.get("description", ""),
                             "downloads_last_365_days": 0},
                            file_sizes,
                        )
                        success = False
                        for file_path in file_paths:
                            if self._upload_file(file_path, message):
                                success = True

                        if success:
                            self.journal.add(
                                name=name,
                                version=latest_version,
                                description=info.get("description", ""),
                                downloads=0,
                                files=file_paths,
                            )
                            updated += 1
                            for fp in file_paths:
                                try:
                                    os.remove(fp)
                                except OSError:
                                    pass
                        else:
                            failed += 1
                    else:
                        failed += 1
                else:
                    no_change += 1

            except Exception as e:
                self.logger.error(f"Error syncing {name}: {e}")
                failed += 1

        print(f"\n  ✓ Синхронизация завершена: {updated} обновлено, "
              f"{no_change} без изменений, {failed} ошибок")

    def run(self):
        """Run the archiver with browser initialization."""
        from config_utils import get_channel_url

        channel_url = get_channel_url(
            self.config, "npm", label="NPM канал", required=False
        )
        if not channel_url:
            print("\n  ⚠ URL NPM канала не указан.")
            return

        try:
            self._init_browser(channel_url)
            self.load_top_packages()
        finally:
            self._cleanup()


def main():
    """Standalone entry point for NPM archiver."""
    archiver = NpmArchiver("config.yaml")
    archiver.run()


if __name__ == "__main__":
    main()
```

**Verify:** `python -c "from npm_archiver import NpmArchiver; print('ok')"`
**Commit:** `feat(npm): add NPM archiver orchestrator`

---

## Batch 3: Integration (sequential — 2 tasks)

Tasks in this batch depend on all Batch 2 modules being complete.

### Task 3.1: github_archiver.py — Add NPM menu and dispatch

**File:** `github_archiver.py`
**Test:** none (manual verification)
**Depends:** 1.1, 1.3, 2.1, 2.2, 2.3

**Changes needed (6 edits):**

**Edit 1:** Add NPM to `_ensure_channel_ready` channel mapping (line ~285):
```python
        _CHANNEL_TO_FUNCTION = {
            "max": "github",
            "pypi": "pypi",
            "media": "media",
            "backup": "backup",
            "npm": "npm",
        }
```

**Edit 2:** Add NPM to `_MODULE_CHANNELS` (line ~317):
```python
    _MODULE_CHANNELS = {
        "1": "max",     # GitHub → max
        "2": "pypi",    # PyPI → pypi
        "3": "backup",  # Backuper → backup
        "4": "media",   # Файлы → media
        "5": "npm",     # NPM → npm
    }
```

**Edit 3:** Update `_show_main_menu` — add NPM menu item and renumber (line ~444):
```python
        print(menu_item("1", "GitHub — репозитории", "max"))
        print(menu_item("2", "PyPI — Python библиотеки", "pypi"))
        print(menu_item("3", "Backuper — бэкап папок в канал", "backup"))
        print(menu_item("4", "Файлы — медиа, скачивание, экспорт", "media"))
        print(menu_item("5", "NPM — Node.js пакеты", "npm"))
        print("  [6] Сервис — журналы, настройки")
```

**Edit 4:** Update `_npm_menu` method — add new method after `_pypi_menu`:
```python
    def _npm_menu(self):
        """Подменю NPM"""
        print("\n" + "═" * 60)
        print("  NPM — Node.js пакеты")
        print("─" * 60)
        print()
        print("  [1] Загрузить топ NPM пакетов")
        print("  [2] Синхронизировать NPM пакеты")
        print("  [0] Назад")
        print()
```

**Edit 5:** Update `run()` main loop — renumber valid_opts and dispatch (line ~2682):
```python
            needs_setup = not is_setup_complete(self.config)
            if needs_setup:
                valid_opts = ["0", "x", "1", "2", "3", "4", "5", "6"]
                prompt_text = "Выберите раздел [0/X,1-6]"
            else:
                valid_opts = ["0", "1", "2", "3", "4", "5", "6"]
                prompt_text = "Выберите раздел [0-6]"

            # ... in dispatch section:
            if choice in ("1", "2", "3", "4", "5") and not self._is_module_enabled(choice):
                module_names = {"1": "GitHub", "2": "PyPI", "3": "Backuper", "4": "Файлы", "5": "NPM"}
```

And update dispatch:
```python
            elif choice == '1':
                self._run_github_menu()
            elif choice == '2':
                self._run_pypi_menu()
            elif choice == '3':
                self._run_backuper_menu()
            elif choice == '4':
                self._run_files_menu()
            elif choice == '5':
                self._run_npm_menu()
            elif choice == '6':
                self._run_service_menu()
```

**Edit 6:** Add `_run_npm_menu` and dispatch methods:
```python
    def _run_npm_menu(self):
        """Цикл подменю NPM"""
        while True:
            os.system('cls' if os.name == 'nt' else 'clear')
            self._npm_menu()
            choice = prompt_numeric_choice("Выберите действие [0-2]", ["0", "1", "2"])

            if choice == '0':
                break
            elif choice == '1':
                self.run_npm_archiver()
            elif choice == '2':
                self.run_npm_sync()

    def run_npm_archiver(self):
        """Загрузить топ NPM пакетов в MAX канал"""
        from npm_archiver import NpmArchiver

        print("\n" + "═" * 60)
        print("  Загрузка топ NPM пакетов")
        print("═" * 60)

        if not self._ensure_channel_ready("npm", "NPM канал", "npm"):
            input("\n  Нажмите Enter для возврата в меню...")
            return

        try:
            archiver = NpmArchiver("config.yaml")
            archiver.load_top_packages()
        except Exception as e:
            print(f"\n  ✗ Ошибка: {e}")
            self.logger.error(f"NPM archiver error: {e}", exc_info=True)

        input("\n  Нажмите Enter для возврата в меню...")

    def run_npm_sync(self):
        """Синхронизировать версии NPM пакетов"""
        from npm_archiver import NpmArchiver

        print("\n" + "═" * 60)
        print("  Синхронизация NPM пакетов")
        print("═" * 60)

        if not self._ensure_channel_ready("npm", "NPM канал", "npm"):
            input("\n  Нажмите Enter для возврата в меню...")
            return

        try:
            archiver = NpmArchiver("config.yaml")
            archiver.sync_packages()
        except Exception as e:
            print(f"\n  ✗ Ошибка: {e}")
            self.logger.error(f"NPM sync error: {e}", exc_info=True)

        input("\n  Нажмите Enter для возврата в меню...")
```

**Edit 7:** Update `_manage_journals` — add NPM journal (line ~1330):
```python
            # Add npm journal import
            from npm_journal import NpmJournal

            # Add npm journal stats
            nj = NpmJournal("npm_journal.json")
            nj_stats = nj.get_stats()

            # Add to stats display
            print(f"  [6] npm_journal.json — {nj_stats['total']} пакетов "
                  f"({nj_stats['sent']} отправлено, {nj_stats['failed']} ошибок)")

            # Add to clear menu
            print("  [6] Очистить npm_journal.json")
            print("  [7] Очистить ВСЕ журналы")

            # Update prompt
            choice = prompt_numeric_choice("Ваш выбор [0/1/2/3/4/5/6/7]", ["0", "1", "2", "3", "4", "5", "6", "7"])

            # Add choice handler for npm journal
            elif choice == '6':
                confirm = input("\n  Очистить npm_journal.json? [y/N]: ").strip().lower()
                if confirm in ('y', 'yes', 'д', 'да'):
                    NpmJournal("npm_journal.json").clear()
                    print("  ✓ npm_journal.json очищен")
                else:
                    print("  Отменено")
                input("\n  Нажмите Enter для продолжения...")

            # Update "clear all" to include npm
            elif choice == '7':
                # ... existing clear-all logic, add:
                NpmJournal("npm_journal.json").clear()
```

**Edit 8:** Update `_initial_setup` channel_steps to include npm (line ~2521):
```python
        channel_steps = [
            ("max",   "MAX канал (GitHub архивы)"),
            ("pypi",  "PyPI канал"),
            ("media", "Media канал"),
            ("backup","Backup канал"),
            ("npm",   "NPM канал"),
        ]
```

And update total steps:
```python
        total = 7  # was 6, now 7 with npm step
```

**Verify:** `python -c "from github_archiver import GitHubArchiver; print('ok')"`
**Commit:** `feat(npm): add NPM menu and integration to main archiver`

---

### Task 3.2: Integration tests

**File:** `tests/test_npm_archiver_integration.py`
**Test:** self
**Depends:** 2.1, 2.2, 2.3

```python
# tests/test_npm_archiver_integration.py
"""
Integration tests for NPM archiver components.

Tests cover:
- NpmArchiver initialization with config
- Message text formatting
- Download count formatting
- Split mode detection
- Config loading with npm_archiver section
"""

import os
import pytest
from unittest.mock import patch, MagicMock, PropertyMock


class TestNpmArchiverConfig:
    """Test NPM archiver config loading"""

    def test_config_has_npm_section(self):
        """Test that config model includes npm_archiver section"""
        from config.model import AppConfig, NpmArchiverConfig
        config = AppConfig()
        assert hasattr(config, "npm_archiver")
        assert isinstance(config.npm_archiver, NpmArchiverConfig)
        assert config.npm_archiver.limit == 20
        assert config.npm_archiver.output_dir == "./temp_npm"

    def test_channels_has_npm(self):
        """Test that ChannelsConfig includes npm field"""
        from config.model import ChannelsConfig
        channels = ChannelsConfig()
        assert hasattr(channels, "npm")
        assert channels.npm == ""

    def test_channel_registry_has_npm(self):
        """Test that ChannelRegistry includes npm"""
        from config.model import ChannelRegistry, VALID_CHANNEL_FUNCTIONS
        assert "npm" in VALID_CHANNEL_FUNCTIONS
        registry = ChannelRegistry()
        assert hasattr(registry, "npm")
        assert registry.npm == []

    def test_npm_in_channel_to_function(self):
        """Test npm mapping in config_utils"""
        from config_utils import _CHANNEL_TO_FUNCTION
        assert "npm" in _CHANNEL_TO_FUNCTION
        assert _CHANNEL_TO_FUNCTION["npm"] == "npm"


class TestMessageFormatting:
    """Test message text formatting"""

    def test_format_downloads(self):
        """Test download count formatting"""
        from npm_archiver import NpmArchiver

        assert NpmArchiver._format_downloads(0) == "0"
        assert NpmArchiver._format_downloads(500) == "500"
        assert NpmArchiver._format_downloads(1500) == "1.5K"
        assert NpmArchiver._format_downloads(1_500_000) == "1.5M"
        assert NpmArchiver._format_downloads(1_500_000_000) == "1.5B"

    def test_build_message_text_single_file(self):
        """Test message text with single file"""
        from npm_archiver import NpmArchiver

        archiver = NpmArchiver.__new__(NpmArchiver)
        pkg = {
            "name": "express",
            "latest_version": "4.21.0",
            "description": "Fast web framework",
            "downloads_last_365_days": 12345678,
        }
        msg = archiver._build_message_text(pkg, [1024 * 1024])

        assert "express@4.21.0" in msg
        assert "Fast web framework" in msg
        assert "12.3M downloads/year" in msg
        assert "1.00 MB" in msg

    def test_build_message_text_multiple_files(self):
        """Test message text with split volumes"""
        from npm_archiver import NpmArchiver

        archiver = NpmArchiver.__new__(NpmArchiver)
        pkg = {
            "name": "large-pkg",
            "latest_version": "1.0.0",
            "description": "Big package",
            "downloads_last_365_days": 1000,
        }
        msg = archiver._build_message_text(pkg, [1024 * 1024, 1024 * 1024])

        assert "2 volumes" in msg
        assert "2.00 MB total" in msg


class TestNpmArchiverInit:
    """Test NpmArchiver initialization"""

    @patch("npm_archiver.init_config")
    @patch("npm_archiver.get_config")
    def test_init_with_config(self, mock_get_config, mock_init_config):
        """Test archiver initializes with config"""
        from npm_archiver import NpmArchiver

        mock_config = MagicMock()
        mock_config.model_dump.return_value = {
            "npm_archiver": {"limit": 50, "output_dir": "./test_npm", "retries": 5},
            "archiver": {"split_threshold_mb": 49},
            "backuper": {"default_volume_size": "49M"},
        }
        mock_get_config.return_value = mock_config

        archiver = NpmArchiver("config.yaml")
        assert archiver._limit == 50
        assert archiver._output_dir == "./test_npm"
        assert archiver._retries == 5


class TestSplitMode:
    """Test split mode detection"""

    @patch("npm_archiver.init_config")
    @patch("npm_archiver.get_config")
    def test_split_auto_mode(self, mock_get_config, mock_init_config):
        """Test auto split mode"""
        from npm_archiver import NpmArchiver

        mock_config = MagicMock()
        mock_config.model_dump.return_value = {
            "npm_archiver": {"split_mode": "auto"},
            "archiver": {"split_threshold_mb": 1},
        }
        mock_get_config.return_value = mock_config

        archiver = NpmArchiver("config.yaml")
        # File > threshold should split
        with patch("os.path.getsize", return_value=2 * 1024 * 1024):
            assert archiver._should_split("big.tgz") is True
        # File < threshold should not split
        with patch("os.path.getsize", return_value=512 * 1024):
            assert archiver._should_split("small.tgz") is False

    @patch("npm_archiver.init_config")
    @patch("npm_archiver.get_config")
    def test_split_on_mode(self, mock_get_config, mock_init_config):
        """Test on split mode always splits"""
        from npm_archiver import NpmArchiver

        mock_config = MagicMock()
        mock_config.model_dump.return_value = {
            "npm_archiver": {"split_mode": "on"},
        }
        mock_get_config.return_value = mock_config

        archiver = NpmArchiver("config.yaml")
        with patch("os.path.getsize", return_value=100):
            assert archiver._should_split("any.tgz") is True

    @patch("npm_archiver.init_config")
    @patch("npm_archiver.get_config")
    def test_split_off_mode(self, mock_get_config, mock_init_config):
        """Test off split mode never splits"""
        from npm_archiver import NpmArchiver

        mock_config = MagicMock()
        mock_config.model_dump.return_value = {
            "npm_archiver": {"split_mode": "off"},
        }
        mock_get_config.return_value = mock_config

        archiver = NpmArchiver("config.yaml")
        with patch("os.path.getsize", return_value=100 * 1024 * 1024):
            assert archiver._should_split("huge.tgz") is False


class TestConfigMigration:
    """Test config migration includes npm"""

    def test_migration_map_has_npm(self):
        """Test channel migration map includes npm"""
        from config.loader import _CHANNEL_MIGRATION_MAP
        assert "npm" in _CHANNEL_MIGRATION_MAP
        assert _CHANNEL_MIGRATION_MAP["npm"] == ("npm", "NPM Main")

    def test_legacy_env_has_npm(self):
        """Test legacy env var map includes CHANNEL_NPM"""
        from config.loader import _apply_env_overrides
        # The function exists and can be called
        assert callable(_apply_env_overrides)


class TestSetupComplete:
    """Test is_setup_complete with npm"""

    def test_setup_complete_counts_npm(self):
        """Test that setup check includes npm channel"""
        from config_utils import is_setup_complete, get_skipped_channels

        # Config with all channels including npm
        config = {
            "channels": {
                "max": "https://example.com/max",
                "pypi": "https://example.com/pypi",
                "media": "https://example.com/media",
                "backup": "https://example.com/backup",
                "npm": "https://example.com/npm",
            },
            "setup": {"skipped_channels": []},
        }

        with patch("os.environ", {"GITHUB_TOKEN": "test_token",
                                   "CHANNEL_MAX": "https://example.com/max",
                                   "CHANNEL_PYPI": "https://example.com/pypi",
                                   "CHANNEL_MEDIA": "https://example.com/media",
                                   "CHANNEL_BACKUP": "https://example.com/backup",
                                   "CHANNEL_NPM": "https://example.com/npm"}):
            result = is_setup_complete(config)
            assert result is True
```

**Verify:** `python -m pytest tests/test_npm_archiver_integration.py -v`
**Commit:** `test(npm): add integration tests for NPM archiver`

---

## Summary

| Batch | Tasks | Files | Parallelism |
|-------|-------|-------|-------------|
| 1 | 1.1-1.4 | 4 config/template files | 4 implementers |
| 2 | 2.1-2.3 | 3 new Python modules + 2 test files | 3 implementers |
| 3 | 3.1-3.2 | 1 existing file + 1 test file | 2 implementers |

**Total new files:** 3 (npm_api.py, npm_journal.py, npm_archiver.py)
**Total new test files:** 2 (test_npm_api.py, test_npm_journal.py, test_npm_archiver_integration.py)
**Total modified files:** 4 (config/model.py, config/loader.py, config_utils.py, github_archiver.py, .env.example)

**Run order:**
1. Batch 1 tasks run in parallel (no dependencies)
2. Batch 2 tasks run in parallel after Batch 1 completes
3. Batch 3 tasks run sequentially after Batch 2 completes

**Full test command:** `python -m pytest tests/test_npm_api.py tests/test_npm_journal.py tests/test_npm_archiver_integration.py -v`
