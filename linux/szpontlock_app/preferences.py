"""User preferences, the Linux analogue of the macOS ``Preferences`` (UserDefaults).

Persisted with ``QSettings`` (``~/.config/SzpontLock/SzpontLock.conf``). QSettings
works without a running QApplication as long as the organisation/application are
named explicitly, so this stays importable in headless self-tests.
"""

from __future__ import annotations

from PySide6.QtCore import QSettings

_AUTO_ARM_KEY = "autoArmMinutes"
_ORG = "SzpontLock"
_APP = "SzpontLock"

# Offered in the tray menu; 0 is "Off". Matches the macOS options exactly.
AUTO_ARM_OPTIONS = [0, 1, 5, 10, 15, 30]

# Length of the clip recorded when the watchdog trips.
RECORDING_SECONDS = 5.0


def _settings() -> QSettings:
    return QSettings(_ORG, _APP)


def auto_arm_minutes() -> int:
    """Minutes of system-wide input inactivity before the watchdog arms itself.
    Zero disables it. Defaults to 5, matching the macOS build."""
    s = _settings()
    if not s.contains(_AUTO_ARM_KEY):
        return 5
    try:
        return int(s.value(_AUTO_ARM_KEY))
    except (TypeError, ValueError):
        return 5


def set_auto_arm_minutes(minutes: int) -> None:
    _settings().setValue(_AUTO_ARM_KEY, int(minutes))


def auto_arm_label(minutes: int) -> str:
    return "Off" if minutes == 0 else f"{minutes} min"
