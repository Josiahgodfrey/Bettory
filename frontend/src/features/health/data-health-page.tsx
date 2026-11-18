import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, CheckCircle2 } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { Link } from 'react-router'
import { toast } from 'sonner'

import { StatTile } from '@/components/charts/stat-tile'
import { ErrorState, PageHeader } from '@/components/page'
import { ToneBadge } from '@/components/status'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Textarea } from '@/components/ui/textarea'
import { RunBadge } from '@/features/recorder/recorder-page'
import { api, unwrap } from '@/lib/api'
import { dateTimeEat, humanize, num, pct } from '@/lib/format'

// --- GET /data-health (open JSON, DATA-HEALTH.md) -------------------------------------------------

interface Agreement {
  compared: number
  agreed: number
  rate: number | null
}
interface CrossCheck {
  id: string
  competition: string
  season: string
  status: string
  accepted: boolean
  accepted_note: string | null
  blocks_bets: boolean
  fifteen_verified: boolean
  checked_at: string
  matches: number
  multi_source: number
  failed_checks: string[]
  agreement: Record<'ft' | 'ht' | 'h15' | 'h15_vs_ht', Agreement>
  min_agreement: number
}
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
  crosscheck: CrossCheck | null
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
  blocking_crosschecks: CrossCheck[]
  runs: Run[]
  open: { open_reviews: number; open_issues: ({ code?: string; level?: string; message?: string } | string)[] }
}
interface Backtest {
  name: string
  params_version: string | null
  gate_b: { name: string; status: string; detail: string }[]
  gate_b_passed: boolean
}

const CC_TONE: Record<string, 'success' | 'danger' | 'muted' | 'warning'> = { PASS: 'success', FAIL: 'danger', FAILED: 'danger', NOT_MEASURABLE: 'muted' }

/** Data health (docs/API.md screen 13): coverage, source agreement, quota, runs, backtests, open reviews. */
export function DataHealthPage() {
  const q = useQuery({ queryKey: ['data-health'], queryFn: async () => (await unwrap(api.GET('/data-health', { params: { query: {} } }))) as unknown as Health })
  // read live from API-Football (/status, free): 503 when the backend has no key
  const quota = useQuery({ queryKey: ['data-health', 'quota'], queryFn: () => unwrap(api.GET('/data-health/quota')), retry: false })

  return (
    <>
      <PageHeader title="Data health" description="Is the data good enough to bet on? A failed cross-check blocks bets in its league." />
      {q.isPending && <Skeleton className="h-64" />}
      {q.isError && <ErrorState error={q.error} onRetry={() => void q.refetch()} />}
      {q.data && (
        <div className="grid gap-4">
          {q.data.blocking_crosschecks.length > 0 && (
            <Alert variant="destructive">
              <AlertTriangle />
              <AlertTitle>Bets blocked by data disagreement</AlertTitle>
              <AlertDescription>
                {q.data.blocking_crosschecks.map((c) => `${c.competition} ${c.season}`).join(', ')}: the sources disagree. Review below and accept
                only if the data is right.
              </AlertDescription>
            </Alert>
          )}
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <StatTile
              label="API-Football quota today"
              value={quota.data ? `${num(quota.data.used)} / ${num(quota.data.limit)}` : '—'}
              detail={quota.data ? `${num(quota.data.remaining)} left` : quota.isError ? (quota.error as Error).message : 'checking…'}
              tone={quota.data && quota.data.remaining <= 0 ? 'bad' : null}
            />
            <StatTile label="League-seasons covered" value={q.data.coverage.length} detail={`${q.data.coverage.filter((c) => c.crosscheck?.status === 'PASS').length} cross-checked`} />
            <StatTile label="Blocking cross-checks" value={q.data.blocking_crosschecks.length} tone={q.data.blocking_crosschecks.length ? 'bad' : 'good'} />
            <StatTile
              label="Open team reviews"
              value={q.data.open.open_reviews}
              detail={q.data.open.open_reviews ? 'resolve with the CLI: review list / review resolve' : 'nothing to review'}
            />
          </div>
          {q.data.open.open_issues.length > 0 && (
            <Card size="sm">
              <CardHeader>
                <CardTitle className="text-sm">Open issues</CardTitle>
              </CardHeader>
              <CardContent>
                <ul className="grid list-disc gap-1 pl-4 text-sm">
                  {q.data.open.open_issues.map((x, i) => (
                    <li key={i}>{typeof x === 'string' ? x : (x.message ?? x.code)}</li>
                  ))}
                </ul>
              </CardContent>
            </Card>
          )}
          <CoverageTable rows={q.data.coverage} />
          <Runs runs={q.data.runs} />
          <Backtests />
        </div>
      )}
    </>
  )
}

function CoverageTable({ rows }: { rows: Coverage[] }) {
  return (
    <Card className="gap-0 pb-0">
      <CardHeader className="pb-4">
        <CardTitle>Coverage and source agreement</CardTitle>
        <CardDescription>
          “Usable 15′” = finished matches whose score after 15 minutes is known (from goal minutes, or derived). The cross-check compares
          sources on full time, half time and 15′.
        </CardDescription>
      </CardHeader>
      <div className="overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>League · season</TableHead>
              <TableHead className="hidden text-right sm:table-cell">Finished</TableHead>
              <TableHead className="text-right">Usable 15′</TableHead>
              <TableHead className="hidden text-right md:table-cell">Exact / derived / partial</TableHead>
              <TableHead>Cross-check</TableHead>
              <TableHead className="hidden lg:table-cell">Sources</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((c) => (
              <TableRow key={`${c.competition}-${c.season}`}>
                <TableCell className="whitespace-normal font-medium">
                  {c.competition} <span className="block text-xs font-normal text-muted-foreground sm:inline">{c.season}</span>
                </TableCell>
                <TableCell className="hidden text-right tabular sm:table-cell">{num(c.finished)}</TableCell>
                <TableCell className="text-right tabular">
                  {num(c.usable_15)} <span className="text-xs text-muted-foreground">{pct(c.usable_15_share, 0)}</span>
                </TableCell>
                <TableCell className="hidden text-right tabular md:table-cell">
                  {c.complete} / {c.derived} / {c.partial}
                  {c.conflicts > 0 && <span className="ml-1 text-destructive">· {c.conflicts} conflicts</span>}
                </TableCell>
                <TableCell className="min-w-40 whitespace-normal">{c.crosscheck ? (
                    <CrossCheckCell c={c.crosscheck} />
                  ) : (
                    <div className="flex flex-wrap items-center gap-1.5">
                      <ToneBadge tone="muted">not run</ToneBadge>
                      <RunCrossCheck competition={c.competition} season={c.season} />
                    </div>
                  )}</TableCell>
                <TableCell className="hidden text-xs text-muted-foreground lg:table-cell">
                  {Object.entries(c.sources)
                    .map(([s, v]) => `${humanize(s)} ${v.matches}`)
                    .join(' · ')}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </Card>
  )
}

function CrossCheckCell({ c }: { c: CrossCheck }) {
  const a = c.agreement
  const failed = c.status === 'FAIL' || c.status === 'FAILED'
  return (
    <div className="grid gap-1">
      <div className="flex flex-wrap items-center gap-1.5">
        <ToneBadge tone={CC_TONE[c.status] ?? 'muted'}>{humanize(c.status)}</ToneBadge>
        {c.accepted && <ToneBadge tone="info">accepted</ToneBadge>}
        {c.blocks_bets && <ToneBadge tone="danger">blocks bets</ToneBadge>}
      </div>
      <div className="text-xs text-muted-foreground tabular">
        FT {a.ft.agreed}/{a.ft.compared} · HT {a.ht.agreed}/{a.ht.compared} · 15′ {a.h15.agreed}/{a.h15.compared}
      </div>
      {c.accepted_note && <div className="text-xs text-muted-foreground">“{c.accepted_note}”</div>}
      <div className="flex flex-wrap gap-1.5">
        <CrossCheckDetails c={c} />
        <RunCrossCheck competition={c.competition} season={c.season} />
        {failed && !c.accepted && <AcceptCrossCheck c={c} />}
      </div>
    </div>
  )
}

/** Run the cross-check of one league-season now (POST /data-health/crosschecks). */
function RunCrossCheck({ competition, season }: { competition: string; season: string }) {
  const qc = useQueryClient()
  const run = useMutation({
    mutationFn: () => unwrap(api.POST('/data-health/crosschecks', { body: { competition, season } })),
    onSuccess: (r) => {
      const st = (r as { status?: string }).status
      toast.success(`${competition} ${season}: cross-check ${st ? humanize(st).toLowerCase() : 'done'}`)
      void qc.invalidateQueries({ queryKey: ['data-health'] })
      void qc.invalidateQueries({ queryKey: ['dashboard'] })
    },
  })
  return (
    <Button size="xs" variant="ghost" className="w-fit" onClick={() => run.mutate()} disabled={run.isPending}>
      {run.isPending ? 'Checking…' : 'Run again'}
    </Button>
  )
}

/** The matches the sources disagree on (GET /data-health/crosschecks/{id}): what to look at before accepting. */
function CrossCheckDetails({ c }: { c: CrossCheck }) {
  const [open, setOpen] = useState(false)
  const q = useQuery({
    queryKey: ['data-health', 'crosscheck', c.id],
    queryFn: async () =>
      (await unwrap(api.GET('/data-health/crosschecks/{crosscheck_id}', { params: { path: { crosscheck_id: c.id } } }))) as unknown as CrossCheck & {
        disagreements: { match: string; kind: string; values: Record<string, string> }[]
      },
    enabled: open,
  })
  const a = c.agreement
  return (
    <>
      <Button size="xs" variant="ghost" className="w-fit" onClick={() => setOpen(true)}>
        Details…
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>
              Cross-check {c.competition} {c.season}
            </DialogTitle>
            <DialogDescription>
              Checked {dateTimeEat(c.checked_at)} · {c.matches} matches, {c.multi_source} with more than one source · needs {pct(c.min_agreement, 0)}{' '}
              agreement on at least {String((c as { min_compared?: number }).min_compared ?? '')} matches.
            </DialogDescription>
          </DialogHeader>
          <dl className="grid grid-cols-4 gap-2 text-sm tabular">
            {(['ft', 'ht', 'h15', 'h15_vs_ht'] as const).map((k) => (
              <div key={k} className="border p-2">
                <dt className="text-xs text-muted-foreground">{k === 'ft' ? 'Full time' : k === 'ht' ? 'Half time' : k === 'h15' ? '15′' : '15′ vs HT'}</dt>
                <dd className="font-medium">
                  {a[k].agreed}/{a[k].compared}
                </dd>
              </div>
            ))}
          </dl>
          {c.failed_checks.length > 0 && <p className="text-sm text-destructive">Failed: {c.failed_checks.join(', ')}</p>}
          <div className="max-h-72 overflow-auto border">
            {q.isPending ? (
              <p className="p-3 text-sm text-muted-foreground">Loading…</p>
            ) : q.isError ? (
              <div className="p-3">
                <ErrorState error={q.error} />
              </div>
            ) : q.data.disagreements.length === 0 ? (
              <p className="p-3 text-sm text-muted-foreground">No disagreement between the sources.</p>
            ) : (
              <table className="w-full text-left text-xs tabular">
                <thead className="sticky top-0 bg-muted">
                  <tr>
                    <th className="px-2 py-1">Match</th>
                    <th className="px-2 py-1">What</th>
                    <th className="px-2 py-1">Sources say</th>
                  </tr>
                </thead>
                <tbody>
                  {q.data.disagreements.map((d, i) => (
                    <tr key={i} className="border-t">
                      <td className="px-2 py-1 font-mono">{d.match}</td>
                      <td className="px-2 py-1">{humanize(d.kind)}</td>
                      <td className="px-2 py-1">
                        {Object.entries(d.values)
                          .map(([src, v]) => `${humanize(src)} ${v}`)
                          .join(' · ')}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </DialogContent>
      </Dialog>
    </>
  )
}

/** Accept a FAILED cross-check after reviewing it (POST /data-health/crosschecks/{id}/accept, with a note). */
function AcceptCrossCheck({ c }: { c: CrossCheck }) {
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const accept = useMutation({
    mutationFn: (note: string) =>
      unwrap(api.POST('/data-health/crosschecks/{crosscheck_id}/accept', { params: { path: { crosscheck_id: c.id } }, body: { note } })),
    onSuccess: () => {
      toast.success(`${c.competition} ${c.season}: cross-check accepted`)
      void qc.invalidateQueries({ queryKey: ['data-health'] })
      void qc.invalidateQueries({ queryKey: ['dashboard'] })
      setOpen(false)
    },
  })
  function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const note = String(new FormData(e.currentTarget).get('note') ?? '').trim()
    if (note.length >= 3) accept.mutate(note)
  }
  return (
    <>
      <Button size="xs" variant="outline" className="w-fit" onClick={() => setOpen(true)}>
        Accept after review…
      </Button>
      <AlertDialog open={open} onOpenChange={setOpen}>
        <AlertDialogContent>
          <form onSubmit={submit} className="grid gap-4">
            <AlertDialogHeader>
              <AlertDialogTitle>
                Accept {c.competition} {c.season}?
              </AlertDialogTitle>
              <AlertDialogDescription>
                Failed: {c.failed_checks.join(', ') || 'agreement below the minimum'}. Accept only after checking the disagreeing matches —
                bets in this league will no longer be blocked. Your note is kept with the check.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <Textarea name="note" placeholder="What did you check, and why is the data right?" required minLength={3} maxLength={2000} rows={3} autoFocus />
            <AlertDialogFooter>
              <AlertDialogCancel>Cancel</AlertDialogCancel>
              <Button type="submit" disabled={accept.isPending}>
                {accept.isPending ? 'Saving…' : 'Accept'}
              </Button>
            </AlertDialogFooter>
          </form>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}

function Runs({ runs }: { runs: Run[] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Recent data runs and audits</CardTitle>
      </CardHeader>
      <CardContent className="px-0">
        <ul className="divide-y">
          {runs.map((r) => (
            <li key={r.id} className="grid gap-0.5 px-4 py-2.5 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <RunBadge status={r.status} />
                {r.audit_status && <ToneBadge tone={r.audit_status === 'OK' ? 'success' : 'warning'}>audit {humanize(r.audit_status)}</ToneBadge>}
                <span className="min-w-0 truncate font-mono text-xs">{r.job}</span>
                <span className="ml-auto text-xs text-muted-foreground tabular">{dateTimeEat(r.started_at)}</span>
              </div>
              <div className="text-xs text-muted-foreground tabular">
                {humanize(r.source)} · {r.requests_used} requests · {r.items_stored} items
              </div>
              {r.error && <div className="break-words text-xs text-destructive">{r.error}</div>}
              {r.findings.map((f, i) => (
                <div key={i} className={f.level === 'ERROR' ? 'text-xs text-destructive' : 'text-xs text-warning'}>
                  {humanize(f.level)}: {f.message}
                </div>
              ))}
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  )
}

function Backtests() {
  const q = useQuery({ queryKey: ['data-health', 'backtests'], queryFn: async () => (await unwrap(api.GET('/data-health/backtests'))) as unknown as Backtest[] })
  if (!q.data?.length) return null
  return (
    <Card>
      <CardHeader>
        <CardTitle>Backtests</CardTitle>
        <CardDescription>Stored backtest and tuning runs (BACKTEST-REPORT.md).</CardDescription>
      </CardHeader>
      <CardContent className="px-0">
        <ul className="divide-y">
          {q.data.map((b) => (
            <li key={b.name} className="grid gap-1 px-4 py-2.5 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                {b.gate_b_passed ? <CheckCircle2 className="size-4 text-success" /> : <AlertTriangle className="size-4 text-muted-foreground" />}
                <Link to={`/data-health/backtests/${encodeURIComponent(b.name)}`} className="min-w-0 truncate font-mono text-xs underline-offset-2 hover:underline">
                  {b.name}
                </Link>
                {b.params_version && <span className="text-xs text-muted-foreground">params {b.params_version}</span>}
              </div>
              <div className="flex flex-wrap gap-1.5">
                {b.gate_b.map((g) => (
                  <ToneBadge key={g.name} tone={g.status === 'PASS' ? 'success' : g.status === 'FAIL' ? 'danger' : 'muted'} title={g.detail}>
                    {g.name}: {humanize(g.status)}
                  </ToneBadge>
                ))}
              </div>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  )
}
