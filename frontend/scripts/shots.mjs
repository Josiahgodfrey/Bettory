// Screenshots of the running dev app (npm run dev + API) for visual review: node scripts/shots.mjs <outDir> [paths…]
import { chromium } from '@playwright/test'

const [, , out = 'shots', ...paths] = process.argv
const base = process.env.APP_URL ?? 'http://localhost:5173'
// paths without a leading slash ("home" → "/"): Git Bash rewrites a bare "/" into a Windows path
const targets = (paths.length ? paths : ['home']).map((p) => (p === 'home' ? '/' : `/${p}`))
const sizes = [
  { name: 'desktop', width: 1366, height: 860 },
  { name: 'phone', width: 390, height: 844 },
]

const browser = await chromium.launch()
for (const theme of ['light', 'dark']) {
  for (const s of sizes) {
    const page = await browser.newPage({ viewport: { width: s.width, height: s.height }, colorScheme: theme })
    const errors = []
    page.on('pageerror', (e) => errors.push(String(e)))
    page.on('console', (m) => m.type() === 'error' && errors.push(m.text()))
    for (const p of targets) {
      await page.goto(base + p, { waitUntil: 'networkidle' })
      await page.waitForTimeout(400)
      const file = `${out}/${p.replace(/\W+/g, '_') || 'root'}-${s.name}-${theme}.png`
      await page.screenshot({ path: file, fullPage: true })
      console.log(file)
    }
    if (errors.length) console.log(`ERRORS (${s.name}/${theme}):`, errors.join('\n'))
    await page.close()
  }
}
await browser.close()
