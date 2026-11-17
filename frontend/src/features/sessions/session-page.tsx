import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronLeft, ChevronRight, Info, Lock, RefreshCw } from 'lucide-react'
import { Link, Navigate, useNavigate, useParams, useSearchParams } from 'react-router'

import { ErrorState, PageHeader } from '@/components/page'
import { Spinner } from '@/components/spinner'
import { DecisionBadge, SessionStateBadge } from '@/components/status'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { EntryCard } from '@/features/sessions/entry-card'
import { FixturesList } from '@/features/sessions/fixtures-list'
import type { Schemas } from '@/lib/api'
import { dateTimeEat, sessionLabel, timeEat } from '@/lib/format'
import { dashboardQuery, sessionReportQuery, sessionsQuery, shiftDate } from '@/lib/queries'

const SESSION_ID = /^\d{4}-\d{2}-\d{2}-(NOON|NIGHT)$/

/** /sessions → the next session with fixtures (or today's noon session). */
export function SessionsIndex() {
  const q = useQuery(dashboardQuery)
  if (q.isPending) return <Spinner label="Finding the next session" />
  if (q.isError) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />
  return <Navigate to={`/sessions/${q.data.next_session?.id ?? `${q.data.today}-NOON`}`} replace />
}

/** The Bets section: one session's report (docs/API.md screen 3) and all its fixtures (screen 4). */
export function SessionPage() {
  const { sessionId = '' } = useParams()
  const [params, setParams] = useSearchParams()
  const tab = params.get('tab') === 'all' ? 'all' : 'listed'

  if (!SESSION_ID.test(sessionId)) {
    return <ErrorState error={new Error(`"${sessionId}" is not a session id (e.g. 2026-11-21-NOON).`)} />
  }
  return (
    <>
      <PageHeader title={sessionLabel(sessionId)} description="Times in EAT" actions={<SessionPicker id={sessionId} />} />
      <Report sessionId={sessionId} tab={tab} onTab={(t) => setParams(t === 'all' ? { tab: 'all' } : {}, { replace: true })} />
    </>
  )
}

/** Session selector: sessions with fixtures from a week before to two weeks after the one shown. */
function SessionPicker({ id }: { id: string }) {
  const navigate = useNavigate()
  const date = id.slice(0, 10)
  const q = useQuery(sessionsQuery(shiftDate(date, -7), shiftDate(date, 14)))
  const withFixtures = (q.data ?? []).filter((s) => s.fixtures > 0 || s.id === id)
  const ids = withFixtures.map((s) => s.id)
  if (!ids.includes(id)) ids.push(id)
  ids.sort(sessionOrder)
  const i = ids.indexOf(id)
  const items = ids.map((s) => ({ value: s, label: sessionLabel(s) }))

  return (
    <div className="flex items-center gap-1">
      <Button
        variant="outline"
        size="icon-sm"
        aria-label="Previous session"
        disabled={i <= 0}
        onClick={() => navigate(`/sessions/${ids[i - 1]}`)}
      >
        <ChevronLeft />
      </Button>
      <Select items={items} value={id} onValueChange={(v) => v && navigate(`/sessions/${v}`)}>
        <SelectTrigger className="min-w-44" aria-label="Session">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {withFixtures.length === 0 && <div className="px-2 py-1.5 text-xs text-muted-foreground">Loading…</div>}
          {ids.map((s) => {
            const info = withFixtures.find((w) => w.id === s)
            return (
              <SelectItem key={s} value={s}>
                {sessionLabel(s)}
                {info && <span className="ml-auto pl-3 text-muted-foreground tabular">{info.fixtures} fx</span>}
              </SelectItem>
            )
          })}
        </SelectContent>
      </Select>
      <Button
        variant="outline"
        size="icon-sm"
        aria-label="Next session"
        disabled={i < 0 || i >= ids.length - 1}
        onClick={() => navigate(`/sessions/${ids[i + 1]}`)}
      >
        <ChevronRight />
      </Button>
    </div>
  )
}

/** NIGHT of a date comes before its NOON (night = 01:00–13:00 EAT, noon = 13:00–01:00 EAT). */
function sessionOrder(a: string, b: string): number {
  const key = (s: string) => `${s.slice(0, 10)}${s.endsWith('NIGHT') ? '0' : '1'}`
  return key(a).localeCompare(key(b))
}

function Report({ sessionId, tab, onTab }: { sessionId: string; tab: string; onTab: (t: string) => void }) {
  const qc = useQueryClient()
  const q = useQuery(sessionReportQuery(sessionId))

  if (q.isPending) return <ReportSkeleton />
  if (q.isError) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />

  const r = q.data
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ['session', sessionId] })
  }
  return (
    <div className="grid gap-4">
      <StateBanner report={r} refreshing={q.isFetching} onRefresh={refresh} />
      <Summary report={r} />
      <Tabs value={tab} onValueChange={(v) => onTab(String(v))}>
        <TabsList>
          <TabsTrigger value="listed">Listed ({r.entries.length})</TabsTrigger>
          <TabsTrigger value="all">All fixtures ({r.session.fixtures})</TabsTrigger>
        </TabsList>
        <TabsContent value="listed" className="grid gap-3 pt-2">
          {r.entries.length > 0 && (
            <p className="flex items-start gap-2 text-sm text-muted-foreground">
              <Info className="mt-0.5 size-4 shrink-0" /> {r.how_to_bet}
            </p>
          )}
          {r.entries.length === 0 ? (
            <EmptyListed report={r} onAll={() => onTab('all')} />
          ) : (
            r.entries.map((e) => <EntryCard key={e.match_id} entry={e} sessionId={sessionId} />)
          )}
        </TabsContent>
        <TabsContent value="all" className="pt-2">
          <FixturesList sessionId={sessionId} />
        </TabsContent>
      </Tabs>
    </div>
  )
}

function ReportSkeleton() {
  return (
    <div className="grid gap-4">
      <Skeleton className="h-16" />
      <Skeleton className="h-10 w-72" />
      <Skeleton className="h-56" />
      <Skeleton className="h-56" />
    </div>
  )
}

function StateBanner({
  report: r,
  refreshing,
  onRefresh,
}: {
  report: Schemas['SessionReportOut']
  refreshing: boolean
  onRefresh: () => void
}) {
  const s = r.session
  if (r.source === 'PREVIEW') {
    return (
      <Alert>
        <Info />
        <AlertTitle className="flex flex-wrap items-center gap-2">
          Preview — not sealed <SessionStateBadge state={s.report_state} />
        </AlertTitle>
        <AlertDescription className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <span>
            Computed {timeEat(r.as_of_utc)} with the data known now; it may still change. The final report is sealed at the
            cut-off {timeEat(s.as_of_utc)} and ready by {timeEat(s.ready_by_utc)}.
          </span>
          <Button size="xs" variant="outline" onClick={onRefresh} disabled={refreshing}>
            <RefreshCw className={refreshing ? 'animate-spin' : undefined} /> Recompute
          </Button>
        </AlertDescription>
      </Alert>
    )
  }
  if (s.report_state === 'PUBLISHED') {
    return (
      <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
        <Lock className="size-4 text-success" />
        <SessionStateBadge state="PUBLISHED" />
        <span>Sealed at the cut-off {dateTimeEat(r.as_of_utc ?? s.as_of_utc)}. This is the record the forward test judges.</span>
      </div>
    )
  }
  if (s.report_state === 'AWAITING_FREEZE' || s.report_state === 'MISSED') {
    return (
      <Alert variant={s.report_state === 'MISSED' ? 'destructive' : 'default'}>
        <Info />
        <AlertTitle className="flex items-center gap-2">
          <SessionStateBadge state={s.report_state} />
        </AlertTitle>
        <AlertDescription>
          {s.report_state === 'MISSED'
            ? 'This session ended without a freeze: the recorder was not running. There is no sealed report.'
            : `The cut-off ${timeEat(s.as_of_utc)} has passed; the recorder freezes the report within minutes.`}
        </AlertDescription>
      </Alert>
    )
  }
  return null
}

function Summary({ report: r }: { report: Schemas['SessionReportOut'] }) {
  const s = r.session
  const decisions = Object.entries(r.decisions as Record<string, number>)
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
      <span className="text-muted-foreground">
        {s.fixtures} fixtures{s.first_kickoff_utc && ` · first kick-off ${timeEat(s.first_kickoff_utc)}`}
      </span>
      <span className="flex flex-wrap gap-1.5">
        {decisions.map(([d, n]) => (
          <span key={d} className="inline-flex items-center gap-1">
            <DecisionBadge decision={d} />
            <span className="text-xs tabular">{n}</span>
          </span>
        ))}
      </span>
    </div>
  )
}

function EmptyListed({ report: r, onAll }: { report: Schemas['SessionReportOut']; onAll: () => void }) {
  const none = r.session.fixtures === 0
  return (
    <Card>
      <CardHeader>
        <CardTitle>{none ? 'No fixtures in this session' : 'No match passed verification'}</CardTitle>
        <CardDescription>
          {none ? (
            <>
              No fixture of the report competitions is played in this session. <Link className="underline" to="/sessions">Go to the next session</Link>.
            </>
          ) : (
            <>
              None of the {r.session.fixtures} fixtures met every rule, so there is nothing to bet.{' '}
              <button type="button" className="underline underline-offset-2" onClick={onAll}>
                See why for each fixture
              </button>
              .
            </>
          )}
        </CardDescription>
      </CardHeader>
    </Card>
  )
}
