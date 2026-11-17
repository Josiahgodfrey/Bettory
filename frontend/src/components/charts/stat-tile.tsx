import type { ReactNode } from 'react'

import { Card, CardContent } from '@/components/ui/card'
import { cn } from '@/lib/utils'

/** Stat tile: label (sentence case) · value · optional detail line. Proportional figures for the big value. */
export function StatTile({
  label,
  value,
  detail,
  tone,
  className,
}: {
  label: string
  value: ReactNode
  detail?: ReactNode
  tone?: 'good' | 'bad' | null
  className?: string
}) {
  return (
    <Card size="sm" className={cn('gap-1', className)}>
      <CardContent className="grid gap-1">
        <div className="text-xs text-muted-foreground">{label}</div>
        <div
          className={cn(
            'font-heading text-2xl font-semibold leading-tight',
            tone === 'good' && 'text-success',
            tone === 'bad' && 'text-destructive',
          )}
        >
          {value}
        </div>
        {detail && <div className="text-xs text-muted-foreground">{detail}</div>}
      </CardContent>
    </Card>
  )
}

/** Progress of a value towards a bar on a log scale (the e-value: 1 = no evidence, 1/alpha = enough). */
export function EvidenceMeter({ value, peak, bar }: { value: number; peak: number; bar: number }) {
  // position on log10 from 1/bar to bar: below 1 is evidence against, 1 is neutral, bar is the threshold
  const lo = Math.log10(1 / bar)
  const hi = Math.log10(bar)
  const at = (v: number) => Math.min(100, Math.max(0, ((Math.log10(Math.max(v, 1e-9)) - lo) / (hi - lo)) * 100))
  return (
    <div className="grid gap-1">
      <div className="relative h-2 rounded-full bg-[color-mix(in_oklch,var(--chart-3)_18%,transparent)]">
        <div className="absolute inset-y-0 left-0 rounded-full" style={{ width: `${at(value)}%`, background: 'var(--chart-3)' }} />
        <div className="absolute -top-1 h-4 w-px bg-muted-foreground" style={{ left: `${at(peak)}%` }} title={`peak ${peak.toFixed(2)}`} />
        <div className="absolute -top-1 h-4 w-px bg-muted-foreground" style={{ left: '50%' }} title="1 = no evidence either way" />
      </div>
      <div className="flex justify-between text-[10px] text-muted-foreground tabular">
        <span>{(1 / bar).toFixed(2)}</span>
        <span>1</span>
        <span>{bar} = enough</span>
      </div>
    </div>
  )
}
