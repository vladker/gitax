"""Startup health checks and config validation."""
from __future__ import annotations

import os
import shutil
import socket
from typing import Sequence

from config import get_config


def check_seven_zip() -> bool:
    """Check if 7-Zip executable is available."""
    cfg = get_config()
    seven_zip_exe = cfg.backuper.seven_zip_exe or "7z"
    return shutil.which(seven_zip_exe) is not None


def check_chrome_cdp(cdp_port: int = 9222) -> bool:
    """Check if Chrome CDP port is reachable."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        result = sock.connect_ex(('localhost', cdp_port))
        sock.close()
        return result == 0
    except OSError:
        return False


def check_github_token() -> bool:
    """Check if GitHub token is configured.

    Checks environment variable first, then config as fallback.
    """
    env_token = os.environ.get("GITHUB_TOKEN", "").strip()
    if env_token:
        return True
    cfg = get_config()
    return bool(cfg.github.token)


def check_channel_urls() -> Sequence[str]:
    """Check that at least one channel URL is configured.

    Returns:
        List of warnings for missing channels.
    """
    cfg = get_config()
    warnings: list[str] = []

    # Check legacy channels
    if not cfg.channels.max:
        warnings.append("  ⚠ CHANNEL_max не указана")
    if not cfg.channels.pypi:
        warnings.append("  ⚠ CHANNEL_pypi не указана")
    if not cfg.channels.media:
        warnings.append("  ⚠ CHANNEL_media не указана")
    if not cfg.channels.backup:
        warnings.append("  ⚠ CHANNEL_backup не указана")

    # Check channel registry
    registry = cfg.channel_registry
    if not registry.github and not registry.pypi and not registry.media and not registry.backup:
        warnings.append("  ⚠ В реестре каналов нет записей")

    return warnings


def run_health_checks(quiet: bool = False) -> bool:
    """Run all startup health checks.

    Args:
        quiet: If True, only return status without printing.

    Returns:
        True if all critical checks pass, False otherwise.
    """
    cfg = get_config()
    all_ok = True
    warnings: list[str] = []

    # GitHub token check (non-critical — tokenless mode supported)
    if not check_github_token():
        msg = "  ⚠ GITHUB_TOKEN не указан — работа без токена (rate limit: 10 req/min)"
        warnings.append(msg)

    # 7-Zip check
    if not check_seven_zip():
        msg = "  ✗ 7-Zip не найден в PATH"
        warnings.append(msg)
        all_ok = False

    # Chrome CDP check (only if not using local browser)
    if not cfg.archiver.use_local_browser:
        cdp_port = cfg.browser.cdp_port
        if not check_chrome_cdp(cdp_port):
            msg = f"  ✗ Chrome CDP недоступен на порту {cdp_port}"
            warnings.append(msg)
            all_ok = False

    # Channel URL warnings (non-critical)
    channel_warnings = check_channel_urls()
    warnings.extend(channel_warnings)

    if not quiet:
        if all_ok and not channel_warnings:
            print("  ✓ Все проверки пройдены")
        else:
            if warnings:
                print("\n  Проверка окружения:")
                for w in warnings:
                    print(w)
                if all_ok:
                    print("  ℹ Предупреждения не критичны, можно продолжить")
                else:
                    print("  ✗ Некоторые критические проверки не пройдены")

    return all_ok
