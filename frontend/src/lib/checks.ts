/** Plain-English names of the verification checks (codes from backend/app/decision). Unknown codes fall back to a
 * readable form of the code, so a new backend check never breaks a screen. */
import { humanize } from '@/lib/format'

const FIXED: Record<string, string> = {
  BASELINE_PLAUSIBLE: 'League baseline plausible',
  LEAGUE_P00: "League 0–0 at 15′ rate",
  FIFTEEN_MINUTE_MARKET: "15′ market offered",
  COMPETITION_BASELINE: 'League baseline data',
  TEAM_WINDOWS: 'Team match windows',
  SOURCE_AGREEMENT: 'Data sources agree',
  MODEL_A_AVAILABLE: 'Model A available',
  MODEL_B2_AVAILABLE: 'Model B available',
  MODEL_C_AVAILABLE: 'Market model available',
  PRICE_AVAILABLE: 'Price computable',
  MODEL_GAP_A_B: 'Models A and B agree',
  TEAM_MARKET_GAP: 'Team models agree with the market',
  MINIMUM_ODDS: 'Minimum odds',
  QUOTED_MARKET_QUALIFIES: 'A quoted market qualifies',
  SELECTED_MARKET: 'Market to bet',
  GRADE: 'Grade',
  FAVOURITE_ODDS: 'Strong favourite (odds)',
  FAVOURITE_PROXY: 'Strong favourite (win rates)',
  H2H: 'Head-to-head usable',
  H2H_EARLY_GOALS: 'Head-to-head early goals',
  H2H_SUPPORT: 'Head-to-head support',
}

const WINDOW: Record<string, string> = {
  HOME_GENERAL: 'home team, all matches',
  HOME_HOME: 'home team, home matches',
  AWAY_GENERAL: 'away team, all matches',
  AWAY_AWAY: 'away team, away matches',
}

const MARKET: Record<string, string> = { UNDER05: 'Under 0.5', DRAW: 'Draw' }

export function checkLabel(code: string): string {
  if (FIXED[code]) return FIXED[code]
  let m = /^WINDOW_(.+)$/.exec(code)
  if (m) return `Last 10 — ${WINDOW[m[1]] ?? humanize(m[1]).toLowerCase()}`
  m = /^RECENT_EARLY_GOALS_(.+)$/.exec(code)
  if (m) return `Recent early goals — ${WINDOW[m[1]] ?? humanize(m[1]).toLowerCase()}`
  m = /^QUOTE_(UNDER05|DRAW)(_PRICE|_BOOKMAKER_GAP)?$/.exec(code)
  if (m) {
    const market = MARKET[m[1]]
    if (m[2] === '_PRICE') return `${market}: offered vs minimum odds`
    if (m[2] === '_BOOKMAKER_GAP') return `${market}: BETTORY vs bookmaker`
    return `${market} quote`
  }
  m = /^CONTEXT_(.+)$/.exec(code)
  if (m) return `Context: ${humanize(m[1]).toLowerCase()}`
  m = /^(.+)_AVAILABLE$/.exec(code)
  if (m) return `${humanize(m[1])} available`
  return humanize(code)
}
