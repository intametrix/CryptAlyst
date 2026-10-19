/**
 * Builds the render smoke bundle with Vite.
 *
 * Vite's bundler (`rolldown`) is a *native* module. Under some Node binaries its
 * `.node` binary fails to load for codesigning reasons ("mapping process and
 * mapped file have different Team IDs"), which is an environment limitation, not
 * a project one — the app's own `pnpm build` goes through Vite's client path and
 * is unaffected.
 *
 * So this script tries the Node that is running it first and falls back to the
 * `node` on PATH, which is the combination that works in this checkout:
 *
 *   - the app build:  `pnpm build`      (any Node)
 *   - the smoke build: needs a Node whose native module loads
 *
 * Override with `PUMPDUMP_NODE=/path/to/node`.
 */
import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, '..');
const vite = join(root, 'node_modules', 'vite', 'bin', 'vite.js');

if (!existsSync(vite)) {
  console.error(`smoke: vite not found at ${vite}`);
  process.exit(2);
}

// Only binaries we have not already tried: an empty `PUMPDUMP_SMOKE_TRIED`
// marker is set on each spawn, so a single command line does not re-probe the
// same Node repeatedly (and cannot loop).
const tried = new Set((process.env.PUMPDUMP_SMOKE_TRIED ?? '').split('|').filter((v) => v !== ''));
const candidates = [process.env.PUMPDUMP_NODE, process.execPath, 'node'].filter(
  (v) => typeof v === 'string' && v !== '' && !tried.has(v),
);

let chosen = null;
for (const candidate of candidates) {
  const probe = spawnSync(candidate, [vite, 'build', '--config', 'vite.smoke.config.ts'], {
    cwd: root,
    stdio: 'pipe',
    encoding: 'utf8',
    env: {
      ...process.env,
      PUMPDUMP_SMOKE_TRIED: [...tried, candidate].join('|'),
    },
  });
  if (probe.status === 0) {
    chosen = candidate;
    if (probe.stdout.trim() !== '') process.stdout.write(probe.stdout);
    break;
  }
  // Re-running the whole probe from the working Node would duplicate the build;
  // hand the remaining candidates to a child of this same script instead.
  if (candidate === process.execPath) {
    const fallback = candidates.filter((c) => c !== process.execPath);
    if (fallback.length > 0) {
      const next = spawnSync(process.execPath, [fileURLToPath(import.meta.url)], {
        cwd: root,
        stdio: 'inherit',
        env: {
          ...process.env,
          PUMPDUMP_NODE: fallback[0],
        },
      });
      process.exit(next.status ?? 1);
    }
  }
}

if (chosen === null) {
  console.error('smoke: could not build the smoke bundle with any available Node binary.');
  console.error('smoke: set PUMPDUMP_NODE to a Node whose native modules load (see this script).');
  process.exit(1);
}

console.log(`smoke: built with ${chosen === process.execPath ? 'the running Node' : chosen}`);
