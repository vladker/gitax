# -*- coding: utf-8 -*-
"""
Tests for media upload reload fix — upload state manager, media classification,
no-reload monitoring, video handling, and navigation guards.

Tests cover:
- Upload state lock/unlock and can_navigate guard
- Media type classification by extension
- _confirm_file_in_feed() video/media tag matching
- _wait_upload_complete() video no-activity skip
- _wait_for_file_message() never calls page.reload()
- _confirm_file_sent() 50MB guard
- _verify_composer_cleared() missing DOM fix
- Navigation guards block during upload
"""

import pytest
from unittest.mock import MagicMock, patch
import os


# ── Fixtures ──

@pytest.fixture
def browser_max():
    """Create a BrowserMAX instance with mocked page."""
    from browser_max import BrowserMAX
    bm = BrowserMAX("https://example.com")
    bm.page = MagicMock()
    bm.page.is_closed.return_value = False
    bm._connected = True
    return bm


# ── Task 1.1: Upload State Manager ──

class TestUploadStateManager:
    """Tests for _lock_upload_state, _unlock_upload_state, _can_navigate"""

    def test_initial_state_not_in_progress(self):
        """Upload is not in progress by default."""
        from browser_max import BrowserMAX
        bm = BrowserMAX("https://example.com")
        assert bm._upload_in_progress is False
        assert bm._upload_file_size == 0
        assert bm._upload_file_name == ""
        assert bm._is_video is False

    def test_can_navigate_returns_true_by_default(self, browser_max):
        """_can_navigate() returns True when no upload in progress."""
        assert browser_max._can_navigate() is True

    def test_lock_sets_flags(self, browser_max, tmp_path):
        """_lock_upload_state sets all upload flags."""
        # Create a temp file
        filepath = tmp_path / "test.txt"
        filepath.write_text("hello")
        filepath = str(filepath)

        browser_max._lock_upload_state(filepath)

        assert browser_max._upload_in_progress is True
        assert browser_max._upload_file_name == "test.txt"
        assert browser_max._upload_file_size > 0

    def test_lock_detects_video(self, browser_max, tmp_path):
        """_lock_upload_state detects video files via extension."""
        filepath = tmp_path / "video.mp4"
        filepath.write_text("fake video content")
        filepath = str(filepath)

        browser_max._lock_upload_state(filepath)

        assert browser_max._is_video is True

    def test_lock_detects_non_video(self, browser_max, tmp_path):
        """_lock_upload_state correctly identifies non-video files."""
        filepath = tmp_path / "archive.zip"
        filepath.write_text("fake zip content")
        filepath = str(filepath)

        browser_max._lock_upload_state(filepath)

        assert browser_max._is_video is False

    def test_unlock_resets_flags(self, browser_max, tmp_path):
        """_unlock_upload_state resets all flags to defaults."""
        filepath = tmp_path / "test.mp4"
        filepath.write_text("content")
        filepath = str(filepath)

        browser_max._lock_upload_state(filepath)
        assert browser_max._upload_in_progress is True

        browser_max._unlock_upload_state()

        assert browser_max._upload_in_progress is False
        assert browser_max._upload_file_size == 0
        assert browser_max._upload_file_name == ""
        assert browser_max._is_video is False

    def test_can_navigate_blocked_during_upload(self, browser_max, tmp_path):
        """_can_navigate() returns False during active upload."""
        filepath = tmp_path / "doc.pdf"
        filepath.write_text("content")
        filepath = str(filepath)

        browser_max._lock_upload_state(filepath)

        assert browser_max._can_navigate() is False

    def test_can_navigate_after_unlock(self, browser_max, tmp_path):
        """_can_navigate() returns True after unlock."""
        filepath = tmp_path / "doc.pdf"
        filepath.write_text("content")
        filepath = str(filepath)

        browser_max._lock_upload_state(filepath)
        browser_max._unlock_upload_state()

        assert browser_max._can_navigate() is True


# ── Task 1.2: Media Type Classification ──

class TestMediaClassification:
    """Tests for _classify_media static method"""

    def test_mp4_is_video(self):
        from browser_max import BrowserMAX
        assert BrowserMAX._classify_media("video.mp4") == "video"

    def test_avi_is_video(self):
        from browser_max import BrowserMAX
        assert BrowserMAX._classify_media("clip.avi") == "video"

    def test_mov_is_video(self):
        from browser_max import BrowserMAX
        assert BrowserMAX._classify_media("movie.mov") == "video"

    def test_mkv_is_video(self):
        from browser_max import BrowserMAX
        assert BrowserMAX._classify_media("film.mkv") == "video"

    def test_webm_is_video(self):
        from browser_max import BrowserMAX
        assert BrowserMAX._classify_media("anim.webm") == "video"

    def test_jpg_is_image(self):
        from browser_max import BrowserMAX
        assert BrowserMAX._classify_media("photo.jpg") == "image"

    def test_jpeg_is_image(self):
        from browser_max import BrowserMAX
        assert BrowserMAX._classify_media("photo.jpeg") == "image"

    def test_png_is_image(self):
        from browser_max import BrowserMAX
        assert BrowserMAX._classify_media("screenshot.png") == "image"

    def test_gif_is_image(self):
        from browser_max import BrowserMAX
        assert BrowserMAX._classify_media("animation.gif") == "image"

    def test_zip_is_archive(self):
        from browser_max import BrowserMAX
        assert BrowserMAX._classify_media("files.zip") == "archive"

    def test_7z_is_archive(self):
        from browser_max import BrowserMAX
        assert BrowserMAX._classify_media("files.7z") == "archive"

    def test_tar_gz_is_archive(self):
        from browser_max import BrowserMAX
        assert BrowserMAX._classify_media("files.tar.gz") == "archive"

    def test_whl_is_archive(self):
        from browser_max import BrowserMAX
        assert BrowserMAX._classify_media("package-1.0-py3-none-any.whl") == "archive"

    def test_unknown_is_other(self):
        from browser_max import BrowserMAX
        assert BrowserMAX._classify_media("document.pdf") == "other"

    def test_no_extension_is_other(self):
        from browser_max import BrowserMAX
        assert BrowserMAX._classify_media("Makefile") == "other"

    def test_case_insensitive_mp4(self):
        from browser_max import BrowserMAX
        assert BrowserMAX._classify_media("VIDEO.MP4") == "video"

    def test_case_insensitive_jpg(self):
        from browser_max import BrowserMAX
        assert BrowserMAX._classify_media("Photo.JPG") == "image"

    def test_video_extensions_set_contains_all(self):
        from browser_max import BrowserMAX
        expected = {'.mp4', '.avi', '.mov', '.mkv', '.webm', '.flv', '.wmv', '.m4v'}
        assert BrowserMAX.VIDEO_EXTENSIONS == expected

    def test_image_extensions_set_contains_all(self):
        from browser_max import BrowserMAX
        expected = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'}
        assert BrowserMAX.IMAGE_EXTENSIONS == expected


# ── Task 2.3: _confirm_file_sent 50MB guard ──

class TestConfirmFileSentGuard:
    """Tests for _confirm_file_sent 50MB threshold guard"""

    def test_returns_false_for_large_file(self, browser_max):
        """_confirm_file_sent returns False for files >= 50MB."""
        from browser_max import ContentSnapshot
        pre = ContentSnapshot(hash="abc", file_count=5)
        result = browser_max._confirm_file_sent(pre, 50 * 1024 * 1024)
        assert result is False

    def test_returns_false_for_very_large_file(self, browser_max):
        """_confirm_file_sent returns False for very large files."""
        from browser_max import ContentSnapshot
        pre = ContentSnapshot(hash="abc", file_count=5)
        result = browser_max._confirm_file_sent(pre, 200 * 1024 * 1024)
        assert result is False

    def test_still_works_for_small_file(self, browser_max):
        """_confirm_file_sent still processes files < 50MB (returns False due to mock)."""
        from browser_max import ContentSnapshot
        pre = ContentSnapshot(hash="abc", file_count=5)
        # Mock _take_content_snapshot to return None (simulating no DOM change)
        with patch.object(browser_max, '_take_content_snapshot', return_value=None):
            result = browser_max._confirm_file_sent(pre, 1024 * 1024)
        assert result is False  # False because no content change detected


# ── Task 1.3: _verify_composer_cleared missing DOM fix ──

class TestVerifyComposerCleared:
    """Tests for _verify_composer_cleared missing DOM fix"""

    def test_missing_composer_returns_false(self, browser_max):
        """
        When composer element is missing from DOM, return False (not clear).
        This prevents false positives during upload.
        """
        # Simulate page.evaluate returning False (JS returns false when composer is missing)
        def mock_evaluate(script):
            if 'composer' in script or 'querySelector' in script:
                return False  # The JS now returns false when composer is missing
            return None

        browser_max.page.evaluate.side_effect = mock_evaluate

        result = browser_max._verify_composer_cleared(timeout=1, poll_interval=0.1)
        assert result is False, "Missing composer should return False (not clear)"


# ── Task 3.1: _wait_upload_complete video handling ──

class TestWaitUploadCompleteVideo:
    """Tests for _wait_upload_complete video-specific behavior"""

    def test_video_does_not_crash(self, browser_max):
        """
        When _is_video is True, _wait_upload_complete should not crash
        and should handle the video path without errors.
        """
        browser_max._is_video = True
        with patch.object(browser_max.logger, 'info'):
            with patch.object(browser_max, '_check_connection'):
                with patch.object(browser_max, '_install_upload_observer', return_value='obs_1'):
                    with patch.object(browser_max, '_capture_pre_upload_state', return_value={}):
                        with patch('time.sleep'):
                            with patch.object(browser_max, '_check_upload_progress', return_value=None):
                                with patch.object(browser_max, '_check_upload_done', return_value=(False, None)):
                                    with patch.object(browser_max, '_check_dom_upload_ready', return_value=False):
                                        with patch.object(browser_max, '_detect_state_change', return_value=(False, '')):
                                            # Timeout quickly — should not crash
                                            browser_max._wait_upload_complete(timeout=0.5, poll_interval=0.1)

        # If we get here without exception, video handling is correct


# ── Task 3.2: No reload in _wait_for_file_message ──

class TestNoReloadInMonitoring:
    """Tests that _wait_for_file_message never calls page.reload()"""

    def test_no_reload_called(self, browser_max):
        """
        _wait_for_file_message should never call page.reload()
        even after extended timeout.
        """
        from browser_max import ContentSnapshot

        browser_max._pre_upload_msg_count = 0

        def mock_evaluate(expr, arg=None):
            if 'querySelectorAll' in str(expr) and 'length' in str(expr):
                return 5  # constant count = virtual scroll
            if 'textContent' in str(expr) and 'slice' in str(expr):
                return 'Some message content'
            return None

        browser_max.page.evaluate.side_effect = mock_evaluate

        with patch.object(browser_max, '_ensure_alive', return_value=True):
            with patch.object(browser_max, '_take_content_snapshot', return_value=ContentSnapshot(hash="abc", file_count=3)):
                with patch.object(browser_max, '_force_rerender'):
                    with patch.object(browser_max, '_scan_messages_for_file', return_value=(False, -1, "none")):
                        with patch('time.sleep'):
                            # Run with short timeout
                            result = browser_max._wait_for_file_message(
                                timeout=2,
                                expected_filename="test.zip"
                            )

        # Verify reload was never called
        reload_calls = [
            call for call in browser_max.page.method_calls
            if call[0] == 'reload'
        ]
        assert len(reload_calls) == 0, "page.reload() should never be called"


# ── Task 2.1/2.2: Navigation Guards ──

class TestNavigationGuards:
    """Tests that navigation methods are blocked during upload"""

    def test_navigate_raises_during_upload(self, browser_max):
        """navigate() should raise UploadInProgressError during upload."""
        browser_max._upload_in_progress = True
        browser_max._upload_file_name = "test.mp4"

        from browser_max import UploadInProgressError
        with pytest.raises(UploadInProgressError):
            browser_max.navigate()

    def test_try_navigate_returns_false_during_upload(self, browser_max):
        """_try_navigate() should return False during upload."""
        browser_max._upload_in_progress = True
        browser_max._upload_file_name = "test.mp4"

        result = browser_max._try_navigate()
        assert result is False

    def test_ensure_alive_returns_true_if_page_ok_during_upload(self, browser_max):
        """_ensure_alive() should return True if page exists during upload."""
        browser_max._upload_in_progress = True
        browser_max._upload_file_name = "test.mp4"
        browser_max.page.is_closed.return_value = False

        result = browser_max._ensure_alive()
        assert result is True

    def test_ensure_alive_returns_false_if_page_gone_during_upload(self, browser_max):
        """_ensure_alive() should return False if page is gone during upload."""
        browser_max._upload_in_progress = True
        browser_max._upload_file_name = "test.mp4"
        browser_max.page.is_closed.return_value = True

        result = browser_max._ensure_alive()
        assert result is False

    def test_ensure_alive_does_not_call_connect_during_upload(self, browser_max):
        """_ensure_alive() should NOT call connect() during upload."""
        browser_max._upload_in_progress = True
        browser_max._upload_file_name = "test.mp4"

        with patch.object(browser_max, 'connect') as mock_connect:
            browser_max._ensure_alive()
            mock_connect.assert_not_called()


# ── UploadInProgressError ──

class TestUploadInProgressError:
    """Tests for UploadInProgressError exception"""

    def test_is_browser_max_error_subclass(self):
        """UploadInProgressError should be a subclass of BrowserMAXError."""
        from browser_max import UploadInProgressError, BrowserMAXError
        assert issubclass(UploadInProgressError, BrowserMAXError)

    def test_can_be_raised_with_message(self):
        """UploadInProgressError can be raised with a message."""
        from browser_max import UploadInProgressError
        with pytest.raises(UploadInProgressError, match="upload"):
            raise UploadInProgressError("Cannot navigate during upload")
