"""Onset scoring: turn a flow burst into a ranked, explainable alert.

The scoring model answers one question: *how confident are we that a sharp
move is starting right now?* Four independent axes, each capped, summing to
100. Every axis is reported in the alert, so an alert can be audited:

======================  ====  ==========================================
axis                     max   meaning
======================  ====  ==========================================
flow burst                30   how abnormal is the volume, vs this
                                symbol's own recent distribution
speed                     25   how fast is price moving, in units of the
                                symbol's own short-horizon volatility
aggression                25   taker-side imbalance from @aggTrade, i.e.
                                is someone actually hitting the offer
quality                   20   liquidity, spread, book depth -- is this a
                                real market or a thin-book artefact
======================  ====  ==========================================

A final **lateness penalty** subtracts points when the 60s move already dwarfs
the window move, because that is the signature of arriving late to a move
rather than catching its start.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .common import clamp, fmt_money, fmt_pct, safe_div
from .config import Config
from .flow import OnsetCandidate

PUMP = "PUMP"
DUMP = "DUMP"
REVERSAL_DUMP = "REVERSAL_DUMP"
REVERSAL_PUMP = "REVERSAL_PUMP"

SEVERITIES = ("WATCH", "ALERT", "CRITICAL")
_SEVERITY_RANK = {name: i for i, name in enumerate(SEVERITIES)}

CRITICAL_SCORE = 75.0
ALERT_SCORE = 55.0

_DIRECTION = {PUMP: 1, DUMP: -1, REVERSAL_DUMP: -1, REVERSAL_PUMP: 1}


@dataclass
class AggressionSnapshot:
    """Taker-side state accumulated from ``@aggTrade`` for one symbol."""

    symbol: str
    buy_quote: float = 0.0
    sell_quote: float = 0.0
    trades: int = 0
    first_ts: int = 0
    last_ts: int = 0
    largest_trade_usd: float = 0.0

    @property
    def total_quote(self) -> float:
        return self.buy_quote + self.sell_quote

    @property
    def buy_ratio(self) -> float:
        return safe_div(self.buy_quote, self.total_quote, 0.5)

    def aligned_ratio(self, direction: int) -> float:
        return self.buy_ratio if direction > 0 else 1.0 - self.buy_ratio

    @property
    def duration_sec(self) -> float:
        return max(0.0, (self.last_ts - self.first_ts) / 1000.0)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agg_buy_quote": round(self.buy_quote, 2),
            "agg_sell_quote": round(self.sell_quote, 2),
            "agg_buy_ratio": round(self.buy_ratio, 4),
            "agg_trades": self.trades,
            "agg_duration_sec": round(self.duration_sec, 2),
            "agg_largest_trade_usd": round(self.largest_trade_usd, 2),
        }


@dataclass
class Event:
    """A detected onset or reversal, ready to alert and log."""

    market: str
    symbol: str
    kind: str
    severity: str
    score: float
    price: float
    ts_ms: int
    reasons: List[str] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    components: Dict[str, float] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    @property
    def direction(self) -> int:
        return _DIRECTION.get(self.kind, 1)

    @property
    def is_reversal(self) -> bool:
        return self.kind in (REVERSAL_DUMP, REVERSAL_PUMP)

    def to_row(self) -> Dict[str, Any]:
        from .common import iso_utc

        row: Dict[str, Any] = {
            "event_id": self.event_id,
            "ts_ms": self.ts_ms,
            "ts_utc": iso_utc(self.ts_ms),
            "market": self.market,
            "symbol": self.symbol,
            "kind": self.kind,
            "severity": self.severity,
            "score": round(self.score, 1),
            "price": self.price,
            "reasons": " | ".join(self.reasons),
        }
        for key, value in sorted(self.metrics.items()):
            if isinstance(value, float):
                row[key] = round(value, 8)
            else:
                row[key] = value
        for key, value in sorted(self.components.items()):
            row[f"score_{key}"] = round(value, 2)
        return row


def severity_for(score: float) -> str:
    if score >= CRITICAL_SCORE:
        return "CRITICAL"
    if score >= ALERT_SCORE:
        return "ALERT"
    return "WATCH"


def passes_severity(severity: str, minimum: str) -> bool:
    return _SEVERITY_RANK.get(severity, 0) >= _SEVERITY_RANK.get(minimum.upper(), 0)


@dataclass
class ScoreResult:
    score: float
    severity: str
    reasons: List[str]
    components: Dict[str, float]
    penalty: float = 0.0
    confirmed_direction: bool = True


def score_onset(
    candidate: OnsetCandidate,
    cfg: Config,
    *,
    aggression: Optional[AggressionSnapshot] = None,
) -> ScoreResult:
    """Score a flow burst as the probable start of a sharp move."""
    direction = candidate.direction
    reasons: List[str] = list(candidate.reasons)
    components: Dict[str, float] = {}

    # --- flow burst (max 30) --------------------------------------------
    gate = max(cfg.min_burst_multiple, 0.1)
    ratio = safe_div(candidate.burst_multiple, gate)
    burst_component = 20.0 * clamp((ratio - 1.0) / 6.0, 0.0, 1.0)
    if cfg.min_burst_z > 0:
        z_ratio = safe_div(candidate.burst_z, 2.0 * cfg.min_burst_z)
        burst_component += 10.0 * clamp(z_ratio, 0.0, 1.0)
    else:
        burst_component += 10.0 * clamp((ratio - 1.0) / 6.0, 0.0, 1.0)
    components["flow"] = round(clamp(burst_component, 0.0, 30.0), 2)

    # --- speed (max 25) --------------------------------------------------
    sigma_component = 25.0 * clamp(
        safe_div(candidate.sigma_multiple, 3.0 * max(cfg.min_sigma_multiple, 0.1)),
        0.0,
        1.0,
    )
    move_component = 25.0 * clamp(
        safe_div(abs(candidate.move) * 100.0, 3.0 * max(cfg.min_move_pct_10s, 0.01)),
        0.0,
        1.0,
    )
    components["speed"] = round(max(sigma_component, move_component), 2)

    # --- aggression (max 25) --------------------------------------------
    if aggression is not None and aggression.total_quote > 0:
        aligned = aggression.aligned_ratio(direction)
        components["aggression"] = round(
            25.0 * clamp((aligned - 0.5) / 0.22, 0.0, 1.0), 2
        )
        side = "buy" if direction > 0 else "sell"
        reasons.append(
            f"taker {side} {aligned * 100:.0f}% of "
            f"{fmt_money(aggression.total_quote)} across {aggression.trades} trades"
        )
        if aggression.largest_trade_usd >= 20_000:
            reasons.append(
                f"largest single trade {fmt_money(aggression.largest_trade_usd)}"
            )
    else:
        # No trade-level feed yet: fall back to where price sits in its own
        # window range, which is a weaker but usable aggression proxy.
        aligned_range = (
            candidate.range_position if direction > 0 else 1.0 - candidate.range_position
        )
        components["aggression"] = round(
            25.0 * clamp((aligned_range - 0.5) / 0.45, 0.0, 1.0) * 0.6, 2
        )
        reasons.append(f"no taker feed; window close position {aligned_range * 100:.0f}%")

    # --- quality (max 20) ------------------------------------------------
    liq_component = 10.0 * clamp(
        safe_div(candidate.quote_volume_24h, 10.0 * max(cfg.min_quote_volume_24h, 1.0)),
        0.0,
        1.0,
    )
    spread_component = 6.0 * clamp(
        1.0 - safe_div(candidate.spread_pct, max(cfg.max_spread_pct, 1e-9)), 0.0, 1.0
    )
    depth_component = 4.0 * clamp(
        safe_div(candidate.book_depth_usd, 20.0 * max(cfg.min_book_depth_usd, 1.0)),
        0.0,
        1.0,
    )
    components["quality"] = round(
        clamp(liq_component + spread_component + depth_component, 0.0, 20.0), 2
    )

    # --- lateness penalty -----------------------------------------------
    penalty = 0.0
    if candidate.change_60s is not None and abs(candidate.move) > 0:
        lateness = safe_div(abs(candidate.change_60s), abs(candidate.move))
        if lateness > 3.0:
            penalty = clamp((lateness - 3.0) * 4.0, 0.0, 20.0)
            reasons.append(
                f"late: 60s move already {fmt_pct(candidate.change_60s)} "
                f"({lateness:.1f}x the window move)"
            )
    if candidate.acceleration is not None and candidate.acceleration < 1.0:
        penalty += clamp((1.0 - candidate.acceleration) * 8.0, 0.0, 8.0)
        reasons.append(f"flow decelerating ({candidate.acceleration:.2f}x prior window)")

    score = clamp(sum(components.values()) - penalty, 0.0, 100.0)

    confirmed = True
    if aggression is not None and aggression.total_quote > 0:
        aligned = aggression.aligned_ratio(direction)
        if aligned < cfg.agg_min_imbalance:
            confirmed = False
            reasons.append(
                f"taker imbalance {aligned * 100:.0f}% below "
                f"{cfg.agg_min_imbalance * 100:.0f}% threshold"
            )

    return ScoreResult(
        score=score,
        severity=severity_for(score),
        reasons=reasons,
        components=components,
        penalty=round(penalty, 2),
        confirmed_direction=confirmed,
    )


def build_onset_event(
    candidate: OnsetCandidate,
    cfg: Config,
    *,
    aggression: Optional[AggressionSnapshot] = None,
    latency_ms: Optional[int] = None,
) -> Optional[Event]:
    """Score a candidate and produce an ``Event``, or ``None`` if it fails a gate."""
    from .common import now_ms

    result = score_onset(candidate, cfg, aggression=aggression)

    if cfg.agg_exclusive and cfg.confirm_with_agg_trades:
        # Strict mode: require positive trade-level confirmation.
        if aggression is None or aggression.total_quote <= 0:
            return None
        if not result.confirmed_direction:
            return None

    kind = PUMP if candidate.direction > 0 else DUMP
    metrics: Dict[str, Any] = {
        "price": candidate.price,
        "window_sec": round(cfg.flow_window_sec, 3),
        "flow_quote": round(candidate.flow_quote, 2),
        "coverage": round(candidate.coverage, 3),
        "burst_multiple": round(candidate.burst_multiple, 3),
        "burst_z": round(candidate.burst_z, 3),
        "change_10s": candidate.change_10s,
        "change_30s": candidate.change_30s,
        "change_60s": candidate.change_60s,
        "sigma_10s": candidate.sigma_10s,
        "sigma_multiple": round(candidate.sigma_multiple, 3),
        "acceleration": candidate.acceleration,
        "range_position": round(candidate.range_position, 4),
        "spread_pct": round(candidate.spread_pct, 6),
        "book_depth_usd": round(candidate.book_depth_usd, 2),
        "quote_volume_24h": round(candidate.quote_volume_24h, 2),
        "trades_10s": candidate.trades_10s,
        "trades_multiple": round(candidate.trades_multiple, 3),
        "latency_ms": latency_ms if latency_ms is not None else 0,
        "penalty": result.penalty,
        "agg_confirmed": bool(aggression and result.confirmed_direction),
    }
    if aggression is not None:
        metrics.update(aggression.to_dict())

    return Event(
        market=candidate.market,
        symbol=candidate.symbol,
        kind=kind,
        severity=result.severity,
        score=result.score,
        price=candidate.price,
        ts_ms=now_ms(),
        reasons=result.reasons,
        metrics=metrics,
        components=result.components,
    )


def build_reversal_event(
    *,
    market: str,
    symbol: str,
    origin_kind: str,
    price: float,
    retrace: float,
    elapsed_min: float,
    extreme_price: float,
    flow_quote: float,
    burst_multiple: float,
    coverage: float,
    quote_volume_24h: float,
    aggression: Optional[AggressionSnapshot] = None,
) -> Event:
    """The reversal leg of an already-alerted move.

    A pump is only a "pump and dump" once it gives the move back, so this is
    scored on its own evidence: how much was retraced, how fast, and whether
    volume is flowing with the reversal.
    """
    from .common import now_ms

    direction = -1 if origin_kind == PUMP else 1
    kind = REVERSAL_DUMP if direction < 0 else REVERSAL_PUMP

    retrace_component = 35.0 * clamp(abs(retrace) * 100.0 / 8.0, 0.0, 1.0)
    flow_component = 25.0 * clamp(safe_div(burst_multiple, 10.0), 0.0, 1.0)
    speed_component = 20.0 * clamp(1.0 - safe_div(elapsed_min, 45.0), 0.0, 1.0)
    components = {
        "retrace": round(retrace_component, 2),
        "flow": round(flow_component, 2),
        "recency": round(speed_component, 2),
    }

    reasons = [
        f"{'gave back' if direction < 0 else 'reversed'} "
        f"{fmt_pct(retrace)} from the "
        f"{'peak' if direction < 0 else 'trough'} of the original {origin_kind.lower()}",
        f"{elapsed_min:.0f} min after the original alert",
        f"reversal flow {fmt_money(flow_quote)} = {burst_multiple:.1f}x baseline",
    ]

    if aggression is not None and aggression.total_quote > 0:
        aligned = aggression.aligned_ratio(direction)
        components["aggression"] = round(
            20.0 * clamp((aligned - 0.5) / 0.22, 0.0, 1.0), 2
        )
        reasons.append(
            f"taker {'sell' if direction < 0 else 'buy'} {aligned * 100:.0f}% of volume"
        )

    score = clamp(sum(components.values()), 0.0, 100.0)
    metrics: Dict[str, Any] = {
        "price": price,
        "extreme_price": extreme_price,
        "retrace": retrace,
        "elapsed_min": round(elapsed_min, 2),
        "flow_quote": round(flow_quote, 2),
        "burst_multiple": round(burst_multiple, 3),
        "coverage": round(coverage, 3),
        "quote_volume_24h": round(quote_volume_24h, 2),
        "origin_kind": origin_kind,
    }
    if aggression is not None:
        metrics.update(aggression.to_dict())

    return Event(
        market=market,
        symbol=symbol,
        kind=kind,
        severity=severity_for(score),
        score=score,
        price=price,
        ts_ms=now_ms(),
        reasons=reasons,
        metrics=metrics,
        components=components,
    )
