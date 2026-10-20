#!/usr/bin/env bash
#
# THE GATE. One command, both languages, no way to run half of it.
#
# WHY THIS EXISTS AS ONE SCRIPT. Rule 0h says always lint. The failure mode a rule
# alone does not fix is an agent linting ITS OWN half and reporting "linted" — I
# did exactly that for most of a session, running `go vet` and `go test` and
# believing I had satisfied a linting requirement while `gofmt -l` was flagging
# eight of my own files. Two separate commands make that mistake possible; one
# command does not.
#
# Usage:  ./check.sh            full gate
#         ./check.sh --quick    skip the slow frontend checks (mid-edit only)
#
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

ROOT="$PWD"
# Toolchain resolution, most-specific first:
#   1. explicit DSH_NODE / DSH_PNPM override (a managed runtime)
#   2. `node` / `pnpm` found on PATH (a normal local checkout)
# The original version hardcoded one machine's managed-runtime paths, which
# made every TS gate step fail on any other checkout regardless of code state.
NODE="${DSH_NODE:-$(command -v node || true)}"
if [ -n "${DSH_PNPM:-}" ]; then
  PNPM="$DSH_PNPM"
else
  PNPM="$(command -v pnpm || true)"
fi
# pnpm may be a node script (pnpm.mjs) or its own executable shim.
pnpm() {
  case "$PNPM" in
    *.mjs) [ -n "$NODE" ] && "$NODE" "$PNPM" "$@" ;;
    *)     [ -n "$PNPM" ] && "$PNPM" "$@" ;;
  esac
}
QUICK=0
[ "${1:-}" = "--quick" ] && QUICK=1

# A gate that cannot find its toolchain must fail, not skip: an empty
# invocation produces empty output, which every check below would read as
# "pass".
[ -n "$NODE" ] || { printf '\033[31mFAIL\033[0m  node not found; set DSH_NODE\n'; exit 1; }

# tsc invocation: via pnpm when available, else the checkout's own
# node_modules/.bin/tsc. `pnpm exec tsc` and the local tsc binary resolve to
# the same compiler here; preferring pnpm keeps the managed-runtime path
# identical to before, the local binary is the no-pnpm fallback.
TSC=""
if [ -n "$PNPM" ]; then
  TSC="pnpm exec tsc"
elif [ -x web/node_modules/.bin/tsc ]; then
  TSC="node_modules/.bin/tsc"
fi
[ -n "$TSC" ] || { printf '\033[31mFAIL\033[0m  neither pnpm nor web/node_modules/.bin/tsc found\n'; exit 1; }

fail=0
step() { printf '\n\033[1m== %s\033[0m\n' "$1"; }
ok()   { printf '   \033[32mPASS\033[0m  %s\n' "$1"; }
bad()  { printf '   \033[31mFAIL\033[0m  %s\n' "$1"; fail=1; }

# ---------------------------------------------------------------- Go (backend)
step "GO  gofmt -l   (must print NOTHING)"
unformatted="$(cd backend && gofmt -l . 2>/dev/null | grep -v '^\.gopath/' || true)"
if [ -z "$unformatted" ]; then ok "all files formatted"
else bad "unformatted:"; printf '         %s\n' $unformatted; fi

# OUTPUT CAPTURE, NOT PIPELINE STATUS. `set -o pipefail` (top of this file)
# makes a failing tool's non-zero exit dominate the pipeline status, so
# `cmd | grep -q .` inverts: a tool that exits non-zero AND prints errors
# looked like "clean" (the if saw failure and took the ok branch). tsc and go
# are exactly such tools, so every typecheck/build/test failure was reported
# as PASS. Capturing the output and testing -n sidesteps pipefail entirely.
step "GO  go vet"
vet_out="$(cd backend && go vet ./... 2>&1 | head -20)"
if [ -n "$vet_out" ]; then bad "vet reported problems"; printf '         %s\n' "$vet_out"; else ok "vet clean"; fi

step "GO  go build"
build_out="$(cd backend && go build ./... 2>&1 | head -20)"
if [ -n "$build_out" ]; then bad "build failed"; printf '         %s\n' "$build_out"; else ok "build clean"; fi

step "GO  go test"
test_out="$(cd backend && go test ./... 2>&1 | grep -E 'FAIL|panic' | head -10)"
if [ -n "$test_out" ]; then bad "tests failed"; printf '         %s\n' "$test_out"; else ok "tests pass"; fi

# ------------------------------------------------------- TypeScript (frontend)
if [ ! -d web ]; then
  printf '\n   (no web/ directory; frontend gate skipped)\n'
else
  step "TS  tsc -b --force  (app + scripts)"
  tsc_app_out="$(cd web && $TSC -b --force 2>&1 | head -30)"
  if [ -n "$tsc_app_out" ]; then
    bad "typecheck reported errors"; printf '         %s\n' "$tsc_app_out"
  else
    ok "app typechecks"
  fi

  step "TS  tsc -p scripts/tsconfig.json"
  tsc_scripts_out="$(cd web && $TSC -p scripts/tsconfig.json 2>&1 | head -30)"
  if [ -n "$tsc_scripts_out" ]; then
    bad "scripts typecheck reported errors"; printf '         %s\n' "$tsc_scripts_out"
  else
    ok "scripts typecheck"
  fi

  if [ "$QUICK" = "1" ]; then
    printf '\n   (--quick: skipping audit-css / verify / build-smoke / smoke)\n'
  else
    # Watchdog: a hung check must FAIL, not stall the gate forever. Known
    # trigger: build-smoke's vite/rolldown native module can hang (rather
    # than error) under a Node whose codesigning it rejects -- the script's
    # header documents this as an environment limitation.
    run_ts() {
      local s=$1 limit=${2:-180} i=0
      (cd web && "$NODE" "scripts/$s.mjs" >/dev/null 2>&1) &
      local pid=$!
      while kill -0 "$pid" 2>/dev/null && [ "$i" -lt "$limit" ]; do sleep 1; i=$((i+1)); done
      if kill -0 "$pid" 2>/dev/null; then
        kill "$pid" 2>/dev/null; wait "$pid" 2>/dev/null
        return 124
      fi
      wait "$pid"; return $?
    }
    for s in audit-css verify build-smoke smoke; do
      step "TS  node scripts/$s.mjs"
      if run_ts "$s"; then ok "$s"; else bad "$s"; fi
    done
  fi
fi

printf '\n'
if [ "$fail" = "0" ]; then
  printf '\033[1;32mGATE PASSED\033[0m  both languages, every check above.\n'
else
  printf '\033[1;31mGATE FAILED\033[0m  fix the FAIL lines; a passing typecheck alone is not the gate.\n'
fi
exit "$fail"
