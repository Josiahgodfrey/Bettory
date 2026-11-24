/**
 * The forward-test and system screens against the real API: the sealed record (verify the chain, replay a session),
 * and the settings screen.
 */
import { expect, test } from '@playwright/test'

import { API, sealedSession, toast, watchErrors } from './helpers'

test.describe.configure({ mode: 'serial' })

test('paper betting: the sealed record verifies and replays identically', async ({ page }) => {
  const errors = watchErrors(page)
  const s = await sealedSession(page)
  await page.goto('/paper')
  const sealed = await (await page.request.get(`${API}/paper/report`)).json()
  expect(sealed.frozen).toBe(3) // two listed games and the one without data
  await expect(page.getByText('Intact')).toBeVisible()
  await expect(page.getByText('Gate B — does BETTORY work?')).toBeVisible()

  await page.getByRole('button', { name: 'Verify now' }).click()
  expect(await toast(page)).toContain('Record chain verified: 3 sealed predictions')

  await page.getByRole('button', { name: 'Replay' }).click()
  await expect(page.locator('[data-sonner-toast]').filter({ hasText: 'identical to the sealed record' })).toBeVisible({ timeout: 45_000 })

  // the sealed listed predictions link to their matches
  await expect(page.locator(`a[href^="/matches/"][href$="session=${s.id}"]`)).toHaveCount(s.matches.length)
  expect(errors).toEqual([])
})

test('settings show the betting rules from the API', async ({ page }) => {
  await page.goto('/settings')
  await expect(page.getByText('TZS')).toBeVisible()
  await expect(page.getByText('recorded and flagged, never refused')).toBeVisible()
  await expect(page.getByText('to confirm').first()).toBeVisible()
  await expect(page.getByText('Team histories (ESPN)')).toBeVisible()
  await expect(page.getByText('Nightly ESPN fill')).toBeVisible()
})
