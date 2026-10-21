#!/usr/bin/env python3
"""Build the correlation-neighborhood design memo PDF.

Re-runnable: python3 tools/build_corr_design_pdf.py
Output: docs/correlation-neighborhoods-design.pdf
"""
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "correlation-neighborhoods-design.pdf")
TMP = os.path.join(ROOT, "docs", ".corr-design-assets")
os.makedirs(TMP, exist_ok=True)

INK = HexColor("#1a2433")
GRAY = HexColor("#5a6b80")
RULE = HexColor("#1b2938")
ACCENT = HexColor("#274b73")

styles = {
    "body": ParagraphStyle(
        "body", fontName="Times-Roman", fontSize=10.5, leading=15.5,
        textColor=INK, spaceAfter=7, alignment=4,
    ),
    "h1": ParagraphStyle(
        "h1", fontName="Times-Bold", fontSize=16, leading=20,
        textColor=INK, spaceBefore=18, spaceAfter=10, keepWithNext=1,
    ),
    "h2": ParagraphStyle(
        "h2", fontName="Times-Bold", fontSize=12.5, leading=16,
        textColor=INK, spaceBefore=14, spaceAfter=7, keepWithNext=1,
    ),
    "caption": ParagraphStyle(
        "caption", fontName="Times-Italic", fontSize=9, leading=12,
        textColor=GRAY, spaceBefore=4, spaceAfter=12, alignment=1,
    ),
    "meta": ParagraphStyle(
        "meta", fontName="Times-Roman", fontSize=11, leading=16,
        textColor=GRAY, alignment=1,
    ),
    "ref": ParagraphStyle(
        "ref", fontName="Times-Roman", fontSize=9.5, leading=13,
        textColor=INK, leftIndent=24, firstLineIndent=-24, spaceAfter=4,
    ),
    "bullet": ParagraphStyle(
        "bullet", fontName="Times-Roman", fontSize=10.5, leading=15,
        textColor=INK, leftIndent=16, bulletIndent=4, spaceAfter=4,
    ),
}


def P(text, style="body"):
    return Paragraph(text, styles[style])


def fig_inverted_pair(path):
    rng = np.random.default_rng(7)
    t = np.arange(0, 300)
    common = np.cumsum(rng.normal(0, 1, len(t)))
    shock = np.zeros(len(t))
    shock[150:] = np.linspace(0, 40, len(t) - 150)  # idiosyncratic event in A
    noise_a, noise_b = np.cumsum(rng.normal(0, 0.8, len(t))), np.cumsum(rng.normal(0, 0.8, len(t)))
    a = 100 + common + noise_a + shock
    b = 100 + common + noise_b - 0.6 * shock  # inverted neighbour bleeds off
    fig, ax = plt.subplots(figsize=(7.2, 3.4))
    ax.plot(t, a, color="#274b73", lw=1.4, label="leg A — event (e.g. listing, upgrade)")
    ax.plot(t, b, color="#6e2c2c", lw=1.4, label="leg B — inverted neighbour")
    ax.axvspan(150, 300, color="#f0b34a", alpha=0.12)
    ax.annotate("idiosyncratic event:\nA jumps, B sags", xy=(225, a[225]), xytext=(200, 60),
                arrowprops=dict(arrowstyle="->", color="#5a6b80"), fontsize=8.5, color="#1a2433")
    ax.set_xlabel("time")
    ax.set_ylabel("price (indexed)")
    ax.legend(fontsize=8.5, frameon=False, loc="upper left")
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def fig_planes(path):
    fig, ax = plt.subplots(figsize=(7.2, 3.6))
    ax.axis("off")

    def box(x, y, w, h, title, lines, fc):
        ax.add_patch(plt.Rectangle((x, y), w, h, facecolor=fc, edgecolor="#1b2938", lw=1))
        ax.text(x + w / 2, y + h - 0.09, title, ha="center", va="top", fontsize=9, weight="bold", color="#1a2433")
        for i, ln in enumerate(lines):
            ax.text(x + 0.05, y + h - 0.24 - i * 0.13, ln, fontsize=7.6, color="#1a2433", va="top")

    box(0.02, 0.55, 0.44, 0.40, "identity plane — refdata registry",
        ["canonical ids, identifiers, class,", "venues, limits, static risk attrs", "",
         "edges: stable pair structure only", "(trades_with / inverts_with /", "same_sector / leads_lag + provenance)"],
        "#eef3fa")
    box(0.54, 0.55, 0.44, 0.40, "analytics plane — lake stores",
        ["returns & spread matrices, rolling", "correlation, z-score series,", "cointegration estimates", "",
         "time-varying: changes every bar", "(npz / parquet under data/)"],
        "#f4f7ef")
    box(0.02, 0.04, 0.44, 0.36, "consumers",
        ["viewer: correlation neighbourhood", "graph (typed edges, coefficients)", "",
         "models: correction-trade signals,", "replay harness, ATLAS sweeps"],
        "#fbf6ee")
    box(0.54, 0.04, 0.44, 0.36, "minting job — tools/corr_*",
        ["resolve symbols → canonical ids", "align bars, estimate, screen", "",
         "mint edges when structure shifts", "publish matrices daily"],
        "#f6eef6")

    ax.annotate("", xy=(0.54, 0.75), xytext=(0.46, 0.75), arrowprops=dict(arrowstyle="<->", color="#5a6b80"))
    ax.annotate("", xy=(0.76, 0.40), xytext=(0.76, 0.55), arrowprops=dict(arrowstyle="->", color="#5a6b80"))
    ax.annotate("", xy=(0.24, 0.40), xytext=(0.24, 0.55), arrowprops=dict(arrowstyle="->", color="#5a6b80"))
    ax.annotate("", xy=(0.46, 0.22), xytext=(0.54, 0.22), arrowprops=dict(arrowstyle="<->", color="#5a6b80"))
    ax.text(0.5, 0.485, "rule: changes every bar → analytics;\nchanges on exchange reconfigure → identity",
            ha="center", fontsize=7.6, color="#6e2c2c", style="italic")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    fig.savefig(path, dpi=160, bbox_inches="tight")
    plt.close(fig)


def fig_spread(path):
    rng = np.random.default_rng(11)
    n = 400
    ar = np.zeros(n)
    eps = rng.normal(0, 1, n)
    for i in range(1, n):
        ar[i] = 0.94 * ar[i - 1] + eps[i]  # half-life ~ 11 bars
    z = ar / np.std(ar)
    entry_hi, exit_hi, stop_hi = 2.0, 0.3, 3.2
    fig, ax = plt.subplots(figsize=(7.2, 3.2))
    ax.plot(z, color="#274b73", lw=1.1)
    for y, c, lbl in [(entry_hi, "#37c98b", "+entry"), (-entry_hi, "#37c98b", "−entry"),
                      (exit_hi, "#5a6b80", "exit"), (-exit_hi, "#5a6b80", ""),
                      (stop_hi, "#f0605f", "stop"), (-stop_hi, "#f0605f", "")]:
        ax.axhline(y, color=c, lw=0.9, ls="--")
    ax.text(395, entry_hi + 0.08, "+entry", fontsize=8, color="#1d5940", ha="right")
    ax.text(395, -entry_hi - 0.30, "−entry", fontsize=8, color="#1d5940", ha="right")
    ax.text(395, exit_hi + 0.08, "exit", fontsize=8, color="#5a6b80", ha="right")
    ax.text(395, stop_hi + 0.08, "stop", fontsize=8, color="#6e2c2c", ha="right")
    ax.set_ylabel("spread z-score")
    ax.set_xlabel("time")
    ax.set_ylim(-3.8, 3.8)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    fig.tight_layout()
    fig.savefig(path, dpi=160)
    plt.close(fig)


def formula(path, tex, fontsize=17, w=12.5):
    fig = plt.figure(figsize=(w * 0.55, 0.7))
    fig.text(0.5, 0.5, tex, fontsize=fontsize, ha="center", va="center")
    fig.savefig(path, dpi=170, bbox_inches="tight", transparent=True)
    plt.close(fig)


def three_line_table(rows, widths, header=True):
    t = Table(rows, colWidths=widths, hAlign="LEFT")
    style = [
        ("FONTNAME", (0, 0), (-1, -1), "Times-Roman"),
        ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("TEXTCOLOR", (0, 0), (-1, -1), INK),
        ("LINEBELOW", (0, 0), (-1, 0), 0.75, RULE),
        ("LINEBELOW", (0, -1), (-1, -1), 1.5, RULE),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]
    if header:
        style.insert(0, ("FONTNAME", (0, 0), (-1, 0), "Times-Bold"))
        style.append(("LINEABOVE", (0, 0), (-1, 0), 1.5, RULE))
    t.setStyle(TableStyle(style))
    return t


def header_footer(canvas, doc):
    canvas.saveState()
    w, h = A4
    canvas.setFont("Times-Italic", 8.5)
    canvas.setFillColor(GRAY)
    canvas.drawCentredString(w / 2, h - 1.5 * cm, "CryptAlyst — correlation neighborhoods & pairs design memo")
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.5)
    canvas.line(2 * cm, h - 1.7 * cm, w - 2 * cm, h - 1.7 * cm)
    canvas.drawCentredString(w / 2, 1.4 * cm, f"page {doc.page}")
    canvas.restoreState()


def cover(canvas, doc):
    canvas.saveState()
    w, h = A4
    canvas.setFillColor(HexColor("#f5f7fa"))
    canvas.rect(0, h - 6.2 * cm, w, 6.2 * cm, stroke=0, fill=1)
    canvas.setFillColor(ACCENT)
    canvas.rect(0, h - 6.35 * cm, w, 0.12 * cm, stroke=0, fill=1)
    canvas.restoreState()


def build():
    p1 = os.path.join(TMP, "inverted.png")
    p2 = os.path.join(TMP, "planes.png")
    p3 = os.path.join(TMP, "spread.png")
    f1 = os.path.join(TMP, "f1.png")
    f2 = os.path.join(TMP, "f2.png")
    f3 = os.path.join(TMP, "f3.png")
    fig_inverted_pair(p1)
    fig_planes(p2)
    fig_spread(p3)
    formula(f1, r"$s_t \;=\; \ln P^{A}_t \;-\; \beta\, \ln P^{B}_t \;-\; \alpha,\qquad \beta \;\; \text{from the cointegrating regression}$")
    formula(f2, r"$\Delta s_t \;=\; \lambda\, s_{t-1} + \varepsilon_t,\qquad \tau_{1/2} \;=\; \frac{-\ln 2}{\ln(1+\lambda)}$")
    formula(f3, r"$r_i(t) \;=\; \alpha_i \;+\; \beta^{BTC}_i\, r_{BTC}(t) \;+\; \beta^{sec}_i\, r_{sec}(t) \;+\; \varepsilon_i(t)$", w=13.5)

    doc = SimpleDocTemplate(
        OUT, pagesize=A4,
        topMargin=2.4 * cm, bottomMargin=2.2 * cm,
        leftMargin=2.2 * cm, rightMargin=2.2 * cm,
        title="Correlation Neighborhoods and Pairs Trading in CryptAlyst",
        author="KIMI (CryptAlyst build lane)",
        subject="Design memo: inverted-pair sensitivity, correction trades, and where the structure lives",
    )
    W = doc.width
    story = []

    # ---- cover
    story.append(Spacer(1, 4.2 * cm))
    story.append(P("Correlation Neighborhoods &amp; Pairs Trading in CryptAlyst", "h1"))
    story[-1].style = ParagraphStyle("t", parent=styles["h1"], fontSize=23, leading=29, alignment=1, spaceAfter=16)
    story.append(P("Inverted-pair sensitivity, correction trades, and where the structure lives "
                   "in the lake / refdata architecture", "meta"))
    story.append(Spacer(1, 0.7 * cm))
    story.append(P("Design memo — for thinking, review, and staging the build", "meta"))
    story.append(P("KIMI · build lane · 21 October 2026 · v1.0", "meta"))
    story.append(Spacer(1, 1.4 * cm))
    story.append(P("Companion documents: docs/solvers.md (Stage-1 methodology), docs/postmortems/ "
                   "(Stage-0 autopsy), docs/refdata.md (registry), DATA_STORAGE.md (stores census). "
                   "Rules: AGENTS.md A1–A6; COMMS_PROTOCOL Rules 1 and 5.", "meta"))
    story.append(PageBreak())

    # ---- 1 motivation
    story.append(P('<a name="s1"/>1&nbsp;&nbsp;Motivation: the equity analogy, in crypto', "h1"))
    story.append(P(
        "In equities, securities respond to shared events through their sector and factor structure: "
        "when a chip maker releases a new product its own price rises while close competitors sag — an "
        "<b>inverted pair</b> driven by one event. The co-movement is not anecdotal; it is the residual "
        "output of a common-factor model, and it is tradeable as relative value."))
    story.append(P(
        "Crypto exhibits the same structure, more strongly and more measurably. Coins share common "
        "features in a disparate way: every alt perpetual loads on BTC; L1s load on each other; DeFi "
        "tokens share protocol-risk exposure; AI-themed coins share a narrative factor; memes share "
        "liquidity regime. An event on one symbol — a listing, an exploit, a funding dislocation, a "
        "regulatory headline on a category — propagates to its neighbours with measurable sign, size, "
        "and delay. That propagation graph <b>is</b> the correlation neighbourhood the registry viewer "
        "already sketches: today it shows static links (identifiers, venues, documents); this memo "
        "designs its tradeable layer."))
    story.append(P(
        "Why now. Two system facts make this the right next move. First, the registry just became rich "
        "enough to support it: 1,396 Binance instruments (567 USDT-M perps, 828 fiat/stablecoin-quoted "
        "spot pairs) with display names, categories, supply, funding, and long/short sentiment, all "
        "keyed by canonical id. Second, the Stage-0 autopsy and Stage-1 counterfactuals show the "
        "selection-level landscape is flat — trip selection cannot pay the 20 bps round trip against a "
        "near-zero gross expectancy. The repair program therefore has to find <b>gross edge</b>, and "
        "relative-value structure is the most promising untested source: it is where systematic, "
        "fee-aware, hedged trades live."))
    story.append(Image(p1, width=W, height=W * 3.4 / 7.2))
    story.append(P('<a name="fig1"/>Figure 1 — Inverted-pair behaviour around an idiosyncratic event: '
                   'leg A jumps on its own news, the inverted neighbour bleeds off the shared factor.', "caption"))

    # ---- 2 concepts
    story.append(P('<a name="s2"/>2&nbsp;&nbsp;Concepts and formalism', "h1"))
    story.append(P('<a name="s21"/>2.1&nbsp;&nbsp;The spread and its half-life', "h2"))
    story.append(P(
        "A <b>pair</b> is two instruments whose log prices share a stochastic trend. The cointegrating "
        "regression fixes the hedge ratio β, and the residual spread is the tradeable object "
        '<super><a href="#ref1">[1]</a></super><super><a href="#ref2">[2]</a></super>:'))
    story.append(Image(f1, width=W * 0.94, height=W * 0.94 * 0.23))
    story.append(Spacer(1, 4))
    story.append(P(
        "Mean reversion speed is what makes a pair worth trading. Modelling the spread as an AR(1) "
        "process gives a closed-form half-life — the expected time for half the dislocation to close — "
        "which disciplines both sizing and holding period:"))
    story.append(Image(f2, width=W * 0.66, height=W * 0.66 * 0.20))
    story.append(Spacer(1, 4))
    story.append(P(
        "Screening rule (proposed): admit a pair only when |ρ| of 5-minute returns over the estimation "
        "window is above 0.6 <i>and</i> the half-life lies between 30 minutes and 5 days. Fast half-lives "
        "are fee fodder at 20 bps round trip; slow ones tie capital and drift out of cointegration."))
    story.append(P('<a name="s22"/>2.2&nbsp;&nbsp;Factor view: where the correction trade comes from', "h2"))
    story.append(P(
        "Pairs are a two-name special case of a factor model. Every instrument's return decomposes into "
        "a market leg, a sector leg, and an idiosyncratic residual:"))
    story.append(Image(f3, width=W * 0.98, height=W * 0.98 * 0.16))
    story.append(Spacer(1, 4))
    story.append(P(
        "The <b>correction trade</b> is the trade of the residual. Concretely: an event moves leg A "
        "beyond what its factor loadings explain; the neighbourhood graph says which neighbours should "
        "absorb the spillover and with what sign; the position is long the under-reacted neighbour (or "
        "short the over-reacted one) with the factor legs hedged, and it is exited as the residual "
        "mean-reverts. This is pairs trading in the Gatev–Rouwenhorst sense "
        '<super><a href="#ref3">[3]</a></super>, generalised from two names to a neighbourhood, and it '
        "is the equities literature's statistical-arbitrage practice transferred to a market where the "
        "factor structure is younger, faster, and far better instrumented "
        '<super><a href="#ref4">[4]</a></super><super><a href="#ref5">[5]</a></super>.'))
    story.append(P('<a name="s23"/>2.3&nbsp;&nbsp;The neighbourhood graph', "h2"))
    story.append(P(
        "Generalising from pairs to a graph: nodes are canonical instruments; edges carry typed "
        "sensitivity. Six predicates exist today (same_as, listed_on, has_document, issued_by, tracks, "
        "derived_from). The design adds four minted predicates for tradeable structure, with provenance "
        "in the edge Meta map:"))
    rows = [
        ["predicate", "meaning", "edge Meta (provenance)"],
        ["trades_with", "co-moving legs; residual mean-reverts", "window, horizon, ρ at mint, half-life"],
        ["inverts_with", "negative-sensitivity neighbour (equity-style inverted pair)", "window, horizon, ρ at mint, event tags"],
        ["same_sector", "shared factor membership (L1, DeFi, AI, meme, …)", "factor name, loadings, regime tag"],
        ["leads_lag", "one leg systematically moves first by k bars", "lag k, lead-lag correlation, window"],
    ]
    cap = P('<a name="tab1"/>Table 1 — Proposed minted predicates for tradeable structure.', "caption")
    story.append(cap)
    story.append(three_line_table(rows, [W * 0.16, W * 0.42, W * 0.42]))
    story.append(P(
        "Edges are <b>stable structure</b>: minted by the estimation job, rewritten only when the "
        "estimate shifts materially (regime change), and versioned in the journal like everything else. "
        "The coefficient <i>series</i> — the thing that changes every bar — is never written to the "
        "registry; it lives in the analytics plane (§3)."))

    # ---- 3 architecture
    story.append(P('<a name="s3"/>3&nbsp;&nbsp;Where the structure lives: identity vs analytics plane', "h1"))
    story.append(P(
        "The governing rule, already circulated to the build lane: <b>if it changes every bar it is "
        "analytics; if it changes when the exchange reconfigures it is reference.</b> The registry "
        "(identity plane) answers what an instrument is; the lake stores (analytics plane) hold what it "
        "did. Conflating the two would bloat the journal with per-bar noise and starve the models of "
        "history."))
    story.append(Image(p2, width=W, height=W * 3.6 / 7.2))
    story.append(P('<a name="fig2"/>Figure 2 — Plane separation and flow. The minting job resolves symbols to '
                   'canonical ids, estimates structure, mints stable edges into refdata, and publishes '
                   'time-varying matrices into the lake.', "caption"))
    rows = [
        ["data", "plane", "store", "owner"],
        ["canonical id, identifiers, venues, limits", "identity", "refdata journal", "refdata service"],
        ["static risk attrs (maint margin, fee, leverage cap)", "identity", "refdata meta/limits", "refseed generator"],
        ["pair edges + provenance", "identity", "refdata edges", "corr minting job"],
        ["return matrices, ρ(t), z-scores, spreads", "analytics", "npz/parquet under data/", "lake tools"],
        ["replay counterfactuals, sweep results", "analytics", "docs/postmortems/", "Stage-1/2 harness"],
    ]
    cap = P('<a name="tab2"/>Table 2 — What lives where, under the plane rule.', "caption")
    story.append(cap)
    story.append(three_line_table(rows, [W * 0.44, W * 0.12, W * 0.24, W * 0.20]))

    # ---- 4 data & compute
    story.append(P('<a name="s4"/>4&nbsp;&nbsp;Data, estimation, and compute plan', "h1"))
    story.append(P('<a name="s41"/>4.1&nbsp;&nbsp;Inputs and alignment', "h2"))
    story.append(P(
        "Market data comes from the recorder stores; instrument identity comes from the enriched "
        "registry. The first pipeline step is <b>resolution</b>: every raw symbol in a market-data frame "
        "is resolved through the refdata client to its canonical id before alignment. This is not "
        "bureaucracy — the registry holds BTCUSDT spot and BTCUSDT perp as distinct records, and a "
        "spread computed across that seam is garbage. Legs are aligned on bar timestamps per venue "
        "session, with the exchange-native tick/lot sizes (now in every record's limits) applied before "
        "any spread is tradable-sized."))
    story.append(P('<a name="s42"/>4.2&nbsp;&nbsp;Estimation cadence', "h2"))
    story.append(P(
        "Hourly returns matrix refresh; daily full re-estimate (ρ at 5m/1h/1d horizons, cointegration "
        "screen over the perp universe, half-life filter, event-tagged inversions from funding and "
        "sentiment extremes already seeded in meta). Edge minting is <i>diff-driven</i>: only material "
        "structure changes append to the journal, so the registry stays lean while the analytics plane "
        "accumulates full history."))
    story.append(P('<a name="s43"/>4.3&nbsp;&nbsp;Compute budget and the GPU gate', "h2"))
    story.append(P(
        "A full 567×567 correlation matrix on 5-minute returns is seconds on CPU — the CPU timing "
        "baselines from Stage 1 (315 full-table passes per second over 809k rows) leave no doubt. The "
        "GPU lane (ATLAS) stays gated behind the path-level replay timing experiment per Rule 5: "
        "vectorise in numpy first, time one window, and move the hot loop to JAX/CuPy only if the "
        "measured sweep count demands it. Seeding and versioning are mandatory — a backtest that cannot "
        "be re-run identically is a liability."))

    # ---- 5 trading
    story.append(P('<a name="s5"/>5&nbsp;&nbsp;Trading integration: the correction trade', "h1"))
    story.append(P(
        "Signal chain: (1) an event or dislocation is detected on leg A — idiosyncratic move beyond its "
        "factor expectation, funding spike, sentiment extreme; (2) the neighbourhood graph returns A's "
        "typed neighbours with sign and provenance; (3) for each admissible pair the spread z-score is "
        "computed from the analytics plane; (4) entry when |z| crosses the band, exit at the inner "
        "band, stop at k·σ beyond entry; (5) factor legs hedged so the position carries residual risk, "
        "not market risk."))
    story.append(Image(p3, width=W, height=W * 3.2 / 7.2))
    story.append(P('<a name="fig3"/>Figure 3 — Spread z-score bands on a synthetic mean-reverting spread '
                   '(half-life ≈ 11 bars). Entry at ±2, exit at ±0.3, stop at ±3.2.', "caption"))
    story.append(P(
        "Fee and risk discipline come straight from the autopsy: at a 20 bps round trip, only spreads "
        "whose expected capture exceeds fees plus slippage by a stated margin are admissible; sizing is "
        "a function of half-life (faster reversion, smaller holding, wider band); and nothing reaches "
        "a live enablement without a human decision — Rule A6 makes the promotion gate explicit. The "
        "first evaluation vehicle is the Stage-1 path-level replay over the losing-model windows: the "
        "same harness that tests the repair hypotheses tests correction trades, on the same data, with "
        "the same acceptance math (purged/embargoed CV, deflated Sharpe, per docs/solvers.md)."))

    # ---- 6 milestones
    story.append(P('<a name="s6"/>6&nbsp;&nbsp;Milestones', "h1"))
    rows = [
        ["milestone", "content", "acceptance"],
        ["M0 — this memo", "design agreed on the channel", "AGREE from both lanes; human nod"],
        ["M1 — pair screen offline", "cointegration screen over perp universe from existing klines", "ranked pair list + half-lives, reproducible script"],
        ["M2 — minting job + viewer typing", "tools/corr_* job; four predicates; graph renders typed edges", "edges versioned; viewer shows provenance"],
        ["M3 — replay evaluation", "correction trades replayed on Stage-1 windows", "expectancy net of fees, CIs, deflated Sharpe"],
        ["M4 — proposal", "ranked trade proposals to the human", "human decision per Rule A6; nothing auto-enabled"],
    ]
    cap = P('<a name="tab3"/>Table 3 — Staging. Each milestone justifies the next.', "caption")
    story.append(cap)
    story.append(three_line_table(rows, [W * 0.22, W * 0.44, W * 0.34]))
    story.append(Spacer(1, 10))
    story.append(P(
        "Open questions to think about: whether funding-rate dislocations (already in every perp's "
        "meta) should mint their own edge predicate or ride trades_with provenance; whether "
        "same_sector should come from the seeded category tags, from an embedding of coin descriptions, "
        "or both; and how aggressively the neighbourhood should generalise beyond two legs before the "
        "residual stops being hedgeable."))

    # ---- references
    story.append(PageBreak())
    story.append(P("References", "h1"))
    refs = [
        ('<a name="ref1"/>[1] Engle, R. F., &amp; Granger, C. W. J. (1987). Co-integration and error '
         'correction: Representation, estimation, and testing. <i>Econometrica, 55</i>(2), 251–276.'),
        ('<a name="ref2"/>[2] Johansen, S. (1991). Estimation and hypothesis testing of cointegration '
         'vectors in Gaussian vector autoregressive models. <i>Econometrica, 59</i>(6), 1551–1580.'),
        ('<a name="ref3"/>[3] Gatev, E., Goetzmann, W. N., &amp; Rouwenhorst, K. G. (2006). Pairs '
         'trading: Performance of a relative-value arbitrage rule. <i>The Review of Financial '
         'Studies, 19</i>(3), 797–827.'),
        ('<a name="ref4"/>[4] Vidyamurthy, G. (2004). <i>Pairs trading: Quantitative methods and '
         'analysis.</i> Wiley.'),
        ('<a name="ref5"/>[5] Avellaneda, M., &amp; Lee, J.-H. (2010). Statistical arbitrage in the US '
         'equities market. <i>Quantitative Finance, 10</i>(7), 761–782.'),
        ('<a name="ref6"/>[6] Pole, A. (2007). <i>Statistical arbitrage: Algorithmic trading insights '
         'and techniques.</i> Wiley.'),
    ]
    for r in refs:
        story.append(P(r, "ref"))

    doc.build(story, onFirstPage=cover, onLaterPages=header_footer)
    print(f"built {OUT}")


if __name__ == "__main__":
    build()
