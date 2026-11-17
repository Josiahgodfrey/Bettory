/** The detail tabs of the match analysis. Every number comes from the API; only "count/n" rates are shown as such
 * (never divided here: ARCHITECTURE.md R2). */
import { useQuery } from '@tanstack/react-query'
import type { ReactNode } from 'react'

import { ErrorState } from '@/components/page'
import { ToneBadge } from '@/components/status'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { PassIcon } from '@/features/match/odds-check'
import type { Schemas } from '@/lib/api'
import { checkLabel, readable } from '@/lib/checks'
import { dateEat, dateTimeEat, humanize, num, odds, pct } from '@/lib/format'
import { matchOddsQuery } from '@/lib/queries'
import { cn } from '@/lib/utils'

// --- the open JSON of GET /matches/{id}/analysis (only the parts shown) ----------------------------

type Rate = { count: number; n: number } | null | undefined

interface Window {
  view: string
  status: string
  reasons: string[]
  sample_size: number
  exact_sample_size: number
  first_kickoff: string | null
  last_kickoff: string | null
  wins: number
  draws: number
  losses: number
  goals_scored: number
  goals_conceded: number
  ht_goals_scored: number
  ht_goals_conceded: number
  zero_zero?: Rate
  level?: Rate
  early_goal?: Rate
  early_scored?: number | null
  early_conceded?: number | null
  recent_early_goal_matches?: Rate
}

interface H2H {
  scope: string
  status: string
  reasons: string[]
  meetings: number
  home_wins: number
  draws: number
  away_wins: number
  avg_ft_goals: number | null
  avg_ht_goals: number | null
  last_meeting: string | null
  zero_zero?: Rate
  early_goal?: Rate
}

interface Baseline {
  status: string
  reasons: string[]
  matches: number
  seasons: string[]
  zero_zero_rate?: number | null
  p00_l?: number | null
  counts?: { matches: number; exact: number; partial: number; incomplete: number }
}

export interface Analysis {
  match_id: string
  session_id: string
  mode: string
  as_of_utc: string
  params_version: string
  formula_version: string | null
  verdict: {
    decision: string
    grade: string
    p_under05: number | null
    p_draw: number | null
    fair_under05: number | null
    fair_draw: number | null
    min_odds_under05: number | null
    min_odds_draw: number | null
    model_spread_pts: number | null
    complete_sample: number | null
    error: string | null
  }
  checks: Schemas['CheckOut'][]
  analysis: {
    baseline?: Baseline
    windows?: Record<string, Window>
    h2h?: H2H
    unavailable?: string[]
    context_flags?: string[]
    untrusted_data?: string[]
  }
}

export function NoData({ children }: { children: ReactNode }) {
  return (
    <Card>
      <CardContent className="py-2 text-sm text-muted-foreground">{children}</CardContent>
    </Card>
  )
}

const rate = (r: Rate) => (r && r.n ? `${r.count}/${r.n}` : '—')

// --- checks ---------------------------------------------------------------------------------------

export function ChecksList({ checks }: { checks: Schemas['CheckOut'][] }) {
  const layers = [...new Map(checks.map((c) => [c.layer, c.layer_name])).entries()].sort((a, b) => a[0] - b[0])
  return (
    <div className="grid gap-3">
      {layers.map(([layer, name]) => (
        <Card key={layer} className="gap-0 py-0">
          <div className="border-b px-4 py-2 text-xs font-medium text-muted-foreground">
            Layer {layer} · {humanize(name)}
          </div>
          <ul className="divide-y">
            {checks
              .filter((c) => c.layer === layer)
              .map((c, i, all) => (
                <li key={`${c.code}-${i}`} className="flex items-start gap-2.5 px-4 py-2.5">
                  <PassIcon passed={c.passed} />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                      <span className="text-sm font-medium">{checkLabel(c.code)}</span>
                      {c.passed === false && c.blocking && <ToneBadge tone="danger">blocks the bet</ToneBadge>}
                    </div>
                    <div className="text-xs tabular">
                      <span className={cn(c.passed === false && 'text-destructive')}>{readable(c.value)}</span>
                      <span className="text-muted-foreground"> · needs {c.threshold}</span>
                    </div>
                    {c.detail && (
                      <div className="mt-0.5 break-words text-xs text-muted-foreground">
                        {i > 0 && all[i - 1].detail === c.detail ? 'Same reason as above.' : c.detail}
                      </div>
                    )}
                  </div>
                </li>
              ))}
          </ul>
        </Card>
      ))}
    </div>
  )
}

// --- models ---------------------------------------------------------------------------------------

export function ModelsPanel({ analysis: a, entry }: { analysis: Analysis; entry: Schemas['ReportEntryOut'] | null }) {
  const v = a.verdict
  const rows: { name: string; hint: string; u: number | null | undefined; d: number | null | undefined; strong?: boolean }[] = [
    { name: 'BETTORY', hint: 'final blended probability', u: v.p_under05, d: v.p_draw, strong: true },
    { name: 'Model A', hint: 'team goal expectancy', u: entry?.p_model_a, d: null },
    { name: 'Model B v2', hint: 'team 15′ indices', u: entry?.p_model_b2, d: null },
    { name: 'Market (Model C)', hint: 'from bookmaker main-market odds', u: entry?.p_market, d: entry?.p_market_draw },
    { name: 'League baseline', hint: 'the competition’s own rate', u: entry?.p_league_00, d: entry?.p_league_draw },
  ]
  const b = a.analysis.baseline
  return (
    <div className="grid gap-3">
      <Card className="gap-0 py-0">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Model</TableHead>
              <TableHead className="text-right">Under 0.5</TableHead>
              <TableHead className="text-right">Draw</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((r) => (
              <TableRow key={r.name} className={cn(r.strong && 'bg-muted/40')}>
                <TableCell>
                  <div className={cn('text-sm', r.strong ? 'font-semibold' : 'font-medium')}>{r.name}</div>
                  <div className="text-xs text-muted-foreground">{r.hint}</div>
                </TableCell>
                <TableCell className="text-right tabular">{pct(r.u)}</TableCell>
                <TableCell className="text-right tabular">{pct(r.d)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        <div className="flex flex-wrap gap-x-4 gap-y-1 border-t px-4 py-2.5 text-xs text-muted-foreground">
          <span>Models within {num(v.model_spread_pts, 1)} pts</span>
          <span>n = {num(v.complete_sample)} (smallest exact-15′ sample)</span>
          {!entry && <span>Per-model values appear once the session is analysed.</span>}
        </div>
      </Card>
      {a.analysis.unavailable && a.analysis.unavailable.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Not available</CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="grid list-disc gap-1 pl-4 text-xs text-muted-foreground">
              {a.analysis.unavailable.map((u, i) => (
                <li key={i} className="break-words">
                  {u}
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}
      {b && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-sm">
              League baseline <StatusBadge status={b.status} />
            </CardTitle>
            <CardDescription>
              {b.matches} matches · seasons {b.seasons?.join(', ')}
              {b.counts && ` · ${b.counts.exact} with exact goal minutes, ${b.counts.incomplete} without`}
            </CardDescription>
          </CardHeader>
          {b.reasons?.length > 0 && (
            <CardContent className="text-xs text-muted-foreground">{b.reasons.join(' · ')}</CardContent>
          )}
        </Card>
      )}
    </div>
  )
}

function StatusBadge({ status }: { status: string }) {
  return <ToneBadge tone={status === 'OK' ? 'success' : 'muted'}>{status === 'OK' ? 'usable' : humanize(status)}</ToneBadge>
}

// --- teams ----------------------------------------------------------------------------------------

const WINDOWS: [string, string, 'home' | 'away'][] = [
  ['home_general', 'all matches', 'home'],
  ['home_home', 'home matches', 'home'],
  ['away_general', 'all matches', 'away'],
  ['away_away', 'away matches', 'away'],
]

export function TeamsPanel({ analysis: a, match: m }: { analysis: Analysis; match: Schemas['MatchOut'] }) {
  const w = a.analysis.windows
  const h = a.analysis.h2h
  if (!w) return <NoData>No team data in this analysis.</NoData>
  return (
    <div className="grid gap-3">
      <div className="grid gap-3 sm:grid-cols-2">
        {WINDOWS.map(([key, label, side]) => {
          const x = w[key]
          if (!x) return null
          return (
            <Card key={key} size="sm">
              <CardHeader>
                <CardTitle className="flex flex-wrap items-center gap-2 text-sm">
                  {side === 'home' ? m.home_team : m.away_team} · last {x.sample_size}, {label} <StatusBadge status={x.status} />
                </CardTitle>
                <CardDescription className="tabular">
                  {dateEat(x.first_kickoff)} – {dateEat(x.last_kickoff)} · {x.exact_sample_size} with exact goal minutes
                </CardDescription>
              </CardHeader>
              <CardContent>
                <dl className="grid grid-cols-3 gap-x-3 gap-y-2 text-sm">
                  <Stat label="W-D-L" value={`${x.wins}-${x.draws}-${x.losses}`} />
                  <Stat label="Goals" value={`${x.goals_scored}:${x.goals_conceded}`} />
                  <Stat label="HT goals" value={`${x.ht_goals_scored}:${x.ht_goals_conceded}`} />
                  <Stat label="0-0 at 15′" value={rate(x.zero_zero)} />
                  <Stat label="Level at 15′" value={rate(x.level)} />
                  <Stat label="Goal before 15′" value={rate(x.early_goal)} />
                </dl>
                {x.reasons.length > 0 && <p className="mt-2 text-xs text-muted-foreground">{x.reasons.join(' · ')}</p>}
              </CardContent>
            </Card>
          )
        })}
      </div>
      {h && (
        <Card size="sm">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-sm">
              Head to head <StatusBadge status={h.status} />
            </CardTitle>
            <CardDescription>
              {h.meetings} meetings{h.last_meeting && ` · last ${dateEat(`${h.last_meeting}T12:00:00Z`)}`}
            </CardDescription>
          </CardHeader>
          <CardContent>
            <dl className="grid grid-cols-3 gap-x-3 gap-y-2 text-sm">
              <Stat label="Home-Draw-Away" value={`${h.home_wins}-${h.draws}-${h.away_wins}`} />
              <Stat label="Avg FT goals" value={num(h.avg_ft_goals, 2)} />
              <Stat label="Avg HT goals" value={num(h.avg_ht_goals, 2)} />
              <Stat label="0-0 at 15′" value={rate(h.zero_zero)} />
              <Stat label="Goal before 15′" value={rate(h.early_goal)} />
            </dl>
            {h.reasons.length > 0 && <p className="mt-2 text-xs text-muted-foreground">{h.reasons.join(' · ')}</p>}
          </CardContent>
        </Card>
      )}
    </div>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="truncate text-xs text-muted-foreground">{label}</dt>
      <dd className="font-medium tabular">{value}</dd>
    </div>
  )
}

// --- odds -----------------------------------------------------------------------------------------

export function OddsPanel({ matchId }: { matchId: string }) {
  const q = useQuery(matchOddsQuery(matchId))
  if (q.isPending) return <Skeleton className="h-40" />
  if (q.isError) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />
  if (!q.data.odds.length) return <NoData>No odds stored for this match yet.</NoData>
  return (
    <Card className="gap-0 py-0">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Bookmaker</TableHead>
            <TableHead>Market</TableHead>
            <TableHead className="text-right">Odds</TableHead>
            <TableHead className="hidden sm:table-cell">Captured</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {q.data.odds.map((o, i) => (
            <TableRow key={i}>
              <TableCell className="font-medium">
                {o.bookmaker}
                {o.source === 'MANUAL' && <span className="ml-1 text-xs text-muted-foreground">(you)</span>}
              </TableCell>
              <TableCell className="text-xs">
                {humanize(o.market)} · {o.selection}
                {o.line != null && ` ${o.line}`}
                <div className="text-muted-foreground">{humanize(o.snapshot_type)}</div>
              </TableCell>
              <TableCell className="text-right font-mono tabular">{odds(o.odds)}</TableCell>
              <TableCell className="hidden text-xs text-muted-foreground sm:table-cell">{dateTimeEat(o.captured_at)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Card>
  )
}

// --- match facts ----------------------------------------------------------------------------------

export function FactsPanel({ match: m }: { match: Schemas['MatchOut'] }) {
  return (
    <div className="grid gap-3">
      <Card size="sm">
        <CardHeader>
          <CardTitle className="text-sm">Goals</CardTitle>
        </CardHeader>
        <CardContent>
          {m.goals.length === 0 ? (
            <p className="text-sm text-muted-foreground">{m.ft_home == null ? 'Not played yet.' : 'No goals.'}</p>
          ) : (
            <ul className="grid gap-1 text-sm">
              {m.goals.map((g) => (
                <li key={g.sequence} className={cn('flex gap-3 tabular', g.minute < 15 && 'font-semibold')}>
                  <span className="w-12 font-mono">
                    {g.minute}
                    {g.extra_minute ? `+${g.extra_minute}` : ''}′
                  </span>
                  <span className="min-w-0 flex-1 truncate">
                    {g.team}
                    {g.player_name && <span className="text-muted-foreground"> · {g.player_name}</span>}
                    {g.is_penalty && ' (pen)'}
                    {g.is_own_goal && ' (og)'}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
      {m.lineups.length > 0 && (
        <div className="grid gap-3 sm:grid-cols-2">
          {m.lineups.map((l) => (
            <Card key={l.team_id} size="sm">
              <CardHeader>
                <CardTitle className="text-sm">
                  {l.team} {l.formation && <span className="font-normal text-muted-foreground">· {l.formation}</span>}
                </CardTitle>
                <CardDescription>fetched {dateTimeEat(l.fetched_at)}</CardDescription>
              </CardHeader>
              <CardContent>
                <ol className="grid gap-0.5 text-xs">
                  {l.players
                    .filter((p) => p.starter)
                    .map((p) => (
                      <li key={`${p.number}-${p.name}`} className="flex gap-2">
                        <span className="w-5 text-right font-mono text-muted-foreground tabular">{p.number ?? ''}</span>
                        {p.name}
                        <span className="text-muted-foreground">{p.position}</span>
                      </li>
                    ))}
                </ol>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
      <Card size="sm">
        <CardHeader>
          <CardTitle className="text-sm">Data</CardTitle>
        </CardHeader>
        <CardContent>
          <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm sm:grid-cols-4">
            <Stat label="15′ data" value={humanize(m.data_status)} />
            <Stat label="Sources" value={m.sources.map((s) => humanize(s.source)).join(', ') || '—'} />
            <Stat label="Agreement" value={humanize(m.reconciliation_status)} />
            <Stat label="Sealed predictions" value={String(m.predictions.length)} />
          </dl>
        </CardContent>
      </Card>
    </div>
  )
}
