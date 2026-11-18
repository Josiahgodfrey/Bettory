/**
 * The forward-test and system screens against the real API: the sealed record (verify the chain, replay a session),
 * a real recorder cycle, Telegram off, calendar, data health, settings.
 */
import { expect, test } from '@playwright/test'

import { API, sealedSession, toast, watchErrors } from './helpers'

test.describe.configure({ mode: 'serial' })

test('paper betting: the sealed record verifies and replays identically', async ({ page }) => {
  const errors = watchErrors(page)
  const s = await sealedSession(page)
  await page.goto('/paper')
  const sealed = await (await page.request.get(`${API}/paper/report`)).json()
  expect(sealed.frozen).toBe(2)
  await expect(page.getByText('Intact')).toBeVisible()
  await expect(page.getByText('Gate B — does BETTORY work?')).toBeVisible()

  await page.getByRole('button', { name: 'Verify now' }).click()
  expect(await toast(page)).toContain('Record chain verified: 2 sealed predictions')

  await page.getByRole('button', { name: 'Replay' }).click()
  await expect(page.locator('[data-sonner-toast]').filter({ hasText: 'identical to the sealed record' })).toBeVisible({ timeout: 45_000 })

  // the sealed listed predictions link to their matches
  await expect(page.locator(`a[href^="/matches/"][href$="session=${s.id}"]`)).toHaveCount(2)
  expect(errors).toEqual([])
})

test('recorder: a real cycle runs from the app and reports back', async ({ page }) => {
  const errors = watchErrors(page)
  await page.goto('/recorder')
  await expect(page.getByText('What the next cycle would do')).toBeVisible()
  await page.getByRole('button', { name: 'Run one cycle now' }).click()
  await expect(page.getByRole('alertdialog')).toContainText('Sealed records cannot be undone')
  await page.getByRole('button', { name: 'Run cycle' }).click()
  expect(await toast(page)).toContain('Recorder cycle started')
  await expect(page.getByText(/Last cycle started here/)).toBeVisible({ timeout: 30_000 })
  await expect(page.getByText('done').or(page.getByText(/error\(s\)/))).toBeVisible({ timeout: 30_000 })

  const st = await (await page.request.get(`${API}/paper/status`)).json()
  expect(st.manual_cycle.running).toBe(false)
  expect(st.telegram.configured).toBe(false)
  expect(errors).toEqual([])
})

test('notifications: Telegram is off in the test backend and the app says so', async ({ page }) => {
  await page.goto('/notifications')
  await expect(page.getByText('Telegram is off for this backend')).toBeVisible()
  await expect(page.getByRole('button', { name: /Send a test message/ })).toBeDisabled()
  const r = await page.request.post(`${API}/notifications/test`)
  expect(r.status()).toBe(409) // nothing can be sent from a test run
})

test('calendar shows the sealed fixtures on their day', async ({ page }) => {
  const errors = watchErrors(page)
  const s = await sealedSession(page)
  const cal = await (await page.request.get(`${API}/fixtures`, { params: { date: new Date().toISOString().slice(0, 10) } })).json()
  const day = cal.fixtures.some((f: { session_id: string }) => f.session_id === s.id) ? cal.date : null
  test.skip(!day, 'the sealed fixtures fall on the next EAT day')
  await page.goto(`/calendar?date=${day}`)
  for (const m of s.matches) await expect(page.getByText(m.title)).toBeVisible()
  expect(errors).toEqual([])
})

test('settings show the betting rules from the API', async ({ page }) => {
  await page.goto('/settings')
  await expect(page.getByText('TZS')).toBeVisible()
  await expect(page.getByText('recorded and flagged, never refused')).toBeVisible()
  await expect(page.getByText('to confirm').first()).toBeVisible()
})
