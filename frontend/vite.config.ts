import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // Proxy /api to the backend so the app is SAME-ORIGIN.
    //
    // This is not a convenience — it is what makes cookie auth possible at all.
    // The page is served from localhost:5173 and the API from 127.0.0.1:8000,
    // which are different sites, so a SameSite=Lax session cookie is never
    // attached to those requests no matter what `credentials` is set to. Setting
    // SameSite=None instead would require Secure, which requires HTTPS.
    //
    // It also matches the production topology: Caddy fronting both (DECISIONS.md).
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: false,
      },
    },
  },
})
