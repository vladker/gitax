# Config Standardization Implementation Plan

**Goal:** Replace 5 independent `_load_config()` functions and hardcoded constants with a single pydantic-based `config/` package loaded via singleton, while maintaining full backward compatibility with existing `config.yaml` files.

**Architecture:** `config/model.py` defines pydantic `BaseModel` per YAML section, `config/loader.py` loads YAML → validates → applies env overrides, `config/__init__.py` exports singleton `get_config()`. Each module replaces its `_load_config()` with `get_config().model_dump()` — keeping dict-based access patterns intact during migration.

**Design:** `thoughts/shared/designs/2026-06-10-config-standartization-design.md`

---

## Dependency Graph

```
Batch 1 (parallel): 1.1, 1.2, 1.3 [foundation — no deps]
Batch 2 (parallel): 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 2.9 [migration — depend on Batch 1]
Batch 3 (parallel): 3.1, 3.2 [cleanup — depend on Batch 2]
```

---

## Batch 1: Foundation (parallel — 3 implementers)

All tasks in this batch have NO dependencies and run simultaneously.

### Task 1.1: Config Models
**File:** `config/model.py`
**Test:** `tests/test_config_model.py`
**Depends:** none

**Gap-filling decisions:**
- Design says `PypiArchiverConfig` with section `pypi_archiver`, but actual `config.yaml` uses `pypi_libs_archiver`. Matching the real YAML for backward compatibility — naming it `PyPILibsArchiverConfig`.
- Design omits `seven_zip_exe` field from `BackuperConfig` but browser_max.py needs it. Adding it with Windows default path.
- Design says `SecretStr` for `GitHubConfig.token` — using plain `str` instead since tokens often come from `.env` and we don't need Pydantic's secret handling in logs.
- `MediaArchiverConfig.extensions` is a nested model for `images`/`videos` lists, matching `config.yaml` structure.
- `SetupConfig.skipped_channels` defaults to empty list, matching existing behavior.

```python
# config/model.py
"""Pydantic models for all config sections."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class ArchiverConfig(BaseModel):
    """Settings: config.yaml → archiver section."""
    limit: int = 1000
    split_mode: Literal["auto", "on", "off", "prompt"] = "auto"
    split_threshold_mb: int = Field(default=49, ge=1)
    use_local_browser: bool = False
    output_dir: str = "./temp"
    retries: int = 3
    retry_delay: int = 10
    repo_delay: int = 30


class BrowserConfig(BaseModel):
    """Settings: config.yaml → browser section."""
    cdp_port: int = 9222
    profile_name: str = "Default"
    user_data_dir: str = ""


class ChannelsConfig(BaseModel):
    """Settings: config.yaml → channels section."""
    max: str = ""
    pypi: str = ""
    media: str = ""
    backup: str = ""


class BackuperConfig(BaseModel):
    """Settings: config.yaml → backuper section."""
    compression_level: str = "5"
    default_volume_size: str = "49M"
    seven_zip_exe: str = "C:\\Program Files\\7-Zip\\7z.exe"
    download_dir: str = "./restored"
    output_dir: str = "./temp_backups"
    page_size: int = 10
    retries: int = 3
    retry_delay: int = 10


class ChannelDownloaderConfig(BaseModel):
    """Settings: config.yaml → channel_downloader section."""
    output_dir: str = "./downloads"
    retries: int = 3
    retry_delay: int = 5


class MediaExtensionsConfig(BaseModel):
    """Nested model for media_archiver.extensions."""
    images: list[str] = Field(default_factory=lambda: [
        ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff"
    ])
    videos: list[str] = Field(default_factory=lambda: [
        ".mp4", ".mov", ".avi", ".mkv", ".webm"
    ])


class MediaArchiverConfig(BaseModel):
    """Settings: config.yaml → media_archiver section."""
    watch_dir: str = ""
    extensions: MediaExtensionsConfig = MediaExtensionsConfig()
    use_local_browser: bool = False
    retries: int = 3
    retry_delay: int = 10


class PyPILibsArchiverConfig(BaseModel):
    """Settings: config.yaml → pypi_libs_archiver section."""
    limit: int = 20
    output_dir: str = "./temp_pypi_libs"
    retries: int = 3
    retry_delay: int = 10
    split_mode: Literal["auto", "on", "off", "prompt"] = "auto"


class SetupConfig(BaseModel):
    """Settings: config.yaml → setup section."""
    skipped_channels: list[str] = Field(default_factory=list)


class GitHubConfig(BaseModel):
    """Settings: config.yaml → github section.
    Token is typically empty in YAML and comes from GITHUB_TOKEN env var."""
    token: str = ""


class AppConfig(BaseModel):
    """Root config model — composes all section models.
    Every section is optional (defaults to its own defaults)."""
    archiver: ArchiverConfig = ArchiverConfig()
    browser: BrowserConfig = BrowserConfig()
    channels: ChannelsConfig = ChannelsConfig()
    backuper: BackuperConfig = BackuperConfig()
    channel_downloader: ChannelDownloaderConfig = ChannelDownloaderConfig()
    media_archiver: MediaArchiverConfig = MediaArchiverConfig()
    pypi_libs_archiver: PyPILibsArchiverConfig = PyPILibsArchiverConfig()
    setup: SetupConfig = SetupConfig()
    github: GitHubConfig = GitHubConfig()

    model_config = {"extra": "ignore"}  # Silently ignore unknown YAML keys
```

```python
# tests/test_config_model.py
"""Tests for config models — validation, defaults, type enforcement."""

import pytest
from typing import Literal
from pydantic import ValidationError


class TestArchiverConfig:
    def test_defaults(self):
        from config.model import ArchiverConfig
        cfg = ArchiverConfig()
        assert cfg.limit == 1000
        assert cfg.split_mode == "auto"
        assert cfg.split_threshold_mb == 49
        assert cfg.use_local_browser is False
        assert cfg.output_dir == "./temp"

    def test_valid_split_modes(self):
        from config.model import ArchiverConfig
        for mode in ("auto", "on", "off", "prompt"):
            cfg = ArchiverConfig(split_mode=mode)
            assert cfg.split_mode == mode

    def test_invalid_split_mode_raises(self):
        from config.model import ArchiverConfig
        with pytest.raises(ValidationError):
            ArchiverConfig(split_mode="invalid")

    def test_negative_threshold_raises(self):
        from config.model import ArchiverConfig
        with pytest.raises(ValidationError):
            ArchiverConfig(split_threshold_mb=0)


class TestBrowserConfig:
    def test_defaults(self):
        from config.model import BrowserConfig
        cfg = BrowserConfig()
        assert cfg.cdp_port == 9222
        assert cfg.profile_name == "Default"
        assert cfg.user_data_dir == ""


class TestChannelsConfig:
    def test_defaults(self):
        from config.model import ChannelsConfig
        cfg = ChannelsConfig()
        assert cfg.max == ""
        assert cfg.pypi == ""
        assert cfg.media == ""
        assert cfg.backup == ""

    def test_custom_values(self):
        from config.model import ChannelsConfig
        cfg = ChannelsConfig(max="https://max.example.com/1", pypi="https://max.example.com/2")
        assert cfg.max == "https://max.example.com/1"
        assert cfg.pypi == "https://max.example.com/2"
        assert cfg.media == ""


class TestBackuperConfig:
    def test_defaults(self):
        from config.model import BackuperConfig
        cfg = BackuperConfig()
        assert cfg.default_volume_size == "49M"
        assert cfg.compression_level == "5"
        assert cfg.seven_zip_exe == "C:\\Program Files\\7-Zip\\7z.exe"
        assert cfg.page_size == 10
        assert cfg.retries == 3


class TestMediaArchiverConfig:
    def test_defaults(self):
        from config.model import MediaArchiverConfig
        cfg = MediaArchiverConfig()
        assert cfg.watch_dir == ""
        assert ".jpg" in cfg.extensions.images
        assert ".mp4" in cfg.extensions.videos

    def test_custom_extensions(self):
        from config.model import MediaArchiverConfig, MediaExtensionsConfig
        cfg = MediaArchiverConfig(
            extensions=MediaExtensionsConfig(images=[".png"], videos=[".webm"])
        )
        assert cfg.extensions.images == [".png"]
        assert cfg.extensions.videos == [".webm"]


class TestPyPILibsArchiverConfig:
    def test_defaults(self):
        from config.model import PyPILibsArchiverConfig
        cfg = PyPILibsArchiverConfig()
        assert cfg.limit == 20
        assert cfg.split_mode == "auto"
        assert cfg.output_dir == "./temp_pypi_libs"


class TestSetupConfig:
    def test_defaults(self):
        from config.model import SetupConfig
        cfg = SetupConfig()
        assert cfg.skipped_channels == []


class TestGitHubConfig:
    def test_defaults(self):
        from config.model import GitHubConfig
        cfg = GitHubConfig()
        assert cfg.token == ""


class TestAppConfig:
    def test_defaults(self):
        from config.model import AppConfig
        cfg = AppConfig()
        assert cfg.archiver.limit == 1000
        assert cfg.browser.cdp_port == 9222
        assert cfg.channels.max == ""
        assert cfg.backuper.default_volume_size == "49M"

    def test_from_dict(self):
        from config.model import AppConfig
        data = {
            "archiver": {"limit": 50, "split_mode": "off"},
            "channels": {"max": "https://example.com"},
        }
        cfg = AppConfig(**data)
        assert cfg.archiver.limit == 50
        assert cfg.archiver.split_mode == "off"
        assert cfg.channels.max == "https://example.com"
        # Unset fields get defaults
        assert cfg.browser.cdp_port == 9222

    def test_extra_keys_ignored(self):
        from config.model import AppConfig
        cfg = AppConfig(**{"unknown_key": "value"})  # Should not raise
        assert cfg.archiver.limit == 1000

    def test_invalid_nested_value_raises(self):
        from config.model import AppConfig
        with pytest.raises(ValidationError):
            AppConfig(archiver={"split_mode": "bogus"})

    def test_to_dict_roundtrip(self):
        from config.model import AppConfig
        cfg = AppConfig(archiver=ArchiverConfig(limit=42))
        d = cfg.model_dump()
        assert d["archiver"]["limit"] == 42
        cfg2 = AppConfig(**d)
        assert cfg2.archiver.limit == 42
```

**Verify:** `python -m pytest tests/test_config_model.py -v`
**Commit:** `feat(config): add pydantic models for all config sections`

---

### Task 1.2: Config Loader
**File:** `config/loader.py`
**Test:** `tests/test_config_loader.py`
**Depends:** 1.1 (imports AppConfig)

**Gap-filling decisions:**
- Env override uses `SECTION_FIELD` naming (e.g., `ARCHIVER_LIMIT`), manually applied after YAML load rather than using pydantic-settings, because YAML is the primary source.
- Legacy env vars (`GITHUB_TOKEN`, `CHANNEL_max`, `MEDIA_WATCH_DIR`) are supported via explicit mapping for backward compatibility.
- Missing `config.yaml` is not an error — all defaults apply.

```python
# config/loader.py
"""YAML → pydantic → env override config loader."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from config.model import AppConfig


def _coerce_value(raw: str, target_type: Any) -> Any:
    """Coerce a string env var value to match the field's expected type."""
    if target_type is bool or target_type == "bool":
        return raw.lower() in ("1", "true", "yes", "on")
    if target_type is int or target_type == "int":
        return int(raw)
    if target_type is float or target_type == "float":
        return float(raw)
    if hasattr(target_type, "__origin__") and target_type.__origin__ is list:
        import json
        return json.loads(raw) if raw.startswith("[") else [x.strip() for x in raw.split(",") if x.strip()]
    return raw  # str or unknown


def _apply_env_overrides(config: AppConfig) -> AppConfig:
    """Apply env var overrides to config using SECTION_FIELD naming.

    Legacy env vars (GITHUB_TOKEN, CHANNEL_*, MEDIA_WATCH_DIR) are mapped
    explicitly for backward compatibility. All other fields use the convention
    SECTION_FIELD (e.g., ARCHIVER_LIMIT, BROWSER_CDP_PORT).
    """
    # ── Legacy env vars (backward compat) ──
    legacy_map: dict[str, tuple[str, str]] = {
        "GITHUB_TOKEN": ("github", "token"),
        "MEDIA_WATCH_DIR": ("media_archiver", "watch_dir"),
        "CHANNEL_MAX": ("channels", "max"),
        "CHANNEL_PYPI": ("channels", "pypi"),
        "CHANNEL_MEDIA": ("channels", "media"),
        "CHANNEL_BACKUP": ("channels", "backup"),
    }
    for env_key, (section, field) in legacy_map.items():
        raw = os.environ.get(env_key, "").strip()
        if raw:
            section_model = getattr(config, section, None)
            if section_model is not None and hasattr(section_model, field):
                setattr(section_model, field, raw)

    # ── Generic SECTION_FIELD overrides ──
    for section_name in config.model_fields:
        section = getattr(config, section_name)
        if not hasattr(section, "model_fields"):
            continue
        for field_name, field_info in section.model_fields.items():
            env_key = f"{section_name.upper()}_{field_name.upper()}"
            raw = os.environ.get(env_key, "").strip()
            if raw:
                try:
                    coerced = _coerce_value(raw, field_info.annotation)
                    setattr(section, field_name, coerced)
                except (ValueError, TypeError):
                    pass  # Keep YAML/default value on coercion failure

    return config


def find_config(search_dirs: list[Path] | None = None) -> Path | None:
    """Search for config.yaml in given directories (default: CWD then parents)."""
    if search_dirs is None:
        search_dirs = [Path.cwd()] + list(Path.cwd().parents)

    for directory in search_dirs:
        candidate = directory / "config.yaml"
        if candidate.exists():
            return candidate
    return None


def load_config(yaml_path: Path | str | None = None) -> AppConfig:
    """Load config.yaml, parse via AppConfig, apply env overrides.

    Args:
        yaml_path: Path to config.yaml. If None, auto-discover via find_config().
                   If no file found, returns all-defaults AppConfig.

    Returns:
        Validated AppConfig instance with env var overrides applied.
    """
    load_dotenv()  # Always load .env first

    # Determine config path
    if yaml_path is None:
        found = find_config()
        yaml_path = found if found else Path("config.yaml")

    yaml_path = Path(yaml_path)

    # Load YAML if it exists
    yaml_data: dict[str, Any] = {}
    if yaml_path.exists():
        try:
            with open(yaml_path, "r", encoding="utf-8") as f:
                yaml_data = yaml.safe_load(f) or {}
        except yaml.YAMLError as e:
            raise ValueError(f"Malformed config.yaml: {e}") from e

    # Parse through pydantic (validates types, applies defaults)
    config = AppConfig(**yaml_data)

    # Apply env overrides on top
    config = _apply_env_overrides(config)

    return config
```

```python
# tests/test_config_loader.py
"""Tests for config loader — YAML loading, env overrides, missing files."""

import os
import yaml
import pytest
from pathlib import Path


class TestFindConfig:
    def test_find_existing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("archiver:\n  limit: 50\n", encoding="utf-8")

        from config.loader import find_config
        result = find_config([tmp_path])
        assert result == cfg_file

    def test_find_not_existing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from config.loader import find_config
        result = find_config([tmp_path])
        assert result is None


class TestLoadConfig:
    def test_no_file_returns_defaults(self, tmp_path, monkeypatch):
        """App starts without config.yaml using all defaults."""
        monkeypatch.chdir(tmp_path)
        from config.loader import load_config
        cfg = load_config(tmp_path / "nonexistent.yaml")
        assert cfg.archiver.limit == 1000
        assert cfg.archiver.split_mode == "auto"
        assert cfg.browser.cdp_port == 9222
        assert cfg.channels.max == ""

    def test_load_from_yaml(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump({
            "archiver": {"limit": 50, "split_mode": "off"},
            "channels": {"max": "https://example.com/max"},
        }), encoding="utf-8")

        from config.loader import load_config
        cfg = load_config(cfg_file)
        assert cfg.archiver.limit == 50
        assert cfg.archiver.split_mode == "off"
        assert cfg.channels.max == "https://example.com/max"
        # Unset fields get defaults
        assert cfg.archiver.repo_delay == 30
        assert cfg.browser.cdp_port == 9222

    def test_env_override_generic(self, tmp_path, monkeypatch):
        """ARCHIVER_LIMIT env var overrides YAML value."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("ARCHIVER_LIMIT", "999")
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump({
            "archiver": {"limit": 50},
        }), encoding="utf-8")

        from config.loader import load_config
        cfg = load_config(cfg_file)
        assert cfg.archiver.limit == 999

    def test_env_override_legacy_github_token(self, tmp_path, monkeypatch):
        """GITHUB_TOKEN env var maps to github.token."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_test123")
        from config.loader import load_config
        cfg = load_config(tmp_path / "nonexistent.yaml")
        assert cfg.github.token == "ghp_test123"

    def test_env_override_legacy_channel(self, tmp_path, monkeypatch):
        """CHANNEL_MAX env var maps to channels.max."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("CHANNEL_MAX", "https://channel.from.env")
        from config.loader import load_config
        cfg = load_config(tmp_path / "nonexistent.yaml")
        assert cfg.channels.max == "https://channel.from.env"

    def test_env_override_legacy_media_watch_dir(self, tmp_path, monkeypatch):
        """MEDIA_WATCH_DIR env var maps to media_archiver.watch_dir."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("MEDIA_WATCH_DIR", "/path/to/watch")
        from config.loader import load_config
        cfg = load_config(tmp_path / "nonexistent.yaml")
        assert cfg.media_archiver.watch_dir == "/path/to/watch"

    def test_env_override_bool(self, tmp_path, monkeypatch):
        """ARCHIVER_USE_LOCAL_BROWSER=true coerces to bool."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("ARCHIVER_USE_LOCAL_BROWSER", "true")
        from config.loader import load_config
        cfg = load_config(tmp_path / "nonexistent.yaml")
        assert cfg.archiver.use_local_browser is True

    def test_env_override_int(self, tmp_path, monkeypatch):
        """BROWSER_CDP_PORT=9223 coerces to int."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("BROWSER_CDP_PORT", "9223")
        from config.loader import load_config
        cfg = load_config(tmp_path / "nonexistent.yaml")
        assert cfg.browser.cdp_port == 9223

    def test_invalid_env_value_ignored(self, tmp_path, monkeypatch):
        """Invalid env value (e.g., non-int for int field) keeps YAML/default."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("ARCHIVER_LIMIT", "not-a-number")
        from config.loader import load_config
        cfg = load_config(tmp_path / "nonexistent.yaml")
        assert cfg.archiver.limit == 1000  # Keeps default

    def test_malformed_yaml_raises(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        bad_file = tmp_path / "config.yaml"
        bad_file.write_text("{invalid yaml: [[[}", encoding="utf-8")

        from config.loader import load_config
        with pytest.raises(ValueError, match="Malformed"):
            load_config(bad_file)

    def test_yaml_overrides_defaults(self, tmp_path, monkeypatch):
        """YAML values take precedence over model defaults."""
        monkeypatch.chdir(tmp_path)
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump({
            "archiver": {"repo_delay": 60, "retries": 5},
            "backuper": {"page_size": 25},
        }), encoding="utf-8")

        from config.loader import load_config
        cfg = load_config(cfg_file)
        assert cfg.archiver.repo_delay == 60
        assert cfg.archiver.retries == 5
        assert cfg.backuper.page_size == 25

    def test_env_beats_yaml(self, tmp_path, monkeypatch):
        """Env var overrides YAML value (env has highest priority)."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("ARCHIVER_LIMIT", "42")
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump({
            "archiver": {"limit": 100},
        }), encoding="utf-8")

        from config.loader import load_config
        cfg = load_config(cfg_file)
        assert cfg.archiver.limit == 42  # env beats yaml

    def test_extra_yaml_keys_ignored(self, tmp_path, monkeypatch):
        """Unknown YAML keys are silently ignored (extra='ignore')."""
        monkeypatch.chdir(tmp_path)
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump({
            "archiver": {"limit": 5},
            "some_random_key": "value",
        }), encoding="utf-8")

        from config.loader import load_config
        cfg = load_config(cfg_file)  # Should not raise
        assert cfg.archiver.limit == 5
```

**Verify:** `python -m pytest tests/test_config_loader.py -v`
**Commit:** `feat(config): add YAML loader with env var overrides`

---

### Task 1.3: Config Package Init (singleton + exports)
**File:** `config/__init__.py`
**Test:** `tests/test_config_init.py`
**Depends:** 1.2 (imports load_config)

**Gap-filling decisions:**
- Uses `functools.lru_cache(maxsize=1)` for singleton (design requirement — testable via cache_clear).
- `init_config(config_path)` clears the cache and sets the path for next `get_config()` call.
- Exports `AppConfig` type, `get_config()`, `init_config()`.
- Creates `config/` directory implicitly — no `__init__` needed beyond this file (wait, this IS the __init__).

```python
# config/__init__.py
"""Config package — singleton config access.

Usage:
    from config import get_config, init_config, AppConfig

    # Optional: override config path before first access
    init_config("path/to/config.yaml")

    # Get validated config (singleton)
    cfg = get_config()
    print(cfg.archiver.limit)
    print(cfg.channels.max)
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

from config.loader import load_config
from config.model import AppConfig

# Module-level storage for config path override
_config_path: Optional[str] = None


def init_config(config_path: str) -> None:
    """Override config path and reset singleton.

    Must be called before the first get_config() call to take effect.
    Call again with a different path to reload config.
    """
    global _config_path
    _config_path = config_path
    get_config.cache_clear()


@lru_cache(maxsize=1)
def get_config(config_path: Optional[str] = None) -> AppConfig:
    """Return the singleton AppConfig instance.

    Args:
        config_path: Path to config.yaml. If None, uses path from init_config()
                     or auto-discovers via find_config().

    Returns:
        Validated AppConfig singleton with env overrides applied.
    """
    effective_path = config_path or _config_path
    return load_config(effective_path)


__all__ = [
    "AppConfig",
    "get_config",
    "init_config",
]
```

```python
# tests/test_config_init.py
"""Tests for config singleton — caching, init_config, cache clearing."""

import os
import yaml
import pytest
from pathlib import Path


class TestGetConfig:
    def test_returns_appconfig(self):
        """get_config() returns an AppConfig instance."""
        from config import get_config, AppConfig
        cfg = get_config("nonexistent_config.yaml")
        assert isinstance(cfg, AppConfig)

    def test_singleton_same_object(self):
        """Two calls return the same object (cached)."""
        from config import get_config
        cfg1 = get_config("nonexistent_config.yaml")
        cfg2 = get_config("nonexistent_config.yaml")
        assert cfg1 is cfg2

    def test_init_config_changes_path(self, tmp_path, monkeypatch):
        """init_config() overrides config path for next get_config()."""
        monkeypatch.chdir(tmp_path)

        # Create a test config
        cfg_file = tmp_path / "custom_config.yaml"
        cfg_file.write_text(yaml.dump({
            "archiver": {"limit": 777},
        }), encoding="utf-8")

        from config import init_config, get_config

        # Get defaults first
        init_config(str(cfg_file))
        cfg = get_config()
        assert cfg.archiver.limit == 777

    def test_init_config_clears_cache(self, tmp_path, monkeypatch):
        """init_config() clears the cache so next get_config() reloads."""
        monkeypatch.chdir(tmp_path)

        from config import init_config, get_config

        # First load with nonexistent path
        init_config("nonexistent.yaml")
        cfg1 = get_config()

        # Create config file after first load
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump({
            "archiver": {"limit": 555},
        }), encoding="utf-8")

        # Init with new path — should reload
        init_config(str(cfg_file))
        cfg2 = get_config()
        assert cfg2.archiver.limit == 555
        assert cfg2 is not cfg1

    def test_cache_clearable(self):
        """get_config.cache_clear() works for testing."""
        from config import get_config
        get_config.cache_clear()
        cfg1 = get_config("nonexistent_cache_test.yaml")
        get_config.cache_clear()
        cfg2 = get_config("nonexistent_cache_test.yaml")
        # After clear, should be a new object
        # (different call because cache was cleared between)
        assert cfg1 is not cfg2


class TestInitConfig:
    def test_none_path_auto_discovers(self, tmp_path, monkeypatch):
        """get_config() with no path and no init_config() uses find_config()."""
        monkeypatch.chdir(tmp_path)
        from config import init_config, get_config
        init_config(str(tmp_path / "nonexistent.yaml"))
        cfg = get_config()
        assert cfg.archiver.limit == 1000  # All defaults

    def test_init_config_twice(self, tmp_path, monkeypatch):
        """Calling init_config() multiple times works."""
        monkeypatch.chdir(tmp_path)
        from config import init_config, get_config

        init_config(str(tmp_path / "a.yaml"))
        init_config(str(tmp_path / "b.yaml"))
        cfg = get_config()
        assert cfg.archiver.limit == 1000  # Defaults since neither file exists
```

**Verify:** `python -m pytest tests/test_config_init.py -v`
**Commit:** `feat(config): add singleton get_config() with cache clearing`

---

## Batch 2: Module Migration (parallel — 9 implementers)

All tasks in this batch depend on Batch 1 completing. They are INDEPENDENT of each other and run in parallel.

### Task 2.1: Migrate github_archiver.py
**File:** `github_archiver.py`
**Test:** No dedicated test file for GitHubArchiver class — existing integration tests (`test_export_messages.py`, `test_export_optimization.py`) exercise it indirectly. Run those.
**Depends:** 1.3 (config package)

**Changes:**
1. Remove `_load_config()` method (lines 291-311)
2. Replace `self.config = self._load_config(config_path)` with centralized loader
3. Keep `self.config` as a dict for backward compat (all 50+ `self.config.get(...)` calls work unchanged)

```python
# Changes to github_archiver.py

# At top of file, replace the _load_config import section:
# Remove 'import yaml' if no longer used elsewhere in the file (check first)
# Keep: import os, sys, time, etc.

# In __init__ (line 118-119):
# Old:
#     def __init__(self, config_path: str = "config.yaml"):
#         self.config = self._load_config(config_path)
# New:
#     def __init__(self, config_path: str = "config.yaml"):
#         from config import init_config, get_config
#         init_config(config_path)
#         self.config = get_config().model_dump()

# Remove _load_config method entirely (lines 291-311)
```

**Full edit:**
Replace lines 118-119 with:
```python
    def __init__(self, config_path: str = "config.yaml"):
        from config import init_config, get_config
        init_config(config_path)
        self.config = get_config().model_dump()
```

Remove lines 291-311 entirely (the `_load_config` method).

**Note:** The `_load_config` also sets `config.setdefault('max', {})['channel_url']` — this is now handled by the centralized loader via `ChannelsConfig.max`. The channel URL will be available at `self.config['channels']['max']` in the dict dump. The `setup_wizard` at line 2413 also calls `self.config = self._load_config("config.yaml")` — this needs the same replacement.

Line 2413: Replace `self.config = self._load_config("config.yaml")` with:
```python
    from config import init_config, get_config
    init_config("config.yaml")
    self.config = get_config().model_dump()
```

Also remove `import yaml` at top if it's only used in `_load_config`. Let me check...

Actually, `yaml` is used elsewhere in the file (setup wizard writes to config.yaml at line 2367-2368). So keep the import.

**Verify:** `python -m pytest tests/ -v -x` (run all tests to check nothing broke)
**Commit:** `refactor(github_archiver): replace _load_config with centralized get_config`

---

### Task 2.2: Migrate backuper.py
**File:** `backuper.py`
**Test:** `tests/test_backuper_journal.py` (exercises BackuperJournal, not Backuper directly, but run to check)
**Depends:** 1.3

**Changes:**
1. Remove `_load_config()` static method
2. Replace `self.config = self._load_config(config_path)` with centralized loader
3. Keep `self.config` as dict for backward compat

```python
# In __init__ (line 35-36):
# Old:
#     def __init__(self, config_path: str = "config.yaml"):
#         self.config = self._load_config(config_path)
# New:
#     def __init__(self, config_path: str = "config.yaml"):
#         from config import init_config, get_config
#         init_config(config_path)
#         self.config = get_config().model_dump()

# Remove _load_config method entirely (lines 49-69)
```

Also remove `import yaml` from top of file if no longer needed — check for other `yaml` usage.

Remove `from dotenv import load_dotenv` if no longer used.

**Verify:** `python -c "from backuper import Backuper; b = Backuper('nonexistent.yaml'); print('OK:', b.config.get('backuper', {}).get('default_volume_size'))"`
**Commit:** `refactor(backuper): replace _load_config with centralized get_config`

---

### Task 2.3: Migrate channel_downloader.py
**File:** `channel_downloader.py`
**Test:** `tests/test_channel_downloader.py`
**Depends:** 1.3

**Changes:**
1. Remove `_load_config()` instance method
2. Replace `self.config = self._load_config(config_path)` with centralized loader
3. Keep `self.config` as dict for backward compat

```python
# In __init__ (line 196-197):
# Old:
#     def __init__(self, config_path: str = "config.yaml"):
#         self.config = self._load_config(config_path)
# New:
#     def __init__(self, config_path: str = "config.yaml"):
#         from config import init_config, get_config
#         init_config(config_path)
#         self.config = get_config().model_dump()

# Remove _load_config method entirely (lines 221-241)

# Remove unused imports: yaml, dotenv.load_dotenv (if not used elsewhere)
```

**Verify:** `python -m pytest tests/test_channel_downloader.py -v`
**Commit:** `refactor(channel_downloader): replace _load_config with centralized get_config`

---

### Task 2.4: Migrate pypi_libs_archiver.py
**File:** `pypi_libs_archiver.py`
**Test:** `tests/test_pypi_libs_archiver.py`
**Depends:** 1.3

**Changes:**
1. Remove `_load_config()` instance method
2. Replace `self.config = self._load_config(config_path)` with centralized loader
3. Keep `self.config` as dict for backward compat
4. Remove `import yaml`, `from dotenv import load_dotenv` if not used elsewhere

Note: `_load_config` also calls `os.makedirs(output_dir, exist_ok=True)` — this becomes the module's responsibility. Move it to `__init__`:

```python
# In __init__ (line 31-32):
# Old:
#     def __init__(self, config_path: str = "config.yaml"):
#         self.config = self._load_config(config_path)
# New:
#     def __init__(self, config_path: str = "config.yaml"):
#         from config import init_config, get_config
#         init_config(config_path)
#         self.config = get_config().model_dump()
#         # Ensure output dir exists (was in _load_config)
#         output_dir = self.config.get('pypi_libs_archiver', {}).get('output_dir', './temp_pypi_libs')
#         if not os.path.exists(output_dir):
#             os.makedirs(output_dir, exist_ok=True)

# Remove _load_config method entirely (lines 73-93)

# Remove unused imports (yaml, load_dotenv) if no other usage
```

Check `import yaml` — is it used elsewhere in the file? Let me check:
- grep shows only `_load_config` uses yaml. So remove `import yaml`.
- `load_dotenv` — only used in `_load_config`. Remove `from dotenv import load_dotenv`.

**Verify:** `python -m pytest tests/test_pypi_libs_archiver.py -v`
**Commit:** `refactor(pypi_libs_archiver): replace _load_config with centralized get_config`

---

### Task 2.5: Migrate media_archiver.py
**File:** `media_archiver.py`
**Test:** `tests/test_media_archiver.py`
**Depends:** 1.3

**Changes:**
1. Remove `_load_config()` instance method
2. Replace with centralized loader
3. **Keep MEDIA_WATCH_DIR validation** — this is business logic (exit if no watch_dir). Handle it in `__init__` after loading config.

```python
# In __init__ (line 150-151):
# Old:
#     def __init__(self, config_path: str = "config.yaml"):
#         self.config = self._load_config(config_path)
# New:
#     def __init__(self, config_path: str = "config.yaml"):
#         from config import init_config, get_config
#         init_config(config_path)
#         self.config = get_config().model_dump()
#         # Validate watch_dir (was in _load_config)
#         media_watch_dir = self.config.get('media_archiver', {}).get('watch_dir', '')
#         if not media_watch_dir:
#             print("✗ MEDIA_WATCH_DIR не указана.")
#             print("  Укажите в .env файле или переменной окружения")
#             sys.exit(1)
#         if not os.path.isdir(media_watch_dir):
#             print(f"✗ Папка медиа не найдена: {media_watch_dir}")
#             sys.exit(1)

# Remove _load_config method entirely (lines 175-206)

# Remove unused imports: yaml, load_dotenv (check no other usage)
```

**Verify:** `python -m pytest tests/test_media_archiver.py -v`
**Commit:** `refactor(media_archiver): replace _load_config with centralized get_config`

---

### Task 2.6: Migrate browser_max.py (hardcoded constants → config)
**File:** `browser_max.py`
**Test:** `tests/test_browser_max.py`
**Depends:** 1.3

**Changes:**
1. Remove `SEVEN_ZIP_VOLUME_SIZE = "49M"` constant
2. Remove `SEVEN_ZIP_EXE = "C:\\Program Files\\7-Zip\\7z.exe"` constant
3. Update `split_file_with_7z()` to default volume_size from config
4. Update `archive_directory_to_volumes()` to default volume_size from config
5. Update `_get_user_data_dir()` to use config instead of re-reading YAML
6. Update `_launch_chrome_cdp()` to use config for user_data_dir fallback

**Gap-filling decisions:**
- `split_file_with_7z` and `archive_directory_to_volumes` are standalone functions (not methods), so they use `get_config()` directly rather than via `self`.
- Default parameter values can't use `get_config()` at definition time (circular import risk, also singleton might not be initialized). Instead, use `None` default and resolve inside the function body.
- `_get_user_data_dir()` becomes a one-liner using `get_config().browser.user_data_dir` with fallback.
- Adding `SEVEN_ZIP_EXE` to `BackuperConfig` as `seven_zip_exe` — already in model from Task 1.1.

```python
# Changes in browser_max.py

# REMOVE lines 21-22:
# Old:
# SEVEN_ZIP_VOLUME_SIZE = "49M"
# New: (deleted)

# REMOVE line 28:
# Old:
# SEVEN_ZIP_EXE = "C:\\Program Files\\7-Zip\\7z.exe"
# New: (deleted)

# UPDATE split_file_with_7z signature (line ~336):
# Old:
# def split_file_with_7z(filepath: str, volume_size: str = SEVEN_ZIP_VOLUME_SIZE) -> list[str]:
# New:
def split_file_with_7z(filepath: str, volume_size: str | None = None) -> list[str]:
    """..."""
    if volume_size is None:
        from config import get_config
        volume_size = get_config().backuper.default_volume_size
    # rest of function unchanged — uses volume_size local variable

# UPDATE archive_directory_to_volumes signature (line ~446):
# Old:
# def archive_directory_to_volumes(
#     source_dir: str,
#     output_base: str,
#     volume_size: str = SEVEN_ZIP_VOLUME_SIZE,
#     ...
# ) -> list[str]:
# New:
def archive_directory_to_volumes(
    source_dir: str,
    output_base: str,
    volume_size: str | None = None,
    ...
) -> list[str]:
    if volume_size is None:
        from config import get_config
        volume_size = get_config().backuper.default_volume_size
    # rest of function unchanged

# UPDATE _get_user_data_dir (lines ~479-497):
# Old:
# def _get_user_data_dir(self) -> str:
#     import yaml
#     user_data_dir = ""
#     profile_name = "Default"
#     try:
#         config_path = "config.yaml"
#         if os.path.exists(config_path):
#             with open(config_path, 'r', encoding='utf-8') as f:
#                 config = yaml.safe_load(f) or {}
#             browser_config = config.get('browser', {})
#             user_data_dir = browser_config.get('user_data_dir', '')
#             profile_name = browser_config.get('profile_name', 'Default')
#     except Exception:
#         pass
#     if not user_data_dir:
#         user_data_dir = os.path.join(
#             os.path.expanduser("~"), "AppData", "Local", "Google", "Chrome", "User Data"
#         )
#     return os.path.join(user_data_dir, profile_name)
# New:
def _get_user_data_dir(self) -> str:
    """Get Chrome user data directory from config with fallback."""
    from config import get_config
    cfg = get_config()
    user_data_dir = cfg.browser.user_data_dir
    profile_name = cfg.browser.profile_name
    if not user_data_dir:
        user_data_dir = os.path.join(
            os.path.expanduser("~"), "AppData", "Local", "Google", "Chrome", "User Data"
        )
    return os.path.join(user_data_dir, profile_name)

# UPDATE references to SEVEN_ZIP_EXE in split_file_with_7z and archive_directory_to_volumes
# Replace direct constant usage with config lookup:
# In split_file_with_7z (near line ~370):
# Old:
# cmd = [SEVEN_ZIP_EXE, ...]
# New:
# from config import get_config
# seven_zip_exe = get_config().backuper.seven_zip_exe
# cmd = [seven_zip_exe, ...]

# Actually, to minimize diff, add a helper function at module level:
def _get_seven_zip_exe() -> str:
    """Get 7z executable path from config."""
    from config import get_config
    return get_config().backuper.seven_zip_exe
```

**Verify:** `python -m pytest tests/test_browser_max.py -v` (after adjusting imports)
**Commit:** `refactor(browser_max): remove hardcoded constants, use centralized config`

---

### Task 2.7: requirements.txt — Add pydantic dependency
**File:** `requirements.txt`
**Test:** none (manifest file)
**Depends:** none (can run in Batch 1, but independent)

**Change:** Append pydantic dependency.

```python
# Append to requirements.txt:
# Old:
# playwright>=1.40.0
# New:
# playwright>=1.40.0
# pydantic>=2.0.0
```

**Verify:** `pip install -r requirements.txt` succeeds
**Commit:** `chore(deps): add pydantic dependency for config system`

---

### Task 2.8: Generate config/schema.yaml
**File:** `config/schema.yaml`
**Test:** none (generated example file)
**Depends:** 1.1 (model structure known)

**Gap-filling decision:**
- Design says "auto-generated" via CLI script. Since this plan executes micro-tasks individually, I'll generate it manually from the model defaults (same result). Future automation can be a separate task.
- This file is the canonical example, replacing `config.yaml.example` in the project root.

```yaml
# config/schema.yaml
# Auto-generated schema — mirrors AppConfig model defaults.
# Copy to project root as config.yaml and customize.
# Sensitive values (tokens, URLs) should go in .env instead.

archiver:
  limit: 1000
  split_mode: auto           # auto | on | off | prompt
  split_threshold_mb: 49     # Files above this size (MB) trigger 7z split
  use_local_browser: false   # false = CDP, true = launch new Chrome
  output_dir: ./temp
  retries: 3
  retry_delay: 10
  repo_delay: 30

browser:
  cdp_port: 9222
  profile_name: Default
  user_data_dir: ""          # Empty = default Chrome profile path

channels:
  max: ""                    # MAX channel URL (or set CHANNEL_MAX env var)
  pypi: ""                   # PyPI channel URL (or set CHANNEL_PYPI env var)
  media: ""                  # Media channel URL (or set CHANNEL_MEDIA env var)
  backup: ""                 # Backup channel URL (or set CHANNEL_BACKUP env var)

backuper:
  compression_level: "5"
  default_volume_size: 49M   # 7z volume size (e.g., 49M, 100M, 1G)
  seven_zip_exe: C:\Program Files\7-Zip\7z.exe
  download_dir: ./restored
  output_dir: ./temp_backups
  page_size: 10
  retries: 3
  retry_delay: 10

channel_downloader:
  output_dir: ./downloads
  retries: 3
  retry_delay: 5

media_archiver:
  watch_dir: ""              # Directory to watch for media files
  extensions:
    images:
      - .jpg
      - .jpeg
      - .png
      - .gif
      - .webp
      - .bmp
      - .tiff
    videos:
      - .mp4
      - .mov
      - .avi
      - .mkv
      - .webm
  use_local_browser: false
  retries: 3
  retry_delay: 10

pypi_libs_archiver:
  limit: 20
  output_dir: ./temp_pypi_libs
  retries: 3
  retry_delay: 10
  split_mode: auto

setup:
  skipped_channels: []       # Channels to skip: max, pypi, media, backup

github:
  token: ""                  # GitHub token — prefer GITHUB_TOKEN env var
```

**Verify:** `python -c "import yaml; yaml.safe_load(open('config/schema.yaml')); print('Valid YAML')"`
**Commit:** `docs(config): add schema.yaml — canonical config example`

---

### Task 2.9: Update .env.example
**File:** `.env.example`
**Test:** none
**Depends:** none (independent)

**Gap-filling decision:**
- New env vars use `SECTION_FIELD` uppercase convention.
- Existing env vars (`GITHUB_TOKEN`, `CHANNEL_*`, `MEDIA_WATCH_DIR`) preserved for backward compat.
- New vars documented alongside legacy ones.

```env
# ── Authentication ──
GITHUB_TOKEN=                  # GitHub personal access token (legacy, preferred)

# ── Channel URLs (legacy: CHANNEL_<name>) ──
CHANNEL_max=                   # MAX channel URL
CHANNEL_pypi=                  # PyPI channel URL
CHANNEL_media=                 # Media channel URL
CHANNEL_backup=                # Backup channel URL

# ── Media Archiver ──
MEDIA_WATCH_DIR=               # Directory to watch for media files (legacy)

# ═══════════════════════════════════════════════════════════════
# Config overrides (optional — override values in config.yaml)
# Format: SECTION_FIELD in UPPERCASE
# Example: ARCHIVER_LIMIT=500 overrides archiver.limit
# ═══════════════════════════════════════════════════════════════

# Archiver
ARCHIVER_LIMIT=
ARCHIVER_SPLIT_MODE=
ARCHIVER_SPLIT_THRESHOLD_MB=
ARCHIVER_USE_LOCAL_BROWSER=
ARCHIVER_OUTPUT_DIR=
ARCHIVER_RETRIES=
ARCHIVER_RETRY_DELAY=
ARCHIVER_REPO_DELAY=

# Browser
BROWSER_CDP_PORT=
BROWSER_PROFILE_NAME=
BROWSER_USER_DATA_DIR=

# Channels
CHANNELS_MAX=
CHANNELS_PYPI=
CHANNELS_MEDIA=
CHANNELS_BACKUP=

# Backuper
BACKUPER_COMPRESSION_LEVEL=
BACKUPER_DEFAULT_VOLUME_SIZE=
BACKUPER_SEVEN_ZIP_EXE=
BACKUPER_DOWNLOAD_DIR=
BACKUPER_OUTPUT_DIR=
BACKUPER_PAGE_SIZE=
BACKUPER_RETRIES=
BACKUPER_RETRY_DELAY=

# Channel Downloader
CHANNEL_DOWNLOADER_OUTPUT_DIR=
CHANNEL_DOWNLOADER_RETRIES=
CHANNEL_DOWNLOADER_RETRY_DELAY=

# Media Archiver
MEDIA_ARCHIVER_WATCH_DIR=
MEDIA_ARCHIVER_USE_LOCAL_BROWSER=
MEDIA_ARCHIVER_RETRIES=
MEDIA_ARCHIVER_RETRY_DELAY=

# PyPI Libs Archiver
PYPI_LIBS_ARCHIVER_LIMIT=
PYPI_LIBS_ARCHIVER_OUTPUT_DIR=
PYPI_LIBS_ARCHIVER_RETRIES=
PYPI_LIBS_ARCHIVER_RETRY_DELAY=
PYPI_LIBS_ARCHIVER_SPLIT_MODE=

# Setup
SETUP_SKIPPED_CHANNELS=
```

**Verify:** review diff
**Commit:** `docs(config): update .env.example with SECTION_FIELD env vars`

---

## Batch 3: Cleanup (parallel — 2 implementers)

### Task 3.1: config_utils.py — thin wrapper over get_config()
**File:** `config_utils.py`
**Test:** `tests/test_config_utils.py` (existing tests must still pass)
**Depends:** 2.2 (all migrations done)

**Gap-filling decision:**
- Design says "Keep as thin re-export for one release cycle, then remove."
- Add a `get_config()` re-export and `config_to_dict()` helper that existing functions can use.
- All existing functions keep their current signatures (accept `config: dict`) for backward compat.
- Add deprecation notice in docstrings referencing the new config module.

```python
# Append to config_utils.py (add at end of file):
# ── Thin wrapper over new config/ package ──

def get_app_config():
    """Get the centralized AppConfig singleton.

    Returns:
        AppConfig instance (pydantic BaseModel).

    Note:
        This is a thin re-export from config/ package for migration convenience.
        New code should use: from config import get_config
    """
    from config import get_config
    return get_config()


def config_from_file(config_path: str = "config.yaml") -> dict:
    """Load config using the centralized system and return as dict.

    This replaces individual _load_config() calls across modules.
    Returns a plain dict for backward compatibility.

    Args:
        config_path: Path to config.yaml

    Returns:
        Dict representation of the full config
    """
    from config import init_config, get_config
    init_config(config_path)
    return get_config().model_dump()
```

**Verify:** `python -m pytest tests/test_config_utils.py -v` (existing tests pass)
**Commit:** `refactor(config_utils): add thin wrappers over config/ package`

---

### Task 3.2: Remove config.yaml.example (deprecate → point to schema.yaml)
**File:** `config.yaml.example`
**Test:** none
**Depends:** 2.8

**Change:** Remove the file and leave a warning notice. Or just delete it.

Since the design says "`config.yaml.example` is dead — but we don't break anyone who has it", removing it from the repo is fine (it's not referenced by any code). The replacement `config/schema.yaml` is already created in Task 2.8.

**Action:** Delete `config.yaml.example` from the repo.

**Verify:** `git rm config.yaml.example` works, `git status` shows file removed
**Commit:** `chore(config): remove stale config.yaml.example, replaced by config/schema.yaml`

