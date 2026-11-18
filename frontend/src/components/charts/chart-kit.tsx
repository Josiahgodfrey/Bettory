/**
 * Shared chart pieces (hand-written SVG, no chart library). Spec: dataviz skill — one series colour (the preset's
 * orange `--chart-3`, validated ≥ 3:1 on both card surfaces), grey reference lines, 1px solid hairline grid, 2px
 * lines, ≥ 8px markers with a 2px surface ring, values in text colours (never the series colour), a crosshair or
 * per-mark tooltip, and a data table behind every chart. Mapping data to pixels is the only arithmetic here.
 */
import { useLayoutEffect, useRef, useState, type ReactNode } from 'react'

import { cn } from '@/lib/utils'

export const SERIES = 'var(--chart-3)'
export const REFERENCE = 'var(--muted-foreground)'
export const GRID = 'var(--border)'

/** Width of a container, kept in sync with resizes (charts fill their card). */
export function useWidth<T extends HTMLElement>(): [React.RefObject<T | null>, number] {
  const ref = useRef<T>(null)
  const [w, setW] = useState(0)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const ro = new ResizeObserver(([e]) => setW(Math.floor(e.contentRect.width)))
    ro.observe(el)
    setW(Math.floor(el.getBoundingClientRect().width))
    return () => ro.disconnect()
  }, [])
  return [ref, w]
}

/** Linear scale. */
export function linear(d0: number, d1: number, r0: number, r1: number) {
  const span = d1 - d0 || 1
  return (v: number) => r0 + ((v - d0) / span) * (r1 - r0)
}

/** "Nice" ticks for an axis: about `count` round values covering [lo, hi]. */
export function niceTicks(lo: number, hi: number, count = 4): number[] {
  if (!(hi > lo)) return [lo]
  const raw = (hi - lo) / count
  const mag = 10 ** Math.floor(Math.log10(raw))
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw
  const out: number[] = []
  for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-9; v += step) out.push(Number(v.toFixed(10)))
  return out
}

/** The tooltip: values lead, labels follow; line keys, not boxes. Positioned inside the chart box. */
export function Tooltip({ x, y, width, children }: { x: number; y: number; width: number; children: ReactNode }) {
  const left = x > width / 2 ? undefined : x + 12
  const right = x > width / 2 ? width - x + 12 : undefined
  return (
    <div
      role="status"
      className="pointer-events-none absolute z-10 min-w-32 border bg-popover px-2.5 py-2 text-xs text-popover-foreground shadow-md"
      style={{ left, right, top: Math.max(0, y - 8) }}
    >
      {children}
    </div>
  )
}

export function TipRow({ value, label, reference }: { value: ReactNode; label: ReactNode; reference?: boolean }) {
  return (
    <div className="flex items-center gap-2">
      <span className="inline-block h-0.5 w-3 shrink-0" style={{ background: reference ? REFERENCE : SERIES }} />
      <span className="font-semibold tabular">{value}</span>
      <span className="text-muted-foreground">{label}</span>
    </div>
  )
}

/** Every chart has its numbers reachable without hovering. */
export function DataTable({ head, rows, className }: { head: string[]; rows: ReactNode[][]; className?: string }) {
  return (
    <details className={cn('group mt-2 text-xs', className)}>
      <summary className="cursor-pointer select-none text-muted-foreground hover:text-foreground">Show the numbers</summary>
      <div className="mt-2 max-h-64 overflow-auto border">
        <table className="w-full text-left tabular">
          <thead className="sticky top-0 bg-muted">
            <tr>
              {head.map((h) => (
                <th key={h} className="px-2 py-1 font-medium">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i} className="border-t">
                {r.map((c, j) => (
                  <td key={j} className="px-2 py-1">
                    {c}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  )
}

/** Placeholder with the chart's frame, so an empty state never looks broken. */
export function EmptyChart({ height, children }: { height: number; children: ReactNode }) {
  return (
    <div
      className="grid place-items-center border border-dashed px-4 text-center text-sm text-muted-foreground"
      style={{ height }}
    >
      <div className="max-w-sm">{children}</div>
    </div>
  )
}
