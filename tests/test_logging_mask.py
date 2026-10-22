# -*- coding: utf-8 -*-
"""Tests for secret/URL masking helpers in logging_config."""

import json
import logging
import os

import pytest

from logging_config import (
    SecretScrubber,
    SessionCapture,
    mask_secret,
    mask_url,
    scrub_secrets,
    setup_logging,
)


class TestMaskSecret:
    def test_fully_masked_by_default(self):
        assert mask_secret("ghp_abcdefghijklmnopqrstuvwxyz") == "***"

    def test_keep_tail(self):
        assert mask_secret("ghp_abcdefghijklmnopqrstuvwxyz", keep=4) == "***wxyz"

    def test_short_value_fully_masked(self):
        assert mask_secret("abc", keep=4) == "***"

    def test_empty_and_none(self):
        assert mask_secret("") == ""
        assert mask_secret(None) == ""


class TestMaskUrl:
    def test_query_string_dropped(self):
        assert mask_url("https://web.max.ru/?token=abc123") == "https://web.max.ru/?***"

    def test_userinfo_dropped(self):
        masked = mask_url("https://user:pw@example.com/path?x=1#frag")
        assert masked == "https://***@example.com/path?***"

    def test_clean_url_unchanged(self):
        assert mask_url("https://api.github.com/repos/a/b") == "https://api.github.com/repos/a/b"

    def test_empty(self):
        assert mask_url("") == ""
        assert mask_url(None) == ""


class TestScrubSecrets:
    def test_key_value_token_keeps_tail(self):
        assert scrub_secrets("token=abc123def456") == "token=***f456"

    def test_password_fully_masked(self):
        assert scrub_secrets("password=hunter2") == "password=***"

    def test_prefixed_github_token(self):
        out = scrub_secrets("using github_pat_11ABCDEFGH0123456789_RESTOFTOKEN")
        assert "RESTOFTOKEN" not in out
        assert out.startswith("using github_pat_***")

    def test_plain_text_untouched(self):
        assert scrub_secrets("Downloading: https://api.github.com/repos/a/b") == \
               "Downloading: https://api.github.com/repos/a/b"

    def test_empty(self):
        assert scrub_secrets("") == ""


class TestSecretScrubber:
    def test_record_args_are_scrubbed(self):
        record = logging.LogRecord(
            "gitax", logging.INFO, __file__, 1, "token=%s", ("abc123def456",), None
        )
        assert SecretScrubber().filter(record) is True
        assert "abc123def456" not in record.getMessage()
        assert "token=***f456" in record.getMessage()


class TestSetupLoggingScrubber:
    def test_file_log_never_contains_token(self, tmp_path):
        log_file = tmp_path / "archiver.log"
        logger = setup_logging(log_file=str(log_file))
        try:
            logger.info("channel opened with token=supersecretvalue999")
        finally:
            for handler in list(logger.handlers):
                handler.close()
            logger.handlers.clear()

        content = log_file.read_text(encoding="utf-8")
        assert "supersecretvalue999" not in content
        assert "token=***e999" in content


class TestSessionCaptureScrubber:
    def test_session_log_scrubs_password(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        capture = SessionCapture()
        try:
            capture.start()
            print("password=hunter2 repo=owner/name")
        finally:
            capture.stop()

        content = open(os.path.join("logs", os.listdir("logs")[0]), encoding="utf-8").read()
        assert "hunter2" not in content
        assert "password=***" in content
        assert "repo=owner/name" in content
