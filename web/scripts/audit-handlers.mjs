/**
 * Dead-handler audit: finds interactive controls that render with a className
 * but no handler — the class of bug that shipped .pane-hide into the dist
 * while its handler had been reverted. A <button>/<select>/<div role="button">
 * that is not disabled and carries no onClick/onChange/onInput is reported.
 *
 *   node scripts/audit-handlers.mjs
 *
 * Exits non-zero when any control lacks a handler (a checker that reports
 * problems and succeeds is a lint a CI would pass while the problems stand).
 */
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const root = process.argv[2] ?? resolve(here, '..', 'src');

const CONTROL = /<(button|select|div|span|li)\b([^>]*?)>/g;
const HANDLER = /\b(onClick|onChange|onInput|onPointerDown|onPointerMove|onPointerUp|onKeyDown|onDoubleClick|onSubmit)=/;
const DISABLED = /\bdisabled\b/;
const ROLE_BUTTON = /role="(button|menuitem|switch)"/;
const INTERACTIVE_CLASS = /className=/;

function walk(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    const st = statSync(path);
    if (st.isDirectory()) out.push(...walk(path));
    else if (name.endsWith('.tsx')) out.push(path);
  }
  return out;
}

let violations = 0;
let checked = 0;
let scanned = 0;
for (const file of walk(root)) {
  const src = readFileSync(file, 'utf8');
  // strip block comments so commented-out controls are not audited
  const code = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\{?\/\*[\s\S]*?\*\/\}?/g, '');
  for (const m of code.matchAll(CONTROL)) {
    scanned += 1;
    const [, tag, attrs] = m;
    const isControl =
      tag === 'button' || tag === 'select' || ROLE_BUTTON.test(attrs) || tag === 'li' || tag === 'div' || tag === 'span';
    if (!isControl) continue;
    if (!INTERACTIVE_CLASS.test(attrs)) continue;
    if (DISABLED.test(attrs) || HANDLER.test(attrs)) continue;
    const line = code.slice(0, m.index).split('\n').length;
    const cls = /className=\{?`?"?([^"`}>]*)"?\}?/.exec(attrs)?.[1]?.trim().slice(0, 40) ?? '';
    // A bare presentational <div>/<span>/<li> with a class is not a control;
    // only interactive-looking tags and role=button reach here as violations.
    if (tag === 'div' || tag === 'span' || tag === 'li') {
      // role="button" already matched; plain divs/spans are layout, skip.
      if (!ROLE_BUTTON.test(attrs)) continue;
    }
    checked += 1;
    violations += 1;
    console.log(`DEAD-HANDLER ${file.slice(root.length + 1)}:${line} <${tag}> class="${cls}" — no handler and not disabled`);
  }
}

console.log(`audit-handlers: scanned ${scanned} tags, ${checked} interactive controls, ${violations} without a handler`);
process.exit(violations === 0 ? 0 : 1);
