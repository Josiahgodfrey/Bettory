import { useState, type ReactNode } from 'react'

import { REFERENCE, SERIES } from '@/components/charts/chart-kit'
import { cn } from '@/lib/utils'

export interface BarRow {
  key: string
  name: ReactNode
  value: number | null // e.g. ROI as a fraction
  low?: number | null // interval, same unit
  high?: number | null
  note?: ReactNode // e.g. "12 bets · hit 75% vs 71% needed"
  tip?: ReactNode
}

/**
 * A meter list of one measure per named row, all bars on one shared zero baseline: positive bars grow right, negative
 * left, so the sign reads at a glance. A grey whisker shows the interval when there is one. One series colour.
 */
export function BarList({ rows, format, emptyText }: { rows: BarRow[]; format: (v: number) => string; emptyText: string }) {
  const [hover, setHover] = useState<string | null>(null)
  const vals = rows.flatMap((r) => [r.value, r.low, r.high]).filter((v): v is number => v != null)
  if (!rows.length || !vals.length) return <p className="py-3 text-sm text-muted-foreground">{emptyText}</p>
  const lo = Math.min(0, ...vals)
  const hi = Math.max(0, ...vals)
  const span = hi - lo || 1
  const pos = (v: number) => ((v - lo) / span) * 100 // % of the track
  const zero = pos(0)

  return (
    <ul className="grid gap-3">
      {rows.map((r) => {
        const v = r.value
        return (
          <li
            key={r.key}
            className="grid grid-cols-[minmax(0,7rem)_1fr_auto] items-center gap-x-3 gap-y-0.5 sm:grid-cols-[minmax(0,10rem)_1fr_auto]"
            onPointerEnter={() => setHover(r.key)}
            onPointerLeave={() => setHover(null)}
          >
            <div className="truncate text-sm">{r.name}</div>
            <div className="relative h-5" title={typeof r.tip === 'string' ? r.tip : undefined}>
              <div className="absolute inset-y-2 w-px" style={{ left: `${zero}%`, background: REFERENCE, opacity: 0.6 }} />
              {v != null && (
                <div
                  className={cn('absolute top-1 h-3 transition-opacity', v >= 0 ? 'rounded-r-[4px]' : 'rounded-l-[4px]')}
                  style={{
                    left: `${Math.min(zero, pos(v))}%`,
                    width: `${Math.max(Math.abs(pos(v) - zero), 0.6)}%`,
                    background: SERIES,
                    opacity: hover && hover !== r.key ? 0.45 : 1,
                  }}
                />
              )}
              {r.low != null && r.high != null && (
                <div
                  className="absolute top-1/2 h-px"
                  style={{ left: `${pos(r.low)}%`, width: `${pos(r.high) - pos(r.low)}%`, background: REFERENCE }}
                >
                  <span className="absolute -top-1 left-0 h-2 w-px" style={{ background: REFERENCE }} />
                  <span className="absolute -top-1 right-0 h-2 w-px" style={{ background: REFERENCE }} />
                </div>
              )}
            </div>
            <div className="text-right text-sm font-medium tabular">{v == null ? '—' : format(v)}</div>
            {r.note && <div className="col-span-3 text-xs text-muted-foreground sm:col-start-2 sm:col-span-2">{r.note}</div>}
          </li>
        )
      })}
    </ul>
  )
}
