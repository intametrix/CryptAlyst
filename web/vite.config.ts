import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

/**
 * `http-proxy-3`'s `ProxyServer` extends `EventEmitter` imported from
 * `node:events`. This project intentionally has no `@types/node` (API.md limits
 * the dependency set), so that base class does not resolve here and TypeScript
 * sees a `ProxyServer` without `.on`. At runtime it is a real Node EventEmitter.
 * This structural view re-exposes exactly the two events we subscribe to.
 */
interface ProxyReqLike {
  setHeader(name: string, value: string): void;
}
interface ProxyServerLike {
  on(event: 'proxyReq', listener: (proxyReq: ProxyReqLike) => void): void;
}

// Dev server proxies /api -> the Go backend on 127.0.0.1:8787, so
// `EventSource('/api/events')` works from :5173 with no CORS involvement.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8787',
        changeOrigin: true,
        ws: false,
        // Ask upstream for an identity (uncompressed) encoding: a gzipped SSE
        // stream can sit in a compression buffer and delay every alert.
        headers: {
          'accept-encoding': 'identity',
          'cache-control': 'no-cache',
          'x-accel-buffering': 'no',
        },
        // Belt and braces: stamp the same headers onto every proxied request.
        // (Vite already installs its own `error` listener on the proxy, so a
        // dead backend surfaces as a 5xx -> EventSource `onerror` -> red pill.)
        configure: (proxy) => {
          const server = proxy as unknown as ProxyServerLike;
          server.on('proxyReq', (proxyReq) => {
            proxyReq.setHeader('Accept-Encoding', 'identity');
            proxyReq.setHeader('Cache-Control', 'no-cache');
            proxyReq.setHeader('X-Accel-Buffering', 'no');
          });
        },
      },
    },
  },
  build: {
    // The Go binary embeds and serves this directory.
    outDir: '../backend/web/dist',
    emptyOutDir: true,
    sourcemap: false,
  },
});
