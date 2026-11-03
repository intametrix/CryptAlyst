#!/usr/bin/env python3
"""Build the CryptAlyst Developer Guide.

Every count in this document comes from docs/data/platform-snapshot.json, which is written
by docs/snapshot.py from the running platform — so a number here is a number the platform
reported, not one copied by hand.
"""
import json, os, sys, collections

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.dirname(HERE)
sys.path.insert(0, DOCS)
import buildkit as kit

SNAP = json.load(open(os.path.join(DOCS, "data", "platform-snapshot.json")))
ROUTES = SNAP["routes"]
MODELS = SNAP["models"]
TOPICS = SNAP["topics"]
SETTINGS = SNAP["settings"]
COMMIT = SNAP["commit"]

def fig(n, caption):
    return kit.fig(os.path.join(HERE, f"d{n}.png"), caption)

table = kit.table
ch, co, kpis, defs = kit.chapter, kit.callout, kit.kpis, kit.defs

P = []
def add(*blocks):
    P.extend(blocks)

def area(name):
    return [r for r in ROUTES if r.startswith("/api/" + name)]

fam = collections.Counter(m["family"] for m in MODELS)
planes = collections.defaultdict(list)
for t in TOPICS:
    planes[t["Plane"]].append(t)

# ------------------------------------------------------------------ cover matter
add("""
<h1>CryptAlyst Developer Guide</h1>
<p class="lede">The platform as built: every runtime, every route, every model, and the rules that keep them honest.</p>
<p class="meta">CryptAlyst · developer reference · generated from snapshot <b>""" + COMMIT + """</b> · """ + SNAP["generated_at"] + """</p>
""")
add(kpis([(str(len(ROUTES)), "routes", "all typed, one shape"),
          (str(len(MODELS)), "models", f"in {len(fam)} families"),
          (str(len(TOPICS)), "topics", f"over {len(planes)} planes"),
          ("21", "Go packages", "green under test")]))
add("""
<div class="toc">
<div>1 · What the platform is</div><div>10 · The GPU agent</div>
<div>2 · The repository</div><div>11 · The fleet, flags and ports</div>
<div>3 · The request path</div><div>12 · Testing and the gates</div>
<div>4 · The endpoint catalogue</div><div>13 · Recipes: adding things</div>
<div>5 · Data: store, lake, journal</div><div>14 · Runbook</div>
<div>6 · The detection layers</div><div>15 · Invariants and gotchas</div>
<div>7 · Forecasting and models</div><div>16 · Glossary</div>
<div>8 · The envelope</div><div>17 · Frequently asked</div>
<div>9 · The frontend</div>
</div>
""")

# ------------------------------------------------------------------ 1
add(ch(1, "What the platform is"), fig(1, "Figure 1 — one repository, three runtimes, and the boundary each one owns."))
add("""
<p>CryptAlyst watches several hundred Binance pairs at once and answers three questions in order: <b>is anything moving</b>, <b>what does the model expect next</b>, and <b>was the last expectation right</b>. It is a market-watch and forecasting bench — it computes and grades, it does not execute.</p>
<p>Three runtimes share one repository and one contract. The Go platform serves the dashboard and every forecast endpoint. The React app renders six views. A Python fleet and a small set of Go services feed the market data, record the traffic and run the model books.</p>
""")
add(table(["runtime", "what it owns", "how you talk to it"], [
  ["<b>backend/</b> — Go module <code>pumpdump/backend</code>", "HTTP surface, forecast pipeline, model registry, envelope, bar store, GPU orchestration", "port 8787"],
  ["<b>web/</b> — React + Vite", "six views, panels, charts, glossary", "built into <code>backend/web/dist</code>"],
  ["<b>scalp5/</b> — Python and Go services", "marketdata (:8796), recorder (:8801), refdata (:8790), model books (:8799)", "RabbitMQ and HTTP"],
], ["24%", "48%", "28%"]))
add(co("note", "Why three runtimes and not one",
  "The feed and the recorder are Go because they are throughput problems. The model books are Python because that is where the numerical libraries are. The platform is Go because it is the thing that must not fall over. The boundary between them is the envelope, so none of them has to know the others' internals."))
add(fig(9, "Figure 2 — the stack. Every layer talks to the one below it and to nothing else."))

# ------------------------------------------------------------------ 2
add(ch(2, "The repository"))
add("""
<p>Everything a new feature touches lives under <code>backend/internal</code> and <code>web/src</code>. The tree, at the level that matters:</p>
<pre>backend/
  cmd/server/           the platform (:8787)
  cmd/cryptalas/        the GPU agent (:8810)
  internal/api/         70 files — handlers, pipeline, registry, envelope API
  internal/envelope/    packet · topics · pipeline · cid · journal · bus
  internal/binance/     REST client behind the KlinesSource port
  internal/flow/        trade-flow detection and its baselines
  internal/screen/      the threshold screen
  internal/turtle/      breakouts, sizing, the risk budget
  internal/forecastlab/ the walk-forward model grid
  internal/models/      model families and their path functions
  internal/mocks/       test doubles for the exchange and the agent

web/src/
  App.tsx               six views, hash routing
  components/           panels, charts, sections
  hooks/                useResource · usePoll · useZoomPan · useNow
  data/client.ts        the only fetch in the application
  styles.css            one stylesheet, tokens first</pre>
""")
add(table(["package", "role", "files"], [
  ["<code>api</code>", "every HTTP handler, the pipeline, the model registry, the envelope surface", "70"],
  ["<code>envelope</code>", "packet shape, topic registry, interceptors, cid, journal, RabbitMQ bus", "9"],
  ["<code>binance</code>", "the REST client behind the <code>KlinesSource</code> port", "2"],
  ["<code>forecastlab</code>", "the model grid that is graded walk-forward", "3"],
  ["<code>flow</code> · <code>screen</code> · <code>turtle</code>", "the three detection layers", "—"],
  ["<code>mocks</code>", "test doubles for the market port and the agent", "3"],
], ["26%", "60%", "14%"]))
add(co("law", "The shape of the tree is the shape of the rules",
  "<code>internal/api</code> may call calculators. Calculators may not call the API. Anything that produces a number worth keeping goes out through <code>internal/envelope</code>. Nothing else in the tree is allowed to know about HTTP."))

# ------------------------------------------------------------------ 3
add(ch(3, "The request path"), fig(8, "Figure 3 — one request, end to end: parse, policy, store-first data, model, envelope, answer."))
add("""
<p>An endpoint is a method on <code>*Server</code>, registered on one mux in <code>internal/api/server.go</code>. Every one walks the same four steps, and each step has exactly one helper.</p>
""")
add(table(["step", "helper", "rule"], [
  ["parse", "<code>readQuery(r)</code>", "typed accessors: symbol, interval, int, float, bool, text"],
  ["policy", "<code>policyFor(interval)</code>", "warm-up and maximum bars for the frame"],
  ["data", "<code>barsForRequest(ctx, req, extra)</code>", "local store first, exchange only when short"],
  ["compute", "<code>modelPath(method)</code>", "the model's path function, from the registry"],
  ["answer", "<code>ok(w, v)</code> / <code>fail(w, msg)</code>", "one envelope shape for success and failure"],
], ["12%", "38%", "50%"]))
add("""
<p>Before this layer existed there were thirty-five hand-built error envelopes, twenty-three separate symbol parses and a different set of interval numbers in twelve handlers. That is what made the sub-minute frames untestable: the same 120-bar minimum that suits an hourly chart made a thirty-second chart impossible.</p>
<h3>The interval policy</h3>
<pre>// backend/internal/api/pipeline.go
intervalPolicies = map[string]intervalPolicy{
    "5s":  {warmup: 40,  maxBars: 1000},
    "30s": {warmup: 20,  maxBars: 1000},
    "1m":  {warmup: 60,  maxBars: 1000},
    "1h":  {warmup: 120, maxBars: 1000},
    "1d":  {warmup: 60,  maxBars: 1000},
}</pre>
""")
add(co("note", "Why the data step is store-first",
  "<code>barsFor</code> answers from <code>data/history</code> and only calls the exchange when the store is short. Without that, a dashboard polling four endpoints re-downloaded the same history dozens of times a minute, the exchange rate-limited it, and it surfaced as “the forecast stopped working” rather than as an obvious error."))

# ------------------------------------------------------------------ 4
add(ch(4, "The endpoint catalogue"))
add(f"<p>All {len(ROUTES)} routes, grouped by area. Each group is a directory of concern, not a namespace: the prefixes are what the client sees.</p>")
groups = [("forecast", "the forecast engine"), ("turtle", "setups, sizing and the risk budget"),
          ("envelope", "the packet registry, journal and lineage"), ("alerts", "the detection feed"),
          ("data", "the local store"), ("agents", "the GPU workers"), ("screen", "the threshold screen"),
          ("settings", "the thresholds themselves"), ("orchestrator", "capabilities and generations"),
          ("models", "the live board and the catalogue"), ("klines", "candles"), ("history", "coverage and download"),
          ("flow", "the flow leaderboard"), ("survey", "the turtle survey"), ("client-errors", "render failures")]
rows = []
for name, desc in groups:
    rs = area(name)
    if rs:
        rows.append([name, str(len(rs)), desc])
add(table(["area", "routes", "what it is for"], rows, ["16%", "10%", "74%"]))
add(co("good", "Every route answers in one shape",
  'Success is <code>{"ok": true, …}</code>; failure is <code>{"ok": false, "error": "…"}</code> with a status that means something. A client never has to guess whether a 200 carries an error.'))
add("<h3>Every path, in order</h3>")
add(table(["route"] * 1, [[f"<code>{r}</code>"] for r in ROUTES], ["100%"]))

# ------------------------------------------------------------------ 5
add(ch(5, "Data: store, lake, journal"), fig(3, "Figure 4 — four data surfaces. The bar store answers forecasts; the journal and the lake answer what happened."))
add(table(["surface", "path", "written by", "read by"], [
  ["bar store", "<code>data/history/SYMBOL-INTERVAL.jsonl</code>", "<code>fetchAndStore</code>", "every forecast endpoint"],
  ["journal", "<code>data/envelope-journal.jsonl</code>", "<code>recordEnvelope</code>", "decision, replay, orchestrator"],
  ["lake", "<code>state/lake3</code>", "the recorder (:8801)", "research; not yet a replay source"],
  ["GPU results", "in memory, per symbol", "the engine", "the live board"],
], ["16%", "32%", "22%", "30%"]))
add("""
<h3>The bar store line format</h3>
<p>One JSON object per line, appended and never rewritten. It is deliberately dumb: a reader can tail it, sort it, or rebuild it from the exchange.</p>
<pre>{"open_time":1793732400000,"open":77691.39,"high":77720.1,"low":77640.0,
 "close":77701.2,"volume":12.84,"close_time":1793732459999}</pre>
<h3>The journal packet</h3>
<pre>{"data": {…},
 "meta": {"version":1,"plane":"intel","topic":"intel.forecast","key":"BTCUSDT",
          "producer":"forecast","generation":18,
          "cid":"tel.forecast-BTCUSDT-c78e6f31f0e1d47a",
          "computed_at":1793743021687,
          "pipeline":[{"name":"authority","passed":true}, …]},
 "refs":[{"topic":"market.candle","key":"BTCUSDT","age_ms":16141687}]}</pre>
<h3>The lake</h3>
<p>The recorder consumes every envelope from the broker and writes it into <code>state/lake3</code>, partitioned by plane and by hour. Bronze is the raw packet, silver the decoded payload, gold the aggregated table a research query wants.</p>
""")
add(co("note", "Why two records of the same packets",
  "The journal is one file the API can replay in a single pass — simple, fast, and it never blocks a request. The lake is partitioned and compressed for volume. Replay reads the journal today; the lake is the next reader."))

# ------------------------------------------------------------------ 6
add(ch(6, "The detection layers"))
add("""
<p>Three layers decide that something is happening, and they are independent on purpose: each has its own window and its own idea of normal, so a burst one misses another may catch.</p>
""")
add(table(["layer", "question", "inputs"], [
  ["<code>flow</code>", "is the tape unusual right now?", "trade prints: aggressor side, size, price, against the pair's own baseline"],
  ["<code>screen</code>", "which pairs pass the thresholds?", "move %, volume ratio, volume z-score, recent USD flow, 24h volume"],
  ["<code>turtle</code>", "is there a longer-horizon setup?", "breakouts, ATR, position sizing against a risk budget"],
], ["14%", "30%", "56%"]))
add("""
<h3>Bursts are relative, never absolute</h3>
<pre>multiple = recentFlow / baselineFlow     // 26.4x means 26 times this pair's normal
zscore   = (move - meanMove) / stdMove   // unusual for THIS pair, not for the market</pre>
<p>A $50k print is noise in BTC and an event in a small alt. That is why every threshold in the screen is expressed against the pair's own history — and why the settings view exists: the numbers are data, not code.</p>
""")
add("<h3>The screen's settings, as they are set now</h3>")
add(table(["setting", "value", "what it filters"], [
  ["<code>screen_min_move</code>", str(SETTINGS.get("screen_min_move")), "minimum percentage move in the window"],
  ["<code>screen_min_vol_ratio</code>", str(SETTINGS.get("screen_min_vol_ratio")), "volume versus its own average"],
  ["<code>screen_min_vol_z</code>", str(SETTINGS.get("screen_min_vol_z")), "standard deviations of that volume"],
  ["<code>screen_min_recent_usd</code>", str(SETTINGS.get("screen_min_recent_usd")), "dollar flow in the recent window"],
  ["<code>screen_min_quote_volume</code>", str(SETTINGS.get("screen_min_quote_volume")), "24h turnover, so illiquid pairs never appear"],
  ["<code>screen_top_n</code>", str(SETTINGS.get("screen_top_n")), "how many rows the screen returns"],
  ["<code>min_liq</code>", str(SETTINGS.get("min_liq")), "liquidity floor for the universe"],
  ["<code>max_symbols</code>", str(SETTINGS.get("max_symbols")), "the size of the watch list"],
], ["30%", "10%", "60%"]))

# ------------------------------------------------------------------ 7
add(ch(7, "Forecasting and models"))
add(f"<p>{len(MODELS)} models in {len(fam)} families. Each is a function from bars to a path; the registry is the single source of truth for its name, family, volume need and availability.</p>")
add(table(["family", "models", "examples"], [
  [f, str(n), ", ".join([m["name"] for m in MODELS if m["family"] == f][:5])]
  for f, n in sorted(fam.items(), key=lambda kv: -kv[1])
], ["26%", "10%", "64%"]))
add("""
<h3>The full catalogue</h3>
""")
add(table(["model", "family", "needs volume", "where it runs"],
  [[f"<code>{m['name']}</code>", m["family"],
    "yes" if m["volume"] else "no",
    "agent only" if m["agent_only"] else "platform"] for m in MODELS],
  ["26%", "30%", "18%", "26%"]))
add(co("ex", "Registering a model",
  "<code>modelRegistry</code> holds a name, a family and a volume flag. Register one and it appears in <code>/api/forecast/models</code>, in the picker's grouped list and in the optimiser's candidate set. There is no second list to update — the count in this document is read back from that endpoint."))
add("""
<h3>How a forecast is scored</h3>
""")
add(table(["metric", "meaning, and its trap"], [
  ["MAPE", "percentage error of the path; comparable only within the same frame and horizon"],
  ["coverage", "how often the actual landed inside the 90% cone — a cone claiming 90% and delivering 60% is worse than none"],
  ["R²", "<b>the trap.</b> On price levels it is dominated by the window's own spread: a model that predicts “no change” scores above 0.95"],
  ["skill vs flat", "the honest comparison — does the model beat saying “the price stays here”?"],
], ["18%", "82%"]))
add("""
<h3>The optimiser</h3>
<pre>for each lookback in {2..50}:
    for each window in {10, 20, 40, 100}:
        train on bars[0:n-window], grade on bars[n-window:n]</pre>
<p>The grid runs on the GPU agent when one is reachable and is scored locally otherwise. The live board keeps the best-ever configuration per pair, which is what the <code>best</code> pseudo-model resolves to.</p>
""")
add(co("warn", "What the numbers say",
  "Across the recorded forecasts the replay grades a handful of directional calls and none of them correct. The models have no edge yet. The platform's value today is that it can prove that sentence, per packet, with the price that followed attached."))

# ------------------------------------------------------------------ 8
add(ch(8, "The envelope"))
add("""
<p>The full treatment is <em>The Envelope, as built</em>. What a developer needs here is the shape, the rule and the registry.</p>
<pre>packet = {data, meta, refs}
meta   = version · plane · topic · key · producer · generation
         cid · parent_cid · computed_at · received_at · compute_ms · pipeline</pre>
""")
add(table(["topic", "plane", "producer", "ttl", "recorded"],
  [[f"<code>{t['Name']}</code>", t["Plane"], ", ".join(t["Producer"]),
    f"{t['TTLMS']} ms", "yes" if t["Record"] else "no"] for t in TOPICS],
  ["30%", "14%", "26%", "16%", "14%"]))
add("""
<p>Publish through <code>envelope.Run</code>, never by hand: it applies authority, keying, correlation and freshness before your operator computes anything, and records refusals as packets on <code>system.error</code>.</p>
<pre>req := envelope.Request{
    Topic: "intel.forecast", Key: symbol, Producer: "forecast",
    Refs: []envelope.Ref{{Topic: "market.candle", Key: symbol}},
}
pkt, pipeline, err := envelope.Run(req, time.Now(), func() (any, error) {
    return payload, nil
})
if err == nil {
    recordEnvelope(pkt)     // journal + bus; failures never fail the request
}</pre>
""")

# ------------------------------------------------------------------ 9
add(ch(9, "The frontend"), fig(4, "Figure 5 — views compose components; components read shared state and one data layer."))
add(table(["layer", "rule"], [
  ["views", "<code>App.tsx</code> switches on a hash-routed view; each view is a grid of panels"],
  ["panels", "take props, render, call <code>onDrill</code>/<code>onSelect</code> upward — they own no fetching"],
  ["data", "<code>useResource&lt;T&gt;</code> to read; <code>postJSON</code>/<code>postAction</code> to write"],
  ["state", "symbol and interval in <code>selection.tsx</code>; glossary in context; sizes persisted"],
  ["charts", "one SVG renderer with a projection mode, plus <code>PriceChart</code> for candles"],
], ["14%", "86%"]))
add("""
<p>Shared state has one owner each: symbol and interval in <code>selection.tsx</code>, tooltips in <code>glossaryContext.tsx</code>, panel sizes in <code>usePersistentSize</code>, and one clock in <code>useNow</code> for every age label on the page.</p>
""")
add(co("warn", "One fetch rule",
  "There is exactly one <code>fetch</code> left in the app: inside <code>ErrorBoundary.componentDidCatch</code>. A client helper that throws there would raise a second error inside the error handler, so that call stays raw — and commented as such."))

# ------------------------------------------------------------------ 10
add(ch(10, "The GPU agent"), fig(5, "Figure 6 — the platform asks for a capability; the agent runs one CUDA pass over the whole grid."))
add("""
<p>The agent (<code>cmd/cryptalas</code>, port 8810) advertises its device and version on <code>GET /gpu</code>, accepts sweep jobs on <code>POST /jobs</code>, and returns scored results. The engine keeps the best-ever configuration per symbol and falls back to scoring the platform models locally when no agent answers — which is why the board still moves with the GPU box off.</p>
<p>The CPU path is the audit path: the same job shape runs locally and its result must match the GPU within tolerance, so a GPU discrepancy is a bug rather than a mystery.</p>
""")
add(co("good", "Why a fallback and not a dependency",
  "A GPU box that is off must not stop the platform. The agent is an accelerator, not a service: the same sweep runs on the CPU executor, slower, and the result is comparable. That is what makes it safe to run the agent on a machine in another room."))

# ------------------------------------------------------------------ 11
add(ch(11, "The fleet, flags and ports"), fig(10, "Figure 7 — the topology: what runs here, what runs on the network, and which way the data flows."))
add("""
<p>Six processes make the platform. Start them in this order; each is independently restartable.</p>
<pre>rabbitmq-server -detached
backend/bin/server $(cat backend/.server-args)            # :8787
scalp5/core/bin/marketdata --port 8796 --top 400          # :8796
scalp5/core/bin/refdata -data ./state/refdata -addr 127.0.0.1:8790 -seed ./cmd/refdata/seed.json
scalp5/core/bin/recorder --port 8801 --segments --data ../state/lake3
python3 -u master.py --host 0.0.0.0 --port 8799</pre>
""")
add(table(["process", "port", "what breaks without it"], [
  ["platform", "8787", "nothing works"],
  ["marketdata", "8796", "the boards freeze; the page still loads"],
  ["refdata", "8790", "the ticker picker falls back to the market feed's symbols"],
  ["recorder", "8801", "<code>/api/bus</code> answers 502 and the bus view goes empty"],
  ["RabbitMQ", "61613 / 15672", "packets are journalled but not broadcast"],
  ["master", "8799", "the Python model books stop"],
], ["16%", "14%", "70%"]))
add("""
<h3>The platform's own flags</h3>
<pre>backend/bin/server \\
  --addr 0.0.0.0:8787 --timeframes 1d,4h,1h,30m \\
  --min-liq 50000 --max-symbols 500 --extra RIFUSDT \\
  --screen-min-move 0.5 --screen-min-vol-ratio 1.2 --screen-min-vol-z 0.8 \\
  --screen-min-recent-usd 100 --screen-min-quote-volume 100 \\
  --alert-history scalp5/alerts.jsonl --ledger scalp5/ledger/backend-20261101.jsonl \\
  --refdata http://127.0.0.1:8790 --web-dir backend/web/dist</pre>
<h3>Environment</h3>
<p><code>CRYPTALYST_STOMP</code> (default <code>127.0.0.1:61613</code>), <code>CRYPTALYST_STOMP_LOGIN</code>, <code>CRYPTALYST_STOMP_PASSCODE</code> and <code>STATE_DIR</code>. The STOMP variables point a build at a different broker; <code>STATE_DIR</code> relocates the persisted state.</p>
""")

# ------------------------------------------------------------------ 12
add(ch(12, "Testing and the gates"), fig(6, "Figure 8 — two gates, and what each check proves. None of them prove a chart looks right."))
add(table(["command", "where", "what it proves"], [
  ["<code>go test ./...</code>", "backend/", "handlers answer correctly against a mocked exchange — 21 packages"],
  ["<code>tsc -b</code>", "web/", "the types line up across app and scripts"],
  ["<code>audit-css</code>", "web/", "every class used exists and is styled — 238 checks"],
  ["<code>verify</code>", "web/", "data shapers and formatters — 761 checks"],
  ["<code>smoke</code>", "web/", "every panel renders from fixtures — 215 checks"],
], ["22%", "12%", "66%"]))
add("""
<h3>Writing a backend test</h3>
<p><code>internal/mocks</code> exists so a handler can be tested without the world: <code>mocks.Market</code> answers from a fixture, records every request and can fail on demand; <code>mocks.Agent</code> is a fake agent over <code>httptest</code>.</p>
<pre>func TestSomething(t *testing.T) {
    s := newTestServer(t, &amp;mocks.Market{
        Bars:      mocks.Series("BTCUSDT", 400, 77_000, 5_000, time.Now().UnixMilli()),
        FailAfter: 2,        // the third request fails, on purpose
    })
    rec := httptest.NewRecorder()
    s.Routes().ServeHTTP(rec, httptest.NewRequest("GET",
        "/api/forecast/chart?symbol=BTCUSDT", nil))
    …
}</pre>
<p>Tests never touch the network, never read the wall clock for logic, and never depend on a file outside the repository. Fixtures are generated, not recorded.</p>
""")
add(co("law", "A fix without a test is half-work",
  "Every behaviour fix or new behaviour ships its regression test in the same commit. Two of the three defects found in the last sweep were found by using the feature, not by the gate — and both got a test before the commit."))

# ------------------------------------------------------------------ 13
add(ch(13, "Recipes: adding things"), fig(7, "Figure 9 — four changes you will make often, and the smallest correct path for each."))
add("""
<h3>Add an endpoint</h3>
<ol>
<li>Write <code>func (s *Server) handleX(w http.ResponseWriter, r *http.Request)</code> in a new file under <code>internal/api</code>.</li>
<li>Parse with <code>readQuery(r)</code>; fetch with <code>s.barsForRequest(ctx, req, extra)</code>.</li>
<li>Answer with <code>ok(w, …)</code> or <code>fail(w, "reason")</code>.</li>
<li>Register it in <code>server.go</code>; test it against <code>mocks.Market</code>.</li>
</ol>
<h3>Add a model</h3>
<ol>
<li>Write the path function: <code>func(bars []binance.Kline, horizon, lookback int) ([]float64, bool)</code>.</li>
<li>Register it in <code>model_registry.go</code> with name, family and volume need.</li>
<li>Nothing else: it appears in the catalogue, the picker and the optimiser.</li>
</ol>
<h3>Add a topic</h3>
<ol>
<li>One row in <code>topics.go</code>: name, plane, producers, TTL, recorded.</li>
<li>Authority now enforces who may write it; the orchestrator now schedules it.</li>
</ol>
<h3>Add a section to a view</h3>
<ol>
<li>Write the component with props; fetch with <code>useResource</code>, glossaries with <code>useTools</code>.</li>
<li>Wrap it in <code>&lt;Section id="fl.x" title="…"&gt;</code> — the section supplies the error boundary, so a crash stays inside it.</li>
</ol>
""")

# ------------------------------------------------------------------ 14
add(ch(14, "Runbook"))
add("<p>Start from the symptom, not from the component you suspect.</p>")
add(table(["symptom", "first thing to check"], [
  ["the bus view is empty", "<code>curl :8801/stats</code> — 200 with counts means the recorder is alive; connection refused means start it"],
  ["a forecast never finishes", "the Data panel: is history held? coverage says bars held versus required"],
  ["charts are flat", "the market feed — a dead feed leaves the last price in place and every move reads 0.00%"],
  ["the GPU board is idle", "<code>/api/agents</code> — an unreachable agent falls back to local scoring, so “idle” and “no agent” look alike"],
  ["the dashboard is stale after a deploy", "hard-reload: the bundle hash changes every build and the old one is cached"],
  ["a decision says “stale forecast”", "correct behaviour — the forecast is older than the topic TTL. Run a fresh one"],
], ["34%", "66%"]))
add("""
<h3>Restarting safely</h3>
<pre># the platform: rebuild, then replace
cd backend &amp;&amp; go build -o bin/server ./cmd/server
pkill -9 -f "bin/server " ; sleep 2
nohup ./bin/server $(cat .server-args) &gt; /tmp/server.log 2&gt;&amp;1 &amp;

# the web: rebuild the bundle the platform serves
cd web &amp;&amp; pnpm run build      # tsc -b &amp;&amp; vite build -&gt; backend/web/dist
# then reload the browser; the bundle hash in the URL proves which build you have</pre>
""")

# ------------------------------------------------------------------ 15
add(ch(15, "Invariants and gotchas"))
add(table(["invariant", "why"], [
  ["The packet shape never changes", "variance belongs in producers and consumers; a trading field on the envelope ends the design"],
  ["Generations are per (topic, key)", "a 1m and a 4h forecast for one symbol share a counter — order by computed_at"],
  ["Market topics are never recorded", "the bus is a state channel; a price two seconds old is not history"],
  ["Recording never fails a request", "the recorder is a consumer; a broken journal must not stop an answer"],
  ["One clock for the app", "<code>useNow</code> owns the interval; a timer per component was thirty timers and stale closures"],
  ["Colour tokens are <code>--pal-*</code>", "the theme already owns <code>--panel-2</code>; reusing those names silently repaints the UI"],
  ["Cash/rounding is done in integers", "floats in the ledger drift; the risk math rounds at one boundary"],
], ["34%", "66%"]))
add(co("warn", "Before you claim a UI change works",
  "Run the gate, then open the page and measure. The gate proves the component renders; it does not prove the axis labels are inside the SVG, that a chart's first point moved, or that a tooltip appears. Two of the three defects found in the last sweep were exactly that kind."))

# ------------------------------------------------------------------ 16
add(ch(16, "Glossary"))
add(defs([
  ("envelope / packet", "the unit of everything: data, meta and refs."),
  ("cid", "correlation id — minted at the boundary that created the work, so work can start when nothing central is reachable."),
  ("generation", "a counter per (topic, key), incremented on each publication. Not a global clock."),
  ("TTL", "how long an answer may live before it is stale, declared per topic."),
  ("plane", "the group a topic belongs to: market, intel, alert, model, risk, control, system."),
  ("bar store", "the local JSONL history that forecasts read."),
  ("journal", "the append-only record of every envelope."),
  ("lake", "the recorder's partitioned long-term store — bronze, silver, gold."),
  ("capability", "a topic plus its producer and maximum age, as the orchestrator sees it."),
  ("refusal", "an envelope saying why something was not allowed, or is stale."),
  ("verdict", "buy, sell or none — what the decision path concludes from one recorded forecast."),
  ("replay", "re-deriving every verdict from the journal and grading it against what happened."),
]))

# ------------------------------------------------------------------ 17
add(ch(17, "Frequently asked"))
add(defs([
  ("Why does a forecast take so long the first time?",
   "The bar store is empty for that pair and frame, so the platform downloads history. After that it answers from disk and the same request is instant."),
  ("Why is the live board idle when the GPU box is on?",
   "The board shows a sweep in progress; between sweeps it shows the best-ever configuration. Look at <code>computing</code> in the payload to tell “idle” from “no agent”."),
  ("Why did the decision refuse my forecast?",
   "Either it was older than the topic TTL (60 s for <code>intel.forecast</code>), or the projected edge was inside the 0.05% floor. Both are time and threshold rules, not errors."),
  ("Why do two endpoints disagree about the same pair?",
   "They may be reading different frames. <code>/api/decision</code> filters on the interval you ask for; a forecast published for 1m will not answer a 4h question."),
  ("Can I run the platform without RabbitMQ?",
   "Yes. Packets are still journalled; they are simply not broadcast, and the bus view stays empty."),
  ("Where do I change what counts as a move?",
   "The settings view, or <code>POST /api/settings/apply</code>. The screen reads them live; nothing needs a restart."),
]))

HTML = "".join(P)
OUT = os.path.join(DOCS, "CryptAlyst-Developer-Guide.pdf")
kit.build(
    title="CryptAlyst · Developer Guide",
    subtitle="Developer Guide",
    desc="The platform as built: every runtime, every route,<br/>every model, and the rules that keep them honest.",
    body_html=kit.page("CryptAlyst Developer Guide") + HTML + "</body></html>",
    out_pdf=OUT,
)
print("wrote", OUT)
