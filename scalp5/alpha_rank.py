#!/usr/bin/env python3
"""alpha_rank - trade the cross-section, not one field at a time.

Every other model asks "does this symbol pass my threshold". This one asks
"which symbols look best *relative to the rest right now*", which is the only
framing that uses the whole dataset.

  feed      subscribes to the signal bus (push, no polling)
  features  ranks each field across the universe and builds a vector
  score     weighted composite of those ranks
  act       long the top, short the bottom, maker entry, risk-governed
"""
import argparse, json, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import maker, risk, tactics as T
from lib import features
from lib.feed import Feed
from lib import bus as _bus
from lib import emit

STATE = os.path.join(HERE, "state")
BOOK = "alpha_rank"


class Book:
    def __init__(self, start, risk_pct):
        self.start, self.risk_pct = start, risk_pct
        self.equity = start
        self.pos, self.trades = {}, []

    def snap(self):
        return {"engine": BOOK, "mode": "paper-maker", "state": "running",
                "equity": round(self.equity, 2), "start_equity": self.start,
                "return_pct": round(100 * (self.equity / self.start - 1), 4),
                "realised": round(self.equity - self.start, 2), "unrealised": 0.0,
                "trade_count": len(self.trades),
                "positions": [{"symbol": s, "side": p["side"], "entry": p["entry"],
                               "notional": p["notional"], "entry_ms": int(p["t"] * 1000),
                               "held_s": time.time() - p["t"], "why": p["why"],
                               "feat": p.get("feat") or {},
                               "rank_score": p.get("rank_score")}
                              for s, p in self.pos.items()],
                "trades": self.trades[-200:]}


def write(bk):
    p = os.path.join(STATE, "pack_%s.json" % BOOK)
    with open(p + ".t", "w") as fh:
        json.dump(bk.snap(), fh)
    os.replace(p + ".t", p)


def log(m):
    print("%s  %s" % (time.strftime("%H:%M:%S"), m), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=float, default=5000.0)
    ap.add_argument("--risk", type=float, default=0.5)
    ap.add_argument("--every", type=float, default=15.0)
    ap.add_argument("--top-n", type=int, default=3)
    ap.add_argument("--min-score", type=float, default=0.35)
    ap.add_argument("--lock", type=float, default=1.0)
    ap.add_argument("--stop", type=float, default=1.5)
    ap.add_argument("--clock", type=float, default=3600)
    a = ap.parse_args()

    feed = Feed.instance().start()
    bk = Book(a.start, a.risk)
    log("ALPHA RANK — top/bottom %d of the cross-section, min score %.2f" % (a.top_n, a.min_score))

    while True:
        rows = list(feed.latest().values())
        if len(rows) < 4:
            write(bk)
            time.sleep(a.every)
            continue

        mat = features.matrix(rows)
        ranked = sorted(((sym, features.composite(f)) for sym, f in mat.items()),
                        key=lambda kv: kv[1], reverse=True)

        # ---- manage what is open
        for sym in list(bk.pos):
            p = bk.pos[sym]
            px = feed.price(sym)
            if not px:
                continue
            chg = (px / p["entry"] - 1) * 100 if p["side"] == "buy" else (1 - px / p["entry"]) * 100
            held = time.time() - p["t"]
            reason = ("lock" if chg >= a.lock else "stop" if chg <= -a.stop else
                      "clock" if held >= a.clock else None)
            if reason and T.hold_loser(held, chg):
                reason = None      # hold losers for a day, by directive
            if reason:
                net = chg - 0.04
                bk.equity += p["notional"] * net / 100
                try:
                    _bus.pub("model.pnl", sym, {"engine": BOOK, "equity": round(bk.equity, 2),
                                                "net_pct": round(net, 4), "symbol": sym},
                             BOOK)
                except Exception:
                    pass
                bk.trades.append({"symbol": sym, "side": p["side"], "entry": p["entry"],
                                  "exit": px, "gross_pct": chg, "net_pct": net,
                                  "peak_pct": max(0.0, chg), "reason": reason, "held_s": held,
                                  "entry_ms": int(p["t"] * 1000), "exit_ms": int(time.time() * 1000),
                                  "notional": p["notional"], "pnl_usd": p["notional"] * net / 100,
                                  "why": p["why"], "features": p.get("feat") or {},
                                  "rank_score": p.get("rank_score")})
                try:
                    emit.fill(BOOK, bk.trades[-1])
                except Exception:
                    pass
                log("CLOSE %-10s %-4s %+6.3f%% net %+6.3f%% %s" % (sym, p["side"], chg, net, reason))
                bk.pos.pop(sym)
        write(bk)

        if _bus:
            try:
                _bus.pub("model.rank", "xsec",
                         {"top": [{"symbol": sym, "score": round(sc, 3)} for sym, sc in ranked[:8]],
                          "bottom": [{"symbol": sym, "score": round(sc, 3)} for sym, sc in ranked[-8:]],
                          "ts": int(time.time() * 1000)}, BOOK)
            except Exception:
                pass
        # ---- targets: strongest longs, weakest shorts
        wanted = []
        for sym, sc in ranked[:a.top_n]:
            if sc >= a.min_score:
                wanted.append((sym, "buy", sc))
        for sym, sc in ranked[-a.top_n:]:
            if sc <= -a.min_score:
                if sc > -0.60:              # only the bottom of the cross-section
                    continue
                ok_s, why_s = T.short_ok(sym)
                if not ok_s:
                    try:
                        risk.record(sym, "sell", False, why_s)
                    except Exception:
                        pass
                    continue
                wanted.append((sym, "sell", sc))

        for sym, side, sc in wanted:
            if sym in bk.pos or len(bk.pos) >= 8:
                continue
            base = min(bk.equity * a.risk / 100 * 40, bk.equity * 0.08)
            conf = (feed.for_symbol(sym) or {}).get("flow_conf") or 0
            notional = risk.size_for(base, conf)
            _sig = feed.for_symbol(sym) or {}
            _age = (time.time() * 1000 - (_sig.get("ts") or 0)) / 1000.0 if _sig else None
            ok, why = risk.allow(sym, side, notional, bk.equity, confidence=conf,
                                 signal_age_s=_age,
              book_positions=list(bk.pos.values()))
            risk.record(sym, side, ok, why)
            try:
                emit.decision(BOOK, sym, side, ok, why, conf)
            except Exception:
                pass
            if not ok:
                continue
            px, prob, _ = maker.quote(sym, side, notional)
            if px is None or prob < 0.30:
                continue
            bk.pos[sym] = {"side": side, "entry": px, "t": time.time(),
                           "notional": notional, "feat": mat.get(sym) or {}, "rank_score": sc,
                           "why": "RANK %s score %+.2f (rank %d/%d) flow %+.0f" % (
                               side, sc, ranked.index((sym, sc)) + 1, len(ranked), conf)}
            log("OPEN  %-10s %-4s %.6g  rank score %+.2f" % (sym, side, px, sc))
            write(bk)

        if int(time.time()) % 60 < a.every:
            log("ranking: " + ", ".join("%s %+.2f" % (s, v) for s, v in ranked[:5]))
        time.sleep(a.every)


if __name__ == "__main__":
    main()
