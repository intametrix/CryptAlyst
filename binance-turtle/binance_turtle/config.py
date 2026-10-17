"""Parameter set for the Binance SPOT Turtle system.

Every field traces to a line of KIMI_TRADING_PLAN.md (the "plan") or
KIMI_ANSWERS2.md (the "answers").  Where the two documents disagree the
answers win -- they were written later and explicitly supersede.  Those
overrides are listed in CONFLICTS so nothing is silently changed.
"""

from dataclasses import dataclass, asdict

CONFLICTS = {
    "adx_is_soft_score_only":
        "plan s1.3 rule 2 makes ADX>=20 a hard gate; answers s1 deletes the "
        "veto branch and keeps ADX as a ~10% soft score component.",
    "min_quote_volume_24h":
        "plan s1.1 implies the existing $750k default; answers s3 adopts $5M.",
    "risk_per_unit":
        "plan s1.1 text mentions 1%; answers s3 adopts 0.5% of equity.",
    "max_units_per_cluster":
        "plan s1.6 mentions 6 units per cluster in passing; answers s3 adopts 2.",
    "notional_cap":
        "plan s1.5 gives the cap in prose ('25% of median hourly quote volume "
        "/ 20'); implemented as the equivalent participation form "
        "cap = 5% of median hourly volume * (minutes_to_exit/60).",
}


@dataclass
class Config:
    # ---- universe / liquidity (plan s1.1, answers s3) -------------------
    quote: str = "USDT"
    min_quote_volume_24h: float = 5_000_000.0
    max_symbols: int = 400
    exclude_suffixes: tuple = ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")

    # ---- volatility band (plan s1.1) ------------------------------------
    atr_pct_min: float = 0.015
    atr_pct_max: float = 0.25

    # ---- timeframes (plan s1.2) -----------------------------------------
    # Primary = daily Turtle; trigger = 4h aggressive layer.  answers s1 of
    # the plan document recommends 55/20 daily primary with 120/60 on 4h
    # rather than the noisy classic 20/10 4h.
    primary_interval: str = "1d"
    trigger_interval: str = "4h"
    primary_entry_bars: int = 55
    primary_exit_bars: int = 20
    trigger_entry_bars: int = 120
    trigger_exit_bars: int = 60
    # once a trigger-framed trade is >= this many N in profit, re-anchor the
    # trailing channel to the daily frame (plan s1.4)
    reanchor_at_atr: float = 3.0

    # ---- indicators (plan s2/s4) ----------------------------------------
    atr_period: int = 20
    atr_rank_window: int = 182          # ~6 months of daily bars
    adx_period: int = 14
    adx_min: float = 20.0               # score plateau, NOT a veto
    adx_score_lo: float = 15.0
    adx_score_hi: float = 25.0
    adx_weight: float = 0.10
    rsi_period: int = 14
    blowoff_rsi: float = 85.0
    blowoff_ma_mult: float = 1.6
    ma_long_period: int = 200

    # ---- volume confirmation (answers s2) -------------------------------
    vol_median_bars: int = 20
    vol_mult: float = 1.5
    vol_median_floor: float = 20_000.0
    vol_abs_floor: float = 150_000.0
    monday_vol_mult: float = 10.0 / 8.0   # plan s1.2 notes Monday 00:00 UTC

    # ---- risk (plan s1.5/s1.6, answers s3) ------------------------------
    risk_per_unit: float = 0.005
    derisk_risk_per_unit: float = 0.0025  # vol-spike de-risk (plan s4)
    stop_atr_mult: float = 2.0
    pyramid_step_atr: float = 0.5
    max_units_total: int = 6
    max_units_per_cluster: int = 2
    max_units_per_symbol_at_entry: int = 1
    max_units_trigger_trade: int = 2
    max_units_primary_trade: int = 3
    max_open_risk_pct: float = 0.03
    max_new_entries_per_day: int = 2
    alt_stop_sanity: float = 0.30          # 2N > 30% of price -> halve unit
    participation_cap: float = 0.05        # never > 5% of median hourly flow
    exit_minutes_at_median_volume: float = 20.0

    # ---- time stop (plan s1.4) ------------------------------------------
    time_stop_bars: int = 10
    time_stop_band_atr: float = 0.5

    # ---- BTC regime (plan s1.1/s4, answers s4) --------------------------
    btc_symbol: str = "BTCUSDT"
    btc_donchian_bars: int = 55
    btc_vol_window: int = 20
    btc_vol_derisk_annualized: float = 1.20

    # ---- onset layer (plan s1.3 rule 5 / s1.4) --------------------------
    onset_veto_minutes: int = 60
    onset_exit_on_critical: bool = True

    # ---- costs (plan s5) ------------------------------------------------
    taker_fee: float = 0.001
    maker_fee: float = 0.00075             # BNB discount
    prefer_maker_entry: bool = True
    entry_slippage: float = 0.001
    stop_slippage: float = 0.0015

    # ---- correlation clustering (plan s1.6) -----------------------------
    cluster_corr_threshold: float = 0.7
    cluster_return_window: int = 30        # bars used for the correlation

    def as_dict(self):
        return asdict(self)


DEFAULT = Config()
