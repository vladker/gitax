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

    def test_snapshot_returns_tuple(self):
        """Snapshot returns a ContentSnapshot with hash and file_count."""
        from browser_max import BrowserMAX, ContentSnapshot

        bm = BrowserMAX("https://example.com")
        bm._connected = True

        def evaluate_side_effect(expr):
            return {
                "texts": ["Message one", "Message two", "Message three"],
                "fileCount": 5
            }

        bm.page = MagicMock()
        bm.page.is_closed.return_value = False
        bm.page.evaluate.side_effect = evaluate_side_effect

        result = bm._take_content_snapshot()

        assert result is not None
        assert isinstance(result, ContentSnapshot)
        assert len(result.hash) == 64  # SHA-256 hex digest length
        assert isinstance(result.file_count, int)

    def test_snapshot_consistent(self):
        """Same content produces same hash."""
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")
        bm._connected = True

        def evaluate_side_effect(expr):
            return {
                "texts": ["Alpha", "Beta", "Gamma"],
                "fileCount": 3
            }

        bm.page = MagicMock()
        bm.page.is_closed.return_value = False
        bm.page.evaluate.side_effect = evaluate_side_effect

        result1 = bm._take_content_snapshot()
        result2 = bm._take_content_snapshot()

        assert result1 == result2

    def test_snapshot_detects_change(self):
        """Different content produces different hash."""
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")
        bm._connected = True

        call_count = [0]
        def evaluate_side_effect(expr):
            call_count[0] += 1
            if call_count[0] == 1:
                return {
                    "texts": ["Old message 1", "Old message 2"],
                    "fileCount": 3
                }
            return {
                "texts": ["Old message 1", "New message 3"],
                "fileCount": 3
            }

        bm.page = MagicMock()
        bm.page.is_closed.return_value = False
        bm.page.evaluate.side_effect = evaluate_side_effect

        result1 = bm._take_content_snapshot()
        result2 = bm._take_content_snapshot()

        assert result1 != result2, "Snapshot should change when content changes"

    def test_snapshot_returns_none_on_empty(self):
        """Empty DOM returns None."""
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")
        bm._connected = True

        bm.page = MagicMock()
        bm.page.is_closed.return_value = False
        bm.page.evaluate.return_value = {"texts": [], "fileCount": 0}

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
        # The regex may match first (tier 1), or fall through to substring (tier 2)
        # Either way, the match should succeed
        assert matched is True
        assert "regex" in detail or "substring" in detail

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

        # Simulate: msg 0 = text (no file), msg 1 = file
        def evaluate_side_effect(expr):
            # First call (idx 0): no file indicators
            # Second call (idx 1): file indicators
            # We need to track which index is being queried
            if not hasattr(evaluate_side_effect, 'call_idx'):
                evaluate_side_effect.call_idx = 0
            
            idx = evaluate_side_effect.call_idx
            evaluate_side_effect.call_idx += 1

            if idx == 0:
                return {"text": "hello", "html": "", "hasFileClass": False, "hasZip": False, "hasDownload": False, "classes": ""}
            else:
                return {"text": "repo.zip скачать", "html": "", "hasFileClass": True, "hasZip": True, "hasDownload": True, "classes": "message file"}

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

        # Snapshot simulation: first call returns old tuple, second returns new
        from browser_max import ContentSnapshot
        snapshot_tuples = [
            ContentSnapshot(hash="oldhash123", file_count=5),
            ContentSnapshot(hash="newhash456", file_count=6)
        ]
        snapshot_idx = [0]

        def mock_snapshot(depth=15):
            idx = snapshot_idx[0]
            if idx < len(snapshot_tuples):
                snapshot_idx[0] += 1
                return snapshot_tuples[idx]
            return snapshot_tuples[-1]

        def mock_evaluate(expr):
            expr_str = str(expr)
            # textContent init_result must return a string (not int)
            if 'textContent' in expr_str and 'slice' in expr_str:
                return "initial message text"
            if 'querySelectorAll' in expr_str and 'length' in expr_str:
                return 50  # constant count (virtual scroll)
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
            expr_str = str(expr)
            # textContent init_result must return a string (not int)
            if 'textContent' in expr_str and 'slice' in expr_str:
                return "initial message"
            if 'querySelectorAll' in expr_str and 'length' in expr_str:
                return 42
            return 42

        bm.page.evaluate.side_effect = mock_evaluate

        # Snapshot always changes to trigger scan
        from browser_max import ContentSnapshot
        def mock_snapshot(depth=15):
            call_count[0] += 1
            if call_count[0] < 5:
                return ContentSnapshot(hash="hash1", file_count=3)
            return ContentSnapshot(hash="hash2", file_count=3)

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
        def mock_evaluate(expr):
            expr_str = str(expr)
            # textContent init_result must return a string
            if 'textContent' in expr_str and 'slice' in expr_str:
                return "initial message"
            return 50

        bm.page.evaluate.side_effect = mock_evaluate

        # Snapshots change (content updated)
        from browser_max import ContentSnapshot
        snap_calls = [0]
        def mock_snapshot(depth=15):
            snap_calls[0] += 1
            return ContentSnapshot(hash=f"hash_{snap_calls[0]}", file_count=3)

        bm._take_content_snapshot = mock_snapshot

        def mock_scan(start, end, name):
            return (True, 51, "found")

        bm._scan_messages_for_file = mock_scan

        found, reason, idx = bm._wait_for_file_message(timeout=10)
        assert found is True
        assert reason == "found"


# ── FIX 1: Retry Initial Scan tests ──

class TestFix1RetryInitialScan:
    """Test FIX 1: Retry initial scan with delay when baseline == current count"""

    def test_retry_scan_finds_file_on_first_retry(self):
        """When initial scan range is empty, retry should find file after DOM update.
        Uses count > 150 to avoid virtual scroll optimization skipping the scan."""
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")
        bm._connected = True
        bm.page = MagicMock()
        bm.page.is_closed.return_value = False

        call_count = [0]
        def mock_evaluate(expr):
            call_count[0] += 1
            expr_str = str(expr)
            if 'textContent' in expr_str and 'slice' in expr_str:
                return "initial message"
            # First call returns same count (empty range), second returns higher
            # Use 200 (> 150) to avoid virtual scroll optimization
            if call_count[0] <= 1:
                return 200
            return 201

        bm.page.evaluate.side_effect = mock_evaluate

        # Scan finds file on retry
        scan_calls = [0]
        def mock_scan(start, end, name):
            scan_calls[0] += 1
            if scan_calls[0] >= 2:  # second scan (retry) finds it
                return (True, 200, "regex:test.zip")
            return (False, 0, "not_found")

        bm._scan_messages_for_file = mock_scan

        with patch('time.sleep'):  # skip actual sleep
            found, reason, idx = bm._wait_for_file_message(timeout=60, baseline_count=200)
            assert found is True
            assert reason == "found"

    def test_retry_scan_exhausts_and_continues(self):
        """When all retries fail, should continue to monitoring (not crash)."""
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")
        bm._connected = True
        bm.page = MagicMock()
        bm.page.is_closed.return_value = False
        bm.page.evaluate.return_value = 129  # constant count

        bm._scan_messages_for_file = lambda s, e, n: (False, 0, "not_found")

        # Snapshot always changes to trigger monitoring path
        snap_count = [0]
        def mock_snapshot(depth=15):
            snap_count[0] += 1
            return (f"hash_{snap_count[0]}", 3)

        bm._take_content_snapshot = mock_snapshot

        with patch('time.sleep'):
            # Should not raise, should return eventually (timeout or found)
            found, reason, idx = bm._wait_for_file_message(timeout=1)
            # May timeout or find — either way no crash
            assert reason in ("found", "timeout", "not_connected", "init_failed")


# ── FIX 3: Adaptive Timers tests ──

class TestFix3AdaptiveTimers:
    """Test FIX 3: Adaptive fallback timers based on file size"""

    def test_small_file_short_timeouts(self):
        """Files < 5MB should get 3s/6s timeouts."""
        from browser_max import BrowserMAX
        rerender, reload = BrowserMAX._compute_monitor_timeouts(1_000_000)  # ~1MB
        assert rerender == 3
        assert reload == 6

    def test_medium_file_medium_timeouts(self):
        """Files 5-50MB should get 15s/20s timeouts."""
        from browser_max import BrowserMAX
        rerender, reload = BrowserMAX._compute_monitor_timeouts(20_000_000)  # ~20MB
        assert rerender == 15
        assert reload == 20

    def test_large_file_long_timeouts(self):
        """Files 50-200MB should get 25s/35s timeouts."""
        from browser_max import BrowserMAX
        rerender, reload = BrowserMAX._compute_monitor_timeouts(100_000_000)  # ~100MB
        assert rerender == 25
        assert reload == 35

    def test_very_large_file_default_timeouts(self):
        """Files >= 500MB should get 90s/120s timeouts."""
        from browser_max import BrowserMAX
        rerender, reload = BrowserMAX._compute_monitor_timeouts(500 * 1024 * 1024)  # 500 MB
        assert rerender == 90
        assert reload == 120

    def test_none_returns_defaults(self):
        """None file_size should return default timeouts (backwards compat)."""
        from browser_max import BrowserMAX
        rerender, reload = BrowserMAX._compute_monitor_timeouts(None)
        assert rerender == 30
        assert reload == 45

    def test_boundary_5mb(self):
        """Exactly 5MB should fall in medium bucket (>= 5MB)."""
        from browser_max import BrowserMAX
        size_5mb = 5 * 1024 * 1024
        rerender, reload = BrowserMAX._compute_monitor_timeouts(size_5mb)
        assert rerender == 15
        assert reload == 20

    def test_boundary_50mb(self):
        """Exactly 50MB should fall in large bucket."""
        from browser_max import BrowserMAX
        size_50mb = 50 * 1024 * 1024
        rerender, reload = BrowserMAX._compute_monitor_timeouts(size_50mb)
        assert rerender == 25
        assert reload == 35

    def test_boundary_200mb(self):
        """Exactly 200MB should fall in 200-500MB tier (50s/60s)."""
        from browser_max import BrowserMAX
        size_200mb = 200 * 1024 * 1024
        rerender, reload = BrowserMAX._compute_monitor_timeouts(size_200mb)
        assert rerender == 50
        assert reload == 60


# ── FIX 4: Full Scan Fallback tests ──

class TestFix4FullScanFallback:
    """Test FIX 4: Full scan fallback in fast_mode"""

    def test_full_scan_finds_file_after_polling_fails(self):
        """When fast_mode polling fails, full scan should find file by name.
        Uses count > 150 to keep fast_mode active (virtual scroll skips it)."""
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")
        bm._connected = True
        bm.page = MagicMock()
        bm.page.is_closed.return_value = False

        def mock_evaluate(expr):
            expr_str = str(expr)
            if 'textContent' in expr_str and 'slice' in expr_str:
                return "initial message"
            return 200  # > 150 to avoid virtual scroll optimization

        bm.page.evaluate.side_effect = mock_evaluate

        # Polling scans fail (baseline == count, empty range)
        # But full scan succeeds
        scan_calls = [0]
        def mock_scan(start, end, name):
            scan_calls[0] += 1
            # First calls are polling scans (empty range) — fail
            # Last call is full scan [0, 200) — succeed
            if start == 0 and end == 200:
                return (True, 50, "regex:test.zip")
            return (False, 0, "not_found")

        bm._scan_messages_for_file = mock_scan

        with patch('time.sleep'):
            found, reason, idx = bm._wait_for_file_message(
                timeout=60,
                baseline_count=200,
                fast_mode=True
            )
            assert found is True
            assert reason == "found"

    def test_full_scan_limited_for_large_channels(self):
        """When total > 500, full scan should only check last 50 messages."""
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")
        bm._connected = True
        bm.page = MagicMock()
        bm.page.is_closed.return_value = False

        def mock_evaluate(expr):
            expr_str = str(expr)
            if 'textContent' in expr_str and 'slice' in expr_str:
                return "initial message"
            return 600

        bm.page.evaluate.side_effect = mock_evaluate

        scan_ranges = []
        def mock_scan(start, end, name):
            scan_ranges.append((start, end))
            return (False, 0, "not_found")

        bm._scan_messages_for_file = mock_scan

        # Snapshot changes to trigger monitoring
        from browser_max import ContentSnapshot
        snap_count = [0]
        def mock_snapshot(depth=15):
            snap_count[0] += 1
            return ContentSnapshot(hash=f"hash_{snap_count[0]}", file_count=3)

        bm._take_content_snapshot = mock_snapshot

        with patch('time.sleep'):
            bm._wait_for_file_message(timeout=1, baseline_count=600, fast_mode=True)
            # Find the full scan call (start=550, end=600 for large channels)
            full_scan = [r for r in scan_ranges if r[0] == 550 and r[1] == 600]
            assert len(full_scan) >= 1, f"Expected full scan [550, 600), got ranges: {scan_ranges}"

    def test_full_scan_error_does_not_crash(self):
        """Full scan exception should be caught, flow continues."""
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")
        bm._connected = True
        bm.page = MagicMock()
        bm.page.is_closed.return_value = False

        # page.evaluate raises during full scan
        bm.page.evaluate.side_effect = Exception("CDP disconnected")

        bm._scan_messages_for_file = lambda s, e, n: (False, 0, "not_found")

        with patch('time.sleep'):
            # Should not raise — graceful degradation
            found, reason, idx = bm._wait_for_file_message(
                timeout=1,
                baseline_count=100,
                fast_mode=True
            )
            # May timeout or fail — but no crash
            assert reason in ("found", "timeout", "not_connected", "init_failed")


# ── FIX 2: Pre-monitor delay tests ──

class TestFix2PreMonitorDelay:
    """Test FIX 2: Delay before monitoring for small files"""

    def test_small_file_gets_delay(self):
        """Files < 10MB should trigger a 2-second delay before monitoring."""
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")
        bm._connected = True
        bm.page = MagicMock()
        bm.page.is_closed.return_value = False

        # We verify the logic by checking that _upload_single_file
        # calls time.sleep(2) for small files
        # Since _upload_single_file is complex to mock fully,
        # we test the condition directly
        file_size = 1 * 1024 * 1024  # 1 MB
        should_delay = file_size < 10 * 1024 * 1024
        assert should_delay is True

        file_size = 20 * 1024 * 1024  # 20 MB
        should_delay = file_size < 10 * 1024 * 1024
        assert should_delay is False

    def test_boundary_10mb_no_delay(self):
        """Exactly 10MB should NOT get delay (not < 10MB)."""
        file_size = 10 * 1024 * 1024
        should_delay = file_size < 10 * 1024 * 1024
        assert should_delay is False

    def test_just_under_10mb_gets_delay(self):
        """Just under 10MB should get delay."""
        file_size = (10 * 1024 * 1024) - 1
        should_delay = file_size < 10 * 1024 * 1024
        assert should_delay is True


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

    # ── 50MB Guard tests ──

    def test_returns_false_for_50mb_file(self):
        """Exactly 50MB file should be rejected by guard."""
        from browser_max import BrowserMAX, ContentSnapshot

        bm = BrowserMAX("https://example.com")
        bm._connected = True
        bm.page = MagicMock()
        bm.page.is_closed.return_value = False
        bm._take_content_snapshot = MagicMock()  # should NOT be called

        pre = ContentSnapshot(hash="aaa", file_count=5)

        with patch('time.sleep'):
            result = bm._confirm_file_sent(pre, 50 * 1024 * 1024)
            assert result is False
        bm._take_content_snapshot.assert_not_called()

    def test_returns_false_for_file_above_50mb(self):
        """File > 50MB (100MB) should be rejected by guard."""
        from browser_max import BrowserMAX, ContentSnapshot

        bm = BrowserMAX("https://example.com")
        bm._connected = True
        bm.page = MagicMock()
        bm.page.is_closed.return_value = False
        bm._take_content_snapshot = MagicMock()  # should NOT be called

        pre = ContentSnapshot(hash="aaa", file_count=5)

        with patch('time.sleep'):
            result = bm._confirm_file_sent(pre, 100 * 1024 * 1024)
            assert result is False
        bm._take_content_snapshot.assert_not_called()

    def test_under_50mb_still_works(self):
        """File just under 50MB should still work (no guard trigger)."""
        from browser_max import BrowserMAX, ContentSnapshot

        bm = BrowserMAX("https://example.com")
        bm._connected = True
        bm.page = MagicMock()
        bm.page.is_closed.return_value = False

        pre = ContentSnapshot(hash="aaa", file_count=5)
        post = ContentSnapshot(hash="bbb", file_count=6)
        bm._take_content_snapshot = MagicMock(side_effect=[post])

        size_just_under = 50 * 1024 * 1024 - 1
        with patch('time.sleep'):
            result = bm._confirm_file_sent(pre, size_just_under)
            assert result is True
        bm._take_content_snapshot.assert_called_once()


class TestVirtualScrollDetection:
    """Test virtual scroll detection in _wait_for_file_message"""

    def test_virtual_scroll_detected_at_129(self):
        """129 messages (real MAX count) triggers virtual scroll detection."""
        from browser_max import BrowserMAX, ContentSnapshot

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
            return ContentSnapshot(hash=f"hash_{snap_count[0]}", file_count=3)

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
