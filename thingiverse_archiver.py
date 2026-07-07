# -*- coding: utf-8 -*-
"""
Thingiverse Archiver — Downloads 3D models from Thingiverse and sends them to MAX.

Follows the same pattern as PyPILibsArchiver:
  fetch → journal check → download → bundle ZIP → split if large → send to MAX → journal → cleanup
"""

from __future__ import annotations

import glob
import os
import time
import logging
from pathlib import Path
from typing import Any

from config import get_config, init_config
from logging_config import LogMixin
from browser_init import BrowserInitMixin
from browser_max import BrowserConnectionError
from thingiverse_api import ThingiverseAPI
from thingiverse_journal import ThingiverseJournal
from sevenzip import split_file_with_7z
from signal_handler import SignalHandler
from progressbar import LiveProgressBar
from utils import format_file_size


logger = logging.getLogger("gitax")


class ThingiverseArchiver(LogMixin, BrowserInitMixin):
    """Download 3D models from Thingiverse and send them to MAX channel."""

    _channel_key = "thingiverse"
    _section_key = "thingiverse_archiver"

    def __init__(self, config_path: str = "config.yaml") -> None:
        """Initialize archiver with config and dependencies."""
        init_config(config_path)
        app_config = get_config()

        # Config dict for BrowserInitMixin compatibility
        self.config: dict[str, Any] = app_config.model_dump()

        # Thingiverse-specific config
        ta_config = app_config.thingiverse_archiver
        self.limit = ta_config.limit
        self.retries = ta_config.retries
        self.retry_delay = ta_config.retry_delay
        self.split_mode = ta_config.split_mode
        self.output_dir = Path(ta_config.output_dir)

        # Shared config for split threshold
        self.split_threshold_mb = app_config.archiver.split_threshold_mb

        # Ensure output directory exists
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Dependencies
        self.api = ThingiverseAPI()
        self.journal = ThingiverseJournal()
        self.browser = None

        # State
        self._shutdown = False
        self._signal_handler = SignalHandler()
        self._signal_handler.register(self)

        self.log_info("ThingiverseArchiver initialized (limit=%d)", self.limit)

    # ── Public entry points ──────────────────────────────────

    def run_popular(self, weeks: int = 4) -> None:
        """Fetch and archive popular things from Thingiverse.

        Args:
            weeks: Number of weeks to look back (default: 4).
        """
        self.log_info("Fetching popular things (last %d weeks)...", weeks)

        try:
            things = self.api.get_popular(weeks=weeks)
        except Exception as e:
            self.log_error("Failed to fetch popular things: %s", e)
            return

        if not things:
            self.log_warning("No popular things found.")
            return

        # Apply limit
        if len(things) > self.limit:
            things = things[:self.limit]

        self.log_info("Fetched %d popular things", len(things))
        self._process_things(things, source="popular")

    def run_by_tag(self, tag: str) -> None:
        """Fetch and archive things by tag.

        Args:
            tag: Tag name to search for.
        """
        self.log_info("Fetching things tagged '%s'...", tag)

        try:
            things = self.api.get_by_tag(tag)
        except Exception as e:
            self.log_error("Failed to fetch things by tag '%s': %s", tag, e)
            return

        if not things:
            self.log_warning("No things found for tag '%s'.", tag)
            return

        # Apply limit
        if len(things) > self.limit:
            things = things[:self.limit]

        self.log_info("Fetched %d things for tag '%s'", len(things), tag)
        self._process_things(things, source=f"tag:{tag}")

    def run_by_category(self, category: str) -> None:
        """Fetch and archive things by category.

        Args:
            category: Category name to search for.
        """
        self.log_info("Fetching things in category '%s'...", category)

        try:
            things = self.api.get_by_category(category)
        except Exception as e:
            self.log_error("Failed to fetch things by category '%s': %s", category, e)
            return

        if not things:
            self.log_warning("No things found for category '%s'.", category)
            return

        # Apply limit
        if len(things) > self.limit:
            things = things[:self.limit]

        self.log_info("Fetched %d things for category '%s'", len(things), category)
        self._process_things(things, source=f"category:{category}")

    def run_by_author(self, author: str) -> None:
        """Fetch and archive things by author.

        Args:
            author: Author/username to search for.
        """
        self.log_info("Fetching things by author '%s'...", author)

        try:
            things = self.api.get_by_author(author)
        except Exception as e:
            self.log_error("Failed to fetch things by author '%s': %s", author, e)
            return

        if not things:
            self.log_warning("No things found for author '%s'.", author)
            return

        # Apply limit
        if len(things) > self.limit:
            things = things[:self.limit]

        self.log_info("Fetched %d things for author '%s'", len(things), author)
        self._process_things(things, source=f"author:{author}")

    # ── Core processing pipeline ─────────────────────────────

    def _process_things(self, things: list[dict], source: str = "") -> None:
        """Process a list of things: download, send, journal, cleanup.

        Args:
            things: List of thing dicts from the API.
            source: Source label for logging (e.g., "popular", "tag:electronics").
        """
        total = len(things)
        skipped = 0
        processed = 0
        failed = 0

        with LiveProgressBar(total=total, label=f"Thingiverse ({source})") as bar:
            for idx, thing in enumerate(things, 1):
                if self._shutdown:
                    self.log_info("Shutdown requested, stopping after current item.")
                    break

                thing_id = thing.get("id", thing.get("thing_id", f"unknown_{idx}"))
                thing_name = thing.get("name", thing.get("title", f"Thing #{thing_id}"))

                bar.update(idx, item_name=thing_name)

                # 1. Check journal — skip if already processed
                if self.journal.is_processed(thing_id):
                    self.log_debug("Skipping already processed: %s", thing_name)
                    skipped += 1
                    continue

                # 2. Download
                zip_path = self._download_thing(thing_id, thing_name)
                if not zip_path or not os.path.exists(zip_path):
                    self.log_warning("Download failed for %s", thing_name)
                    self.journal.mark_failed(thing_id, thing_name, error="download_failed")
                    failed += 1
                    continue

                # 3. Check size → split if needed
                files_to_send = self._prepare_files(zip_path, thing_name)
                if not files_to_send:
                    self.log_warning("No files to send for %s", thing_name)
                    self.journal.mark_failed(thing_id, thing_name, error="no_files")
                    self._cleanup_path(zip_path)
                    failed += 1
                    continue

                # 4. Send each file to MAX
                send_ok = self._send_files(files_to_send, thing_name)

                # 5. Journal result
                if send_ok:
                    self.journal.mark_sent(thing_id, thing_name, files=[os.path.basename(f) for f in files_to_send])
                    processed += 1
                else:
                    self.journal.mark_failed(thing_id, thing_name, error="send_failed")
                    failed += 1

                # 6. Cleanup temp files
                self._cleanup_path(zip_path)
                self._cleanup_split_files(zip_path)

        # Summary
        self.log_info(
            "Done: %d processed, %d skipped, %d failed (of %d total)",
            processed, skipped, failed, total,
        )

    def _download_thing(self, thing_id: str, thing_name: str) -> str | None:
        """Download a thing from Thingiverse.

        Returns:
            Path to downloaded ZIP file, or None on failure.
        """
        for attempt in range(1, self.retries + 1):
            try:
                self.log_info("Downloading: %s (attempt %d/%d)", thing_name, attempt, self.retries)
                zip_path = self.api.download_thing(thing_id, output_dir=str(self.output_dir))
                if zip_path and os.path.exists(zip_path):
                    size = os.path.getsize(zip_path)
                    self.log_info("Downloaded: %s (%s)", thing_name, format_file_size(size))
                    return zip_path
                else:
                    self.log_warning("Download returned no file for %s", thing_name)
            except Exception as e:
                self.log_warning("Download error (%d/%d): %s — %s", attempt, self.retries, thing_name, e)

            if attempt < self.retries:
                time.sleep(self.retry_delay)

        return None

    def _prepare_files(self, zip_path: str, thing_name: str) -> list[str]:
        """Check file size, split if needed. Returns list of file paths to send.

        Args:
            zip_path: Path to the downloaded ZIP.
            thing_name: Human-readable name for logging.

        Returns:
            List of file paths (original ZIP or split volumes).
        """
        file_size = os.path.getsize(zip_path)
        threshold_bytes = self.split_threshold_mb * 1024 * 1024

        if file_size <= threshold_bytes:
            return [zip_path]

        # Need to split
        self.log_info(
            "%s is %s (threshold %s MB), splitting...",
            thing_name, format_file_size(file_size), self.split_threshold_mb,
        )

        volumes = split_file_with_7z(zip_path)
        if volumes:
            self.log_info("Split into %d volumes", len(volumes))
            return volumes

        # Split failed — send original file
        self.log_warning("Split failed for %s, sending original ZIP", thing_name)
        return [zip_path]

    def _send_files(self, file_paths: list[str], thing_name: str) -> bool:
        """Send files to MAX channel.

        Args:
            file_paths: List of file paths to send.
            thing_name: Human-readable name for logging.

        Returns:
            True if all files sent successfully.
        """
        try:
            browser = self._ensure_browser_connected()
        except BrowserConnectionError as e:
            self.log_error("Browser connection failed: %s", e)
            return False
        except Exception as e:
            self.log_error("Browser error: %s", e)
            return False

        # Build message text
        message = f"📦 {thing_name}"

        for i, file_path in enumerate(file_paths):
            if self._shutdown:
                self.log_info("Shutdown requested during send.")
                return False

            filename = os.path.basename(file_path)
            vol_label = f" ({i + 1}/{len(file_paths)})" if len(file_paths) > 1 else ""
            self.log_info("Sending%s: %s", vol_label, filename)

            for attempt in range(1, self.retries + 1):
                try:
                    ok = browser.send_file_with_message(file_path, message)
                    if ok:
                        self.log_info("Sent successfully%s", vol_label)
                        break
                except Exception as e:
                    self.log_warning("Send error (%d/%d): %s", attempt, self.retries, e)

                if attempt < self.retries:
                    time.sleep(self.retry_delay)
            else:
                self.log_error("Failed to send %s after %d retries", filename, self.retries)
                return False

        return True

    # ── Cleanup helpers ──────────────────────────────────────

    def _cleanup_path(self, path: str) -> None:
        """Remove a single file."""
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception as e:
            self.log_debug("Cleanup warning for %s: %s", path, e)

    def _cleanup_split_files(self, zip_path: str) -> None:
        """Remove any split volume files derived from the original ZIP."""
        base = zip_path + ".7z"
        for vol in glob.glob(base + ".*"):
            self._cleanup_path(vol)

    def close(self) -> None:
        """Cleanup resources."""
        self._close_browser()
        self.log_info("ThingiverseArchiver closed.")
