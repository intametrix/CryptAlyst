#!/bin/bash
# build-agent.sh — build the CryptALAS agent for one or every platform.
#
#   ./build-agent.sh            build for the current OS/arch  -> bin/cryptalas
#   ./build-agent.sh mac        build for macOS arm64           -> bin/cryptalas
#   ./build-agent.sh linux      build for Linux amd64           -> bin/cryptalas-linux-amd64
#   ./build-agent.sh windows    build for Windows amd64         -> bin/cryptalas.exe
#   ./build-agent.sh all        all three
#
# The agent is a single static Go binary; no runtime, no Python, no installer
# needed. It multicasts its beacon on 239.255.42.99:8788 so a platform on the
# same site discovers it automatically.
set -u
cd "$(dirname "$0")" || exit 1

GO="${GO:-go}"
OUT=bin
mkdir -p "$OUT"

# Human-readable semver, from the nearest git tag, else a default bumped here.
# This is what the TTY and registry DISPLAY. The git SHA remains the exact
# build identity in the manifest for precise update comparison.
VERSION_TAG="${VERSION_TAG:-$(git describe --tags --abbrev=0 2>/dev/null || echo v1.2.0)}"
SHA="$(git rev-parse --short HEAD 2>/dev/null || echo dev)"
# Full display version: human semver + short sha, e.g. "v1.2.0-a348efa".
VERSION="${VERSION:-${VERSION_TAG#v}-${SHA}}"
BUILDTIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
# Where the agent looks for updates by default. Point this at your release
# host (GitHub releases, a static server, etc.). Override with MANIFEST_BASE.
MANIFEST_BASE="${MANIFEST_BASE:-http://192.168.1.142:8899}"
# Platform the agent registers to over HTTP (baked in so it needs no -register).
REGISTER_URL="${REGISTER_URL:-http://192.168.1.142:8787}"

manifest_for() { # manifest_for <os>
  case "$1" in
    darwin)  echo "$MANIFEST_BASE/manifest-cryptalas.json" ;;
    linux)   echo "$MANIFEST_BASE/manifest-cryptalas-linux-amd64.json" ;;
    windows) echo "$MANIFEST_BASE/manifest-cryptalas.exe.json" ;;
  esac
}

build() { # build <os> <arch> <suffix>
  local os=$1 arch=$2 suffix=$3
  local ldflags="-X main.version=$VERSION -X main.buildTime=$BUILDTIME -X main.defaultManifest=$(manifest_for "$os") -X main.defaultRegister=$REGISTER_URL"
  printf 'building cryptalas for %s/%s (%s, manifest %s) ... ' "$os" "$arch" "$VERSION" "$(manifest_for "$os")"
  if GOOS=$os GOARCH=$arch "$GO" build -ldflags "$ldflags" -o "$OUT/cryptalas$suffix" ./cmd/cryptalas; then
    printf '\033[1;32mok\033[0m -> %s/cryptalas%s\n' "$OUT" "$suffix"
  else
    printf '\033[1;31mfailed\033[0m\n'
    exit 1
  fi
}

case "${1:-current}" in
  current) build "$(go env GOOS)" "$(go env GOARCH)" "" ;;
  mac)     build darwin arm64 "" ;;
  linux)   build linux amd64 "-linux-amd64" ;;
  windows) build windows amd64 ".exe" ;;
  all)
    build darwin arm64 ""
    build linux amd64 "-linux-amd64"
    build windows amd64 ".exe"
    ;;
  *) echo "usage: $0 [current|mac|linux|windows|all]" >&2; exit 2 ;;
esac

echo "run it with:  $OUT/cryptalas${2:+${2#* } } # zero config — it multicasts its beacon"
