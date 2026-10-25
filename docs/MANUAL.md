# CryptAlyst Manual — Books, Strategies, Models & Training

This is the operating manual for the research side of CryptAlyst: how to create
books, build strategies, assign and chain models, pick tickers, and train a
model from your own labels. It describes behaviour, not implementation.

---

## 1. The concepts, in one sentence each

| Term | Meaning |
|---|---|
| **Book** | A named, stored container that holds strategies and notes. |
| **Strategy** | A rule inside a book: which models, in what chain, on which tickers, with how many confirmations. |
| **Model** | A detector/predictor — a signal source. |
| **Chain** | The order models run in, where a later model consumes earlier models' outputs. |
| **Ticker** | A symbol (e.g. `BTCUSDT`) a strategy watches. |
| **Verdict** | The strategy's output — `BULL` or `BEAR`. |
| **Train** | Mark `P`/`D` points on a chart, fit a model, store it. |

---

## 2. Book

A **Book** is the top-level container. It exists to **store** things, not to
compute: it groups strategies and notes so they persist and travel together.

```
Book
 ├── notes (plain text)
 └── strategies (one or more)
       ├── name
       ├── chain[]        ← the models, in evaluation order, with their inputs
       ├── tickers[]      ← auto (engine picks) or a user-defined list
       ├── confirm_signals ← repeated firings before a verdict
       └── notes
```

**Two meanings of "Book" — do not confuse them:**

1. **Research Book** (the "Book shelf") — a strategy container. No money, no
   positions. This is what the manual is about.
2. **Trading Book** (the "Books" tab) — a live trading account with equity and
   PnL (`atr_channel`, `boll_squeeze`, …).

---

## 3. Strategy

A **Strategy** is the rule. It answers one question: *"given these models,
chained this way, watching these tickers, is the move BULL or BEAR?"*

A strategy has:

- **name** — a label.
- **chain** — the models and their dependencies (see §4).
- **tickers** — the symbols to watch, or **auto** (the engine picks a liquid
  universe).
- **confirm_signals** — how many repeated firings are required before a
  verdict (repeated signalling, so one blip does not vote).
- **notes** — free text.

When the confirm count is reached for a symbol, the strategy emits a **verdict**
(`BULL` / `BEAR`) with the chain it used and the final model.

---

## 4. Models and chaining

A **Model** is a signal source. The models available include:

- `flow-imbalance`, `volume-climax`, `volatility-expansion`, `structure-break`,
  `mean-reversion`, `momentum-divergence` — the six async signal models.
- `flow-onset`, `pump-screen`, `turtle-breakout` — the detectors.
- `bulls-and-bears` — the directional bull/bear view.

### True chaining (model B consumes model A's output)

A strategy's `chain` is an ordered list of steps:

```json
"chain": [
  { "model": "flow-imbalance",     "inputs": [] },
  { "model": "volume-climax",      "inputs": [] },
  { "model": "bulls-and-bears",    "inputs": ["flow-imbalance", "volume-climax"] }
]
```

- The first two are **source** models — they read only the market.
- `bulls-and-bears` is a **chained** model — it also receives the outputs of
  `flow-imbalance` and `volume-climax` (their direction and score) as its
  inputs.

Evaluation is **topological**: a model runs only after every model it names in
`inputs` has produced an output. The **final model** (last in the chain) is the
strategy's verdict direction; a chained model combines its upstream models'
directions (they vote) and scores (they weight).

So the chain is a small directed acyclic graph — sources first, combiners last —
not a flat vote.

---

## 5. Tickers

A strategy watches symbols:

- **Auto** — the engine picks a liquid universe (no list stored).
- **User-defined** — an explicit list (e.g. `BTCUSDT, ETHUSDT, SOLUSDT`).

---

## 6. Training a model

The training loop is manual-label supervised:

1. Open the chart (`/chart`) and click **TRAIN** (top-left toolbar).
2. The current chart ticker is used; choose **which model** to train in the bar
   (or click a model's **train** button in the right **models** panel, or in the
   dashboard's **Models** tab — that path opens the ticker picker).
3. Click the chart to **mark points** — green `P` (pump) or red `D` (dump).
4. Toggle `label: P` / `label: D` before each mark.
5. Click **OK train** — the marks are saved to `state/training_labels.jsonl`,
   a logistic P-vs-D classifier is fitted on the marked points' return, and the
   model is stored to `state/models/<model>.json`.
6. The fit result (weight, intercept, n, P/D counts) is shown and persists
   across sessions.

---

## 7. Where it all lives

| Thing | Where |
|---|---|
| Books + strategies + notes | `state/books.json` (via the "Book shelf" / My Books UI) |
| Verdicts | `state/verdicts.json` (live, via the strategy engine) |
| Training labels | `state/training_labels.jsonl` |
| Trained models | `state/models/<model>.json` |

The **strategy engine** runs in the dashboard process every 10s: for each book →
strategy → symbol it evaluates the chain and, on confirm, emits a verdict.

---

## 8. Quick start

1. **Create a book** — "My Books" → **+ Create Book**.
2. **Add a strategy** — the book → **+ strategy** → name, model checkboxes,
   tickers (auto or typed), confirm count.
3. **(Optional) chain** — set the model order/dependencies in the chain.
4. **Train a model** — a model's **train** button → mark P/D → **OK train**.
5. **Watch** — the strategy engine emits BULL/BEAR verdicts in "My Books".
