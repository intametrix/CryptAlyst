/**
 * Runs the pure-logic verification suite (`scripts/verify-logic.mjs`) with the
 * resolve/transpile hooks it needs.
 *
 * The child process is started with the *same* Node binary that runs this file,
 * so `pnpm verify` behaves identically no matter which Node is on PATH.
 */
import { spawnSync } from 'node:child_process';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const result = spawnSync(
  process.execPath,
  ['--import', resolve(here, 'ts-resolve-register.mjs'), resolve(here, 'verify-logic.mjs')],
  { stdio: 'inherit', cwd: resolve(here, '..') },
);
process.exit(result.status ?? 1);
