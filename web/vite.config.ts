import path from 'node:path'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The console is served by the gateway at /ui (see meridian/api/main.py).
// `npm run build` writes straight into the Python package so the gateway
// (and the Docker image) pick it up without a copy step.
const GATEWAY = process.env.MERIDIAN_GATEWAY_URL ?? 'http://127.0.0.1:8080'
// Set to develop the Meridian Cloud console against `meridian-control` in
// cloud mode (it serves /api and /auth; the console detects it at boot).
const CLOUD = process.env.MERIDIAN_CLOUD_URL

export default defineConfig({
  base: '/ui/',
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': path.resolve(import.meta.dirname, './src') },
  },
  build: {
    outDir: '../meridian/ui/dist',
    emptyOutDir: true,
    chunkSizeWarningLimit: 900,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes('node_modules')) return undefined
          if (id.includes('recharts') || id.includes('d3-') || id.includes('victory')) return 'charts'
          if (id.includes('@tanstack')) return 'tanstack'
          if (id.includes('radix-ui') || id.includes('@radix-ui')) return 'radix'
          return 'vendor'
        },
      },
    },
  },
  server: {
    port: 5173,
    // Dev: same-origin API calls are proxied to a running gateway.
    proxy: {
      '/meridian': GATEWAY,
      '/v1': GATEWAY,
      '/metrics': GATEWAY,
      ...(CLOUD ? { '/api': CLOUD, '/auth': CLOUD } : {}),
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    css: false,
  },
})
