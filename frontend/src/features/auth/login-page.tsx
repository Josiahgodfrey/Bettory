import { LockKeyhole, ShieldCheck } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { Navigate } from 'react-router'

import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Field, FieldDescription, FieldGroup, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { InputOTP, InputOTPGroup, InputOTPSlot } from '@/components/ui/input-otp'
import { Spinner } from '@/components/spinner'
import { useAuth } from '@/features/auth/auth-context'

/** /login: email + password, then the authenticator code. There is no sign-up page (ARCHITECTURE.md S1). */
export function LoginPage() {
  const auth = useAuth()
  if (auth.status === 'signed-in') return <Navigate to="/" replace />

  return (
    <main className="grid min-h-svh place-items-center bg-muted/40 px-4 py-10">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex items-center justify-center gap-2">
          <div className="grid size-8 place-items-center bg-primary text-primary-foreground">
            <span className="font-heading text-sm font-bold">B</span>
          </div>
          <span className="font-heading text-lg font-semibold tracking-tight">BETTORY</span>
        </div>
        {auth.status === 'loading' && <Spinner label="Checking your session" />}
        {auth.status === 'not-configured' && <NotConfigured />}
        {auth.status === 'signed-out' && <PasswordStep />}
        {auth.status === 'mfa-verify' && <CodeStep />}
        {auth.status === 'mfa-enroll' && <EnrollStep />}
      </div>
    </main>
  )
}

function NotConfigured() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Login is not configured</CardTitle>
        <CardDescription>
          Set <code>VITE_SUPABASE_URL</code> and <code>VITE_SUPABASE_PUBLISHABLE_KEY</code> in <code>frontend/.env.local</code>,
          or for local development <code>VITE_AUTH_BYPASS=true</code>.
        </CardDescription>
      </CardHeader>
    </Card>
  )
}

function ErrorLine() {
  const { error } = useAuth()
  if (!error) return null
  return (
    <Alert variant="destructive">
      <AlertDescription>{error}</AlertDescription>
    </Alert>
  )
}

function PasswordStep() {
  const { signIn } = useAuth()
  const [busy, setBusy] = useState(false)

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const form = new FormData(e.currentTarget)
    setBusy(true)
    try {
      await signIn(String(form.get('email')), String(form.get('password')))
    } catch {
      /* shown by ErrorLine */
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <LockKeyhole className="size-4" /> Sign in
        </CardTitle>
        <CardDescription>Private system. Owner account only.</CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={submit}>
          <FieldGroup>
            <Field>
              <FieldLabel htmlFor="email">Email</FieldLabel>
              <Input id="email" name="email" type="email" autoComplete="username" required autoFocus />
            </Field>
            <Field>
              <FieldLabel htmlFor="password">Password</FieldLabel>
              <Input id="password" name="password" type="password" autoComplete="current-password" required />
            </Field>
            <ErrorLine />
            <Button type="submit" disabled={busy} className="w-full">
              {busy ? 'Signing in…' : 'Continue'}
            </Button>
          </FieldGroup>
        </form>
      </CardContent>
    </Card>
  )
}

function CodeInput({ onComplete, busy }: { onComplete: (code: string) => void; busy: boolean }) {
  const [code, setCode] = useState('')
  return (
    <InputOTP
      maxLength={6}
      value={code}
      onChange={setCode}
      onComplete={(c: string) => {
        onComplete(c)
        setCode('')
      }}
      disabled={busy}
      autoFocus
      inputMode="numeric"
      pattern="^[0-9]*$"
      aria-label="6-digit code"
      containerClassName="justify-center"
    >
      <InputOTPGroup>
        {[0, 1, 2, 3, 4, 5].map((i) => (
          <InputOTPSlot key={i} index={i} />
        ))}
      </InputOTPGroup>
    </InputOTP>
  )
}

function CodeStep() {
  const { verify, signOut, email } = useAuth()
  const [busy, setBusy] = useState(false)

  async function submit(code: string) {
    setBusy(true)
    try {
      await verify(code)
    } catch {
      /* shown by ErrorLine */
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ShieldCheck className="size-4" /> Two-factor code
        </CardTitle>
        <CardDescription>Enter the 6-digit code from your authenticator app for {email}.</CardDescription>
      </CardHeader>
      <CardContent>
        <FieldGroup>
          <CodeInput onComplete={submit} busy={busy} />
          <ErrorLine />
          <Button variant="ghost" onClick={() => void signOut()}>
            Use another account
          </Button>
        </FieldGroup>
      </CardContent>
    </Card>
  )
}

function EnrollStep() {
  const { startEnroll, confirmEnroll, signOut } = useAuth()
  const [qr, setQr] = useState<{ qr: string; secret: string } | null>(null)
  const [busy, setBusy] = useState(false)

  async function begin() {
    setBusy(true)
    try {
      setQr(await startEnroll())
    } catch {
      /* shown by ErrorLine */
    } finally {
      setBusy(false)
    }
  }

  async function submit(code: string) {
    setBusy(true)
    try {
      await confirmEnroll(code)
    } catch {
      /* shown by ErrorLine */
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ShieldCheck className="size-4" /> Set up two-factor login
        </CardTitle>
        <CardDescription>
          BETTORY requires an authenticator app (Google Authenticator, Microsoft Authenticator or Authy). This is a
          one-time setup.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <FieldGroup>
          {!qr ? (
            <Button onClick={() => void begin()} disabled={busy} className="w-full">
              {busy ? 'Preparing…' : 'Show QR code'}
            </Button>
          ) : (
            <>
              <img src={qr.qr} alt="QR code for the authenticator app" className="mx-auto size-44 bg-white p-2" />
              <Field>
                <FieldDescription className="text-center">
                  Can’t scan? Enter this key: <code className="break-all font-mono text-xs">{qr.secret}</code>
                </FieldDescription>
              </Field>
              <Field>
                <FieldLabel className="justify-center">Then enter the code it shows</FieldLabel>
                <CodeInput onComplete={submit} busy={busy} />
              </Field>
            </>
          )}
          <ErrorLine />
          <Button variant="ghost" onClick={() => void signOut()}>
            Cancel
          </Button>
        </FieldGroup>
      </CardContent>
    </Card>
  )
}
