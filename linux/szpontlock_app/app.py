"""Tray applet entry point: wires the ported ``LockController`` to its Qt/X11
collaborators and runs the event loop — the Linux analogue of the macOS
``AppDelegate`` + ``main.swift``.

The controller stays UI-agnostic; everything Qt-specific lives here: a ``QTimer``
scheduler for the controller's timers, a main-thread marshaller so a finished hash
(computed off-thread) lands back on the GUI thread, and QMessageBox/QInputDialog for
the alerts and the secret prompt.
"""

from __future__ import annotations

import os
import signal
import sys
from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication, QMessageBox

from . import preferences
from .biometrics import Biometrics
from .controller import LockController, LockState
from .displayassertion import DisplayAssertion
from .eventtap import EventTap
from .flash import FlashOverlay
from .idle import IdleMonitor
from .overlay import LockOverlay
from .recorder import CameraRecorder
from .secretprompt import SecretPrompt
from .secretstore import SecretStore
from .singleton import SingleInstance
from .tray import StatusItem


class _TimerHandle:
    def __init__(self, timer: QTimer) -> None:
        self._timer = timer

    def stop(self) -> None:
        # A single-shot timer deleteLater()s itself after firing, so a late
        # stop()/cancel() on an already-fired handle would hit a freed C++ object.
        try:
            self._timer.stop()
        except RuntimeError:
            pass

    def cancel(self) -> None:
        self.stop()


class QtScheduler(QObject):
    """Backs the controller's ``interval``/``after`` with ``QTimer``. Every timer is
    parented to this object so it outlives the call that created it (an unparented
    QTimer with no Python reference would be garbage-collected before it fires)."""

    def interval(self, seconds: float, callback: Callable[[], None]) -> _TimerHandle:
        timer = QTimer(self)
        timer.setInterval(int(seconds * 1000))
        timer.timeout.connect(callback)
        timer.start()
        return _TimerHandle(timer)

    def after(self, seconds: float, callback: Callable[[], None]) -> _TimerHandle:
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(int(seconds * 1000))

        def fire() -> None:
            timer.deleteLater()
            callback()

        timer.timeout.connect(fire)
        timer.start()
        return _TimerHandle(timer)


class _Marshaller(QObject):
    """Delivers a callable onto the main (GUI) thread. Emitting the signal from a
    worker thread queues the slot to run where this object lives — the main thread."""

    invoke = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.invoke.connect(self._run)

    @staticmethod
    def _run(fn: Callable[[], None]) -> None:
        fn()


def _alert(title: str, message: str) -> None:
    box = QMessageBox()
    box.setIcon(QMessageBox.Warning)
    box.setWindowTitle(title)
    box.setText(title)
    box.setInformativeText(message)
    box.exec()


def run_app() -> int:
    # Newest-wins: a fresh launch replaces any tray already running.
    SingleInstance.acquire_newest_wins()

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    QApplication.setApplicationName("SzpontLock")
    QApplication.setOrganizationName("SzpontLock")

    SecretStore.prepare_directories()
    panic = os.environ.get("SZPONTLOCK_PANIC_TIMEOUT", "off")
    SecretStore.log(
        f"LAUNCHED (panic timeout: {panic}, "
        f"auto-arm: {preferences.auto_arm_label(preferences.auto_arm_minutes())})"
    )

    scheduler = QtScheduler()
    marshaller = _Marshaller()

    def post_to_main(fn: Callable[[], None]) -> None:
        marshaller.invoke.emit(fn)

    tap = EventTap(on_key=lambda event: controller.handle_key(event))
    overlay = LockOverlay()
    flash = FlashOverlay()
    assertion = DisplayAssertion()
    recorder = CameraRecorder()
    biometrics = Biometrics()
    idle_monitor = IdleMonitor()
    status = StatusItem()

    controller = LockController(
        tap=tap,
        overlay=overlay,
        flash=flash,
        assertion=assertion,
        recorder=recorder,
        biometrics=biometrics,
        idle_monitor=idle_monitor,
        preferences=preferences,
        scheduler=scheduler,
        on_state_change=status.apply,
        alert=_alert,
        notify=status.notify,
        prompt_secret=SecretPrompt.run,
        post_to_main=post_to_main,
    )
    status.bind(controller)

    # Rebuild the shield if displays are hotplugged mid-lockdown, matching the macOS
    # didChangeScreenParameters handling. rebuild() preserves lock state (typed dots,
    # timestamp) and self-guards on whether the shield is active.
    def screens_changed(*_args) -> None:
        overlay.rebuild()

    app.screenAdded.connect(screens_changed)
    app.screenRemoved.connect(screens_changed)

    # First launch: offer to set a sequence, then nudge for it, matching the macOS
    # welcome alert. Deferred so the tray is up first.
    def first_launch() -> None:
        if SecretStore.has_secret():
            return
        box = QMessageBox()
        box.setWindowTitle("Welcome to SzpontLock")
        box.setText("Welcome to SzpontLock")
        box.setInformativeText(
            "SzpontLock lives in the system tray. Arm it and the screen stays on and "
            "the mouse keeps working, but the keyboard quietly becomes a password "
            "prompt: type your sequence and the watchdog stands down without ever "
            "showing itself. Type anything else and the machine locks, records a few "
            "seconds of whoever is at the keyboard, and swallows all input until the "
            "sequence (or a fingerprint) releases it.\n\nSet an unlock sequence to get "
            "started."
        )
        set_button = box.addButton("Set Unlock Sequence…", QMessageBox.AcceptRole)
        box.addButton("Later", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is set_button:
            controller.set_secret()

    # Smoke hook: boot the whole stack and quit after N ms (used by the harness to
    # verify wiring without a real arm). Skips the first-launch modal so it can't block.
    smoke_ms = os.environ.get("SZPONTLOCK_SMOKE_EXIT_MS")
    if smoke_ms:
        QTimer.singleShot(int(smoke_ms), app.quit)
    else:
        QTimer.singleShot(0, first_launch)

    # Let the interpreter run periodically so Ctrl-C reaches the Python signal handler
    # (Qt's C++ loop would otherwise starve it).
    signal.signal(signal.SIGINT, lambda *_a: app.quit())
    kicker = QTimer(app)  # parented so it is not garbage-collected
    kicker.start(200)
    kicker.timeout.connect(lambda: None)

    def cleanup() -> None:
        # Hand the machine back on shutdown: drop the grab, release the sleep
        # assertion, and stop any recording/prompt so nothing is left dangling.
        try:
            if controller.state != LockState.IDLE:
                assertion.release()
                tap.stop()
                recorder.cancel()
                biometrics.cancel()
        finally:
            SingleInstance.release()
            SecretStore.log("QUIT")

    app.aboutToQuit.connect(cleanup)

    return app.exec()
