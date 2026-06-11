# -*- coding: utf-8 -*-
"""Tests for Backuper._extract_7z() password handling."""

import logging
import subprocess as sp
from unittest.mock import patch, MagicMock

import pytest


class TestExtract7zPassword:
    """Tests for _extract_7z password security"""

    @patch('subprocess.run')
    @patch('os.path.exists')
    @patch('config.get_config')
    def test_password_passed_via_cli_not_file(self, mock_get_config, mock_exists, mock_run):
        """Password is passed via -p flag, not via temp file"""
        from backuper import Backuper
        mock_exists.return_value = True
        mock_run.return_value = MagicMock(returncode=0)

        bu = Backuper.__new__(Backuper)
        with patch.object(logging, 'getLogger', return_value=MagicMock()):
            mock_get_config.return_value.backuper.seven_zip_exe = "C:\\Program Files\\7-Zip\\7z.exe"
            result = bu._extract_7z("test.7z", "output_dir", "mypassword")
            assert result is True
            call_args = mock_run.call_args[0][0]
            assert "-pmypassword" in call_args

    @patch('subprocess.run')
    @patch('os.path.exists')
    @patch('config.get_config')
    def test_no_temp_file_created(self, mock_get_config, mock_exists, mock_run):
        """After fix, no temp file is created for password"""
        from backuper import Backuper
        mock_exists.return_value = True
        mock_run.return_value = MagicMock(returncode=0)

        bu = Backuper.__new__(Backuper)

        named_temp_called = False
        original_named_temp = None
        try:
            import tempfile
            original_named_temp = tempfile.NamedTemporaryFile
            def mock_named_temp(*args, **kwargs):
                nonlocal named_temp_called
                named_temp_called = True
                return original_named_temp(*args, **kwargs)
            tempfile.NamedTemporaryFile = mock_named_temp
        except Exception:
            pass

        with patch.object(logging, 'getLogger', return_value=MagicMock()):
            mock_get_config.return_value.backuper.seven_zip_exe = "C:\\Program Files\\7-Zip\\7z.exe"
            bu._extract_7z("test.7z", "output_dir", "mypassword")

        assert not named_temp_called, "NamedTemporaryFile should NOT be called after fix"

    @patch('subprocess.run')
    @patch('os.path.exists')
    @patch('config.get_config')
    def test_extract_without_password(self, mock_get_config, mock_exists, mock_run):
        """Extract without password works normally"""
        from backuper import Backuper
        mock_exists.return_value = True
        mock_run.return_value = MagicMock(returncode=0)

        bu = Backuper.__new__(Backuper)
        with patch.object(logging, 'getLogger', return_value=MagicMock()):
            mock_get_config.return_value.backuper.seven_zip_exe = "C:\\Program Files\\7-Zip\\7z.exe"
            result = bu._extract_7z("test.7z", "output_dir", None)
            assert result is True
            call_args = mock_run.call_args[0][0]
            assert not any(arg.startswith("-p") for arg in call_args)

    @patch('subprocess.run')
    @patch('os.path.exists')
    @patch('config.get_config')
    def test_extract_returns_false_on_error(self, mock_get_config, mock_exists, mock_run):
        """Returns False when 7z fails"""
        from backuper import Backuper
        mock_exists.return_value = True
        mock_run.return_value = MagicMock(returncode=1)

        bu = Backuper.__new__(Backuper)
        with patch.object(logging, 'getLogger', return_value=MagicMock()):
            mock_get_config.return_value.backuper.seven_zip_exe = "C:\\Program Files\\7-Zip\\7z.exe"
            result = bu._extract_7z("test.7z", "output_dir")
            assert result is False

    @patch('subprocess.run')
    @patch('os.path.exists')
    @patch('config.get_config')
    def test_extract_returns_false_on_timeout(self, mock_get_config, mock_exists, mock_run):
        """Returns False on timeout"""
        from backuper import Backuper
        mock_exists.return_value = True
        mock_run.side_effect = sp.TimeoutExpired("7z", 7200)

        bu = Backuper.__new__(Backuper)
        with patch.object(logging, 'getLogger', return_value=MagicMock()):
            mock_get_config.return_value.backuper.seven_zip_exe = "C:\\Program Files\\7-Zip\\7z.exe"
            result = bu._extract_7z("test.7z", "output_dir")
            assert result is False

    @patch('os.path.exists')
    @patch('config.get_config')
    def test_seven_zip_not_found(self, mock_get_config, mock_exists):
        """Returns False when 7z executable is missing"""
        from backuper import Backuper
        mock_exists.return_value = False

        bu = Backuper.__new__(Backuper)
        with patch.object(logging, 'getLogger', return_value=MagicMock()):
            mock_get_config.return_value.backuper.seven_zip_exe = "C:\\Program Files\\7-Zip\\7z.exe"
            result = bu._extract_7z("test.7z", "output_dir")
            assert result is False
