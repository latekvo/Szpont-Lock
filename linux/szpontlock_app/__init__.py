"""SzpontLock — Linux (Qt6 / PySide6 + X11) port of the macOS menu-bar watchdog.

A thin re-implementation of the same watchdog the macOS app runs: idle -> armed ->
locked, a keyboard that becomes a silent password prompt while armed, a shield on
every display and a webcam clip on a wrong sequence. The behaviour and the on-disk
formats (config hash, event log) match the macOS build; only the platform
mechanisms differ (X11 keyboard grab instead of a CGEventTap, ffmpeg instead of
AVFoundation, fprintd-or-sequence instead of Touch ID, MIT-SCREEN-SAVER idle
instead of CGEventSource).
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
