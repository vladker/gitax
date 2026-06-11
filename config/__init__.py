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
    config = load_config(effective_path)
    # Store path on instance for save() to use
    config._config_path_attr = effective_path or "config.yaml"
    return config


def save_config() -> None:
    """Persist current config to disk."""
    config = get_config()
    config.save()


def init_config(config_path: str) -> None:
    """Override config path and reset singleton.

    Must be called before the first get_config() call to take effect.
    Call again with a different path to reload config.
    """
    global _config_path
    _config_path = config_path
    get_config.cache_clear()


__all__ = [
    "AppConfig",
    "get_config",
    "init_config",
    "save_config",
]
