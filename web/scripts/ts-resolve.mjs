/**
 * ESM hooks used only by the verification scripts.
 *
 * Two problems are solved here, both so the checks can run against the real
 * source instead of a build:
 *
 * 1. the source uses bundler-style relative imports (`./format`, not
 *    `./format.ts`), which Node's resolver rejects — the `resolve` hook retries
 *    with a `.ts` (or `.tsx`) extension;
 * 2. Node strips types from `.ts` but does not understand JSX — the `load` hook
 *    transpiles `.tsx` with the project's own TypeScript, JSX preserved as the
 *    automatic runtime.
 *
 * Only `.tsx` files go through the (slower) transpile path; plain `.ts` still
 * uses Node's built-in type stripping.
 */
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

export async function resolve(specifier, context, nextResolve) {
  try {
    return await nextResolve(specifier, context);
  } catch (error) {
    if (specifier.startsWith('.') && !/\.[cm]?[jt]sx?$/i.test(specifier)) {
      try {
        return await nextResolve(`${specifier}.ts`, context);
      } catch {
        return await nextResolve(`${specifier}.tsx`, context);
      }
    }
    throw error;
  }
}

export async function load(url, context, nextLoad) {
  if (!url.endsWith('.tsx')) return nextLoad(url, context);

  const source = await readFile(fileURLToPath(url), 'utf8');
  const ts = (await import('typescript')).default;
  const transpiled = ts.transpileModule(source, {
    fileName: fileURLToPath(url),
    compilerOptions: {
      target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.ESNext,
      jsx: ts.JsxEmit.ReactJSX,
      esModuleInterop: true,
      verbatimModuleSyntax: false,
      sourceMap: false,
    },
  });
  return { format: 'module', shortCircuit: true, source: transpiled.outputText };
}
