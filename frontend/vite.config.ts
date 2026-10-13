import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: { '/api': { target: process.env.DTL_API_TARGET ?? 'http://127.0.0.1:8717' } },
  },
  preview: { host: '127.0.0.1', port: 4173, strictPort: true },
})
