// The dev server for the headless dialer check ONLY (`backend/tests/browser_dialer.py`).
//
// Identical to `vite.config.ts` except that `src/lib/sipUser.ts` — the one place a SIP.js
// user agent is built — resolves to `e2e/sipUserMock.ts`. Nothing can register or call.
import { fileURLToPath } from 'node:url'
import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'

const MOCK = fileURLToPath(new URL('./sipUserMock.ts', import.meta.url))
const ROOT = fileURLToPath(new URL('..', import.meta.url))

function fakeSip(): Plugin {
  return {
    name: 'ghl-fake-sip-user',
    enforce: 'pre',
    resolveId(source, importer) {
      if (importer && /[\\/]src[\\/]lib[\\/]softphone\.ts$/.test(importer)
          && source === './sipUser') {
        return MOCK
      }
      return null
    },
  }
}

export default defineConfig({
  root: ROOT,
  plugins: [fakeSip(), react()],
  server: {
    port: Number(process.env.GHL_VITE_PORT || 5840),
    strictPort: true,
    proxy: {
      '/api': { target: `http://127.0.0.1:${process.env.GHL_API_PORT || 8740}`, changeOrigin: false },
    },
  },
})
