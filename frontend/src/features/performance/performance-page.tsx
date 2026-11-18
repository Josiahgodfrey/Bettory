import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router'

import { BarList, type BarRow } from '@/components/charts/bar-list'
import { DataTable, EmptyChart } from '@/components/charts/chart-kit'
import { LineChart, TipRow } from '@/components/charts/line-chart'
import { StatTile } from '@/components/charts/stat-tile'
import { ErrorState, PageHeader } from '@/components/page'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { api, unwrap, type Schemas } from '@/lib/api'
import { MARKET_LABEL } from '@/lib/bets'
import { dateEat, dateTimeEat, money, pct } from '@/lib/format'
import { dashboardQuery, shiftDate } from '@/lib/queries'
import { cn } from '@/lib/utils'

type Roi = Schemas['RoiOut']

const RANGES = {
  all: { label: 'All time', days: null },
  '90': { label: '90 days', days: 90 },
  '30': { label: '30 days', days: 30 },
  '7': { label: '7 days', days: 7 },
} as const
type Range = keyof typeof RANGES

const signedPct = (v: number | null | undefined, digits = 1) => (v == null ? '—' : `${v > 0 ? '+' : ''}${pct(v, digits)}`)
const signedMoney = (v: number, c: string) => `${v > 0 ? '+' : ''}${money(v, c)}`

/** Performance of the real bets (docs/API.md screen 9, GET /performance). Every figure is computed by the API. */
export function PerformancePage() {
  const [params, setParams] = useSearchParams()
  const range = ((params.get('range') ?? 'all') in RANGES ? params.get('range') ?? 'all' : 'all') as Range
  const today = useQuery(dashboardQuery).data?.today
  const days = RANGES[range].days
  const from = days && today ? shiftDate(today, -(days - 1)) : null
  const ready = !days || Boolean(today)

  const q = useQuery({
    queryKey: ['performance', from],
    queryFn: () => unwrap(api.GET('/performance', { params: { query: { from, to: from ? today : null } } })),
    enabled: ready,
    placeholderData: keepPreviousData, // refetch keeps the frame (no layout jump)
  })

  return (
    <>
      <PageHeader title="Performance" description="Your real bets. Cancelled bets are excluded." />
      <Tabs value={range} onValueChange={(v) => setParams(v === 'all' ? {} : { range: String(v) }, { replace: true })}>
        <TabsList className="mb-4">
          {(Object.keys(RANGES) as Range[]).map((k) => (
            <TabsTrigger key={k} value={k}>
              {RANGES[k].label}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>
      {(q.isPending || !ready) && <PerfSkeleton />}
      {q.isError && <ErrorState error={q.error} onRetry={() => void q.refetch()} />}
      {q.data && (
        <div className={cn('grid gap-4 transition-opacity', q.isFetching && q.isPlaceholderData && 'opacity-60')}>
          <Kpis p={q.data} />
          <ProfitCurve p={q.data} />
          <div className="grid gap-4 lg:grid-cols-2">
            <Groups title="By market" groups={q.data.by_market} label={(k) => MARKET_LABEL[k] ?? k} currency={q.data.currency} />
            <Groups
              title="Following the rules"
              description="Bets that broke a rule (not listed, below minimum odds, both markets, late, withdrawn) are kept apart."
              groups={q.data.by_rules}
              label={(k) => (k === 'FOLLOWED' ? 'Followed the rules' : 'Broke a rule')}
              currency={q.data.currency}
            />
            <Groups title="By grade" groups={q.data.by_grade} label={(k) => (k === 'NONE' ? 'No grade' : `Grade ${k}`)} currency={q.data.currency} />
            <Groups title="By league" groups={q.data.by_competition} label={(k) => k} currency={q.data.currency} />
          </div>
        </div>
      )}
    </>
  )
}

function PerfSkeleton() {
  return (
    <div className="grid gap-4">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {[0, 1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-24" />
        ))}
      </div>
      <Skeleton className="h-72" />
    </div>
  )
}

function Kpis({ p }: { p: Schemas['PerformanceOut'] }) {
  const o = p.overall
  const iv = o.roi_interval
  const conf = Math.round(p.confidence * 100)
  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      <StatTile
        label="Profit"
        value={signedMoney(o.profit, p.currency)}
        tone={o.settled ? (o.profit > 0 ? 'good' : o.profit < 0 ? 'bad' : null) : null}
        detail={`staked ${money(o.staked, p.currency)}`}
      />
      <StatTile
        label="Return on stake (ROI)"
        value={signedPct(o.roi)}
        detail={
          iv
            ? `${conf}% range ${signedPct(iv.low)} to ${signedPct(iv.high)}${iv.reliable ? '' : ` · rough until 30 sessions (${iv.sessions})`}`
            : 'needs settled bets'
        }
      />
      <StatTile
        label="Hit rate"
        value={pct(o.hit_rate)}
        tone={o.hit_rate != null && o.break_even != null ? (o.hit_rate >= o.break_even ? 'good' : 'bad') : null}
        detail={o.break_even != null ? `break-even needs ${pct(o.break_even)}` : '—'}
      />
      <StatTile
        label="Settled bets"
        value={o.settled}
        detail={`${o.wins} won · ${o.voids} void · ${o.pending} open${p.cancelled ? ` · ${p.cancelled} cancelled` : ''}`}
      />
      <StatTile
        className="col-span-2 lg:col-span-4"
        label="Closing line value (CLV)"
        value={o.clv_n ? signedPct(o.clv_mean) : '—'}
        detail={
          o.clv_n
            ? `${o.clv_n} bets with a closing price · ${pct(o.clv_beat, 0)} beat the closing price. Beating the closing line is the earliest sign of a real edge.`
            : 'Appears once your bets have closing odds (captured 12 minutes before kick-off).'
        }
      />
    </div>
  )
}

function ProfitCurve({ p }: { p: Schemas['PerformanceOut'] }) {
  const pts = p.profit_curve.map((c, i) => ({ x: i, y: c.cumulative, c }))
  return (
    <Card>
      <CardHeader>
        <CardTitle>Profit over time</CardTitle>
        <CardDescription>Cumulative profit after each settled bet, in kick-off order ({p.currency}).</CardDescription>
      </CardHeader>
      <CardContent>
        {pts.length < 2 ? (
          <EmptyChart height={220}>
            {pts.length === 0 ? (
              <>
                No settled bets yet. <Link className="underline" to="/sessions">Bets section</Link> → odds check → record a bet; the curve
                starts with the first settled one.
              </>
            ) : (
              'The curve appears from the second settled bet.'
            )}
          </EmptyChart>
        ) : (
          <>
            <LineChart
              label={`Cumulative profit over ${pts.length} settled bets`}
              points={pts}
              refLines={[{ y: 0, label: 'break-even' }]}
              yFormat={(v) => compact(v)}
              xFormat={(i) => dateEat(pts[Math.round(i)]?.c.kickoff_utc)}
              tip={(_pt, i) => (
                <>
                  <TipRow value={signedMoney(pts[i].c.cumulative, p.currency)} label="total" />
                  <div className="mt-1 text-muted-foreground tabular">
                    bet {i + 1}: {signedMoney(pts[i].c.profit, p.currency)} · {dateTimeEat(pts[i].c.kickoff_utc)}
                  </div>
                </>
              )}
            />
            <DataTable
              head={['#', 'Kick-off', 'Profit', 'Total']}
              rows={pts.map((x, i) => [i + 1, dateTimeEat(x.c.kickoff_utc), signedMoney(x.c.profit, p.currency), signedMoney(x.c.cumulative, p.currency)])}
            />
          </>
        )}
      </CardContent>
    </Card>
  )
}

/** 12,500 → "12.5K" on an axis. */
function compact(v: number): string {
  return new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 }).format(v)
}

function Groups({
  title,
  description,
  groups,
  label,
  currency,
}: {
  title: string
  description?: string
  groups: Record<string, Roi>
  label: (k: string) => string
  currency: string
}) {
  const rows: BarRow[] = Object.entries(groups)
    .filter(([, g]) => g.settled + g.pending > 0)
    .sort((a, b) => b[1].settled - a[1].settled)
    .map(([k, g]) => ({
      key: k,
      name: label(k),
      value: g.roi,
      low: g.roi_interval?.low,
      high: g.roi_interval?.high,
      note: (
        <span className="tabular">
          {g.settled} settled{g.pending ? `, ${g.pending} open` : ''} · {signedMoney(g.profit, currency)}
          {g.hit_rate != null && ` · hit ${pct(g.hit_rate, 0)} vs ${pct(g.break_even, 0)} needed`}
          {g.clv_n > 0 && ` · CLV ${signedPct(g.clv_mean)}`}
        </span>
      ),
    }))
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <CardDescription>{description ?? 'Return on stake per group; the grey whisker is its uncertainty range.'}</CardDescription>
      </CardHeader>
      <CardContent>
        <BarList rows={rows} format={(v) => signedPct(v)} emptyText="No settled bets in this range." />
      </CardContent>
    </Card>
  )
}
