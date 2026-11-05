#!/usr/bin/env python3
"""Build the CryptAlyst Platform Master Plan as a document in the house style.

The plan's own text is kept, in its own order, and converted rather than retyped. What is
added is the structure a long document needs: a cover, coloured chapter bands, a contents
page, figure numbering, callouts, and the engine-bay chapter — interceptors, operators and
transformers — which the original treated in a single bullet.
"""
import json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.dirname(HERE)
sys.path.insert(0, DOCS)
sys.path.insert(0, HERE)
import buildkit as kit
from mdtohtml import convert

SRC = os.path.join(DOCS, "PLATFORM-MASTER-PLAN.md")
md = open(SRC).read()

def figfile(n, caption):
    path = os.path.join(HERE, f"f{n}.png")
    return kit.fig(path, caption)

def engine_fig(n, caption):
    return kit.fig(os.path.join(HERE, f"e{n}.png"), caption)

# ---------------------------------------------------------------- front matter
front = """
<h1>CryptAlyst Platform Master Plan</h1>
<p class="lede">Forecasting, pump-and-dump detection, and the path to DMA auto-execution — the whole platform, verified, sequenced, and honest about what is not built.</p>
<p class="meta">Version 1.0 · plan only · no part of this document is implemented until the human says go</p>
"""
front += kit.kpis([("18", "parts", "each with gates"),
                   ("6", "phases", "correctness → DMA"),
                   ("55", "figures", "all redrawn to one palette"),
                   ("0", "lines of code", "changed by this plan")])

TOC_PLACEHOLDER = "<<TOC>>"


ABOUT = """
<h2>About this edition</h2>
<p>This is the same plan, set as a document. Every part, every table, every figure and every
number is the author's; nothing has been dropped, and the sequence — correctness, detection,
envelope, edge, efficiency, DMA — is unchanged. What this edition adds:</p>
<ul>
<li><b>A chapter the original did not have.</b> The envelope was described in a single bullet;
it now has its own chapter covering the two roles the plan never named — <b>operators</b> and
<b>transformers</b> — as well as the interceptor chain written out stage by stage, with the two
interceptors I would add and the reason for each.</li>
<li><b>One palette across every figure.</b> The plan's fifty-one diagrams are redrawn in the
role colours used by the platform's other references, so a colour means the same thing in
every document in the set.</li>
<li><b>Typography and pagination</b> per the house style guide, with a cover, chapter bands, a
contents page and numbered pages.</li>
</ul>
<div class="co law"><b class="t">Reviewer's note</b>
The plan is unusually honest for a roadmap: it states what the verification found, including
the defects that flattered the numbers, and it refuses to promise minute-scale detection that
the literature cannot deliver. Its weakest claim is not technical — it is the assumption that
six registered-but-unpublished topics are cheap to fill. They are cheap in code; they are not
cheap in judgement, because each one starts producing evidence that will be graded. The
engine-bay chapter says so where it belongs, rather than in this note.
</div>
"""

# ---------------------------------------------------------------- the engine bay chapter
ENGINE = """
<h2 class="chapter"><span class="num">E</span>The engine bay: interceptors, operators, transformers</h2>
<p class="lede">Added by the second author. The plan described the envelope in one bullet; the envelope is the
part of this platform that everything else leans on, so it gets a chapter of its own — including the two
roles the original never named.</p>

<p>The envelope is not a message format. It is a <b>contract with three roles around it</b>, and the contract
only works because the roles are separate. A packet is immutable; the interceptors decide whether it may
exist; the operator decides what it says; the transformer decides what shape it takes on the way out.
Everything else in this document — the backtest being a replay, a decision having lineage, freshness
stopping a stale execution — is a consequence of keeping those four things apart.</p>
"""
ENGINE += engine_fig(1, "Figure E1 — the fixed shape and the three roles around it. The left side never changes; the right side grows forever.")
ENGINE += """
<h3>Why the three roles must stay separate</h3>
<p>They answer three different questions, and collapsing any two of them is how trading systems become
unexplainable:</p>
<dl class="defs">
<dt>An interceptor answers “may this happen?”</dt>
<dd>It never computes a value. It can only allow or refuse, and when it refuses it must say which rule
failed and quote the numbers. It runs <b>before</b> the compute, so a producer that may not write a topic
never spends a microsecond of CPU on one.</dd>
<dt>An operator answers “what is true?”</dt>
<dd>It is the only thing in the system allowed to produce a number. A forecast, a signal, a decision, a
sweep result, a fill — each is an operator. Operators are pure in the sense that matters here: given the
same inputs and the same generation, they produce the same output, which is what makes replay admissible
as a backtest.</dd>
<dt>A transformer answers “what shape?”</dt>
<dd>It may resample, normalise, aggregate or project, and it must not change the meaning. If a transformer
can alter a verdict, it is an operator wearing the wrong hat — and the difference between the two is
exactly the difference between a system you can audit and one you cannot.</dd>
</dl>
"""
ENGINE += kit.callout("law", "The test that keeps the roles honest",
  "If you can describe the step as “it decides”, it is an interceptor. If it produces a number that another "
  "step will believe, it is an operator. If the number is the same before and after and only the shape "
  "changed, it is a transformer. Any step that is two of these at once is a bug in the design, not a "
  "shortcut in the code.")

ENGINE += """
<h3>The interceptor chain, in the order it runs</h3>
<p>The plan names four; the code has four. The two in the middle of the diagram below are the ones I would
add next, and I say why underneath.</p>
"""
ENGINE += engine_fig(2, "Figure E2 — the chain. Each stage can only refuse; the operator is the first thing allowed to compute.")
ENGINE += kit.table(["interceptor", "question it asks", "the refusal it writes"], [
  ["<b>authority</b>", "is this producer registered for this topic?", "names the producer and the topic"],
  ["<b>keying</b>", "is a keyed topic published with its key?", "names the missing key"],
  ["<b>correlation</b>", "—", "mints the cid and chains parent_cid; cannot refuse"],
  ["<b>freshness</b>", "is computed_at within the topic's TTL?", "quotes the age and the limit"],
  ["<i>schema</i> (proposed)", "does the payload decode into the declared shape?", "names the field that failed"],
  ["<i>budget</i> (proposed)", "is this operator within its cost or rate?", "quotes the budget and the spend"],
], ["18%", "44%", "38%"])
ENGINE += kit.callout("note", "Why schema and budget are the next two, and not more",
  "Schema is cheap and catches the class of bug that survives every type checker: a payload that is valid "
  "JSON and wrong in meaning. Budget is the one that keeps a GPU sweep from starving a forecast during a "
  "volatile hour. I would not add more than these two: every interceptor is a veto on the hot path, and a "
  "chain of ten is a system that refuses to work.")

ENGINE += """
<h3>The operators: the only things that compute</h3>
<p>This is the catalogue the plan implies but never writes down. Each row is a producer of a topic, and the
topic registry is the promise that it is the only producer allowed to write it.</p>
"""
ENGINE += engine_fig(3, "Figure E3 — operators compute; transformers reshape. Both end at the same packet shape.")
ENGINE += kit.table(["operator", "input", "output topic", "state"], [
  ["forecast", "bars for a symbol and frame", "<code>intel.forecast</code>", "shipped"],
  ["signal / detection", "tape, book, baselines", "<code>intel.signal</code>", "registered, no producer"],
  ["movers · ta · board", "universe, candles", "<code>intel.movers</code>, <code>intel.ta</code>, <code>intel.board</code>", "registered, no producer"],
  ["sweep", "grid, history window", "<code>model.sweep</code>", "shipped (GPU agent)"],
  ["decision", "one recorded forecast + the clock", "<code>model.decision</code>", "shipped"],
  ["backtest / replay", "the journal", "<code>intel.backtest</code>", "shipped"],
  ["fill · pnl", "order, ledger", "<code>model.fill</code>, <code>model.pnl</code>", "registered, no producer"],
  ["control", "capabilities and generations", "<code>control.engine</code>", "shipped"],
], ["20%", "30%", "32%", "18%"])
ENGINE += kit.callout("warn", "The gap this table exposes",
  "Six of the nine operators are registered and have no producer. That is the honest state of the envelope "
  "today, and it is also the cheapest work in the whole plan: each of those rows is a topic that already "
  "has a TTL, an authority rule and a place in the orchestrator's schedule, waiting for something to publish "
  "it. Everything downstream — the replay scorecard, the detection evaluation, the decision trail — is "
  "starved by exactly those six rows.")

ENGINE += """
<h3>The transformers: the shape, not the meaning</h3>
<p>Transformers are the part people skip when they design a pipeline like this, and then reinvent badly in
the view layer. Naming them has one practical benefit: the dashboard stops doing arithmetic on the wire
payload, because the shape it wants already exists as a transformer with a test.</p>
"""
ENGINE += kit.table(["transformer", "from", "to", "rule it must obey"], [
  ["rollup", "ticks or trades", "candles for a frame", "a bar's close is the last trade at or before its close_time"],
  ["resample", "candles of one frame", "candles of another", "never interpolate a missing bar; mark it"],
  ["normalise", "raw units", "the platform's units", "no rounding that changes a verdict — round at the edge, once"],
  ["project", "a packet", "a view payload", "every projected field traces to a packet field, or to a documented derivation"],
  ["redact", "a packet", "a caller-safe subset", "removal only; a redacted packet never gains a field"],
], ["16%", "26%", "26%", "32%"])
ENGINE += kit.callout("ex", "The one transformer I would write first",
  "<b>project</b>. Every panel in the dashboard currently derives its own view from a raw payload, and every "
  "one of those derivations is a small piece of untested arithmetic. One projection transformer, one test "
  "per panel, and the view stops being a place where numbers can quietly change.")

ENGINE += """
<h3>One packet's life, with the clock</h3>
"""
ENGINE += engine_fig(4, "Figure E4 — the order, and the only rule that matters for latency: after the journal append, nothing can fail the request.")
ENGINE += """
<p>The timings in the diagram are the design's intent, not a measurement, and the design has one hard edge:
<b>the journal append is the commit point</b>. Before it, a failure means the packet does not exist. After it,
a failure means the packet exists and some consumer did not hear about it — which is recoverable by
replaying the journal, and is therefore not allowed to fail the producer's request. That is why the bus is
best-effort and the journal is not.</p>
"""
ENGINE += kit.callout("good", "What this buys the rest of the plan",
  "<b>Part 5 becomes bookkeeping.</b> “Adopt the envelope for forecasting” is not a refactor of the forecast "
  "engine; it is adding producers to topics that already exist, with interceptors that already run and a "
  "journal that already replays. <b>Part 8's freshness gate becomes free</b>, because the TTL is already "
  "declared per topic and enforced before compute. And <b>the detection evaluation in Part 4 gets its "
  "labelled dataset</b>, because every alert is an envelope with the refs that produced it.")

ENGINE += """
<h3>My assessment, in four sentences</h3>
<p>The envelope is the best idea in this codebase and the least finished. The parts that exist are genuinely
load-bearing — freshness before compute, lineage by cid, replay as backtest — and I have watched each of
them catch a real defect during the last sweep. The parts that do not exist are all producers, not
machinery, which is why the work is cheap and why it has not happened: there is no failing test that says
“<code>intel.signal</code> has no producer”. The one thing I would change about the design is to make that
absence loud — an orchestrator that publishes a <code>control.engine</code> request every tick already
knows which capabilities are starving, so the platform could report its own incompleteness on the dashboard
instead of only in this document.
"""

# ---------------------------------------------------------------- assemble
body_md = md
# drop the original title block and contents: the builder supplies both
body_md = body_md[body_md.index("# A History of the Platform"):]
# The engine-bay chapter is HTML, so it is joined after conversion — passing it through the
# markdown reader would escape the figure tags and print them as text.
marker = "# Part 3 — Phase 1: Correctness (the foundation)"
head_md, tail_md = body_md.split(marker, 1)
head_html, next_fig = convert(head_md, HERE, figfile)
tail_html, _ = convert(marker + tail_md, HERE, figfile, start=next_fig)
html = head_html + ENGINE + tail_html

# the plan's own # headings become chapter bands, numbered in order
# The plan numbers its own parts, so the band carries that number rather than a counter:
# "Part 5" stays Part 5, and the history and the appendices get no number at all.
chapters = []

def label_for(title):
    m = re.match(r"Part\s+(\d+)", title)
    if m:
        return "P" + m.group(1)
    m = re.match(r"Appendix\s+([A-Z])", title)
    if m:
        return m.group(1)
    return ""

def chapterise(m):
    title = m.group(1)
    label = label_for(title)
    chapters.append((len(chapters) + 1, label, title))
    chip = f'<span class="num">{label}</span>' if label else ""
    return f'<h2 class="chapter">{chip}{title}</h2>'

html = re.sub(r"<h2>(.*?)</h2>", chapterise, html)
chapters.append((99, "E", "The engine bay: interceptors, operators, transformers"))

toc = '<div class="toc">' + "".join(
    f'<div>{re.sub(r"<[^>]+>", "", t)}</div>' for _, _, t in chapters) + "</div>"

body = front + toc + ABOUT + html
OUT = os.path.join(DOCS, "CryptAlyst-Platform-Master-Plan.pdf")
kit.build(
    title="CryptAlyst · Platform Master Plan",
    subtitle="Platform Master Plan",
    desc="Forecasting, pump-and-dump detection,<br/>and the path to DMA auto-execution.",
    body_html=kit.page("CryptAlyst Platform Master Plan") + body + "</body></html>",
    out_pdf=OUT,
)
print("wrote", OUT, "| chapters:", len(chapters))
