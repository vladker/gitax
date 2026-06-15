"""ParallelGroupUploader — upload same files to multiple channels in parallel."""

from __future__ import annotations

import os
import time
import threading
import logging
from dataclasses import dataclass, field
from typing import Any

from retry import retry

_logger = logging.getLogger("gitax")


@dataclass
class ChannelResult:
    """Result of uploading to a single channel."""
    label: str
    success: bool
    files: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class UploadSummary:
    """Summary of parallel upload results."""
    channel_results: dict[str, ChannelResult] = field(default_factory=dict)
    total_files: int = 0
    total_success: int = 0
    total_failed: int = 0


class ParallelGroupUploader:
    """Upload files to multiple channels in parallel using threading."""

    def __init__(
        self,
        files: list[str],
        channels: list[dict[str, str]],
        cleanup: bool = True,
        stagger_delay_sec: float = 2.0,
        max_concurrent: int = 5,
        journal: Any = None,
    ):
        self.files = files
        self.channels = channels
        self.cleanup = cleanup
        self.stagger_delay_sec = stagger_delay_sec
        self.max_concurrent = max(1, min(max_concurrent, len(channels)))
        self.journal = journal

        self._results_lock = threading.Lock()
        self._results: dict[str, ChannelResult] = {}
        self._channel_failures: dict[str, bool] = {}

    def run(self, mock_browser_class: type = None) -> UploadSummary:
        """Execute parallel upload."""
        _logger.info(
            f"ParallelGroupUploader: {len(self.files)} file(s) -> "
            f"{len(self.channels)} channel(s)"
        )

        threads = []
        semaphore = threading.Semaphore(self.max_concurrent)

        for channel_info in self.channels:
            t = threading.Thread(
                target=self._upload_to_channel,
                args=(channel_info, semaphore, mock_browser_class),
                name=f"upload-{channel_info.get('label', 'unknown')}",
                daemon=False,
            )
            threads.append(t)
            time.sleep(self.stagger_delay_sec)

        for t in threads:
            t.start()

        for t in threads:
            t.join(timeout=300)
            if t.is_alive():
                _logger.warning(
                    f"Thread {t.name} did not complete within 300s timeout"
                )

        summary = self._build_summary()

        if self.cleanup:
            self._cleanup_files(summary)

        return summary

    @retry(max_retries=3, delay=5.0, backoff=1.0, exceptions=(RuntimeError,))
    def _upload_single_file(
        self,
        browser,
        filepath: str,
        label: str,
    ) -> None:
        """Upload a single file with retry support."""
        filename = os.path.basename(filepath)
        success, msg = browser.send_message_with_file(
            text=f"📦 {filename}",
            filepath=filepath,
            retries=1,
            retry_delay=5,
        )
        if not success:
            raise RuntimeError(f"[{label}] Upload failed: {msg}")

    def _upload_to_channel(
        self,
        channel_info: dict[str, str],
        semaphore: threading.Semaphore,
        mock_browser_class: type = None,
    ) -> None:
        """Upload all files to a single channel."""
        label = channel_info.get("label", channel_info.get("url", "Unknown"))
        url = channel_info["url"]

        with semaphore:
            _logger.info(f"[{label}] Starting upload thread")

            browser_class = mock_browser_class
            if browser_class is None:
                from browser_max import BrowserMAX
                browser_class = BrowserMAX

            browser = None
            result = ChannelResult(label=label, success=False)

            try:
                browser = browser_class(url, use_local_browser=False)
                if not browser.keep_alive_connect():
                    result.errors.append("Failed to connect to browser")
                    self._save_result(label, result)
                    return

                browser.navigate()
                browser.ensure_page_ready()

                uploaded = 0
                for filepath in self.files:
                    if not os.path.exists(filepath):
                        result.errors.append(f"File not found: {filepath}")
                        continue

                    filename = os.path.basename(filepath)
                    try:
                        self._upload_single_file(browser, filepath, label)
                        result.files.append(filepath)
                        uploaded += 1
                    except Exception as e:
                        result.errors.append(f"Failed to upload {filename}: {e}")

                result.success = uploaded > 0
                _logger.info(
                    f"[{label}] Upload complete: {uploaded}/{len(self.files)} files"
                )

            except Exception as e:
                _logger.error(f"[{label}] Thread exception: {e}", exc_info=True)
                result.errors.append(f"Thread exception: {e}")

            finally:
                self._save_result(label, result)
                if browser:
                    try:
                        browser.page = None
                        browser.browser = None
                        browser._connected = False
                    except Exception:
                        pass

    def _save_result(self, label: str, result: ChannelResult) -> None:
        """Thread-safe result storage."""
        with self._results_lock:
            self._results[label] = result
            if not result.success:
                self._channel_failures[label] = True

    def _build_summary(self) -> UploadSummary:
        """Build upload summary from results."""
        summary = UploadSummary(
            channel_results=self._results,
            total_files=len(self.files),
        )

        success_count = sum(1 for r in self._results.values() if r.success)
        failed_count = len(self._results) - success_count
        summary.total_success = success_count
        summary.total_failed = failed_count

        return summary

    def _cleanup_files(self, summary: UploadSummary) -> None:
        """Delete files if ≥1 channel succeeded. Preserve if ALL failed."""
        all_failed = summary.total_failed == len(self.channels)

        if all_failed:
            _logger.warning(
                "All channels failed — preserving temp files for manual retry"
            )
            return

        _logger.info("Cleanup: deleting temp files (≥1 channel succeeded)")
        for filepath in self.files:
            try:
                if os.path.exists(filepath):
                    os.remove(filepath)
                    _logger.debug(f"Deleted: {filepath}")
            except Exception as e:
                _logger.warning(f"Failed to delete {filepath}: {e}")
