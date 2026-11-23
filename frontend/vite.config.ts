import path from 'node:path'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { loadEnv } from 'vite'
import { defineConfig } from 'vitest/config'

export default defineConfig(({ command, mode }) => {
  // A production build must know the API's address: without it the app would call 127.0.0.1 and fail in the
  // browser. Stop the build instead (Vercel: Settings -> Environment Variables, docs/DEPLOYMENT.md section 7).
  if (command === 'build' && mode === 'production') {
    const env = { ...loadEnv(mode, process.cwd(), 'VITE_'), ...process.env }
    const missing = ['VITE_API_URL', 'VITE_SUPABASE_URL', 'VITE_SUPABASE_PUBLISHABLE_KEY'].filter((k) => !env[k])
    if (missing.length) throw new Error(`production build: set ${missing.join(', ')} (see docs/DEPLOYMENT.md section 7)`)
  }
  return config
})

const config = {
  plugins: [react(), tailwindcss()],
  // a second dev server (the e2e one) must not share the dependency cache with `npm run dev`
  cacheDir: process.env.BETTORY_VITE_CACHE ?? 'node_modules/.vite',
  resolve: { alias: { '@': path.resolve(__dirname, './src') } },
  server: { port: 5173, strictPort: true },
  test: { environment: 'node', include: ['src/**/*.test.ts?(x)'], exclude: ['e2e/**', 'node_modules/**'] },
}
