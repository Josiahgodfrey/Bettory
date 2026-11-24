import { createBrowserRouter, Link } from 'react-router'

import { AppLayout } from '@/app/app-layout'
import { PageHeader } from '@/components/page'
import { Button } from '@/components/ui/button'
import { LoginPage } from '@/features/auth/login-page'
import { BetsPage } from '@/features/bets/bets-page'
import { DataHealthPage } from '@/features/health/data-health-page'
import { MatchPage } from '@/features/match/match-page'
import { PaperPage } from '@/features/paper/paper-page'
import { PerformancePage } from '@/features/performance/performance-page'
import { SessionPage, SessionsIndex } from '@/features/sessions/session-page'
import { SettingsPage } from '@/features/settings/settings-page'
import { TodayPage } from '@/features/today/today-page'

function NotFound() {
  return (
    <>
      <PageHeader title="Page not found" description="This address does not exist in BETTORY." />
      <Button nativeButton={false} render={<Link to="/" />}>
        Back to Today
      </Button>
    </>
  )
}

export const router = createBrowserRouter([
  { path: '/login', element: <LoginPage /> },
  {
    element: <AppLayout />,
    children: [
      { index: true, element: <TodayPage /> },
      { path: 'sessions', element: <SessionsIndex /> },
      { path: 'sessions/:sessionId', element: <SessionPage /> },
      { path: 'matches/:matchId', element: <MatchPage /> },
      { path: 'bets', element: <BetsPage /> },
      { path: 'performance', element: <PerformancePage /> },
      { path: 'paper', element: <PaperPage /> },
      { path: 'data-health', element: <DataHealthPage /> },
      { path: 'settings', element: <SettingsPage /> },
      { path: '*', element: <NotFound /> },
    ],
  },
])
