#!/usr/bin/env python3
"""Build the CryptAlyst Developer Guide (content assembled from blocks)."""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import buildkit as kit

def fig(n, caption):
    return kit.fig(os.path.join(HERE, f"d{n}.png"), caption)

def table(headers, rows, widths=None):
    return kit.table(headers, rows, widths)

P = []
def add(*blocks):
    P.extend(blocks)

T = {}  # chapter text, kept as named blocks for readability

T['intro'] = """
<h1>CryptAlyst Developer Guide</h1>
<p class="lede">How the platform is put together, how to change it without breaking it, and what each gate actually proves.</p>
<p class="meta">CryptAlyst · developer reference · commit 5ea4dff · every path, route, count and flag here was taken from the tree it describes</p>
<div class="toc">
<div>1 · What the platform is</div>
<div>2 · The repository</div>
<div>3 · The backend request path</div>
<div>4 · The endpoint catalogue</div>
<div>5 · Data: store, lake, journal</div>
<div>6 · The detection layers</div>
<div>7 · Forecasting and models</div>
<div>8 · The envelope</div>
<div>9 · The frontend</div>
<div>10 · The GPU agent</div>
<div>11 · The fleet, flags and ports</div>
<div>12 · Testing and the gates</div>
<div>13 · Recipes: adding things</div>
<div>14 · Runbook</div>
<div>15 · Invariants and gotchas</div>
<div>16 · Glossary</div>
</div>
<h2>1. What the platform is</h2>
<p>CryptAlyst watches several hundred Binance pairs at once and answers three questions in order: <b>is anything moving</b>, <b>what does the model expect next</b>, and <b>was the last expectation right</b>. It is a market-watch and forecasting bench — it computes and grades, it does not execute.</p>
<p>Three runtimes share one repository and one contract. The Go platform serves the dashboard and every forecast endpoint. The React app renders six views. A Python fleet and a small set of Go services feed the market data, record the traffic and run the model books.</p>
"""

T['ch2'] = """
<h2>2. The repository</h2>
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
<p><code>web/src</code> is deliberately flat: no barrel files, no aliasing, no component library. A component is imported by its path.</p>
"""

T['ch3'] = """
<h2>3. The backend request path</h2>
<p>An endpoint is a method on <code>*Server</code>, registered on one mux in <code>internal/api/server.go</code>. Every one walks the same four steps, and each step has exactly one helper.</p>
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
<p>A frame's minimum and maximum are one row. Adding a frame is a row, not a hunt through handlers.</p>
<h3>Why the data step is store-first</h3>
<p><code>barsFor</code> answers from <code>data/history</code> and only calls the exchange when the store is short. Without that, a dashboard polling four endpoints re-downloaded the same history dozens of times a minute, the exchange rate-limited it, and it surfaced as “the forecast stopped working” rather than as an obvious error.</p>
"""

T['ch4'] = """
<h2>4. The endpoint catalogue</h2>
<p>Eighty-nine routes. The count is the number of distinct paths with that prefix.</p>
<h3>The envelope routes in detail</h3>
<h3>Decision and replay</h3>
<p><code>GET /api/decision?symbol=&amp;interval=</code> reads the newest recorded forecast for that symbol <b>and that interval</b>, refuses it if <code>meta.computed_at</code> is older than the topic TTL, and otherwise applies the edge rule.</p>
<p><code>GET /api/replay</code> does the same for every recorded forecast, then grades each verdict against the price that actually arrived at anchor + horizon. It shares the decision function with the live path — that is the point of it.</p>
<div class="note"><b>Every route answers in one shape.</b> Success is <code>{"ok": true, …}</code>; failure is <code>{"ok": false, "error": "…"}</code> with a status that means something. A client never has to guess whether a 200 carries an error.</div>
"""

T['ch5'] = """
<h2>5. Data: store, lake, journal</h2>
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
<div class="note"><b>Why two records of the same packets.</b> The journal is one file the API can replay in a single pass — simple, fast, and it never blocks a request. The lake is partitioned and compressed for volume. Replay reads the journal today; the lake is the next reader.</div>
"""

T['ch6'] = """
<h2>6. The detection layers</h2>
<p>Three layers decide that something is happening, and they are independent on purpose: each has its own window and its own idea of normal, so a burst one misses another may catch.</p>
<h3>Bursts are relative, never absolute</h3>
<pre>multiple = recentFlow / baselineFlow     // 26.4x means 26 times this pair's normal
zscore   = (move - meanMove) / stdMove   // unusual for THIS pair, not for the market</pre>
<p>A $50k print is noise in BTC and an event in a small alt. That is why every threshold in the screen is expressed against the pair's own history — and why the settings view exists: the numbers are data, not code.</p>
<p>They are read from <code>/api/settings</code>, edited in the settings view, and applied without a restart. A detection that fires too often is a settings question before it is a code question.</p>
"""

T['ch7'] = """
<h2>7. Forecasting and models</h2>
<p>Forty-nine models in twelve families. Each is a function from bars to a path; the registry is the single source of truth for its name, family, volume need and lookback.</p>
<pre>// backend/internal/api/model_registry.go
type modelSpec struct {
    name   string
    family string        // trend · damped · ema · analogue · statistical
    volume bool          // needs volume to compute
}</pre>
<p>Register a model and it appears in <code>/api/forecast/models</code>, in the picker's grouped list, and in the optimiser's candidate set. There is no second list to update.</p>
<h3>The optimiser</h3>
<pre>for each lookback in {2..50}:
    for each window in {10, 20, 40, 100}:
        train on bars[0:n-window], grade on bars[n-window:n]</pre>
<p>The grid runs on the GPU agent when one is reachable and is scored locally otherwise. The live board keeps the best-ever configuration per pair, which is what the <code>best</code> pseudo-model resolves to.</p>
"""

T['ch8'] = """
<h2>8. The envelope</h2>
<p>The full treatment is <em>The Envelope, as built</em>. What a developer needs here is the shape, the rule and the topics.</p>
<pre>packet = {data, meta, refs}
meta   = version · plane · topic · key · producer · generation
         cid · parent_cid · computed_at · received_at · compute_ms · pipeline</pre>
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
"""

T['ch9'] = """
<h2>9. The frontend</h2>
<p>Six views in <code>App.tsx</code>, each a grid of panels. Panels take props and render; they do not fetch. Data comes from <code>useResource&lt;T&gt;(path, {pollMs})</code>, which owns loading, polling, aborting and the error.</p>
<p>Shared state has one owner each: symbol and interval in <code>selection.tsx</code>, tooltips in <code>glossaryContext.tsx</code>, panel sizes in <code>usePersistentSize</code>, and one clock in <code>useNow</code> for every age label on the page.</p>
<p>Charts: one SVG renderer (<code>ForecastChart</code>, which also has a projection mode for the live next-bars panel) and <code>PriceChart</code> for candles. Navigation — zoom, pan, presets, crosshair — is shared through <code>useZoomPan</code>.</p>
<div class="warn"><b>One fetch rule.</b> There is exactly one <code>fetch</code> left in the app: inside <code>ErrorBoundary.componentDidCatch</code>. A client helper that throws there would raise a second error inside the error handler, so that call stays raw — and commented.</div>
"""

T['ch10'] = """
<h2>10. The GPU agent</h2>
<p>The agent (<code>cmd/cryptalas</code>, port 8810) advertises its device and version on <code>GET /gpu</code>, accepts sweep jobs on <code>POST /jobs</code>, and returns scored results. The engine keeps the best-ever configuration per symbol and falls back to scoring the platform models locally when no agent answers — which is why the board still moves with the GPU box off.</p>
<p>The CPU path is the audit path: the same job shape runs locally and its result must match the GPU within tolerance, so a GPU discrepancy is a bug rather than a mystery.</p>
"""

T['ch11'] = """
<h2>11. The fleet, flags and ports</h2>
<p>Six processes make the platform. Start them in this order; each is independently restartable.</p>
<pre>rabbitmq-server -detached
backend/bin/server $(cat backend/.server-args)            # :8787
scalp5/core/bin/marketdata --port 8796 --top 400          # :8796
scalp5/core/bin/refdata -data ./state/refdata -addr 127.0.0.1:8790 -seed ./cmd/refdata/seed.json
scalp5/core/bin/recorder --port 8801 --segments --data ../state/lake3
python3 -u master.py --host 0.0.0.0 --port 8799</pre>
<h3>The platform's own flags</h3>
<pre>backend/bin/server \
  --addr 0.0.0.0:8787 \
  --timeframes 1d,4h,1h,30m \
  --min-liq 50000 --max-symbols 500 --extra RIFUSDT \
  --screen-min-move 0.5 --screen-min-vol-ratio 1.2 --screen-min-vol-z 0.8 \
  --screen-min-recent-usd 100 --screen-min-quote-volume 100 \
  --alert-history scalp5/alerts.jsonl --ledger scalp5/ledger/backend-20261101.jsonl \
  --refdata http://127.0.0.1:8790 --web-dir backend/web/dist</pre>
<h3>Environment</h3>
<p><code>CRYPTALYST_STOMP</code> (default <code>127.0.0.1:61613</code>), <code>CRYPTALYST_STOMP_LOGIN</code>, <code>CRYPTALYST_STOMP_PASSCODE</code> and <code>STATE_DIR</code>. The STOMP variables are what point a build at a different broker; <code>STATE_DIR</code> relocates the persisted state.</p>
"""

T['ch12'] = """
<h2>12. Testing and the gates</h2>
<p>Two gates, and each check answers a different question. None of them prove a chart looks right; that is what the browser is for.</p>
<h3>Writing a backend test</h3>
<p><code>internal/mocks</code> exists so a handler can be tested without the world: <code>mocks.Market</code> answers from a fixture, records every request and can fail on demand; <code>mocks.Agent</code> is a fake agent over <code>httptest</code>.</p>
<pre>func TestSomething(t *testing.T) {
    s := newTestServer(t, &amp;mocks.Market{
        Bars:      mocks.Series("BTCUSDT", 400, 77_000, 5_000, time.Now().UnixMilli()),
        FailAfter: 2,        // the third request fails, on purpose
    })
    rec := httptest.NewRecorder()
    s.Routes().ServeHTTP(rec, httptest.NewRequest("GET", "/api/forecast/chart?symbol=BTCUSDT", nil))
    ...
}</pre>
<p>Tests never touch the network, never read the wall clock for logic, and never depend on a file outside the repository. Fixtures are generated, not recorded.</p>
"""

T['ch13'] = """
<h2>13. Recipes: adding things</h2>
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
"""

T['ch14'] = """
<h2>14. Runbook</h2>
<p>Start from the symptom, not from the component you suspect.</p>
"""

T['ch15'] = """
<h2>15. Invariants and gotchas</h2>
"""

T['ch16'] = """
<h2>16. Glossary</h2>
"""

add(T['intro'], fig(1, "Figure 1 — one repository, three runtimes, and the boundary each one owns."))

add(table(["runtime", "what it owns", "how you talk to it"], [
  ["<b>backend/</b> — Go module <code>pumpdump/backend</code>", "HTTP surface, forecast pipeline, model registry, envelope, bar store, GPU orchestration", "port 8787"],
  ["<b>web/</b> — React + Vite", "six views, panels, charts, glossary", "built into <code>backend/web/dist</code>"],
  ["<b>scalp5/</b> — Python and Go services", "marketdata (:8796), recorder (:8801), refdata (:8790), model books (:8799)", "RabbitMQ and HTTP"],
], ["24%", "48%", "28%"]))

add("""
<div class="note"><b>Why three runtimes and not one.</b> The feed and the recorder are Go because they are throughput problems. The model books are Python because that is where the numerical libraries are. The platform is Go because it is the thing that must not fall over. The boundary between them is the envelope, so none has to know the others' internals.</div>
""")

add(T['ch2'], table(["package", "role", "files"], [
  ["<code>api</code>", "every HTTP handler, the pipeline, the model registry, the envelope surface", "70"],
  ["<code>envelope</code>", "packet shape, topic registry, interceptors, cid, journal, RabbitMQ bus", "9"],
  ["<code>binance</code>", "the REST client behind the <code>KlinesSource</code> port", "2"],
  ["<code>forecastlab</code>", "the model grid that is graded walk-forward", "3"],
  ["<code>flow</code> · <code>screen</code> · <code>turtle</code>", "the three detection layers", "—"],
  ["<code>mocks</code>", "test doubles for the market port and the agent", "3"],
], ["26%", "60%", "14%"]))

add(fig(2, "Figure 2 — every endpoint walks the same path: parse, policy, store-first data, model, envelope."))
add(T['ch3'], table(["step", "helper", "rule"], [
  ["parse", "<code>readQuery(r)</code>", "typed accessors: symbol, interval, int, float, bool, text"],
  ["policy", "<code>policyFor(interval)</code>", "warm-up and maximum bars for the frame"],
  ["data", "<code>barsForRequest(ctx, req, extra)</code>", "local store first, exchange only when short"],
  ["compute", "<code>modelPath(method)</code>", "the model's path function, from the registry"],
  ["answer", "<code>ok(w, v)</code> / <code>fail(w, msg)</code>", "one envelope shape for success and failure"],
], ["12%", "38%", "50%"]))

add(T['ch4'], table(["area", "routes", "what they cover"], [
  ["forecast", "19", "chart, lab, models, optimize, live-state, forward, mlr, features, verify, configs"],
  ["turtle", "11", "state, config, trades, orders, equity, exposure, stats, rotations, allocation, settled, session-log"],
  ["envelope", "4", "topics, recent, stats, chain"],
  ["alerts", "4", "feed, groups, history, scored"],
  ["data", "3", "inventory, load, prune"],
  ["agents", "3", "list, register, heartbeat"],
  ["survey · settings · screen · orchestrator · models · klines · history · flow · client-errors", "18", "two or three each"],
  ["other", "27", "health, heat, bus, perf, universe, decision, replay, tools, colours, zoom-presets …"],
], ["34%", "8%", "58%"]))

add(table(["route", "answers"], [
  ["<code>/api/envelope/topics</code>", "the registry: every topic, its plane, its producers and its TTL"],
  ["<code>/api/envelope/recent</code>", "the newest packets from the journal, optionally filtered by topic"],
  ["<code>/api/envelope/stats</code>", "counts per topic and the journal path"],
  ["<code>/api/envelope/chain?cid=</code>", "the lineage: this packet, then its parent, then its parent"],
], ["34%", "66%"]))

add(fig(3, "Figure 3 — four data surfaces. The bar store answers forecasts; the journal and the lake answer what happened."))
add(T['ch5'], table(["surface", "path", "written by", "read by"], [
  ["bar store", "<code>data/history/SYMBOL-INTERVAL.jsonl</code>", "<code>fetchAndStore</code>", "every forecast endpoint"],
  ["journal", "<code>data/envelope-journal.jsonl</code>", "<code>recordEnvelope</code>", "decision, replay, orchestrator"],
  ["lake", "<code>state/lake3</code>", "the recorder (:8801)", "research; not yet a replay source"],
  ["GPU results", "in memory, per symbol", "the engine", "the live board"],
], ["16%", "32%", "22%", "30%"]))

add(T['ch6'], table(["layer", "question", "inputs"], [
  ["<code>flow</code>", "is the tape unusual right now?", "trade prints: aggressor side, size, price, against the pair's own baseline"],
  ["<code>screen</code>", "which pairs pass the thresholds?", "move %, volume ratio, volume z-score, recent USD flow, 24h volume"],
  ["<code>turtle</code>", "is there a longer-horizon setup?", "breakouts, ATR, position sizing against a risk budget"],
], ["14%", "30%", "56%"]))

add(table(["setting", "what it filters"], [
  ["<code>screen_min_move</code>", "minimum percentage move in the window"],
  ["<code>screen_min_vol_ratio</code>", "volume versus its own average"],
  ["<code>screen_min_vol_z</code>", "how many standard deviations that volume is"],
  ["<code>screen_min_recent_usd</code>", "dollar flow in the recent window"],
  ["<code>screen_min_quote_volume</code>", "24h turnover, so illiquid pairs never appear"],
], ["38%", "62%"]))

add(T['ch7'], table(["family", "models", "examples"], [
  ["moving average", "11", "ema5, ema20, sma50, wma, hma"],
  ["exponential", "8", "ses, des, damped80, damped90"],
  ["damped trend", "7", "damped70, damped75, damped85"],
  ["trend", "7", "trend20, trend50, vwtrend"],
  ["volume", "4", "vwap, vwdrift, volweighted"],
  ["analogue", "3", "analog5, analog10, analog20"],
  ["ensemble", "3", "blend, super, vote"],
  ["benchmark", "2", "naive, mean"],
  ["arima · regression · composite · foundation", "4", "arima, ols, super, timestral (agent only)"],
], ["32%", "10%", "58%"]))

add(table(["metric", "meaning, and its trap"], [
  ["MAPE", "percentage error of the path; comparable only within the same frame and horizon"],
  ["coverage", "how often the actual landed inside the 90% cone — a cone claiming 90% and delivering 60% is worse than none"],
  ["R²", "<b>the trap.</b> On price levels it is dominated by the window's own spread: a model that predicts “no change” scores above 0.95"],
  ["skill vs flat", "the honest comparison — does the model beat saying “the price stays here”?"],
], ["18%", "82%"]))

add("""
<div class="warn"><b>What the numbers say.</b> Across 164 recorded forecasts the replay grades seven directional calls and none of them correct. The models have no edge yet. The platform's value today is that it can prove that sentence, per packet.</div>
""")

add(T['ch8'], table(["plane", "topics"], [
  ["market", "price · depth · candle · mini24h · ticks"],
  ["intel", "signal · flow · movers · board · ta · forecast · forecast.outcome · backtest"],
  ["alert", "pumpdump"],
  ["model", "sweep · selection · decision · fill · pnl"],
  ["risk", "state · refusal"],
  ["control / system", "control.engine · system.error"],
], ["22%", "78%"]))

add(fig(4, "Figure 4 — views compose components; components read shared state and one data layer."))
add(T['ch9'], table(["layer", "rule"], [
  ["views", "<code>App.tsx</code> switches on a hash-routed view; each view is a grid of panels"],
  ["panels", "take props, render, call <code>onDrill</code>/<code>onSelect</code> upward — they own no fetching"],
  ["data", "<code>useResource&lt;T&gt;</code> to read; <code>postJSON</code>/<code>postAction</code> to write"],
  ["state", "symbol and interval in <code>selection.tsx</code>; glossary in context; sizes persisted"],
  ["charts", "one SVG renderer with a projection mode, plus <code>PriceChart</code> for candles"],
], ["14%", "86%"]))

add(fig(5, "Figure 5 — the platform asks for a capability; the agent runs one CUDA pass over the whole grid."))
add(T['ch10'])
add(T['ch11'], table(["process", "port", "what breaks without it"], [
  ["platform", "8787", "nothing works"],
  ["marketdata", "8796", "the boards freeze; the page still loads"],
  ["refdata", "8790", "the ticker picker falls back to the market feed's symbols"],
  ["recorder", "8801", "<code>/api/bus</code> answers 502 and the bus view goes empty"],
  ["RabbitMQ", "61613 / 15672", "packets are journalled but not broadcast"],
  ["master", "8799", "the Python model books stop"],
], ["16%", "14%", "70%"]))

add(fig(6, "Figure 6 — two gates, and what each check proves. None of them prove a chart looks right."))
add(T['ch12'], table(["command", "where", "what it proves"], [
  ["<code>go test ./...</code>", "backend/", "handlers answer correctly against a mocked exchange — 21 packages"],
  ["<code>tsc -b</code>", "web/", "the types line up across app and scripts"],
  ["<code>audit-css</code>", "web/", "every class used exists and is styled — 238 checks"],
  ["<code>verify</code>", "web/", "data shapers and formatters — 761 checks"],
  ["<code>smoke</code>", "web/", "every panel renders from fixtures — 215 checks"],
], ["22%", "12%", "66%"]))

add(fig(7, "Figure 7 — four changes you will make often, and the smallest correct path for each."))
add(T['ch13'])
add(T['ch14'], table(["symptom", "first thing to check"], [
  ["the bus view is empty", "<code>curl :8801/stats</code> — 200 with counts means the recorder is alive"],
  ["a forecast never finishes", "the Data panel: is history held? coverage says bars held versus required"],
  ["charts are flat", "the market feed — a dead feed leaves the last price and every move reads 0.00%"],
  ["the GPU board is idle", "<code>/api/agents</code> — an unreachable agent falls back to local scoring"],
  ["the dashboard is stale after a deploy", "hard-reload: the bundle hash changes every build"],
  ["a decision says “stale forecast”", "correct behaviour — the forecast is older than the topic TTL"],
], ["34%", "66%"]))

add(T['ch15'], table(["invariant", "why"], [
  ["The packet shape never changes", "variance belongs in producers and consumers; a trading field on the envelope ends the design"],
  ["Generations are per (topic, key)", "a 1m and a 4h forecast for one symbol share a counter — order by computed_at"],
  ["Market topics are never recorded", "the bus is a state channel; a price two seconds old is not history"],
  ["Recording never fails a request", "the recorder is a consumer; a broken journal must not stop an answer"],
  ["One clock for the app", "<code>useNow</code> owns the interval; a timer per component was thirty timers and stale closures"],
  ["Colour tokens are <code>--pal-*</code>", "the theme already owns <code>--panel-2</code>; reusing those names silently repaints the UI"],
], ["34%", "66%"]))

add(T['ch16'], table(["term", "meaning"], [
  ["envelope / packet", "the unit of everything: data, meta, refs"],
  ["cid", "correlation id — minted at the boundary that created the work"],
  ["generation", "a counter per (topic, key), incremented on each publication"],
  ["TTL", "how long an answer may live before it is stale, declared per topic"],
  ["bar store", "the local JSONL history that forecasts read"],
  ["journal", "the append-only record of every envelope"],
  ["lake", "the recorder's partitioned long-term store"],
  ["capability", "a topic plus its producer and maximum age, as the orchestrator sees it"],
  ["refusal", "an envelope saying why something was not allowed or is stale"],
], ["24%", "76%"]))

HTML = "".join(P)
OUT = os.path.join(os.path.dirname(HERE), "CryptAlyst-Developer-Guide.pdf")

kit.build(
    title="CryptAlyst · Developer Guide",
    subtitle="Developer Guide",
    desc="How the platform is put together,<br/>how to change it without breaking it,<br/>and what each gate actually proves.",
    body_html=kit.page("CryptAlyst Developer Guide") + HTML + "</body></html>",
    out_pdf=OUT,
)
print("wrote", OUT)
