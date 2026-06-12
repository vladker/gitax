# Multi-Ecosystem Archiver — Phase 1 Design

date: 2026-06-12
topic: "Phase 1: Cargo, NuGet, RubyGems archivers"
status: validated

---

## Problem Statement

GitHub Archiver (gitax) currently supports only GitHub repos and PyPI packages. We need to extend it to cover other package ecosystems so users can archive popular libraries across the entire development landscape — not just Python.

**Phase 1 scope:** Three archivers with the simplest implementation profile — one file per package, straightforward APIs, predictable download URLs.

## Constraints

- **No new external dependencies** — only `pyyaml`, `requests`, `playwright`, `pydantic`, `python-dotenv`, `tqdm`
- **Follow PyPI archiver pattern** — three files per archiver: `*_api.py`, `*_archiver.py`, `*_journal.py`
- **Share infrastructure** — `browser_max.py`, `browser_init.py`, `shared_journal.py`, `signal_handler.py`
- **Each archiver gets its own MAX channel** — configured via `channels.*` in config.yaml
- **Split mode configurable per archiver** — large files may need 7z splitting
- **Journal deduplication** — never re-upload the same `(name, version)` pair
- **Graceful shutdown** — SignalHandler cleanup on every archiver

## Approach

### Why Cargo, NuGet, RubyGems first?

All three share these characteristics:
1. **Single download artifact** — `.crate`, `.nupkg`, `.gem` (one file per package)
2. **Official REST API** with version info and download URLs
3. **No build-variant complexity** — unlike Conda (multiple builds) or Conan (multiple packages per version)
4. **Predictable URL patterns** — download URL is derived from name + version, no extra lookups

### Architecture per archiver

```
{ecosystem}_api.py       → fetches top packages list + package metadata + downloads files
{ecosystem}_archiver.py  → CLI menu, orchestration, upload to MAX
{ecosystem}_journal.py   → JSON-backed deduplication journal
```

Each archiver is a standalone script (`if __name__ == "__main__"`) AND accessible from the main `github_archiver.py` menu.

## Architecture

### Shared components (already exist)

| Component | Role |
|-----------|------|
| `browser_max.py` | Playwright CDP automation for MAX uploads |
| `browser_init.py` | `BrowserInitMixin` — channel URL resolution, browser connect |
| `shared_journal.py` | `BaseJournal` — atomic JSON writes, add/exists/get_all |
| `signal_handler.py` | Graceful SIGINT/SIGTERM handling |
| `config/` | Pydantic config models + YAML loader + env overrides |
| `config_utils.py` | `get_channel_url()`, `get_split_mode()` helpers |
| `logging_config.py` | Unified logging to `archiver.log` |
| `utils.py` | `format_file_size()` |

### New components — Phase 1

```
gitax/
├── cargo_api.py
├── cargo_archiver.py
├── cargo_journal.py
├── nuget_api.py
├── nuget_archiver.py
├── nuget_journal.py
├── rubygems_api.py
├── rubygems_archiver.py
├── rubygems_journal.py
└── config/model.py  ← new Pydantic models added
```

## Components

### 1. Cargo Archiver (Rust / crates.io)

**Data source:** `https://crates.io/api/v1/crates?sort=downloads&order=desc&per_page=100`

**API layer (`cargo_api.py`):**
- `fetch_top_packages(limit)` → list of `{name, latest_version, downloads, description}`
- `download_package(name, version)` → downloads `{name}-{version}.crate` to temp dir
- In-memory cache for API responses (same pattern as `pypi_api.py`)

**Download URL:** `https://crates.io/api/v1/crates/{name}/{version}/download`

**Artifact:** Single `.crate` file per package (typically 50KB - 5MB)

**Journal dedup key:** `(name, version)`

**Message format:**
```
🦀 serde 1.0.204
📝 A generic serialization/deserialization framework
📥 Downloads: 215,678,901
📦 serde-1.0.204.crate (145 KB)
🔗 https://crates.io/crates/serde
```

**Config section:**
```yaml
cargo_archiver:
  limit: 500
  output_dir: ./temp_cargo
  retries: 3
  retry_delay: 10
  split_mode: 'off'
```

---

### 2. NuGet Archiver (.NET / nuget.org)

**Data source:** NuGet v3 API — `https://api.nuget.org/v3/registration5-gz-semver2/{package}/index.json`

**Top packages list:** Parse from `https://www.nuget.org/stats/packages` or use a curated list of most-downloaded packages via NuGet API stats endpoint.

**API layer (`nuget_api.py`):**
- `fetch_top_packages(limit)` → list of `{id, version, downloads, summary}`
- `get_package_info(package_id)` → metadata from NuGet v3 registration API
- `download_package(id, version)` → downloads `{id}.{version}.nupkg` to temp dir

**Download URL:** `https://globalcdn.nuget.org/packages/{id}.{version}.nupkg`

**Artifact:** Single `.nupkg` file per package (typically 100KB - 20MB)

**Journal dedup key:** `(id, version)`

**Message format:**
```
🟣 Newtonsoft.Json 13.0.3
📝 Popular JSON framework for .NET
📥 Downloads: 182,450,123
📦 Newtonsoft.Json.13.0.3.nupkg (330 KB)
🔗 https://www.nuget.org/packages/Newtonsoft.Json/13.0.3
```

**Config section:**
```yaml
nuget_archiver:
  limit: 500
  output_dir: ./temp_nuget
  retries: 3
  retry_delay: 10
  split_mode: 'off'
```

---

### 3. RubyGems Archiver (Ruby / rubygems.org)

**Data source:** RubyGems API — `https://rubygems.org/api/v1/gems/{name}.json`

**Top gems list:** Use `https://rubygems.org/stats` page or a curated popular-gems list. Alternative: BestGems API for ranked gems.

**API layer (`rubygems_api.py`):**
- `fetch_top_packages(limit)` → list of `{name, version, downloads, description}`
- `get_package_info(name)` → metadata from RubyGems JSON API
- `download_package(name, version)` → downloads `{name}-{version}.gem` to temp dir

**Download URL:** `https://rubygems.org/downloads/{name}-{version}.gem`

**Artifact:** Single `.gem` file per package (typically 50KB - 10MB)

**Journal dedup key:** `(name, version)`

**Message format:**
```
💎 rails 7.1.3.4
📝 Full-stack web application framework
📥 Downloads: 98,234,567
📦 rails-7.1.3.4.gem (245 KB)
🔗 https://rubygems.org/gems/rails
```

**Config section:**
```yaml
rubygems_archiver:
  limit: 500
  output_dir: ./temp_rubygems
  retries: 3
  retry_delay: 10
  split_mode: 'off'
```

## Data Flow

```
┌─────────────────────────────────────────────────┐
│  Main Menu (github_archiver.py)                  │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐      │
│  │ Cargo    │  │ NuGet    │  │ RubyGems │      │
│  │ Menu     │  │ Menu     │  │ Menu     │      │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘      │
└───────┼─────────────┼─────────────┼─────────────┘
        │             │             │
        ▼             ▼             ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│ *_api.py     │ │ *_api.py     │ │ *_api.py     │
│ fetch_top()  │ │ fetch_top()  │ │ fetch_top()  │
│ download()   │ │ download()   │ │ download()   │
└──────┬───────┘ └──────┬───────┘ └──────┬───────┘
       │                │                │
       ▼                ▼                ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│ *_journal.py │ │ *_journal.py │ │ *_journal.py │
│ exists?      │ │ exists?      │ │ exists?      │
│ add entry    │ │ add entry    │ │ add entry    │
└──────┬───────┘ └──────┬───────┘ └──────┬───────┘
       │                │                │
       ▼                ▼                ▼
┌─────────────────────────────────────────────────┐
│  browser_max.py (shared)                         │
│  send_message_with_file(                         │
│    text=message,                                 │
│    filepath=artifact,                            │
│    expected_extensions=['.crate' / '.nupkg' /    │
│                          '.gem']                  │
│  )                                               │
└─────────────────────────────────────────────────┘
       │
       ▼
  ┌──────────┐
  │  MAX     │
  │  Channel │
  └──────────┘
```

## Error Handling Strategy

- **API failures:** Retry with exponential backoff (inherited `retry` module, 3 attempts default)
- **Download failures:** Skip package, mark as `status: "failed"` in journal, continue to next
- **Upload failures:** Retry per `retries` config, then mark journal entry as failed
- **Browser disconnect:** Reconnect via `_ensure_browser_connected()`, retry upload
- **Graceful shutdown:** SignalHandler pauses processing, completes current upload, cleans temp files
- **Network timeout:** 30s timeout on all HTTP requests (same as PyPI archiver)

## Testing Strategy

For each archiver:
1. **API tests** — mock HTTP responses, verify `fetch_top_packages()` returns correct structure
2. **Journal tests** — test dedup logic, add/exists/clear operations
3. **Integration smoke test** — run with `limit: 1`, verify one package downloads and uploads successfully
4. **Config validation** — test Pydantic model defaults and env overrides

Tests follow existing patterns in `tests/` directory (see `test_pypi_*.py`).

## Config Model Changes

Add to `config/model.py`:

```python
CargoArchiverConfig(limit: int = 500, output_dir: str = "./temp_cargo", ...)
NuGetArchiverConfig(limit: int = 500, output_dir: str = "./temp_nuget", ...)
RubyGemsArchiverConfig(limit: int = 500, output_dir: str = "./temp_rubygems", ...)
```

Add to `AppConfig` composition. Add corresponding YAML sections to `config.yaml` and `config/schema.yaml`.

Add channel URLs to `channels` section: `cargo`, `nuget`, `rubygems`.

## Open Questions

1. **Top packages source for NuGet** — NuGet doesn't have a clean "top N by downloads" API endpoint. Options: parse stats page, use third-party API, or maintain a curated list. **Recommendation:** Start with a curated list of ~200 most popular packages, expand later.
2. **Top gems source for RubyGems** — Similar situation. **Recommendation:** Use BestGems API or maintain a curated list.
3. **Sync mode** — Should each archiver support "check for newer versions" like PyPI's sync? **Recommendation:** Yes, follow PyPI pattern — Phase 1 includes basic sync.
4. **Parallel upload** — Should these archivers support `ParallelGroupUploader`? **Recommendation:** Not in Phase 1. Sequential upload is simpler and sufficient for initial rollout.
