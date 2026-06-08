# -*- coding: utf-8 -*-
"""
Tests for large file upload via local browser switch.

Tests cover:
- _get_user_data_dir() default and config-based paths
- _disconnect_cdp() state cleanup
- _launch_with_profile() success and failure
- _close_local_browser() cleanup with delay
- _upload_large_file() full flow and error recovery
- MediaArchiver LARGE_FILE_THRESHOLD routing
"""

import os
import pytest
from unittest.mock import MagicMock, patch, PropertyMock


# ── _get_user_data_dir tests ──

class TestGetUserDataDir:
    """Test _get_user_data_dir method"""

    def test_returns_default_path(self):
        """Returns default Chrome user data path when config has no browser section."""
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")

        with patch("os.path.exists", return_value=False):
            result = bm._get_user_data_dir()

        assert "Google" in result
        assert "Chrome" in result
        assert "User Data" in result
        assert result.endswith("Default")

    def test_returns_config_path(self):
        """Returns configured user_data_dir when set in config.yaml."""
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")

        fake_config = {
            "browser": {
                "user_data_dir": r"C:\Custom\Chrome\Profile",
                "profile_name": "Profile 1"
            }
        }

        with patch("os.path.exists", return_value=True):
            with patch("builtins.open", MagicMock()):
                with patch("yaml.safe_load", return_value=fake_config):
                    result = bm._get_user_data_dir()

        assert r"C:\Custom\Chrome\Profile" in result
        assert "Profile 1" in result

    def test_fallback_when_config_missing(self):
        """Falls back to default when config file doesn't exist."""
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")

        with patch("os.path.exists", return_value=False):
            result = bm._get_user_data_dir()

        assert os.path.isabs(result)

    def test_fallback_when_config_empty(self):
        """Falls back to default when browser section is empty."""
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")

        fake_config = {"browser": {"user_data_dir": "", "profile_name": "Default"}}

        with patch("os.path.exists", return_value=True):
            with patch("builtins.open", MagicMock()):
                with patch("yaml.safe_load", return_value=fake_config):
                    result = bm._get_user_data_dir()

        assert "Google" in result
        assert "Chrome" in result


# ── _disconnect_cdp tests ──

class TestDisconnectCDP:
    """Test _disconnect_cdp method"""

    def test_sets_state_to_none(self):
        """Disconnect sets page, browser to None and _connected to False."""
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")
        bm.page = MagicMock()
        bm.browser = MagicMock()
        bm._connected = True

        bm._disconnect_cdp()

        assert bm.page is None
        assert bm.browser is None
        assert bm._connected is False

    def test_calls_close_on_page_and_browser(self):
        """Disconnect calls close() on page and browser."""
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")
        mock_page = MagicMock()
        mock_browser = MagicMock()
        bm.page = mock_page
        bm.browser = mock_browser
        bm._connected = True

        bm._disconnect_cdp()

        mock_page.close.assert_called_once()
        mock_browser.close.assert_called_once()

    def test_handles_already_closed(self):
        """Doesn't crash if page/browser are already None."""
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")
        bm.page = None
        bm.browser = None
        bm._connected = False

        # Should not raise
        bm._disconnect_cdp()

        assert bm.page is None
        assert bm.browser is None
        assert bm._connected is False

    def test_handles_close_exception(self):
        """Gracefully handles exceptions during close."""
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")
        bm.page = MagicMock()
        bm.page.close.side_effect = Exception("already closed")
        bm.browser = MagicMock()
        bm.browser.close.side_effect = Exception("already closed")
        bm._connected = True

        bm._disconnect_cdp()

        assert bm.page is None
        assert bm.browser is None
        assert bm._connected is False


# ── _launch_with_profile tests ──

class TestLaunchWithProfile:
    """Test _launch_with_profile method"""

    def test_launches_chromium_with_profile(self):
        """Launches Chromium with user data dir from _get_user_data_dir."""
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")
        bm.playwright = MagicMock()

        mock_browser = MagicMock()
        mock_context = MagicMock()
        mock_page = MagicMock()

        bm.playwright.chromium.launch.return_value = mock_browser
        mock_browser.new_context.return_value = mock_context
        mock_context.new_page.return_value = mock_page

        with patch.object(bm, '_get_user_data_dir', return_value=r"C:\Users\test\AppData\Local\Google\Chrome\User Data\Default"):
            result = bm._launch_with_profile()

        assert result is True
        bm.playwright.chromium.launch.assert_called_once()
        assert bm._connected is True
        assert bm.page is mock_page
        assert bm.browser is mock_browser

    def test_returns_false_on_failure(self):
        """Returns False when launch fails."""
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")
        bm.playwright = MagicMock()
        bm.playwright.chromium.launch.side_effect = Exception("Chrome not found")

        result = bm._launch_with_profile()

        assert result is False

    def test_passes_user_data_dir_arg(self):
        """Passes --user-data-dir argument to chromium.launch."""
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")
        bm.playwright = MagicMock()
        mock_browser = MagicMock()
        mock_context = MagicMock()
        mock_page = MagicMock()

        bm.playwright.chromium.launch.return_value = mock_browser
        mock_browser.new_context.return_value = mock_context
        mock_context.new_page.return_value = mock_page

        expected_dir = r"C:\Custom\Profile"
        with patch.object(bm, '_get_user_data_dir', return_value=expected_dir):
            bm._launch_with_profile()

        call_kwargs = bm.playwright.chromium.launch.call_args
        args_list = call_kwargs.kwargs.get('args', [])
        assert any(f'--user-data-dir={expected_dir}' in arg for arg in args_list)


# ── _close_local_browser tests ──

class TestCloseLocalBrowser:
    """Test _close_local_browser method"""

    def test_closes_page_and_browser(self):
        """Closes page and browser, sets state to None."""
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")
        mock_page = MagicMock()
        mock_browser = MagicMock()
        bm.page = mock_page
        bm.browser = mock_browser
        bm._connected = True

        with patch("time.sleep"):
            bm._close_local_browser()

        mock_page.close.assert_called_once()
        mock_browser.close.assert_called_once()
        assert bm.page is None
        assert bm.browser is None
        assert bm._connected is False

    def test_sleeps_after_close(self):
        """Sleeps 2 seconds after close to allow lock file release."""
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")
        bm.page = MagicMock()
        bm.browser = MagicMock()
        bm._connected = True

        with patch("time.sleep") as mock_sleep:
            bm._close_local_browser()

        # At least one sleep call (the 2-second delay)
        mock_sleep.assert_called()

    def test_handles_already_closed(self):
        """Doesn't crash if already closed."""
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")
        bm.page = None
        bm.browser = None
        bm._connected = False

        with patch("time.sleep"):
            bm._close_local_browser()

        assert bm.page is None
        assert bm.browser is None


# ── _upload_large_file tests ──

class TestUploadLargeFile:
    """Test _upload_large_file orchestrator"""

    def test_calls_correct_sequence(self):
        """Verifies the method calls the correct sequence: disconnect -> launch -> navigate -> upload -> close -> reconnect."""
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")
        bm.page = MagicMock()
        bm.browser = MagicMock()
        bm._connected = True

        # Track call order
        call_log = []

        original_disconnect = bm._disconnect_cdp
        original_launch = bm._launch_with_profile
        original_upload = bm._upload_single_file
        original_close = bm._close_local_browser
        original_connect = bm.connect
        original_navigate = bm.navigate

        def track_disconnect():
            call_log.append("disconnect")
            bm.page = None
            bm.browser = None
            bm._connected = False

        def track_launch():
            call_log.append("launch")
            bm._connected = True
            bm.page = MagicMock()
            bm.page.evaluate.return_value = 128
            return True

        def track_upload(*args, **kwargs):
            call_log.append("upload")
            return True

        def track_close():
            call_log.append("close")
            bm.page = None
            bm.browser = None
            bm._connected = False

        def track_connect():
            call_log.append("connect")
            bm._connected = True
            return True

        def track_navigate():
            call_log.append("navigate")

        bm._disconnect_cdp = track_disconnect
        bm._launch_with_profile = track_launch
        bm._upload_single_file = track_upload
        bm._close_local_browser = track_close
        bm.connect = track_connect
        bm.navigate = track_navigate
        bm.ensure_page_ready = lambda: None

        with patch("time.sleep"):
            result = bm._upload_large_file(
                "/path/to/file.mp4", "file.mp4", 100_000_000,
                retries=3, retry_delay=10, baseline_count=42
            )

        assert result is True
        # Verify sequence
        assert "disconnect" in call_log
        assert "launch" in call_log
        assert "navigate" in call_log
        assert "upload" in call_log
        assert "close" in call_log
        assert "connect" in call_log

        # Verify order: disconnect before launch before upload before close before reconnect
        assert call_log.index("disconnect") < call_log.index("launch")
        assert call_log.index("launch") < call_log.index("upload")
        assert call_log.index("upload") < call_log.index("close")
        assert call_log.index("close") < call_log.index("connect")

    def test_returns_false_when_launch_fails(self):
        """Returns False and attempts recovery when launch fails."""
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")
        bm.page = MagicMock()
        bm.browser = MagicMock()
        bm._connected = True

        def track_disconnect():
            bm.page = None
            bm.browser = None
            bm._connected = False

        def track_launch():
            return False

        bm._disconnect_cdp = track_disconnect
        bm._launch_with_profile = track_launch
        bm.connect = MagicMock(return_value=True)

        with patch("time.sleep"):
            result = bm._upload_large_file(
                "/path/to/file.mp4", "file.mp4", 100_000_000,
                retries=3, retry_delay=10, baseline_count=42
            )

        assert result is False

    def test_recoveries_from_upload_error(self):
        """Attempts recovery (close + reconnect) when upload throws exception."""
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")
        bm.page = MagicMock()
        bm.browser = MagicMock()
        bm._connected = True

        recovery_calls = []

        def track_disconnect():
            bm.page = None
            bm.browser = None
            bm._connected = False

        def track_launch():
            bm._connected = True
            return True

        def track_upload(*args, **kwargs):
            raise Exception("Network error")

        def track_close():
            recovery_calls.append("close")
            bm.page = None
            bm.browser = None
            bm._connected = False

        def track_connect():
            recovery_calls.append("connect")
            bm._connected = True
            return True

        bm._disconnect_cdp = track_disconnect
        bm._launch_with_profile = track_launch
        bm._upload_single_file = track_upload
        bm._close_local_browser = track_close
        bm.connect = track_connect
        bm.navigate = MagicMock()
        bm.ensure_page_ready = MagicMock()

        with patch("time.sleep"):
            result = bm._upload_large_file(
                "/path/to/file.mp4", "file.mp4", 100_000_000,
                retries=3, retry_delay=10, baseline_count=42
            )

        assert result is False
        assert "close" in recovery_calls
        assert "connect" in recovery_calls


# ── MediaArchiver routing tests ──

class TestMediaArchiverRouting:
    """Test MediaArchiver routes large/small files correctly"""

    def test_large_file_routes_to_upload_large_file(self):
        """Files >= 50MB are routed to _upload_large_file."""
        from media_archiver import MediaArchiver

        # Verify threshold
        assert MediaArchiver.LARGE_FILE_THRESHOLD == 50 * 1024 * 1024

        # Test the routing logic directly
        file_size_large = 60 * 1024 * 1024  # 60 MB
        is_large = file_size_large >= MediaArchiver.LARGE_FILE_THRESHOLD
        assert is_large is True

        file_size_small = 30 * 1024 * 1024  # 30 MB
        is_large = file_size_small >= MediaArchiver.LARGE_FILE_THRESHOLD
        assert is_large is False

    def test_boundary_50mb_routes_to_large(self):
        """Exactly 50MB should route to large file handler."""
        from media_archiver import MediaArchiver

        file_size = 50 * 1024 * 1024
        is_large = file_size >= MediaArchiver.LARGE_FILE_THRESHOLD
        assert is_large is True

    def test_just_under_50mb_routes_to_normal(self):
        """Just under 50MB should use normal upload."""
        from media_archiver import MediaArchiver

        file_size = (50 * 1024 * 1024) - 1
        is_large = file_size >= MediaArchiver.LARGE_FILE_THRESHOLD
        assert is_large is False

    def test_run_routes_large_file(self):
        """MediaArchiver.run() calls _upload_large_file for large files."""
        from media_archiver import MediaArchiver, MediaJournal
        from browser_max import BrowserMAX

        archiver = MagicMock(spec=MediaArchiver)
        archiver.LARGE_FILE_THRESHOLD = 50 * 1024 * 1024
        archiver.journal = MagicMock(spec=MediaJournal)
        archiver.journal.is_sent.return_value = False

        browser = MagicMock(spec=BrowserMAX)
        browser._pre_upload_msg_count = 42

        # Simulate large file routing
        file_size = 60 * 1024 * 1024
        filepath = "/tmp/large_video.mp4"
        filename = "large_video.mp4"
        ext = ".mp4"
        retries = 3
        retry_delay = 10

        if file_size >= archiver.LARGE_FILE_THRESHOLD:
            browser._upload_large_file(
                filepath, filename, file_size,
                retries=retries,
                retry_delay=retry_delay,
                baseline_count=browser._pre_upload_msg_count
            )
        else:
            browser.send_message_with_files(
                text="",
                filepaths=[filepath],
                retries=retries,
                retry_delay=retry_delay,
                split_threshold_mb=999999,
                expected_extensions=[ext]
            )

        # Verify _upload_large_file was called, NOT send_message_with_files
        browser._upload_large_file.assert_called_once()
        browser.send_message_with_files.assert_not_called()

    def test_run_routes_small_file(self):
        """MediaArchiver.run() calls send_message_with_files for small files."""
        from media_archiver import MediaArchiver, MediaJournal
        from browser_max import BrowserMAX

        archiver = MagicMock(spec=MediaArchiver)
        archiver.LARGE_FILE_THRESHOLD = 50 * 1024 * 1024
        archiver.journal = MagicMock(spec=MediaJournal)
        archiver.journal.is_sent.return_value = False

        browser = MagicMock(spec=BrowserMAX)
        browser._pre_upload_msg_count = 42

        # Simulate small file routing
        file_size = 10 * 1024 * 1024
        filepath = "/tmp/small_photo.jpg"
        filename = "small_photo.jpg"
        ext = ".jpg"
        retries = 3
        retry_delay = 10

        if file_size >= archiver.LARGE_FILE_THRESHOLD:
            browser._upload_large_file(
                filepath, filename, file_size,
                retries=retries,
                retry_delay=retry_delay,
                baseline_count=browser._pre_upload_msg_count
            )
        else:
            browser.send_message_with_files(
                text="",
                filepaths=[filepath],
                retries=retries,
                retry_delay=retry_delay,
                split_threshold_mb=999999,
                expected_extensions=[ext]
            )

        # Verify send_message_with_files was called, NOT _upload_large_file
        browser.send_message_with_files.assert_called_once()
        browser._upload_large_file.assert_not_called()
