# Strategy Designer — Signal Flow Graph (Design Plan)

**Status:** draft for review — nothing implemented from this doc yet.

## 1. The one-line idea

A **strategy is a signal-flow graph**: a network of small, self-describing
**nodes** that each take typed **signals** in and emit typed **signals** out,
wired together by **links**. The messaging bus is the *source* of raw signals;
everything else is a transform or a sink.

This is the same shape as Node-RED, ComfyUI, LabVIEW, and the Keras functional
API. We borrow the parts that fit crypto signals, not the whole runtime.

---

## 2. The Signal (what flows on a wire)

Every signal is a small typed envelope:

```json
{
  "type":   "DIRECTION",          // what kind of data (see types below)
  "symbol": "BTCUSDT",            // which asset (or null for cross-asset)
  "value":   1,                   // the numeric payload
  "ts":      1729814400000,       // when it was produced
  "basis":   "taker imbalance 0.71 ↑",   // human-readable "why"
  "meta":    {}                   // optional extra (window, model, params…)
}
```

### Signal types (the "wire gauge")

| Type | Payload | Example |
|---|---|---|
| `DIRECTION` | -1 / 0 / +1 | flow-imbalance says **bull** |
| `SCORE` | 0–100 | imbalance strength **82** |
| `PRICE` | float | last trade |
| `VOLUME` | float | rolling volume |
| `FLOW` | struct | taker buy/sell split |
| `TA` | float | RSI(14) = 71 |
| `PROBABILITY` | 0–1 | calibrated P(bull) |
| `TEXT` | string | a note / reason |
| `VERDICT` | BULL / BEAR | the strategy output |

A link is only valid if the source port's type matches (or is a subtype of)
the target port's expected type — typed wires, like LabVIEW / ComfyUI.

---

## 3. Building blocks (node categories)

A node has **declared ports**: inputs it accepts and outputs it produces,
each with a type + a name + a description.

### 3.1 SOURCE — read from the bus (no inputs)
The entry points. They subscribe to the messaging bus and publish a signal.

| Node | Emits |
|---|---|
| `source.market-price` | `PRICE` |
| `source.market-volume` | `VOLUME` |
| `source.ta.rsi` | `TA` (RSI) |
| `source.ta.ema` | `TA` (EMA) |
| `source.flow-imbalance` | `DIRECTION` + `SCORE` |
| `source.volume-climax` | `DIRECTION` + `SCORE` |
| `source.bulls-bears` | `DIRECTION` + `SCORE` |

### 3.2 TRANSFORM / CALC — the building block you described
Takes inputs, computes, **emits** a new signal.

| Node | Inputs | Output | Meaning |
|---|---|---|---|
| `calc.equation` | any | any | a user equation over the inputs |
| `calc.threshold` | `SCORE` | `DIRECTION` | score ≥ t → +1 |
| `calc.combine` | N × `DIRECTION`/`SCORE` | `DIRECTION` + `SCORE` | weighted vote |
| `calc.window` | any | any | aggregate over a rolling window |
| `calc.filter` | any | any | pass/block by a predicate |
| `calc.delay` | any | any | emit after N seconds (for continuation) |

### 3.3 MODEL — a detector (learned or heuristic)
A model is a special transform: it consumes market features and emits a
`DIRECTION` + `SCORE` + `PROBABILITY` + `basis`. It is just a node whose
*internals* are the trained model — same ports, same wires.

### 3.4 SINK — the output (no outputs)
Terminal nodes that consume a signal and act.

| Node | Consumes | Does |
|---|---|---|
| `sink.verdict` | `DIRECTION` | emits a BULL/BEAR verdict (with confirm count) |
| `sink.alert` | `DIRECTION` | pushes an alert to the platform |
| `sink.note` | `TEXT` | writes a note/log entry |

---

## 4. The Link (the mapping between nodes)

A link is **not just an edge** — it is the *mapping* from an output to an input:

```json
{
  "id": "link_1",
  "from": "source.flow-imbalance",  "from_port": "score",
  "to":   "calc.combine",           "to_port":   "input_1",
  "expr": "0.6 * score",            // the transform (default: identity)
  "weight": 0.6
}
```

The `expr` is an **equation** over the upstream signal's fields (`score`,
`direction`, `value`, `weight`, constants). This is the equation builder: each
link carries a small formula that maps source output → target input.

- A plain pass-through link = `expr: "value"`.
- A weighted link = `expr: "0.6 * score"`.
- A gating link = `expr: "direction > 0 ? score : 0"`.

---

## 5. The messaging bus (the available signals)

The bus is a **signal registry + pub/sub hub** (this is the existing
`events.Hub` + SSE `/api/events` extended with typed topics).

Sources that already exist or are planned:

| Domain | Signals |
|---|---|
| **Market data** | `@aggTrade`, `@ticker`, klines, price, volume |
| **TA Analysis** | RSI, EMA, MACD, Bollinger, ATR (from the TA engine) |
| **Model outputs** | the 6 signal models, `flow-onset`, `bulls-and-bears`, micro-burst onset/continuation |
| **Regime** | rising / falling / sideways (the regime cache) |

A **source node** = "subscribe to topic X, reshape into a `Signal`". This is
how market data and TA enter the graph without special-casing.

---

## 6. Execution model

- **Topological / pull (lazy).** A sink asks for its inputs; each input asks
  for *its* inputs; only the needed path runs. (ComfyUI does exactly this.)
- **Cycles are rejected** at design time (a link that creates a loop is red).
- **Deterministic per tick:** on each bus event for a symbol, the graph
  evaluates once and emits downstream signals with the same `ts`.
- **Provenance carried through:** every output signal inherits the `basis`
  chain, so a verdict can be traced back to the raw market event.

---

## 7. The designer UI (what we already started)

- **Canvas** — nodes + typed ports + links (the flow chart).
- **Left palette** — the four categories (Source / Transform / Model / Sink),
  each item showing its **inputs → outputs** signature.
- **Right properties** — the selected node's settings (params), **or** the
  selected link's **equation builder** (weight + `expr` + live preview).
- **Live mode** — toggle to show real signals flowing (nodes light up with
  their current value + basis).
- **Validation** — red highlight on type-mismatched or cyclic links; a "why"
  tooltip explains what's wrong.

---

## 8. Worked example — a "bull continuation" strategy

```
[bus: @aggTrade] ──PRICE──▶ [calc.window (5s)] ──PRICE──▶ [calc.equation: ret] ─┐
[bus: taker flow] ──FLOW──▶ [source.flow-imbalance] ──SCORE──▶ [calc.combine] ──▶ [calc.threshold] ──DIRECTION──▶ [sink.verdict]
                                                                      ▲
[source.volume-climax] ──SCORE───▶ (weight 0.6) ────────────────────┘
```

Read it left to right: market data + taker flow become features; two models
emit scores; a combine node merges them (one weighted 0.6); a threshold turns
the score into a direction; the verdict sink fires on confirm.

Every node and link above is a reusable building block, and the whole graph is
the strategy — editable, saveable, and traceable.

---

## 9. Build phases (incremental, each shippable)

1. **Signal + ports** — define the `Signal` envelope + typed ports; make the
   existing models *declare* their inputs/outputs. (Backward-compatible.)
2. **Node registry** — a catalog of nodes (source/transform/model/sink) with
   their port signatures + metadata (the current `MODEL_META`, generalized).
3. **Link with equation** — extend the current edge to carry `expr` + `weight`
   (the link builder, already partly built).
4. **Transform nodes** — implement `calc.equation`, `calc.threshold`,
   `calc.combine` as first-class nodes.
5. **The bus as source** — source nodes subscribe to market/TA/model topics.
6. **Lazy evaluator** — a small pull-based runtime (topological + cycle check)
   that executes the graph on each tick.
7. **Provenance + live mode** — carry `basis` through + light up live values.
8. **Sinks** — `sink.verdict` replaces the current strategy engine's confirm
   logic as a node.

---

## 10. What this *does not* do (kept out on purpose)

- No arbitrary code execution — equations are a small, sandboxed expression
  language (whitelisted functions only).
- No looping (cycles rejected; continuation is a `calc.delay` node, not a loop).
- No promotion — a graph is a proposal; the human promotes. Rule A6.

---

## Open questions for review

1. **Equation language scope** — arithmetic + `min/max/if/clamp` only, or a
   fuller expression set? (I'd start minimal.)
2. **Time semantics** — is a node stateless (pure function of current inputs)
   or stateful (windows/delays need history)? This decides the runtime's shape.
3. **Symbol scoping** — do signals carry a single `symbol`, or can a node emit
   a *cross-section* (many symbols at once)? (The bus is cross-symbol; the
   graph may need a `by-symbol` fan-out.)
4. **Where the evaluator lives** — in the Go backend (fast, typed) vs Python
   (fast to iterate)? My lean: Go, since the bus and models are already there.
