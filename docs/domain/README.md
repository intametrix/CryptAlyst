# Domain model — documents and sources

| File | What it is |
|---|---|
| `DOMAIN-MODEL.md` | the model as Markdown, figures inline |
| `CryptAlyst-Domain-Model.pdf` | the same, typeset for reading |
| `CryptAlyst-Domain-Model.docx` | the source of the PDF |
| `content.py` | the text, once — both outputs are generated from it |
| `diagrams.py` | the eight figures, drawn as SVG |
| `render_figs.py` | SVG → PNG through headless Chrome |
| `build_md.py`, `build_pdf.py` | the two builders |

Regenerate everything:

```bash
cd docs/domain
python3 diagrams.py && python3 render_figs.py
python3 build_md.py && python3 build_pdf.py
```

The figures are hand-laid-out on purpose: a domain model is an argument about what
belongs together, and an automatic layout cannot make that argument.
