# Channel File Downloader — Implementation Plan

**Goal:** Add menu option [7] to scan a MAX channel for all file messages and download them to a local folder.

**Architecture:** New `channel_downloader.py` module (following `media_archiver.py` pattern) with `DownloadJournal` + `ChannelDownloader`. One new method `scan_channel_for_files()` in `browser_max.py`. Menu integration in `github_archiver.py`.

**Design:** `thoughts/shared/designs/2026-06-09-channel-downloader-design.md`

---

## Dependency Graph

```
Batch 1 (parallel): 1.1, 1.2, 1.3  [all independent — run simultaneously]
Batch 2 (sequential): 2.1          [depends on 1.3 existing]
```

---

## Batch 1: Foundation (parallel — 3 implementers)

All tasks in this batch have NO dependencies and run simultaneously.

### Task 1.1: config.yaml — Add channel_downloader settings
**File:** `config.yaml`
**Test:** none (config file)
**Depends:** none

Add the following block after the `media_archiver:` section (after line 32):

```yaml
channel_downloader:        # Channel file downloader settings [NEW]
  output_dir: "./downloads"  # Папка для скачанных файлов
  retries: 3                 # Повторы при ошибке скачивания
  retry_delay: 5             # Пауза между повторами (сек)
```

**Edit instructions:** Insert 5 lines after line 32 (`  retry_delay: 10       # Пауза между повторами (сек)`) in `config.yaml`.

**Verify:** `python -c "import yaml; c=yaml.safe_load(open('config.yaml')); print(c['channel_downloader'])"`
**Commit:** `feat(config): add channel_downloader section`

---

### Task 1.2: browser_max.py — Add scan_channel_for_files() method
**File:** `browser_max.py` (insert before `close()` method at line 5473)
**Test:** `tests/test_channel_scan.py`
**Depends:** none

**Pattern to follow:** The method follows the same scroll-then-query pattern as `collect_all_messages()` (line 3608) but uses a single `page.evaluate()` call to extract file metadata from all loaded messages after scrolling completes.

**Test file: `tests/test_channel_scan.py`**

```python
# -*- coding: utf-8 -*-
"""
Tests for scan_channel_for_files method in BrowserMAX.

Tests cover:
- Extracting a[download] links with href and download attributes
- Extracting video[src] elements
- Extracting image[src] elements
- Deduplication by filename
- Empty channel (no files)
- Handling page.evaluate errors gracefully
- Parsing file sizes from [class*="size"] elements
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


class TestScanChannelForFiles:
    """Tests for scan_channel_for_files method"""

    def test_method_exists(self):
        """scan_channel_for_files exists on BrowserMAX"""
        from browser_max import BrowserMAX
        bm = BrowserMAX("https://web.max.ru/test-channel")
        assert hasattr(bm, "scan_channel_for_files")

    def test_returns_list(self, browser_max):
        """Returns a list"""
        browser_max.page.evaluate.return_value = []
        with patch.object(browser_max, 'collect_all_messages', return_value=[]):
            result = browser_max.scan_channel_for_files()
        assert isinstance(result, list)

    def test_empty_channel_returns_empty_list(self, browser_max):
        """No files in channel returns empty list"""
        browser_max.page.evaluate.return_value = []
        with patch.object(browser_max, 'collect_all_messages', return_value=[]):
            result = browser_max.scan_channel_for_files()
        assert result == []

    def test_extracts_download_link(self, browser_max):
        """Extracts file info from a[download] elements"""
        fake_files = [{
            "filename": "project-v1.0.0.zip",
            "download_url": "https://cdn.max.ru/file/abc123",
            "file_size": 1048576,
            "message_idx": 0,
            "has_direct_url": True,
            "media_type": "file"
        }]
        browser_max.page.evaluate.return_value = fake_files
        with patch.object(browser_max, 'collect_all_messages', return_value=[]):
            result = browser_max.scan_channel_for_files()
        assert len(result) == 1
        assert result[0]["filename"] == "project-v1.0.0.zip"
        assert result[0]["has_direct_url"] is True

    def test_extracts_video(self, browser_max):
        """Extracts file info from video[src] elements"""
        fake_files = [{
            "filename": "demo.mp4",
            "download_url": "https://cdn.max.ru/video/xyz",
            "file_size": 52428800,
            "message_idx": 1,
            "has_direct_url": True,
            "media_type": "video"
        }]
        browser_max.page.evaluate.return_value = fake_files
        with patch.object(browser_max, 'collect_all_messages', return_value=[]):
            result = browser_max.scan_channel_for_files()
        assert len(result) == 1
        assert result[0]["media_type"] == "video"

    def test_deduplicates_by_filename(self, browser_max):
        """Duplicate filenames are removed"""
        browser_max.page.evaluate.return_value = [
            {"filename": "report.pdf", "download_url": "url1", "file_size": 1024, "message_idx": 0, "has_direct_url": True, "media_type": "file"},
            {"filename": "report.pdf", "download_url": "url2", "file_size": 1024, "message_idx": 5, "has_direct_url": True, "media_type": "file"},
        ]
        with patch.object(browser_max, 'collect_all_messages', return_value=[]):
            result = browser_max.scan_channel_for_files()
        assert len(result) == 1

    def test_handles_evaluate_error(self, browser_max):
        """page.evaluate error returns empty list gracefully"""
        browser_max.page.evaluate.side_effect = Exception("JS error")
        with patch.object(browser_max, 'collect_all_messages', return_value=[]):
            result = browser_max.scan_channel_for_files()
        assert result == []

    def test_file_size_zero_when_not_found(self, browser_max):
        """Missing file_size defaults to 0"""
        fake_files = [{
            "filename": "unknown.zip",
            "download_url": "https://cdn.max.ru/file/zzz",
            "file_size": 0,
            "message_idx": 2,
            "has_direct_url": True,
            "media_type": "file"
        }]
        browser_max.page.evaluate.return_value = fake_files
        with patch.object(browser_max, 'collect_all_messages', return_value=[]):
            result = browser_max.scan_channel_for_files()
        assert result[0]["file_size"] == 0

    def test_all_expected_fields_present(self, browser_max):
        """Each file dict has all required fields"""
        fake_files = [{
            "filename": "test.zip",
            "download_url": "https://cdn.max.ru/f",
            "file_size": 100,
            "message_idx": 0,
            "has_direct_url": True,
            "media_type": "file"
        }]
        browser_max.page.evaluate.return_value = fake_files
        expected_fields = {"filename", "download_url", "file_size", "message_idx", "has_direct_url", "media_type"}
        with patch.object(browser_max, 'collect_all_messages', return_value=[]):
            result = browser_max.scan_channel_for_files()
        assert expected_fields.issubset(result[0].keys())

    def test_calls_collect_all_messages(self, browser_max):
        """collect_all_messages is called during scan"""
        browser_max.page.evaluate.return_value = []
        with patch.object(browser_max, 'collect_all_messages', return_value=[]) as mock_cam:
            browser_max.scan_channel_for_files()
            mock_cam.assert_called_once()

    def test_checks_connection_first(self, browser_max):
        """Raises error if not connected"""
        from browser_max import BrowserMAX
        bm = BrowserMAX("https://web.max.ru/test-channel")
        bm.page = None
        with pytest.raises(ConnectionError):
            bm.scan_channel_for_files()
```

**Implementation — add to `browser_max.py` before the `close()` method (before line 5473):**

```python
    def scan_channel_for_files(self) -> list[dict]:
        """
        Scan all messages in the channel and extract file metadata.

        Uses collect_all_messages() to scroll through and load all messages,
        then extracts file information from the DOM using CSS selectors.

        Returns:
            List of dicts with keys:
            - filename (str): name of the file
            - download_url (str): URL to download the file
            - file_size (int): size in bytes (0 if unknown)
            - message_idx (int): approximate index in the feed
            - has_direct_url (bool): True if URL is directly downloadable
            - media_type (str): "file", "video", or "image"
        """
        self._check_connection()

        # Scroll to load all messages into DOM (reuse existing infrastructure)
        # Uses same auto-converge pattern as export feature
        self.collect_all_messages(passes=2, max_stale=3, overscroll_cycles=3)

        # Extract file info from all loaded messages in a single evaluate call
        try:
            file_data = self.page.evaluate(r"""
                () => {
                    const results = [];
                    const seen = new Set();

                    // Find all message-like elements in the feed
                    const messages = document.querySelectorAll(
                        '[class*="message"],[class*="msg"],' +
                        '[class*="lenta-item"],[class*="feed-item"]'
                    );

                    messages.forEach((msg, idx) => {
                        let filename = '';
                        let downloadUrl = '';
                        let hasDirectUrl = false;
                        let fileSize = 0;
                        let mediaType = 'file';

                        // 1. Direct download links: a[download]
                        const downloadLinks = msg.querySelectorAll('a[download]');
                        for (const a of downloadLinks) {
                            const href = a.getAttribute('href') || '';
                            const name = a.getAttribute('download') || '';
                            if (name) {
                                filename = name;
                                downloadUrl = href;
                                hasDirectUrl = !!href;
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
                                    hasDirectUrl = true;
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
                                hasDirectUrl = true;
                                mediaType = 'video';
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
                                    hasDirectUrl = true;
                                    mediaType = 'image';
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

                        // Skip messages without any file indicators
                        if (!filename) return;

                        // Deduplicate by filename
                        if (seen.has(filename)) return;
                        seen.add(filename);

                        // Extract file size from [class*="size"] elements
                        const sizeEls = msg.querySelectorAll('[class*="size"]');
                        for (const el of sizeEls) {
                            const text = el.textContent?.trim()
                                || el.getAttribute('title') || '';
                            if (text) {
                                const match = text.match(
                                    /([\d.]+)\s*(B|KB|MB|GB)/i
                                );
                                if (match) {
                                    const num = parseFloat(match[1]);
                                    const unit = match[2].toUpperCase();
                                    if (unit === 'GB') {
                                        fileSize = num * 1073741824;
                                    } else if (unit === 'MB') {
                                        fileSize = num * 1048576;
                                    } else if (unit === 'KB') {
                                        fileSize = num * 1024;
                                    } else {
                                        fileSize = num;
                                    }
                                }
                                break;
                            }
                        }

                        results.push({
                            filename: filename,
                            download_url: downloadUrl,
                            file_size: fileSize,
                            message_idx: idx,
                            has_direct_url: hasDirectUrl,
                            media_type: mediaType
                        });
                    });

                    return results;
                }
            """)
        except Exception:
            self.logger.warning("scan_channel_for_files: evaluate failed", exc_info=True)
            return []

        return file_data if file_data else []
```

**Verify:** `python -m pytest tests/test_channel_scan.py -v`
**Commit:** `feat(browser): add scan_channel_for_files method`

---

### Task 1.3: channel_downloader.py — DownloadJournal + ChannelDownloader
**File:** `channel_downloader.py` (new file)
**Test:** `tests/test_channel_downloader.py`
**Depends:** none (imports existing `browser_max.py` and `logging_config.py`)

**Pattern to follow:** This module follows the exact same structure as `media_archiver.py` (line 1-404):
- Class with `LogMixin` base
- Nested journal class with lock-based atomic writes (same as `MediaJournal`, lines 25-131)
- `__init__(config_path="config.yaml")` loads config
- `run()` orchestrates the flow
- `_init_browser()` / `_ensure_browser_connected()` / `_cleanup()` — identical pattern
- `main()` entry point with `SessionCapture`
- `if __name__ == "__main__": main()`

**Test file: `tests/test_channel_downloader.py`**

```python
# -*- coding: utf-8 -*-
"""
Tests for ChannelDownloader module.

Tests cover:
- DownloadJournal: is_downloaded, mark_downloaded, mark_failed, get_stats, atomic writes
- ChannelDownloader: empty file list, skip already downloaded, retry logic, file size formatting
"""

import json
import os
import time
import pytest
from unittest.mock import MagicMock, patch, PropertyMock


# ── DownloadJournal Tests ──

class TestDownloadJournalInit:
    """Test DownloadJournal initialization"""

    def test_init_creates_empty_journal(self, tmp_path):
        """New journal has empty files dict and zero stats"""
        from channel_downloader import DownloadJournal
        j = DownloadJournal(str(tmp_path / "journal.json"))
        assert j.data["files"] == {}
        assert j.data["stats"]["total"] == 0

    def test_loads_existing_journal(self, tmp_path):
        """Existing journal is loaded correctly"""
        fp = tmp_path / "journal.json"
        fp.write_text(json.dumps({
            "files": {"test.zip": {"filename": "test.zip", "size_bytes": 100, "status": "downloaded"}},
            "stats": {"total": 1, "downloaded": 1, "failed": 0, "skipped": 0}
        }))
        from channel_downloader import DownloadJournal
        j = DownloadJournal(str(fp))
        assert len(j.data["files"]) == 1
        assert j.data["files"]["test.zip"]["status"] == "downloaded"

    def test_corrupt_file_gets_backup(self, tmp_path):
        """Corrupt journal is backed up and new empty one created"""
        fp = tmp_path / "journal.json"
        fp.write_text("{invalid json}")
        from channel_downloader import DownloadJournal
        j = DownloadJournal(str(fp))
        assert j.data["files"] == {}
        assert os.path.exists(str(fp) + ".backup")


class TestDownloadJournalIsDownloaded:
    """Tests for is_downloaded method"""

    def test_returns_true_for_downloaded_file(self, tmp_path):
        """is_downloaded returns True for recorded file"""
        from channel_downloader import DownloadJournal
        j = DownloadJournal(str(tmp_path / "j.json"))
        j.mark_downloaded("test.zip", 100, "/tmp/test.zip")
        assert j.is_downloaded("test.zip", 100) is True

    def test_returns_false_for_unknown_file(self, tmp_path):
        """is_downloaded returns False for unknown file"""
        from channel_downloader import DownloadJournal
        j = DownloadJournal(str(tmp_path / "j.json"))
        assert j.is_downloaded("unknown.zip", 100) is False

    def test_returns_false_for_size_mismatch(self, tmp_path):
        """is_downloaded returns False when size differs"""
        from channel_downloader import DownloadJournal
        j = DownloadJournal(str(tmp_path / "j.json"))
        j.mark_downloaded("test.zip", 100, "/tmp/test.zip")
        assert j.is_downloaded("test.zip", 99) is False

    def test_returns_false_for_failed_file(self, tmp_path):
        """is_downloaded returns False for failed entries"""
        from channel_downloader import DownloadJournal
        j = DownloadJournal(str(tmp_path / "j.json"))
        j.mark_failed("test.zip", 100, "timeout")
        assert j.is_downloaded("test.zip", 100) is False


class TestDownloadJournalMark:
    """Tests for mark_downloaded and mark_failed"""

    def test_mark_downloaded_saves_to_journal(self, tmp_path):
        """mark_downloaded adds entry and saves"""
        from channel_downloader import DownloadJournal
        j = DownloadJournal(str(tmp_path / "j.json"))
        j.mark_downloaded("archive.zip", 2048, "./downloads/archive.zip")
        assert j.data["files"]["archive.zip"]["status"] == "downloaded"
        assert j.data["files"]["archive.zip"]["size_bytes"] == 2048
        assert j.data["files"]["archive.zip"]["output_path"] == "./downloads/archive.zip"

    def test_mark_failed_saves_to_journal(self, tmp_path):
        """mark_failed adds entry with error"""
        from channel_downloader import DownloadJournal
        j = DownloadJournal(str(tmp_path / "j.json"))
        j.mark_failed("broken.zip", 512, "connection timeout")
        assert j.data["files"]["broken.zip"]["status"] == "failed"
        assert "connection timeout" in j.data["files"]["broken.zip"]["error"]

    def test_mark_downloaded_persists_to_disk(self, tmp_path):
        """mark_downloaded actually writes to file"""
        fp = tmp_path / "j.json"
        from channel_downloader import DownloadJournal
        j = DownloadJournal(str(fp))
        j.mark_downloaded("persist.zip", 999, "/out/persist.zip")
        with open(str(fp), "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data["files"]["persist.zip"]["size_bytes"] == 999

    def test_mark_updates_stats(self, tmp_path):
        """Stats are recalculated after mark"""
        from channel_downloader import DownloadJournal
        j = DownloadJournal(str(tmp_path / "j.json"))
        assert j.get_stats()["total"] == 0
        j.mark_downloaded("a.zip", 10, "/out/a.zip")
        j.mark_failed("b.zip", 20, "err")
        stats = j.get_stats()
        assert stats["total"] == 2
        assert stats["downloaded"] == 1
        assert stats["failed"] == 1


class TestDownloadJournalStats:
    """Tests for get_stats"""

    def test_get_stats_returns_correct_counts(self, tmp_path):
        """get_stats returns accurate counts"""
        from channel_downloader import DownloadJournal
        j = DownloadJournal(str(tmp_path / "j.json"))
        j.mark_downloaded("a.zip", 10, "/out/a.zip")
        j.mark_downloaded("b.zip", 20, "/out/b.zip")
        j.mark_failed("c.zip", 30, "err")
        stats = j.get_stats()
        assert stats["downloaded"] == 2
        assert stats["failed"] == 1
        assert stats["total"] == 3

    def test_stats_zero_for_empty(self, tmp_path):
        """get_stats returns zeros for empty journal"""
        from channel_downloader import DownloadJournal
        j = DownloadJournal(str(tmp_path / "j.json"))
        stats = j.get_stats()
        assert stats["total"] == 0
        assert stats["downloaded"] == 0
        assert stats["failed"] == 0


class TestDownloadJournalAtomicWrite:
    """Tests for atomic write safety"""

    def test_atomic_write_creates_backup(self, tmp_path):
        """Save creates a .bak backup of previous file"""
        fp = tmp_path / "j.json"
        from channel_downloader import DownloadJournal
        j = DownloadJournal(str(fp))
        j.mark_downloaded("v1.zip", 1, "/out/v1.zip")
        assert os.path.exists(str(fp) + ".bak")

    def test_lock_prevents_concurrent_writes(self, tmp_path):
        """Lock file prevents concurrent save"""
        fp = tmp_path / "j.json"
        from channel_downloader import DownloadJournal
        j1 = DownloadJournal(str(fp))
        j2 = DownloadJournal(str(fp))
        # Acquire lock on j1 manually
        with patch.object(j1, '_acquire_lock', return_value=True):
            with patch.object(j1, '_release_lock'):
                j1.mark_downloaded("a.zip", 10, "/out/a.zip")
        # j2 should still work (different lock instance)
        j2.mark_downloaded("b.zip", 20, "/out/b.zip")
        assert j2.is_downloaded("b.zip", 20)

    def test_stale_lock_is_released(self, tmp_path):
        """Lock older than 5 minutes is automatically released"""
        fp = tmp_path / "j.json"
        lock = str(fp) + ".lock"
        # Create stale lock file
        old_time = time.time() - 310
        with open(lock, "w") as f:
            f.write("stale")
        os.utime(lock, (old_time, old_time))

        from channel_downloader import DownloadJournal
        j = DownloadJournal(str(fp))
        assert j._acquire_lock() is True


# ── ChannelDownloader Tests ──

@pytest.fixture
def channel_downloader():
    """Create ChannelDownloader with mocked dependencies"""
    with patch('channel_downloader.load_dotenv'):
        with patch('channel_downloader.yaml.safe_load', return_value={
            'channel_downloader': {'output_dir': './downloads', 'retries': 2, 'retry_delay': 1},
            'max': {'channel_url': 'https://web.max.ru/test'},
            'archiver': {'use_local_browser': False}
        }):
            from channel_downloader import ChannelDownloader
            cd = ChannelDownloader("fake_config.yaml")
            return cd


class TestChannelDownloaderInit:
    """Test ChannelDownloader initialization"""

    def test_init_creates_journal(self):
        """ChannelDownloader creates a DownloadJournal"""
        with patch('channel_downloader.load_dotenv'):
            with patch('channel_downloader.yaml.safe_load', return_value={'channel_downloader': {}}):
                from channel_downloader import ChannelDownloader, DownloadJournal
                cd = ChannelDownloader("fake.yaml")
                assert isinstance(cd.journal, DownloadJournal)

    def test_init_loads_config(self):
        """Config is loaded with defaults"""
        with patch('channel_downloader.load_dotenv'):
            with patch('channel_downloader.yaml.safe_load', return_value={
                'channel_downloader': {'output_dir': '/custom/path', 'retries': 5}
            }):
                from channel_downloader import ChannelDownloader
                cd = ChannelDownloader("fake.yaml")
                assert cd.config['channel_downloader']['output_dir'] == '/custom/path'
                assert cd.config['channel_downloader']['retries'] == 5

    def test_init_sets_defaults(self):
        """Missing config values get defaults"""
        with patch('channel_downloader.load_dotenv'):
            with patch('channel_downloader.yaml.safe_load', return_value={'channel_downloader': {}}):
                from channel_downloader import ChannelDownloader
                cd = ChannelDownloader("fake.yaml")
                assert cd.config['channel_downloader']['output_dir'] == './downloads'
                assert cd.config['channel_downloader']['retries'] == 3
                assert cd.config['channel_downloader']['retry_delay'] == 5

    def test_logger_property(self, channel_downloader):
        """Logger property returns gitax logger"""
        assert channel_downloader.logger.name == "gitax"


class TestChannelDownloaderFormatSize:
    """Tests for _format_file_size"""

    def test_bytes(self, channel_downloader):
        assert channel_downloader._format_file_size(500) == "500 B"

    def test_kb(self, channel_downloader):
        assert channel_downloader._format_file_size(2048) == "2.0 KB"

    def test_mb(self, channel_downloader):
        assert channel_downloader._format_file_size(1048576) == "1.0 MB"

    def test_gb(self, channel_downloader):
        result = channel_downloader._format_file_size(2147483648)
        assert "GB" in result


class TestChannelDownloaderIntegration:
    """Tests for ChannelDownloader orchestration logic"""

    def test_init_browser_creates_browsermax(self, channel_downloader):
        """_init_browser creates BrowserMAX instance"""
        with patch('channel_downloader.BrowserMAX') as mock_bm:
            browser = channel_downloader._init_browser()
            mock_bm.assert_called_once()

    def test_ensure_browser_connected_calls_keep_alive(self, channel_downloader):
        """_ensure_browser_connected calls keep_alive_connect and navigate"""
        mock_browser = MagicMock()
        mock_browser.keep_alive_connect.return_value = True
        with patch.object(channel_downloader, '_init_browser', return_value=mock_browser):
            result = channel_downloader._ensure_browser_connected()
            mock_browser.keep_alive_connect.assert_called_once()
            mock_browser.navigate.assert_called_once()
            mock_browser.ensure_page_ready.assert_called_once()
            assert result == mock_browser

    def test_ensure_browser_connected_raises_on_failure(self, channel_downloader):
        """_ensure_browser_connected raises if keep_alive_connect fails"""
        mock_browser = MagicMock()
        mock_browser.keep_alive_connect.return_value = False
        with patch.object(channel_downloader, '_init_browser', return_value=mock_browser):
            with pytest.raises(Exception, match="Failed to connect"):
                channel_downloader._ensure_browser_connected()

    def test_run_with_empty_scan(self, channel_downloader):
        """run handles empty file list gracefully"""
        mock_browser = MagicMock()
        mock_browser.scan_channel_for_files.return_value = []

        with patch.multiple(channel_downloader,
            _ensure_browser_connected=MagicMock(return_value=mock_browser),
            _get_output_dir=MagicMock(return_value="/tmp/dl")):
            with patch('builtins.input', return_value=''):
                # Should not crash
                channel_downloader.run()

    def test_skip_already_downloaded(self, channel_downloader):
        """Already downloaded files are skipped"""
        channel_downloader.journal.mark_downloaded("existing.zip", 100, "/tmp/existing.zip")
        mock_browser = MagicMock()
        mock_browser.scan_channel_for_files.return_value = [
            {"filename": "existing.zip", "download_url": "url", "file_size": 100,
             "message_idx": 0, "has_direct_url": True, "media_type": "file"}
        ]

        with patch.multiple(channel_downloader,
            _ensure_browser_connected=MagicMock(return_value=mock_browser),
            _get_output_dir=MagicMock(return_value="/tmp/dl")):
            with patch('builtins.input', return_value='y'):
                channel_downloader.run()
                # The download should be skipped because it's in the journal
                # No actual download happens, no errors
                stats = channel_downloader.journal.get_stats()
                assert stats["downloaded"] >= 1  # was already downloaded

    def test_retry_logic_on_connection_error(self, channel_downloader):
        """Connection errors trigger retry during _download_with_requests"""
        mock_browser = MagicMock()
        url = "https://cdn.max.ru/file/test.zip"
        output_path = "/tmp/dl/test.zip"

        # First two calls fail, third succeeds
        with patch('channel_downloader.requests.get') as mock_get:
            mock_get.side_effect = [
                ConnectionError("Network timeout"),
                ConnectionError("Network timeout"),
                MagicMock(headers={}, iter_content=lambda chunk_size: [b"data"])
            ]

            with patch('os.makedirs'):
                with patch('os.path.exists', return_value=False):
                    with patch('builtins.open', MagicMock()):
                        channel_downloader._download_with_requests(mock_browser, url, output_path)

            assert mock_get.call_count == 3

    def test_download_with_requests_raises_on_final_failure(self, channel_downloader):
        """All retries exhausted raises the last error"""
        mock_browser = MagicMock()
        with patch('channel_downloader.requests.get') as mock_get:
            mock_get.side_effect = ConnectionError("Always fails")
            with pytest.raises(ConnectionError):
                channel_downloader._download_with_requests(mock_browser, "url", "/tmp/fail.zip")


class TestChannelDownloaderCleanup:
    """Tests for signal handling and cleanup"""

    def test_signal_handler_sets_shutdown_flag(self, channel_downloader):
        """Signal handler sets _shutdown to True"""
        channel_downloader._signal_handler(None, None)
        assert channel_downloader._shutdown is True

    def test_cleanup_calls_browser_close(self, channel_downloader):
        """_cleanup closes browser if it exists"""
        mock_browser = MagicMock()
        channel_downloader.browser = mock_browser
        channel_downloader._cleanup()
        mock_browser.close.assert_called_once()

    def test_cleanup_handles_missing_browser(self, channel_downloader):
        """_cleanup does not crash when browser is None"""
        channel_downloader.browser = None
        channel_downloader._cleanup()  # Should not raise
```

**Implementation: `channel_downloader.py`**

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Channel Downloader — Скачивание всех файлов из MAX канала в локальную папку

Самостоятельный скрипт для скачивания файлов через BrowserMAX.
Следует тому же паттерну, что media_archiver.py.
"""

import os
import sys
import json
import time
import yaml
import atexit
import signal
import tempfile
import shutil
import requests
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from logging_config import setup_logging, LogMixin, SessionCapture

from browser_max import BrowserMAX


class DownloadJournal:
    """Журнал скачанных файлов — отслеживает какие файлы уже скачаны

    Структура JSON:
    {
        "files": {
            "filename.zip": {
                "filename": "filename.zip",
                "size_bytes": 1234567,
                "downloaded_at": "2026-06-09T12:00:00",
                "output_path": "./downloads/filename.zip",
                "status": "downloaded"
            }
        },
        "stats": {
            "total": 50,
            "downloaded": 45,
            "failed": 3,
            "skipped": 2
        }
    }
    """

    def __init__(self, file_path: str = "download_journal.json"):
        self.file_path = file_path
        self._lock_file = f"{file_path}.lock"
        self.data = self._load()

    def _acquire_lock(self) -> bool:
        """Acquire exclusive lock for safe writes (5 min stale timeout)"""
        try:
            if os.path.exists(self._lock_file):
                lock_age = time.time() - os.path.getmtime(self._lock_file)
                if lock_age > 300:
                    self._release_lock()
                else:
                    return False
            Path(self._lock_file).touch()
            return True
        except Exception:
            return False

    def _release_lock(self):
        """Release lock file"""
        try:
            if os.path.exists(self._lock_file):
                os.remove(self._lock_file)
        except Exception:
            pass

    def _load(self) -> dict:
        """Загрузить журнал из файла"""
        if os.path.exists(self.file_path):
            try:
                with open(self.file_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                backup_path = f"{self.file_path}.backup"
                if os.path.exists(self.file_path):
                    os.rename(self.file_path, backup_path)
                return self._create_empty()
        return self._create_empty()

    def _create_empty(self) -> dict:
        """Создать пустой журнал"""
        return {
            "files": {},
            "stats": {
                "total": 0,
                "downloaded": 0,
                "failed": 0,
                "skipped": 0
            }
        }

    def save(self):
        """Сохранить журнал в файл (атомарная запись)"""
        if not self._acquire_lock():
            return
        try:
            self._update_stats()
            temp_fd, temp_path = tempfile.mkstemp(
                suffix='.json',
                dir=os.path.dirname(self.file_path) or '.'
            )
            try:
                with os.fdopen(temp_fd, 'w', encoding='utf-8') as f:
                    json.dump(self.data, f, ensure_ascii=False, indent=2)
                if os.path.exists(self.file_path):
                    backup_path = f"{self.file_path}.bak"
                    shutil.copy2(self.file_path, backup_path)
                os.replace(temp_path, self.file_path)
            except Exception:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                raise
        finally:
            self._release_lock()

    def _update_stats(self):
        """Обновить статистику на основе текущих данных"""
        files = self.data.get("files", {})
        stats = self.data.setdefault("stats", {})
        downloaded = sum(1 for f in files.values() if f.get("status") == "downloaded")
        failed = sum(1 for f in files.values() if f.get("status") == "failed")
        stats["total"] = len(files)
        stats["downloaded"] = downloaded
        stats["failed"] = failed

    def is_downloaded(self, filename: str, size_bytes: int) -> bool:
        """Проверить, скачан ли файл (по имени + размеру)"""
        entry = self.data.get("files", {}).get(filename)
        if entry and entry.get("size_bytes") == size_bytes and entry.get("status") == "downloaded":
            return True
        return False

    def mark_downloaded(self, filename: str, size_bytes: int, output_path: str):
        """Отметить файл как скачанный"""
        self.data.setdefault("files", {})[filename] = {
            "filename": filename,
            "size_bytes": size_bytes,
            "downloaded_at": datetime.now().isoformat(),
            "output_path": output_path,
            "status": "downloaded"
        }
        self.save()

    def mark_failed(self, filename: str, size_bytes: int, error: str = ""):
        """Отметить файл как ошибочный"""
        self.data.setdefault("files", {})[filename] = {
            "filename": filename,
            "size_bytes": size_bytes,
            "downloaded_at": datetime.now().isoformat(),
            "error": error,
            "status": "failed"
        }
        self.save()

    def get_stats(self) -> dict:
        """Получить статистику журнала"""
        files = self.data.get("files", {})
        downloaded = sum(1 for f in files.values() if f.get("status") == "downloaded")
        failed = sum(1 for f in files.values() if f.get("status") == "failed")
        return {
            "total": len(files),
            "downloaded": downloaded,
            "failed": failed,
            "skipped": self.data.get("stats", {}).get("skipped", 0)
        }


class ChannelDownloader(LogMixin):
    """Скачивание всех файлов из MAX канала в локальную папку

    Оркестрирует процесс:
    1. Подключение к браузеру MAX через CDP
    2. Сканирование канала — сбор метаданных файлов
    3. Показ списка пользователю
    4. Скачивание через requests + cookies из браузера
    5. Ведение журнала скачанных файлов (resume support)
    """

    def __init__(self, config_path: str = "config.yaml"):
        self.config = self._load_config(config_path)
        self.journal = DownloadJournal("download_journal.json")
        self.browser: BrowserMAX | None = None
        self._shutdown = False

        # Register cleanup handlers (same pattern as media_archiver.py)
        atexit.register(self._cleanup)
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        self.logger.info(f"Received signal {signum}, shutting down...")
        self._shutdown = True

    def _cleanup(self):
        """Clean up resources on exit"""
        if self.browser:
            try:
                self.journal.save()
                self.browser.close()
            except Exception:
                pass

    def _load_config(self, config_path: str) -> dict:
        """Загрузить конфигурацию с дефолтами для channel_downloader"""
        load_dotenv()
        config = {}

        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f) or {}

        # Set defaults for channel_downloader section
        config.setdefault('channel_downloader', {})
        cd_config = config['channel_downloader']
        cd_config.setdefault('output_dir', './downloads')
        cd_config.setdefault('retries', 3)
        cd_config.setdefault('retry_delay', 5)

        return config

    def _init_browser(self) -> BrowserMAX:
        """Инициализировать браузер MAX (реюз подключения)"""
        if self.browser is None:
            channel_url = self.config.get('max', {}).get('channel_url', '')
            use_local = self.config.get('archiver', {}).get('use_local_browser', False)
            self.browser = BrowserMAX(channel_url, use_local_browser=use_local)
        return self.browser

    def _ensure_browser_connected(self):
        """Подключиться к MAX и перейти в канал"""
        browser = self._init_browser()
        if not browser.keep_alive_connect():
            raise Exception("Failed to connect to MAX")
        browser.navigate()
        browser.ensure_page_ready()
        return browser

    def _format_file_size(self, size_bytes: int) -> str:
        """Форматировать размер файла (человекочитаемый)"""
        if size_bytes >= 1073741824:
            return f"{size_bytes / 1073741824:.1f} GB"
        elif size_bytes >= 1048576:
            return f"{size_bytes / 1048576:.1f} MB"
        elif size_bytes >= 1024:
            return f"{size_bytes / 1024:.1f} KB"
        return f"{size_bytes} B"

    def _get_output_dir(self) -> str:
        """Спросить у пользователя папку для скачивания (дефолт из config)"""
        default = self.config.get('channel_downloader', {}).get('output_dir', './downloads')
        try:
            user_dir = input(f"  Папка для скачивания [{default}]: ").strip()
            return user_dir if user_dir else default
        except (EOFError, KeyboardInterrupt):
            return default

    def _download_with_requests(self, browser: BrowserMAX, url: str, output_path: str):
        """
        Скачать файл через requests + cookies из браузера.

        Args:
            browser: BrowserMAX instance (uses page.context.cookies())
            url: Download URL
            output_path: Where to save the file

        Raises:
            ConnectionError: On network errors
            IOError: On content-length mismatch or write errors
            requests.HTTPError: On HTTP errors (4xx, 5xx)
        """
        # Extract cookies from browser context
        cookies = browser.page.context.cookies()
        jar = requests.cookies.RequestsCookieJar()
        for c in cookies:
            jar.set(
                c['name'], c['value'],
                domain=c.get('domain', ''),
                path=c.get('path', '/')
            )

        headers = {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/125.0.0.0 Safari/537.36'
            )
        }

        response = requests.get(
            url, stream=True, timeout=300,
            cookies=jar, headers=headers
        )
        response.raise_for_status()

        # Check Content-Length for validation
        content_length = response.headers.get('Content-Length')
        expected = int(content_length) if content_length else None

        # Stream write to disk
        with open(output_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

        # Verify size after download
        if expected:
            actual = os.path.getsize(output_path)
            if actual != expected:
                os.remove(output_path)
                raise IOError(
                    f"Content-Length mismatch: expected {expected}, got {actual}"
                )

    def run(self):
        """Основной метод — сканирование канала и скачивание файлов"""
        stats = self.journal.get_stats()

        print("\n" + "═" * 60)
        print("  Скачивание файлов из канала MAX")
        print("═" * 60)
        print(f"  Журнал: {stats['total']} файлов "
              f"({stats['downloaded']} скачано, {stats['failed']} ошибок)")
        print("─" * 60)

        # 1. Get output directory from user
        output_dir = self._get_output_dir()
        print(f"  Папка: {output_dir}")

        # 2. Connect to browser
        try:
            browser = self._ensure_browser_connected()
        except Exception as e:
            print(f"\n  ✗ Не удалось подключиться к MAX: {e}")
            return

        # 3. Scan channel for files
        print("\n  Сканирование канала...")
        try:
            files = browser.scan_channel_for_files()
        except Exception as e:
            print(f"\n  ✗ Ошибка сканирования: {e}")
            self.logger.error(f"Scan error: {e}", exc_info=True)
            if self.browser:
                self.browser.close()
            input("\n  Нажмите Enter для возврата в меню...")
            return

        if not files:
            print("  ✓ В канале не найдено файловых сообщений.")
            if self.browser:
                self.browser.close()
            input("\n  Нажмите Enter для возврата в меню...")
            return

        # 4. Show file summary
        total_size = sum(f.get("file_size", 0) for f in files)
        print(f"  Найдено: {len(files)} файлов ({self._format_file_size(total_size)})")

        # Show table
        print(f"\n  {'#':>3}  {'Имя файла':<50} {'Размер':>10}")
        print(f"  {'─'*3}  {'─'*50} {'─'*10}")
        for i, f in enumerate(files, 1):
            fname = f.get("filename", "?")
            fsize = self._format_file_size(f.get("file_size", 0))
            display_name = fname[:47] + "..." if len(fname) > 50 else fname
            print(f"  {i:>3}  {display_name:<50} {fsize:>10}")

        # 5. User confirmation
        try:
            confirm = input(
                f"\n  Скачать {len(files)} файлов в '{output_dir}'? [y/N]: "
            ).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n  Отменено.")
            if self.browser:
                self.browser.close()
            input("\n  Нажмите Enter для возврата в меню...")
            return

        if confirm not in ('y', 'yes', 'д', 'да'):
            print("\n  Отменено.")
            if self.browser:
                self.browser.close()
            input("\n  Нажмите Enter для возврата в меню...")
            return

        # 6. Download files
        os.makedirs(output_dir, exist_ok=True)
        retries = self.config.get('channel_downloader', {}).get('retries', 3)
        retry_delay = self.config.get('channel_downloader', {}).get('retry_delay', 5)

        downloaded_count = 0
        skipped_count = 0
        error_count = 0

        for i, file_info in enumerate(files, 1):
            if self._shutdown:
                print(f"\n  ⚠ Прерывание после {i - 1} файлов")
                break

            filename = file_info.get("filename", f"file_{i}")
            file_size = file_info.get("file_size", 0)
            download_url = file_info.get("download_url", "")
            has_direct_url = file_info.get("has_direct_url", False)
            file_size_str = self._format_file_size(file_size)

            # Check journal for deduplication
            if self.journal.is_downloaded(filename, file_size):
                print(f"  [{i}/{len(files)}] {filename} — ✓ уже скачан (журнал)")
                skipped_count += 1
                continue

            # Check if file exists on disk with matching size
            output_path = os.path.join(output_dir, filename)
            if os.path.exists(output_path):
                existing_size = os.path.getsize(output_path)
                if existing_size == file_size:
                    print(f"  [{i}/{len(files)}] {filename} — ✓ уже существует на диске")
                    self.journal.mark_downloaded(filename, file_size, output_path)
                    skipped_count += 1
                    continue
                else:
                    # Size mismatch — add suffix to avoid overwrite
                    base, ext = os.path.splitext(filename)
                    suffix = 1
                    while os.path.exists(os.path.join(output_dir, f"{base}_{suffix}{ext}")):
                        suffix += 1
                    output_path = os.path.join(output_dir, f"{base}_{suffix}{ext}")

            print(f"\n  [{i}/{len(files)}] {filename} ({file_size_str})")

            # Download with retry
            success = False
            for attempt in range(1, retries + 1):
                if self._shutdown:
                    break
                try:
                    if has_direct_url and download_url:
                        self._download_with_requests(browser, download_url, output_path)
                        success = True
                        break
                    else:
                        # Fallback via browser evaluate for files < 50MB
                        if file_size < 50 * 1024 * 1024:
                            print(f"    → Fallback: загрузка через браузер...")
                            raise NotImplementedError(
                                "Browser-based download fallback not yet implemented"
                            )
                        else:
                            print(f"    ✗ Нет URL для скачивания (файл >50MB)")
                            break

                except (ConnectionError, TimeoutError) as e:
                    if attempt < retries:
                        print(f"    ⚠ Ошибка: {e}, попытка {attempt + 1}/{retries}...")
                        time.sleep(retry_delay)
                        # Refresh cookies on retry
                        cookies = browser.page.context.cookies()
                    else:
                        print(f"    ✗ Ошибка после {retries} попыток: {e}")
                        error_count += 1
                except requests.HTTPError as e:
                    if e.response.status_code == 403 or e.response.status_code == 401:
                        print(f"    ✗ Ошибка авторизации (HTTP {e.response.status_code})")
                    elif e.response.status_code == 404:
                        print(f"    ✗ Файл не найден (HTTP 404)")
                    else:
                        print(f"    ✗ HTTP ошибка: {e}")
                    error_count += 1
                    break
                except Exception as e:
                    print(f"    ✗ Ошибка: {e}")
                    self.logger.error(f"Download error for {filename}: {e}", exc_info=True)
                    error_count += 1
                    break

            if success:
                self.journal.mark_downloaded(filename, file_size, output_path)
                downloaded_count += 1
                print(f"    ✓ Скачано")

        # 7. Final summary
        print()
        print("═" * 60)
        print("Скачивание завершено")
        print(f"  Скачано: {downloaded_count}")
        print(f"  Пропущено: {skipped_count}")
        if error_count:
            print(f"  Ошибок: {error_count}")
        print("═" * 60)

        # Final save
        try:
            self.journal.save()
        except Exception:
            pass

        # Close browser
        if self.browser:
            try:
                self.browser.close()
            except Exception:
                pass

        input("\n  Нажмите Enter для возврата в меню...")


def main():
    """Точка входа для запуска как standalone"""
    session = SessionCapture()
    session.start()
    print(f" Session log: {session.path}")

    setup_logging()
    try:
        downloader = ChannelDownloader("config.yaml")
        downloader.run()
    finally:
        session.stop()


if __name__ == "__main__":
    main()
```

**Verify:** `python -m pytest tests/test_channel_downloader.py -v`
**Commit:** `feat(downloader): add ChannelDownloader and DownloadJournal`

---

## Batch 2: Integration (sequential — 1 implementer)

This batch depends on `channel_downloader.py` existing (Task 1.3 must be complete).

### Task 2.1: github_archiver.py — Add download_channel_files() + renumber menu
**File:** `github_archiver.py`
**Test:** none (verify by reading code + manual test)
**Depends:** 1.3

**Changes needed:**

**A) Add new method — insert before `run()` method (before line 1939, after `run_media_archiver()`):**

```python
    # ──────────────────────────────────────────────
    # Channel File Downloader
    # ──────────────────────────────────────────────

    def download_channel_files(self):
        """Скачать все файлы из MAX канала в указанную папку"""
        from channel_downloader import ChannelDownloader

        try:
            downloader = ChannelDownloader("config.yaml")
            downloader.run()
        except Exception as e:
            print(f"\n  ✗ Ошибка: {e}")
            self.logger.error(f"Channel download error: {e}", exc_info=True)

        # Note: ChannelDownloader.run() handles its own "Press Enter" prompt
```

**B) Update `_show_menu()` — change lines 356-357:**

Old (lines 356-357):
```python
        print("  [7] Удалить все сообщения в ленте")
        print("  [8] Выход")
```

New:
```python
        print("  [7] Скачать все файлы из канала")
        print("  [8] Удалить все сообщения в ленте")
        print("  [9] Выход")
```

**C) Update `run()` method — change lines 1947-1968:**

Old (lines 1947-1968):
```python
            choice = input("  Выберите действие [1-8]: ").strip()

            if choice == '1':
                self.sync_repositories()
            elif choice == '2':
                self.load_new_repositories()
            elif choice == '3':
                self._manage_ignore_list()
            elif choice == '4':
                self.audit_and_restore_publications()
            elif choice == '5':
                self.export_messages_to_file()
            elif choice == '6':
                self.run_media_archiver()
            elif choice == '7':
                self.delete_all_messages_in_channel()
            elif choice == '8':
                print("\n  До свидания!\n")
                break
            else:
                print("\n  Неверный выбор. Нажмите 1, 2, 3, 4, 5, 6, 7 или 8.")
```

New:
```python
            choice = input("  Выберите действие [1-9]: ").strip()

            if choice == '1':
                self.sync_repositories()
            elif choice == '2':
                self.load_new_repositories()
            elif choice == '3':
                self._manage_ignore_list()
            elif choice == '4':
                self.audit_and_restore_publications()
            elif choice == '5':
                self.export_messages_to_file()
            elif choice == '6':
                self.run_media_archiver()
            elif choice == '7':
                self.download_channel_files()
            elif choice == '8':
                self.delete_all_messages_in_channel()
            elif choice == '9':
                print("\n  До свидания!\n")
                break
            else:
                print("\n  Неверный выбор. Нажмите 1..9.")
```

**Verify:** `python -c "from github_archiver import GitHubArchiver; ga = GitHubArchiver(); ga._show_menu()"` — confirm menu shows [7] download, [8] delete, [9] exit
**Commit:** `feat(menu): add download channel files option, renumber menu`

---

## Verification Summary

| # | File | Test Command |
|---|------|-------------|
| 1.1 | `config.yaml` | `python -c "import yaml; c=yaml.safe_load(open('config.yaml')); print(c['channel_downloader'])"` |
| 1.2 | `browser_max.py` | `python -m pytest tests/test_channel_scan.py -v` |
| 1.3 | `channel_downloader.py` | `python -m pytest tests/test_channel_downloader.py -v` |
| 2.1 | `github_archiver.py` | `python -c "from github_archiver import GitHubArchiver; ga = GitHubArchiver(); ga._show_menu()"` |

## Manual Integration Test

After all tasks are complete, run the full flow:
```bash
python github_archiver.py
# Select [7] → should prompt for output dir → scan channel → show files → download
```
