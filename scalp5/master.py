#!/usr/bin/env python3
"""ONE dashboard. Every engine, one page, no port hopping.

It reads each engine's state directly and renders them together:

    turtle (Go)      :8787/api/turtle + /stats
    swing            :8790/api/state
    five adaptive    :8797/api/state
    turtle x5 fam    :8798/api/state
    model aggregate  :8792/api/aggregate

Anything offline is shown as offline rather than silently dropped.

    python3 master.py --port 8799   ->  http://127.0.0.1:8799
"""

import argparse
import json
import os
import sys
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))

SOURCES = [
    ("turtle", "Go · Donchian/2N", "#a371f7"),
    ("swing", "alerts · model gate", "#3fb950"),
    ("multimodels", "5 adaptive TA models", "#f778ba"),
    ("turtlex5", "Turtle 10s-2m", "#58a6ff"),
    ("hft", "RSI/MACD 1.5s", "#d29922"),
]


_META = {"at": 0.0, "rows": {}}


def market_meta():
    """price · volatility · liquidity · volume · market cap for every pair.

    Volume/price/range come from the Go universe (free, already streaming).
    Market cap comes from CoinGecko, batched and cached for 10 minutes.
    """
    now = time.time()
    if _META["rows"] and now - _META["at"] < 600:
        return _META["rows"]
    rows = {}
    try:
        d = jget("http://127.0.0.1:8787/api/universe", timeout=8.0)
        for r in d.get("symbols") or []:
            sym = r.get("symbol") or ""
            if not sym.endswith("USDT"):
                continue
            px = float(r.get("last_price") or 0)
            hi = float(r.get("high_price") or 0)
            lo = float(r.get("low_price") or 0)
            qv = float(r.get("quote_volume_24h") or 0)
            rows[sym] = {
                "price": px,
                "vol_pct": (100.0 * (hi - lo) / px) if px else None,
                "liquidity": qv,
                "volume": float(r.get("volume_24h") or 0),
                "change": float(r.get("change_24h_pct") or 0),
                "mcap": None,
            }
    except Exception:
        pass
    # market caps from the ranked markets endpoint: one list, biggest first,
    # so a symbol collision always resolves to the major coin
    try:
        caps = {}
        for page in (1, 2):
            lst = jget("https://api.coingecko.com/api/v3/coins/markets"
                       "?vs_currency=usd&order=market_cap_desc&per_page=250"
                       "&page=%d&sparkline=false" % page, timeout=20.0)
            for c in lst:
                sym = (c.get("symbol") or "").upper()
                if sym and sym not in caps and c.get("market_cap"):
                    caps[sym] = float(c["market_cap"])
        for sym, m in rows.items():
            m["mcap"] = caps.get(sym[:-4])
    except Exception:
        pass
    _META["rows"] = rows
    _META["at"] = now
    return rows


def jget(url, timeout=3.0):
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "master/1.0"}),
                                timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch(paths, base="http://127.0.0.1"):
    out = {}
    for name, url in paths:
        try:
            out[name] = jget(base + url)
        except Exception:
            out[name] = None
    return out


def collect():
    engines = []

    # ---- turtle (Go backend)
    try:
        t = jget("http://127.0.0.1:8787/api/turtle")
        s = jget("http://127.0.0.1:8787/api/turtle/stats")
        engine = {
            "key": "turtle", "label": "Go · Donchian/2N", "colour": "#a371f7", "alive": True,
            "equity": t.get("equity"), "return_pct": t.get("return_pct"),
            "realised": t.get("realized_pnl"), "unrealised": t.get("unrealized_pnl"),
            "closed": s.get("closed"), "wins": None,
            "win_rate": s.get("win_rate_pct"),
            "positions": [{"symbol": p.get("symbol"), "side": p.get("side", "?"),
                           "gross_pct": 0, "held_s": 0}
                          for p in (t.get("positions") or {}).values()]
            if isinstance(t.get("positions"), dict) else [],
            "recent": [], "scans": None, "settings": {}, "funnel": {},
        }
    except Exception:
        engine = {"key": "turtle", "label": "Go · Donchian/2N", "colour": "#a371f7",
                  "alive": False, "equity": None, "return_pct": None, "realised": None,
                  "unrealised": None, "closed": None, "wins": None, "win_rate": None,
                  "positions": [], "recent": []}
    try:
        gc = jget("http://127.0.0.1:8787/api/turtle/config")["config"]
        engine["settings"] = {
            "risk_pct": round(float(gc.get("risk_pct", 0)) * 100, 3),
            "max_positions": gc.get("max_units_total"),
            "s1_entry": gc.get("s1_entry"), "s1_exit": gc.get("s1_exit"),
            "s2_entry": gc.get("s2_entry"), "s2_exit": gc.get("s2_exit"),
            "stop_n": gc.get("stop_n"), "n_lookback": gc.get("n_lookback"),
            "max_units_per_direction": gc.get("max_units_per_direction"),
            "max_units_per_market": gc.get("max_units_per_market"),
            "vol_veto_pct": gc.get("vol_veto_pct"),
        }
        engine["max_pos"] = gc.get("max_units_total")
    except Exception:
        pass
    engines.append(engine)

    # ---- single-state engines
    for key, label, colour, fname in (
        ("swing", "alerts · model gate", "#3fb950", "swing_state.json"),
        ("multimodels", "5 adaptive TA models", "#f778ba", "multi_models.json"),
        ("statsmodels", "OLS · z · logistic · autocorr", "#39d353", "stats_models.json"),
        ("swing_turtle", "Donchian 15m", "#a371f7", "swing_turtle.json"),
    ):
        try:
            _p = os.path.join(HERE, "state", fname)
            if (time.time() - os.path.getmtime(_p)) > 180:
                raise IOError("stale state file")
            with open(_p) as fh:
                d = json.load(fh)
            engines.append({
                "key": key, "label": label, "colour": colour, "alive": not d.get("missing"),
                "equity": d.get("equity"), "return_pct": d.get("return_pct"),
                "realised": d.get("realised", (d.get("equity", 0) - d.get("start_equity", 0))
                                  if d.get("equity") is not None else None),
                "unrealised": d.get("unrealised", round(sum(
                    (p.get("notional") or 0) * p.get("gross_pct", 0) / 100
                    for p in (d.get("positions") or [])), 2)),
                "closed": d.get("trade_count"), "wins": d.get("wins"), "win_rate": None,
                "positions": d.get("positions") or [],
                "recent": [{"symbol": t.get("symbol"), "side": t.get("side"),
                            "net_pct": t.get("net_pct"), "reason": t.get("reason")}
                           for t in (d.get("trades") or [])[-5:]],
                "trades": (d.get("trades") or [])[-80:],
                "risk_usd": d.get("open_risk_usd"),
                "max_pos": d.get("max_positions"),
                "tp": d.get("tp_pct"), "sl": d.get("sl_pct"),
                "basis": d.get("basis"), "start_equity": d.get("start_equity"),
                "scans": d.get("scans"), "settings": d.get("settings") or {},
                "funnel": d.get("funnel") or {},
                "config": {k: d.get(k) for k in
                           ("lock_trade_pct", "lock_usd", "trail_ratio",
                            "trail_arm_pct", "max_hold", "cycle_seconds",
                            "model_gate", "min_liquidity", "risk_pct")},
            })
        except Exception:
            engines.append({"key": key, "label": label, "colour": colour, "alive": False,
                            "equity": None, "return_pct": None, "realised": None,
                            "unrealised": None, "closed": None, "wins": None,
                            "win_rate": None, "positions": [], "recent": []})

    # ---- the five Turtle timeframes
    try:
        d = jget("http://127.0.0.1:8798/api/state")
        for e in d.get("engines") or []:
            engines.append({
                "key": e.get("name"), "label": "Turtle " + str(e.get("interval")),
                "colour": "#58a6ff", "alive": True,
                "equity": e.get("equity"), "return_pct": e.get("return_pct"),
                "realised": e.get("realised"), "unrealised": e.get("unrealised"),
                "closed": e.get("closed"), "wins": e.get("wins"), "win_rate": None,
                "positions": e.get("open") or [],
                "recent": e.get("recent") or [],
                "trades": e.get("recent") or [],
                "risk_usd": None, "max_pos": None, "tp": None, "sl": None,
                "basis": "Turtle " + str(e.get("interval")), "start_equity": 2000,
                "settings": e.get("settings") or {},
                "funnel": e.get("funnel") or {},
                "scans": e.get("scans"),
                "config": {},
            })
    except Exception:
        pass

    # ---- model aggregate (not an engine, but the signal source)
    models = None
    try:
        models = jget("http://127.0.0.1:8792/api/aggregate")
    except Exception:
        models = None

    return {"ts": int(time.time() * 1000), "engines": engines, "models": models}


PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>All engines</title><style>
 :root{--bg:#080b10;--card:#111823;--line:#1e2733;--fg:#e8eef6;--dim:#8a99ad;
       --faint:#59677a;--up:#3fb950;--down:#f85149;--mono:ui-monospace,Menlo,monospace}
 *{box-sizing:border-box}
 body{margin:0;background:var(--bg);color:var(--fg);
      font:13px/1.45 -apple-system,BlinkMacSystemFont,var(--mono)}
 .num{font-family:var(--mono);font-variant-numeric:tabular-nums}
 header{padding:13px 20px;background:#0b1119;border-bottom:1px solid var(--line);
        display:flex;align-items:center;gap:14px;flex-wrap:wrap}
 h1{margin:0;font-size:12px;letter-spacing:.2em;text-transform:uppercase;color:var(--dim)}
 .pill{padding:3px 10px;border-radius:999px;border:1px solid var(--line);font-size:11px;color:var(--dim)}
 .pill.ok{color:var(--up);border-color:#1d3a26;background:#0e2416}
 .pill.bad{color:var(--down);border-color:#3a1d1d;background:#240f0f}
 .spacer{flex:1}
 .totals{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:1px;
         background:var(--line);border-bottom:1px solid var(--line)}
 .kpi{background:#0d141d;padding:10px 20px}
 .kpi .k{font-size:10px;letter-spacing:.12em;text-transform:uppercase;color:var(--faint)}
 .kpi .v{font-size:17px;margin-top:2px;font-family:var(--mono);font-variant-numeric:tabular-nums}
 .wrap{max-width:1560px;margin:0 auto;padding:0 20px 28px}
 .section{margin-top:22px}
 .section > h2{margin:0 0 10px;font-size:11px;letter-spacing:.16em;text-transform:uppercase;
               color:var(--dim);display:flex;align-items:center;gap:10px}
 .section > h2:after{content:"";flex:1;height:1px;background:var(--line)}
 .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:14px}
 .card{min-height:0;max-height:520px;overflow:auto}
 .card{background:var(--card);border:1px solid var(--line);border-radius:12px;
       padding:14px 16px;display:flex;flex-direction:column;min-width:0}
 .card.off{opacity:.45}
 .top{display:flex;justify-content:space-between;align-items:baseline;gap:8px}
 .name{font-size:12px;letter-spacing:.1em;text-transform:uppercase;font-weight:700}
 .label{font-size:10.5px;color:var(--faint);margin-top:1px}
 .ret{font-size:21px;font-family:var(--mono);font-variant-numeric:tabular-nums;line-height:1.1}
 .card .body{margin-top:8px;flex:1;min-height:0}
 .up{color:var(--up)}.down{color:var(--down)}.flat{color:var(--dim)}
 .rows{display:flex;gap:14px;margin-top:8px;font-size:11px;color:var(--dim);flex-wrap:wrap}
 .rows b{color:var(--fg);font-family:var(--mono);font-weight:600}
 table{width:100%;border-collapse:collapse;font-family:var(--mono);font-size:11.5px;margin-top:9px}
 th{text-align:right;color:var(--faint);font-size:9px;letter-spacing:.1em;text-transform:uppercase;
    padding-bottom:3px;font-weight:600}
 th:first-child{text-align:left}
 td{text-align:right;padding:3px 0;border-top:1px solid #151d27}
 td:first-child{text-align:left;color:#58a6ff}
 .side{font-size:9.5px;padding:0 4px;border-radius:3px}
 .side.buy{color:var(--up);background:#0e2416}
 .side.sell{color:var(--down);background:#240f0f}
 .empty{color:var(--faint);font-size:11px;margin-top:8px}
 footer{padding:0 20px 20px;color:var(--faint);font-size:11px;font-family:var(--mono)}

  /* ---------- detail sheet ---------- */
  .modal{position:fixed;inset:0;background:#04070bd9;backdrop-filter:blur(4px);
         display:none;z-index:60;padding:26px;overflow:auto}
  .modal.open{display:block}
  .sheet{max-width:1240px;margin:0 auto;border:1px solid #2a3a4a;border-radius:18px;
         background:linear-gradient(170deg,#131b26,#0d141d);overflow:hidden;
         box-shadow:0 40px 100px -40px #000}
  .modal.open .sheet{animation:rise .18s ease-out}
  @keyframes rise{from{opacity:0;transform:translateY(12px) scale(.99)}to{opacity:1;transform:none}}

  .sheet .head{position:sticky;top:0;z-index:5;padding:18px 28px 16px;
               background:linear-gradient(120deg,#111b27,#17222f);
               border-bottom:1px solid #24313e}
  .sheet .head h2{margin:0;font-size:21px;font-weight:700;letter-spacing:.02em}
  .sheet .head .label{font-size:11.5px;color:#7d8b9e}
  .sheet .head .sub{margin-top:3px;font-size:11px;color:#59677a;letter-spacing:.06em}
  .sheet .head .big{position:absolute;right:66px;top:16px;font-size:34px;line-height:1;
                    font-family:var(--mono);font-variant-numeric:tabular-nums;letter-spacing:-.02em}
  .close2{position:absolute;right:20px;top:16px;width:30px;height:30px;border-radius:50%;
          border:1px solid #2a3a4a;background:#0c131b;color:#8494a8;cursor:pointer;
          display:flex;align-items:center;justify-content:center;font-size:16px}
  .close2:hover{color:#e8eef6;border-color:#3d5470}

  .body2{padding:18px 28px 26px;max-height:calc(100vh - 150px);overflow:auto}

  .tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(126px,1fr));gap:10px}
  .tile{background:#0f1721;border:1px solid #1d2836;border-radius:11px;padding:10px 13px}
  .tile .k{font-size:9.5px;letter-spacing:.13em;text-transform:uppercase;color:#59677a}
  .tile .v{font-size:17px;font-family:var(--mono);margin-top:3px;
           font-variant-numeric:tabular-nums;color:#e8eef6}

  .expo{margin-top:12px;background:#0f1721;border:1px solid #1d2836;border-radius:11px;
        padding:11px 14px}
  .expo .lbl{display:flex;justify-content:space-between;font-size:10.5px;color:#8494a8;
             letter-spacing:.06em}
  .expo .track{height:9px;background:#18202b;border-radius:6px;margin-top:8px;overflow:hidden}
  .expo .track i{display:block;height:100%;border-radius:6px;
                 background:linear-gradient(90deg,#3fb950,#d29922 60%,#f85149)}

  .cols{display:grid;grid-template-columns:minmax(0,1.05fr) minmax(0,1fr);gap:22px;margin-top:4px}
  @media(max-width:1000px){.cols{grid-template-columns:1fr}}
  .col{min-width:0}

  .hd{display:flex;align-items:center;gap:10px;margin:20px 0 10px}
  .hd h3{margin:0;font-size:10.5px;letter-spacing:.16em;text-transform:uppercase;color:#8494a8}
  .hd .count{font-size:10.5px;color:#59677a;background:#131c26;border:1px solid #1e2733;
             border-radius:999px;padding:1px 8px}
  .hd:after{content:"";flex:1;height:1px;background:#1e2733}

  .pgrid{display:grid;gap:9px}
  .pos{background:#0f1721;border:1px solid #1d2836;border-radius:11px;padding:10px 13px;
       display:grid;grid-template-columns:1fr auto;gap:3px 14px;align-items:center;
       transition:.12s}
  .pos:hover{border-color:#2f4457;background:#111b26}
  .pos .l1{display:flex;align-items:center;gap:9px;font-family:var(--mono);font-size:13.5px}
  .pos .px{color:#59677a;font-size:10.5px;font-family:var(--mono)}
  .pos .gauge{grid-column:1/-1;height:5px;background:#18202b;border-radius:4px;position:relative;margin-top:4px}
  .pos .gauge i{position:absolute;top:0;bottom:0;left:50%;border-radius:4px}
  .pos .why{grid-column:1/-1;font-size:11px;color:#8a99ad;margin-top:3px}

  .trow{display:grid;grid-template-columns:minmax(90px,1fr) 52px minmax(70px,1.1fr) 76px 44px;
        gap:9px;align-items:center;padding:7px 11px;border-radius:8px;
        font-family:var(--mono);font-size:11.5px}
  .trow:nth-child(odd){background:#0f1721}
  .reason{font-size:10px;padding:1px 7px;border-radius:999px;background:#131c26;
          border:1px solid #1e2733;color:#8494a8;justify-self:start;
          overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:100%}

  .side{font-size:9.5px;padding:1px 6px;border-radius:4px;letter-spacing:.06em}
  .side.buy{color:#3fb950;background:#0e2416;border:1px solid #1d3a26}
  .side.sell{color:#f85149;background:#240f0f;border:1px solid #3a1d1d}
  .tabs{display:flex;gap:6px}
  .tab2{padding:6px 16px;border-radius:9px;border:1px solid transparent;cursor:pointer;
        font:inherit;font-size:11.5px;letter-spacing:.08em;color:#8494a8;background:transparent}
  .tab2:hover{color:#e8eef6;background:#131c26}
  .tab2.on{color:#e8eef6;background:#16202b;border-color:#2a3a4a}
  #market{display:none}
  #market.on{display:block}
  #why{display:none}
  #why.on{display:block}
  #review{display:none}
  #review.on{display:block}
  #control{display:none}
  #control.on{display:block}
  #lb{display:none}
  #lb.on{display:block}
  .lbtable{width:100%;border-collapse:collapse;font-family:var(--mono);font-size:12px}
  .lbtable th{text-align:right;color:#59677a;font-size:9px;letter-spacing:.1em;
              text-transform:uppercase;padding:0 10px 8px;white-space:nowrap;font-weight:600}
  .lbtable th:nth-child(2){text-align:left}
  .lbtable td{text-align:right;padding:10px;border-top:1px solid #171f29;white-space:nowrap}
  .lbtable td:nth-child(2){text-align:left}
  .lbtable tr.top1 td{background:#132a1c}
  .lbtable tr.top2 td{background:#16232b}
  .lbtable tr.top3 td{background:#231d16}
  .lbbar{height:6px;border-radius:3px;background:#18202b;overflow:hidden;min-width:90px}
  .lbbar i{display:block;height:100%;border-radius:3px}
  .rank{display:inline-block;width:22px;height:22px;line-height:22px;text-align:center;
        border-radius:6px;background:#131c26;color:#8494a8;font-size:11px}
  .rank.gold{background:#3d3410;color:#f0c44b}
  .rank.silver{background:#2b3238;color:#c8d3dd}
  .rank.bronze{background:#3a2a1c;color:#d79a63}
  .cscope{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}
  .cpanel{background:#0f1721;border:1px solid #1d2836;border-radius:14px;padding:4px 18px 16px;
          margin-top:14px}
  .cfoot{display:flex;align-items:center;gap:12px;margin-top:16px;flex-wrap:wrap}
  #dash.off{display:none}
  .mhead{display:flex;align-items:center;gap:14px;flex-wrap:wrap;padding:16px 0 4px}
  .mhead .sym{font-family:var(--mono);font-size:23px;font-weight:700;color:#58a6ff;letter-spacing:.02em}
  .mhead .px{font-family:var(--mono);font-size:23px;font-variant-numeric:tabular-nums}
  .mhead .chg{font-family:var(--mono);font-size:14px}
  .sympick{background:#0f1721;border:1px solid #2a3a4a;border-radius:8px;color:#e8eef6;
           padding:6px 11px;font:inherit;font-size:12px;font-family:var(--mono);width:130px}
  .chartcard{background:linear-gradient(180deg,#0f1721,#0b1219);border:1px solid #1e2733;
             border-radius:16px;padding:14px 16px 10px;margin-top:12px;
             box-shadow:0 24px 60px -40px #000}
  .chartcard svg{width:100%;height:440px;display:block;touch-action:none;
                 cursor:grab;user-select:none}
  .chartcard svg:active{cursor:grabbing}
  .mlegend{display:flex;gap:16px;flex-wrap:wrap;padding:8px 2px 4px;font-size:11px;color:#8494a8}
  .mlegend span{display:flex;align-items:center;gap:6px}
  .dot2{width:9px;height:9px;border-radius:3px;display:inline-block}
  .mk{font-size:10.5px;color:#59677a;font-family:var(--mono)}
  .chip{display:inline-flex;align-items:center;gap:6px;padding:5px 12px;margin:0 6px 6px 0;
        border-radius:8px;border:1px solid #1e2733;
        background:linear-gradient(180deg,#111a23,#0e161e);color:#8494a8;
        font:inherit;font-size:11px;letter-spacing:.02em;cursor:pointer;
        transition:color .16s,border-color .16s,background .16s,box-shadow .2s,transform .12s}
  .chip:hover{color:#e8eef6;border-color:#35506a;transform:translateY(-1px)}
  .chip.on{color:#dbe9f7;background:linear-gradient(180deg,#182a3b,#132231);
           border-color:#3d6f96;box-shadow:0 0 0 1px #58a6ff22,0 4px 14px -8px #58a6ff88}
  .chip.on:before{content:"";width:5px;height:5px;border-radius:50%;background:#58a6ff;
                  box-shadow:0 0 6px #58a6ff}
  .chip.armed{color:#0d1117;background:linear-gradient(180deg,#f0b429,#d29922);
              border-color:#f0b429;font-weight:600}
  .params{display:flex;flex-wrap:wrap;gap:12px;align-items:flex-end;
          background:#0f1721;border:1px solid #1d2836;border-radius:11px;padding:12px 14px}
  .params label{display:flex;flex-direction:column;gap:4px;font-size:10px;
                letter-spacing:.1em;text-transform:uppercase;color:#59677a}
  .params input{width:92px;background:#0a1017;color:#e8eef6;border:1px solid #24313e;
                border-radius:7px;padding:5px 8px;font-family:var(--mono);font-size:12px}
  .params input:focus{outline:none;border-color:#3d5470}
  .setpage{background:#0f1721;border:1px solid #1d2836;border-radius:12px;padding:4px 16px 14px}
  .sgroup{border-bottom:1px solid #182230;padding:13px 0}
  .sgroup:last-of-type{border-bottom:none}
  .sgt{font-size:10px;letter-spacing:.16em;text-transform:uppercase;color:#58a6ff;margin-bottom:9px}
  .sfields{display:grid;grid-template-columns:repeat(auto-fill,minmax(176px,1fr));gap:11px}
  .sf{display:flex;flex-direction:column;gap:3px}
  .sf span{font-size:10.5px;color:#8494a8;letter-spacing:.03em}
  .sf input{background:#0a1017;border:1px solid #24313e;border-radius:7px;color:#e8eef6;
            padding:6px 9px;font-family:var(--mono);font-size:12.5px}
  .sf input:focus{outline:none;border-color:#3d5470;box-shadow:0 0 0 3px #58a6ff22}
  .sf input:disabled{opacity:.35;cursor:not-allowed}
  .sf em{font-size:9.5px;color:#4c5a6c;font-style:normal;line-height:1.3}
  .sapply{display:flex;align-items:center;gap:12px;padding-top:13px}
  .applybtn{background:linear-gradient(180deg,#1f6feb,#1a5fd0);border:1px solid #2f81f7;
            color:#fff;border-radius:8px;padding:8px 18px;font:inherit;font-size:12px;
            font-weight:600;cursor:pointer}
  .applybtn:hover{filter:brightness(1.12)}
  .mlayout{display:grid;grid-template-columns:246px minmax(0,1fr);gap:14px;margin-top:12px;
           transition:grid-template-columns .25s ease}
  .mlayout.collapsed{grid-template-columns:0px minmax(0,1fr);gap:0}
  .mlayout.collapsed .mside{display:none}
  @media(max-width:900px){.mlayout{grid-template-columns:1fr}}
  .mside{background:#0d151e;border:1px solid #1b2632;border-radius:14px;padding:12px;
         display:flex;flex-direction:column;min-height:420px;max-height:560px}
  .msearch{width:100%;background:#0a1017;border:1px solid #24313e;border-radius:9px;
           color:#e8eef6;padding:9px 11px;font:inherit;font-size:12px;
           font-family:var(--mono);letter-spacing:.04em}
  .msearch:focus{outline:none;border-color:#3d5470;box-shadow:0 0 0 3px #58a6ff22}
  .mlist{overflow:auto;margin-top:9px;display:flex;flex-direction:column;gap:2px}
  .mrow{display:grid;grid-template-columns:1fr auto auto;gap:8px;align-items:center;
        padding:7px 9px;border-radius:8px;cursor:pointer;font-family:var(--mono);font-size:12px;
        color:#9fb0c4;border:1px solid transparent}
  .mrow:hover{background:#131e29;color:#e8eef6}
  .mrow.on{background:#16232f;border-color:#31506b;color:#e8eef6}
  .mrow .n{font-size:10px;color:#59677a}
  .mrow .dots{display:flex;gap:3px}
  .sdot{width:6px;height:6px;border-radius:2px}
  .mempty{color:#4c5a6c;font-size:11px;padding:10px 4px;text-align:center}
  .ctxpill{display:inline-flex;align-items:center;gap:7px;padding:4px 12px;border-radius:999px;
           background:#12261a;border:1px solid #2f6b3f;color:#6ee787;font-size:11px;
           letter-spacing:.05em;cursor:pointer;font-family:var(--mono)}
  .ctxpill:hover{background:#16301f}
  .ctxpill b{color:#a6f0bb}
  .gctl{background:linear-gradient(180deg,#101a24,#0c141c);border:1px solid #1d2836;
        border-radius:14px;padding:14px 18px;margin-top:16px}
  .gtitle{font-size:11px;letter-spacing:.18em;text-transform:uppercase;color:#58a6ff;
          display:flex;align-items:baseline;gap:12px}
  .gsub{font-size:10px;letter-spacing:.06em;color:#4c5a6c;text-transform:none}
  .gfields{display:grid;grid-template-columns:repeat(auto-fit,minmax(132px,1fr));gap:11px;
           margin-top:12px}
  .gf{display:flex;flex-direction:column;gap:4px}
  .gf span{font-size:9.5px;letter-spacing:.1em;text-transform:uppercase;color:#59677a}
  .gf input{background:#0a1017;border:1px solid #24313e;border-radius:7px;color:#e8eef6;
            padding:7px 9px;font-family:var(--mono);font-size:12.5px}
  .gf input:focus{outline:none;border-color:#3d5470;box-shadow:0 0 0 3px #58a6ff22}
  .grow{display:flex;align-items:center;gap:12px;margin-top:13px}
  .ghost{background:#131c26;border:1px solid #2a3a4a;color:#8494a8;border-radius:8px;
         padding:8px 15px;font:inherit;font-size:12px;cursor:pointer}
  .ghost:hover{color:#e8eef6;border-color:#3d5470}
  .gfsel{background:#0a1017;border:1px solid #24313e;border-radius:7px;color:#e8eef6;
         padding:6px 9px;font:inherit;font-size:12px;font-family:var(--mono)}
  .wide{width:100%;border-collapse:collapse;font-family:var(--mono);font-size:11.5px;
        font-variant-numeric:tabular-nums}
  .wide th{text-align:right;color:#59677a;font-size:9px;letter-spacing:.1em;
           text-transform:uppercase;font-weight:600;padding:0 9px 6px;white-space:nowrap}
  .wide th:first-child,.wide th:nth-child(2){text-align:left}
  .wide td{text-align:right;padding:7px 9px;border-top:1px solid #171f29;white-space:nowrap}
  .wide td:first-child{text-align:left;color:#58a6ff;font-weight:600}
  .wide td.why2{text-align:left;color:#7d8b9e;font-size:10.5px;white-space:normal;
                max-width:340px;line-height:1.35}
  .wide tbody tr:nth-child(even){background:#0e1620}
  .wide tbody tr:hover{background:#131e29}
  .mini{color:#59677a;font-size:10px}

  /* ---------- card hover: breathing green glow ---------- */
  .card{cursor:pointer;
        transition:border-color .5s ease, box-shadow .5s ease, transform .2s ease}
  .card:hover{border-color:#3fb950 !important;transform:translateY(-2px);
              animation:breathe 2.6s ease-in-out infinite}
  @keyframes breathe{
    0%   {box-shadow:0 0 0 1px #3fb95044, 0 0 12px -8px #3fb95055}
    45%  {box-shadow:0 0 0 1px #3fb950bb, 0 0 34px -2px #3fb950dd}
    100% {box-shadow:0 0 0 1px #3fb95044, 0 0 12px -8px #3fb95055}
  }
  .ctxpill{display:inline-flex;align-items:center;gap:7px;padding:4px 12px;border-radius:999px;
           background:#12261a;border:1px solid #2f6b3f;color:#6ee787;font-size:11px;
           letter-spacing:.05em;cursor:pointer;font-family:var(--mono)}
  .ctxpill:hover{background:#16301f}
  .ctxpill b{color:#a6f0bb}

  /* ---------- clickable tickers ---------- */
  .tick{display:inline-block;cursor:pointer;padding:1px 7px;margin:-1px -7px;
        border-radius:6px;border:1px solid transparent;
        text-decoration:underline dotted;text-underline-offset:2px;
        transition:background .2s ease,border-color .2s ease,color .2s ease,
                   box-shadow .25s ease,text-decoration-color .2s ease}
  .tick:hover{background:#12222f;border-color:#3d5470;color:#9ecbff;
              text-decoration-color:transparent;
              box-shadow:0 0 0 1px #58a6ff33, 0 0 16px -6px #58a6ffbb}
  .tick:active{background:#16303f;border-color:#58a6ff}
  .tick:hover::after{content:" ↗";opacity:.7;font-size:.85em}
  .trl .tick,.wide .tick{font-weight:600}

  .panel{background:#0f1721;border:1px solid #1d2836;border-radius:12px;padding:14px 16px}
  .dgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:12px}
  .dna{background:#0f1721;border:1px solid #1d2836;border-radius:12px;padding:12px 14px}
  .dhead{display:flex;align-items:center;gap:9px;font-family:var(--mono);font-size:12.5px}
  .dfacts{display:flex;flex-wrap:wrap;gap:12px;margin-top:8px;font-size:10.5px;color:#8494a8;
          font-family:var(--mono)}
  .dfacts b{color:#e8eef6}
  .dwhy{margin-top:7px;font-size:11px;color:#8a99ad}
  .attr{margin-top:9px;display:flex;flex-direction:column;gap:5px}
  .arow{display:grid;grid-template-columns:130px 1fr 48px;gap:8px;align-items:center;
        font-family:var(--mono);font-size:10.5px;color:#8494a8}
  .abar{height:7px;background:#18202b;border-radius:4px;overflow:hidden}
  .abar i{display:block;height:100%;border-radius:4px}
  .aval{text-align:right}
  .rrow{display:grid;grid-template-columns:110px 1fr;gap:10px;align-items:center;margin-bottom:4px}
  .rlab{font-family:var(--mono);font-size:10.5px;color:#8494a8;text-align:right}
  .rcells{display:grid;grid-auto-flow:column;gap:2px;height:16px}
  .rcells i{display:block;height:16px;border-radius:2px;min-width:3px}
  .skrow{display:grid;grid-template-columns:110px 1fr 80px 90px;gap:10px;align-items:center;
         margin-bottom:7px;font-family:var(--mono);font-size:11px}
  .sklab{color:#8494a8;text-align:right}
  .skbar{height:9px;background:#18202b;border-radius:5px;overflow:hidden}
  .skbar i{display:block;height:100%;border-radius:5px}
  .skval{text-align:right}
  .skmeta{color:#59677a;font-size:10px;text-align:right}
  .fgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:16px}
  .fcard{min-width:0}
  .fhead{font-size:11px;letter-spacing:.1em;text-transform:uppercase;margin-bottom:8px;
         font-weight:700}

  .rcard{background:#0f1721;border:1px solid #1d2836;border-radius:14px;padding:16px 18px;
         margin-top:16px}
  .rchead{display:flex;align-items:baseline;gap:12px;font-size:13px;font-weight:700;
          letter-spacing:.08em;text-transform:uppercase}
  .score{display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:10px;
         margin:12px 0}
  .sbox{background:#0b131b;border:1px solid #1a2531;border-radius:10px;padding:9px 12px}
  .sbox b{display:block;font-family:var(--mono);font-size:17px;color:#e8eef6}
  .sbox span{font-size:9.5px;letter-spacing:.1em;text-transform:uppercase;color:#59677a}
  .rcols{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:18px}
  .ccol{min-width:0}
  .ctitle{font-size:10px;letter-spacing:.14em;text-transform:uppercase;color:#58a6ff;
          margin-bottom:8px}
  .wgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:11px;
         margin-top:8px}

  #market{position:relative}
  .popover{display:none;position:absolute;right:0;top:52px;z-index:45;width:min(700px,96vw);
           background:linear-gradient(180deg,#101923,#0b1219);
           border:1px solid #22303d;border-radius:16px;
           box-shadow:0 30px 70px -30px #000,0 0 0 1px #ffffff08 inset}
  .popover.open{display:block;animation:rise .16s ease-out}
  .phead{display:flex;align-items:center;gap:10px;padding:13px 18px 11px;
         border-bottom:1px solid #1b2836}
  .phead b{font-size:11px;letter-spacing:.16em;text-transform:uppercase;color:#8494a8}
  .pbody{padding:14px 18px 6px}
  .pfoot{padding:10px 18px 14px;border-top:1px solid #1b2836;font-size:10.5px;
         color:#4c5a6c;line-height:1.7}
  .pfoot b{color:#7d8b9e;font-weight:600}
  .popover.open{display:block}
  .pgrid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:16px}
  .pgrp{min-width:0}
  .prow{display:grid;grid-template-columns:96px 1fr;gap:14px;align-items:start;
        padding:10px 0;border-bottom:1px dashed #17222e}
  .prow:last-child{border-bottom:none}
  .plab{font-size:9.5px;letter-spacing:.15em;text-transform:uppercase;color:#59677a;
        padding-top:7px}
  .pctl{min-width:0}
  .pbody{max-height:min(70vh,560px);overflow:auto}
  .pglabel{font-size:9.5px;letter-spacing:.14em;text-transform:uppercase;color:#59677a;
           margin-bottom:7px}

  #chartmodal{position:fixed;inset:0;background:#04070bd9;backdrop-filter:blur(4px);
              display:none;z-index:70;padding:18px;overflow:auto}
  #chartmodal.open{display:block}
  .cmsheet{max-width:1500px;margin:0 auto;background:#0b1119;border:1px solid #24313e;
           border-radius:14px;overflow:hidden;box-shadow:0 40px 100px -40px #000}
  .cmhead{display:flex;align-items:center;gap:12px;padding:9px 14px;background:#101923;
          border-bottom:1px solid #1b2836}
  .cmhead b{font-size:10.5px;letter-spacing:.16em;text-transform:uppercase;color:#8494a8}
</style></head><body>
<header><h1>All engines</h1>
  <div class="tabs">
    <button class="tab2 on" id="tab-dash" onclick="showTab('dash')">Dashboard</button>
    <button class="tab2" id="tab-market" onclick="showTab('market')">Market chart</button>
    <button class="tab2" id="tab-why" onclick="showTab('why')">Why</button>
    <button class="tab2" id="tab-review" onclick="showTab('review')">Review</button>
    <button class="tab2" id="tab-control" onclick="showTab('control')">Control</button>
    <button class="tab2" id="tab-lb" onclick="showTab('lb')">Leaderboard</button>
  </div>
  <span class="pill ok" id="live">—</span>
  <span class="pill" id="hz" title="service health">health —</span>
  <span class="pill" id="models">—</span>
  <span class="spacer"></span>
  <span class="pill" id="clock">—</span>
</header>
<div id="err" style="display:none;background:#3a1d1d;border-bottom:1px solid #f85149;
     color:#ffb3ad;padding:8px 16px;font-size:12px;font-family:ui-monospace,monospace"></div>
<div class="wrap">
  <div class="totals" id="totals" style="margin:0 -20px 0"></div>
  <div id="dash">
    <div class="gctl">
      <div class="gtitle">Global controls
        <span class="gsub">one click sets every model</span></div>
      <div class="gfields">
        <label class="gf"><span>risk / trade %</span>
          <input id="g-risk" type="number" step="0.1" min="0.05" max="15" placeholder="—"></label>
        <label class="gf"><span>take profit %</span>
          <input id="g-tp" type="number" step="0.1" min="0.2" max="10" placeholder="—"></label>
        <label class="gf"><span>stop %</span>
          <input id="g-sl" type="number" step="0.1" min="0.2" max="8" placeholder="—"></label>
        <label class="gf"><span>max positions</span>
          <input id="g-max" type="number" step="1" min="1" max="40" placeholder="—"></label>
        <label class="gf"><span>book risk ceiling %</span>
          <input id="g-ceil" type="number" step="1" min="5" max="100" placeholder="—"></label>
        <label class="gf"><span>scan cadence s</span>
          <input id="g-scan" type="number" step="0.5" min="0.5" max="600" placeholder="—"></label>
        <label class="gf"><span>trail ratio</span>
          <input id="g-trail" type="number" step="0.05" min="0" max="1" placeholder="—"></label>
        <label class="gf"><span>max hold s</span>
          <input id="g-hold" type="number" step="10" min="0" max="86400" placeholder="—"></label>
        <label class="gf"><span>min liquidity $</span>
          <input id="g-liq" type="number" step="1000000" min="0" max="5000000000" placeholder="—"></label>
        <label class="gf"><span>hold (turtles) s</span>
          <input id="g-hs" type="number" step="10" min="5" max="3600" placeholder="—"></label>
      </div>
      <div class="grow" style="margin-top:10px">
        <span class="gsub" style="margin-right:4px">chart default</span>
        <select id="g-iv" class="gfsel" onchange="setDefaultIv(this.value)">
          <option value="1m">1m</option><option value="5m" selected>5m</option>
          <option value="15m">15m</option><option value="1h">1h</option>
          <option value="4h">4h</option><option value="1d">1d</option>
        </select>
        <span class="mk">applies to every chart you open</span>
      </div>
      <div class="grow">
        <button class="applybtn" onclick="applyAll()">Apply to all models</button>
        <button class="ghost" onclick="forceAll()">Force rescan all</button>
        <span class="mk" id="gstat"></span>
      </div>
    </div>
    <div id="sections"></div>
  </div>
  <div id="chartmodal" onclick="if(event.target===this)closeChartModal()">
    <div class="cmsheet">
      <div class="cmhead"><b>chart</b><span class="mk" id="cmtitle"></span>
        <span style="flex:1"></span>
        <button class="ghost" onclick="closeChartModal()">close</button></div>
      <iframe id="chartpop" style="width:100%;height:calc(90vh - 46px);border:0;
              border-radius:0 0 14px 14px;display:block;background:#0b0f14"></iframe>
    </div>
  </div>
  <div id="market">
    <iframe id="chartframe" src="/chart"
            style="width:100%;height:calc(100vh - 130px);min-height:520px;border:0;
                   border-radius:12px;display:block;background:#0b0f14"></iframe>
  </div>

  <div id="lb">
    <div class="mhead"><span class="sym">Leaderboard</span>
      <span class="mk">ranked by return on equity</span>
      <span style="flex:1"></span><span class="mk" id="lbn"></span></div>
    <div id="lbbody"></div>
  </div>
  <div id="control">
    <div class="mhead"><span class="sym">Control</span>
      <span class="mk">one panel · every property · every model</span>
      <span style="flex:1"></span><span class="mk" id="cstat"></span></div>
    <div class="cscope" id="cscope"></div>
    <div id="cbody"></div>
  </div>
  <div id="review">
    <div class="mhead"><span class="sym">Model review</span>
      <span class="mk">process, not just P&amp;L</span>
      <span style="flex:1"></span><span class="mk" id="rnote"></span></div>
    <div id="rbody"></div>
  </div>
  <div id="why">
    <div class="mhead">
      <span class="sym" id="wsym">—</span>
      <span class="mk">decision explainability</span>
      <span style="flex:1"></span>
      <span class="mk" id="wnote"></span>
    </div>
    <div id="wbody"></div>
  </div>
  <div class="section">
    <h2>Signal source — model aggregate</h2>
    <div class="card" id="modelspanel" style="max-height:none">
      <div class="empty">loading…</div>
    </div>
  </div>
</div>
<div id="chartbox" onclick="if(event.target===this)closeChart()">
  <div class="sheet">
    <span class="close" onclick="closeChart()">×</span>
    <h2 id="charttitle" style="color:#58a6ff">—</h2>
    <div class="label" id="chartsub">live market</div>
    <div class="ivs" id="ivs"></div>
    <svg id="chartsvg" viewBox="0 0 1000 360" preserveAspectRatio="none"></svg>
    <div class="label" id="chartfoot" style="margin-top:8px">—</div>
  </div>
</div>
<div class="modal" id="modal" onclick="if(event.target===this)closeModal()">
  <div class="sheet" id="sheet"></div>
</div>
<footer id="foot">—</footer>
<script>
const $=i=>document.getElementById(i);
const esc=s=>String(s??"").replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
const pct=v=>(v==null?"—":(v>=0?"+":"")+Number(v).toFixed(3)+"%");
const money=v=>(v==null?"—":Number(v).toLocaleString(undefined,
  {minimumFractionDigits:2,maximumFractionDigits:2}));
const cls=v=>(v==null?"flat":(v>0?"up":v<0?"down":"flat"));
let LAST=null;
const PREV={};
let MKT=new Map();
let TICKERS=[];


/* ============ tabs ============ */
function renderControl(){
  var ctl=(LAST||[]);
  if(!$("cbody")) return;
  $("cbody").innerHTML = ctl.map(function(e){
    var st=e.settings||{};
    var keys=Object.keys(st);
    if(!keys.length) return "";
    return '<div class="panel" style="margin-top:12px">'+
      '<div class="rchead"><span style="color:'+e.colour+'">'+esc(e.key)+'</span>'+
      '<span class="mk">'+keys.length+' rules</span></div>'+
      '<div class="setpage" style="margin-top:8px"><div class="sgroup"><div class="sfields">'+
      keys.map(function(k){
        return '<label class="sf"><span>'+esc(k.replace(/_/g," "))+'</span>'+
               '<input id="ctl-'+esc(e.key)+'-'+esc(k)+'" value="'+esc(st[k])+'"></label>';
      }).join("")+
      '</div></div><div class="sapply">'+
      '<button class="applybtn" onclick="applyEngineSettings(\''+esc(e.key)+'\')">apply</button>'+
      '<span class="mk" id="cstat-'+esc(e.key)+'"></span></div></div></div>';
  }).join("") || '<div class="panel"><div class="empty">no engines reporting settings</div></div>';
}
async function applyEngineSettings(key){
  var e=(LAST||[]).find(function(x){ return x.key===key; });
  if(!e) return;
  var body={};
  Object.keys(e.settings||{}).forEach(function(k){
    var el=$("ctl-"+key+"-"+k);
    if(el && el.value!=="" && !isNaN(parseFloat(el.value))) body[k]=parseFloat(el.value);
  });
  var st=$("cstat-"+key);
  try{
    var r=await fetch("/api/engine-control?engine="+encodeURIComponent(key),
      {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    var d=await r.json();
    if(st) st.textContent = r.ok ? ("✓ applied "+d.applied_count+" rules") : ("rejected: "+(d.error||r.status));
  }catch(err){ if(st) st.textContent="failed"; }
}
function showTab(which){
  ["dash","market","why","review","control","lb"].forEach(function(k){
    var el=$(k); if(!el) return;
    if(k==="dash"){
      el.classList.toggle("off", which!=="dash");   // dash hides with .off
    } else {
      el.classList.toggle("on", which===k);         // others show with .on
    }
    var b=$("tab-"+k); if(b) b.classList.toggle("on", which===k);
  });
  try{ if(which==="why") renderWhy(); }catch(e){ tabErr("why",e); }
  try{ if(which==="review") renderReview(); }catch(e){ tabErr("review",e); }
  try{ if(which==="control") renderControl(); }catch(e){ tabErr("control",e); }
  try{ if(which==="lb") renderLeaderboard(); }catch(e){ tabErr("lb",e); }
}


/* ============ market chart ============ */
const MIV=["1m","5m","15m","1h","4h","1d"];
/* the chart lives in its own page (/chart); these keep the shell intact */
let M={iv:"5m", engine:null, context:null};
let LASTROWS=[], GEO=null;
function openChart(sym, engine){
  var u="/chart?sym="+encodeURIComponent(sym||"BTCUSDT")+"&context=1"+
        (engine?("&engine="+encodeURIComponent(engine)):"");
  var pop=$("chartpop");
  if(pop) pop.src=u;
  var title=$("cmtitle");
  if(title) title.textContent=(sym||"")+(engine?(" · "+engine+" highlighted · all signals"):"");
  var m=$("chartmodal");
  if(m) m.classList.add("open");
}
function tabErr(tab, e){
  var b=$("err");
  if(b){ b.style.display="block"; b.textContent=tab+" tab error: "+(e&&e.message||e); }
  var host=$(tab==="why"?"wbody":tab==="review"?"rbody":"cbody");
  if(host) host.innerHTML='<div class="panel"><div class="empty">'+tab+' failed to render: '+
    esc(String(e&&e.message||e))+'</div></div>';
}
function closeChartModal(){
  var m=$("chartmodal"); if(m) m.classList.remove("open");
}
function restoreSide(){ } function restoreIv(){} function restoreOv(){} function restoreEngine(){}
function refreshCtlBtn(){ } function ctlSummary(){ return ''; }
function toggleSide(){ /* handled inside the chart page */ } function resetView(){} function resetY(){} function zoomStep(){}
function toggleMeasure(){} function renderEngines(){} function renderMivs(){}
function renderOverlays(){} function renderTickerList(){ /* the ticker list lives in the chart page now */ } function setDefaultIv(){}
function toggleChartCtl(){ } function chartGestures(){ } function drawMarket(){ }
function allLayers(){} function setLayerScope(){}


/* ============ per-engine controls ============ */
async function forceScan(key){
  try{
    const map={turtle:"turtle",swing:"swing",multimodels:"multimodels"};
    await fetch("/api/force?engine="+encodeURIComponent(map[key]||"all"),{method:"POST"});
    const c=document.querySelector(`.card[onclick*="${key}"]`);
    if(c){c.classList.add("scanning");setTimeout(()=>c.classList.remove("scanning"),1200);}
  }catch(e){}
}
const SPEC={
  risk:{t:"Risk",f:[
    ["risk_pct","Risk per trade %",0.1,0.05,15,"how much equity each trade can lose"],
    ["max_open_risk_pct","Book risk ceiling %",1,5,100,"total risk allowed across all open trades"],
    ["max_positions","Max open positions",1,1,40,"hard cap on concurrent trades"],
    ["conviction_cap","Conviction cap",0.1,0.5,5,"max size multiplier from signal strength"],
  ]},
  exit:{t:"Exits",f:[
    ["tp_pct","Take profit %",0.1,0.2,10,"profit target — close here"],
    ["sl_pct","Stop loss %",0.1,0.2,8,"hard stop per trade"],
    ["trail_ratio","Trail distance",0.05,0,1,"give back this fraction of profit"],
    ["trail_arm_pct","Trail arms at %",0.1,0,10,"profit needed before trailing starts"],
    ["lock_trade_pct","Per-trade lock %",0.05,0,5,"bank a trade at this profit"],
    ["lock_profit_pct","Book lock %",0.1,0,10,"bank the whole book at this profit"],
    ["lock_usd","Book lock $",1,0,100000,"bank the book once up this many dollars"],
    ["max_hold","Max hold (sec)",5,0,86400,"force close after this long; 0 = no clock"],
  ]},
  entry:{t:"Entry rules",f:[
    ["min_confidence_pct","Min confidence %",1,0,100,"reject alerts below this score"],
    ["min_agree","Models that must agree",1,1,5,"votes required to open a trade"],
    ["scan_every","Scan cadence (sec)",0.5,0.5,600,"how often the model looks for setups"],
  ]},
  turtle:{t:"Donchian rules",f:[
    ["s1_entry","S1 channel in",1,5,120,"bars in the first Donchian entry"],
    ["s1_exit","S1 channel out",1,2,120,"bars in the exit channel"],
    ["s2_entry","S2 add channel",1,5,200,"bars for the second entry"],
    ["s2_exit","S2 exit channel",1,2,200,"bars for the second exit"],
    ["n_lookback","ATR lookback",1,5,100,"bars in the volatility measure"],
    ["stop_n","Stop = N x ATR",0.1,0.5,6,"stop distance in ATRs"],
    ["max_units_total","Max units",1,1,40,"total concurrent units"],
    ["max_units_per_direction","Max per side",1,1,20,"limit on one direction"],
    ["max_units_per_market","Max per market",1,1,10,"limit per market"],
    ["vol_veto_pct","Vol veto %",1,0,200,"blanket volatility veto level"],
  ]},
  sig:{t:"Signal shaping",f:[
    ["vol_mult","Volume multiple",0.1,0,10,"volume vs median required to qualify"],
    ["tolerance","Channel tolerance",0.001,0,0.05,"how near the level counts as a break"],
    ["min_stop_frac","Min stop distance",0.0005,0.0005,0.05,"floor on stop distance for sizing"],
  ]},
};
function settingsGroups(e){
  const have=e.settings||{};
  const out=[];
  for(const k of ["risk","turtle","exit","entry","sig"]){
    const g=SPEC[k];
    const f=g.f.filter(x=>have[x[0]]!==undefined);
    if(f.length) out.push({title:g.t, fields:f.map(x=>({key:x[0],label:x[1],
      step:x[2],min:x[3],max:x[4],hint:x[5]}))});
  }
  return out;
}
async function applyAll(){
  const g=id=>{const el=document.getElementById(id);return el&&el.value!==""?parseFloat(el.value):null;};
  const body={risk_pct:g("g-risk"),tp_pct:g("g-tp"),sl_pct:g("g-sl"),
              max_positions:g("g-max"),max_open_risk_pct:g("g-ceil"),
              scan_every:g("g-scan"),trail_ratio:g("g-trail"),max_hold:g("g-hold"),
              min_liquidity:g("g-liq"),hold_sec:g("g-hs")};
  const st=$("gstat"); st.textContent="applying…";
  try{
    const r=await fetch("/api/control-all",{method:"POST",
      headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    const d=await r.json();
    if(!r.ok){ st.textContent="rejected: "+(d.error||r.status); return; }
    const ok=Object.values(d.applied||{}).filter(Boolean).length;
    st.textContent=`✓ ${d.rules} rules → ${ok} control files${d.go_turtle?" · Go turtle ✓":" · Go turtle ✗"}`;
    setTimeout(()=>{ st.textContent=""; }, 6000);
  }catch(e){ st.textContent="failed"; }
}
async function forceAll(){
  try{ await fetch("/api/force?engine=all",{method:"POST"});
       const st=$("gstat"); st.textContent="✓ forced rescan on all models";
       setTimeout(()=>{st.textContent="";},4000);}catch(e){}
}
async function applySettings(key){
  const g=id=>{const el=document.getElementById("set-"+id);
               return el&&el.value!==""?parseFloat(el.value):null;};
  const body={};
  for(const k of ["s1_entry","s1_exit","s2_entry","s2_exit","n_lookback","stop_n",
                  "max_units_total","max_units_per_direction","max_units_per_market",
                  "vol_veto_pct",
                  "risk_pct","max_open_risk_pct","max_positions","conviction_cap",
                  "tp_pct","sl_pct","trail_ratio","trail_arm_pct","lock_trade_pct",
                  "lock_profit_pct","lock_usd","max_hold","min_confidence_pct",
                  "min_agree","scan_every","vol_mult","tolerance","min_stop_frac"])
    body[k]=g(k);
  const st=document.getElementById("sstat-"+key);
  if(st) st.textContent="applying…";
  try{
    const r=await fetch("/api/engine-control?engine="+encodeURIComponent(key),
      {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    const d=await r.json();
    if(st) st.textContent=r.ok?("✓ applied "+(d.applied_count||"")+" rules — next scan")
                              :("rejected: "+(d.error||r.status));
  }catch(e){ if(st) st.textContent="failed"; }
}
async function applyParams(key){
  const g=id=>{const el=document.getElementById(id);return el&&el.value!==""?parseFloat(el.value):null;};
  const body={risk_pct:g("p-risk"),tp_pct:g("p-tp"),sl_pct:g("p-sl"),
              max_positions:g("p-max"),max_open_risk_pct:g("p-ceil")};
  const st=document.getElementById("pstat-"+key);
  if(st) st.textContent="applying…";
  try{
    const r=await fetch("/api/engine-control?engine="+encodeURIComponent(key),
      {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    const d=await r.json();
    if(st) st.textContent = r.ok ? "✓ applied — next scan" : ("rejected: "+(d.error||r.status));
  }catch(e){ if(st) st.textContent="failed"; }
}


/* ============ ticker routing ============ */
const G={pts:new Map(), s0:0, c0:0, x0:0, pinch:0};
function svgLayer(id){
  const svg=$("msvg"); if(!svg) return null;
  let g=svg.querySelector("#"+id);
  if(!g){ g=document.createElementNS("http://www.w3.org/2000/svg","g"); g.id=id;
          svg.appendChild(g); }
  return g;
}
function localPoint(ev){
  const svg=$("msvg"); const r=svg.getBoundingClientRect();
  return {x:(ev.clientX-r.left)/r.width*1200, y:(ev.clientY-r.top)/r.height*460};
}
function startBand(ev){
  const g=svgLayer("sel"); const p=localPoint(ev);
  G.band={x0:p.x, x1:p.x, y0:p.y, y1:p.y};
  g.setAttribute("opacity","1");
  g.innerHTML=`<rect id="selrect" x="${p.x}" y="${p.y}" width="0" height="0"
      fill="#58a6ff18" stroke="#58a6ff88" stroke-dasharray="3 3"/>`;
}
function updateBand(ev){
  const g=svgLayer("sel"); if(!g||!G.band) return;
  const p=localPoint(ev); G.band.x1=p.x; G.band.y1=p.y;
  const x=Math.min(G.band.x0,p.x), w=Math.abs(p.x-G.band.x0);
  const y=Math.min(G.band.y0,p.y), hh=Math.abs(p.y-G.band.y0);
  const rect=g.querySelector("#selrect");
  if(rect){ rect.setAttribute("x",x.toFixed(1)); rect.setAttribute("width",w.toFixed(1));
            rect.setAttribute("y",y.toFixed(1)); rect.setAttribute("height",hh.toFixed(1)); }
}
function finishMeasure(ev){
  if(!G.band||!GEO||!LASTROWS.length){ clearBand(); return; }
  const {PL,PR,W,PT,PB,H,LO,HI,vs,vc}=GEO;
  const conv=yy=>HI-(HI-LO)*Math.max(0,Math.min(1,(yy-PT)/(H-PT-PB)));
  const iOf=xx=>vs+(xx-PL)/(W-PL-PR)*(vc-1);
  const i0=Math.round(iOf(G.band.x0)), i1=Math.round(iOf(G.band.x1));
  const p0=conv(G.band.y0), p1=conv(G.band.y1);
  clearBand();
  if(Math.abs(G.band.x1-G.band.x0)<6 && Math.abs(G.band.y1-G.band.y0)<6){ MEASURE=null; }
  else MEASURE={i0,i1,p0,p1};
  drawMarket();
}
function clearBand(){ const g=svgLayer("sel"); if(g){ g.innerHTML=""; g.setAttribute("opacity","0"); } G.band=null; }
function finishBand(ev){
  if(!G.band||!LASTROWS.length){ clearBand(); return; }
  const {PL,PR,W}=GEO||{PL:70,PR:86,W:1200};
  const a=Math.min(G.band.x0,G.band.x1), b=Math.max(G.band.x0,G.band.x1);
  const width=b-a;
  const ay=Math.min(G.band.y0,G.band.y1), by=Math.max(G.band.y0,G.band.y1);
  const height=by-ay;
  const y0px=G.band.y0, y1px=G.band.y1;
  clearBand();
  if(width<8 && height<8) return;         // a click, not a selection
  // vertical selection -> price zoom
  if(height>=8 && GEO){
    const {PT,PB,H,LO,HI}=GEO;
    const p=yy=>HI-(HI-LO)*Math.max(0,Math.min(1,(yy-PT)/(H-PT-PB)));
    const pHi=p(ay), pLo=p(by);
    if(pHi>pLo) VIEW.y={lo:pLo, hi:pHi};
  }
  if(width<8){ drawMarket(); return; }
  const n=LASTROWS.length, vc=VIEW.c||n, vs=VIEW.s||0;
  const i0=vs+(a-PL)/(W-PL-PR)*(vc-1);
  const i1=vs+(b-PL)/(W-PL-PR)*(vc-1);
  const nc=Math.max(20,Math.min(n,Math.round(i1-i0)+1));
  VIEW.c=nc;
  VIEW.s=Math.max(0,Math.min(n-nc,Math.round(i0)));
  drawMarket();
}
function chartGestures(){
  const svg=$("msvg"); if(!svg||svg.dataset.gest) return;
  svg.dataset.gest="1";
  const pt=e=>({x:e.clientX,y:e.clientY});
  const dist=()=>{const a=[...G.pts.values()];
    return a.length<2?0:Math.hypot(a[0].x-a[1].x,a[0].y-a[1].y);};

  svg.addEventListener("pointerdown",e=>{
    svg.setPointerCapture(e.pointerId);
    G.pts.set(e.pointerId,pt(e));
    if(G.pts.size===1){
      G.x0=e.clientX; G.y0=e.clientY; G.s0=VIEW.s; G.c0=VIEW.c||0;
      G.mode = M.measure ? "measure" : (e.shiftKey ? "pan" : "select");
      if(G.mode==="select"||G.mode==="measure") startBand(e);
    }
    if(G.pts.size===2){ G.mode="pinch"; G.pinch=dist(); G.c0=VIEW.c||0; G.s0=VIEW.s; clearBand(); }
  });

  svg.addEventListener("pointermove",e=>{
    if(G.pts.size===0) updateCross(e);
    if(!G.pts.has(e.pointerId)) return;
    G.pts.set(e.pointerId,pt(e));
    const n=(LASTROWS||[]).length||120, W=1200, PL=70, PR=86;
    if(G.pts.size>=2 && G.pinch>0){
      const ratio=dist()/G.pinch;
      if(ratio>0){
        const pts=[...G.pts.values()];
        const mid=(pts[0].x+pts[1].x)/2;
        zoomAt(viewBoxX(mid), (G.c0||n)/ratio);
      }
      return;
    }
    if(G.mode==="measure"){ updateBand(e); return; }
    if(G.mode==="select"){ updateBand(e); return; }
    const dx=e.clientX-G.x0;
    if(Math.abs(dx)<1) return;
    const per=W-PL-PR;
    const shift=Math.round(-dx/per*(VIEW.c||n));
    const ns=Math.max(0,Math.min(n-(VIEW.c||n), G.s0+shift));
    if(ns!==VIEW.s){ VIEW.s=ns; drawMarket(); }
  });

  const end=e=>{
    if(G.mode==="measure") finishMeasure(e);
    else if(G.mode==="select") finishBand(e);
    G.pts.delete(e.pointerId);
    if(G.pts.size<2) G.pinch=0;
    if(G.pts.size===0){ G.s0=VIEW.s; G.x0=0; }
  };
  svg.addEventListener("pointerup",end);
  svg.addEventListener("pointercancel",end);
  svg.addEventListener("pointerleave",e=>{
    end(e);
    const g=$("xhair"); if(g) g.setAttribute("opacity","0");
  });

  svg.addEventListener("wheel",e=>{
    e.preventDefault();
    const n=(LASTROWS||[]).length||120, PL=70, PR=86, W=1200;

    // pinch on a trackpad arrives as ctrl+wheel -> zoom, anchored at cursor
    if(e.ctrlKey || e.metaKey){
      const f=Math.pow(1.0025, e.deltaY);
      zoomAt(viewBoxX(e.clientX), (VIEW.c||n)*f);
      return;
    }
    // shift+wheel -> scale the price axis about the cursor
    if(e.shiftKey){ zoomYAt(localPoint(e).y, e.deltaY>0?1.1:0.91); return; }

    // a trackpad reports small pixel deltas (and often a deltaX);
    // a mouse wheel reports large discrete steps -> zoom
    const trackpad = (e.deltaMode===0 && Math.abs(e.deltaY)<50);
    if(trackpad){
      const vc=VIEW.c||n;
      const perBar=(W-PL-PR)/Math.max(1,vc-1);
      const shift=Math.round((e.deltaX||0)/perBar + (e.deltaY||0)/perBar);
      const ns=Math.max(0,Math.min(n-vc,(VIEW.s||0)+shift));
      if(ns!==VIEW.s){ VIEW.s=ns; drawMarket(); }
      return;
    }
    zoomAt(viewBoxX(e.clientX), (VIEW.c||n)*(e.deltaY>0?1.12:0.89));
  },{passive:false});

  svg.addEventListener("dblclick",()=>resetView());
}

function viewBoxX(clientX){
  const svg=$("msvg"); if(!svg) return 0;
  const rect=svg.getBoundingClientRect();
  return (clientX-rect.left)/rect.width*1200;
}
// Zoom keeping the bar under `vx` pinned to the same pixel.
function zoomYAt(vy, factor){
  if(!GEO) return;
  const {PT,PB,H,LO,HI}=GEO;
  const f=Math.max(0,Math.min(1,(vy-PT)/(H-PT-PB)));
  const at=HI-(HI-LO)*f;                 // price under the cursor
  const span=(HI-LO)*factor;
  const nlo=at-span*(1-f), nhi=at+span*f;
  if(!(nhi>nlo)) return;
  VIEW.y={lo:nlo, hi:nhi};
  drawMarket();
}
function zoomAt(vx, newCount){
  const n=(LASTROWS||[]).length||120, PL=70, PR=86, W=1200;
  const nc=Math.max(20, Math.min(n, Math.round(newCount)));
  const f=Math.max(0, Math.min(1, (vx-PL)/(W-PL-PR)));
  const a=(VIEW.s||0) + f*((VIEW.c||n)-1);   // bar under the cursor
  VIEW.c=nc;
  VIEW.s=Math.max(0, Math.min(n-nc, a - f*(nc-1)));  // keep it there
  drawMarket();
}
function hhmm(t){
  const d=new Date(t);
  return d.toLocaleDateString(undefined,{month:"short",day:"2-digit"})+" "+
         d.toLocaleTimeString(undefined,{hour:"2-digit",minute:"2-digit",hour12:false});
}
function updateCross(ev){
  const svg=$("msvg"), g=$("xhair");
  if(!svg||!g||!GEO||!LASTROWS.length) return;
  const rect=svg.getBoundingClientRect();
  const vx=(ev.clientX-rect.left)/rect.width*GEO.W;
  const vy=(ev.clientY-rect.top)/rect.height*GEO.H;
  const {vs,vc,PL,PR,PT,PB,W,H,LO,HI}=GEO;
  if(vx<PL||vx>W-PR||vy<PT||vy>H-PB){ g.setAttribute("opacity","0"); return; }
  const i=Math.max(vs,Math.min(vs+vc-1,
        vs+Math.round((vx-PL)/(W-PL-PR)*(vc-1))));
  const c=LASTROWS[i]; if(!c) return;
  const price=LO+(HI-LO)*(1-(vy-PT)/(H-PT-PB));
  const chg=100*(c.c/c.o-1);
  const up=c.c>=c.o;
  g.setAttribute("opacity","1");
  g.innerHTML=`
    <line x1="${vx.toFixed(1)}" y1="${PT}" x2="${vx.toFixed(1)}" y2="${H-PB}"
          stroke="#4a5c72" stroke-width="1" stroke-dasharray="3 3"/>
    <line x1="${PL}" y1="${vy.toFixed(1)}" x2="${W-PR}" y2="${vy.toFixed(1)}"
          stroke="#4a5c72" stroke-width="1" stroke-dasharray="3 3"/>
    <rect x="${W-PR+3}" y="${(vy-9).toFixed(1)}" width="80" height="18" rx="4"
          fill="#1b2b3d" stroke="#2f4a66"/>
    <text x="${W-PR+43}" y="${(vy+4).toFixed(1)}" fill="#9ecbff" font-size="10.5"
          text-anchor="middle" font-family="ui-monospace,monospace">${price.toPrecision(7)}</text>
    <rect x="${(Math.min(vx,W-PR-74)).toFixed(1)}" y="${H-PB+5}" width="70" height="16" rx="4"
          fill="#1b2b3d" stroke="#2f4a66"/>
    <text x="${(Math.min(vx,W-PR-74)+35).toFixed(1)}" y="${H-PB+16}" fill="#9ecbff"
          font-size="9.5" text-anchor="middle"
          font-family="ui-monospace,monospace">${new Date(c.t).toLocaleTimeString(undefined,{hour:"2-digit",minute:"2-digit",hour12:false})}</text>
    <g>
      <rect x="${(vx>W/2?PL+6:W-PR-236).toFixed(1)}" y="${PT+6}" width="230" height="86" rx="8"
            fill="#0b131bee" stroke="#24313e"/>
      <text x="${(vx>W/2?PL+18:W-PR-224).toFixed(1)}" y="${PT+24}" fill="#8494a8" font-size="10"
            font-family="ui-monospace,monospace">${hhmm(c.t)}</text>
      <text x="${(vx>W/2?PL+18:W-PR-224).toFixed(1)}" y="${PT+42}" fill="#e8eef6" font-size="11"
            font-family="ui-monospace,monospace">
        O ${c.o.toPrecision(7)}   H ${c.h.toPrecision(7)}</text>
      <text x="${(vx>W/2?PL+18:W-PR-224).toFixed(1)}" y="${PT+58}" fill="#e8eef6" font-size="11"
            font-family="ui-monospace,monospace">
        L ${c.l.toPrecision(7)}   C ${c.c.toPrecision(7)}</text>
      <text x="${(vx>W/2?PL+18:W-PR-224).toFixed(1)}" y="${PT+76}" fill="${up?'#3fb950':'#f85149'}"
            font-size="11" font-family="ui-monospace,monospace">
        ${up?"+":""}${chg.toFixed(2)}%   vol ${c.v>=1000?(c.v/1000).toFixed(1)+"k":c.v.toFixed(1)}</text>
    </g>`;
}




/* ================= LEADERBOARD ================= */
var HIST={};          // engine -> [equity samples]
function histPush(es){
  es.forEach(function(e){
    if(!e||e.equity==null) return;
    var h=HIST[e.key]||(HIST[e.key]=[]);
    h.push(e.equity);
    if(h.length>90) h.shift();
  });
}
function spark(h, w, hh){
  if(!h||h.length<2) return '<span class="mk">—</span>';
  var lo=Math.min.apply(null,h), hi=Math.max.apply(null,h);
  var rng=(hi-lo)||1;
  var pts=h.map(function(v,i){
    var x=(i/(h.length-1))*w;
    var y=hh-2-((v-lo)/rng)*(hh-4);
    return x.toFixed(1)+','+y.toFixed(1);
  }).join(' ');
  var up=h[h.length-1]>=h[0];
  return '<svg width="'+w+'" height="'+hh+'" style="display:block">'+
    '<polyline points="'+pts+'" fill="none" stroke="'+(up?'#3fb950':'#f85149')+
    '" stroke-width="1.4"/></svg>';
}
function lbRow(e, rank, best, worst){
  var pos=e.positions||[], tr=e.trades||[];
  var wins=tr.filter(function(t){ return (t.net_pct||0)>0; }).length;
  var wr=tr.length?Math.round(100*wins/tr.length):null;
  var avg=tr.length?(tr.reduce(function(a,t){ return a+(t.net_pct||0); },0)/tr.length):null;
  var ret=e.return_pct||0;
  var span=Math.max(1,Math.abs(best)+Math.abs(worst));
  var pctW=Math.min(100,Math.abs(ret)/span*100);
  var medal = rank===1?'gold':rank===2?'silver':rank===3?'bronze':'';
  var h=HIST[e.key]||[];
  return '<tr class="'+(rank<=3?('top'+rank):'')+'">'+
    '<td><span class="rank '+medal+'">'+rank+'</span></td>'+
    '<td style="color:'+e.colour+';font-weight:700">'+esc(e.key)+
      '<div class="mk" style="font-weight:400">'+esc(e.label||'')+'</div></td>'+
    '<td>$'+money(e.equity)+'</td>'+
    '<td class="'+(ret>=0?'up':'down')+'"><b>'+(ret>=0?'+':'')+ret.toFixed(2)+'%</b>'+
      '<div class="lbbar" style="margin-top:4px"><i style="width:'+pctW.toFixed(0)+'%;background:'+
      (ret>=0?'#3fb950':'#f85149')+'"></i></div></td>'+
    '<td class="'+((e.realised||0)>=0?'up':'down')+'">'+((e.realised||0)>=0?'+':'')+money(e.realised)+'</td>'+
    '<td class="'+((e.unrealised||0)>=0?'up':'down')+'">'+((e.unrealised||0)>=0?'+':'')+money(e.unrealised)+'</td>'+
    '<td>'+tr.length+'</td>'+
    '<td>'+(wr==null?'—':wr+'%')+'</td>'+
    '<td class="'+(avg!=null&&avg>=0?'up':'down')+'">'+(avg==null?'—':(avg>=0?'+':'')+avg.toFixed(3)+'%')+'</td>'+
    '<td>'+pos.length+'</td>'+
    '<td>'+spark(h,90,20)+'</td></tr>';
}
function renderLeaderboard(){
  var es=(LAST||[]).filter(function(e){ return e.equity!=null; });
  histPush(es);
  es.sort(function(a,b){ return (b.return_pct||0)-(a.return_pct||0); });
  var best=Math.max.apply(null, es.map(function(e){ return Math.abs(e.return_pct||0); }).concat([1]));
  var worst=-best;
  var box=$("lbbody"), note=$("lbn");
  if(!box) return;
  if(!es.length){ box.innerHTML='<div class="panel"><div class="empty">no engines reporting</div></div>'; return; }
  var tot=es.reduce(function(a,e){ return a+e.equity; },0);
  var base=es.length*2000;
  if(note) note.textContent={
    value:es.length+" engines · combined $"+money(tot)+" ("+((100*(tot/base-1))>=0?'+':'')+
          (100*(tot/base-1)).toFixed(2)+"%)"
  }.value;
  box.innerHTML='<table class="lbtable"><thead><tr>'+
    '<th>#</th><th>engine</th><th>equity</th><th>return</th><th>realised</th>'+
    '<th>unrealised</th><th>closed</th><th>win</th><th>avg net</th><th>open</th>'+
    '<th>equity curve</th></tr></thead><tbody>'+
    es.map(function(e,i){ return lbRow(e,i+1,best,worst); }).join('')+
    '</tbody></table>';
}

/* ================= MODEL REVIEW ================= */
let PATHS={};
function pctOr(v){ return v==null?"—":(v>=0?"+":"")+Number(v).toFixed(3)+"%"; }

function classify(t){
  const net=t.net_pct||0, peak=t.peak_pct||0, r=(t.reason||"");
  if(Math.abs(net+0.2)<0.06) return "FEE";
  if(r.startsWith("clock")||r.startsWith("time")) return "TIMEOUT";
  if(peak>=0.3 && net<=0) return "LEAKED";
  if(peak>0 && net<=-0.5) return "REVERSED";
  if(peak<=0.05) return "BAD_ENTRY";
  return "OTHER";
}
function scorecard(ts){
  const n=ts.length;
  const wins=ts.filter(t=>(t.net_pct||0)>0).length;
  const gross=ts.reduce((a,t)=>a+(t.gross_pct||0),0)/Math.max(1,n);
  const net=ts.reduce((a,t)=>a+(t.net_pct||0),0)/Math.max(1,n);
  const fee=gross!==0?Math.max(0,Math.min(1,1-(net/gross))):(net<0?1:0);
  const peaks=ts.filter(t=>(t.peak_pct||0)>0.02);
  const capture=peaks.length?peaks.reduce((a,t)=>a+((t.net_pct||0)/(t.peak_pct||1)),0)/peaks.length:null;
  const mfe=ts.reduce((a,t)=>a+(t.peak_pct||0),0)/Math.max(1,n);
  const held=ts.map(t=>t.held_s||0).sort((a,b)=>a-b);
  return {n, wr:n?100*wins/n:0, capture, mfe, fee:100*fee,
          held:held.length?held[Math.floor(held.length/2)]:0};
}
function quadrant(ts){
  const q={earned:0,leaked:0,bad:0,luck:0};
  ts.forEach(t=>{const peak=t.peak_pct||0, net=t.net_pct||0;
    if(peak>0.05&&net>0) q.earned++;
    else if(peak>0.05) q.leaked++;
    else if(net<0) q.bad++; else q.luck++;});
  return q;
}
function qbar(label,v,total,colour){
  const w=total?Math.min(100,(v/total)*100):0;
  return `<div class="skrow"><span class="sklab">${label}</span>
    <span class="skbar"><i style="width:${w.toFixed(0)}%;background:${colour}"></i></span>
    <span class="skval">${v}</span><span class="skmeta">${total?Math.round(100*v/total):0}%</span></div>`;
}
function scatter(ts, eng){
  if(ts.length<2) return '<div class="empty">need more trades</div>';
  const W=360,H=210,L=44,B=26,T=10,R=10;
  const peaks=ts.map(t=>Math.max(0.02,t.peak_pct||0));
  const nets=ts.map(t=>t.net_pct||0);
  const xmax=Math.max(0.5,Math.max(...peaks)*1.1);
  const nmin=Math.min(-0.4,Math.min(...nets)*1.15);
  const nmax=Math.max(0.4,Math.max(...nets)*1.15);
  const X=v=>L+(W-L-R)*Math.min(1,v/xmax);
  const Y=v=>H-B-(H-T-B)*((v-nmin)/(nmax-nmin));
  const zy=Y(0), tx=X(0.05);
  let g=`<rect x="${L}" y="${T}" width="${tx-L}" height="${zy-T}" fill="#3fb95010"/>
    <rect x="${tx}" y="${zy}" width="${W-R-tx}" height="${H-B-zy}" fill="#d2992220"/>
    <rect x="${L}" y="${zy}" width="${tx-L}" height="${H-B-zy}" fill="#f8514914"/>
    <line x1="${L}" y1="${zy.toFixed(1)}" x2="${W-R}" y2="${zy.toFixed(1)}" stroke="#2a3a4a"/>
    <line x1="${tx.toFixed(1)}" y1="${T}" x2="${tx.toFixed(1)}" y2="${H-B}" stroke="#2a3a4a"
          stroke-dasharray="3 3"/>`;
  ts.forEach(t=>{
    const k=classify(t);
    const c=k==="LEAKED"?"#d29922":k==="BAD_ENTRY"?"#f85149":k==="FEE"?"#58a6ff":"#3fb950";
    const r=Math.max(3,Math.min(9,Math.sqrt((t.notional||1000)/900)));
    g+=`<circle cx="${X(Math.max(0.02,t.peak_pct||0)).toFixed(1)}" cy="${Y(t.net_pct||0).toFixed(1)}"
          r="${r.toFixed(1)}" fill="${c}" opacity=".72" stroke="${c}"
          style="cursor:pointer" onclick="openChart('${esc(t.symbol)}','${esc(eng||"")}')">
          <title>${esc(t.symbol)} · peak ${pctOr(t.peak_pct)} · net ${pctOr(t.net_pct)} · ${esc(t.reason||"")} — click for the chart</title></circle>`;
  });
  g+=`<text x="${L}" y="${H-8}" fill="#4c5a6c" font-size="9">MFE →</text>
      <text x="4" y="${T+8}" fill="#4c5a6c" font-size="9">net</text>
      <text x="${((L+tx)/2).toFixed(0)}" y="${T+14}" fill="#3fb95088" font-size="8" text-anchor="middle">LUCK</text>
      <text x="${((tx+W-R)/2).toFixed(0)}" y="${H-B-6}" fill="#d29922aa" font-size="8" text-anchor="middle">LEAKED</text>
      <text x="${((L+tx)/2).toFixed(0)}" y="${H-B-6}" fill="#f8514988" font-size="8" text-anchor="middle">BAD ENTRY</text>`;
  return `<svg viewBox="0 0 ${W} ${H}" style="width:100%;height:210px">${g}</svg>`;
}
function shapeSVG(pts, entry, exit){
  if(!pts||pts.length<3) return '<div class="mk">path unavailable</div>';
  const W=120,H=34, lo=Math.min(...pts), hi=Math.max(...pts);
  const y=v=>H-4-(H-8)*((v-lo)/((hi-lo)||1));
  const x=i=>2+(W-4)*(i/(pts.length-1));
  const pth=pts.map((v,i)=>`${i?"L":"M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const up=exit>=entry;
  return `<svg viewBox="0 0 ${W} ${H}" style="width:118px;height:34px;display:block">
    <path d="${pth}" fill="none" stroke="${up?'#3fb950':'#f85149'}" stroke-width="1.2"/>
    <circle cx="2" cy="${y(entry).toFixed(1)}" r="2" fill="#58a6ff"/>
    <circle cx="${W-2}" cy="${y(exit).toFixed(1)}" r="2" fill="#e8eef6"/></svg>`;
}
function conditional(ts, get, bands){
  bands.forEach(b=>{b.n=0;b.w=0;});
  ts.forEach(t=>{const v=get(t); if(v==null) return;
    for(const b of bands){ if(v>=b.lo&&v<b.hi){ b.n++; if((t.net_pct||0)>0) b.w++; break; } }});
  if(!bands.some(b=>b.n)) return "";
  return `<div class="ccol"><div class="ctitle">by ${bands[0].what}</div>
    ${bands.map(b=>`<div class="skrow"><span class="sklab">${b.label}</span>
      <span class="skbar"><i style="width:${b.n?Math.round(100*b.w/b.n):0}%;background:#3fb950"></i></span>
      <span class="skval">${b.n?Math.round(100*b.w/b.n):0}%</span>
      <span class="skmeta">n=${b.n}</span></div>`).join("")}</div>`;
}
function reviewCard(e){
  const ts=e.trades||[];
  if(!ts.length) return "";
  const sc=scorecard(ts), q=quadrant(ts);
  const mix={}; ts.forEach(t=>{const k=classify(t); mix[k]=(mix[k]||0)+1;});
  const rows=Object.keys(mix).sort((a,b)=>mix[b]-mix[a]);
  const worst=ts.slice().sort((a,b)=>(a.pnl_usd||0)-(b.pnl_usd||0)).slice(0,10);
  const conf=conditional(ts,t=>{const g=t.decision&&t.decision.gates; return g&&g.conf!=null?g.conf:null;},
    [{label:"<65%",lo:0,hi:0.65,what:"confidence"},{label:"65–80%",lo:0.65,hi:0.8,what:"confidence"},
     {label:"80–90%",lo:0.8,hi:0.9,what:"confidence"},{label:"90%+",lo:0.9,hi:2,what:"confidence"}]);
  const agr=conditional(ts,t=>{const v=t.decision&&t.decision.votes;
    return v?Object.values(v).filter(x=>x).length:null;},
    [{label:"1 model",lo:1,hi:1.5,what:"agreement"},{label:"2",lo:1.5,hi:2.5,what:"agreement"},
     {label:"3+",lo:2.5,hi:9,what:"agreement"}]);
  const vol=conditional(ts,t=>{const f=t.decision&&t.decision.features;
    return f&&f.atr_pct!=null?f.atr_pct:null;},
    [{label:"low <3%",lo:0,hi:3,what:"volatility"},{label:"mid",lo:3,hi:6,what:"volatility"},
     {label:"high 6%+",lo:6,hi:999,what:"volatility"}]);
  return `<div class="rcard">
    <div class="rchead"><span style="color:${e.colour}">${esc(e.key)}</span>
      <span class="mk">${ts.length} closed</span>
      <span class="${(e.return_pct||0)>=0?'up':'down'}" style="margin-left:auto;font-family:var(--mono)">
        ${pctOr(e.return_pct)}</span></div>
    <div class="score">
      <div class="sbox"><b>${sc.wr.toFixed(0)}%</b><span>hit rate</span></div>
      <div class="sbox"><b>${sc.capture==null?"—":sc.capture.toFixed(2)}</b><span>capture</span></div>
      <div class="sbox"><b>${sc.mfe.toFixed(2)}%</b><span>avg MFE</span></div>
      <div class="sbox"><b>${sc.fee.toFixed(0)}%</b><span>fee share</span></div>
      <div class="sbox"><b>${Math.round(sc.held)}s</b><span>hold</span></div>
    </div>
    <div class="rcols">
      <div class="ccol"><div class="ctitle">MFE vs captured — where it leaks</div>${scatter(ts,e.key)}</div>
      <div class="ccol"><div class="ctitle">decision quadrant</div>
        ${qbar("earned",q.earned,ts.length,"#3fb950")}
        ${qbar("leaked",q.leaked,ts.length,"#d29922")}
        ${qbar("bad entry",q.bad,ts.length,"#f85149")}
        ${qbar("luck",q.luck,ts.length,"#58a6ff")}
        <div class="ctitle" style="margin-top:12px">error mix</div>
        ${rows.map(k=>qbar(k,mix[k],ts.length,
          k==="FEE"?"#58a6ff":k==="LEAKED"?"#d29922":k==="BAD_ENTRY"?"#f85149":"#8494a8")).join("")}
      </div>
      ${conf||agr||vol?`<div class="ccol">${conf}${agr}${vol}</div>`:""}
    </div>
    <div class="ctitle" style="margin-top:14px">worst 10 — real path, entry ● → exit ●</div>
    <div class="wgrid">${worst.map(t=>`
      <div class="dna">
        <div class="dhead"><span class="tick" data-sym="${esc(t.symbol)}" data-eng="${esc(e.key)}">${esc(t.symbol)}</span>
          <span class="side ${t.side==='buy'?'buy':'sell'}">${esc((t.side||'?').toUpperCase())}</span>
          <span class="mk">${esc(t.reason||"")}</span>
          <span class="${(t.net_pct||0)>0?'up':'down'}" style="margin-left:auto;font-family:var(--mono)">
            ${pctOr(t.net_pct)}</span></div>
        <div style="margin-top:6px">${shapeSVG(PATHS[t.symbol+"|"+(t.entry_ms||0)], t.entry, t.exit)}</div>
        <div class="dfacts"><span>peak <b>${pctOr(t.peak_pct)}</b></span>
          <span>held <b>${Math.round(t.held_s||0)}s</b></span>
          <span>${(t.pnl_usd||0)>=0?'+':''}$${money(t.pnl_usd||0)}</span></div>
        ${t.why?`<div class="dwhy">${esc(t.why)}</div>`:""}
        ${attribBars(t.decision)}
      </div>`).join("")}</div>
  </div>`;
}
async function loadPaths(ts){
  await Promise.all(ts.slice(0,10).filter(t=>t.entry_ms).map(async t=>{
    const k=t.symbol+"|"+(t.entry_ms||0);
    if(PATHS[k]) return;
    try{
      const d=await (await fetch(`/api/path?symbol=${t.symbol}&from=${t.entry_ms||0}&to=${t.exit_ms||Date.now()}`,{cache:"no-store"})).json();
      PATHS[k]=d.points||[];
    }catch(e){ PATHS[k]=[]; }
  }));
}
async function renderReview(){
  const all=(LAST||[]).flatMap(e=>e.trades||[]);
  const worst=all.slice().sort((a,b)=>(a.pnl_usd||0)-(b.pnl_usd||0)).slice(0,10);
  await loadPaths(worst);
  const cards=(LAST||[]).map(reviewCard).filter(Boolean);
  const note=$("rnote");
  if(note) note.textContent=all.length?(all.length+" closed trades across "+(LAST||[]).length+" models"):"no closed trades yet";
  $("rbody").innerHTML=cards.length?cards.join(""):
    '<div class="panel"><div class="empty">No closed trades yet — this fills in as positions close.</div></div>';
}

/* ================= WHY: explainability ================= */
function tradesAll(){
  const out=[];
  (LAST||[]).forEach(e=>(e.trades||[]).forEach(t=>out.push({...t, engine:e.key, colour:e.colour})));
  return out.sort((a,b)=>(a.exit_ms||0)-(b.exit_ms||0));
}
function pctOr(v){ return v==null?"—":(v>=0?"+":"")+Number(v).toFixed(3)+"%"; }

function attribBars(d){
  if(!d||!d.features) return '<div class="empty">no structured decision recorded</div>';
  const f=d.features, g=d.gates||{};
  const rows=[];
  const sc=(f.score!=null?Math.abs(f.score)/100:0);
  if(f.score!=null) rows.push(["alert score "+Math.round(f.score), sc, f.score>0?1:-1]);
  if(f.taker_buy_ratio!=null) rows.push(["taker buy "+(100*f.taker_buy_ratio).toFixed(0)+"%",
      Math.abs(f.taker_buy_ratio-0.5)*2, f.taker_buy_ratio>0.5?1:-1]);
  if(f.range_position!=null) rows.push(["range pos "+(100*f.range_position).toFixed(0)+"%",
      Math.abs(f.range_position-0.5)*2, f.range_position>0.5?1:-1]);
  if(f.burst_multiple!=null) rows.push(["flow burst "+Number(f.burst_multiple).toFixed(1)+"x",
      Math.min(1,Number(f.burst_multiple)/20), 1]);
  if(d.votes){ const n=Object.values(d.votes).filter(v=>v).length;
    rows.push(["model votes "+n, Math.min(1,n/5), n>=2?1:-1]); }
  if(g.liq_floor&&g.liquidity) rows.push(["liquidity vs floor",
      Math.min(1,g.liquidity/g.liq_floor/10), g.liquidity>=g.liq_floor?1:-1]);
  if(d.pcal!=null) rows.push(["calibrated p "+(100*d.pcal).toFixed(0)+"%", d.pcal, d.pcal>0.2?1:-1]);
  if(!rows.length) return '<div class="empty">no attributable features</div>';
  return `<div class="attr">${rows.map(([lab,v,sign])=>`
    <div class="arow"><span class="alab">${esc(lab)}</span>
      <span class="abar"><i style="width:${(v*100).toFixed(0)}%;background:${sign>0?'#3fb950':'#f85149'}"></i></span>
      <span class="aval ${sign>0?'up':'down'}">${(sign>0?'+':'−')}${(v).toFixed(2)}</span></div>`).join("")}</div>`;
}

function funnelView(){
  const es=(LAST||[]).filter(e=>e.funnel&&Object.keys(e.funnel).length);
  if(!es.length) return '<div class="empty">no gate counters published yet</div>';
  const STEPS=[["alerts","alerts received"],["liquidity","liquidity floor"],
               ["technical","technical confirm"],["model","model gate"],
               ["confidence","confidence floor"],["opened","opened"]];
  return es.map(e=>{
    const f=e.funnel;
    const total=Math.max(f.alerts||f.scanned||f.candidates||1,1);
    const rows=STEPS.filter(([k])=>f[k]!==undefined).map(([k,lab])=>{
      const v=f[k]||0, w=Math.min(100,(v/total)*100);
      return `<div class="skrow"><span class="sklab">${lab}</span>
        <span class="skbar"><i style="width:${w.toFixed(0)}%;background:${
          k==="opened"?"#3fb950":"#58a6ff"}"></i></span>
        <span class="skval">${v}</span>
        <span class="skmeta">${k==="opened"?"traded":"rejected here"}</span></div>`;
    }).join("");
    return `<div class="fcard"><div class="fhead" style="color:${e.colour}">${esc(e.key)}</div>${rows}</div>`;
  }).join("");
}
function sankey(trades){
  const by={};
  trades.forEach(t=>{ const k=t.reason||"?";
    by[k]=by[k]||{n:0,pnl:0,wins:0};
    by[k].n++; by[k].pnl+=(t.pnl_usd||0); if((t.net_pct||0)>0) by[k].wins++; });
  const keys=Object.keys(by).sort((a,b)=>by[a].pnl-by[b].pnl);
  if(!keys.length) return '<div class="empty">no closed trades yet</div>';
  const max=Math.max(...keys.map(k=>Math.abs(by[k].pnl)))||1;
  return `<div class="sk">${keys.map(k=>{const v=by[k]; const w=Math.abs(v.pnl)/max*100;
    return `<div class="skrow"><span class="sklab">${esc(k)}</span>
      <span class="skbar"><i style="width:${w.toFixed(0)}%;background:${v.pnl>=0?'#3fb950':'#f85149'}"></i></span>
      <span class="skval ${v.pnl>=0?'up':'down'}">${v.pnl>=0?'+':''}$${money(v.pnl)}</span>
      <span class="skmeta">${v.n}× · ${Math.round(100*v.wins/v.n)}% win</span></div>`;}).join("")}</div>`;
}

function calibration(trades){
  const pts=trades.filter(t=>t.decision&&t.decision.pcal!=null);
  if(pts.length<3) return '<div class="empty">need a few trades with calibrated probability</div>';
  const W=520,H=220,P=34;
  const px=p=>P+(W-2*P)*Math.min(1,Math.max(0,p));
  const py=w=>H-P-(H-2*P)*Math.min(1,Math.max(0,w));
  let g=`<line x1="${P}" y1="${H-P}" x2="${W-P}" y2="${H-P}" stroke="#24313e"/>
         <line x1="${P}" y1="${P}" x2="${P}" y2="${H-P}" stroke="#24313e"/>
         <line x1="${P}" y1="${H-P}" x2="${W-P}" y2="${P}" stroke="#2f4a66" stroke-dasharray="4 4"/>`;
  pts.forEach(t=>{
    const x=px(t.decision.pcal), y=py((t.net_pct||0)>0?1:0);
    g+=`<circle cx="${x.toFixed(1)}" cy="${(y+(Math.random()*10-5)).toFixed(1)}" r="4"
          fill="${(t.net_pct||0)>0?'#3fb950':'#f85149'}" opacity=".85">
          <title>${esc(t.engine)} ${esc(t.symbol)} p=${(100*t.decision.pcal).toFixed(0)}% net ${pctOr(t.net_pct)}</title></circle>`;
  });
  g+=`<text x="${P}" y="${H-10}" fill="#59677a" font-size="10">predicted probability →</text>
      <text x="${P-6}" y="${P+4}" fill="#59677a" font-size="10" text-anchor="end">win</text>
      <text x="${P-6}" y="${H-P}" fill="#59677a" font-size="10" text-anchor="end">loss</text>`;
  return `<svg viewBox="0 0 ${W} ${H}" style="width:100%;max-width:560px;height:220px">${g}</svg>
          <div class="mk">${pts.length} trades with a calibrated probability · dashed line = perfect calibration</div>`;
}

function ribbon(){
  const sym=($("wsym").textContent||"").toUpperCase();
  const al=(RIBBON||{}).models||[]; const nl=(RIBBON||{}).alerts||[];
  if(!al.length&&!nl.length) return '<div class="empty">no signals recorded for this symbol</div>';
  const lo=Math.min(...[...al.map(x=>x.at_ms||0),...nl.map(x=>x.ts_ms||0)].filter(Boolean));
  const hi=Math.max(...[...al.map(x=>x.at_ms||0),...nl.map(x=>x.ts_ms||0)].filter(Boolean));
  const BINS=36, span=Math.max(1,hi-lo);
  const models=[...new Set(al.map(x=>x.model))];
  const cell=(v)=>v>0?'#3fb950':v<0?'#f85149':'#18202b';
  let rows=models.map(m=>{
    let cells="";
    for(let b=0;b<BINS;b++){
      const t0=lo+span*b/BINS, t1=lo+span*(b+1)/BINS;
      const hit=al.filter(x=>x.model===m&&x.at_ms>=t0&&x.at_ms<t1);
      const v=hit.reduce((a,x)=>a+((x.direction||"").includes("BUY")||(x.direction||"").includes("PUMP")?1:-1),0);
      cells+=`<i style="background:${cell(v)}" title="${esc(m)} ${new Date(t0).toLocaleTimeString()}"></i>`;
    }
    return `<div class="rrow"><span class="rlab">${esc(m)}</span><span class="rcells">${cells}</span></div>`;
  }).join("");
  let acells="";
  for(let b=0;b<BINS;b++){
    const t0=lo+span*b/BINS, t1=lo+span*(b+1)/BINS;
    const hit=nl.filter(x=>x.ts_ms>=t0&&x.ts_ms<t1);
    const v=hit.reduce((a,x)=>a+(x.kind==="PUMP"?1:-1),0);
    acells+=`<i style="background:${hit.length?cell(v):'#121a24'}" title="${hit.length} alert(s)"></i>`;
  }
  return `<div class="rrow"><span class="rlab">alerts</span><span class="rcells">${acells}</span></div>${rows}
    <div class="mk">each column is a time bucket · green = long vote, red = short, dark = silent</div>`;
}

function dnaCards(trades, sym){
  const mine=trades.filter(t=>sym?t.symbol===sym:true).slice(-8).reverse();
  if(!mine.length) return '<div class="empty">no trades to explain yet</div>';
  return mine.map(t=>`
    <div class="dna">
      <div class="dhead">
        <span class="tick" data-sym="${esc(t.symbol)}">${esc(t.symbol)}</span>
        <span class="side ${t.side==='buy'?'buy':'sell'}">${esc((t.side||'?').toUpperCase())}</span>
        <span style="color:${t.colour};font-size:10.5px">${esc(t.engine)}</span>
        <span class="${ (t.net_pct||0)>0?'up':'down'}" style="margin-left:auto;font-family:var(--mono)">
          ${pctOr(t.net_pct)} · ${t.pnl_usd>=0?'+':''}$${money(t.pnl_usd||0)}</span>
      </div>
      <div class="dfacts">
        <span>entry <b>${t.entry==null?"—":Number(t.entry).toPrecision(8)}</b></span>
        <span>exit <b>${t.exit==null?"—":Number(t.exit).toPrecision(8)}</b></span>
        <span>peak <b>${pctOr(t.peak_pct)}</b></span>
        <span>held <b>${Math.round(t.held_s||0)}s</b></span>
        <span>closed by <b>${esc(t.reason||"—")}</b></span>
      </div>
      ${t.why?`<div class="dwhy">${esc(t.why)}</div>`:""}
      ${attribBars(t.decision)}
    </div>`).join("");
}

async function renderWhy(){
  const sym=(window.SELECTED_SYM||"BTCUSDT").toUpperCase();
  $("wsym").textContent=sym;
  const trades=tradesAll();
  let sig=null;
  try{ sig=await (await fetch(`/api/signals?symbol=${sym}`,{cache:"no-store"})).json(); }catch(e){}
  RIBBON=sig||{};
  const withDec=trades.filter(t=>t.decision).length;
  $("wnote").textContent=`${trades.length} trades · ${withDec} with structured decisions`;
  $("wbody").innerHTML=`
    <div class="section"><h2>Gate funnel — where candidates die</h2>
      <div class="panel fgrid">${funnelView()}</div></div>
    <div class="section"><h2>Decision ribbon — ${esc(sym)}</h2>
      <div class="panel">${ribbon()}</div></div>
    <div class="section"><h2>What made the decision — ${esc(sym)}</h2>
      <div class="dgrid">${dnaCards(trades, sym)}</div></div>
    <div class="section"><h2>Did confidence mean anything?</h2>
      <div class="panel">${calibration(trades)}</div></div>
    <div class="section"><h2>Where the money went — exit rules</h2>
      <div class="panel">${sankey(trades)}</div></div>`;
}

function closeChart(){ const b=$("chartbox"); if(b) b.classList.remove("open"); }

function tickerMap(){
  const map=new Map();
  (LAST||[]).forEach(e=>{
    const add=(sym,kind)=>{
      if(!sym) return;
      if(!map.has(sym)) map.set(sym,{fills:0,engines:new Map()});
      const m=map.get(sym);
      m.fills++;
      m.engines.set(e.key,(m.engines.get(e.key)||0)+1);
    };
    (e.trades||[]).forEach(t=>add(t.symbol));
    (e.positions||[]).forEach(p=>add(p.symbol));
  });
  return map;
}

async function zoomToSignal(sym){
  let ts=null;
  try{
    const d=await (await fetch(`/api/signals?symbol=${sym}`,{cache:"no-store"})).json();
    const t=[...(d.alerts||[]).map(a=>a.ts_ms),...(d.models||[]).map(m=>m.at_ms)]
              .filter(Boolean);
    if(t.length) ts=Math.max(...t);
  }catch(e){}
  PENDING_TS=ts;
  openChart(sym, null);
}
function renderTickerList(){
  const map=tickerMap();
  const q=($("msearch")?.value||"").trim().toUpperCase();

  // union: everything the Go backend tracks, plus anything already traded
  const vol=new Map(TICKERS.map(t=>[t.s,t.v]));
  let syms=new Set(TICKERS.map(t=>t.s));
  map.forEach((_,k)=>syms.add(k));
  syms=[...syms];
  if(q) syms=syms.filter(s=>s.includes(q));
  syms.sort((a,b)=>{
    const fa=map.get(a)?.fills||0, fb=map.get(b)?.fills||0;
    if(fb!==fa) return fb-fa;
    return (vol.get(b)||0)-(vol.get(a)||0);
  });
  const shown=syms.slice(0,140);
  if(!shown.length){
    $("mlist").innerHTML=`<div class="mempty">no symbol matches “${esc(q)}”</div>`;
    return;
  }
  const cur=($("msym").value||"").toUpperCase();
  $("mlist").innerHTML=shown.map(sym=>{
    const m=map.get(sym);
    const dots=m?[...m.engines.keys()].slice(0,5).map(k=>{
      const col=(LAST||[]).find(e=>e.key===k)?.colour||"#8494a8";
      return `<i class="sdot" style="background:${col}"></i>`;
    }).join(""):"";
    const v=vol.get(sym);
    return `<div class="mrow ${sym===cur?'on':''}" onclick="selectTicker('${esc(sym)}')">
      <span class="tick" data-sym="${esc(sym)}">${esc(sym)}</span>
      <span class="dots">${dots||`<span class="n">${v?("$"+(v/1e6).toFixed(0)+"M"):""}</span>`}</span>
      <span class="n">${m?m.fills:""}</span></div>`;
  }).join("") + (syms.length>shown.length
    ? `<div class="mempty">+${syms.length-shown.length} more — refine the search</div>` : "");
}

function toggleSide(){
  const lay=document.querySelector(".mlayout");
  const btn=$("sidetoggle");
  lay.classList.toggle("collapsed");
  const off=lay.classList.contains("collapsed");
  btn.textContent = off ? "▶ tickers" : "◀ tickers";
  try{ localStorage.setItem("sideCollapsed", off?"1":"0"); }catch(e){}
  if(!off){ renderTickerList(); }
  drawMarket();
}
function restoreSide(){
  let off=false;
  try{ off=localStorage.getItem("sideCollapsed")==="1"; }catch(e){}
  if(off){
    document.querySelector(".mlayout").classList.add("collapsed");
    const b=$("sidetoggle"); if(b) b.textContent="▶ tickers";
  }
}
function clearContext(){ M.context=null; drawMarket(); }
function selectTicker(sym){ if(sym) openChart(sym, null); }


window.addEventListener("error", function(e){
  var b=$("err");
  if(b){ b.style.display="block"; b.textContent="JS error: "+(e.message||"")+" @ line "+(e.lineno||"?"); }
});

async function health(){
  var el=$("hz"); if(!el) return;
  try{
    var r=await fetch("/healthz",{cache:"no-store"});
    var d=await r.json();
    var c=d.checks||{};
    var bad=Object.keys(c).filter(function(k){ return !c[k].ok; });
    el.textContent = bad.length ? ("health \u2717 "+bad.join(",")) : "health \u2713";
    el.className = "pill " + (bad.length ? "bad" : "ok");
  }catch(e){
    el.textContent="health \u2717"; el.className="pill bad";
  }
}

async function tick(){
  let d;
  try{ d=await (await fetch("/api/all",{cache:"no-store"})).json(); }
  catch(e){ $("live").textContent="offline"; $("live").className="pill bad"; return; }
  const es=d.engines||[];
  try{
  const live=es.filter(e=>e.alive).length;
  $("live").textContent=live+"/"+es.length+" live";
  $("live").className="pill "+(live===es.length?"ok":"bad");
  $("clock").textContent=new Date().toLocaleTimeString(undefined,{hour12:false});

  const m=d.models;
  $("models").textContent = m ? ("models: "+JSON.stringify(m.decisions||{})) : "models offline";

  // totals across engines
  const alive=es.filter(e=>e.alive&&e.equity!=null);
  const eq=alive.reduce((a,e)=>a+(e.equity||0),0);
  const base=alive.length*2000;
  const real=alive.reduce((a,e)=>a+(e.realised||0),0);
  const unreal=alive.reduce((a,e)=>a+(e.unrealised||0),0);
  const open=alive.reduce((a,e)=>a+(e.positions||[]).length,0);
  const closed=alive.reduce((a,e)=>a+(e.closed||0),0);
  $("totals").innerHTML=[
    ["combined equity","$"+money(eq)],
    ["combined return",pct(base?100*(eq/base-1):null)],
    ["realised","$"+money(real)],
    ["unrealised","$"+money(unreal)],
    ["open positions",open],
    ["closed trades",closed],
  ].map(([k,v])=>`<div class="kpi"><div class="k">${k}</div><div class="v">${v}</div></div>`).join("");

  LAST=es;
  try{ histPush(es); }catch(e){}
  const groups=[
    ["Core engines", ["turtle","swing"]],
    ["Model ensembles", ["multimodels","statsmodels","swing_turtle"]],
    ["Turtle timeframes", ["t10s","t20s","t45s","t1m","t2m"]],
  ];
  const placed=new Set();
  const card=(e)=>{
    const pos=(e.positions||[]).slice(0,5);
    const scanning=(PREV[e.key]!=null&&e.scans!=null&&e.scans>PREV[e.key]);
    if(e.scans!=null) PREV[e.key]=e.scans;
    return `<div class="card ${e.alive?"":"off"} ${scanning?"scanning":""}"
        onclick="openDetail('${e.key}')" title="click for trades, positions, risk, rationale">
      <div class="top">
        <div><div class="name" style="color:${e.colour}">${esc(e.key)}</div>
             <div class="label">${esc(e.label)}</div></div>
        <div style="text-align:right">
          <div class="ret ${cls(e.return_pct)}">${pct(e.return_pct)}</div>
          <button class="force" onclick="event.stopPropagation();forceScan('${e.key}')">force</button>
        </div>
      </div>
      <div class="scanline"></div>
      <div class="rows">
        <span>equity <b>$${money(e.equity)}</b></span>
        <span>real <b class="${cls(e.realised)}">${money(e.realised)}</b></span>
        <span>unreal <b class="${cls(e.unrealised)}">${money(e.unrealised)}</b></span>
        <span>closed <b>${e.closed??"—"}</b></span>
        <span>open <b>${(e.positions||[]).length}</b></span>
      </div>
      <div class="body">
      ${pos.length?`<table><thead><tr><th>ticker</th><th>side</th><th>P&L</th><th>held</th></tr></thead>
        <tbody>${pos.map(p=>`<tr><td class="tick" data-sym="${esc(p.symbol)}" data-eng="${esc(e.key)}">${esc(p.symbol)}</td>
          <td><span class="side ${p.side==='buy'?'buy':'sell'}">${esc((p.side||'?').toUpperCase())}</span></td>
          <td class="${cls(p.gross_pct)}">${pct(p.gross_pct)}</td>
          <td>${Math.round(p.held_s||0)}s</td></tr>`).join("")}</tbody></table>`
        :'<div class="empty">flat</div>'}
      </div>
    </div>`;
  };
  let html="";
  for(const [title,keys] of groups){
    const items=es.filter(e=>keys.includes(e.key));
    items.forEach(e=>placed.add(e.key));
    if(!items.length) continue;
    html+=`<div class="section"><h2>${title}</h2><div class="grid">${items.map(card).join("")}</div></div>`;
  }
  const rest=es.filter(e=>!placed.has(e.key));
  if(rest.length) html+=`<div class="section"><h2>Other</h2><div class="grid">${rest.map(card).join("")}</div></div>`;
  $("sections").innerHTML=html;

  // model aggregate panel
  if(m){
    const rows=(m.rows||[]).slice(0,10);
    $("modelspanel").innerHTML=`
      <div class="rows" style="margin-top:0">
        <span>symbols <b>${m.symbols??"—"}</b></span>
        <span>signals <b>${m.signals_in_window??"—"}</b></span>
        <span>alerts <b>${m.alerts_in_window??"—"}</b></span>
        <span>decisions <b>${JSON.stringify(m.decisions||{})}</b></span>
      </div>
      ${rows.length?`<table class="wide"><thead><tr>
        <th>ticker</th><th>decision</th><th>net</th><th>agree</th>
        <th>price</th><th>volatility</th><th>volume 24h</th><th>liquidity</th><th>market cap</th>
        <th style="text-align:left">voted</th></tr></thead><tbody>
        ${rows.map(r=>{const m=(MKT&&MKT.get)?(MKT.get(r.symbol)||{}):{};
          const fmt=(v,p)=>v==null?"—":p?v.toFixed(2):money(v);
          return `<tr class="sigrow" data-sym="${esc(r.symbol)}"
                      onclick="zoomToSignal('${esc(r.symbol)}')"
                      title="open ${esc(r.symbol)} chart at its last signal">
          <td class="tick" data-sym="${esc(r.symbol)}">${esc(r.symbol)}</td>
          <td class="${r.net>0?'up':r.net<0?'down':'flat'}">${esc(r.decision)}</td>
          <td>${(r.net>=0?"+":"")+Number(r.net).toFixed(2)}</td>
          <td>${r.agreement}/${r.models_heard}</td>
          <td>${m.price==null?"—":m.price.toPrecision(7)}</td>
          <td class="${(m.vol||0)>8?'down':'flat'}">${m.vol==null?"—":m.vol.toFixed(1)+"%"}</td>
          <td>${m.volume==null?"—":money(m.volume)}</td>
          <td>${m.liquidity==null?"—":"$"+money(m.liquidity)}</td>
          <td>${m.mcap==null?"—":"$"+(m.mcap/1e9).toFixed(1)+"B"}</td>
          <td style="text-align:left;color:#8a99ad">${esc((r.contributors||[]).map(c=>c.model).join(", "))}</td>
        </tr>`;}).join("")}</tbody></table>`:'<div class="empty">no aggregate rows</div>'}`;
  }else{
    $("modelspanel").innerHTML='<div class="empty">model aggregate offline</div>';
  }

  $("foot").textContent="one page, every engine · updated "+
    new Date(d.ts).toLocaleTimeString(undefined,{hour12:false})+" · paper only, no keys, no orders";
  }catch(err){
    var b=$("err");
    if(b){ b.style.display="block"; b.textContent="render error: "+(err&&err.message||err); }
  }
}
let CHART={sym:null, iv:"5m"};
/* TICKERS declared earlier with the other globals */
/* MKT declared earlier with the other globals */
let PENDING_TS=null;
async function loadTickers(){
  try{
    const d=await (await fetch("/api/tickers",{cache:"no-store"})).json();
    TICKERS=d.tickers||[];
    MKT=new Map(TICKERS.map(t=>[t.s,t]));
    const el=$("mcount"); if(el) el.textContent=TICKERS.length+" tickers";
  }catch(e){}
}
async function applyParams(key){
  const g=id=>{const el=document.getElementById(id);return el&&el.value!==""?parseFloat(el.value):null;};
  const body={risk_pct:g("p-risk"),tp_pct:g("p-tp"),sl_pct:g("p-sl"),
              max_positions:g("p-max"),max_open_risk_pct:g("p-ceil")};
  const st=document.getElementById("pstat-"+key);
  if(st) st.textContent="applying…";
  try{
    const r=await fetch("/api/engine-control?engine="+encodeURIComponent(key),
      {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    const d=await r.json();
    if(st) st.textContent = r.ok ? "applied · takes effect next scan" : ("rejected: "+(d.error||r.status));
  }catch(e){ if(st) st.textContent="failed"; }
}
function closeModal(){ $("modal").classList.remove("open"); }
function gauge(p, sl, tp){
  const lo=-(sl||1), hi=(tp||1);
  const v=Math.max(lo,Math.min(hi,p||0));
  const pct=((v-lo)/(hi-lo))*100;
  const pos=p>=0;
  return `<div class="gauge"><i style="left:${Math.min(50,pct)}%;width:${Math.abs(pct-50)}%;
          background:${pos?'#3fb950':'#f85149'};opacity:.85"></i></div>`;
}
function openDetail(key){
  const e=(LAST||[]).find(x=>x.key===key);
  if(!e) return;
  const pos=(e.positions||[]).slice().sort((a,b)=>(b.gross_pct||0)-(a.gross_pct||0));
  const trades=(e.trades||e.recent||[]).slice().reverse();
  const tp=e.tp, sl=e.sl, riskPct=(e.risk_usd!=null&&e.equity)?(100*e.risk_usd/e.equity):null;
  const wr=(e.wins!=null&&e.closed)?(100*e.wins/e.closed):null;
  const avg=trades.length?trades.reduce((a,t)=>a+(t.net_pct||0),0)/trades.length:null;
  const wins=trades.filter(t=>(t.net_pct||0)>0).length;
  const best=trades.length?Math.max(...trades.map(t=>t.net_pct||0)):null;
  const worst=trades.length?Math.min(...trades.map(t=>t.net_pct||0)):null;

  $("sheet").innerHTML=`
    <div class="head">
      <span style="position:absolute;left:0;top:0;bottom:0;width:5px;background:${e.colour}"></span>
      <div style="display:flex;align-items:baseline;gap:12px;flex-wrap:wrap">
        <h2 style="color:${e.colour}">${esc(e.key)}</h2>
        <span class="label" style="font-size:11.5px">${esc(e.label)}</span>
        ${e.basis?`<span class="label">· ${esc(e.basis)}</span>`:""}
      </div>
      <div class="sub">${e.alive?"live":"offline"}${e.scans!=null?" · scan #"+e.scans:""}${e.closed!=null?" · "+e.closed+" closed":""}</div>
      <div class="big ${cls(e.return_pct)}">${pct(e.return_pct)}</div>
      <div class="close2" onclick="closeModal()">×</div>
    </div>

    <div class="body2">
      <div class="tiles">
        <div class="tile"><div class="k">equity</div><div class="v">$${money(e.equity)}</div></div>
        <div class="tile"><div class="k">realised</div><div class="v ${cls(e.realised)}">$${money(e.realised)}</div></div>
        <div class="tile"><div class="k">unrealised</div><div class="v ${cls(e.unrealised)}">$${money(e.unrealised)}</div></div>
        <div class="tile"><div class="k">win rate</div><div class="v">${wr==null?"—":wr.toFixed(0)+"%"}</div></div>
        <div class="tile"><div class="k">avg net</div><div class="v ${cls(avg)}">${avg==null?"—":pct(avg)}</div></div>
        <div class="tile"><div class="k">best / worst</div><div class="v" style="font-size:13px">
          <span class="up">${best==null?"—":pct(best)}</span> / <span class="down">${worst==null?"—":pct(worst)}</span></div></div>
      </div>

      <div class="expo">
        <div class="lbl"><span>open risk · ${pos.length} positions</span>
          <span>$${money(e.risk_usd)} · ${riskPct==null?"—":riskPct.toFixed(1)+"% of equity"}</span></div>
        <div class="track"><i style="width:${Math.min(100,riskPct||0)}%"></i></div>
      </div>

      <div class="hd"><h3>Settings</h3>
        <span class="count">${Object.keys(e.settings||{}).length} live rules</span></div>
      <div class="setpage">
        ${settingsGroups(e).map(g=>`
          <div class="sgroup">
            <div class="sgt">${g.title}</div>
            <div class="sfields">
              ${g.fields.map(f=>`
                <label class="sf">
                  <span>${f.label}</span>
                  <input type="number" step="${f.step}" min="${f.min}" max="${f.max}"
                         id="set-${f.key}" value="${e.settings[f.key] ?? ""}"
                         ${e.settings[f.key]==null?"disabled":""}>
                  <em>${f.hint||""}</em>
                </label>`).join("")}
            </div>
          </div>`).join("")}
        <div class="sapply">
          <button class="applybtn" onclick="applySettings('${e.key}')">Apply settings</button>
          <span class="mk" id="sstat-${e.key}"></span>
        </div>
      </div>

      <div class="hd"><h3>Open positions</h3><span class="count">${pos.length}</span></div>
      ${pos.length?`<table class="wide"><thead><tr>
          <th>ticker</th><th>side</th><th>entry</th><th>now</th>
          <th>P&amp;L</th><th>peak</th><th>stop/trail</th><th>notional</th>
          <th>risk $</th><th>held</th><th>conf</th><th style="text-align:left">why it is open</th>
        </tr></thead><tbody>${pos.map(p=>`<tr>
          <td class="tick" data-sym="${esc(p.symbol)}" data-eng="${esc(e.key)}">${esc(p.symbol)}</td>
          <td><span class="side ${p.side==='buy'?'buy':'sell'}">${esc((p.side||'?').toUpperCase())}</span></td>
          <td>${p.entry==null?"—":Number(p.entry).toPrecision(8)}</td>
          <td>${p.price==null?"—":Number(p.price).toPrecision(8)}</td>
          <td class="${cls(p.gross_pct)}"><b>${pct(p.gross_pct)}</b></td>
          <td class="mini">${pct(p.peak_pct)}</td>
          <td class="mini">${pct(p.trail_line??p.stop)}</td>
          <td>${p.notional==null?"—":"$"+money(p.notional)}</td>
          <td>${p.risk_usd==null?"—":"$"+money(p.risk_usd)}</td>
          <td class="mini">${Math.round(p.held_s||0)}s</td>
          <td class="mini">${p.conf==null?"—":(100*p.conf).toFixed(0)+"%"}</td>
          <td class="why2">${esc(p.why||"—")}</td>
        </tr>`).join("")}</tbody></table>`
        :'<div class="empty">flat — no open positions</div>'}

      <div class="hd"><h3>Trades</h3><span class="count">${trades.length}</span></div>
      ${trades.length?`<table class="wide"><thead><tr>
          <th>ticker</th><th>side</th><th>entry</th><th>exit</th>
          <th>gross</th><th>net</th><th>peak</th><th>P&amp;L $</th>
          <th>notional</th><th>reason</th><th>held</th>
        </tr></thead><tbody>${trades.slice(0,40).map(t=>`<tr>
          <td class="tick" data-sym="${esc(t.symbol)}" data-eng="${esc(e.key)}">${esc(t.symbol)}</td>
          <td><span class="side ${t.side==='buy'?'buy':'sell'}">${esc((t.side||'?').toUpperCase())}</span></td>
          <td>${t.entry==null?"—":Number(t.entry).toPrecision(8)}</td>
          <td>${t.exit==null?"—":Number(t.exit).toPrecision(8)}</td>
          <td class="mini">${pct(t.gross_pct)}</td>
          <td class="${cls(t.net_pct)}"><b>${pct(t.net_pct)}</b></td>
          <td class="mini">${pct(t.peak_pct)}</td>
          <td class="${cls(t.pnl_usd)}">${t.pnl_usd==null?"—":(t.pnl_usd>=0?"+":"")+"$"+money(t.pnl_usd)}</td>
          <td class="mini">${t.notional==null?"—":"$"+money(t.notional)}</td>
          <td class="mini">${esc(t.reason||"—")}</td>
          <td class="mini">${t.held_s==null?"—":Math.round(t.held_s)+"s"}</td>
        </tr>`).join("")}</tbody></table>`
        :'<div class="empty">no closed trades yet</div>'}
    </div>`;
  $("modal").classList.add("open");
}

document.addEventListener("keydown",e=>{
  if(e.key==="Escape"){
    closeChartModal(); closeModal(); closeChart();
    const c=$("cctl"); if(c&&c.classList.contains("open")){ c.classList.remove("open"); refreshCtlBtn(); }
    return;
  }
  if($("market")&&$("market").classList.contains("on")&&LASTROWS.length){
    const n=LASTROWS.length;
    if(e.key==="+"||e.key==="="){ e.preventDefault(); zoomAt((70+1200-86)/2,(VIEW.c||n)*0.8); }
    if(e.key==="-"||e.key==="_"){ e.preventDefault(); zoomAt((70+1200-86)/2,(VIEW.c||n)*1.25); }
    if(e.key==="ArrowLeft"){ e.preventDefault(); VIEW.s=Math.max(0,(VIEW.s||0)-Math.round((VIEW.c||n)*0.15)); drawMarket(); }
    if(e.key==="ArrowRight"){ e.preventDefault(); VIEW.s=Math.min(n-(VIEW.c||n),(VIEW.s||0)+Math.round((VIEW.c||n)*0.15)); drawMarket(); }
  }
});

// every ticker anywhere opens the chart, in one place
document.addEventListener("click", function(ev){
  const el=ev.target.closest(".tick");
  if(!el) return;
  ev.preventDefault();
  ev.stopPropagation();
  const sym=(el.dataset.sym||el.textContent||"").trim().toUpperCase();
  if(sym) openChart(sym, el.dataset.eng||null);
}, true);

function labelTickers(){
  document.querySelectorAll(".tick").forEach(el=>{
    const sym=(el.dataset.sym||el.textContent||"").trim().toUpperCase();
    if(sym && !el.title) el.title="Open "+sym+" chart";
  });
}
setInterval(labelTickers, 2000);
restoreSide();
loadTickers(); setInterval(loadTickers,60000);
tick(); setInterval(tick,2000);
health(); setInterval(health,5000);
</script></body></html>"""


BINANCE = "https://api.binance.com"


def binance_klines(symbol, interval="5m", limit=200):
    """Prefer the Go backend: it returns bars AND computed indicators."""
    try:
        d = jget("http://127.0.0.1:8787/api/klines?symbol=%s&interval=%s&limit=%d"
                 % (symbol, interval, limit), timeout=8.0)
        bars = d.get("bars") or []
        if bars:
            return {"rows": [{"t": b["t"], "o": b["o"], "h": b["h"],
                              "l": b["l"], "c": b["c"], "v": b.get("v") or 0.0}
                             for b in bars],
                    "indicators": d.get("indicators") or {}}
    except Exception:
        pass
    url = ("%s/api/v3/klines?interval=%s&limit=%d&symbol=%s"
           % (BINANCE, interval, limit, symbol))
    raw = jget(url, timeout=6.0)
    return {"rows": [{"t": int(x[0]), "o": float(x[1]), "h": float(x[2]),
                      "l": float(x[3]), "c": float(x[4]), "v": float(x[5])} for x in raw],
            "indicators": {}}


class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        from urllib.parse import parse_qs, urlparse
        if self.path.split("?")[0] == "/api/control-all":
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
            except Exception as exc:
                return self._send(400, json.dumps({"error": str(exc)}), "application/json")
            LIMITS = {"risk_pct": (0.05, 15.0), "tp_pct": (0.2, 10.0),
                      "sl_pct": (0.2, 8.0), "max_positions": (1, 40),
                      "min_agree": (1, 5), "max_open_risk_pct": (5, 100),
                      "min_confidence_pct": (0, 100), "hold_sec": (5, 3600),
                      "trail_ratio": (0, 1), "trail_arm_pct": (0, 10),
                      "lock_trade_pct": (0, 5), "lock_profit_pct": (0, 10),
                      "lock_usd": (0, 100000), "max_hold": (0, 86400),
                      "scan_every": (0.5, 600), "conviction_cap": (0.5, 5),
                      "vol_mult": (0, 10), "tolerance": (0, 0.05),
                      "min_stop_frac": (0.0005, 0.05)}
            clean = {"ts": int(time.time() * 1000)}
            for k, v in body.items():
                if k == "ts" or v in (None, ""):
                    continue
                if k not in LIMITS:
                    continue
                try:
                    f = float(v)
                except Exception:
                    continue
                lo, hi = LIMITS[k]
                if not (lo <= f <= hi):
                    return self._send(400, json.dumps(
                        {"error": "%s must be %s..%s" % (k, lo, hi)}), "application/json")
                clean[k] = f
            if len(clean) <= 1:
                return self._send(400, json.dumps({"error": "no valid rules supplied"}),
                                  "application/json")
            applied = {}
            for fname in ("control.json", "control_multi.json",
                          "control_stats.json", "control_turtle.json",
                          "control_t10s.json", "control_t20s.json",
                          "control_t45s.json", "control_t1m.json",
                          "control_t2m.json"):
                try:
                    path = os.path.join(HERE, "state", fname)
                    with open(path + ".tmp", "w") as fh:
                        json.dump(clean, fh)
                    os.replace(path + ".tmp", path)
                    applied[fname] = True
                except Exception:
                    applied[fname] = False
            # the Go turtle keeps its own config
            go_ok = False
            try:
                base = "http://127.0.0.1:8787"
                cfg = jget(base + "/api/turtle/config")["config"]
                upd = dict(cfg)
                if "risk_pct" in clean:
                    upd["risk_pct"] = round(clean["risk_pct"] / 100.0, 5)
                if "max_positions" in clean:
                    upd["max_units_total"] = int(clean["max_positions"])
                if 0 < upd.get("risk_pct", 0) <= 0.2:
                    data = json.dumps(upd).encode()
                    hdr = {"Content-Type": "application/json"}
                    try:
                        urllib.request.urlopen(urllib.request.Request(
                            base + "/api/turtle/config", data=data, headers=hdr,
                            method="POST"), timeout=6).read()
                    except urllib.error.HTTPError as he:
                        if he.code == 409:
                            for _act in ("stop", None, "start"):
                                try:
                                    if _act is None:
                                        urllib.request.urlopen(urllib.request.Request(
                                            base + "/api/turtle/config", data=data,
                                            headers=hdr, method="POST"), timeout=6).read()
                                    else:
                                        urllib.request.urlopen(urllib.request.Request(
                                            base + "/api/turtle?action=" + _act,
                                            data=b"{}", headers=hdr,
                                            method="POST"), timeout=10).read()
                                except Exception:
                                    pass
                        else:
                            raise
                    # never leave the session stopped after a config change
                    try:
                        st = jget(base + "/api/turtle", timeout=4.0)
                        if (st.get("state") or "").lower() != "running":
                            urllib.request.urlopen(urllib.request.Request(
                                base + "/api/turtle?action=start", data=b"{}",
                                headers=hdr, method="POST"), timeout=10).read()
                    except Exception:
                        pass
                    go_ok = True
            except Exception:
                go_ok = False
            return self._send(200, json.dumps({"applied": applied, "go_turtle": go_ok,
                                               "rules": len(clean) - 1}), "application/json")
        if self.path.split("?")[0] == "/api/engine-control":
            q = parse_qs(urlparse(self.path).query)
            engine = (q.get("engine") or [""])[0]
            if engine == "turtle":
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                    body = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
                except Exception as exc:
                    return self._send(400, json.dumps({"error": str(exc)}),
                                      "application/json")
                try:
                    base = "http://127.0.0.1:8787"
                    cfg = jget(base + "/api/turtle/config")["config"]
                    upd = dict(cfg)
                    for k in ("s1_entry", "s1_exit", "s2_entry", "s2_exit",
                              "max_units_total", "max_units_per_direction",
                              "max_units_per_market", "stop_n", "n_lookback",
                              "vol_veto_pct"):
                        if k in body and body[k] not in (None, ""):
                            upd[k] = int(float(body[k]))
                    if body.get("risk_pct") not in (None, ""):
                        upd["risk_pct"] = round(float(body["risk_pct"]) / 100.0, 5)
                    if not (0 < upd.get("risk_pct", 0) <= 0.2):
                        return self._send(400, json.dumps(
                            {"error": "risk_pct must be 0.05%%..20%% of equity"}),
                            "application/json")
                    data = json.dumps(upd).encode()
                    hdr = {"Content-Type": "application/json"}
                    req = urllib.request.Request(base + "/api/turtle/config", data=data,
                                                 headers=hdr, method="POST")
                    try:
                        urllib.request.urlopen(req, timeout=6).read()
                    except urllib.error.HTTPError as he:
                        if he.code != 409:
                            raise
                        for act in ("stop", "start"):
                            r2 = urllib.request.Request(
                                base + "/api/turtle?action=" + act, data=b"{}",
                                headers=hdr, method="POST")
                            if act == "stop":
                                urllib.request.urlopen(r2, timeout=8).read()
                        req = urllib.request.Request(base + "/api/turtle/config", data=data,
                                                     headers=hdr, method="POST")
                        urllib.request.urlopen(req, timeout=6).read()
                        r3 = urllib.request.Request(base + "/api/turtle?action=start",
                                                    data=b"{}", headers=hdr, method="POST")
                        urllib.request.urlopen(r3, timeout=10).read()
                    try:
                        stt = jget(base + "/api/turtle", timeout=4.0)
                        if (stt.get("state") or "").lower() != "running":
                            urllib.request.urlopen(urllib.request.Request(
                                base + "/api/turtle?action=start", data=b"{}",
                                headers=hdr, method="POST"), timeout=10).read()
                    except Exception:
                        pass
                    return self._send(200, json.dumps(
                        {"engine": "turtle", "file": "go backend",
                         "applied": upd, "applied_count": len(upd)}), "application/json")
                except Exception as exc:
                    return self._send(502, json.dumps({"error": str(exc)}),
                                      "application/json")
            files = {"swing": "control.json", "multimodels": "control_multi.json",
                     "statsmodels": "control_stats.json",
                     "t10s": "control_t10s.json", "t20s": "control_t20s.json",
                     "t45s": "control_t45s.json", "t1m": "control_t1m.json",
                     "t2m": "control_t2m.json"}
            fname = files.get(engine, "control_turtle.json")
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
            except Exception as exc:
                return self._send(400, json.dumps({"error": str(exc)}), "application/json")
            clean = {"ts": int(time.time() * 1000)}
            for k, lo, hi in (("risk_pct", 0.05, 15.0), ("tp_pct", 0.2, 10.0),
                              ("sl_pct", 0.2, 8.0), ("max_positions", 1, 40),
                              ("min_agree", 1, 5), ("max_open_risk_pct", 5, 100),
                              ("min_confidence_pct", 0, 100), ("hold_sec", 5, 3600),
                              ("trail_ratio", 0, 1), ("trail_arm_pct", 0, 10),
                              ("min_liquidity", 0, 5e9),
                              ("lock_trade_pct", 0, 5), ("lock_profit_pct", 0, 10),
                              ("lock_usd", 0, 100000), ("max_hold", 0, 86400),
                              ("scan_every", 0.5, 600), ("conviction_cap", 0.5, 5),
                              ("vol_mult", 0, 10), ("tolerance", 0, 0.05),
                              ("min_stop_frac", 0.0005, 0.05)):
                if k in body and body[k] is not None:
                    try:
                        v = float(body[k])
                    except Exception:
                        return self._send(400, json.dumps({"error": "%s not a number" % k}),
                                          "application/json")
                    if not (lo <= v <= hi):
                        return self._send(400, json.dumps(
                            {"error": "%s must be %s..%s" % (k, lo, hi)}), "application/json")
                    clean[k] = v
            try:
                path = os.path.join(HERE, "state", fname)
                with open(path + ".tmp", "w") as fh:
                    json.dump(clean, fh)
                os.replace(path + ".tmp", path)
            except Exception as exc:
                return self._send(500, json.dumps({"error": str(exc)}), "application/json")
            return self._send(200, json.dumps({"engine": engine, "file": fname,
                                               "applied": clean,
                                               "applied_count": len(
                                                   [k for k in clean if k != "ts"])}),
                              "application/json")
        if self.path.split("?")[0] != "/api/force":
            return self._send(404, json.dumps({"error": "not found"}), "application/json")
        q = parse_qs(urlparse(self.path).query)
        engine = (q.get("engine") or ["all"])[0]
        targets = ([engine] if engine != "all"
                   else ["swing", "multimodels", "t0", "t1", "t2", "t3", "t4"])
        made = []
        for t in targets:
            try:
                path = os.path.join(HERE, "state", "force_%s.json" % t)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w") as fh:
                    json.dump({"at": int(time.time() * 1000)}, fh)
                made.append(t)
            except Exception:
                pass
        return self._send(200, json.dumps({"forced": made}), "application/json")

    def do_GET(self):
        p = self.path.split("?")[0]
        if p in ("/", "/index.html"):
            return self._send(200, PAGE, "text/html")
        if p == "/api/klines":
            from urllib.parse import parse_qs, urlparse
            q = parse_qs(urlparse(self.path).query)
            sym = (q.get("symbol") or ["BTCUSDT"])[0]
            iv = (q.get("interval") or ["5m"])[0]
            try:
                k = binance_klines(sym, iv, 200)
                return self._send(200, json.dumps({"symbol": sym, "interval": iv,
                                                   "rows": k["rows"],
                                                   "indicators": k.get("indicators") or {}}),
                                  "application/json")
            except Exception as exc:
                return self._send(502, json.dumps({"error": str(exc)}), "application/json")
        if p == "/api/path":
            from urllib.parse import parse_qs, urlparse
            q = parse_qs(urlparse(self.path).query)
            sym = (q.get("symbol") or [""])[0]
            a = int((q.get("from") or ["0"])[0] or 0)
            b = int((q.get("to") or ["0"])[0] or 0)
            try:
                k = binance_klines(sym, (q.get("iv") or ["5m"])[0], 500)
                rows = [r for r in k["rows"] if a <= r["t"] <= (b or r["t"])]
                if not rows:
                    rows = k["rows"][-60:]
                step = max(1, len(rows) // 120)
                pts = [round(r["c"], 10) for r in rows][::step]
                return self._send(200, json.dumps(
                    {"symbol": sym, "points": pts,
                     "t0": rows[0]["t"], "t1": rows[-1]["t"]}), "application/json")
            except Exception as exc:
                return self._send(502, json.dumps({"error": str(exc)}), "application/json")
        if p == "/api/signals":
            from urllib.parse import parse_qs, urlparse
            q = parse_qs(urlparse(self.path).query)
            sym = (q.get("symbol") or [""])[0].upper()
            try:
                al = jget("http://127.0.0.1:8787/api/alerts?limit=800", timeout=6.0)
                ms = jget("http://127.0.0.1:8787/api/models/signals?limit=800", timeout=6.0)
                alerts = [a for a in (al.get("alerts") or []) if a.get("symbol") == sym]
                models = [x for x in (ms.get("signals") or []) if x.get("symbol") == sym]
                return self._send(200, json.dumps(
                    {"symbol": sym, "alerts": alerts, "models": models}),
                    "application/json")
            except Exception as exc:
                return self._send(502, json.dumps({"error": str(exc)}),
                                  "application/json")
        if p in ("/lab", "/lab/"):
            try:
                with open(os.path.join(HERE, "chart_simple.html"), "rb") as fh:
                    return self._send(200, fh.read(), "text/html")
            except Exception as exc:
                return self._send(500, json.dumps({"error": str(exc)}), "application/json")
        if p in ("/chart", "/chart/"):
            try:
                with open(os.path.join(HERE, "chart_real.html"), "rb") as fh:
                    return self._send(200, fh.read(), "text/html")
            except Exception as exc:
                return self._send(500, json.dumps({"error": str(exc)}), "application/json")
        if p == "/api/tickers":
            try:
                meta = market_meta()
                out = [{"s": sym, "v": m["liquidity"], "c": m["change"],
                        "price": m["price"], "vol": m["vol_pct"],
                        "qvol": m["volume"], "mcap": m["mcap"]}
                       for sym, m in meta.items()]
                out.sort(key=lambda x: -(x["v"] or 0))
                return self._send(200, json.dumps({"count": len(out), "tickers": out}),
                                  "application/json")
            except Exception as exc:
                return self._send(502, json.dumps({"error": str(exc)}), "application/json")
        if p in ("/healthz", "/api/health"):
            out = {"app": "ok", "checks": {}}
            def probe(name, url):
                try:
                    t0 = time.time()
                    d = jget(url, timeout=6.0)
                    out["checks"][name] = {"ok": True, "ms": int((time.time() - t0) * 1000),
                                           "detail": d}
                except Exception as exc:
                    out["checks"][name] = {"ok": False, "error": str(exc)[:120]}
            probe("backend", "http://127.0.0.1:8787/api/health")
            probe("klines", "http://127.0.0.1:8787/api/klines?symbol=BTCUSDT&interval=5m&limit=2")
            try:
                a = jget("http://127.0.0.1:8799/api/all", timeout=10.0)
                live = sum(1 for e in a.get("engines", []) if e.get("equity") is not None)
                out["checks"]["engines"] = {"ok": live > 0, "detail": {"live": live,
                                             "total": len(a.get("engines", []))}}
            except Exception as exc:
                out["checks"]["engines"] = {"ok": False, "error": str(exc)[:120]}
            ok = all(c.get("ok") for c in out["checks"].values())
            out["ok"] = ok
            return self._send(200 if ok else 503, json.dumps(out), "application/json")
        if p == "/api/all":
            try:
                return self._send(200, json.dumps(collect()), "application/json")
            except Exception as exc:
                return self._send(500, json.dumps({"error": str(exc)}), "application/json")
        if p == "/health":
            return self._send(200, json.dumps({"status": "ready"}), "application/json")
        return self._send(404, json.dumps({"error": "not found"}), "application/json")

    def log_message(self, *a):
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8799)
    args = ap.parse_args()
    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), H)
    print("MASTER dashboard: http://127.0.0.1:%d" % args.port, flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
