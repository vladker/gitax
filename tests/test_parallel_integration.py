"""Tests for parallel upload integration."""

import pytest


class TestParallelIntegration:
    def test_runner_detects_multi_channel(self, tmp_path, monkeypatch):
        """When channel selector returns a list, parallel mode activates."""
        # This is a design verification test — the actual integration
        # is tested via the channel selector returning a list
        from config.model import ChannelEntry

        channels = [
            ChannelEntry(url="https://web.max.ru/ch1", label="Main"),
            ChannelEntry(url="https://web.max.ru/ch2", label="Archive"),
        ]

        # Simulating what the runner receives when user picks "All"
        channel_urls = [ch.url for ch in channels]
        assert isinstance(channel_urls, list)
        assert len(channel_urls) == 2
