# -*- coding: utf-8 -*-
"""
Shared configuration utilities for all archiver modules.

Standardizes channel URL resolution across the codebase.
"""

import os
import sys


def get_channel_url(config: dict, channel_name: str, env_prefix: str = "CHANNEL",
                    required: bool = True, label: str = "") -> str:
    """
    Resolve channel URL with standardized priority chain.

    Priority:
    1. Environment variable: {ENV_PREFIX}_{CHANNEL_NAME_UPPER}
       (e.g., CHANNEL_MAX, CHANNEL_PYPI, CHANNEL_BACKUP)
    2. config.yaml: channels.{channel_name}
       (e.g., channels.max, channels.pypi, channels.backup)

    Args:
        config: Loaded config dict from config.yaml
        channel_name: Channel key name (e.g., "max", "pypi", "media", "backup")
        env_prefix: Env var prefix (default: "CHANNEL")
        required: If True and URL not found, print error and sys.exit(1)
        label: Human-readable label for error messages (default: channel_name)

    Returns:
        Channel URL string (never empty if required=True)

    Examples:
        >>> url = get_channel_url(config, "max", label="MAX канал")
        >>> url = get_channel_url(config, "backup", required=False)
    """
    if not label:
        label = channel_name

    # 1. Check environment variable: CHANNEL_max, CHANNEL_pypi, etc.
    env_var = f"{env_prefix}_{channel_name.upper()}"
    env_url = os.environ.get(env_var) or ""
    env_url = env_url.strip()
    if env_url:
        return env_url

    # 2. Check config.yaml: channels.max, channels.pypi, etc.
    channels = config.get("channels", {}) or {}
    yaml_url = channels.get(channel_name) or ""
    yaml_url = yaml_url.strip()
    if yaml_url:
        return yaml_url

    # 3. Not found
    if required:
        print(f"\u2717 URL канала \"{label}\" не указан.")
        print(f"  Укажите CHANNEL_{channel_name.upper()} в .env файле")
        print(f"  или channels.{channel_name} в config.yaml")
        sys.exit(1)

    return ""


def get_config_value(config: dict, section: str, key: str, default=None,
                     env_var: str = None) -> object:
    """
    Get a config value with env var override.

    Priority: env var > config.yaml[section][key] > default

    Args:
        config: Loaded config dict from config.yaml
        section: Config section name (e.g., "archiver", "backuper")
        key: Key within section (e.g., "retries", "output_dir")
        default: Default value if not found anywhere
        env_var: Optional environment variable name to check first

    Returns:
        Config value (of any type)

    Examples:
        >>> retries = get_config_value(config, "archiver", "retries", default=3)
        >>> output = get_config_value(config, "backuper", "output_dir",
        ...                           default="./temp_backups",
        ...                           env_var="BACKUP_OUTPUT_DIR")
    """
    # 1. Check environment variable if provided
    if env_var:
        env_val = os.environ.get(env_var, "").strip()
        if env_val:
            return env_val

    # 2. Check config.yaml
    section_data = config.get(section, {})
    if section_data and key in section_data:
        return section_data[key]

    # 3. Default
    return default
