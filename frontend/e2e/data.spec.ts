/**
 * Data loading, team review, cross-checks and backtests against the real API (throwaway database, no data-API keys):
 * every backend feature of these screens is reached from the app.
 */
import { expect, test } from '@playwright/test'

import { API, toast, watchErrors } from './helpers'

test.describe.configure({ mode: 'serial' })

test('team reviews: link a name to an existing team, create a new club', async ({ page }) => {
  const errors = watchErrors(page)
  await page.goto('/data-loading')
  const rows = page.getByTestId('review-row')
  await expect(rows).toHaveCount(2)

  // "Team A Utd" means the existing Team A
  const a = rows.filter({ hasText: 'Team A Utd' })
  await a.getByRole('button', { name: /Link to an existing team/ }).click()
  await page.getByLabel('Search teams').fill('Team A')
  await page.getByRole('dialog').getByRole('button', { name: /^Team A/ }).first().click()
  expect(await toast(page)).toContain('“Team A Utd” is now Team A')
  await expect(rows).toHaveCount(1)

  // "Brand New FC" is new to the database
  await rows.first().getByRole('button', { name: /New team/ }).click()
  await page.getByRole('dialog').getByRole('button', { name: 'Create team' }).click()
  await expect(page.locator('[data-sonner-toast]').filter({ hasText: 'Team “Brand New FC” created' })).toBeVisible()
  await expect(page.getByText('Nothing to review.')).toBeVisible()

  const teams = await (await page.request.get(`${API}/teams`, { params: { q: 'Brand New' } })).json()
  expect(teams.map((t: { name: string }) => t.name)).toContain('Brand New FC')
  expect(await (await page.request.get(`${API}/reviews`)).json()).toEqual([])
  expect(errors).toEqual([])
})

test('data loading: the form reaches the API, which refuses API-Football without a key', async ({ page }) => {
  await page.goto('/data-loading')
  await page.getByRole('combobox', { name: 'Source' }).click()
  await page.getByRole('option', { name: /API-Football: one day/ }).click()
  await page.getByRole('button', { name: 'Start' }).click()
  await expect(page.getByRole('alertdialog')).toContainText('Use API-Football requests?')
  await page.getByRole('alertdialog').getByRole('button', { name: 'Start' }).click()
  expect(await toast(page)).toContain('API_FOOTBALL_KEY is not configured')

  // the competitions offered come from GET /jobs/competitions
  await page.getByRole('combobox', { name: 'Source' }).click()
  await page.getByRole('option', { name: /openfootball/ }).click()
  await page.getByRole('combobox', { name: 'Competition' }).click()
  await expect(page.getByRole('option').first()).toBeVisible()
})

test('data health: run a cross-check, read its details', async ({ page }) => {
  const errors = watchErrors(page)
  const r = await page.request.post(`${API}/data-health/crosschecks`, { data: { competition: 'TST1', season: '2025-26' } })
  expect(r.ok()).toBeTruthy()
  await page.goto('/data-health')
  const row = page.getByRole('row').filter({ hasText: 'TST1' }).filter({ hasText: '2025-26' })
  await row.getByRole('button', { name: 'Run again' }).click()
  expect(await toast(page)).toContain('TST1 2025-26: cross-check')
  await row.getByRole('button', { name: 'Details…' }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog).toContainText('Cross-check TST1 2025-26')
  await expect(dialog).toContainText(/No disagreement between the sources|Sources say/)
  expect(errors).toEqual([])
})

test('a stored backtest opens in full', async ({ page }) => {
  const list = await (await page.request.get(`${API}/data-health/backtests`)).json()
  test.skip(list.length === 0, 'no stored backtest in backend/backtests')
  const errors = watchErrors(page)
  await page.goto('/data-health')
  await page.getByRole('link', { name: list[0].name }).click()
  await expect(page.getByRole('heading', { level: 2, name: 'Backtest' })).toBeVisible()
  await expect(page.getByText('Gate B criteria')).toBeVisible()
  await expect(page.getByText('Betting at the minimum odds')).toBeVisible()
  expect(errors).toEqual([])
})

test('settings show the API status from /health', async ({ page }) => {
  await page.goto('/settings')
  await expect(page.getByText('API status')).toBeVisible()
  await expect(page.getByText(/^ok$/)).toBeVisible()
})
