# Interactive Split Mode Implementation Plan

**Goal:** Add `split_mode` parameter to `send_message_with_files()` with 4 modes (auto/on/off/prompt), replacing the current `split_threshold_mb`-only logic.

**Architecture:** The `split_mode` param takes precedence over `split_threshold_mb`. In `"auto"` mode, behavior is unchanged (split if > threshold). In `"on"` mode, always split. In `"off"` mode, never split (replaces `split_threshold_mb=999999` hack). In `"prompt"` mode, user chooses per-file via a 3-option dialog matching the backuper pattern.

**Design:** `thoughts/shared/designs/2026-06-10-interactive-split-mode-design.md`

---

## Dependency Graph

```
Batch 1 (parallel): 1.1, 1.2                  [config changes - no code deps]
Batch 2 (parallel): 2.1, 2.2, 2.3, 2.4, 2.5   [implementation - depend on batch 1, no cross-deps]
Batch 3 (parallel): 3.1                        [tests - depend on batch 2]
```

---

## Batch 1: Config Foundation (parallel — 2 implementers)

All tasks in this batch have NO code dependencies and run simultaneously.

### Task 1.1: Add `split_mode` to config.yaml
**File:** `config.yaml`
**Test:** none (config file)
**Depends:** none

Add `split_mode: auto` to both `archiver:` and `pypi_libs_archiver:` sections.

**Changes to `config.yaml`:**

```yaml
archiver:
  limit: 0
  output_dir: ./temp
  repo_delay: 0
  retries: 1
  retry_delay: 1
  split_mode: auto          # NEW: auto | on | off | prompt
  split_threshold_mb: 49
  use_local_browser: true
  ...
pypi_libs_archiver:
  limit: 1000
  output_dir: ./temp_pypi_libs
  retries: 3
  retry_delay: 10
  split_mode: auto          # NEW: auto | on | off | prompt
  ...
```

**Verify:** `python -c "import yaml; c=yaml.safe_load(open('config.yaml')); assert c['archiver']['split_mode']=='auto'; assert c['pypi_libs_archiver']['split_mode']=='auto'"`
**Commit:** `feat(config): add split_mode to archiver and pypi_libs_archiver`

---

### Task 1.2: Add `get_split_mode()` helper to config_utils.py
**File:** `config_utils.py`
**Test:** `tests/test_config_utils.py` (add tests)
**Depends:** none

Add a helper function that reads split_mode from a config section and validates it. Follows the pattern of `get_config_value()` already in the file.

**Add to `config_utils.py` (after `get_config_value`):**

```python
def get_split_mode(config: dict, section: str, default: str = "auto") -> str:
    """
    Read split_mode from a config section with validation.

    Priority: config.yaml[section][split_mode] > default

    Validates that the value is one of: auto, on, off, prompt.
    Falls back to default if value is missing or invalid.

    Args:
        config: Loaded config dict from config.yaml
        section: Config section name (e.g., "archiver", "pypi_libs_archiver")
        default: Default split mode (default: "auto")

    Returns:
        One of "auto", "on", "off", "prompt"

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
```

**Tests to add in `tests/test_config_utils.py`:**

```python
# ── get_split_mode tests ──

class TestGetSplitMode:
    """Test get_split_mode helper"""

    def test_returns_auto_by_default(self):
        """Returns 'auto' when no split_mode in config."""
        from config_utils import get_split_mode
        assert get_split_mode({}, "archiver") == "auto"

    def test_reads_from_config(self):
        """Reads split_mode from config section."""
        config = {"archiver": {"split_mode": "prompt"}}
        result = get_split_mode(config, "archiver")
        assert result == "prompt"

    def test_fallback_to_default(self):
        """Falls back to supplied default when section missing."""
        config = {}
        result = get_split_mode(config, "pypi_libs_archiver", default="off")
        assert result == "off"

    def test_invalid_value_falls_back_to_default(self):
        """Falls back to default when value is not a valid mode."""
        config = {"archiver": {"split_mode": "invalid"}}
        assert get_split_mode(config, "archiver") == "auto"

    def test_none_value_falls_back(self):
        """Falls back when value is None."""
        config = {"archiver": {"split_mode": None}}
        assert get_split_mode(config, "archiver") == "auto"

    def test_case_insensitive(self):
        """Accepts case-insensitive values."""
        config = {"archiver": {"split_mode": "ON"}}
        assert get_split_mode(config, "archiver") == "on"
```

**Verify:** `python -m pytest tests/test_config_utils.py -v -k "TestGetSplitMode"`
**Commit:** `feat(config_utils): add get_split_mode() helper`

---

## Batch 2: Implementation — All Core Changes (parallel — 5 implementers)

All tasks in this batch depend on Batch 1 completing. No cross-dependencies between batch 2 tasks — all remote config reads → pass-though to browser.

### Task 2.1: Add split_mode to `browser_max.py`
**File:** `browser_max.py`
**Test:** none yet (tests in batch 3)
**Depends:** 1.2 (uses get_split_mode pattern)

**Changes:**

#### 2.1a — Add `_prompt_split_mode()` method to BrowserMAX class (before `send_message_with_files`):

```python
def _prompt_split_mode(self, filename: str, file_size_mb: float) -> int:
    """
    Prompt user for split mode choice for a specific file.

    Matches the backuper's 3-option pattern:
      [1] — single volume (no split)
      [2] — multi-volume (split with default size)
      [3] — custom size (split with user-specified size)

    Args:
        filename: Name of the file being uploaded
        file_size_mb: File size in megabytes

    Returns:
        1, 2, or 3 corresponding to user's choice.
        Loops until valid input (1-3) is received.
    """
    print()
    print(f"  ⚡ Файл: {filename} ({file_size_mb:.1f} MB)")
    print()
    print("  [1] Однотомный — без разделения")
    print(f"  [2] Многотомный — разделить на тома ({SEVEN_ZIP_VOLUME_SIZE})")
    print("  [3] Свой размер тома")
    print()

    while True:
        try:
            choice = input("  Ваш выбор [1/2/3]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 1  # Safe default on interrupt

        if choice in ("1", "2", "3"):
            return int(choice)

        print("  Неверный ввод. Введите 1, 2 или 3.")
```

#### 2.1b — Add `_prompt_split_volume_size()` method:

```python
def _prompt_split_volume_size(self) -> str | None:
    """
    Prompt user for a custom volume size (option 3).

    Validates input — must be a positive number optionally followed
    by K/M/G (e.g., "100M", "50", "1G").

    Returns:
        Size string (e.g., "100M") or None if user cancels.
    """
    print()
    print("  Укажите размер тома (например: 100M, 50M, 1G)")
    print("  [Enter] Отмена — вернуться к выбору")

    while True:
        try:
            raw = input("  Размер тома: ").strip().upper()
        except (EOFError, KeyboardInterrupt):
            print()
            return None

        if not raw:
            return None

        # Accept bare numbers as MB
        if raw.isdigit():
            return raw + "M"

        # Accept K/M/G suffixed
        if re.match(r'^\d+(\.\d+)?[KMG]$', raw):
            return raw

        print("  Неверный формат. Используйте число + K/M/G (например: 100M, 50, 1G)")
```

#### 2.1c — Modify `send_message_with_files()` signature and split logic:

**Current signature (line 3052):**
```python
def send_message_with_files(self, text: str, filepaths: list[str],
                            retries: int = 3, retry_delay: int = 10,
                            split_threshold_mb: float = 49.0,
                            expected_extensions: list[str] | None = None) -> tuple[bool, bool]:
```

**New signature:**
```python
def send_message_with_files(self, text: str, filepaths: list[str],
                            retries: int = 3, retry_delay: int = 10,
                            split_threshold_mb: float = 49.0,
                            split_mode: str = "auto",
                            expected_extensions: list[str] | None = None) -> tuple[bool, bool]:
```

**Replace the split decision block (lines 3080-3102):**

Old code:
```python
for fp in filepaths:
    if not os.path.exists(fp):
        self.logger.error(f"File not found: {fp}")
        continue

    file_size_mb = os.path.getsize(fp) / 1024 / 1024

    if file_size_mb > split_threshold_mb:
        self.logger.info(f"File {os.path.basename(fp)} ({file_size_mb:.1f} MB) > {split_threshold_mb} MB - splitting...")

        # Split into volumes
        volumes = split_file_with_7z(fp, SEVEN_ZIP_VOLUME_SIZE)

        if volumes:
            self.logger.info(f"Split into {len(volumes)} volumes")
            all_files.extend(volumes)
            volumes_to_cleanup.extend(volumes)
        else:
            # Split failed, try sending original
            self.logger.warning("Split failed, trying original file")
            all_files.append(fp)
    else:
        all_files.append(fp)
```

New code:
```python
for fp in filepaths:
    if not os.path.exists(fp):
        self.logger.error(f"File not found: {fp}")
        continue

    file_size_mb = os.path.getsize(fp) / 1024 / 1024
    filename = os.path.basename(fp)
    should_split = False
    volume_size = SEVEN_ZIP_VOLUME_SIZE

    # Determine split behavior based on split_mode
    if split_mode == "on":
        should_split = True
        self.logger.info(f"split_mode=on: splitting {filename}")
    elif split_mode == "off":
        should_split = False
        self.logger.info(f"split_mode=off: no split for {filename}")
    elif split_mode == "auto":
        if file_size_mb > split_threshold_mb:
            should_split = True
            self.logger.info(
                f"{filename} ({file_size_mb:.1f} MB) > "
                f"{split_threshold_mb} MB — splitting"
            )
    elif split_mode == "prompt":
        choice = self._prompt_split_mode(filename, file_size_mb)
        if choice == 1:
            should_split = False
            self.logger.info(f"User chose no split for {filename}")
        elif choice == 2:
            should_split = True
            self.logger.info(f"User chose split (default size) for {filename}")
        elif choice == 3:
            custom_size = self._prompt_split_volume_size()
            if custom_size:
                should_split = True
                volume_size = custom_size
                self.logger.info(f"User chose split (custom size {custom_size}) for {filename}")
            else:
                should_split = False
                self.logger.info(f"User cancelled custom size, no split for {filename}")
    else:
        # Unknown split_mode — default to auto behavior (backward compat)
        should_split = file_size_mb > split_threshold_mb

    if should_split:
        volumes = split_file_with_7z(fp, volume_size)
        if volumes:
            self.logger.info(f"Split into {len(volumes)} volumes")
            all_files.extend(volumes)
            volumes_to_cleanup.extend(volumes)
        else:
            self.logger.warning("Split failed, trying original file")
            all_files.append(fp)
    else:
        all_files.append(fp)
```

#### 2.1d — Update `send_message_with_file()` to pass through `split_mode`:

**Current signature (line 3376):**
```python
def send_message_with_file(self, text: str, filepath: str,
                           retries: int = 3, retry_delay: int = 10,
                           keep_alive: bool = False,
                           expected_extensions: list[str] | None = None) -> tuple[bool, bool]:
```

**New signature:**
```python
def send_message_with_file(self, text: str, filepath: str,
                           retries: int = 3, retry_delay: int = 10,
                           keep_alive: bool = False,
                           split_mode: str = "auto",
                           expected_extensions: list[str] | None = None) -> tuple[bool, bool]:
```

**Update the delegation call inside (line 3393):**
```python
success, deletable = self.send_message_with_files(
    text=text,
    filepaths=[filepath],
    retries=retries,
    retry_delay=retry_delay,
    split_mode=split_mode,
    expected_extensions=expected_extensions
)
```

**Verify:** `python -c "import browser_max; print('Import OK')"`
**Commit:** `feat(browser_max): add split_mode param and _prompt_split_mode()`

---

### Task 2.2: Update `github_archiver.py` — read split_mode, pass to browser
**File:** `github_archiver.py`
**Test:** none (covered by integration)
**Depends:** 1.1, 1.2

**Changes in 3 locations:**

#### 2.2a — Update import (add `get_split_mode`):

At the top import line (25), add `get_split_mode`:
```python
from config_utils import get_channel_url, is_setup_complete, ensure_channel_url, get_skipped_channels, get_split_mode
```

#### 2.2b — Update `_download_and_send()` method (line 882):

Replace:
```python
success, _ = browser.send_message_with_file(
    text=text,
    filepath=zip_path,
    retries=self.config.get('archiver', {}).get('retries', 3),
    retry_delay=self.config.get('archiver', {}).get('retry_delay', 10)
)
```

With:
```python
split_mode = get_split_mode(self.config, "archiver", default="auto")
success, _ = browser.send_message_with_file(
    text=text,
    filepath=zip_path,
    retries=self.config.get('archiver', {}).get('retries', 3),
    retry_delay=self.config.get('archiver', {}).get('retry_delay', 10),
    split_mode=split_mode,
)
```

#### 2.2c — Update `_download_and_send_repo_info_connected()` method (line 961):

Replace:
```python
success, _ = browser.send_message_with_file(
    text=text,
    filepath=zip_path,
    retries=self.config.get('archiver', {}).get('retries', 3),
    retry_delay=self.config.get('archiver', {}).get('retry_delay', 10)
)
```

With:
```python
split_mode = get_split_mode(self.config, "archiver", default="auto")
success, _ = browser.send_message_with_file(
    text=text,
    filepath=zip_path,
    retries=self.config.get('archiver', {}).get('retries', 3),
    retry_delay=self.config.get('archiver', {}).get('retry_delay', 10),
    split_mode=split_mode,
)
```

#### 2.2d — Update the two audit restore calls (lines 1729-1736 and 1936-1943):

**Line 1729-1736:**
Replace:
```python
split_threshold_mb = self.config.get("archiver", {}).get("split_threshold_mb", 49)
success, _ = browser.send_message_with_files(
    text=text,
    filepaths=[zip_path],
    retries=self.config.get("archiver", {}).get("retries", 3),
    retry_delay=self.config.get("archiver", {}).get("retry_delay", 10),
    split_threshold_mb=split_threshold_mb,
)
```

With:
```python
split_mode = get_split_mode(self.config, "archiver", default="auto")
split_threshold_mb = self.config.get("archiver", {}).get("split_threshold_mb", 49)
success, _ = browser.send_message_with_files(
    text=text,
    filepaths=[zip_path],
    retries=self.config.get("archiver", {}).get("retries", 3),
    retry_delay=self.config.get("archiver", {}).get("retry_delay", 10),
    split_threshold_mb=split_threshold_mb,
    split_mode=split_mode,
)
```

Same pattern at lines 1936-1943.

#### 2.2e — Add split_mode to setup wizard (after line 2350):

After the `split_str` prompt and before the `# Write step 6` comment, add:
```python
# ── Split mode prompt (step 6b) ──
print()
current_split_mode = archiver_cfg.get('split_mode', 'auto')
mode_prompt = (
    f"  Режим разделения [{current_split_mode}]\n"
    f"    auto    — автоматически (если > порога)\n"
    f"    on      — дробить всегда\n"
    f"    off     — никогда не дробить\n"
    f"    prompt  — спрашивать для каждого файла\n"
)
try:
    mode_str = input(mode_prompt + "  Ваш выбор [Enter=" + current_split_mode + "]: ").strip().lower()
except (EOFError, KeyboardInterrupt):
    mode_str = ""

if mode_str in ("auto", "on", "off", "prompt"):
    yaml_config["archiver"]["split_mode"] = mode_str
```

**Note:** the `split_str` variable for `split_threshold_mb` is still written to the file when the user enters a custom threshold — both `split_mode` and `split_threshold_mb` coexist in the config.

**Verify:** `python -c "import github_archiver; print('Import OK')"`
**Commit:** `feat(github_archiver): read split_mode from config, pass to browser, add to setup wizard`

---

### Task 2.3: Update `pypi_libs_archiver.py` — read split_mode, pass to browser
**File:** `pypi_libs_archiver.py`
**Test:** none (covered by integration)
**Depends:** 1.1, 1.2

**Changes:**

#### 2.3a — Update import:

```python
from config_utils import get_channel_url, get_split_mode
```

#### 2.3b — Update `load_top_libraries()` (around line 344):

Replace:
```python
success, _ = browser.send_message_with_files(
    text=text,
    filepaths=file_paths,
    retries=retries,
    retry_delay=retry_delay,
    expected_extensions=['.tar.gz', '.whl']
)
```

With:
```python
split_mode = get_split_mode(self.config, "pypi_libs_archiver", default="auto")
success, _ = browser.send_message_with_files(
    text=text,
    filepaths=file_paths,
    retries=retries,
    retry_delay=retry_delay,
    split_mode=split_mode,
    expected_extensions=['.tar.gz', '.whl']
)
```

#### 2.3c — Update `sync_libraries()` (around line 527):

Same change:
```python
split_mode = get_split_mode(self.config, "pypi_libs_archiver", default="auto")
success, _ = browser.send_message_with_files(
    text=text,
    filepaths=file_paths,
    retries=retries,
    retry_delay=retry_delay,
    split_mode=split_mode,
    expected_extensions=['.tar.gz', '.whl']
)
```

**Verify:** `python -c "import pypi_libs_archiver; print('Import OK')"`
**Commit:** `feat(pypi_libs_archiver): read split_mode from config, pass to browser`

---

### Task 2.4: Update `media_archiver.py` — replace split_threshold_mb=999999 with split_mode="off"
**File:** `media_archiver.py`
**Test:** none (covered by existing tests)
**Depends:** 1.1, 1.2

**Change (line 342-348):**

Replace:
```python
success, _ = browser.send_message_with_files(
    text="",
    filepaths=[filepath],
    retries=retries,
    retry_delay=retry_delay,
    split_threshold_mb=999999,
    expected_extensions=[ext]
)
```

With:
```python
success, _ = browser.send_message_with_files(
    text="",
    filepaths=[filepath],
    retries=retries,
    retry_delay=retry_delay,
    split_mode="off",
    expected_extensions=[ext]
)
```

**Verify:** `python -c "import media_archiver; print('Import OK')"`
**Commit:** `refactor(media_archiver): replace split_threshold_mb=999999 with split_mode=off`

---

### Task 2.5: Verify `backuper.py` — NO CHANGES NEEDED
**File:** `backuper.py`
**Test:** none
**Depends:** none (verification task)

The backuper already has its own interactive prompt for splitting (`_prompt_backup_mode()`), and passes already-split volumes to `send_message_with_files()` with `split_threshold_mb=9999`. Per the design:

> **Backuper не меняется** — его промпт остаётся на месте, он уже передаёт готовые тома в `send_message_with_files()` с `split_threshold_mb=9999`

No changes to `backuper.py`. Existing call at line 240 stays as-is; it relies on backward compatibility — `split_threshold_mb=9999` still works in `"auto"` mode (default) since `file_size_mb > 9999` is never true for normal files.

**Verify:** `python -c "import backuper; print('Import OK')"`
**Commit:** none (no changes)

---

## Batch 3: Tests (parallel — 1 implementer)

### Task 3.1: Create `tests/test_browser_max.py` — split_mode tests
**File:** `tests/test_browser_max.py` (NEW)
**Depends:** 2.1 (browser_max.py changes)

This is a new test file that tests:
1. `_prompt_split_mode()` with mocked `input()`
2. `_prompt_split_volume_size()` with mocked `input()`
3. `send_message_with_files()` with different split_mode values

```python
# -*- coding: utf-8 -*-
"""
Tests for BrowserMAX split_mode and _prompt_split_mode.

Tests cover:
- _prompt_split_mode() valid/invalid input handling
- _prompt_split_volume_size() custom size parsing
- send_message_with_files() with split_mode: auto, on, off, prompt
- Backward compatibility — no split_mode param still works
"""

import os
import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from browser_max import BrowserMAX, SEVEN_ZIP_VOLUME_SIZE


# ── _prompt_split_mode tests ──

class TestPromptSplitMode:
    """Test _prompt_split_mode interactive prompt"""

    def test_choice_1_no_split(self):
        """Input '1' returns 1."""
        bm = BrowserMAX("https://example.com")
        with patch("builtins.input", return_value="1"):
            result = bm._prompt_split_mode("test.zip", 100.0)
        assert result == 1

    def test_choice_2_split_default(self):
        """Input '2' returns 2."""
        bm = BrowserMAX("https://example.com")
        with patch("builtins.input", return_value="2"):
            result = bm._prompt_split_mode("test.zip", 100.0)
        assert result == 2

    def test_choice_3_custom_size(self):
        """Input '3' returns 3."""
        bm = BrowserMAX("https://example.com")
        with patch("builtins.input", return_value="3"):
            result = bm._prompt_split_mode("test.zip", 100.0)
        assert result == 3

    def test_invalid_then_valid_input(self):
        """Invalid input is rejected, then valid input is accepted."""
        bm = BrowserMAX("https://example.com")
        inputs = iter(["4", "abc", "", "2"])
        with patch("builtins.input", side_effect=inputs):
            result = bm._prompt_split_mode("test.zip", 100.0)
        assert result == 2

    def test_keyboard_interrupt_defaults_to_1(self):
        """KeyboardInterrupt during input returns 1 (safe default)."""
        bm = BrowserMAX("https://example.com")
        with patch("builtins.input", side_effect=KeyboardInterrupt()):
            result = bm._prompt_split_mode("test.zip", 100.0)
        assert result == 1

    def test_eoferror_defaults_to_1(self):
        """EOFError during input returns 1 (safe default)."""
        bm = BrowserMAX("https://example.com")
        with patch("builtins.input", side_effect=EOFError()):
            result = bm._prompt_split_mode("test.zip", 100.0)
        assert result == 1

    def test_displays_filename_and_size(self):
        """Prompt output includes filename and file size."""
        bm = BrowserMAX("https://example.com")
        # Capture print output to verify filename and size are shown
        with patch("builtins.print") as mock_print:
            with patch("builtins.input", return_value="1"):
                bm._prompt_split_mode("large_repo.zip", 150.5)
            printed_text = "".join(call[0][0] for call in mock_print.call_args_list)
            assert "large_repo.zip" in printed_text
            assert "150.5 MB" in printed_text


# ── _prompt_split_volume_size tests ──

class TestPromptSplitVolumeSize:
    """Test _prompt_split_volume_size custom size prompt"""

    def test_bare_number_adds_mb(self):
        """Bare number like '100' is converted to '100M'."""
        bm = BrowserMAX("https://example.com")
        with patch("builtins.input", return_value="100"):
            result = bm._prompt_split_volume_size()
        assert result == "100M"

    def test_size_with_g_suffix(self):
        """Size with 'G' suffix is kept as-is."""
        bm = BrowserMAX("https://example.com")
        with patch("builtins.input", return_value="1G"):
            result = bm._prompt_split_volume_size()
        assert result == "1G"

    def test_size_with_m_suffix(self):
        """Size with 'M' suffix is kept as-is."""
        bm = BrowserMAX("https://example.com")
        with patch("builtins.input", return_value="49M"):
            result = bm._prompt_split_volume_size()
        assert result == "49M"

    def test_empty_input_returns_none(self):
        """Empty/Enter input returns None (cancelled)."""
        bm = BrowserMAX("https://example.com")
        with patch("builtins.input", return_value=""):
            result = bm._prompt_split_volume_size()
        assert result is None

    def test_invalid_then_valid(self):
        """Invalid input loops, then valid input is accepted."""
        bm = BrowserMAX("https://example.com")
        inputs = iter(["abc", "XYZ", "200M"])
        with patch("builtins.input", side_effect=inputs):
            result = bm._prompt_split_volume_size()
        assert result == "200M"

    def test_keyboard_interrupt_returns_none(self):
        """KeyboardInterrupt returns None."""
        bm = BrowserMAX("https://example.com")
        with patch("builtins.input", side_effect=KeyboardInterrupt()):
            result = bm._prompt_split_volume_size()
        assert result is None


# ── send_message_with_files split_mode tests ──

class TestSendMessageWithFilesSplitMode:
    """Test send_message_with_files with different split_mode values"""

    def setup_browser(self):
        """Create BrowserMAX with all heavy dependencies mocked."""
        bm = BrowserMAX("https://example.com")
        # Mock page and connection
        bm.page = MagicMock()
        bm._connected = True
        bm.connect = MagicMock(return_value=True)
        # Mock evaluate to return consistent counts
        bm.page.evaluate.return_value = 0
        # Mock _find_message_input, _type_message, _send_message, _upload_single_file
        bm._find_message_input = MagicMock(return_value=MagicMock())
        bm._type_message = MagicMock()
        bm._send_message = MagicMock()
        bm._upload_single_file = MagicMock(return_value=True)
        return bm

    @patch("browser_max.os.path.exists", return_value=True)
    @patch("browser_max.split_file_with_7z", return_value=[])
    def test_split_mode_off_never_splits(self, mock_split, mock_exists):
        """split_mode='off' never calls split_file_with_7z even for large files."""
        bm = self.setup_browser()
        # Create a 100MB test file (simulated)
        with patch("browser_max.os.path.getsize", return_value=100 * 1024 * 1024):
            success, _ = bm.send_message_with_files(
                text="test",
                filepaths=["large.zip"],
                split_mode="off",
            )
        mock_split.assert_not_called()

    @patch("browser_max.os.path.exists", return_value=True)
    @patch("browser_max.split_file_with_7z", return_value=["part1.7z.001"])
    def test_split_mode_on_always_splits(self, mock_split, mock_exists):
        """split_mode='on' calls split_file_with_7z even for small files."""
        bm = self.setup_browser()
        with patch("browser_max.os.path.getsize", return_value=1024):  # 1KB
            success, _ = bm.send_message_with_files(
                text="test",
                filepaths=["small.zip"],
                split_mode="on",
            )
        mock_split.assert_called_once()

    @patch("browser_max.os.path.exists", return_value=True)
    @patch("browser_max.split_file_with_7z", return_value=[])
    def test_split_mode_auto_no_split_below_threshold(self, mock_split, mock_exists):
        """split_mode='auto' does NOT split files below threshold."""
        bm = self.setup_browser()
        with patch("browser_max.os.path.getsize", return_value=10 * 1024 * 1024):  # 10MB < 49
            success, _ = bm.send_message_with_files(
                text="test",
                filepaths=["small.zip"],
                split_mode="auto",
                split_threshold_mb=49.0,
            )
        mock_split.assert_not_called()

    @patch("browser_max.os.path.exists", return_value=True)
    @patch("browser_max.split_file_with_7z", return_value=["part1.7z.001", "part2.7z.002"])
    def test_split_mode_auto_splits_above_threshold(self, mock_split, mock_exists):
        """split_mode='auto' splits files above threshold."""
        bm = self.setup_browser()
        with patch("browser_max.os.path.getsize", return_value=100 * 1024 * 1024):  # 100MB > 49
            success, _ = bm.send_message_with_files(
                text="test",
                filepaths=["large.zip"],
                split_mode="auto",
                split_threshold_mb=49.0,
            )
        mock_split.assert_called_once()

    @patch("browser_max.os.path.exists", return_value=True)
    @patch("browser_max.split_file_with_7z", return_value=[])
    def test_split_mode_prompt_choice_1_no_split(self, mock_split, mock_exists):
        """split_mode='prompt' with user choosing 1 does NOT split."""
        bm = self.setup_browser()
        bm._prompt_split_mode = MagicMock(return_value=1)
        with patch("browser_max.os.path.getsize", return_value=100 * 1024 * 1024):
            success, _ = bm.send_message_with_files(
                text="test",
                filepaths=["large.zip"],
                split_mode="prompt",
            )
        mock_split.assert_not_called()

    @patch("browser_max.os.path.exists", return_value=True)
    @patch("browser_max.split_file_with_7z", return_value=["part1.7z.001"])
    def test_split_mode_prompt_choice_2_splits_default(self, mock_split, mock_exists):
        """split_mode='prompt' with user choosing 2 splits with default size."""
        bm = self.setup_browser()
        bm._prompt_split_mode = MagicMock(return_value=2)
        with patch("browser_max.os.path.getsize", return_value=100 * 1024 * 1024):
            success, _ = bm.send_message_with_files(
                text="test",
                filepaths=["large.zip"],
                split_mode="prompt",
            )
        mock_split.assert_called_once_with(mock_split.call_args[0][0], SEVEN_ZIP_VOLUME_SIZE)

    @patch("browser_max.os.path.exists", return_value=True)
    @patch("browser_max.split_file_with_7z", return_value=["part1.7z.001"])
    def test_split_mode_prompt_choice_3_custom_size(self, mock_split, mock_exists):
        """split_mode='prompt' with user choosing 3 splits with custom size."""
        bm = self.setup_browser()
        bm._prompt_split_mode = MagicMock(return_value=3)
        bm._prompt_split_volume_size = MagicMock(return_value="100M")
        with patch("browser_max.os.path.getsize", return_value=500 * 1024 * 1024):
            success, _ = bm.send_message_with_files(
                text="test",
                filepaths=["large.zip"],
                split_mode="prompt",
            )
        mock_split.assert_called_once_with(mock_split.call_args[0][0], "100M")

    @patch("browser_max.os.path.exists", return_value=True)
    @patch("browser_max.split_file_with_7z", return_value=[])
    def test_split_mode_prompt_choice_3_cancelled(self, mock_split, mock_exists):
        """split_mode='prompt' with user cancelling option 3 does NOT split."""
        bm = self.setup_browser()
        bm._prompt_split_mode = MagicMock(return_value=3)
        bm._prompt_split_volume_size = MagicMock(return_value=None)  # cancelled
        with patch("browser_max.os.path.getsize", return_value=500 * 1024 * 1024):
            success, _ = bm.send_message_with_files(
                text="test",
                filepaths=["large.zip"],
                split_mode="prompt",
            )
        mock_split.assert_not_called()

    @patch("browser_max.os.path.exists", return_value=True)
    @patch("browser_max.split_file_with_7z", return_value=["part1.7z.001"])
    def test_backward_compatibility_default_split_mode(self, mock_split, mock_exists):
        """Not passing split_mode defaults to 'auto' (threshold check)."""
        bm = self.setup_browser()
        with patch("browser_max.os.path.getsize", return_value=100 * 1024 * 1024):
            success, _ = bm.send_message_with_files(
                text="test",
                filepaths=["large.zip"],
                split_threshold_mb=49.0,
                # No split_mode param — should default to "auto"
            )
        mock_split.assert_called_once()

    @patch("browser_max.os.path.exists", return_value=True)
    @patch("browser_max.split_file_with_7z", return_value=[])
    def test_backward_compatibility_small_file(self, mock_split, mock_exists):
        """Without split_mode, small files below threshold are not split."""
        bm = self.setup_browser()
        with patch("browser_max.os.path.getsize", return_value=1024):  # 1KB
            success, _ = bm.send_message_with_files(
                text="test",
                filepaths=["small.zip"],
                split_threshold_mb=49.0,
            )
        mock_split.assert_not_called()

    @patch("browser_max.os.path.exists", return_value=True)
    @patch("browser_max.split_file_with_7z", return_value=[])
    def test_unknown_split_mode_falls_back_to_auto(self, mock_split, mock_exists):
        """Unknown split_mode value defaults to auto behavior."""
        bm = self.setup_browser()
        with patch("browser_max.os.path.getsize", return_value=10 * 1024 * 1024):  # 10MB < 49
            success, _ = bm.send_message_with_files(
                text="test",
                filepaths=["small.zip"],
                split_mode="unknown_value",
            )
        mock_split.assert_not_called()


# ── send_message_with_file split_mode passthrough ──

class TestSendMessageWithFileSplitMode:
    """Test send_message_with_file passes split_mode to send_message_with_files"""

    def test_passes_split_mode_to_send_message_with_files(self):
        """send_message_with_file delegates split_mode to send_message_with_files."""
        bm = BrowserMAX("https://example.com")
        with patch.object(bm, "send_message_with_files", return_value=(True, True)) as mock_smwf:
            with patch("browser_max.os.path.exists", return_value=True):
                bm.send_message_with_file(
                    text="test",
                    filepath="/path/to/file.zip",
                    split_mode="off",
                )
            # Verify split_mode was passed through
            assert mock_smwf.call_count == 1
            kwargs = mock_smwf.call_args[1]
            assert kwargs.get("split_mode") == "off"

    def test_defaults_to_auto_when_not_specified(self):
        """send_message_with_file defaults split_mode to 'auto'."""
        bm = BrowserMAX("https://example.com")
        with patch.object(bm, "send_message_with_files", return_value=(True, True)) as mock_smwf:
            with patch("browser_max.os.path.exists", return_value=True):
                bm.send_message_with_file(
                    text="test",
                    filepath="/path/to/file.zip",
                )
            kwargs = mock_smwf.call_args[1]
            assert kwargs.get("split_mode") == "auto"
```

**Verify:** `python -m pytest tests/test_browser_max.py -v`
**Commit:** `test(browser_max): add tests for split_mode and _prompt_split_mode`

---

## Summary of All Changes

| File | Change Type | Lines Changed |
|------|-------------|---------------|
| `config.yaml` | Add `split_mode: auto` to archiver + pypi_libs_archiver | +2 |
| `config_utils.py` | Add `get_split_mode()` function | +25 |
| `browser_max.py` | Add `_prompt_split_mode()`, `_prompt_split_volume_size()`, modify `send_message_with_files()`, update `send_message_with_file()` | ~80 |
| `github_archiver.py` | Add import, pass split_mode in 4 call sites, add to setup wizard | ~20 |
| `pypi_libs_archiver.py` | Add import, pass split_mode in 2 call sites | ~8 |
| `media_archiver.py` | Replace `split_threshold_mb=999999` with `split_mode="off"` | ~2 |
| `backuper.py` | No changes (design explicitly says don't change) | 0 |
| `tests/test_browser_max.py` | New test file | ~250 |
| `tests/test_config_utils.py` | Add `TestGetSplitMode` class | ~40 |

**Total:** ~6 files modified, 1 file created, ~425 lines net

## Verification Plan

1. **Config validation:** `python -c "import yaml; c=yaml.safe_load(open('config.yaml')); assert 'split_mode' in c['archiver']"`
2. **Import validation:** `python -c "from browser_max import BrowserMAX; print('OK')"`
3. **Unit tests:** `python -m pytest tests/test_browser_max.py tests/test_config_utils.py -v`
4. **Existing regression tests:** `python -m pytest tests/ -v`
5. **Manual smoke test:** Run with `split_mode=prompt` in config, upload a file — verify prompt appears
