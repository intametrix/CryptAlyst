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
