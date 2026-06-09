# Implementation Plan: Large File Upload False Positive Fix

**Design doc:** `thoughts/shared/designs/2026-06-09-large-file-upload-fix-design.md`
**Target file:** `browser_max.py` (modify), `tests/test_large_file_upload.py` (add/modify tests)

---

## Task DAG

```
Task 1: Helper — size_to_adaptive_timeout()
  │
  ├──► Task 2: _verify_composer_cleared() — adaptive timeout
  │
  ├──► Task 3: _wait_upload_complete() — two-phase state machine
  │     │
  │     └──► Task 4: _confirm_file_in_feed() — stricter verification
  │
  ├──► Task 5: _upload_single_file() — skip redundant navigate
  │
  └──► Task 6: Tests for all changes
```

**Parallel groups:**
- Group A (independent): Tasks 1, 5
- Group B (depends on 1): Tasks 2, 3, 4  
- Group C (depends on 2,3,4): Task 6

---

## Task 1: Extract size-to-timeout helper

**File:** `browser_max.py`
**Type:** Add new static/class method

**What:** Extract the timeout-scaling logic used in `_compute_monitor_timeouts()` into a more general helper `_size_to_adaptive_timeout(file_size_bytes: int | None, tier: str = "monitor") -> int` that can be reused by `_verify_composer_cleared()` and `_wait_upload_complete()`.

Tiers:
- `"monitor"` — returns (rerender, reload) tuple (existing, unchanged)
- `"composer"` — returns single timeout value:
  - <50MB: 15s
  - 50-200MB: 60s
  - 200-500MB: 120s
  - >=500MB: 180s

Keep `_compute_monitor_timeouts()` as-is but have it call the new helper internally for the values. Or just keep both — simpler is to add a new method alongside.

**Test file:** `tests/test_large_file_upload.py`
**Test class:** `TestAdaptiveTimeouts`

Test cases:
- `test_composer_under_50mb`
- `test_composer_50_to_200mb`
- `test_composer_200_to_500mb`
- `test_composer_over_500mb`
- `test_composer_none_returns_default`

---

## Task 2: Adaptive timeout in `_verify_composer_cleared()`

**File:** `browser_max.py`
**What:** Add `file_size_bytes: int | None = None` parameter to `_verify_composer_cleared()`. When provided, use `_size_to_adaptive_timeout(file_size_bytes, "composer")` instead of the hardcoded `timeout=30`.

**Signature change:** `_verify_composer_cleared(self, timeout: int = 30, poll_interval: float = 1.0, file_size_bytes: int | None = None) -> bool`

If `file_size_bytes` is provided, override `timeout` with the adaptive value. Otherwise keep backward-compatible default of 30.

**Test file:** `tests/test_large_file_upload.py`
**Test class:** `TestVerifyComposerClearedAdaptive` (add to existing)

Test cases:
- `test_default_timeout_when_no_size` — backward compatible
- `test_uses_adaptive_timeout` — 100MB file → 60s timeout
- `test_timeout_still_triggers` — page stays busy past adaptive timeout → False

---

## Task 3: Two-phase upload monitor in `_wait_upload_complete()`

**File:** `browser_max.py`
**Type:** Major modification to `_wait_upload_complete()`

**What:** Implement the two-phase state machine described in the design doc.

**Phase A — FILE_ATTACHED:**
- Same as current behavior: wait for `_check_dom_upload_ready()` to return True
- If file < 50MB OR no `expected_size`: return True immediately (preserve existing path)
- If file >= 50MB: print "[OK] File attached in composer, waiting for upload...", enter Phase B

**Phase B — UPLOAD_CONFIRMED:**
- Monitor for ANY of these signals:
  - `_check_upload_progress()` returns percent >= 100
  - `_check_upload_done()` returns (True, filename)
  - `_check_dom_upload_ready()` returns False (file left composer → was sent)
- Use adaptive timeout per size tier (via `_size_to_adaptive_timeout` with new `"upload"` tier):
  - 50-200MB: 120s
  - 200-500MB: 180s
  - >=500MB: 300s
- If timeout reached without any terminal signal → return False (will trigger retry in caller)

**Edge cases:**
- File detected in composer, then disappears (sent), then appears in feed — first signal that fires wins
- Progress bar stuck at 99% — treat as complete if no activity for 30s AND composer is clear
- Observer fires during Phase A — immediately return True (file already in feed)

**Test file:** `tests/test_large_file_upload.py`
**Test class:** `TestWaitUploadCompleteTwoPhase`

Test cases:
- `test_small_file_returns_immediately` — <50MB, DOM ready → True
- `test_large_file_phase_a_not_terminal` — >=50MB, DOM ready → does NOT return
- `test_large_file_phase_b_progress_100` — progress 100% → True
- `test_large_file_phase_b_composer_clear` — file leaves composer → True
- `test_large_file_phase_b_observer_fires` — observer detects in feed → True
- `test_large_file_phase_b_timeout` — no signals for timeout period → False
- `test_phase_b_ignores_spurious_progress` — progress shows 5% then stops (no 100%) → waits

---

## Task 4: Stricter feed confirmation in `_confirm_file_in_feed()`

**File:** `browser_max.py`
**Type:** Modify JS inside `_confirm_file_in_feed()` evaluate

**What:** Add stricter verification that the matched message has a real file attachment, not just filename text.

The current JS checks:
```javascript
const hasFileClass = !!msg.querySelector(...);
const hasMediaTag = !!msg.querySelector('img, video, audio');
const hasArchive = /\.(ext)/i.test(text);
if (!hasFileClass && !hasMediaTag && !hasArchive) continue;
```

**New logic:** After finding a candidate message, verify it has REAL attachment evidence:
```javascript
// Must pass BOTH: filename check AND real file indicator
const hasDownloadLink = !!msg.querySelector('a[download], [download]');
const hasRealMedia = !!msg.querySelector('img[src*="blob:"], video[src]');
const hasFileSize = /\d+\.?\d*\s*(MB|GB|KB)/i.test(text);
const hasFileClass = !!msg.querySelector('[class*="file"], [class*="attach"], [class*="download"]');

// Accept if: download link exists, OR (file class + file size), OR media tag
const isRealFile = hasDownloadLink || (hasFileClass && hasFileSize) || hasMediaTag;
if (!isRealFile) continue;  // Skip messages with filename text but no real file
```

**Test file:** `tests/test_large_file_upload.py`
**Test class:** `TestConfirmFileInFeedStrict` (new)

Test cases:
- `test_rejects_text_only_message` — filename in text body, no download link → False
- `test_accepts_message_with_download_link` — filename + `<a download>` → True
- `test_accepts_message_with_media_tag` — filename + `<video>` → True
- `test_accepts_message_with_file_class_and_size` — file class + "45 MB" text → True
- `test_rejects_file_class_without_size` — file class but no size text → False
- `test_backward_compatible_zip_with_download` — "repo.zip 45 MB" + download → True

---

## Task 5: Skip redundant navigation in `_upload_single_file()`

**File:** `browser_max.py`
**Type:** Minor modification in `_upload_single_file()`

**What:** Before calling `_try_navigate()`, check if the page is already at `self.channel_url`. Skip navigation if so.

Add after `pre_snapshot = self._take_content_snapshot()` (line ~2946):
```python
# Skip redundant navigation if already on the correct channel page
if not self._is_on_channel_page():
    if not self._try_navigate():
        ...
```

Add new method `_is_on_channel_page() -> bool`:
```python
def _is_on_channel_page(self) -> bool:
    """Check if current page is already at the target channel URL."""
    if not self.page or not self._ensure_alive():
        return False
    try:
        current_url = self.page.url
        return current_url.rstrip('/') == self.channel_url.rstrip('/')
    except Exception:
        return False
```

**Test file:** `tests/test_large_file_upload.py`
**Test class:** `TestSkipRedundantNavigate` (new)

Test cases:
- `test_skips_navigate_when_on_channel` — URL matches, `_try_navigate` not called
- `test_still_navigates_when_on_wrong_page` — URL differs, `_try_navigate` called
- `test_still_navigates_when_page_closed` — page is None, `_try_navigate` called
- `test_still_navigates_when_url_error` — evaluate throws, `_try_navigate` called

---

## Task 6: Update all callers in `_upload_single_file()` and `media_archiver.py`

**File:** `browser_max.py`, `media_archiver.py`
**Type:** Minor call-site updates

**In `_upload_single_file()`:**
- Pass `file_size_bytes` to `_verify_composer_cleared()` call (line ~3004)

**In `media_archiver.py`:**
- No changes needed — `_upload_large_file()` already captures its own baseline

---

## Execution Order

```
Step 1: Task 1 (add helper) + Task 5 (skip navigate) — parallel, independent
Step 2: Task 2 (adaptive composer) + Task 3 (two-phase monitor) — parallel, both depend on Task 1
Step 3: Task 4 (stricter feed) — depends on Task 3 conceptually (confirmation phase)
Step 4: Task 6 (update callers) — depends on Task 2
Step 5: Full test suite run
```

Each task writes/updates only `browser_max.py` and `tests/test_large_file_upload.py`.
