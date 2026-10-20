#!/usr/bin/env python3
"""Build the Envelope Explorer user guide (A4, screenshots, page numbers)."""
import os
from docx import Document
from docx.shared import Pt, Inches, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
SHOTS = os.path.join(HERE, "guide")
ACCENT = RGBColor(0x1F, 0x4E, 0x79)
MUTED = RGBColor(0x59, 0x63, 0x70)
CONTENT_W = 21.0 / 2.54 - 1.5
MAX_FIG_H = 5.2

doc = Document()
for s in doc.sections:
    s.page_width = Cm(21.0); s.page_height = Cm(29.7)
    s.left_margin = Inches(0.75); s.right_margin = Inches(0.75)
    s.top_margin = Inches(0.7); s.bottom_margin = Inches(0.7)

st = doc.styles["Normal"]
st.font.name = "Calibri"; st.font.size = Pt(10)
st.paragraph_format.space_after = Pt(5); st.paragraph_format.line_spacing = 1.12
for name, size, col in (("Heading 1", 15, ACCENT), ("Heading 2", 11.5, ACCENT), ("Heading 3", 10, MUTED)):
    h = doc.styles[name]
    h.font.name = "Calibri"; h.font.size = Pt(size); h.font.color.rgb = col
    h.paragraph_format.space_before = Pt(10 if name == "Heading 1" else 8)


def h(text, level=1): doc.add_heading(text, level=level)


def p(text, bold=False, italic=False, size=None, colour=None):
    para = doc.add_paragraph(); r = para.add_run(text)
    r.bold = bold; r.italic = italic
    if size: r.font.size = Pt(size)
    if colour: r.font.color.rgb = colour
    return para


def bullets(items):
    for it in items: doc.add_paragraph(it, style="List Bullet")


def steps(items):
    for i, it in enumerate(items, 1):
        para = doc.add_paragraph(); para.paragraph_format.left_indent = Inches(0.25)
        para.paragraph_format.first_line_indent = Inches(-0.25)
        r = para.add_run("%d.  " % i); r.bold = True
        para.add_run(it)


def table(headers, rows, widths=None, size=8.8):
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = "Light Grid Accent 1"; t.alignment = WD_TABLE_ALIGNMENT.CENTER
    for i, name in enumerate(headers):
        c = t.rows[0].cells[i]; c.text = ""
        r = c.paragraphs[0].add_run(name); r.bold = True; r.font.size = Pt(9)
    for row in rows:
        cells = t.add_row().cells
        for i, v in enumerate(row):
            cells[i].text = ""
            r = cells[i].paragraphs[0].add_run(str(v)); r.font.size = Pt(size)
    if widths:
        for row in t.rows:
            for i, w in enumerate(widths): row.cells[i].width = Inches(w)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)


def mono(lines):
    for line in lines:
        para = doc.add_paragraph()
        para.paragraph_format.space_after = Pt(0)
        para.paragraph_format.left_indent = Inches(0.2)
        r = para.add_run(line)
        r.font.name = "Courier New"; r.font.size = Pt(8)
        shd = OxmlElement("w:shd"); shd.set(qn("w:fill"), "F3F4F7")
        para._p.get_or_add_pPr().append(shd)


def shot(name, caption):
    path = os.path.join(SHOTS, name)
    if not os.path.exists(path): return
    w, h_ = Image.open(path).size
    aspect = w / h_
    draw_w = min(CONTENT_W, MAX_FIG_H * aspect)
    doc.add_picture(path, width=Inches(draw_w))
    doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
    p(caption, italic=True, size=8, colour=MUTED)


def callout(text):
    t = doc.add_table(rows=1, cols=1); t.style = "Light List Accent 1"
    c = t.rows[0].cells[0]; c.text = ""
    c.paragraphs[0].add_run(text).font.size = Pt(9)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)


def brk(): doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)

# ------------------------------------------------------------------ cover
doc.add_paragraph(); doc.add_paragraph()
t = doc.add_paragraph(); t.alignment = WD_ALIGN_PARAGRAPH.CENTER
r = t.add_run("THE ENVELOPE EXPLORER"); r.font.size = Pt(30); r.bold = True; r.font.color.rgb = ACCENT
s2 = doc.add_paragraph(); s2.alignment = WD_ALIGN_PARAGRAPH.CENTER
r = s2.add_run("User guide"); r.font.size = Pt(18); r.font.color.rgb = MUTED
s3 = doc.add_paragraph(); s3.alignment = WD_ALIGN_PARAGRAPH.CENTER
r = s3.add_run("how to read, filter and trace every message the platform emits")
r.font.size = Pt(10.5); r.italic = True; r.font.color.rgb = MUTED
doc.add_paragraph()
table(["At a glance", ""], [
    ["Where it runs", "http://127.0.0.1:8802"],
    ["What it shows", "every envelope recorded by the lake, live"],
    ["Read-only", "it never writes to the platform or the bus"],
    ["Retains", "the most recent 20,000 envelopes in memory, plus everything on disk"],
    ["Built for", "forensics, compliance and model analysis"],
], widths=[1.5, 5.1], size=9)

callout("This guide is task-oriented. Each section answers one question: how do I find a message, "
        "how do I read one, how do I see what led to a decision, and how do I prove what happened.")

brk()
h("Contents", 1)
for line in [
    "1  Before you start",
    "2  The screen at a glance",
    "3  Finding the message you want",
    "4  Reading an envelope",
    "5  The timeline: browsing history",
    "6  The journey: a flow diagram from a point",
    "7  The execution trace: what a model actually used",
    "8  How and why a model traded — or did not",
    "9  Roles and redaction",
    "10  Three worked examples",
    "11  Keyboard and quick reference",
    "12  Troubleshooting",
]:
    p(line, size=9.5)

brk()
h("1  Before you start", 1)
p("The Explorer is a viewer. It reads the lake that the recorder writes; it does not talk to the exchange "
  "and it cannot change anything. If it is empty, the problem is upstream, not here.")
h("1.1  What should be running", 2)
table(["Service", "Port", "Why the Explorer needs it"], [
    ["market data", ":8796", "the source of every price, depth and candle envelope"],
    ["recorder", ":8801", "consumes the bus once and writes the lake the Explorer reads"],
    ["envelopeapi", ":8802", "the Explorer itself"],
], widths=[1.5, 0.8, 4.3], size=9)
p("Start them in that order. The Explorer will start without the recorder, but it will have nothing to show.")

h("1.2  Opening it", 2)
steps([
    "Start the core, then the Explorer (scripts/start.sh does both in order).",
    "Open http://127.0.0.1:8802 in a browser.",
    "The header should show a rising envelope count within a few seconds.",
])
callout("If the header shows 20,000 and stops climbing, the Explorer is fine and the feed has stopped. "
        "Check the recorder's /stats before touching the Explorer.")

brk()
h("2  The screen at a glance", 1)
p("Four regions, always in the same place.")
shot("g1-list.png", "Figure 1 — the Explorer with an envelope selected: filters on the left, the list in the "
                    "middle, the envelope's anatomy on the right.")
table(["Region", "What it is for"], [
    ["Header", "counts: envelopes held, bytes, planes, topics. The 'viewing as' role is on the right."],
    ["Timeline", "a histogram of traffic over the retained window. Drag across it to select a time range."],
    ["Left panel", "filters: plane, topic, symbol, producer, correlation id, free text."],
    ["Middle", "the list of envelopes (newest first) or the journey flow diagram."],
    ["Right", "the anatomy of the selected envelope: meta, payload, references, chain."],
], widths=[1.2, 5.4], size=9)

h("2.1  The tab bar", 2)
p("Seven tabs, all permanent: ENVELOPES, JOURNEY, EXECUTION, WHY, GANTT, BOOKS, HEALTH. A tab is a view you "
  "can always reach — nothing in the interface removes one because of what is selected.")
shot("g24-tabs-health.png", "Figure 2 — the tab bar with HEALTH open: six services probed, with port, status "
                            "and latency.")
table(["Tab", "What it is for"], [
    ["ENVELOPES", "the live feed, with filters"],
    ["JOURNEY", "the platform as a flow: stages, volumes, correlations"],
    ["EXECUTION", "one outcome's inputs on a scrollable timeline"],
    ["WHY", "decisions and refusals, with the rule that stopped each one"],
    ["GANTT", "spans: holding times, or input validity windows"],
    ["BOOKS", "every engine, its trades and its refusals"],
    ["HEALTH", "the services the Explorer depends on, probed live"],
], widths=[1.2, 5.4], size=8.7)

h("2.2  Breadcrumbs, not modes", 2)
p("The middle pane is a contextual surface. With nothing selected it is the live feed. Select an envelope and "
  "it recomposes around that envelope: its identity, its verdict if it is a decision, the envelopes it read or "
  "the ones that read it, and buttons to the flow and the full trace.")
p("The bar above it is a breadcrumb — FEED / symbol · topic — not a row of equal tabs. You do not choose a "
  "mode; the selection decides what is worth showing. The deeper views (flow, trace, why) open from the "
  "context's buttons.")
p("The ← button at the left of the crumb bar is back. It is greyed out when there is nothing to go back to, "
  "and it is the same action as Escape: one step, restoring the view and the envelope you had selected.")

h("2.3  Resizing: every panel", 2)
p("Three splitters: one between the filters and the list, one between the list and the detail pane, and one "
  "under the timeline. Drag any of them; double-click is not needed because the size persists in the browser, "
  "so the layout you set is the layout you get next time.")
shot("g15-layout.png", "Figure 2 — the filters pane dragged wider, a model decision selected, and the context "
                       "strip showing the four interceptors that passed before the operator ran.")

brk()
h("2.3  What the surface decides", 2)
p("The surface is a set of rules, not a template. Given a selection it asks what kind of thing it is holding "
  "and composes accordingly.")
table(["The selection", "What the surface shows"], [
    ["nothing", "the live feed and the timeline"],
    ["a decision (model.decision)", "its verdict: traded or refused, the reason verbatim, the confidence"],
    ["an outcome (a fill, a ranking)", "what it read, by stage, with the age of each input at execution"],
    ["an input with downstream traffic", "what read it next, in time order"],
    ["an input with no downstream", "the chain it belongs to, and a plain statement that nothing has read it yet"],
    ["a multi-symbol snapshot", "an explanation that it carries every symbol, so it has no per-symbol chain"],
], widths=[1.9, 4.7], size=8.8)
shot("g18-chain-context.png", "Figure 3 — an input with no downstream: the surface shows the chain that led to "
                              "it and says plainly that no model has read it in the window.")
callout("The rule the surface follows: never show an empty panel. If there is no downstream traffic, show "
        "upstream; if there is no chain, say why — a multi-symbol snapshot has none by construction.")

h("2.4  Testing the surface", 2)
p("Six checks that exercise the rules above. Each takes seconds and each has an observable outcome, so a "
  "regression shows up as a wrong panel rather than a vague feeling that the UI is off.")
table(["#", "Do this", "Expect"], [
    ["1", "open the Explorer with nothing selected", "feed plus timeline; no detail, no chain"],
    ["2", "select a market.depth row", "chain of that symbol, and the note that nothing downstream yet"],
    ["3", "select a model.decision row (WHY, then the row)", "verdict block with traded/refused and the reason"],
    ["4", "select a model.fill row", "what it read, with per-stage ages in seconds"],
    ["5", "press ← or Escape", "the previous envelope and view come back, not a blank pane"],
    ["6", "drag both splitters, then reload", "the layout is exactly as you left it"],
], widths=[0.4, 2.6, 3.6], size=8.6)
p("If a check fails, the cause is usually data rather than layout: the decisions are rare, the in-memory window "
  "covers the last 20,000 envelopes, and a symbol with no traffic will legitimately have nothing to show.")

brk()
h("3  Finding the message you want", 1)
p("Filters combine: each one narrows the list further. The count above the list tells you how many matched.")
shot("g2-filter.png", "Figure 2 — the plane filter set to intel. Every row shows a green 'INTEL' pill and the "
                      "topic counts on the left change with it.")
table(["Filter", "Use it when", "Example"], [
    ["Plane", "you want one class of traffic only", "market, intel, model, control, system"],
    ["Topic", "you know the exact kind of message", "intel.forecast, model.decision"],
    ["Symbol / key", "you are following one instrument", "BTCUSDT"],
    ["Producer", "you suspect one component", "market_data, signals, tad"],
    ["Correlation id", "you have an id from a log or a report", "cid-9f2a41…"],
    ["Contains", "you remember a value but not the topic", "0.0431 or 'liquidation'"],
], widths=[1.2, 2.8, 2.6], size=9)
p("Filters match case-insensitively and apply to the symbol, producer, correlation id and free text. The "
  "topic list under 'by volume' is also a filter: click a topic to select it.")

h("3.1  When nothing matches", 2)
p("An empty list is information. The header count drops to zero, the list says so plainly, and the timeline "
  "above it keeps filling — which tells you the feed is alive and the filters are the reason you see nothing.")
shot("g8-empty.png", "Figure 8 — a symbol filter that matches nothing: 0 envelopes, while the timeline keeps "
                     "receiving traffic.")
shot("g9-producer.png", "Figure 9 — filtering by producer: every row is a message that component wrote.")
h("3.2  Live versus paused", 2)
p("The green LIVE button means new envelopes are being prepended as they arrive. Set to PAUSED to freeze "
  "the list while you read. Selecting a time range on the timeline also pauses it, because you are no longer "
  "looking at 'now'.")

brk()
h("4  Reading an envelope", 1)
p("An envelope has three members and the right-hand pane shows all three, always: data, meta and "
  "references. A strip at the top of the pane tells you at a glance which are present.")
h("4.1  The three members", 2)
table(["Member", "What it carries", "If it is missing"], [
    ["data", "the payload for this topic", "never — a message with no data is refused"],
    ["meta", "version, plane, producer, generation, correlation id, computed_at, recorded_at, bytes", "the pane says 'inferred'"],
    ["refs", "topic, key, generation and correlation id of each input", "the pane says 'none' and explains what a reference would contain"],
], widths=[0.8, 3.4, 2.4], size=8.8)
p("References come in two kinds, and the pane always says which: **declared** by the producer, or **derived** "
  "by the recorder. The books do not declare their inputs yet, so the recorder attaches, for every model "
  "decision, the newest envelope of each input topic for that symbol in the preceding five minutes — and marks "
  "each one derived, with its age. A reference is never silently presented as declared when it is inferred.")
shot("g14-refs.png", "Figure 7 — a decision with three derived references: market.depth at 0.2 s, intel.signal "
                     "and intel.flow at 1.8 s. The refs member shows 3, and the section names its own provenance.")
p("When a producer publishes its own wrapper (the Python books do: topic, producer, data, version, seq), the "
  "pane unwraps it: the Data section shows the inner payload and names the topic and producer it was "
  "published under.")

h("4.2  Meta — provenance and freshness", 2)
table(["Field", "Meaning", "Why it matters"], [
    ["version", "the envelope contract revision", "a reader refuses a revision it does not understand"],
    ["plane", "market, intel, model, control or system", "decides priority and how it is handled"],
    ["producer", "the component that wrote it", "the first question in any incident"],
    ["generation", "the producer's own counter for that topic", "gaps reveal a dropped producer"],
    ["correlation id", "the identity that follows the message", "resolves the chain around it"],
    ["computed_at", "when the payload was produced", "this is what staleness gates refuse on"],
    ["recorded_at", "when the recorder stored it", "the difference is transport and queue time"],
    ["bytes", "size on disk", "market data is bytes, and bytes have a budget"],
], widths=[1.1, 2.4, 3.1], size=8.6)
h("4.3  Data — the payload", 2)
p("The payload exactly as it travelled, syntax-coloured. It is never rewritten by the Explorer: what you "
  "see is what the recorder stored.")
shot("g7-detail.png", "Figure 6 — one decision envelope: meta on top, payload in the middle, chain underneath.")
h("4.4  Pipeline — interceptors, operator, transformer", 2)
p("Every envelope carries the stages it passed through: the interceptors that checked it, the operator that "
  "produced it, and the transformers that changed its shape. This is the ATLAS pipeline made visible on the "
  "packet itself rather than in a log beside it. A refusal is recorded as an envelope too, so 'why not' is a "
  "packet you can open rather than a line you have to find.")
shot("g13-pipeline.png", "Figure 8 — the pipeline strip: four interceptors, the operator, and two transformers, "
                         "each with its outcome. The last one is the view-time redaction applied for this role.")
table(["Stage", "What ran", "What the chip means"], [
    ["interceptor", "authority, keying, correlation, freshness", "a pass means the rule was satisfied; a refusal names the rule and the number"],
    ["operator", "the producer (market_data, signals, a model)", "the capability that produced this payload"],
    ["transformer", "record, redact", "a change of shape: stored once, or redacted for the viewing role"],
], widths=[1.1, 2.6, 2.9], size=8.6)
p("Outcome colours: green pass, red refuse, amber modify. Hover any chip for its detail — for a refusal that is "
  "the exact text the governor recorded, for a transformer it is what changed.")

h("4.5  Chain — what else happened for that symbol", 2)
p("Every envelope recorded for the same symbol, oldest first, with arrows. Click any step to jump to it. "
  "This is the quick way to walk a symbol's recent history without leaving the page.")
callout("A message with no key (for example market.mini24h) has no chain. That is not a bug: it is a "
        "multi-symbol snapshot, so there is no single chain to show.")

brk()
h("5  The timeline: browsing history", 1)
p("The histogram above the list is traffic over time, one bar per slice, coloured by plane. It is how you "
  "find a moment rather than a message.")
shot("g3-timeline.png", "Figure 3 — a time range selected by dragging across the bars. Everything outside "
                        "dims, the header names the window, and the list narrows to it.")
steps([
    "Drag across the bars to select a window. The list reloads to that window and the live pill turns to paused.",
    "Press ▶ play to step the window forward, so the retained period plays back like a recording.",
    "Press 'live' to return to the newest 200 envelopes.",
])
p("Two details worth knowing. The selection survives the histogram's own refresh, so the bars do not jump "
  "under the cursor while you are reading. And a gap in the histogram is a real gap: the Explorer never "
  "interpolates traffic that was not recorded.")

brk()
h("6  The journey: a flow diagram from a point", 1)
p("This is the view to use when the question is 'how did this happen', rather than 'what is this message'.")
shot("g4-flow.png", "Figure 4 — the whole platform as a flow: five stages and fifteen nodes, including each "
                    "model that wrote a fill. Box height is volume; ribbon width is the number of "
                    "correlations observed.")
h("6.1  Two ways to open it", 2)
table(["Way", "What you get"], [
    ["Select an envelope, then open JOURNEY", "the flow opens at that message: two minutes of lead-in, five minutes forward, on its symbol. The node containing it is ringed."],
    ["Open JOURNEY with nothing selected", "the last five minutes across all symbols — a live view of the platform's shape."],
], widths=[2.2, 4.4], size=9)
shot("g5-anchored.png", "Figure 5 — the same diagram anchored on a model.fill envelope: the dashed ring marks "
                        "where you came in, and the window runs forward from that moment.")
h("6.2  Reading the diagram", 2)
bullets([
    "Columns are the pipeline, left to right: market data → advisories → risk & alerts → decisions → other.",
    "Box height is volume. market.depth is tall; a handful of fills is a sliver.",
    "Box label is the topic and its count; the small line beneath is the time span those messages cover.",
    "Ribbon width is the number of observed correlations between the two topics.",
    "Colour is the plane: blue market, green intel, amber model, teal control, grey system.",
    "Nodes are split by producer, so three models writing model.fill appear as three boxes, each labelled with its engine.",
    "The dashed ring marks the anchor — the message you came in on.",
])
h("6.3  Moving around", 2)
table(["Action", "What happens"], [
    ["Click a box", "opens the newest envelope of that topic in the right-hand pane"],
    ["Double-click a box", "re-anchors the flow on that node and walks forward from it"],
    ["Select another envelope in the list", "re-anchors the flow there"],
    ["Esc", "back: returns to the previous view and the state you were in — selection, scroll and filters included"],
    ["Brush the timeline", "the flow uses that window too"],
], widths=[2.4, 4.2], size=9)
h("6.4  A worked walk", 2)
steps([
    "Find a model.decision row in the list (filter plane=model, topic=model.decision).",
    "Open JOURNEY. The ring shows where the decision sits in the flow.",
    "Read left: the market data and advisories that preceded it.",
    "Double-click the fill box to re-anchor on the fill and see what followed it.",
])
callout("Honest caveat: ribbons are temporal correlations — 'next stage for the same symbol within 120 "
        "seconds' — not declared references, because the Python books do not emit refs yet. When they do, "
        "the same diagram becomes literal lineage with no interpretation required.")

brk()
h("7  The execution trace: what a model actually used", 1)
p("The journey answers 'how does traffic flow'. The execution trace answers a narrower and more useful "
  "question: for this one fill, which raw envelopes did the model read, in what order, and how old were they "
  "when it acted?")
shot("g10-execution.png", "Figure 10 — the execution trace for one model.fill: lanes by stage, nodes placed in "
                          "time, the dashed line marking the moment of execution, and the inputs listed "
                          "beneath with the age each had when the model acted.")
h("7.1  How to read it", 2)
table(["Element", "Meaning"], [
    ["Lanes", "one row per stage: market, advisories, risk, model, other"],
    ["Node", "one envelope. The label is the topic and how old it was at execution (−1s means one second before the fill)"],
    ["Dashed vertical line", "the moment of execution — the anchor envelope"],
    ["Dotted path", "the reading order: stage by stage through the chosen envelopes"],
    ["1× / 2× / 4×", "zoom. The canvas scrolls horizontally, so a fifteen-minute window stays readable"],
    ["Table beneath", "the inputs the anchor used: stage, topic, producer, age at execution, bytes"],
], widths=[1.5, 5.1], size=8.8)
h("7.2  The Gantt: spans at a glance", 2)
p("The Gantt draws each span as a horizontal bar on one time axis. Open it from the context's gantt button. "
  "It has two modes, and they answer different questions.")
shot("g19-gantt.png", "Figure 12 — positions mode: each bar is one trade from entry to exit, with the engine "
                      "labelled on the bar and the table beneath giving held time and net.")
table(["Mode", "Bars are", "Answers"], [
    ["positions", "one trade per row, from entry to exit, for the last 40 fills", "which trades were held far longer than intended, and what they returned"],
    ["trace", "one input per row, from when it was computed to when the outcome read it", "which inputs were already stale when they were used"],
], widths=[1.1, 3.1, 2.4], size=8.7)
p("Bars are coloured by stage and labelled with the producer; the table under the chart is the same data as "
  "numbers. Click any bar to open that envelope. The horizontal scale is set from the median bar, so a single "
  "very long hold stretches the axis rather than flattening every other bar into a hairline — scroll to follow it.")

h("7.3  Working through an execution", 2)
steps([
    "Open the EXECUTION tab with a fill selected (filter topic = model.fill to find one).",
    "The view scrolls itself to the moment of execution and marks it with a dashed line.",
    "Read left to right: market data first, then the advisories, then anything that followed the fill.",
    "Click any node to see its raw envelope in the right-hand pane — data, meta and references.",
    "Use the table beneath to compare ages: a decision made on a 600-second-old candle is a different decision from one made on a 2-second-old candle.",
])
callout("This is the view for 'why did the model do that'. It shows the exact envelopes, their ages, and their "
        "producers — not a summary of them. When books start emitting references, the dotted path becomes "
        "literal lineage instead of a time-ordered reading of the window.")

brk()
h("8  How and why a model traded — or did not", 1)
p("This is the question a reviewer actually asks. The WHY tab answers it directly: every decision the models "
  "recorded, split into the trades they took and the trades they refused, with the rule that stopped them.")
shot("g11-why.png", "Figure 11 — the WHY tab: reason tally on the left, decisions on the right, and the raw "
                    "refusal envelope open beside them.")
h("8.1  Reading the tab", 2)
table(["Part", "What it tells you"], [
    ["Header", "how many decisions the scan found: traded, refused, and the symbol if filtered"],
    ["Why not (left)", "the refusal reasons, tallest first. 'stale signals' dominating means the feed is late, not that the model is picky"],
    ["Decision rows", "time, symbol, reason as the model recorded it, outcome, engine"],
    ["Right pane", "the raw decision envelope: data, meta, references — including allowed and reason verbatim"],
    ["Filters", "symbol and engine; Enter runs the scan"],
], widths=[1.5, 5.1], size=8.8)
h("8.2  What a decision actually contains", 2)
p("A model.decision payload is short on purpose. It carries the engine, the symbol and side, whether the "
  "governor allowed it, the reason, and the confidence the model had. Everything else is in the envelope: "
  "who produced it, when, and the correlation id that ties it to the rest of the chain.")
table(["Field", "Example", "Why it matters"], [
    ["engine", "alpha_rank", "which model made the call"],
    ["symbol / side", "UNIUSDT · buy", "what it wanted to do"],
    ["allowed", "false", "the governor's verdict"],
    ["reason", "stale signals: 847s old (max 90s)", "the rule and the number that tripped it"],
    ["confidence", "0", "the model's own conviction — 0 means none"],
], widths=[1.1, 2.6, 2.9], size=8.6)
h("8.3  From refusal to evidence in three clicks", 2)
steps([
    "Open WHY and find the refusal you care about — the reason is already visible in the row.",
    "Click it: the right pane shows the raw envelope, so the reason is quoted from the record, not paraphrased.",
    "Double-click it: the EXECUTION tab opens on that decision, with the inputs it read and how old each was.",
])
callout("A refusal is evidence, not noise. 'stale signals: 847s old' names the rule, the age and the limit, and "
        "the envelope that says it is the one the platform stored. That is the difference between 'it did not "
        "trade' and 'we cannot explain why it did not trade'.")

brk()
h("9  Roles and redaction", 1)
p("The 'viewing as' selector in the header changes what you are allowed to see. It is a real filter applied "
  "on the server, not a cosmetic setting.")
shot("g6-redaction.png", "Figure 12 — the same envelope viewed as a bystander: depth fields removed, and a red "
                         "band naming exactly what was withheld.")
table(["Role", "Sees", "Typical use"], [
    ["owner", "everything", "you are the operator"],
    ["auditor", "prices and sizes removed, everything else", "reviewing decisions without market detail"],
    ["bystander", "the shape and provenance only", "showing status to someone outside the desk"],
], widths=[1.1, 2.8, 2.7], size=9)
p("Every redaction is recorded in the envelope's meta as redacted_for and redacted_fields, so the removal is "
  "part of the record rather than a silent edit. If someone asks what you were shown, the answer is in the "
  "message.")

h("10  Three worked examples", 1)
h("10.1  Why did we buy this?", 2)
steps([
    "Filter symbol = BTCUSDT and plane = model.",
    "Open the model.decision row you care about.",
    "Switch to JOURNEY: the ring sits on the decision; read left for the advisories and market data.",
    "Note the correlation id from the meta pane — quote it in any discussion.",
])
h("10.2  What happened at a specific minute?", 2)
steps([
    "Drag the timeline across that minute.",
    "The list narrows to it; sort your reading by plane using the left panel.",
    "Watch for a gap rather than a quiet period: a gap means no producer, not no market.",
])
h("10.3  Which model is producing decisions?", 2)
steps([
    "Filter producer = books (or a specific engine) and topic = model.decision.",
    "Compare the generation numbers: a stall shows as a flat generation while others climb.",
    "Cross-check the recorder's per-topic counts in /stats to confirm delivery, not just display.",
])

brk()
h("11  Keyboard and quick reference", 1)
table(["Key or control", "Effect"], [
    ["← (crumb bar)", "back one step — the same action as Escape"],
    ["Esc", "one step back: the previous view *and* the envelope selected in it; with no history it clears the selection"],
    ["Tab (browser)", "focus the next filter"],
    ["drag splitter", "resize the filters, the list, the detail pane or the timeline"],
    ["live / paused", "follow new envelopes, or freeze the list"],
    ["▶ play", "step the selected window forward"],
    ["reset", "clear every filter and return to live"],
], widths=[1.8, 4.8], size=9)
h("11.1  What the colours mean", 2)
table(["Colour", "Plane", "Typical topics"], [
    ["blue", "market", "ticks, market.price, market.depth, market.candle"],
    ["green", "intel", "intel.signal, intel.flow, intel.ta, intel.forecast"],
    ["amber", "model", "model.decision, model.fill, model.pnl, model.rank"],
    ["teal", "control", "risk.state, exec.quote, alert.pumpdump"],
    ["grey", "system", "system.health, review.verdict"],
], widths=[0.9, 1.1, 4.6], size=9)

h("12  Troubleshooting", 1)
table(["Symptom", "Likely cause", "What to do"], [
    ["List is empty", "nothing matches the filters", "press reset"],
    ["Count frozen at 20,000", "feed or recorder stopped", "check :8801/stats and :8796/health"],
    ["A topic never appears", "its producer is silent", "intel.forecast needs 40 bars before it publishes"],
    ["Journey shows one column", "the symbol only has market data", "pick a symbol with decisions (filter plane=model first)"],
    ["Ribbons are missing", "no correlations in the window", "widen the window or pick a busier symbol"],
    ["Chain says 'no key'", "the topic is a multi-symbol snapshot", "expected for market.mini24h and ticks"],
], widths=[1.7, 2.2, 2.7], size=8.8)
callout("The Explorer is read-only by construction: it opens the lake, never the bus, and never writes. If "
        "something looks wrong in the platform, the Explorer is the wrong place to fix it — but the right "
        "place to prove it.")


brk()
h("13  Investigating a book", 1)
p("This is the compliance workflow end to end: a question arrives about a model's trading, and the answer has "
  "to be evidence rather than opinion. The Explorer's BOOKS view is the starting point.")
shot("g22-books.png", "Figure 15 — the books view: every engine that traded, with trade count, net sum, mean "
                      "net, mean holding time and refusals.")
steps([
    "Open BOOKS from the breadcrumb. The list is every engine that has produced a fill, ordered by activity.",
    "Click a book. The summary shows trades, refusals, mean net, sum of net, mean and longest holding time, and how many instruments it touched.",
    "Scan the trade table: time, instrument, side, held, net, and the envelope id for each trade.",
    "Click any row to open that envelope: its three members, its pipeline, its references.",
    "Press export regulatory report to open the printable book report in a new tab.",
])
callout("The book view is deliberately numeric first. A regulator's first question is what the book did, not "
        "why one trade happened; the narrative belongs in the case file for a specific trade.")

h("13.1  What to look for", 2)
table(["Signal", "What it suggests", "Where to look next"], [
    ["mean held far above the model's stated horizon", "positions are being held without a decision to exit", "Gantt positions mode, longest holds"],
    ["net sum positive but mean net near zero", "a few winners carrying many flat trades", "trade table sorted by net"],
    ["refusals much larger than trades", "the governor is the binding constraint, not the model", "WHY tab, reason tally"],
    ["one instrument dominating the trade count", "concentration risk or a stuck symbol", "exposure by instrument in the report"],
    ["holds clustering at a round number of seconds", "a timeout is closing trades rather than a signal", "Gantt, held distribution"],
], widths=[1.7, 2.4, 2.5], size=8.6)

brk()
h("14  Regulatory reporting", 1)
p("Two documents are produced, both from recorded envelopes, both printable to PDF from the browser: a case "
  "file for a single decision, and a book report for a book over a window.")
h("14.1  The case file", 2)
shot("g20-case.png", "Figure 16 — a case file: what happened, the verdict, what it read, the pipeline, the "
                     "references and the raw payload, with the provenance note in the footer.")
table(["Section", "Contents", "Why it is there"], [
    ["What happened", "topic, symbol, producer, recorded time, correlation id, generation", "identity of the event under review"],
    ["Verdict", "traded or refused, with the rule quoted verbatim", "the decision, in the platform's own words"],
    ["What it read", "input envelopes with their age at the decision", "the evidence the model acted on, and how stale it was"],
    ["Pipeline", "each interceptor, the operator and the transformers, with outcome", "proof the controls ran before the action"],
    ["References", "declared or derived, with age and correlation id", "lineage, and which part of it is inferred"],
    ["Raw payload", "the stored payload, unmodified", "the primary record, readable without this tool"],
], widths=[1.2, 3.0, 2.4], size=8.6)
h("14.2  The book report", 2)
shot("g22-book-report.png", "Figure 17 — the regulatory book report: scope, summary, the full trade table with "
                            "envelope ids, exposure by instrument, methodology and a case hash.")
table(["Section", "Contents"], [
    ["1 Scope", "book, window, counts, instruments, and the data-source statement"],
    ["2 Summary", "trades, mean net, sum of net, sum of gross, mean and longest hold, refusals"],
    ["3 Trades", "every trade in the window with time, instrument, side, entry, exit, held, net, reason, envelope id"],
    ["4 Refusals", "every refused decision with the rule and the envelope id"],
    ["5 Exposure", "trade count per instrument"],
    ["6 Methodology", "how holding time and net are defined, and what derived versus declared means"],
    ["footer", "a case hash over book, window and counts, for comparison between copies"],
], widths=[1.2, 5.4], size=8.6)
h("14.3  Archiving without a document store", 2)
p("Both pages are self-contained HTML with print CSS. Saving as PDF from the browser produces the artefact; the "
  "case hash printed on the report lets two copies be compared, and every row carries an envelope id that can "
  "be resolved back to the lake. That is deliberate: the report is a rendering of records, not a new source of "
  "truth, and nothing in it cannot be checked against the store.")

brk()
h("15  Field dictionary", 1)
p("Every field an investigator will meet, in one place.")
h("15.1  Envelope metadata", 2)
table(["Field", "Type", "Meaning"], [
    ["version", "int", "envelope contract revision; a reader accepts the current and the previous one"],
    ["plane", "string", "market, intel, model, control or system"],
    ["topic", "string", "the bus topic the envelope was published on"],
    ["key", "string", "the symbol, when the message concerns one; empty for snapshots"],
    ["producer", "string", "the component that computed the payload"],
    ["generation", "uint", "the producer's own counter for that topic; gaps mean loss"],
    ["sequence", "uint", "publish order within a producer session"],
    ["cid", "string", "correlation id; the identity that follows the work"],
    ["computed_at", "timestamp", "when the payload was produced — the field staleness gates use"],
    ["received_at", "timestamp", "when the recorder accepted it"],
    ["compute_ms", "int", "how long the producer took, when it reports it"],
    ["refs", "array", "inputs; each has topic, key, generation, cid, derived and age_ms"],
    ["hops", "array", "pipeline stages: kind (interceptor/operator/transformer), name, outcome, detail, ms"],
    ["extra", "map", "free-form markers, for example refused=true or redacted_for"],
], widths=[1.1, 0.9, 4.6], size=8.4)
h("15.2  Payload fields by topic", 2)
table(["Topic", "Fields"], [
    ["model.decision", "engine, symbol, side, allowed, reason, confidence"],
    ["model.fill", "engine, symbol, side, entry, exit, gross_pct, net_pct, peak_pct, reason, held_s, notional, pnl_usd, entry_ms, exit_ms"],
    ["model.rank", "top[] with symbol and score"],
    ["model.pnl", "engine, realised, unrealised, equity"],
    ["intel.signal", "symbol, funding_pct, basis_bps, open_interest, oi_change_1h_pct, taker_ratio, smart_vs_dumb, flow_conf, score"],
    ["intel.flow", "symbol, buy_ratio, flow_conf, score, flow_series"],
    ["intel.ta", "symbol, signals[], count"],
    ["intel.forecast", "symbol, side, horizon_s, prob, move_pct, confidence, action, views[], resolution"],
    ["market.depth", "symbol, bids[], asks[], bid_qty, ask_qty, imbalance"],
    ["ticks", "p{} price per symbol, m{} 24h statistics per symbol"],
    ["market.candle", "symbol, interval, t, o, h, l, c, v, q, Q"],
    ["risk.state", "snapshot{equity, gross_multiple, longs, shorts, per_symbol}, ts"],
], widths=[1.4, 5.2], size=8.3)

brk()
h("16  API reference", 1)
p("Everything the UI does is one of these calls. They are read-only and need no key on localhost.")
table(["Endpoint", "Parameters", "Returns"], [
    ["/api/envelopes", "plane, topic, key, producer, cid, q, from, to, limit", "matching envelopes, newest first"],
    ["/api/envelope/{id}", "role", "one envelope with payload, meta, refs and hops"],
    ["/api/trace", "key", "every envelope recorded for a symbol"],
    ["/api/execution", "id, window_s", "the inputs an outcome read, with ages"],
    ["/api/why", "key, engine, limit", "decisions split into allowed and refused, with reason tallies"],
    ["/api/search", "symbol, engine, outcome, from, to, limit", "candidate decisions in a window"],
    ["/api/books", "—", "engines with trade counts, net and holding summary"],
    ["/api/book", "engine, from, to", "one book's trades, refusals and per-symbol counts"],
    ["/api/book.html", "engine, from, to", "the printable regulatory report"],
    ["/api/case.html", "id", "the printable case file for one decision"],
    ["/api/flow", "key, id, from, to", "stage nodes with volumes and counted links"],
    ["/api/timeline", "buckets", "a stacked histogram of traffic"],
    ["/api/gantt", "mode, id, limit", "spans: position lifetimes or input validity"],
    ["/api/stats", "—", "counts by plane and topic, bytes and window"],
], widths=[1.5, 2.2, 2.9], size=8.3)

brk()
h("17  Known limitations and FAQ", 1)
h("17.1  Limitations, stated plainly", 2)
table(["Limitation", "What it means in practice"], [
    ["references are usually derived", "the books do not declare their inputs; the recorder infers them from the preceding five minutes and marks them derived"],
    ["the in-memory window is finite", "20,000 envelopes; older decisions are found by scanning the lake, which takes about half a second"],
    ["market-floor coverage differs", "some topics are snapshots with no symbol, so they have no per-symbol chain"],
    ["holding time is the payload's figure", "not recomputed from fills"],
    ["net return is the model's figure", "after fees as the model computed them"],
    ["the case file is HTML printed to PDF", "there is no signed PDF; the case hash is the comparison handle"],
], widths=[2.0, 4.6], size=8.5)
h("17.2  Questions an investigator asks", 2)
table(["Question", "Answer"], [
    ["Can I see what the model read?", "CONTEXT on a decision: what it read, by stage, with ages. EXECUTION has the timeline."],
    ["Can I prove a control ran?", "PIPELINE shows each interceptor with pass or refuse, before the operator."],
    ["Can I see why it did nothing?", "WHY lists refusals with the rule and the number that tripped it."],
    ["Can I compare two books?", "BOOKS lists them side by side; the reports carry a case hash each."],
    ["Can I export for a regulator?", "Case file per trade, book report per book, both printable to PDF."],
    ["What if data is missing?", "A gap is shown as a gap: the timeline does not interpolate and the recorder counts refusals."],
], widths=[2.0, 4.6], size=8.5)

brk()
h("18  Glossary", 1)
table(["Term", "Meaning"], [
    ["envelope", "the fixed carrier: data, meta and references"],
    ["interceptor", "a rule that runs before work, and can refuse with the rule named"],
    ["operator", "the capability that produces a payload"],
    ["transformer", "a stage that changes an envelope's shape, never its truth"],
    ["hop", "one recorded pipeline stage on an envelope"],
    ["reference", "a link to an input envelope; declared by a producer or derived by the recorder"],
    ["correlation id", "the identity that follows one piece of work through the platform"],
    ["derived reference", "an input inferred by the recorder, marked derived with its age"],
    ["generation", "a producer's monotonic counter; a gap means missing messages"],
    ["plane", "the traffic class: market, intel, model, control, system"],
    ["case file", "the printable record of one decision and its evidence"],
    ["case hash", "a fingerprint over a report's scope, for comparing copies"],
    ["refusal", "a decision the governor declined, with the rule that stopped it"],
    ["holding time", "entry to exit, as recorded in the fill payload"],
    ["lake", "the recorded store of envelopes the Explorer reads"],
], widths=[1.6, 5.0], size=8.4)


brk()
h("19  A worked investigation, start to finish", 1)
p("A question arrives: the grid book has been trading a lot, and someone wants to know whether it is behaving "
  "as designed. This is the sequence an investigator would follow, and what each step produced.")
steps([
    "BOOKS, then grid. 253 trades, mean net +0.09%, mean hold 1,469 s, longest hold 5,715 s, 37 instruments.",
    "Sort the trade table by held. The long tail is visible immediately: a cluster of holds between 4,000 and 5,800 seconds.",
    "Open GANTT in positions mode for the book. The long bars are the same trades; the short ones sit around 30 to 400 seconds.",
    "Pick the longest hold and open its envelope. The fill carries the engine, the reason and the exit — here, a grid level exit rather than a timeout.",
    "Open the decision behind it. The verdict says traded, with confidence and the model's reason.",
    "Read what it read: a candle and a depth snapshot, both under a second old at the decision.",
    "Export the case file for that trade, and the book report for the window. The two documents answer the individual and the aggregate question.",
])
shot("g23-book-detail.png", "Figure 18 — step 2: the book's summary and trade table, with held time and net per trade.")
shot("g19-gantt.png", "Figure 19 — step 3: the same trades as spans; the long holds are the bars that stretch right.")
h("19.1  What the investigation found", 2)
table(["Observation", "Evidence", "Conclusion"], [
    ["holds cluster in two regimes", "Gantt: ~30–400 s and ~4,000–5,800 s", "two exit paths are in use, not one"],
    ["mean net positive but small", "summary: +0.09% per trade", "edge is thin relative to fees; cost assumptions matter"],
    ["no refusals from this book", "book summary: 0 refusals", "the governor is not the binding constraint here"],
    ["37 instruments", "exposure table", "diversified, but watch per-symbol concentration"],
], widths=[1.8, 2.3, 2.5], size=8.5)
callout("The point of the exercise is that every row above is traceable to an envelope id. The conclusion can be "
        "disputed; the evidence cannot be re-invented.")

brk()
h("20  Design notes", 1)
p("Why the Explorer behaves the way it does. These are decisions, not accidents, and each one has a "
  "consequence an investigator should know about.")
table(["Decision", "Reason", "Consequence"], [
    ["one contextual surface, not tabs", "an investigator should not have to know which mode holds the answer", "the selection decides what is shown; deeper views open from it"],
    ["never show an empty panel", "an empty pane reads as a bug or, worse, as concealment", "absence is stated: 'nothing downstream yet', 'no per-symbol chain'"],
    ["derived references are labelled", "inferred lineage is weaker evidence than declared lineage", "the case file distinguishes them, always"],
    ["refusals are recorded", "'why not' is a compliance question, not a debug question", "refusals appear in WHY and in book reports"],
    ["case hash on reports", "two printed copies must be comparable", "a changed window or count changes the hash"],
    ["read-only by construction", "a viewer that can write cannot be trusted as evidence", "the Explorer opens the lake, never the bus"],
], widths=[1.8, 2.4, 2.4], size=8.5)

brk()
h("21  Appendix — sample case file", 1)
p("An actual case file, reduced to text, so the shape is recognisable without opening the tool.")
mono([
    "Case file — model.decision NEARUSDT",
    "decision 947528192 · recorded 2026-10-19T12:04:05Z · generated 2026-10-19T12:12:49Z",
    "",
    "WHAT HAPPENED",
    "  topic            model.decision",
    "  symbol           NEARUSDT",
    "  producer         alpha_rank",
    "  correlation id   cid-8c50fc9388443f4e",
    "  generation       1695",
    "",
    "VERDICT",
    "  REFUSED          stale signals: 3230s old (max 90s)",
    "",
    "WHAT IT READ",
    "  The inputs are no longer in the in-memory window; the recorded references are:",
    "  market.candle    5007 ms   derived true   cid-fd7bb550b9ba9d5b",
    "  market.depth      906 ms   derived true   cid-59288cd5a55a3bec",
    "",
    "PIPELINE",
    "  interceptor  authority    pass",
    "  interceptor  keying       pass",
    "  interceptor  correlation  pass",
    "  interceptor  freshness    pass",
    "  operator     alpha_rank   pass   produced model.decision",
    "  transformer  record       pass   stored once in the lake",
    "",
    "RAW PAYLOAD",
    '  { "engine": "alpha_rank", "symbol": "NEARUSDT", "side": "buy",',
    '    "allowed": false, "reason": "stale signals: 3230s old (max 90s)", "confidence": 0 }',
])

brk()
h("22  Appendix — sample book report rows", 1)
p("The trade table of a book report, first rows only. Every row carries the envelope id that resolves it.")
mono([
    "#   time      instrument  side  entry      exit       held   net      reason  envelope",
    "1   10:40:23  APTUSDT     sell  0.77724    0.77587     19s   0.116%   grid    1916297078",
    "2   10:40:31  APTUSDT     sell  0.775882   0.774514    65s   0.116%   grid    2104151056",
    "3   10:40:35  ADAUSDT     sell  0.218687   0.218446    67s   0.050%   grid    718800172",
    "4   10:40:56  FETUSDT     sell  0.188821   0.188501    58s   0.109%   grid    1594588925",
    "5   10:40:58  BNBUSDT     sell  725.465    724.739     55s   0.040%   grid    2098522729",
    "6   10:41:01  ETHUSDT     sell  2428.08    2425.55     91s   0.044%   grid    587372541",
    "7   10:41:06  AAVEUSDT    sell  151.708    151.483    101s   0.088%   grid    1382730695",
    "8   10:41:08  ETCUSDT     sell  7.74694    7.73892     98s   0.043%   grid    1841120590",
    "",
    "summary: 253 trades · mean net +0.090% · sum +21.54% · mean held 1469s · longest 5715s",
    "case hash: 485c35a20fff538d",
])

brk()
h("23  Appendix — operations checklist", 1)
table(["Check", "How", "Expected"], [
    ["the Explorer is up", "open :8802, the header counts climb", "envelope count rising"],
    ["the recorder is consuming", "GET :8801/stats", "received = written, refused small, errors 0"],
    ["the feed is alive", "GET :8796/health", "prices > 0, kline series rising"],
    ["the queue is bound", "GET :15672, queue q.rec.model.decision", "consumers 1, publish rising"],
    ["a case file resolves", "open /api/case.html?id=<a recent decision>", "six sections render"],
    ["a book report resolves", "open /api/book.html?engine=grid", "scope counts match BOOKS"],
], widths=[1.6, 3.0, 2.0], size=8.5)


brk()
h("24  Search cookbook", 1)
p("Twelve questions an investigator actually asks, and the exact filter that answers each one. Every recipe is "
  "stated as a filter you can type, not a concept.")
table(["You want to know", "Filter", "What you will see"], [
    ["everything one symbol did today", "symbol = BTCUSDT", "the feed narrows to that symbol; use the chain for order"],
    ["what one model decided", "producer = alpha_rank, topic = model.decision", "every decision that engine recorded"],
    ["why a model stopped trading", "WHY, outcome = refused, window 6 h", "refusals ranked by rule, with the numbers"],
    ["which trades were held too long", "GANTT, positions", "spans sorted by start; the long bars stand out"],
    ["what an input was used for", "select the input, CONTEXT", "what read it next, in time order"],
    ["whether an advisory was stale", "select the decision, CONTEXT what it read", "age per input in seconds"],
    ["a trade from a report id", "paste the envelope id into /api/case.html?id=", "the case file for that trade"],
    ["what happened in a minute", "timeline brush across it", "the window's traffic, by plane"],
    ["whether a producer is alive", "producer = tad, window 1 h", "empty means silent; compare with the header counts"],
    ["which instruments dominate a book", "BOOKS, then the book", "exposure by instrument in the report"],
    ["what a refusal was about", "WHY row, click", "the raw refusal envelope, reason verbatim"],
    ["how the whole platform is moving", "JOURNEY with nothing selected", "the five-stage flow with volumes"],
], widths=[2.0, 2.3, 2.4], size=8.4)

brk()
h("25  Data quality checks", 1)
p("Before a report is signed, these are the checks worth running. Each one is a question with a numeric answer, "
  "so it can be recorded alongside the document.")
table(["Check", "How", "Pass looks like"], [
    ["no gap in a producer", "compare generation between consecutive envelopes of a topic", "generation increases by one"],
    ["no silent queue", "queue q.rec.<topic> consumers and publish rate", "consumers 1, rate matching the producer"],
    ["refusals are explained", "WHY, reason tally", "every refusal carries a rule and a number"],
    ["inputs are fresh", "CONTEXT on a sample of decisions", "ages well under the documented limit"],
    ["holding times are plausible", "GANTT, positions", "no cluster at implausible durations without a reason"],
    ["reports reconcile", "book report counts vs BOOKS summary", "identical trade and refusal counts"],
    ["case files resolve", "open three case files at random", "six sections, payload present, hash printed"],
], widths=[1.7, 2.6, 2.4], size=8.4)
h("25.1  What to do when a check fails", 2)
table(["Failure", "First action"], [
    ["generation gap", "check the producer's process and its queue; a gap is a fact, not a rounding error"],
    ["queue silent with consumers", "check the binding; a missing binding looks exactly like a quiet topic"],
    ["refusal without a rule", "treat as a defect: every refusal must name the rule that produced it"],
    ["stale inputs beyond the limit", "the governor should have refused; if it did not, the limit is wrong"],
    ["report counts disagree", "trust the report's envelope ids and recount from the lake"],
], widths=[2.0, 4.6], size=8.5)

brk()
h("26  Appendix — a search session, as it happened", 1)
p("A transcript of a short session, so the rhythm of the tool is visible: filter, read, drill, export.")
mono([
    "USER  books",
    "TOOL  grid 253 trades · net +21.54% · mean +0.090% · held 1469s · refusals 0",
    "      hft 73 trades · net +3.54% · mean +0.050% · held 439s",
    "      movers 65 trades · net +4.98% · mean +0.080% · held 375s",
    "      alpha_rank 0 trades · 326 refusals",
    "",
    "USER  open grid",
    "TOOL  summary: trades 253 · refusals 0 · mean net 0.09% · sum 21.54%",
    "      mean held 1469s · longest 5715s · instruments 37",
    "",
    "USER  gantt, positions",
    "TOOL  40 spans · mean hold 1876s · bars coloured by stage",
    "",
    "USER  click the longest bar",
    "TOOL  envelope 215876507 · model.fill · GALAUSDT · grid",
    "      held 547s … (a level exit, not a timeout)",
    "",
    "USER  export case file",
    "TOOL  six sections · payload present · case hash printed",
    "",
    "USER  export book report",
    "TOOL  scope 253 trades · 0 refusals · 37 instruments · case hash 485c35a20fff538d",
])


brk()
h("27  Case study: investigating a refusal", 1)
p("A model did nothing for six hours, and someone asks why. The answer is not 'it had no signal' — it is a "
  "rule, a number and an envelope.")
steps([
    "WHY, window 6 h, outcome refused. The tally shows stale signals dominating, with counts in the hundreds.",
    "Open the newest refusal. The verdict reads refused, and the reason quotes the age and the limit: 'stale signals: 3230s old (max 90s)'.",
    "Read the pipeline. The freshness interceptor passed, because the freshness rule measures the envelope's computed_at, not the signal's age — the refusal came from the model's own signal-age check.",
    "Read what it read. Two derived references, both under six seconds old, so the market data was fresh; the signals were not.",
    "Export the case file. It carries the reason verbatim, the references with ages, and the raw payload.",
])
table(["Question", "Answer found", "Where"], [
    ["Did the model try to trade?", "yes, repeatedly, hundreds of times", "WHY row count"],
    ["What stopped it?", "the signal-age rule inside the model, at 90 seconds", "reason text"],
    ["Was the market data stale?", "no: inputs were seconds old", "case file, what it read"],
    ["Was the governor at fault?", "no: the model never presented an order", "pipeline shows no refusal there"],
    ["What is the actual fault?", "the signal producer was not publishing for that symbol", "generation gap on intel.signal"],
], widths=[1.9, 2.6, 2.1], size=8.5)
callout("This is the distinction that matters in a report: the platform did not fail to trade — one producer "
        "stopped, and the model correctly refused to act on stale information. The evidence says so in three "
        "clicks, and the case file carries it.")
shot("g11-why.png", "Figure 20 — the WHY tab where the investigation starts: refusals ranked by rule, and the "
                    "raw refusal beside them.")

brk()
h("28  Operating and retaining", 1)
p("The Explorer is one binary and one directory. This is what it needs and what it keeps.")
table(["Component", "Port", "Purpose"], [
    ["envelopeapi", "8802", "the Explorer and its API"],
    ["recorder", "8801", "consumes the bus once and writes the lake"],
    ["market data", "8796", "the source of market envelopes"],
    ["RabbitMQ", "61613 / 15672", "STOMP and management"],
], widths=[1.4, 1.2, 4.0], size=8.6)
h("28.1  Retention", 2)
table(["Data", "Kept", "Why"], [
    ["in-memory window", "20,000 envelopes", "instant filtering and the live feed"],
    ["lake journals", "until rotated or expired", "the record; case files and reports resolve against it"],
    ["scanned cache", "for the session", "lets a case file open a decision older than the window"],
    ["case files and reports", "whatever you save", "self-contained HTML printed to PDF"],
], widths=[1.6, 2.0, 3.0], size=8.6)
h("28.2  Restarting", 2)
p("Restart order matters because the Explorer reads the lake the recorder writes. Start the broker, then the "
  "feed, then the recorder, then the Explorer. If the Explorer comes up first it will show an empty feed and "
  "fill within a few seconds of the recorder starting.")
table(["Symptom after a restart", "Cause", "Fix"], [
    ["feed empty, header at 0", "recorder not up yet", "start the recorder, wait for /stats"],
    ["feed frozen", "recorder stopped", "check /stats and the recorder log"],
    ["a topic missing entirely", "queue not bound", "the recorder declares bindings at startup; restart it"],
    ["case file 404", "envelope outside the window and never scanned", "open it through WHY first, which scans the lake"],
], widths=[1.8, 2.2, 2.6], size=8.5)


brk()
h("29  The desktop app", 1)
p("The Explorer also ships as a macOS application: the same interface in a native window, with a menu, "
  "keyboard shortcuts and a native print dialog for Save as PDF.")
table(["Property", "Value"], [
    ["bundle", "The Envelope Explorer.app"],
    ["size", "11 MB, of which 11 MB is the bundled Go service; the shell itself is 112 KB"],
    ["built with", "Swift 6.4 and WKWebView, one source file, no runtime dependency"],
    ["starts the service", "yes, when the bundled envelopeapi is present and nothing is listening"],
    ["offline behaviour", "a page that says the service is not running and how to start it"],
    ["signing", "ad-hoc; distributing to another Mac needs a Developer ID and notarisation"],
], widths=[1.5, 5.1], size=8.6)
h("29.1  Why native rather than Electron", 2)
table(["", "native (WKWebView)", "Electron", "Tauri"], [
    ["app size", "11 MB", "~150–200 MB", "~10 MB"],
    ["toolchain on this machine", "Swift 6.4, Xcode 15", "Node — not on PATH", "Rust — absent"],
    ["runtime dependency", "system WebView", "bundled Chromium", "system WebView"],
    ["start time", "instant", "~1 s cold", "instant"],
], widths=[1.3, 1.8, 1.8, 1.7], size=8.4)
p("Electron was written first and is kept in the repository as an alternative, but shipping it would mean "
  "downloading a Chromium runtime to provide a web view the operating system already has.")
h("29.2  Shortcuts in the app", 2)
table(["Key", "Action"], [
    ["⌘1 … ⌘7", "Feed, Journey, Execution, Why, Gantt, Books, Health"],
    ["⌘[", "back one step, the same as Escape"],
    ["⌘R", "reconnect to the service"],
    ["⌘E", "case file for the selected envelope"],
    ["⇧⌘E", "book report"],
    ["⌘P", "print — use Save as PDF"],
], widths=[1.2, 5.4], size=8.6)
callout("The app holds no data. It renders the explorer API and writes nothing, which is the same guarantee the "
        "browser version makes — and the reason either can be used as evidence.")

# footer + save
def add_page_numbers(paragraph):
    def field(instr):
        run = paragraph.add_run()
        b = OxmlElement("w:fldChar"); b.set(qn("w:fldCharType"), "begin")
        i = OxmlElement("w:instrText"); i.set(qn("xml:space"), "preserve"); i.text = instr
        s = OxmlElement("w:fldChar"); s.set(qn("w:fldCharType"), "separate")
        run._r.append(b); run._r.append(i); run._r.append(s)
        return run
    def end(text="1"):
        run = paragraph.add_run(text)
        e = OxmlElement("w:fldChar"); e.set(qn("w:fldCharType"), "end")
        run._r.append(e); return run
    paragraph.add_run("The Envelope Explorer · user guide · page ")
    field(" PAGE "); end("1").font.size = Pt(8)
    paragraph.add_run(" of ")
    field(" NUMPAGES "); end("1").font.size = Pt(8)

for section in doc.sections:
    para = section.footer.paragraphs[0]
    para.text = ""; para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    add_page_numbers(para)

out = os.path.join(HERE, "Envelope-Explorer-User-Guide.docx")
doc.save(out)
print("saved", out)
