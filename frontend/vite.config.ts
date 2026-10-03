import { fileURLToPath } from 'node:url'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
      // Shared contract fixtures; only loaded by mock mode.
      '@contracts': fileURLToPath(new URL('../docs/contracts', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    strictPort: true,
    fs: { allow: ['..'] },
    proxy: {
      // Resolve backend (Harry). Same-origin cookies + CSRF; see docs/contracts.md.
      '/api': { target: 'http://localhost:8080', changeOrigin: false },
    },
  },
})
