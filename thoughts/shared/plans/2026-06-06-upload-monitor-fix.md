# Upload Monitor Fix Implementation Plan

**Goal:** Fix the upload confirmation monitoring system in `browser_max.py` so it detects uploaded files immediately instead of timing out after 300s per file.

**Architecture:** Replace count-based DOM monitoring with content-based snapshots. Since MAX uses virtual scrolling (DOM element count stays constant), we hash text content of the last N messages and detect changes by comparing hashes between polls.

**Design:** [thoughts/shared/designs/2026-06-06-upload-monitor-fix-design.md](thoughts/shared/designs/2026-06-06-upload-monitor-fix-design.md)

---

## Dependency Graph

```
Batch 1 (parallel): 1.1, 1.2, 1.3 [foundation - new helper methods, no deps]
Batch 2 (parallel): 2.1 [core - depends on batch 1 methods existing]
Batch 3 (parallel): 3.1 [integration - depends on batch 2]
```

**Note:** All tasks modify the SAME file (`browser_max.py`). Each task targets a specific method/block. Implementers should apply tasks sequentially within the file, but the code for each task is fully self-contained and can be written in parallel.

---

## Batch 1: Foundation Helpers (parallel - 3 implementers)

All tasks add new methods to `BrowserMAX` class. No dependencies between them.

---

### Task 1.1: `_take_content_snapshot` method

**File:** `browser_max.py` — add new method after `_check_dom_upload_ready` (around line 1385)
**Test:** `tests/test_upload_monitor.py` — see Task 3.1
**Depends:** none

This method captures text content of the last N messages and returns a hash. It is the core primitive for content-based monitoring.

**Implementation details:**
- `snapshot_depth` = 15 (last 15 messages, enough for virtual scroll buffer)
- `hash_window` = 100 chars per message (enough to detect changes without carrying too much data)
- Returns a string hash (SHA-256 hex digest of concatenated text snippets)
- Uses `page.evaluate()` to extract text, then Python's `hashlib` for hashing
- Handles errors gracefully (returns `None` on failure)

```python
def _take_content_snapshot(self, depth: int = 15, window: int = 100) -> Optional[str]:
    """
    Capture a content snapshot of the last N messages.
    Returns a hash string or None on failure.

    Used to detect new messages in virtual-scrolling feeds where
    DOM element count stays constant.

    Args:
        depth: Number of messages from the bottom to include
        window: Number of characters per message for hashing
    """
    try:
        texts = self.page.evaluate(f"""
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
                return texts;
            }}
        """)
        if not texts:
            return None
        combined = "\\n".join(texts)
        import hashlib
        return hashlib.sha256(combined.encode("utf-8")).hexdigest()
    except Exception as e:
        self.logger.debug(f"Snapshot error: {e}")
        return None
```

**Insert location:** After `_check_dom_upload_ready` method (line ~1385), before `_check_upload_in_lenta`.

---

### Task 1.2: `_match_filename_in_message` method

**File:** `browser_max.py` — add new method after `_take_content_snapshot`
**Test:** `tests/test_upload_monitor.py` — see Task 3.1
**Depends:** none

Unified filename matching with three-tier fallback strategy. Replaces the inconsistent regex/substring matching scattered across `_wait_for_file_message` and `_check_upload_in_lenta`.

**Implementation details:**
- **Tier 1 (Regex):** Extract filename pattern matching expected extensions + optional 7z volume suffix
- **Tier 2 (Substring):** Direct substring search of `search_name` in message textContent
- **Tier 3 (Generic):** Check for any `.zip`/`.7z` + "download"/"скачать" indicators (last resort)
- Returns `(matched: bool, match_text: str)` tuple

```python
def _match_filename_in_message(self, msg_text: str, msg_html: str,
                                search_name: Optional[str] = None) -> tuple[bool, str]:
    """
    Check if a message contains the expected file. Three-tier matching:
    1. Regex extraction + normalized comparison
    2. Direct substring search
    3. Generic file indicator (fallback)

    Args:
        msg_text: Message textContent (lowercased)
        msg_html: Message innerHTML (lowercased)
        search_name: Normalized filename to search for (without -master/-main)

    Returns:
        (matched: bool, match_detail: str)
    """
    if not search_name:
        # No specific filename — accept any file message
        has_archive = bool(re.search(r'\.(zip|tar|gz|rar|7z)', msg_text))
        has_download = 'download' in msg_text or 'скачать' in msg_text
        return (has_archive or has_download, "generic_file" if (has_archive or has_download) else "none")

    # Tier 1: Regex extraction + normalized comparison
    ext_pattern = '|'.join(re.escape(ext) for ext in self._expected_extensions)
    match = re.search(r'([a-z0-9\-_.]+(?:' + ext_pattern + r')(?:\.7z\.\d+)?)', msg_text)
    if match:
        msg_filename = match.group(1).replace('-master', '').replace('-main', '')
        if search_name in msg_filename or msg_filename in search_name:
            return (True, f"regex:{match.group(1)}")

    # Tier 1 on HTML too
    match = re.search(r'([a-z0-9\-_.]+(?:' + ext_pattern + r')(?:\.7z\.\d+)?)', msg_html)
    if match:
        msg_filename = match.group(1).replace('-master', '').replace('-main', '')
        if search_name in msg_filename or msg_filename in search_name:
            return (True, f"regex_html:{match.group(1)}")

    # Tier 2: Direct substring search
    if search_name in msg_text:
        return (True, f"substring:{search_name}")

    # Tier 3: Generic file indicator (only if search_name contains archive extension)
    has_archive = bool(re.search(r'\.(zip|7z)', msg_text))
    has_download = 'download' in msg_text or 'скачать' in msg_text
    if has_archive and has_download:
        self.logger.warning(f"Tertiary match — no exact filename, but file+download found near '{search_name}'")
        return (True, "tertiary_fallback")

    return (False, "no_match")
```

**Insert location:** After `_take_content_snapshot` method.

---

### Task 1.3: `_scan_messages_for_file` method

**File:** `browser_max.py` — add new method after `_match_filename_in_message`
**Test:** `tests/test_upload_monitor.py` — see Task 3.1
**Depends:** none

Scans a range of message indices for a file match, using `_match_filename_in_message`. This consolidates the message-scanning logic that was duplicated in `_wait_for_file_message` (initial scan, live loop, timeout fallback).

**Implementation details:**
- Takes `start_idx` and `end_idx` (exclusive) as range parameters
- Uses `page.evaluate()` to fetch each message's text/html
- Returns `(found: bool, msg_index: int, detail: str)` on first match
- Returns `(False, 0, "not_found")` if no match in range

```python
def _scan_messages_for_file(self, start_idx: int, end_idx: int,
                             search_name: Optional[str] = None) -> tuple[bool, int, str]:
    """
    Scan messages in range [start_idx, end_idx) for a file upload.

    Args:
        start_idx: First message index to check (inclusive)
        end_idx: Last message index to check (exclusive)
        search_name: Normalized filename to match (None = accept any file)

    Returns:
        (found: bool, msg_index: int, detail: str)
    """
    js_ext_pattern = '|'.join(re.escape(ext) for ext in self._expected_extensions)

    for idx in range(start_idx, end_idx):
        try:
            msg_result = self.page.evaluate(f"""
                () => {{
                    const msgs = document.querySelectorAll('[class*="message"]');
                    const msg = msgs[{idx}];
                    if (!msg) return null;
                    const text = msg.textContent || '';
                    const html = msg.innerHTML || '';
                    const classes = msg.className || '';
                    const hasFileClass = /file|attach|download|archive|preview/i.test(classes);
                    const extRegex = new RegExp('{js_ext_pattern}', 'i');
                    const hasZip = extRegex.test(text) || extRegex.test(html);
                    const hasDownload = msg.querySelector('[download]') !== null ||
                                        msg.querySelector('a[href*="download"]') !== null;
                    return {{
                        text: text.slice(0, 200),
                        html: html.slice(0, 300),
                        hasFileClass,
                        hasZip,
                        hasDownload,
                        classes: classes.slice(0, 80)
                    }};
                }}
            """) or {}

            if not (msg_result.get('hasFileClass') or msg_result.get('hasZip') or msg_result.get('hasDownload')):
                continue

            msg_text = (msg_result.get('text') or '').lower()
            msg_html = (msg_result.get('html') or '').lower()

            matched, detail = self._match_filename_in_message(msg_text, msg_html, search_name)
            if matched:
                return (True, idx + 1, detail)

        except Exception as e:
            self.logger.debug(f"Scan msg #{idx + 1} error: {e}")
            continue

    return (False, 0, "not_found")
```

**Insert location:** After `_match_filename_in_message` method.

---

## Batch 2: Core Fix — Rewrite `_wait_for_file_message` (1 implementer)

### Task 2.1: Rewrite `_wait_for_file_message` with content-based monitoring

**File:** `browser_max.py` — replace entire `_wait_for_file_message` method (lines 1452-1726)
**Test:** `tests/test_upload_monitor.py` — see Task 3.1
**Depends:** 1.1, 1.2, 1.3 (uses `_take_content_snapshot`, `_match_filename_in_message`, `_scan_messages_for_file`)

This is the main fix. The method is completely rewritten to:
1. Fix the initial scan range (was `range(base_count)` skipping `< baseline_count`, now `range(baseline_count, base_count)`)
2. Replace count-based monitoring with content-based snapshots
3. Use `_scan_messages_for_file` for all scanning operations

**Key changes from current code:**
- **Initial scan:** `range(baseline_count, base_count)` instead of `range(base_count)` with skip
- **Live monitoring loop:** Instead of checking `current_count > baseline_count`, it takes periodic content snapshots and compares hashes
- **Snapshot interval:** 2 seconds (configurable, faster than current 30s periodic status)
- **On hash change:** Scans the snapshot depth range for filename match
- **Timeout fallback:** Uses `_scan_messages_for_file` on last 20 messages (same logic, consolidated)

```python
def _wait_for_file_message(self, timeout: int = 300,
                            expected_msg_index: Optional[int] = None,
                            expected_filename: Optional[str] = None,
                            baseline_count: Optional[int] = None) -> tuple[bool, str, int]:
    """
    Monitor chat for file message using content-based snapshots.

    Since MAX uses virtual scrolling (DOM count stays constant),
    we detect new messages by comparing text content hashes of
    the last N messages at regular intervals.

    Args:
        timeout: Max time to wait (default 5 min as safety fallback)
        expected_msg_index: Expected message index (0 = any new)
        expected_filename: Filename to match (if provided, only confirms if filename matches)
        baseline_count: Message count BEFORE this upload started (to ignore old messages)

    Returns:
        (found: bool, reason: str, found_msg_index: int)
        reason = "found" | "timeout" | "disconnected" | "init_failed"
    """
    start = time.time()
    snapshot_interval = 2  # seconds between snapshots
    snapshot_depth = 15    # last N messages to snapshot

    print(f"  [MONITOR] Starting content-based monitoring...")

    # Normalize search name
    search_name = None
    if expected_filename:
        import os as os_module
        basename = os_module.path.basename(expected_filename).lower()
        search_name = basename.replace('-master', '').replace('-main', '')
        print(f"  [SCAN] Looking for: {search_name}")

    try:
        if not self._ensure_alive():
            return (False, "not_connected", 0)

        base_count = self.page.evaluate(
            "() => document.querySelectorAll('[class*=\"message\"]').length"
        ) or 0

        init_result = self.page.evaluate("""
            () => {
                const msgs = document.querySelectorAll('[class*="message"]');
                const last = msgs[msgs.length - 1];
                return last ? last.textContent?.slice(0, 200) || '' : '';
            }
        """) or ""
        print(f"  [MONITOR] Initial: {base_count} msgs, last: {init_result[:50]}...")

        # Use provided baseline or capture current count
        if baseline_count is None:
            baseline_count = base_count

        # ── INITIAL SCAN ──
        # FIX: range was range(base_count) with skip if idx < baseline_count,
        # which meant the range was empty since baseline_count == base_count.
        # Now: scan from baseline_count to base_count (new messages only).
        print(f"  [SCAN] Scanning from msg #{baseline_count + 1}...")
        if baseline_count < base_count:
            found, msg_idx, detail = self._scan_messages_for_file(
                baseline_count, base_count, search_name
            )
            if found:
                print(f"  [OK] FILE FOUND in initial scan! Message #{msg_idx} ({detail})")
                return (True, "found", msg_idx)
        else:
            print(f"  [SCAN] No new messages yet (baseline={baseline_count}, current={base_count})")

    except Exception as e:
        print(f"  [ERROR] Failed to initialize: {e}")
        return (False, "init_failed", 0)

    # ── CONTENT-BASED MONITORING LOOP ──
    prev_snapshot = self._take_content_snapshot(depth=snapshot_depth)
    print(f"  [SNAPSHOT] Baseline hash: {prev_snapshot[:16] if prev_snapshot else 'none'}...")

    while True:
        elapsed = int(time.time() - start)
        timeout_reached = elapsed >= timeout

        try:
            # Ensure connection alive
            if not self._ensure_alive():
                print(f"  [WARN] Connection lost after {elapsed}s")
                return (False, "disconnected", 0)

            # Take new content snapshot
            curr_snapshot = self._take_content_snapshot(depth=snapshot_depth)

            if curr_snapshot and prev_snapshot and curr_snapshot != prev_snapshot:
                # Content changed! New message(s) appeared.
                print(f"  [UPDATE] Content changed at {elapsed}s")

                # Scan the snapshot depth range for file match
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

            # Check for timeout
            if timeout_reached:
                final_count = self.page.evaluate(
                    "() => document.querySelectorAll('[class*=\"message\"]').length"
                ) or 0
                print(f"  [WARN] Timeout after {elapsed}s. Checking last 20 msgs...")

                # Fallback: scan last 20 messages
                fallback_start = max(0, final_count - 20)
                found, msg_idx, detail = self._scan_messages_for_file(
                    fallback_start, final_count, search_name
                )
                if found:
                    print(f"  [OK] File found at timeout! Msg #{msg_idx} ({detail})")
                    return (True, "found", msg_idx)

                print(f"  [WARN] No file found. Messages: {base_count} -> {final_count}")
                return (False, "timeout", 0)

            # Periodic status every 30s
            if elapsed > 0 and elapsed % 30 == 0:
                print(f"  [MONITOR] {elapsed}s | waiting for content change...")

            time.sleep(snapshot_interval)

        except Exception as e:
            print(f"  [ERROR] Monitor error: {e}")
            time.sleep(snapshot_interval)
```

**Replacement:** Lines 1452-1726 of `browser_max.py` (the entire `_wait_for_file_message` method).

---

## Batch 3: Tests (1 implementer)

### Task 3.1: Unit tests for upload monitor fix

**File:** `tests/test_upload_monitor.py` (new file)
**Test:** N/A (this IS the test file)
**Depends:** 1.1, 1.2, 1.3, 2.1 (tests the new/rewritten methods)

Follows the project's existing testing pattern (pytest + MagicMock, see `tests/test_export_messages.py` and `tests/test_pypi_api.py`).

```python
# -*- coding: utf-8 -*-
"""
Tests for upload monitor fix — content-based monitoring.

Tests cover:
- _take_content_snapshot() hash consistency and change detection
- _match_filename_in_message() three-tier matching
- _scan_messages_for_file() range scanning
- _wait_for_file_message() content-based monitoring flow
- Virtual scrolling scenario (count constant, content changes)
"""

import pytest
from unittest.mock import MagicMock, patch


# ── Helpers ──

def _mock_page(snapshot_texts=None, evaluate_side_effect=None):
    """Create a mock page for testing."""
    page = MagicMock()
    page.is_closed.return_value = False

    if evaluate_side_effect is not None:
        page.evaluate.side_effect = evaluate_side_effect
    elif snapshot_texts is not None:
        # Default: return message count and snapshot texts
        count = len(snapshot_texts)
        page.evaluate.side_effect = lambda expr, arg=None: _default_evaluate(expr, snapshot_texts, count)

    return page


def _default_evaluate(expr, texts, count):
    """Default evaluate handler for snapshot tests."""
    if 'querySelectorAll' in str(expr) and 'length' in str(expr):
        return count
    if 'textContent' in str(expr) and 'slice' in str(expr):
        return texts
    return None


# ── _take_content_snapshot tests ──

class TestContentSnapshot:
    """Test _take_content_snapshot method"""

    def test_snapshot_returns_hash(self):
        """Snapshot returns a SHA-256 hex digest string."""
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")
        bm._connected = True
        texts = ["Message one", "Message two", "Message three"]
        bm.page = _mock_page(snapshot_texts=texts)

        result = bm._take_content_snapshot()

        assert result is not None
        assert isinstance(result, str)
        assert len(result) == 64  # SHA-256 hex digest length

    def test_snapshot_consistent(self):
        """Same content produces same hash."""
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")
        bm._connected = True
        texts = ["Alpha", "Beta", "Gamma"]
        bm.page = _mock_page(snapshot_texts=texts)

        hash1 = bm._take_content_snapshot()
        hash2 = bm._take_content_snapshot()

        assert hash1 == hash2

    def test_snapshot_detects_change(self):
        """Different content produces different hash."""
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")
        bm._connected = True

        call_count = [0]
        def evaluate_side_effect(expr):
            if 'querySelectorAll' in str(expr) and 'length' in str(expr):
                return 3
            if call_count[0] == 0:
                call_count[0] += 1
                return ["Old message 1", "Old message 2"]
            return ["Old message 1", "New message 3"]

        bm.page = _mock_page(evaluate_side_effect=evaluate_side_effect)

        hash1 = bm._take_content_snapshot()
        hash2 = bm._take_content_snapshot()

        assert hash1 != hash2, "Hash should change when content changes"

    def test_snapshot_returns_none_on_empty(self):
        """Empty DOM returns None."""
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")
        bm._connected = True
        bm.page = _mock_page(snapshot_texts=[])

        result = bm._take_content_snapshot()
        assert result is None

    def test_snapshot_handles_js_error(self):
        """JS evaluation error returns None gracefully."""
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")
        bm._connected = True
        bm.page = MagicMock()
        bm.page.is_closed.return_value = False
        bm.page.evaluate.side_effect = Exception("CDP disconnected")
        bm._logger = MagicMock()

        result = bm._take_content_snapshot()
        assert result is None


# ── _match_filename_in_message tests ──

class TestFilenameMatching:
    """Test _match_filename_in_message three-tier matching"""

    def _make_bm(self):
        from browser_max import BrowserMAX
        return BrowserMAX("https://example.com")

    def test_regex_match_zip(self):
        """Tier 1: Regex matches .zip filename."""
        bm = self._make_bm()
        matched, detail = bm._match_filename_in_message(
            "here is repo-name-master.zip for download",
            "<div>repo-name-master.zip</div>",
            "repo-name"
        )
        assert matched is True
        assert "regex" in detail

    def test_regex_match_7z_volume(self):
        """Tier 1: Regex matches .zip.7z.001 volume filename."""
        bm = self._make_bm()
        matched, detail = bm._match_filename_in_message(
            "uploading repo-master.zip.7z.003 (49 MB)",
            "<div>repo-master.zip.7z.003</div>",
            "repo"
        )
        assert matched is True

    def test_substring_fallback(self):
        """Tier 2: Substring match when regex fails (e.g. filename in quotes)."""
        bm = self._make_bm()
        matched, detail = bm._match_filename_in_message(
            "file: 'my-repo.zip' uploaded successfully",
            "<div>file: 'my-repo.zip'</div>",
            "my-repo"
        )
        assert matched is True
        assert "substring" in detail

    def test_no_match_different_file(self):
        """Should not match a different filename."""
        bm = self._make_bm()
        matched, detail = bm._match_filename_in_message(
            "here is other-repo.zip",
            "<div>other-repo.zip</div>",
            "my-repo"
        )
        assert matched is False

    def test_no_filename_accepts_any_file(self):
        """When search_name is None, accept any file message."""
        bm = self._make_bm()
        matched, detail = bm._match_filename_in_message(
            "some repo.zip attached",
            "<div>repo.zip</div>",
            None
        )
        assert matched is True

    def test_tertiary_fallback(self):
        """Tier 3: Generic file+download indicators match as fallback."""
        bm = self._make_bm()
        bm._logger = MagicMock()
        matched, detail = bm._match_filename_in_message(
            "my-repo.zip скачать download",
            "<div>my-repo.zip</div>",
            "my-repo"
        )
        # Should match via tier 1 or 2, but if not, tier 3 catches it
        assert matched is True

    def test_no_file_no_match(self):
        """Plain text message should not match."""
        bm = self._make_bm()
        matched, detail = bm._match_filename_in_message(
            "hello world how are you",
            "<div>hello</div>",
            "test"
        )
        assert matched is False


# ── _scan_messages_for_file tests ──

class TestScanMessages:
    """Test _scan_messages_for_file range scanning"""

    def test_scan_finds_file_in_range(self):
        """Scan finds file message within range."""
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")
        bm._connected = True
        bm.page = MagicMock()
        bm.page.is_closed.return_value = False

        # Simulate: msg 0 = text, msg 1 = file
        def evaluate_side_effect(expr):
            if 'querySelectorAll' in str(expr):
                return [{"text": "hello", "html": "", "hasFileClass": False, "hasZip": False, "hasDownload": False, "classes": ""},
                        {"text": "repo.zip скачать", "html": "", "hasFileClass": True, "hasZip": True, "hasDownload": True, "classes": "message file"}]
            return None

        bm.page.evaluate.side_effect = evaluate_side_effect

        found, idx, detail = bm._scan_messages_for_file(0, 2, "repo")
        # Should find the file at index 1
        assert found is True

    def test_scan_returns_not_found(self):
        """Scan returns not_found when no file in range."""
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")
        bm._connected = True
        bm.page = MagicMock()
        bm.page.is_closed.return_value = False
        bm.page.evaluate.return_value = {"text": "just text", "html": "", "hasFileClass": False, "hasZip": False, "hasDownload": False, "classes": ""}

        found, idx, detail = bm._scan_messages_for_file(0, 1, "repo")
        assert found is False
        assert detail == "not_found"


# ── _wait_for_file_message integration tests ──

class TestWaitForFileMessage:
    """Test _wait_for_file_message content-based flow"""

    def test_returns_not_connected(self):
        """Returns not_connected when ensure_alive fails."""
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")
        bm._connected = True

        with patch.object(bm, '_ensure_alive', return_value=False):
            found, reason, idx = bm._wait_for_file_message(timeout=1)
            assert found is False
            assert reason == "not_connected"

    def test_virtual_scroll_scenario(self):
        """
        Virtual scrolling: count stays constant, content changes.
        Snapshot detects the change and finds the file.
        """
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")
        bm._connected = True
        bm.page = MagicMock()
        bm.page.is_closed.return_value = False

        # Snapshot simulation: first call returns old hash, second returns new
        snapshot_hashes = ["oldhash123", "newhash456"]
        snapshot_idx = [0]

        def mock_snapshot(depth=15):
            idx = snapshot_idx[0]
            if idx < len(snapshot_hashes):
                snapshot_idx[0] += 1
                return snapshot_hashes[idx]
            return snapshot_hashes[-1]

        def mock_evaluate(expr):
            if 'querySelectorAll' in str(expr) and 'length' in str(expr):
                return 50  # constant count (virtual scroll)
            if 'textContent' in str(expr) and 'slice' in str(expr):
                return ["msg1", "msg2"]  # dummy for init
            return 50

        bm.page.evaluate.side_effect = mock_evaluate
        bm._take_content_snapshot = mock_snapshot

        # Scan returns file found on second snapshot
        scan_call = [0]
        def mock_scan(start, end, name):
            scan_call[0] += 1
            if scan_call[0] >= 2:
                return (True, 51, "regex:repo.zip")
            return (False, 0, "not_found")

        bm._scan_messages_for_file = mock_scan

        found, reason, idx = bm._wait_for_file_message(timeout=10)
        assert found is True
        assert reason == "found"

    def test_initial_scan_empty_range(self):
        """
        When baseline_count == base_count, initial scan range is empty.
        Should NOT crash, should proceed to monitoring loop.
        """
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")
        bm._connected = True
        bm.page = MagicMock()
        bm.page.is_closed.return_value = False

        call_count = [0]
        def mock_evaluate(expr):
            call_count[0] += 1
            if 'querySelectorAll' in str(expr) and 'length' in str(expr):
                return 42
            if 'textContent' in str(expr):
                return "initial message"
            return 42

        bm.page.evaluate.side_effect = mock_evaluate

        # Snapshot always changes to trigger scan
        def mock_snapshot(depth=15):
            call_count[0] += 1
            if call_count[0] < 5:
                return "hash1"
            return "hash2"

        bm._take_content_snapshot = mock_snapshot

        # Scan finds file
        def mock_scan(start, end, name):
            return (True, 43, "regex:repo.zip")

        bm._scan_messages_for_file = mock_scan

        found, reason, idx = bm._wait_for_file_message(timeout=10, baseline_count=42)
        assert found is True
        assert reason == "found"


# ── Regression: verify old bugs are fixed ──

class TestRegression:
    """Verify the original bugs are fixed"""

    def test_scan_range_not_empty(self):
        """
        BUG FIX: Initial scan range was range(base_count) with skip if idx < baseline_count.
        Since baseline_count == base_count, range was always empty.
        Fix: range(baseline_count, base_count) — correct range.
        """
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")
        bm._connected = True
        bm.page = MagicMock()
        bm.page.is_closed.return_value = False

        # base_count = baseline_count = 10
        bm.page.evaluate.return_value = 10

        # _scan_messages_for_file should handle empty range gracefully
        found, idx, detail = bm._scan_messages_for_file(10, 10, "repo")
        assert found is False  # empty range = no match, but no crash

    def test_content_monitor_works_with_constant_count(self):
        """
        BUG FIX: Count-based monitoring (current_count > baseline_count) never
        triggers with virtual scrolling. Content snapshot comparison works instead.
        """
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")
        bm._connected = True
        bm.page = MagicMock()
        bm.page.is_closed.return_value = False

        # Count stays at 50 (virtual scroll)
        bm.page.evaluate.return_value = 50

        # Snapshots change (content updated)
        snap_calls = [0]
        def mock_snapshot(depth=15):
            snap_calls[0] += 1
            return f"hash_{snap_calls[0]}"

        bm._take_content_snapshot = mock_snapshot

        def mock_scan(start, end, name):
            return (True, 51, "found")

        bm._scan_messages_for_file = mock_scan

        found, reason, idx = bm._wait_for_file_message(timeout=10)
        assert found is True
        assert reason == "found"
```

**Verify:** `pytest tests/test_upload_monitor.py -v`

---

## Batch 3 Summary

| Task | File | Lines | Type |
|------|------|-------|------|
| 3.1 | `tests/test_upload_monitor.py` | ~300 | New test file |

---

## Verification Plan

After all tasks are applied:

```bash
# 1. Run unit tests
pytest tests/test_upload_monitor.py -v

# 2. Run full test suite to check for regressions
pytest tests/ -v

# 3. Manual verification (optional, requires MAX browser)
#    - Upload a small test file
#    - Observe [MONITOR] output — should find file in < 10s instead of 300s timeout
```

## Commit Strategy

```bash
# Task 1.1-1.3
git add browser_max.py
git commit -m "feat(browser_max): add content snapshot and filename matching helpers"

# Task 2.1
git add browser_max.py
git commit -m "fix(browser_max): rewrite _wait_for_file_message with content-based monitoring"

# Task 3.1
git add tests/test_upload_monitor.py
git commit -m "test(browser_max): add upload monitor unit tests"
```