"""Configuration: market specs, defaults, env overrides, CLI overrides."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional

from . import env as envmod


# --------------------------------------------------------------------------
# Market definitions
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class MarketSpec:
    """Everything market-specific: hosts, paths, and their weight costs.

    Weights are taken from Binance's published limits and were verified
    against the live ``x-mbx-used-weight-1m`` response header.
    """

    name: str
    host: str
    path_ticker_24h: str
    path_ticker_price: str
    path_klines: str
    path_exchange_info: str
    path_ping: str
    is_futures: bool
    weight_ticker_24h: int
    weight_ticker_price: int
    weight_klines: int
    weight_exchange_info: int
    weight_limit_per_min: int
    path_open_interest: Optional[str] = None
    path_premium_index: Optional[str] = None

    @property
    def label(self) -> str:
        return "FUTURES" if self.is_futures else "SPOT"


SPOT = MarketSpec(
    name="spot",
    host="https://api.binance.com",
    path_ticker_24h="/api/v3/ticker/24hr",
    path_ticker_price="/api/v3/ticker/price",
    path_klines="/api/v3/klines",
    path_exchange_info="/api/v3/exchangeInfo",
    path_ping="/api/v3/ping",
    is_futures=False,
    weight_ticker_24h=80,
    weight_ticker_price=4,
    weight_klines=2,
    weight_exchange_info=20,
    weight_limit_per_min=6000,
)

FUTURES = MarketSpec(
    name="futures",
    host="https://fapi.binance.com",
    path_ticker_24h="/fapi/v1/ticker/24hr",
    path_ticker_price="/fapi/v1/ticker/price",
    path_klines="/fapi/v1/klines",
    path_exchange_info="/fapi/v1/exchangeInfo",
    path_ping="/fapi/v1/ping",
    is_futures=True,
    weight_ticker_24h=40,
    weight_ticker_price=2,
    weight_klines=1,
    weight_exchange_info=1,
    weight_limit_per_min=2400,
    path_open_interest="/fapi/v1/openInterest",
    path_premium_index="/fapi/v1/premiumIndex",
)

ALL_MARKETS: Dict[str, MarketSpec] = {"spot": SPOT, "futures": FUTURES}


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

@dataclass
class Config:
    # --- scope -----------------------------------------------------------
    # Spot only by default. Futures is kept as an opt-in escape hatch because
    # the REST/stream plumbing is venue-generic, but the detection model is
    # tuned for spot: no funding, no liquidations, no leverage artefacts.
    markets: List[str] = field(default_factory=lambda: ["spot"])
    quote_assets: List[str] = field(default_factory=lambda: ["USDT"])
    min_quote_volume_24h: float = 750_000.0
    max_symbols_per_market: int = 400
    bootstrap_top_n: int = 250
    exclude_symbols: List[str] = field(default_factory=list)
    # Symbols matching these substrings are skipped (leveraged tokens, etc).
    exclude_patterns: List[str] = field(
        default_factory=lambda: ["UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT"]
    )

    # --- cadence ---------------------------------------------------------
    # How often the flow scan runs. The stream itself delivers every ~1s per
    # symbol; this is the evaluation tick.
    scan_interval_sec: float = 1.0
    context_poll_sec: float = 60.0
    status_every_sec: float = 120.0
    # Re-baseline each symbol from fresh 1s klines this often (0 disables).
    rebaseline_every_sec: float = 1800.0

    # --- onset detection -------------------------------------------------
    # Window over which "sudden volume" is measured. 10s is the design point.
    flow_window_sec: float = 10.0
    # Burst gates: flow vs its own robust baseline.
    min_burst_multiple: float = 8.0
    min_burst_z: float = 4.0
    # Absolute floor so a near-zero baseline cannot manufacture a huge multiple.
    min_abs_flow_usd: float = 5_000.0
    # Price must actually be moving, fast, and in the same direction as the flow.
    min_move_pct_10s: float = 0.4
    min_sigma_multiple: float = 3.0
    # Fraction of the window that must be covered by received ticks.
    min_coverage: float = 0.7
    # Lateness guards: reject the tail of a move that already happened.
    max_move_pct_60s: float = 6.0
    max_24h_move_pct: float = 60.0
    # Require volume to still be building (current window > prior window).
    require_acceleration: bool = True
    min_acceleration: float = 1.0
    # Activity floor: a burst made of 3 prints is not a pump.
    min_trades_10s: int = 8
    # Floor the flow baseline at this fraction of the 24h average 10s flow.
    # A sanity guard against a near-zero divisor, NOT the divisor itself:
    # at 1.0 the daily average overwhelms each symbol's own quiet baseline.
    baseline_floor_fraction: float = 0.25

    # --- book-quality filters (thin-book artefact rejection) -------------
    max_spread_pct: float = 0.004
    min_book_depth_usd: float = 500.0

    # --- trade-level confirmation (dynamic @aggTrade escalation) ---------
    confirm_with_agg_trades: bool = True
    agg_trade_ttl_sec: float = 180.0
    agg_max_symbols: int = 12
    agg_min_imbalance: float = 0.55   # aligned taker share required
    agg_exclusive: bool = False       # True = drop unconfirmed candidates
    # How long to wait for aggTrade data to accumulate before judging.
    agg_warmup_sec: float = 4.0

    # --- cold-start bootstrap --------------------------------------------
    # 1s klines seed the flow baseline immediately (~16 min of history).
    bootstrap_kline_limit: int = 1000
    bootstrap_interval: str = "1s"
    bootstrap_workers: int = 8

    # --- pump -> dump cycle tracking ------------------------------------
    reversal_drop_pct: float = 3.0
    reversal_min_burst: float = 2.0
    watch_window_min: float = 120.0
    cooldown_sec: float = 900.0
    reversal_cooldown_sec: float = 600.0

    # --- alerting --------------------------------------------------------
    alert_jsonl: Optional[str] = "alerts.jsonl"
    alert_csv: Optional[str] = "alerts.csv"
    webhook_url: Optional[str] = None
    desktop_notify: bool = False
    min_severity: str = "WATCH"  # WATCH | ALERT | CRITICAL
    no_color: bool = False

    # --- runtime ---------------------------------------------------------
    duration_sec: float = 0.0  # 0 = run until interrupted
    max_workers: int = 8
    http_timeout_sec: float = 20.0
    log_level: str = "INFO"
    dry_run: bool = False
    once: bool = False  # single scan sweep, then exit (smoke test)

    # --- mode selection --------------------------------------------------
    # onset  : live streaming detection of a move STARTING (default)
    # pumps  : one-pass market-wide pump/dump screen ("what is moving now?")
    # survey : Turtle indicator survey, breakouts, sizing and risk limits
    mode: str = "onset"
    screen_top_n: int = 200
    screen_kline_limit: int = 180
    screen_interval: str = "1m"
    screen_min_move_5m: float = 1.5
    screen_min_move_15m: float = 2.5
    screen_min_vol_ratio: float = 3.0
    screen_min_recent_usd: float = 20_000.0
    screen_repeat_sec: float = 0.0     # 0 = run once
    screen_json: Optional[str] = "pumps.json"
    screen_csv: Optional[str] = "pumps.csv"

    # --- Turtle survey ---------------------------------------------------
    equity: float = 10_000.0
    timeframes: List[str] = field(default_factory=lambda: ["1d", "4h", "1h"])
    turtle_top_n: int = 150
    turtle_kline_limit: int = 250
    turtle_entry_period: int = 20
    turtle_entry_period_slow: int = 55
    turtle_exit_period: int = 10
    turtle_atr_period: int = 20
    turtle_stop_atr: float = 2.0
    turtle_adx_min: float = 20.0
    turtle_risk_per_unit: float = 0.01
    turtle_long_only: bool = True
    survey_json: Optional[str] = "survey.json"
    survey_csv: Optional[str] = "survey_setups.csv"

    # --- credentials (never required for public market data) -------------
    api_key: Optional[str] = None
    api_secret: Optional[str] = None

    def market_specs(self) -> List[MarketSpec]:
        return [ALL_MARKETS[name] for name in self.markets if name in ALL_MARKETS]

    def describe(self) -> str:
        return (
            f"markets={','.join(self.markets)} quotes={','.join(self.quote_assets)} "
            f"min_liq24h=${self.min_quote_volume_24h:,.0f} "
            f"max_symbols={self.max_symbols_per_market} "
            f"window={self.flow_window_sec:.0f}s "
            f"burst>={self.min_burst_multiple}x/>={self.min_burst_z}sigma/"
            f">=${self.min_abs_flow_usd:,.0f} "
            f"move>={self.min_move_pct_10s}%/>={self.min_sigma_multiple}sigma "
            f"agg_confirm={self.confirm_with_agg_trades} "
            f"min_sev={self.min_severity} dry_run={self.dry_run}"
        )


# --------------------------------------------------------------------------
# Environment overrides
# --------------------------------------------------------------------------

def apply_env(cfg: Config) -> Config:
    """Overlay ``PUMPDUMP_*`` / ``BINANCE_*`` environment variables."""
    e = envmod
    cfg.markets = e.get_list("PUMPDUMP_MARKETS", cfg.markets)
    cfg.quote_assets = e.get_list("PUMPDUMP_QUOTE_ASSETS", cfg.quote_assets)
    cfg.min_quote_volume_24h = e.get_float(
        "PUMPDUMP_MIN_QUOTE_VOLUME_24H", cfg.min_quote_volume_24h)
    cfg.max_symbols_per_market = e.get_int(
        "PUMPDUMP_MAX_SYMBOLS_PER_MARKET", cfg.max_symbols_per_market)
    cfg.bootstrap_top_n = e.get_int("PUMPDUMP_BOOTSTRAP_TOP_N", cfg.bootstrap_top_n)
    cfg.exclude_symbols = e.get_list("PUMPDUMP_EXCLUDE_SYMBOLS", cfg.exclude_symbols)
    cfg.scan_interval_sec = e.get_float("PUMPDUMP_SCAN_INTERVAL_SEC", cfg.scan_interval_sec)
    cfg.context_poll_sec = e.get_float("PUMPDUMP_CONTEXT_POLL_SEC", cfg.context_poll_sec)
    cfg.rebaseline_every_sec = e.get_float(
        "PUMPDUMP_REBASELINE_EVERY_SEC", cfg.rebaseline_every_sec)
    cfg.flow_window_sec = e.get_float("PUMPDUMP_FLOW_WINDOW_SEC", cfg.flow_window_sec)
    cfg.min_burst_multiple = e.get_float("PUMPDUMP_MIN_BURST_MULTIPLE", cfg.min_burst_multiple)
    cfg.min_burst_z = e.get_float("PUMPDUMP_MIN_BURST_Z", cfg.min_burst_z)
    cfg.min_abs_flow_usd = e.get_float("PUMPDUMP_MIN_ABS_FLOW_USD", cfg.min_abs_flow_usd)
    cfg.min_move_pct_10s = e.get_float("PUMPDUMP_MIN_MOVE_PCT_10S", cfg.min_move_pct_10s)
    cfg.min_sigma_multiple = e.get_float("PUMPDUMP_MIN_SIGMA_MULTIPLE", cfg.min_sigma_multiple)
    cfg.min_coverage = e.get_float("PUMPDUMP_MIN_COVERAGE", cfg.min_coverage)
    cfg.max_move_pct_60s = e.get_float("PUMPDUMP_MAX_MOVE_PCT_60S", cfg.max_move_pct_60s)
    cfg.max_24h_move_pct = e.get_float("PUMPDUMP_MAX_24H_MOVE_PCT", cfg.max_24h_move_pct)
    cfg.require_acceleration = e.get_bool(
        "PUMPDUMP_REQUIRE_ACCELERATION", cfg.require_acceleration)
    cfg.min_acceleration = e.get_float("PUMPDUMP_MIN_ACCELERATION", cfg.min_acceleration)
    cfg.min_trades_10s = e.get_int("PUMPDUMP_MIN_TRADES_10S", cfg.min_trades_10s)
    cfg.baseline_floor_fraction = e.get_float(
        "PUMPDUMP_BASELINE_FLOOR_FRACTION", cfg.baseline_floor_fraction)
    cfg.max_spread_pct = e.get_float("PUMPDUMP_MAX_SPREAD_PCT", cfg.max_spread_pct)
    cfg.min_book_depth_usd = e.get_float("PUMPDUMP_MIN_BOOK_DEPTH_USD", cfg.min_book_depth_usd)
    cfg.confirm_with_agg_trades = e.get_bool(
        "PUMPDUMP_CONFIRM_WITH_AGG_TRADES", cfg.confirm_with_agg_trades)
    cfg.agg_trade_ttl_sec = e.get_float("PUMPDUMP_AGG_TRADE_TTL_SEC", cfg.agg_trade_ttl_sec)
    cfg.agg_max_symbols = e.get_int("PUMPDUMP_AGG_MAX_SYMBOLS", cfg.agg_max_symbols)
    cfg.agg_min_imbalance = e.get_float("PUMPDUMP_AGG_MIN_IMBALANCE", cfg.agg_min_imbalance)
    cfg.agg_exclusive = e.get_bool("PUMPDUMP_AGG_EXCLUSIVE", cfg.agg_exclusive)
    cfg.bootstrap_kline_limit = e.get_int(
        "PUMPDUMP_BOOTSTRAP_KLINE_LIMIT", cfg.bootstrap_kline_limit)
    cfg.bootstrap_workers = e.get_int("PUMPDUMP_BOOTSTRAP_WORKERS", cfg.bootstrap_workers)
    cfg.reversal_drop_pct = e.get_float("PUMPDUMP_REVERSAL_DROP_PCT", cfg.reversal_drop_pct)
    cfg.watch_window_min = e.get_float("PUMPDUMP_WATCH_WINDOW_MIN", cfg.watch_window_min)
    cfg.cooldown_sec = e.get_float("PUMPDUMP_COOLDOWN_SEC", cfg.cooldown_sec)
    cfg.alert_jsonl = e.get_str("PUMPDUMP_ALERT_JSONL", cfg.alert_jsonl)
    cfg.alert_csv = e.get_str("PUMPDUMP_ALERT_CSV", cfg.alert_csv)
    cfg.webhook_url = e.get_str("PUMPDUMP_WEBHOOK_URL", cfg.webhook_url)
    cfg.desktop_notify = e.get_bool("PUMPDUMP_DESKTOP_NOTIFY", cfg.desktop_notify)
    cfg.min_severity = (e.get_str("PUMPDUMP_MIN_SEVERITY", cfg.min_severity) or "WATCH").upper()
    cfg.max_workers = e.get_int("PUMPDUMP_MAX_WORKERS", cfg.max_workers)
    cfg.log_level = (e.get_str("PUMPDUMP_LOG_LEVEL", cfg.log_level) or "INFO").upper()
    cfg.api_key = e.get_str("BINANCE_API_KEY", cfg.api_key)
    cfg.api_secret = e.get_str("BINANCE_API_SECRET", cfg.api_secret)
    return cfg


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pumpdump",
        description=(
            "Real-time Binance SPOT pump & dump ONSET scanner. Watches the whole "
            "market over a websocket and alerts when volume suddenly starts "
            "flowing into a pair and price begins to move -- i.e. at the start "
            "of a sharp rise, and at the start of the opposite move."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python run.py                          # scan spot with defaults\n"
            "  python run.py --once                   # one scan sweep, then exit\n"
            "  python run.py --window 10 --min-burst-mult 10\n"
            "  python run.py --min-liq 2000000 --min-severity ALERT\n"
            "  python run.py --no-agg-confirm --no-csv   # faster, fewer gates\n"
            "  python run.py --check-keys             # verify API credentials\n"
        ),
    )
    g = p.add_argument_group
    scope = g("scope")
    scope.add_argument("--markets", default=None,
                       help="comma list: spot (futures is opt-in and untuned)")
    scope.add_argument("--quote-assets", default=None, help="comma list, e.g. USDT,USDC")
    scope.add_argument("--min-liq", type=float, default=None,
                       help="minimum 24h quote volume in USD to track a symbol")
    scope.add_argument("--max-symbols", type=int, default=None,
                       help="max symbols tracked per market (by 24h volume)")
    scope.add_argument("--bootstrap-top-n", type=int, default=None,
                       help="how many top symbols to seed flow baselines for from 1s klines")
    scope.add_argument("--exclude", default=None, help="comma list of symbols/substrings to skip")

    cadence = g("cadence")
    cadence.add_argument("--scan-interval", type=float, default=None,
                         help="seconds between flow scans (evaluation tick)")
    cadence.add_argument("--context-poll", type=float, default=None,
                         help="seconds between universe/24h-ticker refreshes")
    cadence.add_argument("--rebaseline-every", type=float, default=None,
                         help="seconds between 1s-kline re-baselines (0 disables)")

    det = g("detection")
    det.add_argument("--window", type=float, default=None,
                     help="seconds of flow to measure (the 'last N seconds' window)")
    det.add_argument("--min-burst-mult", type=float, default=None,
                     help="required flow multiple vs the symbol's own baseline")
    det.add_argument("--min-burst-z", type=float, default=None,
                     help="required flow z-score vs the symbol's own baseline")
    det.add_argument("--min-flow-usd", type=float, default=None,
                     help="absolute minimum flow in the window, in USD")
    det.add_argument("--min-move-pct", type=float, default=None,
                     help="minimum price move %% within the window")
    det.add_argument("--min-move-sigma", type=float, default=None,
                     help="minimum price move as a multiple of window volatility")
    det.add_argument("--min-coverage", type=float, default=None,
                     help="required tick coverage of the window (0-1)")
    det.add_argument("--max-move-60s", type=float, default=None,
                     help="reject if the 60s move already exceeds this %% (too late)")
    det.add_argument("--max-24h-move", type=float, default=None,
                     help="reject if the 24h move already exceeds this %% in the same direction")
    det.add_argument("--min-trades", type=int, default=None,
                     help="minimum number of trades inside the window")
    det.add_argument("--max-spread", type=float, default=None,
                     help="reject symbols whose spread fraction exceeds this")
    det.add_argument("--min-book-depth", type=float, default=None,
                     help="reject symbols with less top-of-book depth than this (USD)")
    det.add_argument("--no-acceleration", action="store_true", default=None,
                     help="do not require flow to still be building")
    det.add_argument("--no-agg-confirm", action="store_true", default=None,
                     help="do not escalate candidates to @aggTrade trade-level data")
    det.add_argument("--agg-exclusive", action="store_true", default=None,
                     help="only emit events confirmed by taker-side aggression")
    det.add_argument("--agg-min-imbalance", type=float, default=None,
                     help="required aligned taker share (0.5-1.0)")
    det.add_argument("--reversal-drop", type=float, default=None,
                     help="%% retrace from the extreme that marks a reversal leg")
    det.add_argument("--watch-window", type=float, default=None,
                     help="minutes to keep watching a symbol after an event")
    det.add_argument("--cooldown", type=float, default=None,
                     help="seconds before re-alerting the same symbol+direction")

    al = g("alerting")
    al.add_argument("--jsonl", default=None, help="JSONL event log path ('-' to disable)")
    al.add_argument("--csv", default=None, help="CSV event log path ('-' to disable)")
    al.add_argument("--webhook", default=None, help="webhook URL for alerts (Discord/Slack/generic)")
    al.add_argument("--desktop-notify", action="store_true", default=None,
                    help="also raise a macOS notification")
    al.add_argument("--min-severity", default=None, choices=["WATCH", "ALERT", "CRITICAL"],
                    help="minimum severity to emit")
    al.add_argument("--no-color", action="store_true", default=None, help="disable ANSI colour")

    rt = g("runtime")
    rt.add_argument("--duration", type=float, default=None,
                    help="run for N seconds then exit (0 = forever)")
    rt.add_argument("--workers", type=int, default=None, help="HTTP worker threads for confirmations")
    rt.add_argument("--log-level", default=None,
                    choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    rt.add_argument("--once", action="store_true", default=None,
                    help="run a single scan sweep and exit")
    rt.add_argument("--dry-run", action="store_true", default=None,
                    help="detect and print, but do not write log files or webhooks")
    rt.add_argument("--status-every", type=float, default=None,
                    help="seconds between heartbeat lines (0 disables)")
    rt.add_argument("--env-file", default=".env", help="path to .env file")

    keys = g("credentials")
    keys.add_argument("--check-keys", action="store_true",
                      help="verify BINANCE_API_KEY/SECRET and exit")

    mode = p.add_argument_group("mode")
    mode.add_argument("--mode", default=None, choices=["onset", "pumps", "survey"],
                      help="onset = live streaming detection; pumps = one-pass "
                           "market-wide pump/dump screen; survey = Turtle indicators")
    mode.add_argument("--top", type=int, default=None,
                      help="how many of the most liquid pairs to screen/survey")
    mode.add_argument("--repeat", type=float, default=None,
                      help="seconds between pump-screen passes (0 = run once)")

    pumps = p.add_argument_group("pump/dump screen")
    pumps.add_argument("--kline-limit", type=int, default=None,
                       help="1m klines fetched per symbol for the screen")
    pumps.add_argument("--screen-interval", default=None,
                       help="kline interval for the screen (1m, 5m, 15m)")
    pumps.add_argument("--min-move-5m", type=float, default=None,
                       help="percent move over 5 minutes required to flag a symbol")
    pumps.add_argument("--min-move-15m", type=float, default=None,
                       help="percent move over 15 minutes required to flag a symbol")
    pumps.add_argument("--min-vol-ratio", type=float, default=None,
                       help="recent volume multiple of the symbol's own median")
    pumps.add_argument("--min-recent-usd", type=float, default=None,
                       help="minimum USD actually traded in the 5m window")
    pumps.add_argument("--screen-json", default=None, help="screen JSON output ('-' disables)")
    pumps.add_argument("--screen-csv", default=None, help="screen CSV output ('-' disables)")

    turtle = p.add_argument_group("turtle survey")
    turtle.add_argument("--timeframes", default=None,
                        help="comma list for the survey, e.g. 1d,4h,1h")
    turtle.add_argument("--equity", type=float, default=None,
                        help="account equity used for position sizing")
    turtle.add_argument("--turtle-top-n", type=int, default=None,
                        help="symbols to survey")
    turtle.add_argument("--entry-period", type=int, default=None,
                        help="fast breakout period (Turtle System 1 = 20)")
    turtle.add_argument("--entry-period-slow", type=int, default=None,
                        help="slow breakout period (Turtle System 2 = 55)")
    turtle.add_argument("--exit-period", type=int, default=None,
                        help="channel exit period (Turtle = 10)")
    turtle.add_argument("--atr-period", type=int, default=None,
                        help="ATR period for N (Turtle = 20)")
    turtle.add_argument("--stop-atr", type=float, default=None,
                        help="stop distance in N (Turtle = 2.0)")
    turtle.add_argument("--adx-min", type=float, default=None,
                        help="ADX trend filter (0 disables = pure original Turtle)")
    turtle.add_argument("--risk-per-unit", type=float, default=None,
                        help="fraction of equity risked per unit (Turtle = 0.01)")
    turtle.add_argument("--allow-short", action="store_true", default=None,
                        help="also report downside breakouts as trades "
                             "(spot cannot actually short)")
    turtle.add_argument("--survey-json", default=None, help="survey JSON output ('-' disables)")
    turtle.add_argument("--survey-csv", default=None, help="survey CSV output ('-' disables)")
    return p


def _clean(value):
    """Treat '.' and '-' as 'disable this optional output'."""
    if value in (".", "-", "none", "off"):
        return None
    return value


def apply_args(cfg: Config, args: argparse.Namespace) -> Config:
    if args.markets is not None:
        cfg.markets = [m.strip().lower() for m in args.markets.split(",") if m.strip()]
    if args.quote_assets is not None:
        cfg.quote_assets = [q.strip().upper() for q in args.quote_assets.split(",") if q.strip()]
    if args.exclude is not None:
        cfg.exclude_symbols = [s.strip().upper() for s in args.exclude.split(",") if s.strip()]

    direct = {
        "min_quote_volume_24h": args.min_liq,
        "max_symbols_per_market": args.max_symbols,
        "bootstrap_top_n": args.bootstrap_top_n,
        "scan_interval_sec": args.scan_interval,
        "context_poll_sec": args.context_poll,
        "rebaseline_every_sec": args.rebaseline_every,
        "flow_window_sec": args.window,
        "min_burst_multiple": args.min_burst_mult,
        "min_burst_z": args.min_burst_z,
        "min_abs_flow_usd": args.min_flow_usd,
        "min_move_pct_10s": args.min_move_pct,
        "min_sigma_multiple": args.min_move_sigma,
        "min_coverage": args.min_coverage,
        "max_move_pct_60s": args.max_move_60s,
        "max_24h_move_pct": args.max_24h_move,
        "min_trades_10s": args.min_trades,
        "max_spread_pct": args.max_spread,
        "min_book_depth_usd": args.min_book_depth,
        "agg_min_imbalance": args.agg_min_imbalance,
        "reversal_drop_pct": args.reversal_drop,
        "watch_window_min": args.watch_window,
        "cooldown_sec": args.cooldown,
        "duration_sec": args.duration,
        "max_workers": args.workers,
        "status_every_sec": args.status_every,
    }
    for name, value in direct.items():
        if value is not None:
            setattr(cfg, name, value)

    if args.no_acceleration:
        cfg.require_acceleration = False
    if args.no_agg_confirm:
        cfg.confirm_with_agg_trades = False
    if args.agg_exclusive:
        cfg.agg_exclusive = True

    if args.jsonl is not None:
        cfg.alert_jsonl = _clean(args.jsonl)
    if args.csv is not None:
        cfg.alert_csv = _clean(args.csv)
    if args.webhook is not None:
        cfg.webhook_url = _clean(args.webhook)
    if args.desktop_notify:
        cfg.desktop_notify = True
    if args.min_severity is not None:
        cfg.min_severity = args.min_severity
    if args.no_color:
        cfg.no_color = True
    if args.log_level is not None:
        cfg.log_level = args.log_level
    if args.once:
        cfg.once = True
    if args.dry_run:
        cfg.dry_run = True

    # --- mode-specific -----------------------------------------------------
    if args.mode is not None:
        cfg.mode = args.mode
    if args.top is not None:
        cfg.screen_top_n = args.top
        cfg.turtle_top_n = args.top
    if args.repeat is not None:
        cfg.screen_repeat_sec = args.repeat
    for name, value in {
        "screen_kline_limit": args.kline_limit,
        "screen_interval": args.screen_interval,
        "screen_min_move_5m": args.min_move_5m,
        "screen_min_move_15m": args.min_move_15m,
        "screen_min_vol_ratio": args.min_vol_ratio,
        "screen_min_recent_usd": args.min_recent_usd,
    }.items():
        if value is not None:
            setattr(cfg, name, value)
    if args.screen_json is not None:
        cfg.screen_json = _clean(args.screen_json)
    if args.screen_csv is not None:
        cfg.screen_csv = _clean(args.screen_csv)

    for name, value in {
        "equity": args.equity,
        "turtle_top_n": args.turtle_top_n,
        "turtle_kline_limit": args.kline_limit,
        "turtle_entry_period": args.entry_period,
        "turtle_entry_period_slow": args.entry_period_slow,
        "turtle_exit_period": args.exit_period,
        "turtle_atr_period": args.atr_period,
        "turtle_stop_atr": args.stop_atr,
        "turtle_adx_min": args.adx_min,
        "turtle_risk_per_unit": args.risk_per_unit,
    }.items():
        if value is not None:
            setattr(cfg, name, value)
    if args.timeframes is not None:
        cfg.timeframes = [t.strip() for t in args.timeframes.split(",") if t.strip()]
    if args.allow_short:
        cfg.turtle_long_only = False
    if args.survey_json is not None:
        cfg.survey_json = _clean(args.survey_json)
    if args.survey_csv is not None:
        cfg.survey_csv = _clean(args.survey_csv)

    if cfg.mode not in ("onset", "pumps", "survey"):
        cfg.mode = "onset"

    if cfg.dry_run:
        # Detection logic runs normally; sinks are suppressed.
        cfg.alert_jsonl = None
        cfg.alert_csv = None
        cfg.webhook_url = None
        cfg.desktop_notify = False
        cfg.screen_json = None
        cfg.screen_csv = None
        cfg.survey_json = None
        cfg.survey_csv = None

    if not cfg.markets:
        cfg.markets = ["spot"]
    return cfg


def load_config(argv: Optional[List[str]] = None) -> tuple:
    """Build the effective config. Returns ``(config, args)``."""
    parser = build_parser()
    args = parser.parse_args(argv)
    envmod.load_env(args.env_file)

    cfg = apply_args(apply_env(Config()), args)

    from .common import Color
    if cfg.no_color:
        Color.enabled = False
    return cfg, args


def with_overrides(cfg: Config, **kwargs) -> Config:
    return replace(cfg, **kwargs)
