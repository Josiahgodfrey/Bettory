import { Check, Circle, CircleDashed, X } from 'lucide-react'
import { Link } from 'react-router'

import { ToneBadge } from '@/components/status'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import type { Schemas } from '@/lib/api'
import { dateEat, sessionLabel, timeEat } from '@/lib/format'
import { cn } from '@/lib/utils'

type S = Schemas['SessionScheduleOut']
type StepState = 'done' | 'failed' | 'next' | 'waiting' | 'none'

/**
 * The two sessions of today and tomorrow (ARCHITECTURE.md 12.2-12.4): when the recorder plans each, captures its
 * odds, seals it at the cut-off and must have the report ready — and which of those already happened.
 */
export function SessionSchedule({ sessions, now }: { sessions: S[]; now: string }) {
  const t = new Date(now).getTime()
  return (
    <Card>
      <CardHeader>
        <CardTitle>Session schedule</CardTitle>
        <CardDescription>
          Night session: kick-offs 01:00–12:59 EAT, planned at 19:00 the evening before. Noon session: 13:00–00:59, planned at
          07:00. Times in EAT.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3 sm:grid-cols-2">
        {sessions.map((s) => (
          <SessionCard key={s.id} s={s} now={t} />
        ))}
      </CardContent>
    </Card>
  )
}

function at(iso: string | null | undefined, now: number): boolean {
  return iso != null && new Date(iso).getTime() <= now
}

function SessionCard({ s, now }: { s: S; now: number }) {
  const planState: StepState =
    s.plan_status === 'SUCCEEDED' ? 'done' : s.plan_status === 'FAILED' ? 'failed' : at(s.plan_at_utc, now) ? 'next' : 'waiting'
  const noFixtures = s.plan_status === 'SUCCEEDED' && s.fixtures === 0
  const steps: { label: string; time: string | null | undefined; state: StepState; note?: string }[] = [
    { label: 'Plan: load fixtures', time: s.plan_at_utc, state: planState, note: s.plan_status === 'SUCCEEDED' ? `${s.fixtures} in the report leagues` : undefined },
    { label: 'History: team goal minutes (ESPN)', time: null, ...historyStep(s, now) },
    {
      label: 'Capture odds',
      time: s.capture_from_utc,
      state: !s.capture_from_utc ? 'none' : s.captured ? 'done' : at(s.as_of_utc, now) ? 'failed' : at(s.capture_from_utc, now) ? 'next' : 'waiting',
    },
    {
      label: 'Cut-off: seal predictions',
      time: s.as_of_utc,
      state: !s.as_of_utc ? 'none' : s.frozen > 0 ? 'done' : at(s.as_of_utc, now) ? 'next' : 'waiting',
      note: s.frozen > 0 ? `${s.frozen} sealed` : undefined,
    },
    { label: 'Report ready by', time: s.ready_by_utc, state: !s.ready_by_utc ? 'none' : s.frozen > 0 ? 'done' : 'waiting' },
    { label: 'First kick-off', time: s.first_kickoff_utc, state: !s.first_kickoff_utc ? 'none' : at(s.first_kickoff_utc, now) ? 'done' : 'waiting' },
  ]
  return (
    <div className={cn('grid gap-2 border p-3', s.ended && 'opacity-60')}>
      <div className="flex flex-wrap items-center gap-2">
        <Link to={`/sessions/${s.id}`} className="font-heading text-sm font-semibold hover:underline">
          {sessionLabel(s.id)}
        </Link>
        {s.ended ? (
          <ToneBadge tone="muted">ended</ToneBadge>
        ) : at(s.start_utc, now) ? (
          <ToneBadge tone="info">in progress</ToneBadge>
        ) : (
          <ToneBadge tone="muted">upcoming</ToneBadge>
        )}
        {noFixtures && <ToneBadge tone="muted">no matches</ToneBadge>}
        <span className="ml-auto text-xs text-muted-foreground tabular">deadline {dateEat(s.deadline_utc)} {timeEat(s.deadline_utc)}</span>
      </div>
      <ol className="grid gap-1">
        {steps.map((st) => (
          <li key={st.label} className="flex items-center gap-2 text-xs">
            <StepIcon state={st.state} />
            <span className={cn('flex-1', st.state === 'none' && 'text-muted-foreground')}>{st.label}</span>
            {st.note && <span className="text-muted-foreground">{st.note}</span>}
            <span className="w-24 text-right font-mono tabular">{st.time ? `${dateEat(st.time).split(' ')[0]} ${timeEat(st.time)}` : '—'}</span>
          </li>
        ))}
      </ol>
    </div>
  )
}

/** The ESPN history step (docs/ESPN.md 5): once, after the plan and before the cut-off. */
function historyStep(s: S, now: number): { state: StepState; note?: string } {
  if (!s.history_enabled) return { state: 'none', note: 'off' }
  const extra = [
    s.history_stopped_at_budget ? 'stopped at its request limit' : null,
    s.history_teams_without_link ? `${s.history_teams_without_link} teams not linked` : null,
  ].filter(Boolean)
  if (s.history_status === 'SUCCEEDED')
    return { state: 'done', note: [`${s.history_summaries ?? 0} matches loaded`, ...extra].join(' · ') }
  if (s.history_status === 'FAILED') return { state: 'failed', note: 'failed (retried once)' }
  if (s.history_status === 'RUNNING') return { state: 'next', note: 'running' }
  if (at(s.as_of_utc, now)) return { state: 'none', note: 'not run before the cut-off' }
  if (s.plan_status === 'SUCCEEDED') return { state: s.fixtures > 0 ? 'next' : 'none', note: s.fixtures > 0 ? 'after the plan' : undefined }
  return { state: 'waiting', note: 'after the plan' }
}

function StepIcon({ state }: { state: StepState }) {
  if (state === 'done') return <Check className="size-3.5 shrink-0 text-success" aria-label="done" />
  if (state === 'failed') return <X className="size-3.5 shrink-0 text-destructive" aria-label="missed or failed" />
  if (state === 'next') return <Circle className="size-3.5 shrink-0 fill-primary text-primary" aria-label="due now" />
  return <CircleDashed className="size-3.5 shrink-0 text-muted-foreground" aria-label={state === 'none' ? 'not applicable' : 'later'} />
}
