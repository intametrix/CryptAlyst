/**
 * A structural audit of `src/styles.css`.
 *
 * The failure this guards against is subtle: in a resizable dashboard the box
 * changes size but the content inside it does not, and the usual cause is a
 * missing `min-width: 0` / `min-height: 0` on a flex or grid child. That is
 * invisible in review and in a type check, so it is checked mechanically:
 *
 *  1. every layout flex/grid container that holds a shrinkable child carries a
 *     min-size guard (or opts out with an explicit `flex: 0 0 auto`);
 *  2. no rule inside a resizable panel sets `height: auto` on a flex child;
 *  3. every resizable panel establishes a container (`container-type`);
 *  4. scrollable list wrappers declare `overflow`, so content can never spill
 *     invisibly past their box.
 *
 * Run: node scripts/audit-css.mjs   (exit 0 = clean)
 */
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const css = readFileSync(resolve(here, '..', 'src', 'styles.css'), 'utf8');

let pass = 0;
const problems = [];
const ok = (name, condition, detail = '') => {
  if (condition) pass += 1;
  else problems.push(`${name}${detail === '' ? '' : ` — ${detail}`}`);
};

/* -- strip comments and split into rules, tracking @media/@container --------- */
const cleaned = css.replace(/\/\*[\s\S]*?\*\//g, '');

/** Flat list of { selector, body, at } in source order. */
const rules = [];
for (const match of cleaned.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
  const selector = match[1].trim().replace(/\s+/g, ' ');
  const body = match[2];
  // Skip at-rule prelude fragments like `@media (...)`, which the regex pairs
  // with the inner block's body.
  if (selector.startsWith('@') || selector === '') continue;
  rules.push({ selector, body });
}

const layoutContainers = rules.filter(
  (r) =>
    /display:\s*(flex|grid|inline-flex)/.test(r.body) &&
    // Filter rows wrap and are not the shrink target; they are identified by the
    // presence of `flex-wrap` plus a chip/badge/legend selector.
    !/flex-wrap:\s*wrap/.test(r.body) &&
    !/\.(chip|badge|legend|tip-|live-pill|pending|disclosure|heat-legend)/.test(r.selector),
);

/* -- 1. min-size guards ------------------------------------------------------ */
for (const rule of layoutContainers) {
  const isColumn = /flex-direction:\s*column/.test(rule.body) || /display:\s*grid/.test(rule.body);
  const guarded = isColumn ? /min-height:\s*0/.test(rule.body) : /min-width:\s*0/.test(rule.body);
  const optsOut = /flex:\s*0 0 auto/.test(rule.body) || /position:\s*(absolute|fixed)/.test(rule.body);
  ok(
    `min-size guard: ${rule.selector}`,
    guarded || optsOut,
    isColumn ? 'column/grid without min-height: 0' : 'row without min-width: 0',
  );
}

/* -- 2. no `height: auto` on flex children ---------------------------------- */
const autoHeights = rules.filter(
  (r) => /(^|;)\s*height:\s*auto/.test(r.body) && /(^|;)\s*flex:/.test(r.body),
);
ok('no flex child sets height: auto', autoHeights.length === 0, autoHeights.map((r) => r.selector).join(', '));

/* -- 3. resizable panels and the overlay establish containers ---------------- */
const resizableRule = rules.find((r) => r.selector === '.resizable');
ok('.resizable establishes a container', /container-type:\s*inline-size/.test(resizableRule?.body ?? ''));
ok('.resizable is a flex column', /flex-direction:\s*column/.test(resizableRule?.body ?? ''));
ok('.resizable clips its content', /overflow:\s*hidden/.test(resizableRule?.body ?? ''));
ok('.resizable can shrink', /min-width:\s*0/.test(resizableRule?.body ?? '') && /min-height:\s*0/.test(resizableRule?.body ?? ''));
ok('container queries exist', /@container\s+panel\s*\(/.test(css), 'no @container rules found');
ok(
  'container queries cover the narrow case',
  /@container\s+panel\s*\(max-width:/.test(css) && /@container\s+panel\s*\(min-width:/.test(css),
);

/* -- 4. scroll wrappers declare overflow ------------------------------------ */
for (const selector of ['.alert-list', '.flow-rows', '.table-wrap', '.overlay-body', '.chart-host']) {
  const rule = rules.find((r) => r.selector === selector);
  ok(`scroll wrapper declares overflow: ${selector}`, /overflow/.test(rule?.body ?? ''), rule?.body ?? 'rule missing');
}

/* -- 5. the chart's box is definite ---------------------------------------- */
const host = rules.find((r) => r.selector === '.chart-host');
ok('chart host has a definite height', /height:\s*100%/.test(host?.body ?? ''), host?.body ?? 'rule missing');
ok('chart host is shrinkable', /flex:\s*1 1 0/.test(host?.body ?? '') && /min-height:\s*0/.test(host?.body ?? ''));
ok('chart host clips mid-drag sizes', /overflow:\s*hidden/.test(host?.body ?? ''));

/* -- 6. tables never overflow invisibly ------------------------------------ */
const wrap = rules.find((r) => r.selector === '.table-wrap');
ok('table wrapper scrolls horizontally', /overflow:\s*auto/.test(wrap?.body ?? ''));
ok('table wrapper is shrinkable', /min-width:\s*0/.test(wrap?.body ?? ''));
ok(
  'numeric and label cells truncate',
  /text-overflow:\s*ellipsis/.test(css) && /td\.num/.test(css) && /\.sym-btn/.test(css),
);

const total = pass + problems.length;
if (problems.length === 0) {
  console.log(`audit-css: ${pass}/${total} checks passed`);
} else {
  console.log(`audit-css: ${pass}/${total} checks passed, ${problems.length} problems`);
  for (const p of problems) console.log(`  FAIL ${p}`);
  process.exitCode = 1;
}
