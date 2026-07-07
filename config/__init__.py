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

import threading
from typing import Optional

from config.loader import load_config
from config.model import AppConfig

# Module-level storage for config path override and singleton instance
_config_path: Optional[str] = None
_config_instance: Optional[AppConfig] = None
_config_lock = threading.Lock()


def get_config(config_path: Optional[str] = None) -> AppConfig:
    """Return the singleton AppConfig instance (thread-safe).

    Args:
        config_path: Path to config.yaml. If None, uses path from init_config()
                     or auto-discovers via find_config().

    Returns:
        Validated AppConfig singleton with env overrides applied.
    """
    global _config_instance
    if _config_instance is not None:
        return _config_instance

    with _config_lock:
        # Double-check after acquiring lock
        if _config_instance is not None:
            return _config_instance

        effective_path = config_path or _config_path
        config = load_config(effective_path)
        # Store path on instance for save() to use
        config._config_path_attr = effective_path or "config.yaml"
        _config_instance = config
        return _config_instance


def save_config() -> None:
    """Persist current config to disk."""
    config = get_config()
    config.save()


def init_config(config_path: str) -> None:
    """Override config path and reset singleton.

    Must be called before the first get_config() call to take effect.
    Call again with a different path to reload config.
    """
    global _config_path, _config_instance
    with _config_lock:
        _config_path = config_path
        _config_instance = None


__all__ = [
    "AppConfig",
    "get_config",
    "init_config",
    "save_config",
]
