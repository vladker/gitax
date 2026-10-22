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
    def test_password_never_appears_in_argv(self, mock_get_config, mock_exists, mock_run):
        """Password is passed via a temp file reference, never as a plain -p argument."""
        from backuper import Backuper
        mock_exists.return_value = True
        mock_run.return_value = MagicMock(returncode=0)

        bu = Backuper.__new__(Backuper)
        with patch.object(logging, 'getLogger', return_value=MagicMock()):
            mock_get_config.return_value.backuper.seven_zip_exe = "C:\\Program Files\\7-Zip\\7z.exe"
            result = bu._extract_7z("test.7z", "output_dir", "mypassword")
            assert result is True
            call_args = mock_run.call_args[0][0]
            assert "-pmypassword" not in call_args
            assert not any("mypassword" in str(arg) for arg in call_args)
            assert any(str(arg).startswith("-p@") for arg in call_args)

    @patch('subprocess.run')
    @patch('os.path.exists')
    @patch('config.get_config')
    def test_password_temp_file_removed_after_run(self, mock_get_config, mock_exists, mock_run):
        """The temp file holding the password is deleted once 7z finishes."""
        import os

        from backuper import Backuper
        mock_run.return_value = MagicMock(returncode=0)
        mock_exists.return_value = True

        bu = Backuper.__new__(Backuper)
        with patch.object(logging, 'getLogger', return_value=MagicMock()):
            mock_get_config.return_value.backuper.seven_zip_exe = "C:\\Program Files\\7-Zip\\7z.exe"
            bu._extract_7z("test.7z", "output_dir", "mypassword")

        call_args = mock_run.call_args[0][0]
        pw_arg = next(str(a) for a in call_args if str(a).startswith("-p@"))
        temp_path = pw_arg[len("-p@"):]
        try:
            os.stat(temp_path)
            still_exists = True
        except OSError:
            still_exists = False
        assert not still_exists, "password temp file must be deleted after 7z exits"



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
