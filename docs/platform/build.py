#!/usr/bin/env python3
"""Build 'The Envelope, as built'.

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
OUT = os.path.join(os.path.dirname(HERE), "The-Envelope-As-Built.pdf")

kit.build(
    title="CryptAlyst · The Envelope, as built",
    subtitle="The Envelope, as built",
    desc=("One packet shape, four interceptors, a recorded lineage,<br/>"
          "a bus, and a decision that can be replayed."),
    body_html=kit.page("The Envelope, as built") + CONTENT + "</body></html>",
    out_pdf=OUT,
)
print("wrote", OUT)
