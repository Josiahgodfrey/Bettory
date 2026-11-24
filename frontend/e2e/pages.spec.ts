/** Every screen loads from the real API without a browser error and fits the screen (desktop and phone). */
import { expect, test } from '@playwright/test'

import { noHorizontalScroll, sealedSession, watchErrors } from './helpers'

const PAGES: [string, string | RegExp][] = [
  ['/', 'Today'],
  ['/bets', 'My bets'],
  ['/performance', 'Performance'],
  ['/paper', 'Paper betting'],
  ['/data-health', 'Data health'],
  ['/settings', 'Settings'],
]

for (const [path, title] of PAGES) {
  test(`page ${path}`, async ({ page }) => {
    const errors = watchErrors(page)
    await page.goto(path)
    await expect(page.getByRole('heading', { level: 2, name: title })).toBeVisible()
    await page.waitForLoadState('networkidle')
    await noHorizontalScroll(page)
    expect(errors).toEqual([])
  })
}

test('bets section and match analysis', async ({ page }) => {
  const errors = watchErrors(page)
  const s = await sealedSession(page)
  await page.goto('/sessions')
  await expect(page).toHaveURL(new RegExp(`/sessions/${s.id}$`))
  await expect(page.getByTestId('entry-card')).toHaveCount(2)
  await noHorizontalScroll(page)

  await page.goto(`/matches/${s.matches[0].id}?session=${s.id}`)
  await expect(page.getByRole('heading', { level: 2 })).toContainText(s.matches[0].title.split(' v ')[0])
  for (const tab of ['Models', 'Teams', 'Odds', 'Match', 'Checks']) {
    await page.getByRole('tab', { name: new RegExp(`^${tab}`) }).click()
  }
  await noHorizontalScroll(page)
  expect(errors).toEqual([])
})

test('unknown address shows a not-found page', async ({ page }) => {
  await page.goto('/does-not-exist')
  await expect(page.getByRole('heading', { name: 'Page not found' })).toBeVisible()
})
