"""ChannelRegistryUI — CLI interface for managing channel registry."""

from __future__ import annotations

import sys
from config import get_config
from config.model import ChannelEntry


def _print_channels(channels: list[ChannelEntry], function: str) -> None:
    """Display channel list with status indicators."""
    print(f"\n  Channels for {function}:")
    if not channels:
        print("    (none configured)")
        return

    for i, ch in enumerate(channels):
        status = "✓" if ch.enabled else "✗"
        label = ch.label if ch.label else f"Channel {i+1}"
        print(f"    [{i}] {status} {label} ({ch.url})")
    print()


def _save_config() -> None:
    """Persist config changes to disk."""
    config = get_config()
    config.save()


def add_channel(function: str, url: str, label: str = "") -> None:
    """Add a new channel to the registry."""
    config = get_config()
    config.channel_registry.add_channel(function, url, label)
    _save_config()
    print(f"  ✓ Added channel to {function}: {url}")


def remove_channel(function: str, index: int) -> None:
    """Remove a channel by index."""
    config = get_config()
    channels = getattr(config.channel_registry, function, [])
    if 0 <= index < len(channels):
        removed = channels[index]
        config.channel_registry.remove_channel(function, index)
        _save_config()
        print(f"  ✓ Removed channel from {function}: {removed.url}")
    else:
        print(f"  ✗ Invalid index: {index}")


def toggle_channel(function: str, index: int) -> None:
    """Toggle enabled state of a channel."""
    config = get_config()
    config.channel_registry.toggle_channel(function, index)
    _save_config()
    channels = getattr(config.channel_registry, function, [])
    if 0 <= index < len(channels):
        status = "enabled" if channels[index].enabled else "disabled"
        print(f"  ✓ Channel {index} {status}")
    else:
        print(f"  ✗ Invalid index: {index}")


def show_channels(function: str) -> None:
    """Display all channels for a function."""
    config = get_config()
    channels = getattr(config.channel_registry, function, [])
    _print_channels(channels, function)


def select_channel(function: str, allow_add: bool = False) -> ChannelEntry | None:
    """Interactive channel selection prompt.

    Args:
        function: Function key ("github", "pypi", "media", "backup")
        allow_add: If True, show option to add a new channel inline

    Returns:
        Selected ChannelEntry, or None if cancelled/no channels
    """
    config = get_config()
    channels = config.channel_registry.get_enabled(function)

    if not channels:
        if allow_add:
            return _prompt_add_channel(function)
        print(f"\n  ✗ Нет активных каналов для {function}")
        print("  Используйте меню 'Управление каналами' чтобы добавить.")
        return None

    if len(channels) == 1 and not allow_add:
        print(f"\n  Используется канал по умолчанию: {channels[0].label or channels[0].url}")
        return channels[0]

    # Show channel list
    print(f"\n  Доступные каналы для {function}:")
    for i, ch in enumerate(channels):
        print(f"    [{i}] {ch.label or ch.url} ({ch.url})")
    
    add_idx = None
    if allow_add:
        add_idx = len(channels)
        print(f"    [{add_idx}] + Добавить новый канал")
    print()

    max_choice = add_idx if add_idx is not None else len(channels) - 1
    while True:
        try:
            choice = input(f"  Выберите канал [0-{max_choice}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  Отменено.")
            return None

        if not choice:
            choice = "0"

        try:
            idx = int(choice)
            if add_idx is not None and idx == add_idx:
                return _prompt_add_channel(function)
            if 0 <= idx < len(channels):
                return channels[idx]
            print(f"  Неверный индекс. Введите 0-{max_choice}.")
        except ValueError:
            print("  Введите число.")


def _prompt_add_channel(function: str) -> ChannelEntry | None:
    """Prompt user to add a new channel inline."""
    print()
    try:
        url = input("  URL канала: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n  Отменено.")
        return None

    if not url:
        print("  ✗ Пустой URL, отменено.")
        return None

    if not url.startswith(("http://", "https://")):
        print("  ✗ URL должен начинаться с http:// или https://")
        return None

    try:
        label = input("  Название (опционально): ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n  Отменено.")
        return None

    if not label:
        config = get_config()
        count = len(getattr(config.channel_registry, function, []))
        func_labels = {"github": "GitHub", "pypi": "PyPI", "media": "Media", "backup": "Backup"}
        label = f"{func_labels.get(function, function)} #{count + 1}"

    add_channel(function, url, label)
    # Return the newly added channel
    config = get_config()
    channels = config.channel_registry.get_enabled(function)
    return channels[-1] if channels else None


def channel_registry_menu() -> None:
    """Full channel registry management menu."""
    from config.model import VALID_CHANNEL_FUNCTIONS

    while True:
        print("\n" + "═" * 60)
        print("  Channel Registry Management")
        print("═" * 60)
        print()

        for i, func in enumerate(VALID_CHANNEL_FUNCTIONS, 1):
            config = get_config()
            channels = getattr(config.channel_registry, func, [])
            enabled = sum(1 for ch in channels if ch.enabled)
            print(f"  [{i}] {func} ({len(channels)} total, {enabled} enabled)")

        print()
        print("  [0] Back")
        print()

        try:
            choice = input("  Select function to manage [0-4]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  Menu cancelled.")
            return

        if choice == "0":
            return

        try:
            idx = int(choice) - 1
            if 0 <= idx < len(VALID_CHANNEL_FUNCTIONS):
                function = VALID_CHANNEL_FUNCTIONS[idx]
            else:
                print("  Invalid selection.")
                continue
        except ValueError:
            print("  Invalid selection.")
            continue

        _function_menu(function)


def _function_menu(function: str) -> None:
    """Management menu for a specific function's channels."""
    config = get_config()
    channels = getattr(config.channel_registry, function, [])

    while True:
        print("\n" + "─" * 60)
        print(f"  {function} channels")
        print("─" * 60)
        _print_channels(channels, function)

        print("  [1] Add new channel")
        print("  [2] Remove channel")
        print("  [3] Toggle channel (enable/disable)")
        print("  [0] Back")
        print()

        try:
            choice = input("  Select action [0-3]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  Menu cancelled.")
            return

        if choice == "0":
            return
        elif choice == "1":
            _add_channel_prompt(function)
        elif choice == "2":
            _remove_channel_prompt(function)
        elif choice == "3":
            _toggle_channel_prompt(function)


def _add_channel_prompt(function: str) -> None:
    """Prompt for and add a new channel."""
    try:
        url = input("  Channel URL: ").strip()
    except (EOFError, KeyboardInterrupt):
        return
    if not url:
        print("  ✗ Empty URL, cancelled.")
        return

    try:
        label = input("  Label (optional): ").strip()
    except (EOFError, KeyboardInterrupt):
        return

    add_channel(function, url, label)


def _remove_channel_prompt(function: str) -> None:
    """Prompt for and remove a channel."""
    config = get_config()
    channels = getattr(config.channel_registry, function, [])
    if not channels:
        print("  No channels to remove.")
        return

    try:
        idx_str = input(f"  Channel index to remove [0-{len(channels)-1}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        return

    try:
        idx = int(idx_str)
        remove_channel(function, idx)
    except ValueError:
        print("  Invalid index.")


def _toggle_channel_prompt(function: str) -> None:
    """Prompt for and toggle a channel."""
    config = get_config()
    channels = getattr(config.channel_registry, function, [])
    if not channels:
        print("  No channels to toggle.")
        return

    try:
        idx_str = input(f"  Channel index to toggle [0-{len(channels)-1}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        return

    try:
        idx = int(idx_str)
        toggle_channel(function, idx)
    except ValueError:
        print("  Invalid index.")
