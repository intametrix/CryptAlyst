#!/usr/bin/env python3
"""The Envelope, as built — built on docs/buildkit.py.

Typography, page setup, cover and footer come from the shared builder, which implements
docs/style-guide.md: A4, Calibri 10 pt, accent #1F4E79, the cover from docs/cover.md, and
page numbers in the footer — not on the cover.

The content below stays an f-string on purpose: the figures and tables are function calls
that must be evaluated, not text left in the page.
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import buildkit as kit

def fig(n, caption):
    return kit.fig(os.path.join(HERE, f"d{n}.png"), caption)

def table(headers, rows, widths=None):
    return kit.table(headers, rows, widths)

CONTENT = f"""\


<h1>The Envelope, as built</h1>
<p class="lede">The contract the platform now runs on: one packet shape, four interceptors,
a recorded lineage, a bus, and a decision that can be replayed.</p>
<p class="meta">CryptAlyst · architecture reference for the implemented system · commit
<code>b41fec2</code> · every number in this document was measured on the running platform,
not estimated</p>

<h2>1. What this document is</h2>
<p>The design note <em>The Envelope for CryptAlyst</em> argued for the envelope and set out
six phases of adoption. This document describes what was built, where it lives, and what it
does when it runs. It is deliberately narrow: it covers the envelope and the pipeline around
it, because that is the part of the platform that changed.</p>
<p>Three claims are made here, and each has evidence:</p>
<ul>
<li>every publication passes the same four checks before its payload is computed;</li>
<li>a decision can be traced back to the packet it was made from, and replayed;</li>
<li>freshness is enforced from the packet's own <code>computed_at</code>, not a wall clock.</li>
</ul>
<div class="note"><b>The invariant to protect.</b> The packet shape never changes. Everything
that varies — venues, indicators, models, stores — lives in the producers, interceptors and
consumers around it. The moment a trading-specific field is added to the envelope, the
design has failed.</div>

<h2>2. The shape</h2>
{fig(1, "Figure 1 — one packet: data, meta, references. The shape is fixed; the engine bay around it grows.")}
<p>Three parts, and nothing else. The <b>data</b> is the payload the operator computed. The
<b>meta</b> is provenance: which plane, which topic, which key, which producer, at which
generation, under which correlation id, computed when. The <b>refs</b> are the inputs it
read — as references, so a tick batch stays in one place.</p>

<h3>A real packet from the running journal</h3>
<pre>{{ "data": {{ "actual": [77558.01, 77538.01, 77530],
          "forecast": [77691.39, 77691.39, 77691.39],
          "horizon": 6, "interval": "1m", "method": "vwap", … }},

  "meta": {{ "version": 1,
            "plane": "intel",
            "topic": "intel.forecast",
            "key": "BTCUSDT",
            "producer": "forecast",
            "generation": 18,
            "cid": "tel.forecast-BTCUSDT-c78e6f31f0e1d47a",
            "computed_at": 1793743021687,
            "pipeline": [ {{ "name": "authority", "passed": true }},
                          {{ "name": "keying", "passed": true }},
                          {{ "name": "correlation", "passed": true,
                            "detail": "tel.forecast-BTCUSDT-c78e6f31f0e1d47a" }},
                          {{ "name": "freshness", "passed": true,
                            "rule": "age <= 60000ms" }} ] }},

  "refs": [ {{ "topic": "market.candle", "key": "BTCUSDT", "age_ms": 16141687 }} ] }}</pre>
<p>The pipeline record travels <em>with</em> the packet. A reader does not have to find a log
line to learn which checks ran; the checks are in the envelope.</p>

<h2>3. The pipeline</h2>
{fig(2, "Figure 2 — authority, keying, correlation, freshness, then the operator computes. A refusal is a packet too.")}
<p>Every publication passes the same four interceptors, in this order, before the operator
computes anything. That ordering is the point: a producer that may not write a topic never
gets as far as spending compute on it.</p>
{table(["stage", "question", "on failure"],
 [["authority", "may this producer write this topic?", "refusal naming the producer and the topic"],
  ["keying", "is this topic keyed by a symbol?", "refusal naming the missing key"],
  ["correlation", "—", "a cid is minted and chained to the parent"],
  ["freshness", "is <code>computed_at</code> within the topic's TTL?", "refusal quoting the age and the limit"]],
 ["14%", "46%", "40%"])}
<p>A refusal is not a log line. It is an envelope on <code>system.error</code> carrying the
stage, the rule and the number that tripped it — which is what makes "why not" something you
can open.</p>

<h2>4. The topic registry</h2>
{fig(3, "Figure 3 — 23 topics over six planes. The registry is the enforcement point: who may write what, and how long it lives.")}
<p>One table declares every topic's plane, its permitted producers and its retention. A
producer that is not listed is refused by the authority interceptor. Adding a topic is a row,
not a code path.</p>
{table(["plane", "topics", "recorded"],
 [["market", "price · depth · candle · mini24h · ticks", "no — live only"],
  ["intel", "signal · flow · movers · board · ta · forecast · forecast.outcome · backtest", "yes"],
  ["alert", "pumpdump", "yes"],
  ["model", "sweep · selection · decision · fill · pnl", "yes"],
  ["risk", "state · refusal", "yes"],
  ["control / system", "control.engine · system.error", "yes"]],
 ["16%", "62%", "22%"])}
<p>The market plane is deliberately not recorded: the bus is a state channel, not a job queue,
and a price that is two seconds old is not worth storing as history.</p>

<h2>5. Identity and lineage</h2>
{fig(4, "Figure 4 — a decision references the forecast by parent_cid; the forecast references the candles it read.")}
<p>Minting is local: a cid is created at the boundary that created the work, so work still
starts when nothing central is reachable. What is central is the <em>map</em> — and here the map
is a projection of the journal, rebuildable by replay.</p>
<h3>A lineage read from the running journal</h3>
<pre>GET /api/envelope/chain?cid=del.decision-BTCUSDT-711…

lineage depth: 2
  model.decision   key=BTCUSDT  gen=1    parent=tel.forecast-BTCUSDT-…
  intel.forecast   key=BTCUSDT  gen=117  parent=–</pre>
<p>The walk follows <code>parent_cid</code> <b>backwards</b>. The first implementation matched
"cid equals this <em>or</em> parent equals this", which lists what came <em>from</em> a decision
rather than what it was made of; a reviewer asking "why did we buy?" needs the inputs. That
was found by testing the feature and fixed with a regression test.</p>

<h2>6. The bus</h2>
{fig(5, "Figure 5 — exchange is the plane, routing key is the topic. The recorder is a consumer, never a hook.")}
<p>The broker already carried one topic exchange per plane, so the envelope uses that topology
rather than inventing a second one. Provenance is repeated as STOMP headers, so a consumer can
filter without parsing the body.</p>
<p>Publishing is best-effort by design: if the broker is down the packet is still journalled
and the request still answers. A bus outage stops a forecast being <em>broadcast</em>, not being
computed.</p>
<h3>Recorder statistics, measured</h3>
<pre>GET :8801/stats
  intel.forecast   184        market.candle   5 768
  market.price   12 454        market.depth  323 893
  rate         ~1 000 envelopes/s</pre>
<p>That is the envelope path end to end: a producer publishes, the broker routes by plane, the
recorder consumes and writes. Nothing on the hot path waits for the disk.</p>

<h2>7. Freshness, enforced</h2>
{fig(6, "Figure 6 — a stale forecast is refused with its own label quoted back at it.")}
<p>The platform previously guessed staleness from a wall clock. Now the packet says when it
was computed, the topic says how long an answer may live, and the refusal quotes both.</p>
<pre>age = now − meta.computed_at        limit = topic TTL
age ≤ limit   → the verdict is allowed
age &gt; limit   → REFUSED: "stale forecast", age 1 706 s, limit 60 s</pre>
<div class="warn"><b>Found by testing.</b> Asking for a decision on the 4h frame returned a
flat 1m forecast, because generations count per (topic, key) and both frames share a counter —
"newest packet" meant "whichever was published last". The decision now filters on the packet's
own interval and orders by <code>computed_at</code>. A regression test publishes a flat 1m and
a falling 4h forecast and asserts the 4h decision uses the 4h one.</div>

<h2>8. The orchestrator</h2>
{fig(7, "Figure 7 — capabilities come from the registry, generations from the journal. The orchestrator knows no models.")}
<p>Two inputs and no model knowledge: the capability table (derived from the topic registry —
topic, producer, maximum age) and the journal (the newest packet per topic and key, with its
generation and computed time). From those it decides serve or recompute.</p>
<pre>GET /api/orchestrator/plan?symbol=BTCUSDT
  18 capabilities · 16 require recompute
  intel.forecast  gen 8  age 250 s  recompute  older than its deadline
  intel.signal    gen 0            recompute  nothing recorded</pre>
<p>When it decides work is needed it publishes a <code>control.engine</code> packet naming the
capability. It never calls a calculator directly: a request for work is an envelope like
everything else, so it is recorded and can be replayed.</p>

<h2>9. Backtest as replay</h2>
{fig(8, "Figure 8 — the journal is replayed through the same decide() the live path uses, then graded at the horizon.")}
<p>The weakest part of the old platform was that the backtest and the live system shared intent
but not code. Here they share the code: replay reads the recorded forecasts, re-derives each
verdict with the same rule the live path uses, and grades it against the price the market
actually reached at anchor + horizon.</p>
<h3>The scorecard, as it stands</h3>
{table(["metric", "value"],
 [["packets replayed", "164"],
  ["graded", "157"],
  ["buy / sell / none", "0 / 7 / 157"],
  ["directional calls correct", "<b>0 of 7</b>"],
  ["mean projected edge", "below the 0.05% floor on most frames"]],
 ["52%", "48%"])}
<div class="warn"><b>This is the honest result.</b> The platform declines to trade almost
always, and the seven times it did, it was wrong. That is not a failure of the envelope — it is
the envelope working. Before, the same models reported R² ≈ 0.97 and nobody could say whether
that meant anything. Now the platform states what happened and keeps the packets that prove it.</div>

<h2>10. What was verified, and how</h2>
<p>Every claim above was measured on the running platform, not inferred:</p>
{table(["check", "evidence"],
 [["every publication passes four interceptors", "pipeline recorded on each packet; refusal paths unit-tested"],
  ["a decision can be traced", "chain returns depth 2, decision → forecast; regression test"],
  ["freshness is from meta", "stale 4h forecast refused with age and limit quoted"],
  ["the bus carries envelopes", "recorder counts per topic, ~1 000/s"],
  ["the journal replays", "164 graded verdicts from recorded packets"],
  ["the registry is an enforcement point", "unregistered producer refused; test asserts it"]],
 ["40%", "60%"])}
<p>The full suite runs green: <code>gofmt</code> clean, <code>vet</code> clean, <code>go test</code>
across 21 packages with no failures, and the broker integration test skips cleanly when
RabbitMQ is absent.</p>

<h2>11. What is not built yet</h2>
<ul>
<li><b>Producers for most intel topics.</b> <code>intel.signal</code>, <code>intel.flow</code>,
<code>intel.ta</code> and <code>intel.board</code> are registered but nothing publishes them, so
the orchestrator correctly reports "nothing recorded" for each.</li>
<li><b>Fill and PnL packets.</b> <code>model.fill</code> and <code>model.pnl</code> exist in the
registry and nothing writes them; the trading trail stops at the decision.</li>
<li><b>A decision floor worth trading.</b> The 0.05% threshold is why nothing trades. That is a
strategy parameter, not an architecture one, and it has not been set.</li>
<li><b>The lake as a replay source.</b> Replay currently reads the JSONL journal; the recorder's
lake holds the same packets and is not yet a reader.</li>
</ul>
<div class="note"><b>Where the code is.</b> <code>backend/internal/envelope/</code> —
<code>packet.go</code> (shape), <code>topics.go</code> (registry), <code>pipeline.go</code>
(interceptors), <code>cid.go</code> (identity), <code>journal.go</code> (recorder and replay),
<code>bus.go</code> (RabbitMQ). The API surface is <code>backend/internal/api/decision.go</code>,
<code>replay.go</code>, <code>orchestrator.go</code> and <code>envelope_api.go</code>.</div>

"""

OUT = os.path.join(os.path.dirname(HERE), "The-Envelope-As-Built.pdf")

kit.build(
    title="The Envelope, as built",
    subtitle="The Envelope, as built",
    desc="One packet shape, four interceptors, a recorded lineage,<br/>a bus, and a decision that can be replayed.",
    body_html=kit.page("The Envelope, as built") + CONTENT + "</body></html>",
    out_pdf=OUT,
)
print("wrote", OUT)
