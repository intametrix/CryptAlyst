#!/usr/bin/env python3
"""Shared document builder for the CryptAlyst guides.

Implements docs/style-guide.md exactly: Calibri/Helvetica 10 pt body, accent #1F4E79
headings at 15.5/12/10.5 pt, 8 pt italic captions, 8.3 pt table body on a
Light-Grid-Accent-1 grid, A4 with 0.8" side margins and 0.75" top/bottom, the cover
from docs/cover.md, and page numbers in the footer — not on the cover.

The cover is printed as its own PDF (no footer), the body as another with a footer
template, and the two are merged, which is how the first page gets no number.
"""
import base64, os, subprocess, tempfile, json

HERE = os.path.dirname(os.path.abspath(__file__))
ACCENT, MUTED, MONO_FILL = "#1F4E79", "#596370", "#F1F3F6"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

# --- page geometry: A4, 0.8" sides, 0.75" top and bottom -----------------------
CSS = f"""
@page {{ size: A4; margin: 0.75in 0.8in 0.9in 0.8in; }}
* {{ box-sizing: border-box; }}
body {{ font-family: Calibri, Helvetica, Arial, sans-serif; font-size: 10pt;
        color: #1a1a1a; line-height: 1.1; margin: 0; }}
p {{ margin: 0 0 5pt; }}
h1 {{ font-size: 15.5pt; font-weight: bold; color: {ACCENT}; margin: 0 0 8pt; }}
h2 {{ font-size: 12pt; font-weight: bold; color: {ACCENT}; margin: 14pt 0 5pt;
      page-break-after: avoid; }}
h3 {{ font-size: 10.5pt; font-weight: bold; color: {MUTED}; margin: 10pt 0 3pt;
      page-break-after: avoid; }}
.lede {{ font-size: 11pt; color: {MUTED}; margin: 0 0 10pt; }}
.meta {{ font-size: 8.5pt; color: {MUTED}; border-top: 0.5pt solid #d8e0e8;
         border-bottom: 0.5pt solid #d8e0e8; padding: 5pt 0; margin: 8pt 0 12pt; }}
figure {{ margin: 8pt 0; page-break-inside: avoid; text-align: center; }}
figure img {{ max-width: 100%; max-height: 7.0in; width: auto; height: auto; }}
figcaption {{ font-size: 8pt; font-style: italic; color: {MUTED}; margin-top: 3pt;
              text-align: center; }}
table {{ width: 100%; border-collapse: collapse; margin: 5pt 0 9pt; }}
th {{ background: {ACCENT}; color: #ffffff; font-weight: bold; text-align: left;
      padding: 3pt 5pt; border: 0.5pt solid #8ea9c1; font-size: 8.6pt; }}
td {{ padding: 2.5pt 5pt; border: 0.5pt solid #c9d4e0; font-size: 8.3pt;
      vertical-align: top; }}
tbody tr:nth-child(even) td {{ background: #f4f7fa; }}
pre {{ background: {MONO_FILL}; padding: 5pt 7pt; margin: 5pt 0 8pt;
       font-family: "Courier New", Courier, monospace; font-size: 8pt; line-height: 1.35;
       white-space: pre-wrap; page-break-inside: avoid; }}
code {{ font-family: "Courier New", Courier, monospace; font-size: 8.5pt; }}
.note {{ border-left: 2.5pt solid {ACCENT}; background: #eef5fa; padding: 6pt 9pt; margin: 7pt 0; }}
.warn {{ border-left: 2.5pt solid #a4232b; background: #fdf0f2; padding: 6pt 9pt; margin: 7pt 0; }}
ul, ol {{ margin: 4pt 0 6pt 16pt; padding: 0; }} li {{ margin: 2pt 0; }}
.toc {{ columns: 2; column-gap: 22pt; font-size: 9.5pt; margin: 6pt 0 4pt; }}
.toc div {{ break-inside: avoid; margin: 1.5pt 0; }}
"""

COVER = """<!doctype html><html><head><meta charset="utf-8"><style>
  @page { size: A4; margin: 0; }
  html, body { margin: 0; padding: 0; }
  .cover { width: 210mm; height: 297mm; background: #0B2545; color: #ffffff;
           display: flex; flex-direction: column; justify-content: center;
           align-items: center; text-align: center; box-sizing: border-box; padding: 20mm; }
  .kicker { font-size: 10pt; letter-spacing: 6px; color: #7ea6d8; text-transform: uppercase;
            margin-bottom: 18px; font-family: Calibri, Helvetica, sans-serif; }
  .title { font-size: 54pt; font-weight: bold; letter-spacing: 2px; color: #ffffff;
           font-family: Calibri, Helvetica, sans-serif; }
  .rule { width: 180px; height: 3px; background: #1d4e89; margin: 22px auto; }
  .subtitle { font-size: 18pt; color: #d9e6f5; font-family: Calibri, Helvetica, sans-serif; }
  .desc { font-size: 11pt; color: #9ab4d4; margin-top: 12px; font-style: italic;
          line-height: 1.6; font-family: Calibri, Helvetica, sans-serif; }
  .attrib-label { margin-top: 60px; font-size: 10pt; color: #7ea6d8;
                  font-family: Calibri, Helvetica, sans-serif; }
  .attrib-name { font-size: 15pt; color: #ffffff; margin-top: 4px;
                 font-family: Calibri, Helvetica, sans-serif; }
  .attrib-org { font-size: 10pt; color: #9ab4d4; margin-top: 2px;
                font-family: Calibri, Helvetica, sans-serif; }
  .version { margin-top: 40px; font-size: 8pt; color: #5a7ba0;
             font-family: Calibri, Helvetica, sans-serif; }
</style></head><body>
<div class="cover">
  <div class="kicker">TekVolt Systems · Trading Architecture</div>
  <div class="title">CRYPTALYST</div>
  <div class="rule"></div>
  <div class="subtitle">__SUBTITLE__</div>
  <div class="desc">__DESC__</div>
  <div class="attrib-label">Designed by</div>
  <div class="attrib-name">Andrew John Preston</div>
  <div class="attrib-org">TekVolt Systems</div>
  <div class="version">Version 1.0 · __DATE__</div>
</div></body></html>"""


def page(title):
    """The opening of a body document: style-guide typography, no cover, no footer."""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>{title}</title><style>{CSS}</style></head><body>"""


def cover_html(subtitle, desc, date="November 2026"):
    return (COVER.replace("__SUBTITLE__", subtitle)
                 .replace("__DESC__", desc)
                 .replace("__DATE__", date))


def fig(path, caption):
    b64 = base64.b64encode(open(path, "rb").read()).decode()
    return (f'<figure><img src="data:image/png;base64,{b64}" alt="">'
            f'<figcaption>{caption}</figcaption></figure>')


def table(headers, rows, widths=None):
    cols = ""
    if widths:
        cols = "<colgroup>" + "".join(f'<col style="width:{w}">' for w in widths) + "</colgroup>"
    head = "".join(f"<th>{h}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table>{cols}<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


NODE = "/Users/users1/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/node/bin/node"

CDP_SCRIPT = r"""
// One job: navigate to a local HTML file and print it with a footer carrying the page
// number. Chrome's command line cannot show a footer; its DevTools printToPDF can.
import { writeFileSync } from 'node:fs';
const [wsUrl, fileUrl, outPath, title] = process.argv.slice(2);
const ws = new WebSocket(wsUrl);
let id = 0; const pend = new Map();
ws.onmessage = (e) => { const m = JSON.parse(e.data); if (m.id && pend.has(m.id)) { pend.get(m.id)(m); pend.delete(m.id); } };
const send = (method, params = {}) => new Promise(r => { const i = ++id; pend.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
ws.onopen = async () => {
  await send('Page.enable');
  await send('Page.navigate', { url: fileUrl });
  await new Promise(r => setTimeout(r, 3500));
  const footer = '<div style="width:100%;font-family:Calibri,Helvetica,Arial,sans-serif;'
    + 'font-size:8pt;color:#596370;padding:0 0.8in;display:flex;justify-content:space-between;'
    + 'align-items:center;"><span>' + title + '</span><span>Page '
    + '<span class="pageNumber"></span> of <span class="totalPages"></span></span></div>';
  const r = await send('Page.printToPDF', { printBackground: true, displayHeaderFooter: true,
      headerTemplate: '<div></div>', footerTemplate: footer,
      marginTop: 0.75, marginBottom: 0.9, marginLeft: 0.8, marginRight: 0.8 });
  writeFileSync(outPath, Buffer.from(r.result.data, 'base64'));
  process.exit(0);
};
"""


def _free_port(start=9400, end=9500):
    import socket
    for p in range(start, end):
        s = socket.socket()
        ok = s.connect_ex(("127.0.0.1", p)) != 0
        s.close()
        if ok:
            return p
    raise RuntimeError("no free port")


def print_plain(html_path, pdf_path):
    """Cover page: chrome CLI, explicitly without any header or footer."""
    subprocess.run([CHROME, "--headless", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={pdf_path}", f"file://{html_path}"],
                   check=True, capture_output=True)


def print_numbered(html_path, pdf_path, title):
    """Body pages: printed through DevTools so the footer can carry the page number."""
    import time, urllib.request
    port = _free_port()
    profile = tempfile.mkdtemp(prefix="chrome-")
    chrome = subprocess.Popen([CHROME, "--headless=new", "--no-first-run",
                               f"--user-data-dir={profile}",
                               f"--remote-debugging-port={port}", "about:blank"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        page = None
        for _ in range(160):
            try:
                targets = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list"))
                pages = [t for t in targets if t["type"] == "page"]
                if pages:
                    page = pages[0]
                    break
            except Exception:
                pass
            time.sleep(0.25)
        if page is None:
            raise RuntimeError("chrome did not expose a target")
        script_path = os.path.join(profile, "print.mjs")
        open(script_path, "w").write(CDP_SCRIPT)
        subprocess.run([NODE, script_path, page["webSocketDebuggerUrl"],
                        f"file://{html_path}", pdf_path, title],
                       check=True, capture_output=True, timeout=180)
    finally:
        chrome.kill()


def build(title, subtitle, desc, body_html, out_pdf, date="November 2026"):
    """Cover without a page number, body numbered from one, merged into one PDF."""
    from pypdf import PdfWriter, PdfReader

    tmp = tempfile.mkdtemp(prefix="doc-")
    cover_html_path = os.path.join(tmp, "cover.html")
    body_html_path = os.path.join(tmp, "body.html")
    cover_pdf = os.path.join(tmp, "cover.pdf")
    body_pdf = os.path.join(tmp, "body.pdf")

    open(cover_html_path, "w").write(cover_html(subtitle, desc, date))
    open(body_html_path, "w").write(body_html)

    print_plain(cover_html_path, cover_pdf)
    print_numbered(body_html_path, body_pdf, title)

    writer = PdfWriter()
    for src in (cover_pdf, body_pdf):
        for p in PdfReader(src).pages:
            writer.add_page(p)
    with open(out_pdf, "wb") as fh:
        writer.write(fh)
    return out_pdf
