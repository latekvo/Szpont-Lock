#!/usr/bin/env bash
# Remove the XDG autostart entry and stop any running SzpontLock tray applet.
set -euo pipefail

DESKTOP="${XDG_CONFIG_HOME:-$HOME/.config}/autostart/szpontlock.desktop"
if [ -f "$DESKTOP" ]; then
    rm -f "$DESKTOP"
    echo "Removed autostart entry: $DESKTOP"
else
    echo "No autostart entry at $DESKTOP"
fi

if pkill -f "python3 -m szpontlock_app" 2>/dev/null; then
    echo "Stopped SzpontLock."
else
    echo "SzpontLock was not running."
fi
