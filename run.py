#!/usr/bin/env python3
"""CLI entry point for the Binance pump & dump onset scanner.

    python run.py                 # scan the whole spot market
    python run.py --once          # one sweep, then exit (smoke test)
    python run.py --check-keys    # verify API credentials

Requires Python 3.10+ and the ``websockets`` package. No API key is needed for
detection; keys are only used by ``--check-keys`` (and any future account
features).
"""

from __future__ import annotations

import asyncio
import sys
import time

from pumpdump import __version__
from pumpdump.alerts import AlertManager
from pumpdump.common import LOG, Color, clock_utc, setup_logging
from pumpdump.config import ALL_MARKETS, Config, load_config
from pumpdump.env import mask
from pumpdump.rest import BinanceError, BinanceREST
from pumpdump.scanner import Scanner, install_signal_handlers


def print_banner(cfg: Config) -> None:
    print(
        Color.paint(
            f"binance-pumpdump {__version__}  |  "
            f"{'+'.join(cfg.markets)}  |  flow window {cfg.flow_window_sec:.0f}s",
            Color.BOLD,
        ),
        flush=True,
    )


def check_keys(cfg: Config) -> int:
    """Verify credentials against both markets and print what they can do."""
    print(f"BINANCE_API_KEY    : {mask(cfg.api_key)}")
    print(f"BINANCE_API_SECRET : {mask(cfg.api_secret)}")
    if not cfg.api_key or not cfg.api_secret:
        print(
            "\nNo credentials found. Set BINANCE_API_KEY and BINANCE_API_SECRET in "
            "the environment or in .env.\nNote: the scanner does NOT need keys -- "
            "all detection uses public market data."
        )
        return 1

    failures = 0
    for name in ("spot", "futures"):
        if name not in cfg.markets and name not in ALL_MARKETS:
            continue
        spec = ALL_MARKETS[name]
        client = BinanceREST(
            spec,
            api_key=cfg.api_key,
            api_secret=cfg.api_secret,
            timeout=cfg.http_timeout_sec,
        )
        label = spec.label
        try:
            client.ping()
        except BinanceError as exc:
            print(f"\n{label}: unreachable ({exc})")
            failures += 1
            continue

        print(f"\n{label} ({spec.host})")
        try:
            account = client.account()
        except BinanceError as exc:
            print(f"  signed account call FAILED: {exc}")
            failures += 1
            continue

        if spec.is_futures:
            balances = [
                item for item in account
                if abs(float(item.get("balance", 0) or 0)) > 0
            ]
            print(f"  signed call OK: {len(balances)} non-zero futures balances")
            for item in balances[:5]:
                print(
                    f"    {item.get('asset'):<8} "
                    f"balance={item.get('balance')} available={item.get('availableBalance')}"
                )
        else:
            can_trade = account.get("canTrade")
            print(
                f"  signed call OK: canTrade={can_trade} "
                f"accountType={account.get('accountType')} "
                f"permissions={account.get('permissions')}"
            )
            balances = [
                b for b in account.get("balances", [])
                if float(b.get("free", 0) or 0) + float(b.get("locked", 0) or 0) > 0
            ]
            print(f"  {len(balances)} non-zero spot balances")
            for item in balances[:5]:
                print(f"    {item.get('asset'):<8} free={item.get('free')}")

        restrictions = client.api_key_permissions()
        if restrictions:
            enabled = [k for k, v in restrictions.items() if v is True]
            print(f"  enabled permissions: {', '.join(sorted(enabled)) or 'none'}")
            if restrictions.get("ipRestrict") is True:
                print("  IP restriction: ON (requests must come from a whitelisted IP)")

    print(
        "\nNote: keys are optional. Detection uses public market data only; "
        "no orders are ever placed by this tool."
    )
    return 1 if failures else 0


def run_pump_screen(cfg: Config) -> int:
    """Market-wide pump/dump screen: one pass, or repeated on an interval."""
    from pumpdump.pumps import (
        PumpDumpScreener,
        ScreenThresholds,
        print_rows,
        write_rows,
    )

    thresholds = ScreenThresholds(
        min_move_5m=cfg.screen_min_move_5m,
        min_move_15m=cfg.screen_min_move_15m,
        min_vol_ratio=cfg.screen_min_vol_ratio,
        min_recent_usd=cfg.screen_min_recent_usd,
        min_quote_volume_24h=cfg.min_quote_volume_24h,
    )
    screener = PumpDumpScreener(
        cfg,
        thresholds,
        top_n=cfg.screen_top_n,
        kline_limit=cfg.screen_kline_limit,
        workers=cfg.max_workers,
        interval=cfg.screen_interval,
    )

    pass_number = 0
    while True:
        pass_number += 1
        rows = screener.screen()
        title = (
            f"PUMP & DUMP SCREEN  {clock_utc()} UTC  |  spot  |  "
            f"top {cfg.screen_top_n} by volume  |  "
            f"{cfg.screen_interval} bars  |  pass {pass_number}"
        )
        print_rows(rows, title=title)
        if cfg.screen_json or cfg.screen_csv:
            write_rows(
                rows, thresholds, cfg.screen_json, cfg.screen_csv,
                scanned=len(screener.universe.tracked),
            )
            if cfg.screen_json:
                print(f"written: {cfg.screen_json}")
            if cfg.screen_csv:
                print(f"written: {cfg.screen_csv}")
        if cfg.screen_repeat_sec <= 0:
            return 0
        LOG.info("next pass in %.0fs (ctrl-c to stop)", cfg.screen_repeat_sec)
        try:
            time.sleep(cfg.screen_repeat_sec)
        except KeyboardInterrupt:
            return 0


def run_turtle_survey(cfg: Config) -> int:
    """Turtle indicator survey with sizing and portfolio risk limits."""
    from pumpdump.survey import MarketSurvey, print_report, write_report
    from pumpdump.turtle import TurtleParams

    params = TurtleParams(
        entry_period=cfg.turtle_entry_period,
        entry_period_slow=cfg.turtle_entry_period_slow,
        exit_period=cfg.turtle_exit_period,
        atr_period=cfg.turtle_atr_period,
        stop_atr_multiple=cfg.turtle_stop_atr,
        adx_min=cfg.turtle_adx_min,
        risk_per_unit=cfg.turtle_risk_per_unit,
    )
    survey = MarketSurvey(
        cfg,
        params,
        timeframes=cfg.timeframes,
        equity=cfg.equity,
        top_n=cfg.turtle_top_n,
        kline_limit=cfg.turtle_kline_limit,
        workers=cfg.max_workers,
        long_only=cfg.turtle_long_only,
    )
    report = survey.run()
    print_report(report, color=not cfg.no_color)
    if cfg.survey_json or cfg.survey_csv:
        write_report(report, cfg.survey_json, cfg.survey_csv)
        if cfg.survey_json:
            print(f"written: {cfg.survey_json}")
        if cfg.survey_csv:
            print(f"written: {cfg.survey_csv}")
    return 0


def main(argv: list | None = None) -> int:
    cfg, args = load_config(argv)
    setup_logging(cfg.log_level)

    if args.check_keys:
        return check_keys(cfg)

    print_banner(cfg)

    # Modes that need no websocket.
    try:
        if cfg.mode == "pumps":
            return run_pump_screen(cfg)
        if cfg.mode == "survey":
            return run_turtle_survey(cfg)
    except BinanceError as exc:
        LOG.error("exchange error: %s", exc)
        return 2

    try:
        import websockets  # noqa: F401
    except ImportError:
        print(
            "ERROR: the 'websockets' package is required.\n"
            "  pip install -r requirements.txt",
            file=sys.stderr,
        )
        return 3

    scanner = Scanner(cfg)

    async def runner() -> int:
        loop = asyncio.get_running_loop()
        install_signal_handlers(scanner, loop)
        if cfg.once:
            return await scanner.run_once()
        return await scanner.run()

    try:
        return asyncio.run(runner())
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    except BinanceError as exc:
        LOG.error("exchange error: %s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
