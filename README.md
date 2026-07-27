# SzpontLock

A watchdog screen lock for macOS that lives in the menu bar.

Arm it and the machine looks completely normal - the screen stays on and the mouse keeps
working. The moment anyone touches the keyboard, SzpontLock swallows that keystroke,
records five seconds of whoever is at the machine, blacks out every display and refuses
all input until it is unlocked with Touch ID or the unlock sequence.

It also locks itself after a configurable stretch of inactivity, so walking away is enough.

## States

| Menu bar icon | State | Behaviour |
| --- | --- | --- |
| open padlock | idle | nothing is running |
| orange eye | armed | mouse passes through, the first keystroke springs the trap |
| red padlock | locked | all input swallowed, shield on every display, photo taken |

## Building

```sh
./scripts/setup-signing.sh  # once per machine - see "Signing" below
./scripts/build.sh          # -> dist/SzpontLock.app
open dist/SzpontLock.app
```

## Permissions

Two grants are needed, both prompted for on first launch:

- **Accessibility** (System Settings › Privacy & Security › Accessibility) - required to
  observe and suppress input. Without it the watchdog cannot arm at all.
- **Camera** - requested when you first arm, so the prompt never lands mid-lockdown where
  the shield would hide it.

## Signing

`scripts/setup-signing.sh` creates a self-signed code-signing certificate in a dedicated
keychain (`szpontlock-signing.keychain`) whose password the script owns, so builds never
prompt. Run it once; `build.sh` picks the identity up automatically.

This is not cosmetic. With ad-hoc signing (`codesign -s -`) the app's designated
requirement is its own cdhash:

```
designated => cdhash H"f3e7a668042f59370a5c236a30e4994c8d4f1ed6"
```

so **every rebuild is a different app to TCC**, and the Accessibility grant silently stops
applying - while System Settings keeps showing a stale row that still looks enabled, so
toggling it appears to work and changes nothing. With the certificate the requirement
becomes:

```
designated => identifier "com.szpont.SzpontLock" and certificate leaf = H"33c82f6c..."
```

which is pinned to the certificate rather than the binary, and survives rebuilds. Verified
by building twice across a real source change: cdhash moved, designated requirement did not.

If Accessibility ever does get into a confused state, clear it and grant once more:

```sh
tccutil reset Accessibility com.szpont.SzpontLock
```

The certificate is not trusted for Gatekeeper, which does not matter here - the app is
built and launched locally, never distributed. Note `openssl` must be the system LibreSSL:
Homebrew's OpenSSL 3.x writes PKCS12 bundles that `security import` rejects with "MAC
verification failed".

## Auto-lock

**Auto-Lock When Idle** in the menu offers Off / 1 / 5 / 10 / 15 / 30 minutes, defaulting
to 5. Inactivity is measured system-wide with
`CGEventSource.secondsSinceLastEventType(.hidSystemState, …)`, so it counts real hardware
input rather than anything SzpontLock sees - and it keeps counting while the app sits idle,
not just while armed. Reaching the threshold goes straight to lockdown, arming on the way
if needed.

It stays quiet rather than nagging: if there is no unlock sequence set, or Accessibility
has not been granted, the timer declines to fire rather than throwing a dialog at an
unattended machine.

## Recording

Tripping records a **5 second clip** to the **Desktop** as `szpontlock_<timestamp>.mov`,
where it is impossible to miss. If Desktop access is denied, recordings fall back to
`~/Library/Application Support/SzpontLock/Captures/`.

Video only - audio would mean a Microphone permission prompt that was never asked for. To
add it, add an audio `AVCaptureDeviceInput` in `CameraRecorder.record` plus an
`NSMicrophoneUsageDescription` key in `scripts/build.sh`.

The clip always runs its full length, even if you unlock after two seconds; stopping early
buys nothing and risks a truncated file.

## Unlocking

- **Touch ID** - offered automatically on lockdown, and via the button for retries.
- **Unlock sequence** - just type it; it is matched as you type, no Return needed. Escape
  or Return clears what you have typed so far. ⌘ and ⌃ combinations are ignored.

Only a salted, stretched SHA-256 of the sequence is written to disk (100k rounds, about
50 ms per attempt), in `~/Library/Application Support/SzpontLock/config.json`.

## Where things land

```
~/Desktop/
  szpontlock_<timestamp>.mov    5 second intrusion recording

~/Library/Application Support/SzpontLock/
  config.json          salt + hash of the unlock sequence (mode 0600)
  events.log           armed / tripped / recorded / unlocked, timestamped
  Captures/            fallback for recordings if Desktop access is denied
```

## Escape hatch

Set `SZPONTLOCK_PANIC_TIMEOUT` to a number of seconds and lockdown releases itself after
that long. It is off unless set, and when it is set the lock screen says so, so it can
never be silently in effect. Useful while testing:

```sh
SZPONTLOCK_PANIC_TIMEOUT=45 dist/SzpontLock.app/Contents/MacOS/SzpontLock
```

If you are ever genuinely stuck: `pkill -f SzpontLock` from another machine over SSH, or
power-cycle. SzpontLock does not install a login item, so it comes back up idle.

## What this is and is not

It is a deterrent against someone sitting down at your unattended laptop: it makes tampering
loud, photographed and logged. It is **not** a security boundary. A CGEventTap is a
user-space mechanism, and it does not stop:

- the power button, or a forced restart
- anyone with SSH or remote access to the machine
- anything that runs before the app does

For real protection when you walk away, use the actual macOS screen lock. This is for the
case you asked for - screen visibly on, mouse alive, keyboard rigged.

## Implementation notes

- **One tap, two phases.** A single `.cgSessionEventTap` with a full event mask serves both
  phases; the handler branches on state. It is registered in `commonModes` *and*
  `NSModalPanelRunLoopMode`, or it would stop suppressing input while a modal panel is up.
- **The trip is deferred, the commitment is not.** `trip()` shows windows and starts the
  camera, far too slow to run inside the tap callback, so it is dispatched to the next
  run-loop turn - but an `isTripping` flag is set synchronously, so keystrokes in that gap
  are already swallowed.
- **The shield ducks for Touch ID.** The shield sits at `CGShieldingWindowLevel`
  (2147483628), above the menu bar, Dock and notification banners. The Touch ID panel is
  drawn by `coreautha` at level 1000 in a *different process*, so it cannot be raised from
  here - instead the shield drops to 999 while a prompt is up, still covering everything
  else, and goes back up afterwards. Prompts are capped at 25 s to bound that window.
- **Sequence matching runs off the tap callback.** The stretched hash costs ~50 ms; doing
  that inside the callback would get the tap disabled for being unresponsive.
- **Display sleep is pinned** with an `IOPMAssertionCreateWithName` no-display-sleep
  assertion while armed or locked, so the screen never blanks.
