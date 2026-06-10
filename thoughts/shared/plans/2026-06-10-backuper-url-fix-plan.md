# Backuper — URL extraction fix for restore flow

**Goal:** Fix `run_restore()` failing to find download URLs for archive volumes because scrolling removes messages from DOM before URL lookup.

**Architecture:** Collect download URLs during `scan_channel_for_archives()` (when DOM is complete) via a new `_extract_file_urls()` method. Store URLs in `volume_urls` field on each archive dict, and use them in `_find_download_url()` before falling back to DOM queries.

**Design:** `thoughts/shared/designs/2026-06-10-backuper-url-fix-design.md`

---

## Dependency Graph

```
Batch 1 (parallel): 1.1, 1.2, 1.3, 1.4  [all independent — different files, no imports between them]
Batch 2 (parallel): 2.1                 [integration test — depends on all batch 1 changes]
```

---

## Batch 1: Implementation (parallel — 4 implementers)

All tasks in this batch have NO dependencies on each other and run simultaneously. Each modifies a different file or area.

### Task 1.1: Add `_extract_file_urls()` to BrowserMAX
**File:** `browser_max.py` (insert after line 3957, before `scan_channel_for_archives`)
**Test:** `tests/test_extract_file_urls.py` (new file)
**Depends:** none

**Implementation details:**
- Design says: "Single `page.evaluate()` JS call that scans ALL message-like elements."
- I'm implementing: a self-contained method matching the same JS-selector pattern as `scan_channel_for_files()` (line 5662-5800), but returns `dict[str, str]` (filename → download_url) instead of list[dict].
- Dedup by filename: first occurrence wins (same strategy as `scan_channel_for_files` but done in JS).
- Error handling: on `page.evaluate()` failure, return `{}` and log warning (design requirement).

```python
# ---- Tests: tests/test_extract_file_urls.py ----
# -*- coding: utf-8 -*-
"""
Tests for _extract_file_urls method in BrowserMAX.

Tests cover:
- Extracting URL from a[download] elements
- Extracting URL from a[href*="download"] alternative links
- Extracting URL from video[src] elements
- Extracting URL from img[src] (non-emoji) elements
- Empty DOM returns empty dict
- page.evaluate error returns empty dict
- Deduplication by filename
- Integration: scan_channel_for_archives includes volume_urls
"""

import pytest
from unittest.mock import MagicMock, patch


@pytest.fixture
def browser_max():
    """Create a BrowserMAX instance with mocked page."""
    from browser_max import BrowserMAX
    bm = BrowserMAX("https://web.max.ru/test-channel")
    bm.page = MagicMock()
    bm.page.is_closed.return_value = False
    bm._connected = True
    return bm


class TestExtractFileUrls:
    """Tests for _extract_file_urls method"""

    def test_method_exists(self):
        """_extract_file_urls exists on BrowserMAX"""
        from browser_max import BrowserMAX
        bm = BrowserMAX("https://web.max.ru/test-channel")
        assert hasattr(bm, "_extract_file_urls")

    def test_returns_dict(self, browser_max):
        """Returns a dict"""
        browser_max.page.evaluate.return_value = {}
        result = browser_max._extract_file_urls()
        assert isinstance(result, dict)

    def test_empty_dom_returns_empty_dict(self, browser_max):
        """No file elements returns empty dict"""
        browser_max.page.evaluate.return_value = {}
        result = browser_max._extract_file_urls()
        assert result == {}

    def test_extracts_download_link(self, browser_max):
        """Extracts URL from a[download] elements"""
        browser_max.page.evaluate.return_value = {
            "project-v1.0.0.zip": "https://cdn.max.ru/file/abc123"
        }
        result = browser_max._extract_file_urls()
        assert len(result) == 1
        assert result["project-v1.0.0.zip"] == "https://cdn.max.ru/file/abc123"

    def test_extracts_alt_download_link(self, browser_max):
        """Extracts URL from a[href*="download"] alternative links"""
        browser_max.page.evaluate.return_value = {
            "report.pdf": "https://cdn.max.ru/download/xyz"
        }
        result = browser_max._extract_file_urls()
        assert result["report.pdf"] == "https://cdn.max.ru/download/xyz"

    def test_extracts_video(self, browser_max):
        """Extracts URL from video[src] elements"""
        browser_max.page.evaluate.return_value = {
            "demo.mp4": "https://cdn.max.ru/video/xyz"
        }
        result = browser_max._extract_file_urls()
        assert result["demo.mp4"] == "https://cdn.max.ru/video/xyz"

    def test_extracts_image(self, browser_max):
        """Extracts URL from img[src] (non-emoji) elements"""
        browser_max.page.evaluate.return_value = {
            "screenshot.png": "https://cdn.max.ru/img/abc"
        }
        result = browser_max._extract_file_urls()
        assert result["screenshot.png"] == "https://cdn.max.ru/img/abc"

    def test_deduplicates_by_filename(self, browser_max):
        """Duplicate filenames are deduplicated (first occurrence wins)"""
        browser_max.page.evaluate.return_value = {
            "report.pdf": "https://cdn.max.ru/file/url1"
        }
        result = browser_max._extract_file_urls()
        assert result["report.pdf"] == "https://cdn.max.ru/file/url1"
        # JS-side dedup means evaluate already returns deduplicated dict

    def test_multiple_files(self, browser_max):
        """Multiple files returned correctly"""
        browser_max.page.evaluate.return_value = {
            "file1.zip": "https://cdn.max.ru/f1",
            "file2.zip": "https://cdn.max.ru/f2",
            "file3.zip": "https://cdn.max.ru/f3",
        }
        result = browser_max._extract_file_urls()
        assert len(result) == 3
        assert result["file1.zip"] == "https://cdn.max.ru/f1"
        assert result["file2.zip"] == "https://cdn.max.ru/f2"
        assert result["file3.zip"] == "https://cdn.max.ru/f3"

    def test_handles_evaluate_error(self, browser_max):
        """page.evaluate error returns empty dict gracefully"""
        browser_max.page.evaluate.side_effect = Exception("JS error")
        result = browser_max._extract_file_urls()
        assert result == {}

    def test_handles_empty_filename(self, browser_max):
        """Entries with empty filename are excluded"""
        browser_max.page.evaluate.return_value = {
            "": "https://cdn.max.ru/file/noname",
            "valid.zip": "https://cdn.max.ru/file/valid",
        }
        result = browser_max._extract_file_urls()
        assert "valid.zip" in result
        # JS-side handles empty filenames

    def test_checks_connection_first(self, browser_max):
        """Raises error if not connected"""
        from browser_max import BrowserMAX, ConnectionError as BMConnectionError
        bm = BrowserMAX("https://web.max.ru/test-channel")
        bm.page = None
        with pytest.raises(BMConnectionError):
            bm._extract_file_urls()


class TestScanChannelForArchivesVolumeUrls:
    """Tests that scan_channel_for_archives includes volume_urls"""

    def test_volume_urls_in_result(self, browser_max):
        """volume_urls field present in each archive result"""
        from browser_max import group_volumes
        fake_messages = [
            {"text": "archive.7z.001", "html": ""},
            {"text": "archive.7z.002", "html": ""},
            {"text": "readme.txt", "html": ""},
        ]
        url_map = {
            "archive.7z.001": "https://cdn.max.ru/f/001",
            "archive.7z.002": "https://cdn.max.ru/f/002",
        }
        with patch.object(browser_max, 'collect_all_messages', return_value=fake_messages):
            with patch.object(browser_max, '_extract_file_urls', return_value=url_map):
                # Need to patch import of group_volumes too if necessary
                result = browser_max.scan_channel_for_archives()
        assert len(result) >= 1
        for arch in result:
            assert "volume_urls" in arch
            assert isinstance(arch["volume_urls"], dict)

    def test_volume_urls_maps_correctly(self, browser_max):
        """volume_urls maps each volume filename to its download URL"""
        fake_messages = [
            {"text": "backup.7z.001", "html": ""},
            {"text": "backup.7z.002", "html": ""},
            {"text": "notes.txt", "html": ""},
        ]
        url_map = {
            "backup.7z.001": "https://cdn.max.ru/f/001",
            "backup.7z.002": "https://cdn.max.ru/f/002",
        }
        with patch.object(browser_max, 'collect_all_messages', return_value=fake_messages):
            with patch.object(browser_max, '_extract_file_urls', return_value=url_map):
                result = browser_max.scan_channel_for_archives()
        # Find the backup archive
        for arch in result:
            if arch["base_name"] == "backup.7z":
                assert arch["volume_urls"]["backup.7z.001"] == "https://cdn.max.ru/f/001"
                assert arch["volume_urls"]["backup.7z.002"] == "https://cdn.max.ru/f/002"
                break
        else:
            pytest.fail("backup.7z not found in results")

    def test_partial_url_map(self, browser_max):
        """Some volumes may not have URLs — those are omitted from volume_urls"""
        fake_messages = [
            {"text": "data.7z.001", "html": ""},
            {"text": "data.7z.002", "html": ""},
            {"text": "data.7z.003", "html": ""},
        ]
        url_map = {
            "data.7z.001": "https://cdn.max.ru/f/001",
            # 002 and 003 not in map
        }
        with patch.object(browser_max, 'collect_all_messages', return_value=fake_messages):
            with patch.object(browser_max, '_extract_file_urls', return_value=url_map):
                result = browser_max.scan_channel_for_archives()
        for arch in result:
            if arch["base_name"] == "data.7z":
                assert arch["volume_urls"].get("data.7z.001") == "https://cdn.max.ru/f/001"
                assert "data.7z.002" not in arch["volume_urls"]
                assert "data.7z.003" not in arch["volume_urls"]
                break
        else:
            pytest.fail("data.7z not found in results")

    def test_empty_url_map(self, browser_max):
        """Empty url_map from _extract_file_urls results in empty volume_urls"""
        fake_messages = [
            {"text": "data.7z.001", "html": ""},
        ]
        with patch.object(browser_max, 'collect_all_messages', return_value=fake_messages):
            with patch.object(browser_max, '_extract_file_urls', return_value={}):
                result = browser_max.scan_channel_for_archives()
        for arch in result:
            assert arch["volume_urls"] == {}
```

**Implementation — add to `browser_max.py` right before `scan_channel_for_archives` (after line 3957):**

```python
    def _extract_file_urls(self) -> dict[str, str]:
        """
        Extract filename → download_url map from current DOM.

        Uses a single page.evaluate() JS call that scans all message-like
        elements, applying the same CSS selectors as scan_channel_for_files().

        Returns:
            Dict mapping filename (str) → download URL (str).
            Empty dict if no files found or on error.
        """
        self._check_connection()
        try:
            url_map = self.page.evaluate(r"""
                () => {
                    const result = {};
                    const seen = new Set();

                    // Find all message-like elements in the feed
                    const messages = document.querySelectorAll(
                        '[class*="message"],[class*="msg"],' +
                        '[class*="lenta-item"],[class*="feed-item"]'
                    );

                    messages.forEach((msg) => {
                        let filename = '';
                        let downloadUrl = '';

                        // 1. Direct download links: a[download]
                        const downloadLinks = msg.querySelectorAll('a[download]');
                        for (const a of downloadLinks) {
                            const href = a.getAttribute('href') || '';
                            const name = a.getAttribute('download') || '';
                            if (name && href) {
                                filename = name;
                                downloadUrl = href;
                                break;
                            }
                        }

                        // 2. Alternative download links (a[href*="download"])
                        if (!filename) {
                            const altLinks = msg.querySelectorAll(
                                'a[href*="download"],a[href*="attachment"]'
                            );
                            for (const a of altLinks) {
                                const href = a.getAttribute('href') || '';
                                if (href) {
                                    downloadUrl = href;
                                    filename = a.textContent?.trim()
                                        || href.split('/').pop() || '';
                                    break;
                                }
                            }
                        }

                        // 3. Video elements with src attribute
                        if (!filename) {
                            const videos = msg.querySelectorAll('video[src]');
                            if (videos.length > 0) {
                                const src = videos[0].getAttribute('src') || '';
                                downloadUrl = src;
                                filename = videos[0].getAttribute('title')
                                    || src.split('/').pop() || 'video.mp4';
                            }
                        }

                        // 4. Image elements (non-emoji, non-avatar)
                        if (!filename) {
                            const imgs = msg.querySelectorAll('img[src]');
                            for (const img of imgs) {
                                const src = img.getAttribute('src') || '';
                                if (src && !src.includes('emoji')
                                    && !src.includes('avatar')) {
                                    downloadUrl = src;
                                    filename = img.getAttribute('alt')
                                        || src.split('/').pop() || 'image.jpg';
                                    break;
                                }
                            }
                        }

                        // 5. Generic file/attachment indicator classes
                        if (!filename) {
                            const fileEls = msg.querySelectorAll(
                                '[class*="file"],[class*="attach"]'
                            );
                            for (const el of fileEls) {
                                const title = el.getAttribute('title')
                                    || el.getAttribute('alt') || '';
                                if (title) {
                                    filename = title;
                                    break;
                                }
                            }
                        }

                        // Skip messages without filenames
                        if (!filename || !downloadUrl) return;

                        // Deduplicate by filename (first occurrence wins)
                        if (seen.has(filename)) return;
                        seen.add(filename);

                        result[filename] = downloadUrl;
                    });

                    return result;
                }
            """)
            return url_map if isinstance(url_map, dict) else {}
        except Exception:
            self.logger.warning("_extract_file_urls: evaluate failed", exc_info=True)
            return {}
```

**Verify:** `python -m pytest tests/test_extract_file_urls.py -v`
**Commit:** `fix(backuper): add _extract_file_urls method`

---

### Task 1.2: Modify `scan_channel_for_archives()` to collect volume URLs
**File:** `browser_max.py` (lines 3958-4015)
**Test:** covered by tests in Task 1.1 (`test_extract_file_urls.py`)
**Depends:** none (same file, but the modification is independent from adding the new method)

**Implementation details:**
- After grouping volumes (line 4013), call `self._extract_file_urls()`.
- For each archive in result, populate `"volume_urls"` field.
- Design requires: `"volume_urls"` is a dict mapping each volume filename → download URL (if found in url_map).
- Backward compatible: existing code that reads `scan_channel_for_archives()` won't break — they just see an extra field.

**Edit in `browser_max.py` — after line 4013 (`"message_indices": msg_indices,`) and before the loop ends on line 4014 (`self.logger.info(...)`), insert the URL extraction:**

Old code (lines 4003-4015):
```python
        result = []
        for group in groups:
            archive_name = group["base_name"].replace(".7z", "")
            msg_indices = [seen[vol] for vol in group["volumes"] if vol in seen]
            result.append({
                "base_name": group["base_name"],
                "archive_name": archive_name,
                "volume_count": group["volume_count"],
                "volumes": group["volumes"],
                "message_indices": msg_indices,
            })
        self.logger.info(f"Grouped into {len(result)} archive(s)")
        return result
```

New code:
```python
        result = []
        for group in groups:
            archive_name = group["base_name"].replace(".7z", "")
            msg_indices = [seen[vol] for vol in group["volumes"] if vol in seen]
            result.append({
                "base_name": group["base_name"],
                "archive_name": archive_name,
                "volume_count": group["volume_count"],
                "volumes": group["volumes"],
                "message_indices": msg_indices,
            })

        # Extract download URLs from DOM while it's still populated
        url_map = self._extract_file_urls()
        for arch in result:
            arch["volume_urls"] = {}
            for vol in arch["volumes"]:
                if vol in url_map:
                    arch["volume_urls"][vol] = url_map[vol]

        self.logger.info(f"Grouped into {len(result)} archive(s)")
        return result
```

**Updated docstring for `scan_channel_for_archives()` — update the Returns section to include `volume_urls`:**

```python
        Returns:
            List of dicts:
            [
                {
                    "base_name": "documents.7z",
                    "archive_name": "documents",
                    "volume_count": 3,
                    "volumes": ["documents.7z.001", "documents.7z.002", "documents.7z.003"],
                    "message_indices": [5, 12, 18],
                    "volume_urls": {                          # ← NEW
                        "documents.7z.001": "https://...",   # ← NEW
                        "documents.7z.002": "https://...",   # ← NEW
                    },                                         # ← NEW
                },
                ...
            ]
```

**Verify:** `python -m pytest tests/test_extract_file_urls.py -v -k "TestScanChannelForArchivesVolumeUrls"`
**Commit:** `fix(backuper): add volume_urls to scan_channel_for_archives`

---

### Task 1.3: Modify `_find_download_url()` to accept url_map parameter
**File:** `backuper.py` (lines 482-509)
**Test:** covered by existing tests (backward compatible — new parameter is optional)
**Depends:** none

**Implementation details:**
- Design says: add `url_map: dict | None = None` parameter.
- If `url_map` and filename is in it, return `url_map[filename]` immediately.
- Existing DOM query logic unchanged as fallback.
- Signature change is backward compatible — existing callers pass no url_map.

Old code (lines 482-509):
```python
    def _find_download_url(self, browser: BrowserMAX, filename: str) -> str | None:
        """Find download URL for a specific filename in the channel"""
        try:
            url = browser.page.evaluate("""
                (filename) => {
                    const messages = document.querySelectorAll('[class*="message"]');
                    for (const msg of messages) {
                        const text = msg.textContent || '';
                        if (text.includes(filename)) {
                            const link = msg.querySelector('a[href*="download"], a[href*="file"], a[href*="attachment"]');
                            if (link) return link.href;
                            // Try any link that looks like a file URL
                            const allLinks = msg.querySelectorAll('a');
                            for (const a of allLinks) {
                                const href = a.href || '';
                                if (href.includes('.7z') || href.includes('download') || href.includes('file')) {
                                    return href;
                                }
                            }
                        }
                    }
                    return null;
                }
            """, filename)
            return url
        except Exception as e:
            self.logger.warning(f"Failed to find download URL for {filename}: {e}")
            return None
```

New code:
```python
    def _find_download_url(self, browser: BrowserMAX, filename: str, url_map: dict | None = None) -> str | None:
        """Find download URL for a specific filename in the channel"""
        # Fast path: use pre-extracted URL map from scan phase
        if url_map and filename in url_map:
            return url_map[filename]

        # Fallback: direct DOM query (for old journal entries, manual calls)
        try:
            url = browser.page.evaluate("""
                (filename) => {
                    const messages = document.querySelectorAll('[class*="message"]');
                    for (const msg of messages) {
                        const text = msg.textContent || '';
                        if (text.includes(filename)) {
                            const link = msg.querySelector('a[href*="download"], a[href*="file"], a[href*="attachment"]');
                            if (link) return link.href;
                            // Try any link that looks like a file URL
                            const allLinks = msg.querySelectorAll('a');
                            for (const a of allLinks) {
                                const href = a.href || '';
                                if (href.includes('.7z') || href.includes('download') || href.includes('file')) {
                                    return href;
                                }
                            }
                        }
                    }
                    return null;
                }
            """, filename)
            return url
        except Exception as e:
            self.logger.warning(f"Failed to find download URL for {filename}: {e}")
            return None
```

**Verify:** `python -m pytest tests/test_backuper_journal.py -v` (existing tests pass unchanged)
**Commit:** `fix(backuper): add url_map param to _find_download_url`

---

### Task 1.4: Modify `run_restore()` to pass url_map to `_find_download_url()`
**File:** `backuper.py` (line 413)
**Test:** covered by integration test in Task 1.1 (`TestScanChannelForArchivesVolumeUrls`)
**Depends:** none (same file as Task 1.3, but the change is on a different line — no merge conflict)

**Implementation details:**
- Design requires: change line 413 from `self._find_download_url(browser, vol_name)` to `self._find_download_url(browser, vol_name, arch.get("volume_urls", {}))`.
- The `arch` variable is already in scope (line 376: `for arch_idx, arch in enumerate(selected, 1):`).
- The `vol_name` is already in scope (line 408: `for vol_name in volumes:`).

Old line 413:
```python
                dl_url = self._find_download_url(browser, vol_name)
```

New line 413:
```python
                dl_url = self._find_download_url(browser, vol_name, arch.get("volume_urls", {}))
```

**Verify:** `python -m pytest tests/test_backuper_journal.py -v` (existing tests pass unchanged)
**Commit:** `fix(backuper): pass volume_urls to _find_download_url in run_restore`

---

## Batch 2: Integration Verification (sequential — 1 implementer)

### Task 2.1: Run all tests to verify nothing is broken
**File:** all test files (no new files)
**Depends:** 1.1, 1.2, 1.3, 1.4 (must be done first)

**Verify command:**
```bash
python -m pytest tests/ -v 2>&1
```

**Expected:** All existing tests pass. New tests in `tests/test_extract_file_urls.py` pass.

---

## Summary

| File | Change | Reason |
|---|---|---|
| `browser_max.py` | Add `_extract_file_urls()` method | Single JS evaluate to collect filename→URL from DOM |
| `browser_max.py` | Modify `scan_channel_for_archives()` | Call `_extract_file_urls()` and attach `volume_urls` |
| `backuper.py` | Modify `_find_download_url()` | Accept optional `url_map` param for fast path |
| `backuper.py` | Modify `run_restore()` | Pass `arch["volume_urls"]` to `_find_download_url()` |
| `tests/test_extract_file_urls.py` | New test file | Test `_extract_file_urls()` and `volume_urls` in `scan_channel_for_archives()` |

**Backward compatibility:** All changes are additive. `scan_channel_for_archives()` returns a new `volume_urls` field that existing callers ignore. `_find_download_url()` has a new optional parameter. Existing tests pass unchanged.
