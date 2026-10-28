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

VERSION="$(git rev-parse --short HEAD 2>/dev/null || echo dev)"
BUILDTIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
LDFLAGS="-X main.version=$VERSION -X main.buildTime=$BUILDTIME"

build() { # build <os> <arch> <suffix>
  local os=$1 arch=$2 suffix=$3
  printf 'building cryptalas for %s/%s (%s) ... ' "$os" "$arch" "$VERSION"
  if GOOS=$os GOARCH=$arch "$GO" build -ldflags "$LDFLAGS" -o "$OUT/cryptalas$suffix" ./cmd/cryptalas; then
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
