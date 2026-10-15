/**
 * Builds the headless-browser reactivity harness in `.rt-harness/`.
 *
 * The harness mounts the real components, drives a real pointer drag on the
 * overlay grip, and asserts that the chart canvas, the stat cards and the table
 * columns all reflowed. It is deliberately outside `src/` so it can never end up
 * in the app bundle, and it is git-ignored.
 *
 *   vite build --config vite.rt.config.ts
 */
import { defineConfig } from 'vite';

export default defineConfig({
  root: '.rt-harness',
  build: {
    outDir: '../node_modules/.tmp/rt',
    emptyOutDir: true,
    minify: false,
    target: 'es2022',
    // Extract the stylesheet instead of injecting it through JS, so the harness
    // page can link it directly and the DOM is styled before the first check.
    cssCodeSplit: false,
    rollupOptions: {
      input: process.env.RT_ENTRY ?? 'main.tsx',
      output: {
        entryFileNames: 'rt.js',
        assetFileNames: 'style.css',
        format: 'es',
      },
    },
  },
});
