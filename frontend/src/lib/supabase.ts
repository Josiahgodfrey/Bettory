import { createClient, type SupabaseClient } from '@supabase/supabase-js'

import { authConfigured, config } from '@/lib/config'

/** Supabase is used for login only (password + authenticator code). All data comes from the Python API. */
export const supabase: SupabaseClient | null = authConfigured
  ? createClient(config.supabaseUrl, config.supabaseKey, {
      auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: false },
    })
  : null

export async function accessToken(): Promise<string | null> {
  if (!supabase) return null
  const { data } = await supabase.auth.getSession()
  return data.session?.access_token ?? null
}
