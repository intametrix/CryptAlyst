#!/usr/bin/env python3
"""export_npz.py — Stage-1 action 1 of the master back-analysis plan.

Parses the append-only JSONL ledger/state stores once and exports them as
typed NumPy arrays (npz) under data/npz/ so that Stage-1/2 analysis and the
GPU sweeps never re-parse JSON.

Schema: union of numeric leaf keys (dot-flattened), NaN where absent.
String leaves become int32 codes + a JSON categories sidecar per source.
Lists are dropped (counted in the report). Read-only on the source stores.

Stdlib + numpy only. Re-runnable; overwrite is the default refresh mode.
"""
import json
import math
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "npz"

SOURCES = {
    "skim": "scalp5/ledger/skim.jsonl",
    "scalp_fast": "scalp5/ledger/scalp_fast.jsonl",
    "skim_ai": "scalp5/ledger/skim_ai.jsonl",
    "swing_turtle": "scalp5/ledger/swing_turtle.jsonl",
    "swing3": "scalp5/ledger/swing3.jsonl",
    "multi_models": "scalp5/ledger/multi_models.jsonl",
    "stats_models": "scalp5/ledger/stats_models.jsonl",
    "fills": "scalp5/state/fills.jsonl",
    "backend": "scalp5/ledger/backend.jsonl",
    "go_turtle_sessions": "data/turtle_sessions.jsonl",
}


def flatten(obj, prefix, out, dropped):
    if isinstance(obj, dict):
        for k, v in obj.items():
            flatten(v, f"{prefix}.{k}" if prefix else k, out, dropped)
    elif isinstance(obj, list):
        dropped[prefix] = dropped.get(prefix, 0) + 1
    elif obj is None:
        out[prefix] = None
    elif isinstance(obj, bool):
        out[prefix] = float(obj)
    elif isinstance(obj, (int, float)):
        f = float(obj)
        out[prefix] = f if math.isfinite(f) else None
    elif isinstance(obj, str):
        out[prefix] = obj
    else:
        dropped[prefix] = dropped.get(prefix, 0) + 1


def export_source(name, rel):
    src = ROOT / rel
    if not src.exists():
        print(f"[skip] {name}: {rel} not found")
        return None
    # Per-column sparse storage: (row_index, value) pairs, scattered into
    # full-length arrays afterwards. Absent keys are interleaved, so simple
    # end-padding would corrupt row alignment.
    numeric_cols = {}   # path -> list[(row_idx, float)]
    cat_cols = {}       # path -> list[(row_idx, int)]
    cat_maps = {}       # path -> {value: code}
    dropped = {}
    n_rows = 0
    t0 = time.time()
    with open(src, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                dropped["<unparseable-line>"] = dropped.get("<unparseable-line>", 0) + 1
                continue
            flat = {}
            flatten(row, "", flat, dropped)
            row_idx = n_rows
            n_rows += 1
            for path, val in flat.items():
                if isinstance(val, str):
                    cmap = cat_maps.setdefault(path, {})
                    code = cmap.get(val)
                    if code is None:
                        code = len(cmap)
                        cmap[val] = code
                    cat_cols.setdefault(path, []).append((row_idx, code))
                else:
                    fval = val if val is not None else float("nan")
                    numeric_cols.setdefault(path, []).append((row_idx, fval))

    # Scatter into aligned full-length arrays (NaN / -1 where absent).
    arrays = {}
    for path, pairs in numeric_cols.items():
        col = np.full(n_rows, np.nan, dtype=np.float64)
        idx = [p[0] for p in pairs]
        val = [p[1] for p in pairs]
        col[np.asarray(idx, dtype=np.int64)] = np.asarray(val, dtype=np.float64)
        arrays[path] = col
    for path, pairs in cat_cols.items():
        col = np.full(n_rows, -1, dtype=np.int32)
        idx = [p[0] for p in pairs]
        val = [p[1] for p in pairs]
        col[np.asarray(idx, dtype=np.int64)] = np.asarray(val, dtype=np.int32)
        arrays[f"{path}__code"] = col
    arrays["n_rows"] = np.int64(n_rows)

    meta = {
        "source": rel,
        "n_rows": n_rows,
        "numeric_fields": sorted(numeric_cols),
        "categorical_fields": {p: sorted(m, key=m.get) for p, m in cat_maps.items()},
        "dropped": dropped,
        "exported_unix_s": time.time(),
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(OUT_DIR / f"{name}.npz", **arrays)
    with open(OUT_DIR / f"{name}.categories.json", "w", encoding="utf-8") as fh:
        json.dump(meta["categorical_fields"], fh, indent=1)
    with open(OUT_DIR / f"{name}.meta.json", "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=1)
    dt = time.time() - t0
    print(f"[ok] {name}: {n_rows:,} rows, {len(numeric_cols)} numeric, "
          f"{len(cat_cols)} categorical, {dt:.1f}s")
    return meta


def main():
    only = sys.argv[1:] or list(SOURCES)
    for name in only:
        export_source(name, SOURCES[name])
    print(f"npz dir: {OUT_DIR}")


if __name__ == "__main__":
    main()
