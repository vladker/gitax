# Upload Monitor Fix Implementation Plan

## Overview

This plan implements fixes for the upload confirmation monitor that hangs when uploading files to MAX messenger. The root causes are:

1. **Enter key doesn't always send the file** - file may stay in composer
2. **Content snapshot hash doesn't change** due to virtual scrolling in MAX
3. **No fallback mechanism** when DOM gets stuck

## Changes Summary

| Component | File | Change Type |
|-----------|------|-------------|
| `_verify_message_sent()` | `browser_max.py` | New method |
| `_take_content_snapshot()` | `browser_max.py` | Enhanced return type |
| `_wait_for_file_message()` | `browser_max.py` | Enhanced with fallback |
| `_upload_single_file()` | `browser_max.py` | Calls new verify method |
| Tests | `tests/test_upload_monitor.py` | New test classes |

## Implementation Details

### 1. New Method: `_verify_message_sent()`

**Location:** `browser_max.py` after `_send_message()` method (around line 1987)

**Purpose:** Verify that pressing Enter actually sent the message with the attached file. If the composer still contains the file, try clicking the send button as an alternative.

**Implementation:**

```python
def _verify_message_sent(self, timeout: int = 10) -> bool:
    \"\"\"
    Verify that the message was actually sent (composer cleared).
    If composer still has content after pressing Enter, try alternative send method.

    Args:
        timeout: Maximum seconds to wait for composer to clear

    Returns:
        True if message was sent successfully, False otherwise
    \"\"\"
    start = time.time()

    # Check if composer is already cleared (message sent immediately)
    if self._is_composer_empty():
        self.logger.debug("Composer cleared - message sent")
        return True

    # Wait for composer to clear
    while time.time() - start < timeout:
        if self._is_composer_empty():
            self.logger.debug("Composer cleared after wait")
            return True
        time.sleep(0.5)

    # Composer still has content - try clicking send button
    self.logger.info("Composer not cleared, trying send button...")
    if self._click_send_button():
        # Wait again for composer to clear
        start = time.time()
        while time.time() - start < 5:
            if self._is_composer_empty():
                self.logger.debug("Composer cleared after button click")
                return True
            time.sleep(0.5)

    self.logger.warning("Composer still not cleared after verification timeout")
    return False


def _is_composer_empty(self) -> bool:
    \"\"\"
    Check if the message composer is empty (no text, no attached files).

    Returns:
        True if composer is empty, False otherwise
    \"\"\"
    try:
        result = self.page.evaluate(\"\"\"
            () => {
                // Find composer element
                const composer = document.querySelector(
                    '[contenteditable="true"], [contenteditable], div[role="textbox"], [class*="composer"]'
                );
                if (!composer) return true; // No composer found = empty

                // Check for text content
                const text = composer.textContent?.trim() || '';
                if (text) return false;

                // Check for file indicators in composer
                const hasFile = composer.querySelector(
                    '[class*="preview"], [class*="file-item"], [class*="attach"], [class*="upload"], [data-file]'
                );
                if (hasFile) return false;

                // Check if composer has file-related classes
                const classes = composer.className || '';
                if (/with-file|has-file|file-attached/.test(classes)) return false;

                return true;
            }
        \"\"\")
        return result is True
    except Exception as e:
        self.logger.debug(f"Composer check error: {e}")
        return True  # Assume empty on error to avoid blocking


def _click_send_button(self) -> bool:
    \"\"\"
    Click the send button as an alternative to pressing Enter.

    Returns:
        True if button was clicked, False otherwise
    \"\"\"
    try:
        # Try various send button selectors
        selectors = [
            'button[type="submit"]',
            '[aria-label="Send"]',
            '[class*="send"] button',
            'button:has-text("Send")',
            'button:has-text("Отправить")',
        ]

        for selector in selectors:
            try:
                btn = self.page.locator(selector).first
                if btn.is_visible(timeout=1000):
                    btn.click()
                    self.logger.debug(f"Send button clicked: {selector}")
                    return True
            except Exception:
                continue

        self.logger.warning("Send button not found")
        return False
    except Exception as e:
        self.logger.debug(f"Send button click error: {e}")
        return False
```

### 2. Enhanced `_take_content_snapshot()`

**Location:** `browser_max.py` around line 1387

**Change:** Return a tuple `(hash, scroll_top, file_count)` instead of just a hash string. Any change in any of the three signals triggers a scan.

**Implementation:**

```python
def _take_content_snapshot(self, depth: int = 15, window: int = 100) -> Optional[tuple[str, int, int]]:
    \"\"\"
    Capture a content snapshot of the last N messages with independent signals.

    Returns a tuple (hash, scroll_top, file_count) or None on failure.
    Used to detect new messages in virtual-scrolling feeds where
    DOM element count stays constant.

    Args:
        depth: Number of messages from the bottom to include
        window: Number of characters per message for hashing

    Returns:
        (hash_string, scroll_top_value, file_element_count) or None
    \"\"\"
    try:
        result = self.page.evaluate(f\"\"\"
            () => {{
                const msgs = document.querySelectorAll('[class*="message"]');
                const depth = {depth};
                const window = {window};
                const start = Math.max(0, msgs.length - depth);
                const texts = [];
                for (let i = start; i < msgs.length; i++) {{
                    const text = (msgs[i].textContent || '').trim();
                    texts.push(text.slice(0, window));
                }}

                // Get scrollTop of scroll container
                let scrollTop = 0;
                const scrollContainer = window.__gitax_scroll ||
                    document.querySelector('[class*="messages"],[class*="lenta"],[class*="feed"]');
                if (scrollContainer) {{
                    scrollTop = scrollContainer.scrollTop;
                }}

                // Count file-related elements
                const fileElements = document.querySelectorAll(
                    '[class*="file"],[class*="attach"],[class*="preview"]'
                );
                const fileCount = fileElements.length;

                return {{ texts, scrollTop, fileCount }};
            }}
        \"\"\")

        if not result or not result.get('texts'):
            return None

        texts = result['texts']
        scroll_top = result.get('scrollTop', 0)
        file_count = result.get('fileCount', 0)

        combined = "\\n".join(texts)
        import hashlib
        hash_value = hashlib.sha256(combined.encode("utf-8")).hexdigest()

        return (hash_value, scroll_top, file_count)
    except Exception as e:
        self.logger.debug(f"Snapshot error: {e}")
        return None
```

### 3. Enhanced `_wait_for_file_message()` with Fallback

**Location:** `browser_max.py` around line 1595

**Changes:**
- Handle new tuple return from `_take_content_snapshot()`
- Add fallback re-render after 60s of no changes (scroll down/up)
- Add page reload after 120s of no changes
- Improved logging every 30 seconds

**Key implementation changes in the monitoring loop:**

```python
# In the monitoring loop, replace the snapshot comparison:
curr_snapshot = self._take_content_snapshot(depth=snapshot_depth)

if curr_snapshot and prev_snapshot:
    # Unpack tuple (hash, scroll_top, file_count)
    curr_hash, curr_scroll, curr_files = curr_snapshot
    prev_hash, prev_scroll, prev_files = prev_snapshot

    # Check if ANY signal changed
    hash_changed = curr_hash != prev_hash
    scroll_changed = curr_scroll != prev_scroll
    files_changed = curr_files != prev_files

    if hash_changed or scroll_changed or files_changed:
        changed_by = []
        if hash_changed: changed_by.append("hash")
        if scroll_changed: changed_by.append("scroll")
        if files_changed: changed_by.append("files")

        print(f"  [UPDATE] Signal changed at {elapsed}s: {', '.join(changed_by)}")

        # Scan for file message
        current_total = self.page.evaluate(
            "() => document.querySelectorAll('[class*=\"message\"]').length"
        ) or 0
        scan_start = max(baseline_count, current_total - snapshot_depth)

        found, msg_idx, detail = self._scan_messages_for_file(
            scan_start, current_total, search_name
        )
        if found:
            print(f"  [OK] FILE FOUND! Message #{msg_idx} ({detail})")
            return (True, "found", msg_idx)

        prev_snapshot = curr_snapshot
    elif curr_snapshot:
        prev_snapshot = curr_snapshot

# Add fallback re-render after 60s of no changes
if elapsed > 0 and elapsed % 30 == 0:
    print(f"  [MONITOR] {elapsed}s | waiting for content change...")
    # Log detailed state
    self._log_monitor_state(elapsed, base_count)

# Fallback: force re-render after 60s of no changes
if elapsed >= 60 and elapsed < 65 and prev_snapshot and not any_signal_changed_since_last_check:
    print(f"  [FALLBACK] No changes for 60s, forcing re-render...")
    self._force_rerender()
    # Take new snapshot after re-render
    new_snapshot = self._take_content_snapshot(depth=snapshot_depth)
    if new_snapshot and new_snapshot != prev_snapshot:
        print(f"  [UPDATE] Re-render detected change")
        # Scan again...

# Fallback: reload page after 120s of no changes
if elapsed >= 120 and elapsed < 125 and prev_snapshot and not any_signal_changed_since_last_check:
    print(f"  [FALLBACK] No changes for 120s, reloading page...")
    self._reload_and_rescan(baseline_count, search_name)
```

### 4. Integration in `_upload_single_file()`

**Location:** `browser_max.py` around line 2218

**Change:** Add call to `_verify_message_sent()` after `_send_message()`:

```python
# After _send_message() call, add:
self.logger.debug("Verifying message was sent...")
if not self._verify_message_sent(timeout=10):
    self.logger.warning("Message verification failed - composer still has content")
    # Continue anyway - _wait_for_file_message will catch it
```

## Testing Strategy

### New Test Classes for `tests/test_upload_monitor.py`:

1. **TestVerifyMessageSent** - Test the new verification method
   - Test composer empty detection
   - Test composer not empty detection
   - Test send button fallback
   - Test timeout handling

2. **TestEnhancedSnapshot** - Test the enhanced snapshot
   - Test tuple return format
   - Test scrollTop signal
   - Test file_count signal
   - Test change detection with any signal

3. **TestFallbackRerender** - Test fallback mechanisms
   - Test scroll re-render
   - Test page reload fallback
   - Test timeout handling

4. **TestImprovedLogging** - Test logging every 30 seconds
   - Test state dump format
   - Test logging frequency

## Verification Steps

1. Run existing tests: `python -m pytest tests/test_upload_monitor.py -v`
2. Run new tests: `python -m pytest tests/test_upload_monitor.py -v -k "Verify|Enhanced|Fallback|Logging"`
3. Manual test with a small file upload to MAX
4. Verify logging output shows state dumps every 30 seconds

## Migration Notes

- Existing code that calls `_take_content_snapshot()` expects a string return. The enhanced version returns a tuple. All call sites in `_wait_for_file_message()` must be updated to handle the tuple.
- The `_verify_message_sent()` method is called after `_send_message()` in `_upload_single_file()`. If verification fails, the flow continues but logs a warning.
- The fallback mechanisms are triggered only after extended periods of no changes (60s scroll, 120s reload), so they won't affect normal operation.
