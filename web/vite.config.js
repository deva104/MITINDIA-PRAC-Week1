import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// The FastAPI service sends no CORS headers, so a browser on http://localhost:5173 cannot read a
// response from http://localhost:8000 directly. Requests therefore go to /api on this origin and
// Vite forwards them to the backend, which needs no changes. To call the backend directly instead,
// add CORSMiddleware to FastAPI and run with VITE_API_BASE=http://localhost:8000.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})
