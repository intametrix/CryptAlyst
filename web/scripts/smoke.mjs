/**
 * Builds `src/renderSmoke.tsx` with Vite and runs it with Node.
 *
 * This is the only check that actually *renders* the dashboard, so it catches
 * what type checking cannot: a bad property read inside a render, a placeholder
 * label leaking into the markup, a badge with no text.
 *
 * The build step runs through the project's own `vite` binary (see the `smoke`
 * script in package.json); this wrapper only orchestrates build-then-run.
 */
import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, '..');
const bundle = join(root, 'node_modules', '.tmp', 'smoke', 'smoke.mjs');

if (!existsSync(bundle)) {
  console.error(`smoke: missing ${bundle} — run "pnpm run smoke" (which builds it first)`);
  process.exit(2);
}

const result = spawnSync(process.execPath, [fileURLToPath(pathToFileURL(bundle))], {
  stdio: 'inherit',
  cwd: root,
});
process.exit(result.status ?? 1);
