"""Shared helpers: logging, ANSI colour, number/time formatting, math."""

from __future__ import annotations

import logging
import math
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from typing import Optional, Sequence

LOG = logging.getLogger("pumpdump")


# --------------------------------------------------------------------------
# Logging
# --------------------------------------------------------------------------

class _Formatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, timezone.utc).strftime("%H:%M:%S")
        level = record.levelname[:4]
        return f"{ts} {level:<4} {record.getMessage()}"


def setup_logging(level: str = "INFO", quiet: bool = False) -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_Formatter())
    root = logging.getLogger("pumpdump")
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    if quiet:
        root.setLevel(logging.WARNING)
    root.propagate = False


# --------------------------------------------------------------------------
# Colour
# --------------------------------------------------------------------------

class Color:
    """ANSI codes, disabled automatically when not writing to a TTY."""

    enabled: bool = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None

    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    GREY = "\033[90m"
    BRIGHT_RED = "\033[91m"
    BRIGHT_GREEN = "\033[92m"
    BRIGHT_YELLOW = "\033[93m"
    BRIGHT_CYAN = "\033[96m"
    BG_RED = "\033[41m"
    BG_GREEN = "\033[42m"

    @classmethod
    def paint(cls, text: str, *codes: str) -> str:
        if not cls.enabled or not codes:
            return text
        return "".join(codes) + text + cls.RESET


# --------------------------------------------------------------------------
# Time
# --------------------------------------------------------------------------

def now_ms() -> int:
    return int(time.time() * 1000)


def iso_utc(epoch_ms: Optional[int] = None) -> str:
    ms = now_ms() if epoch_ms is None else epoch_ms
    return datetime.fromtimestamp(ms / 1000.0, timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.%f"
    )[:-3] + "Z"


def clock_utc(epoch_ms: Optional[int] = None) -> str:
    ms = now_ms() if epoch_ms is None else epoch_ms
    return datetime.fromtimestamp(ms / 1000.0, timezone.utc).strftime("%H:%M:%S")


def dt_from_ms(epoch_ms: int) -> datetime:
    return datetime.fromtimestamp(epoch_ms / 1000.0, timezone.utc)


# --------------------------------------------------------------------------
# Formatting
# --------------------------------------------------------------------------

def fmt_price(value: float) -> str:
    """Adaptive price formatting: keeps ~4 significant digits for tiny alts."""
    if value is None or not math.isfinite(value):
        return "n/a"
    abs_v = abs(value)
    if abs_v == 0:
        return "0"
    if abs_v >= 1000:
        return f"{value:,.2f}"
    if abs_v >= 1:
        return f"{value:,.4f}"
    if abs_v >= 0.01:
        return f"{value:.6f}"
    if abs_v >= 0.0001:
        return f"{value:.8f}"
    return f"{value:.10f}"


def fmt_pct(fraction: float, places: int = 2, signed: bool = True) -> str:
    if fraction is None or not math.isfinite(fraction):
        return "n/a"
    sign = "+" if (signed and fraction > 0) else ""
    return f"{sign}{fraction * 100:.{places}f}%"


def fmt_money(value: float) -> str:
    """Compact USD: 1.2B / 18.2M / 412K / 1,234."""
    if value is None or not math.isfinite(value):
        return "n/a"
    abs_v = abs(value)
    sign = "-" if value < 0 else ""
    if abs_v >= 1e9:
        return f"{sign}${abs_v / 1e9:.2f}B"
    if abs_v >= 1e6:
        return f"{sign}${abs_v / 1e6:.2f}M"
    if abs_v >= 1e3:
        return f"{sign}${abs_v / 1e3:.1f}K"
    return f"{sign}${abs_v:,.0f}"


def fmt_mult(value: float) -> str:
    if value is None or not math.isfinite(value):
        return "n/a"
    return f"{value:.1f}x"


def fmt_sigma(value: float) -> str:
    if value is None or not math.isfinite(value):
        return "n/a"
    return f"{value:.1f}sigma"


# --------------------------------------------------------------------------
# Math
# --------------------------------------------------------------------------

def pct_change(new: float, old: float) -> Optional[float]:
    """Simple fractional change; ``None`` when undefined."""
    if old is None or new is None or old == 0:
        return None
    if not (math.isfinite(new) and math.isfinite(old)):
        return None
    return (new - old) / old


def safe_div(num: float, den: float, default: float = 0.0) -> float:
    if not den:
        return default
    try:
        result = num / den
    except ZeroDivisionError:
        return default
    return result if math.isfinite(result) else default


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def stdev(values: Sequence[float]) -> float:
    """Population stdev; 0.0 when fewer than two samples."""
    if len(values) < 2:
        return 0.0
    try:
        return statistics.pstdev(values)
    except statistics.StatisticsError:
        return 0.0


def median(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    return statistics.median(values)


def mean(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def percentile(values: Sequence[float], q: float) -> float:
    """Linear-interpolation percentile, ``q`` in [0, 1]."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = clamp(q, 0.0, 1.0) * (len(ordered) - 1)
    low = int(math.floor(pos))
    high = min(low + 1, len(ordered) - 1)
    frac = pos - low
    return ordered[low] * (1 - frac) + ordered[high] * frac
