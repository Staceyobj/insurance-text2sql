import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Dev proxy (SPEC-FRONTEND §3): API paths stay same-origin — zero CORS anywhere.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/v1': 'http://127.0.0.1:8000',
      '/healthz': 'http://127.0.0.1:8000',
    },
  },
})
