const fs = require('fs');
const path = require('path');
const MarkdownIt = require('markdown-it');
const puppeteer = require('puppeteer-core');
const md = new MarkdownIt({ html: true, linkify: true });
let html = md.render(fs.readFileSync('uk-gas/UK-GAS.md', 'utf8'));
html = html.replace(/<p><img([^>]*)><\/p>(\s*<p><em>Figure[^<]*<\/em><\/p>)?/g, (m, img, cap) => {
  return `<section class="fig"><img${img}>${cap || ''}</section>`;
});
const css = `
@page { size: A4 landscape; margin: 11mm 14mm 14mm 14mm; }
html, body { margin: 0; padding: 0; }
body { font-family: Helvetica, Arial, sans-serif; font-size: 10pt; color: #1a1a1a; line-height: 1.42; }
h1 { color: #1F4E79; font-size: 16pt; break-before: page; break-after: avoid-page; page-break-before: always; page-break-after: avoid; }
h2 { color: #1F4E79; font-size: 12pt; break-after: avoid-page; page-break-after: avoid; }
table { border-collapse: collapse; width: 100%; font-size: 8.5pt; page-break-inside: avoid; }
th { background: #1F4E79; color: #ffffff; font-weight: bold; padding: 4px 6px; text-align: left; }
td { border: 1px solid #d0d7e2; padding: 3px 6px; }
img { max-width: 100%; }
em { color: #596370; font-size: 8pt; }
pre { background: #F1F3F6; font-family: Courier, monospace; font-size: 8pt; padding: 6px; }
blockquote { border-left: 3px solid #1F4E79; margin-left: 0; padding-left: 12px; color: #444; }
section.fig { break-inside: avoid; page-break-inside: avoid; margin: 10px 0; display: flex; flex-direction: column; align-items: center; justify-content: center; }
section.fig img { max-width: 100%; max-height: 55vh; }
section.fig em { margin-top: 6px; text-align: center; }
`;
const full = `<!doctype html><html><head><meta charset="utf-8"><style>${css}</style></head><body>${html}</body></html>`;
const htmlPath = path.resolve('uk-gas/UK-GAS.html');
fs.writeFileSync(htmlPath, full);
(async () => {
  const browser = await puppeteer.launch({ executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', args: ['--no-sandbox'] });
  const page = await browser.newPage();
  await page.goto('file://' + htmlPath, { waitUntil: 'networkidle0' });
  await page.pdf({ path: 'uk-gas/UK-GAS.pdf', preferCSSPageSize: true, printBackground: true,
    displayHeaderFooter: true, headerTemplate: '<div></div>',
    footerTemplate: '<div style="width:100%;text-align:center;font-size:11px;color:#596370;font-family:Helvetica,Arial,sans-serif;">Designed by Andrew John Preston · TekVolt Systems &nbsp;·&nbsp; Page <span class="pageNumber"></span> of <span class="totalPages"></span></div>',
    margin: { top: '12mm', bottom: '12mm', left: '0', right: '0' } });
  await browser.close();
  console.log('rendered nbp-power pdf');
})();
