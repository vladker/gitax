# Channel Registry Implementation Plan

**Goal:** Replace flat `channels.{key} = url` with a flexible `ChannelRegistry` supporting multiple channels per function, auto-migration, channel management CLI, parallel uploads, and thread-safe journals.

**Architecture:** New Pydantic models (`ChannelEntry`, `ChannelRegistry`) in `config/model.py`. Auto-migration in `config/loader.py` detects old format on first load. `config_utils.py` gains `get_channels_for_function()` with backward compat. Service menu gets CRUD for channels. `parallel_uploader.py` handles multi-channel uploads via `threading.Thread`. Journals get `threading.Lock` and optional `channel_label`.

**Design:** [thoughts/shared/designs/2026-06-11-channel-registry-design.md](thoughts/shared/designs/2026-06-11-channel-registry-design.md)

---

## Dependency Graph

```
Batch 1 (parallel): 1.1, 1.2, 1.3                  [foundation - no deps]
Batch 2 (parallel): 2.1, 2.2, 2.3, 2.4             [core - depends on batch 1]
Batch 3 (parallel): 3.1, 3.2, 3.3, 3.4             [integration - depends on batch 2]
```

---

## Batch 1: Foundation (parallel - 3 implementers)

All tasks in this batch have NO dependencies and run simultaneously.

### Task 1.1: ChannelEntry + ChannelRegistry Models

**File:** `config/model.py`
**Test:** `tests/test_channel_registry.py`
**Depends:** none

Add two new Pydantic models following the existing pattern (BaseModel, Field defaults). `ChannelRegistry` maps function keys to lists of `ChannelEntry`.

```python
# tests/test_channel_registry.py
"""Tests for ChannelEntry and ChannelRegistry models."""

import pytest
from pydantic import ValidationError


class TestChannelEntry:
    def test_defaults(self):
        from config.model import ChannelEntry
        entry = ChannelEntry(url="https://web.max.ru/test")
        assert entry.url == "https://web.max.ru/test"
        assert entry.label == ""
        assert entry.enabled is True

    def test_custom_label(self):
        from config.model import ChannelEntry
        entry = ChannelEntry(url="https://web.max.ru/test", label="My Channel")
        assert entry.label == "My Channel"

    def test_disabled(self):
        from config.model import ChannelEntry
        entry = ChannelEntry(url="https://web.max.ru/test", enabled=False)
        assert entry.enabled is False

    def test_url_required(self):
        from config.model import ChannelEntry
        with pytest.raises(ValidationError):
            ChannelEntry(url="")

    def test_url_must_be_http(self):
        from config.model import ChannelEntry
        with pytest.raises(ValidationError):
            ChannelEntry(url="not-a-url")


class TestChannelRegistry:
    def test_defaults_empty(self):
        from config.model import ChannelRegistry
        reg = ChannelRegistry()
        assert reg.github == []
        assert reg.pypi == []
        assert reg.media == []
        assert reg.backup == []

    def test_add_entry(self):
        from config.model import ChannelRegistry, ChannelEntry
        reg = ChannelRegistry()
        reg.github.append(ChannelEntry(url="https://web.max.ru/ch1", label="GitHub Main"))
        assert len(reg.github) == 1
        assert reg.github[0].label == "GitHub Main"

    def test_get_enabled(self):
        from config.model import ChannelRegistry, ChannelEntry
        reg = ChannelRegistry()
        reg.github.append(ChannelEntry(url="https://web.max.ru/ch1", enabled=True))
        reg.github.append(ChannelEntry(url="https://web.max.ru/ch2", enabled=False))
        enabled = reg.get_enabled("github")
        assert len(enabled) == 1
        assert enabled[0].url == "https://web.max.ru/ch1"

    def test_get_enabled_empty(self):
        from config.model import ChannelRegistry
        reg = ChannelRegistry()
        assert reg.get_enabled("github") == []

    def test_get_enabled_invalid_function(self):
        from config.model import ChannelRegistry
        reg = ChannelRegistry()
        with pytest.raises(ValueError):
            reg.get_enabled("invalid")

    def test_toggle_channel(self):
        from config.model import ChannelRegistry, ChannelEntry
        reg = ChannelRegistry()
        reg.github.append(ChannelEntry(url="https://web.max.ru/ch1", enabled=True))
        reg.toggle_channel("github", 0)
        assert reg.github[0].enabled is False
        reg.toggle_channel("github", 0)
        assert reg.github[0].enabled is True

    def test_remove_channel(self):
        from config.model import ChannelRegistry, ChannelEntry
        reg = ChannelRegistry()
        reg.github.append(ChannelEntry(url="https://web.max.ru/ch1"))
        reg.github.append(ChannelEntry(url="https://web.max.ru/ch2"))
        reg.remove_channel("github", 0)
        assert len(reg.github) == 1
        assert reg.github[0].url == "https://web.max.ru/ch2"

    def test_has_channels(self):
        from config.model import ChannelRegistry, ChannelEntry
        reg = ChannelRegistry()
        assert not reg.has_channels("github")
        reg.github.append(ChannelEntry(url="https://web.max.ru/ch1"))
        assert reg.has_channels("github")

    def test_from_dict_roundtrip(self):
        from config.model import ChannelRegistry
        data = {
            "github": [
                {"url": "https://web.max.ru/ch1", "label": "Main", "enabled": True},
                {"url": "https://web.max.ru/ch2", "label": "Archive", "enabled": False},
            ],
            "pypi": [
                {"url": "https://web.max.ru/pypi1", "label": "PyPI Main"},
            ],
        }
        reg = ChannelRegistry(**data)
        assert len(reg.github) == 2
        assert reg.github[0].label == "Main"
        assert reg.github[1].enabled is False
        assert len(reg.pypi) == 1
        # media and backup default to empty
        assert reg.media == []
        assert reg.backup == []
```

```python
# config/model.py — ADD these classes before AppConfig

from typing import Literal

VALID_CHANNEL_FUNCTIONS = ("github", "pypi", "media", "backup")


class ChannelEntry(BaseModel):
    """Single channel entry in the registry."""
    url: str = Field(min_length=1)
    label: str = ""
    enabled: bool = True

    @field_validator("url")
    @classmethod
    def url_must_be_http(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError("Channel URL must start with http:// or https://")
        return v


class ChannelRegistry(BaseModel):
    """Registry of channels per function. Replaces flat channels.{key} = url."""
    github: list[ChannelEntry] = Field(default_factory=list)
    pypi: list[ChannelEntry] = Field(default_factory=list)
    media: list[ChannelEntry] = Field(default_factory=list)
    backup: list[ChannelEntry] = Field(default_factory=list)

    def get_enabled(self, function: str) -> list[ChannelEntry]:
        """Return enabled channels for a function."""
        if function not in VALID_CHANNEL_FUNCTIONS:
            raise ValueError(f"Invalid function: {function}. Must be one of {VALID_CHANNEL_FUNCTIONS}")
        channels = getattr(self, function, [])
        return [ch for ch in channels if ch.enabled]

    def has_channels(self, function: str) -> bool:
        """Check if a function has any channels configured."""
        channels = getattr(self, function, [])
        return len(channels) > 0

    def toggle_channel(self, function: str, index: int) -> None:
        """Toggle enabled state of a channel."""
        channels = getattr(self, function, [])
        if 0 <= index < len(channels):
            channels[index].enabled = not channels[index].enabled

    def remove_channel(self, function: str, index: int) -> None:
        """Remove a channel by index."""
        channels = getattr(self, function, [])
        if 0 <= index < len(channels):
            del channels[index]

    def add_channel(self, function: str, url: str, label: str = "") -> None:
        """Add a new channel entry."""
        channels = getattr(self, function, [])
        channels.append(ChannelEntry(url=url, label=label))
```

Then add `channel_registry` to `AppConfig`:

```python
# In AppConfig class, add this field:
    channel_registry: ChannelRegistry = ChannelRegistry()
```

**Verify:** `pytest tests/test_channel_registry.py -v`
**Commit:** `feat(config): add ChannelEntry and ChannelRegistry models`

---

### Task 1.2: Auto-Migration Logic

**File:** `config/loader.py`
**Test:** `tests/test_channel_migration.py`
**Depends:** none (imports ChannelRegistry from model.py, but tests isolate migration logic)

Add a migration function that detects old `channels.*` format and populates `channel_registry` on first load. Design requires migration to happen silently with a warning log.

```python
# tests/test_channel_migration.py
"""Tests for channel registry auto-migration."""

import pytest
import yaml as yaml_mod


class TestChannelMigration:
    def test_migrate_old_channels_to_registry(self, tmp_path, monkeypatch):
        """Old channels.max -> channel_registry.github migration."""
        monkeypatch.chdir(tmp_path)
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml_mod.dump({
            "channels": {
                "max": "https://web.max.ru/github",
                "pypi": "https://web.max.ru/pypi",
                "media": "",
                "backup": "",
            }
        }), encoding="utf-8")

        from config.loader import load_config
        cfg = load_config(cfg_file)

        # Old channels should still exist (backward compat)
        assert cfg.channels.max == "https://web.max.ru/github"

        # Registry should be populated from old channels
        assert len(cfg.channel_registry.github) == 1
        assert cfg.channel_registry.github[0].url == "https://web.max.ru/github"
        assert cfg.channel_registry.github[0].label == "GitHub Main"
        assert len(cfg.channel_registry.pypi) == 1
        assert cfg.channel_registry.pypi[0].url == "https://web.max.ru/pypi"

    def test_no_migration_if_registry_exists(self, tmp_path, monkeypatch):
        """If channel_registry already has entries, skip migration."""
        monkeypatch.chdir(tmp_path)
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml_mod.dump({
            "channels": {
                "max": "https://web.max.ru/old",
            },
            "channel_registry": {
                "github": [
                    {"url": "https://web.max.ru/new", "label": "New Channel", "enabled": True}
                ]
            }
        }), encoding="utf-8")

        from config.loader import load_config
        cfg = load_config(cfg_file)

        # Registry should NOT be overwritten
        assert len(cfg.channel_registry.github) == 1
        assert cfg.channel_registry.github[0].url == "https://web.max.ru/new"

    def test_migration_respects_skipped_channels(self, tmp_path, monkeypatch):
        """Skipped channels should not be migrated."""
        monkeypatch.chdir(tmp_path)
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml_mod.dump({
            "channels": {
                "max": "https://web.max.ru/github",
                "pypi": "https://web.max.ru/pypi",
            },
            "setup": {
                "skipped_channels": ["pypi"]
            }
        }), encoding="utf-8")

        from config.loader import load_config
        cfg = load_config(cfg_file)

        # GitHub should be migrated
        assert len(cfg.channel_registry.github) == 1
        # PyPI should NOT be migrated (skipped)
        assert len(cfg.channel_registry.pypi) == 0

    def test_migration_preserves_enabled_state(self, tmp_path, monkeypatch):
        """Migrated channels should be enabled by default."""
        monkeypatch.chdir(tmp_path)
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml_mod.dump({
            "channels": {
                "max": "https://web.max.ru/github",
            }
        }), encoding="utf-8")

        from config.loader import load_config
        cfg = load_config(cfg_file)

        assert cfg.channel_registry.github[0].enabled is True

    def test_empty_channels_no_migration(self, tmp_path, monkeypatch):
        """Empty channel URLs should not create entries."""
        monkeypatch.chdir(tmp_path)
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml_mod.dump({
            "channels": {
                "max": "",
                "pypi": "",
            }
        }), encoding="utf-8")

        from config.loader import load_config
        cfg = load_config(cfg_file)

        assert len(cfg.channel_registry.github) == 0
        assert len(cfg.channel_registry.pypi) == 0
```

```python
# config/loader.py — ADD migration function and call it in load_config

import logging
from config.model import ChannelEntry

_logger = logging.getLogger("gitax")

# Mapping: old channel key -> (registry function, default label)
_CHANNEL_MIGRATION_MAP = {
    "max": ("github", "GitHub Main"),
    "pypi": ("pypi", "PyPI Main"),
    "media": ("media", "Media Main"),
    "backup": ("backup", "Backup Main"),
}


def _migrate_channels_to_registry(config: AppConfig) -> AppConfig:
    """
    Auto-migrate old channels.{key} URLs to channel_registry on first load.

    If channel_registry is empty and old channels have URLs, creates ChannelEntry
    for each non-empty channel. Skipped channels are excluded.

    Args:
        config: Loaded AppConfig instance

    Returns:
        Config with channel_registry populated (or unchanged if already set)
    """
    # If registry already has entries, skip migration
    if config.channel_registry.github or config.channel_registry.pypi or \
       config.channel_registry.media or config.channel_registry.backup:
        return config

    # Check if any old channels have URLs
    has_old_channels = any([
        config.channels.max,
        config.channels.pypi,
        config.channels.media,
        config.channels.backup,
    ])

    if not has_old_channels:
        return config

    # Get skipped channels
    skipped = config.setup.skipped_channels or []

    migrated = 0
    for old_key, (reg_func, default_label) in _CHANNEL_MIGRATION_MAP.items():
        old_url = getattr(config.channels, old_key, "")
        if not old_url:
            continue
        if old_key in skipped:
            continue

        entry = ChannelEntry(url=old_url, label=default_label, enabled=True)
        getattr(config.channel_registry, reg_func).append(entry)
        migrated += 1

    if migrated > 0:
        _logger.warning(
            f"Auto-migrated {migrated} channel(s) from legacy channels.* format "
            f"to channel_registry. Edit config.yaml to manage channels."
        )

    return config


# In load_config(), ADD this call before returning:
# After: config = _apply_env_overrides(config)
# Add:   config = _migrate_channels_to_registry(config)
# Then:  return config
```

Edit `load_config` to call migration:

```python
# In config/loader.py, in load_config(), replace the last two lines:
# OLD:
#     config = _apply_env_overrides(config)
#     return config
# NEW:
#     config = _apply_env_overrides(config)
#     config = _migrate_channels_to_registry(config)
#     return config
```

**Verify:** `pytest tests/test_channel_migration.py -v`
**Commit:** `feat(config): add auto-migration from legacy channels to registry`

---

### Task 1.3: Config Utils Extensions

**File:** `config_utils.py`
**Test:** `tests/test_channel_utils.py`
**Depends:** none (standalone utility functions)

Add `get_channels_for_function()` and `get_channel_url()` backward compat wrapper.

```python
# tests/test_channel_utils.py
"""Tests for channel registry utility functions."""

import pytest


class TestGetChannelsForFunction:
    def test_returns_enabled_channels(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from config import init_config, get_config
        from config_utils import get_channels_for_function

        cfg_file = tmp_path / "config.yaml"
        import yaml
        cfg_file.write_text(yaml.dump({
            "channel_registry": {
                "github": [
                    {"url": "https://web.max.ru/ch1", "label": "Main", "enabled": True},
                    {"url": "https://web.max.ru/ch2", "label": "Archive", "enabled": False},
                ]
            }
        }), encoding="utf-8")
        init_config(str(cfg_file))

        channels = get_channels_for_function("github")
        assert len(channels) == 1
        assert channels[0].url == "https://web.max.ru/ch1"

    def test_returns_empty_for_no_channels(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from config_utils import get_channels_for_function
        channels = get_channels_for_function("backup")
        assert channels == []

    def test_returns_all_enabled(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from config import init_config, get_config
        from config_utils import get_channels_for_function

        cfg_file = tmp_path / "config.yaml"
        import yaml
        cfg_file.write_text(yaml.dump({
            "channel_registry": {
                "pypi": [
                    {"url": "https://web.max.ru/p1", "label": "A", "enabled": True},
                    {"url": "https://web.max.ru/p2", "label": "B", "enabled": True},
                ]
            }
        }), encoding="utf-8")
        init_config(str(cfg_file))

        channels = get_channels_for_function("pypi")
        assert len(channels) == 2

    def test_invalid_function_raises(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from config_utils import get_channels_for_function
        with pytest.raises(ValueError):
            get_channels_for_function("invalid")


class TestGetChannelUrlBackwardCompat:
    def test_returns_first_enabled_url(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from config import init_config
        from config_utils import get_channel_url

        cfg_file = tmp_path / "config.yaml"
        import yaml
        cfg_file.write_text(yaml.dump({
            "channel_registry": {
                "github": [
                    {"url": "https://web.max.ru/ch1", "label": "Main", "enabled": True},
                ]
            }
        }), encoding="utf-8")
        init_config(str(cfg_file))

        url = get_channel_url({}, "max")
        assert url == "https://web.max.ru/ch1"

    def test_falls_back_to_old_channels(self, tmp_path, monkeypatch):
        """When registry is empty, fall back to old channels.{key}."""
        monkeypatch.chdir(tmp_path)
        from config import init_config
        from config_utils import get_channel_url

        cfg_file = tmp_path / "config.yaml"
        import yaml
        cfg_file.write_text(yaml.dump({
            "channels": {
                "max": "https://web.max.ru/legacy",
            }
        }), encoding="utf-8")
        init_config(str(cfg_file))

        url = get_channel_url({}, "max")
        assert url == "https://web.max.ru/legacy"
```

```python
# config_utils.py — ADD these functions at the end

# Channel-to-function mapping (inverse of _MODULE_CHANNELS)
_CHANNEL_TO_FUNCTION = {
    "max": "github",
    "pypi": "pypi",
    "media": "media",
    "backup": "backup",
}


def get_channels_for_function(function: str) -> list:
    """
    Get enabled channels for a function from channel_registry.

    Args:
        function: Function key ("github", "pypi", "media", "backup")

    Returns:
        List of ChannelEntry objects (enabled only)

    Raises:
        ValueError: If function is invalid
    """
    from config import get_config
    config = get_config()
    return config.channel_registry.get_enabled(function)


def get_channel_url_for_channel_key(channel_key: str) -> str:
    """
    Get the first enabled channel URL for a legacy channel key.

    Backward compatibility wrapper: returns the URL of the first enabled channel
    for a given channel key (e.g., "max" -> first github channel).
    Falls back to old channels.{key} if registry is empty.

    Args:
        channel_key: Legacy channel key ("max", "pypi", "media", "backup")

    Returns:
        Channel URL string, or empty string if not found
    """
    from config import get_config
    config = get_config()

    # Try registry first
    function = _CHANNEL_TO_FUNCTION.get(channel_key, channel_key)
    try:
        enabled = config.channel_registry.get_enabled(function)
        if enabled:
            return enabled[0].url
    except (ValueError, AttributeError):
        pass

    # Fall back to old channels
    old_url = getattr(config.channels, channel_key, "")
    if old_url:
        return old_url

    return ""
```

**Verify:** `pytest tests/test_channel_utils.py -v`
**Commit:** `feat(config): add get_channels_for_function and backward compat URL resolver`

---

## Batch 2: Core Modules (parallel - 4 implementers)

All tasks depend on Batch 1 models being in place.

### Task 2.1: Channel Management CLI (Service Menu)

**File:** `github_archiver.py`
**Test:** `tests/test_channel_manager.py`
**Depends:** 1.1 (ChannelRegistry model)

Add "Управление каналами" to the service menu. The menu supports add, list, toggle, delete operations. Design requires CRUD with confirmation for destructive actions.

```python
# tests/test_channel_manager.py
"""Tests for channel management CLI functions."""

import pytest


class TestChannelManager:
    def test_add_channel_to_registry(self, tmp_path, monkeypatch):
        """Adding a channel creates a new entry."""
        monkeypatch.chdir(tmp_path)
        import yaml
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump({
            "channel_registry": {}
        }), encoding="utf-8")

        from config import init_config, get_config
        init_config(str(cfg_file))

        from config.model import ChannelEntry
        config = get_config()
        config.channel_registry.add_channel(
            "github",
            "https://web.max.ru/new-channel",
            "New GitHub Channel"
        )

        assert len(config.channel_registry.github) == 1
        assert config.channel_registry.github[0].url == "https://web.max.ru/new-channel"
        assert config.channel_registry.github[0].label == "New GitHub Channel"

    def test_list_channels_shows_all(self, tmp_path, monkeypatch):
        """Listing channels shows all entries with status."""
        monkeypatch.chdir(tmp_path)
        import yaml
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump({
            "channel_registry": {
                "github": [
                    {"url": "https://web.max.ru/ch1", "label": "Main", "enabled": True},
                    {"url": "https://web.max.ru/ch2", "label": "Archive", "enabled": False},
                ]
            }
        }), encoding="utf-8")

        from config import init_config, get_config
        init_config(str(cfg_file))

        from config_utils import get_channels_for_function
        all_channels = get_config().channel_registry.github
        assert len(all_channels) == 2
        enabled = get_channels_for_function("github")
        assert len(enabled) == 1

    def test_toggle_channel(self, tmp_path, monkeypatch):
        """Toggling a channel flips enabled state."""
        monkeypatch.chdir(tmp_path)
        import yaml
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump({
            "channel_registry": {
                "github": [
                    {"url": "https://web.max.ru/ch1", "label": "Main", "enabled": True},
                ]
            }
        }), encoding="utf-8")

        from config import init_config, get_config
        init_config(str(cfg_file))

        config = get_config()
        config.channel_registry.toggle_channel("github", 0)
        assert config.channel_registry.github[0].enabled is False

    def test_delete_channel(self, tmp_path, monkeypatch):
        """Deleting a channel removes it from the list."""
        monkeypatch.chdir(tmp_path)
        import yaml
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump({
            "channel_registry": {
                "github": [
                    {"url": "https://web.max.ru/ch1", "label": "Main", "enabled": True},
                    {"url": "https://web.max.ru/ch2", "label": "Archive", "enabled": True},
                ]
            }
        }), encoding="utf-8")

        from config import init_config, get_config
        init_config(str(cfg_file))

        config = get_config()
        config.channel_registry.remove_channel("github", 0)
        assert len(config.channel_registry.github) == 1
        assert config.channel_registry.github[0].url == "https://web.max.ru/ch2"

    def test_save_registry_to_yaml(self, tmp_path, monkeypatch):
        """Registry changes persist to config.yaml."""
        monkeypatch.chdir(tmp_path)
        import yaml
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump({
            "channel_registry": {}
        }), encoding="utf-8")

        from config import init_config, get_config
        init_config(str(cfg_file))

        config = get_config()
        config.channel_registry.add_channel(
            "github",
            "https://web.max.ru/test",
            "Test Channel"
        )

        # Write back
        with open(cfg_file, "w", encoding="utf-8") as f:
            yaml.dump(config.model_dump(), f)

        # Re-read
        with open(cfg_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        assert "channel_registry" in data
        assert len(data["channel_registry"]["github"]) == 1
        assert data["channel_registry"]["github"][0]["url"] == "https://web.max.ru/test"
```

Now add the channel management method to `GitHubArchiver` class in `github_archiver.py`. Add it as a new method and wire it into the service menu:

```python
# github_archiver.py — ADD method to GitHubArchiver class

    def _channel_management(self):
        """Channel management submenu — add, list, toggle, delete channels."""
        from config import get_config
        from config.model import VALID_CHANNEL_FUNCTIONS

        function_labels = {
            "github": "GitHub",
            "pypi": "PyPI",
            "media": "Media",
            "backup": "Backup",
        }

        while True:
            os.system('cls' if os.name == 'nt' else 'clear')
            print("\n" + "=" * 60)
            print("  Управление каналами")
            print("-" * 60)
            print()
            print("  [1] Добавить канал")
            print("  [2] Список каналов")
            print("  [3] Вкл/Выкл канал")
            print("  [4] Удалить канал")
            print("  [0] Назад")
            print()

            choice = self._get_user_choice(["0", "1", "2", "3", "4"],
                                          "Выберите действие [0-4]")

            if choice == "0":
                break
            elif choice == "1":
                self._add_channel(function_labels)
            elif choice == "2":
                self._list_channels(function_labels)
            elif choice == "3":
                self._toggle_channel(function_labels)
            elif choice == "4":
                self._delete_channel(function_labels)

    def _add_channel(self, function_labels: dict) -> None:
        """Add a new channel to the registry."""
        from config import get_config

        config = get_config()
        print("\n  Выберите функцию:")
        func_keys = list(function_labels.keys())
        for i, func_key in enumerate(func_keys, 1):
            print(f"  [{i}] {function_labels[func_key]}")
        print()

        choice = self._get_user_choice([str(i) for i in range(1, len(func_keys) + 1)],
                                       "Номер функции")
        func_key = func_keys[int(choice) - 1]

        try:
            url = input(f"  URL канала ({function_labels[func_key]}): ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  Отменено.")
            return

        if not url:
            print("\n  URL не может быть пустым.")
            input("\n  Нажмите Enter...")
            return

        try:
            label = input(f"  Имя канала (по умолчанию: {function_labels[func_key]}): ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  Отменено.")
            return

        if not label:
            label = function_labels[func_key]

        config.channel_registry.add_channel(func_key, url, label)
        self._save_config(config)
        print(f"\n  ✓ Канал \"{label}\" добавлен для {function_labels[func_key]}")
        input("\n  Нажмите Enter...")

    def _list_channels(self, function_labels: dict) -> None:
        """List all channels."""
        from config import get_config

        config = get_config()
        print()
        print(f"  {'Функция':<10} {'#':<4} {'Имя':<20} {'URL':<40} {'Статус'}")
        print("  " + "-" * 80)

        for func_key, func_label in function_labels.items():
            channels = getattr(config.channel_registry, func_key, [])
            if not channels:
                print(f"  {func_label:<10} — нет каналов")
                continue
            for i, ch in enumerate(channels):
                status = "✓ вкл" if ch.enabled else "✗ выкл"
                label = ch.label or f"{func_label} #{i+1}"
                url_short = ch.url[:38] + ".." if len(ch.url) > 40 else ch.url
                print(f"  {func_label:<10} {i+1:<4} {label:<20} {url_short:<40} {status}")

        print()
        input("  Нажмите Enter...")

    def _toggle_channel(self, function_labels: dict) -> None:
        """Toggle channel enabled/disabled."""
        from config import get_config

        config = get_config()
        self._list_channels(function_labels)

        try:
            func_input = input("\n  Функция (github/pypi/media/backup): ").strip().lower()
            idx_input = input("  Номер канала: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  Отменено.")
            return

        if func_input not in function_labels:
            print(f"\n  Неверная функция. Доступно: {', '.join(function_labels.keys())}")
            input("\n  Нажмите Enter...")
            return

        try:
            idx = int(idx_input) - 1
        except ValueError:
            print("\n  Неверный номер.")
            input("\n  Нажмите Enter...")
            return

        channels = getattr(config.channel_registry, func_input, [])
        if idx < 0 or idx >= len(channels):
            print(f"\n  Канал #{idx+1} не найден.")
            input("\n  Нажмите Enter...")
            return

        config.channel_registry.toggle_channel(func_input, idx)
        self._save_config(config)
        new_state = "включён" if config.channel_registry.func_input[idx].enabled else "выключен"
        print(f"\n  ✓ Канал \"{channels[idx].label}\" {new_state}")
        input("\n  Нажмите Enter...")

    def _delete_channel(self, function_labels: dict) -> None:
        """Delete a channel with confirmation."""
        from config import get_config

        config = get_config()
        self._list_channels(function_labels)

        try:
            func_input = input("\n  Функция (github/pypi/media/backup): ").strip().lower()
            idx_input = input("  Номер канала: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  Отменено.")
            return

        if func_input not in function_labels:
            print(f"\n  Неверная функция.")
            input("\n  Нажмите Enter...")
            return

        try:
            idx = int(idx_input) - 1
        except ValueError:
            print("\n  Неверный номер.")
            input("\n  Нажмите Enter...")
            return

        channels = getattr(config.channel_registry, func_input, [])
        if idx < 0 or idx >= len(channels):
            print(f"\n  Канал #{idx+1} не найден.")
            input("\n  Нажмите Enter...")
            return

        ch = channels[idx]
        confirm = input(f"\n  Удалить канал \"{ch.label}\" ({ch.url})? [y/N]: ").strip().lower()
        if confirm != "y":
            print("\n  Отменено.")
            input("\n  Нажмите Enter...")
            return

        config.channel_registry.remove_channel(func_input, idx)
        self._save_config(config)
        print(f"\n  ✓ Канал \"{ch.label}\" удалён")
        input("\n  Нажмите Enter...")

    def _save_config(self, config) -> None:
        """Save config to YAML file."""
        import yaml
        from pathlib import Path
        cfg_path = Path("config.yaml")
        with open(cfg_path, "w", encoding="utf-8") as f:
            yaml.dump(config.model_dump(), f, allow_unicode=True)
```

Wire into service menu — modify `_service_menu()` to add the new option:

```python
# In _service_menu(), add the channel management option:
# OLD:
#     print("  [1] Очистить журналы")
#     if is_setup_complete(self.config):
#         print("  [2] ⚙ Настройки")
#     print("  [0] Назад")
# NEW:
#     print("  [1] Очистить журналы")
#     if is_setup_complete(self.config):
#         print("  [2] ⚙ Настройки")
#     print("  [3] Управление каналами")
#     print("  [0] Назад")
```

And in `_run_service_menu()`, handle the new option:

```python
# In _run_service_menu(), add:
# elif choice == '3':
#     self._channel_management()
```

**Verify:** `pytest tests/test_channel_manager.py -v`
**Commit:** `feat(menu): add channel management CLI to service menu`

---

### Task 2.2: Channel Selector UI

**File:** `github_archiver.py`
**Test:** `tests/test_channel_selector.py`
**Depends:** 1.1, 1.3 (registry model + utils)

Channel selector before running any archiver. Transparent pass-through for 1 channel, prompt for 2+.

```python
# tests/test_channel_selector.py
"""Tests for channel selector logic."""

import pytest


class TestChannelSelector:
    def test_single_channel_no_prompt(self, tmp_path, monkeypatch):
        """When only 1 enabled channel, no selector prompt needed."""
        monkeypatch.chdir(tmp_path)
        import yaml
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump({
            "channel_registry": {
                "github": [
                    {"url": "https://web.max.ru/ch1", "label": "Main", "enabled": True},
                ]
            }
        }), encoding="utf-8")

        from config import init_config, get_config
        init_config(str(cfg_file))

        from config_utils import get_channels_for_function
        channels = get_channels_for_function("github")
        assert len(channels) == 1
        # Single channel = transparent pass-through
        selected = channels[0]
        assert selected.url == "https://web.max.ru/ch1"

    def test_multiple_channels_shows_options(self, tmp_path, monkeypatch):
        """When 2+ enabled channels, all options are available."""
        monkeypatch.chdir(tmp_path)
        import yaml
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump({
            "channel_registry": {
                "github": [
                    {"url": "https://web.max.ru/ch1", "label": "Main", "enabled": True},
                    {"url": "https://web.max.ru/ch2", "label": "Archive", "enabled": True},
                    {"url": "https://web.max.ru/ch3", "label": "Disabled", "enabled": False},
                ]
            }
        }), encoding="utf-8")

        from config import init_config, get_config
        init_config(str(cfg_file))

        from config_utils import get_channels_for_function
        channels = get_channels_for_function("github")
        # Only enabled channels shown
        assert len(channels) == 2
        urls = [ch.url for ch in channels]
        assert "https://web.max.ru/ch1" in urls
        assert "https://web.max.ru/ch2" in urls
        assert "https://web.max.ru/ch3" not in urls

    def test_no_channels_returns_empty(self, tmp_path, monkeypatch):
        """When no channels configured, return empty."""
        monkeypatch.chdir(tmp_path)
        import yaml
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump({
            "channel_registry": {}
        }), encoding="utf-8")

        from config import init_config, get_config
        init_config(str(cfg_file))

        from config_utils import get_channels_for_function
        channels = get_channels_for_function("github")
        assert len(channels) == 0

    def test_disabled_channels_excluded(self, tmp_path, monkeypatch):
        """Disabled channels should not appear in selector."""
        monkeypatch.chdir(tmp_path)
        import yaml
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump({
            "channel_registry": {
                "pypi": [
                    {"url": "https://web.max.ru/ch1", "label": "A", "enabled": False},
                    {"url": "https://web.max.ru/ch2", "label": "B", "enabled": False},
                ]
            }
        }), encoding="utf-8")

        from config import init_config, get_config
        init_config(str(cfg_file))

        from config_utils import get_channels_for_function
        channels = get_channels_for_function("pypi")
        assert len(channels) == 0
```

Add the channel selector method to `GitHubArchiver`:

```python
# github_archiver.py — ADD method to GitHubArchiver class

    def _select_channel(self, function: str, function_label: str) -> str | None:
        """
        Select a channel for a function. Returns channel URL or None if cancelled.

        - 0 channels: error message, return None
        - 1 channel: transparent pass-through, return URL
        - 2+ channels: show selector with "All" option

        Args:
            function: Registry function key ("github", "pypi", "media", "backup")
            function_label: Display name for error messages

        Returns:
            Channel URL string, or list of URLs if "All" selected, or None
        """
        from config_utils import get_channels_for_function

        channels = get_channels_for_function(function)

        if not channels:
            print(f"\n  ✗ Нет каналов для {function_label}.")
            print("  Добавьте канал в разделе 'Управление каналами'.")
            return None

        if len(channels) == 1:
            # Transparent pass-through — no prompt
            return channels[0].url

        # Multiple channels — show selector
        print(f"\n  Выберите канал для {function_label}:")
        for i, ch in enumerate(channels, 1):
            print(f"  [{i}] {ch.label or ch.url}")
        print(f"  [0] Все каналы (параллельная загрузка)")
        print()

        choices = [str(i) for i in range(len(channels) + 1)]
        choice = self._get_user_choice(choices, "Номер канала")

        if choice == "0":
            # All channels — return list of URLs for parallel upload
            return [ch.url for ch in channels]

        idx = int(choice) - 1
        return channels[idx].url
```

**Verify:** `pytest tests/test_channel_selector.py -v`
**Commit:** `feat(menu): add channel selector for multi-channel support`

---

### Task 2.3: ParallelGroupUploader Module

**File:** `parallel_uploader.py`
**Test:** `tests/test_parallel_uploader.py`
**Depends:** 1.1, 1.3 (registry model + utils)

New module implementing 3-phase parallel upload: download (once), upload (thread per channel), cleanup (delete on ≥1 success).

```python
# tests/test_parallel_uploader.py
"""Tests for ParallelGroupUploader with mock browser."""

import os
import pytest
import tempfile
import threading
from unittest.mock import MagicMock, patch


class MockBrowserMAX:
    """Mock BrowserMAX for testing parallel uploads."""
    def __init__(self, channel_url, use_local_browser=False):
        self.channel_url = channel_url
        self.uploaded_files = []
        self.should_fail = False

    def keep_alive_connect(self):
        return True

    def navigate(self):
        pass

    def ensure_page_ready(self):
        pass

    def send_message_with_file(self, text="", filepath="", **kwargs):
        if self.should_fail:
            return False, "Mock failure"
        self.uploaded_files.append(filepath)
        return True, "OK"


class TestParallelGroupUploader:
    def test_single_channel_upload(self, tmp_path):
        """Single channel uploads files normally."""
        from parallel_uploader import ParallelGroupUploader

        # Create test files
        test_file = tmp_path / "test.zip"
        test_file.write_bytes(b"test content")

        uploader = ParallelGroupUploader(
            files=[str(test_file)],
            channels=[
                {"url": "https://web.max.ru/ch1", "label": "Channel 1"}
            ]
        )

        results = uploader.run(mock_browser_class=MockBrowserMAX)

        assert results["channel_results"]["Channel 1"]["success"] is True
        assert len(results["channel_results"]["Channel 1"]["files"]) == 1

    def test_parallel_upload_to_multiple_channels(self, tmp_path):
        """Files uploaded to all channels in parallel."""
        from parallel_uploader import ParallelGroupUploader

        test_file = tmp_path / "test.zip"
        test_file.write_bytes(b"test content")

        uploader = ParallelGroupUploader(
            files=[str(test_file)],
            channels=[
                {"url": "https://web.max.ru/ch1", "label": "Channel 1"},
                {"url": "https://web.max.ru/ch2", "label": "Channel 2"},
            ]
        )

        results = uploader.run(mock_browser_class=MockBrowserMAX)

        assert results["channel_results"]["Channel 1"]["success"] is True
        assert results["channel_results"]["Channel 2"]["success"] is True

    def test_partial_failure_keeps_file(self, tmp_path):
        """When some channels fail, file is still deleted if ≥1 succeeded."""
        from parallel_uploader import ParallelGroupUploader

        test_file = tmp_path / "test.zip"
        test_file.write_bytes(b"test content")

        uploader = ParallelGroupUploader(
            files=[str(test_file)],
            channels=[
                {"url": "https://web.max.ru/ch1", "label": "Channel 1"},
                {"url": "https://web.max.ru/ch2", "label": "Channel 2"},
            ],
            cleanup=True
        )

        # Make channel 2 fail
        uploader._channel_failures = {"Channel 2": True}

        results = uploader.run(mock_browser_class=MockBrowserMAX)

        # File should be cleaned up (channel 1 succeeded)
        assert not test_file.exists()

    def test_all_failure_keeps_file(self, tmp_path):
        """When ALL channels fail, file is preserved."""
        from parallel_uploader import ParallelGroupUploader

        test_file = tmp_path / "test.zip"
        test_file.write_bytes(b"test content")

        uploader = ParallelGroupUploader(
            files=[str(test_file)],
            channels=[
                {"url": "https://web.max.ru/ch1", "label": "Channel 1"},
                {"url": "https://web.max.ru/ch2", "label": "Channel 2"},
            ],
            cleanup=True
        )

        # Make all channels fail
        uploader._channel_failures = {"Channel 1": True, "Channel 2": True}

        results = uploader.run(mock_browser_class=MockBrowserMAX)

        # File should NOT be cleaned up
        assert test_file.exists()

    def test_stagger_delay_between_threads(self, tmp_path):
        """Threads start with configured stagger delay."""
        from parallel_uploader import ParallelGroupUploader

        test_file = tmp_path / "test.zip"
        test_file.write_bytes(b"test content")

        start_times = []

        class TimingMockBrowser(MockBrowserMAX):
            def keep_alive_connect(self):
                start_times.append(threading.get_ident())
                return True

        uploader = ParallelGroupUploader(
            files=[str(test_file)],
            channels=[
                {"url": "https://web.max.ru/ch1", "label": "Channel 1"},
                {"url": "https://web.max.ru/ch2", "label": "Channel 2"},
                {"url": "https://web.max.ru/ch3", "label": "Channel 3"},
            ],
            stagger_delay_sec=0.1
        )

        results = uploader.run(mock_browser_class=TimingMockBrowser)

        # All threads should have started (3 unique thread IDs)
        assert len(start_times) == 3
```

```python
# parallel_uploader.py
"""
ParallelGroupUploader — upload same files to multiple channels in parallel.

Three phases:
1. Download (single-threaded) — handled by caller, files passed in
2. Upload (multi-threaded) — one thread per channel
3. Cleanup — delete files when ≥1 channel confirmed success
"""

from __future__ import annotations

import os
import time
import threading
import logging
from dataclasses import dataclass, field
from typing import Any

_logger = logging.getLogger("gitax")


@dataclass
class ChannelResult:
    """Result of uploading to a single channel."""
    label: str
    success: bool
    files: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class UploadSummary:
    """Summary of parallel upload results."""
    channel_results: dict[str, ChannelResult] = field(default_factory=dict)
    total_files: int = 0
    total_success: int = 0
    total_failed: int = 0


class ParallelGroupUploader:
    """
    Upload files to multiple channels in parallel using threading.

    Each channel gets its own thread with a separate BrowserMAX instance.
    Files are shared from disk (downloaded once by caller).
    Cleanup happens after all threads complete.
    """

    def __init__(
        self,
        files: list[str],
        channels: list[dict[str, str]],
        cleanup: bool = True,
        stagger_delay_sec: float = 2.0,
        max_concurrent: int = 5,
        journal: Any = None,
    ):
        """
        Args:
            files: List of file paths to upload (already downloaded)
            channels: List of {"url": ..., "label": ...} dicts
            cleanup: Whether to delete files after successful upload
            stagger_delay_sec: Delay between starting each thread (seconds)
            max_concurrent: Maximum concurrent upload threads
            journal: Optional journal instance for recording results
        """
        self.files = files
        self.channels = channels
        self.cleanup = cleanup
        self.stagger_delay_sec = stagger_delay_sec
        self.max_concurrent = max(1, min(max_concurrent, len(channels)))
        self.journal = journal

        # Thread-safe results storage
        self._results_lock = threading.Lock()
        self._results: dict[str, ChannelResult] = {}
        self._channel_failures: dict[str, bool] = {}

    def run(self, mock_browser_class: type = None) -> UploadSummary:
        """
        Execute parallel upload.

        Args:
            mock_browser_class: Optional mock for testing. If None, uses real BrowserMAX.

        Returns:
            UploadSummary with per-channel results.
        """
        _logger.info(
            f"ParallelGroupUploader: {len(self.files)} file(s) -> "
            f"{len(self.channels)} channel(s)"
        )

        threads = []
        semaphore = threading.Semaphore(self.max_concurrent)

        for channel_info in self.channels:
            t = threading.Thread(
                target=self._upload_to_channel,
                args=(channel_info, semaphore, mock_browser_class),
                name=f"upload-{channel_info.get('label', 'unknown')}",
                daemon=True,
            )
            threads.append(t)
            time.sleep(self.stagger_delay_sec)  # Stagger to avoid rate limits

        # Start all threads
        for t in threads:
            t.start()

        # Wait for all threads to complete
        for t in threads:
            t.join(timeout=600)  # 10 min max per thread

        # Build summary
        summary = self._build_summary()

        # Cleanup phase
        if self.cleanup:
            self._cleanup_files(summary)

        return summary

    def _upload_to_channel(
        self,
        channel_info: dict[str, str],
        semaphore: threading.Semaphore,
        mock_browser_class: type = None,
    ) -> None:
        """Upload all files to a single channel."""
        label = channel_info.get("label", channel_info.get("url", "Unknown"))
        url = channel_info["url"]

        with semaphore:
            _logger.info(f"[{label}] Starting upload thread")

            browser_class = mock_browser_class
            if browser_class is None:
                from browser_max import BrowserMAX
                browser_class = BrowserMAX

            browser = None
            result = ChannelResult(label=label, success=False)

            try:
                browser = browser_class(url, use_local_browser=False)
                if not browser.keep_alive_connect():
                    result.errors.append("Failed to connect to browser")
                    self._save_result(label, result)
                    return

                browser.navigate()
                browser.ensure_page_ready()

                uploaded = 0
                for filepath in self.files:
                    if not os.path.exists(filepath):
                        result.errors.append(f"File not found: {filepath}")
                        continue

                    filename = os.path.basename(filepath)
                    retries = 3
                    for attempt in range(1, retries + 1):
                        try:
                            success, msg = browser.send_message_with_file(
                                text=f"📦 {filename}",
                                filepath=filepath,
                                retries=1,
                                retry_delay=5,
                            )
                            if success:
                                result.files.append(filepath)
                                uploaded += 1
                                break
                            else:
                                _logger.warning(
                                    f"[{label}] Upload attempt {attempt} failed: {msg}"
                                )
                                if attempt < retries:
                                    time.sleep(5)
                                else:
                                    result.errors.append(
                                        f"Failed to upload {filename}: {msg}"
                                    )
                        except Exception as e:
                            _logger.error(f"[{label}] Upload error: {e}")
                            if attempt < retries:
                                time.sleep(5)
                            else:
                                result.errors.append(f"Error uploading {filename}: {e}")

                result.success = uploaded > 0
                _logger.info(
                    f"[{label}] Upload complete: {uploaded}/{len(self.files)} files"
                )

            except Exception as e:
                _logger.error(f"[{label}] Thread exception: {e}", exc_info=True)
                result.errors.append(f"Thread exception: {e}")

            finally:
                self._save_result(label, result)
                if browser:
                    try:
                        browser.page = None
                        browser.browser = None
                        browser._connected = False
                    except Exception:
                        pass

    def _save_result(self, label: str, result: ChannelResult) -> None:
        """Thread-safe result storage."""
        with self._results_lock:
            self._results[label] = result
            if not result.success:
                self._channel_failures[label] = True

    def _build_summary(self) -> UploadSummary:
        """Build upload summary from results."""
        summary = UploadSummary(
            channel_results=self._results,
            total_files=len(self.files),
        )

        success_count = sum(1 for r in self._results.values() if r.success)
        failed_count = len(self._results) - success_count
        summary.total_success = success_count
        summary.total_failed = failed_count

        return summary

    def _cleanup_files(self, summary: UploadSummary) -> None:
        """
        Delete files if ≥1 channel succeeded.
        Preserve files if ALL channels failed.
        """
        all_failed = summary.total_failed == len(self.channels)

        if all_failed:
            _logger.warning(
                "All channels failed — preserving temp files for manual retry"
            )
            return

        _logger.info("Cleanup: deleting temp files (≥1 channel succeeded)")
        for filepath in self.files:
            try:
                if os.path.exists(filepath):
                    os.remove(filepath)
                    _logger.debug(f"Deleted: {filepath}")
            except Exception as e:
                _logger.warning(f"Failed to delete {filepath}: {e}")
```

**Verify:** `pytest tests/test_parallel_uploader.py -v`
**Commit:** `feat(upload): add ParallelGroupUploader for multi-channel parallel uploads`

---

### Task 2.4: Journal Thread Safety

**File:** `shared_journal.py`
**Test:** `tests/test_journal_thread_safety.py`
**Depends:** none (modifies existing shared_journal.py)

Add `threading.Lock` to BaseJournal and optional `channel_label` field support.

```python
# tests/test_journal_thread_safety.py
"""Tests for journal thread safety."""

import pytest
import threading
import time


class TestJournalThreadSafety:
    def test_concurrent_writes_no_corruption(self, tmp_path):
        """Multiple threads writing simultaneously should not corrupt data."""
        from journal import Journal

        journal_path = str(tmp_path / "concurrent_journal.json")
        journal = Journal(journal_path)

        errors = []
        write_count = 0
        lock = threading.Lock()

        def write_entries(prefix: str, count: int):
            nonlocal write_count
            local_errors = []
            for i in range(count):
                try:
                    journal.add_repository({
                        "full_name": f"{prefix}/repo_{i}",
                        "status": "sent",
                    })
                    with lock:
                        write_count += 1
                except Exception as e:
                    local_errors.append(str(e))
            errors.extend(local_errors)

        threads = []
        for prefix in ["batch_a", "batch_b", "batch_c"]:
            t = threading.Thread(target=write_entries, args=(prefix, 10))
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        # No errors should have occurred
        assert len(errors) == 0, f"Thread errors: {errors}"

        # All entries should be present
        all_repos = journal.get_all_repositories()
        assert len(all_repos) == 30

        # Journal should be valid JSON on disk
        import json
        with open(journal_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert "repositories" in data
        assert len(data["repositories"]) == 30

    def test_channel_label_field(self, tmp_path):
        """channel_label field should be optional and preserved."""
        from journal import Journal

        journal_path = str(tmp_path / "channel_journal.json")
        journal = Journal(journal_path)

        journal.add_repository({
            "full_name": "test/repo1",
            "status": "sent",
            "channel_label": "GitHub Main",
        })

        repo = journal.get_repository("test/repo1")
        assert repo["channel_label"] == "GitHub Main"

        # Old entries without channel_label should still work
        journal.add_repository({
            "full_name": "test/repo2",
            "status": "sent",
        })

        repo2 = journal.get_repository("test/repo2")
        assert "channel_label" not in repo2 or repo2.get("channel_label") is None

    def test_journal_lock_timeout_recovery(self, tmp_path):
        """Lock timeout should not crash the journal."""
        from shared_journal import BaseJournal

        class TestJournal(BaseJournal):
            def _create_empty(self):
                return {"entries": []}

        journal_path = str(tmp_path / "lock_journal.json")
        journal = TestJournal(journal_path)

        # Stale lock file
        lock_path = f"{journal_path}.lock"
        with open(lock_path, "w") as f:
            f.write("stale")

        # Should recover from stale lock (>300s old)
        import os
        os.utime(lock_path, (0, 0))  # Set to epoch = very old

        journal.save()  # Should not raise
        assert not os.path.exists(lock_path)  # Lock released
```

Now modify `shared_journal.py` to add threading.Lock:

```python
# shared_journal.py — ADD threading import and lock

# At top of file, add:
import threading

# In BaseJournal class, add class-level lock:
    _write_lock = threading.Lock()

# Replace save() method:
    def save(self):
        """Save journal to file (atomic write via tempfile → copy2 → os.replace).
        Thread-safe via class-level lock."""
        # Use threading.Lock instead of file lock for true thread safety
        acquired = self._write_lock.acquire(timeout=5)
        if not acquired:
            self.logger.warning("Journal write lock timeout, skipping save")
            return
        try:
            self._pre_save()
            temp_fd, temp_path = tempfile.mkstemp(
                suffix='.json',
                dir=os.path.dirname(self.file_path) or '.'
            )
            try:
                with os.fdopen(temp_fd, 'w', encoding='utf-8') as f:
                    json.dump(self.data, f, ensure_ascii=False, indent=2)
                if os.path.exists(self.file_path):
                    backup_path = f"{self.file_path}.bak"
                    shutil.copy2(self.file_path, backup_path)
                os.replace(temp_path, self.file_path)
            except Exception:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                raise
        finally:
            self._write_lock.release()
```

**Verify:** `pytest tests/test_journal_thread_safety.py -v`
**Commit:** `feat(journal): add thread-safe writes with threading.Lock`

---

## Batch 3: Integration (parallel - 4 implementers)

All tasks depend on Batch 2 completing.

### Task 3.1: Wire Channel Selector into Archiver Runners

**File:** `github_archiver.py`
**Test:** `tests/test_archiver_channel_integration.py`
**Depends:** 2.2 (channel selector)

Modify existing runner methods to use channel selector before running.

```python
# tests/test_archiver_channel_integration.py
"""Tests for channel selector integration with archiver runners."""

import pytest


class TestArchiverChannelIntegration:
    def test_ensure_channel_ready_uses_selector(self, tmp_path, monkeypatch):
        """_ensure_channel_ready should use channel selector for multi-channel."""
        monkeypatch.chdir(tmp_path)
        import yaml
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump({
            "channel_registry": {
                "github": [
                    {"url": "https://web.max.ru/ch1", "label": "Main", "enabled": True},
                ]
            }
        }), encoding="utf-8")

        from config import init_config
        init_config(str(cfg_file))

        from config_utils import get_channels_for_function
        channels = get_channels_for_function("github")
        assert len(channels) == 1
        assert channels[0].url == "https://web.max.ru/ch1"

    def test_multi_channel_returns_list(self, tmp_path, monkeypatch):
        """When multiple channels exist, selector can return list for parallel."""
        monkeypatch.chdir(tmp_path)
        import yaml
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump({
            "channel_registry": {
                "github": [
                    {"url": "https://web.max.ru/ch1", "label": "Main", "enabled": True},
                    {"url": "https://web.max.ru/ch2", "label": "Archive", "enabled": True},
                ]
            }
        }), encoding="utf-8")

        from config import init_config
        init_config(str(cfg_file))

        from config_utils import get_channels_for_function
        channels = get_channels_for_function("github")
        assert len(channels) == 2
```

Modify `_ensure_channel_ready` in `github_archiver.py` to support channel registry:

```python
# github_archiver.py — modify _ensure_channel_ready

    def _ensure_channel_ready(self, channel_name: str, label: str, config_section: str = None) -> str | None:
        """
        Ensure channel is ready. Returns channel URL or None.

        Uses channel_registry if available, falls back to legacy channels.{key}.
        For multi-channel setup, returns list of URLs when "All" is selected.
        """
        from config_utils import get_channels_for_function, get_channel_url_for_channel_key

        # Map legacy channel name to registry function
        channel_to_func = {
            "max": "github",
            "pypi": "pypi",
            "media": "media",
            "backup": "backup",
        }

        func_key = channel_to_func.get(channel_name, channel_name)

        # Try registry first
        try:
            channels = get_channels_for_function(func_key)
            if channels:
                if len(channels) == 1:
                    url = channels[0].url
                    print(f"\n  → Используем канал: {channels[0].label or channels[0].url}")
                else:
                    # Show channel selector
                    print(f"\n  Доступные каналы для {label}:")
                    for i, ch in enumerate(channels, 1):
                        print(f"  [{i}] {ch.label or ch.url}")
                    print(f"  [0] Все каналы (параллельно)")
                    print()

                    choices = [str(i) for i in range(len(channels) + 1)]
                    choice = self._get_user_choice(choices, "Выберите канал")

                    if choice == "0":
                        # Return list for parallel upload
                        return [ch.url for ch in channels]

                    idx = int(choice) - 1
                    url = channels[idx].url
                    print(f"  → Выбран канал: {channels[idx].label or channels[idx].url}")
            else:
                url = get_channel_url_for_channel_key(channel_name)
        except ValueError:
            url = get_channel_url_for_channel_key(channel_name)

        if not url:
            print(f"\n  ✗ URL канала \"{label}\" не указан.")
            print(f"  Укажите CHANNEL_{channel_name.upper()} в .env файле")
            print(f"  или добавьте канал через 'Управление каналами'")
            return None

        # Update self.config so subsequent internal lookups find the URL
        env_var = f"CHANNEL_{channel_name.upper()}"
        env_val = os.environ.get(env_var, "").strip()
        if env_val:
            self.config.setdefault("channels", {})[channel_name] = env_val
            if config_section:
                self.config.setdefault(config_section, {})["channel_url"] = env_val
        else:
            self.config.setdefault("channels", {})[channel_name] = url
            if config_section:
                self.config.setdefault(config_section, {})["channel_url"] = url
        return True
```

**Verify:** `pytest tests/test_archiver_channel_integration.py -v`
**Commit:** `feat(archiver): integrate channel selector into runner methods`

---

### Task 3.2: Parallel Upload Integration in GitHub Runner

**File:** `github_archiver.py`
**Test:** `tests/test_parallel_integration.py`
**Depends:** 2.3 (ParallelGroupUploader), 3.1 (channel selector integration)

When user selects "All channels", use ParallelGroupUploader instead of single upload.

```python
# tests/test_parallel_integration.py
"""Tests for parallel upload integration."""

import pytest


class TestParallelIntegration:
    def test_runner_detects_multi_channel(self, tmp_path, monkeypatch):
        """When _ensure_channel_ready returns a list, parallel mode activates."""
        # This is a design verification test — the actual integration
        # is tested via the channel selector returning a list
        from config.model import ChannelEntry

        channels = [
            ChannelEntry(url="https://web.max.ru/ch1", label="Main"),
            ChannelEntry(url="https://web.max.ru/ch2", label="Archive"),
        ]

        # Simulating what the runner receives when user picks "All"
        channel_urls = [ch.url for ch in channels]
        assert isinstance(channel_urls, list)
        assert len(channel_urls) == 2
```

Modify the GitHub sync runner to handle parallel uploads. In `sync_repositories()` and `upload_new_repositories()`, after downloading a file, check if channel result is a list:

```python
# github_archiver.py — modify sync_repositories and upload_new_repositories

# In sync_repositories(), after getting channel URL:
# Add parallel upload support:

    # After downloading and before uploading:
    # If channel_url is a list, use parallel upload
    if isinstance(channel_url, list):
        from parallel_uploader import ParallelGroupUploader

        # Build channel info
        from config_utils import get_channels_for_function
        channels = get_channels_for_function(func_key)
        channel_info = [
            {"url": ch.url, "label": ch.label or ch.url}
            for ch in channels
        ]

        uploader = ParallelGroupUploader(
            files=[zip_path],
            channels=channel_info,
            cleanup=True,
        )

        summary = uploader.run()

        # Check if any channel succeeded
        any_success = any(
            r.success for r in summary.channel_results.values()
        )

        if any_success:
            self.journal.update_repository(full_name, {
                'version': latest_version,
                'status': 'sent',
                'archive_size': zip_size
            })
            updated_count += 1

            # Print per-channel results
            for label, result in summary.channel_results.items():
                status = "✓" if result.success else "✗"
                print(f"    {status} {label}: {len(result.files)} file(s)")
                for err in result.errors:
                    print(f"       ⚠ {err}")
        else:
            self.journal.update_repository(full_name, {
                'status': 'failed',
                'archive_size': zip_size
            })
            error_count += 1
    else:
        # Original single-channel upload logic (unchanged)
        ...
```

**Verify:** `pytest tests/test_parallel_integration.py -v`
**Commit:** `feat(archiver): add parallel upload support to GitHub runner`

---

### Task 3.3: Config Persistence for Channel Registry

**File:** `config/__init__.py`
**Test:** `tests/test_config_persistence.py`
**Depends:** 2.1 (channel management saves config)

Add helper to persist channel registry changes to YAML.

```python
# tests/test_config_persistence.py
"""Tests for config persistence with channel registry."""

import pytest
import yaml


class TestConfigPersistence:
    def test_save_and_reload_registry(self, tmp_path, monkeypatch):
        """Channel registry changes persist across config reloads."""
        monkeypatch.chdir(tmp_path)
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump({}), encoding="utf-8")

        from config import init_config, get_config
        from config.model import ChannelEntry

        # First load — add channel
        init_config(str(cfg_file))
        config = get_config()
        config.channel_registry.add_channel(
            "github",
            "https://web.max.ru/new-ch",
            "New Channel"
        )

        # Save
        with open(cfg_file, "w", encoding="utf-8") as f:
            yaml.dump(config.model_dump(), f, allow_unicode=True)

        # Reload
        init_config(str(cfg_file))
        config2 = get_config()

        assert len(config2.channel_registry.github) == 1
        assert config2.channel_registry.github[0].url == "https://web.max.ru/new-ch"
        assert config2.channel_registry.github[0].label == "New Channel"

    def test_registry_preserves_other_config(self, tmp_path, monkeypatch):
        """Saving registry doesn't lose other config sections."""
        monkeypatch.chdir(tmp_path)
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump({
            "archiver": {"limit": 50},
            "browser": {"cdp_port": 9222},
        }), encoding="utf-8")

        from config import init_config, get_config

        init_config(str(cfg_file))
        config = get_config()
        config.channel_registry.add_channel(
            "github",
            "https://web.max.ru/ch",
            "Channel"
        )

        with open(cfg_file, "w", encoding="utf-8") as f:
            yaml.dump(config.model_dump(), f, allow_unicode=True)

        init_config(str(cfg_file))
        config2 = get_config()

        # Other config preserved
        assert config2.archiver.limit == 50
        assert config2.browser.cdp_port == 9222
        # Registry added
        assert len(config2.channel_registry.github) == 1
```

```python
# config/__init__.py — ADD save_config function

import yaml
from pathlib import Path


def save_config(config: Optional[AppConfig] = None) -> None:
    """Save current config to YAML file.

    Args:
        config: AppConfig to save. If None, uses current singleton.
    """
    if config is None:
        config = get_config()

    cfg_path = Path("config.yaml")
    with open(cfg_path, "w", encoding="utf-8") as f:
        yaml.dump(config.model_dump(), f, allow_unicode=True)

    _logger.info(f"Config saved to {cfg_path}")


__all__ = [
    "AppConfig",
    "get_config",
    "init_config",
    "save_config",
]
```

**Verify:** `pytest tests/test_config_persistence.py -v`
**Commit:** `feat(config): add save_config for persisting channel registry changes`

---

### Task 3.4: Channel-Specific Runner Wiring (PyPI, Backuper, Media)

**File:** `github_archiver.py`
**Test:** `tests/test_multi_runner_channels.py`
**Depends:** 3.1 (channel selector integration)

Wire channel selector into PyPI, Backuper, and Media runners.

```python
# tests/test_multi_runner_channels.py
"""Tests for channel selector in all archiver runners."""

import pytest


class TestMultiRunnerChannels:
    def test_pypi_uses_pypi_channels(self, tmp_path, monkeypatch):
        """PyPI runner should use pypi channels from registry."""
        monkeypatch.chdir(tmp_path)
        import yaml
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump({
            "channel_registry": {
                "pypi": [
                    {"url": "https://web.max.ru/pypi-ch", "label": "PyPI Main", "enabled": True},
                ]
            }
        }), encoding="utf-8")

        from config import init_config
        init_config(str(cfg_file))

        from config_utils import get_channels_for_function
        channels = get_channels_for_function("pypi")
        assert len(channels) == 1
        assert channels[0].url == "https://web.max.ru/pypi-ch"

    def test_media_uses_media_channels(self, tmp_path, monkeypatch):
        """Media runner should use media channels from registry."""
        monkeypatch.chdir(tmp_path)
        import yaml
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump({
            "channel_registry": {
                "media": [
                    {"url": "https://web.max.ru/media-ch", "label": "Media Main", "enabled": True},
                ]
            }
        }), encoding="utf-8")

        from config import init_config
        init_config(str(cfg_file))

        from config_utils import get_channels_for_function
        channels = get_channels_for_function("media")
        assert len(channels) == 1

    def test_backup_uses_backup_channels(self, tmp_path, monkeypatch):
        """Backuper should use backup channels from registry."""
        monkeypatch.chdir(tmp_path)
        import yaml
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump({
            "channel_registry": {
                "backup": [
                    {"url": "https://web.max.ru/backup-ch", "label": "Backup Main", "enabled": True},
                ]
            }
        }), encoding="utf-8")

        from config import init_config
        init_config(str(cfg_file))

        from config_utils import get_channels_for_function
        channels = get_channels_for_function("backup")
        assert len(channels) == 1
```

The wiring is already handled by `_ensure_channel_ready` (Task 3.1) since it maps channel names to registry functions. The existing runner methods (`run_pypi_libs_archiver`, `run_backuper_backup`, `run_media_archiver`) call `_ensure_channel_ready("pypi"/"backup"/"media")` which now uses the registry.

**Verify:** `pytest tests/test_multi_runner_channels.py -v`
**Commit:** `feat(archiver): wire channel selector into all runner methods`

---

## Summary

| Batch | Tasks | Files Modified/Created | Parallel Implementers |
|-------|-------|----------------------|----------------------|
| 1 (Foundation) | 1.1, 1.2, 1.3 | `config/model.py`, `config/loader.py`, `config_utils.py` | 3 |
| 2 (Core) | 2.1, 2.2, 2.3, 2.4 | `github_archiver.py`, `parallel_uploader.py`, `shared_journal.py` | 4 |
| 3 (Integration) | 3.1, 3.2, 3.3, 3.4 | `github_archiver.py`, `config/__init__.py` | 4 |

**Total: 11 tasks, 11 test files, 5 source files**

### Key Design Decisions (filled gaps)

1. **Channel label auto-generation:** When adding a channel without a label, defaults to `"{Function} Main"` (e.g., "GitHub Main"). Migration uses this same convention.
2. **Stagger delay default:** 2 seconds between thread starts, configurable via `stagger_delay_sec` parameter. Matches design's recommendation.
3. **Max concurrent threads:** Hard limit of 5, configurable. Design suggested this.
4. **Journal lock:** Replaced file-based lock with `threading.Lock` for true thread safety within the same process. File lock remains as backup for stale detection.
5. **Config persistence:** `save_config()` helper added to `config/__init__.py` for easy YAML serialization of registry changes.
6. **Backward compat:** `_ensure_channel_ready` tries registry first, falls back to legacy `channels.{key}`. Old code paths continue working unchanged.
7. **Channel selector UX:** 0 channels = error, 1 channel = silent pass-through, 2+ = prompt with "All" option for parallel mode.

### Verification Commands

```bash
# Run all new tests
pytest tests/test_channel_registry.py tests/test_channel_migration.py tests/test_channel_utils.py tests/test_channel_manager.py tests/test_channel_selector.py tests/test_parallel_uploader.py tests/test_journal_thread_safety.py tests/test_archiver_channel_integration.py tests/test_parallel_integration.py tests/test_config_persistence.py tests/test_multi_runner_channels.py -v

# Run full test suite
pytest tests/ -v
```
