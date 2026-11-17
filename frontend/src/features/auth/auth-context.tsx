/**
 * Login state (ARCHITECTURE.md §7.3): password → 6-digit authenticator code (aal2) → GET /me.
 * The API accepts only the owner with a two-factor session, so a password-only session never reaches the app.
 */
import type { Session } from '@supabase/supabase-js'
import { createContext, use, useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'

import { api, ApiError, setUnauthorizedHandler, unwrap } from '@/lib/api'
import { authConfigured, config } from '@/lib/config'
import { supabase } from '@/lib/supabase'

export type AuthStatus =
  | 'loading'
  | 'not-configured' // no Supabase settings and no local bypass
  | 'signed-out'
  | 'mfa-enroll' // password accepted, no authenticator yet: set one up
  | 'mfa-verify' // password accepted: enter the authenticator code
  | 'signed-in' // two-factor session (or the local development bypass)

interface AuthState {
  status: AuthStatus
  email: string | null
  bypass: boolean
  error: string | null
  signIn: (email: string, password: string, captchaToken?: string) => Promise<void>
  verify: (code: string) => Promise<void>
  startEnroll: () => Promise<{ qr: string; secret: string }>
  confirmEnroll: (code: string) => Promise<void>
  signOut: () => Promise<void>
}

const AuthContext = createContext<AuthState | null>(null)

export function useAuth(): AuthState {
  const ctx = use(AuthContext)
  if (!ctx) throw new Error('useAuth outside AuthProvider')
  return ctx
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>(() =>
    config.authBypass ? 'signed-in' : authConfigured ? 'loading' : 'not-configured',
  )
  const [email, setEmail] = useState<string | null>(config.authBypass ? 'local development' : null)
  const [error, setError] = useState<string | null>(null)
  const [enrollId, setEnrollId] = useState<string | null>(null)

  /** Decide the step from the Supabase session; at aal2 confirm with the API that this is the owner. */
  const resolve = useCallback(async (session: Session | null) => {
    if (!supabase) return
    if (!session) {
      setEmail(null)
      setStatus('signed-out')
      return
    }
    setEmail(session.user.email ?? null)
    const { data: aal } = await supabase.auth.mfa.getAuthenticatorAssuranceLevel()
    if (aal?.currentLevel === 'aal2') {
      try {
        await unwrap(api.GET('/me')) // also queues the Telegram login alert (once per login session)
        setStatus('signed-in')
      } catch (e) {
        setError(e instanceof ApiError && e.status === 403 ? 'This account is not the BETTORY owner.' : String((e as Error).message))
        await supabase.auth.signOut()
        setStatus('signed-out')
      }
      return
    }
    const { data: factors } = await supabase.auth.mfa.listFactors()
    setStatus(factors?.totp?.some((f) => f.status === 'verified') ? 'mfa-verify' : 'mfa-enroll')
  }, [])

  useEffect(() => {
    if (!supabase || config.authBypass) return
    void supabase.auth.getSession().then(({ data }) => resolve(data.session))
    const { data: sub } = supabase.auth.onAuthStateChange((event, session) => {
      // MFA_CHALLENGE_VERIFIED and TOKEN_REFRESHED are handled where they happen; react to sign in / out only.
      if (event === 'SIGNED_OUT') void resolve(null)
      else if (event === 'SIGNED_IN' && session) void resolve(session)
    })
    return () => sub.subscription.unsubscribe()
  }, [resolve])

  // A 401/403 from the API (expired session, lost two-factor): back to the login page.
  useEffect(() => {
    if (config.authBypass) return
    setUnauthorizedHandler(() => {
      void supabase?.auth.signOut()
    })
    return () => setUnauthorizedHandler(null)
  }, [])

  const signIn = useCallback(async (mail: string, password: string, captchaToken?: string) => {
    if (!supabase) return
    setError(null)
    const { error: err } = await supabase.auth.signInWithPassword({
      email: mail,
      password,
      options: captchaToken ? { captchaToken } : undefined,
    })
    if (err) {
      setError(err.message)
      throw err
    }
  }, [])

  const afterMfa = useCallback(async () => {
    if (!supabase) return
    const { data } = await supabase.auth.getSession()
    await resolve(data.session)
  }, [resolve])

  const verify = useCallback(
    async (code: string) => {
      if (!supabase) return
      setError(null)
      const { data: factors } = await supabase.auth.mfa.listFactors()
      const factor = factors?.totp?.find((f) => f.status === 'verified')
      if (!factor) {
        setStatus('mfa-enroll')
        return
      }
      const { error: err } = await supabase.auth.mfa.challengeAndVerify({ factorId: factor.id, code })
      if (err) {
        setError('That code is not valid. Use the current code from your authenticator app.')
        throw err
      }
      await afterMfa()
    },
    [afterMfa],
  )

  const startEnroll = useCallback(async () => {
    if (!supabase) throw new Error('login is not configured')
    setError(null)
    // Remove an unfinished enrolment from an earlier attempt (a pending factor blocks a new one with the same name).
    const { data: factors } = await supabase.auth.mfa.listFactors()
    for (const f of factors?.all ?? []) {
      if (f.factor_type === 'totp' && f.status === 'unverified') await supabase.auth.mfa.unenroll({ factorId: f.id })
    }
    const { data, error: err } = await supabase.auth.mfa.enroll({ factorType: 'totp', friendlyName: 'BETTORY' })
    if (err || !data) {
      setError(err?.message ?? 'Could not start the authenticator setup')
      throw err ?? new Error('enroll failed')
    }
    setEnrollId(data.id)
    return { qr: data.totp.qr_code, secret: data.totp.secret }
  }, [])

  const confirmEnroll = useCallback(
    async (code: string) => {
      if (!supabase || !enrollId) return
      setError(null)
      const { error: err } = await supabase.auth.mfa.challengeAndVerify({ factorId: enrollId, code })
      if (err) {
        setError('That code is not valid. Scan the QR code again or wait for the next code.')
        throw err
      }
      await afterMfa()
    },
    [enrollId, afterMfa],
  )

  const signOut = useCallback(async () => {
    setError(null)
    if (supabase) await supabase.auth.signOut()
  }, [])

  const value = useMemo<AuthState>(
    () => ({ status, email, bypass: config.authBypass, error, signIn, verify, startEnroll, confirmEnroll, signOut }),
    [status, email, error, signIn, verify, startEnroll, confirmEnroll, signOut],
  )
  return <AuthContext value={value}>{children}</AuthContext>
}
