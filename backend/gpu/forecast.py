#!/usr/bin/env python3
"""Time-series forecast runner for the CryptALAS agent.

Reads a JSON job from stdin and writes a forecast to stdout:

  input  { "payload": { "series": [{"t":..,"v":..}, ...],
                        "horizon": N, "method": "ema|sma|trend" } }
  output { "method": .., "horizon": .., "last": .., "forecast": [..], "runner": .. }

Methods are dependency-free (pure Python) so the agent runs this anywhere:
  ema    exponential moving average, held flat
  sma    trailing simple moving average, held flat
  trend  least-squares linear trend extrapolated `horizon` steps
"""
import json
import sys


def forecast(series, horizon, method):
    vals = []
    for x in series:
        v = x.get("v") if isinstance(x, dict) else x
        try:
            vals.append(float(v))
        except (TypeError, ValueError):
            continue
    if len(vals) < 3:
        return {"error": "need >= 3 points"}

    if method == "ema":
        alpha = 0.3
        e = vals[0]
        for v in vals[1:]:
            e = alpha * v + (1 - alpha) * e
        out = [e] * horizon
    elif method == "sma":
        w = min(10, len(vals))
        base = sum(vals[-w:]) / w
        out = [base] * horizon
    elif method == "trend":
        n = min(30, len(vals))
        xs = list(range(n))
        ys = vals[-n:]
        mx = sum(xs) / n
        my = sum(ys) / n
        denom = sum((x - mx) ** 2 for x in xs) or 1.0
        slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom
        intercept = my - slope * mx
        out = [intercept + slope * (n + i) for i in range(horizon)]
    else:
        return {"error": "unknown method: " + method}

    return {
        "method": method,
        "horizon": horizon,
        "last": round(vals[-1], 6),
        "forecast": [round(x, 6) for x in out],
    }


def main():
    raw = sys.stdin.read()
    try:
        job = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        print(json.dumps({"error": "bad json", "runner": "forecast.py"}))
        return

    payload = job.get("payload") or job
    series = payload.get("series") or []
    horizon = int(payload.get("horizon", 5))
    method = str(payload.get("method", "ema")).lower()

    result = forecast(series, horizon, method)
    result["runner"] = "forecast.py"
    result["trained"] = "error" not in result
    print(json.dumps(result))


if __name__ == "__main__":
    main()
