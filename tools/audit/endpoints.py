import json, urllib.request, time, collections, sys

BASE="http://127.0.0.1:8787"
def get(path, timeout=15):
    try:
        with urllib.request.urlopen(BASE+path, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception as e:
        return 0, None

fails=[]
def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ")+name+(("  <- "+detail) if detail and not cond else ""))
    if not cond: fails.append(name)

print("=== AUDIT 1: markers must land on real bar times at EVERY interval ===")
for iv in ["1s","1m","5m","15m","1h"]:
    st,d = get(f"/api/klines?symbol=BTCUSDT&interval={iv}&limit=300")
    if st!=200 or not d: check(f"{iv} klines", False, f"http {st}"); continue
    bars={b["t"] for b in d.get("bars") or []}
    marks=d.get("marks") or []
    bad=[m for m in marks if m["t"] not in bars]
    check(f"{iv}: {len(marks)} marks all on bar boundaries", not bad, f"{len(bad)} orphaned")

print()
print("=== AUDIT 2: every mark must carry letter, colour, direction ===")
st,d = get("/api/klines?symbol=BTCUSDT&interval=1m&limit=300")
marks=(d or {}).get("marks") or []
missing=[m for m in marks if not m.get("color") or not m.get("letter") or m.get("direction") is None]
check(f"{len(marks)} marks fully populated", not missing, f"{len(missing)} incomplete")

print()
print("=== AUDIT 3: flow_split must be internally consistent ===")
st,d = get("/api/flow?split=1&limit=10")
if st==200 and d:
    for key in ("pumps","dumps"):
        rows=d.get(key) or []
        wrong=[r for r in rows if (key=="pumps" and (r.get("change_10s") or 0)<=0)
                              or (key=="dumps" and (r.get("change_10s") or 0)>=0)]
        check(f"{key}: all signs correct ({len(rows)} rows)", not wrong, f"{len(wrong)} wrong sign")
        thin=[r for r in rows if (r.get("flow_quote") or 0) < d.get("min_flow_usd",0)]
        check(f"{key}: floor respected", not thin, f"{len(thin)} below floor")
        quiet=[r for r in rows if abs(r.get("change_10s") or 0) < d.get("min_move_pct",0)]
        check(f"{key}: min-move respected", not quiet, f"{len(quiet)} below min move")
        nocol=[r for r in rows if not r.get("color")]
        check(f"{key}: colour present", not nocol, f"{len(nocol)} missing colour")
        no24=[r for r in rows if r.get("change_24h_pct") is None]
        check(f"{key}: 24h context present", not no24, f"{len(no24)} missing 24h")
else:
    check("flow split reachable", False, f"http {st}")

print()
print("=== AUDIT 4: heat rows must state a source and reasons ===")
st,d = get("/api/heat?limit=50")
rows=(d or {}).get("rows") or []
bad=[r for r in rows if not r.get("source") or not r.get("reasons")]
check(f"{len(rows)} heat rows have source+reasons", not bad, f"{len(bad)} incomplete")
comps=[r for r in rows if not r.get("components")]
check("heat rows have components", not comps, f"{len(comps)} missing")

print()
print("=== AUDIT 5: outlook must never emit a bare probability ===")
st,d = get("/api/outlook?symbol=BTCUSDT")
if st==200 and d:
    sh=(d.get("outlook") or {}).get("short_horizon") or {}
    check("short_horizon has a basis string", bool(sh.get("basis")), str(sh)[:80])
    check("short_horizon states an edge verdict", bool(sh.get("edge")))
    m=d.get("model") or {}
    if not m.get("calibrated"):
        check("uncalibrated model says so", "uncalibrated" in (m.get("basis") or ""), m.get("basis","")[:80])
else:
    check("outlook reachable", False, f"http {st}")

print()
print("=== AUDIT 6: alerts must never carry a probability without a basis ===")
st,d = get("/api/alerts?limit=100")
als=(d or {}).get("alerts") or []
bad=[a for a in als if a.get("probability") is not None and not a.get("probability_basis")]
check(f"{len(als)} alerts: no bare probability", not bad, f"{len(bad)} bare")
nokind=[a for a in als if not a.get("kind") or not a.get("detector") or not a.get("severity")]
check("alerts fully labelled", not nokind, f"{len(nokind)} missing fields")
nocol=[a for a in als if not a.get("color")]
check("alerts carry colour", not nocol, f"{len(nocol)} missing")

print()
print("=== AUDIT 7: ledger integrity ===")
lines=[json.loads(l) for l in open("ledger.jsonl") if l.strip()]
preds={l["prediction"]["id"] for l in lines if l.get("type")=="prediction" and l.get("prediction")}
res={l["id"] for l in lines if l.get("type")=="resolution"}
lab={l["id"] for l in lines if l.get("type")=="label_10s"}
orphan_res=res-preds
orphan_lab=lab-preds
check(f"{len(res)} resolutions all join a prediction", not orphan_res, f"{len(orphan_res)} orphans")
check(f"{len(lab)} 10s labels all join a prediction", not orphan_lab, f"{len(orphan_lab)} orphans")
counts=collections.Counter(l.get("type") for l in lines)
print("       record types:", dict(counts))
# the locked label rule must travel on every label record
missing_rule=[l for l in lines if l.get("type")=="label_10s" and l.get("label_rule")!="locked before data collection"]
check("every 10s label carries the locked-rule provenance", not missing_rule, f"{len(missing_rule)} missing")

print()
print("=== AUDIT 8: SSE must actually be publishing all message types ===")
seen=collections.Counter()
try:
    req=urllib.request.urlopen(BASE+"/api/events", timeout=8)
    start=time.time()
    while time.time()-start < 7:
        line=req.readline().decode(errors="ignore")
        if line.startswith("data: "):
            try: seen[json.loads(line[6:]).get("type")]+=1
            except: pass
except Exception as e:
    pass
print("       message types in 7s:", dict(seen))
for t in ("status","flow","heat","ticker","flow_split"):
    check(f"SSE publishes {t}", seen.get(t,0)>0, f"seen {seen.get(t,0)}")

print()
print("="*60)
print(f"RESULT: {'ALL PASS' if not fails else str(len(fails))+' FAILURES'}")
for f in fails: print("  -", f)
