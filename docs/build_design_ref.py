#!/usr/bin/env python3
"""Build the official CryptAlyst design and architecture reference.

A4, live page numbers, Mermaid diagrams fitted to the page in both directions.
Every use case is a four-column block: need, ATLAS mechanism, how CryptAlyst
delivers it, and the criterion that proves delivery.
"""
import os
from docx import Document
from docx.shared import Pt, Inches, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
DIA = os.path.join(HERE, "envelope")
ACCENT = RGBColor(0x1F, 0x4E, 0x79)
MUTED = RGBColor(0x59, 0x63, 0x70)
CONTENT_W = 21.0 / 2.54 - 1.6      # 6.67 in
MAX_FIG_H = 7.9

doc = Document()
for s in doc.sections:
    s.page_width = Cm(21.0)
    s.page_height = Cm(29.7)
    s.left_margin = Inches(0.8); s.right_margin = Inches(0.8)
    s.top_margin = Inches(0.75); s.bottom_margin = Inches(0.75)

st = doc.styles["Normal"]
st.font.name = "Calibri"; st.font.size = Pt(10)
st.paragraph_format.space_after = Pt(5); st.paragraph_format.line_spacing = 1.1
for name, size, col in (("Heading 1", 15.5, ACCENT), ("Heading 2", 12, ACCENT), ("Heading 3", 10.5, MUTED)):
    h = doc.styles[name]
    h.font.name = "Calibri"; h.font.size = Pt(size); h.font.color.rgb = col
    h.paragraph_format.space_before = Pt(10 if name == "Heading 1" else 8)


def h(text, level=1):
    doc.add_heading(text, level=level)


def p(text, bold=False, italic=False, size=None, colour=None):
    para = doc.add_paragraph()
    r = para.add_run(text)
    r.bold = bold; r.italic = italic
    if size: r.font.size = Pt(size)
    if colour: r.font.color.rgb = colour
    return para


def bullets(items):
    for it in items:
        doc.add_paragraph(it, style="List Bullet")


def mono(lines):
    for line in lines:
        para = doc.add_paragraph()
        para.paragraph_format.space_after = Pt(0)
        para.paragraph_format.left_indent = Inches(0.22)
        r = para.add_run(line)
        r.font.name = "Courier New"; r.font.size = Pt(8)
        shd = OxmlElement("w:shd"); shd.set(qn("w:fill"), "F1F3F6")
        para._p.get_or_add_pPr().append(shd)


def table(headers, rows, widths=None, size=8.3):
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = "Light Grid Accent 1"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    for i, name in enumerate(headers):
        cell = t.rows[0].cells[i]; cell.text = ""
        r = cell.paragraphs[0].add_run(name); r.bold = True; r.font.size = Pt(8.6)
    for row in rows:
        cells = t.add_row().cells
        for i, v in enumerate(row):
            cells[i].text = ""
            r = cells[i].paragraphs[0].add_run(str(v)); r.font.size = Pt(size)
    if widths:
        for row in t.rows:
            for i, w in enumerate(widths):
                row.cells[i].width = Inches(w)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)
    return t


def figure(name, caption):
    path = os.path.join(DIA, name)
    if not os.path.exists(path):
        return
    w_px, h_px = Image.open(path).size
    aspect = w_px / h_px
    draw_w = min(CONTENT_W, MAX_FIG_H * aspect)
    draw_h = draw_w / aspect
    if draw_h > MAX_FIG_H:
        draw_h = MAX_FIG_H; draw_w = draw_h * aspect
    doc.add_picture(path, width=Inches(draw_w), height=Inches(draw_h))
    doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
    p(caption, italic=True, size=8, colour=MUTED)


def callout(text):
    t = doc.add_table(rows=1, cols=1)
    t.style = "Light List Accent 1"
    cell = t.rows[0].cells[0]; cell.text = ""
    r = cell.paragraphs[0].add_run(text); r.font.size = Pt(9)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)


def brk():
    doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)


def usecase(code, title, context, need, atlas, deliver, criterion, figure_name=None):
    h("%s  %s" % (code, title), 2)
    p(context, italic=True, size=9.5, colour=MUTED)
    table(["The need", "What ATLAS provides", "How CryptAlyst delivers it", "Proof of delivery"],
          [[need, atlas, deliver, criterion]], widths=[1.55, 1.9, 1.9, 1.25], size=7.9)
    if figure_name:
        figure(figure_name, "Figure — %s" % title)

# ============================================================ cover
doc.add_paragraph(); doc.add_paragraph()
t = doc.add_paragraph(); t.alignment = WD_ALIGN_PARAGRAPH.CENTER
r = t.add_run("CRYPTALYST"); r.font.size = Pt(36); r.bold = True; r.font.color.rgb = ACCENT
s2 = doc.add_paragraph(); s2.alignment = WD_ALIGN_PARAGRAPH.CENTER
r = s2.add_run("Design and Architecture Reference"); r.font.size = Pt(17); r.font.color.rgb = MUTED
s3 = doc.add_paragraph(); s3.alignment = WD_ALIGN_PARAGRAPH.CENTER
r = s3.add_run("Edition 2 · envelope · correlation · data lake · orchestrator · security · ticker")
r.font.size = Pt(10); r.italic = True; r.font.color.rgb = MUTED

doc.add_paragraph()
table(["Field", "Value"], [
    ["Document", "CryptAlyst Design and Architecture Reference"],
    ["Edition", "2 — supersedes the platform reference for architecture decisions"],
    ["Status", "living document; parts I to III and V normative, part IV current measurements"],
    ["Audience", "engineers, quants, risk, operations, and anyone asked to change the platform"],
    ["Scope", "the running paper-trading platform and the ATLAS design parts adopted into it"],
    ["Companion", "CryptAlyst-Platform-Reference.docx (model mathematics and defect ledger)"],
], widths=[1.3, 5.2], size=9)

callout("Reading order. Part I explains what the platform is and the principles it is held to. Part II is the "
        "architecture itself. Part III is sixteen use cases — each one a need, the ATLAS mechanism that answers "
        "it, how this platform delivers it, and the criterion that proves it. Part IV is the measured evidence, "
        "the delivery plan and the decisions still open. Part V chooses the storage, and specifies the security "
        "and ticker services in the same style.")

brk()
# ============================================================ contents
h("Contents", 1)
contents = [
    ("Part I — Context", [
        "1  Executive summary",
        "2  Principles the platform is held to",
        "3  The platform as it stands",
        "4  Goals, non-goals and constraints",
    ]),
    ("Part II — Architecture", [
        "5  Four planes and one bus",
        "6  The envelope",
        "7  The correlation service",
        "8  The agnostic data lake",
        "9  The orchestrator",
        "10  Bus topology, priority and quality of service",
        "11  Implementation map",
    ]),
    ("Part III — Use cases", [
        "12  Sixteen use cases, from market data to compliance",
    ]),
    ("Part IV — Delivery", [
        "13  Measured performance",
        "14  Test and verification strategy",
        "15  Rollout plan and acceptance gates",
        "16  Risks, open decisions and the invariant",
    ]),
    ("Part V — Storage, security and the ticker service", [
        "18  Storage selection and sizing",
        "19  The security service",
        "20  The ticker service",
        "21  Appendix — topics, glossary, file map",
        "Appendix A — Database technology considerations",
        "Appendix B — Compliance",
        "Appendix C — Trade replay",
    ]),
]
for part, items in contents:
    h(part, 2)
    for it in items:
        p(it, size=9.5)

brk()
# ============================================================ PART I
h("Part I — Context", 1)

h("1  Executive summary", 1)
p("CryptAlyst is a paper-trading platform for crypto markets. A Go market-data service streams 161 websocket "
  "channels from the exchange; a RabbitMQ bus carries nineteen topics; thirty Python model books trade against "
  "it; a dashboard shows what happened. It works, and until this edition it had no shared object describing "
  "what a message was, how fresh it was, what produced it, or which inputs it came from.")
p("This edition adopts four parts of the ATLAS design and makes them normative:", bold=True)
bullets([
    "The envelope — one fixed shape carrying data, metadata and references, extensible in metadata only.",
    "The correlation service — minting is local, mapping is central, resolution is gated and recorded.",
    "The agnostic data lake — one API, tiered storage, retention with rotation and compression, replay from disk.",
    "The orchestrator — capabilities and generations instead of process liveness and fixed timers.",
])
p("The claim of the document is not that these are good ideas. It is that each one removes a specific defect "
  "that the platform demonstrably has, and that the removal is measurable. Part IV contains the measurements: "
  "an envelope validates in 2.5 microseconds, the lake writes 400,000 envelopes per second and replays "
  "1,127,000 per second, and the recorder has run for hours with zero errors.")

h("1.1  What changed in this edition", 2)
table(["Area", "Before", "After"], [
    ["Message shape", "bare JSON per producer, no version, no provenance", "envelope with version, plane, producer, generation, correlation id"],
    ["Priority", "claimed in comments", "plane in metadata, separate topology, measured lag"],
    ["Indicators", "recomputed by every consumer", "published once as advisories with references to inputs"],
    ["History", "fills written by each book in its own format", "every envelope recorded once, in tiers, with retention"],
    ["Backtest", "REST candles, different code path", "replay of recorded envelopes through the same calculators"],
    ["Failure", "log lines on disk", "correlation chain: what happened, why, what was refused and on which rule"],
], widths=[1.2, 2.6, 2.7], size=8.6)

h("1.2  The central finding", 2)
p("The platform's problems were not model problems. They were lineage problems. When a trade lost money, no "
  "one could say which advisory caused it, which ticks produced that advisory, or whether the forecast was "
  "forty minutes old. That is why the first three defects in the previous reference kept recurring: they were "
  "symptoms of a missing abstraction, not independent bugs.")
callout("A trading platform without lineage cannot learn. It can only accumulate trades.")

brk()
h("2  Principles the platform is held to", 1)
p("Twelve principles come from ATLAS and are adopted unchanged. They are stated here in trading language "
  "because a principle that cannot be tested against a trade is decoration.")
table(["#", "Principle", "How it is tested here"], [
    ["P1", "One contract", "every message on every topic decodes as an envelope"],
    ["P2", "The shape never changes", "adding a model or a calculator changes no consumer code"],
    ["P3", "Contents are somebody's job", "the payload is validated per topic; metadata is extensible"],
    ["P4", "One identity", "a correlation id follows a decision from tick to fill"],
    ["P5", "Refer, do not copy", "an advisory points at the ticks it used; it does not embed them"],
    ["P6", "Authority before action", "the risk interceptor refuses before the order is constructed"],
    ["P7", "Evidence is an output", "the decision envelope records the refusal and the rule named"],
    ["P8", "Secure by construction", "resolution is gated and every read is itself recorded"],
    ["P9", "No re-engineering", "new capabilities register; nothing behind them changes"],
    ["P10", "Platform agnostic", "storage backends are swappable behind one interface"],
    ["P11", "Honest metadata", "age, generation and producer travel with the value"],
    ["P12", "Incremental forever", "each phase leaves the platform running and measurable"],
], widths=[0.45, 1.7, 4.4], size=8.6)

h("2.1  Three trading-specific principles", 2)
p("The twelve above are general. Three more are specific to trading, and they are the ones that decide whether "
  "the platform is safe to leave running unattended.")
table(["Principle", "Meaning", "Why it is not negotiable"], [
    ["Market data first", "the data plane is never blocked by the compute plane", "a delayed price is a wrong price, and every decision downstream inherits the error"],
    ["Refuse on evidence, not on suspicion", "a refusal quotes the age, generation and rule", "a refusal without evidence cannot be reviewed, and unreviewable refusals become folklore"],
    ["Record what explains, not everything", "advisories and decisions always; market data by policy", "unbounded recording turns the recorder into the outage"],
], widths=[1.5, 2.3, 2.7], size=8.6)

brk()
h("3  The platform as it stands", 1)
p("The inventory below is the running system, not a plan. Ports and ownership matter because the migration is "
  "half complete by design: Go owns the data path, Python owns the books.")

h("3.1  Processes", 2)
table(["Process", "Language", "Port", "Owns", "Talks to"], [
    ["marketdata", "Go", "8796", "exchange streams, prices, depth, candles", "Binance websocket, bus, HTTP readers"],
    ["busconsumer", "Go", "8798", "one consumer per topic queue, tallies", "bus"],
    ["healthpub", "Go", "—", "probes seven services, publishes system.health", "bus"],
    ["signalbus", "Go", "—", "alert intake from the detector", "backend API, bus"],
    ["recorder", "Go", "8801", "the lake: memory, journal, retention", "bus, disk"],
    ["replay", "Go", "—", "re-emits a recorded lake", "disk, bus"],
    ["signals", "Python", "—", "the 20-field signal set and flow confidence", "market data, bus"],
    ["tad", "Go", "8792", "technical analysis per symbol", "bus"],
    ["forecast", "Go", "8790", "the seven-agent blend", "bus"],
    ["supervisor", "Python", "—", "fleet lifecycle for the books", "processes, state"],
    ["master", "Python", "8799", "dashboard, 38 endpoints, SSE", "everything"],
    ["30 model books", "Python", "—", "trading decisions and books", "bus, risk, tactics"],
], widths=[1.0, 0.65, 0.5, 2.5, 1.85], size=8.2)

h("3.2  What the data path looks like", 2)
p("One process touches the exchange. Everything else reads either the bus or a local endpoint. This is the "
  "property that makes the Go migration safe: the feed can be replaced without the books noticing.")
figure("d3.png", "Figure 3.1 — Four planes. Market data has its own; persistence is a consumer, never inline.")

h("3.3  What is not yet true", 2)
table(["Gap", "Consequence today", "Addressed in"], [
    ["Python books do not emit envelopes", "decisions are recorded but not linked to advisories", "phase 5, section 15"],
    ["Orchestrator not built", "dispatch is still fixed-interval loops and a supervisor", "phase 4, section 9"],
    ["Retention not yet enforced on the live lake", "the journal grows without a window", "phase 2, section 8.4"],
    ["Backtest harness still separate", "replay exists but is not wired to the harness", "phase 6, section 15"],
], widths=[1.9, 2.6, 1.9], size=8.4)

brk()
h("4  Goals, non-goals and constraints", 1)
h("4.1  Goals", 2)
table(["Goal", "Measure of success"], [
    ["Every message carries provenance", "100% of recorded envelopes decode with a version, plane and producer"],
    ["A decision can be explained", "any decision resolves to advisory, ticks and candle in one query"],
    ["Backtest equals live", "replaying a window reproduces the same decisions"],
    ["Market data is never blocked", "market plane lag stays under one bar interval while compute saturates"],
    ["Storage is bounded", "lake size has a window per tier and compressed closed segments"],
    ["Adding a model is a registration", "no existing service changes when a model is added"],
], widths=[2.4, 4.1], size=8.6)

h("4.2  Non-goals", 2)
bullets([
    "Real money execution. This platform is paper-only by design and by instruction.",
    "Multi-venue arbitrage. One venue, one feed, one truth; multi-venue is a later envelope plane.",
    "Sub-millisecond colocation. The envelope is microsecond-cheap, but the architecture targets seconds, not ticks.",
    "A general-purpose streaming platform. Every abstraction here exists to explain a trade.",
])

h("4.3  Constraints", 2)
table(["Constraint", "Effect on the design"], [
    ["The books stay Python", "risk, tactics and maker remain in-process libraries; the envelope crosses the bus, not every call"],
    ["One broker (RabbitMQ)", "priority is enforced by topology and pool sizing, not by separate infrastructure"],
    ["A single operator", "no service may require a second person to run or recover"],
    ["Exchange rate limits", "streams only; the boot ranking call is the single permitted REST request and it falls back"],
], widths=[1.9, 4.6], size=8.6)

brk()
h("Part II — Architecture", 1)

h("5  Four planes and one bus", 1)
p("The platform is organised into four planes with different failure modes and different budgets. The bus is "
  "the only thing they share, and the envelope is the only contract on it.")
table(["Plane", "Traffic", "Failure mode", "Policy"], [
    ["Market", "ticks, depth, candles, 24h statistics", "a dropped update means a stale price", "publish first, drop oldest, never block"],
    ["Compute", "advisories, forecasts, TA, regime", "a late advisory is usable if it says so", "at least once, keyed by symbol"],
    ["Action", "decisions, fills, risk state, control", "a lost decision is an audit hole", "durable, ordered per symbol, never dropped"],
    ["Persistence", "everything, recorded once", "a slow recorder must not slow the feed", "consumer only, batched, bounded by retention"],
], widths=[0.9, 1.9, 1.9, 1.8], size=8.5)
p("Two rules follow, and they are the whole of priority in this design:")
bullets([
    "A publisher never fans out. One message per event; the broker copies it.",
    "Market data consumers are disposable. If one falls behind it is dropped and re-syncs; it never applies backpressure to the feed.",
])

h("5.1  Why the bus is the spine", 2)
p("Every dependency in the platform is a subscription. That makes the graph inspectable: what a model needs is "
  "the set of topics it reads, and what it produces is the set it writes. A service that bypasses the bus is "
  "a service whose dependencies cannot be seen, which is why the legacy direct-call paths were removed.")

brk()
h("6  The envelope", 1)
p("The envelope is a fixed shape: data, metadata, references. It is the only contract between the data layer "
  "and everything else. Because the shape is fixed, new requirements do not reopen old code — behaviour lives "
  "around the envelope in operators, interceptors and transformers.")
figure("d1.png", "Figure 6.1 — The fixed shape, and the engine bay that grows around it.")

h("6.1  The three members", 2)
table(["Member", "Carries", "Never carries"], [
    ["data", "the payload for this topic", "storage types, driver handles, or a second copy of another topic's data"],
    ["meta", "version, plane, producer, generation, sequence, computed_at, received_at, compute_ms, cid, refs", "anything a reader must parse to understand the payload"],
    ["refs", "topic, key, generation, cid of an input", "the input itself"],
], widths=[0.8, 3.4, 2.3], size=8.5)

h("6.2  Versioning policy", 2)
p("A reader accepts the current revision and the one before it, refuses anything newer, and treats an absent "
  "version as revision one. The payload is strictly validated: an unknown top-level member is refused. The "
  "metadata is extensible: an unknown meta key is ignored. That asymmetry is deliberate — provenance can grow "
  "without breaking readers, while a malformed payload cannot silently become a trading decision.")
mono([
    "AcceptVersion(0)      -> ok  (pre-versioning revision)",
    "AcceptVersion(1)      -> ok  (current)",
    "AcceptVersion(2)      -> refused  (newer than this build reads)",
    "Decode({data,meta})   -> ok",
    "Decode({data,meta,x}) -> refused  (unknown top-level member)",
    "Decode(meta{x_new})   -> ok  (metadata is extensible)",
])

h("6.3  The lifecycle: append, monitor, satisfy", 2)
p("An envelope is not a packet that dies on delivery. It travels, being monitored, until the minimum "
  "requirements are satisfied; work that can help appends a step and a correlation id.")
figure("d2.png", "Figure 6.2 — One decision, three envelopes. Each stage appends provenance; none changes the shape.")

brk()
h("7  The correlation service", 1)
p("The service maintains the correlation map: which document exists, which needs it carries, who promised what, "
  "which step fulfilled which need, and which actor may do what. It does three jobs — introduce, pair, resolve — "
  "and its design rests on one asymmetry.")
mono([
    "minting is local      a cid is created at the boundary that created the work",
    "mapping is central    someone must own who is doing this for whom",
    "resolution is gated   reading the map is privileged, and every read is recorded",
])
p("That asymmetry is why the service can be down without stopping trading. Work still starts, promises are "
  "still made and fulfilments still append; only introduction and resolution wait.")
figure("d14.png", "Figure 7.1 — Mint anywhere, map centrally, resolve under authority.")

h("7.1  The map is a projection", 2)
p("The map is built from durable records — the journal, the ledger, the capability registry — and never holds "
  "the only copy. It can be rebuilt by replaying, which is what makes a restart a non-event and more than one "
  "instance possible.")

h("7.2  Two rules that keep a trace trustworthy", 2)
bullets([
    "Every output of the service is either a step on an existing envelope or a new envelope. There is no private API that bypasses the contract.",
    "Resolution is recorded on the document being read, so reading the trace becomes part of the trace.",
])
p("The second rule answers a question a desk will ask on day one: who looked at this decision, and when? A "
  "resolver without that property is an audit hole dressed as an audit tool.")

brk()
h("8  The agnostic data lake", 1)
p("The lake solved, in ATLAS, exactly the problem CryptAlyst had: a single JSON file that grew until every "
  "read was a scan and every aggregate was recomputed. The fix was not a faster query. It was a tiered, "
  "sharded, pluggable store behind one envelope API.")
figure("d15.png", "Figure 8.1 — One API, four tiers, each backend replaceable.")

h("8.1  Tiers and what lives in each", 2)
table(["Tier", "Contents", "CryptAlyst use", "Retention"], [
    ["Bronze", "raw envelopes exactly as received", "replay and forensic reconstruction", "hours to days"],
    ["Silver", "cleaned and normalised records", "bars, flow, volatility for research", "days to weeks"],
    ["Gold", "materialised aggregates", "advisories, per-model performance, dashboards", "weeks"],
    ["Archive", "compressed closed segments", "regulatory and historical study", "months"],
], widths=[0.8, 2.0, 2.2, 1.4], size=8.5)

h("8.2  Backends are swappable", 2)
p("Every tier implements one interface: write a record, close. The consumer never learns which store answered, "
  "and the recorder fans one write out to all configured backends. Two are implemented today, both with tests.")
table(["Backend", "Behaviour", "Benchmark"], [
    ["memory", "keeps the latest N per topic, drops oldest", "1.85 microseconds per write"],
    ["segmented-journal", "rotates, compresses closed segments, deletes expired", "2.39 microseconds per write"],
    ["fan-out (both)", "one call, two durable outcomes", "2.41 microseconds per write"],
], widths=[1.5, 3.2, 1.8], size=8.5)

h("8.3  Retention is part of storage", 2)
p("Recording everything is only affordable if what is recorded has a lifetime, so the policy lives with the "
  "storage rather than in a cron job: rotate at a size, compress what was closed, delete what has aged out.")
mono([
    "RetentionConfig{ SegmentSize: 64 MiB, MaxAge: 1h, Compress: true }",
    "seg-<unixnano>.jsonl  -> closed -> seg-<...>.jsonl.gz -> older than MaxAge -> removed",
])
p("Three tests prove it: rotation happens at the size boundary and replay across segments returns every "
  "record; closed segments are compressed and replay still works through the gzip; expired segments are "
  "deleted and counted. Measured compression on live traffic was 44.7% of original size.")

h("8.4  Replay", 2)
p("Replay reads a journal back in order and re-emits it. Because the stored object is the envelope that "
  "travelled, replaying feeds consumers the same objects they saw live. This is the mechanism that makes a "
  "backtest a replay rather than a re-derivation.")
figure("d8.png", "Figure 8.2 — Journal on the left, replay on the right, identical envelopes between them.")

brk()
h("9  The orchestrator", 1)
p("A supervisor restarts processes on a timer. An orchestrator asks for a capability and receives an answer "
  "with metadata attached. The difference is between knowing a service is alive and knowing its answer is "
  "current.")
figure("d9.png", "Figure 9.1 — The orchestrator never knows what a calculator computes; it knows what it can do and whether its answer is fresh.")

h("9.1  Responsibilities", 2)
table(["Responsibility", "Today", "With the orchestrator"], [
    ["What to compute", "every model runs on its own clock", "a capability is requested when an envelope needs it"],
    ["When to recompute", "fixed loop intervals", "generation and computed_at decide whether the answer is current"],
    ["What to restart", "process liveness only", "a consumer whose lag exceeds its contract is rescheduled"],
    ["What to persist", "books write their own JSON", "the recorder owns persistence; models do not"],
    ["What is allowed", "risk checks inline in each model", "an interceptor refuses before the work starts"],
], widths=[1.5, 2.1, 2.9], size=8.5)

h("9.2  Dispatch contract", 2)
p("A capability advertises a name, the topics it consumes, the topics it produces, a deadline, and a "
  "generation. The orchestrator matches an unsatisfied need to a capability, records the introduction as a "
  "step on the document, and expects a fulfilment envelope within the deadline. Nothing about the contract "
  "mentions a process, a port or a language.")
callout("The orchestrator's ignorance is the feature. It is the only component that can add a thirty-first "
        "model without being redeployed.")

brk()
h("10  Bus topology, priority and quality of service", 1)
p("One broker, one topic space, and a policy per class. Priority is enforced by separating pools, not by "
  "trusting a flag.")
table(["Class", "Topics", "Delivery", "Consumer policy", "On overflow"], [
    ["market", "ticks, market.price, market.depth, market.candle, market.mini24h", "at most once", "own pool, small prefetch", "drop oldest"],
    ["intel", "intel.signal, intel.flow, intel.ta, intel.forecast, intel.movers", "at least once", "shared compute pool", "queue, bounded"],
    ["model", "model.decision, model.fill, model.pnl, model.rank", "at least once, keyed", "durable", "queue, never drop"],
    ["risk/control", "risk.state, exec.quote, alert.pumpdump", "at least once", "durable, high priority", "never drop"],
    ["system", "system.health, review.verdict", "at most once", "best effort", "drop oldest"],
], widths=[0.8, 2.1, 1.1, 1.4, 1.1], size=8.2)
figure("d5.png", "Figure 10.1 — The market-data plane has its own exchange, queue family and consumer budget.")

h("10.1  What was measured after the changes", 2)
p("The priority claim is not asserted; it is observed. Two measurements matter: whether the market plane "
  "kept publishing while compute saturated, and whether payload sizes stayed bounded after the snapshot fix.")
table(["Measurement", "Result"], [
    ["depth published per second", "38.8 per second across 40 symbols, change-driven with a one-second heartbeat"],
    ["ticks envelope size", "7.4 KB after restricting the snapshot to streamed symbols (was 98.6 KB)"],
    ["ticks messages carrying the full universe", "0 — the filter is covered by a unit test"],
    ["recorder errors over the run", "0"],
], widths=[3.0, 3.5], size=8.6)

brk()
h("11  Implementation map", 1)
p("Where each part of the design lives, in the repository, today.")
table(["Design part", "Package", "Tests", "Status"], [
    ["envelope", "core/internal/envelope", "7", "live"],
    ["correlation service", "core/internal/correlation", "5", "live"],
    ["lake: memory, journal, retention", "core/internal/lake", "6", "live"],
    ["market data state and throttling", "core/internal/marketdata", "3", "live"],
    ["recorder", "core/cmd/recorder", "—", "live on :8801"],
    ["replay", "core/cmd/replay", "—", "live"],
    ["bus consumer, health, alerts", "core/cmd", "—", "live"],
    ["orchestrator", "not built", "—", "designed in section 9, phase 4"],
], widths=[2.0, 2.0, 0.7, 1.8], size=8.5)
p("The Python side keeps three libraries that the books call in-process — risk, tactics and maker — because "
  "putting a network hop in front of every trade decision would cost more than the coupling it removes. The "
  "envelope crosses the bus; these three remain part of the book layer.")

brk()
h("Part III — Use cases", 1)
p("Sixteen use cases follow, each in the same four-column form: the need as it exists in this platform, the "
  "ATLAS mechanism that answers it, how CryptAlyst delivers that mechanism, and the criterion that proves "
  "delivery. They are ordered from the data plane outwards to research and compliance.")

h("12  Sixteen use cases", 1)

usecase("UC-01", "Market data stays first when compute saturates",
        "Forty symbols, 161 streams, seven calculators and thirty books read the same feed. During a burst, "
        "a depth handler that blocks must not delay a price.",
        "Priority must be a property of the traffic, not a promise in a comment.",
        "Plane in metadata plus separate topology: the market plane has its own exchange, queue family and consumer budget.",
        "CryptAlyst marks every market message plane=market, publishes it on its own exchange, and records lag per class.",
        "Market-plane lag stays under one bar interval while the compute plane is saturated.",
        "d5.png")

usecase("UC-02", "Compute once, read many",
        "Three models recompute the same flow reading from the same candles, arriving at slightly different "
        "numbers because the reads happen at different moments.",
        "A computed indicator must be published as a value with provenance, not recomputed per consumer.",
        "Advisory envelopes: value, horizon, confidence, expiry, and references to the inputs used.",
        "Calculators publish intel.* advisories; engines subscribe and read rather than re-derive.",
        "Two consumers of the same advisory return identical values and identical input references.",
        "d6.png")

usecase("UC-03", "Why did we buy?",
        "A reviewer asks why the platform bought BTCUSDT at 09:41. Today the answer is a log line written by "
        "the model about itself.",
        "A decision must resolve to the advisories behind it and the candles behind those.",
        "Mint locally, map centrally, resolve under authority; references accumulate as the envelope travels.",
        "The recorder links each decision document to the advisory documents for its symbol and serves a gated trace.",
        "One query returns decision to advisory to ticks to candle, and records that the query happened.",
        "d7.png")

usecase("UC-04", "Backtest equals live",
        "The backtest harness reads REST candles and produces numbers that do not correspond to live trading, "
        "so research and production are different programs.",
        "Replay must be of the exact objects live consumers saw.",
        "An append-only journal with replay from an offset; the stored object is the envelope.",
        "The recorder writes every envelope to the lake; the replay tool re-emits a window to the bus at a chosen speed.",
        "Replaying a recorded window reproduces the same decisions as the live run.",
        "d8.png")

usecase("UC-05", "Refusing stale input with evidence",
        "A forecast older than its limit is refused, but the refusal quotes a wall-clock guess rather than the "
        "envelope that produced it.",
        "Freshness must be carried by the object, and a refusal must quote it.",
        "computed_at and generation in metadata; the age is a property of the envelope.",
        "Every advisory carries computed_at and generation; the risk gate refuses on age and records the numbers.",
        "A refused decision names the age, the limit and the generation it came from.",
        "d11.png")

usecase("UC-06", "A new model is a registration",
        "Adding a model currently means editing a supervisor table, an engine list and a dashboard map.",
        "New capability must not reopen existing code.",
        "Operators advertise capabilities; callers ask for the capability, not the service.",
        "A model registers a capability name and its subscriptions; nothing else changes.",
        "A new model trades after a registration with no diff outside its own directory.",
        "d12.png")

usecase("UC-07", "A refusal is a first-class record",
        "Risk refusals are visible on a dashboard but not stored with the numbers that caused them, so they "
        "cannot be reviewed later.",
        "Authority runs before the action and produces evidence as an output.",
        "Interceptors refuse with the rule named; the refusal is a step on the document.",
        "Every refusal is emitted as a decision envelope with allowed=false and the rule quoted.",
        "Every refusal on the dashboard can be retrieved later with its inputs.",
        "d11.png")

usecase("UC-08", "Storage growth with a policy instead of a surprise",
        "The lake recorded 272 MB in fifteen minutes on the first run — approximately eighteen gigabytes a day — "
        "almost all of it one oversized snapshot.",
        "Recording must be affordable, which means retention and payload discipline are part of the design.",
        "Tiered storage with rotation, compression and expiry; a payload is the smallest object that answers the question.",
        "The snapshot now carries only streamed symbols; segments rotate, compress at 44.7% and expire by tier.",
        "Lake growth has a stated window per tier and the journal never grows unbounded.",
        "d10.png")

usecase("UC-09", "The dashboard explains rather than displays",
        "The dashboard shows numbers. A number without provenance cannot be acted on with confidence.",
        "Every visible value should be able to say when it was computed and from what.",
        "Metadata travels with data, so the UI reads age and generation rather than guessing.",
        "Panels show computed_at and generation; the model drill-down shows the envelope chain behind a decision.",
        "Every panel that shows a derived value can show its age and producer.",
        None)

usecase("UC-10", "Incident forensics at 09:41",
        "Something went wrong at a specific minute. Reconstructing it means reading four log files and hoping "
        "they share timestamps.",
        "The platform must be able to replay a window and explain each decision in it.",
        "Correlation map plus journal: replay the window, then resolve the documents it touches.",
        "Replay the minute, resolve each decision, compare against the live record.",
        "The reconstruction identifies the advisory and the rule responsible for each decision in the window.",
        "d16.png")

usecase("UC-11", "Model decay without a separate research pipeline",
        "A model that worked in walk-forward stops working live, and there is no shared dataset connecting its "
        "live decisions to its backtest.",
        "The dataset used for research must be the dataset produced in production.",
        "One lake of envelopes feeding both replay and study; gold tier holds per-model aggregates.",
        "Per-model performance is materialised from decision envelopes, so decay is measured on the same objects.",
        "A decay curve can be computed from gold without touching the live system.",
        None)

usecase("UC-12", "Compliance evidence produced by doing the work",
        "An auditor asks what the platform did, why, and who looked at it. Today this is an exercise in log "
        "archaeology.",
        "Evidence must be an output of the work, not a report written afterwards.",
        "Steps on documents, gated resolution, and the read itself recorded as a step.",
        "Decision envelopes carry the rule, the inputs and the actor; resolution records the reader.",
        "An evidence pack is a query over the correlation map and the lake, not a reconstruction.",
        "d7.png")

usecase("UC-13", "Thirty books without a dependency tangle",
        "Thirty models share four inputs; today that graph exists only in the code that fetches them.",
        "Dependencies must be declared, inspectable and stable as books are added.",
        "Subscriptions are the graph; advisories are the shared edges.",
        "Each book declares its topics; the bus renders the graph and the dashboard can show it.",
        "Adding or removing a book changes the graph, not the code of any other book.",
        "d13.png")

usecase("UC-14", "Research without touching production",
        "A researcher wants a week of history without loading the live broker or the live lake's write path.",
        "History must be readable in isolation, at any speed.",
        "Replay from a copied journal into a separate topic namespace.",
        "Copy segments, replay into a research instance of the calculators, compare outputs offline.",
        "Research runs read-only, from cold segments, with no impact on live lag.",
        None)

usecase("UC-15", "A failed producer is a gap, not a mystery",
        "A producer stops for ten minutes. Consumers cannot tell whether the market was quiet or the feed died.",
        "Absence must be observable, and recovery must not require re-derivation.",
        "generation and sequence in metadata; the recorder records what arrived and the orchestrator notices what did not.",
        "The recorder exposes per-topic receive rate and last-seen time; the orchestrator alarms on a generation gap.",
        "A producer outage is visible as a gap in generation, not as a quiet dashboard.",
        None)

usecase("UC-16", "Cost accounting per model",
        "The platform knows total fees but not which model spends them, so a model can be net-negative and "
        "invisible.",
        "Cost must be attributable to the decision that incurred it.",
        "Decision envelopes carry notional, fee assumptions and the model identity.",
        "Fills are recorded with their decision reference, so attributed cost is a gold-tier aggregate.",
        "Each model reports gross edge, fees and net in one query, with the trades behind it.",
        None)

brk()
h("Part IV — Delivery", 1)

h("13  Measured performance", 1)
p("Every number below was produced on this machine against the running platform. Nothing here is a target.")
table(["Measurement", "Result", "How it was obtained"], [
    ["envelope decode", "2.46 µs per envelope, 978 B, 12 allocations", "Go benchmark, 5,000 iterations"],
    ["envelope build and encode", "1.08 µs per envelope, 912 B", "Go benchmark"],
    ["memory write", "1.85 µs per write", "Go benchmark"],
    ["journal write", "2.39 µs per write", "Go benchmark"],
    ["lake fan-out (memory and journal)", "2.41 µs per write", "Go benchmark"],
    ["end-to-end write", "200,000 envelopes in 500 ms = 400,000 per second", "timed test"],
    ["end-to-end replay", "200,000 envelopes in 177 ms = 1,127,000 per second", "timed test"],
    ["live recorder throughput", "27.8 per second sustained, 0 errors", "running service, :8801"],
    ["ticks envelope", "7.4 KB after the snapshot fix, was 98.6 KB", "lake inspection"],
    ["depth publish rate", "38.8 per second across 40 symbols", "broker statistics"],
    ["compression on live traffic", "44.7% of original size", "gzip of the live journal"],
    ["test suite", "21 tests across four packages, all passing", "go test ./..."],
], widths=[2.0, 2.7, 1.8], size=8.3)

h("13.1  What the numbers mean", 2)
p("The envelope and the lake are three orders of magnitude faster than the traffic they carry. The platform "
  "publishes about 28 messages per second and the recorder can absorb 400,000. Therefore neither the envelope "
  "nor the lake is a performance risk, and any latency the platform has is in the models, not in the carrier.")
p("The number that did matter was payload size, not rate: a snapshot carrying the full mini-ticker universe "
  "made each ticks envelope 98.6 KB and turned into eighteen gigabytes a day. Restricting the snapshot to "
  "streamed symbols reduced it by a factor of thirteen and is covered by a unit test so it cannot regress.")

brk()
h("14  Test and verification strategy", 1)
h("14.1  What is tested", 2)
table(["Package", "Tests", "What they protect"], [
    ["internal/envelope", "7", "round trip, unknown member refused, unknown meta key ignored, version policy, refs accumulate, age, transformers"],
    ["internal/correlation", "5", "cid uniqueness over 1,000 mints, append, gated read recorded, ancestry ordered root-first, cycle safety"],
    ["internal/lake", "6", "memory keeps latest and drops oldest, journal round trip, replay across segments, fan-out, rotation, compression, expiry"],
    ["internal/marketdata", "3", "snapshot restricted to streamed symbols, depth change detection, candle series bounded"],
], widths=[1.7, 0.6, 4.2], size=8.3)

h("14.2  What is verified against the running system", 2)
bullets([
    "The broker, not the dashboard: message counts and rates are read from the management API.",
    "The lake on disk: record counts, topic mix, and bytes per topic are measured from the journal itself.",
    "The recorder's own counters: received, written, errors, rate, correlation documents, steps and reads.",
    "A trace query end to end: a decision document resolves with its steps recorded.",
])
p("Three real defects were found by this strategy and are worth recording, because they are the class of bug "
  "the design exists to prevent: an ancestry that came back leaf-first (fixed by reversing the walk), a "
  "benchmark that was accidentally O(iterations x records) and hung the suite, and a JSON field collision "
  "where a case-insensitive decoder matched a numeric exchange field to a string field and silently discarded "
  "every candle.")

brk()
h("15  Rollout plan and acceptance gates", 1)
p("Each phase leaves the platform running. A phase is complete when its gate is measured, not when the code "
  "is merged.")
table(["Phase", "Change", "Gate"], [
    ["1", "Envelope in Go; the four Go services wrapped at the boundary", "every recorded message decodes with version, plane and producer"],
    ["2", "Recorder with tiers, rotation, compression and expiry", "a window replays exactly; lake growth is bounded by policy"],
    ["3", "Calculator tier publishes advisories with references", "two consumers of one advisory see identical values and refs"],
    ["4", "Orchestrator: capabilities, deadlines, generation checks", "a stale advisory is superseded, not recomputed by every consumer"],
    ["5", "Books emit decisions with cid and references", "a decision resolves to advisory, ticks and candle in one query"],
    ["6", "Replay wired to the backtest harness", "live and replay agree on the same recorded window"],
    ["7", "Retention tuned per tier; archive verified", "storage stays within its stated window for a week of running"],
], widths=[0.55, 3.0, 2.95], size=8.3)

h("15.1  The one invariant", 2)
callout("The envelope never changes shape. Everything that varies — venues, indicators, models, stores — lives "
        "in operators, interceptors and transformers around it. The moment the envelope grows a trading-specific "
        "field, the design has failed and the platform is back to a bespoke message per consumer.")

brk()
h("16  Risks, open decisions and the invariant", 1)
h("16.1  Risks", 2)
table(["Risk", "Likelihood", "Mitigation"], [
    ["Python books never emit envelopes", "medium", "phase 5 is small: wrap the existing decision payload and add cid and refs"],
    ["Storage outgrows the disk", "high without policy", "tiered retention, compression, and per-topic payload budgets"],
    ["Schema drift between producers", "medium", "refuse unknown top-level members; version the payload before there are two producers"],
    ["Orchestrator becomes a second scheduler", "medium", "capabilities only: it may ask for a name, never a process"],
    ["Correlation becomes a single point of failure", "low", "minting is local; the map is a rebuildable projection"],
], widths=[2.1, 1.0, 3.4], size=8.3)

h("16.2  Open decisions", 2)
bullets([
    "Retention duration per tier — needs a stated research horizon before it can be fixed.",
    "Whether advisories are attributed to the model that computed them or to a separate calculator tier.",
    "Whether depth is published as five levels on change or as a cheaper top-of-book every second.",
    "Whether the archive is local disk or object storage, which decides the cost of the compliance use case.",
])

h("16.3  Closing statement", 2)
p("This edition replaces opinion with lineage. The platform can now say what a message is, when it was "
  "computed, what it was computed from, and who read it. That is not a cosmetic improvement: it is the "
  "difference between a system that accumulates trades and one that can learn from them.")

brk()
h("Part V — Storage, security and the ticker service", 1)

h("18  Storage selection and sizing", 1)
p("The lake defines the shape of storage: tiers behind one interface. This chapter chooses the actual "
  "products, and — more importantly — states the measurement that would force each choice to change. A "
  "recommendation without a switch trigger ages into advocacy.")
p("The constraint that decides most of this: one operator, one machine, no container runtime. Every "
  "technology below installs as a single binary or a Python package, and none of them requires a second "
  "person to run.", italic=True)

h("18.1  The recommended stack", 2)
table(["Data", "Store", "Why this one", "Switch trigger"], [
    ["Operational state — books, positions, orders, fills, risk decisions",
     "SQLite (WAL), one file per book",
     "ACID, zero operations, already present on the machine; per-book files mean no write contention",
     "two writers on one table, or remote concurrent access → Postgres"],
    ["Bronze, silver and gold envelopes",
     "Parquet files, partitioned by topic and date, queried with DuckDB",
     "immutable files, zstd compression, full SQL with no server to run",
     "gold beyond 10^9 rows, or concurrent analytical users → ClickHouse"],
    ["Correlation map — documents, steps, references, cids",
     "SQLite with three tables and indexes on cid and doc_id",
     "a ledger with lookups, not a document store; rebuildable by replay, so a lost file is cheap",
     "multi-instance resolution or multi-user gating → Postgres"],
    ["Hot reads — latest prices, book snapshots, dashboard",
     "Go process memory",
     "40 symbols of small structs; a network hop would be slower and less reliable",
     "more than one dashboard instance → Redis as a cache, never as truth"],
    ["Archive of closed segments",
     "local disk with gzip now; MinIO when off-box retention is required",
     "compression already measured at 44.7% of original size",
     "retention beyond one machine, or an auditor who needs independent access"],
    ["Bus",
     "RabbitMQ (keep)",
     "already the spine; one bus is an architectural rule, not a preference",
     "replay semantics outgrow queues → NATS JetStream (single binary), not Kafka"],
], widths=[1.5, 1.5, 2.0, 1.6], size=8.0)

h("18.2  Sizing arithmetic", 2)
mono([
    "live rate            28 msg/s           = 2.4M envelopes/day",
    "payload              ticks 7.4 KB, depth 0.4 KB, advisories 0.1 KB",
    "raw bronze           ~18 GB/day",
    "after gzip (44.7%)   ~8 GB/day",
    "after parquet+zstd    ~1.5-2 GB/day",
    "SQLite comfortable   to ~50 GB per file",
    "DuckDB scan          2 GB parquet in tens of milliseconds",
])
p("These are the numbers that decide when to move. Today's traffic is two orders of magnitude below what "
  "Parquet and DuckDB handle comfortably, and three below ClickHouse. Adopting ClickHouse now would be "
  "buying headroom with operational cost.")

h("18.3  What not to use, and why", 2)
table(["Rejected", "Why"], [
    ["Kafka", "operationally heavy for one operator, and its log duplicates what the recorder already provides"],
    ["MongoDB", "no cross-document transactions; the correlation ledger needs atomic step appends alongside document state"],
    ["Elasticsearch", "wrong cost curve for tick data; 18 GB/day indexed to answer questions a Parquet scan answers better"],
    ["TimescaleDB", "adds a server for a downsampling job that partitioned Parquet already does"],
    ["Redis as truth", "the moment book state lives only in Redis, a restart is data loss"],
    ["Docker as a prerequisite", "a paper-trading platform should not need a container runtime to book a trade"],
], widths=[1.4, 5.1], size=8.3)

h("18.4  Backup and recovery", 2)
table(["Store", "Backup", "Recovery"], [
    ["SQLite", "online `.backup` per file, nightly", "copy the file back; WAL replays"],
    ["Parquet", "immutable; copy segments to a second disk", "files are the record; no restore step"],
    ["RabbitMQ", "export definitions and bindings", "redeclare; consumers reconnect automatically"],
    ["Lake journal", "gzip segments on rotation", "replay from any segment boundary"],
], widths=[1.2, 2.6, 2.7], size=8.3)

brk()
h("19  The security service", 1)
p("Security in this platform is not a login page. It is the component that answers three questions before any "
  "work happens: who is asking, on whose authority, and what may they see of the answer. It is built in the "
  "ATLAS style — an interceptor before the operator, a gate on resolution, and a ledger entry for every "
  "decision including the refusals.", italic=False)
figure("d17.png", "Figure 19.1 — Authenticate, gate, do the work, redact for the caller, and write the step.")

h("19.1  The three parts", 2)
table(["Part", "Responsibility", "Failure it prevents"], [
    ["Interceptor", "establish the actor: a dashboard user, a service, or an agent", "anonymous work with no attributable actor"],
    ["Authority gate", "decide whether this actor may do this, with the rule named", "unreviewable access to positions, traces or keys"],
    ["Redaction", "shape the answer for the caller's role without changing its truth", "one role leaking another role's data"],
], widths=[1.2, 2.6, 2.7], size=8.3)

h("19.2  Roles and what each may see", 2)
table(["Role", "Sees", "Never sees"], [
    ["Owner", "its own documents in full, with references", "other owners' payloads"],
    ["Peer service", "the needs it may serve, its own steps, artifact names", "another service's pricing inputs"],
    ["Auditor", "actors, outcomes, timings, requirements, artifacts", "payloads, only redacted references"],
    ["Bystander", "state and requirement satisfaction only", "any step detail"],
], widths=[1.1, 3.0, 2.4], size=8.3)
p("Every resolution is recorded as a `read` step on the document being read. That single rule answers the "
  "question a desk asks first in an incident: who looked at this, and when.")

h("19.3  Credentials and keys", 2)
bullets([
    "Service to service: short-lived introduction tokens minted by the correlation service, not shared secrets in config files.",
    "Human: session token on the dashboard, scoped to a role, with the read recorded.",
    "Exchange keys: never on the bus, never in an envelope; held by the execution adapter only, referenced by name.",
    "Every refusal is a step with the rule quoted, so an attempted escalation is visible rather than silent.",
])
callout("The gate is not optional. A trace reveals who asked what, about which symbol, at what time — the "
        "platform's memory of intent. Shipping a resolver before its authorization converts a transient packet "
        "into a queryable record of intent with nothing protecting it.")

brk()
h("20  The ticker service", 1)
p("The ticker service is the public face of the market plane: a price feed for dashboards, external clients and "
  "any service that should not open a bus connection. It is a thin operator over the market plane — it computes "
  "nothing, caches what already exists, and answers fast.")
figure("d18.png", "Figure 20.1 — One operator, a hot cache, three delivery shapes, and a ledger entry per response.")

h("20.1  Responsibilities", 2)
table(["Responsibility", "Behaviour", "Why"], [
    ["Subscribe", "reads the market plane once; never polls the exchange", "the feed stays the only process that talks to Binance"],
    ["Cache", "keeps the latest tick, candle and 24h statistic per symbol in memory", "answers in microseconds without touching the bus per request"],
    ["Serve", "SSE stream for browsers, HTTP snapshot for services, WebSocket for external clients", "one source, three shapes, no duplicated logic"],
    ["Trace", "mints a cid per response and records what was served", "a client can quote the id and the platform can resolve it"],
    ["Gate", "redacts per role; a bystander sees prices but not book depth", "public data is not the same as proprietary data"],
], widths=[1.2, 2.7, 2.6], size=8.3)

h("20.2  The envelope it returns", 2)
mono([
    'envelope(ticker)',
    '  data:  { symbol: BTCUSDT, last: 82750.12, bid: 82750.11, ask: 82750.13,',
    '           change_24h: +1.82, quote_volume: 2465058 }',
    '  meta:  { version: 1, plane: market, producer: ticker,',
    '           computed_at: 09:41:02.114, generation: 8834, cid: cid-9f2a41 }',
    '  refs:  [ md.ticks@8834, md.candle@8834 ]',
])
p("A client that receives this can quote the cid in a support request, and the platform can resolve exactly "
  "which tick and which candle produced the number it saw.")

h("20.3  Interface", 2)
table(["Route", "Shape", "Consumer"], [
    ["GET /ticker/{symbol}", "one envelope, JSON", "services and scripts"],
    ["GET /ticker/stream", "SSE, one event per change", "dashboard"],
    ["WS /ticker", "frames of envelopes, one per symbol per change", "external clients"],
    ["GET /ticker/trace/{cid}", "gated resolution of a served response", "support and audit"],
], widths=[1.7, 2.6, 2.2], size=8.3)

h("20.4  Why this is a service and not a route", 2)
p("A ticker route on the dashboard would work until a second consumer appeared, at which point the platform "
  "would have two definitions of a price. Making it a service with an envelope and a cache means one "
  "definition, one trace, one place to fix a rounding rule — and the dashboard becomes just another client.")
figure("d19.png", "Figure 20.2 — One correlation id across security and ticker: who asked, what was served, what was redacted.")

brk()
h("21  Appendix", 1)
h("21.1  Topic table", 2)
table(["Topic", "Exchange", "Class", "Producer"], [
    ["ticks, market.price, market.depth, market.candle, market.mini24h", "market", "market", "marketdata (Go)"],
    ["intel.signal, intel.flow, intel.movers", "intel", "intel", "signals (Python)"],
    ["intel.ta", "intel", "intel", "tad (Go)"],
    ["intel.forecast", "intel", "intel", "forecast (Go)"],
    ["alert.pumpdump", "alert", "control", "signalbus (Go)"],
    ["model.decision, model.fill, model.pnl, model.rank", "model", "model", "books (Python)"],
    ["risk.state, exec.quote", "risk / exec", "control", "risk, maker"],
    ["system.health, review.verdict", "system / review", "system", "healthpub, review"],
], widths=[2.9, 1.1, 0.8, 1.7], size=8.2)

h("21.2  Metric definitions", 2)
table(["Metric", "Definition"], [
    ["age", "now minus computed_at, in milliseconds; the number an engine refuses on"],
    ["generation", "the store or compute counter an envelope was produced from"],
    ["lag", "publish time to consume time for a class of traffic"],
    ["drop", "a market-plane message discarded because a consumer fell behind"],
    ["rate", "messages received per second, measured by the recorder, not estimated"],
], widths=[1.4, 5.1], size=8.3)

h("21.3  File map", 2)
table(["Path", "Contents"], [
    ["core/internal/envelope", "the contract and its version policy"],
    ["core/internal/correlation", "the map, minting and gated resolution"],
    ["core/internal/lake", "backends, retention, replay"],
    ["core/internal/marketdata", "live state, snapshots, depth change detection"],
    ["core/cmd/recorder", "the persistence plane"],
    ["core/cmd/replay", "replay to the bus"],
    ["docs/envelope", "Mermaid sources and rendered diagrams"],
], widths=[2.4, 4.1], size=8.3)

brk()
h("Appendix A — Database technology considerations", 1)
p("Chapter 18 chooses; this appendix explains the reasoning behind the choices and states what would have to be "
  "measured to overturn them. It is written to be argued with, because a storage decision that cannot be "
  "challenged on evidence is a preference rather than an engineering decision.")

h("A.1  Access patterns by data class", 2)
table(["Data class", "Write rate", "Read shape", "Retention", "Dominant cost"], [
    ["ticks and depth", "tens per second", "latest value, window scans", "hours to days", "write volume and payload size"],
    ["candles", "one per bar per symbol", "ranges, aggregates", "weeks", "scan width"],
    ["advisories", "tens per second", "latest per symbol, per-model history", "weeks", "cardinality of symbol and model"],
    ["decisions and fills", "tens per minute", "exact lookup by cid, per-model aggregates", "years", "durability and audit"],
    ["correlation steps", "one per append", "ancestry walk, time range", "years", "index size"],
    ["dashboard reads", "hundreds per minute", "small, cached, repeated", "none", "latency"],
], widths=[1.2, 1.1, 1.7, 0.8, 1.7], size=8.2)

h("A.2  Candidate matrix", 2)
table(["Technology", "Model", "Write", "Query strength", "Ops cost", "Concurrency", "Verdict here"], [
    ["SQLite (WAL)", "relational, embedded", "excellent single-writer", "good with indexes", "none", "one writer, many readers", "operational state and the correlation map"],
    ["Postgres", "relational, server", "very good", "excellent, JSONB and windows", "low", "MVCC, many writers", "when concurrency or gating needs it"],
    ["ClickHouse", "columnar, server", "excellent, batched", "excellent for aggregates", "medium", "many writers, merge on read", "gold at scale, not yet"],
    ["DuckDB + Parquet", "columnar, embedded", "append-only files", "excellent for scans", "none", "single process", "research and bronze/silver"],
    ["TimescaleDB", "Postgres extension", "very good", "time-series functions", "low", "as Postgres", "viable, redundant with Parquet downsampling"],
    ["MongoDB", "document", "good", "weak joins, no cross-doc transactions", "medium", "sharded", "rejected for the ledger"],
    ["Redis", "in-memory KV", "excellent", "no complex queries", "low", "single-threaded per shard", "cache only, never truth"],
    ["Kafka", "distributed log", "excellent", "no queries, consumer groups", "high", "partitions", "rejected at this scale"],
    ["NATS JetStream", "log, single binary", "excellent", "replay by sequence", "low", "lightweight", "the migration target if the bus outgrows queues"],
    ["MinIO / S3", "object store", "good, immutable", "none without an engine", "medium", "eventually consistent", "archive tier when off-box is required"],
], widths=[1.05, 1.05, 0.95, 1.25, 0.6, 1.0, 1.05], size=7.6)

h("A.3  Concurrency and contention", 2)
p("The single-writer restriction of SQLite looks like a limitation and is, here, a design tool. Each book owns "
  "its own file, so thirty books do not contend; each file is trivially backed up, and cleaning a book is a "
  "file operation rather than a transaction against shared state. Postgres becomes necessary at exactly the "
  "moment two processes must write the same table — which in this design is the correlation service when it "
  "serves more than one reader instance, and the risk state when it is consumed by more than one writer.")
mono([
    "SQLite writer model      one writer at a time, WAL allows concurrent readers",
    "per-book isolation       30 files, 30 independent writers, no contention",
    "contention appears when  two processes write one table, or a table exceeds ~50 GB",
    "Postgres trigger         concurrent write requirement, not volume",
])

h("A.4  Durability and the fsync trade", 2)
table(["Setting", "Behaviour", "Cost", "Use"], [
    ["fsync per record", "every write survives power loss", "thousands of writes per second", "fills, decisions, risk state"],
    ["group commit (batch)", "writes survive in batches", "hundreds of thousands per second", "market data, advisories"],
    ["WAL + periodic flush", "small window of loss", "very high throughput", "bronze market data only"],
], widths=[1.4, 2.2, 1.7, 1.6], size=8.2)
p("The rule: anything that must explain a trade is fsynced; anything that can be re-read from the feed is not. "
  "A lost tick is a gap; a lost fill is an audit failure.")

h("A.5  Schema evolution", 2)
p("The envelope makes schema evolution a metadata problem rather than a migration problem. Payloads version  "
  "independently of the envelope; a reader accepts the current revision and the one before it. In a columnar "
  "store this means: keep the envelope columns stable (topic, key, generation, computed_at, cid), and store "
  "the payload as a JSON column or a struct that can gain fields. Materialise a payload field into a column "
  "only when a query needs it at scan speed.")

h("A.6  What each engine does at scale", 2)
table(["Rows", "SQLite", "DuckDB + Parquet", "ClickHouse"], [
    ["1 million", "instant with an index", "instant", "instant"],
    ["100 million", "slow for scans, fine for lookups", "sub-second scans", "sub-second aggregates"],
    ["1 billion", "not appropriate", "seconds per scan, parallel", "sub-second with partitioning"],
], widths=[1.2, 2.0, 1.8, 1.7], size=8.3)

h("A.7  Migration path", 2)
table(["From", "To", "Trigger", "Method"], [
    ["SQLite", "Postgres", "concurrent writers, or remote access", "dual-write one week, then read-cut, then write-cut"],
    ["Parquet", "ClickHouse", "gold beyond 10^9 rows or concurrent BI", "load Parquet into a ClickHouse table; queries run in both during cut-over"],
    ["RabbitMQ", "NATS JetStream", "replay semantics outgrow queues", "publish both, compare consumer lag, cut consumers topic by topic"],
], widths=[0.9, 1.1, 1.9, 2.6], size=8.2)

h("A.8  Benchmarks to run before deciding", 2)
bullets([
    "Insert 1M envelopes with fsync per record: measure records per second and p99 latency.",
    "Scan a day of Parquet with DuckDB for one symbol's flow history: measure cold and warm.",
    "Aggregate per-model net over 30 days in ClickHouse: measure with and without a projection.",
    "Resolve a decision chain of depth 6 in SQLite and in Postgres: measure p99 under 50 concurrent readers.",
    "Replay 24 hours of envelopes: measure whether the target rate is reached without drops.",
])

brk()
h("Appendix B — Compliance", 1)
p("Compliance is treated here as a consequence of the architecture rather than a reporting layer bolted on at "
  "the end. If the platform records what explains each decision, and records who read it, the evidence pack is "
  "a query — not a reconstruction exercise.")
figure("d21.png", "Figure B.1 — Decision and fill envelopes, the correlation map, the lake, and a gated evidence pack.")

h("B.1  What must be evidenced", 2)
table(["Obligation", "Evidence", "Where it lives"], [
    ["What was traded, when, at what price", "fill envelopes with timestamps and notional", "lake, gold tier"],
    ["Why the decision was made", "decision envelope with refs to advisories and ticks", "lake plus correlation map"],
    ["Who or what decided", "producer identity and model version in metadata", "envelope meta"],
    ["What was refused and on which rule", "decision envelope with allowed=false and the rule quoted", "lake"],
    ["Who looked at the record", "read step appended on resolution", "correlation map"],
    ["That the record was not altered", "hash-chained segments, append-only journal", "archive"],
    ["That data was not missing", "generation and sequence continuity, gap detection", "recorder statistics"],
], widths=[1.9, 2.6, 2.0], size=8.2)

h("B.2  Retention schedule", 2)
table(["Data", "Minimum retention", "Rationale"], [
    ["ticks and depth", "7 days", "replay for forensic and regression windows"],
    ["candles and advisories", "90 days", "model study and decay measurement"],
    ["decisions and fills", "7 years", "regulatory and dispute horizon"],
    ["risk refusals and gate decisions", "7 years", "proof of control"],
    ["access and resolution logs", "7 years", "proof of who saw what"],
], widths=[2.0, 1.4, 3.1], size=8.3)

h("B.3  The evidence pack", 2)
p("One request produces a pack for a window: every decision, its chain, the fills it caused, the rules it "
  "passed or failed, and the identities that read it. The pack is generated by query, gated by role, and the "
  "generation is itself recorded as a step — so the act of producing evidence is auditable.")
mono([
    "window        2026-10-19 09:00 to 10:00",
    "decisions     all model.decision envelopes in window",
    "chains        resolve each decision to advisory, ticks, candle",
    "fills         all model.fill envelopes referencing those decisions",
    "refusals      decision envelopes with allowed = false",
    "readers       read steps on every document in the pack",
    "integrity     segment hashes and continuity of generation",
])

h("B.4  Integrity without a blockchain", 2)
p("Each closed segment carries a hash of the previous segment's hash, forming a chain. Verification recomputes "
  "the chain; a single altered byte breaks it at a known point. This is cheaper than a distributed ledger and "
  "sufficient for the obligation, which is to show that the record was not quietly rewritten.")
mono([
    "seg-0001  hash = H(payload)",
    "seg-0002  hash = H(H(seg-0001) || payload)",
    "verify   -> recompute, report first mismatch by segment and offset",
])

h("B.5  Privacy and the erasure conflict", 2)
p("Retention law and erasure law pull in opposite directions. The resolution adopted here: personal data is "
  "pseudonymised at the boundary (an actor id, never a name), the mapping lives in one access-controlled table, "
  "and erasure removes the mapping while the trading record keeps its pseudonym. The trade remains explainable; "
  "the person becomes unidentifiable. The alternative — deleting the trade — destroys the record the regulator "
  "requires and the evidence the desk needs.")

h("B.6  Controls mapped to mechanism", 2)
table(["Control", "Mechanism", "Test"], [
    ["Segregation of duties", "roles in the security service; owner cannot self-audit", "an owner read is refused and recorded"],
    ["Access logging", "read step on every resolution", "every resolve appears in the map"],
    ["Change control", "code version in envelope metadata", "replay identifies the version that produced a decision"],
    ["Exception reporting", "refusals, gaps and divergence as first-class records", "daily report lists counts, not prose"],
    ["Attestation", "daily hash of the day's segments", "verification script reproduces the hash"],
], widths=[1.7, 2.6, 2.2], size=8.2)

h("B.7  What an auditor finds when something is missing", 2)
p("A gap in generation is visible as a gap, not as a quiet period. The recorder exposes per-topic receive rate "
  "and last-seen time; a producer that stops leaves a discontinuity that the daily report names. That is the "
  "difference between an absence and a mystery.")

brk()
h("Appendix C — Trade replay", 1)
p("Replay is the mechanism that makes a backtest a test rather than a hypothesis. It has three uses in this "
  "platform — forensic reconstruction, regression testing, and backtesting — and they share one requirement: "
  "the same inputs through the same code must produce the same decisions.")
figure("d22.png", "Figure C.1 — Determinism has five inputs. Any difference in one of them is a different experiment.")

h("C.1  Three modes", 2)
table(["Mode", "Input", "Purpose", "Success"], [
    ["Forensic", "a recorded window in production", "explain what happened at 09:41", "the chain resolves and the decisions match"],
    ["Regression", "a fixed recorded window in CI", "prove a change did not alter behaviour", "zero decision divergence, or a reviewed diff"],
    ["Backtest", "a long recorded window", "measure a strategy over history", "metrics reproducible from the same file"],
], widths=[1.1, 1.9, 1.9, 1.6], size=8.2)

h("C.2  The five inputs that must be identical", 2)
table(["Input", "Why it matters", "How it is held fixed"], [
    ["Envelopes", "the data itself", "the lake file is the input, not a re-fetch"],
    ["Code version", "a changed threshold is a different strategy", "version in envelope metadata and in the replay manifest"],
    ["Parameters", "same code, different config is a different run", "config hash recorded with the replay"],
    ["Clock", "age checks and timeouts depend on it", "injected clock from envelope timestamps, never wall clock"],
    ["Iteration order", "maps and sets reorder results", "sorted symbols, stable ordering in aggregations"],
], widths=[1.2, 2.6, 2.7], size=8.2)

h("C.3  How a replay runs", 2)
mono([
    "1  select    window from lake: topic in (...) and ts between t0 and t1",
    "2  load      the exact envelopes, in original order",
    "3  inject    clock = envelope.computed_at; disable wall clock and randomness",
    "4  feed      envelopes through the same calculators and risk gates",
    "5  capture   decisions and refusals with the same envelope shape",
    "6  diff      replayed decisions against live decisions, decision by decision",
    "7  report    zero divergence, or a diff naming the first point of difference",
])

h("C.4  Divergence and what it means", 2)
table(["Divergence", "Usual cause", "Action"], [
    ["different decision set", "code or parameter change", "expected if reviewed; otherwise a regression"],
    ["same decision, different size", "risk state differs at that moment", "replay must reconstruct risk state, not read live"],
    ["same decision, different timestamp", "clock not injected", "fix the replay harness, not the strategy"],
    ["same decision, different fill price", "book state not reconstructed", "replay depth alongside ticks, or accept modelling tolerance"],
    ["flapping between runs", "map iteration order", "sort keys; rerun twice to confirm determinism"],
], widths=[1.7, 2.3, 2.5], size=8.2)

h("C.5  Side effects are disabled, not hoped away", 2)
p("A replay must never place an order, write a live fill, or advance live risk state. The replay process runs "
  "with an explicit paper flag, writes to a separate book namespace, and refuses to start if the live broker "
  "connection is writable. This is a guard, not a convention: a replay that can trade is a replay nobody will "
  "run on a Friday.")

h("C.6  Acceptance criteria", 2)
table(["Criterion", "Threshold"], [
    ["decisions reproduced", "100% of decisions in the window appear in the replay"],
    ["decision divergence", "zero, or every difference reviewed and attributed"],
    ["refusals reproduced", "same refusal reasons and the same ages"],
    ["determinism", "two consecutive replays of the same window are byte-identical"],
    ["replay rate", "at least 10x real time, so a day replays in under three hours"],
], widths=[2.2, 4.3], size=8.3)

h("C.7  Why this closes the loop", 2)
p("With replay, three questions stop being arguments. What happened: resolve the chain. Why: read the refs. "
  "What would have happened: replay the window. The platform no longer needs to believe its own backtests; it "
  "can compare them against the record of what it actually did, on the same data, through the same code, with "
  "the only difference being the clock.")

# ============================================================ footer + save
def add_page_numbers(paragraph):
    def field(instr):
        run = paragraph.add_run()
        b = OxmlElement("w:fldChar"); b.set(qn("w:fldCharType"), "begin")
        i = OxmlElement("w:instrText"); i.set(qn("xml:space"), "preserve"); i.text = instr
        s = OxmlElement("w:fldChar"); s.set(qn("w:fldCharType"), "separate")
        run._r.append(b); run._r.append(i); run._r.append(s)
        return run
    def field_end(text="1"):
        run = paragraph.add_run(text)
        e = OxmlElement("w:fldChar"); e.set(qn("w:fldCharType"), "end")
        run._r.append(e)
        return run
    paragraph.add_run("CryptAlyst · Design and Architecture Reference · edition 2 · page ")
    field(" PAGE ")
    field_end("1").font.size = Pt(8)
    paragraph.add_run(" of ")
    field(" NUMPAGES ")
    field_end("1").font.size = Pt(8)


for section in doc.sections:
    para = section.footer.paragraphs[0]
    para.text = ""
    para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    add_page_numbers(para)

out = os.path.join(HERE, "CryptAlyst-Design-and-Architecture-Reference.docx")
doc.save(out)
print("saved", out)
