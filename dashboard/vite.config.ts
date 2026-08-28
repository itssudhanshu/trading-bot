import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  base: './',
  plugins: [react()],
  server: {
    proxy: {
      '/snapshot': 'http://127.0.0.1:8000',
      '/etf_trend': 'http://127.0.0.1:8000',
      '/company': 'http://127.0.0.1:8000',
      '/watchlist': 'http://127.0.0.1:8000',
      '/sector': 'http://127.0.0.1:8000',
      '/journal': 'http://127.0.0.1:8000',
    },
  },
  preview: {
    proxy: {
      '/snapshot': 'http://127.0.0.1:8000',
      '/etf_trend': 'http://127.0.0.1:8000',
      '/company': 'http://127.0.0.1:8000',
      '/watchlist': 'http://127.0.0.1:8000',
      '/sector': 'http://127.0.0.1:8000',
      '/journal': 'http://127.0.0.1:8000',
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/setupTests.ts'],
  },
})
