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

/** The sealed session of the e2e database and its two listed matches (read from the API, as the app does). */
export async function sealedSession(page: Page): Promise<{ id: string; matches: { id: string; title: string; minU: number; minD: number }[] }> {
  const dash = await (await page.request.get(`${API}/dashboard`)).json()
  const id: string = dash.next_session.id
  const rep = await (await page.request.get(`${API}/sessions/${id}`)).json()
  return {
    id,
    matches: rep.entries.map((e: { match_id: string; fixture: { home_team: string; away_team: string }; min_odds_under05: number; min_odds_draw: number }) => ({
      id: e.match_id,
      title: `${e.fixture.home_team} v ${e.fixture.away_team}`,
      minU: e.min_odds_under05,
      minD: e.min_odds_draw,
    })),
  }
}

export async function toast(page: Page): Promise<string> {
  const t = page.locator('[data-sonner-toast]').first()
  await expect(t).toBeVisible()
  return (await t.textContent()) ?? ''
}
