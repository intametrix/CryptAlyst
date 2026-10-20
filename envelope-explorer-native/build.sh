#!/bin/bash
# Build The Envelope Explorer as a macOS app bundle.
#
#   ./build.sh            app bundle in build/
#   ./build.sh --zip      also produces a distributable zip
set -eu
cd "$(dirname "$0")"
APP="build/The Envelope Explorer.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

echo "· compiling"
swiftc -O Sources/main.swift -o "$APP/Contents/MacOS/EnvelopeExplorer" \
  -framework Cocoa -framework WebKit

cp Resources/offline.html "$APP/Contents/Resources/offline.html"

# optionally bundle the service binary so the app can start it itself
if [ -x ../scalp5/core/bin/envelopeapi ]; then
  cp ../scalp5/core/bin/envelopeapi "$APP/Contents/Resources/envelopeapi"
fi

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>The Envelope Explorer</string>
  <key>CFBundleDisplayName</key><string>The Envelope Explorer</string>
  <key>CFBundleIdentifier</key><string>com.cryptalyst.envelope-explorer</string>
  <key>CFBundleVersion</key><string>1.0.0</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundleExecutable</key><string>EnvelopeExplorer</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>LSApplicationCategoryType</key><string>public.app-category.finance</string>
</dict></plist>
PLIST

codesign --force --deep --sign - "$APP" >/dev/null 2>&1 || true
SIZE=$(du -sh "$APP" | cut -f1)
echo "· built $APP ($SIZE)"

if [ "${1:-}" = "--zip" ]; then
  (cd build && ditto -c -k --sequesterRsrc --keepParent "The Envelope Explorer.app" "The-Envelope-Explorer.zip")
  echo "· packaged build/The-Envelope-Explorer.zip"
fi
