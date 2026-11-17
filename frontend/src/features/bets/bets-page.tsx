import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, StickyNote } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { Link, useSearchParams } from 'react-router'
import { toast } from 'sonner'

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
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Textarea } from '@/components/ui/textarea'
import { api, unwrap, type Schemas } from '@/lib/api'
import { breachLabel, MARKET_LABEL } from '@/lib/bets'
import { dateTimeEat, money, odds, pct } from '@/lib/format'
import { betsQuery } from '@/lib/queries'
import { cn } from '@/lib/utils'

type Bet = Schemas['BetOut']

const FILTERS = {
  all: { label: 'All', test: () => true },
  open: { label: 'Open', test: (b: Bet) => b.state === 'PENDING' },
  settled: { label: 'Settled', test: (b: Bet) => ['WIN', 'LOSE', 'VOID'].includes(b.state) },
  cancelled: { label: 'Cancelled', test: (b: Bet) => b.state === 'CANCELLED' },
} as const
type Filter = keyof typeof FILTERS

/** My bets (docs/API.md screen 8): every real bet with its settlement, read from the stored 15′ result. */
export function BetsPage() {
  const q = useQuery(betsQuery)
  const [params, setParams] = useSearchParams()
  const filter = (params.get('show') as Filter) in FILTERS ? (params.get('show') as Filter) : 'all'

  return (
    <>
      <PageHeader
        title="My bets"
        description="Recorded bets cannot be edited: cancel a mistake and record it again."
        actions={
          <Button variant="outline" size="sm" nativeButton={false} render={<Link to="/performance" />}>
            Performance
          </Button>
        }
      />
      {q.isPending && (
        <div className="grid gap-3">
          <Skeleton className="h-8 w-72" />
          <Skeleton className="h-32" />
          <Skeleton className="h-32" />
        </div>
      )}
      {q.isError && <ErrorState error={q.error} onRetry={() => void q.refetch()} />}
      {q.data && (
        <div className="grid gap-3">
          <Tabs value={filter} onValueChange={(v) => setParams(v === 'all' ? {} : { show: String(v) }, { replace: true })}>
            <TabsList>
              {(Object.keys(FILTERS) as Filter[]).map((k) => (
                <TabsTrigger key={k} value={k}>
                  {FILTERS[k].label} <span className="text-muted-foreground tabular">{q.data.filter(FILTERS[k].test).length}</span>
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>
          <BetList bets={q.data.filter(FILTERS[filter].test)} empty={q.data.length === 0} />
        </div>
      )}
    </>
  )
}

function BetList({ bets, empty }: { bets: Bet[]; empty: boolean }) {
  if (empty) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>No bets recorded yet</CardTitle>
          <CardDescription>
            Record a bet from a match’s odds check, or from its analysis page.{' '}
            <Link className="underline underline-offset-2" to="/sessions">
              Open the Bets section
            </Link>
            .
          </CardDescription>
        </CardHeader>
      </Card>
    )
  }
  if (!bets.length) return <p className="py-6 text-center text-sm text-muted-foreground">No bets in this view.</p>
  return (
    <div className="grid gap-3">
      {bets.map((b) => (
        <BetCard key={b.id} bet={b} />
      ))}
    </div>
  )
}

const STATE: Record<string, { tone: 'success' | 'danger' | 'muted' | 'info' | 'warning'; label: string }> = {
  PENDING: { tone: 'info', label: 'Open' },
  WIN: { tone: 'success', label: 'Won' },
  LOSE: { tone: 'danger', label: 'Lost' },
  VOID: { tone: 'muted', label: 'Void' },
  CANCELLED: { tone: 'muted', label: 'Cancelled' },
}

function BetCard({ bet: b }: { bet: Bet }) {
  const s = STATE[b.state] ?? { tone: 'muted' as const, label: b.state }
  const cancelled = b.state === 'CANCELLED'
  return (
    <Card className={cn('gap-0 py-0', cancelled && 'opacity-70')} data-testid="bet-card">
      <CardContent className="grid gap-3 py-4">
        <div className="flex flex-wrap items-start gap-x-3 gap-y-1">
          <div className="min-w-0 flex-1">
            <div className="text-xs text-muted-foreground">
              {b.competition_name} · kick-off {dateTimeEat(b.kickoff_utc)}
            </div>
            <Link to={`/matches/${b.match_id}?session=${b.session_id}`} className={cn('font-heading font-semibold hover:underline', cancelled && 'line-through')}>
              {b.home_team} v {b.away_team}
            </Link>
          </div>
          <div className="text-right">
            <ToneBadge tone={s.tone}>{s.label}</ToneBadge>
            {b.profit != null && (
              <div
                className={cn(
                  'mt-1 font-heading text-lg font-semibold tabular',
                  b.profit > 0 && 'text-success',
                  b.profit < 0 && 'text-destructive',
                )}
              >
                {b.profit > 0 ? '+' : ''}
                {money(b.profit, b.currency)}
              </div>
            )}
          </div>
        </div>

        <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm sm:grid-cols-4">
          <Item label="Market">{MARKET_LABEL[b.market] ?? b.market}</Item>
          <Item label="Odds taken">
            {odds(b.odds)} <span className="text-xs text-muted-foreground">min {odds(b.min_odds)}</span>
          </Item>
          <Item label="Stake">{money(b.stake, b.currency)}</Item>
          <Item label="Bookmaker">{b.bookmaker}</Item>
        </dl>

        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-xs text-muted-foreground">
          <span>placed {dateTimeEat(b.placed_at)}</span>
          {b.grade && <span>grade {b.grade}</span>}
          {b.result_15 && b.result_15.under05 != null && (
            <span>
              15′: {b.result_15.under05 ? 'no goal' : 'goal'}
              {b.result_15.draw != null && `, ${b.result_15.draw ? 'level' : 'not level'}`}
            </span>
          )}
          {b.clv && (
            <span title="Closing line value: odds taken against the fair closing price">
              CLV <span className={cn('font-medium', b.clv.clv_fair >= 0 ? 'text-success' : 'text-destructive')}>{pct(b.clv.clv_fair)}</span>
              {' '}(closing fair {odds(b.clv.fair_closing_odds)}, {b.clv.books} books)
            </span>
          )}
        </div>

        {(b.rule_breaches.length > 0 || b.note || cancelled) && (
          <div className="grid gap-1.5 border-t pt-3 text-xs">
            {b.rule_breaches.length > 0 && (
              <div className="flex flex-wrap items-center gap-1.5 text-warning">
                <AlertTriangle className="size-3.5" /> Breaks the rules:
                {b.rule_breaches.map((r) => (
                  <ToneBadge key={r} tone="warning">
                    {breachLabel(r)}
                  </ToneBadge>
                ))}
              </div>
            )}
            {b.note && (
              <div className="flex items-start gap-1.5 text-muted-foreground">
                <StickyNote className="mt-0.5 size-3.5 shrink-0" /> {b.note}
              </div>
            )}
            {cancelled && (
              <div className="text-muted-foreground">
                Cancelled {dateTimeEat(b.cancelled_at)}: {b.cancel_reason}
              </div>
            )}
          </div>
        )}
        {!cancelled && b.state === 'PENDING' && <CancelBet bet={b} />}
        {!cancelled && b.state !== 'PENDING' && <CancelBet bet={b} subtle />}
      </CardContent>
    </Card>
  )
}

function Item({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="truncate font-medium tabular">{children}</dd>
    </div>
  )
}

/** Cancel a bet recorded by mistake (POST /bets/{id}/cancel): append-only, it stays visible as cancelled. */
function CancelBet({ bet, subtle }: { bet: Bet; subtle?: boolean }) {
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const cancel = useMutation({
    mutationFn: (reason: string) =>
      unwrap(api.POST('/bets/{bet_id}/cancel', { params: { path: { bet_id: bet.id } }, body: { reason } })),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['bets'] })
      void qc.invalidateQueries({ queryKey: ['performance'] })
      toast.success('Bet cancelled. It stays in the list as cancelled.')
      setOpen(false)
    },
  })
  function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const reason = String(new FormData(e.currentTarget).get('reason') ?? '').trim()
    if (reason) cancel.mutate(reason)
  }
  return (
    <>
      <div className="flex justify-end">
        <Button variant={subtle ? 'ghost' : 'outline'} size="sm" onClick={() => setOpen(true)}>
          Cancel bet…
        </Button>
      </div>
      <AlertDialog open={open} onOpenChange={setOpen}>
        <AlertDialogContent>
          <form onSubmit={submit} className="grid gap-4">
            <AlertDialogHeader>
              <AlertDialogTitle>Cancel this bet?</AlertDialogTitle>
              <AlertDialogDescription>
                {bet.home_team} v {bet.away_team} · {MARKET_LABEL[bet.market]} @ {odds(bet.odds)}. Only for a bet recorded by
                mistake: it is excluded from performance and stays visible as cancelled. This cannot be undone.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <Textarea name="reason" placeholder="Why? (required)" required maxLength={500} rows={2} autoFocus />
            <AlertDialogFooter>
              <AlertDialogCancel>Keep the bet</AlertDialogCancel>
              <Button type="submit" variant="destructive" disabled={cancel.isPending}>
                {cancel.isPending ? 'Cancelling…' : 'Cancel bet'}
              </Button>
            </AlertDialogFooter>
          </form>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
