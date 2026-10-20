# AGENTS.md — standing rules for the agents on this repository

Set by the human. Binds DeepSeek and KIMI both. `COMMS_PROTOCOL.md` governs
how we talk to each other; this file governs what we never do to the code.
The human has control, always, without exception: where a direct human
instruction and this file collide, the instruction wins and this file is
updated to match. Amendments are made by the human, or by an agent at the
human's instruction, announced on the channel.

## Rule A1 — NEVER REMOVE FEATURES

Features are added or fixed. They are never deleted, disabled by default
without sign-off, or "cleaned up" silently.

- Removal or deprecation of anything a user can see or invoke requires the
  human's explicit approval **on the channel before the work starts**, with
  the justification and the replacement path both named.
- If a feature *appears* dead: prove it first — usage search across
  `backend/` and `web/`, the full gate green, and a channel message
  announcing intent. The precedent is the ChartPanel incident: components
  were deleted as "dead code" while `web/scripts/renderSmoke.tsx` still
  imported them, and the smoke suite could not even typecheck. "Dead" is a
  claim that needs evidence, not a vibe.
- Refactors may reorganise, but must preserve behaviour. No behaviour change
  rides inside a cleanup commit.

## Rule A2 — LINT BEFORE DONE

No code is done until it is linted (COMMS_PROTOCOL Rule 0h, restated here for
both lanes):

- **UI (`web/`):** the full `./check.sh` gate passes before any commit —
  `tsc -b` for app and scripts, `audit-css`, `verify`, `build-smoke`, `smoke`.
  A typecheck alone is not a lint: it catches type errors and nothing else.
- **Go (`backend/`):** `gofmt -l .` prints NOTHING, then `go vet`, `go build`,
  `go test` all pass.
- **Shell/Python tools:** `bash -n` / `python3 -m py_compile` on every touched
  file before commit.

## Rule A3 — TEST WITH MOCKS AND GENERATED DATA

Tests drive calculations with deterministic, generated, in-repo data.
Nothing under test may depend on:

- the live network (Binance or otherwise),
- the wall clock (inject time; tick engines by explicit timestamps),
- pre-existing local files or machine-specific paths (build fixtures in
  `t.TempDir()` or in-repo payloads).

Patterns already in the tree, follow them:

- `backend/internal/flow/trades_baseline_test.go` synthesizes ticks so a 10x
  burst must read as ~10x.
- `web/scripts/renderSmoke.tsx` renders the dashboard from in-repo payload
  fixtures, never from a running server.
- `python3 tools/test_flow.py` runs the whole detection flow offline.

Every behaviour fix or new behaviour ships its regression test in the same
commit. A fix without a test is half-work, and half-work is worse than no
work (COMMS_PROTOCOL, Rule 1).

## Rule A4 — LANES AND GUARDIAN, UNCHANGED

DeepSeek writes Go (`backend/**/*.go`, `cmd/`, API, engine, detectors) plus
`JOURNAL.md`, `COMMS_PROTOCOL.md`, and the channel; KIMI writes `web/**` and
owns git — the only agent that commits or pushes (COMMS_PROTOCOL Rule 0e and
0e-b). Cross-lane work happens only on explicit human instruction, disclosed
on the channel, and stated in the commit message.

## Rule A5 — PROVE, DISCLOSE, LEAVE IT BUILDABLE

Every claim carries its command output (rules of engagement, COMMS_PROTOCOL).
Mistakes are disclosed in writing and at volume (Rule 3). The tree is always
left buildable at a handover (engagement rule 7). Journal the decision, not
just the change — in `JOURNAL.md`, because the reasoning is lost first and
costs the most to rediscover.
