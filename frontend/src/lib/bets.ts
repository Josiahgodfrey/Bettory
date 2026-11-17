/** Vocabulary of real bets (docs/API.md §9.2). */

export type Market = 'UNDER05' | 'DRAW'

export const MARKET_LABEL: Record<string, string> = { UNDER05: 'Under 0.5 at 15′', DRAW: 'Draw at 15′' }
export const MARKET_SHORT: Record<string, string> = { UNDER05: 'U0.5', DRAW: 'Draw' }

export const BREACH_LABEL: Record<string, string> = {
  NOT_LISTED: 'Match not listed',
  BELOW_MIN_ODDS: 'Below minimum odds',
  BOTH_MARKETS: 'Both markets bet',
  AFTER_KICKOFF: 'Placed after kick-off',
  WITHDRAWN: 'Match withdrawn',
}

export const breachLabel = (code: string) => BREACH_LABEL[code] ?? code.replace(/_/g, ' ').toLowerCase()

/** The last bookmaker used, remembered in this browser only (a convenience). */
const BOOK_KEY = 'bettory-last-bookmaker'
export function lastBookmaker(): string {
  try {
    return localStorage.getItem(BOOK_KEY) ?? ''
  } catch {
    return ''
  }
}
export function rememberBookmaker(name: string): void {
  try {
    localStorage.setItem(BOOK_KEY, name)
  } catch {
    /* private mode */
  }
}
