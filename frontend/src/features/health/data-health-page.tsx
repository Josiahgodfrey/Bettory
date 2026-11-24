/**
 * Data health, the short version: how much of each league-season is usable, and what the last loads did.
 * Cross-check acceptance, quota and stored-backtest listings were removed in the lean-free pass.
 */

import { useQuery } from '@tanstack/react-query'

import { StatTile } from '@/components/charts/stat-tile'
import { ErrorState, PageHeader } from '@/components/page'
import { ToneBadge } from '@/components/status'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { api, unwrap } from '@/lib/api'
import { dateTimeEat, num, pct } from '@/lib/format'

interface Coverage {
  competition: string
  season: string
  finished: number
  usable_15: number
  usable_15_share: number | null
  complete: number
  derived: number
  partial: number
  unknown_15: number
  conflicts: number
  sources: Record<string, { matches: number; with_minutes: number }>
}
interface Run {
  id: string
  source: string
  job: string
  status: string
  started_at: string
  requests_used: number
  items_stored: number
  error: string | null
  audit_status: string | null
  findings: { code: string; level: string; message: string }[]
}
interface Health {
  coverage: Coverage[]
  runs: Run[]
  open: { open_reviews: number; open_issues: string[] }
}

const STATUS_TONE: Record<string, 'success' | 'danger' | 'muted' | 'warning'> = {
  OK: 'success',
  RUNNING: 'warning',
  PARTIAL: 'warning',
  FAILED: 'danger',
  ERROR: 'danger',
  SKIPPED: 'muted',
}

export function DataHealthPage() {
  const q = useQuery({
    queryKey: ['data-health'],
    queryFn: async () => (await unwrap(api.GET('/data-health', { params: { query: {} } }))) as unknown as Health,
  })

  const total = q.data?.coverage.reduce((n, c) => n + c.finished, 0) ?? 0
  const usable = q.data?.coverage.reduce((n, c) => n + c.usable_15, 0) ?? 0

  return (
    <>
      <PageHeader
        title="Data health"
        description="How much of each league-season can be analysed: ESPN goal minutes, missing matches, and the last loads."
      />
      {q.isPending && <Skeleton className="h-64" />}
      {q.isError && <ErrorState error={q.error} onRetry={() => void q.refetch()} />}
      {q.data && (
        <div className="grid gap-4">
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <StatTile label="League-seasons" value={q.data.coverage.length} />
            <StatTile label="Finished matches" value={num(total)} />
            <StatTile
              label="With a usable 15′ result"
              value={pct(total ? usable / total : null)}
              detail={`${num(usable)} matches`}
              tone={total && usable / total >= 0.9 ? 'good' : 'bad'}
            />
            <StatTile
              label="Open team reviews"
              value={q.data.open.open_reviews}
              detail={q.data.open.open_reviews ? 'resolve with: review list' : 'nothing to review'}
              tone={q.data.open.open_reviews ? 'bad' : 'good'}
            />
          </div>

          <Card size="sm">
            <CardHeader>
              <CardTitle className="text-sm">Coverage</CardTitle>
              <CardDescription>A league-season can be analysed only when its matches have goal minutes.</CardDescription>
            </CardHeader>
            <CardContent>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>League</TableHead>
                    <TableHead>Season</TableHead>
                    <TableHead className="text-right">Finished</TableHead>
                    <TableHead className="text-right">Usable 15′</TableHead>
                    <TableHead className="text-right">Complete</TableHead>
                    <TableHead className="text-right">Derived</TableHead>
                    <TableHead className="text-right">Missing</TableHead>
                    <TableHead className="text-right">Conflicts</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {q.data.coverage.map((c) => (
                    <TableRow key={`${c.competition}-${c.season}`}>
                      <TableCell className="font-mono text-xs">{c.competition}</TableCell>
                      <TableCell className="font-mono text-xs">{c.season}</TableCell>
                      <TableCell className="text-right tabular-nums">{num(c.finished)}</TableCell>
                      <TableCell className="text-right tabular-nums">
                        {num(c.usable_15)} <span className="text-muted-foreground">({pct(c.usable_15_share)})</span>
                      </TableCell>
                      <TableCell className="text-right tabular-nums">{num(c.complete)}</TableCell>
                      <TableCell className="text-right tabular-nums">{num(c.derived)}</TableCell>
                      <TableCell className="text-right tabular-nums">{num(c.partial + c.unknown_15)}</TableCell>
                      <TableCell className="text-right tabular-nums">
                        {c.conflicts ? <ToneBadge tone="danger">{num(c.conflicts)}</ToneBadge> : '0'}
                      </TableCell>
                    </TableRow>
                  ))}
                  {q.data.coverage.length === 0 && (
                    <TableRow>
                      <TableCell colSpan={8} className="text-muted-foreground">
                        Nothing loaded yet. Load a season with: python -m app.cli ingest espn-days
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
            </CardContent>
          </Card>

          <Card size="sm">
            <CardHeader>
              <CardTitle className="text-sm">Recent loads</CardTitle>
              <CardDescription>The last ingestion runs, with anything they flagged.</CardDescription>
            </CardHeader>
            <CardContent>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Started</TableHead>
                    <TableHead>Source</TableHead>
                    <TableHead>Job</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead className="text-right">Stored</TableHead>
                    <TableHead>Notes</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {q.data.runs.map((r) => (
                    <TableRow key={r.id}>
                      <TableCell className="whitespace-nowrap text-xs">{dateTimeEat(r.started_at)}</TableCell>
                      <TableCell className="font-mono text-xs">{r.source}</TableCell>
                      <TableCell className="max-w-64 truncate font-mono text-xs" title={r.job}>
                        {r.job}
                      </TableCell>
                      <TableCell>
                        <ToneBadge tone={STATUS_TONE[r.status] ?? 'muted'}>{r.status}</ToneBadge>
                      </TableCell>
                      <TableCell className="text-right tabular-nums">{num(r.items_stored)}</TableCell>
                      <TableCell className="max-w-96 truncate text-xs" title={r.error ?? r.findings.map((f) => f.message).join('; ')}>
                        {r.error ?? (r.findings.length ? r.findings.map((f) => f.message).join('; ') : '—')}
                      </TableCell>
                    </TableRow>
                  ))}
                  {q.data.runs.length === 0 && (
                    <TableRow>
                      <TableCell colSpan={6} className="text-muted-foreground">
                        No loads yet.
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </div>
      )}
    </>
  )
}
