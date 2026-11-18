import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Database, Link2, Plus, Search } from 'lucide-react'
import { useEffect, useMemo, useState, type FormEvent } from 'react'
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
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Field, FieldDescription, FieldGroup, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { RunBadge } from '@/features/recorder/recorder-page'
import { api, ApiError, unwrap, type Schemas } from '@/lib/api'
import { dateTimeEat, humanize, relative } from '@/lib/format'
import { dashboardQuery } from '@/lib/queries'

/** The ingestion jobs of the API (app/api/routers/jobs.py), one form each. */
const SOURCES = {
  openfootball: { label: 'openfootball (free, full seasons)', source: 'OPENFOOTBALL', kind: 'season', quota: false },
  'football-data-org': { label: 'football-data.org (free tier)', source: 'FOOTBALL_DATA_ORG', kind: 'season', quota: false },
  'football-data-co-uk': { label: 'football-data.co.uk (odds archive)', source: 'FOOTBALL_DATA_CO_UK', kind: 'season', quota: false },
  'api-football-season': { label: 'API-Football: season fixtures', source: 'API_FOOTBALL', kind: 'season', quota: true },
  'api-football-backfill': { label: 'API-Football: goal-minute backfill', source: 'API_FOOTBALL', kind: 'backfill', quota: true },
  'api-football-day': { label: 'API-Football: one day (fixtures, goals, odds)', source: 'API_FOOTBALL', kind: 'day', quota: true },
} as const
type SourceKey = keyof typeof SOURCES

/** Data loading and team review (docs/API.md screen 14). */
export function DataLoadingPage() {
  const [tracked, setTracked] = useState<string | null>(null)
  return (
    <>
      <PageHeader
        title="Data loading"
        description="Load fixtures, results, goal minutes and odds from the data sources, and resolve the team names the system could not match."
      />
      <div className="grid gap-4">
        <div className="grid gap-4 lg:grid-cols-2">
          <StartLoad onStarted={setTracked} />
          <TrackedRun runId={tracked} />
        </div>
        <Reviews />
      </div>
    </>
  )
}

function StartLoad({ onStarted }: { onStarted: (runId: string) => void }) {
  const qc = useQueryClient()
  const comps = useQuery({ queryKey: ['jobs', 'competitions'], queryFn: () => unwrap(api.GET('/jobs/competitions')), staleTime: 30 * 60_000 })
  const quota = useQuery({ queryKey: ['data-health', 'quota'], queryFn: () => unwrap(api.GET('/data-health/quota')), retry: false })
  const today = useQuery(dashboardQuery).data?.today ?? ''
  const [key, setKey] = useState<SourceKey>('openfootball')
  const src = SOURCES[key]
  const options = useMemo(() => (comps.data ?? []).filter((c) => c.sources.includes(src.source)), [comps.data, src.source])
  const [comp, setComp] = useState<string>('')
  const chosen = options.find((c) => c.code === comp) ?? options[0]
  const [season, setSeason] = useState('')
  const [confirm, setConfirm] = useState<null | (() => void)>(null)
  useEffect(() => setSeason(chosen?.example_season ?? ''), [chosen?.code, chosen?.example_season])

  const start = useMutation({
    mutationFn: async (body: Record<string, unknown>) => {
      const path = `/jobs/ingest/${key}` as '/jobs/ingest/openfootball'
      return unwrap(api.POST(path, { body: body as Schemas['OpenFootballIngest'] }))
    },
    onSuccess: (job) => {
      toast.info(`Started: ${job.job_name}`)
      onStarted(job.run_id)
      void qc.invalidateQueries({ queryKey: ['jobs', 'runs'] })
    },
  })

  function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const f = new FormData(e.currentTarget)
    const on = (n: string) => f.get(n) === 'on'
    const num = (n: string) => (String(f.get(n) ?? '').trim() === '' ? null : Number(f.get(n)))
    let body: Record<string, unknown>
    if (src.kind === 'day') {
      body = { date: String(f.get('date')), max_event_requests: num('events') ?? 20, max_odds_requests: num('odds') ?? 20, reparse: on('reparse') }
    } else {
      body = { competition: chosen?.code, season: season.trim(), reparse: on('reparse') }
      if (key === 'openfootball') body.full = on('full')
      if (src.kind === 'backfill') {
        body.max_requests = num('max_requests')
        body.skip_known_15 = on('skip_known_15')
      }
    }
    const go = () => start.mutate(body)
    if (src.quota) setConfirm(() => go)
    else go()
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Database className="size-4" /> Start a data load
        </CardTitle>
        <CardDescription>
          Runs in the background; results, goal minutes and odds are stored append-only and cross-checked automatically.
        </CardDescription>
      </CardHeader>
      <CardContent>
        {comps.isError ? (
          <ErrorState error={comps.error} />
        ) : (
          <form onSubmit={submit}>
            <FieldGroup>
              <Field>
                <FieldLabel>Source</FieldLabel>
                <Select
                  items={Object.entries(SOURCES).map(([v, s]) => ({ value: v, label: s.label }))}
                  value={key}
                  onValueChange={(v) => v && setKey(v as SourceKey)}
                >
                  <SelectTrigger className="w-full" aria-label="Source">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {Object.entries(SOURCES).map(([v, s]) => (
                      <SelectItem key={v} value={v}>
                        {s.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {src.quota && (
                  <FieldDescription>
                    Uses API-Football requests
                    {quota.data ? ` (${quota.data.used}/${quota.data.limit} used today, ${quota.data.remaining} left)` : quota.isError ? ' (quota unavailable: no key on this backend)' : ''}.
                  </FieldDescription>
                )}
              </Field>
              {src.kind === 'day' ? (
                <>
                  <Field>
                    <FieldLabel htmlFor="ing-date">Date (UTC)</FieldLabel>
                    <Input id="ing-date" name="date" type="date" defaultValue={today} required />
                  </Field>
                  <div className="grid grid-cols-2 gap-3">
                    <Field>
                      <FieldLabel htmlFor="ing-ev">Goal-minute requests</FieldLabel>
                      <Input id="ing-ev" name="events" inputMode="numeric" defaultValue="20" />
                    </Field>
                    <Field>
                      <FieldLabel htmlFor="ing-od">Odds requests</FieldLabel>
                      <Input id="ing-od" name="odds" inputMode="numeric" defaultValue="20" />
                    </Field>
                  </div>
                </>
              ) : (
                <div className="grid grid-cols-2 gap-3">
                  <Field>
                    <FieldLabel>Competition</FieldLabel>
                    <Select
                      items={options.map((c) => ({ value: c.code, label: `${c.code} · ${c.name}` }))}
                      value={chosen?.code ?? null}
                      onValueChange={(v) => v && setComp(String(v))}
                    >
                      <SelectTrigger className="w-full" aria-label="Competition">
                        <SelectValue placeholder={comps.isPending ? 'Loading…' : 'Choose'} />
                      </SelectTrigger>
                      <SelectContent>
                        {options.map((c) => (
                          <SelectItem key={c.code} value={c.code}>
                            {c.code} · {c.name}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </Field>
                  <Field>
                    <FieldLabel htmlFor="ing-season">Season</FieldLabel>
                    <Input id="ing-season" value={season} onChange={(e) => setSeason(e.target.value)} placeholder="2025-26" required pattern="^\d{4}(-\d{2})?$" />
                  </Field>
                </div>
              )}
              {src.kind === 'backfill' && (
                <Field>
                  <FieldLabel htmlFor="ing-max">Request budget (optional)</FieldLabel>
                  <Input id="ing-max" name="max_requests" inputMode="numeric" placeholder="what is left today" />
                </Field>
              )}
              <div className="grid gap-2 text-sm">
                {key === 'openfootball' && <Check name="full" label="Full season file (with goal minutes)" checked />}
                {src.kind === 'backfill' && <Check name="skip_known_15" label="Skip matches whose 15′ score is already known" />}
                <Check name="reparse" label="Re-read payloads already stored (no new requests for them)" />
              </div>
              <Button type="submit" disabled={start.isPending || (src.kind !== 'day' && !chosen)}>
                {start.isPending ? 'Starting…' : 'Start'}
              </Button>
            </FieldGroup>
          </form>
        )}
      </CardContent>
      <AlertDialog open={confirm != null} onOpenChange={(o) => !o && setConfirm(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Use API-Football requests?</AlertDialogTitle>
            <AlertDialogDescription>
              This load spends requests from today’s quota (the quota guard keeps the reserve for the recorder). It stops by itself at its
              budget and can be run again to continue.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <Button
              onClick={() => {
                confirm?.()
                setConfirm(null)
              }}
            >
              Start
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Card>
  )
}

function Check({ name, label, checked }: { name: string; label: string; checked?: boolean }) {
  return (
    <label className="flex items-center gap-2">
      <input type="checkbox" name={name} defaultChecked={checked} className="size-4 accent-primary" />
      {label}
    </label>
  )
}

/** Follows a started run (GET /jobs/runs/{id}) until it finishes. */
function TrackedRun({ runId }: { runId: string | null }) {
  const qc = useQueryClient()
  const q = useQuery({
    queryKey: ['jobs', 'run', runId],
    queryFn: () => unwrap(api.GET('/jobs/runs/{run_id}', { params: { path: { run_id: runId! } } })),
    enabled: Boolean(runId),
    refetchInterval: (query) => (query.state.data?.status === 'RUNNING' ? 2_000 : false),
  })
  const status = q.data?.status
  useEffect(() => {
    if (status && status !== 'RUNNING') {
      void qc.invalidateQueries({ queryKey: ['data-health'] })
      void qc.invalidateQueries({ queryKey: ['jobs', 'runs'] })
      void qc.invalidateQueries({ queryKey: ['reviews'] })
    }
  }, [status, qc])
  return (
    <Card>
      <CardHeader>
        <CardTitle>Run</CardTitle>
        <CardDescription>The load started here, followed live. Every run is also listed on the Recorder screen.</CardDescription>
      </CardHeader>
      <CardContent>
        {!runId ? (
          <p className="text-sm text-muted-foreground">Start a load to follow it here.</p>
        ) : q.isPending ? (
          <Skeleton className="h-24" />
        ) : q.isError ? (
          <ErrorState error={q.error} />
        ) : (
          <div className="grid gap-3 text-sm" data-testid="tracked-run">
            <div className="flex flex-wrap items-center gap-2">
              <RunBadge status={q.data.status} />
              <span className="font-mono text-xs">{q.data.job_name}</span>
            </div>
            <div className="text-xs text-muted-foreground tabular">
              started {dateTimeEat(q.data.started_at)}
              {q.data.finished_at && ` · finished ${relative(q.data.finished_at)}`} · {q.data.requests_used} requests · {q.data.items_stored} items
              stored
            </div>
            {q.data.error && <p className="break-words text-xs text-destructive">{q.data.error}</p>}
            {Object.keys(q.data.stats).length > 0 && (
              <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
                {Object.entries(q.data.stats).map(([k, v]) => (
                  <div key={k} className="flex justify-between gap-2">
                    <dt className="truncate text-muted-foreground">{humanize(k)}</dt>
                    <dd className="font-medium tabular">{v}</dd>
                  </div>
                ))}
              </dl>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

// --- team reviews ---------------------------------------------------------------------------------

function Reviews() {
  const q = useQuery({ queryKey: ['reviews'], queryFn: () => unwrap(api.GET('/reviews')) })
  return (
    <Card>
      <CardHeader>
        <CardTitle>Team names to review</CardTitle>
        <CardDescription>
          A source used a team name the system could not match with certainty. Its matches wait until you link the name to the right team, or
          create the team when it is new to the database (e.g. promoted).
        </CardDescription>
      </CardHeader>
      <CardContent className="px-0">
        {q.isPending ? (
          <Skeleton className="mx-4 h-16" />
        ) : q.isError ? (
          <div className="px-4">
            <ErrorState error={q.error} onRetry={() => void q.refetch()} />
          </div>
        ) : q.data.length === 0 ? (
          <p className="px-4 text-sm text-muted-foreground">Nothing to review.</p>
        ) : (
          <ul className="divide-y">
            {q.data.map((r) => (
              <ReviewRow key={r.id} r={r} />
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}

function ReviewRow({ r }: { r: Schemas['ReviewOut'] }) {
  const [mode, setMode] = useState<'link' | 'new' | null>(null)
  const context = Object.entries(r.context ?? {}).filter(([, v]) => v != null && typeof v !== 'object')
  return (
    <li className="grid gap-2 px-4 py-3" data-testid="review-row">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">“{r.provider_name}”</span>
        <ToneBadge tone="info">{humanize(r.source)}</ToneBadge>
        <span className="text-xs text-muted-foreground">
          {humanize(r.entity_type)} · id {r.provider_key} · {dateTimeEat(r.created_at)}
        </span>
      </div>
      {context.length > 0 && (
        <div className="text-xs text-muted-foreground">{context.map(([k, v]) => `${humanize(k)}: ${String(v)}`).join(' · ')}</div>
      )}
      <div className="flex flex-wrap gap-2">
        <Button size="sm" variant="outline" onClick={() => setMode('link')}>
          <Link2 /> Link to an existing team…
        </Button>
        <Button size="sm" variant="outline" onClick={() => setMode('new')}>
          <Plus /> New team…
        </Button>
      </div>
      {mode === 'link' && <LinkTeamDialog r={r} onClose={() => setMode(null)} />}
      {mode === 'new' && <NewTeamDialog r={r} onClose={() => setMode(null)} />}
    </li>
  )
}

function useResolved(onClose: () => void) {
  const qc = useQueryClient()
  return () => {
    void qc.invalidateQueries({ queryKey: ['reviews'] })
    void qc.invalidateQueries({ queryKey: ['data-health'] })
    onClose()
  }
}

function LinkTeamDialog({ r, onClose }: { r: Schemas['ReviewOut']; onClose: () => void }) {
  const done = useResolved(onClose)
  const [q, setQ] = useState(r.provider_name.split(' ').sort((a, b) => b.length - a.length)[0] ?? '')
  const [debounced, setDebounced] = useState(q)
  useEffect(() => {
    const t = window.setTimeout(() => setDebounced(q.trim()), 250)
    return () => window.clearTimeout(t)
  }, [q])
  const teams = useQuery({
    queryKey: ['teams', debounced],
    queryFn: () => unwrap(api.GET('/teams', { params: { query: { q: debounced || null, limit: 20 } } })),
  })
  const link = useMutation({
    mutationFn: (team: Schemas['TeamOut']) =>
      unwrap(api.POST('/reviews/{review_id}/resolve', { params: { path: { review_id: r.id } }, body: { team_id: team.id } })).then(() => team),
    onSuccess: (team) => {
      toast.success(`“${r.provider_name}” is now ${team.name}`)
      done()
    },
  })
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Link “{r.provider_name}”</DialogTitle>
          <DialogDescription>Choose the team this name means. Its matches are then loaded under that team.</DialogDescription>
        </DialogHeader>
        <div className="relative">
          <Search className="absolute top-2 left-2.5 size-4 text-muted-foreground" />
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search teams" className="pl-8" autoFocus aria-label="Search teams" />
        </div>
        <ul className="max-h-72 divide-y overflow-auto border">
          {teams.isPending ? (
            <li className="p-3 text-sm text-muted-foreground">Searching…</li>
          ) : !teams.data?.length ? (
            <li className="p-3 text-sm text-muted-foreground">No team found. If it is new to the database, use “New team”.</li>
          ) : (
            teams.data.map((t) => (
              <li key={t.id}>
                <button
                  type="button"
                  className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-sm transition-colors hover:bg-muted/60 disabled:opacity-50"
                  onClick={() => link.mutate(t)}
                  disabled={link.isPending}
                >
                  <span className="font-medium">{t.name}</span>
                  <span className="text-xs text-muted-foreground">{t.country}</span>
                </button>
              </li>
            ))
          )}
        </ul>
      </DialogContent>
    </Dialog>
  )
}

function NewTeamDialog({ r, onClose }: { r: Schemas['ReviewOut']; onClose: () => void }) {
  const done = useResolved(onClose)
  // 409 "looks like an existing team" -> offer to create anyway; any other refusal is just explained
  const [refusal, setRefusal] = useState<{ text: string; similar: boolean } | null>(null)
  const create = useMutation({
    mutationFn: (v: { name: string; allow_similar: boolean }) =>
      unwrap(api.POST('/reviews/{review_id}/new-team', { params: { path: { review_id: r.id } }, body: v })),
    onSuccess: (_x, v) => {
      toast.success(`Team “${v.name}” created`)
      done()
    },
    onError: (e) => {
      if (e instanceof ApiError && e.status === 409) setRefusal({ text: e.message, similar: e.message.includes('looks like an existing team') })
    },
  })
  function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const name = String(new FormData(e.currentTarget).get('name') ?? '').trim()
    if (name) create.mutate({ name, allow_similar: Boolean(refusal?.similar) })
  }
  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-md">
        <form onSubmit={submit} className="grid gap-4">
          <DialogHeader>
            <DialogTitle>New team</DialogTitle>
            <DialogDescription>Only for a club that is not in the database yet. Otherwise link the name to the existing team.</DialogDescription>
          </DialogHeader>
          <Field>
            <FieldLabel htmlFor="nt-name">Name</FieldLabel>
            <Input id="nt-name" name="name" defaultValue={r.provider_name} maxLength={80} required autoFocus />
          </Field>
          {refusal && (
            <p className={refusal.similar ? 'text-sm text-warning' : 'text-sm text-destructive'}>
              {refusal.text}
              {refusal.similar && ' If it really is a different club, press “Create anyway”; otherwise cancel and link the name instead.'}
            </p>
          )}
          <DialogFooter>
            <Button type="submit" disabled={create.isPending}>
              {create.isPending ? 'Creating…' : refusal?.similar ? 'Create anyway' : 'Create team'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
