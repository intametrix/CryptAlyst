#!/usr/bin/env python3
"""Build the CryptAlyst Developer Guide.

Typography, page setup, cover and footer come from docs/buildkit.py, which implements
docs/style-guide.md: A4, Calibri 10 pt, accent #1F4E79, the cover from docs/cover.md, and
page numbers in the footer — not on the cover.
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import buildkit as kit

def fig(n, caption):
    return kit.fig(os.path.join(HERE, f"d{n}.png"), caption)

def table(headers, rows, widths=None):
    return kit.table(headers, rows, widths)

CONTENT = open(os.path.join(HERE, "body.html")).read()
OUT = os.path.join(os.path.dirname(HERE), "CryptAlyst-Developer-Guide.pdf")

kit.build(
    title="CryptAlyst · Developer Guide",
    subtitle="Developer Guide",
    desc=("How the platform is put together,<br/>"
          "how to change it without breaking it,<br/>"
          "and what each gate actually proves."),
    body_html=kit.page("CryptAlyst Developer Guide") + CONTENT + "</body></html>",
    out_pdf=OUT,
)
print("wrote", OUT)
