import path from 'node:path'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // a second dev server (the e2e one) must not share the dependency cache with `npm run dev`
  cacheDir: process.env.BETTORY_VITE_CACHE ?? 'node_modules/.vite',
  resolve: { alias: { '@': path.resolve(__dirname, './src') } },
  server: { port: 5173, strictPort: true },
  test: { environment: 'node', include: ['src/**/*.test.ts?(x)'], exclude: ['e2e/**', 'node_modules/**'] },
})
