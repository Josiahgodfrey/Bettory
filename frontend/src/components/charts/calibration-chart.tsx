import { useState } from 'react'

import { GRID, linear, niceTicks, REFERENCE, SERIES, Tooltip, TipRow, useWidth } from '@/components/charts/chart-kit'

export interface CalBucket {
  lo_pts: number
  hi_pts: number
  n: number
  mean_p: number
  actual: number
  gap_pts: number
  se_pts: number
}

const pts = (v: number) => `${v.toFixed(0)}%`

/**
 * Calibration: each bucket's mean predicted probability (x) against how often it actually happened (y). On the grey
 * diagonal the forecasts are exactly right; the whisker is ±1 standard error of the actual rate (from the API).
 * Buckets with fewer than `minN` matches are drawn hollow: too few to judge (config `min_bucket_matches`).
 */
export function CalibrationChart({ buckets, minN, label, height = 260 }: { buckets: CalBucket[]; minN: number; label: string; height?: number }) {
  const [box, width] = useWidth<HTMLDivElement>()
  const [hover, setHover] = useState<number | null>(null)
  const m = { top: 12, right: 16, bottom: 34, left: 44 }
  const w = Math.max(0, width - m.left - m.right)
  const h = height - m.top - m.bottom

  // range in percentage points around the data, the same on both axes so the diagonal is 45°
  const vals = buckets.flatMap((b) => [b.lo_pts, b.hi_pts, b.actual * 100 - b.se_pts, b.actual * 100 + b.se_pts])
  const lo = Math.max(0, Math.floor((Math.min(...vals) - 2) / 5) * 5)
  const hi = Math.min(100, Math.ceil((Math.max(...vals) + 2) / 5) * 5)
  const x = linear(lo, hi, 0, w)
  const y = linear(lo, hi, h, 0)
  const ticks = niceTicks(lo, hi, 4)

  const hb = hover != null ? buckets[hover] : null
  return (
    <div ref={box} className="relative w-full select-none" style={{ height }}>
      {width > 0 && (
        <svg width={width} height={height} role="img" aria-label={label}>
          <g transform={`translate(${m.left},${m.top})`}>
            {ticks.map((t) => (
              <g key={t}>
                <line x1={0} x2={w} y1={y(t)} y2={y(t)} stroke={GRID} />
                <text x={-8} y={y(t)} dy="0.32em" textAnchor="end" className="fill-muted-foreground text-[10px] tabular">
                  {pts(t)}
                </text>
                <text x={x(t)} y={h + 16} textAnchor="middle" className="fill-muted-foreground text-[10px] tabular">
                  {pts(t)}
                </text>
              </g>
            ))}
            <text x={w} y={h + 30} textAnchor="end" className="fill-muted-foreground text-[10px]">
              predicted →
            </text>
            <text x={4} y={-2} className="fill-muted-foreground text-[10px]">
              ↑ happened
            </text>
            <line x1={x(lo)} y1={y(lo)} x2={x(hi)} y2={y(hi)} stroke={REFERENCE} strokeWidth={1} opacity={0.7} />
            {buckets.map((b, i) => {
              const cx = x(b.mean_p * 100)
              const cy = y(b.actual * 100)
              const judged = b.n >= minN
              return (
                <g
                  key={i}
                  onPointerEnter={() => setHover(i)}
                  onPointerDown={() => setHover(i)}
                  onPointerLeave={() => setHover(null)}
                  onFocus={() => setHover(i)}
                  onBlur={() => setHover(null)}
                  tabIndex={0}
                  className="outline-none"
                  aria-label={`predicted ${(b.mean_p * 100).toFixed(1)}%, happened ${(b.actual * 100).toFixed(1)}%, ${b.n} matches`}
                >
                  <line x1={cx} x2={cx} y1={y(b.actual * 100 - b.se_pts)} y2={y(b.actual * 100 + b.se_pts)} stroke={SERIES} strokeWidth={1.5} opacity={0.6} />
                  <circle
                    cx={cx}
                    cy={cy}
                    r={hover === i ? 5.5 : 4.5}
                    fill={judged ? SERIES : 'var(--card)'}
                    stroke={judged ? 'var(--card)' : SERIES}
                    strokeWidth={2}
                  />
                  <circle cx={cx} cy={cy} r={14} fill="transparent" />
                </g>
              )
            })}
          </g>
        </svg>
      )}
      {hb && hover != null && (
        <Tooltip x={m.left + x(hb.mean_p * 100)} y={m.top + y(hb.actual * 100)} width={width}>
          <TipRow value={`${(hb.actual * 100).toFixed(1)}%`} label="happened" />
          <TipRow value={`${(hb.mean_p * 100).toFixed(1)}%`} label="predicted" reference />
          <div className="mt-1 text-muted-foreground tabular">
            {hb.lo_pts}–{hb.hi_pts}% bucket · {hb.n} matches · gap {hb.gap_pts > 0 ? '+' : ''}
            {hb.gap_pts.toFixed(1)} pts
            {hb.n < minN && ' · too few to judge'}
          </div>
        </Tooltip>
      )}
    </div>
  )
}
