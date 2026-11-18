import { useQuery } from '@tanstack/react-query'
import { ChevronRight } from 'lucide-react'
import { Link } from 'react-router'

import { ErrorState } from '@/components/page'
import { DecisionBadge, FixtureStatusBadge, Result15 } from '@/components/status'
import { Card } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import type { Schemas } from '@/lib/api'
import { checkLabel } from '@/lib/checks'
import { pct, timeEat } from '@/lib/format'
import { sessionFixturesQuery } from '@/lib/queries'

const ORDER = ['QUALIFIED', 'QUALIFIED_OVER_CAP', 'WITHDRAWN', 'SKIP', 'INSUFFICIENT_DATA', 'ERROR']

/** Every fixture of the session with its decision and why (docs/API.md screen 4). */
export function FixturesList({ sessionId }: { sessionId: string }) {
  const q = useQuery(sessionFixturesQuery(sessionId))
  if (q.isPending) return <Skeleton className="h-64" />
  if (q.isError) return <ErrorState error={q.error} onRetry={() => void q.refetch()} />

  const entries = [...q.data.entries].sort(
    (a, b) =>
      ORDER.indexOf(a.decision) - ORDER.indexOf(b.decision) ||
      (a.fixture?.kickoff_utc ?? '').localeCompare(b.fixture?.kickoff_utc ?? ''),
  )
  return (
    <div className="grid gap-4">
      <Card className="gap-0 py-0">
        {entries.length === 0 ? (
          <p className="p-4 text-sm text-muted-foreground">No analysed fixtures in this session.</p>
        ) : (
          <ul className="divide-y">
            {entries.map((e) => (
              <FixtureRow key={e.match_id} entry={e} sessionId={sessionId} />
            ))}
          </ul>
        )}
      </Card>
      {q.data.not_analysed.length > 0 && (
        <Card className="gap-0 py-0">
          <div className="border-b px-4 py-2.5 text-sm font-medium">Not analysed ({q.data.not_analysed.length})</div>
          <ul className="divide-y">
            {q.data.not_analysed.map((f) => (
              <li key={f.match_id} className="flex items-center gap-3 px-4 py-2.5 text-sm">
                <span className="w-12 font-mono text-xs tabular">{timeEat(f.kickoff_utc)}</span>
                <span className="min-w-0 flex-1 truncate">
                  {f.home_team} v {f.away_team}
                </span>
                <span className="text-xs text-muted-foreground">{f.competition_name}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  )
}

function FixtureRow({ entry: e, sessionId }: { entry: Schemas['ReportEntryOut']; sessionId: string }) {
  const f = e.fixture
  const failed = e.failed_checks
  return (
    <li>
      <Link
        to={`/matches/${e.match_id}?session=${sessionId}`}
        className="group flex items-start gap-3 px-4 py-3 transition-colors hover:bg-muted/60 focus-visible:bg-muted/60 focus-visible:outline-none"
      >
        <span className="w-12 pt-0.5 font-mono text-xs tabular">{timeEat(f?.kickoff_utc)}</span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className="text-sm font-medium">{f ? `${f.home_team} v ${f.away_team}` : e.match_id}</span>
            <DecisionBadge decision={e.decision} />
            {f && <FixtureStatusBadge status={f.status} />}
          </div>
          <div className="mt-1 text-xs text-muted-foreground">
            {f?.competition_name}
            {e.p_under05 != null && (
              <span className="tabular">
                {' '}
                · U0.5 {pct(e.p_under05)} · Draw {pct(e.p_draw)}
              </span>
            )}
          </div>
          {failed.length > 0 && (
            <div className="mt-1 text-xs text-muted-foreground">
              <span className="text-foreground/80">Why:</span> {failed.slice(0, 3).map(checkLabel).join(' · ')}
              {failed.length > 3 && ` · +${failed.length - 3} more`}
            </div>
          )}
          {e.error && <div className="mt-1 text-xs text-destructive">{e.error}</div>}
          {f && <Result15 home={f.h15_home} away={f.h15_away} result={f.result_15} />}
        </div>
        <ChevronRight className="mt-0.5 size-4 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5" />
      </Link>
    </li>
  )
}
