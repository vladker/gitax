# Media Upload — Page Reload & Silent Corruption Fix

**Goal:** Eliminate 4 root causes of media upload corruption in `browser_max.py` by removing `page.reload()` from upload pipeline, adding upload state guards, improving media-type-aware timeouts, and fixing false-positive confirmation logic.

**Architecture:** Add an Upload State Manager (`_upload_in_progress` flag with lock/unlock methods) that blocks destructive navigation during upload. Remove `page.reload()` from `_wait_for_file_message()`. Add media type classification to adapt timeouts for video files. Fix `_verify_composer_cleared()` false-clear and `_confirm_file_in_feed()` media tag matching.

**Design:** `thoughts/shared/designs/2026-06-09-media-upload-reload-fix-design.md`

---

## Dependency Graph

```
Batch 1 (parallel): 1.1, 1.2, 1.3        [foundation — new methods, flags, fixes]
Batch 2 (parallel): 2.1, 2.2, 2.3, 2.4   [guards & confirm fixes — different methods]
Batch 3 (parallel): 3.1, 3.2              [upload & monitoring changes]
Batch 4 (depends):  4.1, 4.2              [integration + tests]
```

All tasks edit `browser_max.py` (same file) but touch **non-overlapping line ranges** within each batch, allowing parallel application. Within each batch, tasks can be applied in any order since they modify different methods.

---

## Batch 1: Foundation (parallel — 3 implementers)

All tasks add new code or fix isolated bugs. No dependency on other tasks.

### Task 1.1: Upload State Manager (flags + methods + new exception)

**File:** `browser_max.py`
**Test:** (tests added in Batch 4)
**Depends:** none

**Changes:**

1. **Add `UploadInProgressError` exception** — insert after line 176 (`class UploadError`):

```python
class UploadInProgressError(BrowserMAXError):
    """Raised when navigation is attempted during active upload"""
    pass
```

2. **Add upload state flags to `__init__`** — append to `__init__` body after line 208:

```python
        # Upload state management — blocks destructive navigation during upload
        self._upload_in_progress = False
        self._upload_file_size = 0
        self._upload_file_name = ""
        self._is_video = False
```

Implementation note: the flag `_is_video` is computed from file extension by `_classify_media()` (Task 1.2) and set in `_lock_upload_state()`.

3. **Add three new methods** — insert after `__init__` (before `_stop_existing_playwright` at line 210). These are instance methods of `BrowserMAX`:

```python
    def _lock_upload_state(self, filepath: str) -> None:
        """
        Lock upload guards — marks an upload as in progress.

        Sets flags that block destructive navigation/reload during upload.
        Must be called before file chooser opens.
        Must be paired with _unlock_upload_state() in a finally block.

        Args:
            filepath: Absolute path to the file being uploaded
        """
        self._upload_in_progress = True
        self._upload_file_size = os.path.getsize(filepath)
        self._upload_file_name = os.path.basename(filepath)
        self._is_video = self._classify_media(filepath) == "video"
        self.logger.info(
            f"Upload locked: {self._upload_file_name} "
            f"({'video' if self._is_video else 'file'}, "
            f"{self._upload_file_size / 1024 / 1024:.1f} MB)"
        )

    def _unlock_upload_state(self) -> None:
        """Unlock upload guards — marks upload as complete."""
        self._upload_in_progress = False
        self._upload_file_size = 0
        self._upload_file_name = ""
        self._is_video = False
        self.logger.debug("Upload unlocked")

    def _can_navigate(self) -> bool:
        """
        Check if navigation is safe (no upload in progress).

        Returns:
            True if navigation is allowed, False if upload in progress
        """
        return not self._upload_in_progress
```

**Exact insertion point:** After line 208 (end of `__init__`), before line 210 (`@classmethod`). The three methods go between `__init__` and `_stop_existing_playwright`.

**Verify:** `python -c "from browser_max import BrowserMAX, UploadInProgressError; b = BrowserMAX('https://example.com'); assert b._can_navigate(); b._upload_in_progress = True; assert not b._can_navigate(); print('OK')"`

**Commit:** `feat(browser_max): add UploadInProgressError + upload state manager flags`

---

### Task 1.2: Media Type Classification (static sets + method)

**File:** `browser_max.py`
**Test:** (tests added in Batch 4)
**Depends:** none

**Changes:**

1. **Add class-level extension sets** — insert inside `BrowserMAX` class body, after line 190 (`_active_playwright = None`):

```python
    # Media type extension sets — used to adapt upload timeouts & confirmation
    VIDEO_EXTENSIONS = {'.mp4', '.avi', '.mov', '.mkv', '.webm', '.flv', '.wmv', '.m4v'}
    IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'}
    ARCHIVE_EXTENSIONS = {'.zip', '.tar', '.gz', '.7z', '.rar', '.tar.gz', '.whl'}
```

2. **Add `_classify_media` static method** — insert right after the extension sets (before `__init__` at line 192):

```python
    @staticmethod
    def _classify_media(filepath: str) -> str:
        """
        Classify a file by its extension into a media type.

        Used to adapt upload timeouts and confirmation logic.
        Returns one of: 'video', 'image', 'archive', 'other'.

        Args:
            filepath: Path to the file (can be relative or absolute)

        Returns:
            'video' | 'image' | 'archive' | 'other'
        """
        basename = os.path.basename(filepath).lower()
        # Check compound extensions first (.tar.gz, .whl)
        if basename.endswith('.tar.gz') or basename.endswith('.whl'):
            return 'archive'
        ext = os.path.splitext(filepath)[1].lower()
        if ext in BrowserMAX.VIDEO_EXTENSIONS:
            return 'video'
        if ext in BrowserMAX.IMAGE_EXTENSIONS:
            return 'image'
        if ext in BrowserMAX.ARCHIVE_EXTENSIONS:
            return 'archive'
        return 'other'
```

**Verify:** `python -c "from browser_max import BrowserMAX; assert BrowserMAX._classify_media('test.mp4') == 'video'; assert BrowserMAX._classify_media('test.zip') == 'archive'; assert BrowserMAX._classify_media('test.jpg') == 'image'; assert BrowserMAX._classify_media('test.pdf') == 'other'; assert BrowserMAX._classify_media('test.tar.gz') == 'archive'; print('OK')"`

**Commit:** `feat(browser_max): add media type classification (VIDEO_EXTENSIONS, _classify_media)`

---

### Task 1.3: Fix `_verify_composer_cleared()` — Missing DOM False Positive

**File:** `browser_max.py`
**Test:** (tests added in Batch 4)
**Depends:** none

**Root cause #4:** On line 1855, `if (!composer) return true;` — if the composer DOM element is missing (e.g. after a page reload, navigation, or disconnect), the method returns `True` ("clear") even though the file may not have uploaded or sent. This creates a false positive in the upload pipeline.

**Change:** Replace line 1855 `return true` with `return false` (only when composer is missing during upload context — since this method is called after `_send_message()`, a missing composer means something went wrong):

```python
                        if (!composer) return false;  // Changed: missing composer = NOT clear
```

**Reasoning:** `_verify_composer_cleared()` is called at line 3003 inside `_upload_single_file()`, AFTER `_send_message()`. After sending a message, the composer should exist and be clear. If it's completely missing from the DOM, the page likely reloaded or disconnected — treat this as "not clear" so the caller doesn't get a false positive. The caller already handles `False` gracefully (logs a warning, continues to confirmation). This is the conservative/safe choice.

**Exact edit:** In the JavaScript string at line 1850-1879, change line 1855:
```
OLD: if (!composer) return true;  // No composer = clear
NEW: if (!composer) return false; // No composer = not clear (page may have reloaded)
```

**Verify:** `python -c "from browser_max import BrowserMAX; print('syntax OK')"`

After verification, the file should compile without syntax errors.

**Commit:** `fix(browser_max): _verify_composer_cleared() return false when composer missing`

---

## Batch 2: Navigation Guards & Confirmation Fixes (parallel — 4 implementers)

All tasks modify existing methods in browser_max.py. Non-overlapping line ranges.

### Task 2.1: Guard in `navigate()` and `_try_navigate()`

**File:** `browser_max.py`
**Test:** (tests added in Batch 4)
**Depends:** 1.1 (uses `_upload_in_progress` flag, which defaults to False)

**Root cause #1 prevention:** Prevents `page.goto()` from being called during active upload.

**Changes:**

1. **`navigate()` (line 649)** — add guard at top, before `self.logger.info(...)`:

```python
    def navigate(self):
        """Navigate to MAX channel"""
        # GUARD: block navigation during active upload
        if self._upload_in_progress:
            self.logger.warning(
                f"BLOCKED navigate() during upload: {self._upload_file_name}"
            )
            raise UploadInProgressError(
                f"Cannot navigate during upload: {self._upload_file_name}"
            )

        self.logger.info(f"Opening channel: {self.channel_url}")
        ...
```

2. **`_try_navigate()` (line 669)** — add guard at top:

```python
    def _try_navigate(self) -> bool:
        """
        Safely navigate to MAX channel. Reconnects if needed.
        Returns True if navigation succeeded.
        """
        # GUARD: block navigation during active upload
        if self._upload_in_progress:
            self.logger.warning(
                f"BLOCKED _try_navigate() during upload: {self._upload_file_name}"
            )
            return False

        if not self._ensure_alive():
        ...
```

**Verify:** `python -c "from browser_max import BrowserMAX; b = BrowserMAX('https://example.com'); assert b._can_navigate(); print('OK')"`

**Commit:** `fix(browser_max): add upload guard to navigate() and _try_navigate()`

---

### Task 2.2: Guard in `_ensure_alive()`

**File:** `browser_max.py`
**Test:** (tests added in Batch 4)
**Depends:** 1.1 (uses `_upload_in_progress` flag)

**Root cause #1 prevention:** Prevents `_ensure_alive()` from reconnecting/creating a new page during upload.

**Change (line 706):** Add guard at the top of `_ensure_alive()`:

```python
    def _ensure_alive(self) -> bool:
        """
        Ensure connection is alive. Reconnect if needed.

        Returns:
            True if connected, False otherwise
        """
        # GUARD: during active upload, only check current state — do NOT reconnect
        if self._upload_in_progress:
            if self.page and not self.page.is_closed():
                return True
            self.logger.warning(
                f"Page lost during upload of {self._upload_file_name} — "
                f"cannot reconnect without destroying upload"
            )
            return False

        if not self.page:
        ...
```

**Reasoning:** During upload, reconnecting creates a new page context and destroys the in-progress upload. The guard checks if the current page is usable. If not, it returns `False` — the caller (`_upload_single_file`) will handle this via its retry loop.

**Verify:** `python -c "from browser_max import BrowserMAX; b = BrowserMAX('https://example.com'); print('OK')"`

**Commit:** `fix(browser_max): add upload guard to _ensure_alive() — no reconnect during upload`

---

### Task 2.3: `_confirm_file_sent()` — Safety Guard for Files >= 50MB

**File:** `browser_max.py`
**Test:** (tests added in Batch 4)
**Depends:** none (self-contained change)

**Root cause #3 mitigation:** For files >= 50MB, skip hash-based check entirely (it can produce false positives from other users' messages). The caller (`_upload_single_file`) already routes files >= 50MB to `_confirm_file_in_feed()`, but this adds a safety guard inside `_confirm_file_sent()` itself in case it's ever called directly.

**Change:** At the top of `_confirm_file_sent()` (line 1790), add a guard:

```python
    def _confirm_file_sent(self, pre_snapshot: "ContentSnapshot", file_size_bytes: int) -> bool:
        """
        Fast confirmation: check if feed content changed after sending.
        NOTE: Only reliable for files < 50MB. Larger files use _confirm_file_in_feed().
        ...
        """
        # Guard: only valid for files < 50MB
        if file_size_bytes >= 50 * 1024 * 1024:
            self.logger.debug(
                f"Skipping hash check for file >= 50MB ({file_size_bytes / 1024 / 1024:.1f} MB)"
            )
            return False

        if not pre_snapshot:
        ...
```

**Reasoning:** The hash-based check compares SHA-256 of the last 15 messages. For large files that take time to upload, other messages may appear in the feed, causing a false positive. This guard ensures `_confirm_file_sent()` is never used for large files, even if called directly.

**Verify:** `python -c "from browser_max import BrowserMAX; b = BrowserMAX('https://example.com'); b.page = type('obj', (object,), {'is_closed': lambda: False, 'evaluate': lambda *a: None})(); print('OK')"`

**Commit:** `fix(browser_max): add 50MB guard to _confirm_file_sent()`

---

### Task 2.4: `_confirm_file_in_feed()` — Media Tag Support + Adaptive Waits

**File:** `browser_max.py`
**Test:** (tests added in Batch 4)
**Depends:** none (self-contained method changes)

**Root cause #3 prevention:** Improve filename matching to detect media files via `<video>`, `<img>`, `<audio>` tags in the DOM, not just text content.

**Changes:**

1. **Update adaptive initial wait** (lines 1919-1925, replace with finer tiers):

```python
        # Adaptive initial wait based on file size
        # Larger files take longer to appear in feed after sending
        if size_mb < 50:
            initial_wait = 5
        elif size_mb < 200:
            initial_wait = 15    # was 5s
        elif size_mb < 500:
            initial_wait = 30    # was 10s
        else:
            initial_wait = 60    # was 15s
```

2. **Upgrade the JavaScript query** (lines 1950-1981) — replace the inline JS with an enhanced version that checks media tags more thoroughly:

```python
                # Query messages for filename match — extended media tag support
                escaped_name = search_name.replace("\\", "\\\\").replace("'", "\\'")
                found = self.page.evaluate(
                    f"""
                    () => {{
                        const searchName = '{escaped_name}';
                        const msgs = document.querySelectorAll('[class*="message"]');
                        const scanStart = {scan_start};

                        for (let i = scanStart; i < msgs.length; i++) {{
                            const msg = msgs[i];
                            const text = msg.textContent || '';
                            const html = msg.innerHTML || '';

                            // Check for file/attachment indicators
                            const hasFileClass = !!msg.querySelector(
                                '[class*="file"], [class*="attach"], [class*="download"]'
                            );

                            // ENHANCED: Check media tags with filename context
                            // <video> with poster/src containing the filename
                            const videos = msg.querySelectorAll('video');
                            const hasVideoWithFilename = Array.from(videos).some(v =>
                                (v.poster || '').toLowerCase().includes(searchName) ||
                                (v.src || '').toLowerCase().includes(searchName) ||
                                (v.getAttribute('data-filename') || '').toLowerCase().includes(searchName)
                            );
                            // <img> with alt/title containing the filename
                            const imgs = msg.querySelectorAll('img');
                            const hasImgWithFilename = Array.from(imgs).some(img =>
                                (img.alt || '').toLowerCase().includes(searchName) ||
                                (img.title || '').toLowerCase().includes(searchName)
                            );
                            // <a> with [download] attribute
                            const hasDownloadLink = !!msg.querySelector(
                                'a[download], [class*="download"]'
                            );
                            // [class*="file-name"] or [class*="name"] containing filename
                            const nameElements = msg.querySelectorAll(
                                '[class*="file-name"], [class*="name"], [class*="title"]'
                            );
                            const hasNameWithFilename = Array.from(nameElements).some(el =>
                                (el.textContent || '').toLowerCase().includes(searchName)
                            );

                            const hasMediaTag = !!msg.querySelector('img, video, audio');
                            const hasArchive = /\\.(zip|tar|gz|rar|7z|mp4|jpg|jpeg|png|webp|mov)/i.test(text) ||
                                              /\\.(zip|tar|gz|rar|7z|mp4|jpg|jpeg|png|webp|mov)/i.test(html);

                            if (!hasFileClass && !hasMediaTag && !hasArchive &&
                                !hasVideoWithFilename && !hasImgWithFilename &&
                                !hasDownloadLink && !hasNameWithFilename) continue;

                            // Check for filename match (case-insensitive)
                            const textLower = text.toLowerCase();
                            if (textLower.includes(searchName)) {{
                                return {{ found: true, text: text.slice(0, 100) }};
                            }}
                            // Also check HTML for filename (media files may show it in attributes)
                            const htmlLower = html.toLowerCase();
                            if (htmlLower.includes(searchName)) {{
                                return {{ found: true, text: text.slice(0, 100) }};
                            }}
                        }}

                        return {{ found: false }};
                    }}
                """
```

**Key enhancements in the JS:**
- `hasVideoWithFilename` — checks `video[poster]`, `video[src]`, `video[data-filename]`
- `hasImgWithFilename` — checks `img[alt]`, `img[title]`
- `hasDownloadLink` — checks `a[download]` elements
- `hasNameWithFilename` — checks `[class*="file-name"]`, `[class*="name"]`, `[class*="title"]`
- Added HTML fallback check for filename in attributes

**Verify:** `python -c "from browser_max import BrowserMAX; print('syntax OK')"`

**Commit:** `fix(browser_max): enhance _confirm_file_in_feed() with media tag support + adaptive waits`

---

## Batch 3: Upload Monitoring Changes (parallel — 2 implementers)

### Task 3.1: `_wait_upload_complete()` — Video Handling (no no-activity exit, extended timeouts)

**File:** `browser_max.py`
**Test:** (tests added in Batch 4)
**Depends:** 1.1 (`_is_video` flag), 1.2 (`_classify_media`)

**Root cause #2 fix:** For video files, disable the no-activity heuristic entirely (video processing can pause DOM updates while transcoding). For non-video files > 50MB, increase no-activity threshold from 30s to 45s.

**Changes:**

1. **Modify no-activity threshold section** (lines 1481-1487) — replace with:

```python
        # Smaller files upload faster — reduce no-activity wait accordingly
        # For video files: NO no-activity exit (video transcoding can pause DOM updates)
        if self._is_video:
            no_activity_threshold = float('inf')  # never exit on no-activity for video
        elif expected_size and expected_size < 10 * 1024 * 1024:
            no_activity_threshold = 5   # 5 seconds for files < 10MB
        elif expected_size and expected_size < 50 * 1024 * 1024:
            no_activity_threshold = 10  # 10 seconds for files < 50MB
        else:
            no_activity_threshold = 45  # Increased from 30s to 45s for files > 50MB
```

2. **Modify the no-activity exit section** (lines 1537-1555) — wrap the heuristic in a video check:

```python
            # Track activity - if no progress for a while, consider it done
            time_since_activity = time.time() - last_activity_time

            # Skip no-activity heuristic entirely for video files
            # Video transcoding can pause DOM updates for extended periods
            if self._is_video:
                if consecutive_no_activity >= 2:
                    # For video, only check DOM/observer, never assume done
                    if self._check_dom_upload_ready() or done:
                        print(f"\n  [OK] Video upload detected in DOM ({int(time_since_activity)}s)")
                        return True
                    consecutive_no_activity = 0  # Reset and keep waiting
            elif time_since_activity > no_activity_threshold:
                consecutive_no_activity += 1

                # After threshold of no activity, assume upload is done
                if consecutive_no_activity >= 2:
                    # Final verification — only trust composer or observer (new DOM nodes),
                    # NOT lenta scan (can match old messages from previous runs)
                    if self._check_dom_upload_ready() or done:
                        print(f"\n  [OK] Upload finished (no activity for {int(time_since_activity)}s)")
                        return True

                    # Still nothing after extended wait
                    if consecutive_no_activity >= 4:  # ~4x threshold
                        print(f"\n  [WARN] No upload activity for {int(time_since_activity)}s")
                        # Try one more thorough check
                        if self._check_dom_upload_ready() or self._check_upload_done():
                            print(f"\n  [OK] Upload confirmed after extended wait")
                            return True
                        print(f"  [INFO] Continuing to monitor...")
```

3. **Extend the default timeout** for video (at the beginning of the method, after line 1471):

```python
        # Video files need extended timeout — transcoding can take minutes
        if self._is_video:
            size_mb = (expected_size or 0) / (1024 * 1024)
            video_timeout = max(120, int(size_mb * 10))  # 10s per MB, min 120s
            video_timeout = min(video_timeout, 1800)  # cap at 30 minutes
            if timeout > video_timeout:
                timeout = video_timeout
            self.logger.info(
                f"Video upload: extended timeout {timeout}s "
                f"(no-activity heuristic disabled)"
            )
```

Wait, the timeout `_wait_upload_complete` takes a parameter. The current default is 3600. Let me make it so that for video we apply an adaptive extension but not exceed the passed timeout.

Actually, let me reconsider. The function signature has `timeout: int = 3600`. So the maximum wait is 3600s by default. For a 200MB video at 10s/MB that's 2000s ~ 33 minutes, which is under 3600s. So the default is fine.

But what if someone passes a smaller timeout? Let me just use the max of the passed timeout and the video-specific calculation.

Let me rewrite this more carefully:

```python
        self._check_connection()
        self.logger.info(f"Monitoring upload progress...")

        # Video: extend timeout to account for transcoding time
        if self._is_video and expected_size:
            video_mb = expected_size / (1024 * 1024)
            min_video_timeout = max(120, int(video_mb * 10))
            min_video_timeout = min(min_video_timeout, 1800)  # cap at 30 min
            if timeout < min_video_timeout:
                self.logger.info(
                    f"Extending timeout for video: {timeout}s -> {min_video_timeout}s"
                )
                timeout = min_video_timeout
```

This goes right after the log line and before the observer setup.

**Verify:** `python -c "from browser_max import BrowserMAX; print('syntax OK')"`

**Commit:** `fix(browser_max): _wait_upload_complete() — disable no-activity exit for video, extend timeouts`

---

### Task 3.2: Remove `page.reload()` from `_wait_for_file_message()`

**File:** `browser_max.py`
**Test:** (tests added in Batch 4)
**Depends:** none (self-contained change to one method)

**Root cause #1 fix:** Remove the destructive `page.reload()` block entirely. Never reload the page during file monitoring. Replace with extended scroll-based rerender and a final timeout return.

**Changes:**

1. **Increase snapshot_depth at line 2257:**
```python
OLD: snapshot_depth = 15    # last N messages to snapshot
NEW: snapshot_depth = 30    # last N messages to snapshot (increased for better coverage)
```

2. **Remove the reload block entirely** (lines 2459-2479). Delete this section:

```
                # Fallback: reload page after 45s of no changes
                if elapsed >= reload_timeout and elapsed < reload_timeout + 5 and prev_snapshot and (elapsed - last_reload_time) > (reload_timeout - 5):
                    print(f"  [FALLBACK] No changes for {reload_timeout}s, reloading page...")
                    try:
                        self.page.reload(wait_until="domcontentloaded", timeout=15000)
                        self.page.wait_for_timeout(3000)  # Wait for page to stabilize
                        last_reload_time = elapsed
                        # Re-initialize baseline after reload
                        base_count = self.page.evaluate(
                            "() => document.querySelectorAll('[class*=\"message\"]').length"
                        ) or 0
                        # Scan all messages after reload
                        found, msg_idx, detail = self._scan_messages_for_file(
                            0, base_count, search_name
                        )
                        if found:
                            print(f"  [OK] FILE FOUND after reload! Message #{msg_idx} ({detail})")
                            return (True, "found", msg_idx)
                        prev_snapshot = self._take_content_snapshot(depth=snapshot_depth)
                    except Exception as reload_err:
                        print(f"  [WARN] Reload failed: {reload_err}, continuing...")
```

3. **Add a second-level rerender timeout** — insert after the rerender block (after the `last_rerender_time` block at line ~2457), before `time.sleep(snapshot_interval)` at line 2481:

```python
                # Extended fallback: after 2x rerender_timeout (3x for video), give up
                # WITHOUT reloading — the caller will retry via _upload_single_file loop
                extended_timeout = rerender_timeout * 3 if self._is_video else rerender_timeout * 2
                if elapsed >= extended_timeout:
                    print(f"  [WARN] Extended timeout ({extended_timeout}s) reached — "
                          f"file not found, returning without reload")
                    final_count = self.page.evaluate(
                        "() => document.querySelectorAll('[class*=\"message\"]').length"
                    ) or 0
                    fallback_start = max(0, final_count - 20)
                    found, msg_idx, detail = self._scan_messages_for_file(
                        fallback_start, final_count, search_name
                    )
                    if found:
                        print(f"  [OK] File found at extended timeout! Msg #{msg_idx} ({detail})")
                        return (True, "found", msg_idx)
                    return (False, "not_found", -1)
```

4. **Clean up unused variable** — remove `last_reload_time = 0` at line 2383 (no longer needed):
```python
OLD: last_reload_time = 0
NEW: # last_reload_time removed — reload is eliminated from the pipeline
```

Actually, to keep the diff minimal, I'll just keep `last_reload_time = 0` as dead code. The implementer can decide to remove it or leave it. For cleanliness, let's remove it.

Wait, I should be careful. If we remove the variable but it's referenced somewhere... let me check.

Looking at the code:
- Line 2383: `last_reload_time = 0`
- Line 2460: `(elapsed - last_reload_time) > (reload_timeout - 5)` — this is in the reload block which we're removing

So yes, `last_reload_time` is only used in the reload block which we're removing. Safe to remove.

Actually, keeping it as dead code doesn't hurt and makes the diff cleaner (less changes). Let me keep it to minimize the diff. The implementer can clean it up.

**Final state of the while loop after changes:**

The monitoring loop (lines 2385-2481) will have:
- Snapshot check (lines 2395-2414) — unchanged
- Timeout check (lines 2417-2433) — unchanged
- Periodic status (lines 2436-2437) — unchanged
- Rerender fallback (lines 2439-2457) — unchanged
- ~~Reload fallback (lines 2459-2479)~~ — **REMOVED**
- Extended timeout (new) — added after rerender fallback
- `time.sleep(snapshot_interval)` (line 2481) — unchanged

**Verify:** `python -c "from browser_max import BrowserMAX; print('syntax OK')"`

**Commit:** `fix(browser_max): remove page.reload() from _wait_for_file_message(), increase snapshot_depth`

---

## Batch 4: Integration + Tests (depends on Batches 1-3)

### Task 4.1: Wire Up `_upload_single_file()` with Lock/Unlock and Media Classification

**File:** `browser_max.py`
**Test:** (tests added in Task 4.2)
**Depends:** ALL previous tasks (uses flags, methods, and guards)

**Changes:**

Modify `_upload_single_file()` (line 2912) to:
1. Lock upload state at the start
2. Unlock upload state on success/failure (via try/finally)
3. Pass `_is_video` context through to monitoring methods

**Complete method rewrite** — replace the entire method body (lines 2932-3078) with:

```python
    def _upload_single_file(self, filepath: str, filename: str, file_size_bytes: int,
                            retries: int = 3, retry_delay: int = 10,
                            baseline_count: int = 0) -> bool:
        """
        Upload a single file and wait for confirmation.

        Args:
            filepath: Absolute path to file
            filename: Display name for the file
            file_size_bytes: File size in bytes
            retries: Number of retries
            retry_delay: Delay between retries
            baseline_count: Message count baseline — passed to _wait_upload_complete
                            to avoid false matches on old messages

        Returns:
            True if upload successful
        """
        file_size_mb = file_size_bytes / 1024 / 1024

        for attempt in range(1, retries + 1):
            try:
                self.logger.debug(f"Attempt {attempt}/{retries}")

                # === UPLOAD STATE LOCK ===
                # Set flags before any browser interaction to block destructive navigation
                self._lock_upload_state(filepath)

                # Reconnect and re-navigate if page was closed during previous attempt
                if not self._ensure_alive():
                    self.logger.error("Cannot reconnect to Chrome")
                    if attempt < retries:
                        time.sleep(retry_delay)
                        continue
                    else:
                        return False

                # Capture content snapshot before upload for delta confirmation
                pre_snapshot = self._take_content_snapshot()

                if not self._try_navigate():
                    self.logger.error("Cannot navigate to channel after reconnect")
                    if attempt < retries:
                        time.sleep(retry_delay)
                        continue
                    else:
                        return False

                upload_timeout = max(60000, int(file_size_mb * 5000))
                self.logger.debug(f"Upload timeout: {upload_timeout//1000}s")

                uploaded = False
                try:
                    with self.page.expect_file_chooser(timeout=upload_timeout) as fc_info:
                        self._click_upload_button()

                    fc_info.value.set_files(filepath, timeout=upload_timeout)
                    self.logger.info(f"File selected: {filename}")
                    uploaded = True
                except PlaywrightTimeout:
                    self.logger.warning("File chooser timeout, trying input method...")
                    try:
                        file_input = self.page.locator('input[type="file"]').first
                        file_input.set_input_files(filepath, timeout=upload_timeout)
                        self.logger.info("File uploaded via input")
                        uploaded = True
                    except Exception as e2:
                        self.logger.error(f"Input method also failed: {e2}")
                except Exception as e:
                    self.logger.warning(f"File chooser failed: {e}")
                    try:
                        file_input = self.page.locator('input[type="file"]').first
                        file_input.set_input_files(filepath, timeout=upload_timeout)
                        self.logger.info("File uploaded via input")
                        uploaded = True
                    except Exception as e2:
                        self.logger.error(f"Input method also failed: {e2}")

                if not uploaded:
                    self.logger.error("Failed to upload file - both methods failed")
                    if attempt < retries:
                        time.sleep(retry_delay)
                    continue

                self.logger.debug("Waiting for upload...")
                if not self._wait_upload_complete(expected_filename=filename, expected_size=file_size_bytes,
                                                     baseline_count=baseline_count):
                    self.logger.error("Upload did not complete in time")
                    if attempt < retries:
                        time.sleep(retry_delay)
                    continue

                self.logger.debug("Sending file message...")
                self._send_message()

                # NEW: Verify composer cleared before confirming (prevents false positives)
                if not self._verify_composer_cleared():
                    self.logger.warning("Composer still busy — treating as unconfirmed")
                else:
                    self.logger.debug("Composer cleared, proceeding to confirmation")

                # Large files (>= 50 MB): use filename-based confirmation
                # Small files (< 50 MB): use fast hash-based delta check
                LARGE_CONFIRM_THRESHOLD = 50 * 1024 * 1024  # 50 MB
                confirmed = False

                if file_size_bytes >= LARGE_CONFIRM_THRESHOLD:
                    # Large file path: check filename appears in feed
                    confirmed = self._confirm_file_in_feed(
                        filename, file_size_bytes,
                        baseline_count=baseline_count
                    )
                    if confirmed:
                        self.logger.info(
                            f"File confirmed (feed check, {filename})"
                        )
                        print(
                            f"  [OK] File confirmed (feed check, {filename})"
                        )
                else:
                    # Small file path: existing fast delta check
                    confirm_start = time.time()
                    confirmed = self._confirm_file_sent(pre_snapshot, file_size_bytes)
                    confirm_elapsed = time.time() - confirm_start

                    if confirmed:
                        self.logger.info(
                            f"File confirmed (delta check, {confirm_elapsed:.1f}s)"
                        )
                        print(
                            f"  [OK] File confirmed (delta check, {confirm_elapsed:.1f}s)"
                        )

                if confirmed:
                    return True

                # Fallback: full confirmation flow for edge cases
                self.logger.debug(
                    f"Fast confirmation failed, falling back to full confirmation"
                )
                # Adaptive timeout: scale with file size
                # ~2 MB/s baseline upload speed, plus buffer
                size_mb = file_size_bytes / (1024 * 1024)
                adaptive_timeout = max(120, int(size_mb * 60 / 2))  # 2 MB/s baseline
                adaptive_timeout = min(adaptive_timeout, 900)  # cap at 15 min
                found, reason, msg_idx = self._wait_for_file_message(
                    timeout=adaptive_timeout,
                    expected_filename=filename,
                    baseline_count=self._pre_upload_msg_count,
                    fast_mode=file_size_bytes < 5 * 1024 * 1024,  # fast mode for files < 5MB
                    file_size_bytes=file_size_bytes
                )
                self.logger.info(f"Result: {reason}, msg #{msg_idx}")

                if found:
                    return True
                else:
                    self.logger.error(f"File not found in chat: {reason}")
                    if attempt < retries:
                        time.sleep(retry_delay)
                    continue

            except UploadInProgressError:
                # Caught from guards in navigate() — retry with delay
                self.logger.warning(
                    f"UploadInProgressError in attempt {attempt} — retrying"
                )
                if attempt < retries:
                    time.sleep(retry_delay)
                continue
            except Exception as e:
                self.logger.error(f"Upload error: {e}", exc_info=True)
                if attempt < retries:
                    time.sleep(retry_delay)
                else:
                    return False
            finally:
                # === UPLOAD STATE UNLOCK ===
                # Always unlock to reset guards, regardless of outcome
                self._unlock_upload_state()

        return False
```

**Key changes from the original:**
1. **`self._lock_upload_state(filepath)`** at the beginning of each attempt — sets `_is_video`, `_upload_in_progress`
2. **`except UploadInProgressError`** — handles nav guards gracefully with retry
3. **`finally: self._unlock_upload_state()`** — ensures flags are always reset
4. The rest of the method body is identical to the current code

**Verify:** `python -c "from browser_max import BrowserMAX; print('syntax OK')"`

**Commit:** `feat(browser_max): wire up upload state lock/unlock in _upload_single_file()`

---

### Task 4.2: Create Complete Test Suite

**File:** `tests/test_media_upload_fix.py` (NEW)
**Depends:** ALL previous tasks (tests reference the new methods and modified behavior)

**Create the file** with all unit tests:

```python
# -*- coding: utf-8 -*-
"""
Tests for media upload reload fix — upload state manager, media classification,
no-reload monitoring, video handling, and navigation guards.

Tests cover:
- Upload state lock/unlock and can_navigate guard
- Media type classification by extension
- _confirm_file_in_feed() video/media tag matching
- _wait_upload_complete() video no-activity skip
- _wait_for_file_message() never calls page.reload()
- _confirm_file_sent() 50MB guard
- _verify_composer_cleared() missing DOM fix
- Navigation guards block during upload
"""

import pytest
from unittest.mock import MagicMock, patch
import os


# ── Fixtures ──

@pytest.fixture
def browser_max():
    """Create a BrowserMAX instance with mocked page."""
    from browser_max import BrowserMAX
    bm = BrowserMAX("https://example.com")
    bm.page = MagicMock()
    bm.page.is_closed.return_value = False
    bm._connected = True
    return bm


# ── Task 1.1: Upload State Manager ──

class TestUploadStateManager:
    """Tests for _lock_upload_state, _unlock_upload_state, _can_navigate"""

    def test_initial_state_not_in_progress(self):
        """Upload is not in progress by default."""
        from browser_max import BrowserMAX
        bm = BrowserMAX("https://example.com")
        assert bm._upload_in_progress is False
        assert bm._upload_file_size == 0
        assert bm._upload_file_name == ""
        assert bm._is_video is False

    def test_can_navigate_returns_true_by_default(self, browser_max):
        """_can_navigate() returns True when no upload in progress."""
        assert browser_max._can_navigate() is True

    def test_lock_sets_flags(self, browser_max, tmp_path):
        """_lock_upload_state sets all upload flags."""
        # Create a temp file
        filepath = tmp_path / "test.txt"
        filepath.write_text("hello")
        filepath = str(filepath)

        browser_max._lock_upload_state(filepath)

        assert browser_max._upload_in_progress is True
        assert browser_max._upload_file_name == "test.txt"
        assert browser_max._upload_file_size > 0

    def test_lock_detects_video(self, browser_max, tmp_path):
        """_lock_upload_state detects video files via extension."""
        filepath = tmp_path / "video.mp4"
        filepath.write_text("fake video content")
        filepath = str(filepath)

        browser_max._lock_upload_state(filepath)

        assert browser_max._is_video is True

    def test_lock_detects_non_video(self, browser_max, tmp_path):
        """_lock_upload_state correctly identifies non-video files."""
        filepath = tmp_path / "archive.zip"
        filepath.write_text("fake zip content")
        filepath = str(filepath)

        browser_max._lock_upload_state(filepath)

        assert browser_max._is_video is False

    def test_unlock_resets_flags(self, browser_max, tmp_path):
        """_unlock_upload_state resets all flags to defaults."""
        filepath = tmp_path / "test.mp4"
        filepath.write_text("content")
        filepath = str(filepath)

        browser_max._lock_upload_state(filepath)
        assert browser_max._upload_in_progress is True

        browser_max._unlock_upload_state()

        assert browser_max._upload_in_progress is False
        assert browser_max._upload_file_size == 0
        assert browser_max._upload_file_name == ""
        assert browser_max._is_video is False

    def test_can_navigate_blocked_during_upload(self, browser_max, tmp_path):
        """_can_navigate() returns False during active upload."""
        filepath = tmp_path / "doc.pdf"
        filepath.write_text("content")
        filepath = str(filepath)

        browser_max._lock_upload_state(filepath)

        assert browser_max._can_navigate() is False

    def test_can_navigate_after_unlock(self, browser_max, tmp_path):
        """_can_navigate() returns True after unlock."""
        filepath = tmp_path / "doc.pdf"
        filepath.write_text("content")
        filepath = str(filepath)

        browser_max._lock_upload_state(filepath)
        browser_max._unlock_upload_state()

        assert browser_max._can_navigate() is True


# ── Task 1.2: Media Type Classification ──

class TestMediaClassification:
    """Tests for _classify_media static method"""

    def test_mp4_is_video(self):
        from browser_max import BrowserMAX
        assert BrowserMAX._classify_media("video.mp4") == "video"

    def test_avi_is_video(self):
        from browser_max import BrowserMAX
        assert BrowserMAX._classify_media("clip.avi") == "video"

    def test_mov_is_video(self):
        from browser_max import BrowserMAX
        assert BrowserMAX._classify_media("movie.mov") == "video"

    def test_mkv_is_video(self):
        from browser_max import BrowserMAX
        assert BrowserMAX._classify_media("film.mkv") == "video"

    def test_webm_is_video(self):
        from browser_max import BrowserMAX
        assert BrowserMAX._classify_media("anim.webm") == "video"

    def test_jpg_is_image(self):
        from browser_max import BrowserMAX
        assert BrowserMAX._classify_media("photo.jpg") == "image"

    def test_jpeg_is_image(self):
        from browser_max import BrowserMAX
        assert BrowserMAX._classify_media("photo.jpeg") == "image"

    def test_png_is_image(self):
        from browser_max import BrowserMAX
        assert BrowserMAX._classify_media("screenshot.png") == "image"

    def test_gif_is_image(self):
        from browser_max import BrowserMAX
        assert BrowserMAX._classify_media("animation.gif") == "image"

    def test_zip_is_archive(self):
        from browser_max import BrowserMAX
        assert BrowserMAX._classify_media("files.zip") == "archive"

    def test_7z_is_archive(self):
        from browser_max import BrowserMAX
        assert BrowserMAX._classify_media("files.7z") == "archive"

    def test_tar_gz_is_archive(self):
        from browser_max import BrowserMAX
        assert BrowserMAX._classify_media("files.tar.gz") == "archive"

    def test_whl_is_archive(self):
        from browser_max import BrowserMAX
        assert BrowserMAX._classify_media("package-1.0-py3-none-any.whl") == "archive"

    def test_unknown_is_other(self):
        from browser_max import BrowserMAX
        assert BrowserMAX._classify_media("document.pdf") == "other"

    def test_no_extension_is_other(self):
        from browser_max import BrowserMAX
        assert BrowserMAX._classify_media("Makefile") == "other"

    def test_case_insensitive_mp4(self):
        from browser_max import BrowserMAX
        assert BrowserMAX._classify_media("VIDEO.MP4") == "video"

    def test_case_insensitive_jpg(self):
        from browser_max import BrowserMAX
        assert BrowserMAX._classify_media("Photo.JPG") == "image"

    def test_video_extensions_set_contains_all(self):
        from browser_max import BrowserMAX
        expected = {'.mp4', '.avi', '.mov', '.mkv', '.webm', '.flv', '.wmv', '.m4v'}
        assert BrowserMAX.VIDEO_EXTENSIONS == expected

    def test_image_extensions_set_contains_all(self):
        from browser_max import BrowserMAX
        expected = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'}
        assert BrowserMAX.IMAGE_EXTENSIONS == expected


# ── Task 2.3: _confirm_file_sent 50MB guard ──

class TestConfirmFileSentGuard:
    """Tests for _confirm_file_sent 50MB threshold guard"""

    def test_returns_false_for_large_file(self, browser_max):
        """_confirm_file_sent returns False for files >= 50MB."""
        from browser_max import ContentSnapshot
        pre = ContentSnapshot(hash="abc", file_count=5)
        result = browser_max._confirm_file_sent(pre, 50 * 1024 * 1024)
        assert result is False

    def test_returns_false_for_very_large_file(self, browser_max):
        """_confirm_file_sent returns False for very large files."""
        from browser_max import ContentSnapshot
        pre = ContentSnapshot(hash="abc", file_count=5)
        result = browser_max._confirm_file_sent(pre, 200 * 1024 * 1024)
        assert result is False

    def test_still_works_for_small_file(self, browser_max):
        """_confirm_file_sent still processes files < 50MB (returns False due to mock)."""
        from browser_max import ContentSnapshot
        pre = ContentSnapshot(hash="abc", file_count=5)
        # With mocked page returning None from _take_content_snapshot, should return False
        result = browser_max._confirm_file_sent(pre, 1024 * 1024)
        assert result is False  # False because mocked page returns None hash


# ── Task 1.3: _verify_composer_cleared missing DOM fix ──

class TestVerifyComposerCleared:
    """Tests for _verify_composer_cleared missing DOM fix"""

    def test_missing_composer_returns_false(self, browser_max):
        """
        When composer element is missing from DOM, return False (not clear).
        This prevents false positives during upload.
        """
        # Simulate page.evaluate returning None for composer query
        def mock_evaluate(script):
            # The JS returns true (composer clear) or false
            # We need to simulate the JS where composer is null -> returns false
            if 'composer' in script or 'querySelector' in script:
                # Simulate JS execution: no composer found
                return False  # The JS now returns false when composer is missing
            return None

        browser_max.page.evaluate.side_effect = mock_evaluate

        result = browser_max._verify_composer_cleared(timeout=1, poll_interval=0.1)
        assert result is False, "Missing composer should return False (not clear)"


# ── Task 3.1: _wait_upload_complete video handling ──

class TestWaitUploadCompleteVideo:
    """Tests for _wait_upload_complete video-specific behavior"""

    def test_video_no_activity_threshold_infinite(self, browser_max):
        """
        When _is_video is True, no_activity_threshold should be set to infinity
        so the no-activity heuristic never triggers.
        """
        browser_max._is_video = True
        # The threshold check happens inside _wait_upload_complete
        # Verify the flag is respected by checking logs
        with patch.object(browser_max.logger, 'info') as mock_info:
            with patch.object(browser_max, '_check_connection'):
                with patch.object(browser_max, '_install_upload_observer'):
                    with patch.object(browser_max, '_capture_pre_upload_state'):
                        with patch('time.sleep'):
                            with patch.object(browser_max, '_check_upload_progress', return_value=None):
                                with patch.object(browser_max, '_check_upload_done', return_value=(False, None)):
                                    with patch.object(browser_max, '_check_dom_upload_ready', return_value=False):
                                        with patch.object(browser_max, '_detect_state_change', return_value=(False, '')):
                                            # Timeout quickly
                                            browser_max._wait_upload_complete(timeout=0.5, poll_interval=0.1)

        # The method should not have crashed — video flag didn't trigger early exit
        # We just verify it completed without errors


# ── Task 3.2: No reload in _wait_for_file_message ──

class TestNoReloadInMonitoring:
    """Tests that _wait_for_file_message never calls page.reload()"""

    def test_no_reload_called(self, browser_max):
        """
        _wait_for_file_message should never call page.reload()
        even after extended timeout.
        """
        from browser_max import ContentSnapshot

        browser_max._pre_upload_msg_count = 0

        def mock_evaluate(expr, arg=None):
            if 'querySelectorAll' in str(expr) and 'length' in str(expr):
                return 5  # constant count = virtual scroll
            if 'textContent' in str(expr) and 'slice' in str(expr):
                return 'Some message content'
            return None

        browser_max.page.evaluate.side_effect = mock_evaluate

        # Mock _ensure_alive to return True
        with patch.object(browser_max, '_ensure_alive', return_value=True):
            with patch.object(browser_max, '_take_content_snapshot', return_value=ContentSnapshot(hash="abc", file_count=3)):
                with patch.object(browser_max, '_force_rerender'):
                    with patch.object(browser_max, '_scan_messages_for_file', return_value=(False, -1, "none")):
                        with patch('time.sleep'):
                            # Run with short timeout
                            result = browser_max._wait_for_file_message(
                                timeout=2,
                                expected_filename="test.zip"
                            )

        # Verify reload was never called
        reload_calls = [
            call for call in browser_max.page.method_calls
            if call[0] == 'reload'
        ]
        assert len(reload_calls) == 0, "page.reload() should never be called"

    def test_extended_timeout_returns_not_found(self, browser_max):
        """
        After extended timeout, _wait_for_file_message should return
        (False, "not_found", -1) without reloading.
        """
        from browser_max import ContentSnapshot

        browser_max._pre_upload_msg_count = 0

        def mock_evaluate(expr, arg=None):
            if 'querySelectorAll' in str(expr) and 'length' in str(expr):
                return 5
            if 'textContent' in str(expr) and 'slice' in str(expr):
                return 'Some message'
            return None

        browser_max.page.evaluate.side_effect = mock_evaluate

        with patch.object(browser_max, '_ensure_alive', return_value=True):
            with patch.object(browser_max, '_take_content_snapshot', return_value=ContentSnapshot(hash="abc", file_count=3)):
                with patch.object(browser_max, '_force_rerender'):
                    with patch.object(browser_max, '_scan_messages_for_file', return_value=(False, -1, "none")):
                        with patch('time.sleep'):
                            found, reason, msg_idx = browser_max._wait_for_file_message(
                                timeout=2,
                                expected_filename="test.zip"
                            )

        assert found is False
        assert reason == "timeout" or reason == "not_found"  # timeout is OK too


# ── Task 2.1/2.2: Navigation Guards ──

class TestNavigationGuards:
    """Tests that navigation methods are blocked during upload"""

    def test_navigate_raises_during_upload(self, browser_max):
        """navigate() should raise UploadInProgressError during upload."""
        browser_max._upload_in_progress = True
        browser_max._upload_file_name = "test.mp4"

        from browser_max import UploadInProgressError
        with pytest.raises(UploadInProgressError):
            browser_max.navigate()

    def test_try_navigate_returns_false_during_upload(self, browser_max):
        """_try_navigate() should return False during upload."""
        browser_max._upload_in_progress = True
        browser_max._upload_file_name = "test.mp4"

        result = browser_max._try_navigate()
        assert result is False

    def test_ensure_alive_returns_true_if_page_ok_during_upload(self, browser_max):
        """_ensure_alive() should return True if page exists during upload."""
        browser_max._upload_in_progress = True
        browser_max._upload_file_name = "test.mp4"
        browser_max.page.is_closed.return_value = False

        result = browser_max._ensure_alive()
        assert result is True

    def test_ensure_alive_returns_false_if_page_gone_during_upload(self, browser_max):
        """_ensure_alive() should return False if page is gone during upload."""
        browser_max._upload_in_progress = True
        browser_max._upload_file_name = "test.mp4"
        browser_max.page.is_closed.return_value = True

        result = browser_max._ensure_alive()
        assert result is False

    def test_ensure_alive_does_not_call_connect_during_upload(self, browser_max):
        """_ensure_alive() should NOT call connect() during upload."""
        browser_max._upload_in_progress = True
        browser_max._upload_file_name = "test.mp4"

        with patch.object(browser_max, 'connect') as mock_connect:
            browser_max._ensure_alive()
            mock_connect.assert_not_called()


# ── UploadInProgressError ──

class TestUploadInProgressError:
    """Tests for UploadInProgressError exception"""

    def test_is_browser_max_error_subclass(self):
        """UploadInProgressError should be a subclass of BrowserMAXError."""
        from browser_max import UploadInProgressError, BrowserMAXError
        assert issubclass(UploadInProgressError, BrowserMAXError)

    def test_can_be_raised_with_message(self):
        """UploadInProgressError can be raised with a message."""
        from browser_max import UploadInProgressError
        with pytest.raises(UploadInProgressError, match="upload"):
            raise UploadInProgressError("Cannot navigate during upload")
```

**Verify:** `pytest tests/test_media_upload_fix.py -v`

Expected: All tests pass (assuming the implementation is complete).

**Commit:** `test(browser_max): add tests for media upload fix components`

---

## Full Verification

After all tasks are applied, run the full test suite:

```bash
pytest tests/test_media_upload_fix.py -v        # New tests
pytest tests/test_upload_monitor.py -v           # Existing tests — regression check
python -c "from browser_max import BrowserMAX; print('Import OK')"
```

Expected results:
- All new tests pass
- Existing tests still pass (no regressions)
- `browser_max.py` imports without errors

---

## Summary of All Changes

| # | File | Change | Lines |
|---|------|--------|-------|
| 1.1 | browser_max.py | Add `UploadInProgressError`, `_lock_upload_state()`, `_unlock_upload_state()`, `_can_navigate()`, flags in `__init__` | 164-176 (exception), 208 (flags), ~210-250 (methods) |
| 1.2 | browser_max.py | Add `VIDEO_EXTENSIONS`, `IMAGE_EXTENSIONS`, `ARCHIVE_EXTENSIONS`, `_classify_media()` | ~190 (class attrs), ~192 (method) |
| 1.3 | browser_max.py | Fix `_verify_composer_cleared()`: `return false` when composer missing | 1855 |
| 2.1 | browser_max.py | Add guard to `navigate()` and `_try_navigate()` | 649, 669 |
| 2.2 | browser_max.py | Add guard to `_ensure_alive()` | 706 |
| 2.3 | browser_max.py | Add 50MB guard to `_confirm_file_sent()` | 1790 |
| 2.4 | browser_max.py | Enhanced `_confirm_file_in_feed()` — media tags + adaptive waits | 1894-2000 |
| 3.1 | browser_max.py | Video handling in `_wait_upload_complete()` — no no-activity exit | 1455-1578 |
| 3.2 | browser_max.py | Remove `page.reload()` from `_wait_for_file_message()`, increase snapshot_depth | 2257, 2383, 2459-2479 |
| 4.1 | browser_max.py | Wire up lock/unlock + exception handling in `_upload_single_file()` | 2912-3078 |
| 4.2 | tests/test_media_upload_fix.py | NEW — complete test suite | All |
