import { AlertTriangle, Construction } from 'lucide-react'
import type { ReactNode } from 'react'

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { ApiError } from '@/lib/api'

export function PageHeader({ title, description, actions }: { title: string; description?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        <h2 className="font-heading text-lg font-semibold tracking-tight">{title}</h2>
        {description && <p className="mt-0.5 text-sm text-muted-foreground">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  )
}

/** An API failure shown in place of the content it was meant to load. */
export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const status = error instanceof ApiError ? error.status : null
  return (
    <Alert variant="destructive">
      <AlertTriangle />
      <AlertTitle>{status ? `Request failed (${status})` : 'Request failed'}</AlertTitle>
      <AlertDescription className="flex flex-wrap items-center gap-3">
        <span>{error instanceof Error ? error.message : String(error)}</span>
        {onRetry && (
          <Button size="xs" variant="outline" onClick={onRetry}>
            Try again
          </Button>
        )}
      </AlertDescription>
    </Alert>
  )
}

export function ComingSoon({ title, step, endpoints }: { title: string; step: number; endpoints: string[] }) {
  return (
    <>
      <PageHeader title={title} />
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Construction className="size-4" /> Built in frontend step {step}
          </CardTitle>
          <CardDescription>
            Uses {endpoints.map((e, i) => (
              <span key={e}>
                {i > 0 && ', '}
                <code className="font-mono text-xs">{e}</code>
              </span>
            ))}
          </CardDescription>
        </CardHeader>
      </Card>
    </>
  )
}
