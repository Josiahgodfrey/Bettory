import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, CircleSlash, Clock, Minus, X } from 'lucide-react'
import { useState, type FormEvent } from 'react'

import { ToneBadge } from '@/components/status'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Field, FieldLabel, FieldLegend, FieldSet } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { RecordBetDialog, type BetDraft } from '@/features/bets/record-bet-dialog'
import { api, unwrap, type Schemas } from '@/lib/api'
import { lastBookmaker, MARKET_LABEL, rememberBookmaker, type Market } from '@/lib/bets'
import { checkLabel, readable } from '@/lib/checks'
import { odds, pct } from '@/lib/format'
import { bookmakersQuery } from '@/lib/queries'
import { cn } from '@/lib/utils'

type QuoteCard = Schemas['QuoteCardOut']

/** Odds check (docs/API.md screen 6): the bookmaker's 15′ prices → BET / NO BET / WAIT with reasons. */
export function OddsCheck({ matchId, title, withdrawn }: { matchId: string; title: string; withdrawn: boolean }) {
  const qc = useQueryClient()
  const books = useQuery(bookmakersQuery)
  const [problem, setProblem] = useState<string | null>(null)
  const [betOpen, setBetOpen] = useState(false)

  const check = useMutation({
    mutationFn: (body: Schemas['QuoteIn']) =>
      unwrap(api.POST('/matches/{match_id}/quotes', { params: { path: { match_id: matchId } }, body })),
    onSuccess: (card, body) => {
      rememberBookmaker(body.bookmaker)
      if (body.store) void qc.invalidateQueries({ queryKey: ['match', matchId, 'odds'] })
      void qc.invalidateQueries({ queryKey: ['bookmakers'] })
      document.getElementById('decision-card')?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
      return card
    },
  })

  function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const f = new FormData(e.currentTarget)
    const n = (k: string) => {
      const v = String(f.get(k) ?? '').trim().replace(',', '.')
      return v === '' ? null : Number(v)
    }
    const u = [n('u_yes'), n('u_no')]
    const d = [n('d_home'), n('d_draw'), n('d_away')]
    const complete = (xs: (number | null)[]) => xs.every((x) => x != null)
    const empty = (xs: (number | null)[]) => xs.every((x) => x == null)
    if (!complete(u) && !empty(u)) return setProblem('Under 0.5: enter both prices (Yes and No), or neither.')
    if (!complete(d) && !empty(d)) return setProblem('15′ result: enter all three prices, or none.')
    if (empty(u) && empty(d)) return setProblem('Enter at least one complete market.')
    if ([...u, ...d].some((x) => x != null && !(x > 1))) return setProblem('Every price must be greater than 1.00.')
    setProblem(null)
    check.mutate({
      bookmaker: String(f.get('bookmaker')).trim(),
      under05: complete(u) ? { yes: u[0]!, no: u[1]! } : null,
      draw: complete(d) ? { home: d[0]!, draw: d[1]!, away: d[2]! } : null,
      store: f.get('store') === 'on',
    })
  }

  const card = check.data
  const draft: BetDraft | null = card
    ? {
        matchId,
        title,
        market: (card.selected_market as Market | null) ?? undefined,
        odds: card.selected_odds,
        bookmaker: card.bookmaker,
        minOdds: { UNDER05: card.min_odds_under05, DRAW: card.min_odds_draw },
      }
    : null

  return (
    <div className="grid gap-4 lg:grid-cols-2" id="odds">
      <Card>
        <CardHeader>
          <CardTitle>Odds check</CardTitle>
          <CardDescription>
            Enter the 15-minute prices your bookmaker offers now. Both sides of a market are needed: one side alone cannot show
            the bookmaker’s margin.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={submit} className="grid gap-5">
            <Field>
              <FieldLabel htmlFor="q-book">Bookmaker</FieldLabel>
              <Input id="q-book" name="bookmaker" list="q-books" defaultValue={lastBookmaker()} required maxLength={60} />
              <datalist id="q-books">
                {books.data?.map((b) => <option key={b.id} value={b.name} />)}
              </datalist>
            </Field>
            <FieldSet>
              <FieldLegend variant="label">Goal in minutes 1–15</FieldLegend>
              <div className="grid grid-cols-2 gap-3">
                <PriceInput name="u_yes" label="Yes" />
                <PriceInput name="u_no" label="No (= Under 0.5)" />
              </div>
            </FieldSet>
            <FieldSet>
              <FieldLegend variant="label">Result after 15 minutes</FieldLegend>
              <div className="grid grid-cols-3 gap-3">
                <PriceInput name="d_home" label="Home" />
                <PriceInput name="d_draw" label="Draw" />
                <PriceInput name="d_away" label="Away" />
              </div>
            </FieldSet>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" name="store" defaultChecked className="size-4 accent-primary" />
              Save these prices (kept as manual odds; never changes a sealed report)
            </label>
            {problem && <p className="text-sm text-destructive">{problem}</p>}
            <Button type="submit" disabled={check.isPending}>
              {check.isPending ? 'Checking…' : 'Check odds'}
            </Button>
          </form>
        </CardContent>
      </Card>

      <div id="decision-card">
        {card ? (
          <DecisionCard card={card} withdrawn={withdrawn} onRecord={() => setBetOpen(true)} />
        ) : (
          <Card className="h-full border-dashed bg-transparent">
            <CardHeader>
              <CardTitle className="text-muted-foreground">Decision</CardTitle>
              <CardDescription>The verdict appears here: which market to bet, at which odds, or why not.</CardDescription>
            </CardHeader>
          </Card>
        )}
      </div>
      {draft && <RecordBetDialog draft={draft} open={betOpen} onOpenChange={setBetOpen} />}
    </div>
  )
}

function PriceInput({ name, label }: { name: string; label: string }) {
  return (
    <Field>
      <FieldLabel htmlFor={`q-${name}`} className="text-xs font-normal text-muted-foreground">
        {label}
      </FieldLabel>
      <Input id={`q-${name}`} name={name} inputMode="decimal" placeholder="—" className="font-mono tabular" autoComplete="off" />
    </Field>
  )
}

const ADVICE = {
  BET: { tone: 'success', icon: Check, title: 'Bet' },
  NO_BET: { tone: 'danger', icon: CircleSlash, title: 'No bet' },
  WAIT: { tone: 'warning', icon: Clock, title: 'Wait' },
} as const

function DecisionCard({ card, withdrawn, onRecord }: { card: QuoteCard; withdrawn: boolean; onRecord: () => void }) {
  const a = ADVICE[card.advice as keyof typeof ADVICE] ?? ADVICE.NO_BET
  const quoteChecks = card.checks.filter((c) => c.code.startsWith('QUOTE') || c.layer >= 4)
  return (
    <Card
      className={cn(
        'animate-in fade-in-0 zoom-in-[0.98] duration-200',
        a.tone === 'success' && 'border-success/50',
        a.tone === 'danger' && 'border-destructive/40',
        a.tone === 'warning' && 'border-warning/50',
      )}
      data-testid="decision-card"
    >
      <CardHeader>
        <CardDescription>
          {card.bookmaker} · {card.mode === 'FROZEN' ? 'against the sealed report' : 'preview (report not sealed yet)'}
        </CardDescription>
        <CardTitle
          className={cn(
            'flex items-center gap-2 text-2xl',
            a.tone === 'success' && 'text-success',
            a.tone === 'danger' && 'text-destructive',
            a.tone === 'warning' && 'text-warning',
          )}
        >
          <a.icon className="size-6" /> {a.title}
        </CardTitle>
        <p className="text-sm">{readable(card.reason)}</p>
      </CardHeader>
      <CardContent className="grid gap-4">
        {card.selected_market && (
          <div className="grid grid-cols-3 gap-3 border bg-muted/30 p-3">
            <div className="col-span-3 text-xs text-muted-foreground sm:col-span-1">
              Market
              <div className="text-sm font-semibold text-foreground">{MARKET_LABEL[card.selected_market]}</div>
            </div>
            <div className="text-xs text-muted-foreground">
              Offered
              <div className="font-heading text-lg font-bold text-foreground tabular">{odds(card.selected_odds)}</div>
            </div>
            <div className="text-xs text-muted-foreground">
              Expected value
              <div className="font-heading text-lg font-bold text-foreground tabular">{pct(card.ev)}</div>
            </div>
          </div>
        )}
        <dl className="grid grid-cols-2 gap-3 text-sm">
          <div>
            <dt className="text-xs text-muted-foreground">Under 0.5 · minimum</dt>
            <dd className="font-medium tabular">
              {odds(card.min_odds_under05)} <span className="text-muted-foreground">(P {pct(card.p_under05)})</span>
            </dd>
          </div>
          <div>
            <dt className="text-xs text-muted-foreground">Draw · minimum</dt>
            <dd className="font-medium tabular">
              {odds(card.min_odds_draw)} <span className="text-muted-foreground">(P {pct(card.p_draw)})</span>
            </dd>
          </div>
        </dl>
        {quoteChecks.length > 0 && (
          <ul className="grid gap-1.5 border-t pt-3">
            {quoteChecks.map((c, i) => (
              <li key={`${c.code}-${i}`} className="flex items-start gap-2 text-xs">
                <PassIcon passed={c.passed} />
                <div className="min-w-0">
                  <span className="font-medium">{checkLabel(c.code)}</span>
                  <span className="text-muted-foreground tabular">
                    {' '}
                    · {readable(c.value)} (needs {c.threshold})
                  </span>
                </div>
              </li>
            ))}
          </ul>
        )}
        <div className="flex flex-wrap items-center gap-2 border-t pt-3">
          {card.listed === false && <ToneBadge tone="muted">Not on the report</ToneBadge>}
          <span className="text-xs text-muted-foreground">
            {card.stored_prices > 0 ? `${card.stored_prices} prices saved` : 'prices not saved'}
          </span>
          <Button
            className="ml-auto"
            variant={card.advice === 'BET' ? 'default' : 'outline'}
            onClick={onRecord}
            disabled={withdrawn}
            title={withdrawn ? 'Withdrawn: never bet this match' : undefined}
          >
            {card.advice === 'BET' ? 'Record this bet' : 'Record a bet anyway'}
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}

export function PassIcon({ passed }: { passed: boolean | null | undefined }) {
  if (passed === true) return <Check className="mt-0.5 size-3.5 shrink-0 text-success" aria-label="passed" />
  if (passed === false) return <X className="mt-0.5 size-3.5 shrink-0 text-destructive" aria-label="failed" />
  return <Minus className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" aria-label="not checked" />
}
