date: 2025-06-09
topic: Fix false positive upload confirmation for large files
status: draft

---

## Problem Statement

Large files (50 MB — 950 MB mp4/jpg) are marked as "confirmed sent" by the archiver, but never actually appear in the MAX chat feed. The log shows `[OK] File confirmed (delta check, 2.0s)` for 900 MB files — which is physically impossible for a real upload.

**Root cause:** The delta check (`_confirm_file_sent`) only verifies that the feed's SHA-256 hash changed after pressing Enter. It does NOT verify that the change corresponds to the specific file being uploaded. For large files, the 2-second wait is far too short for actual upload completion, so any DOM change (another user's message, lazy-rendered content) triggers a false positive.

## Constraints

- Must not break confirmation for small files (< 50 MB) — they work correctly
- Must not add excessive delays for small files
- MAX web interface DOM structure is what it is — we adapt to it
- The fix should be surgical — 3 targeted changes, not a rewrite

## Approach

**Three targeted fixes** in `browser_max.py`:

1. **Replace delta check with content check for large files** — files >= 50 MB get `_confirm_file_in_feed()` instead of `_confirm_file_sent()`
2. **Add composer-clear verification** — new step between upload and confirmation
3. **Increase timeouts for very large files** — 200 MB+ and 500 MB+ tiers

## Architecture

### Current Flow (Broken for Large Files)

```
_upload_single_file():
  ├─ _wait_upload_complete()       ← MutationObserver + DOM check
  ├─ _send_message()               ← presses Enter
  ├─ _confirm_file_sent()          ← SHA-256 hash changed? → FALSE POSITIVE
  └─ _wait_for_file_message()      ← fallback (never reached if delta succeeds)
```

### New Flow

```
_upload_single_file():
  ├─ _wait_upload_complete()       ← existing, gains composer-clear signal
  ├─ _send_message()               ← presses Enter
  ├─ _verify_composer_cleared()    ← NEW: composer has no loading indicators
  ├─ _confirm_file_in_feed()       ← NEW for >= 50MB: filename appears in feed
  │   └─ _confirm_file_sent()      ← existing delta check for < 50MB
  └─ _wait_for_file_message()      ← fallback (as before)
```

## Components

### 1. `_verify_composer_cleared()` — New Method

**Purpose:** Verify the composer area no longer shows upload-in-progress indicators before confirming.

**What it checks (via `page.evaluate`):**
- No `[class*="progress"]` elements in composer
- No `[class*="spinner"]` / `[class*="loading"]` elements
- No file preview/attachment elements (`[class*="preview"]`, `[class*="attach"]`)
- No upload percentage text visible

**Behavior:**
- Polls every 1 second, up to 30 seconds
- Returns `True` if composer is clear, `False` if timeout
- If `False`, the upload is NOT confirmed — falls through to retry

**Why this matters:** Currently the delta check fires immediately after `_send_message()`. For large files, pressing Enter may not actually send anything if the upload hasn't finished — the Enter just does nothing or sends text without the file.

### 2. `_confirm_file_in_feed()` — New Method (Large File Path)

**Purpose:** For files >= 50 MB, verify the specific filename appears in the message feed (not just "something changed").

**How it works:**
- Queries `[class*="message"]` elements from baseline count
- For each new message, checks:
  - Contains `[class*="file"]` or `[class*="attach"]` child
  - Message text contains the expected filename (case-insensitive, normalized)
- Adaptive wait based on file size before first check:
  - 50-200 MB: 5 seconds
  - 200-500 MB: 10 seconds
  - >= 500 MB: 15 seconds
- Up to 3 retries with 3-second delays

**Returns:** `True` if filename found in feed, `False` otherwise.

**Key difference from delta check:** Verifies the SPECIFIC file, not just "feed changed."

### 3. Adaptive Timeout Tiers — Updated

**Current `_compute_monitor_timeouts` and no-activity thresholds:**

| File Size | Current No-Activity | New No-Activity | Current Reload | New Reload |
|-----------|--------------------|-----------------|----------------|------------|
| < 10 MB | 5s | 5s (unchanged) | — | — |
| < 50 MB | 10s | 10s (unchanged) | — | — |
| 50-200 MB | 30s | 30s (unchanged) | 20s | 25s |
| 200-500 MB | 30s | **60s** | 35s | **50s** |
| >= 500 MB | 30s | **120s** | 45s | **90s** |

**Rationale:** A 900 MB file at 5 MB/s takes ~3 minutes. A 30-second no-activity timeout fires well before upload completes. The new tiers scale with actual transfer times.

## Data Flow

```
File selected → Browser upload starts
    ↓
MutationObserver watches for upload completion
    ↓
DOM composer check: preview disappears?
    ↓
_wait_upload_complete() returns
    ↓
Enter pressed (_send_message)
    ↓
_verify_composer_cleared() ← NEW gate
    ├─ Composer still shows loading? → RETRY
    └─ Composer clear? → proceed
    ↓
_confirm_file_in_feed() for >= 50MB ← NEW gate
    ├─ Filename NOT in feed? → fall through to _wait_for_file_message
    └─ Filename in feed? → CONFIRMED
    ↓
_wait_for_file_message() ← existing fallback (unchanged)
```

## Error Handling Strategy

- **Composer not clearing after 30s:** Treated as upload failure → retry loop kicks in
- **Filename not in feed after retries:** Falls through to existing `_wait_for_file_message()` which has its own exhaustive scanning + page reload
- **Timeout exhaustion:** Same as current behavior — logs failure, increments attempt counter
- **No new failure modes introduced:** All new checks are gates that fall back to existing retry logic

## Testing Strategy

1. **Unit tests for new methods:** Mock page.evaluate responses for composer-clear and feed-check
2. **Integration test with large file:** Upload a 100+ MB test file, verify confirmation only after actual upload
3. **Regression test for small files:** Verify < 50 MB files still use fast delta check path
4. **Edge case:** Test what happens when another user posts during upload (should NOT trigger false positive)

## Open Questions

- **Should the 50 MB threshold be configurable?** Currently hardcoded. Could add to `config.yaml` as `confirmation_threshold_mb`.
- **Should we add upload speed estimation?** If we detect upload is slower than expected, we could extend timeouts dynamically.
- **MAX DOM selectors:** The exact class names for composer loading indicators depend on MAX's current DOM — need to verify these are still valid.
