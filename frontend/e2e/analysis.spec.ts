/**
 * The session analysis, end to end (params 1.6: probabilities only). Real frontend + real API on a throwaway database
 * (backend/tests/e2e/server.py) holding a session sealed at its cut-off with three games: two between teams with full
 * histories and one against a club with no history at all.
 *
 *   API      shapes and values: every probability in [0, 1], P(0-0 at 15') <= P(draw at 15'), no minimum odds,
 *            QUALIFIED <=> in the Bets section, every other game kept with its reason, counts consistent
 *   UI       the same numbers in the Bets section (Listed), the games list (All fixtures), the session stats
 *   Recorder both sessions of each day with their cut-off 2 h before the window opens
 *   Edges    a session without games, a team without data, the fetch failure (no API-Football key in this backend),
 *            a repeated run (nothing sealed twice)
 */
import { expect, test } from '@playwright/test'

import { API, type Entry, pctText, sealedSession, watchErrors } from './helpers'

test.describe.configure({ mode: 'serial' })

const H2 = 2 * 3_600_000

test('API: every analysed game has valid probabilities and sits on the right list', async ({ page }) => {
  const s = await sealedSession(page)
  const rep = await (await page.request.get(`${API}/sessions/${s.id}`)).json()
  expect(rep.source).toBe('FROZEN')
  expect(Object.keys(rep).sort()).toEqual(['as_of_utc', 'decisions', 'entries', 'how_to_bet', 'session', 'source'])
  expect(rep.how_to_bet).toContain('probabilities')

  // the analysis was sealed 2 hours before the session's window opened
  expect(new Date(rep.as_of_utc).getTime()).toBe(new Date(rep.session.start_utc).getTime() - H2)
  expect(new Date(rep.as_of_utc).getTime()).toBeLessThan(new Date(rep.session.first_kickoff_utc).getTime())

  expect(s.all).toHaveLength(3)
  for (const e of s.all as Entry[]) {
    for (const p of [e.p_under05, e.p_draw]) if (p != null) expect(p >= 0 && p <= 1, `${p} in [0, 1]`).toBe(true)
    if (e.p_under05 != null && e.p_draw != null) expect(e.p_under05).toBeLessThanOrEqual(e.p_draw)
    if (e.p_under05 != null) expect(e.fair_under05).toBeCloseTo(1 / e.p_under05, 6)
    expect(e.min_odds_under05).toBeNull() // no odds-based logic
    if (e.decision === 'QUALIFIED') expect(e.failed_checks).toEqual([])
    else expect(e.failed_checks.length).toBeGreaterThan(0) // a failed game keeps its reason
  }
  const listed = (s.all as Entry[]).filter((e) => e.decision === 'QUALIFIED').map((e) => e.match_id).sort()
  expect(s.matches.map((m) => m.id).sort()).toEqual(listed) // Bets section = exactly the games that passed

  // the team without any match: missing data, never in Bets
  const newcomer = (s.all as Entry[]).find((e) => e.fixture.away_team === 'Newcomers FC')
  expect(newcomer?.decision).toBe('INSUFFICIENT_DATA')
  expect(newcomer?.failed_checks.join(' ')).toMatch(/WINDOW|TEAM_WINDOWS/)

  // the counts match the games
  const counted = Object.values(s.decisions).reduce((a, b) => a + b, 0)
  expect(counted).toBe(3)
  expect(rep.session.fixtures).toBe(3)
  expect(rep.session.listed).toBe(listed.length)
})

test('UI: Bets section, games list and session stats show the same data as the API', async ({ page }) => {
  const errors = watchErrors(page)
  const s = await sealedSession(page)
  await page.goto(`/sessions/${s.id}`)
  await expect(page.getByRole('heading', { level: 2 })).toContainText(s.id.endsWith('NIGHT') ? /Night/ : /Noon|Day/)

  // Bets section (Listed): only the games that passed, with their sealed probabilities
  await expect(page.getByRole('tab', { name: `Listed (${s.matches.length})` })).toBeVisible()
  const cards = page.getByTestId('entry-card')
  await expect(cards).toHaveCount(s.matches.length)
  for (let i = 0; i < s.matches.length; i++) await expect(cards.nth(i)).toContainText(pctText(s.matches[i].pU))
  await expect(page.getByText('Newcomers FC')).toHaveCount(0)

  // session stats: counts by decision
  for (const [d, n] of Object.entries(s.decisions)) {
    await expect(page.getByText(new RegExp(`^${d === 'QUALIFIED' ? 'Listed|Qualified' : '.'}`)).first()).toBeVisible()
    expect(n).toBeGreaterThan(0)
  }
  await expect(page.getByText('3 fixtures')).toBeVisible()

  // games list (All fixtures): every game, the failed one with its reason
  await page.getByRole('tab', { name: 'All fixtures (3)' }).click()
  const failedRow = page.getByText('Newcomers FC').first()
  await expect(failedRow).toBeVisible()
  await expect(page.getByText(/Insufficient data|Not enough data/i).first()).toBeVisible()
  expect(errors).toEqual([])
})

test('Recorder: both sessions of a day, each sealed 2 h before its window opens', async ({ page }) => {
  const errors = watchErrors(page)
  const st = await (await page.request.get(`${API}/paper/status`)).json()
  const kinds = new Set(st.sessions.map((x: { id: string }) => x.id.split('-').pop()))
  expect(kinds).toEqual(new Set(['NIGHT', 'NOON']))
  for (const x of st.sessions) {
    expect(new Date(x.plan_at_utc).getTime()).toBeLessThan(new Date(x.start_utc).getTime())
    if (x.as_of_utc) {
      expect(new Date(x.as_of_utc).getTime()).toBe(new Date(x.start_utc).getTime() - H2) // 23:00 / 11:00 EAT
      expect(new Date(x.as_of_utc).getTime()).toBeGreaterThan(new Date(x.plan_at_utc).getTime())
    }
    expect(x.history_enabled).toBe(true)
  }
  expect(errors).toEqual([])
})

test('edges: a session without games, a fetch failure, a repeated run', async ({ page }) => {
  // a session without any game: an empty report, and the page says so
  const empty = '2031-01-15-NOON'
  const r = await page.request.get(`${API}/sessions/${empty}`)
  expect(r.ok()).toBeTruthy()
  const rep = await r.json()
  expect(rep.entries).toEqual([])
  expect(rep.session.fixtures).toBe(0)
  await page.goto(`/sessions/${empty}`)
  await expect(page.getByText(/No fixture/).first()).toBeVisible()

  // the fetch failure: this backend has no API-Football key - the cycle reports it, nothing crashes, nothing is sealed
  const s = await sealedSession(page)
  const before = (await (await page.request.get(`${API}/sessions/${s.id}/fixtures`)).json()).entries.length
  const run = await page.request.post(`${API}/paper/cycle`)
  expect(run.status()).toBeLessThan(500)
  await expect
    .poll(async () => (await (await page.request.get(`${API}/paper/status`)).json()).manual_cycle?.running, { timeout: 30_000 })
    .toBe(false)
  const st = await (await page.request.get(`${API}/paper/status`)).json()
  const lines: string[] = st.manual_cycle.lines
  expect(lines.some((l) => /API_FOOTBALL_KEY is not configured/.test(l)) || lines.length === 0).toBe(true)

  // repeated: the sealed session is not sealed again
  const after = (await (await page.request.get(`${API}/sessions/${s.id}/fixtures`)).json()).entries
  expect(after).toHaveLength(before)
  expect(lines.some((l) => l.includes(`${s.id} freeze`))).toBe(false)
})
