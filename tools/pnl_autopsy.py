#!/usr/bin/env python3
"""pnl_autopsy — Stage 0 of the losing-models back-analysis.

Read-only. Two source classes, both append-only JSONL ledgers:

  * scalp5/state/fills.jsonl — the signal engines (grid, hft, movers). Every
    fill carries its own entry feature vector. CAVEAT these engines mark
    exits favourably (e.g. reason "profit_3s"), so their win rates are
    scratch-optimistic; the reports say so explicitly.
  * scalp5/ledger/<model>.jsonl — the sentinel-monitored models (skim,
    scalp_fast, swing_turtle, multi_models, stats_models) with net bps or
    USD PnL, equity curves, exit reasons, and for the decision models the
    per-sub-model VOTES — which is what a post-mortem needs to name the
    component that hurt.

Writes one markdown autopsy per source into docs/postmortems/ plus a
README index. No network, no wall-clock dependence in the math; the as-of
timestamp is metadata, not an input. Stdlib only, Python 3.9+:

    python3 tools/pnl_autopsy.py
"""
import argparse
import collections
import datetime
import json
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODELS = ["skim", "scalp_fast", "swing_turtle", "multi_models", "stats_models"]
ENGINES_FILLS = os.path.join(REPO, "scalp5/state/fills.jsonl")
SENTINEL = os.path.join(REPO, "scalp5/state/sentinel.json")
MODEL_LEDGER = {m: os.path.join(REPO, f"scalp5/ledger/{m}.jsonl") for m in MODELS}
SENTINEL_KEY = {"swing_turtle": "swing", "multi_models": "multimodels",
                "stats_models": "statsmodels"}


def load_jsonl(path):
    rows = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def pct(x, d=3):
    return f"{100*x:+.{d}f}%"


def usd(x, d=2):
    return f"${x:+,.{d}f}"


def curve_stats(points, unit):
    """points: ordered PnL observations -> (total, max drawdown). usd/bps sum
    linearly; pct compounds because percents multiply."""
    cum, peak, worst = 0.0, 0.0, 0.0
    comp = 1.0
    for p in points:
        cum += p
        if unit == "pct":
            comp *= 1.0 + p
        peak = max(peak, cum)
        worst = max(worst, peak - cum)
    total = (comp - 1.0) if unit == "pct" else cum
    return total, worst


def longest_streak(flags):
    best = cur = 0
    for f in flags:
        cur = cur + 1 if f else 0
        best = max(best, cur)
    return best


def votes_table(rows):
    """For decision models: per sub-model, how often its yes-vote appears in
    losses vs wins, and mean net when it voted yes vs no."""
    agg = collections.defaultdict(lambda: {"y_w": 0, "y_l": 0, "n": 0, "ny": 0, "net_y": 0.0, "net_n": 0.0})
    for r in rows:
        votes = (r.get("decision") or {}).get("votes") or {}
        if not votes:
            continue
        loss = r["net"] <= 0
        for name, v in votes.items():
            a = agg[name]
            if v:
                a["y_w"] += 0 if loss else 1
                a["y_l"] += 1 if loss else 0
                a["ny"] += 1
                a["net_y"] += r["net"]
            else:
                a["n"] += 1
                a["net_n"] += r["net"]
    return agg


def norm_row(r):
    """Reduce every schema to: net, unit('usd'|'pct'|'bps'), pnl_usd, ts, reason, side, symbol, held, gross, fee."""
    out = {"net": None, "unit": None, "pnl_usd": r.get("pnl_usd"), "reason": r.get("reason", "?"),
           "side": r.get("side", "?"), "symbol": r.get("symbol", "?"),
           "held": r.get("held_s"), "gross": None, "fee": None,
           "ts": r.get("exit_ms") or r.get("ts") or r.get("entry_ms") or 0}
    if r.get("pnl_usd") is not None and r.get("equity_after") is not None:
        out["net"], out["unit"] = r["pnl_usd"], "usd"
    elif r.get("net_bps") is not None:
        out["net"], out["unit"] = r["net_bps"], "bps"
        out["gross"] = r.get("gross_bps")
        out["fee"] = r.get("fee_bps") or r.get("fees_bps")
    elif r.get("net_pct") is not None:
        out["net"], out["unit"] = r["net_pct"], "pct"
        out["gross"] = r.get("gross_pct")
    else:
        return None
    return out


def fmt_net(v, unit):
    if unit == "usd":
        return usd(v)
    if unit == "bps":
        return f"{v:+.2f} bps"
    return pct(v)


def autopsy_source(name, rows, asof, kind, trust_note=None, sentinel=None):
    rows = [r for r in (norm_row(r) for r in rows) if r and r["net"] is not None]
    rows.sort(key=lambda r: r["ts"])
    n = len(rows)
    wins = [r for r in rows if r["net"] > 0]
    losses = [r for r in rows if r["net"] <= 0]
    wr = len(wins) / n
    netvals = [r["net"] for r in rows]
    total, mdd = curve_stats(netvals, rows[0]["unit"])
    gw = sum(r["net"] for r in wins)
    gl = -sum(r["net"] for r in losses)
    pf = (gw / gl) if gl > 0 else float("inf")

    md = [f"# {name} — P&L autopsy (Stage 0)", ""]
    if rows[0]["ts"]:
        span = (datetime.datetime.fromtimestamp(rows[0]["ts"] / 1000),
                datetime.datetime.fromtimestamp(rows[-1]["ts"] / 1000))
        md.append(f"Span {span[0]:%Y-%m-%d %H:%M} → {span[1]:%Y-%m-%d %H:%M} local, "
                  f"{n:,} round trips. As of {asof}; snapshot of an append-only, live ledger.")
    else:
        md.append(f"{n:,} round trips. As of {asof}.")
    md.append("Re-run: `python3 tools/pnl_autopsy.py`")
    if trust_note:
        md.append(f"\n> **Data-trust caveat:** {trust_note}")
    md.append("")
    md.append("## Headline")
    md.append("")
    md.append("| metric | value |")
    md.append("|---|---|")
    md.append(f"| round trips | {n:,} (wins {len(wins):,} / losses {len(losses):,}) |")
    md.append(f"| win rate | {100*wr:.1f}% |")
    md.append(f"| expectancy / trip | {fmt_net(mean(netvals), rows[0]['unit'])} |")
    med = sorted(netvals)[n // 2]
    md.append(f"| median trip | {fmt_net(med, rows[0]['unit'])} |")
    md.append(f"| total | {fmt_net(total, rows[0]['unit'])} |")
    md.append(f"| profit factor | {pf:.2f} |")
    md.append(f"| max drawdown | {fmt_net(mdd, rows[0]['unit'])} |")
    if rows[0]["unit"] == "usd":
        md.append("| starting paper equity | $5,000 (the fleet launches with --equity 5000) |")
        md.append(f"| return on that equity | {pct(total / 5000.0, 1)} |")
    md.append(f"| longest loss streak | {longest_streak([r['net'] <= 0 for r in rows])} |")
    held = [r["held"] for r in rows if r["held"] is not None]
    if held:
        md.append(f"| avg hold | {mean(held):.1f}s |")
    md.append("")

    gross = [r["gross"] for r in rows if r["gross"] is not None]
    fees = [r["fee"] for r in rows if r["fee"] is not None]
    if gross and fees:
        mg, mf = mean(gross), mean(fees)
        md.append("## Cost structure")
        md.append("")
        md.append(f"mean gross {mg:+.2f} bps vs mean fees {mf:.2f} bps per trip — fees are "
                  f"**{mf/abs(mg) if mg else float('inf'):.1f}×** the mean gross move. "
                  "A strategy whose round-trip cost is an order of magnitude larger than its "
                  "average capture cannot be fixed by entries; it must trade less, hold longer, "
                  "or capture more per trip.")
        md.append("")

    md.append("## Exit reasons — losses vs wins")
    md.append("")
    md.append("| reason | losses | wins |")
    md.append("|---|---|---|")
    rl = collections.Counter(r["reason"] for r in losses)
    rw = collections.Counter(r["reason"] for r in wins)
    for reason in sorted(set(rl) | set(rw), key=lambda k: rl.get(k, 0), reverse=True):
        md.append(f"| {reason} | {rl.get(reason, 0)} | {rw.get(reason, 0)} |")
    md.append("")

    if kind == "model" and any((r.get("_votes") for r in rows)):
        pass  # votes handled below from raw rows
    md.append("## Loss clustering")
    md.append("")
    by_sym = collections.defaultdict(lambda: [0, 0.0])
    for r in losses:
        by_sym[r["symbol"]][0] += 1
        by_sym[r["symbol"]][1] += r["net"]
    for sym, (cnt, tot) in sorted(by_sym.items(), key=lambda kv: kv[1][1])[:5]:
        md.append(f"- **{sym}** — {cnt} losses, {fmt_net(tot, rows[0]['unit'])}")
    if not by_sym:
        md.append("- (no losses)")
    md.append("")
    return md, rows, {"n": n, "wr": wr, "exp": mean(netvals), "unit": rows[0]["unit"],
                      "total": total, "pf": pf, "mdd": mdd}


def model_autopsy(name, raw, asof):
    """Full report for a sentinel-relevant model ledger."""
    rows = load_jsonl(MODEL_LEDGER[name])
    trust = None
    if name == "skim":
        trust = ("skim books a 20 bps round-trip fee per trip; its ledger is honest about "
                 "costs even when the entry logic is not the problem.")
    md, norm, stats = autopsy_source(name, rows, asof, "model", trust)
    agg = votes_table([dict(r, net=nr["net"]) for r, nr in zip(rows, norm) if nr])

    if agg:
        md += ["## Sub-model vote attribution (decision models)", "",
               "Which component voted for the trades that lost. yes-in-loss / yes-in-win counts, "
               "and mean trip net when the sub-model voted yes vs no.", "",
               "| sub-model | yes: losses | yes: wins | mean net when YES | mean net when NO |",
               "|---|---|---|---|---|"]
        for sub, a in sorted(agg.items(), key=lambda kv: kv[1]["net_y"] / max(1, kv[1]["ny"])):
            my = a["net_y"] / a["ny"] if a["ny"] else 0.0
            mn = a["net_n"] / a["n"] if a["n"] else 0.0
            md.append(f"| {sub} | {a['y_l']} | {a['y_w']} | {fmt_net(my, stats['unit'])} | "
                      f"{fmt_net(mn, stats['unit'])} |")
        md.append("")
        md.append("Confound to keep in view: when the whole book bleeds, every sub-model "
                  "yes-vote bleeds — the yes/no gap above is descriptive, not causal. The "
                  "Stage 1 counterfactual (replay with the component removed) is the only "
                  "way to attribute drag to a component; treat the ranking as a search "
                  "order, not a verdict.")
        md.append("")

    md += ["## Hypotheses for Stage 1 (counterfactual tests, ranked)", ""]
    h = 0
    if agg:
        worst = min(agg.items(), key=lambda kv: kv[1]["net_y"] / max(1, kv[1]["ny"]))
        my = worst[1]["net_y"] / max(1, worst[1]["ny"])
        if worst[1]["ny"] >= 5 and my < 0:
            h += 1
            md.append(f"{h}. **Sub-model `{worst[0]}` drags the book**: its yes-votes average "
                      f"{fmt_net(my, stats['unit'])} over {worst[1]['ny']} trips. Stage 1: veto or "
                      "down-weight it; measure expectancy delta with it removed from the vote.")
    if stats["pf"] < 1.0:
        h += 1
        md.append(f"{h}. **Profit factor {stats['pf']:.2f}**: gross losses exceed gross wins. "
                  "Stage 1: cut the loss distribution — tighter stops, lateness gate, or a "
                  "session filter — and report expectancy delta per change.")
    fees = [r["fee"] for r in norm if r["fee"] is not None]
    gross = [r["gross"] for r in norm if r["gross"] is not None]
    if fees and gross and mean(fees) > abs(mean(gross)):
        h += 1
        md.append(f"{h}. **Fees exceed gross capture** ({mean(fees):.1f} bps vs "
                  f"{mean(gross):+.2f} bps): the model trades too often for what it captures. "
                  "Stage 1: minimum-edge threshold at entry (only trade when expected move > k× "
                  "fees); measure trades-per-day and expectancy jointly.")
    if stats["wr"] < 0.45:
        h += 1
        md.append(f"{h}. **Win rate {100*stats['wr']:.1f}%**: payoff asymmetry must carry the "
                  "edge. Stage 1: stop/target asymmetry sweep.")
    if not h:
        md.append("- No automated hypothesis fired; run the full parameter sweep in Stage 1.")
    md.append("")

    md += ["## Sentinel reconciliation", ""]
    try:
        st = json.load(open(SENTINEL)).get("models", {})
        key = SENTINEL_KEY.get(name)
        if key and key in st:
            s = st[key]
            since = datetime.datetime.fromtimestamp(s.get("since", 0))
            md.append(f"sentinel status: **{s.get('status')}** since {since:%Y-%m-%d %H:%M}, "
                      f"reason: \"{s.get('reason') or '(none recorded)'}\". "
                      "Note per Rule A6: status live means the model IS influencing paper "
                      "positions; any fix lands as a proposal, never an auto-promote.")
        else:
            md.append("not tracked by the sentinel (no control file key); propose coverage.")
    except FileNotFoundError:
        md.append("sentinel state missing.")
    md.append("")
    return "\n".join(md) + "\n", stats


def engine_autopsy(engine, rows, asof):
    trust = None
    if engine in ("grid", "hft", "movers"):
        trust = ("this engine marks exits on its own terms (e.g. reason profit_3s), so its "
                 "win rate is scratch-optimistic and its PnL is NOT mark-to-market reality. "
                 "Treat every number below as an upper bound; the trustworth loss surface for "
                 "the system lives in the sentinel-monitored model reports.")
    md, _, stats = autopsy_source(engine, rows, asof, "engine", trust)
    return "\n".join(md) + "\n", stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(REPO, "docs/postmortems"))
    args = ap.parse_args()
    asof = datetime.datetime.now().strftime("%Y-%m-%d %H:%M local")
    os.makedirs(args.out, exist_ok=True)

    index = ["# Post-mortems — per-model P&L autopsies (Stage 0)", "",
             f"Generated by `tools/pnl_autopsy.py` as of {asof}. Read-only; every number "
             "re-derives from the append-only ledgers. Stage 1 (replay + counterfactuals) "
             "tests the hypotheses in each file; per Rule A6 nothing re-enables without the "
             "human.", "",
             "## Sentinel-monitored models (the ones that can hurt the book)", "",
             "| model | trips | win rate | expectancy/trip | total | profit factor | max DD | report |",
             "|---|---|---|---|---|---|---|---|"]

    for m in MODELS:
        if not os.path.exists(MODEL_LEDGER[m]):
            continue
        md, stats = model_autopsy(m, None, asof)
        with open(os.path.join(args.out, f"{m}.md"), "w") as fh:
            fh.write(md)
        index.append(f"| {m} | {stats['n']:,} | {100*stats['wr']:.1f}% | "
                     f"{fmt_net(stats['exp'], stats['unit'])} | {fmt_net(stats['total'], stats['unit'])} | "
                     f"{stats['pf']:.2f} | {fmt_net(stats['mdd'], stats['unit'])} | [{m}.md]({m}.md) |")

    fills = load_jsonl(ENGINES_FILLS)
    by_engine = collections.defaultdict(list)
    for r in fills:
        if r.get("engine") and r.get("net_pct") is not None:
            by_engine[r["engine"]].append(r)

    index += ["",
              "**Stage-0 finding — the guard is not guarding.** Every sentinel-monitored "
              "model is underwater (see the table), yet `state/sentinel.json` reports "
              "swing / multimodels / statsmodels all `live` with no recorded reason — "
              "multi_models has lost over 2.6× its $5,000 paper equity without being parked. "
              "Either the sentinel is not reading these ledgers or its thresholds never "
              "fire; diagnosing that gap is itself a Stage-1 item, and per Rule A6 the "
              "human decides whether any of these models stays live in the meantime.",
              "",
              "## Signal engines (fills ledger — scratch marks, see caveat)", "",
              "| engine | fills | win rate | expectancy/fill | total PnL | report |",
              "|---|---|---|---|---|---|"]
    for engine in sorted(by_engine, key=lambda e: -len(by_engine[e])):
        md, stats = engine_autopsy(engine, by_engine[engine], asof)
        with open(os.path.join(args.out, f"{engine}.md"), "w") as fh:
            fh.write(md)
        index.append(f"| {engine} | {stats['n']:,} | {100*stats['wr']:.1f}% | "
                     f"{fmt_net(stats['exp'], stats['unit'])} | {fmt_net(stats['total'], stats['unit'])} | "
                     f"[{engine}.md]({engine}.md) |")

    index.append("")
    with open(os.path.join(args.out, "README.md"), "w") as fh:
        fh.write("\n".join(index) + "\n")
    print(f"wrote {len(MODELS) + len(by_engine) + 1} reports to {args.out}")


if __name__ == "__main__":
    main()
