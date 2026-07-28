"""Newest-wins single instance: a freshly launched tray terminates every other live
tray instance of the applet (matched by process identity in ``/proc``, not just a
pidfile), then claims the pidfile — so there is never more than one padlock in the
tray. Adapted from the Diplomat applet's ``singleton``.
"""

from __future__ import annotations

import os
import signal
import time
from pathlib import Path

_APPLET_MODULE = "szpontlock_app"


def _pidfile() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR") or os.path.join(
        os.path.expanduser("~"), ".cache"
    )
    directory = Path(base) / "szpontlock"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / "szpontlock.pid"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _is_applet(pid: int) -> bool:
    """Whether a live pid is a ``python -m szpontlock_app`` tray instance."""
    try:
        parts = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    except OSError:
        return False
    tokens = [p.decode("utf-8", "replace") for p in parts if p]
    try:
        i = tokens.index("-m")
    except ValueError:
        return False
    return i + 1 < len(tokens) and tokens[i + 1] == _APPLET_MODULE


def _other_instances() -> set[int]:
    me = os.getpid()
    uid = os.getuid()
    found: set[int] = set()
    try:
        entries = os.listdir("/proc")
    except OSError:
        return found
    for name in entries:
        if not name.isdigit():
            continue
        pid = int(name)
        if pid == me:
            continue
        try:
            if os.stat(f"/proc/{pid}").st_uid != uid:
                continue
        except OSError:
            continue
        if _is_applet(pid):
            found.add(pid)
    return found


class SingleInstance:
    @staticmethod
    def acquire_newest_wins() -> None:
        me = os.getpid()
        victims = _other_instances()
        pidfile = _pidfile()
        try:
            old = int(pidfile.read_text().strip())
            if old and old != me and _alive(old) and _is_applet(old):
                victims.add(old)
        except (OSError, ValueError):
            pass

        for pid in victims:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        for _ in range(20):  # up to ~2s grace for a clean Qt shutdown
            victims = {p for p in victims if _alive(p)}
            if not victims:
                break
            time.sleep(0.1)
        for pid in victims:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass

        try:
            pidfile.write_text(str(me))
        except OSError:
            pass

    @staticmethod
    def release() -> None:
        pidfile = _pidfile()
        try:
            if int(pidfile.read_text().strip()) == os.getpid():
                pidfile.unlink()
        except (OSError, ValueError):
            pass
