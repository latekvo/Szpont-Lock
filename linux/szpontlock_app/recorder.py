"""Records a short webcam clip when the watchdog trips.

The macOS build drives an ``AVAssetWriter``; on Linux the same job is one ``ffmpeg``
child reading V4L2, which keeps the camera indicator lit only for the length of the
clip and gives an exact duration via ``-t``. As with the macOS version, unlocking
inside the window discards the partial file — whoever unlocked it was the owner, so
the footage is of them answering their own trap.

The finished/failed/discarded outcome is marshalled back to the Qt main thread via a
signal, so the controller's callback runs where every other transition does.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QObject, Signal

# Candidate capture devices, in order. Overridable with SZPONTLOCK_CAMERA.
_DEFAULT_DEVICES = ["/dev/video0", "/dev/video1", "/dev/video2"]


def _pick_device() -> Optional[str]:
    forced = os.environ.get("SZPONTLOCK_CAMERA")
    if forced:
        return forced if os.path.exists(forced) else None
    for dev in _DEFAULT_DEVICES:
        if os.path.exists(dev):
            return dev
    return None


class CameraRecorder(QObject):
    # (outcome, path-or-None); delivered on the main thread.
    _finished = Signal(str, object)

    def __init__(self) -> None:
        super().__init__()
        self._finished.connect(self._deliver)
        self._lock = threading.Lock()
        self._proc: Optional[subprocess.Popen] = None
        self._path: Optional[Path] = None
        self._on_done: Optional[Callable[[str, Optional[str]], None]] = None
        self._done = False

    def record(
        self,
        duration: float,
        directory: Path,
        on_done: Callable[[str, Optional[str]], None],
    ) -> None:
        with self._lock:
            self._on_done = on_done
            self._done = False
            self._path = None
            self._proc = None

        if not shutil.which("ffmpeg"):
            self._settle("failed", None)
            return
        device = _pick_device()
        if device is None:
            self._settle("failed", None)
            return

        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        path = Path(directory) / f"szpontlock_{stamp}.mov"
        try:
            path.unlink()
        except OSError:
            pass

        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel", "error",
            "-nostdin",
            "-f", "v4l2",
            "-i", device,
            "-t", f"{duration:.2f}",
            "-y",
            str(path),
        ]
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        except OSError:
            self._settle("failed", None)
            return

        with self._lock:
            self._proc = proc
            self._path = path

        threading.Thread(
            target=self._wait, args=(proc, path, duration), daemon=True
        ).start()

    def _wait(self, proc: subprocess.Popen, path: Path, duration: float) -> None:
        # Give ffmpeg the clip length plus generous head-room for camera warm-up
        # before treating a silent pipeline as a failure.
        try:
            proc.wait(timeout=duration + 15.0)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except OSError:
                pass
            self._settle("failed", None)
            return

        if proc.returncode == 0 and path.exists() and path.stat().st_size > 0:
            self._settle("saved", str(path))
        else:
            # A non-zero code that is *not* our own cancel() is a genuine failure;
            # cancel() settles as "discarded" first and _settle keeps the first word.
            self._settle("failed", None)

    def cancel(self) -> None:
        """Aborts an in-flight recording and deletes the partial file. No-op once the
        clip has already been finalised — a complete recording is kept even if the
        unlock follows a moment later, because the full window was captured.

        The outcome is *claimed* before ffmpeg is killed, so the waiter thread (whose
        ``proc.wait`` unblocks the instant we terminate the child) cannot race in and
        mark the aborted clip as "failed" — the first claim wins, and here it is
        "discarded"."""
        # Claim and read the process handle in the same lock: the fields are written
        # under this lock in record(), so reading them here keeps that contract even
        # if a future caller invokes cancel() off the main thread.
        with self._lock:
            if self._done:
                return
            self._done = True
            proc, path = self._proc, self._path
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    proc.kill()
                except OSError:
                    pass
        if path is not None:
            try:
                path.unlink()
            except OSError:
                pass
        self._finished.emit("discarded", None)

    def _claim(self) -> bool:
        """Atomically take ownership of the single outcome. Returns True to the first
        caller (cancel or the waiter), False to every later one."""
        with self._lock:
            if self._done:
                return False
            self._done = True
            return True

    def _settle(self, outcome: str, path: Optional[str]) -> None:
        if self._claim():
            self._finished.emit(outcome, path)

    def _deliver(self, outcome: str, path: Optional[str]) -> None:
        cb = self._on_done
        self._on_done = None
        if cb is not None:
            cb(outcome, path)
