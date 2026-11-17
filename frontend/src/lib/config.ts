/**
 * Frontend configuration from Vite environment variables (frontend/.env.local, never committed).
 * Only public values belong here: the Supabase URL and publishable key are designed to be public; the service
 * role key and every API key stay on the backend (ARCHITECTURE.md R12).
 */

const env = import.meta.env

export const config = {
  apiUrl: (env.VITE_API_URL as string | undefined)?.replace(/\/$/, '') || 'http://127.0.0.1:8000',
  supabaseUrl: (env.VITE_SUPABASE_URL as string | undefined) ?? '',
  supabaseKey: (env.VITE_SUPABASE_PUBLISHABLE_KEY as string | undefined) ?? '',
  /** Local development without login: only in `vite dev`, and the API must run with AUTH_LOCAL_BYPASS=true. */
  authBypass: env.DEV && env.VITE_AUTH_BYPASS === 'true',
} as const

export const authConfigured = Boolean(config.supabaseUrl && config.supabaseKey)
