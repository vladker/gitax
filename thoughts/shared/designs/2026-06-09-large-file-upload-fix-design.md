---
date: 2026-06-09
topic: "Large File Upload — False Positive Fix"
status: validated
---

## Problem Statement

Large files (>50MB) uploaded through the Media Archiver are reported as successfully sent, but never actually appear in MAX messenger. The program shows three false positive confirmations: "File attached in composer (0s)", "File confirmed (feed check, filename)", and "✓ Отправлено" — yet the file never materializes in the channel.

## Root Cause Analysis

The bug is a chain of **three sequential false positives**:

**1. `_check_dom_upload_ready()` in `_wait_upload_complete()` — immediate false positive**

When a file is selected via `set_input_files()`, MAX immediately shows a file preview/indicator in the composer DOM — **before the upload to the server even starts**. The DOM check detects this at 0 seconds and returns `True`. For a 178MB file, upload takes 30–60 seconds, but the code thinks it's already "attached in composer" after 0s.

**2. Enter pressed while upload is still in progress**

Because `_wait_upload_complete()` returned prematurely, the code immediately presses Enter. At this point the file upload hasn't started or is incomplete. MAX either:
- Sends the message without the file
- Shows an optimistic/pending message with the filename but no actual attachment

**3. `_confirm_file_in_feed()` finds the filename in a non-functional message**

After Enter, MAX may show a pending message containing the filename. The feed check scans messages, finds the filename text, and confirms success — even though the actual file upload failed or was cancelled.

## Constraints

- Must preserve the fast path for small files (<50MB) — delta check works fine for those
- Must not break the GitHub archiver flow (different file types, split archives)
- Browser switching (CDP → local Chrome for large files) is a separate concern — keep that flow
- Cannot rely on MAX API responses (the interceptor is unreliable for this)

## Approach

Fix all three points of failure by introducing a **two-phase upload monitor** and **stricter feed verification**.

**Phase separation in `_wait_upload_complete()`:** Instead of treating any DOM indicator as "done", we track the upload through discrete states: `selecting → attached → uploading → complete`. For large files, the "attached in composer" state is NOT terminal — we require additional evidence (progress, disappearance from composer, or feed appearance).

**Stricter feed confirmation:** `_confirm_file_in_feed()` must verify the message contains a real downloadable file (download link, embedded media, file size), not just a filename text mention.

**Adaptive timeouts:** Scale composer-clear timeout by file size.

## Architecture

The change is entirely within `browser_max.py`. No external API changes. Three methods modified, one new internal helper.

### Modified Methods

| Method | Change |
|--------|--------|
| `_wait_upload_complete()` | Two-phase state machine instead of single-threshold detection |
| `_confirm_file_in_feed()` | Stricter verification — requires real file attachment evidence |
| `_verify_composer_cleared()` | Adaptive timeout based on file size |
| `_upload_single_file()` | Skip redundant `_try_navigate()` when already on page |

## Components

### 1. Two-Phase Upload Monitor (`_wait_upload_complete()`)

**Phase A — FILE_ATTACHED (existing behavior, gated):**
- Wait until `_check_dom_upload_ready()` returns True
- For files <50MB: return immediately (preserve fast path)
- For files ≥50MB: transition to Phase B, do NOT return yet

**Phase B — UPLOAD_CONFIRMED (new behavior for large files):**
Monitor for ANY of these terminal signals:
- Upload progress reaches 100% (`_check_upload_progress()`)
- MutationObserver detects the filename in the feed (`_check_upload_done()`)
- File disappears from composer (was sent/uploaded)
- File indicator in composer changes from "uploading" to "attached" state

**Fallback timers (new, size-adaptive):**
| File Size | Phase A timeout | Phase B timeout |
|-----------|----------------|-----------------|
| 50–200 MB | 30s | 120s |
| 200–500 MB | 60s | 180s |
| 500+ MB | 90s | 300s |

If Phase A times out without seeing the file in composer → retry file selection.
If Phase B times out without any upload signal → retry entire upload.

### 2. Stricter Feed Confirmation (`_confirm_file_in_feed()`)

Add a new JS check that distinguishes "message contains filename text" from "message has a real file attachment":

```javascript
// REAL file attachment indicators:
const hasDownloadLink = msg.querySelector('a[download]') !== null;
const hasMediaTag = !!msg.querySelector('img, video, audio');
const hasFileSize = /\d+\.?\d*\s*(MB|GB|KB)/i.test(text);
const hasFileClass = !!msg.querySelector(
    '[class*="file"], [class*="attach"], [class*="download"]'
);

// Optimistic/pending messages typically have filename text but:
// No download link, no media element, no file size
// We REQUIRE at least TWO indicators OR one strong indicator (download link)
const isConfirmed = hasDownloadLink || (hasFileClass && hasFileSize) || hasMediaTag;
```

The filename text check remains as a pre-filter, but the final confirmation requires a real file indicator.

### 3. Adaptive Composer-Clear Timeout (`_verify_composer_cleared()`)

Scale the timeout by file size:

```python
if file_size is None:
    timeout = 30  # default
elif file_size < 50 * 1024 * 1024:
    timeout = 15
elif file_size < 200 * 1024 * 1024:
    timeout = 60
elif file_size < 500 * 1024 * 1024:
    timeout = 120
else:
    timeout = 180
```

### 4. Remove Redundant Navigation (`_upload_single_file()`)

When called from `_upload_large_file()`, the page is already at the MAX channel URL. Skip `_try_navigate()` — it only causes an unnecessary reload that resets DOM state and invalidates the pre-snapshot.

Check: if `self.channel_url` is already the current page URL, skip navigation.

## Data Flow

```
_upload_single_file(file.mp4, 178MB)
  │
  ├─ [NEW] Skip _try_navigate() if already on correct URL
  │
  ├─ Expect file chooser → set_input_files(file.mp4)
  │
  ├─ _wait_upload_complete(file.mp4)
  │    │
  │    ├─ Phase A: wait for _check_dom_upload_ready()
  │    │    → "File attached in composer" (NOT terminal for large files)
  │    │    → Transition to Phase B
  │    │
  │    ├─ Phase B: wait for terminal signal
  │    │    ├─ Upload progress 100% → OK
  │    │    ├─ File disappears from composer → OK
  │    │    ├─ Observer detects file in feed → OK
  │    │    └─ Timeout → retry
  │    │
  │    └─ return True (only when upload confirmed)
  │
  ├─ _send_message()  → Enter
  │
  ├─ _verify_composer_cleared(timeout=60s for 178MB)
  │
  └─ _confirm_file_in_feed()
       │
       ├─ Scan messages for filename + REAL file indicators
       ├─ Need: download link OR (file class + file size) OR media tag
       └─ return True/False
```

## Error Handling

| Scenario | Action |
|----------|--------|
| File selected but no progress for 60s | Retry: re-select file |
| Upload stuck at 30% for >120s | Retry: cancel and re-upload |
| Feed confirmation says "pending" message | Wait 5s, re-check. If still pending after 3 retries → Fail |
| Composer not clearing after Enter | Fall back to `_wait_for_file_message()` (content snapshot monitoring) |
| All retries exhausted | Mark as failed in journal, continue to next file |

## Testing Strategy

### New Unit Tests (in `test_large_file_upload.py`)

**Two-phase monitor tests:**
- `test_phase_a_attached_does_not_return_for_large_file` — DOM ready returns True, but method keeps waiting
- `test_phase_b_returns_on_progress_100` — progress hits 100% → returns True
- `test_phase_b_returns_on_composer_clear` — file disappears from composer → returns True
- `test_phase_b_timeout_for_large_file` — no progress for extended period → returns False
- `test_small_file_skips_phase_b` — <50MB file returns immediately on DOM ready

**Stricter feed confirmation tests:**
- `test_rejects_message_with_text_only` — filename in text but no download link → False
- `test_accepts_message_with_download_link` — filename + `<a download>` → True
- `test_accepts_message_with_media_tag` — filename + `<video>` → True
- `test_requires_two_indicators` — file class alone without file size → False

**Adaptive timeout tests:**
- `test_composer_timeout_scales_with_size` — 178MB → 60s, 600MB → 180s

**Navigation optimization tests:**
- `test_skips_navigate_when_on_page` — `_try_navigate()` not called when URL matches
- `test_still_navigates_when_on_wrong_page` — `_try_navigate()` called when URL differs

### Integration Tests
- `test_end_to_end_large_file_fails_on_no_upload` — mock no progress → returns False
- `test_end_to_end_large_file_succeeds_on_real_upload` — mock full progress → returns True

## Open Questions

- Does MAX in the local browser show a progress indicator (percentage) for large file uploads? If not, Phase B relies entirely on "file leaves composer" and observer feed detection.
- Is there a CDP-specific size limit on `set_input_files()` that's lower than 50MB? The current assumption is 50MB is the threshold, but if it's lower, some medium files also silently fail.
- Should we add a manual verification step for large files (prompt user to check MAX and confirm)?
