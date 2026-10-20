#!/bin/bash
# seed_deepseek_watch.sh — one-shot: seed /tmp/deepseek_watch/asks.tsv from the
# shared log with every deepseek->kimi message that contains a question or an
# explicit ask, so the watcher starts with history rather than a blank slate.
# Re-runnable: entries whose keyword already exists are skipped.
# Repo root resolved from this script's location, not hardcoded: the checkout
# lives at different paths on different machines (this was andrewpreston's, now
# users1's -- same portability bug check.sh had).
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE=/tmp/deepseek_watch
mkdir -p "$STATE"
touch "$STATE/asks.tsv"
cd "$ROOT" || exit 1

python3 - <<'PY'
import json, subprocess, os, re
state = "/tmp/deepseek_watch/asks.tsv"
existing = set()
if os.path.exists(state):
    for line in open(state):
        existing.add(line.split("|")[0].strip().lower())
out = subprocess.run(["tools/comms.sh","log","500"],capture_output=True,text=True).stdout
new = []
for line in out.splitlines():
    line = line.strip()
    if not line.startswith("{"): continue
    try: m = json.loads(line)
    except Exception: continue
    if m.get("from") != "deepseek" or m.get("to") != "kimi": continue
    body = m.get("body","")
    subj = m.get("subject","")
    # an ask: question mark, or imperative verbs in the body
    if not re.search(r"\?|\b(please|tell me|confirm|answer|say|paste|show)\b", body, re.I):
        continue
    kw = (subj.split(":")[-1].strip() or subj)[:60]
    if kw.lower() in existing: continue
    bf = f"/tmp/deepseek_watch/seed_{m['ts_ms']}.txt"
    open(bf,"w").write(body)
    new.append(f"{kw}|{m['ts_ms']}|{bf}")
with open(state,"a") as f:
    for row in new:
        f.write(row+"\n")
print(f"seeded {len(new)} asks")
PY
cat "$STATE/asks.tsv"