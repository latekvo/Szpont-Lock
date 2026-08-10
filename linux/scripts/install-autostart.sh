#!/usr/bin/env bash
# Install an XDG autostart entry so SzpontLock's tray applet reappears every login
# (the cross-desktop analogue of a macOS LaunchAgent), and start it now.
set -euo pipefail

LINUX_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LAUNCHER="$LINUX_DIR/szpontlock"
AUTOSTART_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"
DESKTOP="$AUTOSTART_DIR/szpontlock.desktop"

mkdir -p "$AUTOSTART_DIR"
cat > "$DESKTOP" <<EOF
[Desktop Entry]
Type=Application
Name=SzpontLock
Comment=Watchdog screen lock (tray applet)
Exec=$LAUNCHER
Terminal=false
X-GNOME-Autostart-enabled=true
EOF
echo "Installed autostart entry: $DESKTOP"

echo "Starting SzpontLock…"
nohup "$LAUNCHER" >/dev/null 2>&1 &
echo "Started (newest-wins: this replaces any tray already running)."
