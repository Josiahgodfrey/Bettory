/**
 * The owner's betting flow with REAL writes on the throwaway database (params 1.6: probabilities only - the odds come
 * from the owner's betting app): Today → Bets section (probabilities, fair odds) → match → record the placed bet →
 * My bets → a second bet that breaks a rule → cancel → Performance. Bets are append-only, so each run uses a fresh
 * database (backend/tests/e2e/server.py).
 */
import { expect, test } from '@playwright/test'

import { API, pctText, sealedSession, toast, watchErrors } from './helpers'

test.describe.configure({ mode: 'serial' })

test('Today leads to the sealed session; the Bets section shows probabilities, never minimum odds', async ({ page }) => {
  const errors = watchErrors(page)
  const s = await sealedSession(page)
  expect(s.matches.length).toBeGreaterThan(0)
  await page.goto('/')
  await expect(page.getByText('Published').first()).toBeVisible()
  await page.getByRole('button', { name: 'Open' }).click()
  await expect(page).toHaveURL(new RegExp(`/sessions/${s.id}$`))
  await expect(page.getByText('Sealed at the cut-off')).toBeVisible()
  const cards = page.getByTestId('entry-card')
  await expect(cards).toHaveCount(s.matches.length)
  // the probabilities shown are the sealed ones from the API
  for (let i = 0; i < s.matches.length; i++) {
    const shown = cards.nth(i).getByTestId('probability')
    await expect(shown.first()).toHaveText(pctText(s.matches[i].pU))
    await expect(shown.nth(1)).toHaveText(pctText(s.matches[i].pD))
  }
  await expect(page.getByTestId('min-odds')).toHaveCount(0)
  await expect(page.getByRole('button', { name: 'Odds check' })).toHaveCount(0)
  await expect(page.getByText(/fair odds/).first()).toBeVisible()
  expect(errors).toEqual([])
})

test('record the placed bet, see it in My bets', async ({ page }) => {
  const errors = watchErrors(page)
  const s = await sealedSession(page)
  const m = s.matches[0]
  await page.goto(`/sessions/${s.id}`)
  await page.getByTestId('entry-card').first().getByText('Analysis').click()
  await expect(page).toHaveURL(new RegExp(`/matches/${m.id}`))
  await expect(page.getByTestId('probability').first()).toHaveText(pctText(m.pU))
  await page.getByRole('button', { name: 'Record a placed bet' }).click()
  await page.fill('#bet-odds', '1.45')
  await page.fill('#bet-stake', '50000')
  await page.fill('#bet-book', 'E2E Book')
  await page.getByRole('button', { name: 'Record bet' }).click()
  expect(await toast(page)).toContain('Bet recorded: Under 0.5')

  const bets = await (await page.request.get(`${API}/bets`)).json()
  expect(bets).toHaveLength(1)
  expect(bets[0]).toMatchObject({ match_id: m.id, market: 'UNDER05', stake: 50000, bookmaker: 'E2E Book', state: 'PENDING', follows_rules: true, currency: 'TZS' })

  await page.goto('/bets')
  const row = page.getByTestId('bet-card').first()
  await expect(row).toContainText(m.title)
  await expect(row).toContainText('Open')
  await expect(row).toContainText('50,000 TZS')
  expect(errors).toEqual([])
})

test('a bet that breaks a rule is recorded and flagged; a mistake is cancelled', async ({ page }) => {
  const errors = watchErrors(page)
  const s = await sealedSession(page)
  const m = s.matches[0]
  await page.goto(`/matches/${m.id}?session=${s.id}`)
  await page.getByRole('button', { name: 'Record a placed bet' }).click()
  await page.getByRole('combobox', { name: 'Market' }).click()
  await page.getByRole('option', { name: /Draw/ }).click()
  await page.fill('#bet-odds', '1.30')
  await page.fill('#bet-stake', '20000')
  await page.fill('#bet-book', 'E2E Book')
  await page.getByRole('button', { name: 'Record bet' }).click()
  expect(await toast(page)).toContain('breaks the rules: Both markets bet')

  await page.goto('/bets')
  const flagged = page.getByTestId('bet-card').filter({ hasText: 'Both markets bet' })
  await expect(flagged).toHaveCount(1)

  await flagged.getByRole('button', { name: 'Cancel bet…' }).click()
  await page.fill('textarea[name=reason]', 'e2e: recorded by mistake')
  await page.getByRole('alertdialog').getByRole('button', { name: 'Cancel bet' }).click()
  expect(await toast(page)).toContain('Bet cancelled')
  await expect(flagged).toContainText('Cancelled')
  await expect(flagged).toContainText('e2e: recorded by mistake')

  // append-only: the cancelled bet is still there, excluded from performance
  const perf = await (await page.request.get(`${API}/performance`)).json()
  expect(perf.cancelled).toBe(1)
  expect(perf.bets).toBe(1)
  await page.goto('/performance')
  await expect(page.getByText(/1 cancelled/)).toBeVisible()
  expect(errors).toEqual([])
})

test('the API refuses an impossible bet and the form says why', async ({ page }) => {
  const s = await sealedSession(page)
  await page.goto(`/matches/${s.matches[0].id}?session=${s.id}`)
  await page.getByRole('button', { name: 'Record a placed bet' }).click()
  await page.fill('#bet-odds', '1.50')
  await page.fill('#bet-stake', '999999999999') // above max_stake: refused before sending
  await page.fill('#bet-book', 'E2E Book')
  await page.getByRole('button', { name: 'Record bet' }).click()
  await expect(page.getByRole('dialog')).toContainText('looks like a typing error')
  const bets = await (await page.request.get(`${API}/bets`)).json()
  expect(bets).toHaveLength(2) // nothing new recorded
})
