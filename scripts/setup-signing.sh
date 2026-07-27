#!/usr/bin/env bash
# Creates a stable local code-signing identity for SzpontLock.
#
# Why this exists: ad-hoc signing (`codesign -s -`) makes an app's designated
# requirement its own cdhash, so every rebuild is a different app as far as TCC is
# concerned - and the Accessibility grant silently stops applying while System Settings
# still shows a stale, enabled-looking row. Signing with a real certificate instead
# gives a designated requirement pinned to the certificate, which survives rebuilds.
#
# The key lives in a dedicated keychain whose password this script owns, so codesign
# never has to prompt. Idempotent: safe to re-run.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KEYCHAIN="$HOME/Library/Keychains/szpontlock-signing.keychain-db"
KEYCHAIN_SHORT="szpontlock-signing.keychain"
KEYCHAIN_PASSWORD="szpontlock"
IDENTITY="SzpontLock Local Signing"
WORK="$ROOT/.signing"

if security find-certificate -c "$IDENTITY" "$KEYCHAIN" >/dev/null 2>&1; then
    echo "Signing identity '$IDENTITY' already present."
    security unlock-keychain -p "$KEYCHAIN_PASSWORD" "$KEYCHAIN"
    exit 0
fi

mkdir -p "$WORK"
chmod 700 "$WORK"

# System LibreSSL, not Homebrew OpenSSL 3.x: the latter writes PKCS12 bundles with a
# SHA-256 MAC that Apple's `security import` cannot verify ("MAC verification failed").
OPENSSL=/usr/bin/openssl

"$OPENSSL" req -x509 -newkey rsa:2048 -sha256 -days 7300 -nodes \
    -keyout "$WORK/signing.key" -out "$WORK/signing.crt" \
    -subj "/CN=$IDENTITY" \
    -addext "basicConstraints=critical,CA:false" \
    -addext "keyUsage=critical,digitalSignature" \
    -addext "extendedKeyUsage=critical,codeSigning" 2>/dev/null

"$OPENSSL" pkcs12 -export -out "$WORK/signing.p12" \
    -inkey "$WORK/signing.key" -in "$WORK/signing.crt" \
    -name "$IDENTITY" -passout "pass:$KEYCHAIN_PASSWORD" 2>/dev/null

security delete-keychain "$KEYCHAIN_SHORT" 2>/dev/null || true
security create-keychain -p "$KEYCHAIN_PASSWORD" "$KEYCHAIN_SHORT"
security set-keychain-settings "$KEYCHAIN_SHORT"          # no auto-lock timeout
security unlock-keychain -p "$KEYCHAIN_PASSWORD" "$KEYCHAIN_SHORT"

security import "$WORK/signing.p12" -k "$KEYCHAIN_SHORT" \
    -P "$KEYCHAIN_PASSWORD" -T /usr/bin/codesign -A >/dev/null

# Without this codesign shows a GUI "wants to use a key" prompt on every build.
security set-key-partition-list -S apple-tool:,apple:,codesign: \
    -s -k "$KEYCHAIN_PASSWORD" "$KEYCHAIN_SHORT" >/dev/null 2>&1

# Put it on the search list so codesign can resolve the identity by name.
EXISTING=$(security list-keychains -d user | sed -e 's/^[[:space:]]*"//' -e 's/"$//')
# shellcheck disable=SC2086
security list-keychains -d user -s $EXISTING "$KEYCHAIN_SHORT"

rm -f "$WORK/signing.key" "$WORK/signing.p12"

echo "Created signing identity '$IDENTITY' in $KEYCHAIN_SHORT."
