import { useMutation, useQuery } from '@tanstack/react-query'
import { CircleCheck, CircleDashed, CircleX, Hourglass, Link2, Link2Off, RotateCcw, ShieldCheck } from 'lucide-react'
import { useState } from 'react'
import { Link } from 'react-router'
import { toast } from 'sonner'

import { CalibrationChart, type CalBucket } from '@/components/charts/calibration-chart'
import { DataTable, EmptyChart } from '@/components/charts/chart-kit'
import { LineChart, TipRow } from '@/components/charts/line-chart'
import { EvidenceMeter, StatTile } from '@/components/charts/stat-tile'
import { ErrorState, PageHeader } from '@/components/page'
import { DecisionBadge, ToneBadge } from '@/components/status'
import { Button } from '@/components/ui/button'
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { api, unwrap } from '@/lib/api'
import { MARKET_SHORT } from '@/lib/bets'
import { dateEat, dateTimeEat, num, odds, pct, sessionLabel } from '@/lib/format'
import { settingsQuery } from '@/lib/queries'

// --- GET /paper/report (open JSON, PAPER-BETTING.md §7) -----------------------------------------------

interface EValue {
  n: number
  value: number
  peak: number
  path?: number[]
}
interface Forecast {
  n: number
  mean_p: number
  actual: number
  brier: number
  log_loss: number
  buckets?: CalBucket[]
}
interface Interval {
  estimate: number
  low: number
  high: number
  sessions: number
}
interface PaperReport {
  params_version: string | null
  frozen: number
  sessions: number
  settled: number
  pending: number
  corrections: number
  chain: { intact: boolean; rows: number; head_pos: number | null; head_hash: string | null }
  gate: { name: string; status: string; detail: string }[]
  forecasts: Record<'UNDER05' | 'DRAW', Forecast | null>
  paired_brier: Interval | null
  bets: {
    settled: number
    wins: number
    voids: number
    pending: number
    staked: number
    profit: number
    roi: number | null
    hit_rate: number | null
    roi_interval: Interval | null
    break_even: number | null
    clv_n: number
    clv_mean: number | null
    clv_beat: number | null
    profit_curve?: { kickoff_utc: string; cumulative: number }[]
  }
  e_profit: EValue
  e_forecast: EValue
  market_gap_pts: number | null
  alpha: number
}

const signed = (v: number | null | undefined, digits = 1) => (v == null ? '—' : `${v > 0 ? '+' : ''}${pct(v, digits)}`)

/** Paper betting — the Gate B forward test (docs/API.md screen 10). */
export function PaperPage() {
  const q = useQuery({
    queryKey: ['paper', 'report'],
    queryFn: async () => (await unwrap(api.GET('/paper/report', { params: { query: {} } }))) as unknown as PaperReport,
  })
  const settings = useQuery(settingsQuery)
  const minN = (settings.data?.gate_b as { calibration?: { min_bucket_matches?: number } } | undefined)?.calibration?.min_bucket_matches ?? 50

  return (
    <>
      <PageHeader
        title="Paper betting"
        description="The forward test: predictions sealed before kick-off, judged on what happened. Nothing here can be edited."
      />
      {q.isPending && (
        <div className="grid gap-4">
          <Skeleton className="h-48" />
          <Skeleton className="h-72" />
        </div>
      )}
      {q.isError && <ErrorState error={q.error} onRetry={() => void q.refetch()} />}
      {q.data && (
        <div className="grid gap-4">
          {q.data.frozen === 0 && <NotStarted />}
          <Gate r={q.data} />
          <Record r={q.data} />
          <Evidence r={q.data} />
          <Calibration r={q.data} minN={minN} />
          <PaperBets r={q.data} />
          <Predictions />
        </div>
      )}
    </>
  )
}

function NotStarted() {
  return (
    <Card className="border-dashed">
      <CardHeader>
        <CardTitle>The forward test has not started</CardTitle>
        <CardDescription>
          Nothing is sealed yet: the recorder freezes each session’s predictions at its cut-off once it runs. The charts
          below fill in as sessions are frozen and settled. <Link className="underline" to="/settings">Settings</Link>
          {' '}shows the recorder schedule.
        </CardDescription>
      </CardHeader>
    </Card>
  )
}

const GATE = {
  PASS: { tone: 'success', icon: CircleCheck, label: 'Pass' },
  FAIL: { tone: 'danger', icon: CircleX, label: 'Fail' },
  IN_PROGRESS: { tone: 'info', icon: Hourglass, label: 'In progress' },
  NOT_EVALUABLE: { tone: 'muted', icon: CircleDashed, label: 'Needs more data' },
} as const

function Gate({ r }: { r: PaperReport }) {
  const passed = r.gate.filter((c) => c.status === 'PASS').length
  return (
    <Card>
      <CardHeader>
        <CardTitle>Gate B — does BETTORY work?</CardTitle>
        <CardDescription>
          {passed} of {r.gate.length} criteria passed. All must pass before real money follows the system with confidence.
        </CardDescription>
      </CardHeader>
      <CardContent className="px-0">
        <ul className="divide-y">
          {r.gate.map((c) => {
            const g = GATE[c.status as keyof typeof GATE] ?? GATE.NOT_EVALUABLE
            return (
              <li key={c.name} className="flex items-start gap-3 px-4 py-3">
                <g.icon className={`mt-0.5 size-4 shrink-0 ${g.tone === 'success' ? 'text-success' : g.tone === 'danger' ? 'text-destructive' : 'text-muted-foreground'}`} />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-medium">{c.name}</span>
                    <ToneBadge tone={g.tone}>{g.label}</ToneBadge>
                  </div>
                  <p className="mt-0.5 break-words text-xs text-muted-foreground">{c.detail}</p>
                </div>
              </li>
            )
          })}
        </ul>
      </CardContent>
    </Card>
  )
}

function Record({ r }: { r: PaperReport }) {
  const chain = useMutation({
    mutationFn: () => unwrap(api.GET('/paper/chain')),
    onSuccess: (c) =>
      c.intact
        ? toast.success(`Record chain verified: ${c.rows} sealed predictions, nothing changed.`)
        : toast.error(`Record chain BROKEN at ${c.problems.length} row(s): ${c.problems.map((p) => `#${p.chain_pos} ${p.problem}`).join('; ')}`, { duration: 20_000 }),
  })
  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      <StatTile label="Sealed predictions" value={num(r.frozen)} detail={`${r.sessions} sessions · params ${r.params_version ?? 'all'}`} />
      <StatTile label="Settled" value={num(r.settled)} detail={`${r.pending} waiting for a result${r.corrections ? ` · ${r.corrections} corrected` : ''}`} />
      <StatTile
        label="Record chain"
        tone={r.chain.intact ? 'good' : 'bad'}
        value={
          <span className="inline-flex items-center gap-2">
            {r.chain.intact ? <Link2 className="size-5" /> : <Link2Off className="size-5" />}
            {r.chain.intact ? 'Intact' : 'Broken'}
          </span>
        }
        detail={
          <Button size="xs" variant="outline" className="mt-1" onClick={() => chain.mutate()} disabled={chain.isPending}>
            <ShieldCheck /> {chain.isPending ? 'Verifying…' : 'Verify now'}
          </Button>
        }
      />
      <Replay />
    </div>
  )
}

/** Recompute a frozen session from the stored data and compare with what was sealed (POST /paper/replay). */
function Replay() {
  const preds = useQuery({
    queryKey: ['paper', 'predictions', 'recent'],
    queryFn: () => unwrap(api.GET('/paper/predictions', { params: { query: { limit: 500 } } })),
  })
  const sessions = [...new Set((preds.data ?? []).map((p) => p.session_id))]
  const [sid, setSid] = useState<string | null>(null)
  const chosen = sid ?? sessions[0] ?? null
  const replay = useMutation({
    mutationFn: (s: string) => unwrap(api.POST('/paper/replay/{session_id}', { params: { path: { session_id: s } } })),
    onSuccess: (x) =>
      x.identical
        ? toast.success(`${sessionLabel(x.session_id)}: recomputed and identical to the sealed record.`)
        : toast.error(`${sessionLabel(x.session_id)}: the recomputation DIFFERS from the sealed record.`, { duration: 20_000 }),
  })
  return (
    <StatTile
      label="Replay a session"
      value={<span className="text-base">{chosen ? sessionLabel(chosen) : 'none sealed'}</span>}
      detail={
        <span className="mt-1 flex flex-wrap items-center gap-1.5">
          {sessions.length > 1 && (
            <Select items={sessions.map((s) => ({ value: s, label: sessionLabel(s) }))} value={chosen} onValueChange={(v) => setSid(v as string)}>
              <SelectTrigger size="sm" aria-label="Session to replay">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {sessions.map((s) => (
                  <SelectItem key={s} value={s}>
                    {sessionLabel(s)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
          <Button size="xs" variant="outline" disabled={!chosen || replay.isPending} onClick={() => chosen && replay.mutate(chosen)}>
            <RotateCcw /> {replay.isPending ? 'Recomputing…' : 'Replay'}
          </Button>
        </span>
      }
    />
  )
}

function EvidenceCard({ title, description, e, bar, unit }: { title: string; description: string; e: EValue; bar: number; unit: string }) {
  const pts = (e.path ?? []).map((v, i) => ({ x: i + 1, y: v })) // series added 2026-11-18: an older API lacks it
  const units = unit === 'match' ? 'matches' : `${unit}s`
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <CardDescription>{description}</CardDescription>
        <CardAction className="text-right">
          <div className="font-heading text-xl font-semibold tabular">{num(e.value, 2)}</div>
          <div className="text-xs text-muted-foreground tabular">peak {num(e.peak, 2)}</div>
        </CardAction>
      </CardHeader>
      <CardContent className="grid gap-4">
        <EvidenceMeter value={e.value} peak={e.peak} bar={bar} />
        {pts.length < 2 ? (
          <EmptyChart height={180}>The evidence line starts after the first settled {units}.</EmptyChart>
        ) : (
          <>
            <LineChart
              height={200}
              logY
              label={`${title}: ${e.n} ${units}, now ${e.value.toFixed(2)}`}
              points={pts}
              refLines={[
                { y: bar, label: `enough evidence (${bar})` },
                { y: 1, label: 'no evidence (1)' },
              ]}
              yFormat={(v) => (v >= 1 ? String(v) : v.toFixed(v >= 0.1 ? 1 : 2))}
              xFormat={(v) => `${Math.round(v)}`}
              tip={(p) => (
                <>
                  <TipRow value={p.y.toFixed(2)} label="e-value" />
                  <div className="mt-1 text-muted-foreground tabular">
                    after {unit} {p.x} · bar {bar}
                  </div>
                </>
              )}
            />
            <DataTable head={[`After ${unit}`, 'e-value']} rows={pts.map((p) => [p.x, p.y.toFixed(3)])} />
          </>
        )}
      </CardContent>
    </Card>
  )
}

function Evidence({ r }: { r: PaperReport }) {
  const bar = Math.round(1 / r.alpha)
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <EvidenceCard
        title="Profit evidence"
        description={`Grows when paper bets win more than their odds imply. Reaching ${bar} (1/alpha) proves an edge however often you look; staying near 1 means no edge yet.`}
        e={r.e_profit}
        bar={bar}
        unit="bet"
      />
      <EvidenceCard
        title="Forecast evidence vs the market"
        description="Grows when BETTORY’s probabilities beat the bookmakers’ de-vigged 15′ prices. Usually gives an earlier signal than profit."
        e={r.e_forecast}
        bar={bar}
        unit="match"
      />
    </div>
  )
}

function Calibration({ r, minN }: { r: PaperReport; minN: number }) {
  const [m, setM] = useState<'UNDER05' | 'DRAW'>('UNDER05')
  const f = r.forecasts[m]
  return (
    <Card>
      <CardHeader>
        <CardTitle>Calibration — are the probabilities right?</CardTitle>
        <CardDescription>
          Each dot groups sealed predictions of similar probability: when BETTORY said ~75%, did it happen ~75% of the time?
          Dots on the grey diagonal are exactly right; hollow dots have fewer than {minN} matches.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <Tabs value={m} onValueChange={(v) => setM(v as 'UNDER05' | 'DRAW')}>
          <TabsList>
            <TabsTrigger value="UNDER05">Under 0.5 at 15′</TabsTrigger>
            <TabsTrigger value="DRAW">Draw at 15′</TabsTrigger>
          </TabsList>
          <TabsContent value={m} className="grid gap-4 pt-3 lg:grid-cols-[1fr_16rem]">
            {!f || !f.buckets?.length ? (
              <EmptyChart height={260}>Calibration appears once sealed predictions are settled.</EmptyChart>
            ) : (
              <div>
                <CalibrationChart buckets={f.buckets} minN={minN} label={`Calibration of ${MARKET_SHORT[m]} forecasts, ${f.n} matches`} />
                <DataTable
                  head={['Bucket', 'Matches', 'Predicted', 'Happened', 'Gap']}
                  rows={f.buckets.map((b) => [`${b.lo_pts}–${b.hi_pts}%`, b.n, pct(b.mean_p), pct(b.actual), `${b.gap_pts > 0 ? '+' : ''}${b.gap_pts.toFixed(1)} pts`])}
                />
              </div>
            )}
            <dl className="grid grid-cols-2 content-start gap-3 text-sm lg:grid-cols-1">
              <Score label="Matches scored" value={f ? num(f.n) : '—'} />
              <Score label="Average prediction" value={f ? pct(f.mean_p) : '—'} />
              <Score label="Actually happened" value={f ? pct(f.actual) : '—'} />
              <Score label="Brier score (lower is better)" value={f ? f.brier.toFixed(4) : '—'} />
              {m === 'UNDER05' && (
                <>
                  <Score
                    label="vs the league-only price"
                    value={r.paired_brier ? `${r.paired_brier.estimate > 0 ? '+' : ''}${r.paired_brier.estimate.toFixed(4)}` : '—'}
                    hint={r.paired_brier ? `range ${r.paired_brier.low.toFixed(4)} to ${r.paired_brier.high.toFixed(4)}; below 0 means BETTORY is better` : undefined}
                  />
                  <Score label="BETTORY vs market" value={r.market_gap_pts != null ? `${r.market_gap_pts > 0 ? '+' : ''}${r.market_gap_pts.toFixed(1)} pts` : '—'} />
                </>
              )}
            </dl>
          </TabsContent>
        </Tabs>
      </CardContent>
    </Card>
  )
}

function Score({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="font-medium tabular">{value}</dd>
      {hint && <dd className="text-xs text-muted-foreground">{hint}</dd>}
    </div>
  )
}

function PaperBets({ r }: { r: PaperReport }) {
  const b = r.bets
  const pts = (b.profit_curve ?? []).map((c, i) => ({ x: i + 1, y: c.cumulative, k: c.kickoff_utc }))
  return (
    <Card>
      <CardHeader>
        <CardTitle>Paper bets</CardTitle>
        <CardDescription>One unit on the market BETTORY would bet, at the best price seen at the cut-off.</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <Mini label="Profit (units)" value={`${b.profit > 0 ? '+' : ''}${b.profit.toFixed(2)}`} detail={`staked ${b.staked.toFixed(0)}`} />
          <Mini
            label="ROI"
            value={signed(b.roi)}
            detail={b.roi_interval ? `${signed(b.roi_interval.low)} to ${signed(b.roi_interval.high)}` : 'needs settled bets'}
          />
          <Mini label="Hit rate vs break-even" value={pct(b.hit_rate)} detail={b.break_even != null ? `needs ${pct(b.break_even)}` : '—'} />
          <Mini label="Closing line value" value={b.clv_n ? signed(b.clv_mean) : '—'} detail={b.clv_n ? `${pct(b.clv_beat, 0)} beat the close` : 'no closing prices yet'} />
        </div>
        {pts.length < 2 ? (
          <EmptyChart height={200}>The paper profit curve starts after the first settled paper bets.</EmptyChart>
        ) : (
          <>
            <LineChart
              height={200}
              label={`Paper profit over ${pts.length} settled bets`}
              points={pts}
              refLines={[{ y: 0, label: 'break-even' }]}
              yFormat={(v) => (Number.isInteger(v) ? String(v) : v.toFixed(1))}
              xFormat={(v) => dateEat(pts[Math.max(0, Math.round(v) - 1)]?.k)}
              tip={(p) => (
                <>
                  <TipRow value={`${p.y > 0 ? '+' : ''}${p.y.toFixed(2)}`} label="units" />
                  <div className="mt-1 text-muted-foreground">after bet {p.x}</div>
                </>
              )}
            />
            <DataTable head={['Bet', 'Kick-off', 'Total (units)']} rows={pts.map((p) => [p.x, dateTimeEat(p.k), p.y.toFixed(2)])} />
          </>
        )}
      </CardContent>
    </Card>
  )
}

/** Small stat inside a card. */
function Mini({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <div className="border bg-muted/30 p-3">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="font-heading text-lg font-semibold">{value}</div>
      <div className="text-xs text-muted-foreground">{detail}</div>
    </div>
  )
}

function Predictions() {
  const q = useQuery({
    queryKey: ['paper', 'predictions', 'recent'],
    queryFn: () => unwrap(api.GET('/paper/predictions', { params: { query: { limit: 500 } } })),
  })
  const listed = (q.data ?? []).filter((p) => p.decision === 'QUALIFIED' || p.paper_bet)
  return (
    <Card>
      <CardHeader>
        <CardTitle>Sealed listed predictions</CardTitle>
        <CardDescription>Newest first. Each row is fixed in the record chain at its position (#).</CardDescription>
      </CardHeader>
      <CardContent className="px-0">
        {q.isPending ? (
          <Skeleton className="mx-4 h-24" />
        ) : listed.length === 0 ? (
          <p className="px-4 text-sm text-muted-foreground">No listed predictions sealed yet.</p>
        ) : (
          <ul className="divide-y">
            {listed.slice(0, 60).map((p) => {
              const f = p.fixture
              const r = f?.result_15
              const won = p.paper_bet ? (p.paper_bet.market === 'UNDER05' ? r?.under05 : r?.draw) : null
              return (
                <li key={p.row_hash}>
                  <Link to={`/matches/${p.match_id}?session=${p.session_id}`} className="flex items-start gap-3 px-4 py-2.5 text-sm transition-colors hover:bg-muted/60">
                    <span className="w-10 pt-0.5 font-mono text-xs text-muted-foreground tabular">#{p.chain_pos}</span>
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-medium">{f ? `${f.home_team} v ${f.away_team}` : p.match_id}</span>
                        <DecisionBadge decision={p.lineup_status === 'WITHDRAWN' ? 'WITHDRAWN' : p.decision} />
                      </div>
                      <div className="text-xs text-muted-foreground tabular">
                        {sessionLabel(p.session_id)} · U0.5 {pct(p.p_under05)} (min {odds(p.min_odds_under05)}) · Draw {pct(p.p_draw)} (min{' '}
                        {odds(p.min_odds_draw)})
                        {p.paper_bet && ` · paper ${MARKET_SHORT[p.paper_bet.market]} @ ${odds(p.paper_bet.odds)}`}
                      </div>
                    </div>
                    {won != null && <ToneBadge tone={won ? 'success' : 'danger'}>{won ? 'Won' : 'Lost'}</ToneBadge>}
                  </Link>
                </li>
              )
            })}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}
