#!/usr/bin/env python3
"""The cover: a dark, technical plate in the landscape page's proportions."""
import html
import os

W, H = 2400, 1697          # A4 landscape ratio, rendered at 2x
BG0, BG1 = "#05080d", "#0d1726"
BLUE, GREEN, AMBER, VIOLET = "#4f9bff", "#38d39f", "#f5b544", "#a879ff"

p = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
     f'font-family="-apple-system, BlinkMacSystemFont, Helvetica, Arial, sans-serif">']
p.append(f'''<defs>
<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
  <stop offset="0" stop-color="{BG0}"/><stop offset="0.55" stop-color="{BG1}"/>
  <stop offset="1" stop-color="#060a10"/></linearGradient>
<radialGradient id="glow" cx="0.72" cy="0.28" r="0.55">
  <stop offset="0" stop-color="{BLUE}" stop-opacity="0.30"/>
  <stop offset="1" stop-color="{BLUE}" stop-opacity="0"/></radialGradient>
<radialGradient id="glow2" cx="0.18" cy="0.82" r="0.5">
  <stop offset="0" stop-color="{GREEN}" stop-opacity="0.16"/>
  <stop offset="1" stop-color="{GREEN}" stop-opacity="0"/></radialGradient>
<linearGradient id="rule" x1="0" y1="0" x2="1" y2="0">
  <stop offset="0" stop-color="{BLUE}" stop-opacity="0.9"/>
  <stop offset="0.5" stop-color="{VIOLET}" stop-opacity="0.7"/>
  <stop offset="1" stop-color="{GREEN}" stop-opacity="0"/></linearGradient>
</defs>''')
p.append(f'<rect width="{W}" height="{H}" fill="url(#bg)"/>')
p.append(f'<rect width="{W}" height="{H}" fill="url(#glow)"/>')
p.append(f'<rect width="{W}" height="{H}" fill="url(#glow2)"/>')

# engineering grid
step = 60
for x in range(0, W, step):
    p.append(f'<line x1="{x}" y1="0" x2="{x}" y2="{H}" stroke="#1b2a3d" stroke-opacity="0.35" stroke-width="1"/>')
for y in range(0, H, step):
    p.append(f'<line x1="0" y1="{y}" x2="{W}" y2="{y}" stroke="#1b2a3d" stroke-opacity="0.35" stroke-width="1"/>')
for x in range(0, W, step * 5):
    p.append(f'<line x1="{x}" y1="0" x2="{x}" y2="{H}" stroke="#24374f" stroke-opacity="0.5" stroke-width="1"/>')
for y in range(0, H, step * 5):
    p.append(f'<line x1="0" y1="{y}" x2="{W}" y2="{y}" stroke="#24374f" stroke-opacity="0.5" stroke-width="1"/>')

# a network of envelopes, left and right, suggesting flow
import random
random.seed(7)
for side, colour in ((0, BLUE), (W, GREEN)):
    nodes = [(random.randint(W * 0.55, W - 120) if side else random.randint(120, W * 0.38),
              random.randint(140, H - 200)) for _ in range(16)]
    for i in range(len(nodes) - 1):
        x1, y1 = nodes[i]; x2, y2 = nodes[i + 1]
        p.append(f'<path d="M{x1},{y1} C{(x1+x2)//2},{y1} {(x1+x2)//2},{y2} {x2},{y2}" '
                 f'fill="none" stroke="{colour}" stroke-opacity="0.18" stroke-width="1.4"/>')
    for x, y in nodes:
        p.append(f'<circle cx="{x}" cy="{y}" r="3.2" fill="{colour}" fill-opacity="0.55"/>')

# title block
p.append(f'<text x="150" y="470" fill="{BLUE}" font-size="26" font-weight="700" '
         f'letter-spacing="0.42em">CRYPTALYST</text>')
p.append(f'<text x="150" y="640" fill="#ffffff" font-size="128" font-weight="800" '
         f'letter-spacing="-0.02em">DOMAIN MODEL</text>')
p.append(f'<rect x="150" y="700" width="900" height="4" fill="url(#rule)"/>')
p.append(f'<text x="150" y="790" fill="#b9c8da" font-size="34">from the first socket message '
         f'to the byte on disk</text>')
p.append(f'<text x="150" y="850" fill="#6f8199" font-size="24">seven bounded contexts · one envelope '
         f'contract · one record</text>')

# context chips
chips = [("MARKET DATA", BLUE), ("INTELLIGENCE", GREEN), ("BOOKS", AMBER),
         ("RISK", "#ef6b6b"), ("RECORDING", VIOLET), ("FORENSICS", VIOLET), ("CONTROL", "#7f8ea3")]
x = 150
for name, colour in chips:
    w = 24 + len(name) * 13.5
    p.append(f'<rect x="{x}" y="960" width="{w}" height="46" rx="23" fill="{colour}" '
             f'fill-opacity="0.10" stroke="{colour}" stroke-opacity="0.55"/>')
    p.append(f'<text x="{x + w/2}" y="989" fill="{colour}" font-size="17" text-anchor="middle" '
             f'letter-spacing="0.12em">{html.escape(name)}</text>')
    x += w + 16

# footer plate
p.append(f'<line x1="150" y1="1420" x2="{W-150}" y2="1420" stroke="#24374f"/>')
p.append(f'<text x="150" y="1470" fill="#8ea0b6" font-size="22">Platform build 1.0.0 · 823eb7a · 2026-10-21</text>')
p.append(f'<text x="150" y="1512" fill="#5a6b80" font-size="19">© 2026 CryptAlyst · designed by '
         f'Andrew Preston · built by the CryptAlyst engineering team</text>')
p.append(f'<text x="{W-150}" y="1470" fill="#5a6b80" font-size="19" text-anchor="end">'
         f'seven contexts · eight figures · four levels</text>')
p.append(f'<text x="{W-150}" y="1512" fill="#3f4d60" font-size="17" text-anchor="end">'
         f'domain model · revision 1.0</text>')

# corner marks
for cx, cy in ((70, 70), (W-70, 70), (70, H-70), (W-70, H-70)):
    p.append(f'<path d="M{cx-18},{cy} L{cx+18},{cy} M{cx},{cy-18} L{cx},{cy+18}" '
             f'stroke="#2c405a" stroke-width="1.4"/>')

p.append("</svg>")
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fig", "00-cover.svg")
with open(out, "w") as fh:
    fh.write("\n".join(p))
print("wrote", out)
