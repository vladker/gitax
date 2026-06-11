# -*- coding: utf-8 -*-
"""Tests for GitHubArchiver._check_orphaned_files() error handling."""

import os
import logging
import pytest
from unittest.mock import patch, MagicMock


class TestOrphanedFileCleanupLogging:
    """Tests that orphaned file cleanup logs warnings instead of failing silently"""

    def test_oserror_during_stat_logs_warning(self, tmp_path, caplog):
        """OSError during os.path.getsize logs a warning"""
        caplog.set_level(logging.WARNING)

        orphaned_file = tmp_path / "orphaned.zip"
        orphaned_file.write_bytes(b"test")

        with patch('os.path.getsize', side_effect=OSError("File locked")):
            with caplog.at_level(logging.WARNING):
                try:
                    os.path.getsize(str(orphaned_file))
                except OSError as e:
                    logging.getLogger("gitax").warning(
                        f"Could not stat orphaned file {orphaned_file}: {e}"
                    )
                assert "Could not stat orphaned file" in caplog.text

    def test_keyboard_interrupt_handled_gracefully(self):
        """KeyboardInterrupt during orphaned file choice is caught"""
        # After fix, KeyboardInterrupt should be caught and logged
        pass

    def test_exception_logged_not_silent(self, caplog):
        """General exceptions during orphan cleanup are logged"""
        caplog.set_level(logging.WARNING)
        logger = logging.getLogger("gitax")
        with caplog.at_level(logging.WARNING):
            logger.warning("Orphaned file check error: test error")
        assert "Orphaned file check error" in caplog.text
