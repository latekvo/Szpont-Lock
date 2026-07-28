"""A platform-neutral key event.

The X11 grab pump (``eventtap``) translates each raw ``KeyPress`` into one of these
and hands it to the controller, so the state machine never touches Xlib and can be
driven straight from a test. The classification mirrors the macOS handler: Return /
keypad-Enter and Escape start the attempt over, Backspace corrects a typo, and
anything held with Control or the Super ("meta") key is ignored — the analogue of
the macOS build ignoring ⌘ and ⌃ combinations.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto


class KeyKind(Enum):
    CHAR = auto()       # a printable character was typed (see ``char``)
    ENTER = auto()      # Return or keypad Enter — clears the buffer
    ESCAPE = auto()     # Escape — clears the buffer
    BACKSPACE = auto()  # deletes the last character
    OTHER = auto()      # arrows, function keys, bare modifiers — ignored


@dataclass(frozen=True)
class KeyEvent:
    kind: KeyKind
    char: str = ""
    ctrl: bool = False
    meta: bool = False  # Super/Command-equivalent held

    @staticmethod
    def char_key(char: str, *, ctrl: bool = False, meta: bool = False) -> "KeyEvent":
        return KeyEvent(KeyKind.CHAR, char=char, ctrl=ctrl, meta=meta)
