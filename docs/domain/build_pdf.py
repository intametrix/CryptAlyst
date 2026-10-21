#!/usr/bin/env python3
"""Build 'CryptAlyst — a domain model' as an A4 PDF from content.py and the figures."""
import os

from PIL import Image
from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Inches, Pt, RGBColor

import content

HERE = os.path.dirname(os.path.abspath(__file__))
FIG = os.path.join(HERE, "fig")
ACCENT = RGBColor(0x1B, 0x4A, 0x6F)
INK = RGBColor(0x1A, 0x22, 0x2C)
MUTED = RGBColor(0x5C, 0x66, 0x72)
RULE = "D8DEE6"
CONTENT_W = 21.0 / 2.54 - 1.6

doc = Document()
sec = doc.sections[0]
sec.page_width, sec.page_height = Cm(21.0), Cm(29.7)
sec.left_margin = sec.right_margin = Inches(0.8)
sec.top_margin = Inches(0.75)
sec.bottom_margin = Inches(0.75)

normal = doc.styles["Normal"]
normal.font.name = "Calibri"
normal.font.size = Pt(10.5)
normal.font.color.rgb = INK
normal.paragraph_format.space_after = Pt(7)
normal.paragraph_format.line_spacing = 1.18


def h1(text):
    para = doc.add_paragraph()
    para.paragraph_format.space_before = Pt(16)
    para.paragraph_format.space_after = Pt(8)
    r = para.add_run(text)
    r.font.size = Pt(17); r.bold = True; r.font.color.rgb = ACCENT
    bar = OxmlElement("w:pBdr"); bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single"); bottom.set(qn("w:sz"), "6")
    bottom.set(qn("w:space"), "6"); bottom.set(qn("w:color"), RULE)
    bar.append(bottom); para._p.get_or_add_pPr().append(bar)


def para(text, size=None, italic=False, colour=None, align=None, space=7):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(space)
    if align:
        p.alignment = align
    r = p.add_run(text)
    r.italic = italic
    if size: r.font.size = Pt(size)
    if colour: r.font.color.rgb = colour
    return p


def bullets(items):
    for it in items:
        p = doc.add_paragraph(style="List Bullet")
        p.paragraph_format.space_after = Pt(3)
        p.add_run(it).font.size = Pt(10.2)


def table(head, rows):
    t = doc.add_table(rows=1, cols=len(head))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    for i, name in enumerate(head):
        cell = t.rows[0].cells[i]
        cell.text = ""
        r = cell.paragraphs[0].add_run(name.upper())
        r.bold = True; r.font.size = Pt(7.8); r.font.color.rgb = MUTED
        shd = OxmlElement("w:shd"); shd.set(qn("w:fill"), "F1F4F7")
        cell._tc.get_or_add_tcPr().append(shd)
    for row in rows:
        cells = t.add_row().cells
        for i, val in enumerate(row):
            cells[i].text = ""
            r = cells[i].paragraphs[0].add_run(val)
            r.font.size = Pt(9.2)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)


def figure(name, caption):
    path = os.path.join(FIG, name)
    with Image.open(path) as im:
        w, h = im.size
    width = min(CONTENT_W, 6.5)
    height = width * h / w
    if height > 4.6:
        height = 4.6
        width = height * w / h
    doc.add_picture(path, width=Inches(width))
    doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
    cap = doc.add_paragraph()
    cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
    cap.paragraph_format.space_before = Pt(2)
    cap.paragraph_format.space_after = Pt(10)
    r = cap.add_run(caption)
    r.italic = True; r.font.size = Pt(8.6); r.font.color.rgb = MUTED


# ---------------------------------------------------------------- cover
cover = doc.add_paragraph()
cover.paragraph_format.space_before = Pt(120)
cover.alignment = WD_ALIGN_PARAGRAPH.LEFT
r = cover.add_run("CRYPTALYST")
r.font.size = Pt(12); r.bold = True; r.font.color.rgb = MUTED
for run, size, colour, space in (("a domain model", 40, ACCENT, 6),
                                 ("from the first socket message to the byte on disk", 15, MUTED, 18)):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(space)
    rr = p.add_run(run); rr.font.size = Pt(size); rr.font.color.rgb = colour
    if size == 40: rr.bold = True

para(content.BLURB, size=12, colour=INK, space=14)
para("Seven contexts · eight figures · four levels, down to the filesystem", size=9.5, colour=MUTED)
p = doc.add_paragraph(); p.add_run().add_break(WD_BREAK.PAGE)

# ---------------------------------------------------------------- contents
h1("Contents")
for i, s in enumerate(content.SECTIONS, 1):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(3)
    r = p.add_run("%02d   " % i); r.font.color.rgb = MUTED; r.font.size = Pt(9.5)
    p.add_run(s["title"]).font.size = Pt(10.5)
p = doc.add_paragraph(); p.add_run().add_break(WD_BREAK.PAGE)

# ---------------------------------------------------------------- body
for i, s in enumerate(content.SECTIONS, 1):
    if i > 1:
        doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
    h1("%02d  %s" % (i, s["title"]))
    if s.get("figure"):
        name, caption = s["figure"]
        figure(name, caption)
    for text in s.get("body", []):
        para(text)
    if s.get("bullets"):
        bullets(s["bullets"])
    if s.get("table"):
        head, rows = s["table"]
        table(head, rows)
    for text in s.get("body2", []):
        para(text)

# ---------------------------------------------------------------- footer
for section in doc.sections:
    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = footer.add_run("CryptAlyst — a domain model   ·   ")
    run.font.size = Pt(8); run.font.color.rgb = MUTED
    fld = OxmlElement("w:fldSimple"); fld.set(qn("w:instr"), "PAGE")
    footer._p.append(fld)

out = os.path.join(HERE, "CryptAlyst-Domain-Model.docx")
doc.save(out)
print("saved", out)
