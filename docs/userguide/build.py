#!/usr/bin/env python3
"""Build the CryptAlyst User Guide as HTML, printed to PDF by headless Chrome."""
import base64, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ACCENT, MUTED = "#1F4E79", "#596370"
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "userguide.html")

def fig(n, caption):
    b64 = base64.b64encode(open(os.path.join(HERE, f"u{n}.png"), "rb").read()).decode()
    return (f'<figure><img src="data:image/png;base64,{b64}" alt="">'
            f'<figcaption>{caption}</figcaption></figure>')

def table(headers, rows, widths=None):
    cols = ""
    if widths:
        cols = "<colgroup>" + "".join(f'<col style="width:{w}">' for w in widths) + "</colgroup>"
    head = "".join(f"<th>{h}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table>{cols}<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"

CSS = f"""
@page {{ size: A4; margin: 20mm 18mm 18mm 18mm; }}
* {{ box-sizing: border-box; }}
body {{ font-family: Helvetica, Arial, sans-serif; font-size: 10.5pt; color: #1a1a1a; line-height: 1.5; margin: 0; }}
h1 {{ font-size: 26pt; color: {ACCENT}; margin: 0 0 6px; letter-spacing: -0.01em; }}
h2 {{ font-size: 14pt; color: {ACCENT}; margin: 26px 0 8px; padding-bottom: 4px;
      border-bottom: 1.5px solid {ACCENT}; page-break-after: avoid; }}
h3 {{ font-size: 11pt; color: {MUTED}; margin: 16px 0 4px; page-break-after: avoid; }}
p {{ margin: 7px 0; }}
.lede {{ font-size: 12pt; color: {MUTED}; margin: 4px 0 18px; }}
.meta {{ font-size: 9pt; color: {MUTED}; border-top: 1px solid #d8e0e8;
         border-bottom: 1px solid #d8e0e8; padding: 7px 0; margin: 14px 0 0; }}
figure {{ margin: 12px 0; page-break-inside: avoid; text-align: center; }}
figure img {{ max-width: 100%; max-height: 150mm; width: auto; height: auto; }}
figcaption {{ font-size: 8pt; font-style: italic; color: {MUTED}; margin-top: 4px; }}
table {{ width: 100%; border-collapse: collapse; margin: 8px 0 12px; font-size: 9pt; }}
th {{ background: {ACCENT}; color: #fff; font-weight: bold; text-align: left; padding: 5px 7px;
      border: 1px solid #16375a; }}
td {{ padding: 4px 7px; border: 1px solid #c9d4e0; vertical-align: top; }}
tbody tr:nth-child(even) td {{ background: #f4f7fa; }}
pre {{ background: #F1F3F6; border-left: 3px solid {ACCENT}; padding: 8px 10px;
       font-family: "Courier New", monospace; font-size: 8.4pt; white-space: pre-wrap; }}
code {{ font-family: "Courier New", monospace; font-size: 9pt; }}
.note {{ border-left: 3px solid {ACCENT}; background: #eef5fa; padding: 8px 12px; margin: 10px 0; }}
.warn {{ border-left: 3px solid #a4232b; background: #fdf0f2; padding: 8px 12px; margin: 10px 0; }}
ul, ol {{ margin: 6px 0 6px 20px; }} li {{ margin: 4px 0; }}
.toc {{ display: grid; grid-template-columns: 1fr 1fr; column-gap: 18px; font-size: 10pt; margin: 8px 0 4px; }}
"""

html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>CryptAlyst User Guide</title><style>{CSS}</style></head><body>

<h1>CryptAlyst User Guide</h1>
<p class="lede">What the dashboard shows, what it means, and how to read it without fooling
yourself.</p>
<p class="meta">CryptAlyst · user guide · commit <code>29866c7</code> · written against the
running platform; every panel name and label below exists in the current build</p>

<div class="toc">
<div>1 · What this is for</div>
<div>2 · The six views</div>
<div>3 · The overview</div>
<div>4 · The board and the alerts</div>
<div>5 · The forecast view</div>
<div>6 · Reading the numbers honestly</div>
<div>7 · Keyboard and mouse</div>
<div>8 · When something looks wrong</div>
</div><div>8 · When something looks wrong</div>
</div>

<h2>1. What this is for</h2>
<p>CryptAlyst watches the whole of Binance — several hundred pairs at once — and answers three
questions in that order: <b>is anything moving</b>, <b>what does the model expect next</b>, and
<b>was the last expectation right</b>.</p>
<p>It is a market-watch and forecasting bench, not an execution system. Nothing here places an
order. Its value is that every claim it makes can be traced to a recorded number, including the
occasions when it declined to make one.</p>

<h2>2. The six views</h2>
{fig(1, "Figure 1 — six views, one question each. The top bar switches between them.")}
{table(["view", "use it when"],
 [["<b>OVERVIEW</b>", "you want the whole market on one page: alerts, leaderboard, split, screen, survey, movers"],
  ["<b>PUMP &amp; DUMP</b>", "you are hunting: the alerts feed, the full leaderboard and the screen, with room to read"],
  ["<b>MESSAGE BUS</b>", "you suspect the plumbing: per-topic envelope counts and the recording rate"],
  ["<b>SETTINGS</b>", "you want to change what counts as a move"],
  ["<b>AGENTS</b>", "you want to know whether the GPU workers are up and what they are running"],
  ["<b>FORECAST</b>", "you want a model's expectation for one pair, graded"]],
 ["18%", "82%"])}

<h2 class="pagebreak">3. The overview</h2>
<p>Six panels, each answering a different question, all live:</p>
{table(["panel", "what it is telling you"],
 [["<b>live alerts</b>", "detections as they happen, newest first — a pump, a dump, a reversal. Each card carries the numbers that fired it"],
  ["<b>flow leaderboard</b>", "every symbol ranked by a chosen measure: burst multiple, 10s move, volume. This is the panel you will use most"],
  ["<b>flow split</b>", "the same market separated into spot and perp, so you can see where the pressure is"],
  ["<b>pump / dump screen</b>", "the filtered table: only rows that pass your thresholds, with a verdict and a score"],
  ["<b>turtle survey</b>", "the longer-horizon setups and their plans, with the risk budget"],
  ["<b>24h movers</b>", "context: today's biggest gainers and losers"]],
 ["26%", "74%"])}
<div class="note"><b>The cards explain themselves.</b> Hover anything with a dotted underline for a
second and a half and a plain-English definition appears — what the number is, how it is computed,
and what a large value implies.</div>

<h2>4. The board and the alerts</h2>
{fig(2, "Figure 2 — one leaderboard row, and what each interaction does.")}
<p>A row shows the symbol, whether it is spot or a perpetual, a burst bar with its multiple, and
the recent move. The multiple is the readable one: <b>26.4x</b> means the last ten seconds carried
twenty-six times the normal flow for that pair.</p>
<h3>Filtering and freezing</h3>
<ul>
<li><b>ALL / PERP / SPOT</b> narrows the board; the count in the header follows.</li>
<li>Clicking a sortable column heading sorts by it; click again to reverse.</li>
<li><b>Hovering the list freezes the order</b>, so a row cannot move out from under the pointer.
Moving away resumes live ordering.</li>
<li>Arrow keys walk the list and light exactly one row; <b>Enter</b> opens that pair's chart.</li>
</ul>
<h3>The alert card</h3>
<p>Each card is one detection: the symbol, the direction, the score, the window it fired on and
the evidence — price, move, flow multiple, z-score, aggressor share. <b>compact</b> at the top of
the feed shrinks the cards to one line each when you want many on screen.</p>

<h2 class="pagebreak">5. The forecast view</h2>
{fig(3, "Figure 3 — the workflow. The panel deliberately reads left to right: pair, frame, horizon, model, run.")}
<table>
<colgroup><col style="width:24%"><col style="width:76%"></colgroup>
<thead><tr><th>section</th><th>what to do with it</th></tr></thead>
<tbody>
<tr><td><b>Parameters</b></td><td>ticker, interval, how far ahead, how much history, and the two
buttons that matter: <b>train + forecast</b> and <b>optimise on GPU</b></td></tr>
<tr><td><b>Live engine</b></td><td>what the GPU workers are doing now, and the best-ever
configuration per pair</td></tr>
<tr><td><b>Data</b></td><td>how much history is held locally, and a button to download more</td></tr>
<tr><td><b>History</b></td><td>seven numbers that decide whether a forecast is even possible:
bars held, span, freshness, bars required</td></tr>
<tr><td><b>Model to train</b></td><td>fifty models in twelve families, filterable, each showing
how it has scored on this pair</td></tr>
<tr><td><b>Forecast chart</b></td><td>the actual price, the forecast, the confidence cone and the
individual simulated paths</td></tr>
<tr><td><b>Comparison</b></td><td>every model run on this pair, side by side, so you can see
disagreement rather than one opinion</td></tr>
<tr><td><b>Backtest</b></td><td>the same frame graded walk-forward, which is the only number that
predicts future usefulness</td></tr>
</tbody></table>
<div class="note"><b>If History says "not enough history"</b>, press <b>download more</b> before
training. A forecast that cannot hold bars back cannot be graded, and an ungraded forecast is a
drawing.</div>

<h3>What the decision trail shows you</h3>
{fig(4, "Figure 4 — how a forecast becomes a verdict, or an honest refusal.")}
<p>The platform records the time each forecast was computed. If it is older than a minute, the
decision is <b>refused as stale</b> and says so. If it is fresh but projects a move smaller than
0.05%, the verdict is <b>none</b> — no trade. Only a fresh forecast with a real edge produces a
<b>buy</b> or <b>sell</b>, with a price and a target. Later, replay grades every one of those
against the price that actually arrived.</p>

<h2>6. Reading the numbers honestly</h2>
{table(["number", "what it means", "how to read it"],
 [["<b>MAPE</b>", "mean absolute percentage error of the forecast",
   "lower is better; compare only on the same frame and horizon"],
  ["<b>coverage</b>", "how often the actual price landed inside the cone",
   "a 90% cone should be right about 90% of the time; far off means the cone is lying"],
  ["<b>R&sup2;</b>", "variance explained",
   "<b>be careful</b> &mdash; on price levels it is flattered by the window's own spread, so a useless model can score 0.97"],
  ["<b>burst</b>", "recent flow versus normal",
   "a multiple; 10x is a real event, 2x is noise"],
  ["<b>edge</b>", "projected move against the floor",
   "below 0.05% the platform will not call a trade, and that is deliberate"],
  ["<b>z-score</b>", "how unusual a move is for that pair",
   "above 4 is rare; on quiet pairs it is common"]],
 ["13%", "38%", "49%"])}
<div class="warn"><b>The one mistake worth avoiding.</b> Do not read a high R² as skill. The
platform's own analysis found the old ranking rewarded models that simply followed the window's
drift; that is why the comparison table shows MAPE and coverage next to it, and why the backtest
section exists.</div>

<h2>7. Keyboard and mouse</h2>
{fig(5, "Figure 5 — everything that responds to input, in one place.")}
<table>
<colgroup><col style="width:26%"><col style="width:74%"></colgroup>
<thead><tr><th>input</th><th>does</th></tr></thead>
<tbody>
<tr><td><b>↑ ↓</b></td><td>move the leaderboard cursor</td></tr>
<tr><td><b>Enter</b></td><td>open the chart for the highlighted row</td></tr>
<tr><td><b>Esc</b></td><td>close a panel, a popup, or the drill-down</td></tr>
<tr><td><b>wheel</b> on a chart</td><td>zoom about the pointer</td></tr>
<tr><td><b>drag</b> on a chart</td><td>pan</td></tr>
<tr><td><b>double-click</b> on a chart</td><td>fit the whole window</td></tr>
<tr><td><b>5s … all</b></td><td>zoom presets, the same ones on every chart</td></tr>
<tr><td><b>full / min</b></td><td>expand a panel to the screen, or put it back</td></tr>
</tbody></table>

<h2>8. When something looks wrong</h2>
{fig(6, "Figure 6 — start from which symptom you have, not from which component you suspect.")}
<div class="note"><b>Nothing is silently empty.</b> A panel that cannot load shows why, in place,
and the rest of the page keeps working. If a panel is blank with no message, that is a bug worth
reporting — it should never happen.</div>
<h3>The honest state of the trading evidence</h3>
<p>If you open the decision trail today you will mostly see refusals, and occasionally a call.
That is the true state: across one hundred and sixty-four recorded forecasts the platform issued
seven directional calls and none of them were right yet. The machinery is working — it is the
models that have no edge. Treat every buy or sell you see as an experiment being measured, not a
recommendation.</p>

</body></html>"""

open(OUT, "w").write(html)
print("wrote", OUT, len(html), "bytes")
