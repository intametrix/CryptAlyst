# PLAN flow-split — split the flow leaderboard into biggest pumps and dumps

**Human instruction:** "Split flow leaderboard to biggest pump and dumps"

**Status:** PROPOSED — awaiting `AGREE` / `DISAGREE` / `AMEND` from kimi and the
frontend agent. No further work starts until both have answered.

## The split of work

### P1 — backend (deepseek), half-written, deliberately paused
`GET /api/flow?split=1&limit=N` returns:

```json
{ "type": "flow", "split": true, "window_sec": 10, "symbols": 285,
  "pumps": [ ...flow rows... ], "dumps": [ ...flow rows... ],
  "pump_count": 10, "dump_count": 10 }
```

Without `split`, the response is unchanged, so existing callers are unaffected.

**The design decision that matters:** when splitting, the engine is snapshotted
over the **whole tracked universe**, not the top N by flow. Taking the flow
leaders first and *then* splitting them would systematically hide the biggest
dumps whenever the market is broadly rising — the exact moment a dump list is
most valuable. This is the one non-obvious part of the change.

Sort key: `|change_10s|` descending, falling back to `change_60s` when the 10s
window is not populated.

### P2 — frontend (subagent)
Replace the single flow leaderboard with **two panels**: "Biggest Pumps" and
"Biggest Dumps". Requirements: independently resizable with the existing
reactive-child guarantees; rows clickable through to the drill-down; tooltips
preserved; direction-appropriate colour; and an **honest empty state** — a
"Biggest Dumps" panel with nothing in it must say so rather than looking broken.

### P3 — deepseek
Journal the decision, commit, verify end to end against the running server.

## Acceptance test

```
curl -s 'http://127.0.0.1:8787/api/flow?split=1&limit=5'
```
must return both lists with correct signs (`pumps` all positive, `dumps` all
negative), accurate counts, and `symbols` equal to the tracked universe size.
The UI must show two panels, and clicking a row must open that symbol's
drill-down.

## Open questions — I want answers, not silence

1. **kimi:** is `|move|` the right sort key for "biggest"? The alternatives are
   dollar flow (`flow_quote`) or abnormality (`burst_multiple`). My reasoning
   for `|move|`: the human said *biggest pumps and dumps*, and burst multiple
   alone can be enormous on dust where no money actually moved.
2. **frontend agent:** does the panel architecture take two more panels without
   breaking the resize/reactivity guarantees that were just fixed? If not, say
   so now rather than discovering it mid-change.
3. **kimi:** should the split be a separate SSE message, or is a polled endpoint
   enough? The existing `flow` SSE message is top-25 by flow, which is *not* the
   same set as the top pumps or top dumps.

## Honest status

P1 is **partially applied** and was interrupted. It is correct as far as it goes
and the tree is buildable again, but it is not verified and I am treating it as
unreviewed until the plan is agreed.
