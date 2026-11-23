import { useQuery } from '@tanstack/react-query'
import { ArrowLeft, CircleCheck, CircleDashed, CircleX } from 'lucide-react'
import { useState } from 'react'
import { Link, useParams } from 'react-router'

import { BarList } from '@/components/charts/bar-list'
import { CalibrationChart, type CalBucket } from '@/components/charts/calibration-chart'
import { DataTable } from '@/components/charts/chart-kit'
import { StatTile } from '@/components/charts/stat-tile'
import { ErrorState, PageHeader } from '@/components/page'
import { DecisionBadge, ToneBadge } from '@/components/status'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { api, unwrap } from '@/lib/api'
import { checkLabel } from '@/lib/checks'
import { humanize, num, pct } from '@/lib/format'
import { settingsQuery } from '@/lib/queries'

// --- GET /data-health/backtests/{name} (open JSON, BACKTEST-REPORT.md) -----------------------------------

interface Score {
  n: number
  mean_p: number
  actual: number
  brier: number
  log_loss: number
}
interface Roi {
  bets: number
  wins: number
  profit: number
  roi: number | null
  hit_rate: number | null
  break_even: number | null
}
interface Interval {
  estimate: number
  low: number
  high: number
}
type Market = 'UNDER05' | 'DRAW'
interface Backtest {
  params_version: string | null
  formula_version: string | null
  first: string | null
  last: string | null
  split: string | null
  competitions: string[] | null
  fixtures: number | null
  sessions?: unknown[]
  decisions: Record<string, number> | null
  decisions_by_competition?: Record<string, Record<string, number>>
  blocking?: Record<string, number>
  calibration?: Record<Market, Record<string, { score: Score | null; buckets: CalBucket[] }>>
  models?: Record<string, Score | null>
  min_odds_roi?: Record<string, Partial<Record<Market, Roi>>>
  roi_interval?: Record<string, Partial<Record<Market, Interval | null>>>
  gate_b: { name: string; status: string; detail: string }[]
  gate_b_passed: boolean
}

const signed = (v: number | null | undefined) => (v == null ? '—' : `${v > 0 ? '+' : ''}${pct(v)}`)
const GATE = { PASS: CircleCheck, FAIL: CircleX } as Record<string, typeof CircleCheck>

/** One stored backtest or tuning run, in full: Gate B, decisions, what blocked, calibration, models, ROI. */
export function BacktestPage() {
  const { name = '' } = useParams()
  const q = useQuery({
    queryKey: ['data-health', 'backtest', name],
    queryFn: async () => (await unwrap(api.GET('/data-health/backtests/{name}', { params: { path: { name } } }))) as unknown as Backtest,
  })
  const settings = useQuery(settingsQuery)
  const minN = (settings.data?.gate_b as { calibration?: { min_bucket_matches?: number } } | undefined)?.calibration?.min_bucket_matches ?? 50

  return (
    <>
      <Button variant="ghost" size="sm" className="-ml-2 mb-2 w-fit" nativeButton={false} render={<Link to="/data-health" />}>
        <ArrowLeft /> Data health
      </Button>
      <PageHeader title="Backtest" description={<span className="break-all font-mono text-xs">{name}</span>} />
      {q.isPending && <Skeleton className="h-64" />}
      {q.isError && <ErrorState error={q.error} onRetry={() => void q.refetch()} />}
      {q.data && <Detail b={q.data} minN={minN} />}
    </>
  )
}

function Detail({ b, minN }: { b: Backtest; minN: number }) {
  const qualified = b.decisions?.QUALIFIED ?? 0
  return (
    <div className="grid gap-4">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile label="Period" value={<span className="text-base">{b.first ?? '—'} → {b.last ?? '—'}</span>} detail={b.split ? `tuning before ${b.split}, validation after` : ''} />
        <StatTile label="Fixtures" value={num(b.fixtures)} detail={`${(b.competitions ?? []).join(', ')} · ${b.sessions?.length ?? 0} sessions`} />
        <StatTile label="Listed (qualified)" value={num(qualified)} detail={`params ${b.params_version ?? '—'} · formula ${b.formula_version ?? '—'}`} />
        <StatTile label="Gate B" value={b.gate_b_passed ? 'Passed' : 'Not passed'} tone={b.gate_b_passed ? 'good' : 'bad'} detail={`${b.gate_b.filter((g) => g.status === 'PASS').length} of ${b.gate_b.length} criteria`} />
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Gate B criteria</CardTitle>
        </CardHeader>
        <CardContent className="px-0">
          <ul className="divide-y">
            {b.gate_b.map((g) => {
              const Icon = GATE[g.status] ?? CircleDashed
              return (
                <li key={g.name} className="flex items-start gap-3 px-4 py-2.5">
                  <Icon className={`mt-0.5 size-4 shrink-0 ${g.status === 'PASS' ? 'text-success' : g.status === 'FAIL' ? 'text-destructive' : 'text-muted-foreground'}`} />
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2 text-sm font-medium">
                      {g.name}
                      <ToneBadge tone={g.status === 'PASS' ? 'success' : g.status === 'FAIL' ? 'danger' : 'muted'}>{humanize(g.status)}</ToneBadge>
                    </div>
                    <p className="break-words text-xs text-muted-foreground">{g.detail}</p>
                  </div>
                </li>
              )
            })}
          </ul>
        </CardContent>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Decisions b={b} />
        <Blocking b={b} />
      </div>
      <Calibration b={b} minN={minN} />
      <div className="grid gap-4 lg:grid-cols-2">
        <Models b={b} />
        <RoiTable b={b} />
      </div>
    </div>
  )
}

function Decisions({ b }: { b: Backtest }) {
  const by = b.decisions_by_competition ?? {}
  const kinds = Object.keys(b.decisions ?? {})
  return (
    <Card>
      <CardHeader>
        <CardTitle>Decisions</CardTitle>
        <CardDescription>Every fixture of the period, decided point in time as the live system would have.</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3">
        <div className="flex flex-wrap gap-2">
          {kinds.map((k) => (
            <span key={k} className="inline-flex items-center gap-1">
              <DecisionBadge decision={k} /> <span className="text-sm tabular">{b.decisions?.[k]}</span>
            </span>
          ))}
        </div>
        {Object.keys(by).length > 0 && (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>League</TableHead>
                {kinds.map((k) => (
                  <TableHead key={k} className="text-right">
                    {humanize(k)}
                  </TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {Object.entries(by).map(([c, d]) => (
                <TableRow key={c}>
                  <TableCell className="font-mono text-xs">{c}</TableCell>
                  {kinds.map((k) => (
                    <TableCell key={k} className="text-right tabular">
                      {d[k] ?? 0}
                    </TableCell>
                  ))}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  )
}

function Blocking({ b }: { b: Backtest }) {
  const rows = Object.entries(b.blocking ?? {})
    .sort((x, y) => y[1] - x[1])
    .slice(0, 10)
    .map(([code, n]) => ({ key: code, name: checkLabel(code), value: n }))
  return (
    <Card>
      <CardHeader>
        <CardTitle>What blocked fixtures most</CardTitle>
        <CardDescription>Fixtures stopped by each check (one fixture can fail several).</CardDescription>
      </CardHeader>
      <CardContent>
        <BarList rows={rows} format={(v) => num(v)} emptyText="Nothing blocked." />
      </CardContent>
    </Card>
  )
}

function Calibration({ b, minN }: { b: Backtest; minN: number }) {
  const [market, setMarket] = useState<Market>('UNDER05')
  const [set, setSet] = useState('listed')
  const sets = Object.keys(b.calibration?.[market] ?? {})
  const cur = b.calibration?.[market]?.[set] ?? b.calibration?.[market]?.[sets[0]]
  return (
    <Card>
      <CardHeader>
        <CardTitle>Calibration</CardTitle>
        <CardDescription>Predicted against what happened, per 2-point bucket; hollow dots have fewer than {minN} matches.</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3">
        <div className="flex flex-wrap gap-2">
          <Tabs value={market} onValueChange={(v) => setMarket(v as Market)}>
            <TabsList>
              <TabsTrigger value="UNDER05">Under 0.5</TabsTrigger>
              <TabsTrigger value="DRAW">Draw</TabsTrigger>
            </TabsList>
          </Tabs>
          {sets.length > 1 && (
            <Tabs value={set} onValueChange={(v) => setSet(String(v))}>
              <TabsList>
                {sets.map((x) => (
                  <TabsTrigger key={x} value={x}>
                    {x === 'listed' ? 'Listed only' : x === 'priced' ? 'All priced' : humanize(x)}
                  </TabsTrigger>
                ))}
              </TabsList>
            </Tabs>
          )}
        </div>
        {!cur || cur.buckets.length === 0 ? (
          <p className="text-sm text-muted-foreground">No calibration in this run.</p>
        ) : (
          <>
            <CalibrationChart buckets={cur.buckets} minN={minN} label={`Backtest calibration, ${market}`} />
            {cur.score && (
              <p className="text-xs text-muted-foreground tabular">
                {cur.score.n} fixtures · predicted {pct(cur.score.mean_p)} · happened {pct(cur.score.actual)} · Brier {cur.score.brier.toFixed(4)}
              </p>
            )}
            <DataTable
              head={['Bucket', 'Matches', 'Predicted', 'Happened', 'Gap']}
              rows={cur.buckets.map((x) => [`${x.lo_pts}–${x.hi_pts}%`, x.n, pct(x.mean_p), pct(x.actual), `${x.gap_pts > 0 ? '+' : ''}${x.gap_pts.toFixed(1)} pts`])}
            />
          </>
        )}
      </CardContent>
    </Card>
  )
}

function Models({ b }: { b: Backtest }) {
  const rows = Object.entries(b.models ?? {}).filter(([, s]) => s)
  return (
    <Card className="gap-0 pb-0">
      <CardHeader className="pb-4">
        <CardTitle>Models (Under 0.5)</CardTitle>
        <CardDescription>On the same fixtures; lower Brier is better.</CardDescription>
      </CardHeader>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Model</TableHead>
            <TableHead className="text-right">n</TableHead>
            <TableHead className="text-right">Predicted</TableHead>
            <TableHead className="text-right">Happened</TableHead>
            <TableHead className="text-right">Brier</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map(([name, s]) => (
            <TableRow key={name}>
              <TableCell className="font-medium">{name}</TableCell>
              <TableCell className="text-right tabular">{s!.n}</TableCell>
              <TableCell className="text-right tabular">{pct(s!.mean_p)}</TableCell>
              <TableCell className="text-right tabular">{pct(s!.actual)}</TableCell>
              <TableCell className="text-right tabular">{s!.brier.toFixed(4)}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Card>
  )
}

function RoiTable({ b }: { b: Backtest }) {
  const sets = Object.keys(b.min_odds_roi ?? {})
  return (
    <Card className="gap-0 pb-0">
      <CardHeader className="pb-4">
        <CardTitle>Betting at the minimum odds</CardTitle>
        <CardDescription>One unit on every listed fixture at exactly its minimum odds; the range is a session bootstrap.</CardDescription>
      </CardHeader>
      <div className="overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Period</TableHead>
              <TableHead>Market</TableHead>
              <TableHead className="text-right">Bets</TableHead>
              <TableHead className="text-right">Hit / needed</TableHead>
              <TableHead className="text-right">ROI (range)</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {sets.flatMap((set) =>
              (['UNDER05', 'DRAW'] as Market[]).map((m) => {
                const r = b.min_odds_roi?.[set]?.[m]
                const iv = b.roi_interval?.[set]?.[m]
                if (!r) return null
                return (
                  <TableRow key={`${set}-${m}`}>
                    <TableCell>{humanize(set)}</TableCell>
                    <TableCell>{m === 'UNDER05' ? 'Under 0.5' : 'Draw'}</TableCell>
                    <TableCell className="text-right tabular">{r.bets}</TableCell>
                    <TableCell className="text-right tabular">
                      {pct(r.hit_rate, 0)} / {pct(r.break_even, 0)}
                    </TableCell>
                    <TableCell className="text-right tabular">
                      {signed(r.roi)}
                      {iv && (
                        <span className="block text-xs text-muted-foreground">
                          {signed(iv.low)} to {signed(iv.high)}
                        </span>
                      )}
                    </TableCell>
                  </TableRow>
                )
              }),
            )}
          </TableBody>
        </Table>
      </div>
    </Card>
  )
}
