"""The watchdog state machine driven by fakes — no Qt, no X11.

Every collaborator is a stub and the hash runs synchronously, so these assert the
ported transition logic directly: arming needs a secret and a working grab, the armed
challenge disarms silently on the right sequence and locks on the wrong one, the lock
screen unlocks on the sequence, auto-arm respects idle/secret preconditions, and
handing the machine back needs proof.
"""

from __future__ import annotations

import pytest

from szpontlock_app.controller import LockController, LockState
from szpontlock_app.keys import KeyEvent, KeyKind


class FakeTap:
    def __init__(self):
        self.running = False
        self.start_result = True
    def start(self):
        if self.start_result:
            self.running = True
        return self.start_result
    def stop(self):
        self.running = False
    @property
    def is_running(self):
        return self.running


class FakeOverlay:
    def __init__(self):
        self.shown = False
        self.typed_count = None
        self.capture_note = "unset"
        self.authenticating = False
        self.bio_visible = False
        self.on_touch_id = lambda: None
    def show(self):
        self.shown = True
    def hide(self):
        self.shown = False
    def set_message(self, m):
        self.message = m
    def set_typed_count(self, c):
        self.typed_count = c
    def set_capture_note(self, n):
        self.capture_note = n
    def set_authenticating(self, a):
        self.authenticating = a
    def set_biometric_prompt_visible(self, v):
        self.bio_visible = v


class FakeFlash:
    def __init__(self):
        self.flashes = 0
    def flash(self):
        self.flashes += 1


class FakeAssertion:
    def __init__(self):
        self.held = False
    def acquire(self, reason):
        self.held = True
    def release(self):
        self.held = False


class FakeRecorder:
    def __init__(self):
        self.recorded = False
        self.cancelled = False
    def record(self, duration, directory, on_done):
        self.recorded = True
        self.on_done = on_done
    def cancel(self):
        self.cancelled = True


class FakeBiometrics:
    def __init__(self, available=False):
        self._available = available
        self.is_running = False
        self._cb = None
    def is_available(self):
        return self._available
    def authenticate(self, reason, on_done):
        self._cb = on_done
    def deliver(self, success, message=None):
        cb, self._cb = self._cb, None
        cb(success, message)
    def cancel(self):
        pass


class FakeIdle:
    def __init__(self, seconds):
        self.seconds = seconds
    def seconds_idle(self):
        return self.seconds


class FakePrefs:
    RECORDING_SECONDS = 5.0
    def __init__(self, minutes=0):
        self.minutes = minutes
    def auto_arm_minutes(self):
        return self.minutes


def build(store, *, secret="abcd", minutes=0, idle=0.0,
          biometrics_available=False, tap_ok=True):
    if secret is not None:
        store.set_secret(secret)
    alerts = []
    notifications = []
    tap = FakeTap()
    tap.start_result = tap_ok
    overlay = FakeOverlay()
    flash = FakeFlash()
    assertion = FakeAssertion()
    recorder = FakeRecorder()
    biometrics = FakeBiometrics(biometrics_available)
    idle_mon = FakeIdle(idle)
    prefs = FakePrefs(minutes)
    from szpontlock_app.secretstore import SecretStore

    controller = LockController(
        tap=tap, overlay=overlay, flash=flash, assertion=assertion,
        recorder=recorder, biometrics=biometrics, idle_monitor=idle_mon,
        preferences=prefs,
        alert=lambda t, m: alerts.append((t, m)),
        notify=lambda t, m: notifications.append((t, m)),
        prompt_secret=lambda: None,
        # Synchronous hashing so the verdict lands inside handle_key.
        match_runner=lambda attempt, cb: cb(SecretStore.matches(attempt)),
    )
    ctx = dict(
        controller=controller, tap=tap, overlay=overlay, flash=flash,
        assertion=assertion, recorder=recorder, biometrics=biometrics,
        prefs=prefs, alerts=alerts, notifications=notifications,
    )
    return ctx


def type_string(controller, text):
    for ch in text:
        controller.handle_key(KeyEvent.char_key(ch))


def test_arm_requires_secret(store):
    ctx = build(store, secret=None)
    c = ctx["controller"]
    c.arm()
    # No secret: arm bails (prompt returns None), stays idle, no grab.
    assert c.state == LockState.IDLE
    assert not ctx["tap"].running
    assert ctx["alerts"]  # warned about the missing sequence


def test_arm_success(store):
    ctx = build(store)
    c = ctx["controller"]
    c.arm()
    assert c.state == LockState.ARMED
    assert ctx["tap"].running
    assert ctx["assertion"].held


def test_arm_fails_when_grab_unavailable(store):
    ctx = build(store, tap_ok=False)
    c = ctx["controller"]
    c.arm()
    assert c.state == LockState.IDLE
    assert ctx["alerts"]  # "Could not capture input"


def test_correct_challenge_disarms_silently(store):
    ctx = build(store, secret="abcd")
    c = ctx["controller"]
    c.arm()
    type_string(c, "abcd")
    assert c.state == LockState.IDLE
    assert not ctx["overlay"].shown       # watchdog never surfaced
    assert not ctx["recorder"].recorded   # nothing recorded
    assert not ctx["tap"].running          # grab released


def test_wrong_challenge_trips_to_lockdown(store):
    ctx = build(store, secret="abcd")
    c = ctx["controller"]
    c.arm()
    type_string(c, "abce")
    assert c.state == LockState.LOCKED
    assert ctx["overlay"].shown
    assert ctx["recorder"].recorded


def test_partial_challenge_resets_and_does_not_trip(store):
    ctx = build(store, secret="abcd")
    c = ctx["controller"]
    c.arm()
    # Escape starts the attempt over; a short brush must not corrupt the next go.
    type_string(c, "ab")
    c.handle_key(KeyEvent(KeyKind.ESCAPE))
    type_string(c, "abcd")
    assert c.state == LockState.IDLE  # correct after the reset


def test_backspace_corrects_a_typo(store):
    ctx = build(store, secret="abcd")
    c = ctx["controller"]
    c.arm()
    type_string(c, "abx")
    c.handle_key(KeyEvent(KeyKind.BACKSPACE))
    type_string(c, "cd")
    assert c.state == LockState.IDLE


def test_modified_keys_ignored_in_challenge(store):
    ctx = build(store, secret="abcd")
    c = ctx["controller"]
    c.arm()
    # Control/Super-modified keys are ignored, like ⌘/⌃ on macOS.
    c.handle_key(KeyEvent.char_key("a", ctrl=True))
    c.handle_key(KeyEvent.char_key("b", meta=True))
    type_string(c, "abcd")
    assert c.state == LockState.IDLE


def test_lock_screen_unlocks_on_sequence(store):
    ctx = build(store, secret="abcd")
    c = ctx["controller"]
    c.arm()
    type_string(c, "zzzz")      # wrong -> locked
    assert c.state == LockState.LOCKED
    type_string(c, "abcd")      # correct at the shield -> unlock
    assert c.state == LockState.IDLE
    assert not ctx["overlay"].shown
    assert ctx["recorder"].cancelled  # partial clip discarded on owner unlock


def test_lock_screen_dots_track_typing(store):
    ctx = build(store, secret="abcdef")
    c = ctx["controller"]
    c.arm()
    type_string(c, "wrongg")  # 6 chars, wrong -> locked
    assert c.state == LockState.LOCKED
    c.handle_key(KeyEvent.char_key("x"))
    assert ctx["overlay"].typed_count == 1


def test_lock_now_from_idle(store):
    ctx = build(store, secret="abcd")
    c = ctx["controller"]
    c.lock_now()
    assert c.state == LockState.LOCKED
    assert ctx["overlay"].shown


def test_lock_now_from_armed(store):
    ctx = build(store, secret="abcd")
    c = ctx["controller"]
    c.arm()
    c.lock_now()
    assert c.state == LockState.LOCKED


def test_auto_arm_fires_past_threshold(store):
    ctx = build(store, secret="abcd", minutes=5, idle=5 * 60 + 1)
    c = ctx["controller"]
    c._check_inactivity()
    assert c.state == LockState.ARMED
    assert ctx["flash"].flashes == 1  # announced with a white flash


def test_auto_arm_holds_below_threshold(store):
    ctx = build(store, secret="abcd", minutes=5, idle=60)
    c = ctx["controller"]
    c._check_inactivity()
    assert c.state == LockState.IDLE
    assert ctx["flash"].flashes == 0


def test_auto_arm_declines_without_secret(store):
    ctx = build(store, secret=None, minutes=5, idle=9999)
    c = ctx["controller"]
    c._check_inactivity()
    assert c.state == LockState.IDLE


def test_auto_arm_declines_when_idle_unknown(store):
    ctx = build(store, secret="abcd", minutes=5, idle=None)
    c = ctx["controller"]
    c._check_inactivity()
    assert c.state == LockState.IDLE


def test_disarm_needs_proof_without_biometrics(store):
    ctx = build(store, secret="abcd", biometrics_available=False)
    c = ctx["controller"]
    c.arm()
    c.request_disarm()
    # No fingerprint hardware: the menu route can't disarm; it points at the sequence
    # via a non-blocking tray notification (not a modal, since the keyboard is grabbed).
    assert c.state == LockState.ARMED
    assert ctx["notifications"]
    assert not ctx["alerts"]


def test_disarm_with_biometric_success(store):
    ctx = build(store, secret="abcd", biometrics_available=True)
    c = ctx["controller"]
    c.arm()
    c.request_disarm()
    ctx["biometrics"].deliver(True)
    assert c.state == LockState.IDLE


def test_quit_while_armed_requires_proof(store):
    ctx = build(store, secret="abcd", biometrics_available=True)
    c = ctx["controller"]
    c.arm()
    results = []
    c.request_quit(results.append)
    ctx["biometrics"].deliver(True)
    assert results == [True]
    assert c.state == LockState.IDLE  # disarmed before the quit is allowed


def test_quit_while_idle_is_allowed(store):
    ctx = build(store, secret="abcd")
    c = ctx["controller"]
    results = []
    c.request_quit(results.append)
    assert results == [True]
