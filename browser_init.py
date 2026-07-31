"""Browser initialization mixin for archiver classes."""

from __future__ import annotations

from browser_max import BrowserMAX, BrowserConnectionError


class BrowserInitMixin:
    """
    Mixin providing _init_browser() and _ensure_browser_connected().
    
    Subclasses must define:
    - self.config: dict with config data
    - self.browser: BrowserMAX | None
    
    Override _channel_key and _section_key to customize config lookup.
    """
    
    _channel_key: str = "max"
    _section_key: str | None = None
    
    def _init_browser(self) -> BrowserMAX:
        """Initialize BrowserMAX, reusing existing connection if alive."""
        if self.browser is None:
            channel_url = self.config.get("channels", {}).get(self._channel_key, "")
            section = self._section_key or self._channel_key
            use_local = self.config.get(section, {}).get(
                "use_local_browser",
                self.config.get("archiver", {}).get("use_local_browser", False),
            )
            self.browser = BrowserMAX(channel_url, use_local_browser=use_local)
        return self.browser
    
    def _ensure_browser_connected(self) -> BrowserMAX:
        """Ensure browser is connected and ready."""
        browser = self._init_browser()
        if not browser.keep_alive_connect():
            raise BrowserConnectionError("Failed to connect to MAX")
        browser.navigate()
        browser.ensure_page_ready()
        return browser
    
    def _close_browser(self) -> None:
        """Safely close browser connection."""
        if self.browser:
            try:
                self.browser.close()
            except Exception:
                pass
            self.browser = None
