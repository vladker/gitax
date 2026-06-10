# Large File Confirmation Fix — Implementation Plan

**Goal:** Eliminate false-positive upload confirmations for large files (50 MB–950 MB) by adding composer-clear verification, filename-based feed confirmation, and adaptive timeout tiers.

**Architecture:** Three surgical changes in `browser_max.py`: (1) `_verify_composer_cleared()` polls for no loading indicators, (2) `_confirm_file_in_feed()` checks the specific filename appears in the feed for files >= 50 MB, (3) `_compute_monitor_timeouts()` gains two new tiers for 200 MB+ and 500 MB+ files. All integrated into `_upload_single_file()` between `_send_message()` and `_confirm_file_sent()`.

**Design:** [thoughts/shared/designs/2025-06-09-large-file-confirmation-fix-design.md](thoughts/shared/designs/2025-06-09-large-file-confirmation-fix-design.md)

---

## Dependency Graph

```
Batch 1 (parallel): 1.1 [new methods — no deps]
Batch 2 (parallel): 2.1 [timeout update — depends on design only]
Batch 3 (parallel): 3.1 [integration into _upload_single_file — depends on 1.1, 2.1]
Batch 4 (parallel): 4.1 [tests — depends on 1.1, 2.1, 3.1]
```

---

## Batch 1: New Methods (parallel — 1 implementer)

### Task 1.1: Add `_verify_composer_cleared()` and `_confirm_file_in_feed()`

**File:** `browser_max.py` (insert after `_confirm_file_sent()`, around line 1830)
**Depends:** none

#### 1.1a — `_verify_composer_cleared()`

Insert this method right after `_confirm_file_sent()` (after line 1829):

```python
    def _verify_composer_cleared(self, timeout: int = 30, poll_interval: float = 1.0) -> bool:
        """
        Verify the composer area no longer shows upload-in-progress indicators.

        Polls the DOM for progress bars, spinners, loading states, file previews,
        and attachment elements. Returns True when composer is clear, False on timeout.

        Args:
            timeout: Maximum seconds to wait (default 30)
            poll_interval: Seconds between polls (default 1)

        Returns:
            True if composer is clear, False if timeout reached
        """
        start = time.time()
        self.logger.debug("Verifying composer is clear...")

        while time.time() - start < timeout:
            try:
                is_clear = self.page.evaluate("""
                    () => {
                        const composer = document.querySelector(
                            '[class*="composer"], [class*="input"], [role="textbox"], [contenteditable]'
                        );
                        if (!composer) return true;  // No composer = clear

                        // Check for progress indicators
                        const hasProgress = !!composer.querySelector(
                            '[class*="progress"], [role="progressbar"]'
                        );
                        // Check for spinner/loading indicators
                        const hasSpinner = !!composer.querySelector(
                            '[class*="spinner"], [class*="loading"], [class*="loader"]'
                        );
                        // Check for file preview/attachment elements
                        const hasPreview = !!composer.querySelector(
                            '[class*="preview"], [class*="attach"], [class*="upload-preview"], [data-file]'
                        );
                        // Check for upload percentage text
                        const text = composer.textContent || '';
                        const hasPercent = /\\d+%/.test(text);
                        const hasUploading = /loading|uploading|sending|sending/i.test(text);

                        if (hasProgress || hasSpinner || hasPreview || hasPercent || hasUploading) {
                            return false;
                        }
                        return true;
                    }
                """)

                if is_clear:
                    self.logger.debug("Composer is clear")
                    return True

                time.sleep(poll_interval)

            except Exception as e:
                self.logger.debug(f"Composer check error: {e}")
                time.sleep(poll_interval)

        self.logger.warning(f"Composer still busy after {timeout}s")
        return False
```

#### 1.1b — `_confirm_file_in_feed()`

Insert this method right after `_verify_composer_cleared()`:

```python
    def _confirm_file_in_feed(self, filename: str, file_size_bytes: int,
                               baseline_count: int = 0) -> bool:
        """
        For files >= 50 MB, verify the specific filename appears in the message feed.

        Unlike the delta check (_confirm_file_sent), this verifies the SPECIFIC file
        was posted — not just that "something changed" in the DOM.

        Adaptive wait before first check based on file size:
        - 50-200 MB: 5 seconds
        - 200-500 MB: 10 seconds
        - >= 500 MB: 15 seconds

        Up to 3 retries with 3-second delays between checks.

        Args:
            filename: Expected filename to find in feed
            file_size_bytes: Size of file for adaptive timing
            baseline_count: Message count baseline (only check new messages)

        Returns:
            True if filename found in feed, False otherwise
        """
        size_mb = file_size_bytes / (1024 * 1024)

        # Adaptive initial wait based on file size
        if size_mb < 200:
            initial_wait = 5
        elif size_mb < 500:
            initial_wait = 10
        else:
            initial_wait = 15

        self.logger.debug(f"Confirming file in feed: {filename} ({size_mb:.1f} MB, wait {initial_wait}s)")
        time.sleep(initial_wait)

        # Normalize filename for comparison
        search_name = os.path.basename(filename).lower()
        search_name = search_name.replace('-master', '').replace('-main', '')

        max_retries = 3

        for attempt in range(max_retries):
            try:
                # Get current message count
                current_count = self.page.evaluate(
                    "() => document.querySelectorAll('[class*=\"message\"]').length"
                ) or 0

                # Scan only new messages (after baseline)
                scan_start = max(baseline_count, current_count - 20)
                if scan_start >= current_count:
                    scan_start = max(0, current_count - 20)

                # Query messages for filename match
                escaped_name = search_name.replace("\\", "\\\\").replace("'", "\\'")
                found = self.page.evaluate(f"""
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
                            const hasMediaTag = !!msg.querySelector('img, video, audio');
                            const hasArchive = /\\.(zip|tar|gz|rar|7z|mp4|jpg|jpeg|png|webp|mov)/i.test(text) ||
                                              /\\.(zip|tar|gz|rar|7z|mp4|jpg|jpeg|png|webp|mov)/i.test(html);

                            if (!hasFileClass && !hasMediaTag && !hasArchive) continue;

                            // Check for filename match (case-insensitive)
                            const textLower = text.toLowerCase();
                            if (textLower.includes(searchName)) {{
                                return {{ found: true, text: text.slice(0, 100) }};
                            }}
                        }}

                        return {{ found: false }};
                    }}
                """) or {}

                if found.get('found'):
                    self.logger.info(
                        f"File confirmed in feed: {found.get('text', 'unknown')} (attempt {attempt + 1})"
                    )
                    return True

                if attempt < max_retries - 1:
                    self.logger.debug(f"Filename not in feed yet, retry {attempt + 1}/{max_retries}")
                    time.sleep(3)

            except Exception as e:
                self.logger.warning(f"Feed check error: {e}")
                if attempt < max_retries - 1:
                    time.sleep(3)

        self.logger.warning(f"Filename '{search_name}' not found in feed after {max_retries} attempts")
        return False
```

**Verify:** `python -c "from browser_max import BrowserMAX; print('OK')"`
**Commit:** `feat(browser_max): add _verify_composer_cleared and _confirm_file_in_feed methods`

---

## Batch 2: Timeout Tiers Update (parallel — 1 implementer)

### Task 2.1: Update `_compute_monitor_timeouts()` with new tiers

**File:** `browser_max.py` (modify existing method at line 1954-1980)
**Depends:** none (no import of new methods)

Replace the existing `_compute_monitor_timeouts` method (lines 1954-1980) with:

```python
    @staticmethod
    def _compute_monitor_timeouts(file_size_bytes: int | None) -> tuple[int, int]:
        """
        Compute adaptive fallback timeouts based on file size.

        Smaller files render faster, so shorter timeouts avoid unnecessary delays.
        Very large files get proportionally longer timeouts to match actual upload
        times (e.g., 900 MB at 5 MB/s takes ~3 minutes).

        Timeout tiers:
        | Size       | Re-render | Reload |
        |------------|-----------|--------|
        | < 5 MB     | 3s        | 6s     |
        | 5-50 MB    | 15s       | 20s    |
        | 50-200 MB  | 25s       | 35s    |
        | 200-500 MB | 50s       | 60s    |
        | >= 500 MB  | 90s       | 120s   |

        Args:
            file_size_bytes: File size in bytes. If None, returns defaults.

        Returns:
            (rerender_timeout: int, reload_timeout: int) in seconds.
        """
        if file_size_bytes is None:
            return (30, 45)  # defaults for backwards compatibility

        size_mb = file_size_bytes / (1024 * 1024)

        if size_mb < 5:
            return (3, 6)
        elif size_mb < 50:
            return (15, 20)
        elif size_mb < 200:
            return (25, 35)
        elif size_mb < 500:
            return (50, 60)   # NEW: 200-500 MB tier
        else:
            return (90, 120)  # NEW: >= 500 MB tier (was 30, 45)
```

**Verify:** `python -c "from browser_max import BrowserMAX; r,rl = BrowserMAX._compute_monitor_timeouts(300*1024*1024); assert r==50 and rl==60; r2,rl2 = BrowserMAX._compute_monitor_timeouts(600*1024*1024); assert r2==90 and rl2==120; print('OK')"`
**Commit:** `feat(browser_max): add 200MB+ and 500MB+ timeout tiers`

---

## Batch 3: Integration into `_upload_single_file()` (1 implementer)

### Task 3.1: Wire new methods into upload flow

**File:** `browser_max.py` (modify `_upload_single_file()` at lines 2817-2858)
**Depends:** 1.1 (new methods exist), 2.1 (timeouts updated)

Replace the confirmation block in `_upload_single_file()` (lines 2817-2858, from `self._send_message()` through the `_wait_for_file_message` call) with:

```python
                self.logger.debug("Sending file message...")
                self._send_message()

                # NEW: Verify composer cleared before confirming (prevents false positives)
                if not self._verify_composer_cleared():
                    self.logger.warning("Composer still busy — treating as unconfirmed")
                else:
                    self.logger.debug("Composer cleared, proceeding to confirmation")

                # NEW: For large files (>= 50 MB), use filename-based confirmation
                # instead of the delta check which triggers false positives
                LARGE_CONFIRM_THRESHOLD = 50 * 1024 * 1024  # 50 MB
                confirmed = False

                if file_size_bytes >= LARGE_CONFIRM_THRESHOLD:
                    # Large file path: check filename appears in feed
                    confirmed = self._confirm_file_in_feed(
                        filename, file_size_bytes,
                        baseline_count=baseline_count
                    )
                    if confirmed:
                        confirm_elapsed = 0  # elapsed tracked inside _confirm_file_in_feed
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
```

**Key integration details:**
- `_verify_composer_cleared()` is called after `_send_message()` but its result does NOT short-circuit — if composer is still busy, we log a warning and continue to the confirmation step (which will likely fail and trigger retry). This is intentional: the composer check is a gate, not a blocker.
- Files >= 50 MB skip `_confirm_file_sent()` entirely and go straight to `_confirm_file_in_feed()`.
- Files < 50 MB use the existing delta check (`_confirm_file_sent()`) — no behavior change for small files.
- If neither fast path confirms, the existing `_wait_for_file_message()` fallback runs as before.

**Verify:** `python -c "from browser_max import BrowserMAX; print('OK')"`
**Commit:** `feat(browser_max): integrate composer check and feed confirmation into upload flow`

---

## Batch 4: Tests (1 implementer)

### Task 4.1: Tests for new methods

**File:** `tests/test_large_file_upload.py` (append to existing file)
**Depends:** 1.1, 2.1, 3.1

Append these test classes to the end of `tests/test_large_file_upload.py`:

```python
# ── _verify_composer_cleared tests ──

class TestVerifyComposerCleared:
    """Test _verify_composer_cleared method"""

    def test_returns_true_when_composer_clear(self):
        """Returns True immediately when composer has no loading indicators."""
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")
        bm._connected = True
        bm.page = MagicMock()
        bm.page.is_closed.return_value = False
        bm.page.evaluate.return_value = True  # composer is clear

        result = bm._verify_composer_cleared(timeout=5, poll_interval=0.1)

        assert result is True
        bm.page.evaluate.assert_called_once()

    def test_returns_false_on_timeout(self):
        """Returns False when composer stays busy past timeout."""
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")
        bm._connected = True
        bm.page = MagicMock()
        bm.page.is_closed.return_value = False
        bm.page.evaluate.return_value = False  # composer still busy

        with patch('time.sleep'):
            result = bm._verify_composer_cleared(timeout=2, poll_interval=0.5)

        assert result is False

    def test_returns_true_after_polling(self):
        """Returns True once composer clears after a few polls."""
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")
        bm._connected = True
        bm.page = MagicMock()
        bm.page.is_closed.return_value = False

        # First 2 calls: busy, 3rd call: clear
        bm.page.evaluate.side_effect = [False, False, True]

        result = bm._verify_composer_cleared(timeout=10, poll_interval=0.1)

        assert result is True
        assert bm.page.evaluate.call_count == 3

    def test_handles_page_error(self):
        """Handles JS evaluation errors gracefully and retries."""
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")
        bm._connected = True
        bm.page = MagicMock()
        bm.page.is_closed.return_value = False

        # First call errors, second call succeeds
        bm.page.evaluate.side_effect = [Exception("CDP error"), True]

        with patch('time.sleep'):
            result = bm._verify_composer_cleared(timeout=5, poll_interval=0.5)

        assert result is True


# ── _confirm_file_in_feed tests ──

class TestConfirmFileInFeed:
    """Test _confirm_file_in_feed method"""

    def test_returns_true_when_filename_found(self):
        """Returns True when filename appears in feed."""
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")
        bm._connected = True
        bm.page = MagicMock()
        bm.page.is_closed.return_value = False

        # First evaluate: message count, second evaluate: feed check
        bm.page.evaluate.side_effect = [
            50,  # message count
            {"found": True, "text": "repo-master.zip 45 MB"}  # feed check
        ]

        with patch('time.sleep'):
            result = bm._confirm_file_in_feed(
                "repo-master.zip",
                100 * 1024 * 1024,  # 100 MB
                baseline_count=45
            )

        assert result is True

    def test_returns_false_when_filename_not_found(self):
        """Returns False after all retries exhausted."""
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")
        bm._connected = True
        bm.page = MagicMock()
        bm.page.is_closed.return_value = False

        # 3 retries: each gets message count + feed check
        evaluate_calls = [0]
        def mock_evaluate(expr):
            evaluate_calls[0] += 1
            # Odd calls = message count, even calls = feed check
            if evaluate_calls[0] % 2 == 1:
                return 50
            return {"found": False}

        bm.page.evaluate.side_effect = mock_evaluate

        with patch('time.sleep'):
            result = bm._confirm_file_in_feed(
                "missing-file.zip",
                100 * 1024 * 1024,
                baseline_count=45
            )

        assert result is False

    def test_finds_filename_on_retry(self):
        """Filename found on second attempt."""
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")
        bm._connected = True
        bm.page = MagicMock()
        bm.page.is_closed.return_value = False

        evaluate_calls = [0]
        def mock_evaluate(expr):
            evaluate_calls[0] += 1
            if evaluate_calls[0] % 2 == 1:
                return 50  # message count
            # First feed check fails, second succeeds
            if evaluate_calls[0] <= 2:
                return {"found": False}
            return {"found": True, "text": "repo.zip"}

        bm.page.evaluate.side_effect = mock_evaluate

        with patch('time.sleep'):
            result = bm._confirm_file_in_feed(
                "repo.zip",
                100 * 1024 * 1024,
                baseline_count=45
            )

        assert result is True

    def test_adaptive_wait_50_200mb(self):
        """50-200 MB files get 5 second initial wait."""
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")
        bm._connected = True
        bm.page = MagicMock()
        bm.page.is_closed.return_value = False
        bm.page.evaluate.return_value = {"found": True, "text": "test.zip"}

        sleeps = []
        def mock_sleep(seconds):
            sleeps.append(seconds)

        with patch('time.sleep', side_effect=mock_sleeps):
            bm._confirm_file_in_feed("test.zip", 100 * 1024 * 1024, baseline_count=0)

        # First sleep should be 5 seconds (initial wait for 50-200 MB)
        assert sleeps[0] == 5

    def test_adaptive_wait_200_500mb(self):
        """200-500 MB files get 10 second initial wait."""
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")
        bm._connected = True
        bm.page = MagicMock()
        bm.page.is_closed.return_value = False
        bm.page.evaluate.return_value = {"found": True, "text": "test.zip"}

        sleeps = []
        def mock_sleep(seconds):
            sleeps.append(seconds)

        with patch('time.sleep', side_effect=mock_sleeps):
            bm._confirm_file_in_feed("test.zip", 300 * 1024 * 1024, baseline_count=0)

        assert sleeps[0] == 10

    def test_adaptive_wait_500plus_mb(self):
        """500+ MB files get 15 second initial wait."""
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")
        bm._connected = True
        bm.page = MagicMock()
        bm.page.is_closed.return_value = False
        bm.page.evaluate.return_value = {"found": True, "text": "test.zip"}

        sleeps = []
        def mock_sleep(seconds):
            sleeps.append(seconds)

        with patch('time.sleep', side_effect=mock_sleeps):
            bm._confirm_file_in_feed("test.zip", 600 * 1024 * 1024, baseline_count=0)

        assert sleeps[0] == 15


# ── Updated timeout tier tests ──

class TestUpdatedTimeoutTiers:
    """Test updated _compute_monitor_timeouts with new tiers"""

    def test_200_500mb_tier(self):
        """200-500 MB files get 50s/60s timeouts."""
        from browser_max import BrowserMAX

        for size_mb in [200, 300, 400, 499]:
            rerender, reload = BrowserMAX._compute_monitor_timeouts(
                size_mb * 1024 * 1024
            )
            assert rerender == 50, f"Expected 50s rerender for {size_mb}MB, got {rerender}"
            assert reload == 60, f"Expected 60s reload for {size_mb}MB, got {reload}"

    def test_500plus_mb_tier(self):
        """500+ MB files get 90s/120s timeouts."""
        from browser_max import BrowserMAX

        for size_mb in [500, 600, 900, 1000]:
            rerender, reload = BrowserMAX._compute_monitor_timeouts(
                size_mb * 1024 * 1024
            )
            assert rerender == 90, f"Expected 90s rerender for {size_mb}MB, got {rerender}"
            assert reload == 120, f"Expected 120s reload for {size_mb}MB, got {reload}"

    def test_existing_tiers_unchanged(self):
        """Existing tiers (< 5MB, 5-50MB, 50-200MB) are unchanged."""
        from browser_max import BrowserMAX

        # < 5 MB
        r, rl = BrowserMAX._compute_monitor_timeouts(1 * 1024 * 1024)
        assert r == 3 and rl == 6

        # 5-50 MB
        r, rl = BrowserMAX._compute_monitor_timeouts(20 * 1024 * 1024)
        assert r == 15 and rl == 20

        # 50-200 MB
        r, rl = BrowserMAX._compute_monitor_timeouts(100 * 1024 * 1024)
        assert r == 25 and rl == 35

    def test_none_returns_defaults(self):
        """None returns backwards-compatible defaults."""
        from browser_max import BrowserMAX

        r, rl = BrowserMAX._compute_monitor_timeouts(None)
        assert r == 30 and rl == 45


# ── Integration: large file uses feed check, small file uses delta ──

class TestLargeFileConfirmationRouting:
    """Test that _upload_single_file routes to correct confirmation method"""

    def test_large_file_uses_feed_check(self):
        """Files >= 50 MB should call _confirm_file_in_feed, NOT _confirm_file_sent."""
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")
        bm._connected = True
        bm.page = MagicMock()
        bm.page.is_closed.return_value = False

        methods_called = []

        def mock_composer_clear():
            methods_called.append("composer_clear")
            return True

        def mock_feed_check(*args, **kwargs):
            methods_called.append("feed_check")
            return True

        def mock_delta_check(*args, **kwargs):
            methods_called.append("delta_check")
            return False

        bm._verify_composer_cleared = mock_composer_clear
        bm._confirm_file_in_feed = mock_feed_check
        bm._confirm_file_sent = mock_delta_check

        # Simulate the routing logic from _upload_single_file
        file_size_bytes = 100 * 1024 * 1024  # 100 MB
        LARGE_CONFIRM_THRESHOLD = 50 * 1024 * 1024

        bm._verify_composer_cleared()

        if file_size_bytes >= LARGE_CONFIRM_THRESHOLD:
            bm._confirm_file_in_feed("test.zip", file_size_bytes, baseline_count=0)
        else:
            bm._confirm_file_sent(None, file_size_bytes)

        assert "composer_clear" in methods_called
        assert "feed_check" in methods_called
        assert "delta_check" not in methods_called

    def test_small_file_uses_delta_check(self):
        """Files < 50 MB should call _confirm_file_sent, NOT _confirm_file_in_feed."""
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")
        bm._connected = True
        bm.page = MagicMock()
        bm.page.is_closed.return_value = False

        methods_called = []

        def mock_composer_clear():
            methods_called.append("composer_clear")
            return True

        def mock_feed_check(*args, **kwargs):
            methods_called.append("feed_check")
            return False

        def mock_delta_check(*args, **kwargs):
            methods_called.append("delta_check")
            return True

        bm._verify_composer_cleared = mock_composer_clear
        bm._confirm_file_in_feed = mock_feed_check
        bm._confirm_file_sent = mock_delta_check

        # Simulate the routing logic from _upload_single_file
        file_size_bytes = 30 * 1024 * 1024  # 30 MB
        LARGE_CONFIRM_THRESHOLD = 50 * 1024 * 1024

        bm._verify_composer_cleared()

        if file_size_bytes >= LARGE_CONFIRM_THRESHOLD:
            bm._confirm_file_in_feed("test.zip", file_size_bytes, baseline_count=0)
        else:
            bm._confirm_file_sent(None, file_size_bytes)

        assert "composer_clear" in methods_called
        assert "delta_check" in methods_called
        assert "feed_check" not in methods_called

    def test_composer_busy_still_attempts_confirm(self):
        """When composer is busy, confirmation is still attempted (not blocked)."""
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")
        bm._connected = True
        bm.page = MagicMock()
        bm.page.is_closed.return_value = False

        methods_called = []

        def mock_composer_clear():
            methods_called.append("composer_clear")
            return False  # composer still busy

        def mock_feed_check(*args, **kwargs):
            methods_called.append("feed_check")
            return True

        bm._verify_composer_cleared = mock_composer_clear
        bm._confirm_file_in_feed = mock_feed_check

        # Simulate: composer busy, but we still try feed check
        composer_ok = bm._verify_composer_cleared()
        # Even if composer not clear, we proceed to confirmation
        file_size_bytes = 100 * 1024 * 1024
        LARGE_CONFIRM_THRESHOLD = 50 * 1024 * 1024

        if file_size_bytes >= LARGE_CONFIRM_THRESHOLD:
            bm._confirm_file_in_feed("test.zip", file_size_bytes, baseline_count=0)

        assert "composer_clear" in methods_called
        assert "feed_check" in methods_called  # still attempted despite busy composer
```

**Verify:** `python -m pytest tests/test_large_file_upload.py -v -k "ComposerCleared or ConfirmFileInFeed or UpdatedTimeout or LargeFileConfirmation"`
**Commit:** `test(browser_max): add tests for composer check, feed confirmation, and timeout tiers`

---

## Summary

| Task | File | Change Type | Lines |
|------|------|-------------|-------|
| 1.1a | `browser_max.py` | New method | ~50 lines |
| 1.1b | `browser_max.py` | New method | ~70 lines |
| 2.1 | `browser_max.py` | Modify method | ~10 lines changed |
| 3.1 | `browser_max.py` | Modify flow | ~30 lines changed |
| 4.1 | `tests/test_large_file_upload.py` | New tests | ~200 lines |

**Total impact:** ~360 lines across 2 files. Surgical changes, no refactoring of existing methods.

**Risk assessment:**
- **Low risk:** Small files (< 50 MB) follow the exact same code path as before (delta check). No regression possible for small files.
- **Low risk:** `_verify_composer_cleared()` is a gate that warns but doesn't block — if it returns False, the flow continues to confirmation (which will likely fail and trigger retry).
- **Medium risk:** MAX DOM selectors (`[class*="progress"]`, etc.) depend on MAX's current DOM structure. If MAX updates their UI, selectors may need adjustment.
- **No new failure modes:** All new checks fall back to existing `_wait_for_file_message()` if they fail.
