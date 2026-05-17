# -*- coding: utf-8 -*-
"""
MAX messenger automation using Playwright
"""

import os
import re
import time
import sys
import logging
import subprocess
from typing import Optional
from playwright.sync_api import sync_playwright, Page, Browser, TimeoutError as PlaywrightTimeout
from logging_config import LogMixin, setup_logging


# 7z volume size (49MB to leave buffer for CDN limits)
SEVEN_ZIP_VOLUME_SIZE = "49M"

# Module-level logger for standalone functions
_logger = logging.getLogger("gitax")

# 7-Zip executable path (Windows default)
SEVEN_ZIP_EXE = "C:\\Program Files\\7-Zip\\7z.exe"


def split_file_with_7z(filepath: str, volume_size: str = SEVEN_ZIP_VOLUME_SIZE) -> list[str]:
    """
    Split a file into volumes using 7z.

    Args:
        filepath: Path to file to split
        volume_size: Volume size (e.g., "49M", "100M"). Default: 49M

    Returns:
        List of volume file paths (e.g., ['file.7z.001', 'file.7z.002', ...])
        Returns empty list if split failed or file is small enough.
    """
    if not os.path.exists(filepath):
        return []

    file_size = os.path.getsize(filepath)
    volume_bytes = _parse_size(volume_size)

    # No split needed if file is smaller than threshold
    if file_size <= volume_bytes:
        return []

    filename = os.path.basename(filepath)
    output_base = filepath + ".7z"

    # Remove any existing volumes with same base
    _cleanup_existing_volumes(output_base)

    cmd = [
        SEVEN_ZIP_EXE,
        "a",
        "-v" + volume_size,  # Volume size (e.g., -v49m)
        "-mx=0",             # No compression (faster, raw split)
        output_base,
        filepath
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=3600  # 1 hour max for large files
        )

        if result.returncode != 0:
            _logger.warning(f"7z split failed: {result.stderr}")
            _cleanup_existing_volumes(output_base)
            return []

        # Find created volumes
        volumes = _find_volumes(output_base)

        if volumes:
            _logger.info(f"Split into {len(volumes)} volumes: {volumes[0]}...")
            return volumes
        else:
            _logger.warning("7z succeeded but no volumes found")
            return []

    except subprocess.TimeoutExpired:
        _logger.error("7z split timeout")
        _cleanup_existing_volumes(output_base)
        return []
    except FileNotFoundError:
        _logger.error(f"7z not found at {SEVEN_ZIP_EXE}")
        return []
    except Exception as e:
        _logger.error(f"7z split error: {e}")
        _cleanup_existing_volumes(output_base)
        return []


def _parse_size(size_str: str) -> int:
    """Parse size string like '49M' or '1G' to bytes"""
    size_str = size_str.upper().strip()
    multipliers = {
        'K': 1024,
        'M': 1024 * 1024,
        'G': 1024 * 1024 * 1024
    }

    for suffix, mult in multipliers.items():
        if size_str.endswith(suffix):
            try:
                return int(float(size_str[:-1]) * mult)
            except ValueError:
                pass

    # Plain number
    try:
        return int(size_str)
    except ValueError:
        return 0


def _cleanup_existing_volumes(base_path: str):
    """Remove any existing volume files matching base pattern"""
    import glob
    pattern = base_path + ".*"
    for f in glob.glob(pattern):
        try:
            os.remove(f)
        except Exception:
            pass


def _find_volumes(base_path: str) -> list[str]:
    """Find all volume files matching base.7z.xxx pattern, sorted"""
    import glob
    pattern = base_path + ".*"
    volumes = sorted(glob.glob(pattern))
    return volumes


def cleanup_volumes(volume_paths: list[str]):
    """Remove volume files after successful upload"""
    for vp in volume_paths:
        try:
            if os.path.exists(vp):
                os.remove(vp)
                _logger.debug(f"Removed: {vp}")
        except Exception as e:
            _logger.warning(f"Failed to remove {vp}: {e}")


class BrowserMAXError(Exception):
    """Base exception for BrowserMAX errors"""
    pass


class ConnectionError(BrowserMAXError):
    """Failed to connect to Chrome"""
    pass


class UploadError(BrowserMAXError):
    """Failed to upload file"""
    pass


class ElementNotFoundError(BrowserMAXError):
    """Required element not found"""
    pass


class BrowserMAX(LogMixin):
    """MAX messenger automation using Playwright"""

    def __init__(self, channel_url: str, use_local_browser: bool = False):
        """
        Initialize MAX browser automation.

        Args:
            channel_url: MAX channel URL
            use_local_browser: If True, launch new Chrome. If False, connect to existing browser via CDP.
                              Default is False (connect to existing) for seamless UX.
        """
        self.channel_url = channel_url
        self.use_local_browser = use_local_browser
        self.playwright = None
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None
        self._connected = False

    def connect(self) -> bool:
        """Connect to Chrome - local browser or via CDP"""
        self.logger.info(f"Connecting to Chrome (local={self.use_local_browser})...")

        try:
            self.playwright = sync_playwright().start()

            if self.use_local_browser:
                # Launch local Chrome browser (no 50MB file limit)
                self.logger.info("Launching local Chrome...")
                self.browser = self.playwright.chromium.launch(
                    headless=False,
                    args=['--disable-blink-features=Automation']
                )
                context = self.browser.new_context(
                    viewport={'width': 1200, 'height': 900}
                )
                self.page = context.new_page()
            else:
                # Use CDP connection to existing Chrome (must be open at channel_url)
                # This preserves existing browser state and cookies
                self.logger.info("Connecting via CDP (port 9222) to existing browser...")
                try:
                    self.browser = self.playwright.chromium.connect_over_cdp(
                        "http://localhost:9222",
                        timeout=30000
                    )
                except Exception as e:
                    self.logger.error(f"CDP connection failed: {e}")
                    self.playwright.stop()
                    self.playwright = None
                    return False

                context = self.browser.contexts[0] if self.browser.contexts else self.browser.new_context()
                self.page = context.pages[0] if context.pages else context.new_page()

            self._connected = True
            self.logger.info("Connected successfully")
            return True
        except Exception as e:
            self.logger.error(f"Connection failed: {e}", exc_info=True)
            return False

    def keep_alive_connect(self) -> bool:
        """Connect to Chrome and stay connected. Use this for multiple operations."""
        if self.browser and self.page:
            self.logger.debug("Already connected (reusing connection)")
            return True
        return self.connect()

    def ensure_page_ready(self):
        """Ensure page is loaded and ready for interaction"""
        if not self.page:
            raise ConnectionError("Not connected. Call keep_alive_connect() first.")

        try:
            self.page.wait_for_load_state("networkidle", timeout=15000)
        except PlaywrightTimeout:
            self.logger.warning("Page load timeout, continuing anyway")
        except Exception as e:
            self.logger.warning(f"Unexpected error waiting for page: {e}")

    def navigate(self):
        """Navigate to MAX channel"""
        self.logger.info(f"Opening channel: {self.channel_url}")

        # Reconnect if needed
        if not self._ensure_alive():
            raise ConnectionError("Failed to connect to Chrome")

        try:
            self.page.goto(self.channel_url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(2)
        except Exception as e:
            self.logger.warning(f"Navigation error: {e}, reconnecting...")
            self._connected = False
            self.page = None
            if not self._ensure_alive():
                raise ConnectionError("Failed to reconnect to Chrome")
            self.page.goto(self.channel_url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(2)

    def wait_page_ready(self, timeout: int = 30):
        """Wait for page to be ready"""
        if not self._ensure_alive():
            return

        try:
            self.page.wait_for_load_state("networkidle", timeout=timeout * 1000)
        except PlaywrightTimeout:
            self.logger.warning(f"Page ready timeout after {timeout}s")
        except Exception as e:
            self.logger.warning(f"Unexpected error: {e}")

    def _check_connection(self):
        """Verify page is available and alive"""
        if not self.page:
            raise ConnectionError("Not connected. Call connect() first.")
        if self.page.is_closed():
            self.logger.warning("Page was closed, reconnecting...")
            self._connected = False
            self.page = None
            raise ConnectionError("Page was closed, need to reconnect")

    def _ensure_alive(self) -> bool:
        """
        Ensure connection is alive. Reconnect if needed.

        Returns:
            True if connected, False otherwise
        """
        if not self.page:
            return self.connect()

        try:
            if self.page.is_closed():
                self.logger.warning("Page is closed, reconnecting...")
                self._connected = False
                self.page = None
                return self.connect()
            return True
        except Exception:
            return self.connect()

    def _safe_evaluate(self, script: str, default=None):
        """
        Safely evaluate JavaScript, reconnecting if page was closed.

        Args:
            script: JavaScript to evaluate
            default: Default value if evaluation fails

        Returns:
            Result of evaluation or default
        """
        if not self._ensure_alive():
            return default

        try:
            return self.page.evaluate(script)
        except Exception as e:
            self.logger.debug(f"Safe evaluate error: {e}")
            return default

    def get_message_count(self) -> int:
        """Get current message count in chat"""
        self._check_connection()
        try:
            count = self.page.evaluate(
                "() => document.querySelectorAll('[class*=\"message\"]').length"
            )
            return count or 0
        except Exception as e:
            self.logger.debug(f"Failed to get message count: {e}")
            return 0

    def _click_upload_button(self):
        """Find and click upload/file button - handles dropdown menu"""
        self._check_connection()
        self.logger.info("Looking for upload button...")

        for selector in ['[aria-label="Upload file"]', 'button:has-text("File")']:
            try:
                btn = self.page.locator(selector).first
                if btn.is_visible(timeout=3000):
                    btn.scroll_into_view_if_needed()
                    btn.click(timeout=5000)
                    self.logger.debug(f"Button clicked: {selector}")
                    time.sleep(0.5)

                    menu_btn = self.page.locator('button:has-text("File")').first
                    if menu_btn.is_visible(timeout=3000):
                        menu_btn.click()
                        self.logger.debug("File menu item clicked")
                        time.sleep(0.3)
                        return True
            except PlaywrightTimeout:
                self.logger.debug(f"Selector not found: {selector}")
                continue
            except Exception as e:
                self.logger.warning(f"Error with selector {selector}: {e}")
                continue

        try:
            upload_btn = self.page.locator('[aria-label="Upload file"]').first
            upload_btn.click()
            time.sleep(0.3)

            file_btn = self.page.get_by_text("File")
            file_btn.click(timeout=5000)
            self.logger.info("Upload > File clicked")
            return True
        except Exception as e:
            self.logger.warning(f"Menu click failed: {e}")

        return False

    def _validate_file(self, filepath: str) -> tuple[bool, str]:
        """Validate file before upload"""
        if not os.path.exists(filepath):
            return False, f"File not found: {filepath}"

        file_size = os.path.getsize(filepath)
        if file_size == 0:
            return False, "File is empty"

        if file_size > 2 * 1024 * 1024 * 1024:
            return False, f"File too large: {file_size / 1024 / 1024 / 1024:.1f} GB"

        filename = os.path.basename(filepath)
        self.logger.debug(f"File validated: {filename} ({file_size / 1024 / 1024:.1f} MB)")
        return True, ""

    def _upload_file(self, filepath: str) -> bool:
        """Upload file using input[type=file]"""
        valid, err = self._validate_file(filepath)
        if not valid:
            self.logger.error(f"File validation failed: {err}")
            return False

        abs_path = os.path.abspath(filepath)
        filename = os.path.basename(filepath)
        file_size_mb = os.path.getsize(abs_path) / 1024 / 1024

        self.logger.info(f"Selecting file: {filename} ({file_size_mb:.1f} MB)")

        for selector in ['input[type="file"]', 'input[type=file]']:
            try:
                file_input = self.page.locator(selector).first
                file_input.set_input_files(abs_path, timeout=60000)
                self.logger.info("File selected")
                return True
            except PlaywrightTimeout:
                self.logger.debug(f"Selector timeout: {selector}")
                continue
            except Exception as e:
                self.logger.warning(f"Error with selector {selector}: {e}")
                continue

        self.logger.error("Could not select file - no input found")
        return False

    def _upload_file_drag_drop(self, filepath: str) -> bool:
        """Upload file using file chooser"""
        self._check_connection()
        valid, err = self._validate_file(filepath)
        if not valid:
            self.logger.error(f"File validation failed: {err}")
            return False

        abs_path = os.path.abspath(filepath)
        filename = os.path.basename(filepath)
        file_size_mb = os.path.getsize(abs_path) / 1024 / 1024
        self.logger.info(f"Selecting file: {filename} ({file_size_mb:.1f} MB)")

        try:
            with self.page.expect_file_chooser(timeout=60000) as fc_info:
                self.logger.info("Waiting for file dialog...")

            file_chooser = fc_info.value
            file_chooser.set_files(abs_path)
            self.logger.info("File selected via dialog")
            return True
        except PlaywrightTimeout:
            self.logger.error("File chooser timeout")
            return False
        except Exception as e:
            self.logger.error(f"File chooser failed: {e}")
            return False

    def _install_upload_observer(self, expected_filename: str | None = None, expected_size: int | None = None) -> Optional[str]:
        """
        Install MutationObserver to track file upload.
        Returns unique observer ID for tracking.

        Args:
            expected_filename: Filename to match (e.g., "repo-master.zip")
            expected_size: File size in bytes to match for verification
        """
        self._check_connection()
        upload_id = f"gitax_{int(time.time() * 1000)}"
        window_key = "gitax_upload_done"

        # Build size pattern for matching (e.g., "388.2 MB" or "388 MB")
        size_pattern = ""
        if expected_size:
            if expected_size >= 1024 * 1024 * 1024:
                size_str = f"{expected_size / 1024 / 1024 / 1024:.1f} GB"
            elif expected_size >= 1024 * 1024:
                size_str = f"{expected_size / 1024 / 1024:.2f} MB"
            else:
                size_str = f"{expected_size / 1024:.1f} KB"
            size_pattern = size_str.replace(".", "\\.").replace(" ", "\\s*")

        script = f"""
            () => {{
                const id = '{upload_id}';
                const target = '{window_key}';
                const searchName = '{expected_filename or ''}';
                const sizePattern = '{size_pattern}';
                const expectedSize = {expected_size or 0};

                window[target] = null;
                window[target + '_file'] = null;
                window[target + '_selector'] = null;

                const observer = new MutationObserver((mutations) => {{
                    for (const m of mutations) {{
                        if (m.type === 'childList') {{
                            for (const node of m.addedNodes) {{
                                if (node.nodeType !== 1) continue;

                                const text = node.textContent || '';
                                const className = node.className || '';

                                // Check if this looks like a file message
                                const hasFileClass = /file|attach|upload|preview|item/i.test(className);
                                const hasZip = /\\.zip/i.test(text);
                                const hasDownload = /скачать|download/i.test(text);

                                if (!hasFileClass && !hasZip && !hasDownload) continue;

                                // If we have search name, check for filename match
                                if (searchName) {{
                                    // Normalize for comparison (remove -master, -main, extensions)
                                    const normalizedSearch = searchName.toLowerCase()
                                        .replace('.zip', '')
                                        .replace('-master', '')
                                        .replace('-main', '')
                                        .replace('.git', '');

                                    // Look for repo name pattern in text
                                    const textLower = text.toLowerCase();
                                    const hasRepoName = textLower.includes(normalizedSearch);

                                    // If no repo name match, skip unless this is a "Скачать" button
                                    if (!hasRepoName && !hasDownload) continue;
                                }}

                                // If we have size pattern, verify size matches
                                if (sizePattern) {{
                                    const sizeRegex = new RegExp(sizePattern, 'i');
                                    if (!sizeRegex.test(text)) continue;
                                }}

                                // Match found!
                                if (!window[target]) {{
                                    window[target] = Date.now();
                                    window[target + '_file'] = text.slice(0, 100);
                                    window[target + '_selector'] = id;
                                }}
                            }}
                        }}

                        if (m.type === 'attributes' && m.attributeName === 'src') {{
                            const src = m.target.src || '';
                            if (src.includes('file') || src.includes('upload') || src.includes('attach')) {{
                                if (!window[target]) {{
                                    window[target] = Date.now();
                                    window[target + '_file'] = src.split('/').pop() || 'unknown';
                                    window[target + '_selector'] = id;
                                }}
                            }}
                        }}
                    }}
                }});

                try {{
                    observer.observe(document.body, {{ childList: true, subtree: true, attributes: true, attributeFilter: ['src', 'class'] }});
                    return id;
                }} catch (e) {{
                    return null;
                }}
            }}
        """

        try:
            result = self.page.evaluate(script)
            if result:
                self.logger.debug(f"Upload observer installed: {result}")
                return result
            return None
        except Exception as e:
            self.logger.warning(f"Failed to install observer: {e}")
            return None

    def _check_upload_done(self) -> tuple[bool, Optional[str]]:
        """
        Check if upload was completed via observer.

        Returns:
            (done: bool, filename: Optional[str])
        """
        script = """
            () => {
                const target = 'gitax_upload_done';
                if (window[target]) {
                    return {
                        done: true,
                        time: window[target],
                        file: window[target + '_file'] || 'unknown',
                        id: window[target + '_selector'] || null
                    };
                }
                return { done: false, time: null, file: null, id: null };
            }
        """

        try:
            result = self.page.evaluate(script)
            if result and result.get('done'):
                return True, result.get('file')
            return False, None
        except Exception as e:
            self.logger.debug(f"Check upload status error: {e}")
            return False, None

    def _capture_pre_upload_state(self) -> Optional[dict]:
        """
        Capture DOM state before upload to detect changes.
        """
        self._check_connection()
        script = """
            () => {
                const msgs = document.querySelectorAll('[class*="message"]');
                const lastMsg = msgs.length > 0 ? msgs[msgs.length - 1].innerHTML.slice(0, 500) : '';

                const attachments = document.querySelectorAll('[class*="attach"], [class*="file"], [class*="preview"]');
                const attachCount = attachments.length;

                const composer = document.querySelector('[class*="composer"], [class*="input"], [role="textbox"]');
                const composerHtml = composer ? composer.innerHTML.slice(0, 200) : '';

                return {
                    msgCount: msgs.length,
                    lastMsg: lastMsg,
                    attachCount: attachCount,
                    composerHtml: composerHtml
                };
            }
        """

        try:
            return self.page.evaluate(script)
        except Exception as e:
            self.logger.warning(f"Failed to capture state: {e}")
            return None

    def _detect_state_change(self, before: Optional[dict]) -> tuple[bool, str]:
        """
        Detect if DOM state changed (file uploaded).
        """
        if not before:
            return False, "no_baseline"

        script = """
            () => {
                const msgs = document.querySelectorAll('[class*="message"]');
                const newMsgs = document.querySelectorAll('[class*="message"]');
                const lastMsgNew = newMsgs.length > 0 ? (newMsgs[newMsgs.length - 1].textContent || '') : '';

                return {
                    msgCount: msgs.length,
                    lastMsgNew: lastMsgNew.slice(0, 100)
                };
            }
        """

        try:
            current = self.page.evaluate(script)

            changed = False
            reason = "none"

            if current.get('attachCount', 0) > before.get('attachCount', 0):
                changed = True
                reason = "new_attachment"

            composer_current = current.get('composerHtml', '') or ''
            composer_before = before.get('composerHtml', '') or ''
            if composer_current != composer_before:
                if 'file' in composer_current.lower() or 'preview' in composer_current.lower():
                    changed = True
                    reason = "composer_changed_with_file"

            if current.get('msgCount', 0) > before.get('msgCount', 0):
                changed = True
                reason = "new_message"

            if changed:
                return True, reason

            return False, reason
        except Exception as e:
            self.logger.debug(f"State change detection error: {e}")
            return False, "error"

    def _wait_upload_complete(self, timeout: int = 3600, poll_interval: float = 1.0,
                               expected_filename: str | None = None, expected_size: int | None = None) -> bool:
        """
        Wait for file upload to complete - uses PROGRESS-BASED detection.
        No fixed timeout - waits until upload is confirmed or user cancels.

        Args:
            timeout: Maximum seconds (default 1h, for huge files)
            poll_interval: How often to check (seconds)
            expected_filename: Filename to match for upload confirmation
            expected_size: File size in bytes to match
        """
        self._check_connection()
        self.logger.info(f"Monitoring upload progress...")

        observer_id = self._install_upload_observer(expected_filename, expected_size)
        pre_state = self._capture_pre_upload_state()
        start = time.time()
        last_activity_time = start
        last_progress_log = start
        consecutive_no_activity = 0
        max_no_activity_cycles = 30  # ~30 seconds of no progress = done or stalled

        print(f"  [OK] Waiting for upload to complete... (Ctrl+C to cancel)")

        while time.time() - start < timeout:
            elapsed = int(time.time() - start)

            # Check for upload progress (progress bar, percentage, activity)
            progress_info = self._check_upload_progress()
            if progress_info:
                pct = progress_info.get('percent', 0)
                speed = progress_info.get('speed', '')
                remaining = progress_info.get('remaining', '')

                if pct > 0 and pct < 100:
                    last_activity_time = time.time()
                    consecutive_no_activity = 0

                    # Log progress every 10 seconds
                    if time.time() - last_progress_log >= 10:
                        print(f"\r  [UPLOAD] {pct}% {speed} {remaining}", end="", flush=True)
                        last_progress_log = time.time()

                elif pct >= 100:
                    print(f"\n  [OK] Upload reached 100%")
                    last_activity_time = time.time()

            # Check if file appeared in message feed (definitive confirmation)
            done, filename = self._check_upload_done()
            if done:
                self.logger.info(f"Upload complete: {filename} ({elapsed}s)")
                return True

            # Check lenta for file message
            done_lenta, _ = self._check_upload_in_lenta(None)
            if done_lenta:
                print(f"\n  [OK] File confirmed in feed ({elapsed}s)")
                return True

            # Check DOM for attached file
            if self._check_dom_upload_ready():
                print(f"\n  [OK] File attached in composer ({elapsed}s)")
                return True

            # Check for state changes
            changed, reason = self._detect_state_change(pre_state)
            if changed:
                self.logger.info(f"DOM change detected: {reason} ({elapsed}s)")
                last_activity_time = time.time()
                consecutive_no_activity = 0

            # Track activity - if no progress for a while, consider it done
            time_since_activity = time.time() - last_activity_time

            if time_since_activity > 30:
                consecutive_no_activity += 1

                # After ~30-60 seconds of no activity, assume upload is done
                if consecutive_no_activity >= 2:
                    # Final verification
                    done_lenta, _ = self._check_upload_in_lenta(None)
                    if done_lenta or self._check_dom_upload_ready() or done:
                        print(f"\n  [OK] Upload finished (no activity for {int(time_since_activity)}s)")
                        return True

                    # Still nothing after extended wait
                    if consecutive_no_activity >= 4:  # ~60 seconds
                        print(f"\n  [WARN] No upload activity for {int(time_since_activity)}s")
                        # Try one more thorough check
                        done_lenta, _ = self._check_upload_in_lenta(None)
                        if not done_lenta:
                            print(f"  [INFO] Continuing to monitor...")

            # Show elapsed time every 30s if no progress
            if time.time() - last_progress_log >= 30 and elapsed > 30:
                print(f"\r  [MONITOR] Elapsed: {elapsed}s | No progress for: {int(time_since_activity)}s", end="", flush=True)
                last_progress_log = time.time()

            time.sleep(poll_interval)

        # Timeout - do final checks
        print(f"\n  [WARN] Upload monitoring timeout ({timeout}s) - final check...")
        time.sleep(2)

        done, filename = self._check_upload_done()
        if done:
            self.logger.info(f"Upload complete at timeout: {filename}")
            return True

        done_lenta, _ = self._check_upload_in_lenta(None)
        if done_lenta:
            self.logger.info("Upload confirmed in lenta at timeout")
            return True

        if self._check_dom_upload_ready():
            self.logger.info("Upload confirmed in composer at timeout")
            return True

        self.logger.error("Upload not confirmed - file may not have been uploaded")
        return False

    def _check_upload_progress(self) -> Optional[dict]:
        """
        Check for upload progress indicators in DOM.

        Returns:
            dict with keys: percent (int), speed (str), remaining (str) or None
        """
        script = """
            () => {
                // Look for various upload progress indicators
                const selectors = [
                    // Progress bar elements
                    '[class*="progress"] [class*="bar"]',
                    '[class*="upload"] [class*="progress"]',
                    '[role="progressbar"]',
                    '[class*="percent"]',
                    // Loading/spinner while uploading
                    '[class*="uploading"]',
                    '[class*="loading"]',
                    '[class*="transferring"]',
                    // Text indicators
                    '*[class*="upload"]',
                    '*[class*="progress"]'
                ];

                for (const sel of selectors) {
                    const els = document.querySelectorAll(sel);
                    for (const el of els) {
                        const text = el.textContent || '';
                        const style = el.getAttribute('style') || '';
                        const width = style.includes('width:') ? style.match(/width:\\s*([\\d.]+)%/) : null;
                        const aria = el.getAttribute('aria-valuenow') || '';

                        // Check for percentage patterns
                        const percentMatch = text.match(/(\\d+)%/) || aria.match(/(\\d+)/);
                        if (percentMatch) {
                            return {
                                percent: parseInt(percentMatch[1]),
                                text: text.slice(0, 50),
                                type: 'progress'
                            };
                        }

                        // Check for width style
                        if (width) {
                            return {
                                percent: parseFloat(width[1]),
                                text: text.slice(0, 50),
                                type: 'width'
                            };
                        }
                    }
                }

                // Check for network activity indicator (spinner, loading)
                const bodyClass = document.body.className || '';
                if (bodyClass.includes('uploading') || bodyClass.includes('loading')) {
                    return { percent: -1, text: 'uploading', type: 'class' };
                }

                // Check for any visible spinner
                const spinners = document.querySelectorAll('[class*="spinner"], [class*="loader"], [class*="activity"]');
                for (const sp of spinners) {
                    if (sp.offsetHeight > 0 && sp.offsetParent !== null) {
                        return { percent: -1, text: 'activity', type: 'spinner' };
                    }
                }

                return null;
            }
        """

        try:
            result = self.page.evaluate(script)
            return result if result else None
        except Exception as e:
            self.logger.debug(f"Progress check error: {e}")
            return None

    def _check_dom_upload_ready(self) -> bool:
        """
        Final DOM check for attached file in composer.
        NOTE: Does NOT check input[type=file] - that only means file is selected, not uploaded.
        """
        script = """
            () => {
                // Only check for visible file indicators in composer
                const composer = document.querySelector('[class*="composer"], [role="textbox"], [contenteditable]');
                if (!composer) return false;

                // Check for file preview/attachment elements within composer
                const fileIndicators = composer.querySelectorAll(
                    '[class*="preview"], [class*="file-item"], [class*="attach"], [class*="upload"], [data-file]'
                );

                for (const el of fileIndicators) {
                    if (el.offsetHeight > 0) {
                        const text = el.textContent || '';
                        if (text.includes('.zip') || text.includes('.tar') || text.includes('MB') || text.includes('KB')) {
                            return true;
                        }
                    }
                }

                // Alternative: check if composer has changed to include file class
                const composerClasses = composer.className || '';
                if (composerClasses.includes('with-file') || composerClasses.includes('has-file') || composerClasses.includes('file-attached')) {
                    return true;
                }

                return false;
            }
        """

        try:
            result = self.page.evaluate(script)
            return result is True
        except Exception as e:
            self.logger.debug(f"DOM composer check error: {e}")
            return False

    def _check_upload_in_lenta(self, expected_filename: Optional[str] = None) -> tuple[bool, Optional[str]]:
        """
        Check if file appears in message feed (not in composer).
        This is the definitive check that file was actually uploaded.

        Args:
            expected_filename: Optional filename to match

        Returns:
            (found: bool, filename: Optional[str])
        """
        script = """
            () => {
                // Get ALL messages with their full details
                const msgs = document.querySelectorAll('[class*="message"]');
                const results = [];

                for (const msg of msgs) {
                    const text = msg.textContent || '';
                    const html = msg.innerHTML || '';
                    const hasFile = msg.querySelector('[class*="file"], [class*="attach"]') !== null;
                    const hasDownload = msg.querySelector('a[download], [download]') !== null;
                    const hasSvg = msg.querySelector('svg') !== null;
                    const hrefs = Array.from(msg.querySelectorAll('a')).map(a => a.href || '');

                    results.push({
                        text: text.slice(0, 150),
                        hasFile: hasFile,
                        hasDownload: hasDownload,
                        hasSvg: hasSvg,
                        hrefs: hrefs.slice(0, 5)
                    });
                }

                // Check if any message has file indicators
                for (const r of results) {
                    const hasArchive = /\\.(zip|tar|gz|rar|7z|zip\\.\\w+)/i.test(r.text) ||
                                     /\\.(zip|tar|gz|rar|7z|zip\\.\\w+)/i.test(r.html || '');
                    const hasFile = r.hasFile || r.hasDownload;
                    const hasHrefArchive = r.hrefs.some(h => /\\.(zip|tar|gz|archive)/i.test(h));

                    if (hasFile || hasArchive || hasHrefArchive) {
                        return { found: true, text: r.text.slice(0, 100) };
                    }
                }

                // Debug: return last 3 messages for troubleshooting
                return { found: false, lastMessages: results.slice(-3).map(r => r.text.slice(0, 80)) };
            }
        """

        try:
            result = self.page.evaluate(script)
            if result and result.get('found'):
                filename = result.get('text', 'unknown')
                self.logger.info(f"File found in lenta: {filename}")
                return (True, filename)
            elif result:
                self.logger.debug(f"Lenta check: no file, last msgs: {result.get('lastMessages', [])}")
            return (False, None)
        except Exception as e:
            self.logger.debug(f"Lenta check error: {e}")
            return (False, None)

    def _wait_for_file_message(self, timeout: int = 300,
                                expected_msg_index: Optional[int] = None,
                                expected_filename: Optional[str] = None) -> tuple[bool, str, int]:
        """
        Monitor chat online until file message is found.
        No hard timeout - exits when file is confirmed or error occurs.

        Args:
            timeout: Max time to wait (default 5 min as safety fallback)
            expected_msg_index: Expected message index (0 = any new)
            expected_filename: Filename to match (if provided, only confirms if filename matches)

        Returns:
            (found: bool, reason: str, found_msg_index: int)
            reason = "found" | "timeout" | "disconnected" | "no_activity" | "filename_mismatch"
            found_msg_index: 0 if not found, otherwise message index
        """
        start = time.time()
        base_count = 0
        last_activity_time = start

        print(f"  [MONITOR] Starting live monitoring...")

        # If filename provided, normalize it for comparison
        search_name = None
        if expected_filename:
            import os as os_module
            basename = os_module.path.basename(expected_filename).lower()
            search_name = basename.replace('.zip', '').replace('-master', '').replace('-main', '')
            print(f"  [SCAN] Looking for: {search_name}")

        try:
            if not self._ensure_alive():
                return (False, "not_connected", 0)

            base_count = self.page.evaluate(
                "() => document.querySelectorAll('[class*=\"message\"]').length"
            ) or 0

            init_result = self.page.evaluate("""
                () => {
                    const msgs = document.querySelectorAll('[class*="message"]');
                    const last = msgs[msgs.length - 1];
                    return last ? last.textContent?.slice(0, 200) || '' : '';
                }
            """) or ""
            print(f"  [MONITOR] Initial: {base_count} msgs, last: {init_result[:50]}...")

            # Search ALL messages (not just last 10) to find our file
            print(f"  [SCAN] Searching all {base_count} messages...")
            for idx in range(base_count):
                msg_result = self.page.evaluate(f"""
                    () => {{
                        const msgs = document.querySelectorAll('[class*="message"]');
                        const msg = msgs[{idx}];
                        if (!msg) return null;

                        const text = msg.textContent || '';
                        const html = msg.innerHTML || '';
                        const classes = msg.className || '';

                        const hasFileClass = /file|attach|download|archive|preview/i.test(classes);
                        const hasZip = /\\.zip/i.test(text) || /\\.zip/i.test(html);
                        const hasDownload = msg.querySelector('[download]') !== null ||
                                            msg.querySelector('a[href*="download"]') !== null;

                        return {{
                            text: text.slice(0, 150),
                            hasFileClass,
                            hasZip,
                            hasDownload,
                            classes: classes.slice(0, 80)
                        }};
                    }}
                """) or {}

                msg_text = msg_result.get('text', '').lower()
                msg_html = msg_result.get('html', '').lower()
                msg_classes = msg_result.get('classes', '').lower()

                match = re.search(r'([a-z0-9\-_]+\.zip)', msg_text)
                if match:
                    msg_filename = match.group(1).replace('-master', '').replace('-main', '')
                    if search_name not in msg_filename:
                        continue  # Skip - name mismatch

                # Option 2: Check for download button or file icon
                has_download_btn = (
                    'download' in msg_classes or
                    'download' in msg_html or
                    msg_result.get('hasDownload') or
                    'скачать' in msg_text.lower() or
                    'download' in msg_text.lower()
                )

                # Option 3: Check for file size indicator (e.g., "388 MB", "388.2 MB")
                has_size = re.search(r'\d+\.?\d*\s*(mb|gb|kb)', msg_text)
                if not has_size:
                    # Also check in HTML
                    has_size = re.search(r'\d+\.?\d*\s*(mb|gb|kb)', msg_html)

                # File message must have either download button OR size indicator
                if not has_download_btn and not has_size:
                    continue

                print(f"  [OK] FILE FOUND! Message #{idx + 1}")
                print(f"       {msg_result.get('text', '')[:100]}...")
                return (True, "found", idx + 1)

            print(f"  [SCAN] No matching file found")
            last_activity_time = time.time()
        except Exception as e:
            print(f"  [ERROR] Failed to initialize: {e}")
            return (False, "init_failed", 0)

        no_change_count = 0
        check_interval = 0.5  # Check every 0.5 seconds
        checked_initial = False

        while True:
            elapsed = int(time.time() - start)
            timeout_reached = elapsed >= timeout

            try:
                # Ensure connection alive
                if not self._ensure_alive():
                    print(f"  [WARN] Connection lost after {elapsed}s")
                    return (False, "disconnected", 0)

                # Get current state
                current_count = self.page.evaluate(
                    "() => document.querySelectorAll('[class*=\"message\"]').length"
                ) or 0

                if current_count > base_count:
                    # New message(s) appeared!
                    print(f"  [UPDATE] New messages: {base_count} -> {current_count}")
                    last_activity_time = time.time()
                    no_change_count = 0

                    # Check new messages for file
                    for idx in range(base_count, current_count):
                        msg_result = self.page.evaluate(f"""
                            () => {{
                                const msgs = document.querySelectorAll('[class*="message"]');
                                const msg = msgs[{idx}];
                                if (!msg) return null;

                                const text = msg.textContent || '';
                                const html = msg.innerHTML || '';
                                const classes = msg.className || '';

                                const hasFileClass = /file|attach|download|archive|preview/i.test(classes);
                                const hasZip = /\\.zip/i.test(text) || /\\.zip/i.test(html);
                                const hasDownload = msg.querySelector('[download]') !== null ||
                                                    msg.querySelector('a[href*="download"]') !== null;
                                const hasSvg = msg.querySelector('svg') !== null;

                                return {{
                                    text: text.slice(0, 150),
                                    hasFileClass,
                                    hasZip,
                                    hasDownload,
                                    hasSvg,
                                    classes: classes.slice(0, 80)
                                }};
                            }}
                        """) or {}

                        is_file = (msg_result.get('hasFileClass') or
                                  msg_result.get('hasZip') or
                                  msg_result.get('hasDownload'))

                        # Apply same robust checks as initial scan
                        if is_file:
                            msg_text = msg_result.get('text', '').lower()
                            msg_html = (msg_result.get('html') or '').lower()

                            # Check for download indicator
                            has_download = msg_result.get('hasDownload')
                            if not has_download:
                                has_download = 'download' in msg_text or 'скачать' in msg_text

                            # Check for file size (e.g., "388 MB", "388.2 MB")
                            has_size = re.search(r'\d+\.?\d*\s*(mb|gb|kb)', msg_text)
                            if not has_size:
                                has_size = re.search(r'\d+\.?\d*\s*(mb|gb|kb)', msg_html)

                            # File must have download OR size
                            if not has_download and not has_size:
                                print(f"  [SKIP] Msg #{idx + 1}: no download/size indicator")
                                continue

                            print(f"  [OK] FILE FOUND! Message #{idx + 1}")
                            print(f"       {msg_result.get('text', '')[:100]}...")
                            return (True, "found", idx + 1)

                        print(f"  [INFO] Msg #{idx + 1}: {msg_result.get('text', '')[:60]}...")

                    base_count = current_count

                # Check for timeout
                if timeout_reached:
                    # Final check - scan last 20 messages for any file
                    final_count = self.page.evaluate(
                        "() => document.querySelectorAll('[class*=\"message\"]').length"
                    ) or 0
                    print(f"  [WARN] Timeout after {elapsed}s. Checking last 20 msgs...")

                    for idx in range(max(0, final_count - 20), final_count):
                        msg_result = self.page.evaluate(f"""
                            () => {{
                                const msgs = document.querySelectorAll('[class*="message"]');
                                const msg = msgs[{idx}];
                                if (!msg) return null;
                                const text = msg.textContent || '';
                                const html = msg.innerHTML || '';
                                const classes = msg.className || '';
                                const hasFileClass = /file|attach|download|archive|preview/i.test(classes);
                                const hasZip = /\\.zip/i.test(text) || /\\.zip/i.test(html);
                                const hasDownload = msg.querySelector('[download]') !== null;
                                return {{
                                    text: text.slice(0, 150),
                                    hasFileClass,
                                    hasZip,
                                    hasDownload
                                }};
                            }}
                        """) or {}

                        if msg_result.get('hasFileClass') or msg_result.get('hasZip') or msg_result.get('hasDownload'):
                            print(f"  [OK] File found at timeout! Msg #{idx + 1}")
                            return (True, "found", idx + 1)

                    print(f"  [WARN] No file found. Messages: {base_count} -> {final_count}")
                    return (False, "timeout", 0)

                # Periodic status
                if elapsed > 0 and elapsed % 30 == 0:
                    curr = self.page.evaluate(
                        "() => document.querySelectorAll('[class*=\"message\"]').length"
                    ) or 0
                    print(f"  [MONITOR] {elapsed}s | {curr} msgs | no activity: {int(time.time() - last_activity_time)}s")

                no_change_count += 1
                time.sleep(check_interval)

            except Exception as e:
                print(f"  [ERROR] Monitor error: {e}")
                time.sleep(check_interval)

    def _watch_message_feed(self, timeout: int = 10800, progress: bool = True) -> tuple[bool, str]:
        """
        Monitor MAX feed for file confirmation.

        Args:
            timeout: Maximum seconds to wait (default: 10800 = 3 hours)
            progress: Show progress bar

        Returns:
            (confirmed: bool, reason: str)
            reason = "confirmed" | "timeout" | "cancelled"
        """
        self._check_connection()
        hours = timeout // 3600
        mins = (timeout % 3600) // 60
        secs = timeout % 60
        time_str = f"{hours}:{mins:02d}:{secs:02d}" if hours > 0 else f"{mins}:{secs:02d}"

        self.logger.info(f"Watching feed for file confirmation (timeout: {time_str})")

        start = time.time()
        last_progress_time = start
        cancel_requested = False

        initial_count = 0
        initial_last_msg = ""
        try:
            initial_count = self.page.evaluate("""
                () => document.querySelectorAll('[class*="message"]').length
            """) or 0
            initial_last_msg = self.page.evaluate("""
                () => {
                    const msgs = document.querySelectorAll('[class*="message"]');
                    return msgs[msgs.length - 1]?.textContent?.slice(0, 200) || '';
                }
            """) or ""
        except Exception as e:
            self.logger.warning(f"Failed to get initial state: {e}")

        self.logger.debug(f"Initial: count={initial_count}, last_msg={initial_last_msg[:50]}")

        while time.time() - start < timeout:
            remaining = int(timeout - (time.time() - start))

            if progress and time.time() - last_progress_time >= 60:
                last_progress_time = time.time()

                r_hours = remaining // 3600
                r_mins = (remaining % 3600) // 60
                r_secs = remaining % 60
                remaining_str = f"{r_hours}:{r_mins:02d}:{r_secs:02d}" if r_hours > 0 else f"{r_mins}:{r_secs:02d}"

                elapsed = int(time.time() - start)
                e_hours = elapsed // 3600
                e_mins = (elapsed % 3600) // 60
                e_secs = elapsed % 60
                elapsed_str = f"{e_hours}:{e_mins:02d}:{e_secs:02d}" if e_hours > 0 else f"{e_mins}:{e_secs:02d}"

                progress_pct = min(100, int((elapsed / timeout) * 100))
                filled = progress_pct // 2
                empty = 50 - filled
                bar = "█" * filled + "░" * empty

                print(f"\r  [{bar}] {elapsed_str}/{remaining_str} remaining | Q to cancel", end="", flush=True)

            try:
                import select
                import tty

                if sys.platform == 'win32':
                    import msvcrt
                    if msvcrt.kbhit():
                        key = msvcrt.getch()
                        if key in [b'q', b'Q', b'\x03']:
                            cancel_requested = True
                else:
                    if select.select([sys.stdin], [], [], 0)[0]:
                        key = sys.stdin.read(1)
                        if key.lower() == 'q':
                            cancel_requested = True

            except Exception as e:
                self.logger.debug(f"Keyboard check error: {e}")

            if cancel_requested:
                self.logger.info("Cancelled by user")
                return (False, "cancelled")

            time.sleep(2)

            try:
                current_count = self.page.evaluate("""
                    () => document.querySelectorAll('[class*="message"]').length
                """) or 0

                if current_count > initial_count:
                    last_msg = self.page.evaluate("""
                        () => {
                            const msgs = document.querySelectorAll('[class*="message"]');
                            const last = msgs[msgs.length - 1];
                            if (!last) return null;

                            const text = last.textContent || '';
                            const html = last.innerHTML || '';
                            
                            // Check for various file indicators
                            const hasFileClass = last.querySelector(
                                '[class*="file"], [class*="attach"], [class*="download"], [class*="archive"]'
                            ) !== null;
                            
                            // Check for SVG/file icons
                            const hasSvgIcon = last.querySelector('svg') !== null;
                            
                            // Check for download button/link
                            const hasDownloadLink = last.querySelector('a[href*="download"], [download]') !== null;
                            
                            // Check for file size patterns (common in file messages)
                            const hasFileSize = /\\d+\\.?\\d*\\s*(MB|GB|KB|mb|gb|kb)/i.test(text);
                            
                            // Check for archive extensions
                            const hasArchive = /\\.(zip|tar|gz|rar|7z|tgz|zip\\.\\w+)/i.test(text) ||
                                              /\\.(zip|tar|gz|rar|7z|tgz|zip\\.\\w+)/i.test(html);
                            
                            // Check for specific file message patterns
                            const isFileMessage = (
                                hasFileClass ||
                                hasDownloadLink ||
                                hasFileSize ||
                                hasArchive
                            );

                            return {
                                text: text.slice(0, 200),
                                html: html.slice(0, 500),
                                hasFile: isFileMessage,
                                hasFileClass: hasFileClass,
                                hasArchive: hasArchive,
                                hasFileSize: hasFileSize
                            };
                        }
                    """) or {}

                    if last_msg:
                        has_file = last_msg.get('hasFile', False)
                        self.logger.debug(f"New message detected: hasFile={has_file}, text={last_msg.get('text', '')[:50]}")

                        if has_file:
                            elapsed = int(time.time() - start)
                            self.logger.info(f"File confirmed in feed! ({elapsed}s)")
                            self.logger.debug(f"Message details: {last_msg.get('text', '')[:100]}")
                            return (True, "confirmed")

            except Exception as e:
                self.logger.debug(f"Feed check error: {e}")

        self.logger.warning(f"File confirmation timeout ({timeout}s)")
        return (False, "timeout")

    def _find_message_input(self):
        """Find message input field"""
        self._check_connection()
        self.logger.debug("Looking for message input...")

        selectors = [
            '[contenteditable="true"]',
            '[contenteditable]',
            'div[role="textbox"]',
        ]

        for selector in selectors:
            try:
                inp = self.page.locator(selector).first
                if inp.is_visible(timeout=3000):
                    self.logger.debug(f"Input found: {selector}")
                    return inp
            except Exception as e:
                self.logger.debug(f"Selector failed: {selector} ({e})")
                continue

        self.logger.warning("Message input not found")
        return None

    def _click_composer_area(self):
        """Click on message composer area to ensure focus"""
        self._check_connection()
        self.logger.debug("Clicking composer area...")

        selectors = [
            '[contenteditable="true"]',
            '[contenteditable]',
            'div[role="textbox"]',
            '[class*="composer"]',
            '[class*="input"]',
        ]

        for selector in selectors:
            try:
                elem = self.page.locator(selector).first
                if elem.is_visible(timeout=2000):
                    elem.click()
                    self.page.wait_for_timeout(200)
                    self.logger.debug(f"Clicked on: {selector}")
                    return True
            except Exception as e:
                self.logger.debug(f"Selector failed: {selector} ({e})")
                continue

        return False

    def _type_message(self, text: str, input_elem):
        """Type message into input field using clipboard paste"""
        self.logger.debug("Typing message...")

        try:
            import pyperclip

            self._click_composer_area()
            input_elem.scroll_into_view_if_needed()
            input_elem.click(click_count=3)
            self.page.wait_for_timeout(100)

            pyperclip.copy(text)
            self.page.wait_for_timeout(50)

            self.page.keyboard.press("Control+A")
            self.page.wait_for_timeout(50)
            self.page.keyboard.press("Control+V")

            self.logger.debug("Message typed via clipboard")
            return True
        except ImportError:
            self.logger.error("pyperclip not installed")
            return False
        except Exception as e:
            self.logger.error(f"Type failed: {e}")
            return False

    def _send_message(self):
        """Send message (press Enter)"""
        self.logger.debug("Sending message...")

        try:
            self._click_composer_area()
            self.page.wait_for_timeout(100)

            self.page.keyboard.press("Enter")
            self.logger.debug("Message sent (Enter)")
            return True
        except Exception as e:
            self.logger.error(f"Send failed: {e}")
            return False

    def _wait_confirmation(self, timeout: int = 30) -> bool:
        """Wait for message to appear in chat - checks for new file attachment message"""
        self._check_connection()
        self.logger.debug(f"Waiting for confirmation (timeout: {timeout}s)...")

        start = time.time()

        try:
            before_msgs = self.page.evaluate("""
                () => {
                    const msgs = document.querySelectorAll('[class*="message"]');
                    return msgs.length;
                }
            """) or 0
        except Exception as e:
            self.logger.warning(f"Failed to get initial count: {e}")
            before_msgs = 0

        while time.time() - start < timeout:
            time.sleep(2)

            try:
                after_msgs = self.page.evaluate("""
                    () => {
                        const msgs = document.querySelectorAll('[class*="message"]');
                        return msgs.length;
                    }
                """) or 0

                if after_msgs > before_msgs:
                    last_msg = self.page.evaluate("""
                        () => {
                            const msgs = document.querySelectorAll('[class*="message"]');
                            const last = msgs[msgs.length - 1];
                            return {
                                text: last?.textContent?.slice(0, 80) || '',
                                hasFile: last?.querySelector('[class*="file"], [class*="attach"]') !== null,
                                html: last?.innerHTML?.slice(0, 200) || ''
                            };
                        }
                    """) or {}

                    if last_msg.get('hasFile') or 'test' in last_msg.get('text', '').lower():
                        self.logger.info("Message with file appeared!")
                        return True

                    self.logger.debug(f"New message: {last_msg.get('text', '')[:50]}")

                last_msg_text = self.page.evaluate("""
                    () => {
                        const msgs = document.querySelectorAll('[class*="message"]');
                        return msgs[msgs.length - 1]?.textContent?.slice(0, 80) || '';
                    }
                """) or ""

                if last_msg_text and last_msg_text != "Канал создан" and last_msg_text:
                    if "attached" in last_msg_text.lower() or ".zip" in last_msg_text.lower() or ".txt" in last_msg_text.lower():
                        self.logger.info(f"File message: {last_msg_text[:40]}...")
                        return True

            except Exception as e:
                self.logger.debug(f"Check error: {e}")

        self.logger.warning("No confirmation - continuing anyway")
        return True

    def send_message_with_file(self, text: str, filepath: str,
                               retries: int = 3, retry_delay: int = 10,
                               keep_alive: bool = False) -> tuple[bool, bool]:
        """
        Send text message first, then file as second message

        Args:
            keep_alive: If True, don't close connection after sending

        Returns:
            Tuple of (success: bool, file_deletable: bool)
            file_deletable indicates if file can be safely deleted after upload
        """
        valid, err = self._validate_file(filepath)
        if not valid:
            self.logger.error(f"File not found or invalid: {err}")
            return (False, True)  # success=False, file_deletable=True (nothing to clean)

        abs_path = os.path.abspath(filepath)
        file_size_mb = os.path.getsize(abs_path) / 1024 / 1024
        file_size_bytes = os.path.getsize(abs_path)
        filename = os.path.basename(filepath)

        self.logger.info(f"Sending message with file: {filename} ({file_size_mb:.1f} MB)")

        for attempt in range(1, retries + 1):
            try:
                self.logger.info(f"Attempt {attempt}/{retries}")

                if not self.page:
                    self.logger.info("Connecting to MAX...")
                    if not self.connect():
                        raise ConnectionError("Failed to connect to Chrome")

                self.logger.debug("Opening channel...")
                self.navigate()
                self.wait_page_ready()

                self.logger.debug("Typing message...")
                input_elem = self._find_message_input()
                if input_elem:
                    self._type_message(text, input_elem)

                self.logger.debug("Sending about message...")
                self._send_message()
                time.sleep(1)

                self.logger.debug("Opening upload dialog...")
                time.sleep(0.3)

                self.logger.info("Uploading file...")
                upload_timeout = max(60000, int(file_size_mb * 5000))
                self.logger.debug(f"Upload timeout: {upload_timeout//1000}s")

                uploaded = False
                try:
                    with self.page.expect_file_chooser(timeout=upload_timeout) as fc_info:
                        self._click_upload_button()

                    fc_info.value.set_files(abs_path, timeout=upload_timeout)
                    self.logger.info(f"File selected: {filename}")
                    uploaded = True
                except PlaywrightTimeout:
                    self.logger.warning("File chooser timeout, trying input method...")
                    try:
                        file_input = self.page.locator('input[type="file"]').first
                        file_input.set_input_files(abs_path, timeout=upload_timeout)
                        self.logger.info("File uploaded via input")
                        uploaded = True
                    except Exception as e2:
                        self.logger.error(f"Input method also failed: {e2}")
                except Exception as e:
                    self.logger.warning(f"File chooser failed: {e}")
                    try:
                        file_input = self.page.locator('input[type="file"]').first
                        file_input.set_input_files(abs_path, timeout=upload_timeout)
                        self.logger.info("File uploaded via input")
                        uploaded = True
                    except Exception as e2:
                        self.logger.error(f"Input method also failed: {e2}")

                if not uploaded:
                    raise UploadError("Failed to upload file - both methods failed")

                self.logger.debug("Waiting for upload...")
                if not self._wait_upload_complete(expected_filename=filename, expected_size=file_size_bytes):
                    raise UploadError("Upload did not complete in time")

                self.logger.debug("Sending file message...")
                self._send_message()

                self.logger.debug("Waiting for file message confirmation...")
                # Monitor online until file message is found
                found, reason, msg_idx = self._wait_for_file_message(
                    timeout=300,
                    expected_filename=filename
                )
                self.logger.info(f"Result: {reason}, msg #{msg_idx}")

                # If file not confirmed, return failure
                if not found:
                    self.logger.error(f"File not found in chat: {reason}")
                    return (False, True)  # failure, but file may be deletable

                self.logger.info("About + file sent successfully!")
                return (True, True)  # success, file_deletable

            except (ConnectionError, UploadError, PlaywrightTimeout) as e:
                self.logger.error(f"{type(e).__name__}: {e}")

                if attempt < retries:
                    self.logger.info(f"Retrying in {retry_delay}s...")
                    time.sleep(retry_delay)
                else:
                    self.logger.error("Max retries exceeded")
                    return (False, True)  # success=False, file may be deletable
            except Exception as e:
                self.logger.error(f"Unexpected error: {e}", exc_info=True)

                if attempt < retries:
                    self.logger.info(f"Retrying in {retry_delay}s...")
                    time.sleep(retry_delay)
                else:
                    self.logger.error("Max retries exceeded")
                    return (False, True)  # success=False, file may be deletable

        return (False, True)

    def send_message_with_files(self, text: str, filepaths: list[str],
                                retries: int = 3, retry_delay: int = 10,
                                split_threshold_mb: float = 49.0) -> tuple[bool, bool]:
        """
        Send text message with one or more files (supports split archives).

        Files larger than split_threshold_mb will be split using 7z into volumes.
        Each volume is sent as a separate message.

        Args:
            text: Message text
            filepaths: List of file paths to upload
            retries: Number of retries per file
            retry_delay: Delay between retries (seconds)
            split_threshold_mb: Threshold in MB to trigger splitting (default: 49)

        Returns:
            Tuple of (all_success: bool, all_files_deletable: bool)
        """
        all_files = []
        volumes_to_cleanup = []

        # Process each file - split if needed
        for fp in filepaths:
            if not os.path.exists(fp):
                self.logger.error(f"File not found: {fp}")
                continue

            file_size_mb = os.path.getsize(fp) / 1024 / 1024

            if file_size_mb > split_threshold_mb:
                self.logger.info(f"File {os.path.basename(fp)} ({file_size_mb:.1f} MB) > {split_threshold_mb} MB - splitting...")

                # Split into volumes
                volumes = split_file_with_7z(fp, SEVEN_ZIP_VOLUME_SIZE)

                if volumes:
                    self.logger.info(f"Split into {len(volumes)} volumes")
                    all_files.extend(volumes)
                    volumes_to_cleanup.extend(volumes)
                else:
                    # Split failed, try sending original
                    self.logger.warning("Split failed, trying original file")
                    all_files.append(fp)
            else:
                all_files.append(fp)

        if not all_files:
            self.logger.error("No files to upload")
            return (False, True)

        self.logger.info(f"Uploading {len(all_files)} file(s)")

        # Ensure connected
        if not self.page:
            self.logger.info("Connecting to MAX...")
            if not self.connect():
                raise ConnectionError("Failed to connect to Chrome")

        self.navigate()
        self.wait_page_ready()

        # Send message text first
        self.logger.debug("Typing message...")
        input_elem = self._find_message_input()
        if input_elem:
            self._type_message(text, input_elem)

        self.logger.debug("Sending about message...")
        self._send_message()
        time.sleep(1)

        # Upload each file - delete immediately after confirmation
        all_success = True
        all_deletable = True

        for i, fp in enumerate(all_files, 1):
            filename = os.path.basename(fp)
            file_size_bytes = os.path.getsize(fp)
            file_size_mb = file_size_bytes / 1024 / 1024

            self.logger.info(f"Uploading file {i}/{len(all_files)}: {filename} ({file_size_mb:.1f} MB)")

            success = self._upload_single_file(
                fp, filename, file_size_bytes,
                retries=retries, retry_delay=retry_delay
            )

            if not success:
                all_success = False
                self.logger.error(f"Failed to upload: {filename}")
                # Keep failed file for potential retry
            else:
                self.logger.info(f"✓ Uploaded: {filename}")
                # Delete volume IMMEDIATELY after confirmation
                # This ensures partial uploads are not lost on interrupt
                if fp in volumes_to_cleanup:
                    try:
                        if os.path.exists(fp):
                            os.remove(fp)
                            self.logger.debug(f"Deleted: {filename}")
                    except Exception as e:
                        self.logger.warning(f"Failed to delete {filename}: {e}")

            # Small delay between files
            if i < len(all_files):
                time.sleep(1)

        # Cleanup any remaining volumes (should be none with immediate delete)
        if volumes_to_cleanup:
            remaining = [v for v in volumes_to_cleanup if os.path.exists(v)]
            if remaining:
                self.logger.info(f"Cleaning up {len(remaining)} remaining volumes...")
                cleanup_volumes(remaining)

        return (all_success, all_deletable)

    def _upload_single_file(self, filepath: str, filename: str, file_size_bytes: int,
                            retries: int = 3, retry_delay: int = 10) -> bool:
        """
        Upload a single file and wait for confirmation.

        Args:
            filepath: Absolute path to file
            filename: Display name for the file
            file_size_bytes: File size in bytes
            retries: Number of retries
            retry_delay: Delay between retries

        Returns:
            True if upload successful
        """
        file_size_mb = file_size_bytes / 1024 / 1024

        for attempt in range(1, retries + 1):
            try:
                self.logger.debug(f"Attempt {attempt}/{retries}")

                upload_timeout = max(60000, int(file_size_mb * 5000))
                self.logger.debug(f"Upload timeout: {upload_timeout//1000}s")

                uploaded = False
                try:
                    with self.page.expect_file_chooser(timeout=upload_timeout) as fc_info:
                        self._click_upload_button()

                    fc_info.value.set_files(filepath, timeout=upload_timeout)
                    self.logger.info(f"File selected: {filename}")
                    uploaded = True
                except PlaywrightTimeout:
                    self.logger.warning("File chooser timeout, trying input method...")
                    try:
                        file_input = self.page.locator('input[type="file"]').first
                        file_input.set_input_files(filepath, timeout=upload_timeout)
                        self.logger.info("File uploaded via input")
                        uploaded = True
                    except Exception as e2:
                        self.logger.error(f"Input method also failed: {e2}")
                except Exception as e:
                    self.logger.warning(f"File chooser failed: {e}")
                    try:
                        file_input = self.page.locator('input[type="file"]').first
                        file_input.set_input_files(filepath, timeout=upload_timeout)
                        self.logger.info("File uploaded via input")
                        uploaded = True
                    except Exception as e2:
                        self.logger.error(f"Input method also failed: {e2}")

                if not uploaded:
                    self.logger.error("Failed to upload file - both methods failed")
                    if attempt < retries:
                        time.sleep(retry_delay)
                    continue

                self.logger.debug("Waiting for upload...")
                if not self._wait_upload_complete(expected_filename=filename, expected_size=file_size_bytes):
                    self.logger.error("Upload did not complete in time")
                    if attempt < retries:
                        time.sleep(retry_delay)
                    continue

                self.logger.debug("Sending file message...")
                self._send_message()

                self.logger.debug("Waiting for file message confirmation...")
                found, reason, msg_idx = self._wait_for_file_message(
                    timeout=300,
                    expected_filename=filename
                )
                self.logger.info(f"Result: {reason}, msg #{msg_idx}")

                if found:
                    return True
                else:
                    self.logger.error(f"File not found in chat: {reason}")
                    if attempt < retries:
                        time.sleep(retry_delay)
                    continue

            except Exception as e:
                self.logger.error(f"Upload error: {e}", exc_info=True)
                if attempt < retries:
                    time.sleep(retry_delay)
                else:
                    return False

        return False

    def send_message_with_file(self, text: str, filepath: str,
                               retries: int = 3, retry_delay: int = 10,
                               keep_alive: bool = False) -> tuple[bool, bool]:
        """
        Send text message first, then file as second message.

        Args:
            keep_alive: If True, don't close connection after sending

        Returns:
            Tuple of (success: bool, file_deletable: bool)
            file_deletable indicates if file can be safely deleted after upload
        """
        # Use the new multi-file method for backward compatibility
        success, deletable = self.send_message_with_files(
            text=text,
            filepaths=[filepath],
            retries=retries,
            retry_delay=retry_delay
        )
        return (success, deletable)

    def close(self):
        """Close browser connection gracefully"""
        self.logger.debug("Closing connection...")
        try:
            if self.browser:
                self.browser.close()
            if self.playwright:
                self.playwright.stop()
        except Exception as e:
            self.logger.warning(f"Error during cleanup: {e}")
        finally:
            self._connected = False
            self.playwright = None
            self.browser = None
            self.page = None
        self.logger.debug("Connection closed")


if __name__ == "__main__":
    print("Browser MAX module (Playwright)")
    print("Usage: from browser_max import BrowserMAX")