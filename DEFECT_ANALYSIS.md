## Anti-Pattern Analysis: gitax Project

**Date:** 2026-06-10
**Scope:** Full Python source audit (16 files, ~6,500 lines)

---

### 🔴 CRITICAL Issues

#### C1. Passwords Stored in Plaintext (Security — Data Exposure)
| File | Line | Issue |
|------|------|-------|
| `backuper_journal.py` | 137-149 | `store_password()` writes passwords in cleartext to `backuper_journal.json` |
| `backuper.py` | 327 | `self.journal.store_password(archive_name, password, hint=password_hint)` — no encryption |

**Impact:** If the journal file is leaked (e.g., committed to git, shared accidentally), ALL archive passwords are exposed. The journal is JSON on disk with no encryption layer.

**Recommendation:** Encrypt passwords at rest (e.g., `cryptography.fernet`) or at minimum use OS keyring. Add `.json` files to `.gitignore` (already done, but the risk of accidental commit is high given this is sensitive data).

---

#### C2. Password Visible in Process List (Security — Credential Leakage)
| File | Line | Issue |
|------|------|-------|
| `browser_max.py` | 243 | `cmd.insert(2, f"-p{password}")` — password on command line |
| `backuper.py` | 906 | `cmd.extend([f"-p{password}"])` — password on command line |

**Impact:** On Windows, passwords appear in Task Manager's process list, in `Get-Process` output, and in any process-monitoring tools. Any user on the system can see archive passwords.

**Recommendation:** Use 7z's `-p-` flag to read password from stdin:
```python
result = subprocess.run(cmd, input=password + "\n", capture_output=True, text=True)
```

---

#### C3. Subprocess Injection via Unsanitized Password
| File | Line | Issue |
|------|------|-------|
| `browser_max.py` | 243 | f-string interpolation of password into shell command |
| `backuper.py` | 906 | Same — user-supplied password injected directly |

**Impact:** If a password contains shell metacharacters (`;`, `&`, `` ` ``, `$()`), the 7z command could be manipulated. While `subprocess.run(list)` avoids shell interpretation, the 7z binary itself may interpret special characters differently.

**Recommendation:** Validate password characters or use stdin-based password input.

---

#### C4. Non-Atomic Journal Write in rollback_journal.py (Data Corruption)
| File | Line | Issue |
|------|------|-------|
| `rollback_journal.py` | 33-34 | Direct `json.dump()` to journal file without temp+rename pattern |

**Impact:** If the process crashes mid-write, `journal.json` is corrupted. Every other journal class uses atomic writes (`tempfile.mkstemp` + `os.replace`), but this script bypasses that protection.

**Recommendation:** Use the same atomic-write pattern as other journals, or import and use an existing journal class.

---

### 🟠 HIGH Issues

#### H1. Non-Recursive Scan Returns Directories as "Files" (Logic Error — Crash Risk)
| File | Line | Issue |
|------|------|-------|
| `backuper.py` | 121 | `[(source_path, [], os.listdir(source_path))]` — includes directories |

**Impact:** When `recursive=False`, `os.listdir()` returns both files AND directories. The code then calls `os.path.getsize(filepath)` on directories, which succeeds but returns the directory's metadata size (not file size). Worse, if a directory entry is passed to upload functions, it may cause crashes or upload empty data.

**Recommendation:** Filter with `os.path.isfile(filepath)`:
```python
items = [f for f in os.listdir(source_path) if os.path.isfile(os.path.join(source_path, f))]
```

---

#### H2. Dead Code: Cookie Refresh Never Used (Logic Error — Misleading Retry)
| File | Line | Issue |
|------|------|-------|
| `channel_downloader.py` | 459-461 | `cookies = browser.page.context.cookies()` — stored in local var, never used |

**Impact:** On network retry, the code appears to refresh cookies but the new cookies are discarded. The next call to `self._download_with_requests()` fetches its OWN fresh cookies from `browser.page.context.cookies()` (line 274), making the explicit refresh redundant and misleading.

**Recommendation:** Remove the dead code or add a comment explaining why it's unnecessary.

---

#### H3. Unimplemented Fallback Causes Silent Error Count Inflation
| File | Line | Issue |
|------|------|-------|
| `channel_downloader.py` | 446-450 | Files >50MB without direct URL raise `NotImplementedError` |

**Impact:** The `NotImplementedError` is caught by the generic `except Exception` (line 473), increments `error_count`, and `break`s. The user sees "Ошибка: Browser-based download fallback not yet implemented" — confusing for end users who don't understand the internal limitation.

**Recommendation:** Either implement the fallback or show a clearer message like "Файл слишком большой (>50MB) и нет прямой ссылки для скачивания".

---

#### H4. `_cleanup` Deletes Configured Output Dir on Exit (Data Loss Risk)
| File | Line | Issue |
|------|------|-------|
| `pypi_libs_archiver.py` | 52-68 | `shutil.rmtree("./temp_pypi")` hardcoded; also deletes configured `output_dir` |

**Impact:** On ANY exit (including successful completion), the cleanup function deletes `./temp_pypi` AND the configured output directory. If a user set `output_dir` to a meaningful path (e.g., `./pypi_packages`), it gets wiped. The comment says "Clean up pypi_api temp directory" but the code also deletes the archiver's own output dir.

**Recommendation:** Only delete temp files created during THIS run, not the entire directory. Or only clean on error/abort, not on success.

---

#### H5. Potential IndexError on Empty Download Result
| File | Line | Issue |
|------|------|-------|
| `pypi_libs_archiver.py` | 358 | `os.path.dirname(file_paths[0])` — `file_paths` could be empty |

**Impact:** If `download_package()` returns an empty list (which the code checks at line 299 and `continue`s), the cleanup at line 358 is safe. BUT: if `download_package()` raises an exception that's caught at line 292, `file_paths` is never assigned and the `for fp in file_paths` loop at line 351 would fail with `NameError`. Actually, the `continue` at line 297 prevents reaching line 351, so this is safe in the current control flow. **Downgraded to Medium** — fragile but currently protected.

---

#### H6. Content Hash Based on mtime, Not Content (Logic Error — False Dedup)
| File | Line | Issue |
|------|------|-------|
| `backuper_journal.py` | 195-222 | `compute_content_hash()` uses `stat.st_mtime` instead of file content |

**Impact:** Two directories with identical file contents but different modification times produce different hashes (false negative — same content treated as different). Conversely, touching a file changes the hash even if content is identical. This means:
- Re-backing up an unchanged directory after a file `touch` creates a duplicate
- The "duplicate detection" feature is unreliable

**Recommendation:** Hash file contents (or at least content-based checksums) rather than metadata.

---

### 🟡 MEDIUM Issues

#### M1. TOCTOU Race in Lock File Acquisition
| File | Line | Issue |
|------|------|-------|
| `journal.py` | 58-67 | Gap between `os.path.exists()` check and `Path.touch()` |
| `backuper_journal.py` | 30-38 | Same pattern |
| `pypi_libs_journal.py` | 29-39 | Same pattern |
| `media_journal.py` | 28-38 | Same pattern |
| `channel_downloader.py` | 58-68 | Same pattern |

**Impact:** In a multi-process scenario (unlikely but possible if user runs two instances), two processes could both pass the existence check before either creates the lock. The 5-minute stale timeout helps but doesn't eliminate the race.

**Recommendation:** Use `fcntl.lockf` (Unix) or `msvcrt.locking` (Windows) for proper file locking. Or use `filelock` package.

---

#### M2. Generic Exception Raised Instead of Custom Type
| File | Line | Issue |
|------|------|-------|
| `channel_downloader.py` | 235 | `raise Exception("Failed to connect to MAX")` |
| `pypi_libs_archiver.py` | 92 | `raise Exception("Failed to connect to MAX")` |
| `backuper.py` | 59 | `raise ConnectionError("Не удалось подключиться к MAX")` |

**Impact:** Inconsistent exception types make it harder for callers to handle specific errors. `backuper.py` uses `ConnectionError` (correct), but the other two use bare `Exception`.

**Recommendation:** Use `ConnectionError` consistently or define a custom `MAXConnectionError`.

---

#### M3. Rate Limit Message Is Misleading (403 ≠ Rate Limit)
| File | Line | Issue |
|------|------|-------|
| `github_api.py` | 153 | `print(f"  ⚠ Rate limit exceeded...")` on HTTP 403 |

**Impact:** A 403 response could mean: token revoked, insufficient scopes, repo private, or actual rate limiting. The message always says "Rate limit exceeded" regardless of the actual cause.

**Recommendation:** Check `response.headers.get('X-RateLimit-Remaining')` to distinguish rate limits from auth errors.

---

#### M4. Missing `__main__` Guard in rollback_journal.py
| File | Line | Issue |
|------|------|-------|
| `rollback_journal.py` | 1-38 | All code runs at module level, no `if __name__ == "__main__"` |

**Impact:** If this module is ever imported (e.g., `from rollback_journal import ...`), it immediately executes and modifies `journal.json`. This is a one-off utility script, but the pattern is dangerous.

**Recommendation:** Wrap in `if __name__ == "__main__":` block.

---

#### M5. `os.system('cls')` in Main Loop (Security + Portability)
| File | Line | Issue |
|------|------|-------|
| `backuper.py` | 923 | `os.system('cls' if os.name == 'nt' else 'clear')` |

**Impact:** `os.system()` spawns a shell. While the command string is hardcoded here (not user input), this pattern is fragile and unnecessary. On Windows, `cls` works, but if `PATH` is manipulated, a malicious `cls.exe` could execute.

**Recommendation:** Use `print("\033[2J\033[H")` (ANSI escape) or `shutil.which('cls')` validation.

---

#### M6. Input Validation Missing on Interactive Prompts
| File | Line | Issue |
|------|------|-------|
| `backuper.py` | 230 | `volume_size = input(...)` — no validation of format |
| `backuper.py` | 374 | `max_size_mb = int(size_input) if size_input.isdigit() else 0` — negative numbers pass as `0` |
| `github_archiver.py` | 1062+ | Multiple `input()` calls with no validation |

**Impact:** Invalid input like `"abc"` for volume size causes 7z to fail silently or crash. Negative numbers for size limits are silently converted to 0 (no limit), which may not be the user's intent.

**Recommendation:** Add input validation loops with re-prompts on invalid input.

---

#### M7. Large File Upload Uses Private Method Across Module Boundaries
| File | Line | Issue |
|------|------|-------|
| `media_archiver.py` | 308 | `browser._upload_large_file(...)` — calls private method |
| `backuper.py` | 483 | `browser._upload_large_file(...)` — same |

**Impact:** `_upload_large_file` is a private method (leading underscore) but is called from outside the `BrowserMAX` class. If the method signature changes, callers break with no warning.

**Recommendation:** Make it public (`upload_large_file`) or provide a public wrapper.

---

#### M8. Config Dict Access Without Default Fallback
| File | Line | Issue |
|------|------|-------|
| `channel_downloader.py` | 226 | `self.config.get('channels', {}).get('max', '')` — empty string if missing |
| `pypi_libs_archiver.py` | 80 | `self.config.get('channels', {}).get('pypi', '')` — same |

**Impact:** If the channel URL is missing from config, an empty string is passed to `BrowserMAX`. The browser may fail to navigate or connect with a confusing error message rather than a clear "missing configuration" error.

**Recommendation:** Validate required config values at startup and fail fast with a clear error message.

---

#### M9. Scroll Position Tracking Only Captures Current View
| File | Line | Issue |
|------|------|-------|
| `scroll_registry.py` | 106-111 | `window.scrollY` only captures current scroll position |

**Impact:** For channels with many messages, the scroll registry saves only the currently visible scroll position. If the channel has more messages below the fold (loaded via infinite scroll), those are not tracked. On reconnect, the scanner may miss messages between the saved position and the actual bottom of the feed.

**Recommendation:** Scroll to bottom before saving position, or use a message-count-based approach instead of pixel-based.

---

### 🟢 LOW Issues

#### L1. Unnecessary `import glob` (Unused Import)
| File | Line | Issue |
|------|------|-------|
| `backuper.py` | 15 | `import glob` — never used |

---

#### L2. Token Fragment Logged
| File | Line | Issue |
|------|------|-------|
| `github_api.py` | 39 | `self.logger.info(f"Using token: {self.token[:8]}...")` |

**Impact:** First 8 characters of the GitHub token appear in `archiver.log`. While this is only a prefix, combined with other information it could aid token reconstruction attacks.

**Recommendation:** Remove from logs or log only the token type/length.

---

#### L3. Duplicate Cleanup Logic Across Classes (DRY Violation)
| File | Lines | Issue |
|------|-------|-------|
| `media_archiver.py`, `channel_downloader.py`, `pypi_libs_archiver.py`, `backuper.py` | various | Browser cleanup, journal save, and temp file cleanup duplicated in 4 classes |

**Impact:** Maintenance burden — any fix to cleanup logic must be applied in 4 places.

**Recommendation:** Extract to a shared `ResourceCleanup` mixin or context manager.

---

#### L4. Hardcoded Browser Timeout Values
| File | Line | Issue |
|------|------|-------|
| `browser_max.py` | 169 | `timeout=30000` (30s) for page load |
| `browser_max.py` | 358 | `timeout=10000` (10s) for various waits |
| `backuper.py` | 855 | `timeout=10000` for download button click |

**Impact:** On slow connections or large channels, these timeouts may be insufficient. No configuration option exists for tuning them.

**Recommendation:** Add timeout config options or use adaptive timeouts.

---

#### L5. `ensure_page_ready` May Return Before Page Is Truly Ready
| File | Line | Issue |
|------|------|-------|
| `browser_max.py` | 176-184 | Waits for `document.readyState === 'complete'` |

**Impact:** `readyState === 'complete'` fires when the initial HTML loads, but MAX is a SPA that loads messages via JavaScript after page load. The method then waits for `[class*="message"]` elements, but if the channel is empty or messages load slowly, this may still return prematurely.

**Recommendation:** Add a minimum wait time or poll for a stable message count before returning.

---

#### L6. Progress Bar Uses `\r` Which May Not Work in All Terminals
| File | Line | Issue |
|------|------|-------|
| `pypi_libs_archiver.py` | 167 | `print(f"\r  Прогресс: ...", end="", flush=True)` |
| `pypi_libs_archiver.py` | 427 | Same in sync loop |

**Impact:** `\r` (carriage return) works in most modern terminals but may not work in some Windows console configurations. The progress bar may appear as multiple lines instead of updating in place.

**Recommendation:** Use `rich.progress` or similar for cross-platform progress display.

---

#### L7. Summary Count Excludes Skipped Items
| File | Line | Issue |
|------|------|-------|
| `pypi_libs_archiver.py` | 373 | `print(f"  Всего: {sent_count + error_count}")` — excludes `skipped_in_journal` |

**Impact:** The "total" count doesn't match the number of packages that were initially fetched. Users may be confused why `sent + errors < total`.

**Recommendation:** Include skipped count in summary: `Всего: {sent + errors + skipped}`.

---

### Summary Table

| Severity | Count | Categories |
|----------|-------|------------|
| 🔴 Critical | 4 | Security (passwords in plaintext, process-list leakage, injection), Data corruption |
| 🟠 High | 6 | Logic errors, data loss, unimplemented features |
| 🟡 Medium | 9 | Race conditions, missing validation, fragile patterns |
| 🟢 Low | 7 | Style, DRY violations, UX improvements |

### Recommendations for .mindmodel/

**patterns/password-handling.md:**
```python
# DON'T: Store passwords in plaintext JSON
self.journal.store_password(name, password)  # cleartext in .json

# DON'T: Pass passwords on command line (visible in process list)
cmd.extend([f"-p{password}"])

# DO: Use stdin for 7z passwords
result = subprocess.run(
    [seven_zip_exe, "a", "-p-", archive, source],
    input=password + "\n", capture_output=True, text=True
)

# DO: Encrypt sensitive data at rest
from cryptography.fernet import Fernet
encrypted = Fernet(key).encrypt(password.encode())
```

**patterns/file-scanning.md:**
```python
# DON'T: Use os.listdir() without filtering directories
files = os.listdir(path)  # includes dirs!

# DO: Filter for files only
files = [f for f in os.listdir(path) if os.path.isfile(os.path.join(path, f))]
```

**patterns/error-handling.md:**
```python
# DON'T: Raise generic Exception
raise Exception("Failed to connect")

# DO: Use specific exception types
raise ConnectionError("Failed to connect to MAX")
```

**patterns/journal-writes.md:**
```python
# DON'T: Write journal directly (risk of corruption on crash)
with open("journal.json", "w") as f:
    json.dump(data, f)

# DO: Atomic write via temp file + rename
temp_fd, temp_path = tempfile.mkstemp(suffix='.json')
try:
    with os.fdopen(temp_fd, 'w') as f:
        json.dump(data, f)
    os.replace(temp_path, "journal.json")
except:
    os.remove(temp_path)
    raise
```
