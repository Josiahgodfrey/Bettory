/**
 * Display formatting only. No probabilities, odds or EV are calculated in the frontend (ARCHITECTURE.md R2):
 * every number shown comes from the Python API and is only formatted here.
 */

export const TIMEZONE = 'Africa/Nairobi' // EAT, UTC+3 (the API also reports it in GET /dashboard)

const dash = '—'

export function pct(p: number | null | undefined, digits = 1): string {
  return p == null ? dash : `${(p * 100).toFixed(digits)}%`
}

export function odds(o: number | null | undefined): string {
  return o == null ? dash : o.toFixed(2)
}

export function num(n: number | null | undefined, digits = 0): string {
  return n == null ? dash : n.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

export function money(n: number | null | undefined, currency = 'TZS'): string {
  return n == null ? dash : `${n.toLocaleString('en-US', { maximumFractionDigits: 0 })} ${currency}`
}

function fmt(iso: string | null | undefined, opts: Intl.DateTimeFormatOptions): string {
  if (!iso) return dash
  return new Intl.DateTimeFormat('en-GB', { timeZone: TIMEZONE, hour12: false, ...opts }).format(new Date(iso))
}

/** 21:30 */
export const timeEat = (iso?: string | null) => fmt(iso, { hour: '2-digit', minute: '2-digit' })
/** Sat 21 Nov */
export const dateEat = (iso?: string | null) => fmt(iso, { weekday: 'short', day: 'numeric', month: 'short' })
/** Sat 21 Nov, 21:30 */
export const dateTimeEat = (iso?: string | null) =>
  fmt(iso, { weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })

/** "in 2 h 15 min" / "5 min ago": a time distance for display. */
export function relative(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return dash
  const mins = Math.round((new Date(iso).getTime() - now) / 60000)
  const abs = Math.abs(mins)
  const text =
    abs < 1 ? 'now' : abs < 60 ? `${abs} min` : abs < 1440 ? `${Math.floor(abs / 60)} h ${abs % 60} min` : `${Math.floor(abs / 1440)} d`
  if (text === 'now') return text
  return mins > 0 ? `in ${text}` : `${text} ago`
}

/** "2026-11-21-NOON" → "Noon · Sat 21 Nov" */
export function sessionLabel(id: string): string {
  const m = /^(\d{4}-\d{2}-\d{2})-(NOON|NIGHT)$/.exec(id)
  if (!m) return id
  const day = new Intl.DateTimeFormat('en-GB', { timeZone: 'UTC', weekday: 'short', day: 'numeric', month: 'short' })
    .format(new Date(`${m[1]}T00:00:00Z`))
  return `${m[2] === 'NOON' ? 'Noon' : 'Night'} · ${day}`
}

/** UPPER_SNAKE → "Upper snake" */
export function humanize(code: string | null | undefined): string {
  if (!code) return dash
  const s = code.replace(/_/g, ' ').toLowerCase()
  return s.charAt(0).toUpperCase() + s.slice(1)
}
