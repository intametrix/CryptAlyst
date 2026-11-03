#!/usr/bin/env python3
"""Snapshot the platform's own facts so the documents can state them without guessing.

Writes docs/data/platform-snapshot.json: every registered route, the model catalogue, the
topic registry and the live settings. The documents read this file, so a number in a
document is a number the platform reported.
"""
import json, os, re, subprocess, sys, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BASE = "http://127.0.0.1:8787"

def get(path):
    out = subprocess.run(["curl", "-sS", "--max-time", "20", BASE + path],
                         capture_output=True, text=True)
    try:
        return json.loads(out.stdout)
    except Exception:
        return None

server = open(os.path.join(ROOT, "backend/internal/api/server.go")).read()
routes = re.findall(r'mux\.HandleFunc\("(/api/[^"]+)"', server)

models = (get("/api/forecast/models") or {}).get("models", [])
topics = (get("/api/envelope/topics") or {}).get("topics", [])
settings = get("/api/settings") or {}
stats = get("/api/envelope/stats") or {}
replay = get("/api/replay?symbol=BTCUSDT&limit=1") or {}

snap = {
    "generated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "commit": subprocess.run(["git", "-C", ROOT, "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True).stdout.strip(),
    "routes": sorted(set(routes)),
    "models": models,
    "topics": topics,
    "settings": settings,
    "envelope_stats": stats,
    "replay_summary": replay.get("summary"),
}
path = os.path.join(HERE, "data", "platform-snapshot.json")
json.dump(snap, open(path, "w"), indent=1)
print(f"{path}: {len(snap['routes'])} routes, {len(models)} models, {len(topics)} topics, "
      f"{len(settings)} settings, commit {snap['commit']}")
