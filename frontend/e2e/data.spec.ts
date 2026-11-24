/**
 * Data health against the real API (throwaway database, no data-API keys): the seeded league's coverage is shown,
 * and the operational endpoints removed in the lean-free pass are really gone.
 */
import { expect, test } from '@playwright/test'

import { API, noHorizontalScroll, watchErrors } from './helpers'

test('data health: coverage and recent loads', async ({ page }) => {
  const errors = watchErrors(page)
  const health = await (await page.request.get(`${API}/data-health`)).json()
  const tst = health.coverage.find((c: { competition: string; season: string }) => c.competition === 'TST1' && c.season === '2025-26')
  expect(tst, 'the seeded league-season is in the coverage').toBeTruthy()
  expect(tst.finished).toBeGreaterThan(0)
  expect(tst.usable_15).toBeGreaterThan(0)

  await page.goto('/data-health')
  await expect(page.getByRole('heading', { level: 2, name: 'Data health' })).toBeVisible()
  const row = page.getByRole('row').filter({ hasText: 'TST1' }).filter({ hasText: '2025-26' })
  await expect(row).toBeVisible()
  await expect(row).toContainText(String(tst.usable_15))
  await expect(page.getByText('Recent loads')).toBeVisible()
  await noHorizontalScroll(page)
  expect(errors).toEqual([])
})

test('the removed operational endpoints are gone', async ({ page }) => {
  for (const path of ['/jobs/competitions', '/reviews', '/data-health/crosschecks', '/data-health/quota',
    '/data-health/backtests']) {
    expect((await page.request.get(`${API}${path}`)).status(), path).toBe(404)
  }
})
