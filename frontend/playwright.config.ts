/**
 * Whole-application tests: the real frontend (Vite, port 5190) against the real API (port 8100) on a throwaway
 * database seeded with the synthetic league and a just-sealed session (backend/tests/e2e/server.py).
 * Needs the local test PostgreSQL (TEST_DATABASE_URL, default 127.0.0.1:55432). Run: npm run e2e
 */
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { defineConfig, devices } from '@playwright/test'

const backend = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../backend')
const python = path.join(backend, process.platform === 'win32' ? 'venv/Scripts/python.exe' : 'venv/bin/python')
const API = 'http://127.0.0.1:8100'
const APP = 'http://127.0.0.1:5190'

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1, // one shared database: the flows run in order
  retries: 0,
  timeout: 60_000,
  expect: { timeout: 15_000 },
  reporter: [['list'], ['html', { open: 'never' }]],
  use: { baseURL: APP, trace: 'retain-on-failure', screenshot: 'only-on-failure' },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'], viewport: { width: 1366, height: 860 } } },
    { name: 'phone', use: { ...devices['Pixel 7'] }, testMatch: /pages\.spec\.ts/ },
  ],
  webServer: [
    {
      command: `"${python}" -m tests.e2e.server --port 8100`,
      cwd: backend,
      url: `${API}/health`,
      timeout: 180_000,
      reuseExistingServer: false,
      stdout: 'pipe',
      env: { TEST_DATABASE_URL: process.env.TEST_DATABASE_URL ?? 'postgresql://postgres:test@127.0.0.1:55432/postgres' },
    },
    {
      command: 'npx vite --host 127.0.0.1 --port 5190 --strictPort',
      url: APP,
      timeout: 120_000,
      reuseExistingServer: false,
      env: { VITE_API_URL: API, VITE_AUTH_BYPASS: 'true', BETTORY_VITE_CACHE: 'node_modules/.vite-e2e' },
    },
  ],
})
