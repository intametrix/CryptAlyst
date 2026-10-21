# Domain model — documents and sources

| File | What it is |
|---|---|
| `CryptAlyst-Domain-Model.pdf` | A4 **landscape**, 16 pages, dark cover, numbered figures |
| `CryptAlyst-Domain-Model.docx` | the source of the PDF |
| `DOMAIN-MODEL.md` | the same content as Markdown, figures inline |
| `content.py` | the text once — both outputs are generated from it |
| `diagrams.py` + `cover.py` | the nine figures, drawn as SVG |
| `render_figs.py` | SVG → PNG through headless Chrome |
| `build_md.py`, `build_pdf.py` | the two builders |

Format rules, matching the Design and Architecture Reference:

- every text layer is measured and fitted: box titles, box rows, the tag column,
  diamond labels, arrow labels, notes and the figure header. Each is set as large as
  its space allows (up to 25 px for the header, 16 px for a row) and never larger,
  with a 6% safety margin so a line never touches the border it is measured against
- figures run at full content width (10.2 in), which prints a 16 px label at about
  7 pt instead of 5 pt

- every figure is numbered and captioned where it appears, and listed in the contents
- every section opens with **What this shows**, one sentence, before the figure
- page numbers in the footer on every page **except the cover**
- the cover carries the build identity: version, commit, date, copyright

Regenerate everything:

```bash
cd docs/domain
python3 diagrams.py && python3 cover.py && python3 render_figs.py
python3 build_md.py && python3 build_pdf.py
```
