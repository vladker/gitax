# -*- coding: utf-8 -*-
"""
Base archiver class for ecosystem package archivers.

Provides common functionality for Cargo, NuGet, RubyGems, PyPI archivers:
- Config loading, output directory management
- Browser initialization and cleanup
- Progress display, signal handling
- Download format, message building templates
- Journal save/close on exit

Subclasses must implement:
- _api_class: The API class (e.g., CratesIOAPI, NuGetAPI)
- _journal_class: The journal class (e.g., CargoJournal)
- _channel_key: Config key for channel URL (e.g., "cargo")
- _section_key: Config section for archiver settings
- _build_message_text(): Build message for a package
- _get_packages(): Fetch packages from API
- _download_package(): Download a single package
"""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING

from logging_config import LogMixin
from browser_init import BrowserInitMixin
from signal_handler import SignalHandler

if TYPE_CHECKING:
    from browser_max import BrowserMAX


class BaseArchiver(LogMixin, BrowserInitMixin):
    """Base class for ecosystem package archivers.

    Subclasses must set:
        _channel_key: str - config key for channel (e.g., "cargo", "pypi")
        _section_key: str | None - config section for archiver settings
        _api_class: type - API class to instantiate
        _journal_class: type - Journal class to instantiate
        _journal_file: str - Journal JSON filename
    """

    _channel_key: str = ""
    _section_key: str | None = None
    _api_class: type | None = None
    _journal_class: type | None = None
    _journal_file: str = ""

    def __init__(self, config_path: str = "config.yaml"):
        from config import init_config, get_config
        init_config(config_path)
        self.config = get_config().model_dump()
        self._init_output_dir()
        self.api = self._init_api()
        self.journal = self._init_journal()
        self.browser: BrowserMAX | None = None
        self._shutdown = False

        SignalHandler().register(self, on_cleanup=self._cleanup)

    def _init_output_dir(self) -> None:
        """Create output directory from config."""
        output_dir = self._get_output_dir()
        if not os.path.exists(output_dir):
            os.makedirs(output_dir, exist_ok=True)

    def _get_output_dir(self) -> str:
        """Get output directory path from config."""
        if self._section_key:
            return self.config.get(self._section_key, {}).get(
                'output_dir', f'./temp_{self._channel_key}')
        return f'./temp_{self._channel_key}'

    def _init_api(self):
        """Initialize the API client."""
        if self._api_class is None:
            raise NotImplementedError("Subclass must set _api_class")
        return self._api_class()

    def _init_journal(self):
        """Initialize the journal."""
        if self._journal_class is None:
            raise NotImplementedError("Subclass must set _journal_class")
        return self._journal_class(self._journal_file)

    def _cleanup(self) -> None:
        """Cleanup on shutdown."""
        if self.browser:
            try:
                self.journal.save()
                self.browser.close()
            except Exception:
                pass

    @staticmethod
    def _format_downloads(count: int) -> str:
        """Format download count for display."""
        if count >= 1_000_000_000:
            return f"{count / 1_000_000_000:.1f}B"
        elif count >= 1_000_000:
            return f"{count / 1_000_000:.1f}M"
        elif count >= 1_000:
            return f"{count / 1_000:.1f}K"
        return str(count)

    @staticmethod
    def _print_progress(current: int, total: int, sent: int, skipped: int,
                        status: str = "") -> None:
        """Print progress bar to console."""
        if total == 0:
            return
        pct = int(current / total * 100)
        filled = int(pct / 10)
        bar = "█" * filled + "░" * (10 - filled)
        print(f"\r  Прогресс: {current}/{total} | {bar} {pct}% | "
              f"✓{sent} | –{skipped} {status}",
              end="", flush=True)
        if current >= total:
            print()

    def _get_channel_url(self) -> str:
        """Get channel URL from config."""
        return self.config.get('channels', {}).get(self._channel_key, '')

    def _get_limit(self) -> int:
        """Get package limit from config."""
        if self._section_key:
            return self.config.get(self._section_key, {}).get('limit', 100)
        return 100

    def _get_delay(self) -> float:
        """Get delay between packages from config."""
        if self._section_key:
            return self.config.get(self._section_key, {}).get('delay', 2)
        return 2
