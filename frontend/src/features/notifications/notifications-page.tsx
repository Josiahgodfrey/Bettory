import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { BellOff, Send } from 'lucide-react'
import { toast } from 'sonner'

import { ErrorState, PageHeader } from '@/components/page'
import { ToneBadge } from '@/components/status'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { api, ApiError, unwrap } from '@/lib/api'
import { dateTimeEat, humanize } from '@/lib/format'

const STATUS = { SENT: 'success', PENDING: 'info', FAILED: 'danger', EXPIRED: 'muted' } as const
const KIND: Record<string, string> = {
  REPORT_READY: 'Report ready',
  NO_MATCHES: 'No matches',
  WITHDRAWN: 'Withdrawal',
  FAILURE: 'Failure',
  LOGIN: 'Login',
  TEST: 'Test',
}

/** Notifications: the Telegram outbox (GET /notifications) and a test message (POST /notifications/test). */
export function NotificationsPage() {
  const qc = useQueryClient()
  const q = useQuery({ queryKey: ['notifications'], queryFn: () => unwrap(api.GET('/notifications', { params: { query: { limit: 100 } } })), refetchInterval: 60_000 })
  const test = useMutation({
    mutationFn: () => unwrap(api.POST('/notifications/test')),
    onSuccess: (r) => {
      if (r.sent) toast.success('Test message sent to Telegram')
      else toast.error(`The test message was not delivered (${r.failed} failed)`)
      void qc.invalidateQueries({ queryKey: ['notifications'] })
    },
    onError: (e) => e instanceof ApiError && e.status === 409 && void qc.invalidateQueries({ queryKey: ['notifications'] }),
  })

  return (
    <>
      <PageHeader
        title="Notifications"
        description="Telegram messages: report ready, no matches, withdrawals, failures and logins. Each event is sent once; messages older than a day expire instead of arriving late."
        actions={
          <Button size="sm" variant="outline" onClick={() => test.mutate()} disabled={test.isPending || q.data?.configured === false}>
            <Send /> {test.isPending ? 'Sending…' : 'Send a test message'}
          </Button>
        }
      />
      {q.isPending && <Skeleton className="h-48" />}
      {q.isError && <ErrorState error={q.error} onRetry={() => void q.refetch()} />}
      {q.data && (
        <div className="grid gap-4">
          {!q.data.configured && (
            <Alert>
              <BellOff />
              <AlertTitle>Telegram is off for this backend</AlertTitle>
              <AlertDescription>
                Messages are kept in the outbox but not sent. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in the backend’s .env (the development
                API runs with Telegram off on purpose).
              </AlertDescription>
            </Alert>
          )}
          <div className="flex flex-wrap gap-2 text-sm">
            {Object.entries(q.data.counts).map(([k, n]) => (
              <span key={k} className="inline-flex items-center gap-1">
                <ToneBadge tone={STATUS[k as keyof typeof STATUS] ?? 'muted'}>{humanize(k)}</ToneBadge>
                <span className="tabular">{n}</span>
              </span>
            ))}
          </div>
          {q.data.items.length === 0 ? (
            <p className="py-6 text-center text-sm text-muted-foreground">No notifications yet.</p>
          ) : (
            <div className="grid gap-2">
              {q.data.items.map((n) => (
                <Card key={n.id} size="sm">
                  <CardContent className="grid gap-1.5">
                    <div className="flex flex-wrap items-center gap-2 text-xs">
                      <ToneBadge tone={STATUS[n.status as keyof typeof STATUS] ?? 'muted'}>{humanize(n.status)}</ToneBadge>
                      <span className="font-medium">{KIND[n.kind] ?? humanize(n.kind)}</span>
                      <span className="ml-auto text-muted-foreground tabular">{dateTimeEat(n.sent_at ?? n.created_at)}</span>
                    </div>
                    <p className="whitespace-pre-line break-words text-sm">{n.message}</p>
                    {n.last_error && (
                      <p className="text-xs text-destructive">
                        {n.attempts} attempt(s): {n.last_error}
                      </p>
                    )}
                  </CardContent>
                </Card>
              ))}
            </div>
          )}
        </div>
      )}
    </>
  )
}
