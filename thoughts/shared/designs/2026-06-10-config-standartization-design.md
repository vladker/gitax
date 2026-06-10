---
date: 2026-06-10
topic: "Config Standardization"
status: validated
---

## Problem Statement

The project has **no standardized config system** — config loading is duplicated across 5+ modules, `config.yaml.example` is completely out of sync with the actual config schema, hardcoded constants duplicate YAML values, and there's zero type validation. This makes onboarding confusing and maintenance fragile.

## Constraints

- **`config.yaml` stays** — it's the primary config file, not going anywhere
- **`.env` stays** — for secrets (tokens, URLs), must override YAML values
- **Backward compatibility** — existing `config.yaml` files must work without changes
- **No runtime breakage for missing optional config** — app should start without `config.yaml` (using defaults + `.env`)
- **`config.yaml.example`** is dead — but we don't break anyone who has it

## Approach

Replace the **5 independent `_load_config()` functions** with a **single pydantic-based config model** loaded once via singleton. Pydantic provides type validation, nested models for sections, and built-in `.env` support through `pydantic-settings`.

**Rejected alternatives:**

- **Plain dataclasses** — no validation, no env override mechanism, fragile
- **Dict-based loader (status quo but centralized)** — no type safety, still duplicating key paths as strings everywhere
- **ydantic.BaseSettings subclass** — over-engineered for this; we need YAML as primary source, not env

## Architecture

```
config/
  __init__.py       # get_config(), init_config(config_path), AppConfig
  model.py          # Pydantic models for each section
  loader.py         # load_config() — YAML → pydantic → env overrides
  schema.yaml       # Auto-generated example (replaces config.yaml.example)
```

### Config Priority Chain

```
Defaults (model) < config.yaml < .env / env vars
```

- **Defaults** are defined in each pydantic model field
- **config.yaml** overrides matching fields
- **.env** env vars have highest priority, overriding both defaults and YAML

### Key Design Decisions

**1. `pydantic.BaseModel` with nested models per section**

Each config section (`archiver`, `browser`, `channels`, `backuper`, etc.) gets its own model. The root `AppConfig` composes them. This mirrors the YAML structure exactly.

```python
class ArchiverConfig(BaseModel):
    limit: int = 1000
    split_mode: Literal["auto", "on", "off", "prompt"] = "auto"
    split_threshold_mb: int = Field(default=49, ge=1)
    use_local_browser: bool = False
    output_dir: Path = Path("./temp")
    # ...

class AppConfig(BaseModel):
    archiver: ArchiverConfig = ArchiverConfig()
    browser: BrowserConfig = BrowserConfig()
    channels: ChannelsConfig = ChannelsConfig()
    # ...
```

**2. YAML is primary, env is override**

Unlike typical `pydantic-settings` where env vars are primary — here we load YAML explicitly, parse it as `AppConfig`, then apply env overrides on top. This is intentional: `config.yaml` is the source of truth for non-secret config, `.env` just overrides secrets and URLs.

**3. Singleton via `get_config()`**

```python
from functools import lru_cache

@lru_cache(maxsize=1)
def get_config(config_path: str = "config.yaml") -> AppConfig:
    ...
```

Called everywhere as `from config import get_config; cfg = get_config()`.

**4. Env var naming convention**

Pydantic-settings style: `ARCHIVER_LIMIT`, `ARCHIVER_SPLIT_MODE`, `CHANNELS_MAX`, etc. This is documented in `.env.example`.

**5. `config/schema.yaml` replaces `config.yaml.example`**

`config.yaml.example` is removed. `config/schema.yaml` is a generated example — or manually maintained but always in sync because it mirrors `AppConfig`. It's in `config/` to signal it belongs to the config system, not the project root.

**6. `Literal` type for enum fields**

`split_mode` is typed as `Literal["auto", "on", "off", "prompt"]`. Any other value raises `ValidationError` at startup. Catches typos immediately.

## Components

### `config/model.py`

Every section from `config.yaml` mapped as a pydantic model:

| Model | Section | Key fields |
|---|---|---|
| `ArchiverConfig` | `archiver` | limit, split_mode, split_threshold_mb, use_local_browser, output_dir, retries, retry_delay, repo_delay |
| `BrowserConfig` | `browser` | cdp_port, profile_name, user_data_dir |
| `ChannelsConfig` | `channels` | max, pypi, media, backup |
| `BackuperConfig` | `backuper` | compression_level, default_volume_size, download_dir, output_dir, page_size, retries, retry_delay |
| `ChannelDownloaderConfig` | `channel_downloader` | output_dir, retries, retry_delay |
| `MediaArchiverConfig` | `media_archiver` | watch_dir, extensions (nested), use_local_browser, retries, retry_delay |
| `PypiArchiverConfig` | `pypi_archiver` | limit, output_dir, retries, retry_delay, split_mode |
| `SetupConfig` | `setup` | skipped_channels |
| `GitHubConfig` | `github` | token (SecretStr) |
| `AppConfig` | root | All of the above as optional fields |

### `config/loader.py`

Functions:
- `load_config(yaml_path: Path = Path("config.yaml")) -> AppConfig` — loads YAML, validates via pydantic, applies env overrides
- `find_config() -> Path` — searches for config.yaml in CWD, then parent dirs

### `config/__init__.py`

Exports:
- `get_config(config_path: str = "config.yaml") -> AppConfig`
- `init_config(config_path: str)` — override config path before first `get_config()` call

### `browser_max.py` changes

- Remove `SEVEN_ZIP_VOLUME_SIZE = "49M"` constant
- Remove `SEVEN_ZIP_EXE` hardcoded path
- Both come from `get_config().backuper.default_volume_size` and `get_config().backuper.seven_zip_exe`
- `browser.user_data_dir` comes from config, not from re-reading YAML directly

## Data Flow

```
Startup:
  1. main.py / archiver entrypoint
  2. init_config("config.yaml")   ← optionally override path
  3. load_config() reads config.yaml → validates via AppConfig
  4. pydantic-settings applies .env overrides
  5. get_config() → singleton instance
  6. Every module: from config import get_config → cfg = get_config()

Module access:
  get_config().channels.max        → URL for max
  get_config().archiver.split_mode → split mode
  get_config().backuper.default_volume_size → 7z volume size
```

## Error Handling

| Scenario | Behavior |
|---|---|
| No `config.yaml` | Uses all defaults + `.env`. App starts fine. |
| Invalid value (e.g. `split_mode: wat`) | `ValidationError` on startup with clear message |
| Missing `.env` | Optional. Those fields keep YAML or default values. |
| Malformed YAML | `yaml.YAMLError` → wrapped in friendlier error |
| Extra unknown keys in YAML | pydantic ignores by default (or warns) |

## Migration Plan

**Phase 1 — Foundation (no breakage)**
1. Create `config/` package with models and loader
2. `get_config()` returns a validated `AppConfig` singleton
3. Old `_load_config()` and `config_utils.py` continue working unchanged

**Phase 2 — Adoption (module by module)**
4. `github_archiver.py` → replace `_load_config()` with `get_config()`
5. `pypi_archiver.py` / `pypi_libs_archiver.py` → same
6. `backuper.py` → same
7. `channel_downloader.py` → same
8. `media_archiver.py` → same
9. `browser_max.py` → remove `SEVEN_ZIP_VOLUME_SIZE`, use config

**Phase 3 — Cleanup**
10. Remove `config.yaml.example` (replace with `config/schema.yaml`)
11. Update `.env.example`
12. Remove or re-export `config_utils.py`

## Testing Strategy

- **Unit: model validation** — `AppConfig(yaml_data)` with valid/invalid data checks `ValidationError`
- **Unit: env override** — set env var, load config, verify override
- **Unit: singleton** — `get_config()` returns same object
- **Regression: old config_utils** — existing calls still resolve correctly
- **Integration: full load** — load actual `config.yaml`, verify all sections populated

## Open Questions

- [ ] Should `config/schema.yaml` be auto-generated from model or manually maintained?
  - **Decision:** Auto-generate via CLI script `config/generate_schema.py` that serializes `AppConfig` to YAML. Run on model changes.
- [ ] What about `config.yaml` in production vs development?
  - **Decision:** `init_config()` accepts path. Default is `config.yaml` in CWD. Dev setups can use env var `GITAX_CONFIG_PATH`.
- [ ] Do we keep `config_utils.py` as thin wrapper or remove entirely?
  - **Decision:** Keep as thin re-export for one release cycle, then remove.
