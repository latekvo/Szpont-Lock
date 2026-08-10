"""A session-wide keyboard grab that swallows every keystroke.

The Linux counterpart of the macOS ``EventTap`` (a ``.cgSessionEventTap``). An active
X11 ``XGrabKeyboard`` on the root window redirects *all* key events to this client
until it is released, so — exactly like the macOS tap — nothing an intruder types
reaches whatever app happens to be focused. Only the keyboard is grabbed; the mouse
carries on as normal, which is what lets the armed state look like nothing is wrong.

The grab and its event pump run on a dedicated thread with their own Xlib
connection; each ``KeyPress`` is translated into a platform-neutral ``KeyEvent`` and
handed to the controller on the Qt main thread via a queued signal.

DANGER: while this grab is held, the machine's keyboard belongs to SzpontLock. Do
not start it on a live desktop session you are using — it is the real, whole-keyboard
grab, and only the controller's unlock path releases it.
"""

from __future__ import annotations

import select
import threading
import time
from typing import Callable, Optional

from PySide6.QtCore import QObject, Qt, Signal

from .keys import KeyEvent, KeyKind


class EventTap(QObject):
    # Emitted from the pump thread; delivered to the controller on the main thread.
    _key = Signal(object)

    def __init__(self, on_key: Callable[[KeyEvent], None]) -> None:
        super().__init__()
        self._key.connect(on_key, Qt.QueuedConnection)
        self._display = None
        self._root = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self) -> bool:
        if self._running:
            return True
        try:
            from Xlib import X, display

            self._display = display.Display()
            self._root = self._display.screen().root
            # A passive grab elsewhere (an open menu, a WM shortcut being pressed) can
            # make the first attempt return AlreadyGrabbed even on an otherwise free
            # keyboard, so retry briefly before giving up. A persistent AlreadyGrabbed
            # means another client really holds the keyboard (or this is Wayland).
            code = X.AlreadyGrabbed
            for attempt in range(6):
                status = self._root.grab_keyboard(
                    False,  # owner_events: every key comes to us, not the focused window
                    X.GrabModeAsync,
                    X.GrabModeAsync,
                    X.CurrentTime,
                )
                # grab_keyboard returns the status directly (or in a .status field,
                # depending on the xlib version); accept either shape.
                code = getattr(status, "status", status)
                if code == X.GrabSuccess:
                    break
                time.sleep(0.05)
            if code != X.GrabSuccess:
                self._teardown_display()
                return False
        except Exception:
            self._teardown_display()
            return False

        self._running = True
        self._stop.clear()
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        if not self._running:
            return
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        try:
            from Xlib import X

            if self._display is not None:
                self._display.ungrab_keyboard(X.CurrentTime)
                self._display.flush()
        except Exception:
            pass
        self._teardown_display()
        self._running = False

    def _teardown_display(self) -> None:
        if self._display is not None:
            try:
                self._display.close()
            except Exception:
                pass
        self._display = None
        self._root = None

    def _pump(self) -> None:
        from Xlib import X

        display = self._display
        if display is None:
            return
        fd = display.fileno()
        while not self._stop.is_set():
            # Block up to 100 ms for input, then drain everything queued. The timeout
            # is what lets stop() take effect promptly without a wakeup event.
            try:
                select.select([fd], [], [], 0.1)
            except (OSError, ValueError):
                break
            try:
                pending = display.pending_events()
            except Exception:
                break
            for _ in range(pending):
                try:
                    event = display.next_event()
                except Exception:
                    return
                if event.type == X.KeyPress:
                    key = self._translate(event)
                    if key is not None:
                        self._key.emit(key)
                # KeyRelease and everything else are swallowed silently: a swallowed
                # keyDown must not have its keyUp leak through either.

    def _translate(self, event) -> Optional[KeyEvent]:
        from Xlib import X, XK

        display = self._display
        if display is None:
            return None
        state = event.state
        shift = bool(state & X.ShiftMask)
        ctrl = bool(state & X.ControlMask)
        meta = bool(state & X.Mod4Mask)  # Super — the closest analogue to ⌘

        keysym = display.keycode_to_keysym(event.detail, 1 if shift else 0)
        if keysym in (XK.XK_Return, XK.XK_KP_Enter):
            return KeyEvent(KeyKind.ENTER)
        if keysym == XK.XK_Escape:
            return KeyEvent(KeyKind.ESCAPE)
        if keysym == XK.XK_BackSpace:
            return KeyEvent(KeyKind.BACKSPACE)

        char = XK.keysym_to_string(keysym) or ""
        if char and char.isprintable():
            return KeyEvent.char_key(char, ctrl=ctrl, meta=meta)
        return KeyEvent(KeyKind.OTHER)
