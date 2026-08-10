"""Offscreen renders for verification and screenshots — no keyboard grab, no tray.

``SZPONTLOCK_RENDER=lock`` paints the lock screen (panel + strobe frame) to a PNG;
``SZPONTLOCK_RENDER=icons`` lays the three tray-state icons side by side. Both use
``QWidget.grab``, which renders through the raster paint engine, so they work under
the ``offscreen`` Qt platform with no real display.
"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

from .controller import LockState
from .overlay import _Panel, _StrobeWidget, lockdown_image_path
from .tray import _icon_for


def _render_lock(out_path: str) -> int:
    width, height = 1440, 900
    canvas = QPixmap(width, height)
    canvas.fill(QColor("black"))

    path = lockdown_image_path()
    image = QPixmap(str(path)) if path else None

    strobe = _StrobeWidget(image)
    strobe.resize(width, height)
    strobe._phase = 1  # force the "picture" frame so the render is not just black
    strobe._finished = False

    panel = _Panel(on_touch_id=lambda: None)
    panel.resize(width, height)
    panel.set_message("Fingerprint, or type your unlock sequence")
    panel.set_typed_count(4)
    panel.set_authenticating(False)
    panel.set_footer(datetime.now(), "Recording saved to Desktop")

    painter = QPainter(canvas)
    painter.drawPixmap(0, 0, strobe.grab())
    # Panel is drawn with a translucent background so the strobe shows through.
    painter.drawPixmap(0, 0, panel.grab())
    painter.end()

    ok = canvas.save(out_path)
    print(f"rendered lock screen -> {out_path} ({'ok' if ok else 'FAILED'})")
    return 0 if ok else 1


def _render_icons(out_path: str) -> int:
    size = 64
    canvas = QPixmap(size * 3 + 40, size + 20)
    canvas.fill(QColor("#202020"))
    painter = QPainter(canvas)
    for i, state in enumerate(
        (LockState.IDLE, LockState.ARMED, LockState.LOCKED)
    ):
        icon = _icon_for(state)
        pixmap = icon.pixmap(size, size)
        painter.drawPixmap(10 + i * (size + 10), 10, pixmap)
    painter.end()
    ok = canvas.save(out_path)
    print(f"rendered tray icons -> {out_path} ({'ok' if ok else 'FAILED'})")
    return 0 if ok else 1


def run(what: str, out_path: str) -> int:
    QApplication.instance() or QApplication([])
    if what == "lock":
        return _render_lock(out_path)
    if what == "icons":
        return _render_icons(out_path)
    print(f"unknown render target: {what!r} (expected 'lock' or 'icons')")
    return 2
