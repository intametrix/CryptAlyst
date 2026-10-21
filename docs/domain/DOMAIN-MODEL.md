# CryptAlyst — a domain model

*from the first socket message to the byte on disk*

Seven bounded contexts, one contract between them, and a record that can be read without the platform running.

**Platform build 1.0.0 · 823eb7a · 2026-10-21**  ·  domain model · revision 1.0  ·  © 2026 CryptAlyst · designed by Andrew Preston · built by the CryptAlyst engineering team

## Contents

- **—** [How to read this](#how-to-read) — How the document narrows its lens, from the whole platform to a single byte on disk.
- **1** [The context map](#contexts) — The seven contexts, the aggregates each owns, and the direction knowledge travels between them.
- **2** [The envelope — the only thing that travels](#envelope) — The one object that crosses every boundary, and the hops that record what happened to it.
- **3.1** [Market data — the only process that talks to the exchange](#market) — How an exchange socket becomes topics: shards, a single parse, in-memory state, and a publisher that cannot block the read.
- **3.2** [The book — the aggregate that decides](#books) — The aggregate that decides: equity, positions, the decisions that opened them and the refusals that did not.
- **3.3** [The gate — what must pass before money moves](#risk) — The order of the gate — identity and freshness before arithmetic — and the exit policy that books losses.
- **3.4** [The recorder — the only writer](#recorder) — The only writer of the record, and the derived references it attaches when a producer did not declare its inputs.
- **3.5** [Control — what may change behaviour](#control) — What may change behaviour — supervise and stop — and what STOP must never touch.
- **3.6** [The engines — what trades, and in what families](#engines) — Thirty-eight supervised engines, grouped by the edge they claim to have, and the five things every one of them does identically.
- **3.7** [What an engine depends on](#deps) — The seven dependencies every engine shares — and the three things an engine is not allowed to do.
- **3.8** [The life of a model](#models) — How a model is proposed, trained on the record, admitted, measured, and disabled — and why it cannot come back without a human.
- **4.1** [The lake — what is actually on disk](#lake) — What is actually on disk: the journal, its line format, the index that avoids scanning it, and retention.
- **4.2** [The data stores, as they exist](#stores) — Every place data is actually written on this machine, in which format, by which process, for how long.
- **4.3** [How the lake chooses](#tree) — The decision tree that decides which store receives an envelope, how long it lives, and when a segment is sealed.
- **5** [Beneath the lake](#beneath) — The filesystem properties that let a case file be called evidence a year later.
- **—** [Crossing concerns — time, identity and generations](#crossing) — Time, identity and generations, modelled once in the envelope rather than per context.
- **—** [What the model deliberately refuses to do](#failures) — The boundaries the model will not pretend to cross, stated so they can be argued with.

### Figures

| Fig | Figure | What it shows |
|---|---|---|
| 1 | The context map | seven contexts, their aggregates, the flow between them |
| 2 | The envelope | data, meta, refs, and the hops appended along the way |
| 3.1 | Market data | shards, parse, state, topics — and a publisher off the read path |
| 3.2 | The book aggregate | equity, positions, trades, decisions, refusals |
| 3.3 | The gate | interceptors before arithmetic, and the exit policy |
| 3.4 | The recorder | one writer, hops recorded, derived references attached |
| 3.5 | Control | supervise, stop, and what STOP must never touch |
| 3.6 | The engines | Thirty-eight supervised engines, grouped by the edge they claim to have, and the five th |
| 3.7 | What an engine depends on | The seven dependencies every engine shares — and the three things an engine is not allow |
| 3.8 | The life of a model | How a model is proposed, trained on the record, admitted, measured, and disabled — and w |
| 4.1 | The lake | journal, line format, index, retention, replay |
| 4.2 | Data stores | every store on disk: format, writer, reader, lifetime |
| 4.3 | How the lake chooses | the rules that decide where data lands and how long it lives |

---

## How to read this

> **What this shows** — How the document narrows its lens, from the whole platform to a single byte on disk.

A domain model is an argument about what belongs together, not an inventory of files. Each level here narrows the lens: the contexts, then one context at a time, then the record, and finally the bytes underneath the record.

Two rules run through every level. First, exactly one kind of thing travels between contexts: an envelope. Second, only the control plane may change what the platform does; data may inform a decision but never stop the machinery.

| Level | Question it answers | Where |
|---|---|---|
| 1 | what is this platform about? | the context map |
| 2 | what travels between the parts? | the envelope |
| 3 | what is inside one part? | one figure per context |
| 4 | what is the record, and where does it live? | the lake and the data stores |
| 5 | what is beneath the record? | the filesystem |

---

## 1  The context map

> **What this shows** — The seven contexts, the aggregates each owns, and the direction knowledge travels between them.

![The platform in one picture: contexts, the aggregates they own, and which way knowledge flows.](fig/01-contexts.png)

*The platform in one picture: contexts, the aggregates they own, and which way knowledge flows.*

Seven contexts, arranged by the direction knowledge travels. Market data observes. Intelligence interprets. Books decide. Risk refuses. Recording writes. Forensics reads. Control may stop.

The arrows are not calls. Each one is a stream of envelopes on one bus, so a context can be stopped, restarted or replaced without the others knowing.

| Context | Owns | May |
|---|---|---|
| Market data | the exchange connection, the price and depth state | publish market.* |
| Intelligence | signals, flow, TA, forecasts | publish intel.* |
| Books | equity, positions, trades, decisions, refusals | publish model.* |
| Risk & execution | the gate, the sizing rule, the exit policy | allow or refuse |
| Recording | the envelope, the journal, derived references | append to the lake |
| Forensics | case files, reports, search, journey | read only |
| Control | trading state, the stop flag, the registries | supervise, stop |

---

## 2  The envelope — the only thing that travels

> **What this shows** — The one object that crosses every boundary, and the hops that record what happened to it.

![Three members, and the hops that prove what happened to the envelope on the way.](fig/02-envelope.png)

*Three members, and the hops that prove what happened to the envelope on the way.*

An envelope is data, meta and refs. The data is the payload a producer computed and is never rewritten. The meta is provenance: version, plane, topic, key, producer, generation, sequence, correlation id, computed_at, received_at, compute time, and a free-form extra map. The refs are the inputs, declared or derived, each carrying its age.

The contract is deliberately strict in one direction and lenient in the other. An unknown top-level member is refused, because a reader must never silently ignore something it does not understand. An unknown meta key is ignored, because provenance may grow without breaking every reader. One previous version is accepted, so a writer can be rolled forward before every reader is.

- interceptors run first and append a hop each — authority, keying, freshness, correlation, redaction
- the operator produces the payload and appends a hop
- transformers may change the shape, never the truth, and append a hop
- a refusal is a hop with outcome refuse and the rule in its detail

---

## 3.1  Market data — the only process that talks to the exchange

> **What this shows** — How an exchange socket becomes topics: shards, a single parse, in-memory state, and a publisher that cannot block the read.

![Shards, one parse, in-memory state, then topics. Publishing never blocks the socket.](fig/03-market-data.png)

*Shards, one parse, in-memory state, then topics. Publishing never blocks the socket.*

The feed streams the top symbols by 24-hour quote volume, four streams each, sharded so no websocket carries more than about a thousand streams. Each shard reconnects with backoff. The parse is single-pass: bookTicker becomes top of book, depth becomes the depth book, klines become series with a closed flag, miniTicker becomes 24-hour stats.

The one design decision worth naming: publishing happens on a goroutine through a bounded queue. Publishing inline on the read path means a slow broker write stops the reader, the exchange's ping times out, and the connection dies. Under pressure the feed drops depth samples and counts them, because a missed sample is better than a dead stream.

| State | Fed by | Served by |
|---|---|---|
| Prices | bookTicker | GET /streamprices |
| Books | depth5 | GET /depth?symbol= |
| Klines | kline_1m / kline_5m | GET /klines?symbol=&interval= |
| Mini stats | miniTicker | GET /movers |

---

## 3.2  The book — the aggregate that decides

> **What this shows** — The aggregate that decides: equity, positions, the decisions that opened them and the refusals that did not.

![A book is one aggregate: equity, positions, and the decisions that produced them.](fig/04-book.png)

*A book is one aggregate: equity, positions, and the decisions that produced them.*

A book is the unit of consistency. Its equity moves only when a position closes, by that trade's net, and never by an opinion about an open position. Positions live inside the book; a decision that would open one either becomes an allowed decision or a refusal, and both are recorded.

The refusals are the part most systems omit. A refusal carries the rule that stopped it and the number that tripped it, so the question 'why did it not trade' is answered by the same object as 'why did it trade'.

- a position is never opened without an allowed decision
- a trade is only born from a position that existed
- equity moves only by a closed trade's net
- a refusal is recorded even though nothing was bought

---

## 3.3  The gate — what must pass before money moves

> **What this shows** — The order of the gate — identity and freshness before arithmetic — and the exit policy that books losses.

![Interceptors, then the arithmetic. Every stage appends a hop, so a refusal explains itself.](fig/05-risk.png)

*Interceptors, then the arithmetic. Every stage appends a hop, so a refusal explains itself.*

The gate is ordered deliberately: identity and freshness before arithmetic. There is no point in sizing a trade on a signal that is four hours old, and no point in asking whether a stale signal is affordable.

The exit policy is part of the model, not an implementation detail. The platform once refused every losing exit, which made the fills table contain only winners while equity fell on the losers still open. The policy now holds a loss only while it is shallow and young, and releases it at a stop or a time cap. A loss is booked like any other outcome.

| Check | Refuses when |
|---|---|
| size | notional is zero |
| forecast age | older than the limit |
| signal age | older than the limit |
| kill switch | the stop flag exists |
| gross multiple | exposure would exceed the cap |
| symbol share | one position would exceed the book |

---

## 3.4  The recorder — the only writer

> **What this shows** — The only writer of the record, and the derived references it attaches when a producer did not declare its inputs.

![One consumer per topic, hops recorded as they pass, derived references attached at the door.](fig/06-recorder.png)

*One consumer per topic, hops recorded as they pass, derived references attached at the door.*

The recorder owns one queue per topic, so it sees every message exactly once. It runs the interceptors one at a time and records each as a hop on the envelope itself — which is why the pipeline an envelope went through is data, not a log line.

Books do not declare their inputs. Rather than leave a decision parentless, the recorder attaches derived references: the newest input per topic within five minutes, marked derived, carrying the age it had at decision time. It is the weakest form of lineage and it is always labelled as such.

- a refused envelope is recorded too, with extra.refused = true
- the record therefore contains what did not happen
- derived references are labelled, never implied
- the recorder declares its own topology; it does not rely on another process to do it

---

## 3.5  Control — what may change behaviour

> **What this shows** — What may change behaviour — supervise and stop — and what STOP must never touch.

![The supervisor, the stop flag and the heartbeat, and what STOP must never touch.](fig/08-control.png)

*The supervisor, the stop flag and the heartbeat, and what STOP must never touch.*

STOP is the most important verb in the platform, so it is defined precisely. It kills every trading process, writes a flag that every gate reads, publishes a control envelope on the bus, and resets books and statistics after archiving them. It must never stop market data, the recorder or the explorer: an operator needs the record most at the moment they stop trading.

The inverse is equally deliberate: no data path can stop the platform. A feed outage, a dead book or a missing consumer degrades what you see; only the control plane changes what the platform will do next.

- STOP kills trading, writes the flag, publishes system.control, resets the books
- STOP leaves market data, the recorder and the explorer running
- starting the platform does not start trading unless it is asked to
- a stopped book is visibly stopped; it does not linger as a row on a dashboard

---

## 3.6  The engines — what trades, and in what families

> **What this shows** — Thirty-eight supervised engines, grouped by the edge they claim to have, and the five things every one of them does identically.

![Six families and one shape: the difference between engines is the signal they trust, not the way they are built.](fig/11-engines.png)

*Six families and one shape: the difference between engines is the signal they trust, not the way they are built.*

The platform runs thirty-eight engine processes. They are grouped here by the edge they claim: cross-sectional ranking, microstructure, momentum, mean reversion, intelligence-led, and the ten hybrids that combine two families on purpose.

The grouping is a reading aid, not a code boundary. Every engine reads the same state, asks the same gate, publishes the same two topics and writes the same kind of file. That is what makes adding a thirty-ninth safe.

| Family | Engines | The edge it claims |
|---|---|---|
| Cross-section | alpha_rank, movers, sympathy_lag, multi_models | rank every symbol, trade the extremes |
| Microstructure | grid, hft, scalp_fast, skim_pump, flash, fast_ignition | the book moves before the price does |
| Momentum | crypto_pack (12 strategies), swing_v2, curve_rider, churn | the move continues |
| Mean reversion | vwap_revert, vwap_scalp, rsi2, boll_squeeze | the move overshoots |
| Intelligence-led | alpha_signals, crypto_edges, slow_edges, skim_ai | flow and derivatives lead price |
| Hybrids | ten strategies, two families each | one signal confirming another |

---

## 3.7  What an engine depends on

> **What this shows** — The seven dependencies every engine shares — and the three things an engine is not allowed to do.

![One engine, seven dependencies. The same shape for all thirty-eight, which is why none of them can quietly become special.](fig/12-dependencies.png)

*One engine, seven dependencies. The same shape for all thirty-eight, which is why none of them can quietly become special.*

An engine reads market and intelligence state, asks the risk gate, publishes its decision and its fill, writes its own pack file, and is restarted by the supervisor if it dies. Those seven dependencies are the same for every engine, including the ones added last week.

The refusals matter more than the dependencies. An engine may not open a socket to the exchange, may not write the lake, and may not choose its own size. Each refusal removes a way for a single engine to damage the record or the book.

- may not talk to the exchange — the feed is the only process that does
- may not write the lake — the recorder is the only writer
- may not size itself — risk.allow and size_for decide
- may not trade while the stop flag exists
- must publish its decision even when it is refused

---

## 3.8  The life of a model

> **What this shows** — How a model is proposed, trained on the record, admitted, measured, and disabled — and why it cannot come back without a human.

![Eight stages, one loop back. The dashed return path is the one an automated system is not allowed to take.](fig/13-model-life.png)

*Eight stages, one loop back. The dashed return path is the one an automated system is not allowed to take.*

A model is proposed, trained on data built from the record rather than from memory, measured with a metric that would catch a coin-flip, admitted to the registry, traded as one book, and evaluated on its own fills.

The last two stages are the ones most systems leave out. Disabling requires the exact inputs in play, the rule that fired and the number that tripped it, written down at the moment of disabling. Re-enabling is a human act: the dashed line back to admission is drawn because it exists, and dashed because no cron job may take it.

| Stage | Artefact | Rule |
|---|---|---|
| propose | model_gen.py | an idea, not a claim |
| record | datasets/features.csv | built from the lake, never from memory |
| train | datasets/model/model.json | time split, forward-return label |
| measure | metrics.json | Brier and AUC, not accuracy alone |
| admit | supervisor registry | human sign-off |
| trade | state/pack_<engine>.json | one book, one process |
| evaluate | fill records | judged on what it actually did |
| disable | sentinel + post-mortem | written at the moment of disabling |

---

## 4.1  The lake — what is actually on disk

> **What this shows** — What is actually on disk: the journal, its line format, the index that avoids scanning it, and retention.

![A journal, a line format, an index, and the retention that keeps it from growing forever.](fig/07-lake.png)

*A journal, a line format, an index, and the retention that keeps it from growing forever.*

The lake is a directory of JSONL journals. One line is one envelope. The line carries the topic, the key, the timestamp and the envelope itself. There is no database, no queue and no cache between the record and the filesystem, which is what makes it readable by grep and jq a year later.

Reader queries do not scan the journal. An index of topic to byte offsets is built once at startup and extended as the recorder appends; refreshing it is throttled, because a cold query that walks a multi-gigabyte file is indistinguishable from a hang.

| Layer | Contents | Written by |
|---|---|---|
| bronze | the raw journal, every envelope | the recorder |
| silver | features extracted from fills | the historian |
| gold | datasets for training and study | the trainer |
| archive | sealed, compressed, cold | retention |

---

## 4.2  The data stores, as they exist

> **What this shows** — Every place data is actually written on this machine, in which format, by which process, for how long.

![Eight stores. The journal is the only one that is a record; the rest are derived, cached, archived or diagnostic.](fig/09-stores.png)

*Eight stores. The journal is the only one that is a record; the rest are derived, cached, archived or diagnostic.*

It is worth being literal about storage, because most architectures hide their stores behind services. Here there are eight, and every one of them is a file you can open.

The journal is the record: 60 GB of JSONL, one envelope per line, written by the recorder alone. The datasets are derived from it by the historian. Live state is what the books currently hold, rewritten in place and explicitly not a record. Ledgers are the per-engine trade files, appended by the engines themselves. Archives are copies taken before a reset. The index is memory, rebuilt at boot. Logs are diagnostic. Older journals are kept for replay comparison.

| Store | Format | Written by | Lifetime |
|---|---|---|---|
| state/lake3/envelopes.jsonl | JSONL | recorder | the record; rotated and expired by policy |
| datasets/ | CSV + JSON | historian | rebuilt on demand |
| state/pack_*.json | JSON | each book | until the next write |
| ledger/*.jsonl | JSONL | each engine | append only, never expired |
| state/archive_* | JSON | control on reset | kept for recovery |
| state/lake, lake2, lake4 | JSONL | older recorders | cold, for comparison |
| index (memory) | offsets | the explorer | rebuilt on boot |
| logs/ | text | every process | diagnostic |

---

## 4.3  How the lake chooses

> **What this shows** — The decision tree that decides which store receives an envelope, how long it lives, and when a segment is sealed.

![One question per level: what is it, how long does it live, is it big enough to seal. Nothing is deleted by a rule that is not drawn here.](fig/10-decision-tree.png)

*One question per level: what is it, how long does it live, is it big enough to seal. Nothing is deleted by a rule that is not drawn here.*

Retention is not a setting in a configuration file; it is a short tree. An envelope arrives, and the first question is whether it is a trading outcome. Outcomes go to the silver record — the ledger and the fills file — which is never rotated and never expired, because a trade record that disappears is not a record.

Everything else goes to the bronze journal. There the topic class decides the lifetime: market and intelligence traffic is the bulk of the volume and lives long enough to replay; control and health messages are small, high-value and short-lived. Finally the segment is measured: past a gigabyte it is sealed and compressed, and the index keeps pointing into it.

- a trading outcome is never expired; it is the part of the record that must survive
- the journal is the default, and its lifetime is decided by topic class, not by age alone
- sealing is about the size of the hot file, not about the calendar
- sealing never invalidates the index: offsets stay valid because the line does not change

---

## 5  Beneath the lake

> **What this shows** — The filesystem properties that let a case file be called evidence a year later.

Underneath the lake there is a filesystem and nothing else. The properties that follow from that are worth stating plainly, because they are the reason a case file can be called evidence.

| Property | Consequence |
|---|---|
| one line, one envelope, append only | a line never changes after it is written |
| plain UTF-8 JSON | grep, jq, awk and a shell are enough to read it |
| no service dependency to read | an investigator does not need the platform running |
| rotation by size, then gzip | the working journal stays small and fast |
| expiry by window | the record has a stated lifetime, not an accidental one |
| offsets in memory, rebuilt on boot | the index is disposable, the journal is not |

The separation matters architecturally: the journal is the durable fact, the index is a convenience that can be thrown away and rebuilt. A corruption in the index costs a restart; a corruption in the journal costs history. Only the second one is serious, and the design keeps them apart.

---

## Crossing concerns — time, identity and generations

> **What this shows** — Time, identity and generations, modelled once in the envelope rather than per context.

Three things are shared by every context and are therefore modelled once, in the envelope.

| Concern | Field | Rule |
|---|---|---|
| when it was computed | computed_at | staleness is measured from here, never from arrival |
| who is asking | cid | one identity follows work across contexts |
| what changed | generation | a gap is a missing message, not an accident |
| what it read | refs | declared or derived, always with age |
| what happened to it | hops | every stage, with outcome and duration |

A correlation id is minted locally at the boundary, so work can start without a central registry. Mapping it centrally and resolving it are separate, gated operations — a reader that cannot resolve still knows which id it was handed.

---

## What the model deliberately refuses to do

> **What this shows** — The boundaries the model will not pretend to cross, stated so they can be argued with.

A model is also a list of things it will not pretend to know. These are the boundaries, stated so they can be argued with.

- it does not reconstruct what a producer read if the producer did not say so — it derives and labels
- it does not treat a dashboard as the record; the journal is the record
- it does not let a viewer write, which is why the explorer has no write path
- it does not hide a loss: a losing exit is booked, not deferred forever
- it does not call a stopped book running, or an empty book profitable

---

*Figures are generated by `docs/domain/diagrams.py` and rendered by `docs/domain/render_figs.py`; this document and its PDF are built from `docs/domain/content.py`, so they cannot disagree.*
