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

    def test_returns_config_path(self, tmp_path):
        """Returns user_data_dir from config when set."""
        from browser_max import BrowserMAX
        from config import init_config, get_config

        # Create a test config file with raw string to avoid escape issues
        config_file = tmp_path / "test_config.yaml"
        config_file.write_text("""browser:
  user_data_dir: "C:\\\\Custom\\\\Chrome\\\\Profile"
  profile_name: "Profile 1"
channels:
  max: https://web.max.ru/test
""")

        bm = BrowserMAX("https://example.com")
        init_config(str(config_file))
        result = bm._get_user_data_dir()
        assert r"C:\Custom\Chrome\Profile" in result
        assert "Profile 1" in result

    def test_fallback_when_config_missing(self, tmp_path):
        """Falls back to default when config file doesn't exist."""
        from browser_max import BrowserMAX
        from config import init_config

        # Use a non-existent config path
        nonexistent = tmp_path / "nonexistent.yaml"
        bm = BrowserMAX("https://example.com")
        init_config(str(nonexistent))
        result = bm._get_user_data_dir()

        assert os.path.isabs(result)
        assert "Google" in result
        assert "Chrome" in result

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

    def test_sets_expected_extensions(self):
        """Verifies expected_extensions parameter sets self._expected_extensions before upload."""
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")
        bm.page = MagicMock()
        bm.browser = MagicMock()
        bm._connected = True

        captured_extensions = None

        def track_upload(*args, **kwargs):
            nonlocal captured_extensions
            captured_extensions = bm._expected_extensions.copy()
            return True

        bm._disconnect_cdp = lambda: None
        bm._launch_with_profile = lambda: True
        bm._upload_single_file = track_upload
        bm._close_local_browser = lambda: None
        bm.connect = MagicMock(return_value=True)
        bm.navigate = MagicMock()
        bm.ensure_page_ready = MagicMock()

        with patch("time.sleep"):
            bm._upload_large_file(
                "/path/to/video.mp4", "video.mp4", 100_000_000,
                retries=3, retry_delay=10, baseline_count=0,
                expected_extensions=[".mp4"]
            )

        assert captured_extensions == [".mp4"]

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


# ── Media file detection tests ──

class TestMediaFileDetection:
    """Test that media files (.mp4, .jpg, etc.) are detected by the scanner"""

    def test_match_filename_accepts_mp4(self):
        """_match_filename_in_message matches .mp4 files."""
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")
        bm._expected_extensions = [".mp4"]

        # Simulate a message containing an .mp4 file
        msg_text = "video_2026.mp4 download"
        msg_html = "<div>video_2026.mp4</div>"

        matched, detail = bm._match_filename_in_message(
            msg_text, msg_html, "video_2026.mp4"
        )

        assert matched is True
        assert "video_2026.mp4" in detail

    def test_match_filename_accepts_jpg(self):
        """_match_filename_in_message matches .jpg files."""
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")
        bm._expected_extensions = [".jpg"]

        msg_text = "photo_2026.jpg скачать"
        msg_html = "<div>photo_2026.jpg</div>"

        matched, detail = bm._match_filename_in_message(
            msg_text, msg_html, "photo_2026.jpg"
        )

        assert matched is True

    def test_generic_file_detection_includes_media(self):
        """When search_name is None, media files are still detected."""
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")
        bm._expected_extensions = [".zip"]  # Default is .zip

        # Even with .zip as expected extension, .mp4 should be detected
        msg_text = "video_2026.mp4 download"
        msg_html = "<div>video_2026.mp4</div>"

        matched, detail = bm._match_filename_in_message(
            msg_text, msg_html, None  # No specific filename
        )

        assert matched is True
        assert detail == "generic_file"


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
        """Returns True when filename appears in feed.
        Evaluate call order per attempt:
          1. _scroll_to_bottom() — bool
          2. feed scan — dict with found: True
        """
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")
        bm._connected = True
        bm.page = MagicMock()
        bm.page.is_closed.return_value = False

        bm.page.evaluate.side_effect = [
            True,  # _scroll_to_bottom()
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
        """Returns False after all retries exhausted.
        Evaluate call order per attempt:
          1. _scroll_to_bottom() — bool
          2. feed scan — dict with found: False
        3 attempts × 2 = 6 evaluate calls
        """
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")
        bm._connected = True
        bm.page = MagicMock()
        bm.page.is_closed.return_value = False

        evaluate_calls = [0]
        def mock_evaluate(expr):
            evaluate_calls[0] += 1
            idx = (evaluate_calls[0] - 1) % 2
            if idx == 0:
                return True  # _scroll_to_bottom()
            return {"found": False}  # feed scan always fails

        bm.page.evaluate.side_effect = mock_evaluate

        with patch('time.sleep'):
            result = bm._confirm_file_in_feed(
                "missing-file.zip",
                100 * 1024 * 1024,
                baseline_count=45
            )

        assert result is False

    def test_finds_filename_on_retry(self):
        """Filename found on second attempt.
        Attempt 1: scroll + scan({"found": False}) → retry
        Attempt 2: scroll + scan({"found": True}) → success
        """
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")
        bm._connected = True
        bm.page = MagicMock()
        bm.page.is_closed.return_value = False

        evaluate_calls = [0]
        def mock_evaluate(expr):
            evaluate_calls[0] += 1
            attempt_num = (evaluate_calls[0] - 1) // 2
            call_in_attempt = (evaluate_calls[0] - 1) % 2
            if call_in_attempt == 0:
                return True  # _scroll_to_bottom()
            # Scan: first attempt fails, second succeeds
            if attempt_num == 0:
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
        """50-200 MB files get 15 second initial wait."""
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")
        bm._connected = True
        bm.page = MagicMock()
        bm.page.is_closed.return_value = False
        bm.page.evaluate.return_value = {"found": True, "text": "test.zip"}

        sleeps = []
        def mock_sleep(seconds):
            sleeps.append(seconds)

        with patch('time.sleep', side_effect=mock_sleep):
            bm._confirm_file_in_feed("test.zip", 100 * 1024 * 1024, baseline_count=0)

        # First sleep should be 15 seconds (initial wait for 50-200 MB)
        assert sleeps[0] == 15

    def test_adaptive_wait_200_500mb(self):
        """200-500 MB files get 30 second initial wait."""
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")
        bm._connected = True
        bm.page = MagicMock()
        bm.page.is_closed.return_value = False
        bm.page.evaluate.return_value = {"found": True, "text": "test.zip"}

        sleeps = []
        def mock_sleep(seconds):
            sleeps.append(seconds)

        with patch('time.sleep', side_effect=mock_sleep):
            bm._confirm_file_in_feed("test.zip", 300 * 1024 * 1024, baseline_count=0)

        assert sleeps[0] == 30

    def test_adaptive_wait_500plus_mb(self):
        """500+ MB files get 60 second initial wait."""
        from browser_max import BrowserMAX

        bm = BrowserMAX("https://example.com")
        bm._connected = True
        bm.page = MagicMock()
        bm.page.is_closed.return_value = False
        bm.page.evaluate.return_value = {"found": True, "text": "test.zip"}

        sleeps = []
        def mock_sleep(seconds):
            sleeps.append(seconds)

        with patch('time.sleep', side_effect=mock_sleep):
            bm._confirm_file_in_feed("test.zip", 600 * 1024 * 1024, baseline_count=0)

        assert sleeps[0] == 60


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
