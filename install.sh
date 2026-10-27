#!/bin/bash
# install.sh — build every CryptAlyst binary and the web dashboard.
#
# Two Go modules, one React app:
#   backend/      (module pumpdump/backend)  -> backend/bin/
#   scalp5/core/  (module cryptalyst/core)   -> scalp5/core/bin/
#   web/          (React, vite)              -> backend/web/dist/  (embedded by the Go server)
#
# Usage:
#   ./install.sh                 build everything
#   ./install.sh --no-web        skip the web build
#   ./install.sh --help
#
# Idempotent: safe to re-run. After it finishes, bring the platform up with
#   ./scalp5/scripts/start.sh            (trading OFF)
#   ./scalp5/scripts/start.sh --trade    (books running)
set -u
cd "$(dirname "$0")" || exit 1

ROOT=$(pwd)
BACKEND="$ROOT/backend"
CORE="$ROOT/scalp5/core"
WEB="$ROOT/web"

# Toolchain. `go` comes from PATH; node/pnpm fall back to the DSH runtime so
# the web build works even when they are not on PATH.
GO="${GO:-$(command -v go || true)}"
NODE="${NODE:-/Users/users1/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/node/bin/node}"
PNPM="${PNPM:-/Users/users1/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/pnpm/bin/pnpm.mjs}"

BUILD_WEB=1
for arg in "$@"; do
  case "$arg" in
    --no-web) BUILD_WEB=0 ;;
    --help|-h)
      sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "unknown option: $arg (try --help)" >&2; exit 2 ;;
  esac
done

say() { printf '\033[1;36m%s\033[0m\n' "$*"; }
ok()  { printf '  \033[1;32m✓\033[0m %s\n' "$*"; }
bad() { printf '  \033[1;31m✗\033[0m %s\n' "$*"; }

[ -n "$GO" ] || { echo "go not found on PATH" >&2; exit 1; }

build_mod() { # build_mod <dir> <out-dir> <module-name>
  local dir=$1 out=$2 mod=$3
  say "install: $mod"
  mkdir -p "$out"
  local n=0
  for c in "$dir"/cmd/*; do
    [ -d "$c" ] || continue
    local name
    name=$(basename "$c")
    (cd "$dir" && "$GO" build -o "$out/$name" "./cmd/$name") || { bad "$name"; exit 1; }
    ok "$name"
    n=$((n+1))
  done
  printf '  \033[1;32m%d\033[0m binaries -> %s\n' "$n" "$out"
}

build_mod "$BACKEND" "$BACKEND/bin" "backend Go (pumpdump/backend)"
build_mod "$CORE" "$CORE/bin" "core Go (cryptalyst/core)"

if [ "$BUILD_WEB" = "1" ]; then
  say "install: web dashboard (React -> backend/web/dist)"
  if [ ! -x "$NODE" ] || [ ! -f "$PNPM" ]; then
    bad "node/pnpm not found (NODE=$NODE PNPM=$PNPM) — use --no-web or set NODE/PNPM"
    exit 1
  fi
  (cd "$WEB" && "$NODE" "$PNPM" run build >/dev/null) || { bad "web build"; exit 1; }
  ok "web dashboard"
else
  warn "skipped web build (--no-web)"
fi

say "install: state directories"
mkdir -p "$ROOT/scalp5/state" "$ROOT/scalp5/logs" "$ROOT/backend/data" "$ROOT/scalp5/core/state"
ok "directories ready"

say "install complete"
echo
echo "  bring the platform up:  ./scalp5/scripts/start.sh            (trading OFF)"
echo "                          ./scalp5/scripts/start.sh --trade    (books running)"
echo "  stop everything:        ./scalp5/scripts/stop.sh"
echo "  GPU agent (optional):   $BACKEND/bin/cryptalas --tty   (or --device gpu)"
