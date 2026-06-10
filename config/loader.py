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
