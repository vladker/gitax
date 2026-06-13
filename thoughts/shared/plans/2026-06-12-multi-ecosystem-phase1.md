# Multi-Ecosystem Phase 1 — Implementation Plan

**Date:** 2026-06-12
**Design:** `thoughts/shared/designs/2026-06-12-multi-ecosystem-phase1-design.md`
**Scope:** Three new archivers — Cargo (Rust), NuGet (.NET), RubyGems (Ruby)

---

## Architecture Overview

Each archiver follows the **PyPI pattern** (3 files):
| Layer | Cargo | NuGet | RubyGems |
|-------|-------|-------|----------|
| API | `cargo_api.py` | `nuget_api.py` | `rubygems_api.py` |
| Archiver | `cargo_archiver.py` | `nuget_archiver.py` | `rubygems_archiver.py` |
| Journal | `cargo_journal.py` | `nuget_journal.py` | `rubygems_journal.py` |

Shared infrastructure (no changes needed):
- `browser_max.py` — MAX upload automation
- `browser_init.py` — `BrowserInitMixin`
- `shared_journal.py` — `BaseJournal`
- `signal_handler.py` — graceful shutdown
- `config_utils.py` — channel URL resolution, split mode

---

## Dependency Graph

```
Batch 1 (Foundation — config + channel wiring)
  └── 1.1 config/model.py          ← must exist before archivers import it
  └── 1.2 config/schema.yaml       ← mirrors model defaults
  └── 1.3 config.yaml              ← user config with new sections
  └── 1.4 .env.example             ← new channel env vars
  └── 1.5 config_utils.py          ← channel registry updates

Batch 2 (Three archivers — fully parallel)
  └── 2.1 cargo_api.py             ← independent
  └── 2.2 cargo_journal.py         ← independent
  └── 2.3 cargo_archiver.py        ← depends on 2.1, 2.2
  └── 2.4 nuget_api.py             ← independent
  └── 2.5 nuget_journal.py         ← independent
  └── 2.6 nuget_archiver.py        ← depends on 2.4, 2.5
  └── 2.7 rubygems_api.py          ← independent
  └── 2.8 rubygems_journal.py      ← independent
  └── 2.9 rubygems_archiver.py     ← depends on 2.7, 2.8

Batch 3 (Menu integration)
  └── 3.1 github_archiver.py       ← menu wiring + runner methods

Batch 4 (Tests — fully parallel)
  └── 4.1 tests/test_cargo_api.py
  └── 4.2 tests/test_cargo_journal.py
  └── 4.3 tests/test_cargo_archiver.py
  └── 4.4 tests/test_nuget_api.py
  └── 4.5 tests/test_nuget_journal.py
  └── 4.6 tests/test_nuget_archiver.py
  └── 4.7 tests/test_rubygems_api.py
  └── 4.8 tests/test_rubygems_journal.py
  └── 4.9 tests/test_rubygems_archiver.py
```

---

## Batch 1: Foundation (Config + Channel Wiring)

### Task 1.1: `config/model.py` — Add Pydantic models for 3 archivers

**File:** `config/model.py`
**Changes:** Add 3 new model classes + wire into `AppConfig`

```python
# Add after PyPILibsArchiverConfig:

class CargoArchiverConfig(BaseModel):
    """Configuration for Cargo (crates.io) archiver."""
    limit: int = 100
    output_dir: str = "./temp_cargo"
    retries: int = 3
    retry_delay: int = 10
    split_mode: str = "auto"


class NuGetArchiverConfig(BaseModel):
    """Configuration for NuGet (.NET) archiver."""
    limit: int = 100
    output_dir: str = "./temp_nuget"
    retries: int = 3
    retry_delay: int = 10
    split_mode: str = "auto"


class RubyGemsArchiverConfig(BaseModel):
    """Configuration for RubyGems archiver."""
    limit: int = 100
    output_dir: str = "./temp_rubygems"
    retries: int = 3
    retry_delay: int = 10
    split_mode: str = "auto"
```

In `AppConfig`, add:
```python
    cargo_archiver: CargoArchiverConfig = Field(default_factory=CargoArchiverConfig)
    nuget_archiver: NuGetArchiverConfig = Field(default_factory=NuGetArchiverConfig)
    rubygems_archiver: RubyGemsArchiverConfig = Field(default_factory=RubyGemsArchiverConfig)
```

In `ChannelsConfig`, add:
```python
    cargo: str = ""
    nuget: str = ""
    rubygems: str = ""
```

In `ChannelRegistryConfig`, add to `__init__` defaults:
```python
        "cargo": [],
        "nuget": [],
        "rubygems": [],
```

**Test:** `from config import get_config; cfg = get_config(); assert hasattr(cfg, 'cargo_archiver')`

---

### Task 1.2: `config/schema.yaml` — Add schema sections

**File:** `config/schema.yaml`
**Changes:** Append 3 new sections after `pypi_libs_archiver`:

```yaml
cargo_archiver:
  limit: 100
  output_dir: ./temp_cargo
  retries: 3
  retry_delay: 10
  split_mode: auto

nuget_archiver:
  limit: 100
  output_dir: ./temp_nuget
  retries: 3
  retry_delay: 10
  split_mode: auto

rubygems_archiver:
  limit: 100
  output_dir: ./temp_rubygems
  retries: 3
  retry_delay: 10
  split_mode: auto
```

In `channels` section, add:
```yaml
  cargo: ""                     # Cargo channel URL
  nuget: ""                     # NuGet channel URL
  rubygems: ""                  # RubyGems channel URL
```

In `setup.skipped_channels` comment, add the 3 new names.

**Test:** Schema loads without error via `config/loader.py`

---

### Task 1.3: `config.yaml` — Add user config sections

**File:** `config.yaml`
**Changes:** Same as schema.yaml (append the 3 sections + channel entries).

**Test:** `python -c "from config import get_config; print(get_config().cargo_archiver.limit)"`

---

### Task 1.4: `.env.example` — Add channel env vars

**File:** `.env.example`
**Changes:** After `CHANNEL_npm=`, add:

```env
CHANNEL_cargo=                  # Cargo channel URL
CHANNEL_nuget=                  # NuGet channel URL
CHANNEL_rubygems=               # RubyGems channel URL
```

After the NPM archiver env overrides, add:

```env
# Cargo Archiver
CARGO_ARCHIVER_LIMIT=
CARGO_ARCHIVER_OUTPUT_DIR=
CARGO_ARCHIVER_RETRIES=
CARGO_ARCHIVER_RETRY_DELAY=
CARGO_ARCHIVER_SPLIT_MODE=

# NuGet Archiver
NUGET_ARCHIVER_LIMIT=
NUGET_ARCHIVER_OUTPUT_DIR=
NUGET_ARCHIVER_RETRIES=
NUGET_ARCHIVER_RETRY_DELAY=
NUGET_ARCHIVER_SPLIT_MODE=

# RubyGems Archiver
RUBYGEMS_ARCHIVER_LIMIT=
RUBYGEMS_ARCHIVER_OUTPUT_DIR=
RUBYGEMS_ARCHIVER_RETRIES=
RUBYGEMS_ARCHIVER_RETRY_DELAY=
RUBYGEMS_ARCHIVER_SPLIT_MODE=
```

---

### Task 1.5: `config_utils.py` — Update channel registry

**File:** `config_utils.py`
**Changes:**

1. In `_CHANNEL_TO_FUNCTION` dict, add:
```python
    "cargo": "cargo",
    "nuget": "nuget",
    "rubygems": "rubygems",
```

2. In `is_setup_complete()`, update the channel loop to include new channels:
```python
    for ch_name in ("max", "pypi", "media", "backup", "npm", "cargo", "nuget", "rubygems"):
```
And update the final check:
```python
    return has_configured or len(skipped) >= 8
```

**Test:** `from config_utils import is_setup_complete; is_setup_complete({})` returns `False`

---

## Batch 2: Three Archivers (Parallel)

### Task 2.1: `cargo_api.py` — crates.io API wrapper

**File:** `cargo_api.py`
**Pattern:** Mirror `pypi_api.py` structure

```python
"""Cargo (crates.io) API wrapper."""

from __future__ import annotations
import os
import time
import requests
from logging_config import LogMixin

# ── Exceptions ──

class CargoAPIError(Exception):
    """Base exception for Cargo API."""

class NetworkError(CargoAPIError):
    pass

class RateLimitError(CargoAPIError):
    pass


# ── API Client ──

class CargoAPI(LogMixin):
    """Wrapper around crates.io API."""
    
    DEFAULT_TIMEOUT = 60
    _CRATES_API = "https://crates.io/api/v1"
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "gitax-cargo-archiver/1.0",
            "Accept": "application/json",
        })
        self._cache: dict = {}
    
    # ── Rate-limited request ──
    
    def _request_with_backoff(self, url: str, max_retries: int = 3) -> requests.Response:
        """GET request with exponential backoff on 429/5xx."""
        for attempt in range(max_retries):
            try:
                resp = self.session.get(url, timeout=self.DEFAULT_TIMEOUT)
                if resp.status_code == 429:
                    wait = int(resp.headers.get("Retry-After", 2 ** attempt))
                    self.logger.warning(f"Rate limited, waiting {wait}s")
                    time.sleep(wait)
                    continue
                if 500 <= resp.status_code < 600:
                    time.sleep(2 ** attempt)
                    continue
                resp.raise_for_status()
                return resp
            except requests.exceptions.RequestException as e:
                if attempt == max_retries - 1:
                    raise NetworkError(f"Request failed: {e}") from e
                time.sleep(2 ** attempt)
        raise RateLimitError("Max retries exceeded")
    
    # ── Fetch top crates ──
    
    def fetch_top_crates(self, limit: int = 100) -> list[dict]:
        """
        Fetch top crates by recent downloads from crates.io.
        
        Uses the crates.io search API sorted by recent-downloads.
        
        Returns list of dicts:
          {name, version, downloads, recent_downloads, description, crate_size}
        """
        url = f"{self._CRATES_API}/crates?q=&per_page={min(limit, 100)}&sort=recent-downloads"
        resp = self._request_with_backoff(url)
        data = resp.json()
        
        results = []
        for crate in data.get("crates", []):
            results.append({
                "name": crate["name"],
                "version": crate["max_version"],
                "downloads": crate["downloads"],
                "recent_downloads": crate["recent_downloads"],
                "description": (crate.get("description") or ""),
                "crate_size": crate.get("crate_size", 0),
            })
        return results
    
    # ── Get crate info ──
    
    def get_crate_info(self, name: str) -> dict | None:
        """Fetch detailed info for a single crate (with caching)."""
        if name in self._cache:
            return self._cache[name]
        
        url = f"{self._CRATES_API}/crates/{name}"
        resp = self._request_with_backoff(url)
        data = resp.json()
        crate = data["crate"]
        v = data["versions"][0] if data.get("versions") else {}
        
        info = {
            "name": crate["name"],
            "version": v.get("num", crate.get("max_version", "")),
            "description": crate.get("description", ""),
            "downloads": crate.get("downloads", 0),
            "recent_downloads": crate.get("recent_downloads", 0),
            "license": crate.get("license", ""),
            "crate_size": v.get("crate_size", 0),
            "homepage": crate.get("homepage", ""),
            "repository": crate.get("repository", ""),
        }
        self._cache[name] = info
        return info
    
    # ── Download crate file ──
    
    def download_crate(self, name: str, version: str, output_dir: str) -> str | None:
        """
        Download a single .crate file for the given crate+version.
        Returns path to downloaded file, or None on failure.
        """
        url = f"{self._CRATES_API}/crates/{name}/{version}/download"
        filepath = os.path.join(output_dir, f"{name}-{version}.crate")
        
        if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
            self.logger.info(f"Already downloaded: {filepath}")
            return filepath
        
        os.makedirs(output_dir, exist_ok=True)
        resp = self._request_with_backoff(url, max_retries=3)
        
        with open(filepath, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        
        size = os.path.getsize(filepath)
        self.logger.info(f"Downloaded {name}-{version}: {size / 1024 / 1024:.1f} MB")
        return filepath
    
    # ── Cache ──
    
    def clear_cache(self):
        self._cache.clear()
```

**Key API details:**
- crates.io search: `https://crates.io/api/v1/crates?q=&per_page=100&sort=recent-downloads`
- Crate detail: `https://crates.io/api/v1/crates/{name}`
- Download: `https://crates.io/api/v1/crates/{name}/{version}/download`
- File extension: `.crate`
- No auth token required

---

### Task 2.2: `cargo_journal.py` — Cargo JSON journal

**File:** `cargo_journal.py`
**Pattern:** Mirror `pypi_libs_journal.py`

```python
"""Cargo archiver journal — tracks processed Rust crates."""

from __future__ import annotations
import os
from datetime import datetime
from shared_journal import BaseJournal


class CargoJournal(BaseJournal):
    """Journal for tracking uploaded Rust crates."""
    
    def _create_empty(self) -> dict:
        return {"crates": []}
    
    def add(self, name: str, version: str, description: str = "",
            downloads: int = 0, crate_size: int = 0) -> bool:
        """Add a crate entry. Returns False if duplicate (name, version)."""
        if self.exists(name, version):
            return False
        self.data["crates"].append({
            "name": name,
            "version": version,
            "description": description,
            "downloads": downloads,
            "crate_size": crate_size,
            "status": "sent",
            "sent_at": datetime.now().isoformat(),
        })
        self.save()
        return True
    
    def exists(self, name: str, version: str) -> bool:
        """Check if exact (name, version) pair exists."""
        for entry in self.data["crates"]:
            if entry["name"] == name and entry["version"] == version:
                return True
        return False
    
    def get(self, name: str) -> dict | None:
        """Get latest entry by crate name."""
        latest = None
        for entry in self.data["crates"]:
            if entry["name"] == name:
                if latest is None or entry["version"] > latest["version"]:
                    latest = entry
        return latest
    
    def get_all(self) -> list[dict]:
        return list(self.data["crates"])
    
    def get_count(self) -> int:
        return len(self.data["crates"])
    
    def get_stats(self) -> dict:
        sent = sum(1 for e in self.data["crates"] if e["status"] == "sent")
        failed = sum(1 for e in self.data["crates"] if e["status"] == "failed")
        return {"total": len(self.data["crates"]), "sent": sent, "failed": failed}
    
    def update(self, name: str, version: str, updates: dict) -> bool:
        for entry in self.data["crates"]:
            if entry["name"] == name and entry["version"] == version:
                entry.update(updates)
                entry["updated_at"] = datetime.now().isoformat()
                self.save()
                return True
        return False
    
    def mark_failed(self, name: str, version: str, error: str = ""):
        self.data["crates"].append({
            "name": name,
            "version": version,
            "description": "",
            "downloads": 0,
            "crate_size": 0,
            "status": "failed",
            "error": error,
            "failed_at": datetime.now().isoformat(),
        })
        self.save()
```

---

### Task 2.3: `cargo_archiver.py` — Cargo archiver orchestrator

**File:** `cargo_archiver.py`
**Pattern:** Mirror `pypi_libs_archiver.py`

```python
"""Cargo (crates.io) archiver — downloads top Rust crates and sends to MAX."""

from __future__ import annotations
import os
import sys
import time
from pathlib import Path
from browser_init import BrowserInitMixin
from signal_handler import SignalHandler
from logging_config import LogMixin
from config_utils import get_channel_url, get_split_mode
from cargo_api import CargoAPI
from cargo_journal import CargoJournal
from utils import format_file_size


class CargoArchiver(BrowserInitMixin, LogMixin):
    """Download top Rust crates and upload to MAX channel."""
    
    _channel_key = "cargo"
    _section_key = "cargo_archiver"
    
    def __init__(self, config_path: str = "config.yaml"):
        from config import init_config, get_config
        init_config(config_path)
        cfg = get_config()
        self.config = cfg.model_dump()
        self.browser = None
        
        # Config values
        cargo_cfg = self.config.get("cargo_archiver", {})
        self.limit = cargo_cfg.get("limit", 100)
        self.output_dir = cargo_cfg.get("output_dir", "./temp_cargo")
        self.retries = cargo_cfg.get("retries", 3)
        self.retry_delay = cargo_cfg.get("retry_delay", 10)
        self.split_mode = get_split_mode(self.config, "cargo_archiver", "auto")
        
        # Channel URL
        self.channel_url = get_channel_url(
            self.config, "cargo", label="Cargo канал", required=False
        )
        
        # Journal + API
        self.journal = CargoJournal("cargo_journal.json")
        self.api = CargoAPI()
        
        # Shutdown
        self._shutdown = False
        SignalHandler().register(self)
    
    @staticmethod
    def _format_downloads(count: int) -> str:
        if count >= 1_000_000_000:
            return f"{count / 1_000_000_000:.1f}B"
        if count >= 1_000_000:
            return f"{count / 1_000_000:.1f}M"
        if count >= 1_000:
            return f"{count / 1_000:.1f}K"
        return str(count)
    
    def _build_message_text(self, crate_data: dict, file_sizes: list[int] | None = None) -> str:
        name = crate_data.get("name", "unknown")
        version = crate_data.get("version", "")
        desc = crate_data.get("description", "") or "Без описания"
        downloads = crate_data.get("downloads", 0)
        license_str = crate_data.get("license", "")
        repo = crate_data.get("repository", "") or crate_data.get("homepage", "")
        
        lines = [
            f"🦀 **{name}** — {version}",
            f"",
            f"{desc}",
            f"",
            f"📥 Загрузок: {self._format_downloads(downloads)}",
        ]
        if license_str:
            lines.append(f"📜 Лицензия: {license_str}")
        lines.append(f"📦 crates.io/crates/{name}")
        if repo:
            lines.append(repo)
        
        if file_sizes:
            for i, size in enumerate(file_sizes, 1):
                lines.append(f"Файл {i}: {format_file_size(size)}")
        
        return "\n".join(lines)
    
    def _print_progress(self, current: int, total: int, uploaded: int, 
                        skipped: int, failed: int):
        pct = (current / total * 100) if total else 0
        print(f"\r  Прогресс: {current}/{total} ({pct:.0f}%) "
              f"✓{uploaded} ⏭{skipped} ✗{failed}", end="", flush=True)
    
    def _download_and_send(self, crate_data: dict) -> bool:
        """Download single crate and upload to MAX."""
        name = crate_data["name"]
        version = crate_data["version"]
        
        # Download .crate file
        filepath = self.api.download_crate(name, version, self.output_dir)
        if not filepath or not os.path.exists(filepath):
            return False
        
        file_size = os.path.getsize(filepath)
        message_text = self._build_message_text(crate_data, [file_size])
        
        # Upload to MAX
        browser = self._ensure_browser_connected()
        success = browser.send_file_with_message(filepath, message_text)
        
        # Cleanup
        try:
            os.remove(filepath)
        except OSError:
            pass
        
        return success
    
    def load_top_crates(self):
        """Download top Rust crates and upload to MAX channel."""
        print("\n" + "=" * 60)
        print("  Загрузка топ Rust crates (crates.io)")
        print("=" * 60)
        
        if not self.channel_url:
            print("\n  ✗ URL канала Cargo не указан.")
            return
        
        crates = self.api.fetch_top_crates(self.limit)
        total = len(crates)
        print(f"\n  Найдено {total} crates\n")
        
        uploaded = 0
        skipped = 0
        failed = 0
        
        for i, crate in enumerate(crates, 1):
            if self._shutdown:
                print("\n  Остановка...")
                break
            
            name = crate["name"]
            version = crate["version"]
            
            if self.journal.exists(name, version):
                skipped += 1
                self._print_progress(i, total, uploaded, skipped, failed)
                continue
            
            try:
                # Get detailed info
                info = self.api.get_crate_info(name)
                if info:
                    crate.update(info)
                
                success = self._download_and_send(crate)
                if success:
                    self.journal.add(
                        name, version, crate.get("description", ""),
                        crate.get("downloads", 0), crate.get("crate_size", 0)
                    )
                    uploaded += 1
                else:
                    self.journal.mark_failed(name, version, "Upload failed")
                    failed += 1
            except Exception as e:
                self.logger.error(f"Error processing {name}: {e}")
                self.journal.mark_failed(name, version, str(e))
                failed += 1
            
            self._print_progress(i, total, uploaded, skipped, failed)
        
        print(f"\n\n  ✓ Загружено: {uploaded}")
        print(f"  ⏭ Пропущено: {skipped}")
        print(f"  ✗ Ошибки: {failed}")
        
        self._close_browser()
    
    def sync_crates(self):
        """Check for new versions of already-tracked crates."""
        print("\n" + "=" * 60)
        print("  Синхронизация Rust crates")
        print("=" * 60)
        
        if not self.channel_url:
            print("\n  ✗ URL канала Cargo не указан.")
            return
        
        all_crates = self.journal.get_all()
        if not all_crates:
            print("\n  Журнал пуст. Нечего синхронизировать.")
            return
        
        total = len(all_crates)
        updated = 0
        no_change = 0
        
        for i, entry in enumerate(all_crates, 1):
            if self._shutdown:
                print("\n  Остановка...")
                break
            
            name = entry["name"]
            current_version = entry["version"]
            
            try:
                info = self.api.get_crate_info(name)
                if info and info["version"] != current_version:
                    crate_data = {**entry, **info}
                    success = self._download_and_send(crate_data)
                    if success:
                        self.journal.add(
                            name, info["version"], info.get("description", ""),
                            info.get("downloads", 0), info.get("crate_size", 0)
                        )
                        updated += 1
                    else:
                        self.logger.warning(f"Failed to upload {name}-{info['version']}")
                else:
                    no_change += 1
            except Exception as e:
                self.logger.error(f"Sync error for {name}: {e}")
            
            self._print_progress(i, total, updated, no_change, 0)
        
        print(f"\n\n  ✓ Обновлено: {updated}")
        print(f"  ⏭ Без изменений: {no_change}")
        
        self._close_browser()
```

---

### Task 2.4: `nuget_api.py` — NuGet API wrapper

**File:** `nuget_api.py`
**Pattern:** Mirror `pypi_api.py` structure

```python
"""NuGet (.NET) API wrapper."""

from __future__ import annotations
import os
import time
import requests
from logging_config import LogMixin


class NuGetAPIError(Exception):
    """Base exception for NuGet API."""

class NetworkError(NuGetAPIError):
    pass

class RateLimitError(NuGetAPIError):
    pass


class NuGetAPI(LogMixin):
    """Wrapper around NuGet.org V3 API."""
    
    DEFAULT_TIMEOUT = 60
    _REGISTRY_URL = "https://api.nuget.org/v3"
    _QUERY_URL = "https://api.nuget.org/v3-flatcontainer"
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "gitax-nuget-archiver/1.0",
            "Accept": "application/json",
        })
        self._cache: dict = {}
        self._search_service_url: str | None = None
    
    def _request_with_backoff(self, url: str, max_retries: int = 3) -> requests.Response:
        """GET request with exponential backoff on 429/5xx."""
        for attempt in range(max_retries):
            try:
                resp = self.session.get(url, timeout=self.DEFAULT_TIMEOUT)
                if resp.status_code == 429:
                    wait = int(resp.headers.get("Retry-After", 2 ** attempt))
                    self.logger.warning(f"Rate limited, waiting {wait}s")
                    time.sleep(wait)
                    continue
                if 500 <= resp.status_code < 600:
                    time.sleep(2 ** attempt)
                    continue
                resp.raise_for_status()
                return resp
            except requests.exceptions.RequestException as e:
                if attempt == max_retries - 1:
                    raise NetworkError(f"Request failed: {e}") from e
                time.sleep(2 ** attempt)
        raise RateLimitError("Max retries exceeded")
    
    def _get_search_service_url(self) -> str:
        """Resolve the search service URL from NuGet V3 registry."""
        if self._search_service_url:
            return self._search_service_url
        
        resp = self._request_with_backoff(f"{self._REGISTRY_URL}/index.json")
        data = resp.json()
        for resource in data.get("resources", []):
            if resource.get("@type") == "SearchQueryService":
                self._search_service_url = resource["@id"]
                return self._search_service_url
        
        # Fallback
        self._search_service_url = f"{self._REGISTRY_URL}/query"
        return self._search_service_url
    
    def fetch_top_packages(self, limit: int = 100) -> list[dict]:
        """
        Fetch top NuGet packages by download count.
        
        Returns list of dicts:
          {id, version, downloads, summary, package_size}
        """
        search_url = self._get_search_service_url()
        params = {
            "semVerLevel": "2.0.0",
            "q": "*",
            "skip": "0",
            "take": str(min(limit, 50)),
        }
        resp = self._request_with_backoff(search_url, params=params)
        data = resp.json()
        
        results = []
        for pkg in data.get("data", []):
            versions = pkg.get("versions", [])
            latest = versions[-1] if versions else {}
            results.append({
                "id": pkg.get("id", ""),
                "version": latest.get("version", ""),
                "downloads": pkg.get("totalDownloads", 0),
                "summary": (latest.get("description", "") or ""),
                "package_size": 0,
            })
        return results
    
    def get_package_info(self, package_id: str) -> dict | None:
        """Fetch detailed info for a single package (with caching)."""
        if package_id in self._cache:
            return self._cache[package_id]
        
        url = f"{self._REGISTRY_URL}/package/{package_id}/index.json"
        resp = self._request_with_backoff(url)
        data = resp.json()
        
        # Get latest version
        items = data.get("items", [])
        latest = items[-1] if items else {}
        
        # Get version details
        version_url = latest.get("url", "")
        version_info = {}
        if version_url:
            try:
                vresp = self._request_with_backoff(version_url)
                version_info = vresp.json()
            except Exception:
                pass
        
        info = {
            "id": package_id,
            "version": latest.get("version", ""),
            "summary": version_info.get("description", ""),
            "downloads": version_info.get("totalDownloads", 0),
            "license_url": version_info.get("licenseUrl", ""),
            "project_url": version_info.get("projectUrl", ""),
            "package_size": version_info.get("packageSize", 0),
        }
        self._cache[package_id] = info
        return info
    
    def download_package(self, package_id: str, version: str, output_dir: str) -> str | None:
        """
        Download a single .nupkg file.
        Returns path to downloaded file, or None on failure.
        """
        url = f"{self._QUERY_URL}/{package_id}/{version}/{package_id}.{version}.nupkg"
        filepath = os.path.join(output_dir, f"{package_id}.{version}.nupkg")
        
        if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
            self.logger.info(f"Already downloaded: {filepath}")
            return filepath
        
        os.makedirs(output_dir, exist_ok=True)
        resp = self._request_with_backoff(url, max_retries=3)
        
        with open(filepath, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        
        size = os.path.getsize(filepath)
        self.logger.info(f"Downloaded {package_id}.{version}: {size / 1024 / 1024:.1f} MB")
        return filepath
    
    def clear_cache(self):
        self._cache.clear()
```

**Key API details:**
- Registry: `https://api.nuget.org/v3/index.json`
- Search: resolved from registry's `SearchQueryService` URL
- Package index: `https://api.nuget.org/v3/package/{id}/index.json`
- Download: `https://api.nuget.org/v3-flatcontainer/{id}/{version}/{id}.{version}.nupkg`
- File extension: `.nupkg`
- No auth token required

---

### Task 2.5: `nuget_journal.py` — NuGet JSON journal

**File:** `nuget_journal.py`
**Pattern:** Mirror `pypi_libs_journal.py`

```python
"""NuGet archiver journal — tracks processed .NET packages."""

from __future__ import annotations
from datetime import datetime
from shared_journal import BaseJournal


class NuGetJournal(BaseJournal):
    """Journal for tracking uploaded NuGet packages."""
    
    def _create_empty(self) -> dict:
        return {"packages": []}
    
    def add(self, name: str, version: str, description: str = "",
            downloads: int = 0, package_size: int = 0) -> bool:
        if self.exists(name, version):
            return False
        self.data["packages"].append({
            "name": name,
            "version": version,
            "description": description,
            "downloads": downloads,
            "package_size": package_size,
            "status": "sent",
            "sent_at": datetime.now().isoformat(),
        })
        self.save()
        return True
    
    def exists(self, name: str, version: str) -> bool:
        for entry in self.data["packages"]:
            if entry["name"] == name and entry["version"] == version:
                return True
        return False
    
    def get(self, name: str) -> dict | None:
        latest = None
        for entry in self.data["packages"]:
            if entry["name"] == name:
                if latest is None or entry["version"] > latest["version"]:
                    latest = entry
        return latest
    
    def get_all(self) -> list[dict]:
        return list(self.data["packages"])
    
    def get_count(self) -> int:
        return len(self.data["packages"])
    
    def get_stats(self) -> dict:
        sent = sum(1 for e in self.data["packages"] if e["status"] == "sent")
        failed = sum(1 for e in self.data["packages"] if e["status"] == "failed")
        return {"total": len(self.data["packages"]), "sent": sent, "failed": failed}
    
    def update(self, name: str, version: str, updates: dict) -> bool:
        for entry in self.data["packages"]:
            if entry["name"] == name and entry["version"] == version:
                entry.update(updates)
                entry["updated_at"] = datetime.now().isoformat()
                self.save()
                return True
        return False
    
    def mark_failed(self, name: str, version: str, error: str = ""):
        self.data["packages"].append({
            "name": name,
            "version": version,
            "description": "",
            "downloads": 0,
            "package_size": 0,
            "status": "failed",
            "error": error,
            "failed_at": datetime.now().isoformat(),
        })
        self.save()
```

---

### Task 2.6: `nuget_archiver.py` — NuGet archiver orchestrator

**File:** `nuget_archiver.py`
**Pattern:** Mirror `pypi_libs_archiver.py`

```python
"""NuGet (.NET) archiver — downloads top NuGet packages and sends to MAX."""

from __future__ import annotations
import os
import time
from browser_init import BrowserInitMixin
from signal_handler import SignalHandler
from logging_config import LogMixin
from config_utils import get_channel_url, get_split_mode
from nuget_api import NuGetAPI
from nuget_journal import NuGetJournal
from utils import format_file_size


class NuGetArchiver(BrowserInitMixin, LogMixin):
    """Download top NuGet packages and upload to MAX channel."""
    
    _channel_key = "nuget"
    _section_key = "nuget_archiver"
    
    def __init__(self, config_path: str = "config.yaml"):
        from config import init_config, get_config
        init_config(config_path)
        cfg = get_config()
        self.config = cfg.model_dump()
        self.browser = None
        
        nuget_cfg = self.config.get("nuget_archiver", {})
        self.limit = nuget_cfg.get("limit", 100)
        self.output_dir = nuget_cfg.get("output_dir", "./temp_nuget")
        self.retries = nuget_cfg.get("retries", 3)
        self.retry_delay = nuget_cfg.get("retry_delay", 10)
        self.split_mode = get_split_mode(self.config, "nuget_archiver", "auto")
        
        self.channel_url = get_channel_url(
            self.config, "nuget", label="NuGet канал", required=False
        )
        
        self.journal = NuGetJournal("nuget_journal.json")
        self.api = NuGetAPI()
        
        self._shutdown = False
        SignalHandler().register(self)
    
    @staticmethod
    def _format_downloads(count: int) -> str:
        if count >= 1_000_000_000:
            return f"{count / 1_000_000_000:.1f}B"
        if count >= 1_000_000:
            return f"{count / 1_000_000:.1f}M"
        if count >= 1_000:
            return f"{count / 1_000:.1f}K"
        return str(count)
    
    def _build_message_text(self, pkg_data: dict, file_sizes: list[int] | None = None) -> str:
        name = pkg_data.get("id", "unknown")
        version = pkg_data.get("version", "")
        desc = pkg_data.get("summary", "") or "Без описания"
        downloads = pkg_data.get("downloads", 0)
        project_url = pkg_data.get("project_url", "")
        
        lines = [
            f"📦 **{name}** — {version}",
            f"",
            f"{desc}",
            f"",
            f"📥 Загрузок: {self._format_downloads(downloads)}",
            f"📦 www.nuget.org/packages/{name}",
        ]
        if project_url:
            lines.append(project_url)
        
        if file_sizes:
            for i, size in enumerate(file_sizes, 1):
                lines.append(f"Файл {i}: {format_file_size(size)}")
        
        return "\n".join(lines)
    
    def _print_progress(self, current: int, total: int, uploaded: int,
                        skipped: int, failed: int):
        pct = (current / total * 100) if total else 0
        print(f"\r  Прогресс: {current}/{total} ({pct:.0f}%) "
              f"✓{uploaded} ⏭{skipped} ✗{failed}", end="", flush=True)
    
    def _download_and_send(self, pkg_data: dict) -> bool:
        name = pkg_data["id"]
        version = pkg_data["version"]
        
        filepath = self.api.download_package(name, version, self.output_dir)
        if not filepath or not os.path.exists(filepath):
            return False
        
        file_size = os.path.getsize(filepath)
        message_text = self._build_message_text(pkg_data, [file_size])
        
        browser = self._ensure_browser_connected()
        success = browser.send_file_with_message(filepath, message_text)
        
        try:
            os.remove(filepath)
        except OSError:
            pass
        
        return success
    
    def load_top_packages(self):
        """Download top NuGet packages and upload to MAX channel."""
        print("\n" + "=" * 60)
        print("  Загрузка топ .NET пакетов (NuGet)")
        print("=" * 60)
        
        if not self.channel_url:
            print("\n  ✗ URL канала NuGet не указан.")
            return
        
        packages = self.api.fetch_top_packages(self.limit)
        total = len(packages)
        print(f"\n  Найдено {total} пакетов\n")
        
        uploaded = 0
        skipped = 0
        failed = 0
        
        for i, pkg in enumerate(packages, 1):
            if self._shutdown:
                print("\n  Остановка...")
                break
            
            name = pkg["id"]
            version = pkg["version"]
            
            if self.journal.exists(name, version):
                skipped += 1
                self._print_progress(i, total, uploaded, skipped, failed)
                continue
            
            try:
                info = self.api.get_package_info(name)
                if info:
                    pkg.update(info)
                
                success = self._download_and_send(pkg)
                if success:
                    self.journal.add(
                        name, version, pkg.get("summary", ""),
                        pkg.get("downloads", 0), pkg.get("package_size", 0)
                    )
                    uploaded += 1
                else:
                    self.journal.mark_failed(name, version, "Upload failed")
                    failed += 1
            except Exception as e:
                self.logger.error(f"Error processing {name}: {e}")
                self.journal.mark_failed(name, version, str(e))
                failed += 1
            
            self._print_progress(i, total, uploaded, skipped, failed)
        
        print(f"\n\n  ✓ Загружено: {uploaded}")
        print(f"  ⏭ Пропущено: {skipped}")
        print(f"  ✗ Ошибки: {failed}")
        
        self._close_browser()
    
    def sync_packages(self):
        """Check for new versions of already-tracked packages."""
        print("\n" + "=" * 60)
        print("  Синхронизация .NET пакетов")
        print("=" * 60)
        
        if not self.channel_url:
            print("\n  ✗ URL канала NuGet не указан.")
            return
        
        all_pkgs = self.journal.get_all()
        if not all_pkgs:
            print("\n  Журнал пуст. Нечего синхронизировать.")
            return
        
        total = len(all_pkgs)
        updated = 0
        no_change = 0
        
        for i, entry in enumerate(all_pkgs, 1):
            if self._shutdown:
                print("\n  Остановка...")
                break
            
            name = entry["name"]
            current_version = entry["version"]
            
            try:
                info = self.api.get_package_info(name)
                if info and info["version"] != current_version:
                    pkg_data = {**entry, **info}
                    success = self._download_and_send(pkg_data)
                    if success:
                        self.journal.add(
                            name, info["version"], info.get("summary", ""),
                            info.get("downloads", 0), info.get("package_size", 0)
                        )
                        updated += 1
                    else:
                        self.logger.warning(f"Failed to upload {name}-{info['version']}")
                else:
                    no_change += 1
            except Exception as e:
                self.logger.error(f"Sync error for {name}: {e}")
            
            self._print_progress(i, total, updated, no_change, 0)
        
        print(f"\n\n  ✓ Обновлено: {updated}")
        print(f"  ⏭ Без изменений: {no_change}")
        
        self._close_browser()
```

---

### Task 2.7: `rubygems_api.py` — RubyGems API wrapper

**File:** `rubygems_api.py`
**Pattern:** Mirror `pypi_api.py` structure

```python
"""RubyGems API wrapper."""

from __future__ import annotations
import os
import time
import requests
from logging_config import LogMixin


class RubyGemsAPIError(Exception):
    """Base exception for RubyGems API."""

class NetworkError(RubyGemsAPIError):
    pass

class RateLimitError(RubyGemsAPIError):
    pass


class RubyGemsAPI(LogMixin):
    """Wrapper around rubygems.org API."""
    
    DEFAULT_TIMEOUT = 60
    _API_BASE = "https://rubygems.org/api/v1"
    _DOWNLOAD_BASE = "https://rubygems.org/downloads"
    
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "gitax-rubygems-archiver/1.0",
            "Accept": "application/json",
        })
        self._cache: dict = {}
    
    def _request_with_backoff(self, url: str, max_retries: int = 3) -> requests.Response:
        """GET request with exponential backoff on 429/5xx."""
        for attempt in range(max_retries):
            try:
                resp = self.session.get(url, timeout=self.DEFAULT_TIMEOUT)
                if resp.status_code == 429:
                    wait = int(resp.headers.get("Retry-After", 2 ** attempt))
                    self.logger.warning(f"Rate limited, waiting {wait}s")
                    time.sleep(wait)
                    continue
                if 500 <= resp.status_code < 600:
                    time.sleep(2 ** attempt)
                    continue
                resp.raise_for_status()
                return resp
            except requests.exceptions.RequestException as e:
                if attempt == max_retries - 1:
                    raise NetworkError(f"Request failed: {e}") from e
                time.sleep(2 ** attempt)
        raise RateLimitError("Max retries exceeded")
    
    def fetch_top_gems(self, limit: int = 100) -> list[dict]:
        """
        Fetch top Ruby gems by download count.
        
        Uses the reverse dependency API and search to find popular gems.
        Falls back to searching with no query for most-downloaded.
        
        Returns list of dicts:
          {name, version, downloads, description}
        """
        # RubyGems doesn't have a direct "top by downloads" API.
        # We use the search API which returns gems sorted by relevance/popularity.
        url = f"{self._API_BASE}/reverse_dependencies/rails.json"
        
        try:
            resp = self._request_with_backoff(url)
            data = resp.json()
            # This returns gems that depend on rails — a good proxy for popularity
            results = []
            seen = set()
            for gem in data[:limit]:
                name = gem.get("name", "")
                if name and name not in seen:
                    seen.add(name)
                    results.append({
                        "name": name,
                        "version": gem.get("version", ""),
                        "downloads": 0,
                        "description": "",
                    })
            return results
        except Exception:
            pass
        
        # Fallback: search with empty query
        url = f"{self._API_BASE}/search.json?query=&per_page={min(limit, 100)}"
        resp = self._request_with_backoff(url)
        data = resp.json()
        
        results = []
        for gem in data.get("results", []):
            name = gem.get("name", "")
            if name:
                results.append({
                    "name": name,
                    "version": gem.get("version", ""),
                    "downloads": gem.get("downloads", {}).get("all_time", 0),
                    "description": (gem.get("info", "") or ""),
                })
        return results[:limit]
    
    def get_gem_info(self, name: str) -> dict | None:
        """Fetch detailed info for a single gem (with caching)."""
        if name in self._cache:
            return self._cache[name]
        
        url = f"{self._API_BASE}/gems/{name}.json"
        resp = self._request_with_backoff(url)
        data = resp.json()
        
        info = {
            "name": data.get("name", name),
            "version": data.get("version", ""),
            "description": (data.get("description", "") or ""),
            "downloads": data.get("downloads_count", 0),
            "license": data.get("licenses", ""),
            "homepage": data.get("homepage_uri", ""),
            "source_code_uri": data.get("source_code_uri", ""),
        }
        self._cache[name] = info
        return info
    
    def download_gem(self, name: str, version: str, output_dir: str) -> str | None:
        """
        Download a single .gem file.
        Returns path to downloaded file, or None on failure.
        """
        url = f"{self._DOWNLOAD_BASE}/{name}-{version}.gem"
        filepath = os.path.join(output_dir, f"{name}-{version}.gem")
        
        if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
            self.logger.info(f"Already downloaded: {filepath}")
            return filepath
        
        os.makedirs(output_dir, exist_ok=True)
        resp = self._request_with_backoff(url, max_retries=3)
        
        with open(filepath, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        
        size = os.path.getsize(filepath)
        self.logger.info(f"Downloaded {name}-{version}: {size / 1024 / 1024:.1f} MB")
        return filepath
    
    def clear_cache(self):
        self._cache.clear()
```

**Key API details:**
- Gem detail: `https://rubygems.org/api/v1/gems/{name}.json`
- Search: `https://rubygems.org/api/v1/search.json?query=&per_page=100`
- Download: `https://rubygems.org/downloads/{name}-{version}.gem`
- File extension: `.gem`
- No auth token required

---

### Task 2.8: `rubygems_journal.py` — RubyGems JSON journal

**File:** `rubygems_journal.py`
**Pattern:** Mirror `pypi_libs_journal.py`

```python
"""RubyGems archiver journal — tracks processed Ruby gems."""

from __future__ import annotations
from datetime import datetime
from shared_journal import BaseJournal


class RubyGemsJournal(BaseJournal):
    """Journal for tracking uploaded Ruby gems."""
    
    def _create_empty(self) -> dict:
        return {"gems": []}
    
    def add(self, name: str, version: str, description: str = "",
            downloads: int = 0) -> bool:
        if self.exists(name, version):
            return False
        self.data["gems"].append({
            "name": name,
            "version": version,
            "description": description,
            "downloads": downloads,
            "status": "sent",
            "sent_at": datetime.now().isoformat(),
        })
        self.save()
        return True
    
    def exists(self, name: str, version: str) -> bool:
        for entry in self.data["gems"]:
            if entry["name"] == name and entry["version"] == version:
                return True
        return False
    
    def get(self, name: str) -> dict | None:
        latest = None
        for entry in self.data["gems"]:
            if entry["name"] == name:
                if latest is None or entry["version"] > latest["version"]:
                    latest = entry
        return latest
    
    def get_all(self) -> list[dict]:
        return list(self.data["gems"])
    
    def get_count(self) -> int:
        return len(self.data["gems"])
    
    def get_stats(self) -> dict:
        sent = sum(1 for e in self.data["gems"] if e["status"] == "sent")
        failed = sum(1 for e in self.data["gems"] if e["status"] == "failed")
        return {"total": len(self.data["gems"]), "sent": sent, "failed": failed}
    
    def update(self, name: str, version: str, updates: dict) -> bool:
        for entry in self.data["gems"]:
            if entry["name"] == name and entry["version"] == version:
                entry.update(updates)
                entry["updated_at"] = datetime.now().isoformat()
                self.save()
                return True
        return False
    
    def mark_failed(self, name: str, version: str, error: str = ""):
        self.data["gems"].append({
            "name": name,
            "version": version,
            "description": "",
            "downloads": 0,
            "status": "failed",
            "error": error,
            "failed_at": datetime.now().isoformat(),
        })
        self.save()
```

---

### Task 2.9: `rubygems_archiver.py` — RubyGems archiver orchestrator

**File:** `rubygems_archiver.py`
**Pattern:** Mirror `pypi_libs_archiver.py`

```python
"""RubyGems archiver — downloads top Ruby gems and sends to MAX."""

from __future__ import annotations
import os
import time
from browser_init import BrowserInitMixin
from signal_handler import SignalHandler
from logging_config import LogMixin
from config_utils import get_channel_url, get_split_mode
from rubygems_api import RubyGemsAPI
from rubygems_journal import RubyGemsJournal
from utils import format_file_size


class RubyGemsArchiver(BrowserInitMixin, LogMixin):
    """Download top Ruby gems and upload to MAX channel."""
    
    _channel_key = "rubygems"
    _section_key = "rubygems_archiver"
    
    def __init__(self, config_path: str = "config.yaml"):
        from config import init_config, get_config
        init_config(config_path)
        cfg = get_config()
        self.config = cfg.model_dump()
        self.browser = None
        
        rg_cfg = self.config.get("rubygems_archiver", {})
        self.limit = rg_cfg.get("limit", 100)
        self.output_dir = rg_cfg.get("output_dir", "./temp_rubygems")
        self.retries = rg_cfg.get("retries", 3)
        self.retry_delay = rg_cfg.get("retry_delay", 10)
        self.split_mode = get_split_mode(self.config, "rubygems_archiver", "auto")
        
        self.channel_url = get_channel_url(
            self.config, "rubygems", label="RubyGems канал", required=False
        )
        
        self.journal = RubyGemsJournal("rubygems_journal.json")
        self.api = RubyGemsAPI()
        
        self._shutdown = False
        SignalHandler().register(self)
    
    @staticmethod
    def _format_downloads(count: int) -> str:
        if count >= 1_000_000_000:
            return f"{count / 1_000_000_000:.1f}B"
        if count >= 1_000_000:
            return f"{count / 1_000_000:.1f}M"
        if count >= 1_000:
            return f"{count / 1_000:.1f}K"
        return str(count)
    
    def _build_message_text(self, gem_data: dict, file_sizes: list[int] | None = None) -> str:
        name = gem_data.get("name", "unknown")
        version = gem_data.get("version", "")
        desc = gem_data.get("description", "") or "Без описания"
        downloads = gem_data.get("downloads", 0)
        license_str = gem_data.get("license", "")
        homepage = gem_data.get("homepage", "")
        
        lines = [
            f"💎 **{name}** — {version}",
            f"",
            f"{desc}",
            f"",
            f"📥 Загрузок: {self._format_downloads(downloads)}",
        ]
        if license_str:
            lines.append(f"📜 Лицензия: {license_str}")
        lines.append(f"📦 rubygems.org/gems/{name}")
        if homepage:
            lines.append(homepage)
        
        if file_sizes:
            for i, size in enumerate(file_sizes, 1):
                lines.append(f"Файл {i}: {format_file_size(size)}")
        
        return "\n".join(lines)
    
    def _print_progress(self, current: int, total: int, uploaded: int,
                        skipped: int, failed: int):
        pct = (current / total * 100) if total else 0
        print(f"\r  Прогресс: {current}/{total} ({pct:.0f}%) "
              f"✓{uploaded} ⏭{skipped} ✗{failed}", end="", flush=True)
    
    def _download_and_send(self, gem_data: dict) -> bool:
        name = gem_data["name"]
        version = gem_data["version"]
        
        filepath = self.api.download_gem(name, version, self.output_dir)
        if not filepath or not os.path.exists(filepath):
            return False
        
        file_size = os.path.getsize(filepath)
        message_text = self._build_message_text(gem_data, [file_size])
        
        browser = self._ensure_browser_connected()
        success = browser.send_file_with_message(filepath, message_text)
        
        try:
            os.remove(filepath)
        except OSError:
            pass
        
        return success
    
    def load_top_gems(self):
        """Download top Ruby gems and upload to MAX channel."""
        print("\n" + "=" * 60)
        print("  Загрузка топ Ruby gems (RubyGems)")
        print("=" * 60)
        
        if not self.channel_url:
            print("\n  ✗ URL канала RubyGems не указан.")
            return
        
        gems = self.api.fetch_top_gems(self.limit)
        total = len(gems)
        print(f"\n  Найдено {total} gems\n")
        
        uploaded = 0
        skipped = 0
        failed = 0
        
        for i, gem in enumerate(gems, 1):
            if self._shutdown:
                print("\n  Остановка...")
                break
            
            name = gem["name"]
            version = gem["version"]
            
            if self.journal.exists(name, version):
                skipped += 1
                self._print_progress(i, total, uploaded, skipped, failed)
                continue
            
            try:
                info = self.api.get_gem_info(name)
                if info:
                    gem.update(info)
                
                success = self._download_and_send(gem)
                if success:
                    self.journal.add(
                        name, version, gem.get("description", ""),
                        gem.get("downloads", 0)
                    )
                    uploaded += 1
                else:
                    self.journal.mark_failed(name, version, "Upload failed")
                    failed += 1
            except Exception as e:
                self.logger.error(f"Error processing {name}: {e}")
                self.journal.mark_failed(name, version, str(e))
                failed += 1
            
            self._print_progress(i, total, uploaded, skipped, failed)
        
        print(f"\n\n  ✓ Загружено: {uploaded}")
        print(f"  ⏭ Пропущено: {skipped}")
        print(f"  ✗ Ошибки: {failed}")
        
        self._close_browser()
    
    def sync_gems(self):
        """Check for new versions of already-tracked gems."""
        print("\n" + "=" * 60)
        print("  Синхронизация Ruby gems")
        print("=" * 60)
        
        if not self.channel_url:
            print("\n  ✗ URL канала RubyGems не указан.")
            return
        
        all_gems = self.journal.get_all()
        if not all_gems:
            print("\n  Журнал пуст. Нечего синхронизировать.")
            return
        
        total = len(all_gems)
        updated = 0
        no_change = 0
        
        for i, entry in enumerate(all_gems, 1):
            if self._shutdown:
                print("\n  Остановка...")
                break
            
            name = entry["name"]
            current_version = entry["version"]
            
            try:
                info = self.api.get_gem_info(name)
                if info and info["version"] != current_version:
                    gem_data = {**entry, **info}
                    success = self._download_and_send(gem_data)
                    if success:
                        self.journal.add(
                            name, info["version"], info.get("description", ""),
                            info.get("downloads", 0)
                        )
                        updated += 1
                    else:
                        self.logger.warning(f"Failed to upload {name}-{info['version']}")
                else:
                    no_change += 1
            except Exception as e:
                self.logger.error(f"Sync error for {name}: {e}")
            
            self._print_progress(i, total, updated, no_change, 0)
        
        print(f"\n\n  ✓ Обновлено: {updated}")
        print(f"  ⏭ Без изменений: {no_change}")
        
        self._close_browser()
```

---

## Batch 3: Menu Integration

### Task 3.1: `github_archiver.py` — Menu wiring

**File:** `github_archiver.py`
**Changes:** Multiple sections

#### 3.1a: Main menu — Add 3 new menu items

In `_show_main_menu()`, after the existing menu items:
```python
        print(menu_item("1", "GitHub — репозитории", "max"))
        print(menu_item("2", "PyPI — Python библиотеки", "pypi"))
        print(menu_item("3", "Backuper — бэкап папок в канал", "backup"))
        print(menu_item("4", "Файлы — медиа, скачивание, экспорт", "media"))
        print(menu_item("6", "Cargo — Rust crates", "cargo"))
        print(menu_item("7", "NuGet — .NET пакеты", "nuget"))
        print(menu_item("8", "RubyGems — Ruby gems", "rubygems"))
        print("  [5] Сервис — журналы, настройки")
```

#### 3.1b: Module channel mapping

In `_MODULE_CHANNELS`, add:
```python
    _MODULE_CHANNELS = {
        "1": "max",
        "2": "pypi",
        "3": "backup",
        "4": "media",
        "6": "cargo",
        "7": "nuget",
        "8": "rubygems",
    }
```

#### 3.1c: Main menu valid options

In `run()`, update valid options:
```python
            if needs_setup:
                valid_opts = ["0", "x", "1", "2", "3", "4", "5", "6", "7", "8"]
                prompt_text = "Выберите раздел [0/X,1-8]"
            else:
                valid_opts = ["0", "1", "2", "3", "4", "5", "6", "7", "8"]
                prompt_text = "Выберите раздел [0-8]"
```

In the disabled-module check:
```python
            if choice in ("1", "2", "3", "4", "6", "7", "8") and not self._is_module_enabled(choice):
                module_names = {
                    "1": "GitHub", "2": "PyPI", "3": "Backuper", "4": "Файлы",
                    "6": "Cargo", "7": "NuGet", "8": "RubyGems",
                }
```

In the dispatch section:
```python
            elif choice == '6':
                self._run_cargo_menu()
            elif choice == '7':
                self._run_nuget_menu()
            elif choice == '8':
                self._run_rubygems_menu()
```

#### 3.1d: Sub-menu methods

Add after `_run_pypi_menu()`:

```python
    def _cargo_menu(self):
        """Подменю Cargo"""
        print("\n" + "═" * 60)
        print("  Cargo — Rust crates")
        print("─" * 60)
        print()
        print("  [1] Загрузить топ Rust crates")
        print("  [2] Синхронизировать Rust crates")
        print("  [0] Назад")
        print()

    def _run_cargo_menu(self):
        """Цикл подменю Cargo"""
        while True:
            os.system('cls' if os.name == 'nt' else 'clear')
            self._cargo_menu()
            choice = prompt_numeric_choice("Выберите действие [0-2]", ["0", "1", "2"])
            if choice == '0':
                break
            elif choice == '1':
                self.run_cargo_archiver()
            elif choice == '2':
                self.run_cargo_sync()

    def _nuget_menu(self):
        """Подменю NuGet"""
        print("\n" + "═" * 60)
        print("  NuGet — .NET пакеты")
        print("─" * 60)
        print()
        print("  [1] Загрузить топ .NET пакетов")
        print("  [2] Синхронизировать .NET пакеты")
        print("  [0] Назад")
        print()

    def _run_nuget_menu(self):
        """Цикл подменю NuGet"""
        while True:
            os.system('cls' if os.name == 'nt' else 'clear')
            self._nuget_menu()
            choice = prompt_numeric_choice("Выберите действие [0-2]", ["0", "1", "2"])
            if choice == '0':
                break
            elif choice == '1':
                self.run_nuget_archiver()
            elif choice == '2':
                self.run_nuget_sync()

    def _rubygems_menu(self):
        """Подменю RubyGems"""
        print("\n" + "═" * 60)
        print("  RubyGems — Ruby gems")
        print("─" * 60)
        print()
        print("  [1] Загрузить топ Ruby gems")
        print("  [2] Синхронизировать Ruby gems")
        print("  [0] Назад")
        print()

    def _run_rubygems_menu(self):
        """Цикл подменю RubyGems"""
        while True:
            os.system('cls' if os.name == 'nt' else 'clear')
            self._rubygems_menu()
            choice = prompt_numeric_choice("Выберите действие [0-2]", ["0", "1", "2"])
            if choice == '0':
                break
            elif choice == '1':
                self.run_rubygems_archiver()
            elif choice == '2':
                self.run_rubygems_sync()
```

#### 3.1e: Runner methods

Add after `run_pypi_libs_sync()`:

```python
    def run_cargo_archiver(self):
        """Загрузить топ Rust crates в MAX канал"""
        from cargo_archiver import CargoArchiver

        print("\n" + "═" * 60)
        print("  Загрузка топ Rust crates")
        print("═" * 60)

        if not self._ensure_channel_ready("cargo", "Cargo канал", "cargo_archiver"):
            input("\n  Нажмите Enter для возврата в меню...")
            return

        try:
            archiver = CargoArchiver("config.yaml")
            archiver.load_top_crates()
        except Exception as e:
            print(f"\n  ✗ Ошибка: {e}")
            self.logger.error(f"Cargo archiver error: {e}", exc_info=True)

        input("\n  Нажмите Enter для возврата в меню...")

    def run_cargo_sync(self):
        """Синхронизировать версии Rust crates"""
        from cargo_archiver import CargoArchiver

        print("\n" + "═" * 60)
        print("  Синхронизация Rust crates")
        print("═" * 60)

        if not self._ensure_channel_ready("cargo", "Cargo канал", "cargo_archiver"):
            input("\n  Нажмите Enter для возврата в меню...")
            return

        try:
            archiver = CargoArchiver("config.yaml")
            archiver.sync_crates()
        except Exception as e:
            print(f"\n  ✗ Ошибка: {e}")
            self.logger.error(f"Cargo sync error: {e}", exc_info=True)

        input("\n  Нажмите Enter для возврата в меню...")

    def run_nuget_archiver(self):
        """Загрузить топ .NET пакетов в MAX канал"""
        from nuget_archiver import NuGetArchiver

        print("\n" + "═" * 60)
        print("  Загрузка топ .NET пакетов")
        print("═" * 60)

        if not self._ensure_channel_ready("nuget", "NuGet канал", "nuget_archiver"):
            input("\n  Нажмите Enter для возврата в меню...")
            return

        try:
            archiver = NuGetArchiver("config.yaml")
            archiver.load_top_packages()
        except Exception as e:
            print(f"\n  ✗ Ошибка: {e}")
            self.logger.error(f"NuGet archiver error: {e}", exc_info=True)

        input("\n  Нажмите Enter для возврата в меню...")

    def run_nuget_sync(self):
        """Синхронизировать версии .NET пакетов"""
        from nuget_archiver import NuGetArchiver

        print("\n" + "═" * 60)
        print("  Синхронизация .NET пакетов")
        print("═" * 60)

        if not self._ensure_channel_ready("nuget", "NuGet канал", "nuget_archiver"):
            input("\n  Нажмите Enter для возврата в меню...")
            return

        try:
            archiver = NuGetArchiver("config.yaml")
            archiver.sync_packages()
        except Exception as e:
            print(f"\n  ✗ Ошибка: {e}")
            self.logger.error(f"NuGet sync error: {e}", exc_info=True)

        input("\n  Нажмите Enter для возврата в меню...")

    def run_rubygems_archiver(self):
        """Загрузить топ Ruby gems в MAX канал"""
        from rubygems_archiver import RubyGemsArchiver

        print("\n" + "═" * 60)
        print("  Загрузка топ Ruby gems")
        print("═" * 60)

        if not self._ensure_channel_ready("rubygems", "RubyGems канал", "rubygems_archiver"):
            input("\n  Нажмите Enter для возврата в меню...")
            return

        try:
            archiver = RubyGemsArchiver("config.yaml")
            archiver.load_top_gems()
        except Exception as e:
            print(f"\n  ✗ Ошибка: {e}")
            self.logger.error(f"RubyGems archiver error: {e}", exc_info=True)

        input("\n  Нажмите Enter для возврата в меню...")

    def run_rubygems_sync(self):
        """Синхронизировать версии Ruby gems"""
        from rubygems_archiver import RubyGemsArchiver

        print("\n" + "═" * 60)
        print("  Синхронизация Ruby gems")
        print("═" * 60)

        if not self._ensure_channel_ready("rubygems", "RubyGems канал", "rubygems_archiver"):
            input("\n  Нажмите Enter для возврата в меню...")
            return

        try:
            archiver = RubyGemsArchiver("config.yaml")
            archiver.sync_gems()
        except Exception as e:
            print(f"\n  ✗ Ошибка: {e}")
            self.logger.error(f"RubyGems sync error: {e}", exc_info=True)

        input("\n  Нажмите Enter для возврата в меню...")
```

#### 3.1f: `_ensure_channel_ready` — Add channel mappings

In `_ensure_channel_ready()`, update `_CHANNEL_TO_FUNCTION`:
```python
        _CHANNEL_TO_FUNCTION = {
            "max": "github",
            "pypi": "pypi",
            "media": "media",
            "backup": "backup",
            "cargo": "cargo",
            "nuget": "nuget",
            "rubygems": "rubygems",
        }
```

---

## Batch 4: Tests

### Task 4.1: `tests/test_cargo_api.py`

**File:** `tests/test_cargo_api.py`
**Pattern:** Mirror `tests/test_pypi_api.py`

Test classes:
- `TestCargoAPIInit` — init, logger, timeout
- `TestRequestWithBackoff` — success, 429, 500, connection error, max retries
- `TestFetchTopCrates` — success, empty response, network error
- `TestGetCrateInfo` — success, caching, invalid crate, clear cache
- `TestDownloadCrate` — success, already exists, failure
- `TestExceptions` — hierarchy

### Task 4.2: `tests/test_cargo_journal.py`

**File:** `tests/test_cargo_journal.py`
**Pattern:** Mirror `tests/test_pypi_libs_journal.py`

Test classes:
- `TestCargoJournalInit` — empty, load existing, corrupted JSON, logger
- `TestCargoJournalAdd` — new entry, duplicate blocked, same name diff version
- `TestCargoJournalExists` — true, false
- `TestCargoJournalGet` — latest version, missing
- `TestCargoJournalStats` — empty, with entries, get_count, get_all
- `TestCargoJournalUpdate` — existing, missing
- `TestCargoJournalClear` — clear resets
- `TestCargoJournalMarkFailed` — mark failed

### Task 4.3: `tests/test_cargo_archiver.py`

**File:** `tests/test_cargo_archiver.py`
**Pattern:** Mirror `tests/test_pypi_libs_archiver.py`

Test classes:
- `TestFormatDownloads` — billions, millions, thousands, small
- `TestBuildMessageText` — basic, with file sizes, no description, crates.io URL
- `TestConfigValidation` — missing channel URL, from env, from YAML
- `TestJournalIntegration` — creates CargoJournal

### Task 4.4: `tests/test_nuget_api.py`

**File:** `tests/test_nuget_api.py`
**Pattern:** Mirror `tests/test_pypi_api.py` (adapted for NuGet)

Test classes:
- `TestNuGetAPIInit` — init, logger, timeout, registry URL
- `TestRequestWithBackoff` — same as PyPI
- `TestFetchTopPackages` — success, empty, network error
- `TestGetPackageInfo` — success, caching, invalid, clear cache
- `TestDownloadPackage` — success, already exists
- `TestExceptions` — hierarchy

### Task 4.5: `tests/test_nuget_journal.py`

**File:** `tests/test_nuget_journal.py`
**Pattern:** Mirror `tests/test_pypi_libs_journal.py` (adapted for NuGet)

Same test classes as Cargo journal, using `NuGetJournal` and `"packages"` key.

### Task 4.6: `tests/test_nuget_archiver.py`

**File:** `tests/test_nuget_archiver.py`
**Pattern:** Mirror `tests/test_pypi_libs_archiver.py` (adapted for NuGet)

Same test classes as Cargo archiver, using `NuGetArchiver` and nuget-specific data.

### Task 4.7: `tests/test_rubygems_api.py`

**File:** `tests/test_rubygems_api.py`
**Pattern:** Mirror `tests/test_pypi_api.py` (adapted for RubyGems)

Test classes:
- `TestRubyGemsAPIInit` — init, logger, timeout, API base
- `TestRequestWithBackoff` — same as PyPI
- `TestFetchTopGems` — success, fallback search, network error
- `TestGetGemInfo` — success, caching, invalid, clear cache
- `TestDownloadGem` — success, already exists
- `TestExceptions` — hierarchy

### Task 4.8: `tests/test_rubygems_journal.py`

**File:** `tests/test_rubygems_journal.py`
**Pattern:** Mirror `tests/test_pypi_libs_journal.py` (adapted for RubyGems)

Same test classes as Cargo journal, using `RubyGemsJournal` and `"gems"` key.

### Task 4.9: `tests/test_rubygems_archiver.py`

**File:** `tests/test_rubygems_archiver.py`
**Pattern:** Mirror `tests/test_pypi_libs_archiver.py` (adapted for RubyGems)

Same test classes as Cargo archiver, using `RubyGemsArchiver` and rubygems-specific data.

---

## Verification Checklist

Each archiver must pass these verification steps:

### Unit tests
```bash
pytest tests/test_cargo_api.py -v
pytest tests/test_cargo_journal.py -v
pytest tests/test_cargo_archiver.py -v
# (same for nuget, rubygems)
```

### Config loading
```bash
python -c "from config import get_config; c = get_config(); print(c.cargo_archiver.limit)"
python -c "from config import get_config; c = get_config(); print(c.nuget_archiver.limit)"
python -c "from config import get_config; c = get_config(); print(c.rubygems_archiver.limit)"
```

### API connectivity (dry run)
```bash
python -c "from cargo_api import CargoAPI; api = CargoAPI(); crates = api.fetch_top_crates(3); print(f'{len(crates)} crates')""
python -c "from nuget_api import NuGetAPI; api = NuGetAPI(); pkgs = api.fetch_top_packages(3); print(f'{len(pkgs)} packages')"
python -c "from rubygems_api import RubyGemsAPI; api = RubyGemsAPI(); gems = api.fetch_top_gems(3); print(f'{len(gems)} gems')"
```

### Full integration (limit=1, with browser)
```bash
# Each archiver with limit=1 and a valid channel URL
# (requires Chrome with CDP on port 9222)
python -c "
from cargo_archiver import CargoArchiver
a = CargoArchiver('config.yaml')
a.config['cargo_archiver']['limit'] = 1
a.limit = 1
a.load_top_crates()
"
```

---

## File Summary

| # | File | Type | Batch |
|---|------|------|-------|
| 1 | `config/model.py` | modify | 1 |
| 2 | `config/schema.yaml` | modify | 1 |
| 3 | `config.yaml` | modify | 1 |
| 4 | `.env.example` | modify | 1 |
| 5 | `config_utils.py` | modify | 1 |
| 6 | `cargo_api.py` | new | 2 |
| 7 | `cargo_journal.py` | new | 2 |
| 8 | `cargo_archiver.py` | new | 2 |
| 9 | `nuget_api.py` | new | 2 |
| 10 | `nuget_journal.py` | new | 2 |
| 11 | `nuget_archiver.py` | new | 2 |
| 12 | `rubygems_api.py` | new | 2 |
| 13 | `rubygems_journal.py` | new | 2 |
| 14 | `rubygems_archiver.py` | new | 2 |
| 15 | `github_archiver.py` | modify | 3 |
| 16 | `tests/test_cargo_api.py` | new | 4 |
| 17 | `tests/test_cargo_journal.py` | new | 4 |
| 18 | `tests/test_cargo_archiver.py` | new | 4 |
| 19 | `tests/test_nuget_api.py` | new | 4 |
| 20 | `tests/test_nuget_journal.py` | new | 4 |
| 21 | `tests/test_nuget_archiver.py` | new | 4 |
| 22 | `tests/test_rubygems_api.py` | new | 4 |
| 23 | `tests/test_rubygems_journal.py` | new | 4 |
| 24 | `tests/test_rubygems_archiver.py` | new | 4 |

**Total: 24 files (15 new, 5 modified, 9 test files)**
**Total micro-tasks: 24**
