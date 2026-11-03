#!/usr/bin/env python3
"""Build the CryptAlyst User Guide — colour chapters, worked examples, honest numbers."""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.dirname(HERE)
sys.path.insert(0, DOCS)
import buildkit as kit

SNAP = json.load(open(os.path.join(DOCS, "data", "platform-snapshot.json")))
SETTINGS = SNAP["settings"]
REPLAY = SNAP["replay_summary"] or {}
COMMIT = SNAP["commit"]

def fig(n, caption):
    return kit.fig(os.path.join(HERE, f"u{n}.png"), caption)

table, ch, co, kpis, defs = kit.table, kit.chapter, kit.callout, kit.kpis, kit.defs

P = []
def add(*b): P.extend(b)

add("""
<h1>CryptAlyst User Guide</h1>
<p class="lede">What the dashboard shows, what each number means, and how to use it without fooling yourself.</p>
<p class="meta">CryptAlyst · user guide · snapshot """ + COMMIT + """ · every panel, label and setting named here exists in the running build</p>
""")
add(kpis([("6", "views", "one question each"),
          (str(len(SETTINGS)), "settings", "the thresholds you control"),
          (str(REPLAY.get("graded", "—")), "graded calls", "replayed from the record"),
          ("0.05%", "decision floor", "below this, no call")]))
add("""
<div class="toc">
<div>1 · What this is for</div><div>9 · Settings and what they change</div>
<div>2 · The six views</div><div>10 · Agents and the GPU</div>
<div>3 · The overview, panel by panel</div><div>11 · The message bus</div>
<div>4 · The board</div><div>12 · Keyboard, mouse and layout</div>
<div>5 · Alerts and cards</div><div>13 · A worked session</div>
<div>6 · The forecast view</div><div>14 · Troubleshooting</div>
<div>7 · Reading the chart</div><div>15 · Glossary</div>
<div>8 · Reading the numbers</div><div>16 · Frequently asked</div>
</div>
""")

add(ch(1, "What this is for"))
add("""
<p>CryptAlyst watches the whole of Binance — several hundred pairs at once — and answers three questions in that order: <b>is anything moving</b>, <b>what does the model expect next</b>, and <b>was the last expectation right</b>.</p>
<p>It is a market-watch and forecasting bench, not an execution system. Nothing here places an order. Its value is that every claim it makes can be traced to a recorded number, including the occasions when it declined to make one.</p>
""")
add(co("note", "The one habit that makes this platform useful",
  "Read the second number before the first. A big move with no flow behind it is noise; a small move with 30x flow is the thing this platform was built to find."))
add(fig(1, "Figure 1 — six views, one question each."))

add(ch(2, "The six views"))
add(table(["view", "use it when", "what you get"], [
  ["<b>OVERVIEW</b>", "you want the whole market on one page", "alerts, leaderboard, split, screen, survey, movers"],
  ["<b>PUMP &amp; DUMP</b>", "you are hunting", "the alerts feed, the full board and the screen with room to read"],
  ["<b>MESSAGE BUS</b>", "you suspect the plumbing", "per-topic envelope counts and the recording rate"],
  ["<b>SETTINGS</b>", "you want to change what counts as a move", "the threshold table, applied live"],
  ["<b>AGENTS</b>", "you want to know if the GPU workers are up", "device, version, current job"],
  ["<b>FORECAST</b>", "you want a model's expectation for one pair, graded", "parameters, chart, comparison, backtest"],
], ["18%", "34%", "48%"]))

add(ch(3, "The overview, panel by panel"))
add(table(["panel", "what it is telling you"], [
  ["<b>live alerts</b>", "detections as they happen, newest first — a pump, a dump, a reversal. Each card carries the numbers that fired it"],
  ["<b>flow leaderboard</b>", "every symbol ranked by a measure you choose: burst multiple, 10s move, volume. The panel you will use most"],
  ["<b>flow split</b>", "the same market separated into spot and perp, so you can see where the pressure is"],
  ["<b>pump / dump screen</b>", "the filtered table: only rows that pass the thresholds, with a verdict and a score"],
  ["<b>turtle survey</b>", "longer-horizon setups and their plans, with the risk budget"],
  ["<b>24h movers</b>", "context: today's biggest gainers and losers"],
  ["<b>models</b> and <b>turtle idle</b>", "what the model engine and the turtle state are doing right now"],
], ["26%", "74%"]))
add(co("good", "Everything explains itself",
  "Hover anything with a dotted underline for a moment and a plain-English definition appears — what the number is, how it is computed, and what a large value implies. You should not have to leave the page to learn what a word means."))

add(ch(4, "The board"), fig(2, "Figure 2 — one leaderboard row, and what each interaction does."))
add("""
<p>A row shows the symbol, whether it is spot or a perpetual, a burst bar with its multiple, and the recent move. The multiple is the readable one: <b>26.4x</b> means the last ten seconds carried twenty-six times the normal flow for that pair.</p>
<h3>Filtering and freezing</h3>
""")
add(table(["action", "what happens"], [
  ["<b>ALL / PERP / SPOT</b>", "narrows the board; the count in the header follows"],
  ["a column heading", "sorts by it; click again to reverse"],
  ["hovering the list", "freezes the order, so a row cannot move out from under the pointer"],
  ["<b>↑ ↓</b>", "walk the list; exactly one row is lit"],
  ["<b>Enter</b>", "opens that pair's chart"],
  ["the diamond glyph", "opens the ATLAS chart for that row"],
], ["26%", "74%"]))
add(co("ex", "A worked read",
  "MUUBUSDT at 26.4x burst with +0.22% in ten seconds is a real event on a quiet pair. DOTUSDT at 3.1x with −0.06% is the market breathing. The first deserves a look; the second does not."))

add(ch(5, "Alerts and cards"))
add("""
<p>Each card is one detection: the symbol, the direction, the score, the window it fired on and the evidence — price, move, flow multiple, z-score, aggressor share. <b>compact</b> at the top of the feed shrinks every card to one line when you want many on screen.</p>
<p><b>drill</b> opens the detail panel for that symbol: eleven fields covering the last minute, the last hour and the session, plus the level structure and the book.</p>
""")
add(co("note", "What the score is, and is not",
  "The score ranks one detection against others in the same window. It is not a probability and does not know the future. A 64 is usually worth a look; a 40 usually is not."))

add(ch(6, "The forecast view"), fig(3, "Figure 3 — the workflow. The panel reads left to right: pair, frame, horizon, model, run."))
add(table(["section", "what to do with it"], [
  ["<b>Parameters</b>", "ticker, interval, how far ahead, how much history, and the two buttons that matter: <b>train + forecast</b> and <b>optimise on GPU</b>"],
  ["<b>Live engine</b>", "what the GPU workers are doing now, and the best-ever configuration per pair"],
  ["<b>Data</b>", "how much history is held locally, and a button to download more"],
  ["<b>History</b>", "seven numbers that decide whether a forecast is possible at all: bars held, span, freshness, bars required"],
  ["<b>Model to train</b>", "every model in the catalogue, grouped by family, filterable, each showing how it has scored on this pair"],
  ["<b>Forecast chart</b>", "the actual price, the forecast, the confidence cone and the individual simulated paths"],
  ["<b>Comparison</b>", "every model run on this pair, side by side, so you see disagreement rather than one opinion"],
  ["<b>Backtest</b>", "the same frame graded walk-forward — the only number that predicts future usefulness"],
], ["22%", "78%"]))
add(co("warn", "If History says “not enough history”",
  "Press <b>download more</b> before training. A forecast that cannot hold bars back cannot be graded, and an ungraded forecast is a drawing."))

add(ch(7, "Reading the chart"), fig(8, "Figure 4 — what is drawn on the forecast chart, and which part to believe."))
add("""
<p>The solid line is the price that happened. The line leaving its end is the model's path. Around it is a cone: the tenth to ninetieth percentile of many simulated futures. Behind the cone are individual futures, faint — they show the shape the cone is summarising.</p>
<p><b>Read the width of the cone before you read its direction.</b> A narrow cone that points the wrong way is a small error; a wide cone that points the right way is a guess with a confident font.</p>
""")
add(co("good", "Zoom, pan, and why the presets exist",
  "Wheel zooms about the pointer, drag pans, double-click fits the window. The presets (5s … all) are the same on every chart, so a screenshot at <b>1m</b> means the same thing wherever you took it."))

add(ch(8, "Reading the numbers"))
add(table(["number", "what it means", "how to read it"], [
  ["<b>MAPE</b>", "mean absolute percentage error of the forecast", "lower is better; compare only on the same frame and horizon"],
  ["<b>coverage</b>", "how often the actual landed inside the cone", "a 90% cone should be right about 90% of the time"],
  ["<b>R²</b>", "variance explained", "<b>be careful</b>: on price levels it is flattered by the window's own spread"],
  ["<b>burst</b>", "recent flow versus normal", "a multiple; 10x is a real event, 2x is noise"],
  ["<b>edge</b>", "projected move against the floor", "below 0.05% the platform will not call a trade"],
  ["<b>z-score</b>", "how unusual a move is for that pair", "above 4 is rare; on quiet pairs it is common"],
  ["<b>P(cont)</b>", "probability the move continues", "context, not a signal; it is not calibrated to your frame"],
], ["14%", "38%", "48%"]))
add(co("warn", "The one mistake worth avoiding",
  "Do not read a high R² as skill. The platform's own analysis found the old ranking rewarded models that simply followed the window's drift. That is why the comparison table shows MAPE and coverage beside it, and why the backtest section exists."))

add(ch(9, "Settings and what they change"))
add("<p>These are live: the screen reads them and nothing needs a restart.</p>")
add(table(["setting", "now", "what it does"], [
  ["<code>screen_min_move</code>", str(SETTINGS.get("screen_min_move")), "minimum percentage move in the window"],
  ["<code>screen_min_vol_ratio</code>", str(SETTINGS.get("screen_min_vol_ratio")), "volume versus its own average"],
  ["<code>screen_min_vol_z</code>", str(SETTINGS.get("screen_min_vol_z")), "standard deviations of that volume"],
  ["<code>screen_min_recent_usd</code>", str(SETTINGS.get("screen_min_recent_usd")), "dollar flow in the recent window"],
  ["<code>screen_min_quote_volume</code>", str(SETTINGS.get("screen_min_quote_volume")), "24h turnover floor"],
  ["<code>screen_top_n</code>", str(SETTINGS.get("screen_top_n")), "how many rows the screen returns"],
  ["<code>min_liq</code>", str(SETTINGS.get("min_liq")), "liquidity floor for the universe"],
  ["<code>max_symbols</code>", str(SETTINGS.get("max_symbols")), "the size of the watch list"],
], ["30%", "10%", "60%"]))
add(co("ex", "Raising a threshold",
  "If the screen floods with small pairs, raise <code>screen_min_quote_volume</code> first. It removes illiquid names without touching the sensitivity to a real move — the opposite of raising <code>screen_min_move</code>, which blinds you to slow squeezes."))

add(ch(10, "Agents and the GPU"))
add("""
<p>The agents view lists every GPU worker the platform knows: its device, its version, what it is computing and how long since it last spoke. Two are typical — one CUDA box on the network, one Metal machine (this laptop, when it is not doing something else).</p>
<p>If an agent is unreachable the platform scores the models locally. The board still moves; the sweeps are simply slower. Nothing silently stops.</p>
""")

add(ch(11, "The message bus"))
add("""
<p>This view answers one question: is the plumbing alive. It shows the envelope counts per topic and the recording rate, read from the recorder on port 8801.</p>
<p>If it is empty, the recorder is not running — that is the first thing to check, and the runbook in the developer guide says how.</p>
""")

add(ch(12, "Keyboard, mouse and layout"), fig(5, "Figure 5 — everything that responds to input."))
add(table(["input", "does"], [
  ["<b>↑ ↓</b>", "move the leaderboard cursor"],
  ["<b>Enter</b>", "open the chart for the highlighted row"],
  ["<b>Esc</b>", "close a panel, a popup, or the drill-down"],
  ["<b>wheel</b> on a chart", "zoom about the pointer"],
  ["<b>drag</b> on a chart", "pan"],
  ["<b>double-click</b> on a chart", "fit the whole window"],
  ["<b>full / min</b>", "expand a panel to the screen, or put it back"],
  ["<b>reset layout</b>", "restore every panel's default size and position"],
], ["26%", "74%"]))

add(ch(13, "A worked session"), fig(7, "Figure 6 — from noticing to grading, which is the whole loop in one picture."))
add("""
<p><b>1 · Notice.</b> A card fires for PROMUSDT: pump, score 64, flow 6.5x, +0.06% in ten seconds.</p>
<p><b>2 · Confirm.</b> Drill in. The flow is spread across several prints and the aggressor share is high — this is not one whale print.</p>
<p><b>3 · Look.</b> Open the ATLAS chart. Price is at the top of the recent range, volume rising, no obvious resistance above.</p>
<p><b>4 · Forecast.</b> Go to the forecast view, set the pair, the frame (1m), and a horizon. Press <b>train + forecast</b>.</p>
<p><b>5 · Judge.</b> The cone is wide and the median is flat: the model is saying “this could go either way, by about this much”. That is information — it says do not size up.</p>
<p><b>6 · Grade.</b> Backtest the frame. If the walk-forward result is worse than simply predicting no change, the model has no skill here, whatever the chart looked like.</p>
<p><b>7 · Decide.</b> That decision is yours. The platform's job was to put the numbers in front of you with their provenance attached.</p>
""")

add(ch(14, "Troubleshooting"), fig(6, "Figure 7 — start from the symptom you have."))
add(table(["symptom", "what it usually means"], [
  ["a panel is empty with a message", "that panel could not load; the rest of the page keeps working"],
  ["a panel is empty with no message", "a bug — it should never happen; report it"],
  ["the bus view is empty", "the recorder (:8801) is not running"],
  ["charts are flat", "the market feed is down; the last price is being held"],
  ["“not enough history”", "press download more, then train again"],
  ["“stale forecast” on a decision", "the forecast is older than 60 s — run a fresh one"],
  ["the page looks like last week", "hard-reload; the bundle hash changed"],
], ["34%", "66%"]))

add(ch(15, "Glossary"))
add(defs([
  ("burst", "recent flow divided by this pair's normal flow. A multiple, not a percentage."),
  ("z-score", "how many standard deviations a move is from that pair's own distribution."),
  ("aggressor", "the side that crossed the spread — the buyer lifting the offer, or the seller hitting the bid."),
  ("cone", "the tenth-to-ninetieth percentile band of simulated futures."),
  ("coverage", "how often the actual price landed inside that band."),
  ("MAPE", "mean absolute percentage error: the average size of the forecast's miss."),
  ("edge", "the projected move over the horizon, as a percentage, which the decision floor compares."),
  ("verdict", "buy, sell or none — the decision path's conclusion."),
  ("stale", "an answer older than its topic's time limit; the platform refuses to act on it."),
]))

add(ch(16, "Frequently asked"))
add(defs([
  ("Why does the first forecast of a pair take so long?",
   "The bar store is empty for that pair and frame, so history is downloaded first. Afterwards it answers from disk."),
  ("Why does the platform refuse to call a trade?",
   "Either the forecast is older than 60 seconds, or the projected move is inside the 0.05% floor. Both are rules, not errors."),
  ("Why do two models disagree completely?",
   "They are built on different assumptions — a damped trend and an analogue search are not the same opinion. The comparison table exists to show that."),
  ("Can I trust a green number?",
   "Trust it as a measurement, not as advice. The platform has recorded more directional calls than it has had correct."),
  ("Does it trade for me?",
   "No. Nothing in this platform places an order."),
]))

HTML = "".join(P)
OUT = os.path.join(DOCS, "CryptAlyst-User-Guide.pdf")
kit.build(
    title="CryptAlyst · User Guide",
    subtitle="User Guide",
    desc="What the dashboard shows, what each number means,<br/>and how to use it without fooling yourself.",
    body_html=kit.page("CryptAlyst User Guide") + HTML + "</body></html>",
    out_pdf=OUT,
)
print("wrote", OUT)
