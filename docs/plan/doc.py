"""The master plan, written rather than converted.

Each chapter is my own prose over the facts of the plan: the ten verified defects, the five
principles, the six phases with their exit gates, the DMA ladder, and the reference material.
Every figure is one of the plan's own diagrams, redrawn.
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.dirname(HERE)
sys.path.insert(0, DOCS)
import buildkit as kit

def fig(n, caption):
    return kit.fig(os.path.join(HERE, f"f{n}.png"), caption)

def efig(n, caption):
    return kit.fig(os.path.join(HERE, f"e{n}.png"), caption)

table, ch, co, kpis, defs = kit.table, kit.chapter, kit.callout, kit.kpis, kit.defs

B = []
def add(*blocks):
    B.extend(blocks)

# ---------------------------------------------------------------------------
add("""
<h1>The CryptAlyst Master Plan</h1>
<p class="lede">Ten verified defects, six phases, and a ladder to automated execution that only climbs when the evidence says it may. This is the whole platform, written down: what it is, what is wrong with it, and the order in which that gets fixed.</p>
""")
add(table(["field", "value"], [
  ["Document", "CryptAlyst — Master Plan: forecasting, detection, and the path to DMA"],
  ["Version", "1.0 · plan only"],
  ["Status", "no part of this is implemented until the operator says go"],
  ["Audience", "the operator, the two agents, and anyone asked to change the platform"],
  ["Scope", "every runtime: the Go platform, the React dashboard, the Python fleet, the GPU agent"],
  ["Basis", "a line-by-line audit of every model, the backtest and the optimiser; a survey of the published state of the art in pump-and-dump detection; and the envelope as built"],
  ["Designed by", "Andrew John Preston"],
  ["Copyright", "© TekVolt Systems 2026"],
], ["22%", "78%"]))
add(co("note", "Reading order",
  "Chapter 1 states the argument and the three questions the platform exists to answer. Chapter 2 is the uncomfortable one: what the audit actually found. Chapter 3 is the contract everything else is built on — the envelope, and the three roles around it. Chapters 4 to 9 are the six phases, each with its exit gate. Chapters 10 to 13 cover the platform around the phases: data, compute, surfaces, operations, and the adversary. Chapter 14 argues the case, and says what would falsify it. Chapter 15 is reference material: metrics, routes, evidence, glossary."))

# ---------------------------------------------------------------------------
add(ch(1, "The argument"))
add("""
<p>This platform can already detect a pump, forecast a path, and grade itself. What it could not do, until the audit that produced this plan, was prove that any of those numbers were true. That is the whole argument: three things are wrong with forecasting tools of this kind, and this platform is in a position to fix them properly rather than to paper over them.</p>
<h3>Three failures that every tool in this market shares</h3>
<p><b>One: the numbers are not the numbers.</b> A model is trained on data that includes the bars it is then graded on. A sweep runs a formula that is not the formula the live path runs. An optimiser reports the error of a weighted blend anchored somewhere no runnable model anchors. Each of these looks like a small implementation detail and each one makes the leaderboard a work of fiction.</p>
<p><b>Two: the detection is either late or unmeasurable.</b> Onset detectors fire when the move is already visible; that is not a failure of code but of definition. And the detectors that do fire are rarely graded, so nobody can say what their precision is at any operating budget — which means nobody can size a position against them.</p>
<p><b>Three: nothing has lineage.</b> When an automated strategy buys, there is usually no stored chain from the order back through the signal, the forecast and the data that produced it. Post-mortems become archaeology.</p>
<h3>What is different here</h3>
<p>The platform already owns the antidote to all three, and it is not a better model. It is a <b>contract</b>: the envelope — fixed shape, four interceptors, append-only journal, replayable — already runs in <code>backend/internal/envelope/</code> and is tested. Backtest-as-replay, decisions-with-lineage and publish-your-own-coverage become registry rows and producers around that contract rather than new systems. That is what “registration over refactor” means in practice, and it is why this plan is cheap where it matters.</p>
""")
add(kpis([("10", "verified defects", "each with a test"),
          ("6", "phases", "each with an exit gate"),
          ("5", "principles", "that arbitrate conflicts"),
          ("1", "human key", "the last one, always")]))
add(fig(1, "Figure 1 — the four eras. Each layer answers a specific failure of the one before it."))
add("""
<h3>Where the platform came from</h3>
<p>None of this was designed on a whiteboard; it accreted in four eras, and the architecture is best understood as four answers to four failures.</p>
""")
add(table(["era", "what it was", "the lesson that survived"], [
  ["I · the scanner", "a Python pump-and-dump onset scanner over ten-second flow windows, firing P/D/R/B alerts", "per-coin anomalies beat absolute thresholds; latency <i>is</i> the product; an ungraded alert breeds superstition"],
  ["II · the platform", "one Go binary serving ingest, detectors, history and a React dashboard — and the import of the envelope from ATLAS", "a fixed packet shape makes market data first class, indicators auditable, and backtests identical to live"],
  ["III · two agents", "development under an explicit protocol, with lanes and standing rules earned by named incidents", "the rules exist because something once went wrong; they are not bureaucracy"],
  ["IV · the verification", "a line-by-line mathematical audit of every model, backtest and optimiser", "the platform's trust surface can be audited at all — and the audit found ten places where it was lying"],
], ["16%", "46%", "38%"]))
add(co("law", "The three questions the platform must answer",
  "For any moment in its history, in under a minute: <b>what did we believe, and why?</b> (the envelope chain) · <b>was it true?</b> (replay-graded outcomes) · <b>what did we do about it, and what happened?</b> (the order lifecycle, slippage, refusals). No forecasting product, pump detector or bot we know of answers all three. That — not any single model — is the claim this plan is engineered to earn."))
add(co("warn", "What this document is not",
  "It is not a promise that the models will start working. The audit found the maths mostly correct and the grading partly dishonest, which is a different problem with a different fix. Nothing here claims an edge; it claims that after Phase 4 the platform will be able to say honestly whether it has one."))

# ---------------------------------------------------------------------------
add(ch(2, "What the audit found"))
add("""
<p>Before any of this plan was written, every model in the catalogue was read against its documented intent, with look-ahead bias, off-by-one errors and division by zero audited line by line. The result is not a list of bugs; it is a map of where trust breaks. Two of the ten findings are critical, three are high, three medium, two low — and the pattern matters more than the severity: every serious defect sits in a place users look at and believe.</p>
""")
add(table(["#", "finding", "severity"], [
  ["<b>F1</b>", "The verify handler grades the volume-family models on a forecast computed over unsliced bars — the model sees every bar it is graded against, and the path is anchored on the last graded bar. Those graded errors are meaningless and the leaderboard ranks them on fiction.", "critical"],
  ["<b>F2</b>", "<code>smaValue</code> permanently mutates its captured window the first time a training window is shorter than <code>n</code>, so the live sweep computes sma80/100/150 as <b>sma60 on every cut</b>, for every symbol.", "critical"],
  ["<b>F3</b>", "MLR re-derives its cut internally instead of taking the walk cut from the backtest loop, so every MLR forecast is graded against a price one horizon too far ahead. All MLR backtest numbers are wrong.", "high"],
  ["<b>F4</b>", "The GPU sweep's “hybrid” and “vwdrift” are different formulas from the production models of those names. The live engine's R² and MAPE were earned by models nothing executes.", "high"],
  ["<b>F5</b>", "The optimiser anchors its blend at the regression's fitted last value while production anchors at the actual close; its reported MAE describes a model nothing can run, and tuned weights are never persisted.", "high"],
  ["<b>F6</b>", "The feature model standardises over the full series, so the mean and standard deviation encode bars after the cut — look-ahead by the strict walk-forward rule.", "medium"],
  ["<b>F7</b>", "The agent's CPU analogue fallback omits the step-0 anchor and drift-centring the Go and jax paths apply: the same model name returns different forecasts depending on which hardware ran it.", "medium"],
  ["<b>F8</b>", "The hybrid blend's undo loop subtracts a failed component's weight across the whole path, including indices where nothing was added, corrupting the tail.", "medium"],
  ["<b>F9</b>", "Go blends raw sma/ema levels while the agent blends converged paths — same catalogued name, two formulas.", "medium"],
  ["<b>F10</b>", "Small but real: <code>TrainFrom</code> reports the wrong window start; super's check loop drops the last anchor; split-conformal uses one order statistic below the textbook index; a handler can panic on empty results; out-of-range horizons are reset rather than clamped; calibration bins double-count; one division is unguarded; two UI labels describe a method the code does not use.", "low"],
], ["6%", "82%", "12%"]))
add(co("good", "What was verified correct",
  "The trend and damped OLS (with damping semantics confirmed), the EMA convention, the analog search windows (pinned by test), ARIMA (Hannan–Rissanen identification, BIC order selection, log-space forecasting with a naive fallback), the vwap/vwtrend/vwdrift formulas, MLR's walk-forward training rows, super's inverse-squared-error weighting with its keep-only-if-better guard, the backtest's cuts and its MAE/RMSE/MAPE/R²/skill formulas, the optimiser's counters and history, the calibration λ fit, the bootstrap fan, the ACI update rule, and every graded error metric."))
add("""
<p>That split — mostly correct, with a few devastating defects hiding exactly where users trust most — is the strongest argument for correctness coming first, and for the verification discipline becoming permanent rather than a one-off.</p>
<h3>The five principles</h3>
<p>Everything in this plan follows from five statements. When two sections seem to disagree, these arbitrate.</p>
""")
add(defs([
  ("Correctness before features", "no new capability ships on top of a number that lies. The leaderboard, the fan, the backtest panel and eventually the order ticket are worth exactly as much as the honesty of the numbers behind them."),
  ("Walk-forward purity", "a forecast for bars after the cut uses only bars strictly before it — including training slices, standardisation, model selection, similarity metrics and calibration. Four of the ten defects are this one principle in four costumes, so it becomes a review checklist and a set of mechanical leak tests rather than a virtue."),
  ("Displayed number = runnable model", "every MAE, R², MAPE and coverage on screen must equal the metric of a model the platform can actually execute, on the data it actually consumed. F4 and F5 are violations: they describe models that exist nowhere."),
  ("The envelope is the contract", "forecasts, detections, decisions and fills travel as envelopes. New capability is a registry row and a producer around the contract, never a change to it."),
  ("One spec, three runtimes", "the GPU, the agent and the platform are workers under a single model registry. Every model that exists in more than one runtime is generated from one spec and parity-tested in the gate. Hardware never changes what a model means."),
]))
add(co("law", "The meta-principle that outranks the five",
  "<b>The human holds the last key.</b> Model disables and re-enables, execution rungs and kill-switch resets are human acts, durable across restarts and audited. Nothing in this plan automates those, and nothing in this plan should ever be allowed to."))

# ---------------------------------------------------------------------------
add(ch(3, "The contract: the envelope and the engine bay"))
add("""
<p>Everything in this plan leans on one idea, so it gets a chapter before any phase. The envelope is not a message format. It is a fixed shape with three roles around it, and the contract only works because those roles are kept apart: the packet is immutable, the interceptors decide whether it may exist, the operator decides what it says, and the transformer decides what shape it takes on the way out.</p>
""")
add(efig(1, "Figure 1 — the fixed shape and the three roles around it. The left side never changes; the right side grows forever."))
add(table(["role", "the question it answers", "what it may never do"], [
  ["<b>interceptor</b>", "may this happen at all?", "compute a value, or refuse without quoting the rule and the numbers"],
  ["<b>operator</b>", "what is true?", "be anything other than deterministic for the same inputs and generation"],
  ["<b>transformer</b>", "what shape does it take?", "change the meaning — a transformer that can alter a verdict is an operator wearing the wrong hat"],
], ["16%", "30%", "54%"]))
add(efig(2, "Figure 2 — the interceptor chain. Each stage can only refuse; the operator is the first thing allowed to compute."))
add("""
<p>Four interceptors exist today: <b>authority</b> (is this producer registered for this topic), <b>keying</b> (has a keyed topic got its key), <b>correlation</b> (mint the cid, chain the parent) and <b>freshness</b> (<code>age = now − computed_at</code> against the topic's TTL). Two more belong in the chain, and I would not add more than two: every interceptor is a veto on the hot path, and a chain of ten is a system that refuses to work.</p>
""")
add(table(["proposed", "question", "why it earns its veto"], [
  ["<b>schema</b>", "does the payload decode into the declared shape?", "catches the class of bug that survives every type checker — valid JSON, wrong in meaning"],
  ["<b>budget</b>", "is this operator within its cost or rate?", "keeps a GPU sweep from starving a forecast during a volatile hour"],
], ["16%", "36%", "48%"]))
add(efig(3, "Figure 3 — operators compute; transformers reshape. Both end at the same packet shape."))
add("""
<h3>The operators, and the honest column</h3>
<p>An operator is the only thing in this system allowed to produce a number. The registry is the promise that it is the only producer allowed to write its topic — which makes the state of each row the most useful table in the document.</p>
""")
add(table(["operator", "output", "state"], [
  ["forecast", "<code>intel.forecast</code>", "shipped"],
  ["sweep", "<code>model.sweep</code>", "shipped, on the GPU agent"],
  ["decision", "<code>model.decision</code>", "shipped"],
  ["replay / backtest", "<code>intel.backtest</code>", "shipped"],
  ["control", "<code>control.engine</code>", "shipped"],
  ["detection", "<code>intel.signal</code>", "registered, <b>no producer</b>"],
  ["movers · ta · board", "<code>intel.movers</code>, <code>intel.ta</code>, <code>intel.board</code>", "registered, <b>no producer</b>"],
  ["fill · pnl", "<code>model.fill</code>, <code>model.pnl</code>", "registered, <b>no producer</b>"],
], ["28%", "46%", "26%"]))
add(co("warn", "The gap this table exposes, and why it is the cheapest work in the plan",
  "Six of the nine operators are registered and have nothing publishing them. Each already has a TTL, an authority rule and a slot in the orchestrator's schedule. Everything downstream — the replay scorecard, the detection evaluation, the decision trail — is starved by exactly those six rows. Phase 3 is, almost literally, filling them."))
add(efig(4, "Figure 4 — one packet's life. The journal append is the commit point; after it, nothing may fail the producer's request."))
add("""
<p>That last figure carries the only latency rule in the architecture: <b>before the journal append, a failure means the packet does not exist; after it, a failure means a consumer did not hear about it</b>, which replay can repair. That is why the bus is best effort and the journal is not.</p>
<h3>What this buys the rest of the plan</h3>
<p>Because the machinery exists, Phase 3 is producers rather than plumbing; because the TTL is declared per topic and enforced before compute, the execution gate in Phase 6 comes free; and because every alert is an envelope with the refs that produced it, Phase 2 gets its labelled dataset. Three phases are cheap for one reason: the contract was built before it was needed.</p>
""")

# ---------------------------------------------------------------------------
add(ch(4, "Phase 1 — make every number true"))
add(fig(22, "Figure 5 — the verify request lifecycle. This is the route F1 was hiding in: the model was graded on bars it had already seen."))
add("""
<p>The first phase fixes the ten defects and, more importantly, builds the machinery that would have caught them. No new capability ships until this is done: there is no point adding a detector to a platform whose grades are fiction.</p>
""")
add(table(["fix", "what changes", "how we know it worked"], [
  ["<b>F1</b>", "the verify handler slices the training window before computing; the path is anchored at the cut, never at the graded bar", "a leak test that fails if any graded index appears in the training slice"],
  ["<b>F2</b>", "<code>smaValue</code> stops mutating its captured window; a window is a value, not state", "a test that calls sma80 with a short window and then checks sma150 is still sma150"],
  ["<b>F3</b>", "MLR takes the cut from the loop instead of deriving its own", "MLR backtest numbers move to a new baseline, and the horizon offset test asserts alignment"],
  ["<b>F4</b>", "the sweep imports the production formulas rather than reimplementing them", "a parity test that runs both and asserts equality within tolerance, per model"],
  ["<b>F5</b>", "the optimiser anchors at the actual last close and persists its weights", "a saved config reproduces the reported MAE exactly when replayed"],
  ["<b>F6</b>", "standardisation uses training rows only", "a leak test on the feature z-scores"],
  ["<b>F7</b>", "one spec generates the Go, jax and CPU paths", "the parity harness, run on every cut"],
  ["<b>F8</b>", "the undo loop subtracts only where it added", "a property test on random failure positions"],
  ["<b>F9</b>", "one blend formula, generated from the same spec", "parity again — same name, same numbers"],
  ["<b>F10</b>", "the eight small ones, each with its own test", "the regression suite, which is the point"],
], ["8%", "56%", "36%"]))
add("""
<h3>Walk-forward purity as machinery, not virtue</h3>
<p>The principle is one sentence: a forecast for bars after the cut uses only bars before it. Turning it into code means a shared slice type that cannot be constructed past the cut, and a family of leak tests that fail loudly when a model reaches for data it should not see. Four of the ten defects are this principle in disguise, which is the argument for making it structural.</p>
<h3>The gate</h3>
""")
add(co("good", "Phase 1 exit gate",
  "All ten fixes merged, each with the regression test that fails without it; the two-language lint suite green (<code>gofmt</code>, <code>vet</code>, <code>go test</code> across every package; <code>tsc</code>, <code>audit-css</code>, <code>verify</code>, <code>render-smoke</code>, <code>smoke</code>); the leak tests and the parity harness running in the gate rather than by hand; and a written statement of what each defect did to a number a user saw."))
add(co("note", "Why this phase is not optional, and not short",
  "Two critical defects sat in the grading handler and the live sweep for as long as they did because nothing forced them into the open: the test suite exercised default lab configurations, which F2 did not affect. The lesson is not “write more tests”; it is “test the path the user actually reads”. That is why the leak and parity harnesses are exit criteria rather than nice-to-haves."))

# ---------------------------------------------------------------------------
add(ch(5, "Phase 2 — detection that can be measured"))
add(fig(27, "Figure 6 — the two-tier funnel: the cheap screen over the whole market, then the expensive book-aware pass over the suspects only."))
add("""
<p>Onset detection fires when the move is visible. That is a definition, not a bug, and it is why onset alerts are worth less than they look. Phase 2 moves the platform from <i>“tell me when it has started”</i> to <i>“rank the suspects, seconds before the signal”</i> — and, just as important, makes the ranking gradeable so its precision at any operating point can be stated.</p>
<h3>The features the literature has already proven</h3>
""")
add(table(["feature family", "what it measures", "why it is in the list"], [
  ["per-coin z-scores", "deviation from that coin's own behaviour", "the single most reproduced finding: absolute thresholds do not transfer between pairs"],
  ["trade-count bursts", "trades per window against baseline", "a pump is a burst of participants, not only a burst of volume"],
  ["taker imbalance", "which side crossed the spread", "the direction of intent, and the cheapest feature to compute honestly"],
  ["book shape", "depth, spread, and where it thins", "the one new infrastructure piece: a two-tier order-book feed"],
  ["cross-sectional rank", "how unusual this coin is today", "separates a market-wide move from a single-coin event"],
], ["24%", "30%", "46%"]))
add(co("warn", "The number nobody wants to print",
  "Minute-scale early warning of a pump, measured across the published literature, is roughly <b>19% top-5 precision</b>. This plan says it in print rather than promising a number it cannot defend. The deliverable of Phase 2 is a <b>ranked suspect list with a measured precision curve</b>, not a promise that the list is right."))
add("""
<h3>What “measurable” means here</h3>
<p>Every detection is an envelope with the refs that produced it, so grading is not a separate project: a detection resolves to an outcome the same way a forecast does, and the journal supplies the labels. That is the second reason Phase 3 follows immediately after this one — the envelope is what makes detection quality a number instead of an opinion.</p>
<h3>The gate</h3>
""")
add(co("good", "Phase 2 exit gate",
  "A ranked suspect list live on the dashboard, graded by replay, with a published precision/recall curve at the operating budget; the two-tier book feed ingested and journaled; every feature computed under the walk-forward rule with its own leak test; and a written comparison against the published baselines — including the cases where we do worse."))

# ---------------------------------------------------------------------------
add(ch(6, "Phase 3 — the envelope does the work"))
add(fig(33, "Figure 7 — the topic registry as a map: planes down the side, producers across the top, and the six rows with nothing publishing them still empty."))
add("""
<p>Phase 3 is where the contract stops being architecture and starts being how the platform remembers. Forecasts and decisions become first-class packets, backtests become replays, and freshness becomes the gate in front of execution. Almost all of the machinery exists; the work is producers, consumers and one interceptor rule.</p>
<h3>The rule that operationalises the whole idea</h3>
""")
add(co("law", "The refs validation rule",
  "A forecast packet must cite the candle generation it consumed. If its <code>refs</code> are stale (age beyond the topic TTL) or its generation does not match the platform's current candle generation for that key, the packet is <b>refused at the authority interceptor</b>. That single rule is the platform-wide look-ahead firewall: a forecast cannot claim data it did not have."))
add("""
<h3>What each operator must publish</h3>
""")
add(table(["packet", "who publishes it", "what it makes possible"], [
  ["<code>intel.forecast</code>", "the forecast path, on every graded forecast", "replay as backtest; the decision trail"],
  ["<code>intel.signal</code>", "the detectors", "graded detection quality; the suspect list"],
  ["<code>intel.movers</code> · <code>intel.ta</code> · <code>intel.board</code>", "the market-intel operators", "a dashboard that reads numbers with provenance"],
  ["<code>model.decision</code>", "the decision path", "why we bought, traceable to one forecast"],
  ["<code>model.fill</code> · <code>model.pnl</code>", "the execution path, when it exists", "the third question: what did we do, and what happened"],
], ["34%", "26%", "40%"]))
add("""
<h3>The gate</h3>
""")
add(co("good", "Phase 3 exit gate",
  "Every forecast and detection path publishing; replay-parity tests green for each migrated endpoint; freshness refusals visible in the UI; and <b>zero packet-shape changes</b>, asserted by the existing envelope tests. Nothing here is allowed to alter the contract — the phase is judged on producers, not on edits to the packet."))

# ---------------------------------------------------------------------------
add(ch(7, "Phase 4 — the forecasting edge"))
add(fig(6, "Figure 8 — the forecast picture: the cut, the holdout, and the horizon. Walk-forward purity is the claim that nothing right of the cut was visible when the path was made."))
add("""
<p>This is the phase everyone assumes comes first. It comes fourth for a reason: an edge measurement is worth nothing if the tape measure is wrong, and after Phase 1 it finally is not. Phase 4 has four jobs — make the bands tell the truth, make the blends runnable, make the features leak-free, and grade everything including the foundation model.</p>
<h3>Calibrated bands that hit their coverage</h3>
<p>A 90% cone should contain the outcome 90% of the time. Today nobody can say whether it does, because coverage is not measured. Phase 4 makes it a first-class metric with telemetry and a promotion rule: a model whose achieved coverage is materially below its advertised coverage cannot be promoted, whatever its MAPE.</p>
<h3>Blends that a runtime can actually execute</h3>
<p>F4, F5 and F9 all say the same thing in different places: the number on screen belonged to a model nobody could run. The fix is structural — one model spec generating every runtime's kernels — and it is checked by a parity harness in the gate rather than by reading the code carefully at the end.</p>
<h3>Leak-free features, and an admission log</h3>
<p>Features enter through a store with an admission log: each candidate records the data it saw, under which cut, and whether its skill survived a holdout. The log is the answer to “why is this feature here?” — the same question the envelope answers for numbers.</p>
<h3>The foundation model gets graded too</h3>
<p>An agent-only model (<code>timestral</code>) has been exempt from grading by accident of where it runs. Exemption is how a model becomes folklore. It gets graded like everything else, on the same cuts, with the same metrics.</p>
<h3>The gate</h3>
""")
add(co("good", "Phase 4 exit gate",
  "Coverage telemetry live with at least 30 days of resolved windows; a tuned blend promoted — or honestly failed to promote — through the full gate at least once; <code>timestral</code> graded; the feature store showing at least one candidate evaluated; and the parity harness green across runtimes."))

# ---------------------------------------------------------------------------
add(ch(8, "Phase 5 — efficiency, without lying"))
add(fig(21, "Figure 9 — one spec, every runtime. The GPU, the agent and the platform generate the same kernels from one description, which is what makes parity a test rather than a hope."))
add("""
<p>Efficiency work is where honesty usually dies quietly: a cache that returns a slightly different series, an incremental scorer whose updates drift from the full computation, a sweep that starves the live path it was supposed to serve. Phase 5 is therefore defined by constraints as much as targets.</p>
""")
add(table(["work", "the constraint that keeps it honest"], [
  ["one model spec → every runtime's kernels", "parity in the gate, per model, on every cut"],
  ["cached series", "a cache key includes the generation; a stale key misses rather than returns"],
  ["incremental scoring", "incremental and full results must agree exactly on every cut — a test, not an intention"],
  ["GPU sweeps", "a soak test replays a busy window and asserts the live paths were never starved"],
  ["bundle size and paint", "measured on the dashboard, not inferred from a bundler report"],
], ["34%", "66%"]))
add(co("good", "Phase 5 exit gate",
  "Cache-reuse above target on a scripted session; incremental-versus-full parity green; a soak test proving sweeps never starve live paths; bundle sizes within target; and every counter published on an operations panel — because an efficiency claim that is not on a panel is a rumour."))

# ---------------------------------------------------------------------------
add(ch(9, "Phase 6 — the ladder to live execution"))
add(fig(44, "Figure 10 — the evidence ladder. Each rung is a claim, and each claim needs the evidence file of the rung below it."))
add("""
<p>Automated execution is not a feature that gets switched on; it is a ladder that gets climbed, one rung at a time, with evidence at each rung and a human signature before the next. The purpose of the ladder is to make it structurally hard to lose money faster than the platform can learn.</p>
""")
add(table(["rung", "what runs", "what must be true before the next rung"], [
  ["<b>shadow</b>", "the engine decides, nothing is sent", "decisions reproduce on replay; refusal reasons are visible"],
  ["<b>paper</b>", "orders are simulated against the recorded book", "slippage assumptions match the recorded fills within tolerance"],
  ["<b>risk gateway</b>", "the gateway sits in front of a live adapter, refusing everything", "every limit is enforced by test, including the kill switch"],
  ["<b>adapter</b>", "one venue, smallest allowed size", "a full week with no unexplained deviation between intent and fill"],
  ["<b>small live</b>", "one venue, a capped notional", "realised PnL tracks the shadow within the stated band"],
  ["<b>scaled</b>", "the cap rises with evidence", "the human signs each rise, durably and audibly"],
], ["16%", "34%", "50%"]))
add(co("law", "The rules that do not bend at any rung",
  "A kill switch that a human resets and no process can. A daily loss limit enforced in the gateway rather than in the strategy. Position and notional caps that are configuration, not code. Freshness checked before the order is built, not after. And the one standing rule from the repository: <b>re-enabling anything, or raising a cap, is a human act</b>, durable across restarts, written to the audit trail."))
add("""
<h3>What the ladder is not</h3>
<p>It is not a schedule. No date in this plan is a commitment to be live; the rungs are gated on evidence, and the honest outcome of a rung is sometimes “not yet, and here is the number that says so”. That is the same discipline Phase 1 applies to the leaderboard, applied to the platform's own ambition.</p>
<h3>The gate</h3>
""")
add(co("good", "Phase 6 exit gate, per rung",
  "The evidence file for the rung appended to the journal; the risk limits proven by test; the kill switch exercised, not described; and the human's signature on the rung in the audit trail. Nothing climbs without all four, and nothing climbs automatically."))

# ---------------------------------------------------------------------------
add(ch(10, "The platform around the phases"))
add(fig(9, "Figure 11 — the service and port map: six processes, one machine, and the network where the GPU workers live."))
add("""
<p>Six phases change behaviour; four areas change the platform they run on. None of them is a phase, because each is a standing requirement rather than a delivery.</p>
<h3>Data</h3>
<p>Four surfaces, each with one job: the bar store answers forecasts, the journal answers questions about what happened, the lake holds everything durably, and the GPU results are ephemeral. The rule that keeps them coherent is that a number a user sees must be reproducible from one of them — which is why the journal is append-only and the bar store is line-oriented.</p>
<h3>Compute</h3>
<p>The platform, the agent and the GPU are three tiers of one registry. A model that exists in more than one tier is generated from one spec and parity-tested. The fallback matters as much as the accelerator: with the GPU box off, the platform still scores locally, slower, and the board still moves.</p>
<h3>Surfaces</h3>
<p>The dashboard is where every honesty claim becomes visible: coverage against advertised coverage, freshness refusals, the suspect list with its precision, the decision trail for any past moment. A metric that is not on a surface is not being managed.</p>
<h3>Operations and security</h3>
""")
add(table(["concern", "the standing position"], [
  ["secrets", "none in the repository; broker credentials from the environment"],
  ["lanes", "the two agents have territory, and the git lane is the only writer"],
  ["the human gate", "phases, rungs and re-enables are human acts, audited"],
  ["observability", "health endpoints, the recorder's counters, the journal's counts, the gate's results"],
  ["recovery", "the journal replays; the lake rebuilds; nothing depends on in-memory state surviving"],
], ["24%", "76%"]))

# ---------------------------------------------------------------------------
add(ch(11, "The adversary"))
add("""
<p>A detection programme that assumes a static adversary is a detection programme that works for one quarter. This chapter is short on purpose: it states what a manipulator does to avoid us, and what we already hold that they cannot cheaply remove.</p>
""")
add(table(["evasion", "what it costs them", "what we already have"], [
  ["slower, longer accumulation", "time, and a bigger footprint in the book", "per-coin baselines over long windows"],
  ["wash trading to inflate volume", "fees, and a flat taker imbalance", "aggressor share and trade-count features, not just volume"],
  ["spreading across many pairs", "coordination cost, and a visible cross-sectional rank", "cross-sectional ranking, which is exactly a many-pair signal"],
  ["spoofing the book", "capital at risk of being hit", "two-tier book shape features, and journaled snapshots"],
  ["attacking the detector's parameters", "knowledge of them, which they do not have", "thresholds as configuration, changed without a deploy"],
], ["26%", "34%", "40%"]))
add(co("note", "The honest limit",
  "Every one of these counters is a cost imposed on the adversary, not an immunity. The plan's claim is narrower and defensible: the platform measures the cost it imposes, because every detection is graded and every grade is journaled. A detector that stops working shows up as a falling precision curve rather than as silence."))

# ---------------------------------------------------------------------------
add(ch(12, "Risk register and the decisions still open"))
add("""
<p>The register is the list of things that could make this plan wrong. Each entry names the risk, how it would show up, and the decision that is still owed.</p>
""")
add(fig(41, "Figure 12 — the order lifecycle the ladder eventually reaches: intent, risk gateway, adapter, fill, and the journaled packet at every step."))
add(table(["risk", "how it would show up", "decision owed"], [
  ["The models have no edge at all", "Phase 4's coverage telemetry never beats the flat baseline", "whether to keep the forecast surface as a measurement tool or retire the claim"],
  ["Detection precision stays near the published ceiling", "the suspect list sits at ~19% top-5 however it is tuned", "whether to trade a ranked list, or to sell it as context only"],
  ["The ladder's first live rung loses faster than shadow predicted", "deviation between intent and fill beyond the stated band", "whether to descend a rung, and by whose signature"],
  ["Filling six topics costs more judgement than expected", "each new producer surfaces grading work nobody scheduled", "whether to fill all six or the three that the scorecard needs"],
  ["The single-spec generation is more work than the parity it buys", "Phase 5 slips while parity keeps failing", "whether to accept two specs with a parity test, temporarily"],
  ["Volume of evidence exceeds what a human can review", "nobody reads the audit trail", "whether to publish a weekly digest, and what belongs in it"],
], ["30%", "34%", "36%"]))
add(co("warn", "The open decision this document cannot make for you",
  "Whether the platform is a <b>measurement instrument</b> or a <b>trading system</b>. Everything up to Phase 5 is defensible as the former and cheap; Phase 6 is only worth starting if the instrument has produced something worth acting on. That is a judgement about evidence, and it belongs to the operator."))

# ---------------------------------------------------------------------------
add(ch(13, "Why this could be best in class, and what would falsify it"))
add("""
<p>Best in class is not a claim about model quality. Any number of people can fit an ARIMA. The claim is narrower: <b>no forecasting product, pump detector or bot we know of can answer all three questions</b> — what did we believe and why, was it true, and what did we do about it — from its own recorded evidence, in under a minute, for any moment in its history.</p>
<h3>What makes that claim credible</h3>
""")
add(defs([
  ("It is checkable by a stranger", "the evidence is a journal of packets, not a dashboard of assertions. Anyone with the file can replay it."),
  ("It survives the models changing", "the contract outlives every model in this plan; replacing all forty-nine of them would not touch it."),
  ("It is cheap where it counts", "Phase 3 is producers, not plumbing, because the contract was built before it was needed."),
  ("It states its failures", "ten verified defects, a detection ceiling of ~19%, and a replay scorecard that shows the directional calls it got wrong. A document that only lists strengths is a brochure."),
]))
add("""
<h3>What would falsify it</h3>
<p>Three observations would do it, and any of them should be enough to make us stop claiming the above:</p>
<ul>
<li><b>The replay cannot reproduce the live path.</b> If a recorded forecast replayed through the same decision function reaches a different verdict, the lineage claim is empty.</li>
<li><b>Coverage never matches what the bands advertise.</b> If thirty days of resolved windows show a 90% cone containing 60% of outcomes, the forecasting surface is decoration.</li>
<li><b>Nobody can find the answer to “why did we buy?” in under a minute.</b> The claim is a user-facing one; if a person cannot do it, the architecture has failed regardless of what the journal contains.</li>
</ul>
""")
add(co("warning" if False else "warn", "The one claim I would not make",
  "That this plan produces a profitable strategy. It produces a platform that can tell you whether it has one — and the difference between those two sentences is the whole document."))

# ---------------------------------------------------------------------------
add(ch(14, "Reference"))
add("<h3>The metric dictionary</h3>")
add(table(["metric", "definition", "how it is read"], [
  ["MAPE", "mean absolute percentage error of the path", "lower is better; only comparable within a frame and horizon"],
  ["coverage", "the fraction of outcomes inside the advertised band", "a 90% band should hold 90%; below that the band is decoration"],
  ["R²", "variance explained, computed on price levels", "flattered by the window's own spread — never read alone"],
  ["skill vs flat", "does the model beat “the price stays here”?", "the honest comparison, and the one that decides promotion"],
  ["edge", "projected move over the horizon, as a percentage", "the decision floor compares against it"],
  ["generation", "a counter per (topic, key), incremented on publication", "order by <code>computed_at</code>, not by this, across frames"],
  ["age", "<code>now − computed_at</code>", "against the topic's TTL; the freshness refusal quotes both"],
  ["cid / parent_cid", "identity, and the packet this one was made from", "the lineage walk answers “why”"],
], ["16%", "44%", "40%"]))
add("<h3>The routes that matter to this plan</h3>")
add(table(["route", "what it gives the phases"], [
  ["<code>/api/forecast/verify</code>", "the graded comparison the leaderboard reads — the route F1 was hiding in"],
  ["<code>/api/forecast/lab</code>", "the live model study, and where F2 surfaced"],
  ["<code>/api/backtest</code> · <code>/api/forecast/optimize-backtest</code>", "walk-forward grading — F3 and F5"],
  ["<code>/api/decision</code>", "a verdict from one recorded forecast, with its parent cid"],
  ["<code>/api/replay</code>", "every verdict, re-derived and graded"],
  ["<code>/api/envelope/chain</code>", "the lineage walk"],
  ["<code>/api/orchestrator/plan</code>", "capabilities and generations: serve or recompute"],
], ["40%", "60%"]))
add("<h3>Glossary</h3>")
add(defs([
  ("envelope", "the fixed packet: data, meta, refs."),
  ("interceptor", "a gate that may only refuse, with a reason and the numbers."),
  ("operator", "the only component allowed to compute a value."),
  ("transformer", "reshapes a packet without changing its meaning."),
  ("refs", "the inputs a packet consumed, by reference — the look-ahead firewall."),
  ("journal", "the append-only record of every packet."),
  ("lake", "the recorder's partitioned long-term store."),
  ("walk-forward", "training only on data strictly before the cut being graded."),
  ("parity", "the same model, in two runtimes, producing the same numbers."),
  ("rung", "a step on the execution ladder, gated by evidence and a human signature."),
]))

# ---------------------------------------------------------------------------
# Contents, then build
titles = [t for t in __import__("re").findall(r'<span class="num">(\d+)</span>([^<]+)</h2>', "".join(B))]
toc = '<div class="toc">' + "".join(f"<div>{n} · {t.strip()}</div>" for n, t in titles) + "</div>"
B.insert(2, toc)

HTML = "".join(B)
OUT = os.path.join(DOCS, "CryptAlyst-Master-Plan.pdf")
kit.build(
    title="CryptAlyst · Master Plan",
    subtitle="Master Plan",
    desc="Ten verified defects, six phases, and a ladder<br/>to automated execution that only climbs on evidence.",
    body_html=kit.page("CryptAlyst Master Plan") + HTML + "</body></html>",
    out_pdf=OUT,
)
print("wrote", OUT)
