/**
 * Mock refdata API + static server for the reference-data viewer screenshots.
 *
 * Serves scalp5/core/cmd/refdata statically (the layout its index.html expects:
 * /static/*) and answers /v1/* from the real registry at
 * scalp5/state/refdata/refdata.jsonl, so the viewer renders its true content
 * while the platform is stopped.
 *
 *   node scripts/mock-refdata.mjs [port]      # default 8801
 */
import { createServer } from 'node:http';
import { readFileSync, existsSync, statSync } from 'node:fs';
import { dirname, extname, join, normalize, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const webRoot = resolve(here, '..');
const repo = resolve(webRoot, '..');
const staticRoot = join(repo, 'scalp5', 'core', 'cmd', 'refdata');
const registryPath = join(repo, 'scalp5', 'state', 'refdata', 'refdata.jsonl');
const port = Number(process.argv[2] ?? 8801);

if (!existsSync(registryPath)) {
  console.error(`registry not found: ${registryPath}`);
  process.exit(1);
}

const instruments = new Map();
const documents = [];
const edges = [];
for (const line of readFileSync(registryPath, 'utf8').split('\n')) {
  if (!line.trim()) continue;
  let rec;
  try {
    rec = JSON.parse(line);
  } catch {
    continue;
  }
  if (rec.kind === 'instrument' && rec.instrument) instruments.set(rec.instrument.id, rec.instrument);
  else if (rec.kind === 'document' && rec.document) documents.push(rec.document);
  else if (rec.kind === 'edge' && rec.edge) edges.push(rec.edge);
}
const instList = [...instruments.values()];

const MIME = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.json': 'application/json' };

const server = createServer((req, res) => {
  const url = new URL(req.url ?? '/', `http://127.0.0.1:${port}`);
  const path = url.pathname;
  const sendJson = (obj, code = 200) => {
    res.writeHead(code, { 'content-type': 'application/json' });
    res.end(JSON.stringify(obj));
  };

  if (path === '/v1/instruments') return sendJson(instList);
  if (path.startsWith('/v1/instruments/')) {
    const rest = decodeURIComponent(path.slice('/v1/instruments/'.length));
    if (rest.endsWith('/documents')) {
      const id = rest.slice(0, -'/documents'.length);
      return sendJson(documents.filter((d) => JSON.stringify(d).includes(id)));
    }
    const inst = instruments.get(rest);
    return inst ? sendJson(inst) : sendJson({ error: 'not found' }, 404);
  }
  if (path === '/v1/documents') return sendJson(documents);
  if (path === '/v1/edges') return sendJson(edges);
  if (path === '/v1/stats') {
    const identifiers = instList.reduce((n, r) => n + Object.keys(r.identifiers ?? {}).length, 0);
    return sendJson({ instruments: instList.length, documents: documents.length, edges: edges.length, identifiers });
  }

  const file = normalize(join(staticRoot, path === '/' ? 'static/index.html' : path));
  if (!file.startsWith(staticRoot) || !existsSync(file) || !statSync(file).isFile()) {
    res.writeHead(404);
    res.end('not found');
    return;
  }
  res.writeHead(200, { 'content-type': MIME[extname(file)] ?? 'application/octet-stream' });
  res.end(readFileSync(file));
});

server.listen(port, '127.0.0.1', () => {
  console.log(`refdata viewer on http://127.0.0.1:${port}/  (${instList.length} instruments, ${documents.length} docs, ${edges.length} edges)`);
});
