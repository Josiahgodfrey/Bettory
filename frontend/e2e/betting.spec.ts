/**
 * The owner's betting flow with REAL writes on the throwaway database: Today → Bets section → match → odds check
 * (stored prices, decision computed by the API) → record the bet → My bets → a second bet that breaks a rule →
 * cancel → Performance. Bets are append-only, so each run uses a fresh database (backend/tests/e2e/server.py).
 */
import { expect, test } from '@playwright/test'

import { API, sealedSession, toast, watchErrors } from './helpers'

test.describe.configure({ mode: 'serial' })

/** Both sides of "Goal in minutes 1-15" with the given bookmaker margin, the No side (= Under 0.5) at `no`. */
function under05Prices(no: number, margin: number): { yes: string; no: string } {
  const yes = 1 / (1 + margin - 1 / no)
  return { yes: yes.toFixed(2), no: no.toFixed(2) }
}

test('Today leads to the sealed session and its listed matches', async ({ page }) => {
  const errors = watchErrors(page)
  const s = await sealedSession(page)
  await page.goto('/')
  await expect(page.getByText('Published').first()).toBeVisible()
  await page.getByRole('button', { name: 'Open' }).click()
  await expect(page).toHaveURL(new RegExp(`/sessions/${s.id}$`))
  await expect(page.getByText('Sealed at the cut-off')).toBeVisible()
  const cards = page.getByTestId('entry-card')
  await expect(cards).toHaveCount(2)
  // the minimum odds shown are the sealed ones from the API
  await expect(cards.first().getByTestId('min-odds').first()).toHaveText(s.matches[0].minU.toFixed(2))
  expect(errors).toEqual([])
})

test('odds check, record the bet, see it in My bets', async ({ page }) => {
  const errors = watchErrors(page)
  const s = await sealedSession(page)
  const m = s.matches[0]
  await page.goto(`/sessions/${s.id}`)
  await page.getByTestId('entry-card').first().getByRole('button', { name: 'Odds check' }).click()
  await expect(page).toHaveURL(new RegExp(`/matches/${m.id}`))

  const check = async (prices: { yes: string; no: string }) => {
    await page.fill('#q-book', 'E2E Book')
    await page.fill('#q-u_yes', prices.yes)
    await page.fill('#q-u_no', prices.no)
    await page.getByRole('button', { name: 'Check odds' }).click()
  }
  const card = page.getByTestId('decision-card')
  const title = card.locator('[data-slot=card-title]')

  // too good to be true: a high price with a 5 % margin implies far less than BETTORY's probability -> refused
  await check(under05Prices(m.minU + 0.08, 0.05))
  await expect(title).toHaveText(/No bet/)
  await expect(card).toContainText('BETTORY vs bookmaker')

  // a realistic price just above the minimum, 2 % margin -> the API says BET on Under 0.5
  const p = under05Prices(m.minU + 0.02, 0.02)
  await check(p)
  await expect(title).toHaveText(/^\s*Bet$/)
  await expect(card).toContainText('against the sealed report')
  await expect(card).toContainText('Under 0.5 at 15′')
  await expect(card).toContainText(/2 prices saved/)

  // the stored prices appear in the match's odds, marked as entered by hand
  await page.getByRole('tab', { name: 'Odds' }).click()
  await expect(page.getByText('E2E Book').first()).toBeVisible()

  await card.getByRole('button', { name: 'Record this bet' }).click()
  expect(Number(await page.locator('#bet-odds').inputValue())).toBeCloseTo(Number(p.no), 2) // prefilled from the card
  await page.fill('#bet-stake', '50000')
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

test('a bet below the minimum is recorded and flagged; a mistake is cancelled', async ({ page }) => {
  const errors = watchErrors(page)
  const s = await sealedSession(page)
  const m = s.matches[1]
  await page.goto(`/matches/${m.id}?session=${s.id}`)
  await page.getByRole('button', { name: 'Record a placed bet' }).click()
  const below = (m.minU - 0.05).toFixed(2)
  await page.fill('#bet-odds', below)
  await expect(page.getByRole('dialog').getByText(/Below the minimum odds/)).toBeVisible()
  await page.fill('#bet-stake', '20000')
  await page.fill('#bet-book', 'E2E Book')
  await page.getByRole('button', { name: 'Record bet' }).click()
  expect(await toast(page)).toContain('breaks the rules: Below minimum odds')

  await page.goto('/bets')
  const flagged = page.getByTestId('bet-card').filter({ hasText: m.title })
  await expect(flagged).toContainText('Below minimum odds')

  await flagged.getByRole('button', { name: 'Cancel bet…' }).click()
  await page.fill('textarea[name=reason]', 'e2e: recorded by mistake')
  await page.getByRole('alertdialog').getByRole('button', { name: 'Cancel bet' }).click()
  expect(await toast(page)).toContain('Bet cancelled')
  await expect(flagged).toContainText('Cancelled')
  await expect(flagged).toContainText('e2e: recorded by mistake')

  await page.getByRole('tab', { name: /Cancelled/ }).click()
  await expect(page.getByTestId('bet-card')).toHaveCount(1)

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
