"""System-wide input-idle time, the Linux analogue of the macOS
``CGEventSource.secondsSinceLastEventType(.hidSystemState, …)``.

Read from the X11 MIT-SCREEN-SAVER extension, which reports milliseconds since the
last real hardware input across the whole session — exactly what auto-arm needs, and
independent of anything SzpontLock itself sees. A dedicated Xlib connection is kept
open so the 5-second poll never re-handshakes.
"""

from __future__ import annotations

from typing import Optional


class IdleMonitor:
    def __init__(self) -> None:
        self._display = None
        self._root = None
        self._connect()

    def _connect(self) -> None:
        try:
            from Xlib import display  # imported lazily so tests need no X server

            self._display = display.Display()
            if not self._display.has_extension("MIT-SCREEN-SAVER"):
                self._display = None
                return
            self._root = self._display.screen().root
        except Exception:
            # No display, or the extension is missing: report "unknown" and let the
            # controller decline to auto-arm rather than fire blind.
            self._display = None
            self._root = None

    def seconds_idle(self) -> Optional[float]:
        """Seconds since the last hardware input, or ``None`` if it cannot be read —
        in which case auto-arm declines rather than arming an actively used machine."""
        if self._root is None:
            self._connect()
        if self._root is None:
            return None
        try:
            info = self._root.screensaver_query_info()
            return info.idle / 1000.0
        except Exception:
            # A server round-trip can fail if the display went away; drop the handle
            # so the next poll reconnects.
            self._display = None
            self._root = None
            return None
