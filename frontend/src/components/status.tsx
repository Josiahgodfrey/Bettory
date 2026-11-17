/** Status badges: one look per meaning across every screen. */
import type { ComponentProps } from 'react'

import { Badge } from '@/components/ui/badge'
import { humanize } from '@/lib/format'
import { cn } from '@/lib/utils'

type Tone = 'success' | 'warning' | 'danger' | 'info' | 'muted' | 'primary'

const TONES: Record<Tone, string> = {
  success: 'border-success/30 bg-success/10 text-success',
  warning: 'border-warning/40 bg-warning/10 text-warning',
  danger: 'border-destructive/30 bg-destructive/10 text-destructive',
  info: 'border-border bg-secondary text-secondary-foreground',
  muted: 'border-border bg-transparent text-muted-foreground',
  primary: 'border-transparent bg-primary text-primary-foreground',
}

export function ToneBadge({ tone, className, ...props }: { tone: Tone } & ComponentProps<typeof Badge>) {
  return <Badge variant="outline" className={cn(TONES[tone], className)} {...props} />
}

const SESSION_STATE: Record<string, { tone: Tone; label: string; hint: string }> = {
  PUBLISHED: { tone: 'success', label: 'Published', hint: 'Frozen at the cut-off: the report is final and sealed.' },
  PLANNED: { tone: 'info', label: 'Planned', hint: 'Before the cut-off: the report is a preview and may still change.' },
  AWAITING_FREEZE: { tone: 'warning', label: 'Awaiting freeze', hint: 'Cut-off passed; the recorder freezes it within minutes.' },
  MISSED: { tone: 'danger', label: 'Missed', hint: 'The session ended without a freeze (the recorder was off).' },
  NO_FIXTURES: { tone: 'muted', label: 'No fixtures', hint: 'No fixture of the report competitions in this session.' },
}

export function sessionStateInfo(state: string) {
  return SESSION_STATE[state] ?? { tone: 'muted' as Tone, label: humanize(state), hint: '' }
}

export function SessionStateBadge({ state }: { state: string }) {
  const s = sessionStateInfo(state)
  return (
    <ToneBadge tone={s.tone} title={s.hint}>
      {s.label}
    </ToneBadge>
  )
}

const DECISION: Record<string, { tone: Tone; label: string }> = {
  QUALIFIED: { tone: 'success', label: 'Listed' },
  QUALIFIED_OVER_CAP: { tone: 'warning', label: 'Over the cap' },
  SKIP: { tone: 'muted', label: 'Skipped' },
  WITHDRAWN: { tone: 'danger', label: 'Withdrawn' },
  INSUFFICIENT_DATA: { tone: 'muted', label: 'Not enough data' },
  ERROR: { tone: 'danger', label: 'Error' },
}

export function DecisionBadge({ decision }: { decision: string }) {
  const d = DECISION[decision] ?? { tone: 'muted' as Tone, label: humanize(decision) }
  return <ToneBadge tone={d.tone}>{d.label}</ToneBadge>
}

export function GradeBadge({ grade }: { grade: string }) {
  if (grade === 'SKIP') return null
  return (
    <ToneBadge tone={grade === 'A' ? 'primary' : 'info'} title={`Grade ${grade}`}>
      Grade {grade}
    </ToneBadge>
  )
}

const LINEUP: Record<string, { tone: Tone; label: string; hint: string }> = {
  NOT_CHECKED: { tone: 'muted', label: 'Lineups not checked', hint: 'The re-check runs 30–75 minutes before kick-off.' },
  LINEUPS_PENDING: { tone: 'warning', label: 'Lineups pending', hint: 'Re-checked, but the lineups were not published yet.' },
  CONFIRMED: { tone: 'success', label: 'Confirmed', hint: 'Re-verified with the lineups and fresh odds.' },
  WITHDRAWN: { tone: 'danger', label: 'Withdrawn — do not bet', hint: 'The re-check failed. Never bet this match.' },
}

export function LineupBadge({ status }: { status: string }) {
  const l = LINEUP[status] ?? { tone: 'muted' as Tone, label: humanize(status), hint: '' }
  return (
    <ToneBadge tone={l.tone} title={l.hint}>
      {l.label}
    </ToneBadge>
  )
}

export function FixtureStatusBadge({ status }: { status: string }) {
  if (status === 'SCHEDULED') return null
  const tone: Tone = status === 'LIVE' ? 'warning' : status === 'FINISHED' ? 'info' : 'danger'
  return <ToneBadge tone={tone}>{humanize(status)}</ToneBadge>
}

/** The 15' result of a played match: e.g. "0–0 at 15'" with the two markets' outcomes. */
export function Result15({
  home,
  away,
  result,
}: {
  home: number | null | undefined
  away: number | null | undefined
  result: { under05: boolean | null; draw: boolean | null } | null | undefined
}) {
  if (home == null || away == null) return null
  return (
    <span className="inline-flex items-center gap-1.5 text-xs">
      <span className="font-mono tabular">
        {home}–{away} at 15′
      </span>
      {result?.under05 != null && (
        <ToneBadge tone={result.under05 ? 'success' : 'danger'}>U0.5 {result.under05 ? 'won' : 'lost'}</ToneBadge>
      )}
      {result?.draw != null && (
        <ToneBadge tone={result.draw ? 'success' : 'danger'}>Draw {result.draw ? 'won' : 'lost'}</ToneBadge>
      )}
    </span>
  )
}
