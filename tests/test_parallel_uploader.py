# -*- coding: utf-8 -*-
"""Tests for ParallelGroupUploader with mock browser."""

import os
import pytest
import threading
from parallel_uploader import ParallelGroupUploader, UploadSummary, ChannelResult


class MockBrowserMAX:
    """Mock BrowserMAX for testing parallel uploads."""
    def __init__(self, channel_url, use_local_browser=False):
        self.channel_url = channel_url
        self.uploaded_files = []
        self.should_fail = False

    def keep_alive_connect(self):
        return True

    def navigate(self):
        pass

    def ensure_page_ready(self):
        pass

    def send_message_with_file(self, text="", filepath="", **kwargs):
        if self.should_fail:
            return False, "Mock failure"
        self.uploaded_files.append(filepath)
        return True, "OK"


class TestParallelGroupUploader:
    def test_single_channel_upload(self, tmp_path):
        """Single channel uploads files normally."""
        test_file = tmp_path / "test.zip"
        test_file.write_bytes(b"test content")

        uploader = ParallelGroupUploader(
            files=[str(test_file)],
            channels=[
                {"url": "https://web.max.ru/ch1", "label": "Channel 1"}
            ],
            cleanup=False,
            stagger_delay_sec=0.01,
        )

        summary = uploader.run(mock_browser_class=MockBrowserMAX)

        assert summary.channel_results["Channel 1"].success is True
        assert len(summary.channel_results["Channel 1"].files) == 1

    def test_parallel_upload_to_multiple_channels(self, tmp_path):
        """Files uploaded to all channels in parallel."""
        test_file = tmp_path / "test.zip"
        test_file.write_bytes(b"test content")

        uploader = ParallelGroupUploader(
            files=[str(test_file)],
            channels=[
                {"url": "https://web.max.ru/ch1", "label": "Channel 1"},
                {"url": "https://web.max.ru/ch2", "label": "Channel 2"},
            ],
            cleanup=False,
            stagger_delay_sec=0.01,
        )

        summary = uploader.run(mock_browser_class=MockBrowserMAX)

        assert summary.channel_results["Channel 1"].success is True
        assert summary.channel_results["Channel 2"].success is True

    def test_partial_failure_keeps_file(self, tmp_path):
        """When some channels fail, file is still deleted if >=1 succeeded."""
        test_file = tmp_path / "test.zip"
        test_file.write_bytes(b"test content")

        def make_browser(url, **kwargs):
            browser = MockBrowserMAX(url, **kwargs)
            if "ch2" in url:
                browser.should_fail = True
            return browser

        uploader = ParallelGroupUploader(
            files=[str(test_file)],
            channels=[
                {"url": "https://web.max.ru/ch1", "label": "Channel 1"},
                {"url": "https://web.max.ru/ch2", "label": "Channel 2"},
            ],
            cleanup=True,
            stagger_delay_sec=0.01,
        )

        summary = uploader.run(mock_browser_class=make_browser)

        assert summary.channel_results["Channel 1"].success is True
        assert summary.channel_results["Channel 2"].success is False
        assert not test_file.exists()

    def test_all_failure_keeps_file(self, tmp_path):
        """When ALL channels fail, file is preserved."""
        test_file = tmp_path / "test.zip"
        test_file.write_bytes(b"test content")

        class FailingBrowser(MockBrowserMAX):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.should_fail = True

        uploader = ParallelGroupUploader(
            files=[str(test_file)],
            channels=[
                {"url": "https://web.max.ru/ch1", "label": "Channel 1"},
                {"url": "https://web.max.ru/ch2", "label": "Channel 2"},
            ],
            cleanup=True,
            stagger_delay_sec=0.01,
        )

        summary = uploader.run(mock_browser_class=FailingBrowser)

        assert summary.total_failed == 2
        assert test_file.exists()

    def test_stagger_delay_between_threads(self, tmp_path):
        """Threads start with configured stagger delay."""
        test_file = tmp_path / "test.zip"
        test_file.write_bytes(b"test content")

        start_times = []

        class TimingMockBrowser(MockBrowserMAX):
            def keep_alive_connect(self):
                start_times.append(threading.get_ident())
                return True

        uploader = ParallelGroupUploader(
            files=[str(test_file)],
            channels=[
                {"url": "https://web.max.ru/ch1", "label": "Channel 1"},
                {"url": "https://web.max.ru/ch2", "label": "Channel 2"},
                {"url": "https://web.max.ru/ch3", "label": "Channel 3"},
            ],
            stagger_delay_sec=0.1,
            cleanup=False,
        )

        summary = uploader.run(mock_browser_class=TimingMockBrowser)

        assert len(start_times) == 3
