/**
 * Builds the reactivity harness and writes a single self-contained page into
 * `node_modules/.tmp/rt/index.html`.
 *
 * The page inlines the bundle (an external `<script src>` under `file://` is
 * unreliable) and links the extracted stylesheet, so the DOM is fully styled
 * before the harness measures anything — measuring unstyled DOM is how a
 * layout test lies to you.
 *
 *   node scripts/rt-bundle.mjs [entry]     entry defaults to main.tsx
 *
 * Vite's bundler is a native module that some Node binaries cannot load for
 * codesigning reasons (the app's own `pnpm build` uses a different path and is
 * unaffected), so the build is attempted with each candidate interpreter.
 * Override with `PUMPDUMP_NODE`.
 */
import { execFileSync } from 'node:child_process';
import { readFileSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, '..');
const entry = process.argv[2] ?? 'main.tsx';
const vite = join(root, 'node_modules', 'vite', 'bin', 'vite.js');
const outDir = join(root, 'node_modules', '.tmp', 'rt');

const candidates = [process.env.PUMPDUMP_NODE, process.execPath, 'node'].filter(
  (value, index, all) => typeof value === 'string' && value !== '' && all.indexOf(value) === index,
);

let lastError = '';
let built = false;
for (const node of candidates) {
  try {
    execFileSync(node, [vite, 'build', '--config', 'vite.rt.config.ts'], {
      cwd: root,
      stdio: 'pipe',
      env: { ...process.env, RT_ENTRY: entry },
    });
    built = true;
    break;
  } catch (error) {
    lastError = error instanceof Error ? error.message : String(error);
  }
}
if (!built) {
  console.error(`rt-bundle: no Node could run the Vite build.\n  tried: ${candidates.join(', ')}\n  last error: ${lastError}`);
  process.exit(1);
}

const js = readFileSync(join(outDir, 'rt.js'), 'utf8');
writeFileSync(
  join(outDir, 'index.html'),
  `<!doctype html>\n<html lang="en" data-theme="dark"><head><meta charset="UTF-8">` +
    `<title>pumpdump reactivity harness</title><link rel="stylesheet" href="./style.css"></head>` +
    `<body>\n<script type="module">\n${js}\n</script>\n</body></html>\n`,
  'utf8',
);
console.log(`rt-bundle: built ${entry} -> node_modules/.tmp/rt/index.html`);
