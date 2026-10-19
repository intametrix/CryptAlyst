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
    "7  Roles and redaction",
    "8  Three worked examples",
    "9  Keyboard and quick reference",
    "10  Troubleshooting",
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

h("2.1  Two tabs in the middle", 2)
p("ENVELOPES is a list, one row per message. JOURNEY is a flow diagram of how traffic moves through the "
  "platform, from market data to advisories to decisions. Escape always takes you back to the previous view.")

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
shot("g8-empty.png", "Figure 7 — a symbol filter that matches nothing: 0 envelopes, while the timeline keeps "
                     "receiving traffic.")
shot("g9-producer.png", "Figure 8 — filtering by producer: every row is a message that component wrote.")
h("3.2  Live versus paused", 2)
p("The green LIVE button means new envelopes are being prepended as they arrive. Set to PAUSED to freeze "
  "the list while you read. Selecting a time range on the timeline also pauses it, because you are no longer "
  "looking at 'now'.")

brk()
h("4  Reading an envelope", 1)
p("Click any row and the right-hand pane shows everything known about that message, in three parts.")
h("4.1  Meta — provenance and freshness", 2)
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
h("4.2  Data — the payload", 2)
p("The payload exactly as it travelled, syntax-coloured. It is never rewritten by the Explorer: what you "
  "see is what the recorder stored.")
shot("g7-detail.png", "Figure 6 — one decision envelope: meta on top, payload in the middle, chain underneath.")
h("4.3  Chain — what else happened for that symbol", 2)
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
shot("g4-flow.png", "Figure 4 — the whole platform as a flow: five stages, thirteen nodes, fourteen "
                    "correlations. Box height is volume; ribbon width is how many correlations were observed.")
h("6.1  Two ways to open it", 2)
table(["Way", "What you get"], [
    ["Select an envelope, then open JOURNEY", "the flow opens at that message: two minutes of lead-in, five minutes forward, on its symbol. The node containing it is ringed."],
    ["Open JOURNEY with nothing selected", "the last five minutes across all symbols — a live view of the platform's shape."],
], widths=[2.2, 4.4], size=9)
shot("g5-anchored.png", "Figure 5 — the same diagram anchored on a decision envelope: the dashed ring marks "
                        "where you came in, and the window runs forward from that moment.")
h("6.2  Reading the diagram", 2)
bullets([
    "Columns are the pipeline, left to right: market data → advisories → risk & alerts → decisions → other.",
    "Box height is volume. market.depth is tall; a handful of fills is a sliver.",
    "Box label is the topic and its count; the small line beneath is the time span those messages cover.",
    "Ribbon width is the number of observed correlations between the two topics.",
    "Colour is the plane: blue market, green intel, amber model, teal control, grey system.",
    "The dashed ring marks the anchor — the message you came in on.",
])
h("6.3  Moving around", 2)
table(["Action", "What happens"], [
    ["Click a box", "opens the newest envelope of that topic in the right-hand pane"],
    ["Double-click a box", "re-anchors the flow on that node and walks forward from it"],
    ["Select another envelope in the list", "re-anchors the flow there"],
    ["Esc", "back to the previous view; pressed again it clears the selection"],
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
h("7  Roles and redaction", 1)
p("The 'viewing as' selector in the header changes what you are allowed to see. It is a real filter applied "
  "on the server, not a cosmetic setting.")
shot("g6-redaction.png", "Figure 9 — the same envelope viewed as a bystander: depth fields removed, and a red "
                         "band naming exactly what was withheld.")
table(["Role", "Sees", "Typical use"], [
    ["owner", "everything", "you are the operator"],
    ["auditor", "prices and sizes removed, everything else", "reviewing decisions without market detail"],
    ["bystander", "the shape and provenance only", "showing status to someone outside the desk"],
], widths=[1.1, 2.8, 2.7], size=9)
p("Every redaction is recorded in the envelope's meta as redacted_for and redacted_fields, so the removal is "
  "part of the record rather than a silent edit. If someone asks what you were shown, the answer is in the "
  "message.")

h("8  Three worked examples", 1)
h("8.1  Why did we buy this?", 2)
steps([
    "Filter symbol = BTCUSDT and plane = model.",
    "Open the model.decision row you care about.",
    "Switch to JOURNEY: the ring sits on the decision; read left for the advisories and market data.",
    "Note the correlation id from the meta pane — quote it in any discussion.",
])
h("8.2  What happened at a specific minute?", 2)
steps([
    "Drag the timeline across that minute.",
    "The list narrows to it; sort your reading by plane using the left panel.",
    "Watch for a gap rather than a quiet period: a gap means no producer, not no market.",
])
h("8.3  Which model is producing decisions?", 2)
steps([
    "Filter producer = books (or a specific engine) and topic = model.decision.",
    "Compare the generation numbers: a stall shows as a flat generation while others climb.",
    "Cross-check the recorder's per-topic counts in /stats to confirm delivery, not just display.",
])

brk()
h("9  Keyboard and quick reference", 1)
table(["Key or control", "Effect"], [
    ["Esc", "back to the previous view; again to clear the selection"],
    ["Tab (browser)", "focus the next filter"],
    ["live / paused", "follow new envelopes, or freeze the list"],
    ["▶ play", "step the selected window forward"],
    ["reset", "clear every filter and return to live"],
], widths=[1.8, 4.8], size=9)
h("9.1  What the colours mean", 2)
table(["Colour", "Plane", "Typical topics"], [
    ["blue", "market", "ticks, market.price, market.depth, market.candle"],
    ["green", "intel", "intel.signal, intel.flow, intel.ta, intel.forecast"],
    ["amber", "model", "model.decision, model.fill, model.pnl, model.rank"],
    ["teal", "control", "risk.state, exec.quote, alert.pumpdump"],
    ["grey", "system", "system.health, review.verdict"],
], widths=[0.9, 1.1, 4.6], size=9)

h("10  Troubleshooting", 1)
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
