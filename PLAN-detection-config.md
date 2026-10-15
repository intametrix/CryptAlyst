# PLAN detection-config — runtime configuration of how alerts are computed

**Human instruction:** *"Split the leaderboard to pumps and dumps. We should allow a
configuration to how these alerts are computed. Moving time window length,
alerting models used and capabilities etc."*

**Status:** PROPOSED — awaiting `AGREE` / `DISAGREE` / `AMEND` from kimi. The
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
