import { describe, expect, it } from 'vitest'

import { checkLabel } from '@/lib/checks'
import { dateEat, humanize, odds, pct, relative, sessionLabel, timeEat } from '@/lib/format'
import { shiftDate } from '@/lib/queries'

describe('format (display only)', () => {
  it('formats probabilities and odds from the API', () => {
    expect(pct(0.7781)).toBe('77.8%')
    expect(pct(null)).toBe('—')
    expect(odds(1.3)).toBe('1.30')
    expect(odds(undefined)).toBe('—')
  })

  it('shows UTC times in EAT (UTC+3)', () => {
    expect(timeEat('2026-11-21T09:15:00Z')).toBe('12:15')
    expect(timeEat('2026-11-21T22:30:00Z')).toBe('01:30')
    expect(dateEat('2026-11-21T22:30:00Z')).toBe('Sun 22 Nov') // after midnight EAT: next day
  })

  it('names sessions', () => {
    expect(sessionLabel('2026-11-21-NOON')).toBe('Noon · Sat 21 Nov')
    expect(sessionLabel('2026-11-22-NIGHT')).toBe('Night · Sun 22 Nov')
    expect(sessionLabel('bad')).toBe('bad')
  })

  it('describes time distances', () => {
    const now = Date.parse('2026-11-21T09:00:00Z')
    expect(relative('2026-11-21T09:15:00Z', now)).toBe('in 15 min')
    expect(relative('2026-11-21T07:30:00Z', now)).toBe('1 h 30 min ago')
    expect(relative('2026-11-24T09:00:00Z', now)).toBe('in 3 d')
  })

  it('shifts calendar dates across months', () => {
    expect(shiftDate('2026-11-28', 7)).toBe('2026-12-05')
    expect(shiftDate('2026-03-01', -1)).toBe('2026-02-28')
  })

  it('humanizes codes', () => {
    expect(humanize('INSUFFICIENT_DATA')).toBe('Insufficient data')
  })
})

describe('check labels', () => {
  it('names every check family in plain English', () => {
    expect(checkLabel('WINDOW_HOME_GENERAL')).toBe('Last 10 — home team, all matches')
    expect(checkLabel('MODEL_GAP_A_B')).toBe('Models A and B agree')
    expect(checkLabel('QUOTE_UNDER05_PRICE')).toBe('Under 0.5: offered vs minimum odds')
    expect(checkLabel('QUOTE_DRAW_BOOKMAKER_GAP')).toBe('Draw: BETTORY vs bookmaker')
    expect(checkLabel('RECENT_EARLY_GOALS_AWAY_AWAY')).toBe('Recent early goals — away team, away matches')
    expect(checkLabel('PRICE_AVAILABLE')).toBe('Price computable')
    expect(checkLabel('SOMETHING_NEW')).toBe('Something new') // unknown codes stay readable
  })
})
