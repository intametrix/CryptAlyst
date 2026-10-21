#!/usr/bin/env python3
"""Build the printable PDF of docs/master-plan-back-analysis.md.

The Markdown file is the canonical source; this script renders its content
with print layout (A4, three-line tables, page numbers). Re-run after
editing the plan: python3 docs/build_master_plan_pdf.py
"""
import os

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether,
)

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "master-plan-back-analysis.pdf")

INK = HexColor("#1a2733")
ACCENT = HexColor("#274866")
RULE = HexColor("#8a97a5")
BODY = HexColor("#2b2b2b")
GRAY = HexColor("#666666")

S = {
    "title": ParagraphStyle("title", fontName="Times-Bold", fontSize=21,
                            leading=25, textColor=INK, spaceAfter=4),
    "subtitle": ParagraphStyle("subtitle", fontName="Times-Italic", fontSize=11.5,
                               leading=15, textColor=GRAY, spaceAfter=2),
    "h1": ParagraphStyle("h1", fontName="Times-Bold", fontSize=14, leading=17,
                         textColor=ACCENT, spaceBefore=16, spaceAfter=6,
                         keepWithNext=1),
    "h2": ParagraphStyle("h2", fontName="Times-Bold", fontSize=11.5, leading=14,
                         textColor=INK, spaceBefore=10, spaceAfter=4,
                         keepWithNext=1),
    "body": ParagraphStyle("body", fontName="Times-Roman", fontSize=10.5,
                           leading=14.5, textColor=BODY, spaceAfter=5),
    "bullet": ParagraphStyle("bullet", fontName="Times-Roman", fontSize=10.5,
                             leading=14.5, textColor=BODY, spaceAfter=3,
                             leftIndent=14, bulletIndent=4),
    "cell": ParagraphStyle("cell", fontName="Times-Roman", fontSize=9.5,
                           leading=12.5, textColor=BODY),
    "cellb": ParagraphStyle("cellb", fontName="Times-Bold", fontSize=9.5,
                            leading=12.5, textColor=INK),
    "note": ParagraphStyle("note", fontName="Times-Italic", fontSize=9.5,
                           leading=13, textColor=GRAY, spaceBefore=8),
}


def header_footer(canvas, doc):
    canvas.saveState()
    w, h = A4
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(GRAY)
    canvas.drawString(2.2 * cm, h - 1.4 * cm,
                      "Master Plan - Back-Analysis of the Models That Hurt the P&L")
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.5)
    canvas.line(2.2 * cm, h - 1.6 * cm, w - 2.2 * cm, h - 1.6 * cm)
    canvas.drawCentredString(w / 2, 1.3 * cm, f"Page {doc.page}")
    canvas.restoreState()


def three_line(data, widths, header=True):
    t = Table(data, colWidths=widths, repeatRows=1 if header else 0)
    style = [
        ("LINEABOVE", (0, 0), (-1, 0), 1.2, INK),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, INK),
        ("LINEBELOW", (0, -1), (-1, -1), 1.2, INK),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
    ]
    t.setStyle(TableStyle(style))
    return t


story = []

story.append(Paragraph("Master Plan", S["title"]))
story.append(Paragraph("Back-Analysis of the Models That Hurt the P&amp;L", S["title"]))
story.append(Spacer(1, 6))
story.append(Paragraph(
    "Stage 0 complete - Stages 1-3 planned, awaiting team agreement (Rule 1) - "
    "Commissioned by the human - 2026-10-21", S["subtitle"]))
story.append(Paragraph(
    "Sources of truth: DATA_STORAGE.md (data census) - docs/postmortems/ (Stage 0 reports) - "
    "AGENTS.md Rule A6 (model governance) - JOURNAL.md Entries 9-11 (reasoning)", S["subtitle"]))
story.append(Spacer(1, 10))

story.append(Paragraph("1. Mandate", S["h1"]))
story.append(Paragraph(
    "When a model is disabled it must never influence a trade unless a human re-enables it, and "
    "every disable gets a post-mortem on the data that went in and why it failed. This plan is the "
    "systematic version of that rule, applied retroactively: a full back-analysis of every model "
    "that hurt the P&amp;L, using the exact data each model consumed, producing per-model reports "
    "and tested fix proposals - with the GPU and the ATLAS agent leveraged where they genuinely help.", S["body"]))

story.append(Paragraph("2. What Stage 0 found (the autopsy, done)", S["h1"]))
story.append(Paragraph(
    "Nine per-model reports in docs/postmortems/, re-derivable from the append-only ledgers by "
    "running <font face='Courier'>python3 tools/pnl_autopsy.py</font>. The three findings that matter:", S["body"]))
story.append(Paragraph(
    "<b>Every sentinel-monitored model is underwater, and the guard is not guarding.</b> "
    "skim -19.96 bps/trip over 3,591 trips; scalp_fast -19.92 bps over 3,898; swing_turtle "
    "-$2,001 (-40% of $5,000 paper equity); multi_models -$13,353 (-267%); stats_models "
    "-$4,638 (-93%). Yet the sentinel still reports swing, multimodels and statsmodels all "
    "<b>live</b>, no reason recorded. Why it never parks them is itself a Stage-1 investigation. "
    "Per Rule A6, whether they stay live is the human's call.", S["bullet"], bulletText="1."))
story.append(Paragraph(
    "<b>skim and scalp_fast are fee-machines, not alpha machines.</b> Mean gross +0.04 bps against "
    "20 bps round-trip fees - 529x cost-to-capture - with a 2,023-loss streak on skim and "
    "time_cap exits dominating. Only trading less, holding longer, or a minimum-edge threshold at "
    "entry can rescue that.", S["bullet"], bulletText="2."))
story.append(Paragraph(
    "<b>The fills engines mark their own homework.</b> grid/hft/movers exit on their own terms "
    "(profit_3s) and report ~100% win rates - scratch marks, flagged as upper bounds in every "
    "report. The trustworthy loss surface is the model ledgers.", S["bullet"], bulletText="3."))
story.append(Paragraph(
    "Discipline note recorded in every report: when the whole book bleeds, every component bleeds - "
    "vote-attribution rankings are a search order for the Stage-1 counterfactuals, not verdicts.", S["body"]))

story.append(Paragraph("3. The data estate (what the analysis runs on)", S["h1"]))
story.append(Paragraph(
    "No database; everything is append-only JSONL (census in DATA_STORAGE.md):", S["body"]))
for b in [
    "<b>scalp5/ledger/&lt;model&gt;.jsonl</b> - per-round-trip records: entry/exit, gross/net, fees, "
    "exit reason, and for the decision models the per-sub-model votes (multi_models, stats_models). "
    "This is the data that went in.",
    "<b>scalp5/state/fills.jsonl</b> - 12,503 engine fills with per-fill entry features (upper-bound marks).",
    "<b>scalp5/ledger/backend.jsonl</b> - 809,627 prediction records with full feature snapshots.",
    "<b>scalp5/state/sentinel.json</b> + archives - park/promote history with metrics.",
    "<b>backend/turtle_sessions.jsonl</b> - 4,310 Turtle session records (rejects included), now under the "
    "-data-dir anchoring; backend/ledger.jsonl and calibration.json currently empty (uncalibrated).",
]:
    story.append(Paragraph(b, S["bullet"], bulletText="-"))

story.append(Paragraph("4. The program", S["h1"]))
story.append(Paragraph("Stage 0 - P&amp;L autopsy (complete)", S["h2"]))
story.append(Paragraph(
    "Per-model equity curves, drawdowns, expectancy, exit-reason and loss clustering, fee-vs-capture "
    "math, vote attribution, ranked hypotheses. Deliverables shipped: 9 reports + index + tool.", S["body"]))
story.append(Paragraph("Stage 1 - Replay and counterfactuals (CPU; next)", S["h2"]))
story.append(Paragraph(
    "Rebuild each losing trade's window from the ledgers; re-run exit logic under perturbed parameters; "
    "walk-forward splits. Every Stage-0 hypothesis names its perturbation: stop/target asymmetry sweeps; "
    "minimum-edge threshold at entry (expected move &gt; k x fees); sub-model vetoes; session filters; "
    "symbol caps. <b>Deliverable:</b> backtest report with fixes ranked by expectancy delta, confidence "
    "interval and sample size; every result re-runnable from a committed command. <b>Also:</b> diagnose "
    "why the sentinel never parks models that have lost their equity.", S["body"]))
story.append(Paragraph("Stage 2 - GPU-accelerated sweeps via ATLAS (conditional on a timing experiment)", S["h2"]))
story.append(Paragraph(
    "Vectorize the Stage-1 replay in array semantics (JAX), then grid thousands of parameter combinations "
    "per model with walk-forward folds. Gated by the Rule-5 experiment: time one sweep window on "
    "device=cpu vs device=gpu; the ratio decides ATLAS's role (~3x stays local, ~30x ATLAS owns sweeps).", S["body"]))
story.append(Paragraph("Stage 3 - Learned regime filters (only if the evidence demands it)", S["h2"]))
story.append(Paragraph(
    "If Stage-1 counterfactuals show structure thresholds cannot express: a gradient-boosted model on CPU "
    "first (the dataset is small), a GPU neural net only as the last resort. The Go cmd/learner "
    "champion/challenger machinery and the empty calibration.json slot are the landing point - and under "
    "Rule A6 promotion is always a human act.", S["body"]))

story.append(Paragraph("5. GPU &amp; ATLAS - the architecture", S["h1"]))
story.append(Paragraph(
    "<b>The GPU is the fast path; the CPU is the audit path.</b> Every GPU result must be re-runnable on "
    "CPU at reduced scale. ATLAS is a job runner behind a contract: the repo hands it a job (data arrays + "
    "parameter grid + exit-rule spec version + seed); it returns per-combo metrics plus provenance (git "
    "SHA, library versions, device). GPU outputs are proposals, never promotions (Rule A6).", S["body"]))
story.append(Paragraph("The five rules, each with its exception named (Rule 5):", S["body"]))
for b in [
    "<b>GPU fast / CPU audit.</b> <i>Exception:</i> floating-point non-associativity - equity accumulates "
    "in fp64 on both devices; agreement is within documented tolerance, not bit-equality.",
    "<b>Replay must reproduce the recorded ledger exactly before it sweeps anything.</b> <i>Exception:</i> "
    "entry-time indicators (e.g. RSI) replay from the recorded snapshot, not re-derived.",
    "<b>Branch-heavy exits (ratchets, trailing stops) break naive vectorization.</b> <i>Exception named up "
    "front:</i> masked segment logic or per-trade batching - built only if the timing experiment says the "
    "simple form is the bottleneck.",
    "<b>No result counts without provenance</b> (seed, spec version, device, library versions).",
    "<b>ATLAS relates to this repo like DeepSeek does:</b> files in, files out, channel for questions.",
]:
    story.append(Paragraph(b, S["bullet"], bulletText="-"))
story.append(Paragraph(
    "<b>Why JAX and not CUDA-shaped code:</b> identical code runs on CPU and GPU - development and the "
    "audit path use device=cpu, sweeps flip to device=gpu. Build the GPU agent collapses into write the "
    "array semantics, then flip a flag.", S["body"]))
story.append(Paragraph(
    "<b>Honest sizing:</b> the feature matrix (800k x ~20 float32 ~ 64 MB) is small; the GPU's value is "
    "throughput on sweeps and validation repeats, not capacity. If Stage 2's timing experiment disappoints, "
    "the plan stays CPU-local and nothing else changes.", S["body"]))

story.append(Paragraph("6. Governance", S["h1"]))
for b in [
    "<b>Rule A6:</b> a disabled model influences nothing; re-enabling is a human act, on the channel; no "
    "post-mortem, no disable; disables survive restarts and are audited.",
    "<b>Rule A1:</b> no feature is ever removed; fixes land alongside, behind flags, as proposals.",
    "<b>Lanes:</b> DeepSeek owns Go/model semantics and the math review; KIMI owns the harness, reports, "
    "and the repo; ATLAS owns GPU compute behind the contract; the human owns every promotion.",
    "<b>Rule 1:</b> Stages 1-3 build only after the team answers on the channel - silence is not agreement.",
]:
    story.append(Paragraph(b, S["bullet"], bulletText="-"))

story.append(Paragraph("7. Acceptance criteria", S["h1"]))
acc = [
    [Paragraph("stage", S["cellb"]), Paragraph("acceptance", S["cellb"])],
    [Paragraph("1", S["cell"]), Paragraph(
        "Vectorized replay reproduces each model's recorded trades exactly (diffs explained); every "
        "proposed fix carries expectancy delta, CI, sample size, and a committed re-run command; sentinel "
        "non-parking diagnosed with evidence.", S["cell"])],
    [Paragraph("2", S["cell"]), Paragraph(
        "Timing experiment recorded (numpy vs JAX cpu vs gpu on one window); sweep results reproducible on "
        "CPU within tolerance; provenance attached to every row.", S["cell"])],
    [Paragraph("3", S["cell"]), Paragraph(
        "A learned model is justified only by Stage-1 evidence of non-linear structure; it enters as "
        "champion/challenger, never as an auto-promotion.", S["cell"])],
]
story.append(three_line(acc, [1.6 * cm, 14.2 * cm]))

story.append(Paragraph("8. Immediate next actions", S["h1"]))
for b in [
    "<b>tools/export_npz.py</b> - parse the ledgers once into typed arrays (never re-parse JSONL).",
    "Vectorized NumPy replay of <b>skim</b> (simple exits, fee-dominated, huge n); acceptance = bit-match "
    "against its ledger.",
    "JAX port + the timing experiment (Rule 5) - decides ATLAS's role.",
    "Stage-1 counterfactual battery on the losing clusters, per the hypotheses in docs/postmortems/.",
    "<b>Human decision point:</b> the five bleeding models - stay live, park, or trial - each with its "
    "Stage-0 report in hand.",
]:
    story.append(Paragraph(b, S["bullet"], bulletText="-"))

story.append(Paragraph(
    "Everything in this plan re-derives from committed artifacts. If a number here and a ledger disagree, "
    "the ledger wins.", S["note"]))

doc = SimpleDocTemplate(OUT, pagesize=A4, title="Master Plan - Back-Analysis of the Models That Hurt the P&L",
                        author="KIMI + DeepSeek, per the human", subject="Model post-mortem and back-analysis program",
                        topMargin=2.2 * cm, bottomMargin=2.2 * cm, leftMargin=2.2 * cm, rightMargin=2.2 * cm)
doc.build(story, onFirstPage=header_footer, onLaterPages=header_footer)
print(f"wrote {OUT}")
