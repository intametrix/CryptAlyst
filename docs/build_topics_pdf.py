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
from reportlab.lib.units import cm
from reportlab.platypus import (
    BaseDocTemplate, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)
from reportlab.platypus.tableofcontents import TableOfContents

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "topics-pubsub.pdf")

INK = HexColor("#1a2733")
ACCENT = HexColor("#274866")
RULE = HexColor("#8a97a5")
BODY = HexColor("#2b2b2b")
GRAY = HexColor("#5c6b7a")
ZEBRA = HexColor("#f2f5f8")
BLACK = HexColor("#000000")
YELLOW = HexColor("#ffd400")

S = {
    "title": ParagraphStyle("title", fontName="Times-Bold", fontSize=20,
                            leading=24, textColor=INK, spaceAfter=2),
    "subtitle": ParagraphStyle("subtitle", fontName="Times-Italic", fontSize=10.5,
                               leading=13, textColor=GRAY, spaceAfter=10),
    "h2": ParagraphStyle("h2", fontName="Times-Bold", fontSize=13,
                         leading=16, textColor=ACCENT, spaceBefore=12, spaceAfter=4),
    "body": ParagraphStyle("body", fontName="Times-Roman", fontSize=10,
                           leading=13, textColor=BODY, spaceAfter=4),
    "small": ParagraphStyle("small", fontName="Times-Roman", fontSize=8.5,
                            leading=11, textColor=GRAY),
    "cell": ParagraphStyle("cell", fontName="Times-Roman", fontSize=9,
                           leading=11, textColor=BODY),
    "cellb": ParagraphStyle("cellb", fontName="Times-Bold", fontSize=9,
                            leading=11, textColor=INK),
    "head": ParagraphStyle("head", fontName="Times-Bold", fontSize=10,
                           leading=12, textColor=YELLOW),
    "mono": ParagraphStyle("mono", fontName="Courier", fontSize=8.5,
                           leading=10.5, textColor=BODY),
}

# cover / intro / TOC styles
COVER_TITLE = ParagraphStyle("covertitle", fontName="Times-Bold", fontSize=30,
                             leading=36, textColor=INK, spaceAfter=10)
COVER_SUB = ParagraphStyle("coversub", fontName="Times-Italic", fontSize=12,
                           leading=16, textColor=GRAY, spaceAfter=16)
COVER_BODY = ParagraphStyle("coverbody", fontName="Times-Roman", fontSize=11,
                            leading=16, textColor=BODY, spaceAfter=8)
TOC_STYLE = ParagraphStyle("toc", fontName="Times-Roman", fontSize=10.5,
                           leading=17, textColor=BODY)


class TopicsDoc(SimpleDocTemplate):
    """Multi-pass template: collects headings into a table of contents and
    draws page numbers on every page except the cover (page 1)."""

    def afterFlowable(self, flowable):
        if isinstance(flowable, Paragraph) and flowable.style.name == "h2":
            self.notify("TOCEntry", (0, flowable.getPlainText(), self.page))


def _page_footer(canvas, doc):
    page = canvas.getPageNumber()
    if page <= 1:
        return  # no page number on the cover
    canvas.saveState()
    canvas.setFont("Times-Roman", 9)
    canvas.setFillColor(GRAY)
    canvas.drawCentredString(A4[0] / 2.0, 1.0 * cm, str(page - 1))
    canvas.restoreState()


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


def _model_paragraphs():
    out = []
    for name, how, used in MODELS_DETAIL:
        out.append(Paragraph("<b>%s</b>" % name, ParagraphStyle(
            "mname", fontName="Times-Bold", fontSize=11, leading=13.5, textColor=ACCENT,
            spaceBefore=6, spaceAfter=2)))
        out.append(Paragraph("How it works — %s" % how, S["body"]))
        out.append(Paragraph("<b>Best used for</b> — %s" % used, S["body"]))
        out.append(Spacer(1, 2))
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
    out.append(Paragraph("Message flow — how a model's output travels", S["h2"]))
    out.append(Paragraph(
        "A model (book) computes a result and publishes it to <b>one topic</b>. The bus wraps "
        "it in an <b>envelope</b> and routes it by exchange + routing key. Every consumer "
        "receives the envelope and reads <b>meta.key</b> (the symbol) to act on the right "
        "instrument — routing is by key, never by broadcast.", S["body"]))
    out.append(_envelope_table())
    out.append(Spacer(1, 4))
    out.append(Paragraph("Pseudo-code", S["h2"]))
    code = Paragraph(CODE.replace("\n", "<br/>"), ParagraphStyle(
        "code", fontName="Courier", fontSize=7.5, leading=9.5, textColor=BODY,
        backColor=HexColor("#f7f9fb"), borderPadding=5, borderColor=RULE, borderWidth=0.4))
    out.append(code)
    return out


def _decision_section():
    out = []
    out.append(Paragraph("Engines, models and books — and how a number becomes a decision", S["h2"]))
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


def doc():
    story = []

    # ---- cover page (no page number) ----
    story.append(Spacer(1, 4 * cm))
    story.append(Paragraph("CryptAlyst", COVER_TITLE))
    story.append(Paragraph("Message-Bus Topics — Publisher &amp; Subscriber Reference", COVER_SUB))
    story.append(Spacer(1, 1 * cm))
    story.append(Paragraph(
        "CryptAlyst is a paper-trading crypto platform: a Go market-data feed, a RabbitMQ "
        "message bus, and a fleet of Python engines that compute signals, weigh them, and trade "
        "paper books. This document is the reference for the bus — the one broker, its eight "
        "exchanges and twenty-two topics, who publishes and who subscribes, how a model's "
        "numbers become a weighted decision, and the places where the wiring is still wrong.", COVER_BODY))
    story.append(Paragraph(
        "How to read it: the first section defines the engine/model/book layers and the decision "
        "weighting; then the architecture counts; then the registry; then each model; then the "
        "message flow and the per-subscriber breakdown; finally the known gaps.", COVER_BODY))
    story.append(PageBreak())

    # ---- table of contents ----
    story.append(Paragraph("Contents", S["h2"]))
    story.append(TableOfContents())
    story.append(PageBreak())

    # ---- body ----
    story.extend(_decision_section())
    story.append(Paragraph("Bus architecture — how many of each", S["h2"]))
    story.append(_arch_table())
    story.append(Spacer(1, 6))
    story.append(Paragraph("Registry", S["h2"]))
    story.append(_reg_table())
    story.append(Spacer(1, 6))

    story.append(Paragraph("Per-model descriptions — what each one does", S["h2"]))
    story.extend(_model_paragraphs())
    story.append(Spacer(1, 6))

    story.append(Paragraph("Producer contract — what each model sends and where it is inserted", S["h2"]))
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

    story.append(Paragraph("Subscriber breakdown", S["h2"]))
    story.append(Paragraph(
        "<b>recorder (:8801)</b> and <b>bus_consumer (:8798)</b> subscribe to every topic; "
        "the breakdown lists them per topic plus each topic-specific consumer with its usage "
        "and code location.", S["body"]))
    story.append(_breakdown_table())
    story.append(Spacer(1, 6))

    story.append(Paragraph("Models not receiving the topics they should listen for", S["h2"]))
    story.append(Paragraph(
        "These models read a <b>HTTP/file proxy</b> instead of the bus topic, or the topic "
        "has <b>no consumer</b>. The bus carries the topic; the model never subscribes to it.", S["body"]))
    story.append(_not_listening_table())

    return story


def build():
    dd = TopicsDoc(OUT, pagesize=A4,
                   leftMargin=1.4 * cm, rightMargin=1.4 * cm,
                   topMargin=1.4 * cm, bottomMargin=1.6 * cm,
                   title="CryptAlyst Message-Bus Topics",
                   author="CryptAlyst")
    dd.multiBuild(doc(), onFirstPage=_page_footer, onLaterPages=_page_footer)
    print("wrote", OUT, os.path.getsize(OUT), "bytes")


if __name__ == "__main__":
    build()
