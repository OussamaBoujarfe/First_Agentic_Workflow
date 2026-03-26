import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // Forward /verify and /health calls to the FastAPI backend
      '/verify': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
    },
  },
})
