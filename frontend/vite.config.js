import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8123',
      '/healthz': 'http://127.0.0.1:8123',
    },
  },
  // Explicit, not relying on Vite's default — a source map in the prod
  // build would ship readable original source (including comments) to
  // anyone who opens devtools against the live app.
  build: {
    sourcemap: false,
    rollupOptions: {
      output: {
        // Splits the framework and the charting library (the two heaviest,
        // most stable dependencies) out of the app code, which was all one
        // ~1MB chunk. These change far less often than app code between
        // deploys, so browsers keep serving them from cache across most
        // releases instead of re-downloading everything on every deploy.
        manualChunks: {
          vendor: ['react', 'react-dom', 'react-router-dom'],
          charts: ['apexcharts', 'react-apexcharts'],
        },
      },
    },
  },
});
