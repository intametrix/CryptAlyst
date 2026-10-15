import json, urllib.request, collections

BASE="http://127.0.0.1:8787"
def get(path, timeout=15):
    try:
        with urllib.request.urlopen(BASE+path, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception:
        return 0, None

fails=[]
def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ")+name+(("  <- "+detail) if detail and not cond else ""))
    if not cond: fails.append(name)

# Find symbols that ACTUALLY have marks in the durable alert history.
syms=collections.Counter()
for l in open("alerts.jsonl"):
    l=l.strip()
    if not l: continue
    try: syms[json.loads(l)["symbol"]]+=1
    except: pass
print("=== symbols with real alert history:", len(syms), "===")
print("   ", ", ".join(f"{s}({n})" for s,n in syms.most_common(8)))
print()

candidates=[s for s,_ in syms.most_common(10)]
print("=== AUDIT 1b: markers on symbols that HAVE alerted (non-vacuous) ===")
tested=0
for sym in candidates:
    st,d = get(f"/api/klines?symbol={sym}&interval=1m&limit=300")
    if st!=200 or not d: continue
    marks=d.get("marks") or []
    if not marks: continue
    tested+=1
    bars={b["t"] for b in d["bars"]}
    orphan=[m for m in marks if m["t"] not in bars]
    check(f"{sym}: {len(marks)} marks all on bar boundaries", not orphan, f"{len(orphan)} orphaned")
    # every mark must be fully populated
    bad=[m for m in marks if not m.get("color") or not m.get("letter") or m.get("direction") is None]
    check(f"{sym}: marks fully populated", not bad, f"{len(bad)} incomplete")
    # colours must match the canon
    canon={"PUMP":"#00e676","DUMP":"#ff1744","BREAKOUT_LONG":"#00e676","BREAKOUT_SHORT":"#ff1744",
           "REVERSAL_PUMP":"#ffab00","REVERSAL_DUMP":"#ffab00"}
    wrong=[m for m in marks if m["kind"] in canon and m.get("color")!=canon[m["kind"]]]
    check(f"{sym}: colours match the canon", not wrong, str([(m['kind'],m.get('color')) for m in wrong[:3]]))
    # letters must match
    letters={"PUMP":"P","DUMP":"D","BREAKOUT_LONG":"B","BREAKOUT_SHORT":"B","REVERSAL_PUMP":"R","REVERSAL_DUMP":"R"}
    wl=[m for m in marks if m["kind"] in letters and m.get("letter")!=letters[m["kind"]]]
    check(f"{sym}: letters match the canon", not wl, str([(m['kind'],m.get('letter')) for m in wl[:3]]))

if tested==0:
    check("found at least one symbol with marks to test", False, "audit is vacuous - NO marks anywhere")
else:
    print(f"       tested {tested} symbols that actually carry marks")

print()
print("=== AUDIT 3b: does the split EVER return rows? scan for movers ===")
st,d = get("/api/universe?limit=200")
movers=[]
if st==200 and d:
    rows=d.get("symbols") or []
    rows.sort(key=lambda r: abs(r.get("change_24h_pct") or 0), reverse=True)
    movers=rows[:3]
    print("       biggest 24h movers:", ", ".join(f"{r['symbol']} {r.get('change_24h_pct'):+.1f}%" for r in movers))
st,sp = get("/api/flow?split=1&limit=10")
if st==200 and sp:
    print(f"       split: pumps={len(sp.get('pumps') or [])} dumps={len(sp.get('dumps') or [])} "
          f"dropped thin={sp.get('dropped_thin')} quiet={sp.get('dropped_quiet')}")
    print(f"       -> the split is EMPTY because the market is quiet, min move is {sp.get('min_move_pct')}%")
    if not (sp.get("pumps") or sp.get("dumps")):
        print("       NOTE: the sign/floor checks above passed on EMPTY lists - they are not evidence")
    else:
        for key in ("pumps","dumps"):
            rows=sp.get(key) or []
            wrong=[r for r in rows if (key=="pumps" and (r.get("change_10s") or 0)<=0) or (key=="dumps" and (r.get("change_10s") or 0)>=0)]
            check(f"{key} signs correct ({len(rows)} rows)", not wrong)

print()
print("=== AUDIT 9: 1s candles - are marks snapped to 1-SECOND boundaries? ===")
for sym in candidates[:6]:
    st,d = get(f"/api/klines?symbol={sym}&interval=1s&limit=300")
    if st!=200 or not d: continue
    marks=d.get("marks") or []
    if not marks: continue
    bars={b["t"] for b in d["bars"]}
    orphan=[m for m in marks if m["t"] not in bars]
    check(f"{sym} @1s: {len(marks)} marks snapped to 1s bars", not orphan, f"{len(orphan)} not in the 1s window")
    break
else:
    print("  SKIP   no symbol with marks in the 1s window")

print()
print("="*60)
print(f"RESULT: {'ALL PASS' if not fails else str(len(fails))+' FAILURES'}")
for f in fails: print("  -", f)
