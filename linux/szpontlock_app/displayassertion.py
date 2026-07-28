"""Keeps the display awake while the watchdog is armed or locked — the whole point
is that the screen stays on rather than blanking.

The macOS build holds an ``IOPMAssertionCreateWithName`` no-display-sleep assertion.
Two mechanisms cover the same ground on Linux, because logind and X keep separate
idle clocks:

* a held ``systemd-inhibit`` child blocks logind's idle/sleep actions, and
* a background nudge resets the X screen-saver / DPMS countdown every so often, so
  the server never blanks the display.

Either alone leaves a gap; both together match "the screen never blanks".
"""

from __future__ import annotations

import shutil
import subprocess
import threading


class DisplayAssertion:
    _NUDGE_INTERVAL = 20.0

    def __init__(self) -> None:
        self._held = False
        self._inhibitor: subprocess.Popen | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def acquire(self, reason: str) -> None:
        if self._held:
            return
        self._held = True

        if shutil.which("systemd-inhibit"):
            try:
                self._inhibitor = subprocess.Popen(
                    [
                        "systemd-inhibit",
                        "--what=idle:sleep",
                        "--who=SzpontLock",
                        f"--why={reason}",
                        "--mode=block",
                        "sleep",
                        "infinity",
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except OSError:
                self._inhibitor = None

        self._stop.clear()
        self._thread = threading.Thread(target=self._nudge_loop, daemon=True)
        self._thread.start()

    def release(self) -> None:
        if not self._held:
            return
        self._held = False

        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None

        if self._inhibitor is not None:
            try:
                self._inhibitor.terminate()
                self._inhibitor.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    self._inhibitor.kill()
                except OSError:
                    pass
            self._inhibitor = None

    def _nudge_loop(self) -> None:
        """Resets the X screen-saver / DPMS countdown on its own Xlib connection so the
        display never blanks while armed or locked. Best-effort — any failure just
        leaves logind's inhibitor doing the work."""
        display = None
        try:
            from Xlib import X, display as xdisplay

            display = xdisplay.Display()
            while not self._stop.is_set():
                try:
                    display.force_screen_saver(X.ScreenSaverReset)
                    display.sync()
                except Exception:
                    break
                self._stop.wait(self._NUDGE_INTERVAL)
        except Exception:
            return
        finally:
            if display is not None:
                try:
                    display.close()
                except Exception:
                    pass
