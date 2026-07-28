"""Fingerprint unlock, the Linux stand-in for Touch ID.

macOS has Touch ID everywhere it matters; Linux fingerprint support is fronted by
``fprintd``, which most desktops lack. So this is strictly best-effort: if
``fprintd-verify`` is present *and* the user has an enrolled finger, the lock screen
grows an "Unlock with fingerprint" button and disarm/quit can be proven with it —
exactly as on macOS. Where it is absent (as on a typical machine, and on this one),
``is_available`` reports ``False`` and the only proof is the unlock sequence, which
is the same fallback the macOS build shows when Touch ID is unavailable.

Biometrics run in their own process, so a verify still works while the keyboard is
grabbed — it never depends on the X input SzpontLock has taken over.
"""

from __future__ import annotations

import getpass
import shutil
import subprocess
import threading
from typing import Callable, Optional

from PySide6.QtCore import QObject, Signal


class Biometrics(QObject):
    # (success, message-or-None); delivered on the main thread.
    _done = Signal(bool, object)

    def __init__(self) -> None:
        super().__init__()
        self._done.connect(self._deliver)
        self._proc: Optional[subprocess.Popen] = None
        self._on_done: Optional[Callable[[bool, Optional[str]], None]] = None
        self._running = False
        self._available: Optional[bool] = None

    def is_available(self) -> bool:
        if self._available is None:
            self._available = self._probe()
        return self._available

    @staticmethod
    def _probe() -> bool:
        if not shutil.which("fprintd-verify") or not shutil.which("fprintd-list"):
            return False
        try:
            out = subprocess.run(
                ["fprintd-list", getpass.getuser()],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        text = (out.stdout + out.stderr).lower()
        # fprintd-list prints "has no fingers enrolled" when nothing is registered.
        return out.returncode == 0 and "no fingers" not in text

    @property
    def is_running(self) -> bool:
        return self._running

    def authenticate(
        self, reason: str, on_done: Callable[[bool, Optional[str]], None]
    ) -> None:
        if self._running:
            return
        self._on_done = on_done
        self._running = True
        # Spawn the child on the main thread so ``self._proc`` is set before any
        # cancel() could race in — a cancel arriving during startup then sees the
        # real process and terminates it, rather than missing a not-yet-assigned one.
        try:
            proc = subprocess.Popen(
                ["fprintd-verify"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
        except OSError as exc:
            self._settle(False, str(exc))
            return
        self._proc = proc
        threading.Thread(target=self._run, args=(proc,), daemon=True).start()

    def _run(self, proc: subprocess.Popen) -> None:
        try:
            output, _ = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except OSError:
                pass
            self._settle(False, None)
            return

        if proc.returncode == 0 and "verify-match" in (output or ""):
            self._settle(True, None)
        elif "verify-no-match" in (output or ""):
            self._settle(False, "Fingerprint not recognised")
        else:
            # A cancel (invalidate) or any other non-match: no message, like a
            # user-cancelled Touch ID prompt.
            self._settle(False, None)

    def cancel(self) -> None:
        proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass

    def _settle(self, success: bool, message: Optional[str]) -> None:
        self._proc = None
        self._done.emit(success, message)

    def _deliver(self, success: bool, message: Optional[str]) -> None:
        self._running = False
        cb = self._on_done
        self._on_done = None
        if cb is not None:
            cb(success, message)
