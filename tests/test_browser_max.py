# -*- coding: utf-8 -*-
"""
Tests for BrowserMAX split_mode and _prompt_split_mode.

Tests cover:
- _prompt_split_mode() valid/invalid input handling
- _prompt_split_volume_size() custom size parsing
- send_message_with_files() with split_mode: auto, on, off, prompt
- Backward compatibility - no split_mode param still works
"""

import os
import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from browser_max import BrowserMAX, SEVEN_ZIP_VOLUME_SIZE


# ── _prompt_split_mode tests ──

class TestPromptSplitMode:
    """Test _prompt_split_mode interactive prompt"""

    def test_choice_1_no_split(self):
        """Input '1' returns 1."""
        bm = BrowserMAX("https://example.com")
        with patch("builtins.input", return_value="1"):
            result = bm._prompt_split_mode("test.zip", 100.0)
        assert result == 1

    def test_choice_2_split_default(self):
        """Input '2' returns 2."""
        bm = BrowserMAX("https://example.com")
        with patch("builtins.input", return_value="2"):
            result = bm._prompt_split_mode("test.zip", 100.0)
        assert result == 2

    def test_choice_3_custom_size(self):
        """Input '3' returns 3."""
        bm = BrowserMAX("https://example.com")
        with patch("builtins.input", return_value="3"):
            result = bm._prompt_split_mode("test.zip", 100.0)
        assert result == 3

    def test_invalid_then_valid_input(self):
        """Invalid input is rejected, then valid input is accepted."""
        bm = BrowserMAX("https://example.com")
        inputs = iter(["4", "abc", "", "2"])
        with patch("builtins.input", side_effect=inputs):
            result = bm._prompt_split_mode("test.zip", 100.0)
        assert result == 2

    def test_keyboard_interrupt_defaults_to_1(self):
        """KeyboardInterrupt during input returns 1 (safe default)."""
        bm = BrowserMAX("https://example.com")
        with patch("builtins.input", side_effect=KeyboardInterrupt()):
            result = bm._prompt_split_mode("test.zip", 100.0)
        assert result == 1

    def test_eoferror_defaults_to_1(self):
        """EOFError during input returns 1 (safe default)."""
        bm = BrowserMAX("https://example.com")
        with patch("builtins.input", side_effect=EOFError()):
            result = bm._prompt_split_mode("test.zip", 100.0)
        assert result == 1

    def test_displays_filename_and_size(self):
        """Prompt output includes filename and file size."""
        bm = BrowserMAX("https://example.com")
        with patch("builtins.print") as mock_print:
            with patch("builtins.input", return_value="1"):
                bm._prompt_split_mode("large_repo.zip", 150.5)
            printed_text = "".join(
                call[0][0] if call[0] else "" for call in mock_print.call_args_list
            )
            assert "large_repo.zip" in printed_text
            assert "150.5 MB" in printed_text


# ── _prompt_split_volume_size tests ──

class TestPromptSplitVolumeSize:
    """Test _prompt_split_volume_size custom size prompt"""

    def test_bare_number_adds_mb(self):
        """Bare number like '100' is converted to '100M'."""
        bm = BrowserMAX("https://example.com")
        with patch("builtins.input", return_value="100"):
            result = bm._prompt_split_volume_size()
        assert result == "100M"

    def test_size_with_g_suffix(self):
        """Size with 'G' suffix is kept as-is."""
        bm = BrowserMAX("https://example.com")
        with patch("builtins.input", return_value="1G"):
            result = bm._prompt_split_volume_size()
        assert result == "1G"

    def test_size_with_m_suffix(self):
        """Size with 'M' suffix is kept as-is."""
        bm = BrowserMAX("https://example.com")
        with patch("builtins.input", return_value="49M"):
            result = bm._prompt_split_volume_size()
        assert result == "49M"

    def test_empty_input_returns_none(self):
        """Empty/Enter input returns None (cancelled)."""
        bm = BrowserMAX("https://example.com")
        with patch("builtins.input", return_value=""):
            result = bm._prompt_split_volume_size()
        assert result is None

    def test_invalid_then_valid(self):
        """Invalid input loops, then valid input is accepted."""
        bm = BrowserMAX("https://example.com")
        inputs = iter(["abc", "XYZ", "200M"])
        with patch("builtins.input", side_effect=inputs):
            result = bm._prompt_split_volume_size()
        assert result == "200M"

    def test_keyboard_interrupt_returns_none(self):
        """KeyboardInterrupt returns None."""
        bm = BrowserMAX("https://example.com")
        with patch("builtins.input", side_effect=KeyboardInterrupt()):
            result = bm._prompt_split_volume_size()
        assert result is None


# ── send_message_with_files split_mode tests ──

class TestSendMessageWithFilesSplitMode:
    """Test send_message_with_files with different split_mode values"""

    def setup_browser(self):
        """Create BrowserMAX with all heavy dependencies mocked."""
        bm = BrowserMAX("https://example.com")
        bm.page = MagicMock()
        bm._connected = True
        bm.connect = MagicMock(return_value=True)
        bm.page.evaluate.return_value = 0
        bm._find_message_input = MagicMock(return_value=MagicMock())
        bm._type_message = MagicMock()
        bm._send_message = MagicMock()
        bm._upload_single_file = MagicMock(return_value=True)
        return bm

    @patch("browser_max.os.path.exists", return_value=True)
    @patch("browser_max.split_file_with_7z", return_value=[])
    def test_split_mode_off_never_splits(self, mock_split, mock_exists):
        """split_mode='off' never calls split_file_with_7z even for large files."""
        bm = self.setup_browser()
        with patch("browser_max.os.path.getsize", return_value=100 * 1024 * 1024):
            success, _ = bm.send_message_with_files(
                text="test",
                filepaths=["large.zip"],
                split_mode="off",
            )
        mock_split.assert_not_called()

    @patch("browser_max.os.path.exists", return_value=True)
    @patch("browser_max.split_file_with_7z", return_value=["part1.7z.001"])
    def test_split_mode_on_always_splits(self, mock_split, mock_exists):
        """split_mode='on' calls split_file_with_7z even for small files."""
        bm = self.setup_browser()
        with patch("browser_max.os.path.getsize", return_value=1024):
            success, _ = bm.send_message_with_files(
                text="test",
                filepaths=["small.zip"],
                split_mode="on",
            )
        mock_split.assert_called_once()

    @patch("browser_max.os.path.exists", return_value=True)
    @patch("browser_max.split_file_with_7z", return_value=[])
    def test_split_mode_auto_no_split_below_threshold(self, mock_split, mock_exists):
        """split_mode='auto' does NOT split files below threshold."""
        bm = self.setup_browser()
        with patch("browser_max.os.path.getsize", return_value=10 * 1024 * 1024):
            success, _ = bm.send_message_with_files(
                text="test",
                filepaths=["small.zip"],
                split_mode="auto",
                split_threshold_mb=49.0,
            )
        mock_split.assert_not_called()

    @patch("browser_max.os.path.exists", return_value=True)
    @patch("browser_max.split_file_with_7z", return_value=["part1.7z.001", "part2.7z.002"])
    def test_split_mode_auto_splits_above_threshold(self, mock_split, mock_exists):
        """split_mode='auto' splits files above threshold."""
        bm = self.setup_browser()
        with patch("browser_max.os.path.getsize", return_value=100 * 1024 * 1024):
            success, _ = bm.send_message_with_files(
                text="test",
                filepaths=["large.zip"],
                split_mode="auto",
                split_threshold_mb=49.0,
            )
        mock_split.assert_called_once()

    @patch("browser_max.os.path.exists", return_value=True)
    @patch("browser_max.split_file_with_7z", return_value=[])
    def test_split_mode_prompt_choice_1_no_split(self, mock_split, mock_exists):
        """split_mode='prompt' with user choosing 1 does NOT split."""
        bm = self.setup_browser()
        bm._prompt_split_mode = MagicMock(return_value=1)
        with patch("browser_max.os.path.getsize", return_value=100 * 1024 * 1024):
            success, _ = bm.send_message_with_files(
                text="test",
                filepaths=["large.zip"],
                split_mode="prompt",
            )
        mock_split.assert_not_called()

    @patch("browser_max.os.path.exists", return_value=True)
    @patch("browser_max.split_file_with_7z", return_value=["part1.7z.001"])
    def test_split_mode_prompt_choice_2_splits_default(self, mock_split, mock_exists):
        """split_mode='prompt' with user choosing 2 splits with default size."""
        bm = self.setup_browser()
        bm._prompt_split_mode = MagicMock(return_value=2)
        with patch("browser_max.os.path.getsize", return_value=100 * 1024 * 1024):
            success, _ = bm.send_message_with_files(
                text="test",
                filepaths=["large.zip"],
                split_mode="prompt",
            )
        mock_split.assert_called_once()
        # Verify default volume size is used
        call_args = mock_split.call_args[0]
        assert len(call_args) == 2
        assert call_args[1] == SEVEN_ZIP_VOLUME_SIZE

    @patch("browser_max.os.path.exists", return_value=True)
    @patch("browser_max.split_file_with_7z", return_value=["part1.7z.001"])
    def test_split_mode_prompt_choice_3_custom_size(self, mock_split, mock_exists):
        """split_mode='prompt' with user choosing 3 splits with custom size."""
        bm = self.setup_browser()
        bm._prompt_split_mode = MagicMock(return_value=3)
        bm._prompt_split_volume_size = MagicMock(return_value="100M")
        with patch("browser_max.os.path.getsize", return_value=500 * 1024 * 1024):
            success, _ = bm.send_message_with_files(
                text="test",
                filepaths=["large.zip"],
                split_mode="prompt",
            )
        mock_split.assert_called_once()
        call_args = mock_split.call_args[0]
        assert len(call_args) == 2
        assert call_args[1] == "100M"

    @patch("browser_max.os.path.exists", return_value=True)
    @patch("browser_max.split_file_with_7z", return_value=[])
    def test_split_mode_prompt_choice_3_cancelled(self, mock_split, mock_exists):
        """split_mode='prompt' with user cancelling option 3 does NOT split."""
        bm = self.setup_browser()
        bm._prompt_split_mode = MagicMock(return_value=3)
        bm._prompt_split_volume_size = MagicMock(return_value=None)
        with patch("browser_max.os.path.getsize", return_value=500 * 1024 * 1024):
            success, _ = bm.send_message_with_files(
                text="test",
                filepaths=["large.zip"],
                split_mode="prompt",
            )
        mock_split.assert_not_called()

    @patch("browser_max.os.path.exists", return_value=True)
    @patch("browser_max.split_file_with_7z", return_value=["part1.7z.001"])
    def test_backward_compatibility_default_split_mode(self, mock_split, mock_exists):
        """Not passing split_mode defaults to 'auto' (threshold check)."""
        bm = self.setup_browser()
        with patch("browser_max.os.path.getsize", return_value=100 * 1024 * 1024):
            success, _ = bm.send_message_with_files(
                text="test",
                filepaths=["large.zip"],
                split_threshold_mb=49.0,
            )
        mock_split.assert_called_once()

    @patch("browser_max.os.path.exists", return_value=True)
    @patch("browser_max.split_file_with_7z", return_value=[])
    def test_backward_compatibility_small_file(self, mock_split, mock_exists):
        """Without split_mode, small files below threshold are not split."""
        bm = self.setup_browser()
        with patch("browser_max.os.path.getsize", return_value=1024):
            success, _ = bm.send_message_with_files(
                text="test",
                filepaths=["small.zip"],
                split_threshold_mb=49.0,
            )
        mock_split.assert_not_called()

    @patch("browser_max.os.path.exists", return_value=True)
    @patch("browser_max.split_file_with_7z", return_value=[])
    def test_unknown_split_mode_falls_back_to_auto(self, mock_split, mock_exists):
        """Unknown split_mode value defaults to auto behavior."""
        bm = self.setup_browser()
        with patch("browser_max.os.path.getsize", return_value=10 * 1024 * 1024):
            success, _ = bm.send_message_with_files(
                text="test",
                filepaths=["small.zip"],
                split_mode="unknown_value",
            )
        mock_split.assert_not_called()


# ── send_message_with_file split_mode passthrough ──

class TestSendMessageWithFileSplitMode:
    """Test send_message_with_file passes split_mode to send_message_with_files"""

    def test_passes_split_mode_to_send_message_with_files(self):
        """send_message_with_file delegates split_mode to send_message_with_files."""
        bm = BrowserMAX("https://example.com")
        with patch.object(bm, "send_message_with_files", return_value=(True, True)) as mock_smwf:
            with patch("browser_max.os.path.exists", return_value=True):
                bm.send_message_with_file(
                    text="test",
                    filepath="/path/to/file.zip",
                    split_mode="off",
                )
            assert mock_smwf.call_count == 1
            kwargs = mock_smwf.call_args[1]
            assert kwargs.get("split_mode") == "off"

    def test_defaults_to_auto_when_not_specified(self):
        """send_message_with_file defaults split_mode to 'auto'."""
        bm = BrowserMAX("https://example.com")
        with patch.object(bm, "send_message_with_files", return_value=(True, True)) as mock_smwf:
            with patch("browser_max.os.path.exists", return_value=True):
                bm.send_message_with_file(
                    text="test",
                    filepath="/path/to/file.zip",
                )
            kwargs = mock_smwf.call_args[1]
            assert kwargs.get("split_mode") == "auto"
