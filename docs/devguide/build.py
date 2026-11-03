#!/usr/bin/env python3
"""Build the CryptAlyst Developer Guide as HTML, printed to PDF by headless Chrome.

Style per docs/style-guide.md: A4, Helvetica, accent #1F4E79, muted #596370, figures in
the role palette, tables in the Light-Grid-Accent-1 idiom.
"""
import base64, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ACCENT, MUTED = "#1F4E79", "#596370"
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "devguide.html")

def fig(n, caption):
    b64 = base64.b64encode(open(os.path.join(HERE, f"d{n}.png"), "rb").read()).decode()
    return (f'<figure><img src="data:image/png;base64,{b64}" alt="">'
            f'<figcaption>{caption}</figcaption></figure>')

def table(headers, rows, widths=None):
    cols = ""
    if widths:
        cols = "<colgroup>" + "".join(f'<col style="width:{w}">' for w in widths) + "</colgroup>"
    head = "".join(f"<th>{h}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table>{cols}<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"

CSS = f"""
@page {{ size: A4; margin: 20mm 18mm 18mm 18mm; }}
* {{ box-sizing: border-box; }}
body {{ font-family: Helvetica, Arial, sans-serif; font-size: 10pt; color: #1a1a1a; line-height: 1.45; margin: 0; }}
h1 {{ font-size: 26pt; color: {ACCENT}; margin: 0 0 6px; letter-spacing: -0.01em; }}
h2 {{ font-size: 14pt; color: {ACCENT}; margin: 26px 0 8px; padding-bottom: 4px;
      border-bottom: 1.5px solid {ACCENT}; page-break-after: avoid; }}
h3 {{ font-size: 11pt; color: {MUTED}; margin: 16px 0 4px; page-break-after: avoid; }}
p {{ margin: 6px 0; }}
.lede {{ font-size: 12pt; color: {MUTED}; margin: 4px 0 18px; }}
.meta {{ font-size: 9pt; color: {MUTED}; border-top: 1px solid #d8e0e8;
         border-bottom: 1px solid #d8e0e8; padding: 7px 0; margin: 14px 0 0; }}
figure {{ margin: 12px 0; page-break-inside: avoid; text-align: center; }}
figure img {{ max-width: 100%; max-height: 160mm; width: auto; height: auto; }}
figcaption {{ font-size: 8pt; font-style: italic; color: {MUTED}; margin-top: 4px; }}
table {{ width: 100%; border-collapse: collapse; margin: 8px 0 12px; font-size: 8.6pt; }}
th {{ background: {ACCENT}; color: #fff; font-weight: bold; text-align: left; padding: 5px 7px;
      border: 1px solid #16375a; font-size: 8.8pt; }}
td {{ padding: 4px 7px; border: 1px solid #c9d4e0; vertical-align: top; }}
tbody tr:nth-child(even) td {{ background: #f4f7fa; }}
pre {{ background: #F1F3F6; border-left: 3px solid {ACCENT}; padding: 8px 10px;
       font-family: "Courier New", monospace; font-size: 8pt; line-height: 1.4;
       white-space: pre-wrap; page-break-inside: avoid; }}
code {{ font-family: "Courier New", monospace; font-size: 8.6pt; }}
.note {{ border-left: 3px solid {ACCENT}; background: #eef5fa; padding: 8px 12px; margin: 10px 0; }}
.warn {{ border-left: 3px solid #a4232b; background: #fdf0f2; padding: 8px 12px; margin: 10px 0; }}
ul, ol {{ margin: 6px 0 6px 18px; }} li {{ margin: 3px 0; }}
.toc {{ columns: 2; column-gap: 22px; font-size: 9.5pt; margin: 8px 0 4px; }}
.toc div {{ break-inside: avoid; }}
.toc div {{ margin: 2px 0; }}
"""

html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>CryptAlyst Developer Guide</title><style>{CSS}</style></head><body>

<h1>CryptAlyst Developer Guide</h1>
<p class="lede">How the platform is put together, how to change it without breaking it, and
what each gate actually proves.</p>
<p class="meta">CryptAlyst · developer reference · commit <code>29866c7</code> · every path,
route and count in this document was taken from the tree it describes</p>

<div class="toc">
<div>1 · What the platform is</div>
<div>2 · The repository</div>
<div>3 · The backend request path</div>
<div>4 · Data: store, lake, journal</div>
<div>5 · The detection layers</div>
<div>6 · Forecasting and models</div>
<div>7 · The envelope</div>
<div>8 · The frontend</div>
<div>9 · The GPU agent</div>
<div>10 · The fleet and its ports</div>
<div>11 · Testing and the gates</div>
<div>12 · Recipes: adding things</div>
<div>13 · Runbook</div>
<div>14 · Invariants and gotchas</div>
</div>
</div></div>

<h2>1. What the platform is</h2>
<p>Three runtimes in one repository. A Go platform serves the dashboard and all forecasts; a
React app renders six views; a Python fleet runs model books and a GPU agent scores parameter
grids. They share one contract — the envelope — and one broker.</p>
{fig(1, "Figure 1 — one repository, three runtimes, and the boundary each one owns.")}
<table>
<colgroup><col style="width:24%"><col style="width:38%"><col style="width:38%"></colgroup>
<thead><tr><th>runtime</th><th>what it owns</th><th>how you talk to it</th></tr></thead>
<tbody>
<tr><td><b>backend/</b> — Go</td><td>HTTP surface, forecast pipeline, model registry, envelope,
bar store, GPU orchestration</td><td>port 8787</td></tr>
<tr><td><b>web/</b> — React</td><td>the six views, panels, charts, glossary</td><td>built into <code>backend/web/dist</code></td></tr>
<tr><td><b>scalp5/</b> — Python + Go services</td><td>marketdata (:8796), recorder (:8801), refdata (:8790), model books (:8799)</td><td>RabbitMQ and HTTP</td></tr>
</tbody></table>

<h2>2. The repository</h2>
<p><code>backend/internal</code> holds 25 packages. The ones a new feature usually involves:</p>
{table(["package", "role", "files"],
 [["<code>api</code>", "every HTTP handler, the pipeline, the model registry, the envelope surface", "70"],
  ["<code>envelope</code>", "packet shape, topic registry, interceptors, cid, journal, RabbitMQ bus", "9"],
  ["<code>binance</code>", "the REST client behind the <code>KlinesSource</code> port", "2"],
  ["<code>forecastlab</code>", "the model grid that is graded walk-forward", "3"],
  ["<code>flow</code> · <code>screen</code> · <code>turtle</code>", "the three detection layers", "—"],
  ["<code>mocks</code>", "test doubles for the market port and the agent", "3"]],
 ["27%", "58%", "15%"])}
<p><code>web/src</code> is deliberately flat: <code>App.tsx</code> owns the six views,
<code>components/</code> holds panels and charts, <code>hooks/</code> the shared behaviour, and
<code>data/client.ts</code> the only place that calls <code>fetch</code>.</p>

<h2>3. The backend request path</h2>
{fig(2, "Figure 2 — every endpoint walks the same path: parse, policy, store-first data, model, envelope.")}
<p>An endpoint is a function on <code>*Server</code> registered in <code>routes.go</code>'s
single mux. It reads its parameters through <code>readQuery</code>, asks
<code>barsForRequest</code> for data, computes with <code>modelPath</code>, and answers with
<code>ok</code> or <code>fail</code>. Those four helpers are the whole request layer — before
them, thirty-five handlers wrote their own error envelope and twenty-three parsed the symbol
themselves.</p>
<h3>The interval policy</h3>
<pre>// backend/internal/api/pipeline.go
intervalPolicies = map[string]intervalPolicy{{
  "5s":  {{warmup: 40,  maxBars: 1000}},
  "30s": {{warmup: 20,  maxBars: 1000}},
  "1h":  {{warmup: 120, maxBars: 1000}},
  …
}}</pre>
<p>A frame's minimum and maximum live in one table. Twelve handlers previously carried their
own numbers, which is how the sub-minute frames came to be untestable: the same 120-bar
minimum that suits an hourly chart made a thirty-second chart impossible.</p>

<h2>4. Data: store, lake, journal</h2>
{fig(3, "Figure 3 — four data surfaces. The bar store answers forecasts; the journal and lake answer questions about what happened.")}
{table(["surface", "path", "written by", "read by"],
 [["bar store", "<code>data/history/SYMBOL-INTERVAL.jsonl</code>", "<code>fetchAndStore</code>", "every forecast endpoint"],
  ["envelope journal", "<code>data/envelope-journal.jsonl</code>", "<code>recordEnvelope</code>", "<code>/api/decision</code>, <code>/api/replay</code>, the orchestrator"],
  ["lake", "<code>state/lake3</code>", "the recorder (:8801)", "research; not yet a replay source"],
  ["GPU results", "in-memory per symbol", "the engine", "the live board"]],
 ["16%", "32%", "22%", "30%"])}
<div class="note"><b>Store first, network second.</b> <code>barsFor</code> answers from the local
store and only calls the exchange when the store is short. That is what stopped forecasts
re-downloading the same history dozens of times a minute — and it is why the data inventory
panel exists.</div>


<h2>5. The detection layers</h2>
<p>Three independent layers decide that something is happening. They are separate on purpose:
each has its own window and its own idea of "normal", so a burst that one misses another may
catch.</p>
<table>
<colgroup><col style="width:16%"><col style="width:26%"><col style="width:58%"></colgroup>
<thead><tr><th>layer</th><th>question</th><th>how it answers</th></tr></thead>
<tbody>
<tr><td><code>flow</code></td><td>is the tape unusual right now?</td><td>trade flow against a
baseline built from the same pair's quiet periods; aggressor share, buy ratio, burst multiple</td></tr>
<tr><td><code>screen</code></td><td>which pairs pass the thresholds?</td><td>move, volume ratio,
volume z-score, USD flow and 24h quote volume, all from the settings table</td></tr>
<tr><td><code>turtle</code></td><td>is there a longer-horizon setup?</td><td>the classic
breakout rules with a risk budget: starting equity, risk per unit, position sizing</td></tr>
</tbody></table>
<div class="note"><b>Thresholds are data, not code.</b> <code>/api/settings</code> carries the
numbers the screen uses, and the settings view edits them. A detection that fires too often is a
settings question before it is a code question.</div>
<pre>// a burst is measured against the pair's own baseline, not a global one
multiple = recentFlow / baselineFlow      // 26.4x = 26 times normal
zscore   = (move - meanMove) / stdMove    // unusual for THIS pair</pre>

<h2>6. Forecasting and models</h2>
<p>Fifty models in twelve families, each a function from bars to a path. The registry is the
single source of truth: name, family, whether it needs volume, and how much history it wants.</p>
<pre>// backend/internal/api/model_registry.go
type modelSpec struct {{
    name   string
    family string        // trend · damped · ema · analogue · statistical · …
    volume bool          // needs volume to compute
}}</pre>
<h3>How a forecast is scored</h3>
<table>
<colgroup><col style="width:22%"><col style="width:78%"></colgroup>
<thead><tr><th>metric</th><th>meaning, and its trap</th></tr></thead>
<tbody>
<tr><td>MAPE</td><td>percentage error of the path; comparable only within the same frame and horizon</td></tr>
<tr><td>coverage</td><td>how often the actual landed inside the 90% cone; a cone that claims 90%
and delivers 60% is worse than no cone</td></tr>
<tr><td>R&sup2;</td><td><b>the trap.</b> Computed on price levels it is dominated by the window's
own spread: a model that predicts "no change" scores above 0.95. It is shown because it is
conventional, not because it is decisive</td></tr>
<tr><td>skill vs flat</td><td>computed, and the honest comparison: does the model beat simply
saying "the price stays here"?</td></tr>
</tbody></table>
<pre>// backend/internal/forecastlab — the walk-forward grid
for each lookback in {{2..50}}:
    for each window in {{…}}:
        train on bars[0:n-window], grade on bars[n-window:n]</pre>
<p>The optimiser runs that grid on the GPU agent when one is reachable and falls back to local
scoring otherwise. The live board keeps the best-ever configuration per pair, which is what the
"best" pseudo-model resolves to.</p>

<h2>7. The envelope</h2>
<p>The full treatment is in <em>The Envelope, as built</em>; what a developer needs here is the
shape and the rule.</p>
<pre>packet = {{ data, meta, refs }}
meta   = version · plane · topic · key · producer · generation
         cid · parent_cid · computed_at · received_at · compute_ms · pipeline</pre>
<p>Publish through <code>envelope.Run</code>, never by hand: it applies authority, keying,
correlation and freshness before your operator computes anything, and records the refusals.</p>
<pre>req := envelope.Request{{Topic: "intel.forecast", Key: symbol, Producer: "forecast",
                        Refs: []envelope.Ref{{{{Topic: "market.candle", Key: symbol}}}}}}
pkt, pipeline, err := envelope.Run(req, time.Now(), func() (any, error) {{ return payload, nil }})
if err == nil {{ recordEnvelope(pkt) }}</pre>

<h2>8. The frontend</h2>
{fig(4, "Figure 4 — views compose components; components read shared state and one data layer.")}
{table(["layer", "rule"],
 [["views", "<code>App.tsx</code> switches on a hash-routed view; each view is a grid of panels"],
  ["panels", "take props, render, call <code>onDrill</code>/<code>onSelect</code> upward — they own no fetching"],
  ["data", "<code>useResource&lt;T&gt;(path, {{pollMs}})</code> for anything read; <code>postJSON</code>/<code>postAction</code> for anything written"],
  ["state", "symbol and interval live in <code>selection.tsx</code>; the glossary in <code>glossaryContext</code>; layout in <code>usePersistentSize</code>"],
  ["charts", "one SVG renderer (<code>ForecastChart</code>, with a projection mode) and <code>PriceChart</code> for candles"]],
 ["14%", "86%"])}
<div class="warn"><b>One fetch rule.</b> There is exactly one <code>fetch</code> left in the app:
inside <code>ErrorBoundary.componentDidCatch</code>. A client helper that throws there would raise
a second error inside the error handler, so that call stays raw — and commented.</div>

<h2>9. The GPU agent</h2>
{fig(5, "Figure 5 — the platform asks for a capability; the agent runs one CUDA pass over the whole grid.")}
<p>The agent (<code>cmd/cryptalas</code>, port 8810) advertises itself, accepts sweep jobs and
returns scored results. The engine keeps the best-ever configuration per symbol and falls back
to scoring the platform models locally when no agent answers — which is why the board still
moves with the GPU box off.</p>


<h2>10. The fleet and its ports</h2>
<p>Six processes make the platform. Start them in this order; each one is independently
restartable.</p>
<table>
<colgroup><col style="width:20%"><col style="width:10%"><col style="width:70%"></colgroup>
<thead><tr><th>process</th><th>port</th><th>what it does, and what breaks without it</th></tr></thead>
<tbody>
<tr><td>platform</td><td>8787</td><td>the dashboard and every forecast endpoint. Nothing works
without it</td></tr>
<tr><td>marketdata</td><td>8796</td><td>the Binance feed: 1s bars, top 400 pairs, aggregated to
5s and 30s. Without it the boards freeze but the page still loads</td></tr>
<tr><td>refdata</td><td>8790</td><td>identity: symbol, base, quote, contract type. Without it the
ticker picker falls back to whatever the market feed knows</td></tr>
<tr><td>recorder</td><td>8801</td><td>consumes every envelope and writes the lake. Without it
<code>/api/bus</code> answers 502 and the message-bus view goes empty</td></tr>
<tr><td>RabbitMQ</td><td>61613 / 15672</td><td>the envelope bus (STOMP / management). Without it
packets are journalled but not broadcast</td></tr>
<tr><td>master</td><td>8799</td><td>the Python model books</td></tr>
</tbody></table>
<pre># the order that works
rabbitmq-server -detached
backend/bin/server $(cat backend/.server-args)          # :8787
scalp5/core/bin/marketdata --port 8796 --top 400        # :8796
scalp5/core/bin/refdata -data ./state/refdata -addr 127.0.0.1:8790 -seed ./cmd/refdata/seed.json
scalp5/core/bin/recorder --port 8801 --segments --data ../state/lake3
python3 -u master.py --host 0.0.0.0 --port 8799</pre>

<h2>13. Runbook</h2>
<table>
<colgroup><col style="width:32%"><col style="width:68%"></colgroup>
<thead><tr><th>symptom</th><th>first thing to check</th></tr></thead>
<tbody>
<tr><td>the bus view is empty</td><td><code>curl :8801/stats</code> — 200 with counts means the
recorder is alive; connection refused means start it</td></tr>
<tr><td>a forecast never finishes</td><td>the Data panel: is history held? <code>/api/history/coverage</code>
tells you bars held versus bars required</td></tr>
<tr><td>charts are flat</td><td>the market feed: <code>/api/health</code> and the ticker. A dead
feed leaves the last price in place and every move reads 0.00%</td></tr>
<tr><td>the GPU board is idle</td><td><code>/api/agents</code> — an unreachable agent falls back to
local scoring, so "idle" and "no agent" look similar on the board</td></tr>
<tr><td>the dashboard is stale after a deploy</td><td>hard-reload: the bundle hash changes every
build and the old one is cached</td></tr>
<tr><td>a decision says "stale forecast"</td><td>that is correct behaviour — the forecast is older
than the topic TTL. Run a fresh forecast first</td></tr>
</tbody></table>

<h2>11. Testing and the gates</h2>
{fig(6, "Figure 6 — two gates, and what each check actually proves. None of them prove a chart looks right; that is what the browser is for.")}
<table>
<colgroup><col style="width:22%"><col style="width:24%"><col style="width:54%"></colgroup>
<thead><tr><th>command</th><th>where</th><th>what it proves</th></tr></thead>
<tbody>
<tr><td><code>go test ./...</code></td><td>backend/</td><td>handlers answer correctly against a mocked exchange — 21 packages</td></tr>
<tr><td><code>tsc -b</code></td><td>web/</td><td>the types line up across app and scripts</td></tr>
<tr><td><code>audit-css</code></td><td>web/</td><td>every class used exists, is styled, and keeps the min-size guard — 238 checks</td></tr>
<tr><td><code>verify</code></td><td>web/</td><td>data shapers and formatters — 761 checks</td></tr>
<tr><td><code>smoke</code></td><td>web/</td><td>every panel renders from fixtures — 215 checks</td></tr>
</tbody></table>
<p><code>internal/mocks</code> exists so a handler can be tested without the world:
<code>mocks.Market</code> answers from a fixture, records every request and can fail on demand;
<code>mocks.Agent</code> is a fake CryptALAS box over <code>httptest</code>.</p>

<h2>12. Recipes</h2>
{fig(7, "Figure 7 — four changes you will make often, and the smallest correct path for each.")}
<h3>Add an endpoint</h3>
<ol>
<li>Write <code>func (s *Server) handleX(w http.ResponseWriter, r *http.Request)</code> in a new
file under <code>internal/api</code>.</li>
<li>Parse with <code>readQuery(r)</code>; fetch with <code>s.barsForRequest(ctx, req, extra)</code>.</li>
<li>Answer with <code>ok(w, map[string]any{{...}})</code> or <code>fail(w, "reason")</code>.</li>
<li>Register it in <code>routes.go</code>; test it with <code>mocks.Market</code>.</li>
</ol>
<h3>Add a model</h3>
<ol>
<li>Write the path function: <code>func(bars []binance.Kline, horizon, lookback int) ([]float64, bool)</code>.</li>
<li>Register it in <code>model_registry.go</code> with name, family and whether it needs volume.</li>
<li>Nothing else: it appears in <code>/api/forecast/models</code>, in the picker, and in the
optimiser's candidate set.</li>
</ol>
<h3>Add a topic</h3>
<ol>
<li>One row in <code>topics.go</code>: name, plane, producers, TTL, whether it is recorded.</li>
<li>Authority now enforces who may write it; the orchestrator now schedules it.</li>
</ol>
<h3>Add a section to the forecast view</h3>
<ol>
<li>Write the component with props; fetch with <code>useResource</code>, glossaries with
<code>useTools</code>.</li>
<li>Wrap it in <code>&lt;Section id="fl.x" title="…"&gt;</code> — the section supplies the
error boundary, so a crash stays inside it.</li>
</ol>

<h2>14. Invariants and gotchas</h2>
<table>
<colgroup><col style="width:34%"><col style="width:66%"></colgroup>
<thead><tr><th>invariant</th><th>why</th></tr></thead>
<tbody>
<tr><td>The packet shape never changes</td><td>Variance belongs in producers and consumers. A trading
field on the envelope ends the design.</td></tr>
<tr><td>Generations are per (topic, key)</td><td>Not per topic. A 1m and a 4h forecast for one symbol
share a counter — compare <code>computed_at</code> to order them, not generation alone.</td></tr>
<tr><td>Market topics are never recorded</td><td>The bus is a state channel. A price two seconds old
is not history.</td></tr>
<tr><td>Recording never fails a request</td><td>The recorder is a consumer. If the journal or the
broker is down, the answer still goes out.</td></tr>
<tr><td>One clock for the app</td><td><code>useNow</code> owns the interval; a timer per component
was thirty timers and several stale closures.</td></tr>
<tr><td>Colour tokens are <code>--pal-*</code></td><td>The theme already owns
<code>--panel-2</code> and friends. Reusing those names silently repaints the UI.</td></tr>
</tbody></table>
<div class="note"><b>Before you claim a UI change works:</b> run the gate, then open the page and
measure. The gate proves the component renders; it does not prove the axis labels are inside the
SVG, that a chart's first point moved, or that a tooltip appears. Two of the three defects found
in the last sweep were of exactly that kind.</div>

</body></html>"""

open(OUT, "w").write(html)
print("wrote", OUT, len(html), "bytes")
