# -*- coding: utf-8 -*-
"""
Shared configuration utilities for all archiver modules.

Standardizes channel URL resolution across the codebase.
"""

import os
import sys
import tempfile
from pathlib import Path
from dotenv import load_dotenv


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


def get_split_mode(config: dict, section: str, default: str = "auto") -> str:
    """
    Get split_mode from config section with fallback to default.

    Validates the value against allowed modes: auto, on, off, prompt.

    Priority: config.yaml[section].split_mode > default

    Args:
        config: Loaded config dict from config.yaml
        section: Config section name (e.g., "archiver", "pypi_libs_archiver")
        default: Default split mode if not found or invalid (default: "auto")

    Returns:
        Lowercase valid split mode string: "auto", "on", "off", or "prompt"

    Examples:
        >>> mode = get_split_mode(config, "archiver")
        >>> mode = get_split_mode(config, "pypi_libs_archiver", default="off")
    """
    VALID_MODES = {"auto", "on", "off", "prompt"}
    section_data = config.get(section, {}) or {}
    value = section_data.get("split_mode", default)
    if not isinstance(value, str) or value.lower() not in VALID_MODES:
        return default
    return value.lower()


def set_env_value(key: str, value: str):
    """
    Set or update an environment variable in .env file.

    - Reads .env line by line, preserves comments and blank lines
    - If key exists, updates the value in-place
    - If key doesn't exist, appends at end of file
    - Writes atomically (temp file + rename, matching journal pattern)
    - Calls load_dotenv(override=True) to pick up changes
    - If .env doesn't exist, creates it with a header comment

    Args:
        key: Environment variable name (e.g., "GITHUB_TOKEN")
        value: Value to set
    """
    env_path = Path(".env")

    # Create .env with header if it doesn't exist
    if not env_path.exists():
        with open(env_path, "w", encoding="utf-8") as f:
            f.write("# Environment variables for GitHub Archiver\n")
            f.write("# Automatically generated by setup wizard\n\n")

    # Read existing lines
    with open(env_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    # Try to find and update the key
    key_found = False
    new_lines = []
    for line in lines:
        stripped = line.strip()
        # Match KEY=value or KEY = value (with optional spaces around =)
        is_key_line = stripped.startswith(f"{key}=") or stripped.startswith(f"{key} =")
        if is_key_line:
            # Preserve the original spacing around =
            if stripped.startswith(f"{key} ="):
                new_lines.append(f"{key} = {value}\n")
            else:
                new_lines.append(f"{key}={value}\n")
            key_found = True
        else:
            new_lines.append(line)

    # If key not found, append it at the end
    if not key_found:
        # Ensure there's a blank line before the new entry for readability
        if new_lines and not new_lines[-1].strip() == "" and not new_lines[-1].endswith("\n\n"):
            # Add a blank line
            if new_lines[-1].endswith("\n"):
                new_lines.append("\n")
            else:
                new_lines.append("\n\n")
        new_lines.append(f"{key}={value}\n")

    # Atomic write: temp file + rename
    fd, tmp_path = tempfile.mkstemp(suffix=".env", dir=str(env_path.parent) or ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
        os.replace(tmp_path, str(env_path))
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise

    # Reload env vars to make the change immediately available in-process
    load_dotenv(dotenv_path=env_path, override=True)


def ensure_channel_url(config: dict, channel_name: str, label: str = "") -> str:
    """
    Get channel URL; if missing from both env var and config, prompt interactively
    and save to .env for persistence.

    Priority: env var > config.yaml channels section > interactive prompt

    Args:
        config: Loaded config dict (may contain channels section)
        channel_name: Channel key (e.g., "max", "pypi", "media", "backup")
        label: Human-readable label (e.g., "MAX канал")

    Returns:
        Channel URL string, or empty string if user skips the prompt
    """
    if not label:
        label = channel_name

    # 1. Check env var directly (may have been set by set_env_value in this session)
    env_var = f"CHANNEL_{channel_name.upper()}"
    current_url = os.environ.get(env_var, "").strip()

    # 2. If not in env, check config dict channels section
    if not current_url:
        channels = config.get("channels", {}) or {}
        current_url = str(channels.get(channel_name, "")).strip()

    if current_url:
        return current_url

    # 3. Interactive prompt
    print(f"\n  ⚠ URL канала \"{label}\" не указан.")
    print()
    print("  [Enter] Ввести URL сейчас (сохранится в .env)")
    print("  [S] Пропустить — функция недоступна")
    print()

    try:
        choice = input("  Ваш выбор [Enter/S]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\n  Отменено.")
        return ""

    if choice == "s":
        print(f"\n  Функция недоступна без URL канала.")
        return ""

    # Prompt for URL
    try:
        url = input(f"  Введите URL канала \"{label}\": ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n  Отменено.")
        return ""

    if url:
        set_env_value(env_var, url)
        current_url = os.environ.get(env_var, "").strip()
        print(f"  ✓ URL сохранён в .env")
        return current_url

    return ""


def get_skipped_channels(config: dict) -> list:
    """
    Get list of channels explicitly skipped during setup.

    Reads from config.yaml setup.skipped_channels section.

    Args:
        config: Loaded config dict (may contain 'setup' key)

    Returns:
        List of skipped channel names (e.g., ["pypi", "media"])
    """
    setup_section = config.get("setup", {}) or {}
    return setup_section.get("skipped_channels", []) or []


def is_setup_complete(config: dict) -> bool:
    """
    Check if required configuration is present.

    Required values:
    - GITHUB_TOKEN from env var only (never from config.yaml)
    - At least one non-skipped channel has a URL configured
      (from env var or config.yaml channels section)
    - OR: all 4 channels are explicitly skipped (user's choice)

    Args:
        config: Loaded config dict (may contain 'channels' and 'setup' keys)

    Returns:
        True if token exists and at least one channel is ready, or all skipped
    """
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        return False

    skipped = get_skipped_channels(config)
    channels = config.get("channels", {}) or {}

    # All channels defined in ChannelsConfig model
    ALL_CHANNELS = ("max", "pypi", "media", "backup", "npm", "cargo", "nuget", "rubygems")

    has_configured = False
    for ch_name in ALL_CHANNELS:
        if ch_name in skipped:
            continue
        env_var = f"CHANNEL_{ch_name.upper()}"
        val = os.environ.get(env_var, "").strip()
        if not val:
            val = str(channels.get(ch_name, "")).strip()
        if val:
            has_configured = True
        # NOTE: intentionally NOT returning False here — we only need
        # AT LEAST ONE channel configured (not all of them).

    # Complete if at least one non-skipped channel has a URL, or all skipped
    return has_configured or (len(skipped) >= len(ALL_CHANNELS))


# ── Thin wrapper over new config/ package ──
# Deprecated: use from config import get_config, init_config instead

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


# ── Channel Registry Utilities ──

_CHANNEL_TO_FUNCTION = {
    "max": "github",
    "pypi": "pypi",
    "media": "media",
    "backup": "backup",
    "npm": "npm",
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

    function = _CHANNEL_TO_FUNCTION.get(channel_key, channel_key)
    try:
        enabled = config.channel_registry.get_enabled(function)
        if enabled:
            return enabled[0].url
    except (ValueError, AttributeError):
        pass

    old_url = getattr(config.channels, channel_key, "")
    if old_url:
        return old_url

    return ""
