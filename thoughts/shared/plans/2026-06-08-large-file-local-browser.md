# Implementation Plan: Large File Upload via Local Browser Switch

**Design:** `thoughts/shared/designs/2026-06-08-large-file-local-browser-design.md`
**Problem:** Files > 50MB fail with `Cannot transfer files larger than 50Mb to a browser not co-located with the server`

---

## Micro-Tasks

### Task 1: Add `_get_user_data_dir()` to BrowserMAX

**File:** `browser_max.py`
**Location:** After `_launch_chrome_cdp()` method (around line 231)

**What to implement:**
- New method `_get_user_data_dir(self) -> str`
- Reads `browser.user_data_dir` from config if set
- Falls back to `~\AppData\Local\Google\Chrome\User Data`
- Appends `browser.profile_name` from config (default: `Default`)
- Returns full path string

**Verification:** `ast.parse()`, import test

---

### Task 2: Add `_disconnect_cdp()` to BrowserMAX

**File:** `browser_max.py`
**Location:** After `_get_user_data_dir()` (around line 250)

**What to implement:**
- New method `_disconnect_cdp(self) -> None`
- Closes `self.page` if not None
- Closes `self.browser` if not None (CDP close = disconnect, not kill)
- Sets `self.page = None`, `self.browser = None`, `self._connected = False`
- Wraps in try/except to handle already-closed state

**Verification:** `ast.parse()`, import test

---

### Task 3: Add `_launch_with_profile()` to BrowserMAX

**File:** `browser_max.py`
**Location:** After `_disconnect_cdp()` (around line 270)

**What to implement:**
- New method `_launch_with_profile(self) -> bool`
- Gets `user_data_dir` from `_get_user_data_dir()`
- Launches Chromium via `self.playwright.chromium.launch()` with:
  - `headless=False`
  - `args=['--disable-blink-features=Automation', f'--user-data-dir={user_data_dir}']`
- Creates new context and page
- Sets `self._connected = True`
- Returns True on success, False on failure

**Verification:** `ast.parse()`, import test

---

### Task 4: Add `_close_local_browser()` to BrowserMAX

**File:** `browser_max.py`
**Location:** After `_launch_with_profile()` (around line 295)

**What to implement:**
- New method `_close_local_browser(self) -> None`
- Closes `self.page` if exists
- Closes `self.browser` if exists (this kills the locally-launched Chrome)
- Sets `self.page = None`, `self.browser = None`, `self._connected = False`
- Sleep 2 seconds after close (allow lock file to release)
- Wraps in try/except

**Verification:** `ast.parse()`, import test

---

### Task 5: Add `_upload_large_file()` to BrowserMAX

**File:** `browser_max.py`
**Location:** After `_close_local_browser()` (around line 320)

**What to implement:**
- New method `_upload_large_file(self, filepath, filename, file_size_bytes, retries, retry_delay, baseline_count) -> bool`
- Flow:
  1. `_disconnect_cdp()` — disconnect from CDP
  2. Sleep 1s — let connection settle
  3. `_launch_with_profile()` — launch local Chrome with same profile
  4. `navigate()` — go to MAX channel
  5. `ensure_page_ready()` — wait for page
  6. `_upload_single_file(filepath, filename, file_size_bytes, retries, retry_delay, baseline_count)` — upload
  7. `_close_local_browser()` — close local Chrome
  8. Sleep 2s — let lock file release
  9. `connect()` — reconnect CDP
  10. `navigate()` — go to MAX channel
  11. Return upload success status
- Wraps entire flow in try/except with recovery:
  - On any error: attempt `_close_local_browser()`, `connect()`, return False

**Verification:** `ast.parse()`, import test

---

### Task 6: Add Size Check to MediaArchiver

**File:** `media_archiver.py`
**Location:** In `run()` method, around line 330-338 (where `send_message_with_files` is called)

**What to change:**
- Add `LARGE_FILE_THRESHOLD = 50 * 1024 * 1024` constant at class level
- Before calling `browser.send_message_with_files()`:
  - Check `if file_size >= LARGE_FILE_THRESHOLD:`
  - If yes: call `browser._upload_large_file(filepath, filename, file_size_bytes, retries, retry_delay, browser._pre_upload_msg_count)`
  - If no: call existing `browser.send_message_with_files()` as before
- Print different message for large files: `→ Отправляю в MAX (большой файл, переключение браузера)...`

**Verification:** `ast.parse()`, import test

---

### Task 7: Add Browser Config to config.yaml

**File:** `config.yaml`
**Location:** At end of file

**What to add:**
```yaml
browser:
  user_data_dir: ""
  profile_name: "Default"
```

**Verification:** File exists, valid YAML

---

### Task 8: Tests

**File:** `tests/test_upload_monitor.py` (or new file `tests/test_large_file_upload.py`)

**What to test:**
1. `_get_user_data_dir()` returns correct default path
2. `_get_user_data_dir()` reads from config when set
3. `_disconnect_cdp()` sets state to None
4. `_upload_large_file()` calls correct sequence of methods (mocked)
5. `_upload_large_file()` recovers from error (mocked)
6. MediaArchiver routes large files to `_upload_large_file` (mocked)
7. MediaArchiver routes small files to `send_message_with_files` (mocked)

**Verification:** `pytest tests/ -v` — all pass

---

## Batch Structure

| Batch | Tasks | Parallel |
|-------|-------|----------|
| 1 | Task 1, 2, 3, 4, 7 | All parallel (independent additions) |
| 2 | Task 5 | Depends on Batch 1 |
| 3 | Task 6 | Depends on Batch 2 |
| 4 | Task 8 | Depends on Batch 3 |

---

## Verification Checklist

- [ ] `ast.parse('browser_max.py')` — syntax OK
- [ ] `from browser_max import BrowserMAX` — import OK
- [ ] `ast.parse('media_archiver.py')` — syntax OK
- [ ] `pytest tests/ -v` — all pass
- [ ] Manual test: file 60MB uploads successfully (requires real browser)
