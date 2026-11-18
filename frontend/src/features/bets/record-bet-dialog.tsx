import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router'
import { toast } from 'sonner'

import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Field, FieldDescription, FieldGroup, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { api, unwrap, type Schemas } from '@/lib/api'
import { breachLabel, lastBookmaker, MARKET_LABEL, rememberBookmaker, type Market } from '@/lib/bets'
import { odds as fmtOdds } from '@/lib/format'
import { bookmakersQuery, settingsQuery } from '@/lib/queries'

export interface BetDraft {
  matchId: string
  title: string // "Manchester City v Fulham"
  market?: Market
  odds?: number | null
  bookmaker?: string
  minOdds: { UNDER05: number | null | undefined; DRAW: number | null | undefined }
}

const MARKETS = [
  { value: 'UNDER05', label: MARKET_LABEL.UNDER05 },
  { value: 'DRAW', label: MARKET_LABEL.DRAW },
]

/** Record a real bet (POST /bets). Append-only: a mistake is cancelled in My bets and recorded again (D2). */
export function RecordBetDialog({ draft, open, onOpenChange }: { draft: BetDraft; open: boolean; onOpenChange: (o: boolean) => void }) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        {/* re-mounted on each opening so the form starts from the latest draft */}
        {open && <BetForm draft={draft} onDone={() => onOpenChange(false)} />}
      </DialogContent>
    </Dialog>
  )
}

/** "2026-11-21T15:40" typed in EAT → ISO with the EAT offset (the API stores UTC). */
const eatToIso = (local: string) => `${local}:00+03:00`

function BetForm({ draft, onDone }: { draft: BetDraft; onDone: () => void }) {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const settings = useQuery(settingsQuery)
  const books = useQuery(bookmakersQuery)
  const currency = settings.data?.betting.currency ?? 'TZS'
  const maxStake = settings.data?.betting.max_stake

  const [market, setMarket] = useState<Market>(draft.market ?? 'UNDER05')
  const [oddsText, setOddsText] = useState(draft.odds ? String(draft.odds) : '')
  const [placedNow, setPlacedNow] = useState(true)
  const [problem, setProblem] = useState<string | null>(null)

  const min = draft.minOdds[market]
  const oddsValue = Number(oddsText)
  const belowMin = min != null && oddsText !== '' && oddsValue < min

  const save = useMutation({
    mutationFn: (body: Schemas['BetIn']) => unwrap(api.POST('/bets', { body })),
    onSuccess: (bet) => {
      rememberBookmaker(bet.bookmaker)
      void qc.invalidateQueries({ queryKey: ['bets'] })
      void qc.invalidateQueries({ queryKey: ['performance'] })
      const breaches = bet.rule_breaches.map(breachLabel).join(', ')
      const action = { label: 'My bets', onClick: () => navigate('/bets') }
      if (breaches) toast.warning(`Bet recorded — breaks the rules: ${breaches}`, { action, duration: 10_000 })
      else toast.success(`Bet recorded: ${MARKET_LABEL[bet.market]} @ ${fmtOdds(bet.odds)}`, { action })
      onDone()
    },
  })

  function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const f = new FormData(e.currentTarget)
    const stake = Number(f.get('stake'))
    const placed = String(f.get('placed_at') ?? '')
    if (!(oddsValue > 1)) return setProblem('Odds must be greater than 1.00.')
    if (!(stake > 0)) return setProblem('Enter the stake.')
    if (maxStake != null && stake > maxStake) return setProblem(`A stake above ${maxStake.toLocaleString()} ${currency} looks like a typing error.`)
    if (!placedNow && !placed) return setProblem('Enter when the bet was placed.')
    setProblem(null)
    save.mutate({
      match_id: draft.matchId,
      market,
      odds: oddsValue,
      stake,
      bookmaker: String(f.get('bookmaker')).trim(),
      placed_at: placedNow ? null : eatToIso(placed),
      note: String(f.get('note') ?? '').trim() || null,
    })
  }

  return (
    <form onSubmit={submit}>
      <DialogHeader>
        <DialogTitle>Record a placed bet</DialogTitle>
        <DialogDescription>{draft.title}. Bets cannot be edited; a mistake is cancelled and recorded again.</DialogDescription>
      </DialogHeader>
      <FieldGroup className="py-4">
        <Field>
          <FieldLabel>Market</FieldLabel>
          <Select items={MARKETS} value={market} onValueChange={(v) => v && setMarket(v as Market)}>
            <SelectTrigger className="w-full" aria-label="Market">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {MARKETS.map((m) => (
                <SelectItem key={m.value} value={m.value}>
                  {m.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field>
            <FieldLabel htmlFor="bet-odds">Odds taken</FieldLabel>
            <Input
              id="bet-odds"
              name="odds"
              inputMode="decimal"
              value={oddsText}
              onChange={(e) => setOddsText(e.target.value.replace(',', '.'))}
              aria-invalid={belowMin || undefined}
              required
              autoFocus
            />
            <FieldDescription className="tabular">minimum {fmtOdds(min)}</FieldDescription>
          </Field>
          <Field>
            <FieldLabel htmlFor="bet-stake">Stake ({currency})</FieldLabel>
            <Input id="bet-stake" name="stake" inputMode="numeric" required />
          </Field>
        </div>
        {belowMin && (
          <Alert>
            <AlertTriangle />
            <AlertDescription>
              Below the minimum odds {fmtOdds(min)}. It will be recorded and flagged as breaking the rules.
            </AlertDescription>
          </Alert>
        )}
        <Field>
          <FieldLabel htmlFor="bet-book">Bookmaker</FieldLabel>
          <Input id="bet-book" name="bookmaker" list="bet-books" defaultValue={draft.bookmaker ?? lastBookmaker()} required maxLength={60} />
          <datalist id="bet-books">
            {books.data?.map((b) => <option key={b.id} value={b.name} />)}
          </datalist>
        </Field>
        <Field>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={placedNow} onChange={(e) => setPlacedNow(e.target.checked)} className="size-4 accent-primary" />
            Placed just now
          </label>
          {!placedNow && (
            <>
              <Input name="placed_at" type="datetime-local" aria-label="Placed at (EAT)" />
              <FieldDescription>Time in EAT. It cannot be in the future.</FieldDescription>
            </>
          )}
        </Field>
        <Field>
          <FieldLabel htmlFor="bet-note">Note (optional)</FieldLabel>
          <Textarea id="bet-note" name="note" maxLength={500} rows={2} />
        </Field>
        {problem && <p className="text-sm text-destructive">{problem}</p>}
      </FieldGroup>
      <DialogFooter>
        <Button type="submit" disabled={save.isPending}>
          {save.isPending ? 'Recording…' : 'Record bet'}
        </Button>
      </DialogFooter>
    </form>
  )
}
