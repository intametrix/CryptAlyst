const fs = require('fs');
const MarkdownIt = require('markdown-it');
const puppeteer = require('puppeteer-core');

const md = new MarkdownIt({ html: true, linkify: true });
let html = md.render(fs.readFileSync('ARCHITECTURE.md', 'utf8'));

// wrap each figure (img + optional caption) in a landscape page section
html = html.replace(/<p><img([^>]*)><\/p>(\s*<p><em>Figure[^<]*<\/em><\/p>)?/g, (m, img, cap) => {
  return `<section class="landscape"><img${img}>${cap || ''}</section>`;
});

const css = `
@page { size: A4 portrait; margin: 15mm 16mm 16mm 16mm; }
@page landscape { size: A4 landscape; margin: 10mm 12mm 12mm 12mm; }
body { font-family: Helvetica, Arial, sans-serif; font-size: 10pt; color: #1a1a1a; line-height: 1.42; }
h1 { color: #1F4E79; font-size: 16pt; }
h2 { color: #1F4E79; font-size: 12pt; }
h3 { color: #596370; font-size: 10.5pt; }
table { border-collapse: collapse; width: 100%; font-size: 8.5pt; page-break-inside: avoid; }
th { background: #1F4E79; color: #ffffff; font-weight: bold; padding: 4px 6px; text-align: left; }
td { border: 1px solid #d0d7e2; padding: 3px 6px; }
img { max-width: 100%; }
em { color: #596370; font-size: 8pt; }
pre { background: #F1F3F6; font-family: Courier, monospace; font-size: 8pt; padding: 6px; }
blockquote { border-left: 3px solid #1F4E79; margin-left: 0; padding-left: 12px; color: #444; }
section.landscape { page: landscape; break-before: page; break-after: page; display: flex; flex-direction: column; align-items: center; justify-content: center; }
section.landscape img { max-width: 100%; max-height: 72vh; }
section.landscape em { margin-top: 6px; text-align: center; }
`;

const full = `<!doctype html><html><head><meta charset="utf-8"><style>${css}</style></head><body>${html}</body></html>`;

(async () => {
  const browser = await puppeteer.launch({ executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', args: ['--no-sandbox'] });
  const page = await browser.newPage();
  await page.setContent(full, { waitUntil: 'networkidle0' });
  await page.pdf({
    path: 'content.pdf',
    preferCSSPageSize: true,
    printBackground: true,
    displayHeaderFooter: true,
    headerTemplate: '<div></div>',
    footerTemplate: '<div style="width:100%;font-size:7.5px;color:#596370;padding:0 12px;display:flex;justify-content:space-between;font-family:Helvetica,Arial,sans-serif;"><span>Designed by Andrew John Preston · TekVolt Systems</span><span>Page <span class="pageNumber"></span> of <span class="totalPages"></span></span></div>',
    margin: { top: '12mm', bottom: '12mm', left: '0', right: '0' }
  });
  await browser.close();
  console.log('rendered content.pdf');
})();
