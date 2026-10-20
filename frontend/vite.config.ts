import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: {
      // Preserve browser Host so backend same-origin checks compare it with Origin.
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: false },
      '/health': { target: 'http://127.0.0.1:8000', changeOrigin: false },
    },
  },
  build: { chunkSizeWarningLimit: 1100 },
  test: { environment: 'node', include: ['tests/**/*.test.ts'], restoreMocks: true },
})
