/**
 * Typed client for the Python API. Types come from docs/api/openapi.json (`npm run api-types`), so a change in the
 * backend contract fails the type check here instead of breaking a screen silently.
 */
import createClient, { type Middleware } from 'openapi-fetch'

import type { components, paths } from '@/lib/api-types'
import { config } from '@/lib/config'
import { accessToken } from '@/lib/supabase'

export type Schemas = components['schemas']

/** An API error with the backend's `detail` (ARCHITECTURE.md: 401 / 403 / 404 / 409 / 422). */
export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

/** Called on 401 / 403 so the app can send the user back to the login page. */
let onUnauthorized: (() => void) | null = null
export function setUnauthorizedHandler(fn: (() => void) | null): void {
  onUnauthorized = fn
}

const auth: Middleware = {
  async onRequest({ request }) {
    if (!config.authBypass) {
      const token = await accessToken()
      if (token) request.headers.set('Authorization', `Bearer ${token}`)
    }
    return request
  },
}

export const api = createClient<paths>({ baseUrl: config.apiUrl })
api.use(auth)

function detailText(detail: unknown): string {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    // FastAPI validation errors: [{loc, msg}]
    return detail
      .map((d: { loc?: unknown[]; msg?: string }) => `${(d.loc ?? []).slice(1).join('.') || 'input'}: ${d.msg ?? ''}`)
      .join('; ')
  }
  return ''
}

/**
 * Unwrap an openapi-fetch result: the data, or a thrown ApiError with the backend's reason.
 * Usage: `const me = await unwrap(api.GET('/me'))`
 */
export async function unwrap<T>(
  call: Promise<{ data?: T; error?: unknown; response: Response }>,
): Promise<T> {
  let result: { data?: T; error?: unknown; response: Response }
  try {
    result = await call
  } catch {
    throw new ApiError(0, `Cannot reach the BETTORY API at ${config.apiUrl}`)
  }
  const { data, error, response } = result
  if (response.ok) return data as T
  if (response.status === 401 || response.status === 403) onUnauthorized?.()
  const detail = detailText((error as { detail?: unknown } | undefined)?.detail)
  throw new ApiError(response.status, detail || `${response.status} ${response.statusText}`)
}
