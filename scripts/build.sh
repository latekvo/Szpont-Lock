#!/usr/bin/env bash
# Builds SzpontLock and assembles dist/SzpontLock.app.
#
# SwiftPM cannot emit an .app bundle, and macOS will not hand out Camera or
# Accessibility permission to a bare executable, so the bundle is assembled here.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP="$ROOT/dist/SzpontLock.app"
CONFIG="${CONFIG:-release}"

cd "$ROOT"
swift build -c "$CONFIG"
BINARY="$(swift build -c "$CONFIG" --show-bin-path)/SzpontLock"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BINARY" "$APP/Contents/MacOS/SzpontLock"
cp "$ROOT/Resources/lockdown.png" "$APP/Contents/Resources/lockdown.png"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleDevelopmentRegion</key>
    <string>en</string>
    <key>CFBundleExecutable</key>
    <string>SzpontLock</string>
    <key>CFBundleIdentifier</key>
    <string>com.szpont.SzpontLock</string>
    <key>CFBundleInfoDictionaryVersion</key>
    <string>6.0</string>
    <key>CFBundleName</key>
    <string>SzpontLock</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0</string>
    <key>CFBundleVersion</key>
    <string>1</string>
    <key>LSMinimumSystemVersion</key>
    <string>13.0</string>
    <key>LSUIElement</key>
    <true/>
    <key>NSCameraUsageDescription</key>
    <string>SzpontLock records whoever is at the keyboard when the watchdog trips.</string>
    <key>NSDesktopFolderUsageDescription</key>
    <string>SzpontLock saves intrusion recordings to your Desktop.</string>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>NSPrincipalClass</key>
    <string>NSApplication</string>
</dict>
</plist>
PLIST

# Prefer the stable local identity from scripts/setup-signing.sh. Ad-hoc signing would
# pin the designated requirement to the binary's own cdhash, so every rebuild would read
# as a different app and silently invalidate the Accessibility grant.
KEYCHAIN="$HOME/Library/Keychains/szpontlock-signing.keychain-db"
IDENTITY="SzpontLock Local Signing"

if security find-certificate -c "$IDENTITY" "$KEYCHAIN" >/dev/null 2>&1; then
    security unlock-keychain -p szpontlock "$KEYCHAIN"
    codesign --force --sign "$IDENTITY" --keychain "$KEYCHAIN" \
        --identifier com.szpont.SzpontLock "$APP"
else
    echo "warning: no stable signing identity; run scripts/setup-signing.sh." >&2
    echo "         Ad-hoc signing invalidates the Accessibility grant on every build." >&2
    codesign --force --sign - --identifier com.szpont.SzpontLock "$APP"
fi

echo "Built $APP"
codesign -d -r- "$APP" 2>&1 | grep designated
