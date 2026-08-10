"""The watchdog state machine: idle -> armed -> locked -> idle.

A faithful port of the macOS ``LockController``. Every collaborator (the keyboard
grab, the shield overlay, the recorder, the display-sleep inhibitor, biometrics,
the idle monitor) is injected, and so are the few things that need the Qt main
loop (a scheduler for timers, ``post_to_main`` for marshalling a finished hash
back). That keeps the transition logic identical to the macOS build while letting
a test drive it with plain fakes — no Qt, no X11.
"""

from __future__ import annotations

import enum
import os
import threading
import time
from typing import Callable, Optional

from .keys import KeyEvent, KeyKind
from .secretstore import SecretStore


class LockState(enum.Enum):
    IDLE = "idle"
    ARMED = "armed"
    LOCKED = "locked"


class _NullHandle:
    def stop(self) -> None: ...
    def cancel(self) -> None: ...


class NullScheduler:
    """No-op scheduler: timers never fire. Used by tests and any headless path."""

    def interval(self, seconds: float, callback: Callable[[], None]) -> _NullHandle:
        return _NullHandle()

    def after(self, seconds: float, callback: Callable[[], None]) -> _NullHandle:
        return _NullHandle()


def _panic_timeout_from_env() -> float:
    raw = os.environ.get("SZPONTLOCK_PANIC_TIMEOUT")
    try:
        seconds = float(raw) if raw else 0.0
    except ValueError:
        return 0.0
    return seconds if seconds > 0 else 0.0


class LockController:
    # A half-finished attempt left by someone brushing the keyboard must not corrupt
    # the next one, so the armed challenge buffer resets after this long without a key.
    CHALLENGE_RESET_INTERVAL = 5.0

    # How long a biometric prompt may stay up before it is cancelled.
    BIOMETRIC_PROMPT_LIMIT = 25.0

    def __init__(
        self,
        *,
        tap,
        overlay,
        flash,
        assertion,
        recorder,
        biometrics,
        idle_monitor,
        preferences,
        scheduler=None,
        on_state_change: Optional[Callable[[LockState], None]] = None,
        alert: Optional[Callable[[str, str], None]] = None,
        notify: Optional[Callable[[str, str], None]] = None,
        prompt_secret: Optional[Callable[[], Optional[str]]] = None,
        matcher: Optional[Callable[[str], bool]] = None,
        match_runner: Optional[Callable[[str, Callable[[bool], None]], None]] = None,
        post_to_main: Optional[Callable[[Callable[[], None]], None]] = None,
        panic_timeout: Optional[float] = None,
    ) -> None:
        self._tap = tap
        self._overlay = overlay
        self._flash = flash
        self._assertion = assertion
        self._recorder = recorder
        self._biometrics = biometrics
        self._idle = idle_monitor
        self._prefs = preferences
        self._scheduler = scheduler or NullScheduler()
        self._on_state_change = on_state_change or (lambda _s: None)
        self._alert = alert or (lambda _t, _m: None)
        # Non-blocking feedback for the armed/locked phases (keyboard grabbed);
        # falls back to the modal alert if the caller supplies none.
        self._notify = notify or self._alert
        self._prompt_secret = prompt_secret or (lambda: None)
        self._matcher = matcher or SecretStore.matches
        self._post_to_main = post_to_main or (lambda fn: fn())
        self._match_runner = match_runner or self._default_match_runner
        self._panic_timeout = (
            panic_timeout if panic_timeout is not None else _panic_timeout_from_env()
        )

        self._state = LockState.IDLE

        # Lock-screen buffer (what has been typed at the shield).
        self._buffer = ""
        # Armed-watchdog buffer (typed at the silent challenge).
        self._armed_buffer = ""
        self._last_armed_key_at = 0.0
        self._is_verifying_challenge = False

        self._panic_handle = _NullHandle()
        self._biometric_handle = _NullHandle()
        self._auto_arm_handle = _NullHandle()

        # The shield's Touch-ID button (or fingerprint prompt) routes back here.
        self._overlay.on_touch_id = self._request_biometric_unlock

        self.restart_auto_arm_timer()

    # -- state ------------------------------------------------------------

    @property
    def state(self) -> LockState:
        return self._state

    def _set_state(self, new: LockState) -> None:
        self._state = new
        self._on_state_change(new)

    # -- auto-arm on inactivity ------------------------------------------

    def restart_auto_arm_timer(self) -> None:
        """Called at launch and whenever the interval changes."""
        self._auto_arm_handle.stop()
        self._auto_arm_handle = _NullHandle()
        if self._prefs.auto_arm_minutes() > 0:
            self._auto_arm_handle = self._scheduler.interval(5.0, self._check_inactivity)

    def _check_inactivity(self) -> None:
        """Walking away arms the watchdog rather than locking outright: come back, type
        the sequence, and it stands down without ever showing itself."""
        minutes = self._prefs.auto_arm_minutes()
        if minutes <= 0 or self._state != LockState.IDLE:
            return
        # Bail out silently on anything that would put a dialog on screen unprompted.
        if not SecretStore.has_secret():
            return
        idle = self._idle.seconds_idle()
        if idle is None or idle < minutes * 60:
            return

        self.arm()
        if self._state != LockState.ARMED:
            return
        SecretStore.log(f"AUTO-ARMED after {int(idle)}s idle (threshold {minutes}m)")
        # Announce it: nobody was watching, and whoever sits down needs to know the
        # keyboard has become a password prompt.
        self._flash.flash()

    # -- commands ---------------------------------------------------------

    def arm(self) -> None:
        if self._state != LockState.IDLE:
            return
        if not SecretStore.has_secret():
            self._alert(
                "No unlock sequence set",
                "Set an unlock sequence before arming the watchdog, otherwise a "
                "fingerprint (if present) would be the only way back in.",
            )
            self.set_secret()
            return
        self._prime_permissions()

        if not self._tap.start():
            self._alert(
                "Could not capture input",
                "SzpontLock failed to grab the keyboard. Another program may already "
                "hold a keyboard grab, or this is a Wayland session (X11 is required).",
            )
            return

        self._assertion.acquire("SzpontLock watchdog armed")
        self._armed_buffer = ""
        self._set_state(LockState.ARMED)
        SecretStore.log("ARMED")

    def disarm(self, reason: str = "menu") -> None:
        if self._state != LockState.ARMED:
            return
        self._armed_buffer = ""
        self._is_verifying_challenge = False
        self._tap.stop()
        self._assertion.release()
        self._set_state(LockState.IDLE)
        SecretStore.log(f"DISARMED ({reason})")

    def request_disarm(self) -> None:
        """Standing the watchdog down hands the machine back, so it takes proof — a
        fingerprint where one exists, otherwise the sequence the watchdog already
        listens for."""
        self._authenticate_for_release(
            "disarm the SzpontLock watchdog",
            lambda ok: self.disarm(reason="fingerprint") if ok else None,
        )

    def request_quit(self, completion: Callable[[bool], None]) -> None:
        """Quitting while armed is the same hole as disarming, one menu item down."""
        if self._state != LockState.ARMED:
            completion(self._state == LockState.IDLE)
            return

        def released(ok: bool) -> None:
            # Disarm first: the app refuses to quit unless idle.
            if ok:
                self.disarm(reason="quit")
            completion(ok)

        self._authenticate_for_release(
            "quit SzpontLock and stand the watchdog down", released
        )

    def _authenticate_for_release(
        self, reason: str, completion: Callable[[bool], None]
    ) -> None:
        if not self._biometrics.is_available():
            # Notify, don't modal: the keyboard is grabbed here, so a modal alert
            # would be keyboard-dead and can fail to take focus under the X grab.
            self._notify(
                "Fingerprint unavailable",
                "Type your unlock sequence instead — the armed watchdog is "
                "listening for it.",
            )
            completion(False)
            return

        def done(ok: bool, message: Optional[str]) -> None:
            if not ok and message:
                self._notify("Not disarmed", message)
            completion(ok)

        self._biometrics.authenticate(reason, done)

    def lock_now(self) -> None:
        """Manual panic lock — skips the watchdog phase and goes straight to lockdown."""
        if self._state == LockState.LOCKED:
            return
        if self._state == LockState.ARMED:
            self._trip("manual")
        elif self._state == LockState.IDLE:
            self.arm()
            if self._state == LockState.ARMED:
                self._trip("manual")

    def set_secret(self) -> None:
        if self._state == LockState.LOCKED:
            return
        secret = self._prompt_secret()
        if secret is None:
            return
        try:
            SecretStore.set_secret(secret)
            SecretStore.log(f"SECRET SET (length {len(secret)})")
            self._alert(
                "Unlock sequence saved",
                f"{len(secret)} characters. Only its hash is stored on disk.",
            )
        except OSError as exc:
            self._alert("Could not save", str(exc))

    # -- event handling (called on the main thread) ----------------------

    def handle_key(self, event: KeyEvent) -> None:
        """A key from the grab pump, already marshalled to the main thread. Only the
        armed and locked states consume keys; idle never grabs so it never gets here."""
        if self._state == LockState.ARMED:
            self._consume_challenge_key(event)
        elif self._state == LockState.LOCKED:
            self._consume_key(event)

    def _consume_challenge_key(self, event: KeyEvent) -> None:
        if self._state != LockState.ARMED or self._is_verifying_challenge:
            return

        now = time.monotonic()
        if now - self._last_armed_key_at > self.CHALLENGE_RESET_INTERVAL:
            self._armed_buffer = ""
        self._last_armed_key_at = now

        if event.kind in (KeyKind.ESCAPE, KeyKind.ENTER):
            self._armed_buffer = ""
            return
        if event.kind == KeyKind.BACKSPACE:
            self._armed_buffer = self._armed_buffer[:-1]
            return
        if event.kind != KeyKind.CHAR or event.ctrl or event.meta or not event.char:
            return
        self._armed_buffer += event.char

        length = SecretStore.secret_length()
        if length <= 0 or len(self._armed_buffer) < length:
            return

        attempt = self._armed_buffer
        self._armed_buffer = ""
        self._is_verifying_challenge = True
        self._match_runner(attempt, self._challenge_verdict)

    def _challenge_verdict(self, is_correct: bool) -> None:
        # Clear before the state check: bailing out with this still set would leave
        # the challenge permanently deaf.
        self._is_verifying_challenge = False
        if self._state != LockState.ARMED:
            return
        if is_correct:
            self.disarm(reason="correct sequence - watchdog never surfaced")
        else:
            self._trip("wrong sequence")

    def _consume_key(self, event: KeyEvent) -> None:
        if self._state != LockState.LOCKED:
            return

        if event.kind in (KeyKind.ESCAPE, KeyKind.ENTER):
            self._buffer = ""
        elif event.kind == KeyKind.BACKSPACE:
            self._buffer = self._buffer[:-1]
        else:
            if event.kind != KeyKind.CHAR or event.ctrl or event.meta or not event.char:
                return
            self._buffer += event.char
            if len(self._buffer) > 256:
                self._buffer = self._buffer[-256:]

        self._overlay.set_typed_count(len(self._buffer))

        length = SecretStore.secret_length()
        if length <= 0 or len(self._buffer) < length:
            return
        candidate = self._buffer[-length:]
        self._match_runner(
            candidate,
            lambda ok: self.unlock("sequence") if ok else None,
        )

    # -- transitions ------------------------------------------------------

    def _trip(self, reason: str) -> None:
        if self._state != LockState.ARMED:
            return
        self._set_state(LockState.LOCKED)
        self._buffer = ""
        self._assertion.acquire("SzpontLock locked")
        self._overlay.show()
        SecretStore.log(f"TRIPPED ({reason})")

        self._overlay.set_message(self._idle_lock_message)
        if self._panic_timeout > 0:
            self._panic_handle = self._scheduler.after(
                self._panic_timeout, lambda: self.unlock("panic timeout")
            )

        seconds = int(self._prefs.RECORDING_SECONDS)
        self._overlay.set_capture_note(f"Recording {seconds}s…")
        self._recorder.record(
            self._prefs.RECORDING_SECONDS,
            SecretStore.capture_directory(),
            self._recording_finished,
        )

        # Offer the fingerprint immediately; the button is there for retries.
        self._scheduler.after(0.5, self._request_biometric_unlock)

    def _recording_finished(self, outcome: str, path: Optional[str]) -> None:
        if outcome == "saved" and path:
            folder = os.path.basename(os.path.dirname(path))
            self._overlay.set_capture_note(f"Recording saved to {folder}")
            SecretStore.log(f"RECORDED {path}")
        elif outcome == "failed":
            self._overlay.set_capture_note("Recording failed")
            SecretStore.log("RECORDING FAILED")
        elif outcome == "discarded":
            SecretStore.log("RECORDING DISCARDED (unlocked before the clip finished)")

    def _request_biometric_unlock(self) -> None:
        if self._state != LockState.LOCKED or self._biometrics.is_running:
            return
        if not self._biometrics.is_available():
            self._overlay.set_message(
                "Fingerprint unavailable - type your unlock sequence"
            )
            return

        self._overlay.set_authenticating(True)
        self._overlay.set_biometric_prompt_visible(True)
        self._biometric_handle = self._scheduler.after(
            self.BIOMETRIC_PROMPT_LIMIT, self._biometrics.cancel
        )
        self._biometrics.authenticate("unlock SzpontLock", self._biometric_verdict)

    def _biometric_verdict(self, success: bool, message: Optional[str]) -> None:
        self._end_biometric_prompt()
        if success:
            self.unlock("fingerprint")
        elif message:
            self._overlay.set_message(message)
            SecretStore.log(f"FINGERPRINT FAILED: {message}")
        else:
            self._overlay.set_message(self._idle_lock_message)

    def _end_biometric_prompt(self) -> None:
        self._biometric_handle.cancel()
        self._biometric_handle = _NullHandle()
        self._overlay.set_authenticating(False)
        self._overlay.set_biometric_prompt_visible(False)

    @property
    def _idle_lock_message(self) -> str:
        if self._panic_timeout > 0:
            return (
                f"Safety auto-unlock in {int(self._panic_timeout)}s - "
                "fingerprint or type your sequence"
            )
        return "Fingerprint, or type your unlock sequence"

    def unlock(self, method: str) -> None:
        if self._state != LockState.LOCKED:
            return
        self._panic_handle.cancel()
        self._panic_handle = _NullHandle()
        # The sequence may have been typed while a biometric prompt was still up.
        self._biometrics.cancel()
        self._end_biometric_prompt()
        # Unlocking inside the recording window means the owner answered their own
        # trap, so the half-finished clip is of them. Drop it.
        self._recorder.cancel()
        self._armed_buffer = ""
        self._is_verifying_challenge = False
        self._buffer = ""
        self._overlay.hide()
        self._tap.stop()
        self._assertion.release()
        self._set_state(LockState.IDLE)
        SecretStore.log(f"UNLOCKED via {method}")

    # -- helpers ----------------------------------------------------------

    def _prime_permissions(self) -> None:
        """Poke the Desktop up front so a recording never has to create it mid-lockdown
        (the X11 keyboard grab is what actually gates arming, and ``tap.start`` proves
        that separately)."""
        desktop = SecretStore._desktop_dir()
        if desktop is not None:
            SecretStore.is_writable(desktop)

    def _default_match_runner(
        self, attempt: str, on_verdict: Callable[[bool], None]
    ) -> None:
        """Hash off the main thread (~50 ms), then deliver the verdict back on it."""

        def work() -> None:
            result = self._matcher(attempt)
            self._post_to_main(lambda: on_verdict(result))

        threading.Thread(target=work, daemon=True).start()
