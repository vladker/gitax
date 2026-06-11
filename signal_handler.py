"""Reusable signal handling and cleanup registration."""

from __future__ import annotations

import atexit
import signal
import logging


class SignalHandler:
    """
    Register signal handlers and atexit cleanup.
    """
    
    def __init__(self):
        self._registered = False
        self._cleanup_callbacks = []
    
    def register(
        self,
        obj: object,
        shutdown_attr: str = "_shutdown",
        on_signal=None,
        on_cleanup=None,
    ) -> None:
        """
        Register signal handlers and atexit cleanup for an object.
        """
        if self._registered:
            return
        self._registered = True
        
        logger = logging.getLogger("gitax")
        
        def _handler(signum, frame):
            logger.info(f"Received signal {signum}, shutting down...")
            if shutdown_attr and hasattr(obj, shutdown_attr):
                setattr(obj, shutdown_attr, True)
            if on_signal:
                on_signal(signum, frame)
        
        def _cleanup():
            if on_cleanup:
                try:
                    on_cleanup()
                except Exception:
                    pass
        
        atexit.register(_cleanup)
        signal.signal(signal.SIGINT, _handler)
        signal.signal(signal.SIGTERM, _handler)
