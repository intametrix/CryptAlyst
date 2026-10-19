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
NODE="${DSH_NODE:-/Users/andrewpreston/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/node/bin/node}"
PNPM="${DSH_PNPM:-/Users/andrewpreston/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/pnpm/bin/pnpm.mjs}"
QUICK=0
[ "${1:-}" = "--quick" ] && QUICK=1

fail=0
step() { printf '\n\033[1m== %s\033[0m\n' "$1"; }
ok()   { printf '   \033[32mPASS\033[0m  %s\n' "$1"; }
bad()  { printf '   \033[31mFAIL\033[0m  %s\n' "$1"; fail=1; }

# ---------------------------------------------------------------- Go (backend)
step "GO  gofmt -l   (must print NOTHING)"
unformatted="$(cd backend && gofmt -l . 2>/dev/null | grep -v '^\.gopath/' || true)"
if [ -z "$unformatted" ]; then ok "all files formatted"
else bad "unformatted:"; printf '         %s\n' $unformatted; fi

step "GO  go vet"
if (cd backend && go vet ./... 2>&1 | head -20 | grep -q .); then bad "vet reported problems"; else ok "vet clean"; fi

step "GO  go build"
if (cd backend && go build ./... 2>&1 | head -20 | grep -q .); then bad "build failed"; else ok "build clean"; fi

step "GO  go test"
if (cd backend && go test ./... 2>&1 | grep -E 'FAIL|panic' | head -10 | grep -q .); then bad "tests failed"; else ok "tests pass"; fi

# ------------------------------------------------------- TypeScript (frontend)
if [ ! -d web ]; then
  printf '\n   (no web/ directory; frontend gate skipped)\n'
else
  step "TS  tsc -b --force  (app + scripts)"
  if (cd web && "$NODE" "$PNPM" exec tsc -b --force 2>&1 | head -30 | grep -q .); then
    bad "typecheck reported errors"
  else
    ok "app typechecks"
  fi

  step "TS  tsc -p scripts/tsconfig.json"
  if (cd web && "$NODE" "$PNPM" exec tsc -p scripts/tsconfig.json 2>&1 | head -30 | grep -q .); then
    bad "scripts typecheck reported errors"
  else
    ok "scripts typecheck"
  fi

  if [ "$QUICK" = "1" ]; then
    printf '\n   (--quick: skipping audit-css / verify / build-smoke / smoke)\n'
  else
    for s in audit-css verify build-smoke smoke; do
      step "TS  node scripts/$s.mjs"
      if (cd web && "$NODE" "scripts/$s.mjs" >/dev/null 2>&1); then ok "$s"; else bad "$s"; fi
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
