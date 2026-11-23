import { expect, type Page } from '@playwright/test'

export const API = 'http://127.0.0.1:8100'

/** Collect browser errors. Expected ones: the live API-Football quota answers 503 (no key in the e2e backend). */
export function watchErrors(page: Page): string[] {
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`))
  page.on('console', (m) => {
    if (m.type() !== 'error') return
    const t = m.text()
    if (/status of (503|409)/.test(t)) return
    errors.push(t)
  })
  return errors
}

export async function noHorizontalScroll(page: Page): Promise<void> {
  const wider = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
  expect(wider, 'page is wider than the screen').toBeLessThanOrEqual(1)
}

export type Entry = {
  match_id: string
  decision: string
  failed_checks: string[]
  p_under05: number | null
  p_draw: number | null
  fair_under05: number | null
  fair_draw: number | null
  min_odds_under05: number | null
  fixture: { home_team: string; away_team: string }
}

/** The sealed session of the e2e database, its listed matches (the Bets section) and all its analysed games. */
export async function sealedSession(page: Page): Promise<{ id: string; matches: { id: string; title: string; pU: number; pD: number }[]; all: Entry[]; decisions: Record<string, number> }> {
  const dash = await (await page.request.get(`${API}/dashboard`)).json()
  const id: string = dash.next_session.id
  const rep = await (await page.request.get(`${API}/sessions/${id}`)).json()
  const all = await (await page.request.get(`${API}/sessions/${id}/fixtures`)).json()
  return {
    id,
    matches: rep.entries.map((e: Entry) => ({
      id: e.match_id,
      title: `${e.fixture.home_team} v ${e.fixture.away_team}`,
      pU: e.p_under05 ?? NaN,
      pD: e.p_draw ?? NaN,
    })),
    all: all.entries,
    decisions: rep.decisions,
  }
}

/** The app's percentage format (lib/format.ts pct): one decimal. */
export const pctText = (p: number) => `${(p * 100).toFixed(1)}%`

export async function toast(page: Page): Promise<string> {
  const t = page.locator('[data-sonner-toast]').first()
  await expect(t).toBeVisible()
  return (await t.textContent()) ?? ''
}
