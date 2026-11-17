/** Shared API queries (TanStack Query). Keys are stable so screens share the cache. */
import { queryOptions } from '@tanstack/react-query'

import { api, unwrap } from '@/lib/api'

export const dashboardQuery = queryOptions({
  queryKey: ['dashboard'],
  queryFn: () => unwrap(api.GET('/dashboard')),
  refetchInterval: 60_000, // cheap (docs/API.md §10.3)
})

export const sessionsQuery = (from: string, to: string) =>
  queryOptions({
    queryKey: ['sessions', from, to],
    queryFn: () => unwrap(api.GET('/sessions', { params: { query: { from, to } } })),
  })

/** The session report: frozen once published; a preview is recomputed by the server, so it is refreshed on demand. */
export const sessionReportQuery = (id: string) =>
  queryOptions({
    queryKey: ['session', id],
    queryFn: () => unwrap(api.GET('/sessions/{session_id}', { params: { path: { session_id: id } } })),
    staleTime: 5 * 60_000,
    refetchOnWindowFocus: false,
  })

export const sessionFixturesQuery = (id: string) =>
  queryOptions({
    queryKey: ['session', id, 'fixtures'],
    queryFn: () => unwrap(api.GET('/sessions/{session_id}/fixtures', { params: { path: { session_id: id } } })),
    staleTime: 5 * 60_000,
    refetchOnWindowFocus: false,
  })

/** "2026-11-18" shifted by `days` (calendar arithmetic on EAT dates; no time zone involved). */
export function shiftDate(date: string, days: number): string {
  const d = new Date(`${date}T00:00:00Z`)
  d.setUTCDate(d.getUTCDate() + days)
  return d.toISOString().slice(0, 10)
}

// --- match, odds, bets (step 3) -----------------------------------------------------------------

export const matchQuery = (id: string) =>
  queryOptions({
    queryKey: ['match', id],
    queryFn: () => unwrap(api.GET('/matches/{match_id}', { params: { path: { match_id: id } } })),
  })

/** The full analysis at the session's cut-off: recomputed by the server (deterministic), so cached for long. */
export const analysisQuery = (id: string, session: string | null) =>
  queryOptions({
    queryKey: ['match', id, 'analysis', session],
    queryFn: () =>
      unwrap(api.GET('/matches/{match_id}/analysis', { params: { path: { match_id: id }, query: { session } } })),
    staleTime: 10 * 60_000,
    refetchOnWindowFocus: false,
  })

export const matchOddsQuery = (id: string) =>
  queryOptions({
    queryKey: ['match', id, 'odds'],
    queryFn: () => unwrap(api.GET('/matches/{match_id}/odds', { params: { path: { match_id: id } } })),
  })

export const bookmakersQuery = queryOptions({
  queryKey: ['bookmakers'],
  queryFn: () => unwrap(api.GET('/bookmakers')),
  staleTime: 10 * 60_000,
})

export interface SettingsView {
  timezone: string
  betting: { currency: string; max_stake: number }
  [key: string]: unknown
}

export const settingsQuery = queryOptions({
  queryKey: ['settings'],
  queryFn: async () => (await unwrap(api.GET('/settings'))) as SettingsView,
  staleTime: 30 * 60_000,
})

export const betsQuery = queryOptions({
  queryKey: ['bets'],
  queryFn: () => unwrap(api.GET('/bets', { params: { query: { include_cancelled: true } } })),
})
