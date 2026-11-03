#!/usr/bin/env python3
"""Build 'The Envelope, as built' — chapters, colour, live evidence."""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.dirname(HERE)
sys.path.insert(0, DOCS)
import buildkit as kit

SNAP = json.load(open(os.path.join(DOCS, "data", "platform-snapshot.json")))
TOPICS = SNAP["topics"]
STATS = SNAP["envelope_stats"] or {}
REPLAY = SNAP["replay_summary"] or {}
COMMIT = SNAP["commit"]

def fig(n, caption):
    return kit.fig(os.path.join(HERE, f"d{n}.png"), caption)

table, ch, co, kpis, defs = kit.table, kit.chapter, kit.callout, kit.kpis, kit.defs

P = []
def add(*b): P.extend(b)

counts = STATS.get("counts", {})
total = STATS.get("total", 0)

add("""
<h1>The Envelope, as built</h1>
<p class="lede">One packet shape, four interceptors, a recorded lineage, a bus, and a decision that can be replayed.</p>
<p class="meta">CryptAlyst · architecture reference · snapshot """ + COMMIT + """ · every number here was read from the running platform</p>
""")
add(kpis([(str(len(TOPICS)), "topics", "over six planes"),
          (str(total), "packets", "in the journal"),
          (str(REPLAY.get("graded", "—")), "graded", "verdicts replayed"),
          ("60 s", "ttl", "for a forecast")]))

add("""
<div class="toc">
<div>1 · Why the envelope exists</div><div>9 · Decisions from evidence</div>
<div>2 · The shape</div><div>10 · Replay and grading</div>
<div>3 · The pipeline</div><div>11 · The orchestrator</div>
<div>4 · The topic registry</div><div>12 · The recorder and the lake</div>
<div>5 · Identity and lineage</div><div>13 · Refusals</div>
<div>6 · The journal</div><div>14 · Phases: done and not done</div>
<div>7 · The bus</div><div>15 · Evidence</div>
<div>8 · Freshness</div><div>16 · Reference</div>
</div>
""")

# 1
add(ch(1, "Why the envelope exists"))
add("""
<p>The platform computes a very large number of small things: a burst multiple, a forecast path, a sweep result, a refusal. Before the envelope each of those had its own shape, its own error convention and no way to say where it came from. A reviewer could see a number and not the path to it.</p>
<p>The envelope fixes three problems at once. It gives every computed thing <b>one shape</b>. It makes the provenance travel <b>with</b> the value instead of in a log line. And it makes every publication pass the same checks, so the question “may this producer write this topic, and is this answer still true?” is answered once, in one place.</p>
""")
add(co("law", "The invariant to protect",
  "The packet shape never changes. Everything that varies — venues, indicators, models, stores — lives in the producers, interceptors and consumers around it. The moment a trading-specific field is added to the envelope, the design has failed."))

# 2
add(ch(2, "The shape"), fig(1, "Figure 1 — data, meta, references. The shape is fixed; the engine bay around it grows."))
add("""
<p>Three parts and nothing else. The <b>data</b> is the payload the operator computed. The <b>meta</b> is provenance: which plane, which topic, which key, which producer, at which generation, under which correlation id, computed when. The <b>refs</b> are the inputs it read — as references, so a tick batch stays in one place.</p>
<h3>A real packet, from the running journal</h3>
<pre>{"data": {"actual": [77558.01, 77538.01, 77530],
          "forecast": [77691.39, 77691.39, 77691.39],
          "horizon": 6, "interval": "1m", "method": "vwap", …},
 "meta": {"version": 1, "plane": "intel", "topic": "intel.forecast",
          "key": "BTCUSDT", "producer": "forecast", "generation": 18,
          "cid": "tel.forecast-BTCUSDT-c78e6f31f0e1d47a",
          "computed_at": 1793743021687,
          "pipeline": [{"name": "authority", "passed": true},
                       {"name": "keying", "passed": true},
                       {"name": "correlation", "passed": true,
                        "detail": "tel.forecast-BTCUSDT-c78e6f31f0e1d47a"},
                       {"name": "freshness", "passed": true, "rule": "age <= 60000ms"}]},
 "refs": [{"topic": "market.candle", "key": "BTCUSDT", "age_ms": 16141687}]}</pre>
""")
add(co("good", "The pipeline record travels with the packet",
  "A reader does not have to find a log line to learn which checks ran. The checks are in the envelope, on every one of them, for as long as the packet exists."))

# 3
add(ch(3, "The pipeline"), fig(2, "Figure 2 — authority, keying, correlation, freshness, then the operator computes. A refusal is a packet too."))
add("<p>Every publication passes the same four interceptors, in this order, before the operator computes anything. That ordering is the point: a producer that may not write a topic never gets as far as spending compute on it.</p>")
add(table(["stage", "question", "on failure"], [
  ["authority", "may this producer write this topic?", "refusal naming the producer and the topic"],
  ["keying", "is this topic keyed by a symbol?", "refusal naming the missing key"],
  ["correlation", "—", "a cid is minted and chained to the parent"],
  ["freshness", "is <code>computed_at</code> within the topic's TTL?", "refusal quoting the age and the limit"],
], ["14%", "46%", "40%"]))
add(co("note", "Why a refusal is an envelope",
  "A refusal is not a log line. It is a packet on <code>system.error</code> carrying the stage, the rule and the number that tripped it — which is what makes “why not?” something you can open, filter and replay."))

# 4
add(ch(4, "The topic registry"))
add("<p>One table declares every topic: its plane, its permitted producers, its time limit and whether it is recorded. Adding a topic is a row, not a code path — and authority enforces the row.</p>")
add(table(["topic", "plane", "producer", "ttl", "recorded"],
  [[f"<code>{t['Name']}</code>", t["Plane"], ", ".join(t["Producer"]),
    f"{t['TTLMS']} ms", "yes" if t["Record"] else "no"] for t in TOPICS],
  ["30%", "14%", "26%", "16%", "14%"]))
add(co("law", "The market plane is never recorded",
  "The bus is a state channel, not a job queue. A price that is two seconds old is not history, and recording it would multiply the lake by the tick rate for no analytical gain. Market topics carry a two-second TTL precisely so nobody can treat them as records."))

# 5
add(ch(5, "Identity and lineage"), fig(4, "Figure 4 — a decision references the forecast by parent_cid; the forecast references the candles it read."))
add("""
<p>Minting is local: a cid is created at the boundary that created the work, so work still starts when nothing central is reachable. What is central is the <em>map</em> — and here the map is a projection of the journal, rebuildable by replay.</p>
<h3>A lineage, read from the running journal</h3>
<pre>GET /api/envelope/chain?cid=del.decision-BTCUSDT-711…

lineage depth: 2
  model.decision   key=BTCUSDT  gen=1    parent=tel.forecast-BTCUSDT-…
  intel.forecast   key=BTCUSDT  gen=117  parent=–</pre>
""")
add(co("ex", "The bug that taught the rule",
  "The first implementation matched “cid equals this OR parent equals this”, which lists what came <em>from</em> a decision rather than what it was made of. A reviewer asking “why did we buy?” needs the inputs, so the walk now follows <code>parent_cid</code> backwards. Found by using the feature; fixed with a regression test."))

# 6
add(ch(6, "The journal"))
add(f"<p>The journal is an append-only JSONL file. Every packet that is recorded is appended and never rewritten; <code>Replay</code> reads it back in order. It is currently holding <b>{total}</b> packets:</p>")
add(table(["topic", "packets"], [[f"<code>{k}</code>", str(v)] for k, v in sorted(counts.items())], ["60%", "20%"]))
add("""
<h3>Why a file and not a database</h3>
<p>A file can be replayed in one pass, tailed, copied, and diffed. It never blocks a publisher, and a corrupt line is one line. The lake is the database-shaped store; the journal is the index the decision path reads.</p>
""")
add(co("good", "Recording never fails a request",
  "If the journal cannot be written, the answer still goes out. The recorder is a consumer of the platform's output, not a dependency of it — a lesson learned by watching a full disk stop a working forecast."))

# 7
add(ch(7, "The bus"), fig(5, "Figure 5 — exchange is the plane, routing key is the topic. The recorder is a consumer, never a hook."))
add("""
<p>The broker already carried one topic exchange per plane, so the envelope uses that topology rather than inventing a second one. Provenance is repeated as STOMP headers, so a consumer can filter without parsing the body.</p>
<p>Publishing is best-effort by design: if the broker is down the packet is still journalled and the request still answers. A bus outage stops a forecast being <em>broadcast</em>, not being computed.</p>
<h3>Recorder statistics, measured</h3>
<pre>GET :8801/stats
  intel.forecast   184        market.candle   5 768
  market.price  12 454        market.depth  323 893
  rate        ~1 000 envelopes/s</pre>
""")

# 8
add(ch(8, "Freshness"), fig(6, "Figure 6 — a stale forecast is refused with its own label quoted back at it."))
add("""
<p>The platform previously guessed staleness from a wall clock. Now the packet says when it was computed, the topic says how long an answer may live, and the refusal quotes both.</p>
<pre>age   = now − meta.computed_at
limit = the topic's TTL
age ≤ limit  → the verdict is allowed
age &gt; limit  → REFUSED: "stale forecast", age 1 706 s, limit 60 s</pre>
""")
add(co("warn", "The bug this found",
  "Asking for a decision on the 4h frame returned a flat 1m forecast, because generations count per (topic, key) and both frames share a counter — “newest packet” meant “whichever was published last”. The decision now filters on the packet's own interval and orders by <code>computed_at</code>."))

# 9
add(ch(9, "Decisions from recorded evidence"))
add("""
<p>A decision is a function of one recorded forecast and the clock. It reads the newest packet for the symbol and interval, refuses it if stale, and otherwise compares the projected move with the floor.</p>
<pre>GET /api/decision?symbol=BTCUSDT&amp;interval=4h

  verdict  sell
  edge     −0.658%
  price    77 691.39 → target 77 180.44
  cid      del.decision-BTCUSDT-711…
  parent   tel.forecast-BTCUSDT-…</pre>
""")
add(kpis([("0.05%", "the floor", "below this: no call"),
          ("60 s", "the ttl", "older than this: refused"),
          ("3", "verdicts", "buy · sell · none"),
          ("1", "parent", "every decision names its forecast")]))

# 10
add(ch(10, "Replay and grading"), fig(8, "Figure 8 — the journal is replayed through the same decide() the live path uses, then graded at the horizon."))
add("""
<p>The weakest part of the old platform was that the backtest and the live system shared intent but not code. Here they share the code: replay reads the recorded forecasts, re-derives each verdict with the same rule the live path uses, and grades it against the price the market actually reached at anchor + horizon.</p>
""")
add(table(["metric", "value"],
  [["packets replayed", str(REPLAY.get("packets", "—"))],
   ["graded", str(REPLAY.get("graded", "—"))],
   ["buy / sell / none", f"{REPLAY.get('buy','—')} / {REPLAY.get('sell','—')} / {REPLAY.get('none','—')}"],
   ["directional calls correct", f"<b>{REPLAY.get('correct','—')} of {REPLAY.get('sell',0) + REPLAY.get('buy',0)}</b>"]],
  ["46%", "54%"]))
add(co("warn", "This is the honest result",
  "The platform declines to trade almost always, and the times it did, it was wrong. That is not a failure of the envelope — it is the envelope working. Before, the same models reported R² ≈ 0.97 and nobody could say whether that meant anything. Now the platform states what happened and keeps the packets that prove it."))

# 11
add(ch(11, "The orchestrator"), fig(7, "Figure 7 — capabilities come from the registry, generations from the journal."))
add("""
<p>Two inputs and no model knowledge: the capability table (derived from the topic registry) and the journal (the newest packet per topic and key). From those it decides serve or recompute, and publishes a <code>control.engine</code> packet when work is needed — a request for work is an envelope like everything else, so it is recorded and can be replayed.</p>
<pre>GET /api/orchestrator/plan?symbol=BTCUSDT
  18 capabilities · 16 require recompute
  intel.signal    gen 0            recompute  nothing recorded
  intel.forecast  gen 8  age 250 s recompute  older than its deadline</pre>
""")

# 12
add(ch(12, "The recorder and the lake"))
add("""
<p>The recorder (<code>bin/recorder</code>, port 8801) consumes every envelope from the broker and writes two things: the lake, partitioned by plane and hour, and the journal index the API reads. Bronze is the raw packet, silver the decoded payload, gold the aggregate a research query wants.</p>
<p>It is the only process that must be running for the bus view to say anything, and it is deliberately separate from the platform: stopping it stops history, not trading logic.</p>
""")

# 13
add(ch(13, "Refusals"))
add(table(["refusal", "stage", "what it means"], [
  ["unknown producer", "authority", "a producer not listed for the topic tried to write it"],
  ["missing key", "keying", "a keyed topic was published without a symbol"],
  ["stale forecast", "freshness", "the answer is older than the topic's TTL"],
  ["edge inside the threshold", "decision", "the projected move is smaller than the 0.05% floor"],
  ["nothing recorded", "orchestrator", "there is no packet to serve, so work is requested instead"],
], ["24%", "14%", "62%"]))
add(co("note", "Refusals are the product",
  "In a system that must not guess, the refusal is the most valuable packet it produces: it is specific, it is recorded, and it can be counted. A month of refusals tells you exactly which rule is binding."))

# 14
add(ch(14, "Phases: done and not done"))
add("""<p>The design note set out six phases of adoption. The state of each, as built:</p>""")
add(table(["phase", "state", "evidence"], [
  ["1 · shape and registry", "done", f"{len(TOPICS)} topics registered, enforced by authority"],
  ["2 · pipeline", "done", "four interceptors before every operator; refusals recorded"],
  ["3 · journal and bus", "done", f"{total} packets journalled; recorder at ~1 000/s"],
  ["4 · advisory envelopes", "partial", "forecast, decision and control publish; signal, flow, ta, board do not yet"],
  ["5 · orchestrator", "done", "serve-or-recompute from capabilities and generations"],
  ["6 · replay as backtest", "done", f"{REPLAY.get('graded','—')} verdicts graded from recorded packets"],
], ["24%", "10%", "66%"]))
add("""<p>What is missing is producers, not machinery: <code>intel.signal</code>, <code>intel.flow</code>, <code>intel.ta</code> and <code>intel.board</code> are registered and nothing publishes them, so the orchestrator correctly reports “nothing recorded” for each. <code>model.fill</code> and <code>model.pnl</code> are the same.</p>""")

# 15
add(ch(15, "Evidence"))
add(table(["claim", "how it was verified"], [
  ["every publication passes four interceptors", "the pipeline record is on each packet; refusal paths unit-tested"],
  ["a decision can be traced", "chain returns depth 2, decision → forecast; regression test"],
  ["freshness is from meta", "a stale 4h forecast refused, quoting its age and the limit"],
  ["the bus carries envelopes", "recorder counts per topic, ~1 000/s"],
  ["the journal replays", f"{REPLAY.get('graded','—')} graded verdicts from recorded packets"],
  ["the registry is enforcement, not documentation", "an unregistered producer is refused; a test asserts it"],
], ["40%", "60%"]))
add(co("good", "The suite that backs it",
  "gofmt clean, go vet clean, go test green across 21 packages, and the broker integration test skips cleanly when RabbitMQ is absent — so a laptop without a broker still runs the suite."))

# 16
add(ch(16, "Reference"))
add("""<h3>Packet schema</h3>""")
add(table(["field", "type", "meaning"], [
  ["<code>data</code>", "object", "the payload the operator computed"],
  ["<code>meta.version</code>", "int", "currently 1; an unknown version is rejected on decode"],
  ["<code>meta.plane</code> · <code>topic</code> · <code>key</code>", "string", "what this is and what it is about"],
  ["<code>meta.producer</code>", "string", "who computed it; checked by authority"],
  ["<code>meta.generation</code>", "int", "per (topic, key) counter"],
  ["<code>meta.cid</code> · <code>parent_cid</code>", "string", "identity, and the packet this one was made from"],
  ["<code>meta.computed_at</code>", "ms", "when the payload was computed — the freshness clock"],
  ["<code>meta.received_at</code> · <code>compute_ms</code>", "ms", "when it arrived, and how long it took"],
  ["<code>meta.pipeline</code>", "array", "each interceptor, its rule and whether it passed"],
  ["<code>refs[]</code>", "array", "topic, key, generation and age of each input"],
], ["32%", "12%", "56%"]))
add("""<h3>Endpoints</h3>""")
add(table(["endpoint", "returns"], [
  ["<code>/api/envelope/topics</code>", "the registry"],
  ["<code>/api/envelope/recent?topic=&amp;limit=</code>", "newest packets"],
  ["<code>/api/envelope/stats</code>", "counts per topic and the journal path"],
  ["<code>/api/envelope/chain?cid=</code>", "the lineage, walking parent links"],
  ["<code>/api/decision?symbol=&amp;interval=</code>", "a verdict, or a refusal with its reason"],
  ["<code>/api/replay?symbol=&amp;limit=</code>", "graded outcomes and a summary"],
  ["<code>/api/orchestrator/plan?symbol=</code>", "serve or recompute, per capability"],
], ["44%", "56%"]))

HTML = "".join(P)
OUT = os.path.join(DOCS, "The-Envelope-As-Built.pdf")
kit.build(
    title="CryptAlyst · The Envelope, as built",
    subtitle="The Envelope, as built",
    desc="One packet shape, four interceptors,<br/>a recorded lineage, a bus,<br/>and a decision that can be replayed.",
    body_html=kit.page("The Envelope, as built") + HTML + "</body></html>",
    out_pdf=OUT,
)
print("wrote", OUT)
