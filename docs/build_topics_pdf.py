#!/usr/bin/env python3
"""Build the topics publisher/subscriber reference PDF.

A registry table, then a per-topic subscriber breakdown (each subscriber with
its usage, code file and line number). Re-run after the registry changes:
    python3 docs/build_topics_pdf.py
"""
import os

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm, inch
from reportlab.platypus import (
    Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)
from reportlab.platypus.tableofcontents import TableOfContents
from reportlab.graphics.shapes import Drawing, Circle, Line, String, Group, Wedge, PolyLine
from reportlab.lib.colors import white as _white

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "topics-pubsub.pdf")

# --- document style: matches the Design & Architecture Reference ---
ACCENT = HexColor("#1f4e79")   # accent blue (headings, table headers)
MUTED = HexColor("#596370")    # muted grey (captions, footers, H3)
BODY = HexColor("#1a1a1a")     # near-black body text
INK = ACCENT                   # headings/title use the accent
RULE = HexColor("#b0b6bf")     # light grid border
GRAY = MUTED
ZEBRA = HexColor("#f1f3f6")    # zebra rows + mono background
BLACK = ACCENT                 # table header background = accent blue
YELLOW = HexColor("#ffffff")   # table header text = white

S = {
    "title": ParagraphStyle("title", fontName="Helvetica-Bold", fontSize=24,
                            leading=29, textColor=INK, spaceAfter=2),
    "subtitle": ParagraphStyle("subtitle", fontName="Helvetica-Oblique", fontSize=10.5,
                               leading=13, textColor=GRAY, spaceAfter=10),
    "h2": ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=12,
                         leading=15, textColor=ACCENT, spaceBefore=8, spaceAfter=8,
                         keepWithNext=1),
    "body": ParagraphStyle("body", fontName="Helvetica", fontSize=10,
                           leading=13, textColor=BODY, spaceAfter=5),
    "small": ParagraphStyle("small", fontName="Helvetica", fontSize=8.5,
                            leading=11, textColor=GRAY),
    "cell": ParagraphStyle("cell", fontName="Helvetica", fontSize=8.3,
                           leading=10.5, textColor=BODY),
    "cellb": ParagraphStyle("cellb", fontName="Helvetica-Bold", fontSize=8.3,
                            leading=10.5, textColor=INK),
    "head": ParagraphStyle("head", fontName="Helvetica-Bold", fontSize=8.6,
                           leading=11, textColor=YELLOW),
    "mono": ParagraphStyle("mono", fontName="Courier", fontSize=8,
                           leading=10, textColor=BODY),
    "chapter": ParagraphStyle("chapter", fontName="Helvetica-Bold", fontSize=15.5,
                              leading=19, textColor=ACCENT, spaceBefore=10, spaceAfter=8),
    "h3": ParagraphStyle("h3", fontName="Helvetica-Bold", fontSize=10.5,
                         leading=13, textColor=MUTED, spaceBefore=8, spaceAfter=3),
    "caption": ParagraphStyle("caption", fontName="Helvetica-Oblique", fontSize=9.5,
                              leading=12, textColor=MUTED, spaceAfter=10, alignment=1),
}

# cover / intro / TOC styles (cover matches the Design & Architecture Reference)
COVER_TITLE = ParagraphStyle("covertitle", fontName="Helvetica-Bold", fontSize=36,
                             leading=42, textColor=ACCENT, spaceAfter=10, alignment=1)
COVER_SUB = ParagraphStyle("coversub", fontName="Helvetica", fontSize=18,
                           leading=24, textColor=MUTED, spaceAfter=4, alignment=1,
                           wordSpace=3)
COVER_EDITION = ParagraphStyle("coveredition", fontName="Helvetica-Oblique", fontSize=10,
                               leading=13, textColor=MUTED, spaceAfter=12, alignment=1)
COVER_BODY = ParagraphStyle("coverbody", fontName="Helvetica", fontSize=11,
                            leading=16, textColor=BODY, spaceAfter=8)
TOC_STYLE = ParagraphStyle("toc", fontName="Helvetica", fontSize=10.5,
                           leading=17, textColor=BODY)

# shared code-block style (design-ref mono: Courier 8pt on #f1f3f6)
CODE_STYLE = ParagraphStyle("codestyle", fontName="Courier", fontSize=8, leading=10,
                            textColor=BODY, backColor=ZEBRA, borderPadding=5,
                            borderColor=RULE, borderWidth=0.4)


class TopicsDoc(SimpleDocTemplate):
    """Multi-pass template: collects headings into a table of contents and
    draws page numbers on every page except the cover (page 1)."""

    def afterFlowable(self, flowable):
        # Only chapters appear in the Contents (the numbered subsections are
        # already visible in the body). Tracking h2 too made the TOC overflow
        # onto a second, mostly-empty page.
        if isinstance(flowable, Paragraph) and flowable.style.name == "chapter":
            self.notify("TOCEntry", (0, flowable.getPlainText(), self.page))


def _page_footer(canvas, doc):
    page = canvas.getPageNumber()
    if page <= 1:
        return  # no page number on the cover
    canvas.saveState()
    canvas.setFont("Helvetica", 8.5)
    canvas.setFillColor(GRAY)
    canvas.drawCentredString(A4[0] / 2.0, 1.0 * cm, str(page - 1))
    canvas.restoreState()


def diagram(name, max_w=16.5 * cm, max_h=20 * cm):
    """Embed a rendered Mermaid PNG, scaled to fit the page."""
    path = os.path.join(HERE, "diagrams", name + ".png")
    if not os.path.exists(path):
        return Paragraph("<i>[diagram %s missing]</i>" % name, S["caption"])
    try:
        from PIL import Image as PILImage
        im = PILImage.open(path)
        w, h = im.size
    except Exception:
        return Paragraph("<i>[diagram %s unreadable]</i>" % name, S["caption"])
    scale = min(max_w / w, max_h / h)
    return Image(path, width=w * scale, height=h * scale)


def fig(name, caption, max_w=16.5 * cm, max_h=20 * cm):
    """A diagram + its caption. No leading spacer, so a preceding heading's
    keepWithNext binds to the image and they stay together on the same page."""
    return [diagram(name, max_w, max_h), Paragraph(caption, S["caption"])]


def sec_fig(title, name, caption, max_h=20 * cm):
    """A numbered subsection heading kept together with its diagram + caption
    (KeepTogether, so a heading is never orphaned at the bottom of a page)."""
    return [KeepTogether([sec(title), diagram(name, 16.5 * cm, max_h),
                          Paragraph(caption, S["caption"])])]


_CH = [0]   # current chapter number
_SEC = [0]  # current subsection number within the chapter


def chapter(title, num=None):
    """Start a new chapter (page break + chapter heading). Resets the subsection counter."""
    if num is not None:
        _CH[0] = num
    _SEC[0] = 0
    return [PageBreak(), Paragraph(title, S["chapter"])]


def sec(title):
    """A numbered subsection heading: 'N.M — Title'."""
    _SEC[0] += 1
    return Paragraph("%d.%d &middot; %s" % (_CH[0], _SEC[0], title), S["h2"])


def title_box(word, fill, stroke, text_color):
    """A single word in its own coloured box, for the cover title stack."""
    p = Paragraph(word, ParagraphStyle("titlebox", fontName="Helvetica-Bold",
                 fontSize=16, leading=20, textColor=text_color, alignment=1))
    t = Table([[p]], colWidths=[11 * cm], hAlign="CENTER")
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), fill),
        ("BOX", (0, 0), (-1, -1), 1.2, stroke),
        ("LEFTPADDING", (0, 0), (-1, -1), 14), ("RIGHTPADDING", (0, 0), (-1, -1), 14),
        ("TOPPADDING", (0, 0), (-1, -1), 9), ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
    ]))
    return t


# cover title stack: (word, fill, stroke, text) — one colour per term
TITLE_BOXES = [
    ("Supervisor", HexColor("#d8eef2"), HexColor("#0f6b7a"), HexColor("#08343c")),
    ("Lanes", HexColor("#eef1f5"), HexColor("#4b5b6e"), HexColor("#1f2b38")),
    ("Engines", HexColor("#dbe9fb"), HexColor("#1d4e89"), HexColor("#0b2545")),
    ("Models", HexColor("#ffe9c7"), HexColor("#b7791f"), HexColor("#4a2f00")),
    ("Messaging", HexColor("#d9f2e3"), HexColor("#1d7a4d"), HexColor("#0d3b26")),
    ("Data Event Types", HexColor("#e9e3f7"), HexColor("#5b3fa8"), HexColor("#2a1a55")),
]


import math as _math


def _radial_drawing():
    """A cartoon hand-drawn mindmap: CRYPTALYST centre, six pastel
    role-coloured nodes, joined by gently bowed hand-drawn branches.
    Colours honour style-guide.md (the classDef role palette)."""
    W, H = 16.8 * cm, 12 * cm
    cx, cy = W / 2.0, H / 2.0
    d = Drawing(W, H)
    # style-guide role palette (c_control, c_neutral, c_market, c_model, c_intel, c_store)
    nodes = [
        ("Supervisor", "#d8eef2", "#0f6b7a", "#08343c"),
        ("Lanes", "#eef1f5", "#4b5b6e", "#1f2b38"),
        ("Engines", "#dbe9fb", "#1d4e89", "#0b2545"),
        ("Models", "#ffe9c7", "#b7791f", "#4a2f00"),
        ("Messaging", "#d9f2e3", "#1d7a4d", "#0d3b26"),
        ("Data Event Types", "#e9e3f7", "#5b3fa8", "#2a1a55"),
    ]
    R, Rs, Rc = 4.25 * cm, 1.7 * cm, 1.85 * cm
    # hand-drawn branches: gently bowed polylines, each in its node's stroke colour
    for i, (label, fill, stroke, textc) in enumerate(nodes):
        ang = _math.radians(90 - i * 60)
        sx = cx + Rc * _math.cos(ang); sy = cy + Rc * _math.sin(ang)
        ex = cx + (R - Rs) * _math.cos(ang); ey = cy + (R - Rs) * _math.sin(ang)
        mx = (sx + ex) / 2.0 - 0.20 * cm * _math.sin(ang)
        my = (sy + ey) / 2.0 + 0.20 * cm * _math.cos(ang)
        d.add(PolyLine([sx, sy, mx, my, ex, ey],
                       strokeColor=HexColor(stroke), strokeWidth=2.4,
                       strokeLineJoin=1, strokeLineCap=1))
    # pastel sticker nodes with drop shadows
    for i, (label, fill, stroke, textc) in enumerate(nodes):
        ang = _math.radians(90 - i * 60)
        x = cx + R * _math.cos(ang)
        y = cy + R * _math.sin(ang)
        d.add(Circle(x + 0.09 * cm, y - 0.09 * cm, Rs,
                     fillColor=HexColor("#d8dce2"), strokeColor=None))
        d.add(Circle(x, y, Rs, fillColor=HexColor(fill),
                     strokeColor=HexColor(stroke), strokeWidth=2.5))
        d.add(String(x, y - 0.12 * cm, label, fontName="Helvetica-Bold", fontSize=10,
                     fillColor=HexColor(textc), textAnchor="middle"))
    # centre node in the accent navy (style-guide primary)
    d.add(Circle(cx + 0.09 * cm, cy - 0.09 * cm, Rc,
                 fillColor=HexColor("#d8dce2"), strokeColor=None))
    d.add(Circle(cx, cy, Rc, fillColor=HexColor("#1f4e79"),
                 strokeColor=HexColor("#16395a"), strokeWidth=2.5))
    d.add(Circle(cx, cy, Rc - 0.14 * cm, strokeColor=HexColor("#3a6ea5"),
                 strokeWidth=1.0, fillColor=None))
    d.add(String(cx, cy - 0.21 * cm, "CRYPTALYST", fontName="Helvetica-Bold", fontSize=16,
                 fillColor=_white, textAnchor="middle"))
    return d


def callout(text):
    """A single-cell shaded box (design-ref 'Light List Accent 1' callout)."""
    t = Table([[Paragraph(text, ParagraphStyle("callout", fontName="Helvetica", fontSize=9,
                 leading=12, textColor=BODY))]], colWidths=[17.3 * cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), HexColor("#dbe9fb")),
        ("BOX", (0, 0), (-1, -1), 0.5, ACCENT),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def _cover_meta_table():
    rows = [
        ("Document", "CryptAlyst — Supervisor, Lanes, Engines, Models, Messaging, Data Event Types"),
        ("Platform Version", "2.0"),
        ("Document Revision", "2"),
        ("Status", "living reference; the 22-topic registry is normative"),
        ("Audience", "engineers, quants, risk, and anyone asked to change the platform"),
        ("Scope", "the running paper-trading platform: bus, engines, models, books"),
        ("Designed by", "Andrew Preston"),
        ("Copyright", "© TekVolt Systems 2026"),
    ]
    head = [Paragraph("Field", S["head"]), Paragraph("Value", S["head"])]
    data = [head] + [[Paragraph(r[0], S["cellb"]), Paragraph(r[1], S["cell"])] for r in rows]
    t = Table(data, colWidths=[3.2 * cm, 14.1 * cm], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BLACK), ("TEXTCOLOR", (0, 0), (-1, 0), YELLOW),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [ZEBRA, HexColor("#ffffff")]),
        ("GRID", (0, 0), (-1, -1), 0.4, RULE), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, 0), 5), ("BOTTOMPADDING", (0, 0), (-1, 0), 5),
        ("TOPPADDING", (0, 1), (-1, -1), 4), ("BOTTOMPADDING", (0, 1), (-1, -1), 4),
    ]))
    return t


# (topic, exchange, retention, publishers, subscriber note)
TOPICS = [
    ("market.price",  "market", "drop", "market_data", "backend (turtle), master"),
    ("market.candle", "market", "drop", "market_data", "tad (TA), forecast (warm-up)"),
    ("market.depth",  "market", "drop", "market_data", "backend (detectors), feeds"),
    ("market.mini24h","market", "drop", "market_data", "master (24h change)"),
    ("ticks",         "market", "drop", "market_data", "backend (flow windows)"),
    ("intel.signal",  "intel",  "60s",  "signals",     "books (crypto_pack / executor / alpha via :8793)"),
    ("intel.flow",    "intel",  "60s",  "signals",     "books (flow confidence)"),
    ("intel.movers",  "intel",  "60s",  "market_data, signals", "books (movers surface)"),
    ("intel.board",   "intel",  "60s",  "market_agent","books, master (opportunity board)"),
    ("intel.ta",      "intel",  "60s",  "tad",         "books (ta_vote via ta_live.json)"),
    ("intel.forecast","intel",  "60s",  "forecast",    "books (forecast gate)"),
    ("alert.pumpdump","alert",  "300s", "backend, signal_bus", "fast_ignition, crypto_strategies"),
    ("model.decision","model",  "5m",   "alpha_rank, models10, crypto_pack, executor, alpha_signals, skim_pump, curve_rider, hft, movers, grid", "master (journal)"),
    ("model.fill",    "model",  "5m",   "same as decision", "master, fit_weights (research)"),
    ("model.pnl",     "model",  "5m",   "decision producers + supervisor", "master, capital (allocator)"),
    ("model.rank",    "model",  "5m",   "alpha_rank",  "master"),
    ("model.evaluate","model",  "60s",  "tad",         "books (signal-completion pacer)"),
    ("risk.state",    "risk",   "5m",   "risk",        "books (risk gate)"),
    ("review.verdict","review", "5m",   "portfolio_review", "master"),
    ("exec.quote",    "exec",   "60s",  "maker",       ""),
    ("system.health", "system", "10m",  "healthpub, master", "master"),
    ("system.control","system", "1d",   "master, supervisor, cli", "master"),
]

RECORDER = ("recorder (:8801)", "single lake writer (all topics)", "core/cmd/recorder/main.go:222")
BUSCON = ("bus_consumer (:8798)", "record projection (all topics)", "bus_consumer.py:64")

# topic-specific subscribers beyond the two all-topic consumers:
# (subscriber, usage, file:line)
SPECIFIC = {
    "market.price":   [("backend (turtle)", "turtle book pricing", "backend/internal/turtle/store.go")],
    "market.candle":  [("tad", "TA bars (1m)", "tad/main.go:62"),
                       ("forecast", "warm-up bars", "forecast/main.go:89")],
    "market.depth":   [("backend (detectors)", "depth/pump detectors", "backend/internal/detect/*.go"),
                       ("feeds", "book imbalance", "feeds.py:74")],
    "market.mini24h": [("master", "24h change surface", "master.py")],
    "ticks":          [("backend (flow)", "flow windows", "backend/internal/flow/*.go")],
    "intel.signal":   [("books", "entry signals via :8793 /latest", "crypto_pack.py:182, executor.py:41")],
    "intel.flow":     [("books", "flow confidence", "crypto_pack.py:182")],
    "intel.movers":   [("books", "movers surface", "crypto_pack.py:182")],
    "intel.board":    [("books / master", "opportunity board", "market_agent.py:126 (pub)")],
    "intel.ta":       [("tactics.ta_signals", "TA vote → ta_live.json", "tactics.py:362")],
    "intel.forecast": [("books", "forecast gate", "tactics.py forecast()")],
    "alert.pumpdump": [("fast_ignition", "alert-scored entries", "fast_ignition.py"),
                       ("crypto_strategies", "pump/dump signals", "crypto_strategies.py:12")],
    "model.decision": [("master", "decision journal", "master.py:1009")],
    "model.fill":     [("master", "fill journal", "master.py:1009"),
                       ("fit_weights", "research weights", "fit_weights.py")],
    "model.pnl":      [("master", "pnl surface", "master.py:1009"),
                       ("capital", "allocator input", "capital.py")],
    "model.rank":     [("master", "rank surface", "master.py:1009")],
    "model.evaluate": [("books", "signal-completion pacer pulse", "tad/main.go:320 (pub)")],
    "risk.state":     [("books", "risk gate", "risk.py")],
    "review.verdict": [("master", "review surface", "master.py:1009")],
    "exec.quote":     [],
    "system.health":  [("master", "health panel", "master.py:7239")],
    "system.control": [("master", "control commands", "master.py")],
}


# Producer contract: what each model is, the data its envelope carries, and the
# exact insertion points (publish call sites, file:line). Surveyed from code on
# 2026-10-21; re-verify after producer refactors.
# topic -> (description, envelope data, [insertion sites])
CONTRACT = {
    "market.price": (
        "Last trade price plus 24h-change metadata for the whole liquid universe, one compact map per tick.",
        "{p: {sym: px}, m: {sym: {c: pct24h, q: quoteVol}}, ts, msgs} — Go variant: {p, ts}",
        ["market_data.py:292", "core/cmd/marketdata/main.go:228"]),
    "market.candle": (
        "Closed 1m/15m OHLCV bars only (k.K.X) — the TA and forecast warm-up feed.",
        "{symbol, interval, t, o, h, l, c, v, q, Q, ts}",
        ["market_data.py:249", "core/cmd/marketdata/main.go:589→426"]),
    "market.depth": (
        "5-level order book per symbol with sizes, spread and bid/ask imbalance; published on change or 1s heartbeat.",
        "py: {symbol, bids: [[p,q]×5], asks: [[p,q]×5], spread_bps, bid_qty, ask_qty, imbalance, ts} — Go: bids/asks as [{price,qty}]",
        ["market_data.py:328-333", "core/cmd/marketdata/main.go:243"]),
    "market.mini24h": (
        "Universe-size heartbeat — how many symbols the 24h mini-ticker window covers.",
        "{count, ts}",
        ["market_data.py:246", "core/cmd/marketdata/main.go:229-230"]),
    "ticks": (
        "All-symbol tick snapshot (price + 24h move + quote vol); the flow-window input on the backend.",
        "{p: {sym: px}, m: {sym: {pct, qv, p}}, ts} — backup path: {p, ts, msgs} raw, no envelope",
        ["market_data.py:293", "core/cmd/marketdata/main.go:227", "market_data_rest_backup.py:304 (raw, bypasses registry)"]),
    "intel.signal": (
        "signals.py per-symbol analysis score — taker-buy ratio, flow confidence and parts blended into one rankable number.",
        "{symbol, taker_by_ratio, flow_conf, flow_parts, score, ts} (from analyse())",
        ["signals.py:325"]),
    "intel.flow": (
        "Flow confidence per symbol with the last 10 flow readings — the books' flow-conviction input.",
        "{symbol, buy_ratio, flow_conf, flow_series: last 10, score}",
        ["signals.py:326-332"]),
    "intel.movers": (
        "Gainers/losers/most-traded surface. Two producer variants with different payload shapes.",
        "signals: {gainers, losers, most_traded, universe, ts} — market_data (slimmer): {gainers: [{s,pct,qv}]×15, losers, ts}",
        ["signals.py:306", "market_data.py:269-271"]),
    "intel.board": (
        "The market_agent opportunity board: top-15 ranked rows with score components, its natural-language thoughts, and flagged symbols. Owner-critical.",
        "{ts, universe, opportunities: rows[:15], thoughts: [{kind,sym,score,text,ts}][:20], flagged: [sym]}",
        ["market_agent.py:127"]),
    "intel.ta": (
        "tad per-symbol TA snapshot — the full signal set per cycle, or a single firing signal.",
        "{symbol, signals: [sig], count, ts} or {signal: sig, ts}; sig = {cursor, symbol, kind, side, strength, note, ts}",
        ["tad/main.go:317", "tad/main.go:326", "tad/main.go:414 (/scan)"]),
    "intel.forecast": (
        "forecast service blended forecast per symbol+side+horizon with agent views and a trade action (enter/hold/liquidate). Gates book entries.",
        "{symbol, side, horizon_s, prob, move_pct, confidence, action, resolution, views: [{agent, side, prob, move_pct, confidence, note}], price, bar_age_s, ts}",
        ["forecast/main.go:502", "forecast/main.go:565"]),
    "alert.pumpdump": (
        "Pump/dump alert from the backend detectors with severity, reasons and full metrics; signal_bus republishes with cursor+received_ms.",
        "{symbol, kind, score, event_id, severity, summary, ts_ms, detector, reasons: []} + {cursor, received_ms} on republish",
        ["signal_bus.py:33-50", "core/cmd/signalbus/main.go:75"]),
    "model.decision": (
        "A book's entry decision, chained by decision_id to every later fill. Emitted through the single lib/emit emitter by every book that opens a position.",
        "{decision_id, engine, symbol, side, allowed, reason, confidence, ts_ms}",
        ["lib/emit.py:85 ← models10.py:268, crypto_strategies.py:222, movers.py:263, hybrids.py:287, alpha_rank.py:172, grid.py:218, churn.py:76, hft.py:280, alpha_signals.py:363"]),
    "model.fill": (
        "A closed position's result, carrying fill_id + the originating decision_id — the decision-outcome substrate.",
        "{fill_id, decision_id, engine, symbol, side, entry, exit, gross_pct, net_pct, peak_pct, reason, held_s, notional, pnl_usd, entry_ms, exit_ms, features, rank_score, why, slippage_bps, signal_price}",
        ["lib/emit.py:61 ← models10.py:299, crypto_strategies.py:173,213, movers.py:230, hybrids.py:311, alpha_rank.py:125, grid.py:253, churn.py:66, hft.py:249, alpha_signals.py:180,223,272"]),
    "model.pnl": (
        "Equity mark: alpha_rank on each position close; the supervisor on each cycle for every graded book.",
        "alpha_rank: {engine, equity, net_pct, symbol} — supervisor: {engine, equity, return_pct, trades, open}",
        ["alpha_rank.py:111-113", "supervisor.py:317-320"]),
    "model.rank": (
        "alpha_rank cross-sectional ranking — top and bottom of the liquid universe by blended score.",
        "{top: [{symbol, score}]×8, bottom: [...]×8, ts} (key 'xsec')",
        ["alpha_rank.py:134-137"]),
    "model.evaluate": (
        "tad's bar-close pacer pulse with a fresh feature snapshot — the book-decision layer's completion signal. Currently NO subscriber.",
        "{symbol, reason: 'bar_close', ts_ms, inputs: fresh.snapshot(sym, now)}",
        ["tad/main.go:363"]),
    "risk.state": (
        "risk.py portfolio snapshot — gross exposure, direction counts, per-symbol notionals; the shared gate's view of the books.",
        "{snapshot: {equity, start_equity, return_pct, gross_notional, gross_multiple, longs, shorts, per_symbol: {sym: notional}}, ts}",
        ["risk.py:185"]),
    "review.verdict": (
        "portfolio_review's per-book verdict each cycle — stop for evidence-losing books, keep for the rest. Its disable write is a read-modify-UNION (A6).",
        "{engine, action: stop|keep, why, return_pct}",
        ["portfolio_review.py:25-34 (pub fn)", "portfolio_review.py:102 (call)"]),
    "exec.quote": (
        "maker.py quote intent — the paper execution layer's priced quote per symbol+side.",
        "{symbol, side, px, prob, notional}",
        ["maker.py:49-50"]),
    "system.health": (
        "Platform health snapshot — every service's up/down, latency and port; the dashboard's health panel source.",
        "{services: [{name, up, ms, detail?, port}], up, total, worst_ms, ts}",
        ["health_pub.py:70", "core/cmd/healthpub/main.go:97"]),
    "system.control": (
        "Control plane for operator commands (restart/stop/flag). WARNING: the only publish site is malformed (TypeError swallowed at master.py:241; Bus has no close()) — the topic is effectively unsent.",
        "{action, actor: 'dashboard', ts} — intended shape",
        ["master.py:241 (BROKEN — never sends)"]),
}


def _contract_table():
    head = [Paragraph("Topic", S["head"]), Paragraph("What the model produces", S["head"]),
            Paragraph("Data in the envelope", S["head"]), Paragraph("Inserted at (publish call sites)", S["head"])]
    rows = [head]
    for topic, _e, _r, _p, _s in TOPICS:
        desc, env, sites = CONTRACT.get(topic, ("—", "—", []))
        rows.append([
            Paragraph("<font face='Courier'>%s</font>" % topic, S["cell"]),
            Paragraph(desc, S["cell"]),
            Paragraph("<font face='Courier'>%s</font>" % env, S["mono"]),
            Paragraph("<font face='Courier'>%s</font>" % "<br/>".join(sites), S["mono"]),
        ])
    t = Table(rows, colWidths=[2.6 * cm, 5.6 * cm, 5.6 * cm, 4.0 * cm], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BLACK),
        ("TEXTCOLOR", (0, 0), (-1, 0), YELLOW),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [ZEBRA, HexColor("#ffffff")]),
        ("GRID", (0, 0), (-1, -1), 0.4, RULE),
        ("LINEBELOW", (0, 0), (-1, 0), 1.2, BLACK),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, 0), 6),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
        ("TOPPADDING", (0, 1), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 4),
    ]))
    return t


def _reg_table():
    head = [Paragraph("Topic", S["head"]), Paragraph("Exch", S["head"]),
            Paragraph("Ret", S["head"]), Paragraph("Publisher(s)", S["head"]),
            Paragraph("Other subscriber(s)", S["head"])]
    rows = [head]
    for topic, exch, ret, pub, sub in TOPICS:
        rows.append([
            Paragraph("<font face='Courier'>%s</font>" % topic, S["cell"]),
            Paragraph(exch, S["cell"]), Paragraph(ret, S["cell"]),
            Paragraph(pub, S["cell"]), Paragraph(sub, S["cell"]),
        ])
    t = Table(rows, colWidths=[3.0 * cm, 1.4 * cm, 1.1 * cm, 4.6 * cm, 7.2 * cm],
              repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BLACK),
        ("TEXTCOLOR", (0, 0), (-1, 0), YELLOW),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [ZEBRA, HexColor("#ffffff")]),
        ("GRID", (0, 0), (-1, -1), 0.4, RULE),
        ("LINEBELOW", (0, 0), (-1, 0), 1.2, BLACK),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, 0), 6),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
        ("TOPPADDING", (0, 1), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 4),
    ]))
    return t


def _breakdown_table():
    head = [Paragraph("Topic", S["head"]), Paragraph("Subscriber", S["head"]),
            Paragraph("Usage", S["head"]), Paragraph("Code (file:line)", S["head"])]
    rows = [head]
    for topic, _exch, _ret, _pub, _sub in TOPICS:
        # the two all-topic consumers, then the topic-specific ones
        subs = [RECORDER, BUSCON] + SPECIFIC.get(topic, [])
        for i, (name, usage, loc) in enumerate(subs):
            rows.append([
                Paragraph("<font face='Courier'>%s</font>" % (topic if i == 0 else ""), S["cell"]),
                Paragraph(name, S["cell"]), Paragraph(usage, S["cell"]),
                Paragraph("<font face='Courier'>%s</font>" % loc, S["mono"]),
            ])
    t = Table(rows, colWidths=[3.0 * cm, 4.2 * cm, 5.2 * cm, 5.4 * cm], repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), BLACK),
        ("TEXTCOLOR", (0, 0), (-1, 0), YELLOW),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [ZEBRA, HexColor("#ffffff")]),
        ("GRID", (0, 0), (-1, -1), 0.4, RULE),
        ("LINEBELOW", (0, 0), (-1, 0), 1.2, BLACK),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, 0), 6),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
        ("TOPPADDING", (0, 1), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 3),
    ]
    # span the topic cell across its subscriber rows for readability
    t.setStyle(TableStyle(style))
    return t


# Models/topics that are MISWIRED: a model should listen to a topic but reads an
# HTTP/file proxy instead, or the topic has no consumer at all.
NOT_LISTENING = [
    ("all books", "model.evaluate",
     "the signal-completion pacer pulse (published by tad) has NO subscriber — the book-decision layer is unpaced",
     "tad/main.go:320 (pub) — no consumer"),
    ("alpha (funding_carry, xsec_momentum)", "intel.board",
     "reads the opportunity board via master /api/opportunities (file proxy), not the intel.board topic",
     "alpha.py:175"),
    ("crypto_strategies (model_stack)", "alert.pumpdump",
     "reads /api/alerts from backend :8787 (HTTP), not the alert.pumpdump topic",
     "crypto_strategies.py:12"),
    ("fast_ignition (disabled)", "alert.pumpdump",
     "reads the alert stream via backend HTTP, not the topic",
     "fast_ignition.py"),
    ("models10 (funding_fade)", "intel.ta",
     "reads TA via ta_live.json (tactics.ta_signals), not the intel.ta topic",
     "tactics.py:362"),
    ("models10 (funding_fade)", "intel.forecast",
     "reads forecast via FC_API :8790, not the intel.forecast topic",
     "models10.py:66"),
    ("crypto_pack / executor (disabled)", "intel.signal / intel.flow / intel.movers",
     "the signal consumers are disabled under best-5, so these topics have no live subscriber",
     "crypto_pack.py:182, executor.py:41"),
]


def _not_listening_table():
    head = [Paragraph("Model", S["head"]), Paragraph("Should listen to", S["head"]),
            Paragraph("Gap", S["head"]), Paragraph("Code (file:line)", S["head"])]
    rows = [head]
    for model, topic, gap, loc in NOT_LISTENING:
        rows.append([
            Paragraph(model, S["cell"]),
            Paragraph("<font face='Courier'>%s</font>" % topic, S["cell"]),
            Paragraph(gap, S["cell"]),
            Paragraph("<font face='Courier'>%s</font>" % loc, S["mono"]),
        ])
    t = Table(rows, colWidths=[4.0 * cm, 4.4 * cm, 5.8 * cm, 3.6 * cm], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BLACK),
        ("TEXTCOLOR", (0, 0), (-1, 0), YELLOW),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [ZEBRA, HexColor("#ffffff")]),
        ("GRID", (0, 0), (-1, -1), 0.4, RULE),
        ("LINEBELOW", (0, 0), (-1, 0), 1.2, BLACK),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, 0), 6),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
        ("TOPPADDING", (0, 1), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 4),
    ]))
    return t


def _arch_table():
    rows = [
        ["Broker", "1", "RabbitMQ, STOMP :61613 — the single message backbone"],
        ["Exchanges", "8", "market, intel, alert, model, risk, review, exec, system (routing groups)"],
        ["Topics", "22", "routing keys within the exchanges (the full registry above)"],
        ["Queues", "~60", "2 per topic (q.<topic> for bus_consumer, q.rec.<topic> for recorder) + stomp-subscription-* for live subscribers"],
        ["Producers", "14", "market_data, tad, forecast, signals, feeds, market_agent, portfolio_review, books, backend, supervisor, healthpub, master, cli, maker"],
        ["Consumers", "5", "recorder (:8801), bus_consumer (:8798), backend (:8787), signal_bus (:8793), master (:8799)"],
        ["Secondary bus", "1", "signal_bus :8793 is an HTTP proxy (/latest) the books poll — not a second RabbitMQ broker"],
    ]
    head = [Paragraph("Layer", S["head"]), Paragraph("Count", S["head"]),
            Paragraph("Description", S["head"])]
    data = [head] + [[Paragraph(r[0], S["cellb"]), Paragraph(r[1], S["cell"]),
                      Paragraph(r[2], S["cell"])] for r in rows]
    t = Table(data, colWidths=[3.0 * cm, 1.6 * cm, 13.2 * cm], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BLACK),
        ("TEXTCOLOR", (0, 0), (-1, 0), YELLOW),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [ZEBRA, HexColor("#ffffff")]),
        ("GRID", (0, 0), (-1, -1), 0.4, RULE),
        ("LINEBELOW", (0, 0), (-1, 0), 1.2, BLACK),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, 0), 6),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
        ("TOPPADDING", (0, 1), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 4),
    ]))
    return t


# per-model IN / function / OUT
MODELS_IO = [
    ("market_data (Go :8796)", "Binance websocket (bookTicker/depth/kline)",
     "normalise + aggregate raw market data", "market.price / candle / depth / mini24h / ticks"),
    ("tad (Go :8792)", "Binance 1m klines (tad/main.go:62)",
     "TA indicators: EMA / RSI / ATR / VWAP / Donchian", "intel.ta, model.evaluate (pacer)"),
    ("forecast (Go :8790)", "marketdata /klines (forecast/main.go:89)",
     "7-agent forecast blend (momentum/volume/vol/reversion/ma/breakout/pressure)", "intel.forecast"),
    ("signals (Python)", "marketdata /movers + /streamprices",
     "taker ratio + flow confidence per symbol", "intel.signal, intel.flow, intel.movers"),
    ("feeds (Python)", "Binance OI / depth / taker (feeds.py)",
     "OI change (5m/15m/1h) + book imbalance + price×OI quadrant", "features.json (file, not bus)"),
    ("market_agent (Python)", "marketdata ticks",
     "rank the opportunity board", "intel.board + opportunities.json"),
    ("alpha · funding_carry, xsec_momentum", "board (alpha.py:175), klines, premiumIndex, forecast, TA",
     "momentum + funding long (side-agnostic)", "model.decision, model.fill, model.pnl"),
    ("models10 · funding_fade", "klines, forecast (:8790), TA (ta_live.json)",
     "funding_fade rule (15m bars)", "model.decision, model.fill"),
    ("crypto_strategies · model_stack", "/api/alerts + /api/models/signals (:8787)",
     "consensus of ≥3 model votes", "model.decision, model.fill"),
    ("churn", "ticker prices (SPOT)",
     "1 fill/sec throughput demo (no edge)", "model.decision, model.fill"),
    ("backend · turtle/detectors (Go :8787)", "market.depth / flow / ticks",
     "pump/dump + breakout detection", "alert.pumpdump + screen/survey"),
    ("recorder (Go :8801)", "all topics (recorder/main.go:222)",
     "persist every envelope to the lake", "state/lake3 (not a bus topic)"),
    ("portfolio_review (Python)", "/api/all (every book)",
     "grade books; stop underperformers", "review.verdict + disabled.json (union)"),
]


# per-model detailed descriptions: (name, how it works, best used for)
MODELS_DETAIL = [
    ("market_data (Go :8796)",
     "The single process allowed to talk to Binance. It subscribes to the exchange websocket "
     "(bookTicker, depth5, kline_1m/15m, miniTicker) and shards the streams — at most 900 per "
     "connection — so ~400 symbols are carried on several independent sockets. On startup it "
     "BACKFILLS 200 × 1m and 200 × 15m bars per symbol from REST, so the platform is never "
     "history-starved after a restart. It serves REST endpoints (/klines, /movers, /streamprices) "
     "and publishes the normalised topics.",
     "The raw truth everything else derives from. Books must never hit Binance directly — they read "
     "these topics or the REST endpoints. The backfill is what made the platform start trading again "
     "after a fresh boot."),
    ("tad (Go :8792)",
     "The technical-analysis daemon. It pulls 1m klines per symbol and computes EMA (9/21/50), RSI14, "
     "ATR14, VWAP, a 20-bar Donchian channel and a volume-spike ratio, then emits the signals that "
     "actually FIRE — EMA crosses, overbought/oversold, breakouts, VWAP stretch, trend vs EMA50. "
     "Since the per-symbol rebuild it recomputes only when a symbol's own bar closes (no batch sweep), "
     "and it paces the model-evaluate pulse to the books.",
     "Its intel.ta signals are the TA vote in the decision chain — a book reads them as confirmation, "
     "and the 60s staleness turns an absent vote into a no-vote. Its model.evaluate pulse is the pacer "
     "that should wake each book when a symbol's signal set completes."),
    ("forecast (Go :8790)",
     "Seven agents look at the same 1m series from a different angle — momentum (EMA slope + recent "
     "return), volume, volatility, reversion, MA, breakout, pressure — and each returns a probability "
     "(a sigmoid of its raw score) plus an expected move. A supervisor BLENDS them into one "
     "probability and move estimate, and a grade() feedback loop reweights each agent by whether its "
     "past calls made money. It refuses to answer below MIN_BARS=50 (warm-up).",
     "The primary weight in a decision: its blended probability must clear forecast_min (0.45) to "
     "enter, its move must clear min_expected_move, and consensus_dead closes a losing position only "
     "when the blend AND a majority of agents turn against the thesis."),
    ("signals (Python)",
     "Pulls the most-traded names from marketdata, then computes each symbol's taker buy/sell ratio "
     "(long/short pressure) and a flow confidence from its recent flow series. It also republishes "
     "the movers list so that surface reaches the bus.",
     "Its intel.flow feed is the conviction input to sizing (risk_for scales capital with flow "
     "confidence); intel.signal is the entry signal for the signal-consuming books."),
    ("feeds (Python)",
     "The data the models could not see before. It polls open interest, the order book and the taker "
     "ratio, keeps a 1-hour ring buffer of OI and mid-price per symbol, and computes the OI change "
     "over 5m/15m/1h plus the price×OI quadrant (confirmation/divergence/accumulation/distribution/"
     "climax).",
     "Its features.json feeds the OI gate — the quadrant is a strategy-aware score multiplier (momentum "
     "prefers confirmation, reversion prefers divergence), and it is the substrate for the F4 with/without-gate "
     "replay."),
    ("market_agent (Python)",
     "Watches the liquid universe and scores every name, then ranks the top opportunities and flags "
     "the ones above a score threshold. It writes the board to disk AND publishes it to intel.board.",
     "The owner-critical opportunity board the owner reviews every cycle — and alpha reads its flagged "
     "names to know which symbols to trade."),
    ("alpha · funding_carry, xsec_momentum, cascade_rebound (Python)",
     "Three fee-viable books, each its own strategy: xsec_momentum is cross-sectional — long the "
     "strongest N and short the weakest N by 24h return, rebalanced every 15m, market-neutral so BTC "
     "beta cancels and only relative alpha remains. cascade_rebound buys the snap-back after a 1m "
     "drop of 3 sigma on 5× median volume (a liquidation proxy without the feed). funding_carry "
     "fades a perpetual-funding extreme while spot is flat, collecting the reversion over ~8h. Each "
     "reads the board, klines, funding, forecast and TA, then trades through the shared gates.",
     "The owner-selected winners. Their model.decision/fill/pnl are the realised PnL the leaderboard "
     "and the solver measure. Each book's tunables live in its CFG (tp/sl/hold/risk)."),
    ("models10 · funding_fade (Python)",
     "Reads the funding premium (premiumIndex). If the funding rate is above +0.08% it shorts — the "
     "crowd is paying heavily to stay long, so the reversion is fadable; below -0.08% it buys. It is "
     "gated by the forecast and the higher-timeframe trend, and sized by RISK with the 8%-of-book cap "
     "scaled by size_mult.",
     "The kept funding book. Its signal is a mean-reversion on the funding premium; the forecast and "
     "trend gates stop it fading into a regime that is still trending."),
    ("crypto_strategies · model_stack (Python)",
     "Sums the model votes for a symbol; when at least two agree (|net| ≥ 2) it trades the consensus "
     "— long if the net is positive, short if negative. It reads the alert and model-vote streams from "
     "the Go backend.",
     "A consensus filter: it only acts when several independent models point the same way, which is "
     "meant to cut the single-model noise that Stage-0 found costs the book."),
    ("churn (Python)",
     "A throughput demo, honestly labelled: one fill per second regardless of direction, alternating "
     "side. It exists to prove the whole pipeline (signal → decision → fill → ledger) moves, not as "
     "an edge — at 0.2% round-trip its fees alone bleed the book.",
     "A canary for the pipeline, not a strategy. Its fills exercise the decision→fill→ledger chain so "
     "the substrate stays honest."),
    ("backend · turtle / detectors (Go :8787)",
     "The Go detector server: it consumes market flow/depth/ticks and runs the pump/dump onset and "
     "turtle-breakout detectors, emitting alerts plus the screen/survey surfaces.",
     "Its alert.pumpdump feed is the alert stream fast_ignition and crypto_strategies act on; the "
     "screen/survey surfaces are the owner's watch view."),
    ("recorder (Go :8801)",
     "The single bus consumer and the only writer of the lake. Every envelope passes the interceptor "
     "pipeline — authority, keying, correlation, freshness — each recorded as a hop, then is written "
     "once to a rotating, indexed, compressed segment.",
     "The durable record (state/lake3) every research artefact reads: the F4 replay, the solver, "
     "fit_weights and the decision journal all replay this lake. Nothing is ever deleted — segments "
     "seal, never truncate."),
    ("portfolio_review (Python)",
     "Grades every book (trade count, return_pct, recent-loss streak) and stops the underperformers, "
     "UNION-adding them to the disable list so it never clobbers the owner's config. Publishes each "
     "verdict.",
     "The book lifecycle: it removes books that have had enough trades to judge and still lose, and "
     "its review.verdict feed is the audit trail of those stops."),
]


# who consumes each model's output (parallel to MODELS_DETAIL)
USED_BY = [
    "every engine &mdash; the single raw feed no one else is allowed to touch",
    "the books (TA confirmation) &mdash; and the model.evaluate pacer pulse",
    "the gate chain &mdash; the primary weight (forecast_min) for every entry",
    "the books &mdash; flow confidence feeds the conviction sizing",
    "the OI gate &mdash; the quadrant multiplier on each decision",
    "the owner's board review &mdash; alpha reads its flagged names",
    "the owner-selected winners &mdash; the leaderboard and the solver measure them",
    "the kept funding book",
    "the kept consensus book",
    "the pipeline canary (honestly not an edge)",
    "fast_ignition + crypto_strategies &mdash; the alert feed",
    "every research artefact &mdash; F4 replay, solver, fit_weights",
    "the book lifecycle &mdash; stops the underperformers",
]


def _model_paragraphs():
    out = []
    for (name, how, used), by in zip(MODELS_DETAIL, USED_BY):
        out.append(Paragraph("<b>%s</b>" % name, ParagraphStyle(
            "mname", fontName="Helvetica-Bold", fontSize=10.5, leading=13, textColor=ACCENT,
            spaceBefore=6, spaceAfter=2)))
        out.append(Paragraph("How it works — %s" % how, S["body"]))
        out.append(Paragraph("<b>Best used for</b> — %s" % used, S["body"]))
        box = Table([[Paragraph("<b>Used by</b> &mdash; %s" % by, ParagraphStyle(
            "usedby", fontName="Helvetica", fontSize=8.5, leading=11, textColor=BODY))]],
            colWidths=[17.3 * cm])
        box.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), HexColor("#ffe9c7")),
            ("BOX", (0, 0), (-1, -1), 0.5, HexColor("#b7791f")),
            ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        out.append(box)
        out.append(Spacer(1, 4))
    return out



def _io_table():
    head = [Paragraph("Model", S["head"]), Paragraph("IN (reads)", S["head"]),
            Paragraph("Function", S["head"]), Paragraph("OUT (publishes)", S["head"])]
    rows = [head]
    for model, inn, fn, out in MODELS_IO:
        rows.append([
            Paragraph("<font face='Courier'>%s</font>" % model, S["cell"]),
            Paragraph(inn, S["cell"]), Paragraph(fn, S["cell"]),
            Paragraph(out, S["cell"]),
        ])
    t = Table(rows, colWidths=[4.0 * cm, 4.6 * cm, 5.4 * cm, 4.2 * cm], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BLACK),
        ("TEXTCOLOR", (0, 0), (-1, 0), YELLOW),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [ZEBRA, HexColor("#ffffff")]),
        ("GRID", (0, 0), (-1, -1), 0.4, RULE),
        ("LINEBELOW", (0, 0), (-1, 0), 1.2, BLACK),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, 0), 6),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
        ("TOPPADDING", (0, 1), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 4),
    ]))
    return t


def _envelope_table():
    rows = [
        ("The model REQUIRES (reads from an inbound envelope)", "meta.key (the symbol), meta.topic, data (the payload) — that is all a consumer needs to route and act"),
        ("The model ADDS (when it publishes)", "producer (its name), data (its calculation), key (the symbol), plus CID / refs (the decision→fill provenance)"),
        ("The model COMMUNICATES BACK", "one topic it publishes to (e.g. model.decision / model.fill) carrying the payload"),
    ]
    head = [Paragraph("Aspect", S["head"]), Paragraph("Envelope fields", S["head"])]
    data = [head] + [[Paragraph(r[0], S["cellb"]), Paragraph(r[1], S["cell"])] for r in rows]
    t = Table(data, colWidths=[6.0 * cm, 11.8 * cm], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BLACK),
        ("TEXTCOLOR", (0, 0), (-1, 0), YELLOW),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [ZEBRA, HexColor("#ffffff")]),
        ("GRID", (0, 0), (-1, -1), 0.4, RULE),
        ("LINEBELOW", (0, 0), (-1, 0), 1.2, BLACK),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, 0), 6),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
        ("TOPPADDING", (0, 1), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 4),
    ]))
    return t


CODE = """# 1 · a model computes a result
side, conf = funding_fade_strategy(klines("BTCUSDT"))     # -> ("buy", 0.7)

# 2 · it publishes ONE result to ONE topic, tagged with the symbol
emit.decision("models10", "BTCUSDT", "buy", True, "funding_fade")
  -> bus.pub("model.decision", "BTCUSDT", payload, producer="models10")
  -> spec = TopicRegistry["model.decision"]                # exchange "model"
  -> guard: producer "models10" must be in spec.producers  # else REFUSED
  -> env = Envelope(topic="model.decision", key="BTCUSDT",
                    producer="models10", data=payload)      # stamps version+received_at
  -> transport.publish(exchange="model", routing_key="model.decision", env.json)

# 3 · the broker fans out to the queues
#     q.model.decision (bus_consumer) · q.rec.model.decision (recorder)
#     stomp-subscription-* (master + other live subscribers)

# 4 · the recorder runs the interceptor pipeline, then writes the lake
for rule in [authority, keying, correlation, freshness]:
    env = rule(env); env.meta.hops.append(hop(rule, outcome))   # pass | refuse

# 5 · the correct engine receives it and routes by key (the symbol)
def on_model_decision(env):
    sym = env.meta.key        # "BTCUSDT" -> the engine acts on THAT symbol
    data = env.data           # the decision payload
    book.consider(sym, data)  # no broadcast; the key selects the symbol"""


def _flow_section():
    out = []
    out.append(sec("Message flow — how a model's output travels"))
    out.append(Paragraph(
        "A model (book) computes a result and publishes it to <b>one topic</b>. The bus wraps "
        "it in an <b>envelope</b> and routes it by exchange + routing key. Every consumer "
        "receives the envelope and reads <b>meta.key</b> (the symbol) to act on the right "
        "instrument — routing is by key, never by broadcast.", S["body"]))
    out.append(_envelope_table())
    out.append(Spacer(1, 4))
    out.append(Paragraph("Pseudo-code", S["h3"]))
    code = Paragraph(CODE.replace("\n", "<br/>"), CODE_STYLE)
    out.append(code)
    return out


def _decision_section():
    out = []
    out.append(Paragraph(
        "<b>Engine</b> = a running process (the supervisor launches one "
        "<font face='Courier'>python3 -u &lt;file&gt;.py</font>). <b>Model</b> = a strategy/rule "
        "inside an engine — one engine can run many models, and <font face='Courier'>--only</font> "
        "narrows it. <b>Book</b> = the paper-trading account for one model: its equity, open positions "
        "and closed trades. One book per model. A model's numbers land on its book.", S["body"]))
    out.append(Paragraph(
        "When a model has finished computing, its numbers are not a trade — they are a <b>signal</b> "
        "that then passes a chain of gates and weights before the book opens a position.", S["body"]))
    out.append(Paragraph(
        "<b>1 · the model scores.</b> The model's rule reads its inputs and returns a <b>side</b> "
        "(buy/sell) and a <b>score</b> (its conviction). "
        "<b>2 · the forecast.</b> The 7-agent forecaster blends momentum/volume/volatility/reversion/ma/"
        "breakout/pressure into one <b>probability (0–1)</b> plus an <b>expected move</b>. "
        "<b>3 · the TA.</b> tad supplies signal votes (EMA cross, RSI, Donchian). "
        "<b>4 · the OI gate.</b> the price×OI quadrant multiplies the score, strategy-aware. "
        "<b>5 · the gates.</b> forecast_min (0.45), min_expected_move, allow_counter_trend, and the "
        "higher-timeframe trend gate (ext_gate) each veto or admit. "
        "<b>6 · the risk gate.</b> risk.allow checks max_positions, confidence and signal age. "
        "<b>7 · the entry.</b> if every gate passes, the book opens a position, sized by the weighting below.", S["body"]))
    out.append(Paragraph(
        "<b>How the weighting works.</b> The forecast is the primary weight: its blended probability "
        "must clear forecast_min to enter at all. Conviction sizing scales capital with the number of "
        "confirmations — <font face='Courier'>risk_for(base, conf) = base × (0.6 + 0.2 × min(conf, 4))</font>. "
        "The OI quadrant is a <b>strategy-aware multiplier</b> on the score (momentum prefers confirmation, "
        "reversion prefers divergence), and <font face='Courier'>size_mult</font> (now 3.0) scales the final "
        "notional. No single number is decisive — the decision is the product of the model score, the "
        "forecast probability, and these multipliers.", S["body"]))
    out.append(Spacer(1, 6))
    return out


ENGINE_ROSTER = [
    ("market_data (Go)", "the only Binance feed", "market.price / candle / depth / mini24h / ticks"),
    ("tad (Go)", "technical-analysis daemon", "intel.ta, model.evaluate"),
    ("forecast (Go)", "7-agent forecaster", "intel.forecast"),
    ("signals (Python)", "taker ratio + flow confidence", "intel.signal / intel.flow / intel.movers"),
    ("feeds (Python)", "open-interest + book imbalance", "features.json (a file, not a topic)"),
    ("market_agent (Python)", "the opportunity board", "intel.board + opportunities.json"),
    ("alpha (Python)", "funding_carry, xsec_momentum, cascade_rebound", "model.decision / fill / pnl"),
    ("models10 (Python)", "funding_fade (via --only)", "model.decision / fill"),
    ("crypto_strategies (Python)", "model_stack (via --only)", "model.decision / fill"),
    ("churn (Python)", "throughput demo", "model.decision / fill"),
    ("backend (Go)", "pump/dump + breakout detectors", "alert.pumpdump + screen/survey"),
    ("recorder (Go)", "the single lake writer", "state/lake3 (not a topic)"),
    ("portfolio_review (Python)", "grades books + stops losers", "review.verdict + disabled.json"),
]


def _engine_roster_table():
    head = [Paragraph("Engine", S["head"]), Paragraph("Role", S["head"]),
            Paragraph("Publishes", S["head"])]
    rows = [head]
    for name, role, pub in ENGINE_ROSTER:
        rows.append([Paragraph("<font face='Courier'>%s</font>" % name, S["cell"]),
                     Paragraph(role, S["cell"]), Paragraph(pub, S["cell"])])
    t = Table(rows, colWidths=[5.4 * cm, 6.0 * cm, 6.8 * cm], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BLACK), ("TEXTCOLOR", (0, 0), (-1, 0), YELLOW),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [ZEBRA, HexColor("#ffffff")]),
        ("GRID", (0, 0), (-1, -1), 0.4, RULE), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, 0), 6), ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
        ("TOPPADDING", (0, 1), (-1, -1), 4), ("BOTTOMPADDING", (0, 1), (-1, -1), 4),
    ]))
    return t


def _engines_chapter():
    out = chapter("Chapter 3 &middot; Engines &mdash; the running processes", 3)
    out.append(Paragraph(
        "An <b>engine</b> is one running process &mdash; a Go binary or a "
        "<font face='Courier'>python3 -u file.py</font>. It owns a lane: the supervisor launches it, "
        "it subscribes to the topics it needs, and it loops. One engine can host several models; "
        "<font face='Courier'>--only</font> narrows it to one. <font face='Courier'>alpha.py</font> runs "
        "three models (funding_carry, xsec_momentum, cascade_rebound), "
        "<font face='Courier'>models10.py</font> runs funding_fade, and "
        "<font face='Courier'>crypto_strategies.py</font> runs model_stack. Some engines are Go "
        "(market_data, tad, forecast, backend, recorder), some are Python (the books, signals, feeds) &mdash; "
        "they interoperate only through the bus and the envelope.", S["body"]))
    out += fig("engine-lifecycle",
               "Figure 3.1 &mdash; an engine's life: launch, subscribe, loop, and the supervisor's "
               "watchdog restart when it dies.")
    out.append(Paragraph(
        "The lifecycle matters because it is <b>stateless across restarts</b>: an engine's book state is "
        "rebuilt from disk, not carried in memory. The supervisor keeps the fleet alive, but it never "
        "decides <i>what</i> trades &mdash; it only decides <i>which engines are allowed to run</i> "
        "(the disable list) and restarts the ones that crash.", S["body"]))

    out += sec_fig("How an engine discovers its topics", "discovery",
               "Figure 3.2 &mdash; topic discovery: the registry is the single source of truth; an engine "
               "declares its needs in code, and the bus refuses anything unregistered or unauthorised.")
    out.append(Paragraph(
        "There is no magic. The <b>topic registry</b> "
        "(<font face='Courier'>core/internal/topics/topics.go</font> in Go, "
        "<font face='Courier'>lib/bus.py</font> in Python) is the one list of every topic, its exchange, "
        "and its allowed producers. An engine 'knows' its topics because they are <b>declared in its code</b>: "
        "it either subscribes to a queue for the topic, or reads the latest via an HTTP proxy "
        "(forecast :8790, tad :8792, signal_bus :8793, master :8799). On publish, the bus re-checks the "
        "registry &mdash; an unregistered topic or an unauthorised producer is refused before it reaches "
        "the wire. That is the discovery process: <i>declare, then validate against the registry.</i>", S["body"]))

    out.append(sec("Python and Go in one fleet"))
    out.append(Paragraph(
        "A book is Python; the market-data feed and the forecasters are Go. They are not 'all in one "
        "engine' &mdash; they are separate processes that meet at the <b>envelope</b>, a language-agnostic "
        "JSON shape "
        "<font face='Courier'>{topic, key, producer, data, version, seq, ts}</font>. A Go producer "
        "(market_data, tad, forecast) marshals its struct to that JSON and publishes it; a Python consumer "
        "receives the body and, if it has no <font face='Courier'>topic</font> field, wraps it into the "
        "same envelope shape (<font face='Courier'>bus.py:_decoding</font>). The language is invisible at "
        "the boundary: <font face='Courier'>models10.py</font> calls the Go forecast over HTTP and reads a "
        "Go-written envelope without ever importing Go. The only contract is the JSON shape and the topic "
        "name &mdash; which is why the two registries must stay in sync.", S["body"]))

    out += sec_fig("What an engine reads &mdash; inputs and their shapes", "engine-inputs",
               "Figure 3.3 &mdash; every input an engine can read, and the one thing it writes: a "
               "decision on the book.")
    out.append(Paragraph(
        "No engine talks to Binance directly except <b>market_data</b>. Everything else reads the bus "
        "topics or the REST/file proxies. The inputs, with their exact shapes, are:", S["body"]))
    out.append(_input_shapes_table())
    out.append(Paragraph(
        "An engine reads only the inputs its models need &mdash; a funding book ignores the OI quadrant, "
        "a momentum book ignores funding. Each input is fetched fresh every cycle, and its age is carried "
        "through the gates so a stale value becomes a refusal, never a silent trade.", S["body"]))

    out.append(sec("K-lines"))
    out.append(Paragraph(
        "A <b>K-line</b> (candlestick) is one bar of OHLCV: <b>o</b>pen, <b>h</b>igh, <b>l</b>ow, "
        "<b>c</b>lose, <b>v</b>olume, stamped with a timestamp. market_data serves them per symbol and "
        "interval &mdash; "
        "<font face='Courier'>/klines?symbol=BTCUSDT&amp;interval=1m&amp;limit=120</font> returns 120 "
        "one-minute candles. Everything derived &mdash; EMA, RSI, ATR, Donchian, the forecast, the "
        "momentum rank &mdash; is computed from these bars. The intervals matter: 1m for fast TA and "
        "liquidation proxies, 15m for the funding_fade rule and the forecast blend, 4h for the BTC regime "
        "drift every alt inherits. On a fresh boot market_data backfills 200 &times; 1m and 200 &times; "
        "15m bars per symbol, so the platform is never history-starved.", S["body"]))

    out += sec_fig("The Engine Scan Loop", "engine-loop",
               "Figure 3.4 &mdash; the engine's loop: read, score, gate, trade, publish, repeat.")
    out.append(Paragraph(
        "The loop is the same shape in every engine: pull fresh inputs, ask each model's rule for a "
        "signal, run the gate chain, and &mdash; only if every gate passes &mdash; open a position and "
        "publish the decision. The gate chain is shared (risk.py, tactics.py), so no engine can invent "
        "its own risk policy.", S["body"]))

    out += sec_fig("The OI quadrant", "oi-quadrant",
               "Figure 3.5 &mdash; the price &times; open-interest quadrant: where price and OI move "
               "together, or diverge, and the climax that demotes both directions.")
    out.append(Paragraph(
        "Open interest is the number of open contracts; it tracks whether money is entering or leaving. "
        "Crossing it with price gives four regimes. <b>Confirmation</b> (OI and price move together) is a "
        "genuine trend &mdash; new money pushing. <b>Divergence</b> (they move apart) is a weak move &mdash; "
        "distribution or short covering. The <b>climax</b> case (an extreme OI move) is exhaustion and "
        "demotes <b>both</b> directions. The engine carries this quadrant on each decision "
        "(<font face='Courier'>oi_quadrant</font>), and the OI gate turns it into a strategy-aware score "
        "multiplier &mdash; momentum prefers confirmation, reversion prefers divergence.", S["body"]))

    out.append(sec("The engine roster"))
    out.append(_engine_roster_table())
    return out


def _input_shapes_table():
    rows = [
        ("market.candle", "klines", "array of {t, o, h, l, c, v} — one bar per row"),
        ("intel.forecast", "REST :8790", "{views:[{agent, side, prob, move_pct}], prob, move_pct, horizon, bar_age_s}"),
        ("intel.ta", "REST :8792 / ta_live.json", "{symbol, signals:[{kind, side}], count}"),
        ("intel.signal", "REST :8793", "{symbol, score, side, flow_conf}"),
        ("intel.flow", "REST :8793", "{symbol, flow_conf} — conviction for sizing"),
        ("features.json (OI)", "file", "{symbol, oi_chg_5m, oi_chg_15m, oi_chg_1h, px_chg_15m, oi_quadrant}"),
        ("alert.pumpdump", "REST :8787", "{symbol, kind(PUMP/DUMP), severity, score, metrics:{burst_z,...}}"),
    ]
    head = [Paragraph("Input", S["head"]), Paragraph("Via", S["head"]),
            Paragraph("Shape", S["head"])]
    data = [head] + [[Paragraph("<font face='Courier'>%s</font>" % r[0], S["cell"]),
                      Paragraph(r[1], S["cell"]), Paragraph("<font face='Courier'>%s</font>" % r[2], S["cell"])]
                     for r in rows]
    t = Table(data, colWidths=[3.2 * cm, 2.6 * cm, 11.5 * cm], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BLACK), ("TEXTCOLOR", (0, 0), (-1, 0), YELLOW),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [ZEBRA, HexColor("#ffffff")]),
        ("GRID", (0, 0), (-1, -1), 0.4, RULE), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, 0), 5), ("BOTTOMPADDING", (0, 0), (-1, 0), 5),
        ("TOPPADDING", (0, 1), (-1, -1), 4), ("BOTTOMPADDING", (0, 1), (-1, -1), 4),
    ]))
    return t


def _models_chapter():
    out = chapter("Chapter 5 &middot; Models &mdash; the rules that score", 5)
    out.append(Paragraph(
        "A <b>model</b> is a pure rule: it takes inputs and returns a <b>side</b> (buy / sell / none) "
        "plus a <b>score</b> (its conviction). It has no memory of its own &mdash; its memory is the "
        "book it writes to. A model never trades directly; it only produces a signal that the gate chain "
        "(Chapter 4) then accepts or rejects.", S["body"]))
    out += fig("model-rule",
               "Figure 5.1 &mdash; a model is a function: inputs in, a scored signal out. Below the "
               "threshold it stays silent.")
    out.append(Paragraph(
        "Every model follows this shape, but the <i>rule inside the box</i> is different for each one. "
        "funding_fade looks at the funding rate, xsec_momentum looks at 24h returns, model_stack looks at "
        "the model votes. What is identical is the output: a scored signal that enters the same gate "
        "chain.", S["body"]))
    out += sec_fig("The models, side by side", "model-inputs",
               "Figure 5.2 &mdash; four models, four different inputs, the same output shape: a scored "
               "signal.")
    out.append(Paragraph(
        "This is the key to understanding the platform: <b>the model is the smallest replaceable part</b>. "
        "Swapping a rule changes only what happens inside one box &mdash; the inputs, the gates, the "
        "sizing and the book are all shared. The owner's best-5 selection chose specific models to keep; "
        "the rest are disabled (A6) and influence nothing. Each description below ends with a "
        "<b>Used by</b> box naming its consumer.", S["body"]))
    out.append(sec("Each model in detail"))
    out.extend(_model_paragraphs())
    return out


def _books_chapter():
    out = chapter("Chapter 6 &middot; Books &mdash; the paper accounts", 6)
    out.append(Paragraph(
        "A <b>book</b> is one model's paper account: its <b>equity</b>, its <b>open positions</b>, and "
        "its <b>closed trades</b>. One book per model. The book is the only thing that remembers &mdash; "
        "the model re-scores every cycle, but the book accumulates the realised PnL that the leaderboard "
        "and the solver measure.", S["body"]))
    out += fig("book-anatomy",
               "Figure 6.1 &mdash; a book's anatomy: equity, positions, trades, and the capital "
               "allocation that decides how much it may size.")
    out.append(Paragraph(
        "Equity moves two ways: an open position is <b>marked to market</b> each cycle (equity drifts "
        "with the unrealised PnL), and a closed position realises PnL into equity permanently. "
        "<font face='Courier'>return_pct = equity / start_equity &minus; 1</font> is the number the "
        "leaderboard shows, and <font face='Courier'>capital.json</font> stores each book's "
        "<font face='Courier'>risk_mult</font> &mdash; a promoted book may size up, a demoted one may "
        "only size down, and a disabled one gets zero allocation (A6).", S["body"]))
    out += sec_fig("From signal to an open position", "signal-to-trade",
               "Figure 6.2 &mdash; the sequence: risk, then a maker quote, then the book opens, and the "
               "decision/fill are published with linked ids.")
    out.append(Paragraph(
        "Notice the ordering: <b>risk before size, quote before open</b>. risk.allow refuses an oversized "
        "or stale entry; maker.quote returns a limit price that joins the book instead of crossing it; "
        "then the book opens and publishes model.decision + model.fill with linked decision_id / "
        "fill_id so the lake can thread the whole life of the position.", S["body"]))
    out += sec_fig("The life of one position", "position-lifecycle",
               "Figure 6.3 &mdash; a position is opened once, marked every cycle, and closed by one of "
               "four triggers; the realised PnL lands in equity and the trades list.")
    out.append(sec("How size is decided"))
    out.append(Paragraph(
        "Three multipliers stack, in order. First the model's <b>risk budget</b>: "
        "<font face='Courier'>risk_pct</font> of equity, capped at 8% (models10) or 3&times; (alpha), "
        "turned into a notional by the stop distance. Then <b>conviction sizing</b>: "
        "<font face='Courier'>risk_for(base, conf) = base &times; (0.6 + 0.2 &times; min(conf, 4))</font> "
        "&mdash; more confirmations, more capital. Then the global <b>size_mult</b> (now 3.0) scales the "
        "final notional. Shorts on models10 carry half the size (the unbounded-tail rule).", S["body"]))
    return out


def _signal_chapter():
    out = chapter("Chapter 7 &middot; A signal becomes a trade, end to end", 7)
    out.append(Paragraph(
        "Everything in one sequence. Read top to bottom: market_data feeds the whole platform; tad, "
        "forecast and feeds each derive one input; the model scores; risk admits; the book opens; and "
        "the decision + fill are published back onto the bus for the lake and the dashboard.", S["body"]))
    out += fig("full-flow", "Figure 7.1 &mdash; the full signal-to-trade flow, one symbol.")
    out.append(sec("A worked example, in pseudo-code"))
    code = Paragraph(
        "# 1. inputs arrive on the bus (BTCUSDT)<br/>"
        "klines  = market_data.klines(\"BTCUSDT\", \"1m\")        # 120 bars<br/>"
        "ta      = tad.signals(\"BTCUSDT\")                      # EMA/RSI/Donchian votes<br/>"
        "fc      = forecast.blend(\"BTCUSDT\", \"buy\")           # prob 0.61, move 0.4%<br/>"
        "oi      = feeds.quadrant(\"BTCUSDT\")                   # \"confirmation\"<br/>"
        "<br/>"
        "# 2. the model scores<br/>"
        "side, score = funding_fade(\"BTCUSDT\")                 # fr=+0.10%  -> (\"sell\", 0.8)<br/>"
        "<br/>"
        "# 3. the gates run<br/>"
        "if fc.prob &lt; 0.45: skip                              # 0.61 passes<br/>"
        "score *= oi_gate.adjust(score, oi, side)               # confirmation boosts the move<br/>"
        "ok, why = risk.allow(\"BTCUSDT\", side, notional, equity, fc.move_pct)<br/>"
        "if not ok: skip                                        # exposure/staleness gate<br/>"
        "<br/>"
        "# 4. the trade opens<br/>"
        "px, prob = maker.quote(\"BTCUSDT\", side, notional)      # join the bid<br/>"
        "book.open(\"BTCUSDT\", side, px, notional)              # position live<br/>"
        "emit.decision + emit.fill                              # linked ids -> lake", CODE_STYLE)
    out.append(code)
    return out


def _supervisors_chapter():
    out = chapter("Chapter 2 &middot; Supervisors &amp; lanes", 2)
    out.append(Paragraph(
        "The <b>supervisor</b> (<font face='Courier'>supervisor.py</font>) is the platform's shepherd, "
        "not its trader. It never decides <i>what</i> to trade &mdash; it decides <i>which engines are "
        "allowed to run</i> and keeps them alive. A <b>lane</b> is one engine's slot in the fleet: the "
        "process it runs in, the code it owns, and the inputs it is allowed to read. The relationships "
        "are fixed and simple: <b>an engine is 1-to-1 with a lane</b>, and <b>an engine is 1-to-many "
        "with models</b> (each model is one book).", S["body"]))
    out += fig("supervisor-lanes",
               "Figure 2.1 &mdash; the supervisor allocates lanes; each lane is one engine (1:1); each "
               "engine hosts many models (1:many), one book per model.")
    out.append(Paragraph(
        "What the supervisor actually does, in order: it reads the <b>disable list</b> "
        "(<font face='Courier'>state/disabled.json</font>) and refuses to launch any disabled engine "
        "(A6 &mdash; a re-enable is a human act); it launches each enabled engine as its own process; "
        "it <b>watches</b> them (a crash or a hang is restarted by the watchdog); and it grades each "
        "book (trades, return, streak) for the leaderboard. Because the disable check happens again "
        "inside <font face='Courier'>start()</font>, a relaunch cannot silently revive a disabled "
        "book &mdash; that hole was closed after the best-5 enforcement.", S["body"]))
    out.append(Paragraph(
        "The 1:1 / 1:many split is the whole point of the layering. A lane is the unit of <b>lifecycle</b> "
        "(launch, disable, restart); a model is the unit of <b>logic</b> (one rule); a book is the unit of "
        "<b>state</b> (equity, positions, trades). Changing a model's rule never touches the lane; killing "
        "a lane never touches another engine's models.", S["body"]))
    return out


def _gates_chapter():
    out = chapter("Chapter 4 &middot; Gates &mdash; from rule to allowed trade", 4)
    out.append(Paragraph(
        "A signal is not a trade. Between the rule's output and an open position sits the <b>gate "
        "chain</b> &mdash; a fixed sequence of checks that every signal must survive, in order. A "
        "<b>no</b> at any gate drops the signal for that cycle; nothing is retried in a loop. This is "
        "the one place the platform refuses risk, and it is shared by every book (risk.py, tactics.py), "
        "so no model can invent its own risk policy.", S["body"]))
    out += fig("gates",
               "Figure 4.1 &mdash; the gate chain. Each gate is a veto; a no sends the model back to the "
               "next scan cycle.")
    out.append(sec("The rule"))
    out.append(Paragraph(
        "The <b>rule</b> is the pure function at the head of the chain: it reads the inputs and returns "
        "a <b>side</b> (<font face='Courier'>buy</font> / <font face='Courier'>sell</font> / "
        "<font face='Courier'>none</font>) plus a <b>score</b> (its conviction). It has no memory and no "
        "opinion about risk &mdash; risk is the gates' job. The rule only answers one question: "
        "<i>given these inputs, which way, and how strongly?</i>", S["body"]))
    out.append(sec("How a BUY or SELL is decided, with worked examples"))
    out.append(Paragraph(
        "The decision is the rule's answer surviving the gates. Three worked examples:", S["body"]))
    out.append(Paragraph(
        "<b>Example 1 &mdash; funding_fade decides SELL.</b> The rule reads "
        "<font face='Courier'>lastFundingRate</font>. The crowd is paying heavily to stay long, so the "
        "reversion is short. fr = +0.10% &rarr; above +0.08% &rarr; rule returns "
        "<font face='Courier'>(\"sell\", 0.8)</font>. Then the gates: the forecast must not say "
        "strongly buy (prob &ge; 0.45 in the sell direction), the move must clear the fee, the OI "
        "quadrant must not be a fresh long-accumulation, and risk.allow must admit the size. If all pass, "
        "the book opens a short.", S["body"]))
    out.append(Paragraph(
        "<b>Example 2 &mdash; xsec_momentum decides BUY.</b> The rule ranks symbols by 24h return and "
        "longs the strongest, shorts the weakest (market-neutral). For a name in the strongest bucket it "
        "returns <font face='Courier'>(\"buy\", rank_score)</font>. The forecast must agree (prob &ge; "
        "0.45 for buy), and because it is a momentum long, the OI gate <i>boosts</i> a confirmation "
        "quadrant (OI up + price up) and <i>demotes</i> a divergence. If the gates pass, the book buys.", S["body"]))
    out.append(Paragraph(
        "<b>Example 3 &mdash; a signal the gates REJECT.</b> funding_fade returns "
        "<font face='Courier'>(\"sell\", 0.9)</font>, but the forecast returns prob 0.55 <i>for buy</i> "
        "&mdash; the forecast gate vetoes the short (the blend disagrees). No trade. The model re-scores "
        "next cycle. This is the whole point: a strong rule answer is overruled by a disagreeing "
        "forecast, because the forecast is the primary weight.", S["body"]))
    out.append(sec("The forecast, in detail"))
    out.append(Paragraph(
        "The forecast is the primary gate. Seven agents each look at the same 1m/15m candle series from "
        "a different angle &mdash; <b>momentum</b> (EMA slope + recent return), <b>volume</b>, "
        "<b>volatility</b>, <b>reversion</b>, <b>moving-average</b>, <b>breakout</b>, and "
        "<b>pressure</b> &mdash; and each returns a probability (a sigmoid of its raw score) plus an "
        "expected move. A supervisor blends them, weighting each agent by its historical hit rate "
        "(<font face='Courier'>w = 0.5 + accuracy</font>, so 0.5&ndash;1.5), into one "
        "<font face='Courier'>prob</font> and one <font face='Courier'>move_pct</font>.", S["body"]))
    out.append(Paragraph(
        "<b>Worked blend.</b> Seven agents return probabilities 0.62, 0.58, 0.50, 0.66, 0.55, 0.60, "
        "0.49. With equal weights the blend is &asymp; 0.57 &mdash; above 0.45, so the forecast admits. "
        "But if momentum and breakout (the two with the best hit rate) are the 0.62 and 0.66, their "
        "higher weights pull the blend toward 0.60, strengthening the admission. The action is labelled "
        "from the blend: <font face='Courier'>enter</font> when prob &ge; 0.58 and move &ge; 0.25%, "
        "<font face='Courier'>liquidate</font> when prob &le; 0.42, otherwise <font face='Courier'>"
        "hold</font>. Below MIN_BARS=50 bars the forecaster refuses to answer at all.", S["body"]))
    return out


def doc():
    story = []

    # ---- cover page (artistic: brand + hand-drawn radial centrepiece) ----
    story.append(Spacer(1, 0.6 * cm))
    story.append(Paragraph("CRYPTALYST", COVER_TITLE))
    story.append(Spacer(1, 0.3 * cm))
    story.append(_radial_drawing())
    story.append(Spacer(1, 0.4 * cm))
    story.append(Paragraph("Supervisor &middot; Lanes &middot; Engines &middot; Models &middot; "
                           "Messaging &middot; Data Event Types", COVER_EDITION))
    # push the credits to the bottom of the cover, in bold and larger
    story.append(Spacer(1, 1.4 * cm))
    story.append(Paragraph(
        "Platform Version 2.0 &nbsp;&middot;&nbsp; Document Revision 2<br/>"
        "Designed by Andrew Preston &nbsp;&middot;&nbsp; &copy; TekVolt Systems 2026",
        ParagraphStyle("credits", fontName="Helvetica-Bold", fontSize=12,
                       leading=17, textColor=INK, alignment=1)))
    story.append(PageBreak())

    # ---- front matter: about + contents ----
    story.append(_cover_meta_table())
    story.append(Spacer(1, 0.5 * cm))
    story.append(callout(
        "<b>Reading order.</b> Chapter 1 defines the engine/model/book layers and the decision "
        "weighting; Chapter 2 the supervisors and lanes; Chapter 3 the engines; Chapter 4 the gate "
        "chain and how a BUY or SELL is decided; Chapter 5 the models; Chapter 6 the books; Chapter 7 "
        "a worked example of a signal becoming a trade &mdash; each with Mermaid flow diagrams. "
        "Chapter 8 is the bus reference (architecture, registry, producers, subscribers); "
        "Chapter 9 lists the known gaps."))
    story.append(Spacer(1, 0.6 * cm))
    story.append(Paragraph("Contents", S["h2"]))
    story.append(TableOfContents())

    # ---- body ----
    # Chapter 1 — Overview
    story.extend(chapter("Chapter 1 &middot; Overview &mdash; engines, models and books", 1))
    story.extend(_decision_section())

    # Chapter 2 — Supervisors & lanes
    story.extend(_supervisors_chapter())

    # Chapter 3 — Engines
    story.extend(_engines_chapter())

    # Chapter 4 — Gates
    story.extend(_gates_chapter())

    # Chapter 5 — Models
    story.extend(_models_chapter())

    # Chapter 6 — Books
    story.extend(_books_chapter())

    # Chapter 7 — Signal becomes a trade
    story.extend(_signal_chapter())

    # Chapter 8 — Message bus
    story.extend(chapter("Chapter 8 &middot; The message bus", 8))
    story.append(sec("Bus architecture &mdash; how many of each"))
    story.append(_arch_table())
    story.append(Spacer(1, 6))
    story.append(sec("Registry &mdash; the 22 topics"))
    story.append(_reg_table())
    story.append(Spacer(1, 6))
    story.append(sec("Producer contract &mdash; what each model sends and where it is inserted"))
    story.append(Paragraph(
        "Wire format: Python producers pass the registry gate (topic registered AND producer in its "
        "allow-list), then wrap the payload in an envelope "
        "<font face='Courier'>{topic, key, producer, data, version:1, seq, ts}</font> and SEND it to "
        "<font face='Courier'>/exchange/&lt;exchange&gt;/&lt;topic&gt;</font> on RabbitMQ :61613 "
        "(lib/bus.py:101-126,176-198). Go producers send the raw payload body (marketdata adds "
        "<font face='Courier'>v, plane, topic, producer, key, gen, seq, cid, computed_at</font> headers, "
        "stomp.go:183-204); Python consumers wrap it on receipt when the body has no topic field "
        "(bus.py:307-325). The exchange fans each message out to every bound queue — the recorder's "
        "durable mirror, bus_consumer's projection, and each live subscriber's private queue.", S["body"]))
    story.append(_contract_table())
    story.append(Spacer(1, 6))
    story.extend(_flow_section())
    story.append(Spacer(1, 6))
    story.append(sec("Subscriber breakdown &mdash; every topic, every subscriber"))
    story.append(Paragraph(
        "<b>recorder (:8801)</b> and <b>bus_consumer (:8798)</b> subscribe to every topic; "
        "the breakdown lists them per topic plus each topic-specific consumer with its usage "
        "and code location.", S["body"]))
    story.append(_breakdown_table())
    story.append(Spacer(1, 6))

    # Chapter 7 — Known gaps
    story.extend(chapter("Chapter 9 &middot; Known gaps &mdash; models not listening", 9))
    story.append(Paragraph(
        "These models read a <b>HTTP/file proxy</b> instead of the bus topic, or the topic "
        "has <b>no consumer</b>. The bus carries the topic; the model never subscribes to it.", S["body"]))
    story.append(_not_listening_table())

    return story


def build():
    dd = TopicsDoc(OUT, pagesize=A4,
                   leftMargin=0.8 * inch, rightMargin=0.8 * inch,
                   topMargin=0.75 * inch, bottomMargin=0.75 * inch,
                   title="CryptAlyst — Supervisor, Lanes, Engines, Models, Messaging, Data Event Types",
                   author="CryptAlyst")
    dd.multiBuild(doc(), onFirstPage=_page_footer, onLaterPages=_page_footer)
    print("wrote", OUT, os.path.getsize(OUT), "bytes")


if __name__ == "__main__":
    build()
