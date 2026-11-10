import fs from 'node:fs'
import path from 'node:path'
import zlib from 'node:zlib'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig, type Plugin } from 'vite'

// Precompress text assets (.br + .gz) at build time. The gateway and
// meridian-control send them when the browser accepts the encoding, so the
// console ships ~70% fewer bytes without compressing API/SSE responses.
function precompress(): Plugin {
  let outDir = ''
  return {
    name: 'meridian-precompress',
    apply: 'build',
    configResolved(c) {
      outDir = path.resolve(c.root, c.build.outDir)
    },
    closeBundle() {
      const walk = (dir: string): string[] =>
        fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
          const p = path.join(dir, e.name)
          return e.isDirectory() ? walk(p) : [p]
        })
      for (const file of walk(outDir)) {
        if (!/\.(js|css|svg|json|txt|webmanifest)$/.test(file)) continue
        const raw = fs.readFileSync(file)
        if (raw.length < 1024) continue
        fs.writeFileSync(`${file}.br`, zlib.brotliCompressSync(raw, { params: { [zlib.constants.BROTLI_PARAM_QUALITY]: 11 } }))
        fs.writeFileSync(`${file}.gz`, zlib.gzipSync(raw, { level: 9 }))
      }
    },
  }
}

// The console is served by the gateway at /ui (see meridian/api/main.py).
// `npm run build` writes straight into the Python package so the gateway
// (and the Docker image) pick it up without a copy step.
const GATEWAY = process.env.MERIDIAN_GATEWAY_URL ?? 'http://127.0.0.1:8080'
// Set to develop the Meridian Cloud console against `meridian-control` in
// cloud mode (it serves /api and /auth; the console detects it at boot).
const CLOUD = process.env.MERIDIAN_CLOUD_URL

export default defineConfig({
  base: '/ui/',
  plugins: [react(), tailwindcss(), precompress()],
  resolve: {
    alias: { '@': path.resolve(import.meta.dirname, './src') },
  },
  build: {
    outDir: '../meridian/ui/dist',
    emptyOutDir: true,
    chunkSizeWarningLimit: 900,
    rollupOptions: {
      // The console (index.html, served at /ui) and the public site
      // (site.html: landing page + install configurator, served at / and
      // /install by Meridian Cloud).
      input: {
        main: path.resolve(import.meta.dirname, 'index.html'),
        site: path.resolve(import.meta.dirname, 'site.html'),
      },
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
