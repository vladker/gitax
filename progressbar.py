"""Live progress bar with ETA, speed, and in-place rendering.

Usage:
    with LiveProgressBar(total=100, label="Processing") as bar:
        for i, item in enumerate(items, 1):
            # ... process item ...
            bar.update(i, item_name=item.name)

    # Or manual:
    bar = LiveProgressBar(total=50, label="Syncing")
    bar.start()
    for i in range(50):
        # ...
        bar.update(i + 1)
    bar.finish()

Features:
    - Animated progress bar: [████████░░] 80%
    - ETA: estimated time remaining
    - Speed: items/second
    - Current item name (optional)
    - Per-item timing (smooth EMA)
    - Windows VT100 support
    - Thread-safe
    - Auto-detects terminal width
"""

from __future__ import annotations

import os
import sys
import time
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Platform helpers
# ---------------------------------------------------------------------------

def _enable_vt100() -> None:
    """Enable ANSI escape sequences on Windows."""
    if os.name != "nt":
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.GetStdHandle(-11)
        mode: ctypes.c_ulong = ctypes.c_ulong()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:
        pass


def _supports_color() -> bool:
    """Check if terminal supports ANSI colors."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    # Windows 10+ with VT100
    if os.name == "nt" and sys.platform == "win32":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            handle = kernel32.GetStdHandle(-11)
            mode: ctypes.c_ulong = ctypes.c_ulong()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                return bool(mode.value & 0x0004) or True  # VT100 enabled
        except Exception:
            pass
        return False
    # Unix: check if stdout is a tty
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


# ---------------------------------------------------------------------------
# ANSI helpers
# ---------------------------------------------------------------------------

class _ANSI:
    CLEAR_LINE = "\033[2K\r"
    BOLD = "\033[1m"
    RESET = "\033[0m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    CYAN = "\033[96m"
    DIM = "\033[2m"

    @classmethod
    def maybe(cls, seq: str) -> str:
        """Return ANSI sequence only if terminal supports it."""
        return seq if _supports_color() else ""


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ProgressState:
    """Mutable state for a progress bar."""
    current: int = 0
    total: int = 0
    item_name: str = ""
    start_time: float = 0.0
    last_update_time: float = 0.0
    _item_durations: list[float] = field(default_factory=list)
    _ema_speed: float = 0.0  # items per second (exponential moving average)
    _last_render: str = ""
    _finished: bool = False
    _status: str = ""  # "success", "error", "cancelled"


# ---------------------------------------------------------------------------
# LiveProgressBar
# ---------------------------------------------------------------------------

class LiveProgressBar:
    """Live-updating progress bar with ETA and speed.

    Args:
        total: Total number of items.
        label: Display label (e.g. "Processing repos").
        width: Width of the bar in characters (default: auto).
        show_eta: Show ETA (default: True).
        show_speed: Show items/sec (default: True).
        show_name: Show current item name (default: True).
    """

    def __init__(
        self,
        total: int,
        label: str = "Processing",
        width: int | None = None,
        show_eta: bool = True,
        show_speed: bool = True,
        show_name: bool = True,
    ):
        self.total = max(1, total)
        self.label = label
        self.width = width or min(30, self._terminal_width() // 4)
        self.show_eta = show_eta
        self.show_speed = show_speed
        self.show_name = show_name

        self._state = ProgressState(total=total)
        self._lock = threading.Lock()
        self._render_thread: threading.Thread | None = None
        self._render_interval = 0.5  # seconds between auto-renders

    # ---- context manager ----

    def __enter__(self) -> LiveProgressBar:
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        status = "error" if exc_type else "success"
        self.finish(status=status)

    # ---- public API ----

    def start(self) -> None:
        """Start the progress bar."""
        with self._lock:
            self._state.start_time = time.monotonic()
            self._state.last_update_time = self._state.start_time
            self._render("  " + self.label)

    def update(self, current: int, item_name: str = "") -> None:
        """Update progress to `current` (1-indexed)."""
        with self._lock:
            now = time.monotonic()
            if self._state.current > 0:
                duration = now - self._state.last_update_time
                self._state._item_durations.append(duration)
                # Keep last 20 samples for EMA
                if len(self._state._item_durations) > 20:
                    self._state._item_durations.pop(0)

            self._state.current = min(current, self.total)
            self._state.last_update_time = now
            self._state.item_name = item_name
            self._render()

    def finish(self, status: str = "success") -> None:
        """Finish the progress bar with a final status."""
        with self._lock:
            self._state.current = self.total
            self._state._finished = True
            self._state._status = status
            self._render()
            # Print a newline after the bar
            print()

    def set_total(self, total: int) -> None:
        """Update the total count (useful when total changes dynamically)."""
        with self._lock:
            self.total = max(1, total)
            self._state.total = total
            self._render()

    # ---- internals ----

    def _render(self, prefix: str = "") -> None:
        """Render the current progress line."""
        if not _supports_color() and not os.environ.get("FORCE_COLOR"):
            # Fallback: simple text
            self._render_simple(prefix)
            return

        state = self._state
        pct = state.current / state.total if state.total > 0 else 0.0

        # Bar
        filled = int(pct * self.width)
        empty = self.width - filled
        bar = f"[{_ANSI.GREEN}{'█' * filled}{_ANSI.RESET}{'░' * empty}]"

        # Percentage
        pct_str = f"{pct * 100:.0f}%"

        # Counter
        counter = f"{state.current}/{state.total}"

        # ETA
        eta_str = ""
        if self.show_eta and not state._finished and state.current > 0:
            avg_dur = sum(state._item_durations) / len(state._item_durations) if state._item_durations else 0
            remaining = state.total - state.current
            eta_sec = avg_dur * remaining
            eta_str = f" ETA: {self._format_duration(eta_sec)}"

        # Speed
        speed_str = ""
        if self.show_speed and state._item_durations:
            avg_dur = sum(state._item_durations) / len(state._item_durations)
            speed = 1.0 / avg_dur if avg_dur > 0 else 0
            speed_str = f" {speed:.1f}/s"

        # Item name
        name_str = ""
        if self.show_name and state.item_name:
            # Truncate if too long
            max_name_len = max(10, self._terminal_width() // 3 - 40)
            name = state.item_name[:max_name_len]
            if len(state.item_name) > max_name_len:
                name += "…"
            name_str = f" {name}"

        # Status icon
        status_icon = ""
        if state._finished:
            if state._status == "success":
                status_icon = f" {_ANSI.GREEN}✓{_ANSI.RESET}"
            elif state._status == "error":
                status_icon = f" {_ANSI.RED}✗{_ANSI.RESET}"
            else:
                status_icon = f" {_ANSI.YELLOW}⚠{_ANSI.RESET}"

        # Elapsed
        elapsed = time.monotonic() - state.start_time
        elapsed_str = f" {self._format_duration(elapsed)}"

        # Assemble
        line = f"{_ANSI.CLEAR_LINE}{prefix} {bar} {pct_str} {counter}{eta_str}{speed_str}{elapsed_str}{name_str}{status_icon}"

        # Clear previous line if needed
        if self._state._last_render:
            # Move cursor up to overwrite
            print(f"\r{' ' * len(self._state._last_render)}\r", end="", flush=True)

        print(line, end="", flush=True)
        self._state._last_render = line

    def _render_simple(self, prefix: str = "") -> None:
        """Simple text fallback when ANSI is not available."""
        state = self._state
        pct = state.current / state.total if state.total > 0 else 0.0
        counter = f"{state.current}/{state.total}"
        elapsed = time.monotonic() - state.start_time

        if state._finished:
            status = "✓" if state._status == "success" else "✗"
            print(f"{prefix} [{counter}] {self.label} — {status} ({self._format_duration(elapsed)})")
        else:
            print(f"\r{prefix} [{counter}] {self.label}", end="", flush=True)

    @staticmethod
    def _format_duration(seconds: float) -> str:
        """Format seconds to human-readable duration."""
        if seconds < 1:
            return f"{seconds:.1f}s"
        elif seconds < 60:
            return f"{int(seconds)}s"
        elif seconds < 3600:
            mins = int(seconds // 60)
            secs = int(seconds % 60)
            return f"{mins}m {secs}s"
        else:
            hours = int(seconds // 3600)
            mins = int((seconds % 3600) // 60)
            return f"{hours}h {mins}m"

    @staticmethod
    def _terminal_width() -> int:
        """Get terminal width, default 80."""
        try:
            return os.get_terminal_size().columns
        except OSError:
            return 80


# ---------------------------------------------------------------------------
# Convenience: archive_progress context manager
# ---------------------------------------------------------------------------

@contextmanager
def archive_progress(total: int, label: str = "Archiving", **kwargs):
    """Context manager for archiver loops.

    Usage:
        with archive_progress(100, "Processing repos") as bar:
            for i, repo in enumerate(repos, 1):
                process(repo)
                bar.update(i, item_name=repo.name)
    """
    with LiveProgressBar(total=total, label=label, **kwargs) as bar:
        yield bar


# ---------------------------------------------------------------------------
# Init on import
# ---------------------------------------------------------------------------

_enable_vt100()
