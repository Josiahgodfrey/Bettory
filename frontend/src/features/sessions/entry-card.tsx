import { Ban, FlaskConical, Microscope, Scale } from 'lucide-react'
import { Link } from 'react-router'

import { FixtureStatusBadge, GradeBadge, LineupBadge, Result15 } from '@/components/status'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardFooter, CardHeader } from '@/components/ui/card'
import type { Schemas } from '@/lib/api'
import { checkLabel } from '@/lib/checks'
import { num, odds, pct, timeEat } from '@/lib/format'
import { cn } from '@/lib/utils'

type Entry = Schemas['ReportEntryOut']

/** One listed match of a session report: what to bet and at which minimum odds (docs/API.md §7.2). */
export function EntryCard({ entry, sessionId }: { entry: Entry; sessionId: string }) {
  const f = entry.fixture
  const withdrawn = entry.lineup_status === 'WITHDRAWN'
  const rc = entry.recheck
  const analysis = `/matches/${entry.match_id}?session=${sessionId}`

  return (
    <Card className={cn('gap-0 py-0', withdrawn && 'border-destructive/50')} data-testid="entry-card">
      <CardHeader className="gap-1 border-b py-3">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
          <span className="font-mono font-medium text-foreground tabular">{timeEat(f?.kickoff_utc)}</span>
          <span>·</span>
          <span className="truncate">{f?.competition_name ?? entry.match_id}</span>
          <span className="ml-auto flex flex-wrap items-center gap-1">
            {f && <FixtureStatusBadge status={f.status} />}
            <GradeBadge grade={entry.grade} />
            <LineupBadge status={entry.lineup_status} />
          </span>
        </div>
        <div className="font-heading text-base font-semibold leading-snug">
          {f ? (
            <>
              {f.home_team} <span className="font-normal text-muted-foreground">v</span> {f.away_team}
            </>
          ) : (
            entry.match_id
          )}
        </div>
        {f && <Result15 home={f.h15_home} away={f.h15_away} result={f.result_15} />}
      </CardHeader>

      {withdrawn && (
        <div className="flex items-start gap-2 border-b bg-destructive/10 px-4 py-2.5 text-sm text-destructive">
          <Ban className="mt-0.5 size-4 shrink-0" />
          <div>
            <strong>Withdrawn — never bet this match.</strong>
            {rc && rc.failed_checks.length > 0 && (
              <span className="block text-xs">Failed on re-check: {rc.failed_checks.map(checkLabel).join(' · ')}</span>
            )}
          </div>
        </div>
      )}

      <CardContent className={cn('grid grid-cols-2 gap-2 py-4 sm:gap-3', withdrawn && 'opacity-50')}>
        <Market
          title="Under 0.5 at 15′"
          p={entry.p_under05}
          fair={entry.fair_under05}
          minOdds={entry.min_odds_under05}
          recheckMin={rc?.min_odds_under05}
          bestOdds={entry.best_under05_odds}
          bestBook={entry.best_under05_book}
          withdrawn={withdrawn}
        />
        <Market
          title="Draw at 15′"
          p={entry.p_draw}
          fair={entry.fair_draw}
          minOdds={entry.min_odds_draw}
          recheckMin={rc?.min_odds_draw}
          bestOdds={entry.best_draw_odds}
          bestBook={entry.best_draw_book}
          withdrawn={withdrawn}
        />
      </CardContent>

      <CardFooter className="flex flex-wrap items-center gap-x-4 gap-y-2 border-t py-3 text-xs text-muted-foreground">
        <span title="Smallest exact-15′ sample among the team windows">n = {num(entry.complete_sample)}</span>
        {entry.model_spread_pts != null && (
          <span title="Gap between the models' probabilities">
            <Scale className="mr-1 inline size-3" />
            models within {num(entry.model_spread_pts, 1)} pts
          </span>
        )}
        <span>params {entry.params_version}</span>
        {entry.paper_bet && (
          <span className="inline-flex items-center gap-1 text-foreground" title="The forward test's bet on this match">
            <FlaskConical className="size-3" /> paper: {entry.paper_bet.market === 'UNDER05' ? 'U0.5' : 'Draw'} @{' '}
            {odds(entry.paper_bet.odds)}
          </span>
        )}
        <span className="ml-auto flex gap-2">
          <Button variant="outline" size="sm" nativeButton={false} render={<Link to={analysis} />}>
            <Microscope /> Analysis
          </Button>
          {!withdrawn && (
            <Button size="sm" nativeButton={false} render={<Link to={`${analysis}#odds`} />}>
              Odds check
            </Button>
          )}
        </span>
      </CardFooter>
    </Card>
  )
}

function Market({
  title,
  p,
  fair,
  minOdds,
  recheckMin,
  bestOdds,
  bestBook,
  withdrawn,
}: {
  title: string
  p: number | null | undefined
  fair: number | null | undefined
  minOdds: number | null | undefined
  recheckMin: number | null | undefined
  bestOdds: number | null | undefined
  bestBook: string | null | undefined
  withdrawn: boolean
}) {
  const changed = recheckMin != null && minOdds != null && recheckMin !== minOdds
  return (
    <div className="grid content-start gap-1.5 border bg-muted/30 p-3">
      <div className="text-xs font-medium text-muted-foreground">{title}</div>
      <div>
        <div className="text-[10px] uppercase tracking-wide text-muted-foreground">Minimum odds</div>
        <div
          className={cn(
            'font-heading text-2xl font-bold leading-tight tabular',
            withdrawn ? 'text-muted-foreground line-through' : 'text-success',
          )}
          data-testid="min-odds"
        >
          {odds(minOdds)}
        </div>
      </div>
      <div className="text-xs tabular">
        P <span className="font-medium">{pct(p)}</span>
        <span className="text-muted-foreground"> · fair {odds(fair)}</span>
      </div>
      <div className="text-xs text-muted-foreground tabular">
        {bestOdds != null ? (
          <>
            best seen <span className="font-medium text-foreground">{odds(bestOdds)}</span>
            <span className="block truncate sm:inline"> {bestBook}</span>
          </>
        ) : (
          'no price at the cut-off'
        )}
      </div>
      {changed && <div className="text-xs text-warning tabular">Re-check min {odds(recheckMin)}</div>}
    </div>
  )
}
