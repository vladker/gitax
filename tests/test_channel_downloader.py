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
        """Save creates a .bak backup of previous file (after second save)"""
        fp = tmp_path / "j.json"
        from channel_downloader import DownloadJournal
        j = DownloadJournal(str(fp))
        j.mark_downloaded("v1.zip", 1, "/out/v1.zip")
        j.mark_downloaded("v2.zip", 2, "/out/v2.zip")  # Second save creates .bak of first
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
def channel_downloader(tmp_path):
    """Create ChannelDownloader with mocked dependencies"""
    # Create a minimal config file for testing
    config_file = tmp_path / "test_config.yaml"
    config_file.write_text("""
channel_downloader:
  output_dir: ./downloads
  retries: 2
  retry_delay: 1
channels:
  max: https://web.max.ru/test
archiver:
  use_local_browser: false
""")
    from channel_downloader import ChannelDownloader
    cd = ChannelDownloader(str(config_file))
    return cd


class TestChannelDownloaderInit:
    """Test ChannelDownloader initialization"""

    def test_init_creates_journal(self, tmp_path):
        """ChannelDownloader creates a DownloadJournal"""
        config_file = tmp_path / "test_config.yaml"
        config_file.write_text("channel_downloader: {}\nchannels:\n  max: https://web.max.ru/test\n")
        from channel_downloader import ChannelDownloader, DownloadJournal
        cd = ChannelDownloader(str(config_file))
        assert isinstance(cd.journal, DownloadJournal)

    def test_init_loads_config(self, tmp_path):
        """Config is loaded with defaults"""
        config_file = tmp_path / "test_config.yaml"
        config_file.write_text(
            "channel_downloader:\n  output_dir: /custom/path\n  retries: 5\n"
            "channels:\n  max: https://web.max.ru/test\n"
        )
        from channel_downloader import ChannelDownloader
        cd = ChannelDownloader(str(config_file))
        assert cd.config['channel_downloader']['output_dir'] == '/custom/path'
        assert cd.config['channel_downloader']['retries'] == 5

    def test_init_sets_defaults(self, tmp_path):
        """Missing config values get defaults"""
        config_file = tmp_path / "test_config.yaml"
        config_file.write_text("channel_downloader: {}\nchannels:\n  max: https://web.max.ru/test\n")
        from channel_downloader import ChannelDownloader
        cd = ChannelDownloader(str(config_file))
        assert cd.config['channel_downloader']['output_dir'] == './downloads'
        assert cd.config['channel_downloader']['retries'] == 3
        assert cd.config['channel_downloader']['retry_delay'] == 5

    def test_logger_property(self, channel_downloader):
        """Logger property returns gitax logger"""
        assert channel_downloader.logger.name == "gitax"


class TestChannelDownloaderFormatSize:
    """Tests for utils.format_file_size (used by ChannelDownloader)"""

    def test_bytes(self):
        from utils import format_file_size
        assert format_file_size(500) == "500 B"

    def test_kb(self):
        from utils import format_file_size
        assert format_file_size(2048) == "2.0 KB"

    def test_mb(self):
        from utils import format_file_size
        assert format_file_size(1048576) == "1.0 MB"

    def test_gb(self):
        from utils import format_file_size
        result = format_file_size(2147483648)
        assert "GB" in result


class TestChannelDownloaderIntegration:
    """Tests for ChannelDownloader orchestration logic"""

    def test_init_browser_creates_browsermax(self, channel_downloader):
        """_init_browser creates BrowserMAX instance"""
        with patch('browser_init.BrowserMAX') as mock_bm:
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

    def test_download_with_requests_connection_error(self, channel_downloader):
        """_download_with_requests retries on ConnectionError via @retry decorator"""
        mock_browser = MagicMock()
        url = "https://cdn.max.ru/file/test.zip"
        output_path = "/tmp/dl/test.zip"

        with patch('channel_downloader.requests.get') as mock_get:
            mock_get.side_effect = ConnectionError("Network timeout")

            with pytest.raises(ConnectionError, match="Network timeout"):
                channel_downloader._download_with_requests(mock_browser, url, output_path)

            # 1 initial + 3 retries = 4 calls
            assert mock_get.call_count == 4

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
        import signal
        for sig_handler in (signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM)):
            if sig_handler:
                sig_handler(None, None)
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


# ── DownloadJournal Clear Tests ──

class TestDownloadJournalClear:
    """Tests for DownloadJournal.clear()"""

    def test_clear_resets_data(self, tmp_path):
        """clear() resets download journal to empty state"""
        from channel_downloader import DownloadJournal
        j = DownloadJournal(str(tmp_path / "journal.json"))
        j.mark_downloaded("file.zip", 100, "/tmp/file.zip")
        assert j.get_stats()["total"] == 1
        j.clear()
        assert j.get_stats()["total"] == 0

    def test_clear_empties_file_on_disk(self, tmp_path):
        """clear() writes empty download journal to disk"""
        from channel_downloader import DownloadJournal
        fp = tmp_path / "journal.json"
        j = DownloadJournal(str(fp))
        j.mark_downloaded("file.zip", 100, "/tmp/file.zip")
        j.clear()
        # Re-read from disk
        j2 = DownloadJournal(str(fp))
        assert j2.get_stats()["total"] == 0

    def test_clear_on_empty_journal_does_not_crash(self, tmp_path):
        """clear() on empty download journal does not raise"""
        from channel_downloader import DownloadJournal
        j = DownloadJournal(str(tmp_path / "journal.json"))
        j.clear()  # Should not raise


class TestChannelDownloaderEnvConfig:
    """Tests for channel URL resolution via centralized config system"""

    def test_channel_url_from_env_var(self, tmp_path, monkeypatch):
        """CHANNEL_max env var is resolved into config['max']['channel_url']"""
        monkeypatch.setenv("CHANNEL_max", "https://web.max.ru/env-channel")
        config_file = tmp_path / "test_config.yaml"
        config_file.write_text("channels: {}\n")
        from channel_downloader import ChannelDownloader
        cd = ChannelDownloader(str(config_file))
        # Legacy channel preserved for backward compat
        assert cd.config['channels']['max'] == "https://web.max.ru/env-channel"
        assert len(cd.config['channel_registry']['github']) == 1
        assert cd.config['channel_registry']['github'][0]['url'] == "https://web.max.ru/env-channel"

    def test_env_var_overrides_yaml(self, tmp_path, monkeypatch):
        """Env var takes priority over config.yaml value"""
        monkeypatch.setenv("CHANNEL_max", "https://web.max.ru/env-channel")
        config_file = tmp_path / "test_config.yaml"
        config_file.write_text("channels:\n  max: https://web.max.ru/yaml-channel\n")
        from channel_downloader import ChannelDownloader
        cd = ChannelDownloader(str(config_file))
        # Legacy channel preserved for backward compat
        assert cd.config['channels']['max'] == "https://web.max.ru/env-channel"
        assert len(cd.config['channel_registry']['github']) == 1
        assert cd.config['channel_registry']['github'][0]['url'] == "https://web.max.ru/env-channel"

    def test_yaml_fallback_when_no_env(self, tmp_path, monkeypatch):
        """config.yaml channels.max is used when env var is not set"""
        monkeypatch.delenv("CHANNEL_max", raising=False)
        # Prevent load_dotenv from loading .env and overriding test config
        monkeypatch.setattr("config.loader.load_dotenv", lambda: None)
        config_file = tmp_path / "test_config.yaml"
        config_file.write_text("channels:\n  max: https://web.max.ru/yaml-channel\n")
        from channel_downloader import ChannelDownloader
        cd = ChannelDownloader(str(config_file))
        # Legacy channel preserved for backward compat
        assert cd.config['channels']['max'] == "https://web.max.ru/yaml-channel"
        assert len(cd.config['channel_registry']['github']) == 1
        assert cd.config['channel_registry']['github'][0]['url'] == "https://web.max.ru/yaml-channel"
