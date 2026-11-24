import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, ArrowRight, CircleAlert, Link2, Link2Off, RefreshCw } from 'lucide-react'
import { Link } from 'react-router'

import { ErrorState, PageHeader } from '@/components/page'
import { SessionStateBadge, sessionStateInfo } from '@/components/status'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { useNow } from '@/hooks/use-now'
import type { Schemas } from '@/lib/api'
import { dateEat, dateTimeEat, relative, sessionLabel, timeEat } from '@/lib/format'
import { dashboardQuery } from '@/lib/queries'
import { cn } from '@/lib/utils'

type Session = Schemas['SessionOut']

/** Today: the next session, alerts, nearby sessions and the recorder (docs/API.md screen 2, GET /dashboard). */
export function TodayPage() {
  const q = useQuery(dashboardQuery)

  return (
    <>
      <PageHeader
        title="Today"
        description={q.data ? `${dateEat(q.data.now_utc)} · times in EAT` : 'Times in EAT'}
        actions={
          <Button variant="outline" size="sm" onClick={() => void q.refetch()} disabled={q.isFetching}>
            <RefreshCw className={cn(q.isFetching && 'animate-spin')} /> Refresh
          </Button>
        }
      />
      {q.isPending && <TodaySkeleton />}
      {q.isError && <ErrorState error={q.error} onRetry={() => void q.refetch()} />}
      {q.data && (
        <div className="grid gap-4">
          <Alerts alerts={q.data.alerts} />
          <NextSession session={q.data.next_session ?? null} />
          <div className="grid gap-4 lg:grid-cols-3">
            <NearbySessions sessions={q.data.sessions} className="lg:col-span-2" />
            <Recorder paper={q.data.paper} />
          </div>
        </div>
      )}
    </>
  )
}

function TodaySkeleton() {
  return (
    <div className="grid gap-4">
      <Skeleton className="h-44" />
      <div className="grid gap-4 lg:grid-cols-3">
        <Skeleton className="h-56 lg:col-span-2" />
        <Skeleton className="h-56" />
      </div>
    </div>
  )
}

function Alerts({ alerts }: { alerts: Schemas['AlertOut'][] }) {
  if (!alerts.length) return null
  return (
    <div className="grid gap-2">
      {alerts.map((a, i) => (
        <Alert key={`${a.code}-${i}`} variant={a.level === 'BLOCKING' ? 'destructive' : 'default'}>
          {a.level === 'BLOCKING' ? <AlertTriangle /> : <CircleAlert />}
          <AlertTitle>{a.level === 'BLOCKING' ? 'Blocking' : 'Warning'} · {a.code.replace(/_/g, ' ').toLowerCase()}</AlertTitle>
          <AlertDescription>
            {a.message}
            {a.session && (
              <Link className="ml-1 underline underline-offset-2" to={`/sessions/${a.session}`}>
                Open {sessionLabel(a.session)}
              </Link>
            )}
          </AlertDescription>
        </Alert>
      ))}
    </div>
  )
}

function Milestone({ label, iso, now, note }: { label: string; iso: string | null | undefined; now: number; note?: string }) {
  const passed = iso ? new Date(iso).getTime() <= now : false
  return (
    <div className={cn('min-w-0 border-l-2 pl-3', passed ? 'border-border text-muted-foreground' : 'border-primary')}>
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="font-heading text-base font-semibold tabular">{timeEat(iso)}</div>
      <div className="text-xs text-muted-foreground">{iso ? relative(iso, now) : note ?? '—'}</div>
    </div>
  )
}

function Count({ label, value, tone }: { label: string; value: number; tone?: 'danger' }) {
  return (
    <div>
      <div className={cn('font-heading text-xl font-semibold tabular', tone === 'danger' && value > 0 && 'text-destructive')}>{value}</div>
      <div className="text-xs text-muted-foreground">{label}</div>
    </div>
  )
}

function NextSession({ session }: { session: Session | null }) {
  const now = useNow()
  if (!session) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>No session with fixtures in the next 14 days</CardTitle>
          <CardDescription>Fixtures appear here once they are loaded for the report competitions.</CardDescription>
        </CardHeader>
      </Card>
    )
  }
  const state = sessionStateInfo(session.report_state)
  return (
    <Card>
      <CardHeader>
        <CardDescription>Next session</CardDescription>
        <CardTitle className="flex flex-wrap items-center gap-2 text-lg">
          {sessionLabel(session.id)} <SessionStateBadge state={session.report_state} />
        </CardTitle>
        <CardAction>
          <Button size="sm" nativeButton={false} render={<Link to={`/sessions/${session.id}`} />}>
            Open <ArrowRight data-icon="inline-end" />
          </Button>
        </CardAction>
      </CardHeader>
      <CardContent className="grid gap-5">
        <p className="text-sm text-muted-foreground">{state.hint}</p>
        <div className="grid grid-cols-3 gap-3">
          <Milestone label="Data cut-off" iso={session.as_of_utc} now={now} />
          <Milestone label="Report ready" iso={session.ready_by_utc} now={now} />
          <Milestone label="First kick-off" iso={session.first_kickoff_utc} now={now} />
        </div>
        <div className="grid grid-cols-4 gap-3 border-t pt-4">
          <Count label="Fixtures" value={session.fixtures} />
          <Count label="Listed" value={session.listed} />
          <Count label="Withdrawn" value={session.withdrawn} tone="danger" />
          <Count label="Paper bets" value={session.paper_bets} />
        </div>
      </CardContent>
    </Card>
  )
}

function NearbySessions({ sessions, className }: { sessions: Session[]; className?: string }) {
  return (
    <Card className={className}>
      <CardHeader>
        <CardTitle>Sessions</CardTitle>
        <CardDescription>Yesterday to tomorrow</CardDescription>
      </CardHeader>
      <CardContent className="px-0">
        <ul className="divide-y">
          {sessions.map((s) => (
            <li key={s.id}>
              <Link
                to={`/sessions/${s.id}`}
                className="flex items-center gap-3 px-4 py-2.5 transition-colors hover:bg-muted/60 focus-visible:bg-muted/60 focus-visible:outline-none"
              >
                <div className="min-w-0 flex-1">
                  <div className="text-sm font-medium">{sessionLabel(s.id)}</div>
                  <div className="text-xs text-muted-foreground tabular">
                    {s.fixtures ? `${s.fixtures} fixtures · ${s.listed} listed` : 'no fixtures'}
                    {s.first_kickoff_utc && ` · first kick-off ${timeEat(s.first_kickoff_utc)}`}
                  </div>
                </div>
                <SessionStateBadge state={s.report_state} />
              </Link>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  )
}

function Recorder({ paper }: { paper: Schemas['DashboardPaperOut'] }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Paper record</CardTitle>
        <CardDescription>The forward test</CardDescription>
        <CardAction>
          <Button variant="ghost" size="sm" nativeButton={false} render={<Link to="/paper" />}>
            Paper betting
          </Button>
        </CardAction>
      </CardHeader>
      <CardContent>
        <dl className="grid gap-3 text-sm">
          <Row label="Record chain">
            {paper.chain_intact ? (
              <span className="inline-flex items-center gap-1 text-success">
                <Link2 className="size-3.5" /> intact
              </span>
            ) : (
              <span className="inline-flex items-center gap-1 text-destructive">
                <Link2Off className="size-3.5" /> broken
              </span>
            )}
          </Row>
          <Row label="Frozen predictions">{paper.frozen}</Row>
          <Row label="Last freeze">{paper.last_freeze_at ? dateTimeEat(paper.last_freeze_at) : 'never'}</Row>
          <Row label="Kicked off, not settled">
            <span className={cn(paper.kicked_off_unsettled > 0 && 'text-warning')}>{paper.kicked_off_unsettled}</span>
          </Row>
        </dl>
      </CardContent>
    </Card>
  )
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="font-medium tabular">{children}</dd>
    </div>
  )
}
