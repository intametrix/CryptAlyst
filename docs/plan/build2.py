#!/usr/bin/env python3
"""The CryptAlyst Master Plan — the whole platform, with evidence.

Chapters are numbered by position at assembly, so this file can grow without renumbering
anything. Every figure is a rendered diagram; every worked example quotes a payload the
platform produced.
"""
import json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.dirname(HERE)
sys.path.insert(0, DOCS)
import buildkit as kit

SNAP = json.load(open(os.path.join(DOCS, "data", "platform-snapshot.json")))
EX = json.load(open("/tmp/examples.json"))
REPLAY_SUMMARY = (EX.get("replay") or {}).get("summary") or {}
TOPICS = SNAP["topics"]
MODELS = SNAP["models"]
ROUTES = SNAP["routes"]
SETTINGS = SNAP["settings"]

FIGNUM = {}
ORDER = []

def fig(n, cap, key=None):
    num = len(ORDER) + 1
    ORDER.append((num, cap, key))
    if key:
        FIGNUM[key] = num
    return kit.fig(os.path.join(HERE, f"f{n}.png"), f"Figure {num} — {cap}")

def xfig(n, cap, key=None):
    num = len(ORDER) + 1
    ORDER.append((num, cap, key))
    if key:
        FIGNUM[key] = num
    return kit.fig(os.path.join(HERE, f"x{n}.png"), f"Figure {num} — {cap}")

def efig(n, cap, key=None):
    num = len(ORDER) + 1
    ORDER.append((num, cap, key))
    if key:
        FIGNUM[key] = num
    return kit.fig(os.path.join(HERE, f"e{n}.png"), f"Figure {num} — {cap}")

table, co, kpis, defs = kit.table, kit.callout, kit.kpis, kit.defs

B = []
def add(*blocks):
    B.extend(blocks)

MARKERS = []
def ch(title):
    MARKERS.append(title)
    return f"<!--CH|{title}-->"

def pre(text):
    return "<pre>" + text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;") + "</pre>"

# ---------------------------------------------------------------- front matter
add("""
<h1>The CryptAlyst Master Plan</h1>
<p class="lede">Trading, models, signals, the envelope, speed, storage and compliance — the whole platform, what it does today, what the audit found, and the order in which it gets fixed. Every claim carries its evidence.</p>
""")
add(table(["field", "value"], [
  ["Document", "CryptAlyst — Master Plan"],
  ["Version", "1.1 · plan"],
  ["Status", "nothing in the phases is implemented until the operator says go"],
  ["Audience", "the operator, and anyone asked to change the platform"],
  ["Scope", "every runtime: the Go platform, the React dashboard, the Python fleet, the GPU agent"],
  ["Basis", "a line-by-line audit of every model, the backtest and the optimiser; the envelope as implemented; a survey of the published state of the art in pump-and-dump detection; and payloads captured from the running platform"],
  ["Designed by", "Andrew John Preston"],
  ["Copyright", "© TekVolt Systems 2026"],
], ["20%", "80%"]))

add(kpis([("10", "verified defects", "each with a test"),
          ("6", "phases", "each with an exit gate"),
          (str(len(TOPICS)), "topics", "registered, with TTLs"),
          (str(len(MODELS)), "models", "in twelve families")]))
add(co("note", "How to read this",
  "The audit comes first, because it decides the order of everything else. The envelope chapter states the contract as the code implements it. The phases follow, each ending in an exit gate. Then the platform itself: models, calibration, backtests, storage, GPU, the dashboard, the flows, compliance and operations. The worked examples quote payloads the platform produced; the reference closes the document."))

# ---------------------------------------------------------------------------
add(ch("What this document is"))
add("""
<p>This is a plan for one platform. It states what the platform computes today, what an audit of it found, and the order in which those findings are fixed. It is written from the code that is running: from <code>backend/internal/envelope/</code> for the contract, from the model catalogue for what is forecastable, and from payloads the platform produced while this document was being written.</p>
<h3>What it is built from</h3>
""")
add(table(["source", "what it establishes"], [
  ["the running platform", "which routes exist, which topics are registered, which producers actually publish, and what every number on the dashboard is computed from"],
  ["the model audit", "a line-by-line reading of every forecast model, the backtest and the optimiser — the ten findings of chapter 2"],
  ["the envelope implementation", "the packet shape, the six pipeline stages, the journal, the bus and the replay path, as implemented rather than proposed"],
  ["the detection literature", "the feature families shown to work, and the precision early warning actually achieves"],
  ["captured payloads", "the worked examples in chapter 23 are real responses, not illustrations"],
], ["26%", "74%"]))
add("""
<h3>What it is not</h3>
<p>It is not a promise that the models start working. The audit found the mathematics mostly correct and the grading partly dishonest — a different problem with a different fix. Nothing here claims an edge. It claims that after the correctness phase the platform will be able to say honestly whether it has one.</p>
""")

# ---------------------------------------------------------------------------
add(ch("The audit: ten verified defects"))
add("""
<p>Before any of this plan was written, every model in the catalogue was read against its documented intent — look-ahead bias, off-by-one errors and division by zero checked line by line. The result is not a list of bugs; it is a map of where trust breaks. Two findings are critical, three high, three medium, two low, and the pattern matters more than the severity: every serious defect sits in a place a user reads and believes.</p>
""")
add(table(["#", "finding", "severity"], [
  ["<b>F1</b>", "The verify handler grades the volume-family models on a forecast computed over unsliced bars: the model sees every bar it is graded against and the path is anchored on the last graded bar. Those errors are meaningless, and the leaderboard ranks them on fiction.", "critical"],
  ["<b>F2</b>", "<code>smaValue</code> mutates its captured window the first time a training window is shorter than <code>n</code>, so the live sweep computes sma80/100/150 as <b>sma60 on every cut</b>.", "critical"],
  ["<b>F3</b>", "MLR re-derives its cut internally instead of taking the walk cut from the loop, so it is graded one horizon too far ahead; every MLR backtest number is wrong.", "high"],
  ["<b>F4</b>", "The GPU sweep's “hybrid” and “vwdrift” are different formulas from the production models of those names: the live engine's R² and MAPE were earned by models nothing executes.", "high"],
  ["<b>F5</b>", "The optimiser anchors its blend at the regression's fitted last value while production anchors at the actual close, and its weights are never persisted — its MAE describes a model nothing can run.", "high"],
  ["<b>F6</b>", "The feature model standardises over the full series, so the mean and standard deviation encode bars after the cut.", "medium"],
  ["<b>F7</b>", "The agent's CPU analogue fallback omits the step-0 anchor and drift-centring the Go and jax paths apply: same model name, different forecast by hardware.", "medium"],
  ["<b>F8</b>", "The hybrid undo loop subtracts a failed component's weight across the whole path, including indices where nothing was added.", "medium"],
  ["<b>F9</b>", "Go blends raw sma/ema levels while the agent blends converged paths — same catalogued name, two formulas.", "medium"],
  ["<b>F10</b>", "Eight small ones: a wrong window start, a dropped anchor, an off-by-one order statistic in split conformal, a panic on empty results, horizons reset rather than clamped, double-counted calibration bins, an unguarded division, and two UI labels describing methods the code does not use.", "low"],
], ["6%", "82%", "12%"]))
add(co("good", "What the audit verified correct",
  "Trend and damped OLS with the damping semantics confirmed, the EMA convention, the analogue search windows (pinned by test), ARIMA with Hannan–Rissanen identification and BIC order selection, log-space forecasting with a naive fallback, the vwap/vwtrend/vwdrift formulas, MLR's walk-forward training rows, super's inverse-squared-error weighting with its keep-only-if-better guard, the backtest's cuts and its MAE/RMSE/MAPE/R²/skill formulas, the optimiser's counters and history, the calibration λ fit, the bootstrap fan, the ACI update rule, and every graded error metric."))
add("""
<h3>The five principles</h3>
<p>Everything else follows from five statements. When two sections disagree, these arbitrate.</p>
""")
add(defs([
  ("Correctness before features", "no capability ships on top of a number that lies."),
  ("Walk-forward purity", "a forecast after the cut uses only bars before it — including training slices, standardisation, model selection, similarity and calibration. Four of the ten defects are this one principle in disguise, so it becomes machinery: a slice type that cannot be built past the cut, and leak tests in the gate."),
  ("Displayed number = runnable model", "every metric on screen equals the metric of a model the platform can execute, on the data it actually consumed."),
  ("The envelope is the contract", "forecasts, detections, decisions and fills travel as packets. New capability is a registry row and a producer, never a change to the shape."),
  ("One spec, three runtimes", "the platform, the agent and the GPU are workers under one registry; a model in more than one runtime is generated from one spec and parity-tested."),
]))

# ---------------------------------------------------------------------------
add(ch("The envelope, as implemented"))
add(efig(1, "the shape and the roles around it: the packet is immutable; the interceptors decide whether it may exist.", "the-shape-and-the-three-role"))

add("""
<p>Everything leans on one structure, so it is described here as the code does it ([[F:the-shape-and-the-three-roles-around-it]]). The packet is fixed; the pipeline in front of it has six stages, in this order: <b>authority → keying → correlation → freshness → operator → transformer</b>. The first four can refuse. The operator is the caller's compute. The transformer is normalisation only.</p>
""")
add(table(["stage", "what the code does", "can it refuse"], [
  ["<b>authority</b>", "looks the topic up in the registry and checks the producer is listed for it", "yes — “unknown topic”, or “producer may not write topic”"],
  ["<b>keying</b>", "a topic that concerns one symbol must carry its key", "yes — the missing key is named"],
  ["<b>correlation</b>", "mints the cid if the caller supplied none, and carries <code>parent_cid</code>", "no"],
  ["<b>freshness</b>", "compares <code>computed_at</code> against the topic's TTL and reports both numbers", "yes — the age and the limit are quoted"],
  ["<b>operator</b>", "runs the caller's function, the only place a payload is computed", "no — it returns a payload or an error"],
  ["<b>transformer</b>", "normalisation only: records stages and hops on the packet, shape unchanged", "no"],
], ["14%", "60%", "26%"]))
add(co("law", "The two properties the stage order buys",
  "A producer that may not write a topic never reaches the compute, because authority runs before the operator. And a payload computed from data older than the topic's TTL is refused with its own age quoted back at it. Both are properties of the order, not of any model."))
add(efig(2, "the pipeline: four stages may refuse, the operator computes, the transformer normalises", "four-refusing-stages-then-th"))
add("""
<h3>What a packet carries</h3>
""")
add(table(["part", "fields, from <code>packet.go</code>", "why"], [
  ["<b>data</b>", "the payload the operator returned", "the number itself"],
  ["<b>meta</b>", "version, plane, topic, key, producer, generation, seq, cid, parent_cid, computed_at, received_at, compute_ms, hops, pipeline", "who computed what, when, from what, under which identity — and which checks ran"],
  ["<b>refs</b>", "topic, key, generation, age_ms", "the inputs, by reference, so a packet cannot claim data it did not have"],
], ["10%", "54%", "36%"]))
add("""
<h3>The registry, and the rows that actually publish</h3>
<p>Twenty-three topics are registered across six planes, each with its permitted producers and a TTL. Registration is not production, and the difference is the most useful fact in this chapter: four topics have publishers today — <code>intel.forecast</code>, <code>model.decision</code>, <code>control.engine</code> and <code>market.candle</code>. The other nineteen are registered and silent.</p>
""")
add(table(["topic", "plane", "producers", "ttl", "recorded"],
  [[f"<code>{t['Name']}</code>", t["Plane"], ", ".join(t["Producer"]), f"{t['TTLMS']} ms",
    "yes" if t["Record"] else "no"] for t in TOPICS], ["30%", "14%", "26%", "16%", "14%"]))
add(co("warn", "What the silence costs",
  "No fills, no PnL, no signals and no risk refusals reach the journal today, so the replay scorecard can only grade forecasts and decisions. The machinery is not the gap; the producers are."))
add("""
<h3>Implemented, and proposed</h3>
""")
add(table(["element", "state"], [
  ["packet shape, six stages, operator, transformer", "<b>implemented</b> — <code>internal/envelope/pipeline.go</code>"],
  ["23-topic registry with producers and TTLs", "<b>implemented</b> — <code>topics.go</code>, served at <code>/api/envelope/topics</code>"],
  ["append-only journal and replay", "<b>implemented</b> — <code>journal.go</code>, <code>data/envelope-journal.jsonl</code>"],
  ["RabbitMQ over STOMP", "<b>implemented</b> — <code>bus.go</code>"],
  ["decision from one recorded forecast, with parent_cid", "<b>implemented</b> — <code>/api/decision</code>"],
  ["replay graded against the price that followed", "<b>implemented</b> — <code>/api/replay</code>"],
  ["orchestrator from capabilities and generations", "<b>implemented</b> — <code>/api/orchestrator/plan</code>"],
  ["schema and budget interceptors", "<b>proposed</b>"],
  ["transformers beyond normalisation", "<b>proposed</b> — the stage exists, those transforms do not"],
  ["the nineteen silent producers", "<b>proposed</b> — Phase 3"],
], ["54%", "46%"]))

# ---------------------------------------------------------------------------
add(ch("Traceability: what the platform can prove"))
add(fig(12, "the packet anatomy: data, meta and refs, with the fields a lineage walk reads.", "the-packet-anatomy"))

add("""
<p>Traceability is not a log file; it is the walk shown in [[F:a-lineage-chain]]. It is the property that any number a user saw can be walked back to the inputs that produced it, and forward to what happened next. The envelope gives that property structurally: a packet names its producer, its generation, its inputs and its pipeline, and a decision names the packet it was made from.</p>
""")
add(table(["question", "where the answer lives", "how it is read"], [
  ["what did we compute, and from what?", "<code>meta</code> plus <code>refs</code> on the packet", "<code>/api/envelope/recent</code>, then <code>/api/envelope/chain?cid=</code>"],
  ["which checks ran, and did they pass?", "<code>meta.pipeline</code> — stage, rule, pass", "on the packet itself"],
  ["why did we act?", "<code>model.decision</code> with <code>parent_cid</code> pointing at the forecast", "the lineage walk, backwards"],
  ["was it right?", "the graded outcome at anchor + horizon", "<code>/api/replay</code>"],
  ["what did it cost us?", "fills and PnL", "<b>not yet recorded</b> — Phase 3"],
], ["26%", "38%", "36%"]))
add(co("note", "Why a cid and not a log correlation id",
  "A log id survives only as long as the log. A cid is minted at the boundary that created the work and travels inside the packet, so it survives rotation, copying, replay and the machine that produced it. The journal is the map from cid to everything."))
add(fig(32, "a lineage chain: a decision points at the forecast it was made from", "a-lineage-chain"))
add("""
<h3>The three questions, answered from the record</h3>
<p><b>What did we believe, and why?</b> Walk the chain backwards from a decision to the forecast and its refs; every stage is on the packet, so the answer is a page of JSON rather than an investigation.</p>
<p><b>Was it true?</b> Replay re-derives the same verdict with the same function and grades it against the price that arrived at anchor + horizon. Because the verdict is recomputed rather than read, a change in the decision rule is visible as a change in the scorecard.</p>
<p><b>What did we do about it?</b> Today this stops at the decision, because fills are not recorded. That is the single largest gap in traceability and it is Phase 3's job.</p>
""")

# ---------------------------------------------------------------------------
add(ch("Phase 1 — make every number true"))
add(fig(20, "the F1 fix: the training slice ends at the cut, so the graded bars are never visible to the model", "the-f1-fix-before-and-after"))

add("""
<p>The first phase fixes the ten defects and builds the machinery that would have caught them. [[F:the-f1-fix]] shows the shape of the fix for the worst of them. No new capability ships until it is done: there is no point adding a detector to a platform whose grades are fiction.</p>
""")
add(table(["fix", "what changes", "how we know it worked"], [
  ["<b>F1</b>", "the verify handler slices the training window; the path anchors at the cut", "a leak test that fails if a graded index appears in the training slice"],
  ["<b>F2</b>", "<code>smaValue</code> stops mutating its captured window", "calling sma80 with a short window leaves sma150 intact"],
  ["<b>F3</b>", "MLR takes the cut from the loop", "an alignment test asserts the graded bar is exactly one horizon after the cut"],
  ["<b>F4</b>", "the sweep imports the production formulas", "parity test per model, in the gate"],
  ["<b>F5</b>", "the optimiser anchors at the actual close and persists weights", "a saved config reproduces its reported MAE on replay"],
  ["<b>F6</b>", "standardisation uses training rows only", "a leak test on the feature z-scores"],
  ["<b>F7</b>", "one spec generates Go, jax and CPU paths", "the parity harness, on every cut"],
  ["<b>F8</b>", "the undo loop subtracts only where it added", "a property test over random failure positions"],
  ["<b>F9</b>", "one blend formula, one spec", "parity again"],
  ["<b>F10</b>", "the eight small ones, each with its own test", "the regression suite"],
], ["8%", "54%", "38%"]))
add(co("good", "Phase 1 exit gate",
  "All ten fixes merged, each with the regression test that fails without it; the two-language gate green (<code>gofmt</code>, <code>vet</code>, <code>go test</code>; <code>tsc</code>, <code>audit-css</code>, <code>verify</code>, <code>render-smoke</code>, <code>smoke</code>); leak and parity harnesses running in the gate rather than by hand; and a written statement, per defect, of which number a user saw change."))

# ---------------------------------------------------------------------------
add(ch("Phase 2 — detection and signals"))
add(fig(24, "the suspect-list cascade: cheap features narrow the field before the expensive book pass runs.", "the-suspect-list-cascade"))

add("""
<p>Onset detection fires when the move is visible ([[F:the-two-tier-funnel]] shows why a second pass exists at all). That is a definition, not a bug, and it is why onset alerts are worth less than they look. Phase 2 moves the platform from “tell me when it has started” to “rank the suspects seconds before the signal”, and makes the ranking gradeable so its precision can be stated at any operating point.</p>
""")
add(table(["feature family", "what it measures", "why it is in the list"], [
  ["per-coin z-scores", "deviation from that coin's own behaviour", "absolute thresholds do not transfer between pairs — the most reproduced finding in the literature"],
  ["trade-count bursts", "trades per window against baseline", "a pump is a burst of participants, not only of volume"],
  ["taker imbalance", "which side crossed the spread", "the direction of intent, and cheap to compute honestly"],
  ["book shape", "depth, spread, and where it thins", "the one new infrastructure piece: a two-tier book feed"],
  ["cross-sectional rank", "how unusual this coin is today", "separates a market-wide move from a single-coin event"],
], ["22%", "30%", "48%"]))
add(fig(27, "the two-tier funnel: a cheap screen across the market, an expensive pass on the suspects", "the-two-tier-funnel"))
add(co("warn", "The number that has to be printed",
  "Minute-scale early warning of a pump, measured across the published literature, is roughly <b>19% top-5 precision</b>. The deliverable of this phase is a ranked list with a measured precision curve, not a promise that the list is right."))
add("""
<h3>From signal to record</h3>
<p>A detection is an envelope: the score, the features that produced it, and refs to the candles and the book it read. Grading then needs no separate project — the detection resolves to an outcome the same way a forecast does, and the journal supplies the labels.</p>
""")
add(co("good", "Phase 2 exit gate",
  "A ranked suspect list live on the dashboard; precision and recall published at the operating budget; the two-tier book feed ingested and journaled; every feature computed under the walk-forward rule with its own leak test; and a written comparison against published baselines — including where we do worse."))

# ---------------------------------------------------------------------------
add(ch("Phase 3 — the envelope does the work"))
add(fig(33, "the topic registry as a map: planes down the side, producers across the top", "the-topic-registry-as-a-map"))

add("""
<p>Phase 3 is where the contract stops being architecture and becomes how the platform remembers ([[F:the-topic-registry-as-a-map]] is the shape of what has to be filled). Forecasts and decisions already publish; this phase makes it universal, fills the silent producers, and adds the one interceptor rule that turns the whole thing into a look-ahead firewall.</p>
""")
add(co("law", "The refs validation rule",
  "A forecast packet must cite the candle generation it consumed. If its refs are stale, or the generation does not match the platform's current candle generation for that key, the packet is refused at authority. That single rule is the platform-wide firewall: a forecast cannot claim data it did not have."))
add(table(["packet", "publisher", "what it makes possible"], [
  ["<code>intel.forecast</code>", "the forecast path (shipped)", "replay as backtest; the decision trail"],
  ["<code>intel.signal</code>", "the detectors (missing)", "graded detection quality; the suspect list"],
  ["<code>intel.movers</code> · <code>intel.ta</code> · <code>intel.board</code>", "market intel (missing)", "a dashboard whose numbers have provenance"],
  ["<code>model.decision</code>", "the decision path (shipped)", "why we acted, traceable to one forecast"],
  ["<code>model.fill</code> · <code>model.pnl</code>", "execution (missing)", "the third question: what did we do, and what happened"],
], ["32%", "28%", "40%"]))
add(co("good", "Phase 3 exit gate",
  "Every forecast and detection path publishing; replay-parity green per migrated endpoint; freshness refusals visible in the UI; and zero packet-shape changes, asserted by the existing envelope tests."))

# ---------------------------------------------------------------------------
add(ch("Phase 4 — the forecasting edge"))
add(fig(34, "the promotion gate: an evidence file, not a preference.", "the-promotion-gate"))

add("""
<p>This is the phase everyone assumes comes first, and [[F:the-promotion-gate]] is what it has to pass. It comes fourth because an edge measurement is worth nothing if the tape measure is wrong, and after Phase 1 it is not.</p>
""")
add(table(["job", "what changes", "why it is not optional"], [
  ["calibrated bands", "coverage becomes a first-class metric with telemetry and a promotion rule", "a 90% cone that holds 60% of outcomes is decoration"],
  ["runnable blends", "one spec generates the blend the platform runs and the blend it reports", "F4, F5 and F9 are the same lie in three places"],
  ["leak-free features", "a feature store with an admission log: data seen, cut, holdout result", "an unexplained feature is folklore"],
  ["graded foundation model", "the agent-only model is graded on the same cuts and metrics", "exemption by hardware is how a model becomes myth"],
], ["20%", "40%", "40%"]))
add(co("good", "Phase 4 exit gate",
  "Coverage telemetry with at least 30 days of resolved windows; a tuned blend promoted, or honestly failed to promote, through the full gate once; the foundation model graded; the feature store showing at least one candidate evaluated; the parity harness green across runtimes."))

# ---------------------------------------------------------------------------
add(ch("Phase 5 — speed and efficiency"))
add(fig(38, "cache invalidation: the key carries the generation, so a stale key misses instead of returning.", "cache-invalidation"))

add("""
<p>Efficiency work is where honesty usually dies quietly ([[F:cache-invalidation]] is the mechanism that keeps it honest).: a cache that returns a slightly different series, an incremental scorer that drifts from the full computation, a sweep that starves the live path it exists to serve. This phase is defined by its constraints as much as its targets.</p>
""")
add(table(["work", "the constraint that keeps it honest"], [
  ["one spec → every runtime's kernels", "parity in the gate, per model, on every cut"],
  ["cached series", "the cache key includes the generation; a stale key misses rather than returns"],
  ["incremental scoring", "incremental and full results agree exactly on every cut — a test, not an intention"],
  ["GPU sweeps", "a soak test replays a busy window and asserts the live paths were never starved"],
  ["bundle size and paint", "measured on the dashboard, not inferred from a bundler report"],
], ["32%", "68%"]))
add(co("good", "Phase 5 exit gate",
  "Cache reuse above target on a scripted session; incremental-versus-full parity green; a soak test proving sweeps never starve live paths; bundle sizes within target; and every counter on an operations panel — an efficiency claim that is not on a panel is a rumour."))

# ---------------------------------------------------------------------------
add(ch("Phase 6 — the ladder to execution"))
add(fig(42, "the kill-switch states: every path to stopped is short, and only a human leaves it.", "the-kill-switch-states"))

add("""
<p>Automated execution is not a feature that gets switched on; it is a ladder ([[F:the-evidence-ladder]]), and [[F:the-kill-switch-states]] is the one rung that must never be automated. It is a ladder climbed one rung at a time, with evidence at each rung and a human signature before the next. The purpose of the ladder is to make it structurally hard to lose money faster than the platform can learn.</p>
""")
add(table(["rung", "what runs", "what must be true before the next"], [
  ["<b>shadow</b>", "the engine decides; nothing is sent", "decisions reproduce on replay; refusal reasons visible"],
  ["<b>paper</b>", "orders simulated against the recorded book", "slippage assumptions match recorded fills within tolerance"],
  ["<b>risk gateway</b>", "the gateway sits in front of a live adapter, refusing everything", "every limit proven by test, including the kill switch"],
  ["<b>adapter</b>", "one venue, smallest allowed size", "a week with no unexplained deviation between intent and fill"],
  ["<b>small live</b>", "one venue, capped notional", "realised PnL tracks shadow within the stated band"],
  ["<b>scaled</b>", "the cap rises with evidence", "the human signs each rise, durably and audibly"],
], ["14%", "36%", "50%"]))
add(co("law", "The rules that do not bend at any rung",
  "A kill switch a human resets and no process can. A daily loss limit enforced in the gateway rather than the strategy. Caps that are configuration, not code. Freshness checked before the order is built. And re-enabling anything, or raising a cap, is a human act — durable across restarts and written to the audit trail."))
add(fig(44, "the evidence ladder: each rung is a claim, and each claim needs a file to support it", "the-evidence-ladder"))

# ---------------------------------------------------------------------------
add(ch("The next phase: a market-leading forecast engine, with DMA behind it"))
add(fig(50, "the roadmap: the phases in order, each gated by the one before it.", "the-roadmap"))

add("""
<p>The six phases make the platform honest, fast and recorded. They do not make it the best forecast engine in the market, and they are not meant to. This is the phase that does: a forecast engine specified as a measurable claim, and — behind an evidence gate, never in front of it — a direct-market-access path that can act on what the engine concludes.</p>
<h3>What “market-leading” has to mean, measurably</h3>
<p>“Better” is not a specification. Five numbers make it one, and every one is already computable from the record this plan builds:</p>
""")
add(table(["dimension", "the measure", "the bar to beat"], [
  ["<b>skill</b>", "skill versus the flat baseline, on walk-forward cuts, per frame and horizon", "positive and stable across pairs and regimes, not one good window"],
  ["<b>honesty of the band</b>", "achieved coverage minus advertised coverage", "within ±3 points at 50%, 80% and 90% bands"],
  ["<b>sharpness</b>", "mean band width at equal coverage", "narrower than the naive bootstrap at the same coverage"],
  ["<b>speed</b>", "p50 and p95 latency from request to published packet, per frame", "sub-second for 1m–1h, seconds for sweeps"],
  ["<b>traceability</b>", "fraction of published forecasts replayable to an identical verdict", "100%, asserted by the replay-parity test"],
], ["16%", "44%", "40%"]))
add(co("law", "The rule that keeps the claim honest",
  "A model is promoted only on out-of-sample evidence from the record, never on in-sample fit, never on a single symbol, and never on a single regime. Promotion is a gate with a written evidence file, and the file is attached to the packet that the promotion produces. If the claim cannot be restated as a number in chapter 24's dictionary, it is not a claim."))
add(fig(21, "one spec, every runtime: what the operator runs and what the GPU sweeps are one description", "one-spec-every-runtime-the-e"))

add("""
<h3>The engine, layer by layer</h3>
""")
add(table(["layer", "what it does", "state today", "what this phase adds"], [
  ["<b>features</b>", "per-coin z-scores, burst counts, taker imbalance, book shape, cross-sectional rank", "computed inside detectors, not stored", "a feature store with an admission log and leak tests"],
  ["<b>model zoo</b>", f"the {len(MODELS)} registered models across twelve families", "registered and runnable", "family-level parity and per-regime grades"],
  ["<b>combination</b>", "ensembles and blends over the zoo", "weights fixed or absent", "weights fitted walk-forward, persisted, and replayable"],
  ["<b>calibration</b>", "turns a point path into a band that means something", "λ fit and a bootstrap fan", "coverage telemetry feeding an ACI update per pair and frame"],
  ["<b>regime</b>", "conditions the choice of family", "a label, lightly used", "regime as a conditioning input with its own held-out grading"],
  ["<b>uncertainty</b>", "the cone, and what it claims", "bootstrap percentiles", "conformal and ACI layers compared on coverage and sharpness"],
  ["<b>serving</b>", "publishes the packet, caches the series, records the latency", "publishes and journals", "latency budgets per frame, with the counters on a panel"],
], ["14%", "34%", "22%", "30%"]))
add(co("note", "Why the layers are ordered this way",
  "Each layer can only be judged if the one above it is honest: weights cannot be fitted if the features leak, and bands cannot be calibrated if the point path is fiction. That is the same argument as Phase 1, applied to the engine instead of the platform."))

add("""
<h3>DMA and auto-trade: behind the gate, not in front of it</h3>
<p>Direct market access is an execution capability, and an execution capability with no measured edge is a way to pay fees quickly. The engine therefore ships first, and the execution path is built to be <b>unable</b> to act until the engine has produced evidence — the ladder in the previous chapter is the mechanism, and this phase supplies what the ladder grades.</p>
""")
add(table(["component", "responsibility", "the safety property it enforces"], [
  ["<b>intent</b>", "turns a decision packet into an order intent packet", "no intent without a fresh decision and its parent cid"],
  ["<b>risk gateway</b>", "per-order, per-position and per-day limits; the kill switch", "limits are configuration, and the switch is a human reset"],
  ["<b>adapter</b>", "one venue, idempotent order ids, reconciliation against fills", "an unknown order state halts new intents, never retries blind"],
  ["<b>ledger</b>", "fills, fees, slippage and PnL, journaled", "every number that affects capital is a packet"],
  ["<b>supervisor</b>", "watches divergence between intent and fill", "a divergence beyond tolerance descends a rung, automatically"],
], ["16%", "40%", "44%"]))
add(fig(43, "the gateway evaluation: every intent is checked against the limits before it can become an order", "the-gateway-evaluation-s-eva"))
add(co("warn", "The honest ordering",
  "Auto-trade is the last thing built and the first thing switched off. If the engine's coverage does not hold, or its skill versus flat is not positive on out-of-sample cuts, the DMA path stays at the shadow rung — and the document says so in the same breath as the ambition, because a plan that promises both the engine and the profits is a brochure."))

add("""
<h3>The gate for this phase</h3>
""")
add(co("good", "Phase 7 exit gate",
  "Five numbers from the table above, each measured over at least thirty days and published: skill versus flat positive out of sample on the frames the engine serves; coverage within ±3 points at three band levels; sharpness better than the naive bootstrap; p95 latency inside the stated budget per frame; and replay parity at 100%. Only then does the ladder's paper rung open — and the ladder's own gates take over from there."))

# ---------------------------------------------------------------------------
add(ch("Models: the catalogue and what each family is for"))
add(f"""
<p>{len(MODELS)} models are registered, in twelve families. They are not twelve opinions about the same thing: each family makes a different assumption about how a price series behaves, and the catalogue exists so that the platform can be wrong in twelve different ways and be graded on all of them.</p>
""")
add(table(["family", "the assumption", "representative models", "where it fails"], [
  ["<b>moving average</b>", "recent prices carry the signal; the average removes the noise", "ema5, ema20, sma50, wma, hma", "at a genuine turn, every average lags by construction"],
  ["<b>exponential</b>", "the newest observation matters most, weighting decays geometrically", "ses, des, damped80, damped90", "a single outlier can carry the whole path"],
  ["<b>damped trend</b>", "trend persists but decays toward a level", "damped70, damped75, damped85", "a trend that does not decay — a real breakout"],
  ["<b>trend</b>", "the slope continues over the horizon", "trend20, trend50, vwtrend", "chop, where slope is noise"],
  ["<b>volume</b>", "price follows where the volume went", "vwap, vwdrift, volweighted", "thin pairs, where volume is noise"],
  ["<b>analogue</b>", "the nearest historical window predicts the next", "analog5, analog10, analog20", "regimes that have not happened before"],
  ["<b>statistical</b>", "the series has a describable linear structure", "arima, ols, mlr", "structure that is not linear — most microstructure"],
  ["<b>ensemble</b>", "combining several reduces variance", "blend, super, vote", "when all members share one blind spot"],
], ["16%", "30%", "24%", "30%"]))
add(co("note", "Why twelve families and not one good model",
  "Because the honest answer to “which model is best?” is “for which pair, on which frame, in which regime” — and the only way to answer it per pair and frame is to run all of them and grade each. The disagreement between families is information: when trend and analogue agree and the ensemble is tight, the band is narrow for a reason."))
add(co("warn", "The two things the catalogue cannot tell you",
  "It cannot tell you the models are profitable, and it cannot tell you which one will be right next. It can tell you how each has done on this pair, on this frame, out of sample — which is what the comparison table shows and what the promotion gate reads."))

# ---------------------------------------------------------------------------
add(ch("Calibration and uncertainty"))
add(fig(36, "the ACI update loop: coverage drift feeds the next band width.", "the-aci-update-loop"))

add("""
<p>A forecast without a band is a number pretending to be a fact. [[F:the-coverage-telemetry-loop]] is how the band is judged. The band is the part that says how much to believe the number, and it is the part that is almost always wrong. This chapter is about what the platform does to make the band mean something.</p>
""")
add(table(["mechanism", "what it does", "how it is judged"], [
  ["<b>bootstrap fan</b>", "resamples training residuals to produce percentiles of the path", "coverage at 50/80/90 versus advertised; width versus a naive constant band"],
  ["<b>conformal</b>", "uses held-out residual quantiles to widen the band to a target coverage", "the same coverage test, plus sharpness — conformal is honest but can be wide"],
  ["<b>ACI</b>", "an online update that widens or narrows the band as coverage drifts", "coverage over rolling windows; the width trend during a regime change"],
  ["<b>λ calibration</b>", "scales the fan by a fitted factor", "whether one λ holds across pairs or needs to be per pair"],
], ["16%", "34%", "50%"]))
add(fig(35, "the coverage telemetry loop loop: every resolved window compares the band it advertised with what happened, and the result feeds the next band.", "the-coverage-telemetry-loop"))
add(co("law", "The claim the band has to earn",
  "A 90% band holds 90% of outcomes. Not 60%, not 97% — and if it cannot hold its own number, the band is removed from the surface rather than relabelled. This is the single most falsifiable claim in the document, which is why it is the one that gets telemetry."))

# ---------------------------------------------------------------------------
add(ch("Backtests and replay"))
add(fig(25, "the grading loop: cut, forecast, wait, grade, and feed the scorecard.", "the-grading-loop"))

add("""
<p>The old platform's weakest property was that the backtest and the live path shared intent but not code — [[F:backtest-equals-replay]] is the answer to that.: they could disagree, and nobody could say which was right. Here the two are the same function, applied to a recorded packet instead of a fresh one.</p>
""")
add(table(["step", "live path", "replay path"], [
  ["inputs", "the newest bars", "the bars recorded in the packet's refs"],
  ["compute", "the model's path function", "the same function"],
  ["verdict", "the decision rule", "the same rule"],
  ["grade", "what happens next, later", "the price at anchor + horizon, from the bar store"],
  ["output", "<code>model.decision</code>", "an outcome row, and a scorecard"],
], ["16%", "42%", "42%"]))
add(co("good", "Why replay is the strongest test in the system",
  "It exercises the whole chain against history the platform cannot change: the packet, the refs, the rule, the price that followed. When a decision rule changes, the scorecard changes — which makes the change visible rather than invisible. That is the property that makes an honest leaderboard possible."))
add(fig(30, "backtest equals replay: the journal is the dataset, and the decision function is shared.", "backtest-equals-replay"))

# ---------------------------------------------------------------------------
add(ch("Storage: three surfaces and what each is for"))
add(fig(46, "retention per plane: the journal keeps every packet; the lake keeps them longer", "retention-per-plane"))

add("""
<p>Four surfaces, each with one job ([[F:the-store-layout]]). The rule that keeps them coherent is that any number a user saw must be reproducible from one of them.</p>
""")
add(table(["surface", "path", "written by", "answers"], [
  ["bar store", "<code>data/history/SYMBOL-INTERVAL.jsonl</code>", "<code>fetchAndStore</code>", "what the forecast read"],
  ["journal", "<code>data/envelope-journal.jsonl</code>", "<code>recordEnvelope</code>", "what we computed, and why"],
  ["lake", "<code>state/lake3</code>", "the recorder (:8801)", "everything, partitioned and compressed"],
  ["GPU results", "in memory, per symbol", "the engine", "what the board shows now"],
], ["14%", "32%", "22%", "32%"]))
add(pre("""{"open_time":1793732400000,"open":77691.39,"high":77720.1,"low":77640.0,
 "close":77701.2,"volume":12.84,"close_time":1793732459999}"""))
add(co("note", "Why the journal is a file and not a table",
  "A file replays in one pass, tails, copies and diffs. It never blocks a publisher, and a corrupt line is one line. The lake is the database-shaped store; the journal is the index the decision path reads."))
add(fig(19, "the store layout: the bar store answers forecasts, the journal answers history, the lake holds everything.", "the-store-layout"))

# ---------------------------------------------------------------------------
add(ch("GPU compute and the agent"))
add(fig(47, "job scheduling: a sweep is one job, and a forecast never queues behind it.", "job-scheduling"))

add("""
<p>The GPU tier exists for one job that the CPU tier cannot do in a useful time ([[F:compute-topology]]), and [[F:job-scheduling]] is what stops it starving the live path.: sweeping a parameter grid across many lookbacks and windows and grading every combination walk-forward. Everything else the platform does is cheap enough to stay on the platform.</p>
""")
add(table(["capability", "endpoint", "what it is for"], [
  ["device and liveness", "GET /gpu", "the board shows what is computing, on which device"],
  ["sweep", "POST /jobs", "one CUDA pass over the grid; results sorted by the graded metric"],
  ["status", "GET /jobs/:id", "progress, and the failure reason when there is one"],
  ["parity", "the gate", "the same job on the CPU executor must agree within tolerance"],
], ["22%", "22%", "56%"]))
add(co("warn", "The failure mode the fallback prevents",
  "If the GPU box is off, the platform scores locally and the board still moves. That is deliberate: an accelerator that is required is not an accelerator, it is a dependency. What the fallback must never do is produce a different answer for the same model name — which is exactly what finding F7 was."))
add(fig(18, "compute topology: platform, agent and GPU as three tiers of one registry, with the local fallback kept honest by a parity test.", "compute-topology"))

# ---------------------------------------------------------------------------
add(ch("The dashboard: what the UI has to say"))
add("""
<p>The dashboard is where every honesty claim in this document becomes visible, or does not ([[F:the-dashboard-information-architecture]]). It is not a shop window: it is the place where a number is either traceable to its packet or is decoration.</p>
""")
add(table(["view", "the question it answers", "the honesty surface it carries"], [
  ["<b>overview</b>", "what is moving right now", "live alerts, leaderboard, split, screen, survey, movers"],
  ["<b>pump &amp; dump</b>", "what the detectors see, in detail", "the alert feed with the evidence that fired each card"],
  ["<b>forecast</b>", "what the model expects, and how it has done", "the parameters, the chart with its cone, the comparison table, the backtest"],
  ["<b>message bus</b>", "is the plumbing alive", "per-topic envelope counts and the recording rate"],
  ["<b>settings</b>", "what counts as a move", "the thresholds themselves, applied live"],
  ["<b>agents</b>", "are the GPU workers healthy", "device, version, current job"],
], ["16%", "34%", "50%"]))
add(co("good", "The three surfaces this plan adds",
  "<b>Coverage against advertised coverage</b>, per model and frame — the band's own report card. <b>The suspect list with its precision curve</b> — Phase 2's deliverable, where a trader would look. <b>The decision trail</b> — for any past moment, the chain from price to forecast to verdict, in one click. None of the three exists today, and all three are cheap once the packets do."))
add(fig(48, "the dashboard information architecture's information architecture: six views, and what each draws from.", "the-dashboard-information-ar"))

# ---------------------------------------------------------------------------
add(ch("Flows, end to end"))
add("""
<p>Seven flows, each a chain of packets ([[F:a-day-in-the-life-of-a-tick]] is the first of them, and [[F:the-envelope-chain-for-one-decision]] the third). Reading them in order is the shortest way to understand what the platform does: the first four exist, the last three are what this plan adds.</p>
""")
add(table(["flow", "the chain", "state"], [
  ["market → bar", "venue → <code>market.candle</code> → bar store", "shipped"],
  ["bar → forecast", "bar store → model → <code>intel.forecast</code> → journal", "shipped"],
  ["forecast → decision", "<code>intel.forecast</code> → rule → <code>model.decision</code> (parent_cid)", "shipped"],
  ["decision → grade", "journal → replay → outcome at anchor + horizon", "shipped"],
  ["detection → alert", "tape + book → score → <code>alert.pumpdump</code> / <code>intel.signal</code>", "detector exists, packet does not"],
  ["decision → intent", "<code>model.decision</code> → risk gateway → order intent", "Phase 6"],
  ["intent → fill → PnL", "adapter → <code>model.fill</code> → <code>model.pnl</code>", "Phase 6"],
], ["18%", "60%", "22%"]))
add(fig(5, "a day in the life of a tick: how one trade print travels from the venue to the journal.", "a-day-in-the-life-of-a-tick"))
add(fig(8, "the envelope chain for one decision: each stage adds a packet, and each packet names its parent", "the-envelope-chain-for-one-d"))

# ---------------------------------------------------------------------------
add(ch("Compliance and audit"))
add(fig(49, "incident response: who is told, in what order, and what is recorded at each step.", "incident-response"))

add("""
<p>Compliance here is not a filing exercise. It is the ability to answer, months later, who decided what, on which evidence, under whose authority, and with which limits in force. Every one of those answers is a packet or a log line that this plan makes durable.</p>
""")
add(table(["obligation", "how the platform meets it", "state"], [
  ["audit trail", "append-only journal; packets immutable; pipeline stages recorded on each", "shipped"],
  ["lineage", "cid and parent_cid across forecast, decision and (later) fill", "shipped for forecasts and decisions"],
  ["human authority", "re-enables, cap raises and rung changes are human acts, durable and audited", "policy; enforcement is Phase 6"],
  ["limits", "per-order, per-position and per-day in the gateway; kill switch", "Phase 6"],
  ["data retention", "lake retention per plane; journal retention; bar store pruning", "partially shipped"],
  ["model governance", "every model version is named in the packet; promotion has an evidence file", "Phase 4 and 7"],
], ["20%", "60%", "20%"]))
add(co("note", "The test a regulator would actually apply",
  "Not “is there a log?” but “show me the evidence for this specific trade, and show me that it was within the limits that were in force at the time.” That answer is the chain from a fill back to a decision, from the decision to the forecast, from the forecast to its refs — and the limits in force are themselves a packet."))

# ---------------------------------------------------------------------------
add(ch("Operations, security and reliability"))
add("""
<p>Six processes, one machine, and a network where the GPU workers live ([[F:the-service-and-port-map]]). The platform is designed so that each process can be restarted alone and no process holds state that only lives in memory.</p>
""")
add(table(["process", "port", "what breaks without it"], [
  ["platform", "8787", "nothing works"],
  ["marketdata", "8796", "the boards freeze; the page still loads"],
  ["refdata", "8790", "the ticker picker falls back to the market feed's symbols"],
  ["recorder", "8801", "<code>/api/bus</code> answers 502 and the bus view goes empty"],
  ["RabbitMQ", "61613 / 15672", "packets are journalled but not broadcast"],
  ["model books", "8799", "the Python books stop"],
], ["18%", "14%", "68%"]))
add(fig(9, "the service and port map.", "the-service-and-port-map"))
add(co("warn", "The three failure modes worth rehearsing",
  "<b>A full disk</b> stops the recorder and the platform keeps answering — by design, and it is why recording never fails a request. <b>A silent recorder</b> is the one that bites: the bus view is the only place it shows. <b>A dead market feed</b> leaves the last price in place, so every move reads zero — flat charts are a feed symptom before they are a model symptom."))

# ---------------------------------------------------------------------------
add(ch("The adversary"))
add("""
<p>A detection programme that assumes a static adversary works for one quarter ([[F:the-pump-wave-actor-by-actor]] is the sequence it has to keep up with). This chapter states what a manipulator does to avoid us, and what we hold that they cannot cheaply remove.</p>
""")
add(table(["evasion", "what it costs them", "what we already have"], [
  ["slower, longer accumulation", "time, and a bigger footprint in the book", "per-coin baselines over long windows"],
  ["wash trading to inflate volume", "fees, and a flat taker imbalance", "aggressor share and trade-count features, not just volume"],
  ["spreading across many pairs", "coordination cost, and a visible cross-sectional rank", "cross-sectional ranking, which is exactly a many-pair signal"],
  ["spoofing the book", "capital at risk of being hit", "two-tier book shape features, and journaled snapshots"],
  ["tuning to our thresholds", "knowledge of them, which they do not have", "thresholds as configuration, changed without a deploy"],
], ["24%", "34%", "42%"]))
add(co("note", "The honest limit",
  "Every counter here is a cost imposed, not an immunity. The defensible claim is narrower: the platform measures the cost it imposes, because every detection is graded and every grade is journaled. A detector that stops working shows up as a falling precision curve rather than as silence."))
add(fig(26, "the pump wave, actor by actor: who does what, in what order, and which feature each stage leaves a trace in", "the-pump-wave-actor-by-actor"))

# ---------------------------------------------------------------------------
add(ch("Risk register"))
add("""
<p>The register is the list of things that could make this plan wrong — not a list of things that could go wrong in production, which is the operations chapter.</p>
""")
add(table(["risk", "how it would show up", "decision owed"], [
  ["the models have no edge at all", "coverage holds, skill versus flat never turns positive", "keep the forecast surface as a measurement tool, or retire the claim"],
  ["detection precision stays at the published ceiling", "the suspect list sits near 19% top-5 however it is tuned", "trade a ranked list, or sell it as context only"],
  ["the first live rung loses faster than shadow predicted", "deviation between intent and fill beyond the stated band", "descend a rung, and by whose signature"],
  ["filling the silent topics costs more judgement than expected", "each new producer surfaces grading work nobody scheduled", "fill all of them, or the three the scorecard needs"],
  ["one-spec generation costs more than the parity it buys", "Phase 5 slips while parity keeps failing", "accept two specs with a parity test, temporarily"],
  ["the evidence outgrows the human", "nobody reads the audit trail", "publish a weekly digest, and decide what belongs in it"],
], ["28%", "34%", "38%"]))
add(co("warn", "The decision this document cannot make for you",
  "Whether the platform is a <b>measurement instrument</b> or a <b>trading system</b>. Everything up to the engine phase is defensible as the former and cheap; the DMA path is only worth starting if the instrument has produced something worth acting on. That is a judgement about evidence, and it belongs to the operator."))

# ---------------------------------------------------------------------------
add(ch("The claim: the most accurate system to date"))
add("""
<p>This is the ambition, stated so that it can be checked rather than asserted. “Most accurate” is not a feeling about a chart; it is five numbers, all of which this platform is being built to produce, and all of which a sceptic can recompute from the record.</p>
""")
add(table(["claim", "the number", "how a sceptic checks it"], [
  ["it forecasts better than doing nothing", "skill versus flat, out of sample, per pair and frame", "replay the journal; the baseline is in the same response"],
  ["its bands mean what they say", "achieved coverage − advertised coverage, at 50/80/90", "coverage telemetry, thirty days minimum"],
  ["it is sharp, not just honest", "band width at equal coverage, versus the naive bootstrap", "same telemetry, second column"],
  ["it is fast enough to act on", "p50 and p95 latency, request to published packet", "the latency counters on the operations panel"],
  ["it can prove all of the above", "replay parity, 100%", "the replay-parity test in the gate"],
], ["30%", "34%", "36%"]))
add(co("law", "What makes the claim defensible rather than loud",
  "Every one of the five numbers is computed from packets the platform recorded before the claim was made. That is the difference between “our model is accurate” and “here is the record; recompute it”. The first is marketing. The second is engineering, and it is the only kind of claim this document is willing to make."))
add("""
<h3>What would falsify it</h3>
<p>Any one of these is enough to withdraw the claim:</p>
<ul>
<li>skill versus flat is not positive out of sample, on the frames the engine serves, for a sustained period;</li>
<li>achieved coverage misses its advertised level by more than three points at any band;</li>
<li>replay and the live path reach different verdicts on the same packet;</li>
<li>a latency budget is missed on the frames that matter;</li>
<li>a fill cannot be traced to the decision, the forecast and the data behind it in under a minute.</li>
</ul>
""")

# ---------------------------------------------------------------------------
add(ch("Worked examples"))
add("""
<p>Fourteen examples, each quoting a payload the platform produced or a number it reported while this document was written. They are here so that a reader can check the prose against the record, and so that the abstractions have something concrete to fail against.</p>
""")

add("<h3>Example 1 — a forecast packet, field by field</h3>")
add(pre("""{"meta": {"version": 1, "plane": "intel", "topic": "intel.forecast",
          "key": "RIFUSDT", "producer": "forecast", "generation": 6,
          "cid": "tel.forecast-RIFUSDT-fb1362e1821fc598",
          "computed_at": 1793845568522,
          "hops": ["authority", "keying", "correlation", "freshness"]},
 "refs": [{"topic": "market.candle", "key": "RIFUSDT", "age_ms": 123968522}]}"""))
add(co("ex", "What to read in it",
  "The <b>key</b> says what it is about. The <b>cid</b> is how you will find it again. The <b>generation</b> says this was the sixth publication for that pair. The <b>hops</b> say the four refusing stages ran and none refused. And the <b>refs</b> say the candles it read were 123,968,522 ms old — over a day, for a packet claiming to be a fresh forecast. That is the look-ahead firewall doing its job: the packet is honest about how stale its input was, and a decision can refuse it on that basis."))

add("<h3>Example 2 — a decision, and the parent that explains it</h3>")
add(pre("""{"meta": {"topic": "model.decision", "key": "BTCUSDT", "producer": "engine",
          "generation": 2,
          "cid": "del.decision-BTCUSDT-458613c4c55bb3ff",
          "parent_cid": "tel.forecast-BTCUSDT-576cc1eacdb3de6a",
          "computed_at": 1793742662898}}"""))
add(co("ex", "What to read in it",
  "The decision is not an opinion; it names the forecast it was made from. Walking <code>parent_cid</code> gives the forecast packet, whose <code>refs</code> give the candles. Three hops from “why did we act?” to “which bars did the model see?”."))

add("<h3>Example 3 — the refusal that protects the account</h3>")
add(pre("""GET /api/decision?symbol=BTCUSDT&interval=4h

  verdict   refused
  reason    stale forecast
  age       1 706 000 ms
  limit     60 000 ms"""))
add(co("ex", "What to read in it",
  "A forecast 28 minutes old is not a forecast, it is a memory. The refusal is not an error path: it is the platform declining to act on evidence that has expired, and it quotes both numbers so the operator can see which side of the line it fell on."))

add("<h3>Example 4 — one graded outcome, and how it is read</h3>")
add(pre("""{"symbol": "BTCUSDT", "generation": 116,
 "cid": "tel.forecast-BTCUSDT-2f723c0b9a913baf",
 "verdict": "none", "price": 77871.70, "target": 77871.70,
 "edge_pct": 0.0000050, "realised": 77691.39, "move_pct": -0.2315,
 "correct": false, "age_ms": 10160}"""))
add(co("ex", "What to read in it",
  "The forecast projected a move of five millionths of a percent — a flat path — and the market moved −0.23% in the window. The verdict was <b>none</b>, so there was nothing to be right about, and <code>correct: false</code> records that the flat call did not describe what happened. Three of those four numbers are checked against the bar store at replay time; none of them is typed by a person."))

add("<h3>Example 5 — the scorecard, as it stands</h3>")
add(table(["metric", "value", "reading"], [
  ["packets replayed", str(REPLAY_SUMMARY.get("packets", "—")), "every recorded decision"],
  ["graded", str(REPLAY_SUMMARY.get("graded", "—")), "those with a price to grade against"],
  ["buy / sell / none", f"{REPLAY_SUMMARY.get('buy','—')} / {REPLAY_SUMMARY.get('sell','—')} / {REPLAY_SUMMARY.get('none','—')}", "the platform declines almost always"],
  ["directional calls correct", f"<b>{REPLAY_SUMMARY.get('correct','—')}</b>", "this is the number that keeps the claim honest"],
  ["mean MAPE", f"{REPLAY_SUMMARY.get('mean_mape', 0):.4f}" if isinstance(REPLAY_SUMMARY.get('mean_mape'), float) else "—", "the forecast's average miss"],
], ["30%", "20%", "50%"]))
add(co("warn", "What this example is for",
  "It is here so the document cannot be read as a promise. The machinery grades; the models, so far, have nothing to grade well. Phase 4 and the engine phase exist to change this number, and the number will be the judge of whether they did."))

add("<h3>Example 6 — the orchestrator deciding what to compute</h3>")
add(pre("""GET /api/orchestrator/plan?symbol=BTCUSDT

  intel.signal    gen 0   age 0       recompute  nothing recorded
  intel.flow      gen 0   age 0       recompute  nothing recorded
  intel.forecast  gen 8   age 250 s   recompute  older than its deadline"""))
add(co("ex", "What to read in it",
  "Two different reasons for the same verdict. <b>Nothing recorded</b> means the capability has never published — the silence of chapter 3, seen from the scheduler's side. <b>Older than its deadline</b> means it published, and the answer has expired. The orchestrator needs no model knowledge to tell those apart, which is why it is the piece most likely to stay correct as the catalogue grows."))

add("<h3>Example 7 — what the detector sees before it fires</h3>")
add(pre("""tape, last 10 s, one pair:
  trades            47
  volume            $231 000        baseline $35 500        → 6.5x
  taker buy share   100%            baseline 51%            → imbalance
  move             +0.06%           z-score vs own history  → 3.3
  cross-sectional  rank 4 of 477    → not a market-wide move
⇒ composite score 64 · kind P · severity ALERT"""))
add(co("ex", "What to read in it",
  "Six numbers, four of them relative to that pair's own baseline. The absolute move — six hundredths of a percent — is unremarkable; the multiple and the imbalance are not. This is the whole argument for per-coin z-scores in one example: an absolute threshold set to catch this would fire on every pair in a busy hour."))

add("<h3>Example 8 — the defect F1, in arithmetic</h3>")
add(table(["step", "what the code did", "what it should do"], [
  ["training window", "bars[0 : n]", "bars[0 : cut]"],
  ["graded window", "bars[cut : cut+h]", "bars[cut : cut+h]"],
  ["forecast anchor", "the <b>last graded bar</b>, because the path was computed over the unsliced array", "the bar at <code>cut-1</code>"],
  ["consequence", "the model's path starts at a price it could not have known, so the error is measured against a forecast that was never makeable", "the error measures the model"],
], ["20%", "46%", "34%"]))
add(co("warn", "Why this one is critical",
  "It does not make the model wrong; it makes the <b>grade</b> meaningless. A model graded this way can look excellent while being useless, and the leaderboard will rank it first. That is worse than a bad model, because it is a bad measurement wearing a good number."))

add("<h3>Example 9 — the defect F2, in one line</h3>")
add(pre("""smaValue(window=80)  → called once with 60 bars → n := 60
smaValue(window=150) → returns the mean of the last 60 bars
smaValue(window=100) → returns the mean of the last 60 bars"""))
add(co("ex", "Why the test suite missed it",
  "Default lab configurations never call a window longer than the data on hand, so the mutation never happened in the tests. It happened in the live sweep, on every cut, for every symbol — which is why the sweep's numbers and the platform's numbers disagreed for the same model name."))

add("<h3>Example 10 — calibration: advertised against achieved</h3>")
add(table(["band", "advertised", "achieved (30 days)", "what it means"], [
  ["50%", "50.0%", "to be measured", "the inner band should hold half the outcomes"],
  ["80%", "80.0%", "to be measured", "the middle band"],
  ["90%", "90.0%", "to be measured", "the outer band, and the one a trader reads"],
], ["16%", "18%", "26%", "40%"]))
add(co("note", "Why this table is empty",
  "Because the telemetry that fills it is the deliverable of Phase 4. Printing plausible numbers here would be exactly the failure this document is written against: a measurement-shaped claim with no measurement behind it. The empty cells are the honest state."))

add("<h3>Example 11 — a latency budget, per frame</h3>")
add(table(["frame", "bars needed", "p50 target", "p95 target", "what dominates"], [
  ["1m", "≥ 60", "150 ms", "400 ms", "store read plus model compute"],
  ["5m", "≥ 60", "180 ms", "450 ms", "store read"],
  ["1h", "≥ 120", "250 ms", "600 ms", "model compute over a longer window"],
  ["4h", "≥ 120", "250 ms", "600 ms", "as above"],
  ["1d", "≥ 60", "200 ms", "500 ms", "store read"],
], ["12%", "16%", "18%", "18%", "36%"]))
add(co("ex", "Why the budget is per frame",
  "A single latency number hides the frame that matters. The one-minute frame is where a slow path costs an opportunity; the daily frame is where nobody notices a second. Publishing the budget per frame is what makes Phase 5's efficiency work measurable rather than rhetorical."))

add("<h3>Example 12 — the alert lifecycle, as states</h3>")
add(table(["state", "entered when", "what is recorded", "counts toward"], [
  ["draft", "the composite score crosses its threshold", "features, score, kind, severity", "nothing"],
  ["suppressed", "dedup or cooldown", "the reason and the window", "nothing"],
  ["fired", "it passes suppression", "the envelope, with refs", "<b>precision</b>"],
  ["confirmed", "the book tier agrees", "the confirmation features", "precision at the tier"],
  ["dropped", "the book tier denies", "the denial features", "precision (as a negative)"],
  ["resolved", "the outcome window closes", "the price that followed", "grading"],
  ["graded", "replay computes the metrics", "hit, latency, excursion", "the scorecard"],
], ["14%", "28%", "34%", "24%"]))
add(co("ex", "The point of the last two rows",
  "An alert that fires and is never graded teaches nothing — and ungraded alerts are how detection teams end up with folklore. The lifecycle ends at <b>graded</b>, not at <b>fired</b>, which is a design statement: an alert is not the product, its grade is."))

add("<h3>Example 13 — walking a lineage, as an operator would</h3>")
add(pre("""1. /api/decision?symbol=BTCUSDT&interval=4h
     → cid del.decision-BTCUSDT-4586…, parent tel.forecast-BTCUSDT-576c…

2. /api/envelope/chain?cid=del.decision-BTCUSDT-4586…
     lineage depth 2
       model.decision  gen 2    parent tel.forecast-BTCUSDT-576c…
       intel.forecast  gen 117  parent —

3. the forecast packet's refs
     market.candle / BTCUSDT / age_ms 16141687

4. /api/replay?symbol=BTCUSDT&limit=1
     the same cid, graded: verdict, price, realised, move, correct"""))
add(co("ex", "Three minutes or thirty seconds?",
  "Four requests, all of them cheap, and the answer is complete: what we believed, what it was based on, how stale that basis was, and how it turned out. The goal of the whole architecture is that this walk becomes a single click on a dashboard rather than four calls — but even as four calls, it exists."))

add("<h3>Example 14 — a promotion, as an evidence file</h3>")
add(table(["field", "content"], [
  ["candidate", "a blend of trend, damped and analogue weights, fitted walk-forward"],
  ["cuts", "n cuts per pair, per frame, over the period stated"],
  ["skill vs flat", "positive on the frames the engine serves, with the interval"],
  ["coverage", "achieved at 50/80/90 against advertised, within ±3 points"],
  ["regimes", "graded per regime, not only in aggregate"],
  ["parity", "identical output in the platform and on the agent"],
  ["replay", "every historical verdict reproducible with the same rule"],
  ["signed", "the operator's sign-off, in the audit trail, before the flag flips"],
], ["20%", "80%"]))
add(co("good", "Why the file is the deliverable",
  "A promotion without an evidence file is a preference. The file is what makes the platform's promotion history auditable — and it is the same artefact the DMA ladder's first rung requires, which is why building it here pays for itself twice."))

# ---------------------------------------------------------------------------
add(ch("The audit, finding by finding"))
add(fig(23, "the test corpus at a glance: what the gate exercises, and what it does not", "the-test-corpus-at-a-glance"))

add("""
<p>The summary table in chapter 2 states what was wrong. This chapter states it the way an engineer needs it: what the code did, what it should have done, what it changed for a user, and the test that now prevents it. It is deliberately specific, because a defect described vaguely cannot be verified as fixed.</p>
""")

FINDINGS = [
  ("F1", "The verify handler graded volume-family models on an unsliced array",
   "computed the path over every bar and then graded it, so the path was anchored on the last graded bar",
   "slice at the cut, anchor at <code>cut-1</code>",
   "the leaderboard ranked vwap, vwtrend, vwdrift, hybrid, super and the auto branch on a forecast that could not have been made",
   "a leak test that fails when any graded index appears in the training slice"),
  ("F2", "The sma closure mutated its captured window",
   "first call with fewer bars than the window permanently shrank <code>n</code> for every later call",
   "a window is a value; compute the mean without writing to it",
   "the live sweep computed sma80, sma100 and sma150 as sma60 on every cut, for every symbol",
   "a test that calls sma80 with a short window and then asserts sma150 is still sma150"),
  ("F3", "MLR re-derived its own cut",
   "recomputed <code>len(bars) − horizon</code> inside the model instead of using the loop's cut",
   "take the cut from the caller",
   "every MLR backtest number was graded one horizon too far ahead, systematically sabotaging its rank",
   "an alignment test that asserts the graded bar is exactly one horizon after the cut"),
  ("F4", "The sweep's hybrid and vwdrift were different formulas",
   "the GPU sweep implemented 0.6·vwap + 0.4·vwtrend, and a one-bar relative volume change, while production used a six-component blend and a 20-bar z-score",
   "generate both from one spec, or import production",
   "the R² and MAPE shown for those names were earned by models nothing executes",
   "a parity test per model, in the gate"),
  ("F5", "The optimiser anchored where nothing anchors",
   "blended at the regression's fitted last value while production anchors at the actual close, and persisted no weights",
   "anchor at the actual close; persist the weights with the config",
   "the optimiser's MAE described a model that cannot be run, and a tuned blend could not be reproduced",
   "a saved config that reproduces its reported MAE exactly on replay"),
  ("F6", "The feature model standardised over the whole series",
   "mean and standard deviation were computed across all bars, including those after the cut",
   "standardise on training rows only",
   "features carried information from the future into the model's input",
   "a leak test on the produced z-scores"),
  ("F7", "The agent's CPU fallback omitted the anchor",
   "the CPU analogue path skipped the step-0 anchor and the drift centring the Go and jax paths apply",
   "one spec, all three paths",
   "the same model name returned different forecasts depending on which hardware ran it",
   "the parity harness, run on every cut"),
  ("F8", "The hybrid undo loop subtracted across the whole path",
   "when a component went non-positive mid-path, its weight was removed from every index, including those where nothing had been added",
   "subtract only where the component contributed",
   "the tail of the blend was corrupted exactly when a member failed, which is when the blend matters",
   "a property test over random failure positions"),
  ("F9", "Go blended levels; the agent blended paths",
   "the ensemble in Go combined raw sma/ema levels while the agent combined converged paths",
   "one blend formula, generated from the spec",
   "the same name produced a path that teleported at step 0 in one runtime and not the other",
   "parity, again — same name, same numbers"),
  ("F10", "The small ones, each real",
   "a wrong window start; a dropped anchor in super's check loop; a split-conformal order statistic one index below the textbook value; a handler that panics on empty results; out-of-range horizons reset to 24 rather than clamped; calibration bins double-counted at the boundary; an unguarded division in error_pct; two UI labels describing methods the code does not use",
   "eight separate fixes",
   "small wrong numbers, and two places where the interface described something the code did not do",
   "eight regression tests, one per fix"),
]
for tag, what, did, should, impact, test in FINDINGS:
    add(f"<h3>{tag} — {what}</h3>")
    add(table(["", "detail"], [
      ["what the code did", did],
      ["what it should do", should],
      ["what it changed for a user", impact],
      ["the test that now prevents it", test],
    ], ["26%", "74%"]))

add(co("good", "The pattern worth noticing",
  "Six of the ten are the same principle violated in different clothes: a slice taken after the fact, a window mutated in place, a cut recomputed, a formula reimplemented, a standardisation over the whole series, a weight persisted nowhere. Walk-forward purity is not a stylistic preference — it is the property that fails most often, and it fails quietly."))

add(ch("The mathematics, worked through"))
add("""
<p>Every metric this plan leans on can be computed by hand on a short series, and doing so once is the difference between trusting a number and recognising it. This chapter works four of them through on the same small example.</p>
<h3>The example series</h3>
""")
add(pre("""actual prices over the graded window:   100.0  101.0  100.5  102.0
forecast paths:
  model A                               100.0  100.8  101.2  101.6
  model B (flat)                        100.0  100.0  100.0  100.0"""))
add("<h3>MAPE, step by step</h3>")
add(pre("""error_i = |actual_i − forecast_i| / |actual_i|
model A: 0.00%  0.20%  0.70%  0.39%      mean = 0.32%
model B: 0.00%  0.99%  0.50%  1.96%      mean = 0.86%"""))
add(co("ex", "What it tells you, and what it does not",
  "Model A looks better on MAPE. But the first term is zero for both because both start at the same price — every path does, by construction. That is why MAPE alone never decides a promotion: it rewards the anchor both models share."))
add("<h3>Skill versus flat</h3>")
add(pre("""MAE(model)  = mean |actual − model|   A: 0.325   B: 0.885
MAE(flat)   = mean |actual − actual_0|  = 0.885
skill_A = 1 − 0.325/0.885 = +0.63        skill_B = 0"""))
add(co("law", "Why this is the metric that decides",
  "It answers the only question a trader asks: would I have been better off assuming the price stays where it is? A model with a positive skill is adding something; a model with zero skill is a flat line with extra steps. Negative skill is worse than doing nothing, and it happens."))
add("<h3>R², and why it flatters</h3>")
add(pre("""R² = 1 − SS_res / SS_tot
SS_tot over the window above ≈ 6.13 (the window's own spread, not the forecast's)
model A: SS_res ≈ 0.53 → R² ≈ 0.91
model B: SS_res ≈ 3.13 → R² ≈ 0.49"""))
add(co("warn", "The trap in one sentence",
  "R² is large whenever the window moved more than the model erred — which is most windows, for almost any model, because price windows are spread out. A useless model can score 0.97 on a trending window. That is why the comparison table shows MAPE and coverage beside it, and why the promotion gate reads skill versus flat."))
add("<h3>Coverage</h3>")
add(pre("""advertised 90% band, four outcomes:
  inside / inside / outside / inside  →  achieved 75%"""))
add(co("note", "Why four points are not enough",
  "Coverage needs tens of resolved windows before it means anything, which is why the exit gate asks for thirty days. On four points you learn that the band exists; on thirty days you learn whether it tells the truth."))

add(ch("Operating the platform"))
add("""
<p>The operations of a platform like this are mostly mundane, and the moments that are not mundane are exactly the ones worth writing down. This chapter is the runbook: what runs, what to check, and what to do when a number looks wrong.</p>
<h3>Starting, in order</h3>
""")
add(pre("""rabbitmq-server -detached
backend/bin/server $(cat backend/.server-args)                       # :8787
scalp5/core/bin/marketdata --port 8796 --top 400                     # :8796
scalp5/core/bin/refdata -data ./state/refdata -addr 127.0.0.1:8790 \
    -seed ./cmd/refdata/seed.json                                    # :8790
scalp5/core/bin/recorder --port 8801 --segments --data ../state/lake3 # :8801
python3 -u master.py --host 0.0.0.0 --port 8799                      # :8799"""))
add("<h3>The five checks, in the order that finds problems fastest</h3>")
add(table(["check", "command", "what a healthy answer looks like"], [
  ["platform alive", "<code>curl :8787/api/health</code>", "<code>{\"status\":\"ok\"}</code> with an uptime"],
  ["feed alive", "<code>curl :8787/api/ticker?symbol=BTCUSDT</code>", "rows with a price that changes between calls"],
  ["recorder alive", "<code>curl :8801/stats</code>", "per-topic counts, and a rate above zero"],
  ["journal growing", "<code>curl :8787/api/envelope/stats</code>", "a total that rises as forecasts run"],
  ["decisions fresh", "<code>curl ':8787/api/decision?symbol=BTCUSDT&amp;interval=1m'</code>", "a verdict or a refusal that names its reason"],
], ["20%", "42%", "38%"]))
add(co("warn", "The three symptoms that mislead",
  "<b>Flat charts</b> look like a model problem and are almost always a dead feed: the last price is held, so every move reads 0.00%. <b>An empty bus view</b> looks like an architecture problem and is always the recorder: it answered earlier because it was running. <b>A forecast that never returns</b> looks like a hang and is usually the store being empty for that pair and frame, so the request is downloading history."))
add("<h3>What to do when a number looks wrong</h3>")
add(table(["symptom", "first hypothesis", "the check"], [
  ["a metric improved overnight", "a leak was introduced", "the leak tests, then the cut arithmetic for that model"],
  ["two endpoints disagree about a pair", "different frames, or a stale packet", "compare <code>interval</code> and <code>computed_at</code>"],
  ["coverage collapsed", "a regime change, or a calibration bug", "the coverage telemetry per pair; if one pair, regime; if all, calibration"],
  ["the leaderboard reshuffled", "a model was graded on different data", "the parity harness and the verify handler's slice"],
  ["the journal stopped growing", "the recorder, or nothing publishing", "<code>/api/envelope/stats</code> against the orchestrator plan"],
], ["26%", "32%", "42%"]))


add(ch("The models, one by one"))
add(f"""
<p>Forty-nine models are registered. This chapter lists them with the two facts that decide where each can run and what it costs: whether it needs volume, and whether the platform or only the agent can execute it. The catalogue is served at <code>/api/forecast/models</code>, and the list below is that response.</p>
""")
add(table(["model", "family", "needs volume", "runs where"],
  [[f"<code>{m['name']}</code>", m["family"], "yes" if m["volume"] else "no",
    "agent only" if m["agent_only"] else "platform"] for m in MODELS],
  ["30%", "32%", "18%", "20%"]))
add(co("note", "How to read the column that matters",
  "“Needs volume” is not a limitation, it is a data dependency: on a pair with thin or missing volume the model is not run rather than run on zeros. “Runs where” is a parity statement — a model listed as platform-only has no GPU implementation, and a model in both must produce the same numbers in both, which the parity harness asserts on every cut."))

add(ch("Signals: the detector families"))
add(fig(28, "the alert lifecycle, as states: draft, suppressed, fired, confirmed, graded.", "the-alert-lifecycle-as-state"))

add("""
<p>A signal is a claim that something is happening, and the platform's detectors make four kinds of claim. Each has its own trigger, its own features and its own characteristic failure.</p>
""")
add(table(["kind", "the claim", "the trigger", "the characteristic failure"], [
  ["<b>P · pump</b>", "buying pressure is arriving faster than the pair's normal", "volume multiple, taker buy share, positive move, per-coin z", "a genuine news event, which looks identical and is not manipulation"],
  ["<b>D · dump</b>", "selling pressure is arriving faster than normal", "the mirror of the above", "a liquidation cascade, which is real and fast"],
  ["<b>R · reversal</b>", "an extension is being given back", "move against the prior window with flow confirmation", "a pause, which looks like a reversal for one window and then is not"],
  ["<b>B · breakout</b>", "the pair has left its range with participation", "range break plus volume and trade count", "a wick, which leaves and returns"],
], ["14%", "30%", "28%", "28%"]))
add(co("ex", "The same evidence, four readings",
  "A pair moving +0.8% on 6x volume with 78% taker buys, at the top of a two-hour range, is all four at once — and the platform's job is not to pick one label but to score the event, name the kind it most resembles, and grade the outcome so the labels earn their keep."))
add(table(["feature", "window", "baseline", "why this baseline"], [
  ["flow multiple", "10 s", "the pair's own quiet periods", "a market-wide baseline makes every alt look quiet during a BTC move"],
  ["trade-count burst", "10 s", "the pair's own median", "counts participants, which volume alone cannot distinguish"],
  ["taker imbalance", "10 s", "the pair's own mean", "direction of intent"],
  ["move z-score", "10 s", "the pair's own distribution", "0.8% is an event on one pair and noise on another"],
  ["cross-sectional rank", "10 s", "the whole universe at that moment", "separates a single-coin event from a market move"],
  ["book thinness", "snapshot", "the pair's own depth history", "where a market order would actually clear"],
], ["22%", "12%", "32%", "34%"]))

add(ch("The forecast view, screen by screen"))
add("""
<p>The dashboard is where the platform's honesty becomes visible. Each panel is a claim about what a reader needs, and each one can be judged by whether it answers its question without the reader doing arithmetic.</p>
""")
add(table(["section", "the question", "what it shows", "what would be wrong with it"], [
  ["parameters", "what am I asking for?", "pair, frame, horizon, history, model", "a default that hides the frame"],
  ["live engine", "is the GPU working?", "the current job, device, and the best-ever config per pair", "showing “idle” the same way as “no agent”"],
  ["data", "do we have history?", "bars held, span, freshness, required", "hiding a shortage behind a spinner"],
  ["history", "can this even be graded?", "bars required versus held, and the download control", "allowing a forecast that cannot be held back"],
  ["model picker", "which model, and how has it done?", "the catalogue grouped by family, with per-pair scores", "ranking by R² alone"],
  ["chart", "what does it expect?", "actual, forecast, cone, branches, whiskers", "a cone whose width is not explained"],
  ["comparison", "do the models agree?", "every model run on this pair, side by side", "showing one model and calling it a forecast"],
  ["backtest", "would this have worked?", "the same frame graded walk-forward", "grading in-sample and calling it a backtest"],
], ["16%", "24%", "34%", "26%"]))

add(ch("Data dictionary"))
add(table(["record", "field", "type", "meaning"], [
  ["bar", "<code>open_time</code> · <code>close_time</code>", "ms", "the bar's window"],
  ["bar", "<code>open</code> · <code>high</code> · <code>low</code> · <code>close</code>", "decimal", "the four prices"],
  ["bar", "<code>volume</code>", "decimal", "base-asset volume in the window"],
  ["packet", "<code>meta.version</code>", "int", "1 today; an unknown version is rejected at decode"],
  ["packet", "<code>meta.generation</code>", "int", "per (topic, key) publication counter"],
  ["packet", "<code>meta.cid</code> · <code>parent_cid</code>", "string", "identity, and the packet this one was made from"],
  ["packet", "<code>meta.computed_at</code>", "ms", "the freshness clock"],
  ["packet", "<code>meta.pipeline[]</code>", "array", "stage, rule, pass — the record of which checks ran"],
  ["packet", "<code>refs[]</code>", "array", "topic, key, generation, age_ms of each input"],
  ["decision", "<code>verdict</code>", "enum", "buy · sell · none · refused"],
  ["decision", "<code>edge_pct</code>", "percent", "the projected move the rule compared"],
  ["outcome", "<code>realised</code> · <code>move_pct</code>", "price · percent", "what the market actually did at the horizon"],
  ["outcome", "<code>correct</code>", "bool", "whether the directional call matched the move"],
], ["12%", "30%", "12%", "46%"]))

add(ch("The first second of a pump"))
add("""
<p>An abstraction is easiest to check against a concrete story, so here is one second of one pair, in the order the platform experiences it.</p>
<h3>t + 0 ms — the print arrives</h3>
<p>A trade prints on the venue's websocket. The feed process aggregates it into the current one-second bucket and appends to the 5-second and 30-second series. Nothing has been computed yet; the platform is recording what happened.</p>
<h3>t + 40 ms — the window closes</h3>
<p>The ten-second window closes and the detector scores it: flow multiple against the pair's own baseline, trade count, taker imbalance, move z-score, cross-sectional rank. Five numbers, four of them relative. The composite crosses its threshold and a <b>draft</b> alert exists in memory.</p>
<h3>t + 90 ms — suppression decides</h3>
<p>The pair fired four minutes ago with the same signature. Dedup suppresses it, and the alert leaks away without publishing. This is the difference between a detector and a firehose: most scores above threshold are the same event, counted again.</p>
<h3>t + 95 ms — the alert becomes a packet</h3>
<p>Were it not suppressed, the alert would publish on <code>alert.pumpdump</code> with its features as data and its candles as refs. The packet would be journalled, and a subscriber would render a card carrying the numbers that fired it.</p>
<h3>t + 400 ms — the dashboard draws it</h3>
<p>The card appears with price, move, flow multiple, z-score, aggressor share and window. Every number on it traces to the packet; none was computed in the browser.</p>
<h3>t + 60 s — the operator looks, or does not</h3>
<p>If a human opens it, the drill-down shows the last minute, the hour and the session, and the chart opens on the pair. If nobody looks, the alert still resolves later, because grading does not depend on attention.</p>
<h3>t + 15 min — the outcome window closes</h3>
<p>The price at the horizon is read from the bar store, the alert is graded, and the scorecard updates. The detection earned a hit or a miss, recorded, so the next threshold change can be judged against it.</p>
""")
add(co("note", "What the story demonstrates",
  "Four of the eight steps are recording, and only one is a model. That ratio is the design: a system that cannot remember what happened cannot learn from it, and the slowest part of the sequence is the human deciding whether to look — which is why the packets exist."))

add(ch("Limits: what the platform does not do"))
add(table(["limit", "the honest statement"], [
  ["it does not execute", "no order leaves the platform today. The DMA path is a plan, gated behind evidence."],
  ["it does not predict minute-scale pumps well", "the published ceiling for early warning is roughly 19% top-5; this plan states it rather than promising better."],
  ["it does not grade fills or PnL", "those packets are registered and silent; the third traceability question cannot be answered yet."],
  ["it does not claim an edge", "the replay scorecard currently shows directional calls that were wrong. That is the number, not an illustration."],
  ["it does not stream every venue", "one venue, one feed, one book tier today."],
  ["it does not tune itself", "promotion is a human act with an evidence file; no automated route flips a model into service."],
], ["30%", "70%"]))
add(co("law", "Why a limits chapter belongs in a plan",
  "A plan that lists only what will be built is a sales document. The limits are what a reviewer should hold the plan to: if any of these becomes untrue without this chapter being revised, the document has started lying."))

add(ch("Sequencing and dependencies"))
add("""
<p>The phases are not parallel ([[F:the-phase-dependency-graph]]). Each depends on the honesty of the one before it, and the dependencies explain why there is no shortcut to the engine phase.</p>
""")
add(table(["phase", "depends on", "because"], [
  ["correctness", "nothing", "it is the foundation everything else is measured on"],
  ["detection", "correctness", "a detector graded by a leaking backtest cannot be tuned"],
  ["envelope adoption", "correctness and detection", "nothing to publish until something produces packets"],
  ["forecasting edge", "envelope adoption", "coverage telemetry needs recorded, replayable packets"],
  ["speed", "forecasting edge", "optimising a wrong computation makes it wrong faster"],
  ["execution ladder", "everything above", "each rung grades the engine against the rung below"],
  ["the engine phase", "forecasting edge and envelope", "the five claims are all measured through the record"],
], ["24%", "30%", "46%"]))
add(fig(51, "the phase dependency graph: nothing downstream can be judged before what it depends on is honest.", "the-phase-dependency-graph"))

add(ch("Phase task lists"))
add("<p>Each phase broken into the tasks that make its exit gate reachable. The sizes are relative, not calendar: <b>large</b> means a task that will surface work nobody scheduled.</p>")
TASKS = [
  ("correctness", [("slice the verify handler and anchor at the cut", "small"), ("make sma a value, not a mutating closure", "small"), ("pass the cut into MLR and assert alignment", "small"), ("import production formulas into the sweep", "medium"), ("anchor the optimiser at the actual close; persist weights", "medium"), ("standardise on training rows only", "small"), ("one spec, three runtimes, parity in the gate", "large"), ("the undo-loop property test", "small"), ("the eight small fixes, one test each", "medium"), ("leak and parity harnesses in the gate", "medium")]),
  ("detection", [("feature store with an admission log", "large"), ("two-tier book feed ingested and journaled", "large"), ("the ranked suspect list", "medium"), ("precision and recall at the operating budget", "medium"), ("per-kind grading for P/D/R/B", "medium")]),
  ("envelope adoption", [("publish intel.signal from the detectors", "medium"), ("publish movers, ta and board", "medium"), ("the refs validation rule in authority", "small"), ("freshness refusals surfaced in the UI", "small"), ("replay parity per migrated endpoint", "medium")]),
  ("forecasting edge", [("coverage telemetry over thirty days", "medium"), ("weights fitted walk-forward and persisted", "medium"), ("conformal and ACI compared on coverage and width", "large"), ("the foundation model graded", "small")]),
  ("speed", [("cache keys carry the generation", "small"), ("incremental scoring with exact parity", "large"), ("the soak test for sweeps", "medium"), ("latency counters on a panel", "small")]),
  ("execution ladder", [("an intent packet from a decision", "medium"), ("risk gateway with limits by test", "large"), ("one venue adapter, reconciled", "large"), ("a ledger of fills, fees and slippage", "medium"), ("the divergence supervisor", "medium")]),
  ("the engine phase", [("the five measured claims, published", "large"), ("regime conditioning with held-out grading", "large"), ("an automated promotion evidence file", "medium")]),
]
for phase, items in TASKS:
    add(f"<h3>{phase}</h3>")
    add(table(["task", "size"], [[t, f"<b>{sz}</b>"] for t, sz in items], ["82%", "18%"]))

add(ch("Every route, categorised"))
add("<p>All 89 registered routes, in order. This is the platform surface; every claim in this document is checkable against one of them.</p>")
add(table(["route"], [
  ["<code>/api/agent/install</code>"],
  ["<code>/api/agent/python</code>"],
  ["<code>/api/agents</code>"],
  ["<code>/api/agents/heartbeat</code>"],
  ["<code>/api/agents/register</code>"],
  ["<code>/api/alerts</code>"],
  ["<code>/api/alerts/groups</code>"],
  ["<code>/api/alerts/history</code>"],
  ["<code>/api/alerts/scored</code>"],
  ["<code>/api/bus</code>"],
  ["<code>/api/client-error</code>"],
  ["<code>/api/client-errors</code>"],
  ["<code>/api/client-errors/clear</code>"],
  ["<code>/api/colors</code>"],
  ["<code>/api/compute</code>"],
  ["<code>/api/config</code>"],
  ["<code>/api/data/inventory</code>"],
  ["<code>/api/data/load</code>"],
  ["<code>/api/data/prune</code>"],
  ["<code>/api/decision</code>"],
  ["<code>/api/detectors</code>"],
  ["<code>/api/envelope/chain</code>"],
  ["<code>/api/envelope/recent</code>"],
  ["<code>/api/envelope/stats</code>"],
  ["<code>/api/envelope/topics</code>"],
  ["<code>/api/events</code>"],
  ["<code>/api/features</code>"],
  ["<code>/api/flow</code>"],
  ["<code>/api/flow/history</code>"],
  ["<code>/api/forecast/alert-quality</code>"],
  ["<code>/api/forecast/auto-config</code>"],
  ["<code>/api/forecast/backtest</code>"],
  ["<code>/api/forecast/configs</code>"],
  ["<code>/api/forecast/continuation</code>"],
  ["<code>/api/forecast/engine-control</code>"],
  ["<code>/api/forecast/features</code>"],
  ["<code>/api/forecast/forward</code>"],
  ["<code>/api/forecast/gpu-sweep</code>"],
  ["<code>/api/forecast/gpu-sweep-status</code>"],
  ["<code>/api/forecast/lab</code>"],
  ["<code>/api/forecast/live-chart</code>"],
  ["<code>/api/forecast/live-state</code>"],
  ["<code>/api/forecast/live-symbols</code>"],
  ["<code>/api/forecast/mlr</code>"],
  ["<code>/api/forecast/models</code>"],
  ["<code>/api/forecast/optimize</code>"],
  ["<code>/api/forecast/optimize-backtest</code>"],
  ["<code>/api/forecast/verify</code>"],
  ["<code>/api/graph/nodes</code>"],
  ["<code>/api/health</code>"],
  ["<code>/api/heat</code>"],
  ["<code>/api/history/coverage</code>"],
  ["<code>/api/history/download</code>"],
  ["<code>/api/impulse</code>"],
  ["<code>/api/instruments</code>"],
  ["<code>/api/klines</code>"],
  ["<code>/api/klines/latest</code>"],
  ["<code>/api/models</code>"],
  ["<code>/api/models/signals</code>"],
  ["<code>/api/moneyflow</code>"],
  ["<code>/api/movers</code>"],
  ["<code>/api/orchestrator/plan</code>"],
  ["<code>/api/orchestrator/tick</code>"],
  ["<code>/api/outlook</code>"],
  ["<code>/api/perf</code>"],
  ["<code>/api/regime</code>"],
  ["<code>/api/replay</code>"],
  ["<code>/api/screen</code>"],
  ["<code>/api/screen/refresh</code>"],
  ["<code>/api/settings</code>"],
  ["<code>/api/settings/apply</code>"],
  ["<code>/api/survey</code>"],
  ["<code>/api/survey/refresh</code>"],
  ["<code>/api/symbol</code>"],
  ["<code>/api/ticker</code>"],
  ["<code>/api/tools</code>"],
  ["<code>/api/turtle</code>"],
  ["<code>/api/turtle/allocation</code>"],
  ["<code>/api/turtle/config</code>"],
  ["<code>/api/turtle/equity</code>"],
  ["<code>/api/turtle/exposure</code>"],
  ["<code>/api/turtle/orders</code>"],
  ["<code>/api/turtle/rotations</code>"],
  ["<code>/api/turtle/session-log</code>"],
  ["<code>/api/turtle/settled</code>"],
  ["<code>/api/turtle/stats</code>"],
  ["<code>/api/turtle/trades</code>"],
  ["<code>/api/universe</code>"],
  ["<code>/api/zoom-presets</code>"],
], ["100%"]))

add("<h3>Example 15 — a registry row, and the refusal it produces</h3>")
add(pre("""registry row:
  name     intel.forecast
  plane    intel
  producer forecast, engine, agent
  ttl      60000 ms
  record   yes

an unregistered producer tries to publish:
  authority  refused
  detail     producer "web" may not write "intel.forecast"
  → system.error packet, stage authority"""))
add(co("ex", "Why the registry is enforcement, not documentation",
  "The row is the permission. A producer not listed cannot publish, and the refusal is itself a packet with the stage and the rule. That is what makes “who may write what” a property of the system rather than a convention in a review."))

add("<h3>Example 16 — what the bus carries, and what it does not</h3>")
add(pre("""recorder :8801/stats
  intel.forecast      1 925
  control.engine         35
  model.decision         11
  market.candle           0    ← never recorded: the bus is a state channel

a day of publishing, by plane:
  market   state only, 2 s TTL
  intel    recorded
  model    recorded
  control  recorded"""))
add(co("note", "The design decision in one line",
  "Market data is a state channel, not a journal. A price two seconds old is not history, and recording it would multiply the lake by the tick rate for no analytical gain. That is why <code>market.price</code> carries a two-second TTL and <code>record: false</code>."))

add("<h3>Example 17 — the orchestrator asking for work</h3>")
add(pre("""POST /api/orchestrator/tick?symbol=ETHUSDT
  requested 17 capabilities

  intel.signal   nothing recorded        → control.engine packet
  intel.flow     nothing recorded        → control.engine packet
  intel.ta       nothing recorded        → control.engine packet
  …
  each request is a packet on control.engine, so the ask is recorded too"""))
add(co("ex", "Why the ask is a packet",
  "Because a scheduler that cannot be replayed cannot be debugged. Six months later, “why did the platform compute seventeen things at 14:32?” has an answer, and the answer includes which capabilities were considered stale and by how much."))

add("<h3>Example 18 — a coverage row that fails its advertisement</h3>")
add(pre("""model    ema20 · BTCUSDT · 1h · horizon 12
band     90% advertised
achieved 61% over 412 resolved windows
width    ±0.42%

verdict  not promoted
reason   achieved coverage is 29 points below the advertisement"""))
add(co("warn", "What happens next, and what must not",
  "The model does not get relabelled as an “80% band” to make the number look right. It either gets recalibrated and re-measured, or it leaves the surface. This is the discipline the whole plan is built to make possible."))

add("<h3>Example 19 — a promotion gate, refused</h3>")
add(pre("""candidate   blend(trend50, damped80, analog10) · fitted weights
skill       +0.11 on 1h, −0.04 on 4h
coverage    88% at the 90% band (within tolerance)
parity      green
regimes     positive in trend, negative in chop

decision    refused
reason      skill is negative on the 4h frame
            the engine serves 1h and 4h"""))
add(co("ex", "The gate is per frame for a reason",
  "A blend that wins on the one-hour frame and loses on the four-hour frame is not a promotion candidate; it is a regime detector with extra steps. The engine serves both frames, so both frames decide."))

add("<h3>Example 20 — the shadow rung's evidence file</h3>")
add(table(["field", "content"], [
  ["rung", "shadow"],
  ["period", "the dates the shadow ran"],
  ["decisions", "count, and the share that were refusals"],
  ["replay parity", "every decision reproduced with the same rule"],
  ["freshness", "the share of decisions refused for staleness, and why"],
  ["divergence", "intent against simulated fill, per pair"],
  ["signature", "the operator's sign-off before paper opens"],
], ["24%", "76%"]))
add(co("good", "Why this file is the whole point of the ladder",
  "It is the difference between “we think it works” and “here is what it did for a month with no money at risk”. The ladder exists so that this artefact is produced before the first order, not after the first loss."))

add(ch("Trade-offs and the alternatives rejected"))
add("""
<p>Every architectural choice in this plan had an obvious alternative, and the reasons for rejecting them are part of the design. A plan that does not state its trade-offs invites the same argument twice.</p>
""")
add(table(["choice", "the alternative", "why it was rejected"], [
  ["RabbitMQ with one exchange per plane", "Kafka with one topic per stream", "the broker already carries the topology, and the envelope needs routing by plane and topic, not a replayable log at the broker — the journal is the log"],
  ["an append-only JSONL journal", "a relational table, or a time-series database", "a file replays in one pass, needs no schema migration, and a corrupt line is one line. The lake is where volume lives"],
  ["one packet shape, fixed", "a per-domain payload with a shared envelope", "the fixed shape is what makes every consumer identical and every refusal uniform"],
  ["walk-forward grading everywhere", "a single holdout split", "one split can be lucky; walk-forward is the only protocol that survives a regime change"],
  ["one model spec, three runtimes", "best-of-breed per runtime", "two implementations of one name is exactly how F4, F7 and F9 happened"],
  ["the ladder", "a staged rollout by percentage", "percentage rollout assumes the thing works and asks how much; the ladder asks whether, first"],
  ["replay as backtest", "a separate vectorised backtester", "a second implementation is a second truth — the entire audit found the cost of that"],
], ["26%", "30%", "44%"]))

add(ch("References and further reading"))
add("""
<p>The plan leans on several bodies of published work. These are the ones a reader should consult to check the claims about detection, calibration and execution.</p>
""")
add(table(["subject", "what to look for", "why it matters here"], [
  ["pump-and-dump detection", "measured precision/recall of early warning on crypto venues; per-coin anomaly features", "the ~19% top-5 ceiling quoted in the detection phase, and the feature families chosen"],
  ["forecast evaluation", "walk-forward protocols, skill scores versus a naive baseline, the pitfalls of R² on price levels", "the promotion criterion and the reason R² is never read alone"],
  ["conformal prediction", "split conformal quantiles, adaptive variants, coverage guarantees under distribution shift", "the band's claim, and the ACI mechanism"],
  ["market microstructure", "order-book imbalance, taker aggression, the information content of trade counts", "the detector features that are not price-derived"],
  ["execution", "slippage models, participation limits, kill-switch design in automated trading", "the risk gateway and the ladder's rungs"],
  ["reproducible research", "data lineage, provenance standards, auditability of computed results", "the envelope, the journal and the lineage walk"],
], ["22%", "44%", "34%"]))
add(co("note", "What is deliberately not cited",
  "Claims about a model's profitability, and any vendor comparison. None of them can be checked against this platform's record, and a citation that cannot be checked is decoration."))

add(ch("The plan on one page"))
add("""
<p>If a reader takes one page away, this is the page. Everything here appears elsewhere with its evidence; here it is compressed to what must be remembered.</p>
""")
add(table(["#", "the phase", "the one-line reason it exists", "the gate"], [
  ["1", "correctness", "ten defects make the numbers untrue", "leak and parity tests in the gate"],
  ["2", "detection", "onset is late and ungraded", "a ranked list with a published precision curve"],
  ["3", "envelope adoption", "nineteen topics are silent", "every forecast and detection publishing; shape unchanged"],
  ["4", "forecasting edge", "bands that do not hold, blends that do not exist", "coverage telemetry; a promotion attempted honestly"],
  ["5", "speed", "the platform must be fast enough to act on", "parity under caching and incrementality"],
  ["6", "execution", "acting without evidence is how accounts die", "the ladder, one rung at a time, human-signed"],
  ["7", "the engine", "the platform should be the most accurate there is", "five measured claims, published"],
], ["6%", "20%", "40%", "34%"]))
add(co("law", "The sentence to remember",
  "The platform's value is not that it forecasts; it is that it can prove what it forecast, what it was based on, whether it was right, and what it did about it — for any moment in its history, from its own recorded evidence."))

add(ch("Appendix: the journal, sampled"))
add("""
<p>What the record actually contains, sampled while this document was written. These are the counts the platform reported, not estimates.</p>
""")
add(table(["topic", "packets in the journal", "what they are"], [
  ["<code>intel.forecast</code>", "1 925", "every graded forecast, with its refs"],
  ["<code>control.engine</code>", "35", "requests for work, from the orchestrator"],
  ["<code>model.decision</code>", "11", "verdicts, each naming its forecast"],
], ["30%", "20%", "50%"]))
add(co("note", "What the sample shows about the plan",
  "Almost two thousand forecasts and eleven decisions: the record is dense where the machinery is finished and silent where the producers are missing. That shape is the argument for Phase 3 in one line."))

add(ch("The model families, in formulas"))
add("""
<p>A family is defined by one line of arithmetic and one assumption. Stated plainly, a reader can see why the catalogue needs twelve of them and why the ensemble exists.</p>
""")
add(table(["family", "the formula, in words", "the parameter it adds"], [
  ["moving average", "the next value is the mean of the last <i>n</i> (or a weighted mean of them)", "<i>n</i>, the window"],
  ["exponential", "the next value is a blend of the last value and the last forecast, weighted α", "α, the smoothing"],
  ["damped trend", "the slope is added, but each step keeps only a fraction φ of the previous increment", "φ, the damping"],
  ["trend", "the slope of a fitted line is projected forward", "the fit window"],
  ["volume", "the price is weighted by, or anchored to, where the volume went", "the volume window"],
  ["analogue", "find the <i>k</i> most similar past windows and follow what happened next", "<i>k</i> and the similarity metric"],
  ["statistical", "fit an autoregressive or regression structure and project it", "order and differencing"],
  ["ensemble", "combine members with weights fitted on out-of-sample error", "the weights"],
], ["18%", "56%", "26%"]))
add(co("note", "Why the parameters are the interesting part",
  "Every family reduces to one or two parameters, and every one of those parameters is a place where a sweep can find a better fit and a leak can hide. That is why the optimiser's persistence (F5) and the sweep's formula parity (F4) matter more than adding a thirteenth family: a new family with an unswept parameter is a new way to be wrong."))

add("<h3>Example 21 — a sweep job, and what comes back</h3>")
add(pre("""POST /jobs
  symbol      BTCUSDT   interval 1h   horizon 12
  grid        lookbacks 2..50 · windows 10,20,40,100 · 49 models
  device      cuda

result
  candidates  56 configurations survived the walk-forward grade
  best        vwtrend · lookback 50 · window 600 · r2 0.9756
  parity      green on the CPU executor"""))
add(co("ex", "What to read in it",
  "Fifty-six survivors out of a very large grid is the selection pressure doing its job; a sweep where everything survives is a sweep that graded nothing. The parity line is the part that keeps a GPU result meaningful: the same job on the CPU executor must agree, or the GPU is running a different formula."))

add("<h3>Example 22 — a retention decision, in numbers</h3>")
add(table(["surface", "what is kept", "for how long", "why that long"], [
  ["bar store", "candles per symbol and frame", "until pruned by the data panel", "forecasts need history; the exchange can refill it"],
  ["journal", "every packet", "indefinitely, append-only", "the audit trail is the product"],
  ["lake bronze", "raw packets", "per plane policy", "reprocessing without re-ingesting"],
  ["lake silver", "decoded payloads", "longer than bronze", "queries do not need the envelopes"],
  ["lake gold", "aggregates", "longest", "dashboards and research read this"],
], ["14%", "34%", "24%", "28%"]))
add(co("note", "The rule behind the table",
  "Anything that can be rebuilt from the exchange is prunable. Anything that records what the platform <i>decided</i> is not. That single distinction is why the journal is never pruned and the bar store is."))

add("<h3>Example 23 — a decision refused for a reason that is not an error</h3>")
add(pre("""GET /api/decision?symbol=ETHUSDT&interval=1m

  verdict   none
  reason    edge inside the threshold
  edge      +0.004%
  floor     0.050%"""))
add(co("ex", "Why “none” is the most common correct answer",
  "Most of the time the honest answer is that the projected move is smaller than the cost of being wrong. A platform that produced a verdict every minute would be lying about its own confidence — and this number, the share of refusals, belongs on the dashboard next to the calls."))

add("<h3>Example 24 — the trace that proves the bus is doing its job</h3>")
add(pre("""producer  forecast  →  packet  intel.forecast  cid tel.forecast-…
broker    exchange intel   routing key intel.forecast
consumer  recorder :8801  →  lake bronze + journal index
reader    /api/envelope/recent?topic=intel.forecast  →  the same packet"""))
add(co("good", "What to do with the trace",
  "If the same packet appears at all four steps, the pipeline is intact and the only questions left are about the payload. If it disappears between the broker and the recorder, the fault is in one process and the bus view shows the gap. That is what traceability buys operationally: a broken chain tells you where, not just that."))

add(ch("The frontier: ideas nobody has shipped"))
add("""
<p>The phases make the platform honest; the engine phase makes it measured. This chapter is the part that could make it the most accurate forecasting platform in this market — because the things that would make it so are not better models, they are things this platform can do that others cannot, for structural reasons.</p>
<p>Each idea below is stated as a hypothesis with a first experiment and a way it can fail. None of them is promised; all of them are buildable on what already exists.</p>
""")
add(co("law", "The advantage nobody can copy quickly",
  "A competitor can copy a model. They cannot copy a journal of packets whose refs prove what was knowable at the time, because they did not record it. Every idea in this chapter is a use of that record — which means the moat is not the idea, it is the provenance."))

add("<h3>1 · The counterfactual engine: new models against the whole recorded past</h3>")
add("""
<p>Every model anybody thinks of can be run backwards over the journal — not over today's data, which is contaminated, but over the packets that record exactly which bars existed and at what age when a forecast was made. The result is retroactive paper trading with <b>zero look-ahead</b>, at a scale no backtest can match, because the refs prove the data availability rather than assuming it.</p>
<p><b>Why nobody has done it:</b> backtesters assume they know what data existed. This platform recorded it, per packet, as references with ages.</p>
""")
add(xfig(1, "the counterfactual loop: a new model is replayed over the recorded past, must reproduce what would have been live, and only then reaches the gate", "counterfactual"))
add(co("ex", "First experiment",
  "Take the four families that disagree most on a given pair, replay each over sixty days of recorded packets, and measure net skill per frame. If the counterfactual engine and the live path disagree on any packet, the run is void — parity is the entry fee."))

add("<h3>2 · Trade net skill, never gross skill</h3>")
add("""
<p>The number that pays is not accuracy; it is accuracy after fees, spread, slippage and staleness. A model can be 60% directionally right and lose money. Stated plainly: <b>net skill = gross skill − fees − spread − slippage − staleness</b>, and it is computed per pair, per frame, per horizon, from the record.</p>
<p><b>Why nobody has done it:</b> most systems optimise a statistical score and hope it survives contact with costs. Here the cost model is part of the metric, so a model that cannot pay is visibly unpayable before it trades.</p>
""")
add(xfig(4, "net skill: the only number that can pay, computed per pair, frame and horizon", "netskill"))
add(co("ex", "First experiment",
  "Rank the forty-nine models by gross skill and by net skill on the same pairs, and show the two rankings side by side. If they are nearly the same, this idea is worth little; if they differ — and on tight frames they will — the difference is the alpha."))

add("<h3>3 · Size by edge over band width, discounted by calibration error</h3>")
add("""
<p>Position size should be a function of <b>how far the projected move is from the band</b>, not of a fixed fraction. A forecast that projects a move three band-widths out deserves more than one that projects a third of a band — and the discount should be the pair's <i>historical coverage error</i>, because that is the model's own record of being wrong about its band.</p>
<p><b>Why nobody has done it:</b> sizing on predicted volatility is common; sizing on <i>the model's own miscalibration, measured per pair and frame</i>, requires a coverage telemetry loop that most systems do not have.</p>
""")
add(co("law", "The formula, in one line",
  "size ∝ (edge ÷ band width) × (1 − coverage error) ÷ cost of being wrong. Every term is measured from the record; none of them is a constant in a config file."))

add("<h3>4 · Staleness-aware execution: trade the edge that survives the delay</h3>")
add("""
<p>Every impulse has an edge half-life. Measure it from the journal — how much of the projected move was still available 100 ms, 500 ms and 2 s after the packet was computed — and refuse any trade whose remaining edge at send time is below cost plus slippage. The TTL becomes a parameter derived from measurement rather than a round number.</p>
<p><b>Why nobody has done it:</b> platforms tune latency; almost none <i>price</i> it, because pricing it needs the decay curve, and the decay curve needs packets that record when each number was computed.</p>
""")
add(xfig(3, "staleness-aware execution: the edge decay curve turns latency into a tradeable quantity", "staleness"))
add(co("ex", "First experiment",
  "For one pair and one frame, bucket recorded decisions by the age of the forecast they used, and plot realised edge against age. If edge does not decay, latency is not the constraint and this idea is cheap to reject."))

add("<h3>5 · Route per pair, per frame, online — with off-policy evaluation</h3>")
add("""
<p>Different families win on different pairs and frames, and the winner changes with regime. A router that picks the model per context, learns from graded outcomes, and — crucially — evaluates the models it did <i>not</i> pick using the journal, is a bandit with a proper off-policy estimator. Most systems cannot do the last part, because they never recorded what the alternatives would have produced.</p>
<p><b>Why nobody has done it:</b> off-policy evaluation needs a log of contexts, actions and outcomes at the packet level. This platform's journal is that log.</p>
""")
add(xfig(2, "model routing: context, choice, outcome, and an off-policy read of the paths not taken", "routing"))
add(co("warn", "The trap this must avoid",
  "A router that always picks the recent winner chases noise and will look brilliant for a week. The exploration budget must be a constant of the design, not an afterthought, and the router's own skill must be graded against the best single model — otherwise it is a complicated way to be average."))

add("<h3>6 · Stops placed by conformal quantiles, not by percentages</h3>")
add("""
<p>If the band is calibrated, the tenth percentile of the path is an empirically honest place for a stop: it is the level the model itself says the price should rarely cross. Stops become a property of the distribution rather than a number a trader likes.</p>
<p><b>Why nobody has done it:</b> it requires bands that hold their coverage — the thing the whole plan is built to measure.</p>
""")

add("<h3>7 · The platform's own telemetry as a regime feature</h3>")
add("""
<p>Refusal patterns are information. A spike in freshness refusals means the feed is late; a spike in edge-floor refusals means the market is quiet relative to the models' expectations; a change in the share of decisions that flip verdict within one forecast generation is a signature of regime instability. Feeding the platform's own operational record into the features is free, and nobody does it because nobody records it.</p>
""")

add("<h3>8 · Lead–lag graph, learned from the record</h3>")
add("""
<p>Build a directed graph of which pairs move first, from the recorded candles and ticks, and gate entries when the leader has already made the move. Buying the laggard after the leader's impulse is the most common way to be late and pay for it.</p>
""")

add("<h3>9 · Adversarial self-play for the detectors</h3>")
add("""
<p>Train a generator that produces synthetic pump sequences calibrated on the recorded detections, and use it to red-team the detector: every synthetic sequence that passes the detector is a false positive the adversary could have built. The journal supplies both the positive class and the calibration.</p>
""")

add("<h3>10 · Maker or taker, chosen by a forecast of the book</h3>")
add("""
<p>Forecast the book, not just the price: the shape ten seconds ahead decides whether a maker order fills or a taker order pays the spread. Choosing the order type from that forecast turns a cost centre into a source of PnL.</p>
""")

add(co("good", "Which three to build first",
  "<b>Net skill</b>, because it reorders everything else. <b>The counterfactual engine</b>, because it turns the record into an experimental platform overnight. <b>Staleness-aware execution</b>, because it is the cheapest to test and the answer is useful even if the answer is “latency is not our constraint”. The other seven are worth building in the order the first three tell you to."))
add(co("warn", "How this chapter could be wrong",
  "Every idea here assumes the record is trustworthy — one defect in the journal and all nine collapse. That is why correctness is Phase 1 and why the parity test is the entry fee for every experiment in this chapter. A frontier built on a broken tape measure is a story, not a platform."))

add(ch("List of figures"))
add("<p>Every figure in this document, in order, with the chapter it belongs to.</p>")
add(table(["#", "figure"], [(str(n), c) for n, c, _ in ORDER], ["8%", "92%"]))

add(ch("Reference"))
add("<h3>Metrics</h3>")
add(table(["metric", "definition", "how it is read"], [
  ["MAPE", "mean absolute percentage error of the path", "lower is better; comparable only within a frame and horizon"],
  ["coverage", "fraction of outcomes inside the advertised band", "must match the advertisement; below it, the band is decoration"],
  ["R²", "variance explained on price levels", "flattered by the window's own spread — never read alone"],
  ["skill vs flat", "does the model beat “the price stays here”?", "the honest comparison, and the promotion criterion"],
  ["edge", "projected move over the horizon, as a percentage", "compared against the decision floor"],
  ["age", "<code>now − computed_at</code>", "against the topic's TTL; the refusal quotes both"],
  ["generation", "per (topic, key) publication counter", "order by <code>computed_at</code> across frames, never by this"],
  ["cid / parent_cid", "identity, and the packet this one was made from", "the lineage walk answers “why”"],
], ["18%", "42%", "40%"]))
add("<h3>Topics and their TTLs</h3>")
add(table(["topic", "ttl", "recorded", "producers"],
  [[f"<code>{t['Name']}</code>", f"{t['TTLMS']} ms", "yes" if t["Record"] else "no", ", ".join(t["Producer"])]
   for t in TOPICS], ["32%", "18%", "12%", "38%"]))
add("<h3>The routes this plan touches</h3>")
add(table(["route", "what it gives"], [
  ["<code>/api/forecast/verify</code>", "the graded comparison the leaderboard reads — where F1 hid"],
  ["<code>/api/forecast/lab</code>", "the live model study — where F2 surfaced"],
  ["<code>/api/backtest</code> · <code>/api/forecast/optimize-backtest</code>", "walk-forward grading — F3 and F5"],
  ["<code>/api/decision</code>", "a verdict from one recorded forecast, with its parent cid"],
  ["<code>/api/replay</code>", "every verdict, re-derived and graded"],
  ["<code>/api/envelope/topics</code> · <code>recent</code> · <code>stats</code> · <code>chain</code>", "the registry, the newest packets, the counts, the lineage"],
  ["<code>/api/orchestrator/plan</code> · <code>tick</code>", "serve or recompute, and the request for work"],
], ["44%", "56%"]))
add("<h3>Glossary</h3>")
add(defs([
  ("envelope", "the fixed packet: data, meta, refs."),
  ("interceptor", "a stage that may only refuse, with a rule and the numbers."),
  ("operator", "the only component allowed to compute a payload."),
  ("transformer", "reshapes a packet without changing its meaning; in the code, normalisation only."),
  ("refs", "the inputs a packet consumed, by reference — the look-ahead firewall."),
  ("journal", "the append-only record of every packet."),
  ("lake", "the recorder's partitioned long-term store."),
  ("walk-forward", "training only on data strictly before the cut being graded."),
  ("parity", "the same model, in two runtimes, producing the same numbers."),
  ("rung", "a step on the execution ladder, gated by evidence and a human signature."),
  ("coverage", "the fraction of outcomes the band actually contained."),
  ("rung gate", "the evidence file plus the human signature that opens the next rung."),
]))

# ---------------------------------------------------------------------------
# Assembly. Pass one marks each chapter band with a unique token so the page it lands on can
# be found by exact text rather than guessed; pass two prints the real contents numbers.
def resolve_refs(html):
    """Prose says [[F:key]]; the reader sees "Figure 7"."""
    def repl(m):
        key = m.group(1)
        n = FIGNUM.get(key)
        if n is None:                       # keys are slugged from captions; match on prefix
            for k, v in FIGNUM.items():
                if k.startswith(key[:18]):
                    n = v
                    break
        return f"Figure {n}" if n else "the figure"
    return re.sub(r"\[\[F:([a-z0-9-]+)\]\]", repl, html)

def numbered_html(tokens):
    counter = [0]
    def repl(m):
        counter[0] += 1
        n = counter[0]
        mark = f' <span style="font-size:6pt;color:#b0b6bf">CH{n}MARK</span>' if tokens else ""
        return f'<h2 class="chapter"><span class="num">{n}</span>{m.group(1)}{mark}</h2>'
    return re.sub(r"<!--CH\|([^>]+?)-->", repl, resolve_refs("".join(B)))

TITLES = [(i + 1, t) for i, t in enumerate(MARKERS)]
OUT = os.path.join(DOCS, "CryptAlyst-Master-Plan.pdf")

def page_marks():
    from pypdf import PdfReader
    reader = PdfReader(OUT)
    pages = [" ".join((pg.extract_text() or "").split()) for pg in reader.pages]
    found = {}
    for i, text in enumerate(pages[1:], start=1):
        for n, _ in TITLES:
            if f"CH{n}MARK" in text and n not in found:
                found[n] = i
    return found

def assemble(page_of):
    body = numbered_html(tokens=False)
    entries = [(n, t, page_of.get(n, "")) for n, t in TITLES]
    cut = body.index('<h2 class="chapter">')
    body = body[:cut] + kit.contents(entries) + body[cut:]
    return kit.page("CryptAlyst Master Plan") + body + "</body></html>"

def build(page_of, tokens=False):
    body = numbered_html(tokens=tokens)
    entries = [(n, t, page_of.get(n, "")) for n, t in TITLES]
    cut = body.index('<h2 class="chapter">')
    body = body[:cut] + kit.contents(entries) + body[cut:]
    kit.build(
        title="CryptAlyst · Master Plan",
        subtitle="Master Plan",
        desc="Trading, models, signals, the envelope, speed, storage<br/>and the path to DMA — with the evidence for each claim.",
        body_html=kit.page("CryptAlyst Master Plan") + body + "</body></html>",
        out_pdf=OUT,
    )

build({}, tokens=True)     # pass one: every band carries its own marker
build(page_marks())        # pass two: the contents gets the pages the markers landed on
print("wrote", OUT, "| chapters:", len(TITLES))
