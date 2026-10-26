import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Dev: the UI proxies /api to the FastAPI gateway (http://127.0.0.1:8080).
// If the backend is down the proxy answers with an error and the UI falls back to
// the static fixtures in public/fixtures (see src/api.ts).
// Prod: `npm run build` writes ui/dist, which FastAPI serves with
// StaticFiles(directory="ui/dist", html=True).
const BACKEND = process.env.CUSTOS_API ?? 'http://127.0.0.1:8080';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: false,
    proxy: {
      '/api': {
        target: BACKEND,
        changeOrigin: true,
        // Long-lived SSE stream (/api/live/stream) must not be buffered or timed out.
        timeout: 0,
        proxyTimeout: 0,
      },
    },
  },
  preview: {
    port: 4173,
    proxy: {
      '/api': { target: BACKEND, changeOrigin: true },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    chunkSizeWarningLimit: 1500,
  },
});
