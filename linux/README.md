# SzpontLock — Linux applet (Qt6 / PySide6 + X11)

The Linux port of the macOS menu-bar watchdog: a **system-tray applet** that runs the
same state machine — idle → armed → locked — with the same behaviour. Arm it and the
machine looks normal (screen on, mouse alive) while the keyboard quietly becomes a
silent password prompt; type the sequence and it stands down without ever showing
itself, type anything else and every display goes to a shield, a webcam clip is
recorded, and all input is swallowed until the sequence (or a fingerprint) releases
it. It also arms itself after a stretch of inactivity, flashing the screen white to
say so.

The macOS build under [`../Sources`](../Sources) and [`../Package.swift`](../Package.swift)
is **untouched** — this port adds a parallel front-end and never changes the Swift
target. The on-disk formats match: the salted-hash `config.json` and the `events.log`
are written exactly as the macOS `SecretStore` writes them, and the shared
[`../Resources/lockdown.png`](../Resources) is the lockdown picture on both.

Universal across desktops via Qt6's `QSystemTrayIcon` (StatusNotifierItem / XEmbed):
works on **XFCE**, **KDE**, and **GNOME** (with an AppIndicator extension).

## How the platform pieces map

| Concern | macOS | Linux (this port) |
| --- | --- | --- |
| Swallow every keystroke | `CGEventTap` (`.cgSessionEventTap`) | X11 `XGrabKeyboard` on the root window (`eventtap.py`) |
| System-wide idle time | `CGEventSource.secondsSinceLastEventType` | X11 MIT-SCREEN-SAVER `idle` (`idle.py`) |
| Webcam clip | `AVAssetWriter` / AVFoundation | one `ffmpeg` V4L2 child (`recorder.py`) |
| Shield on every display | `NSWindow` at `CGShieldingWindowLevel` | Qt fullscreen windows, `X11BypassWindowManagerHint` (`overlay.py`) |
| Proof to disarm / unlock | Touch ID (`LocalAuthentication`) | `fprintd` if present, else the unlock sequence (`biometrics.py`) |
| Keep the display awake | `IOPMAssertionCreateWithName` | `systemd-inhibit` + X screen-saver reset (`displayassertion.py`) |
| Menu-bar item | `NSStatusItem` | `QSystemTrayIcon` (`tray.py`) |
| Secret hash + log | `SecretStore` (CryptoKit) | `secretstore.py` (hashlib) — identical scheme |

## Requirements

- **X11.** The keyboard grab is an X mechanism; a **Wayland** session cannot grab the
  keyboard this way, so arming will fail there. Check with `echo $XDG_SESSION_TYPE`.
- Python 3.10+
- PySide6 and python-xlib (`pip install -r requirements.txt`)
- `ffmpeg` and a V4L2 webcam (`/dev/video0`) for the intrusion clip — without them the
  lock still works, the recording just reports "failed"
- Optional: `fprintd` with an enrolled finger, for fingerprint disarm/unlock. Without
  it the only proof is the unlock sequence (the same fallback macOS shows without
  Touch ID).

## Run

```bash
cd linux
pip install -r requirements.txt      # or: pip install --user --break-system-packages ...
./szpontlock                         # tray applet (padlock in the tray)
```

On first launch it offers to set an unlock sequence — do that before arming, or a
fingerprint (if you have one) would be the only way back in. Quit from the tray menu,
or `pkill -f "python -m szpontlock_app"`.

> **Careful:** arming installs a real, whole-keyboard grab and, on a wrong sequence,
> a full-screen shield. Set an unlock sequence you remember, and note the escape
> hatch below before you try it. `SZPONTLOCK_PANIC_TIMEOUT` is the safety net while
> testing.

## Autostart on login

```bash
./scripts/install-autostart.sh    # XDG autostart .desktop, and starts it now
./scripts/uninstall-autostart.sh  # removes it and stops the app
```

Installs `~/.config/autostart/szpontlock.desktop` so the padlock reappears every
login — the cross-desktop analogue of the macOS LaunchAgent.

## Escape hatch

Set `SZPONTLOCK_PANIC_TIMEOUT` to a number of seconds and lockdown releases itself
after that long. Off unless set, and always announced on the lock screen so it can
never be silently in effect:

```bash
SZPONTLOCK_PANIC_TIMEOUT=45 ./szpontlock
```

If you are ever genuinely stuck: `pkill -f szpontlock_app` from another machine over
SSH, or switch VT (`Ctrl-Alt-F3`) and kill it there — the X grab is confined to the
graphical session. SzpontLock installs no login item unless you ask, so it comes back
up idle.

## Where things land

```
~/Desktop/
  szpontlock_<timestamp>.mov          intrusion clip (falls back below if Desktop is unwritable)

$XDG_DATA_HOME/SzpontLock/  (default ~/.local/share/SzpontLock/)
  config.json          salt + hash of the unlock sequence (mode 0600)
  events.log           armed / tripped / recorded / unlocked, timestamped
  Captures/            fallback for recordings if the Desktop is unwritable
```

Preferences (auto-arm interval) live in `QSettings`
(`~/.config/SzpontLock/SzpontLock.conf`).

## Offscreen renders & tests (no display needed)

```bash
# Paint the lock screen / tray icons to a PNG via the offscreen Qt platform:
SZPONTLOCK_RENDER=lock  SZPONTLOCK_RENDER_OUT=/tmp/lock.png  python -m szpontlock_app
SZPONTLOCK_RENDER=icons SZPONTLOCK_RENDER_OUT=/tmp/icons.png python -m szpontlock_app

# The pure-logic suite (hashing round-trip + the full state machine, with fakes):
QT_QPA_PLATFORM=offscreen python -m pytest tests/ -q
```

## What this is and is not

Same as the macOS build: a deterrent that makes tampering loud, photographed and
logged — **not** a security boundary. An `XGrabKeyboard` is a user-space mechanism. It
does not stop the power button, a VT switch, SSH, or anything that runs before the app
does. For real protection when you walk away, use your desktop's actual screen lock.
