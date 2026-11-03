#!/usr/bin/env python3
"""Build the CryptAlyst User Guide.

Typography, page setup, cover and footer come from docs/buildkit.py, which implements
docs/style-guide.md: A4, Calibri 10 pt, accent #1F4E79, the cover from docs/cover.md, and
page numbers in the footer — not on the cover.
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import buildkit as kit

def fig(n, caption):
    return kit.fig(os.path.join(HERE, f"u{n}.png"), caption)

def table(headers, rows, widths=None):
    return kit.table(headers, rows, widths)

CONTENT = open(os.path.join(HERE, "body.html")).read()
OUT = os.path.join(os.path.dirname(HERE), "CryptAlyst-User-Guide.pdf")

kit.build(
    title="CryptAlyst · User Guide",
    subtitle="User Guide",
    desc=("What the dashboard shows, what it means,<br/>"
          "and how to read it without fooling yourself."),
    body_html=kit.page("CryptAlyst User Guide") + CONTENT + "</body></html>",
    out_pdf=OUT,
)
print("wrote", OUT)
