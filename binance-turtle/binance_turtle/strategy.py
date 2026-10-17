"""Signal layer for the Binance SPOT Turtle system.

Implements, from KIMI_TRADING_PLAN.md / KIMI_ANSWERS2.md:

  * entries          plan s1.3  (+ answers s1/s2 overrides: ADX is a soft
                     score, never a veto; volume confirmation is mandatory
                     and fails closed)
  * exits + ratchet  plan s1.4  (stop = max(2N, trailing channel, best fill-N),
                     never retreats; time stop; re-anchor at +3N)
  * sizing           plan s1.5  (Wilder 0.5% risk/unit, participation cap,
                     alt-stop sanity cap)
  * portfolio        plan s1.6  (6 units, 2/cluster, 3% open risk, 2 entries/day)
  * BTC regime       answers s4 (latched 55d Donchian risk-off, no new longs)

No keys, no orders -- this layer only *decides*.
"""

import math

from . import indicators as ta
from .binance_api import floor_to_step, median


# --------------------------------------------------------------------------
# per-symbol prepared state
# --------------------------------------------------------------------------
class SymbolState(object):
    """Indicator state for one symbol on the daily (primary) frame plus an
    optional faster trigger frame."""

    def __init__(self, symbol, daily, trigger=None, cfg=None):
        self.symbol = symbol
        self.cfg = cfg
        self.daily = daily or []
        self.trigger = trigger or []
        self.ok = False
        self.reasons = []
        d = self.daily
        if len(d) < max(cfg.atr_period, cfg.ma_long_period // 2, 60):
            self.reasons.append("insufficient_history")
            return
        dh = [b["high"] for b in d]
        dl = [b["low"] for b in d]
        dc = [b["close"] for b in d]
        dv = [b["quote_volume"] for b in d]
        self.d_close = dc
        self.d_quote_vol = dv
        self.atr = ta.atr_wilder(dh, dl, dc, cfg.atr_period)
        self.atr_pct = [(self.atr[i] / dc[i]) if (self.atr[i] and dc[i]) else None
                        for i in range(len(dc))]
        self.atr_rank = ta.percentile_rank(self.atr_pct, cfg.atr_rank_window)
        self.dc_up, self.dc_dn = ta.donchian_prior(dh, dl, cfg.primary_entry_bars)
        self.adx, self.pdi, self.mdi = ta.adx_dmi(dh, dl, dc, cfg.adx_period)
        self.rsi = ta.rsi_wilder(dc, cfg.rsi_period)
        self.sma_long = ta.sma(dc, cfg.ma_long_period)
        self.realized_vol = ta.realized_vol(dc, cfg.btc_vol_window)

        self.t_close = []
        self.t_quote_vol = []
        self.tc_up = None
        self.tc_dn = None
        self.t_exit_dn = None
        self.t_adx = self.t_pdi = self.t_mdi = None
        if self.trigger:
            th = [b["high"] for b in self.trigger]
            tl = [b["low"] for b in self.trigger]
            tc = [b["close"] for b in self.trigger]
            tv = [b["quote_volume"] for b in self.trigger]
            self.t_close = tc
            self.t_quote_vol = tv
            self.tc_up, _ = ta.donchian_prior(th, tl, cfg.trigger_entry_bars)
            _, self.t_exit_dn = ta.donchian_prior(th, tl, cfg.trigger_exit_bars)
            self.tc_dn = ta.lowest(tl, cfg.trigger_exit_bars)
            self.t_adx, self.t_pdi, self.t_mdi = ta.adx_dmi(th, tl, tc, cfg.adx_period)
        self.dc_exit_dn = ta.lowest(dl, cfg.primary_exit_bars)
        self.ok = True

    # -- convenience ------------------------------------------------------
    def last(self, seq, offset=0):
        if not seq:
            return None
        idx = len(seq) - 1 - offset
        if idx < 0:
            return None
        return seq[idx]

    def atr_pct_now(self):
        return self.last(self.atr_pct)

    def volatility_ok(self):
        pct = self.atr_pct_now()
        if pct is None:
            return False, "no_atr"
        if pct < self.cfg.atr_pct_min:
            return False, "atr_pct_below_band(%.4f)" % pct
        if pct > self.cfg.atr_pct_max:
            return False, "atr_pct_above_band(%.4f)" % pct
        return True, "atr_pct=%.4f" % pct

    def blowoff(self):
        rsi = self.last(self.rsi)
        sma = self.last(self.sma_long)
        px = self.last(self.d_close)
        if rsi is None or sma is None or px is None:
            return False, "no_data"
        if rsi > self.cfg.blowoff_rsi and px > self.cfg.blowoff_ma_mult * sma:
            return True, "rsi=%.1f px/sma200=%.2f" % (rsi, px / sma)
        return False, ""

    def median_hourly_quote_volume(self):
        """Median hourly traded quote volume, used by the sizing cap."""
        vols = [b["quote_volume"] for b in self.trigger[-24 * 7:]] if self.trigger else []
        if not vols:
            vols = [b["quote_volume"] for b in self.daily[-30:]]
            med = median(vols)
            return (med / 24.0) if med else None
        med = median(vols)
        return (med / 4.0) if med else None

    def adx_score(self, frame="daily"):
        if frame == "trigger":
            adx, pdi, mdi = self.last(self.t_adx), self.last(self.t_pdi), self.last(self.t_mdi)
        else:
            adx, pdi, mdi = self.last(self.adx), self.last(self.pdi), self.last(self.mdi)
        if adx is None or pdi is None or mdi is None or pdi <= mdi:
            return 0.0, adx
        lo, hi = self.cfg.adx_score_lo, self.cfg.adx_score_hi
        return max(0.0, min(1.0, (adx - lo) / (hi - lo))), adx


# --------------------------------------------------------------------------
# entries (plan s1.3)
# --------------------------------------------------------------------------
class Setup(object):
    __slots__ = ("symbol", "system", "score", "price", "atr", "atr_pct",
                 "entry_level", "vol_mult", "adx", "reasons", "order_qty_hint",
                 "stop", "notional")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))

    def as_dict(self):
        return {k: getattr(self, k) for k in self.__slots__}


def evaluate_entry(state, system, cfg, btc_risk_off=False, onset_veto=False,
                   funding_veto=False):
    """Returns (Setup or None, list_of_reject_reasons).

    ``system`` is "trigger" (4h/120) or "primary" (1d/55).
    """
    rejects = []
    if not state.ok:
        return None, ["not_ready"]

    if system == "trigger":
        closes = state.t_close
        vols = state.t_quote_vol
        upper = state.tc_up
        i = len(closes) - 1
        bars = state.trigger
        adx_frame = "trigger"
        entry_level = state.last(upper)
    else:
        closes = state.d_close
        vols = state.d_quote_vol
        upper = state.dc_up
        i = len(closes) - 1
        bars = state.daily
        adx_frame = "daily"
        entry_level = state.last(upper)

    if entry_level is None or i < 1:
        return None, ["no_channel"]

    # 1. structural breakout on the completed bar
    if not (closes[i] > entry_level):
        rejects.append("no_breakout(close=%.8g<=level=%.8g)" % (closes[i], entry_level))

    # 2. volume confirmation -- mandatory, fails closed (answers s2)
    confirmed, med = ta.volume_confirmed(vols, i, cfg)
    if not confirmed:
        rejects.append("volume_unconfirmed(vol=%.0f,med=%s)" % (vols[i], med))

    # 3. volatility band (plan s1.1)
    vol_ok, vol_note = state.volatility_ok()
    if not vol_ok:
        rejects.append(vol_note)

    # 4. blow-off filter (plan s4)
    blow, blow_note = state.blowoff()
    if blow:
        rejects.append("blowoff(" + blow_note + ")")

    # 5. BTC regime gate -- blocks all new alt longs (answers s4)
    if btc_risk_off:
        rejects.append("btc_risk_off")

    # 6. onset-layer veto (plan s1.3 rule 5); inert without flow.py
    if onset_veto:
        rejects.append("onset_veto_downside")

    # 7. funding/OI crowded-long veto (plan s4 / s6.5)
    if funding_veto:
        rejects.append("funding_veto")

    # NOTE: ADX is deliberately NOT in the reject list.  The plan made it a
    # gate; answers s1 deleted that branch.  It scores only.
    adx_score, adx_val = state.adx_score(adx_frame)

    if rejects:
        return None, rejects

    vol_multiple = (vols[i] / med) if med else None
    score = 0.0
    score += cfg.adx_weight * adx_score
    if vol_multiple:
        score += 0.45 * max(0.0, min(1.0, (vol_multiple - cfg.vol_mult) / 3.0))
    rank = state.last(state.atr_rank)
    if rank is not None:
        score += 0.25 * rank
    score += 0.20 * (1.0 if system == "primary" else 0.5)

    atr = state.last(state.atr)
    price = closes[i]
    stop = price - cfg.stop_atr_mult * atr if atr else None
    return Setup(
        symbol=state.symbol, system=system, score=round(score, 4),
        price=price, atr=atr, atr_pct=state.atr_pct_now(),
        entry_level=entry_level,
        vol_mult=round(vol_multiple, 3) if vol_multiple else None,
        adx=round(adx_val, 2) if adx_val is not None else None,
        reasons=["breakout", "volume_confirmed", "in_band"],
        stop=stop, order_qty_hint=None, notional=None,
    ), []


# --------------------------------------------------------------------------
# sizing (plan s1.5)
# --------------------------------------------------------------------------
def size_unit(equity, atr, price, lot_step, median_hourly_qv, cfg,
              risk_per_unit=None):
    """Wilder unit size with the two crypto-specific caps."""
    if not atr or atr <= 0 or not price:
        return 0.0, {"reason": "bad_inputs"}
    risk = risk_per_unit if risk_per_unit is not None else cfg.risk_per_unit
    qty = equity * risk / atr
    notes = {}
    stop_frac = (cfg.stop_atr_mult * atr) / price
    if stop_frac > cfg.alt_stop_sanity:
        qty *= 0.5
        notes["alt_stop_sanity"] = round(stop_frac, 4)
    if median_hourly_qv:
        cap_notional = (cfg.participation_cap * median_hourly_qv *
                        (cfg.exit_minutes_at_median_volume / 60.0))
        capped = cap_notional / price
        if capped < qty:
            qty = capped
            notes["participation_cap"] = round(cap_notional, 2)
    qty = floor_to_step(qty, lot_step)
    notes["risk_per_unit"] = risk
    notes["notional"] = round(qty * price, 2)
    return qty, notes


# --------------------------------------------------------------------------
# BTC regime latch (answers s4)
# --------------------------------------------------------------------------
class BTCRegime(object):
    """Latched risk-off: enter on a daily close below the prior 55-day low,
    exit only on a daily close above the prior 55-day high."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.risk_off = False
        self.history = []

    def update(self, daily_bars):
        cfg = self.cfg
        if len(daily_bars) < cfg.btc_donchian_bars + 1:
            return self.risk_off
        highs = [b["high"] for b in daily_bars]
        lows = [b["low"] for b in daily_bars]
        closes = [b["close"] for b in daily_bars]
        up, dn = ta.donchian_prior(highs, lows, cfg.btc_donchian_bars)
        i = len(closes) - 1
        if up[i] is None or dn[i] is None:
            return self.risk_off
        close = closes[i]
        changed = False
        if not self.risk_off and close < dn[i]:
            self.risk_off = True
            changed = True
        elif self.risk_off and close > up[i]:
            self.risk_off = False
            changed = True
        if changed or not self.history:
            self.history.append({
                "time": daily_bars[i]["open_time"],
                "close": close,
                "up": up[i], "dn": dn[i],
                "risk_off": self.risk_off,
            })
        return self.risk_off

    def realized_vol(self, daily_bars):
        cfg = self.cfg
        closes = [b["close"] for b in daily_bars]
        rv = ta.realized_vol(closes, cfg.btc_vol_window)
        return rv[-1] if rv else None

    def derisk(self, daily_bars):
        rv = self.realized_vol(daily_bars)
        return bool(rv is not None and rv > self.cfg.btc_vol_derisk_annualized), rv


# --------------------------------------------------------------------------
# portfolio governor (plan s1.6)
# --------------------------------------------------------------------------
def correlation_clusters(states, cfg):
    """Greedy clustering of daily log returns at ``cluster_corr_threshold``.

    Stands in for the groups `survey.py` computes (plan s1.6).
    """
    window = cfg.cluster_return_window
    rets = {}
    for sym, st in states.items():
        if not st.ok:
            continue
        closes = st.d_close[-window - 1:]
        if len(closes) < window + 1:
            continue
        rets[sym] = [math.log(closes[i] / closes[i - 1])
                     for i in range(1, len(closes))]

    def corr(a, b):
        n = min(len(a), len(b))
        if n < 5:
            return 0.0
        a, b = a[-n:], b[-n:]
        ma, mb = sum(a) / n, sum(b) / n
        va = sum((x - ma) ** 2 for x in a)
        vb = sum((x - mb) ** 2 for x in b)
        if va <= 0 or vb <= 0:
            return 0.0
        cov = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
        return cov / math.sqrt(va * vb)

    clusters = []
    for sym in sorted(rets, key=lambda s: -len(rets[s])):
        placed = False
        for cl in clusters:
            if corr(rets[sym], rets[cl[0]]) >= cfg.cluster_corr_threshold:
                cl.append(sym)
                placed = True
                break
        if not placed:
            clusters.append([sym])
    return {sym: i for i, cl in enumerate(clusters) for sym in cl}, clusters


class PortfolioGovernor(object):
    """Hard portfolio caps; the binding one is total open risk <= 3%."""

    def __init__(self, cfg, equity, clusters=None):
        self.cfg = cfg
        self.equity = equity
        self.clusters = clusters or {}
        self.positions = {}          # symbol -> dict(units, cluster)
        self.entries_today = []
        self.blocked = []

    def units_total(self):
        return sum(p["units"] for p in self.positions.values())

    def units_in_cluster(self, cluster):
        return sum(p["units"] for p in self.positions.values()
                   if p.get("cluster") == cluster)

    def open_risk_pct(self, marks):
        total = 0.0
        for sym, pos in self.positions.items():
            px = marks.get(sym, pos["avg_price"])
            stop = pos["stop"]
            total += max(0.0, (px - stop)) * pos["qty"]
        return total / self.equity if self.equity else 0.0

    def can_open(self, symbol, qty, price, stop, now_ts, marks):
        cfg = self.cfg
        reasons = []
        if symbol in self.positions:
            reasons.append("already_in_symbol")
        if self.units_total() >= cfg.max_units_total:
            reasons.append("max_units_total")
        cl = self.clusters.get(symbol)
        if cl is not None and self.units_in_cluster(cl) >= cfg.max_units_per_cluster:
            reasons.append("max_units_cluster")
        day = now_ts // 86_400_000
        today = [t for t in self.entries_today if t // 86_400_000 == day]
        if len(today) >= cfg.max_new_entries_per_day:
            reasons.append("max_entries_per_day")
        risk = max(0.0, price - stop) * qty
        if self.open_risk_pct(marks) + risk / self.equity > cfg.max_open_risk_pct:
            reasons.append("max_open_risk_3pct")
        if reasons:
            self.blocked.append({"symbol": symbol, "reasons": reasons})
            return False, reasons
        return True, []

    def open(self, symbol, qty, price, stop, now_ts):
        self.positions[symbol] = {
            "units": 1, "qty": qty, "avg_price": price, "stop": stop,
            "cluster": self.clusters.get(symbol), "opened": now_ts,
        }
        self.entries_today.append(now_ts)

    def close(self, symbol):
        self.positions.pop(symbol, None)

    def pyramid(self, symbol, qty, price, new_stop):
        pos = self.positions[symbol]
        total_qty = pos["qty"] + qty
        pos["avg_price"] = (pos["avg_price"] * pos["qty"] + price * qty) / total_qty
        pos["qty"] = total_qty
        pos["units"] += 1
        pos["stop"] = max(pos["stop"], new_stop)


# --------------------------------------------------------------------------
# exit state machine (plan s1.4)
# --------------------------------------------------------------------------
class Position(object):
    """Turtle exit rules with the exact, never-retreating ratchet."""

    def __init__(self, symbol, system, entry_time, entry_price, atr, qty, cfg,
                 stop=None):
        self.symbol = symbol
        self.system = system
        self.entry_time = entry_time
        self.entry_price = entry_price
        self.n_atr = atr                 # daily ATR at entry (sizing N)
        self.qty = qty
        self.units = 1
        self.highest_fill = entry_price
        self.stop = stop if stop is not None else entry_price - cfg.stop_atr_mult * atr
        self.initial_stop = self.stop
        self.bars_held = 0
        self.exit_reason = None
        self.exit_price = None
        self.exit_time = None
        self.realized_r = None

    def risk_per_unit(self, cfg):
        return cfg.stop_atr_mult * self.n_atr * self.qty

    def update_ratchet(self, cfg, channel_low, price):
        """stop = max(current, trailing channel, best fill - 2N).  Never retreats."""
        candidates = [self.stop]
        if channel_low is not None:
            candidates.append(channel_low)
        candidates.append(self.highest_fill - cfg.stop_atr_mult * self.n_atr)
        self.stop = max(candidates)

    def profit_atr(self, price):
        if not self.n_atr:
            return 0.0
        return (price - self.entry_price) / self.n_atr

    def add_unit(self, cfg, price, qty):
        total = self.qty + qty
        self.entry_price = (self.entry_price * self.qty + price * qty) / total
        self.qty = total
        self.units += 1
        self.highest_fill = max(self.highest_fill, price)

    def time_stop_hit(self, cfg, price):
        if self.bars_held < cfg.time_stop_bars or not self.n_atr:
            return False
        move = (price - self.entry_price) / self.n_atr
        return -cfg.time_stop_band_atr <= move <= cfg.time_stop_band_atr

    def can_pyramid(self, cfg, price):
        if not self.n_atr:
            return False
        cap = (cfg.max_units_primary_trade if self.system == "primary"
               else cfg.max_units_trigger_trade)
        if self.units >= cap:
            return False
        step = cfg.pyramid_step_atr * self.n_atr
        return price >= self.highest_fill + step

    def close(self, price, time, reason):
        self.exit_price = price
        self.exit_time = time
        self.exit_reason = reason
        risk = self.n_atr * 2.0 if self.n_atr else None
        if risk:
            self.realized_r = (price - self.entry_price) / risk
        return self
