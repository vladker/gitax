# Initial Setup & Lazy Config Prompt — Implementation Plan

**Goal:** Add an interactive setup wizard and lazy config prompts to GitHub Archiver, eliminating crashes from missing config.

**Architecture:** Three new functions in `config_utils.py` (`set_env_value`, `ensure_channel_url`, `is_setup_complete`) handle .env file manipulation and config state queries. `GitHubArchiver` gains `_initial_setup()` wizard, state-aware menus, auto-prompt on first launch, and lazy URL prompts in all 11 upload/export functions. The `_load_config()` method changes channel URL loading from `required=True` to `required=False` so missing URLs don't crash at startup.

**Design:** `thoughts/shared/designs/2026-06-10-initial-setup-design.md`

---

## Dependency Graph

```
Batch 1 (parallel — 1 task): 1.1  [foundation — config_utils.py + tests]
Batch 2 (depends on 1):      2.1  [github_archiver.py — wizard, menus, lazy prompts]
Batch 3 (verification):       3.1  [manual verification checklist]
```

---

## Batch 1: Foundation (parallel — 1 implementer)

### Task 1.1: config_utils — new utility functions
**File:** `config_utils.py` (modify — add 3 functions + imports)
**Test:** `tests/test_config_utils.py` (create)
**Depends:** none

**Design decisions:**
- `set_env_value()` uses `Path(".env")` (hardcoded to project root, matching existing convention). Atomic write via `tempfile.mkstemp` + `shutil.move` matching the journal.py pattern.
- `ensure_channel_url()` checks env var → config dict → interactive prompt. On URL entry calls `set_env_value()` + `load_dotenv(override=True)` to make the value immediately available to the same session.
- `is_setup_complete()` checks `GITHUB_TOKEN` via env var only (never in yaml), and all 4 `CHANNEL_*` via env var or config.yaml channels section.

**Imports to add to config_utils.py:**
```python
import tempfile
import shutil
from pathlib import Path
from dotenv import load_dotenv
```

#### Test file: `tests/test_config_utils.py`

```python
# -*- coding: utf-8 -*-
"""
Tests for config_utils — set_env_value, is_setup_complete, ensure_channel_url
"""

import os
import pytest


class TestSetEnvValue:
    """Tests for set_env_value() — .env file manipulation"""

    def test_set_env_value_new_key(self, tmp_path, monkeypatch):
        """Add a new key to an existing .env file"""
        monkeypatch.chdir(tmp_path)
        from config_utils import set_env_value

        # Create a .env with existing content
        env_file = tmp_path / ".env"
        env_file.write_text("# Existing config\nEXISTING_KEY=old_value\n", encoding="utf-8")

        set_env_value("NEW_KEY", "new_value")

        content = env_file.read_text(encoding="utf-8")
        assert "NEW_KEY=new_value" in content
        assert "EXISTING_KEY=old_value" in content
        assert os.environ.get("NEW_KEY") == "new_value"

    def test_set_env_value_update_key(self, tmp_path, monkeypatch):
        """Update an existing key in .env"""
        monkeypatch.chdir(tmp_path)
        from config_utils import set_env_value

        env_file = tmp_path / ".env"
        env_file.write_text("MY_KEY=old_value\n", encoding="utf-8")

        set_env_value("MY_KEY", "new_value")

        content = env_file.read_text(encoding="utf-8")
        assert "MY_KEY=new_value" in content
        assert "MY_KEY=old_value" not in content
        assert os.environ.get("MY_KEY") == "new_value"

    def test_set_env_value_new_file(self, tmp_path, monkeypatch):
        """Create .env from scratch if it doesn't exist"""
        monkeypatch.chdir(tmp_path)
        from config_utils import set_env_value

        assert not (tmp_path / ".env").exists()

        set_env_value("FRESH_KEY", "fresh_value")

        assert (tmp_path / ".env").exists()
        content = (tmp_path / ".env").read_text(encoding="utf-8")
        assert "FRESH_KEY=fresh_value" in content
        assert os.environ.get("FRESH_KEY") == "fresh_value"

    def test_set_env_value_preserve_comments(self, tmp_path, monkeypatch):
        """Preserve comments and blank lines in .env"""
        monkeypatch.chdir(tmp_path)
        from config_utils import set_env_value

        original = (
            "# This is a header comment\n"
            "\n"
            "# GitHub token\n"
            "GITHUB_TOKEN=old_token\n"
            "\n"
            "# Channel URLs\n"
            "CHANNEL_max=https://old.url\n"
        )
        env_file = tmp_path / ".env"
        env_file.write_text(original, encoding="utf-8")

        set_env_value("GITHUB_TOKEN", "new_token")

        content = env_file.read_text(encoding="utf-8")
        # Comments preserved
        assert "# This is a header comment" in content
        assert "# GitHub token" in content
        assert "# Channel URLs" in content
        # Blank lines preserved
        assert "\n\n" in content
        # Value updated
        assert "GITHUB_TOKEN=new_token" in content
        assert "GITHUB_TOKEN=old_token" not in content

    def test_set_env_value_same_value(self, tmp_path, monkeypatch):
        """Setting the same value does not corrupt the file"""
        monkeypatch.chdir(tmp_path)
        from config_utils import set_env_value

        env_file = tmp_path / ".env"
        env_file.write_text("KEY=value\n", encoding="utf-8")

        set_env_value("KEY", "value")

        content = env_file.read_text(encoding="utf-8")
        assert content.strip() == "KEY=value"

    def test_set_env_value_with_spaces_around_equals(self, tmp_path, monkeypatch):
        """Handle KEY = value format with spaces around ="""
        monkeypatch.chdir(tmp_path)
        from config_utils import set_env_value

        env_file = tmp_path / ".env"
        env_file.write_text("KEY = old_value\n", encoding="utf-8")

        set_env_value("KEY", "new_value")

        content = env_file.read_text(encoding="utf-8")
        # Should preserve the space-around-equals format
        assert "KEY = new_value" in content


class TestIsSetupComplete:
    """Tests for is_setup_complete()"""

    def test_all_values_present_via_env(self, monkeypatch):
        """Returns True when all values are set via env vars"""
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_test123")
        monkeypatch.setenv("CHANNEL_max", "https://max.example.com/max")
        monkeypatch.setenv("CHANNEL_pypi", "https://max.example.com/pypi")
        monkeypatch.setenv("CHANNEL_media", "https://max.example.com/media")
        monkeypatch.setenv("CHANNEL_backup", "https://max.example.com/backup")

        from config_utils import is_setup_complete
        assert is_setup_complete({}) is True

    def test_all_values_present_via_config(self, monkeypatch):
        """Returns True when channels are in config dict"""
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_test123")
        # Channels in config, not env
        monkeypatch.delenv("CHANNEL_max", raising=False)
        monkeypatch.delenv("CHANNEL_pypi", raising=False)
        monkeypatch.delenv("CHANNEL_media", raising=False)
        monkeypatch.delenv("CHANNEL_backup", raising=False)

        config = {
            "channels": {
                "max": "https://max.example.com/max",
                "pypi": "https://max.example.com/pypi",
                "media": "https://max.example.com/media",
                "backup": "https://max.example.com/backup",
            }
        }

        from config_utils import is_setup_complete
        assert is_setup_complete(config) is True

    def test_missing_token(self, monkeypatch):
        """Returns False when GITHUB_TOKEN is missing"""
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.setenv("CHANNEL_max", "url")
        monkeypatch.setenv("CHANNEL_pypi", "url")
        monkeypatch.setenv("CHANNEL_media", "url")
        monkeypatch.setenv("CHANNEL_backup", "url")

        from config_utils import is_setup_complete
        assert is_setup_complete({}) is False

    def test_missing_one_channel(self, monkeypatch):
        """Returns False when one CHANNEL_* is missing"""
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_test123")
        monkeypatch.setenv("CHANNEL_max", "url")
        monkeypatch.setenv("CHANNEL_pypi", "url")
        monkeypatch.setenv("CHANNEL_media", "url")
        monkeypatch.delenv("CHANNEL_backup", raising=False)

        from config_utils import is_setup_complete
        assert is_setup_complete({}) is False

    def test_missing_all_channels(self, monkeypatch):
        """Returns False when no channels are set"""
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_test123")
        monkeypatch.delenv("CHANNEL_max", raising=False)
        monkeypatch.delenv("CHANNEL_pypi", raising=False)
        monkeypatch.delenv("CHANNEL_media", raising=False)
        monkeypatch.delenv("CHANNEL_backup", raising=False)

        from config_utils import is_setup_complete
        assert is_setup_complete({}) is False

    def test_empty_strings_not_valid(self, monkeypatch):
        """Empty string values don't count as configured"""
        monkeypatch.setenv("GITHUB_TOKEN", "")
        monkeypatch.setenv("CHANNEL_max", "url")
        monkeypatch.setenv("CHANNEL_pypi", "url")
        monkeypatch.setenv("CHANNEL_media", "url")
        monkeypatch.setenv("CHANNEL_backup", "url")

        from config_utils import is_setup_complete
        assert is_setup_complete({}) is False

    def test_partial_config_via_both_sources(self, monkeypatch):
        """Mix of env and config dict sources"""
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_test123")
        monkeypatch.setenv("CHANNEL_max", "url_from_env")
        monkeypatch.delenv("CHANNEL_pypi", raising=False)
        monkeypatch.delenv("CHANNEL_media", raising=False)
        monkeypatch.delenv("CHANNEL_backup", raising=False)

        config = {
            "channels": {
                "pypi": "url_from_config",
                "media": "url_from_config",
                "backup": "url_from_config",
            }
        }

        from config_utils import is_setup_complete
        assert is_setup_complete(config) is True
```

#### Implementation changes: `config_utils.py`

Add these imports at the top (after existing `import sys`):
```python
import tempfile
import shutil
from pathlib import Path
from dotenv import load_dotenv
```

Add these three functions at the end of the file (before any trailing whitespace):

```python
# ──────────────────────────────────────────────
# Initial Setup & Lazy Config
# ──────────────────────────────────────────────


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
        # shutil.move handles cross-device moves (temp may be on different drive)
        shutil.move(tmp_path, str(env_path))
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise

    # Reload env vars to make the change immediately available in-process
    load_dotenv(override=True)


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
    print(f"\n  \u26a0 URL \u043a\u0430\u043d\u0430\u043b\u0430 \"{label}\" \u043d\u0435 \u0443\u043a\u0430\u0437\u0430\u043d.")
    print()
    print("  [Enter] \u0412\u0432\u0435\u0441\u0442\u0438 URL \u0441\u0435\u0439\u0447\u0430\u0441 (\u0441\u043e\u0445\u0440\u0430\u043d\u0438\u0442\u0441\u044f \u0432 .env)")
    print("  [S] \u041f\u0440\u043e\u043f\u0443\u0441\u0442\u0438\u0442\u044c \u2014 \u0444\u0443\u043d\u043a\u0446\u0438\u044f \u043d\u0435\u0434\u043e\u0441\u0442\u0443\u043f\u043d\u0430")
    print()

    try:
        choice = input("  \u0412\u0430\u0448 \u0432\u044b\u0431\u043e\u0440 [Enter/S]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\n  \u041e\u0442\u043c\u0435\u043d\u0435\u043d\u043e.")
        return ""

    if choice == "s":
        print(f"\n  \u0424\u0443\u043d\u043a\u0446\u0438\u044f \u043d\u0435\u0434\u043e\u0441\u0442\u0443\u043f\u043d\u0430 \u0431\u0435\u0437 URL \u043a\u0430\u043d\u0430\u043b\u0430.")
        return ""

    # Prompt for URL
    try:
        url = input(f"  \u0412\u0432\u0435\u0434\u0438\u0442\u0435 URL \u043a\u0430\u043d\u0430\u043b\u0430 \"{label}\": ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n  \u041e\u0442\u043c\u0435\u043d\u0435\u043d\u043e.")
        return ""

    if url:
        set_env_value(env_var, url)
        # Re-read env — set_env_value calls load_dotenv(override=True)
        current_url = os.environ.get(env_var, "").strip()
        print(f"  \u2713 URL \u0441\u043e\u0445\u0440\u0430\u043d\u0451\u043d \u0432 .env")
        return current_url

    return ""


def is_setup_complete(config: dict) -> bool:
    """
    Check if required configuration is present.

    Required values:
    - GITHUB_TOKEN from env var only (never from config.yaml — token is secret)
    - CHANNEL_max, CHANNEL_pypi, CHANNEL_media, CHANNEL_backup from env var
      or config.yaml channels section

    Args:
        config: Loaded config dict (may contain 'channels' key)

    Returns:
        True only if all 5 values are non-empty
    """
    # GITHUB_TOKEN must be set as env var (never in yaml)
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        return False

    # Channel URLs: check env var first, then config.yaml channels section
    channels = config.get("channels", {}) or {}

    for ch_name in ("max", "pypi", "media", "backup"):
        env_var = f"CHANNEL_{ch_name.upper()}"
        val = os.environ.get(env_var, "").strip()
        if not val:
            val = str(channels.get(ch_name, "")).strip()
        if not val:
            return False

    return True
```

**Note:** The `ensure_channel_url` implementation above uses Unicode escapes for Cyrillic text. In the actual file, use the literal Cyrillic characters as shown in the design:
- `⚠ URL канала "... не указан.`
- `[Enter] Ввести URL сейчас (сохранится в .env)`
- `[S] Пропустить — функция недоступна`
- `Ваш выбор [Enter/S]:`
- `Отменено.`
- `Функция недоступна без URL канала.`
- `Введите URL канала "XXX":`
- `✓ URL сохранён в .env`

**Verify tests:** `pytest tests/test_config_utils.py -v`
**Commit:** `feat(config): add set_env_value, ensure_channel_url, is_setup_complete`

---

## Batch 2: Core Changes (depends on Batch 1)

### Task 2.1: github_archiver — wizard, menus, lazy prompts
**File:** `github_archiver.py` (modify — multiple changes throughout)
**Test:** none (manual tests per design)
**Depends:** 1.1 (imports `is_setup_complete` from `config_utils`)

**Design decisions:**
- `_load_config()` changes `required=True` → `required=False` for channel URLs. Token still `sys.exit(1)` if missing.
- `_show_main_menu()` is state-aware: before setup shows `[0] ⚡ Начальная настройка` + `[X] Выход`, after setup shows `[1]-[5]` + `[0] Выход`.
- `_service_menu()` adds `[2] ⚙ Настройки` after setup complete (re-runs setup wizard).
- `_show_auto_prompt()` shows welcome banner on first launch when setup incomplete, offers `[Enter]` to run wizard or `[S]` to skip.
- `_initial_setup()` is a 6-step wizard: GitHub token → 4 channel URLs → archiver params. Steps 1-5 write to `.env` via `set_env_value()`. Step 6 writes to `config.yaml` (merge, not overwrite). After completion: reloads config, status updates.
- `_ensure_channel_ready()` is a helper for lazy prompts: calls `ensure_channel_url()`, updates `self.config` with the result so subsequent code finds it.
- All 11 upload/export wrapper methods get a lazy prompt guard at the top.

#### Complete list of edits to `github_archiver.py`:

**Edit 1 — Import line (line 25):**
Change:
```python
from config_utils import get_channel_url
```
To:
```python
from config_utils import get_channel_url, is_setup_complete
```

**Edit 2 — `_load_config()` (line 255):**
Change:
```python
        channel_url = get_channel_url(config, "max", label="MAX канал")
```
To:
```python
        channel_url = get_channel_url(config, "max", label="MAX канал", required=False)
```

**Edit 3 — `_show_main_menu()` (lines 360-372):**
Replace the entire method body with state-aware version:
```python
    def _show_main_menu(self):
        """Показать главное меню"""
        self._show_header()

        if not is_setup_complete(self.config):
            print("\n  ⚡ Требуется начальная настройка")
            print()
            print("  [0] ⚡ Начальная настройка")
        else:
            print()

        ignored_count = self.journal.get_ignored_count()
        ignored_str = f" ({ignored_count} в игноре)" if ignored_count else ""
        print("  [1] GitHub — репозитории")
        print("  [2] PyPI — Python библиотеки")
        print("  [3] Backuper — бэкап папок в канал")
        print("  [4] Файлы — медиа, скачивание, экспорт")
        print("  [5] Сервис — журналы, настройки")

        if not is_setup_complete(self.config):
            print("  [X] Выход")
        else:
            print("  [0] Выход")

        print()
```

**Edit 4 — `_service_menu()` (lines 413-421):**
Replace the method body to add settings option after setup:
```python
    def _service_menu(self):
        """Подменю Сервис"""
        print("\n" + "═" * 60)
        print("  Сервис — журналы, настройки")
        print("─" * 60)
        print()
        print("  [1] Очистить журналы")
        if is_setup_complete(self.config):
            print("  [2] ⚙ Настройки")
        print("  [0] Назад")
        print()
```

**Edit 5 — `_run_service_menu()` (lines 2243-2256):**
Replace to handle settings choice:
```python
    def _run_service_menu(self):
        """Цикл подменю Сервис"""
        while True:
            os.system('cls' if os.name == 'nt' else 'clear')
            self._service_menu()
            setup_done = is_setup_complete(self.config)
            prompt = "[0-2]" if setup_done else "[0-1]"
            choice = input(f"  Выберите действие {prompt}: ").strip()

            if choice == '0':
                break
            elif choice == '1':
                self._manage_journals()
            elif choice == '2' and setup_done:
                self._initial_setup()
            else:
                print("\n  Неверный выбор.")
                time.sleep(1)
```

**Edit 6 — `run()` (lines 2128-2154):**
Replace to add auto-prompt + state-aware dispatch:
```python
    def run(self):
        """Запустить главный цикл программы"""

        # Auto-prompt on first launch if setup is incomplete
        if not is_setup_complete(self.config):
            self._show_auto_prompt()

        while True:
            os.system('cls' if os.name == 'nt' else 'clear')

            self._show_main_menu()

            needs_setup = not is_setup_complete(self.config)
            prompt = "[0/X,1-5]" if needs_setup else "[0-5]"
            choice = input(f"  Выберите раздел {prompt}: ").strip().lower()

            # ── State-aware dispatch ──
            if needs_setup and choice == '0':
                self._initial_setup()
            elif needs_setup and choice == 'x':
                print("\n  До свидания!\n")
                break
            elif not needs_setup and choice == '0':
                print("\n  До свидания!\n")
                break
            elif choice == '1':
                self._run_github_menu()
            elif choice == '2':
                self._run_pypi_menu()
            elif choice == '3':
                self._run_backuper_menu()
            elif choice == '4':
                self._run_files_menu()
            elif choice == '5':
                self._run_service_menu()
            else:
                print("\n  Неверный выбор. Нажмите 0..5.")
                time.sleep(1)
```

**Edit 7 — Add helper method `_ensure_channel_ready`** (add after `_check_orphaned_files()` or near other helper methods, e.g., after line 238):
```python
    def _ensure_channel_ready(self, channel_name: str, label: str,
                              config_section: str = None) -> bool:
        """
        Ensure a channel URL is configured. If missing, prompt interactively
        and save to .env. Updates self.config so subsequent code finds the URL.

        Args:
            channel_name: Channel key name (e.g., "max", "pypi", "media", "backup")
            label: Human-readable label for prompts (e.g., "MAX канал")
            config_section: Optional section in self.config to update
                            (e.g., "max" for self.config['max']['channel_url'])

        Returns:
            True if URL is available (after optional prompt), False if user skips
        """
        from config_utils import ensure_channel_url
        url = ensure_channel_url(self.config, channel_name, label)
        if not url:
            return False

        # Update self.config so subsequent internal lookups find the URL
        env_var = f"CHANNEL_{channel_name.upper()}"
        env_val = os.environ.get(env_var, "").strip()
        if env_val:
            self.config.setdefault("channels", {})[channel_name] = env_val
            if config_section:
                self.config.setdefault(config_section, {})["channel_url"] = env_val
        return True
```

**Edit 8 — Add `_show_auto_prompt()` method** (add before `run()` or with other UI methods):
```python
    def _show_auto_prompt(self):
        """Показать приветствие при первом запуске без настройки"""
        print("\n" + "╔" + "═" * 56 + "╗")
        print("║               ДОБРО ПОЖАЛОВАТЬ В GITHUB ARCHIVER             ║")
        print("║" + " " * 58 + "║")
        print("║  Программа не настроена. Для работы необходимо указать:      ║")
        print("║  • GitHub токен для доступа к API                            ║")
        print("║  • URL каналов MAX для разных типов архивов                  ║")
        print("║" + " " * 58 + "║")
        print("║  [Enter] Выполнить начальную настройку                       ║")
        print("║  [S] Пропустить (пункт настройки будет в меню)               ║")
        print("╚" + "═" * 56 + "╝")
        print()

        try:
            choice = input("  Ваш выбор [Enter/S]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return

        if choice != "s":
            self._initial_setup()
```

**Edit 9 — Add `_initial_setup()` wizard method:**
```python
    def _initial_setup(self):
        """Интерактивный мастер начальной настройки (6 шагов)"""
        import yaml
        import tempfile
        import shutil
        from config_utils import set_env_value as _set_env

        print("\n" + "═" * 60)
        print("        НАЧАЛЬНАЯ НАСТРОЙКА")
        print("═" * 60)
        print()

        # ── Шаг 1: GitHub токен ──
        current_token = os.environ.get("GITHUB_TOKEN", "")
        if len(current_token) > 8:
            masked = current_token[:4] + "*" * (len(current_token) - 8) + current_token[-4:]
        elif current_token:
            masked = current_token[:4] + "****"
        else:
            masked = "(не указан)"
        print(f"  Шаг 1 из 6: GitHub токен")
        print(f"  Текущее: {masked}")
        try:
            val = input("  Введите токен (Enter = оставить): ").strip()
        except (EOFError, KeyboardInterrupt):
            val = ""
        if val:
            _set_env("GITHUB_TOKEN", val)
        elif not current_token:
            print("  ⚠ Токен не указан. Программа не сможет работать с GitHub API.")

        # ── Шаги 2-5: URL каналов ──
        channel_steps = [
            ("max",   "MAX канал (GitHub архивы)"),
            ("pypi",  "PyPI канал"),
            ("media", "Media канал"),
            ("backup","Backup канал"),
        ]

        for step_num, (ch_name, ch_label) in enumerate(channel_steps, 2):
            env_var = f"CHANNEL_{ch_name.upper()}"
            current_url = os.environ.get(env_var, "")
            if not current_url:
                channels = self.config.get("channels", {}) or {}
                current_url = channels.get(ch_name, "")
            display = current_url if current_url else "(не указан)"
            print(f"\n  Шаг {step_num} из 6: {ch_label}")
            print(f"  Текущее: {display}")
            try:
                val = input("  Введите URL (Enter = оставить): ").strip()
            except (EOFError, KeyboardInterrupt):
                val = ""
            if val:
                _set_env(env_var, val)

        # ── Шаг 6: Параметры архивации ──
        print(f"\n  Шаг 6 из 6: Параметры архивации")
        archiver_cfg = self.config.get("archiver", {})

        try:
            limit_str = input(f"  Лимит репозиториев [{archiver_cfg.get('limit', 100)}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            limit_str = ""
        try:
            retries_str = input(f"  Retries [{archiver_cfg.get('retries', 3)}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            retries_str = ""
        try:
            delay_str = input(f"  Задержка между репо (сек) [{archiver_cfg.get('repo_delay', 30)}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            delay_str = ""
        try:
            split_str = input(f"  Порог разделения (MB) [{archiver_cfg.get('split_threshold_mb', 49)}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            split_str = ""

        # Write step 6 to config.yaml (merge, preserve existing keys)
        yaml_config = {}
        if os.path.exists("config.yaml"):
            with open("config.yaml", "r", encoding="utf-8") as f:
                yaml_config = yaml.safe_load(f) or {}

        yaml_config.setdefault("archiver", {})
        if limit_str:
            yaml_config["archiver"]["limit"] = int(limit_str)
        if retries_str:
            yaml_config["archiver"]["retries"] = int(retries_str)
        if delay_str:
            yaml_config["archiver"]["repo_delay"] = int(delay_str)
        if split_str:
            yaml_config["archiver"]["split_threshold_mb"] = int(split_str)

        # Atomic write for config.yaml
        fd, tmp_path = tempfile.mkstemp(suffix=".yaml", dir=".")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                yaml.dump(yaml_config, f, default_flow_style=False, allow_unicode=True)
            shutil.move(tmp_path, "config.yaml")
        except Exception:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise

        # ── Reload config to pick up all changes ──
        load_dotenv(override=True)
        self.config = self._load_config("config.yaml")

        print(f"\n  ✓ Настройка завершена!")
        print(f"  Переменные сохранены в .env и config.yaml")
        input("\n  Нажмите Enter для продолжения...")
```

**Edit 10 — Lazy prompt guards in wrapper methods:**

For each of these methods, add at the top (after the docstring / print header, before any logic):

**`sync_repositories()`** — after line 435:
```python
        if not self._ensure_channel_ready("max", "MAX канал", "max"):
            input("\n  Нажмите Enter для возврата в меню...")
            return
```

**`load_new_repositories()`** — after line 620:
```python
        if not self._ensure_channel_ready("max", "MAX канал", "max"):
            input("\n  Нажмите Enter для возврата в меню...")
            return
```

**`audit_and_restore_publications()`** — after line 1288:
```python
        if not self._ensure_channel_ready("max", "MAX канал", "max"):
            input("\n  Нажмите Enter для возврата в меню...")
            return
```

**`run_pypi_libs_archiver()`** — after line 2260:
```python
        if not self._ensure_channel_ready("pypi", "PyPI канал", "pypi_libs"):
            input("\n  Нажмите Enter для возврата в меню...")
            return
```

**`run_pypi_libs_sync()`** — after line 2277:
```python
        if not self._ensure_channel_ready("pypi", "PyPI канал", "pypi_libs"):
            input("\n  Нажмите Enter для возврата в меню...")
            return
```

**`run_media_archiver()`** — after line 2095:
```python
        if not self._ensure_channel_ready("media", "Media канал", "media_archiver"):
            input("\n  Нажмите Enter для возврата в меню...")
            return
```

**`download_channel_files()`** — after line 2116:
```python
        if not self._ensure_channel_ready("max", "MAX канал", "max"):
            input("\n  Нажмите Enter для возврата в меню...")
            return
```

**`export_messages_to_file()`** — after line 1906:
```python
        if not self._ensure_channel_ready("max", "MAX канал", "max"):
            input("\n  Нажмите Enter для возврата в меню...")
            return
```

**`delete_all_messages_in_channel()`** — after line 2020:
```python
        if not self._ensure_channel_ready("max", "MAX канал", "max"):
            input("\n  Нажмите Enter для возврата в меню...")
            return
```

**`run_backuper_backup()`** — after line 2294:
```python
        if not self._ensure_channel_ready("backup", "Backup канал", "backup"):
            input("\n  Нажмите Enter для возврата в меню...")
            return
```

**`run_backuper_restore()`** — after line 2311:
```python
        if not self._ensure_channel_ready("backup", "Backup канал", "backup"):
            input("\n  Нажмите Enter для возврата в меню...")
            return
```

**Verify:** `python github_archiver.py` — test with various config states
**Commit:** `feat(archiver): add setup wizard, state-aware menus, lazy config prompts`

---

## Batch 3: Verification

### Task 3.1: Manual verification checklist
**Depends:** 2.1

Run through each scenario listed in the design:

| # | Scenario | Steps | Expected result |
|---|----------|-------|-----------------|
| 1 | Fresh setup | Delete `.env`, run `python github_archiver.py` | Auto-prompt banner appears, [Enter] → wizard runs, after wizard → standard menu with [0] exit |
| 2 | Skip auto-prompt | Same as #1 but press [S] | Main menu shows with `⚡ Требуется начальная настройка` banner, [0] setup option, [X] exit |
| 3 | Run wizard via menu | Press [0] on incomplete setup menu | Wizard runs, values saved to `.env` and `config.yaml`, menu switches to standard |
| 4 | Restart after wizard | Re-run the app after wizard completion | Standard menu (no banner, [0] = exit) |
| 5 | Clear config | Delete `.env`, re-run | Setup prompt reappears |
| 6 | Lazy prompt for channel | Keep `.env` with only GITHUB_TOKEN, clear CHANNEL_max | Enter GitHub submenu → pick any function → lazy prompt appears for MAX URL |
| 7 | Lazy prompt skip | In lazy prompt, press [S] | "Функция недоступна без URL канала", returns to menu |
| 8 | Lazy prompt accept | Enter URL in lazy prompt | URL saved to `.env`, function proceeds normally |
| 9 | Service menu after setup | Open service menu after setup | Shows [2] ⚙ Настройки option |
| 10 | Service menu before setup | Open service menu before setup | Only [1] Очистить журналы, no settings option |
| 11 | Settings re-runs wizard | Press [2] in service menu | Setup wizard runs again, can update values |
| 12 | Token still missing | Run with no GITHUB_TOKEN, run wizard but leave token empty | `_load_config()` still sys.exit(1) with token error |
| 13 | All PyPI functions | Trigger pypi archiver/sync without CHANNEL_pypi | Lazy prompt for PyPI URL |
| 14 | All Media functions | Trigger media upload without CHANNEL_media | Lazy prompt for Media URL |
| 15 | All Backuper functions | Trigger backup/restore without CHANNEL_backup | Lazy prompt for Backup URL |
| 16 | Channel ops (download, export, delete) | Trigger without CHANNEL_max | Lazy prompt for MAX URL |

**Verify:** All 16 scenarios pass

---

## Summary of files changed

| File | Change type | Tasks |
|------|-------------|-------|
| `config_utils.py` | Modify — add 3 functions + imports | 1.1 |
| `tests/test_config_utils.py` | Create — 11 test methods | 1.1 |
| `github_archiver.py` | Modify — ~15 edits across file | 2.1 |

Total: **3 files** (2 modified, 1 new), **2 implementation tasks**, **1 verification task**

## Key decisions

1. **`ensure_channel_url` in config_utils, not in GitHubArchiver**: Keeps the function reusable for other modules if needed. The GitHubArchiver wrapper `_ensure_channel_ready` adds the config-dict update logic specific to that class.

2. **No changes to submodule _load_config methods**: The submodules (pypi_libs_archiver, media_archiver, backuper, channel_downloader) keep their `get_channel_url(..., required=True)`. Since the lazy prompt in GitHubArchiver sets the env var before the submodule is instantiated, `load_dotenv()` in the submodule will find it.

3. **`set_env_value` uses hardcoded `Path(".env")`**: Matches the existing convention where all modules load `.env` from the current working directory. Tests use `monkeypatch.chdir(tmp_path)` to isolate.

4. **Config merge for config.yaml step 6**: Read existing → update specific keys → write back. Preserves all unknown sections. Follows the design's merge approach.

5. **No new files for state tracking**: `is_setup_complete()` infers state from actual config presence. User can clear `.env` and setup reappears naturally.
