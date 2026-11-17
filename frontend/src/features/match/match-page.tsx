import { useQuery } from '@tanstack/react-query'
import { ArrowLeft, Ban } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link, useLocation, useParams, useSearchParams } from 'react-router'

import { ErrorState } from '@/components/page'
import { DecisionBadge, FixtureStatusBadge, GradeBadge, LineupBadge, Result15, ToneBadge } from '@/components/status'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { RecordBetDialog } from '@/features/bets/record-bet-dialog'
import { ChecksList, FactsPanel, ModelsPanel, OddsPanel, TeamsPanel, type Analysis } from '@/features/match/panels'
import { OddsCheck } from '@/features/match/odds-check'
import type { Schemas } from '@/lib/api'
import { dateTimeEat, num, odds, pct, sessionLabel } from '@/lib/format'
import { analysisQuery, matchQuery, sessionFixturesQuery } from '@/lib/queries'

type Tab = 'checks' | 'models' | 'teams' | 'odds' | 'match'

/** Match analysis (docs/API.md screens 5–7): the verdict, the odds check, and every number behind it. */
export function MatchPage() {
  const { matchId = '' } = useParams()
  const [params] = useSearchParams()
  const match = useQuery(matchQuery(matchId))
  const session = params.get('session') ?? match.data?.session_id ?? null
  const analysis = useQuery({ ...analysisQuery(matchId, session), enabled: Boolean(session) })
  const fixtures = useQuery({ ...sessionFixturesQuery(session ?? ''), enabled: Boolean(session) })
  const entry = fixtures.data?.entries.find((e) => e.match_id === matchId) ?? null
  useScrollToHash(Boolean(analysis.data))

  if (match.isPending) return <MatchSkeleton />
  if (match.isError) return <ErrorState error={match.error} onRetry={() => void match.refetch()} />
  const m = match.data
  const a = analysis.data as Analysis | undefined
  const title = `${m.home_team} v ${m.away_team}`
  const withdrawn = entry?.lineup_status === 'WITHDRAWN'

  return (
    <div className="grid gap-4">
      <Header match={m} session={session} />
      {withdrawn && (
        <div className="flex items-center gap-2 border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          <Ban className="size-4 shrink-0" />
          <strong>Withdrawn by the re-check — never bet this match.</strong>
        </div>
      )}
      {analysis.isPending && <Skeleton className="h-40" />}
      {analysis.isError && <ErrorState error={analysis.error} onRetry={() => void analysis.refetch()} />}
      {a && <Verdict analysis={a} entry={entry} title={title} />}
      {m.status === 'SCHEDULED' && <OddsCheck matchId={matchId} title={title} withdrawn={withdrawn} />}
      {a && <Details analysis={a} entry={entry} match={m} />}
    </div>
  )
}

/** The Bets section links to `#odds`: scroll there once the content above it has loaded. */
function useScrollToHash(ready: boolean) {
  const { hash } = useLocation()
  useEffect(() => {
    if (ready && hash) document.getElementById(hash.slice(1))?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }, [ready, hash])
}

function MatchSkeleton() {
  return (
    <div className="grid gap-4">
      <Skeleton className="h-28" />
      <Skeleton className="h-40" />
      <Skeleton className="h-72" />
    </div>
  )
}

function Header({ match: m, session }: { match: Schemas['MatchOut']; session: string | null }) {
  const played = m.ft_home != null
  return (
    <div className="grid gap-2">
      {session && (
        <Button variant="ghost" size="sm" className="-ml-2 w-fit" nativeButton={false} render={<Link to={`/sessions/${session}`} />}>
          <ArrowLeft /> {sessionLabel(session)}
        </Button>
      )}
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
        <span>{m.competition_name}</span>
        {m.round && <span>· {m.round}</span>}
        <span>· {dateTimeEat(m.kickoff_utc)} EAT</span>
        <FixtureStatusBadge status={m.status} />
      </div>
      <h2 className="font-heading text-xl font-semibold tracking-tight sm:text-2xl">
        {m.home_team} <span className="font-normal text-muted-foreground">v</span> {m.away_team}
      </h2>
      {played && (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm tabular">
          <span>
            FT <strong>{m.ft_home}–{m.ft_away}</strong>
          </span>
          {m.ht_home != null && (
            <span className="text-muted-foreground">
              HT {m.ht_home}–{m.ht_away}
            </span>
          )}
          <Result15 home={m.h15_home} away={m.h15_away} result={m.result_15} />
        </div>
      )}
    </div>
  )
}

function Verdict({ analysis: a, entry, title }: { analysis: Analysis; entry: Schemas['ReportEntryOut'] | null; title: string }) {
  const v = a.verdict
  const [betOpen, setBetOpen] = useState(false)
  return (
    <Card>
      <CardHeader>
        <CardDescription className="flex flex-wrap items-center gap-2">
          {a.mode === 'FROZEN' ? (
            <ToneBadge tone="success">Sealed record</ToneBadge>
          ) : (
            <ToneBadge tone="info">Preview</ToneBadge>
          )}
          <span>
            data up to {dateTimeEat(a.as_of_utc)} · params {a.params_version} · formula {a.formula_version ?? '—'}
          </span>
        </CardDescription>
        <CardTitle className="flex flex-wrap items-center gap-2">
          <DecisionBadge decision={v.decision} />
          <GradeBadge grade={v.grade} />
          {entry && <LineupBadge status={entry.lineup_status} />}
        </CardTitle>
      </CardHeader>
      <CardContent className="grid gap-4">
        {v.error && <p className="text-sm text-destructive">{v.error}</p>}
        <div className="grid grid-cols-2 gap-3">
          <MarketSummary label="Under 0.5 at 15′" p={v.p_under05} fair={v.fair_under05} min={v.min_odds_under05} />
          <MarketSummary label="Draw at 15′" p={v.p_draw} fair={v.fair_draw} min={v.min_odds_draw} />
        </div>
        <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
          <span>n = {v.complete_sample ?? '—'}</span>
          {v.model_spread_pts != null && <span>models within {num(v.model_spread_pts, 1)} pts</span>}
          <Button size="sm" variant="outline" className="ml-auto" onClick={() => setBetOpen(true)}>
            Record a placed bet
          </Button>
        </div>
      </CardContent>
      <RecordBetDialog
        open={betOpen}
        onOpenChange={setBetOpen}
        draft={{ matchId: a.match_id, title, minOdds: { UNDER05: v.min_odds_under05, DRAW: v.min_odds_draw } }}
      />
    </Card>
  )
}

function MarketSummary({ label, p, fair, min }: { label: string; p?: number | null; fair?: number | null; min?: number | null }) {
  return (
    <div className="border bg-muted/30 p-3">
      <div className="text-xs font-medium text-muted-foreground">{label}</div>
      <div className="text-[10px] uppercase tracking-wide text-muted-foreground">Minimum odds</div>
      <div className="font-heading text-2xl font-bold text-success tabular">{odds(min)}</div>
      <div className="text-xs tabular">
        P <span className="font-medium">{pct(p)}</span> <span className="text-muted-foreground">· fair {odds(fair)}</span>
      </div>
    </div>
  )
}

function Details({ analysis, entry, match }: { analysis: Analysis; entry: Schemas['ReportEntryOut'] | null; match: Schemas['MatchOut'] }) {
  const [tab, setTab] = useState<Tab>('checks')
  const failed = analysis.checks.filter((c) => c.passed === false).length
  return (
    <Tabs value={tab} onValueChange={(v) => setTab(v as Tab)}>
      <div className="-mx-4 overflow-x-auto px-4 md:mx-0 md:px-0">
        <TabsList>
          <TabsTrigger value="checks">
            Checks {failed > 0 && <span className="text-destructive tabular">({failed} failed)</span>}
          </TabsTrigger>
          <TabsTrigger value="models">Models</TabsTrigger>
          <TabsTrigger value="teams">Teams</TabsTrigger>
          <TabsTrigger value="odds">Odds</TabsTrigger>
          <TabsTrigger value="match">Match</TabsTrigger>
        </TabsList>
      </div>
      <TabsContent value="checks" className="pt-2">
        <ChecksList checks={analysis.checks} />
      </TabsContent>
      <TabsContent value="models" className="pt-2">
        <ModelsPanel analysis={analysis} entry={entry} />
      </TabsContent>
      <TabsContent value="teams" className="pt-2">
        <TeamsPanel analysis={analysis} match={match} />
      </TabsContent>
      <TabsContent value="odds" className="pt-2">
        <OddsPanel matchId={match.match_id} />
      </TabsContent>
      <TabsContent value="match" className="pt-2">
        <FactsPanel match={match} />
      </TabsContent>
    </Tabs>
  )
}
