"""A single white flash across every display — the macOS ``FlashOverlay``.

Announces that the watchdog armed itself while nobody was at the machine, so whoever
sits down knows their next keystrokes go to a password prompt rather than into
whatever app is focused. It is a notification, not a lock: the windows are
click-through (``WindowTransparentForInput``) and the machine stays usable
underneath. Ramps to white in ~80 ms, fades out over ~280 ms, then dismisses.
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt, QVariantAnimation
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QWidget

_RAMP_MS = 80
_FADE_MS = 280


class FlashOverlay:
    def __init__(self) -> None:
        self._windows: List[QWidget] = []
        self._anim: Optional[QVariantAnimation] = None

    def flash(self) -> None:
        if self._windows:
            return  # already flashing

        for screen in QGuiApplication.screens():
            window = QWidget()
            window.setWindowFlags(
                Qt.FramelessWindowHint
                | Qt.WindowStaysOnTopHint
                | Qt.Tool
                | Qt.X11BypassWindowManagerHint
                | Qt.WindowTransparentForInput
            )
            window.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            window.setAttribute(Qt.WA_ShowWithoutActivating, True)
            window.setStyleSheet("background-color: white;")
            window.setWindowOpacity(0.0)
            window.setGeometry(screen.geometry())
            window.setScreen(screen)
            # Geometry is the exact screen rect and the window bypasses the WM, so
            # show() lands it on the right monitor without showFullScreen()'s
            # screen re-resolution (see overlay.py).
            window.show()
            self._windows.append(window)

        total = _RAMP_MS + _FADE_MS
        anim = QVariantAnimation()
        anim.setDuration(total)
        anim.setKeyValueAt(0.0, 0.0)
        anim.setKeyValueAt(_RAMP_MS / total, 1.0)
        anim.setKeyValueAt(1.0, 0.0)
        anim.valueChanged.connect(self._apply)
        anim.finished.connect(self._dismiss)
        self._anim = anim
        anim.start()

    def _apply(self, value) -> None:
        for window in self._windows:
            window.setWindowOpacity(float(value))

    def _dismiss(self) -> None:
        for window in self._windows:
            window.close()
        self._windows = []
        self._anim = None
