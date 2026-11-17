import { useId, useState, type KeyboardEvent, type PointerEvent, type ReactNode } from 'react'

import { GRID, linear, niceTicks, REFERENCE, SERIES, Tooltip, TipRow, useWidth } from '@/components/charts/chart-kit'

export interface LinePoint {
  x: number // time in ms, or an index
  y: number
}

export interface RefLine {
  y: number
  label: string
}

/**
 * One series over time: 2px line, a 10% area wash to the baseline, an end dot with a surface ring, grey reference
 * lines (zero, break-even, the evidence bar), and a crosshair that snaps to the nearest point (pointer, touch,
 * and arrow keys).
 */
export function LineChart({
  points,
  height = 220,
  yFormat,
  xFormat,
  tip,
  refLines = [],
  logY = false,
  label,
}: {
  points: LinePoint[]
  height?: number
  yFormat: (v: number) => string
  xFormat: (v: number) => string
  tip: (p: LinePoint, i: number) => ReactNode
  refLines?: RefLine[]
  logY?: boolean
  label: string
}) {
  const [box, width] = useWidth<HTMLDivElement>()
  const [hover, setHover] = useState<number | null>(null)
  const clipId = useId()

  const m = { top: 12, right: 16, bottom: 26, left: 52 }
  const w = Math.max(0, width - m.left - m.right)
  const h = height - m.top - m.bottom
  const tf = (v: number) => (logY ? Math.log10(Math.max(v, 1e-6)) : v)

  const ys = [...points.map((p) => p.y), ...refLines.map((r) => r.y)]
  let lo = Math.min(...ys)
  let hi = Math.max(...ys)
  if (!logY) {
    lo = Math.min(lo, 0)
    hi = Math.max(hi, 0)
  }
  const pad = (tf(hi) - tf(lo)) * 0.08 || 1
  const y = linear(tf(lo) - pad, tf(hi) + pad, h, 0)
  const sy = (v: number) => y(tf(v))
  const x0 = points[0]?.x ?? 0
  const x1 = points.at(-1)?.x ?? 1
  const x = linear(x0, x1 === x0 ? x0 + 1 : x1, 0, w)

  // only ticks inside the plotted range (a label outside it would land on neighbouring content)
  const inRange = (t: number) => tf(t) >= tf(lo) - pad - 1e-9 && tf(t) <= tf(hi) + pad + 1e-9
  const ticks = (
    logY
      ? niceTicks(Math.floor(tf(lo) - pad), Math.ceil(tf(hi) + pad), 4).filter((t) => Number.isInteger(t)).map((t) => 10 ** t)
      : niceTicks(lo, hi, 4)
  ).filter(inRange)
  const xTicks = points.length > 1 ? [x0, x0 + (x1 - x0) / 2, x1] : [x0]

  const path = points.map((p, i) => `${i ? 'L' : 'M'}${x(p.x).toFixed(1)},${sy(p.y).toFixed(1)}`).join('')
  const base = logY ? sy(1) : sy(0)
  const area = points.length > 1 ? `${path}L${x(x1).toFixed(1)},${base.toFixed(1)}L${x(x0).toFixed(1)},${base.toFixed(1)}Z` : ''

  function nearest(px: number) {
    let best = 0
    for (let i = 1; i < points.length; i++) if (Math.abs(x(points[i].x) - px) < Math.abs(x(points[best].x) - px)) best = i
    return best
  }
  const onMove = (e: PointerEvent<SVGRectElement>) => {
    const r = e.currentTarget.getBoundingClientRect()
    setHover(nearest(e.clientX - r.left))
  }
  const onKey = (e: KeyboardEvent<SVGSVGElement>) => {
    if (e.key === 'ArrowRight') setHover((i) => Math.min(points.length - 1, (i ?? -1) + 1))
    else if (e.key === 'ArrowLeft') setHover((i) => Math.max(0, (i ?? points.length) - 1))
    else if (e.key === 'Escape') setHover(null)
    else return
    e.preventDefault()
  }

  const end = points.at(-1)
  const hp = hover != null ? points[hover] : null
  return (
    <div ref={box} className="relative w-full select-none" style={{ height }}>
      {width > 0 && (
        <svg
          width={width}
          height={height}
          role="img"
          aria-label={label}
          tabIndex={0}
          onKeyDown={onKey}
          onBlur={() => setHover(null)}
          className="overflow-visible outline-none focus-visible:ring-1 focus-visible:ring-ring"
        >
          <defs>
            <clipPath id={clipId}>
              <rect x={0} y={-4} width={w} height={h + 8} />
            </clipPath>
          </defs>
          <g transform={`translate(${m.left},${m.top})`}>
            {ticks.map((t) => (
              <g key={t} transform={`translate(0,${sy(t)})`}>
                <line x2={w} stroke={GRID} strokeWidth={1} />
                <text x={-8} dy="0.32em" textAnchor="end" className="fill-muted-foreground text-[10px] tabular">
                  {yFormat(t)}
                </text>
              </g>
            ))}
            {xTicks.map((t, i) => (
              <text
                key={i}
                x={x(t)}
                y={h + 18}
                textAnchor={i === 0 ? 'start' : i === xTicks.length - 1 ? 'end' : 'middle'}
                className="fill-muted-foreground text-[10px] tabular"
              >
                {xFormat(t)}
              </text>
            ))}
            {refLines.map((r) => (
              <g key={r.label}>
                <line x2={w} y1={sy(r.y)} y2={sy(r.y)} stroke={REFERENCE} strokeWidth={1} opacity={0.7} />
                <text x={w} y={sy(r.y) - 4} textAnchor="end" className="fill-muted-foreground text-[10px]">
                  {r.label}
                </text>
              </g>
            ))}
            <g clipPath={`url(#${clipId})`}>
              {area && <path d={area} fill={SERIES} opacity={0.1} />}
              <path d={path} fill="none" stroke={SERIES} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
            </g>
            {end && hover == null && <circle cx={x(end.x)} cy={sy(end.y)} r={4} fill={SERIES} stroke="var(--card)" strokeWidth={2} />}
            {hp && (
              <g>
                <line x1={x(hp.x)} x2={x(hp.x)} y2={h} stroke={REFERENCE} strokeWidth={1} />
                <circle cx={x(hp.x)} cy={sy(hp.y)} r={4.5} fill={SERIES} stroke="var(--card)" strokeWidth={2} />
              </g>
            )}
            <rect
              width={w}
              height={h}
              fill="transparent"
              onPointerMove={onMove}
              onPointerDown={onMove}
              onPointerLeave={() => setHover(null)}
              style={{ touchAction: 'pan-y' }}
            />
          </g>
        </svg>
      )}
      {hp && hover != null && (
        <Tooltip x={m.left + x(hp.x)} y={m.top + sy(hp.y)} width={width}>
          {tip(hp, hover)}
        </Tooltip>
      )}
    </div>
  )
}

export { TipRow }
