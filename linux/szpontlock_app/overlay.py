"""The lock screen: one opaque, always-on-top shield window per display.

A port of the macOS ``LockOverlay`` + SwiftUI ``LockScreenView``. The interactive
panel (typed-character dots, fingerprint button, timestamps) goes on the **primary**
display; the **topmost** display gets ``Resources/lockdown.png`` strobing
black -> picture -> white for two seconds before holding black; every other display
is plain blackout. With a single display the panel is drawn over the strobe on the
one screen.

Shield windows use ``X11BypassWindowManagerHint`` and cover each screen's full
geometry, which puts them above panels, docks and notifications — the X11 stand-in
for ``CGShieldingWindowLevel``. Unlike macOS there is no separate ``coreautha`` panel
to duck under (fprintd has no window of its own), so ``set_biometric_prompt_visible``
only tracks state.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


def lockdown_image_path() -> Optional[Path]:
    """``Resources/lockdown.png`` — the shared asset the macOS build bundles. Missing
    just means no picture, never a crash mid-lockdown."""
    forced = os.environ.get("SZPONTLOCK_LOCKDOWN_IMAGE")
    if forced:
        p = Path(forced)
        return p if p.exists() else None
    repo_root = Path(__file__).resolve().parents[2]
    candidate = repo_root / "Resources" / "lockdown.png"
    return candidate if candidate.exists() else None


class _StrobeWidget(QWidget):
    """Cycles black -> picture -> white at ~60 ms a phase for two seconds, then holds
    black for the rest of the lockdown — the macOS ``StrobeView``.

    WARNING (carried over from the macOS build): the strobe flashes black to white at
    roughly 5.5 Hz, inside the range that can provoke photosensitive seizures.
    """

    _PHASE_MS = 60
    _STROBE_MS = 2000

    def __init__(self, image: Optional[QPixmap], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._image = image
        self._phase = 0
        self._finished = False
        self._elapsed = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(self._PHASE_MS)

    def _tick(self) -> None:
        self._elapsed += self._PHASE_MS
        if self._elapsed >= self._STROBE_MS:
            self._finished = True
            self._timer.stop()
            self.update()
            return
        self._phase += 1
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("black"))
        if self._finished:
            return
        phase = self._phase % 3
        if phase == 1 and self._image is not None and not self._image.isNull():
            scaled = self._image.scaled(
                self.size(),
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
            x = (self.width() - scaled.width()) // 2
            y = (self.height() - scaled.height()) // 2
            painter.drawPixmap(x, y, scaled)
        elif phase == 2:
            painter.fillRect(self.rect(), QColor("white"))


class _DotsWidget(QWidget):
    """The twelve keystroke dots — filled as characters are typed at the shield."""

    _COUNT = 12

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._typed = 0
        self.setFixedSize(self._COUNT * 18, 12)

    def set_typed(self, count: int) -> None:
        self._typed = count
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        filled = QColor("white")
        empty = QColor(64, 64, 64)
        for i in range(self._COUNT):
            painter.setBrush(filled if i < min(self._typed, self._COUNT) else empty)
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(i * 18, 2, 9, 9)


class _Panel(QWidget):
    """The interactive lock panel on the primary display."""

    def __init__(self, on_touch_id: Callable[[], None], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._on_touch_id = on_touch_id
        self.setAttribute(Qt.WA_TranslucentBackground)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(20)

        glyph = QLabel("\U0001F512")  # 🔒
        glyph.setAlignment(Qt.AlignCenter)
        glyph.setStyleSheet("color: #F25151; font-size: 48px;")
        layout.addWidget(glyph)

        title = QLabel("LOCKED")
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(
            "color: white; font-size: 30px; font-weight: 600; letter-spacing: 6px;"
        )
        layout.addWidget(title)

        self._message = QLabel("Fingerprint, or type your unlock sequence")
        self._message.setAlignment(Qt.AlignCenter)
        self._message.setStyleSheet("color: #9E9E9E; font-size: 14px;")
        layout.addWidget(self._message)

        dots_row = QHBoxLayout()
        dots_row.setAlignment(Qt.AlignCenter)
        self._dots = _DotsWidget()
        dots_row.addWidget(self._dots)
        layout.addLayout(dots_row)

        self._button = QPushButton("Unlock with fingerprint")
        self._button.setCursor(Qt.PointingHandCursor)
        self._button.setStyleSheet(
            "QPushButton { color: white; background-color: #292929;"
            " border-radius: 15px; padding: 9px 18px; font-size: 13px; }"
            "QPushButton:disabled { color: #7A7A7A; }"
        )
        self._button.clicked.connect(lambda: self._on_touch_id())
        button_row = QHBoxLayout()
        button_row.setAlignment(Qt.AlignCenter)
        button_row.addWidget(self._button)
        layout.addLayout(button_row)

        self._footer = QLabel("")
        self._footer.setAlignment(Qt.AlignCenter)
        self._footer.setStyleSheet("color: #6B6B6B; font-size: 11px;")
        layout.addWidget(self._footer)

    def set_message(self, text: str) -> None:
        self._message.setText(text)

    def set_typed_count(self, count: int) -> None:
        self._dots.set_typed(count)

    def set_authenticating(self, active: bool) -> None:
        self._button.setText(
            "Waiting for fingerprint…" if active else "Unlock with fingerprint"
        )
        self._button.setDisabled(active)

    def set_footer(self, locked_at: datetime, capture_note: Optional[str]) -> None:
        stamp = locked_at.strftime("%H:%M:%S")
        text = f"Locked at {stamp}"
        if capture_note:
            text += f"\n{capture_note}"
        self._footer.setText(text)


class _ShieldWindow(QWidget):
    def __init__(
        self,
        geometry,
        *,
        shows_panel: bool,
        shows_image: bool,
        image: Optional[QPixmap],
        on_touch_id: Callable[[], None],
    ):
        super().__init__()
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.X11BypassWindowManagerHint
        )
        self.setStyleSheet("background-color: black;")
        self.setGeometry(geometry)

        self.panel: Optional[_Panel] = None
        if shows_image:
            self._strobe = _StrobeWidget(image, self)
            self._strobe.setGeometry(0, 0, geometry.width(), geometry.height())
        if shows_panel:
            self.panel = _Panel(on_touch_id, self)
            self.panel.setGeometry(0, 0, geometry.width(), geometry.height())
            self.panel.raise_()

    def resizeEvent(self, event) -> None:
        for child in self.children():
            if isinstance(child, (_StrobeWidget, _Panel)):
                child.setGeometry(0, 0, self.width(), self.height())
        super().resizeEvent(event)


class LockOverlay:
    def __init__(self) -> None:
        self.on_touch_id: Callable[[], None] = lambda: None
        self._windows: List[_ShieldWindow] = []
        self._active = False
        self._typed_count = 0
        self._message = "Fingerprint, or type your unlock sequence"
        self._capture_note: Optional[str] = None
        self._authenticating = False
        self._locked_at = datetime.now()
        self._biometric_prompt_visible = False
        self._image: Optional[QPixmap] = None

    def show(self) -> None:
        if self._active:
            return
        self._active = True
        self._locked_at = datetime.now()
        self._typed_count = 0
        self._capture_note = None
        path = lockdown_image_path()
        self._image = QPixmap(str(path)) if path else None
        self._build_windows()

    def hide(self) -> None:
        self._active = False
        for window in self._windows:
            window.close()
        self._windows = []

    def rebuild(self) -> None:
        """Rebuild the shield for the current display set *without* touching lock state
        — for a display hotplug mid-lockdown. Preserves the typed-dot count and the
        lock timestamp (which ``show`` would reset), and survives a transient
        zero-screen moment: ``_active`` stays set, so a screen returning rebuilds."""
        if not self._active:
            return
        for window in self._windows:
            window.close()
        self._windows = []
        self._build_windows()

    def _build_windows(self) -> None:
        screens = QGuiApplication.screens()
        if not screens:
            return
        primary = QApplication.primaryScreen()
        # "Top" is the physically topmost display. Qt's y grows downward, so that is
        # the smallest ``top()`` (the macOS build picks the greatest maxY under a
        # y-up axis — the same physical screen).
        topmost = min(screens, key=lambda s: s.geometry().top())

        for screen in screens:
            window = _ShieldWindow(
                screen.geometry(),
                shows_panel=(screen == primary),
                shows_image=(screen == topmost),
                image=self._image,
                on_touch_id=lambda: self.on_touch_id(),
            )
            window.setScreen(screen)
            # The geometry is already exactly this screen's rect and the window
            # bypasses the WM, so a plain show() places it deterministically on the
            # intended monitor. showFullScreen() would re-resolve the target screen
            # itself and can land a shield on the wrong head in a multi-monitor setup.
            window.show()
            self._windows.append(window)
        self._refresh_panels()

    def _refresh_panels(self) -> None:
        for window in self._windows:
            if window.panel is not None:
                window.panel.set_message(self._message)
                window.panel.set_typed_count(self._typed_count)
                window.panel.set_authenticating(self._authenticating)
                window.panel.set_footer(self._locked_at, self._capture_note)

    # -- controller-facing API -------------------------------------------

    def set_message(self, text: str) -> None:
        self._message = text
        self._refresh_panels()

    def set_typed_count(self, count: int) -> None:
        self._typed_count = count
        self._refresh_panels()

    def set_capture_note(self, note: Optional[str]) -> None:
        self._capture_note = note
        self._refresh_panels()

    def set_authenticating(self, active: bool) -> None:
        self._authenticating = active
        self._refresh_panels()

    def set_biometric_prompt_visible(self, visible: bool) -> None:
        # No external biometric panel to accommodate on Linux; tracked for parity.
        self._biometric_prompt_visible = visible
