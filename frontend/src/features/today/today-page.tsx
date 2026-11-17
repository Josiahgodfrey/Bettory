import { useQuery } from '@tanstack/react-query'
import { CheckCircle2 } from 'lucide-react'

import { Card, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { ComingSoon, ErrorState } from '@/components/page'
import { Spinner } from '@/components/spinner'
import { api, unwrap } from '@/lib/api'
import { config } from '@/lib/config'

/** Step 1: proves the connection (login → API). The dashboard itself arrives in step 2. */
export function TodayPage() {
  const me = useQuery({ queryKey: ['me'], queryFn: () => unwrap(api.GET('/me')) })

  return (
    <div className="space-y-4">
      {me.isPending && <Spinner label="Connecting to the API" />}
      {me.isError && <ErrorState error={me.error} onRetry={() => void me.refetch()} />}
      {me.data && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <CheckCircle2 className="size-4 text-primary" /> Connected to the BETTORY API
            </CardTitle>
            <CardDescription>
              {config.apiUrl} ·{' '}
              {me.data.local_bypass ? 'local development (no login)' : `owner, two-factor (${me.data.aal})`}
            </CardDescription>
          </CardHeader>
        </Card>
      )}
      <ComingSoon title="Today" step={2} endpoints={['GET /dashboard']} />
    </div>
  )
}
