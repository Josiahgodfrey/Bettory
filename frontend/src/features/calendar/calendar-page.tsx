import { useQuery } from '@tanstack/react-query'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { Link, useSearchParams } from 'react-router'

import { ErrorState, PageHeader } from '@/components/page'
import { DecisionBadge, FixtureStatusBadge, Result15 } from '@/components/status'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { api, unwrap, type Schemas } from '@/lib/api'
import { sessionLabel, timeEat } from '@/lib/format'
import { dashboardQuery, shiftDate } from '@/lib/queries'

const DATE = /^\d{4}-\d{2}-\d{2}$/

/** Calendar (docs/API.md screen 12): every fixture of one EAT date (GET /fixtures?date=). */
export function CalendarPage() {
  const [params, setParams] = useSearchParams()
  const today = useQuery(dashboardQuery).data?.today
  const asked = params.get('date')
  const date = asked && DATE.test(asked) ? asked : today
  const go = (d: string) => setParams({ date: d }, { replace: true })

  const q = useQuery({
    queryKey: ['fixtures', date],
    queryFn: () => unwrap(api.GET('/fixtures', { params: { query: { date: date! } } })),
    enabled: Boolean(date),
  })

  const label = date
    ? new Intl.DateTimeFormat('en-GB', { timeZone: 'UTC', weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' }).format(new Date(`${date}T00:00:00Z`))
    : ''

  return (
    <>
      <PageHeader
        title="Calendar"
        description={label ? `${label} · times in EAT` : 'Times in EAT'}
        actions={
          date && (
            <div className="flex items-center gap-1">
              <Button variant="outline" size="icon-sm" aria-label="Previous day" onClick={() => go(shiftDate(date, -1))}>
                <ChevronLeft />
              </Button>
              <Input type="date" aria-label="Date" value={date} onChange={(e) => DATE.test(e.target.value) && go(e.target.value)} className="h-7 w-36" />
              <Button variant="outline" size="icon-sm" aria-label="Next day" onClick={() => go(shiftDate(date, 1))}>
                <ChevronRight />
              </Button>
              {today && date !== today && (
                <Button variant="ghost" size="sm" onClick={() => go(today)}>
                  Today
                </Button>
              )}
            </div>
          )
        }
      />
      {(q.isPending || !date) && <Skeleton className="h-64" />}
      {q.isError && <ErrorState error={q.error} onRetry={() => void q.refetch()} />}
      {q.data && <Day fixtures={q.data.fixtures} />}
    </>
  )
}

function Day({ fixtures }: { fixtures: Schemas['CalendarFixtureOut'][] }) {
  if (!fixtures.length) {
    return <p className="py-10 text-center text-sm text-muted-foreground">No fixtures loaded for this day.</p>
  }
  const groups = new Map<string, Schemas['CalendarFixtureOut'][]>()
  for (const f of fixtures) groups.set(f.competition_name, [...(groups.get(f.competition_name) ?? []), f])
  return (
    <div className="grid gap-4">
      {[...groups.entries()].map(([name, list]) => (
        <Card key={name} className="gap-0 py-0">
          <div className="flex items-center gap-2 border-b px-4 py-2.5 text-sm font-medium">
            {name} <span className="text-xs font-normal text-muted-foreground">{list[0].competition_country}</span>
          </div>
          <ul className="divide-y">
            {list.map((f) => (
              <li key={f.match_id}>
                <Link
                  to={`/matches/${f.match_id}?session=${f.session_id}`}
                  className="flex items-start gap-3 px-4 py-2.5 transition-colors hover:bg-muted/60"
                >
                  <span className="w-12 pt-0.5 font-mono text-xs tabular">{timeEat(f.kickoff_utc)}</span>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-medium">
                        {f.home_team} v {f.away_team}
                      </span>
                      {f.ft_home != null && (
                        <span className="font-mono text-sm tabular">
                          {f.ft_home}–{f.ft_away}
                        </span>
                      )}
                      <FixtureStatusBadge status={f.status} />
                      {f.decision && <DecisionBadge decision={f.decision} />}
                    </div>
                    <div className="text-xs text-muted-foreground">
                      {sessionLabel(f.session_id)}
                      {f.round && ` · ${f.round}`}
                    </div>
                    <Result15 home={f.h15_home} away={f.h15_away} result={f.result_15} />
                  </div>
                </Link>
              </li>
            ))}
          </ul>
        </Card>
      ))}
    </div>
  )
}
