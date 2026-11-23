import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Bell, BellOff, Play, RefreshCw } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router'
import { toast } from 'sonner'

import { StatTile } from '@/components/charts/stat-tile'
import { ErrorState, PageHeader } from '@/components/page'
import { ToneBadge } from '@/components/status'
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
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { SessionSchedule } from '@/features/recorder/session-schedule'
import { api, ApiError, unwrap, type Schemas } from '@/lib/api'
import { dateTimeEat, humanize, num, relative } from '@/lib/format'
import { cn } from '@/lib/utils'

/** Recorder / system status (docs/API.md screen 11): GET /paper/status, POST /paper/cycle, GET /jobs/runs. */
export function RecorderPage() {
  const q = useQuery({
    queryKey: ['paper', 'status'],
    queryFn: () => unwrap(api.GET('/paper/status')),
    // poll while a cycle started here runs; otherwise every minute (docs/API.md §10.3)
    refetchInterval: (query) => (query.state.data?.manual_cycle?.running ? 3_000 : 60_000),
  })

  return (
    <>
      <PageHeader
        title="Recorder"
        description={`Plans each session's fixtures, captures odds, seals predictions at the cut-off, re-checks lineups, stores closing odds and settles results — every ${q.data?.schedule_every_minutes ?? 5} minutes when running.`}
        actions={
          <Button variant="outline" size="sm" onClick={() => void q.refetch()} disabled={q.isFetching}>
            <RefreshCw className={cn(q.isFetching && 'animate-spin')} /> Refresh
          </Button>
        }
      />
      {q.isPending && <Skeleton className="h-64" />}
      {q.isError && <ErrorState error={q.error} onRetry={() => void q.refetch()} />}
      {q.data && (
        <div className="grid gap-4">
          <Tiles s={q.data} />
          <SessionSchedule sessions={q.data.sessions ?? []} now={q.data.now_utc} />
          <div className="grid gap-4 lg:grid-cols-2">
            <DueNow s={q.data} />
            <LastRuns s={q.data} />
          </div>
          <JobRuns />
        </div>
      )}
    </>
  )
}

function Tiles({ s }: { s: Schemas['RecorderStatusOut'] }) {
  const share = s.requests_daily_bound ? Math.min(100, (s.requests_today / s.requests_daily_bound) * 100) : 0 // bar width only
  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      <StatTile
        label="API requests today"
        value={
          <span>
            {s.requests_today} <span className="text-base font-normal text-muted-foreground">/ {s.requests_daily_bound}</span>
          </span>
        }
        detail={
          <span className="grid gap-1">
            <span className="h-1.5 w-full rounded-full bg-muted">
              <span className={cn('block h-full rounded-full', share >= 90 ? 'bg-destructive' : 'bg-primary')} style={{ width: `${share}%` }} />
            </span>
            {humanize(s.api_football_plan)} plan · recorder bound
          </span>
        }
      />
      <StatTile label="Sealed predictions" value={num(s.frozen)} detail={`last freeze ${s.last_freeze_at ? relative(s.last_freeze_at) : 'never'}`} />
      <StatTile
        label="Kicked off, not settled"
        value={s.kicked_off_unsettled}
        tone={s.kicked_off_unsettled > 0 ? 'bad' : null}
        detail={`last settlement ${s.last_settlement_at ? relative(s.last_settlement_at) : 'never'}`}
      />
      <StatTile
        label="Telegram"
        value={
          <span className="inline-flex items-center gap-2 text-lg">
            {s.telegram.configured ? <Bell className="size-5" /> : <BellOff className="size-5" />}
            {s.telegram.configured ? 'On' : 'Off'}
          </span>
        }
        detail={
          <Link className="underline underline-offset-2" to="/notifications">
            {s.telegram.pending} waiting · {s.telegram.failed} failed
          </Link>
        }
      />
    </div>
  )
}

function DueNow({ s }: { s: Schemas['RecorderStatusOut'] }) {
  const qc = useQueryClient()
  const [confirm, setConfirm] = useState(false)
  const m = s.manual_cycle
  const run = useMutation({
    mutationFn: () => unwrap(api.POST('/paper/cycle', { params: { query: { dry_run: false } } })),
    onSuccess: () => {
      toast.info('Recorder cycle started')
      void qc.invalidateQueries({ queryKey: ['paper', 'status'] })
      setConfirm(false)
    },
    onError: (e) => {
      if (e instanceof ApiError && e.status === 409) toast.warning('A cycle is already running')
      setConfirm(false)
    },
  })
  const running = s.cycle_running || Boolean(m?.running)

  return (
    <Card>
      <CardHeader>
        <CardTitle>What the next cycle would do</CardTitle>
        <CardDescription>A dry run of the recorder, computed now ({dateTimeEat(s.now_utc)}).</CardDescription>
        <CardAction>
          <Button size="sm" onClick={() => setConfirm(true)} disabled={running || run.isPending}>
            <Play /> {running ? 'Running…' : 'Run one cycle now'}
          </Button>
        </CardAction>
      </CardHeader>
      <CardContent className="grid gap-4">
        {s.due_now.length === 0 ? (
          <p className="text-sm text-muted-foreground">Nothing is due: no capture, freeze, re-check, closing odds, results or settlement.</p>
        ) : (
          <ul className="grid gap-1.5 font-mono text-xs">
            {s.due_now.map((l, i) => (
              <li key={i} className="break-words border-l-2 border-primary pl-2">
                {l}
              </li>
            ))}
          </ul>
        )}
        {m && (
          <div className="grid gap-1.5 border-t pt-3">
            <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              Last cycle started here {m.started_at ? relative(m.started_at) : ''}
              {m.running ? (
                <ToneBadge tone="info">running</ToneBadge>
              ) : m.errors || m.error ? (
                <ToneBadge tone="danger">{m.errors || 1} error(s)</ToneBadge>
              ) : (
                <ToneBadge tone="success">done</ToneBadge>
              )}
            </div>
            {m.error && <p className="text-xs text-destructive">{m.error}</p>}
            <ul className="grid gap-1 font-mono text-xs">
              {m.lines.map((l, i) => (
                <li key={i} className="break-words">
                  {l}
                </li>
              ))}
            </ul>
          </div>
        )}
        <p className="text-xs text-muted-foreground">
          Competitions: {s.competitions.join(', ')}. The cycle uses the same lock and daily request bound as the scheduled one.
        </p>
      </CardContent>
      <AlertDialog open={confirm} onOpenChange={setConfirm}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Run one recorder cycle now?</AlertDialogTitle>
            <AlertDialogDescription>
              It does exactly what is due ({s.due_now.length ? `${s.due_now.length} step(s)` : 'nothing at the moment'}), may spend
              API-Football requests (within today’s bound of {s.requests_daily_bound}) and seals predictions if a cut-off has
              passed. Sealed records cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <Button onClick={() => run.mutate()} disabled={run.isPending}>
              {run.isPending ? 'Starting…' : 'Run cycle'}
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Card>
  )
}

function LastRuns({ s }: { s: Schemas['RecorderStatusOut'] }) {
  const rows: [string, Schemas['RunMarkOut'] | null | undefined, string | null | undefined][] = [
    ['Odds capture', s.last_capture, null],
    ['Results', s.last_results, null],
  ]
  return (
    <Card>
      <CardHeader>
        <CardTitle>Last activity</CardTitle>
        <CardDescription>Times in EAT.</CardDescription>
      </CardHeader>
      <CardContent className="px-0">
        <ul className="divide-y text-sm">
          {rows.map(([label, run]) => (
            <li key={label} className="flex items-start gap-3 px-4 py-2.5">
              <span className="w-28 shrink-0 text-muted-foreground">{label}</span>
              {run ? (
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <RunBadge status={run.status} />
                    <span className="tabular">{dateTimeEat(run.started_at)}</span>
                    <span className="text-xs text-muted-foreground">{run.requests_used ?? 0} requests</span>
                  </div>
                  <div className="truncate text-xs text-muted-foreground">{run.job_name}</div>
                  {run.error && <div className="break-words text-xs text-destructive">{run.error}</div>}
                </div>
              ) : (
                <span className="text-muted-foreground">never</span>
              )}
            </li>
          ))}
          <li className="flex gap-3 px-4 py-2.5">
            <span className="w-28 shrink-0 text-muted-foreground">Freeze</span>
            <span className="tabular">{s.last_freeze_at ? dateTimeEat(s.last_freeze_at) : 'never'}</span>
          </li>
          <li className="flex gap-3 px-4 py-2.5">
            <span className="w-28 shrink-0 text-muted-foreground">Settlement</span>
            <span className="tabular">{s.last_settlement_at ? dateTimeEat(s.last_settlement_at) : 'never'}</span>
          </li>
        </ul>
      </CardContent>
    </Card>
  )
}

export function RunBadge({ status }: { status: string }) {
  const tone = status === 'SUCCEEDED' ? 'success' : status === 'FAILED' ? 'danger' : status === 'RUNNING' ? 'info' : 'muted'
  return <ToneBadge tone={tone}>{humanize(status)}</ToneBadge>
}

function JobRuns() {
  const q = useQuery({ queryKey: ['jobs', 'runs'], queryFn: () => unwrap(api.GET('/jobs/runs', { params: { query: { limit: 25 } } })) })
  return (
    <Card>
      <CardHeader>
        <CardTitle>Recent job runs</CardTitle>
        <CardDescription>Every data load and recorder step, newest first.</CardDescription>
      </CardHeader>
      <CardContent className="px-0">
        {q.isPending ? (
          <Skeleton className="mx-4 h-24" />
        ) : q.isError ? (
          <div className="px-4">
            <ErrorState error={q.error} onRetry={() => void q.refetch()} />
          </div>
        ) : q.data.length === 0 ? (
          <p className="px-4 text-sm text-muted-foreground">No runs yet.</p>
        ) : (
          <ul className="divide-y">
            {q.data.map((r) => (
              <li key={r.id} className="grid gap-0.5 px-4 py-2.5 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <RunBadge status={r.status} />
                  <span className="min-w-0 truncate font-mono text-xs">{r.job_name}</span>
                  <span className="ml-auto text-xs text-muted-foreground tabular">{dateTimeEat(r.started_at)}</span>
                </div>
                <div className="text-xs text-muted-foreground tabular">
                  {humanize(r.source)} · {r.requests_used} requests · {r.items_stored} items stored
                  {r.finished_at && ` · finished ${relative(r.finished_at)}`}
                </div>
                {r.error && <div className="break-words text-xs text-destructive">{r.error}</div>}
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}
