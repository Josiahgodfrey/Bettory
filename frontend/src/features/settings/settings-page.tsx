import { useQuery } from '@tanstack/react-query'
import type { ReactNode } from 'react'

import { ErrorState, PageHeader } from '@/components/page'
import { ToneBadge } from '@/components/status'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { useAuth } from '@/features/auth/auth-context'
import { api, unwrap } from '@/lib/api'
import { config } from '@/lib/config'
import { dateTimeEat, humanize, num } from '@/lib/format'
import { bookmakersQuery, settingsQuery } from '@/lib/queries'

type Obj = Record<string, unknown>

/** Settings (docs/API.md screen 15) — read-only: what the system runs with. */
export function SettingsPage() {
  const s = useQuery(settingsQuery)
  const me = useQuery({ queryKey: ['me'], queryFn: () => unwrap(api.GET('/me')) })
  const books = useQuery(bookmakersQuery)
  const health = useQuery({ queryKey: ['health'], queryFn: () => unwrap(api.GET('/health')), refetchInterval: 60_000 })
  const auth = useAuth()

  return (
    <>
      <PageHeader title="Settings" description="Read-only. A change means a new params file or a config change in the repository, reviewed and versioned." />
      {s.isPending && <Skeleton className="h-64" />}
      {s.isError && <ErrorState error={s.error} onRetry={() => void s.refetch()} />}
      {s.data && (
        <div className="grid gap-4 lg:grid-cols-2">
          <Section title="Account and connection">
            <Rows
              rows={[
                ['Signed in as', auth.bypass ? 'local development (no login)' : (auth.email ?? '—')],
                ['Two-factor', me.data ? (me.data.local_bypass ? 'bypassed (this machine only)' : me.data.aal) : '—'],
                ['API', config.apiUrl],
                ['API status', health.isError ? 'unreachable' : (health.data?.status ?? '…')],
                ['Backend environment', humanize(String(s.data.environment))],
                ['API-Football plan', humanize(String(s.data.api_football_plan))],
              ]}
            />
          </Section>
          <Sessions s={s.data} />
          <Betting s={s.data} />
          <GateB s={s.data} />
          <Recorder s={s.data} />
          <Section title="Bookmakers" description="Known to the system; a new name is added when you enter its prices.">
            {books.data?.length ? (
              <ul className="grid gap-2 text-sm">
                {books.data.map((b) => (
                  <li key={b.id} className="flex flex-wrap items-center gap-2">
                    <span className="font-medium">{b.name}</span>
                    {b.is_users_bookmaker && <ToneBadge tone="primary">yours</ToneBadge>}
                    <span className="text-xs text-muted-foreground">
                      15′ ends {b.first15_cutoff} · own goals {b.own_goals_count ? 'count' : 'do not count'}
                      {b.last_odds_at && ` · last odds ${dateTimeEat(b.last_odds_at)}`}
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-muted-foreground">None yet.</p>
            )}
          </Section>
          <Competitions s={s.data} />
          <Params s={s.data} />
        </div>
      )}
    </>
  )
}

function Section({ title, description, children, className }: { title: string; description?: string; children: ReactNode; className?: string }) {
  return (
    <Card className={className}>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        {description && <CardDescription>{description}</CardDescription>}
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  )
}

function Rows({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="grid gap-2 text-sm">
      {rows.map(([k, v]) => (
        <div key={k} className="flex items-baseline justify-between gap-4">
          <dt className="text-muted-foreground">{k}</dt>
          <dd className="min-w-0 truncate text-right font-medium tabular">{v}</dd>
        </div>
      ))}
    </dl>
  )
}

const get = (o: unknown, ...path: string[]): unknown => path.reduce<unknown>((x, k) => (x && typeof x === 'object' ? (x as Obj)[k] : undefined), o)
const str = (v: unknown) => (v == null ? '—' : typeof v === 'number' ? num(v, Number.isInteger(v) ? 0 : 2) : String(v))

function Sessions({ s }: { s: Obj }) {
  const se = get(s, 'params', 'sessions') as Obj | undefined
  return (
    <Section title="Sessions and time" description="All times in EAT; the database stores UTC.">
      <Rows
        rows={[
          ['Time zone', str(s.timezone)],
          ['Night session', `${str(get(se, 'night', 'start'))}–${str(get(se, 'night', 'end'))}`],
          ['Noon session', `${str(get(se, 'noon', 'start'))}–${str(get(se, 'noon', 'end'))}`],
          ['Report lead time', `${str(se?.lead_time_hours)} h before the first kick-off`],
          ['Matches per session (cap)', str(se?.max_matches_per_session)],
          ['Report competitions', ((s.report_competitions as string[]) ?? []).join(', ')],
          ['Tuning plan', str(s.tuning_plan)],
        ]}
      />
    </Section>
  )
}

function Betting({ s }: { s: Obj }) {
  return (
    <Section title="Real bets" description="Owner decisions D1–D3 (docs/API.md §6).">
      <Rows
        rows={[
          ['Currency', str(get(s, 'betting', 'currency'))],
          ['Largest stake accepted', `${str(get(s, 'betting', 'max_stake'))} (typing-error guard)`],
          ['Rule breaches', 'recorded and flagged, never refused'],
          ['Editing', 'none: cancel and record again'],
        ]}
      />
    </Section>
  )
}

function GateB({ s }: { s: Obj }) {
  const pb = get(s, 'gate_b', 'paper_betting') as Obj | undefined
  const cal = get(s, 'gate_b', 'calibration') as Obj | undefined
  return (
    <Section title="Gate B (forward test)" description="alpha and min_bets are proposals until you confirm them.">
      <Rows
        rows={[
          ['alpha (evidence bar = 1/alpha)', <span key="a">{str(pb?.alpha)} <ToneBadge tone="warning">to confirm</ToneBadge></span>],
          ['Minimum settled paper bets', <span key="m">{str(pb?.min_bets)} <ToneBadge tone="warning">to confirm</ToneBadge></span>],
          ['Minimum qualified selections', str(get(s, 'gate_b', 'min_qualified'))],
          ['Calibration buckets', `${str(cal?.bucket_width_pts)} pts wide, judged from ${str(cal?.min_bucket_matches)} matches`],
          ['Calibration tolerance', `${str(cal?.tolerance_pts)} pts`],
          ['Paper stake', `${str(s.stake)} unit`],
        ]}
      />
    </Section>
  )
}

function Recorder({ s }: { s: Obj }) {
  const r = s.recorder as Obj | undefined
  const plan = String(s.api_football_plan ?? 'free')
  return (
    <Section title="Recorder" description="config/paper.yaml">
      <Rows
        rows={[
          ['Runs every', `${str(get(r, 'schedule', 'every_minutes'))} minutes`],
          ['Daily request bound', `${str(get(r, 'daily_max_requests', plan))} (${humanize(plan)} plan)`],
          ['Odds capture', `${str(get(r, 'capture', 'lead_minutes'))} min before the cut-off`],
          ['Lineup re-check', `${str(get(r, 'recheck', 'from_minutes'))}–${str(get(r, 'recheck', 'to_minutes'))} min before kick-off`],
          ['Closing odds', `${str(get(r, 'closing', 'minutes_before'))} min before kick-off`],
          ['Results', `every ${str(get(r, 'results', 'every_hours'))} h`],
        ]}
      />
    </Section>
  )
}

function Competitions({ s }: { s: Obj }) {
  const list = (s.competitions as Obj[]) ?? []
  const report = new Set((s.report_competitions as string[]) ?? [])
  return (
    <Card className="gap-0 pb-0 lg:col-span-2">
      <CardHeader className="pb-4">
        <CardTitle>Competitions</CardTitle>
        <CardDescription>The registry; marked ones are in the session reports.</CardDescription>
      </CardHeader>
      <div className="overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Code</TableHead>
              <TableHead>Name</TableHead>
              <TableHead className="hidden sm:table-cell">Country</TableHead>
              <TableHead>15′ market</TableHead>
              <TableHead className="hidden md:table-cell">Sources</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {list.map((c) => (
              <TableRow key={String(c.code)}>
                <TableCell className="font-mono text-xs">
                  {str(c.code)} {report.has(String(c.code)) && <ToneBadge tone="primary">report</ToneBadge>}
                </TableCell>
                <TableCell>{str(c.name)}</TableCell>
                <TableCell className="hidden sm:table-cell">{str(c.country)}</TableCell>
                <TableCell>{c.fifteen_minute_market ? 'yes' : 'no'}</TableCell>
                <TableCell className="hidden text-xs text-muted-foreground md:table-cell">
                  {Object.keys((c.providers as Obj) ?? {}).map(humanize).join(' · ')}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </Card>
  )
}

/** The whole params file, as a collapsible tree (every rule and threshold the analysis uses). */
function Params({ s }: { s: Obj }) {
  const p = (s.params as Obj) ?? {}
  return (
    <Section
      className="lg:col-span-2"
      title={`Parameters v${str(p.params_version)}`}
      description={`Formula ${str(p.formula_version)} · ${str(s.params_file)}. Every threshold the verification uses.`}
    >
      <div className="grid gap-1">
        {Object.entries(p)
          .filter(([k]) => !['params_version', 'formula_version'].includes(k))
          .map(([k, v]) => (
            <Node key={k} name={k} value={v} depth={0} />
          ))}
      </div>
    </Section>
  )
}

function Node({ name, value, depth }: { name: string; value: unknown; depth: number }) {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    return (
      <details className="group" open={false}>
        <summary className="cursor-pointer select-none py-0.5 text-sm hover:text-foreground">
          <span className="font-medium">{humanize(name)}</span>
        </summary>
        <div className="ml-3 grid gap-0.5 border-l pl-3">
          {Object.entries(value as Obj).map(([k, v]) => (
            <Node key={k} name={k} value={v} depth={depth + 1} />
          ))}
        </div>
      </details>
    )
  }
  return (
    <div className="flex items-baseline justify-between gap-4 py-0.5 text-xs">
      <span className="text-muted-foreground">{name}</span>
      <span className="min-w-0 break-all text-right font-mono tabular">{Array.isArray(value) ? value.join(', ') : str(value)}</span>
    </div>
  )
}
