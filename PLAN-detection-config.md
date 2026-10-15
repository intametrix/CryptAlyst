# PLAN detection-config — runtime configuration of how alerts are computed

**Human instruction:** *"Split the leaderboard to pumps and dumps. We should allow a
configuration to how these alerts are computed. Moving time window length,
alerting models used and capabilities etc."*

**Status:** AGREED (4 amendments, see the end of this file). The
leaderboard split is already built and agreed (see PLAN-flow-split); this plan
covers only the configuration surface.

## Why this is not just "expose the flags"

The flags already exist at startup. Exposing them at runtime is a different
problem because **some of them invalidate state and some do not**, and a
configuration UI that does not distinguish those two will silently corrupt the
detector.

## The split: safe vs state-invalidating

| parameter | live change | consequence |
|---|---|---|
| cooldown seconds | **safe** | read at evaluation time |
| burst multiple, sigma multiple, min move %, min trades, min flow USD, coverage, max spread, book depth | **safe** | thresholds compared at evaluation time |
| detectors enabled | **safe** | already implemented, `POST /api/detectors` |
| **flow window seconds** | **INVALIDATES STATE** | the engine's per-symbol state is bucketed by window length; changing it makes every baseline meaningless and every in-flight evaluation wrong |
| baseline floor fraction, sample cap | **invalidates baselines** | the stored reference was computed under the old rule |

So the config surface must mark each parameter, and a change that invalidates
state must be **explicit and visible** rather than silently applied.

## Proposal

**1. A typed parameter registry.** Each parameter publishes:
```json
{ "name": "window_sec", "label": "Flow window", "type": "int", "unit": "seconds",
  "value": 10, "default": 10, "min": 5, "max": 60, "step": 1,
  "group": "window", "requires_reseed": true,
  "effect": "Changing this re-seeds every symbol's baseline and briefly blinds detection." }
```
This is also what KIMI asked for earlier: a typed `ParamSpec` rather than
`map[string]any`.

**2. `GET /api/detection-config`** returns every parameter with its metadata,
grouped (`window`, `gates`, `models`, `scoring`), plus the current enabled
detector set and their own parameters.

**3. `POST /api/detection-config`** with `{ "changes": { "window_sec": 15 } }`:
- validates every value against min/max/type **before applying anything** — all
  or nothing, because a half-applied config is a state nobody designed
- returns what changed, plus a `reseeded: true` flag and a warning when the
  change invalidated state
- broadcasts the new config on SSE so every client agrees

**4. `requires_reseed` is surfaced, not hidden.** The UI must confirm before
applying one, and afterwards show that baselines are rebuilding rather than
presenting an empty leaderboard as "no activity".

## Open questions for agreement

1. **Should the window length be changeable at all?** My instinct is yes but
   guarded: it is the single most useful knob and the most dangerous. The
   alternative is to require a restart, which is honest but makes the setting
   useless for live experimentation. Agreement wanted.
2. **Persistence:** write changes back to a config file so they survive a
   restart, or keep them in-memory and require a flag to make them permanent? My
   instinct is persist, because a setting that silently reverts on restart is a
   trap — but persisting means a bad value survives a restart too.
3. **Per-detector parameters:** should `turtle-breakout` (ATR period, Donchian
   length, risk %) be configurable from the same surface, or is that a separate
   system with its own plan? My instinct is same surface, different group.
4. **Guard rails:** should the server refuse obviously self-defeating
   combinations — e.g. burst multiple below the noise floor at the chosen
   window, or a coverage gate so tight nothing can pass? My instinct is warn,
   never block: the detector's whole purpose is to be measured, and a user may
   deliberately want a noisy configuration to see what it catches.

## Acceptance

`GET` returns every parameter with metadata and no `map[string]any`; `POST`
rejects an out-of-range value with the bound named and applies nothing; a
window change reports `reseeded: true` and the leaderboard repopulates within
one window; an invalid change leaves the running config untouched.

## Honest status

Not started. Per Rule 1 this waits for agreement, and question 1 is the one that
changes the shape of the work.

---

## AGREED — with four amendments from the other agent

Status: **AGREED, build approved.** The amendments are recorded here because two
of them change the design, and one identifies a parameter class this plan missed
entirely.

### Amendment 1 — the missed class: safe for the ENGINE, invalidating for the MEASUREMENT

This plan classified parameters as safe or state-invalidating **for the
detector**. It missed a third class: parameters that are safe for detection and
**silently corrupt the measurement**.

A runtime `burst_multiple` change does not break the engine. It breaks the
**ledger**. After the change, CORRECT and WRONG labels mix two gate regimes, and
the learner's accuracy numbers drift with no signal anywhere in the data that
anything changed.

> *"A tuning system that cannot tell you which regime produced which label is a
> slot machine with a spreadsheet."*

**Required, therefore:**
1. Every `POST` appends to a **config-change log** — a file, with timestamp and
   the full value set, not a diff. A diff cannot reconstruct the regime.
2. Every prediction carries a **config fingerprint** — a hash of the gate values
   at alert time, one field on the existing ledger record.
3. The learner **segments its report by config epoch**, or at minimum prints
   `config changed at T, before/after n=…`.

This is the same failure shape as the lateness finding: a number that silently
averages two different things and reports the average as a fact.

### Amendment 2 — a second invalidating parameter, and two STRUCTURAL refusals

- `TrackCapacitySeconds` (ring-buffer depth), if exposed, is `requires_reseed`:
  shrinking it discards in-flight window history.
- Two refusals belong in the validator, and they are **state-corruption guards,
  not taste guards** — a different category from the "warn never block" answer:
  - `window_sec` must be **less than the usable ring-buffer horizon**.
  - **Refuse a second window change while a reseed is in flight.**

### Amendment 3 — yes, the window is changeable, and the cost is named

Agreed, with the reasoning that a restart-required knob is dead weight for live
experimentation. The `effect` string must state the cost plainly: **during the
~80 s reseed, detection is blind**, and a blind window can coincide with a pump.
Visible, not hidden.

### Amendment 4 — persistence, Turtle params, guard rails

- **Persist.** A silently-reverting setting is a trap, and the config-change log
  covers the "bad value survives a restart" objection: you can see it and revert
  it.
- **Turtle parameters on the same surface, own group**, same `requires_reseed`
  semantics — and changing `EntryPeriod` must **force a survey re-run** rather
  than serving stale setups.
- **Warn, never block** — except the two structural guards above. A deliberately
  noisy configuration is a legitimate experiment, which the config-epoch
  stamping now makes honest rather than confusing.

### Agreed as written
All-or-nothing POST: validate → apply → reseed **in one critical section**, with
the SSE broadcast only **after** commit, so no client ever sees a configuration
that was not applied.
