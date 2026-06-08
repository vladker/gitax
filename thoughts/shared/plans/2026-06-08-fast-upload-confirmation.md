# Fast Upload Confirmation — Delta Snapshot Implementation Plan

**Goal:** Replace the 18-second multi-phase confirmation flow with a 0.5–3 second delta-snapshot check, falling back to the existing monitoring only when needed.

**Architecture:** Capture a content hash before pressing Enter → wait for render → capture hash again → if changed, file was sent. No filename matching needed because uploads are sequential. When the delta check fails (rare), fall back to `_wait_for_file_message()` which is itself optimized to skip useless phases under virtual scrolling.

**Design:** `thoughts/shared/designs/2026-06-08-fast-upload-confirmation-design.md`

---

## Dependency Graph

```
Batch 1 (parallel): 1.1, 1.2, 1.3 [foundation — no deps]
Batch 2 (parallel): 2.1, 2.2 [core — depends on batch 1]
Batch 3 (parallel): 3.1 [tests — depends on batch 2]
```

---

## Batch 1: Foundation (parallel — 3 implementers)

All tasks in this batch have NO dependencies and run simultaneously.

### Task 1.1: `_take_content_snapshot()` — expose hash as accessible field

**File:** `browser_max.py` (line ~1737)
**Test:** none (minor refactor, covered by existing tests)
**Depends:** none

**What to change:**
The method currently returns `Optional[tuple[str, int]]` — a plain tuple `(hash, file_count)`. The design's `_confirm_file_sent()` calls `pre_snapshot.hash`, implying an object with a `.hash` attribute. Add a lightweight named-tuple-like wrapper so callers can do `snapshot.hash` instead of `snapshot[0]`.

**Implementation approach:** Use a simple `dataclass` or named tuple. Since the existing tests in `test_upload_monitor.py` compare snapshots as tuples (e.g., `assert result1 == result2`), the wrapper must remain tuple-compatible. A `collections.namedtuple` or a lightweight class with `__eq__` will work.

**Exact change at line ~1737:**

```python
# BEFORE (line ~1737):
    def _take_content_snapshot(self, depth: int = 15, window: int = 100) -> Optional[tuple[str, int]]:

# AFTER:
    def _take_content_snapshot(self, depth: int = 15, window: int = 100) -> Optional["ContentSnapshot"]:
```

And the return statement (line ~1773):

```python
# BEFORE:
            return (hashlib.sha256(combined.encode("utf-8")).hexdigest(), result.get('fileCount', 0))

# AFTER:
            return ContentSnapshot(
                hash=hashlib.sha256(combined.encode("utf-8")).hexdigest(),
                file_count=result.get('fileCount', 0)
            )
```

Add the `ContentSnapshot` class near the top of the file (after imports, ~line 155):

```python
from dataclasses import dataclass

@dataclass
class ContentSnapshot:
    """Content snapshot with hash for change detection."""
    hash: str
    file_count: int
```

**Also update all callers that use tuple unpacking.** The existing code at line ~2137-2138 does:
```python
prev_snapshot = self._take_content_snapshot(depth=snapshot_depth)
if prev_snapshot:
    print(f"  [SNAPSHOT] Baseline hash: {prev_snapshot[0][:16]}... files: {prev_snapshot[1]}")
```

This must change to:
```python
print(f"  [SNAPSHOT] Baseline hash: {prev_snapshot.hash[:16]}... files: {prev_snapshot.file_count}")
```

Similarly, the comparison `curr_snapshot != prev_snapshot` at line ~2158 still works because dataclasses auto-generate `__eq__` based on fields.

**Also update line ~2204-2205:**
```python
# BEFORE:
new_snapshot = self._take_content_snapshot(depth=snapshot_depth)
if new_snapshot and new_snapshot != prev_snapshot:

# AFTER (no change needed — dataclass __eq__ works the same as tuple __eq__)
```

And line ~2237:
```python
# BEFORE:
prev_snapshot = self._take_content_snapshot(depth=snapshot_depth)

# AFTER (no change needed)
```

**Verify:** `python -m pytest tests/test_upload_monitor.py -v` — existing tests must still pass. The tests mock `_take_content_snapshot` to return tuples like `("hash1", 3)`. Those mocks need to be updated to return `ContentSnapshot` objects OR the tests mock the method directly (which they do, so the mocks override the real method and are unaffected).

**Commit:** `feat(browser_max): expose snapshot hash as attribute`

---

### Task 1.2: Add `_confirm_file_sent()` method

**File:** `browser_max.py` (insert after `_take_content_snapshot`, ~line 1777)
**Test:** covered by Task 3.1
**Depends:** 1.1 (uses `ContentSnapshot`)

**What to add:** A new method that checks if content changed after sending a file.

**Complete implementation — insert at line ~1777 (after `_take_content_snapshot`):**

```python
    def _confirm_file_sent(self, pre_snapshot: "ContentSnapshot", file_size_bytes: int) -> bool:
        """
        Fast confirmation: check if feed content changed after sending.

        Adaptive wait based on file size — photos render faster than videos.

        Args:
            pre_snapshot: Content snapshot taken BEFORE pressing Enter
            file_size_bytes: Size of the file in bytes

        Returns:
            True if content changed (file likely sent), False if no change detected
        """
        if not pre_snapshot:
            return False

        # Adaptive wait: photos render faster than videos
        size_mb = file_size_bytes / (1024 * 1024)
        if size_mb < 5:
            initial_wait = 0.5
        elif size_mb < 50:
            initial_wait = 1.0
        else:
            initial_wait = 2.0

        time.sleep(initial_wait)

        # First check
        post = self._take_content_snapshot()
        if post and post.hash != pre_snapshot.hash:
            return True

        # Retry with increasing delay
        for _ in range(2):
            time.sleep(1.0)
            post = self._take_content_snapshot()
            if post and post.hash != pre_snapshot.hash:
                return True

        return False  # Caller falls back to _wait_for_file_message
```

**Verify:** `python -c "from browser_max import BrowserMAX; bm = BrowserMAX('https://example.com'); print(hasattr(bm, '_confirm_file_sent'))"`

**Commit:** `feat(browser_max): add delta-snapshot confirmation method`

---

### Task 1.3: Reduce monitoring timeouts for small files

**File:** `browser_max.py` (line ~1902, `_compute_monitor_timeouts`)
**Test:** covered by existing tests in `test_upload_monitor.py`
**Depends:** none

**What to change:** The design specifies faster fallback timeouts for photos:
- Current: rerender=8s, reload=12s for <5MB
- New: rerender=3s, reload=6s for <5MB

**Exact change at lines ~1920-1921:**

```python
# BEFORE:
        if size_mb < 5:
            return (8, 12)

# AFTER:
        if size_mb < 5:
            return (3, 6)
```

**Verify:** Existing tests `test_small_file_short_timeouts` at line ~543 expect `(8, 12)`. This test MUST be updated:

```python
# In tests/test_upload_monitor.py, line ~543-545:
# BEFORE:
    def test_small_file_short_timeouts(self):
        """Files < 5MB should get 8s/12s timeouts."""
        from browser_max import BrowserMAX
        rerender, reload = BrowserMAX._compute_monitor_timeouts(1_000_000)  # ~1MB
        assert rerender == 8
        assert reload == 12

# AFTER:
    def test_small_file_short_timeouts(self):
        """Files < 5MB should get 3s/6s timeouts."""
        from browser_max import BrowserMAX
        rerender, reload = BrowserMAX._compute_monitor_timeouts(1_000_000)  # ~1MB
        assert rerender == 3
        assert reload == 6
```

**Commit:** `feat(browser_max): reduce monitoring timeouts for small files`

---

## Batch 2: Core Modules (parallel — 2 implementers)

All tasks in this batch depend on Batch 1 completing.

### Task 2.1: Modify `_upload_single_file()` — add delta confirmation

**File:** `browser_max.py` (line ~2672)
**Test:** covered by Task 3.1
**Depends:** 1.1, 1.2

**What to change:**
1. Capture pre-snapshot before the upload loop starts
2. After `_send_message()`, call `_confirm_file_sent()` instead of `time.sleep(2)` + `_wait_for_file_message()`
3. If delta check succeeds, return immediately
4. If delta check fails, fall back to existing `_wait_for_file_message()`

**Exact changes:**

At line ~2692, inside the retry loop, BEFORE `self._try_navigate()` (line ~2704), add the pre-snapshot. The snapshot should be captured ONCE per attempt (not per file — the loop retries on failure):

```python
# At line ~2692, inside the for loop, add:
                # Capture content snapshot before upload for delta confirmation
                pre_snapshot = self._take_content_snapshot()
```

At lines ~2756-2776, replace the current confirmation block:

```python
# BEFORE (lines 2756-2776):
                self.logger.debug("Sending file message...")
                self._send_message()

                # FIX 2: Give MAX time to render file message for small files
                # Large files (>10MB) already take time to upload, no delay needed
                if file_size_bytes < 10 * 1024 * 1024:
                    time.sleep(2)

                self.logger.debug("Waiting for file message confirmation...")
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

# AFTER:
                self.logger.debug("Sending file message...")
                self._send_message()

                # Fast delta confirmation — check if content changed
                import time as _time
                confirm_start = _time.time()
                confirmed = self._confirm_file_sent(pre_snapshot, file_size_bytes)
                confirm_elapsed = _time.time() - confirm_start

                if confirmed:
                    self.logger.info(
                        f"File confirmed (delta check, {confirm_elapsed:.1f}s)"
                    )
                    print(
                        f"  [OK] File confirmed (delta check, {confirm_elapsed:.1f}s)"
                    )
                    return True

                # Fallback: full confirmation flow for edge cases
                self.logger.debug(
                    f"Delta check took {confirm_elapsed:.1f}s, falling back to full confirmation"
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
```

**Key removals:**
- `time.sleep(2)` for small files is REMOVED — `_confirm_file_sent()` handles the adaptive delay internally
- The `if file_size_bytes < 10 * 1024 * 1024:` block is REMOVED

**Commit:** `feat(browser_max): add delta confirmation to upload flow`

---

### Task 2.2: Optimize `_wait_for_file_message()` — skip useless phases under virtual scroll

**File:** `browser_max.py` (line ~1994)
**Test:** covered by Task 3.1
**Depends:** 1.1

**What to change:** When virtual scrolling is detected (message count <= 150), skip the retry scan and fast mode phases since they rely on `message_count > baseline` which never triggers under virtual scroll. Jump directly to the content-based monitoring loop.

**Exact changes:**

At line ~2040, after getting `base_count`, add virtual scroll detection:

```python
# AFTER getting base_count (line ~2040-2042), add:
            # Detect virtual scrolling — stable count means count-based checks are useless
            is_virtual_scroll = base_count <= 150
            if is_virtual_scroll:
                print(f"  [MONITOR] Virtual scroll detected ({base_count} msgs), skipping count-based phases")
```

Then wrap the retry scan and fast mode phases with the virtual scroll check. The current code structure is:

```
[INIT] → [INITIAL SCAN] → [RETRY SCAN] → [FAST MODE] → [MONITORING LOOP]
```

Under virtual scroll, skip to:

```
[INIT] → [MONITORING LOOP]
```

**Exact implementation:**

At line ~2057, wrap the initial scan and retry scan:

```python
# BEFORE (lines ~2057-2089):
            # ── INITIAL SCAN ──
            # FIX: range was range(base_count) with skip if idx < baseline_count,
            # which meant the range was empty since baseline_count == base_count.
            # Now: scan from baseline_count to base_count (new messages only).
            # FIX 1: If range is empty, retry with delay to allow DOM to update.
            print(f"  [SCAN] Scanning from msg #{baseline_count + 1}...")
            if baseline_count < base_count:
                found, msg_idx, detail = self._scan_messages_for_file(
                    baseline_count, base_count, search_name
                )
                if found:
                    print(f"  [OK] FILE FOUND in initial scan! Message #{msg_idx} ({detail})")
                    return (True, "found", msg_idx)
            else:
                print(f"  [SCAN] No new messages yet (baseline={baseline_count}, current={base_count}), retrying...")
                # FIX 1: Retry initial scan with delay — gives MAX time to render
                for retry in range(2):
                    time.sleep(2)
                    try:
                        new_count = self.page.evaluate(
                            "() => document.querySelectorAll('[class*=\"message\"]').length"
                        ) or 0
                        scan_start = max(baseline_count, new_count - 15)
                        if scan_start < new_count:
                            found, msg_idx, detail = self._scan_messages_for_file(
                                scan_start, new_count, search_name
                            )
                            if found:
                                print(f"  [OK] FILE FOUND in retry scan #{retry + 1}! Message #{msg_idx} ({detail})")
                                return (True, "found", msg_idx)
                    except Exception as retry_err:
                        print(f"  [WARN] Retry scan #{retry + 1} failed: {retry_err}")

# AFTER:
            # ── INITIAL SCAN ──
            if not is_virtual_scroll:
                print(f"  [SCAN] Scanning from msg #{baseline_count + 1}...")
                if baseline_count < base_count:
                    found, msg_idx, detail = self._scan_messages_for_file(
                        baseline_count, base_count, search_name
                    )
                    if found:
                        print(f"  [OK] FILE FOUND in initial scan! Message #{msg_idx} ({detail})")
                        return (True, "found", msg_idx)
                else:
                    print(f"  [SCAN] No new messages yet (baseline={baseline_count}, current={base_count}), retrying...")
                    for retry in range(2):
                        time.sleep(2)
                        try:
                            new_count = self.page.evaluate(
                                "() => document.querySelectorAll('[class*=\"message\"]').length"
                            ) or 0
                            scan_start = max(baseline_count, new_count - 15)
                            if scan_start < new_count:
                                found, msg_idx, detail = self._scan_messages_for_file(
                                    scan_start, new_count, search_name
                                )
                                if found:
                                    print(f"  [OK] FILE FOUND in retry scan #{retry + 1}! Message #{msg_idx} ({detail})")
                                    return (True, "found", msg_idx)
                        except Exception as retry_err:
                            print(f"  [WARN] Retry scan #{retry + 1} failed: {retry_err}")
            else:
                print(f"  [SCAN] Skipping count-based scan (virtual scroll, {base_count} msgs)")
```

At line ~2094, wrap the fast mode block:

```python
# BEFORE (line ~2094):
        # ── FAST MODE: quick polls for small media files ──
        if fast_mode:

# AFTER:
        # ── FAST MODE: quick polls for small media files ──
        # Skip under virtual scroll — count-based polls are useless
        if fast_mode and not is_virtual_scroll:
```

**Verify:** `python -m pytest tests/test_upload_monitor.py -v` — existing tests must still pass. The virtual scroll detection is a new optimization that doesn't change the fallback behavior.

**Commit:** `feat(browser_max): skip useless phases under virtual scroll`

---

## Batch 3: Tests (1 implementer)

Depends on Batch 2 completing.

### Task 3.1: Tests for delta confirmation

**File:** `tests/test_upload_monitor.py` (append to existing file)
**Depends:** 2.1, 2.2

**What to add:** New test classes for the delta confirmation flow and virtual scroll detection.

**Complete test code — append to `tests/test_upload_monitor.py`:**

```python
# ── Delta Confirmation tests ──

class TestConfirmFileSent:
    """Test _confirm_file_sent delta-snapshot confirmation"""

    def test_confirms_on_first_check(self):
        """Hash changes on first check → confirmed immediately."""
        from browser_max import BrowserMAX, ContentSnapshot

        bm = BrowserMAX("https://example.com")
        bm._connected = True
        bm.page = MagicMock()
        bm.page.is_closed.return_value = False

        pre = ContentSnapshot(hash="aaa", file_count=5)
        post = ContentSnapshot(hash="bbb", file_count=6)

        bm._take_content_snapshot = MagicMock(side_effect=[post])

        with patch('time.sleep'):
            result = bm._confirm_file_sent(pre, 1_000_000)
            assert result is True
            bm._take_content_snapshot.assert_called_once()

    def test_confirms_on_retry(self):
        """Hash unchanged first time, changes on retry → confirmed."""
        from browser_max import BrowserMAX, ContentSnapshot

        bm = BrowserMAX("https://example.com")
        bm._connected = True
        bm.page = MagicMock()
        bm.page.is_closed.return_value = False

        pre = ContentSnapshot(hash="aaa", file_count=5)
        same = ContentSnapshot(hash="aaa", file_count=5)
        changed = ContentSnapshot(hash="bbb", file_count=6)

        bm._take_content_snapshot = MagicMock(side_effect=[same, changed])

        with patch('time.sleep'):
            result = bm._confirm_file_sent(pre, 1_000_000)
            assert result is True

    def test_returns_false_after_all_retries(self):
        """Hash never changes → falls back to caller."""
        from browser_max import BrowserMAX, ContentSnapshot

        bm = BrowserMAX("https://example.com")
        bm._connected = True
        bm.page = MagicMock()
        bm.page.is_closed.return_value = False

        pre = ContentSnapshot(hash="aaa", file_count=5)
        same = ContentSnapshot(hash="aaa", file_count=5)

        bm._take_content_snapshot = MagicMock(side_effect=[same, same, same])

        with patch('time.sleep'):
            result = bm._confirm_file_sent(pre, 1_000_000)
            assert result is False

    def test_returns_false_on_none_snapshot(self):
        """Pre-snapshot is None → cannot confirm."""
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")
        bm._connected = True
        bm.page = MagicMock()
        bm.page.is_closed.return_value = False

        with patch('time.sleep'):
            result = bm._confirm_file_sent(None, 1_000_000)
            assert result is False

    def test_post_snapshot_none_treated_as_no_change(self):
        """Post-snapshot returns None (JS error) → treated as no change, retries."""
        from browser_max import BrowserMAX, ContentSnapshot

        bm = BrowserMAX("https://example.com")
        bm._connected = True
        bm.page = MagicMock()
        bm.page.is_closed.return_value = False

        pre = ContentSnapshot(hash="aaa", file_count=5)
        changed = ContentSnapshot(hash="bbb", file_count=6)

        # First two calls return None (JS error), third succeeds with change
        bm._take_content_snapshot = MagicMock(side_effect=[None, None, changed])

        with patch('time.sleep'):
            result = bm._confirm_file_sent(pre, 1_000_000)
            # Should succeed on third call (retry 2)
            assert result is True


class TestVirtualScrollDetection:
    """Test virtual scroll detection in _wait_for_file_message"""

    def test_virtual_scroll_detected_at_129(self):
        """129 messages (real MAX count) triggers virtual scroll detection."""
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")
        bm._connected = True
        bm.page = MagicMock()
        bm.page.is_closed.return_value = False

        count_calls = [0]
        def mock_evaluate(expr):
            count_calls[0] += 1
            expr_str = str(expr)
            if 'textContent' in expr_str and 'slice' in expr_str:
                return "initial message"
            return 129  # typical virtual scroll count

        bm.page.evaluate.side_effect = mock_evaluate

        # Snapshot changes to trigger monitoring
        snap_count = [0]
        def mock_snapshot(depth=15):
            snap_count[0] += 1
            return (f"hash_{snap_count[0]}", 3)

        bm._take_content_snapshot = mock_snapshot

        # Scan finds file on first monitoring check
        def mock_scan(start, end, name):
            return (True, 130, "regex:test.zip")

        bm._scan_messages_for_file = mock_scan

        with patch('time.sleep'):
            found, reason, idx = bm._wait_for_file_message(timeout=10)
            assert found is True
            assert reason == "found"

    def test_no_virtual_scroll_above_150(self):
        """Count > 150 means NOT virtual scroll — count-based phases run."""
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")
        bm._connected = True
        bm.page = MagicMock()
        bm.page.is_closed.return_value = False

        def mock_evaluate(expr):
            expr_str = str(expr)
            if 'textContent' in expr_str and 'slice' in expr_str:
                return "initial message"
            return 200  # above threshold

        bm.page.evaluate.side_effect = mock_evaluate

        # Initial scan should run (not skipped)
        scan_called = [False]
        def mock_scan(start, end, name):
            scan_called[0] = True
            return (True, 150, "regex:test.zip")

        bm._scan_messages_for_file = mock_scan

        with patch('time.sleep'):
            bm._wait_for_file_message(timeout=10, baseline_count=100)
            assert scan_called[0], "Initial scan should run when count > 150"


class TestContentSnapshotDataclass:
    """Test ContentSnapshot dataclass behavior"""

    def test_snapshot_equality(self):
        """Snapshots with same hash and file_count are equal."""
        from browser_max import ContentSnapshot

        a = ContentSnapshot(hash="abc123", file_count=5)
        b = ContentSnapshot(hash="abc123", file_count=5)
        assert a == b

    def test_snapshot_inequality_hash(self):
        """Different hash means different snapshot."""
        from browser_max import ContentSnapshot

        a = ContentSnapshot(hash="abc123", file_count=5)
        b = ContentSnapshot(hash="def456", file_count=5)
        assert a != b

    def test_snapshot_inequality_count(self):
        """Different file_count means different snapshot."""
        from browser_max import ContentSnapshot

        a = ContentSnapshot(hash="abc123", file_count=5)
        b = ContentSnapshot(hash="abc123", file_count=6)
        assert a != b

    def test_snapshot_hash_attribute(self):
        """Hash is accessible as .hash attribute."""
        from browser_max import ContentSnapshot

        s = ContentSnapshot(hash="sha256hashvalue", file_count=10)
        assert s.hash == "sha256hashvalue"
        assert s.file_count == 10

    def test_snapshot_repr(self):
        """Dataclass has readable repr."""
        from browser_max import ContentSnapshot

        s = ContentSnapshot(hash="abc", file_count=3)
        assert "abc" in repr(s)
        assert "3" in repr(s)
```

**Verify:** `python -m pytest tests/test_upload_monitor.py -v`

**Commit:** `test(browser_max): add delta confirmation and virtual scroll tests`

---

## Summary of Changes

| File | Change | Lines affected |
|------|--------|----------------|
| `browser_max.py` | Add `ContentSnapshot` dataclass | ~line 155, +5 lines |
| `browser_max.py` | `_take_content_snapshot()` returns `ContentSnapshot` | ~line 1737, ~line 1773 |
| `browser_max.py` | All snapshot tuple accesses → attribute access | ~line 2138, and monitoring loop |
| `browser_max.py` | Add `_confirm_file_sent()` method | ~line 1777, +40 lines |
| `browser_max.py` | `_upload_single_file()` — pre-snapshot + delta check | ~line 2692, ~line 2756 |
| `browser_max.py` | `_upload_single_file()` — remove `time.sleep(2)` | ~line 2761 |
| `browser_max.py` | `_wait_for_file_message()` — virtual scroll detection | ~line 2042, ~line 2057, ~line 2094 |
| `browser_max.py` | `_compute_monitor_timeouts()` — faster timeouts for <5MB | ~line 1920 |
| `tests/test_upload_monitor.py` | Update timeout expectation for <5MB | ~line 543 |
| `tests/test_upload_monitor.py` | Add delta confirmation tests | append, +100 lines |
| `tests/test_upload_monitor.py` | Add virtual scroll detection tests | append, +50 lines |
| `tests/test_upload_monitor.py` | Add ContentSnapshot tests | append, +30 lines |

## Expected Performance Improvement

| Scenario | Before | After |
|----------|--------|-------|
| Photo 3MB (best case, 95%) | ~18s | ~0.6s |
| Photo 3MB (1 retry needed) | ~18s | ~1.6s |
| Photo 3MB (falls to monitoring) | ~18s | ~5-7s |
| Large file >49MB | unchanged | unchanged (uses different path) |
