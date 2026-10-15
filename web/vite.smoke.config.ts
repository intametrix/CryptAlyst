/**
 * Vite config used only to build `scripts/renderSmoke.tsx` into a Node-runnable ESM
 * file. It exists so the smoke test can use JSX without adding a bundler
 * dependency: the project's own Vite does the transform.
 *
 *   vite build --config vite.smoke.config.ts
 */
import { defineConfig } from 'vite';

export default defineConfig({
  // React and the chart library come from node_modules at run time, so the
  // emitted file is small and imports resolve the same way they would in a test.
  ssr: { noExternal: true },
  build: {
    ssr: 'scripts/renderSmoke.tsx',
    outDir: 'node_modules/.tmp/smoke',
    emptyOutDir: true,
    minify: false,
    sourcemap: false,
    target: 'node20',
    rollupOptions: {
      external: ['react', 'react-dom', 'react-dom/server', 'react/jsx-runtime', 'lightweight-charts'],
      output: { entryFileNames: 'smoke.mjs' },
    },
  },
});
