#!/usr/bin/env python3
"""Draw the platform's domain model as one SVG.

Hand-laid rather than auto-laid-out: a domain model is an argument about what
belongs together, and an automatic layout cannot make that argument.
"""
import os

W, H = 1680, 900
BG = "#0b1118"
INK = "#e6eef8"
DIM = "#8ea0b6"
FAINT = "#5a6b80"
LINE = "#233244"

DASH = ' stroke-dasharray="6 5"'

PLANE = {
    "market": "#4f9bff",
    "intel":  "#38d39f",
    "model":  "#f5b544",
    "risk":   "#ef6b6b",
    "evidence": "#a879ff",
    "control": "#7f8ea3",
}
CONTEXT_FILL = {
    "market": "#0f1a28",
    "intel":  "#0d1f1a",
    "model":  "#221a10",
    "risk":   "#251315",
    "evidence": "#1a1528",
    "control": "#151b24",
}

import html

def esc(t):
    return html.escape(t, quote=False)


parts = []
def add(s): parts.append(s)

def box(x, y, w, h, title, rows, colour, dashed=False):
    fill = CONTEXT_FILL[colour]
    stroke = PLANE[colour]
    add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{fill}" '
        f'stroke="{stroke}" stroke-opacity="{0.35 if dashed else 0.85}" '
        f'stroke-width="1.2"{DASH if dashed else ""}/>')
    add(f'<text x="{x+14}" y="{y+22}" fill="{stroke}" font-size="12" font-weight="700" '
        f'letter-spacing="0.08em">{esc(title.upper())}</text>')
    yy = y + 42
    for row in rows:
        name, kind = row if isinstance(row, tuple) else (row, "")
        weight = "600" if kind in ("aggregate", "entity") else "400"
        col = INK if kind in ("aggregate", "entity") else DIM
        add(f'<text x="{x+14}" y="{yy}" fill="{col}" font-size="12.5" font-weight="{weight}">{esc(name)}</text>')
        if kind:
            add(f'<text x="{x+w-14}" y="{yy}" fill="{FAINT}" font-size="9.5" '
                f'text-anchor="end" letter-spacing="0.06em">{esc(kind.upper())}</text>')
        yy += 19
    return yy

def arrow(points, colour, dashed=False, width=1.4, label=None, lx=0, ly=0, curve=False):
    d = "M " + " L ".join(f"{p[0]},{p[1]}" for p in points)
    add(f'<path d="{d}" fill="none" stroke="{PLANE[colour]}" stroke-opacity="0.75" '
        f'stroke-width="{width}"{DASH if dashed else ""} '
        f'marker-end="url(#arrow-{colour})"/>')
    if label:
        add(f'<text x="{lx}" y="{ly}" fill="{DIM}" font-size="10.5">{esc(label)}</text>')

def note(x, y, text, colour="control", size=10.5, anchor="start"):
    add(f'<text x="{x}" y="{y}" fill="{DIM}" font-size="{size}" text-anchor="{anchor}">{esc(text)}</text>')

add(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
    f'font-family="-apple-system, BlinkMacSystemFont, Helvetica, Arial, sans-serif">')
add(f'<rect width="{W}" height="{H}" fill="{BG}"/>')
for key, colour in PLANE.items():
    add(f'<marker id="arrow-{key}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
        f'markerHeight="7" orient="auto-start-reverse">'
        f'<path d="M 0 0 L 10 5 L 0 10 z" fill="{colour}"/></marker>')

# ---- titles
add(f'<text x="48" y="58" fill="{INK}" font-size="24" font-weight="700">CryptAlyst — domain model</text>')
add(f'<text x="48" y="76" fill="{DIM}" font-size="13">what the platform is about: the contracts, the books, the record'
    f'</text>')
add(f'<text x="{W-48}" y="58" fill="{FAINT}" font-size="11" text-anchor="end">one envelope contract · one bus · one record</text>')


# ---- data plane
box(48, 110, 330, 200, "Market data", [
    ("Symbol universe", "value"), ("Kline series", "entity"), ("Depth book", "entity"),
    ("Top of book", "entity"), ("Feed shard", "service"),
], "market")
box(48, 470, 330, 170, "Intelligence", [
    ("Signal", "entity"), ("Flow window", "entity"), ("TA reading", "entity"),
    ("Forecast", "entity"),
], "intel")

# ---- decision plane
box(470, 110, 330, 290, "Books", [
    ("Book", "aggregate"), ("Position", "entity"), ("Trade (fill)", "entity"),
    ("Decision", "entity"), ("Refusal", "entity"), ("Strategy", "value"),
], "model")
box(470, 470, 330, 170, "Risk & execution", [
    ("Risk gate", "service"), ("Kill switch", "aggregate"), ("Sizing rule", "value"),
    ("Exit policy", "value"),
], "risk")

# ---- evidence plane
box(900, 110, 340, 210, "Recording", [
    ("Recorder", "service"), ("Lake segment", "entity"), ("Envelope", "aggregate"),
    ("Derived reference", "value"),
], "evidence")
box(900, 470, 340, 170, "Forensics", [
    ("Case file", "aggregate"), ("Book report", "aggregate"), ("Search", "service"),
    ("Journey", "service"), ("Chronology", "service"),
], "evidence")

# ---- control plane
box(1330, 110, 300, 530, "Control", [
    ("Supervisor", "service"), ("Heartbeat", "service"), ("Trading state", "aggregate"),
    ("Stop flag", "entity"), ("Topic registry", "aggregate"), ("Interceptor chain", "service"),
    ("Operator registry", "service"),
], "control", dashed=True)

# ---- edges: data -> books
arrow([(378, 200), (430, 200)], "market")
note(386, 186, "market.*", size=9.5)
arrow([(378, 555), (430, 555), (430, 330), (470, 330)], "intel", width=1.2)
note(384, 570, "intel.*", size=9.5)

# ---- books -> execution -> risk
arrow([(635, 400), (635, 470)], "model", width=1.6, label="proposed order", lx=646, ly=424)

# ---- risk back to books (verdict)
arrow([(505, 470), (505, 400)], "risk", width=1.2, dashed=True)
note(300, 432, "allowed / refused", size=10.5)

# ---- books -> recorder
arrow([(800, 240), (856, 240), (856, 210), (900, 210)], "model", width=1.6)
note(806, 274, "model.decision · model.fill", size=10)

# ---- market -> recorder (dashed, bypass)
arrow([(390, 104), (1020, 104)], "market", dashed=True, width=1.0)
note(404, 98, "every topic is recorded, not only trades", size=10.5)

# ---- recording -> forensics
arrow([(1070, 320), (1070, 470)], "evidence", width=1.6, label="read-only replay", lx=1082, ly=415)

# ---- control couplings
arrow([(1330, 430), (1244, 430)], "control", dashed=True, width=1.0)
note(1318, 470, "supervises books, publishes system.control", size=9.5, anchor="end")
note(1614, 560, "STOP resets books and stats;", size=9.5, anchor="end")
note(1614, 574, "market data keeps running.", size=9.5, anchor="end")
note(1614, 592, "The kill switch is read by", size=9.5, anchor="end")
note(1614, 606, "every gate, never hoped for.", size=9.5, anchor="end")

# ---- the contract, stated once
add(f'<rect x="872" y="700" width="760" height="150" rx="10" fill="#0d141d" stroke="{LINE}"/>')
add(f'<text x="892" y="730" fill="{INK}" font-size="12" font-weight="700" letter-spacing="0.08em">THE CONTRACT</text>')
add(f'<text x="892" y="756" fill="{DIM}" font-size="11.5">Envelope = data + meta + refs. '
    f'meta carries version, plane, topic, producer, generation, cid, computed_at, hops.</text>')
add(f'<text x="892" y="778" fill="{DIM}" font-size="11.5">Unknown top-level member refused · unknown meta key ignored · '
    f'one previous version accepted.</text>')
add(f'<text x="892" y="800" fill="{DIM}" font-size="11.5">A reference is declared by a producer or derived by the recorder, and '
    f'always carries its age.</text>')

# ---- legend
add(f'<rect x="48" y="700" width="800" height="150" rx="10" fill="#0d141d" stroke="{LINE}"/>')
add(f'<text x="66" y="730" fill="{INK}" font-size="12" font-weight="700" letter-spacing="0.08em">READING THIS</text>')
x = 66
for label, colour in (("data plane", "market"), ("decision plane", "model"), ("risk", "risk"),
                      ("evidence", "evidence"), ("control", "control")):
    add(f'<rect x="{x}" y="750" width="10" height="10" rx="2" fill="{PLANE[colour]}"/>')
    add(f'<text x="{x+16}" y="759" fill="{DIM}" font-size="10.5">{esc(label)}</text>')
    x += 22 + len(label) * 6.4
add(f'<text x="66" y="788" fill="{DIM}" font-size="10.5">Bold names are aggregates — the things that must stay consistent. '
    f'Others are entities, services or values.</text>')
add(f'<text x="66" y="808" fill="{DIM}" font-size="10.5">Dashed arrows are control, not data: they change what the platform will do next.</text>')
add(f'<text x="66" y="828" fill="{FAINT}" font-size="10">Every arrow in this diagram carries an envelope; there is one bus and no side channels.</text>')

add('</svg>')

out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "domain-model.svg")
with open(out, "w") as fh:
    fh.write("\n".join(parts))
print("wrote", out, len("\n".join(parts)), "bytes")
