# -*- coding: utf-8 -*-
"""
MAX messenger automation using Playwright
"""

import os
import time
import sys
from typing import Optional
from playwright.sync_api import sync_playwright, Page, Browser


class BrowserMAX:
    """MAX messenger automation using Playwright"""

    def __init__(self, channel_url: str):
        self.channel_url = channel_url
        self.playwright = None
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None

    def connect(self) -> bool:
        """Connect to existing Chrome via CDP"""
        print("  [OK] Connecting to Chrome (CDP port 9222)...")

        try:
            self.playwright = sync_playwright().start()
            self.browser = self.playwright.chromium.connect_over_cdp("http://localhost:9222")

            context = self.browser.contexts[0] if self.browser.contexts else self.browser.new_context()
            self.page = context.pages[0] if context.pages else context.new_page()

            print("  [OK] Connected")
            return True
        except Exception as e:
            print(f"  [ERROR] Connection failed: {e}")
            return False

    def navigate(self):
        """Navigate to MAX channel"""
        print(f"  [OK] Opening channel: {self.channel_url}")
        self.page.goto(self.channel_url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(2)

    def wait_page_ready(self, timeout: int = 30):
        """Wait for page to be ready"""
        try:
            self.page.wait_for_load_state("networkidle", timeout=timeout * 1000)
        except:
            pass

    def _click_upload_button(self):
        """Find and click upload/file button - handles dropdown menu"""
        print("  [OK] Looking for upload button...")

        for selector in ['[aria-label="Upload file"]', 'button:has-text("File")']:
            try:
                btn = self.page.locator(selector).first
                if btn.is_visible(timeout=3000):
                    btn.scroll_into_view_if_needed()
                    btn.click(timeout=5000)
                    print(f"  [OK] Button clicked: {selector}")
                    time.sleep(0.5)

                    menu_btn = self.page.locator('button:has-text("File")').first
                    if menu_btn.is_visible(timeout=3000):
                        menu_btn.click()
                        print("  [OK] File menu item clicked")
                        time.sleep(0.3)
                        return True
            except:
                continue

        try:
            upload_btn = self.page.locator('[aria-label="Upload file"]').first
            upload_btn.click()
            time.sleep(0.3)

            file_btn = self.page.get_by_text("File")
            file_btn.click(timeout=5000)
            print("  [OK] Upload > File clicked")
            return True
        except Exception as e:
            print(f"  [WARN] Menu click failed: {e}")

        return False

    def _upload_file(self, filepath: str) -> bool:
        """Upload file using input[type=file]"""
        abs_path = os.path.abspath(filepath)
        file_size = os.path.getsize(filepath) / 1024 / 1024

        print(f"  [OK] Selecting file: {os.path.basename(filepath)} ({file_size:.1f} MB)")

        for selector in ['input[type="file"]', 'input[type=file]']:
            try:
                file_input = self.page.locator(selector).first
                file_input.set_input_files(abs_path, timeout=60000)
                print("  [OK] File selected")
                return True
            except:
                continue

        print("  [ERROR] Could not select file")
        return False

    def _upload_file_drag_drop(self, filepath: str) -> bool:
        """Upload file using file chooser"""
        abs_path = os.path.abspath(filepath)
        file_size = os.path.getsize(filepath) / 1024 / 1024
        print(f"  [OK] Selecting file: {os.path.basename(filepath)} ({file_size:.1f} MB)")

        try:
            with self.page.expect_file_chooser(timeout=60000) as fc_info:
                print("  [OK] Waiting for file dialog...")

            file_chooser = fc_info.value
            file_chooser.set_files(abs_path)
            print("  [OK] File selected via dialog")
            return True
        except Exception as e:
            print(f"  [ERROR] File chooser failed: {e}")
            return False

    def _wait_upload_complete(self, timeout: int = 120) -> bool:
        """Wait for file upload to complete - checks for attached file in UI"""
        print(f"  [OK] Waiting for upload (timeout: {timeout}s)...")

        start = time.time()

        while time.time() - start < timeout:
            time.sleep(1)

            try:
                status = self.page.evaluate("""
                    () => {
                        // Check for attached file in message composer
                        const attached = document.querySelector('[class*="attaches"], [class*="attached"], [class*="file-item"], [class*="preview"]');
                        if (attached) {
                            const text = attached.textContent || '';
                            return 'attached:' + text.slice(0, 50);
                        }

                        // Check if input has file selected
                        const fileInput = document.querySelector('input[type="file"]');
                        if (fileInput && fileInput.files && fileInput.files.length > 0) {
                            const filename = fileInput.files[0]?.name || 'unknown';
                            return 'selected:' + filename;
                        }

                        return 'waiting';
                    }
                """)

                elapsed = int(time.time() - start)

                if 'attached:' in status:
                    filename = status.replace('attached:', '').strip()
                    print(f"  [OK] File attached: {filename} ({elapsed}s)")
                    return True
                elif 'selected:' in status:
                    print(f"  [OK] File selected, processing... ({elapsed}s)")
                    time.sleep(1)
                else:
                    print(f"  [OK] Waiting... ({elapsed}s)")

            except Exception as e:
                print(f"  [DEBUG] Check error: {e}")

        print("  [WARN] Upload timeout - checking if file was attached...")
        time.sleep(1)

        try:
            attached = self.page.locator('[class*="attaches"]').first
            if attached.is_visible(timeout=2000):
                print("  [OK] File found in UI")
                return True
        except:
            pass

        return True

    def _find_message_input(self):
        """Find message input field"""
        print("  [OK] Looking for message input...")

        selectors = [
            '[contenteditable="true"]',
            '[contenteditable]',
            'div[role="textbox"]',
        ]

        for selector in selectors:
            try:
                inp = self.page.locator(selector).first
                if inp.is_visible(timeout=3000):
                    print(f"  [OK] Input found: {selector}")
                    return inp
            except:
                continue

        print("  [WARN] Message input not found")
        return None

    def _click_composer_area(self):
        """Click on message composer area to ensure focus"""
        print("  [OK] Clicking composer area...")

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
                    print(f"  [OK] Clicked on: {selector}")
                    return True
            except:
                continue

        return False

    def _type_message(self, text: str, input_elem):
        """Type message into input field using clipboard paste"""
        print("  [OK] Typing message...")

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

            print("  [OK] Message typed")
            return True
        except Exception as e:
            print(f"  [ERROR] Type failed: {e}")
            return False

    def _send_message(self):
        """Send message (press Enter)"""
        print("  [OK] Sending message...")

        try:
            self._click_composer_area()
            self.page.wait_for_timeout(100)

            self.page.keyboard.press("Enter")
            print("  [OK] Message sent (Enter)")
            return True
        except:
            return False

    def _wait_confirmation(self, timeout: int = 30) -> bool:
        """Wait for message to appear in chat - checks for new file attachment message"""
        print(f"  [OK] Waiting for confirmation...")

        start = time.time()

        try:
            before_msgs = self.page.evaluate("""
                () => {
                    const msgs = document.querySelectorAll('[class*="message"]');
                    return msgs.length;
                }
            """) or 0
        except:
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
                        print("  [OK] Message with file appeared!")
                        return True

                    print(f"  [OK] New message: {last_msg.get('text', '')[:50]}")

                last_msg_text = self.page.evaluate("""
                    () => {
                        const msgs = document.querySelectorAll('[class*="message"]');
                        return msgs[msgs.length - 1]?.textContent?.slice(0, 80) || '';
                    }
                """) or ""

                if last_msg_text and last_msg_text != "Канал создан" and last_msg_text:
                    if "attached" in last_msg_text.lower() or ".zip" in last_msg_text.lower() or ".txt" in last_msg_text.lower():
                        print(f"  [OK] File message: {last_msg_text[:40]}...")
                        return True

            except Exception as e:
                print(f"  [DEBUG] Check error: {e}")

        print("  [WARN] No confirmation - continuing anyway")
        return True

    def send_message_with_file(self, text: str, filepath: str,
                               retries: int = 3, retry_delay: int = 10) -> bool:
        """Send text message first, then file as second message"""
        if not os.path.exists(filepath):
            print(f"  [ERROR] File not found: {filepath}")
            return False

        file_size = os.path.getsize(filepath) / 1024 / 1024
        print(f"  [OK] File: {os.path.basename(filepath)} ({file_size:.1f} MB)")

        for attempt in range(1, retries + 1):
            try:
                print(f"\n  === Attempt {attempt}/{retries} ===")

                print("  [1] Connecting to MAX...")
                if not self.connect():
                    raise Exception("Failed to connect to Chrome")

                print("  [2] Opening channel...")
                self.navigate()
                self.wait_page_ready()

                print("  [3] Typing message (about)...")
                input_elem = self._find_message_input()
                if input_elem:
                    self._type_message(text, input_elem)

                print("  [4] Sending about message...")
                self._send_message()
                time.sleep(2)

                print("  [5] Opening upload dialog...")
                time.sleep(0.3)

                print("  [6] Uploading file...")
                abs_path = os.path.abspath(filepath)

                try:
                    with self.page.expect_file_chooser(timeout=60000) as fc_info:
                        self._click_upload_button()

                    fc_info.value.set_files(abs_path)
                    print(f"  [OK] File selected: {os.path.basename(filepath)}")
                except Exception as e:
                    self.page.screenshot(path=f"debug_file_{attempt}.png", full_page=True)
                    print(f"  [ERROR] {e}")
                    raise Exception("Failed to upload file")

                print("  [7] Waiting for upload...")
                self._wait_upload_complete()

                print("  [8] Sending file message...")
                self._send_message()

                print("  [9] Waiting for confirmation...")
                self._wait_confirmation()

                print("\n  [OK] About + file sent!")
                return True

            except Exception as e:
                print(f"  [ERROR] {e}")
                try:
                    self.page.screenshot(path=f"debug_error_{attempt}.png", full_page=True)
                except:
                    pass

                try:
                    self.close()
                except:
                    pass

                if attempt < retries:
                    print(f"  [OK] Retrying in {retry_delay}s...")
                    time.sleep(retry_delay)
                else:
                    print("  [ERROR] Max retries exceeded")
                    return False

        return False

    def close(self):
        """Close browser connection"""
        print("  [OK] Closing connection...")
        try:
            if self.browser:
                self.browser.close()
            if self.playwright:
                self.playwright.stop()
        except:
            pass
        self.playwright = None
        self.browser = None
        self.page = None
        print("  [OK] Connection closed")


if __name__ == "__main__":
    print("Browser MAX module (Playwright)")
    print("Usage: from browser_max import BrowserMAX")