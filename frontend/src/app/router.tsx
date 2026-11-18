import { createBrowserRouter, Link } from 'react-router'

import { AppLayout } from '@/app/app-layout'
import { PageHeader } from '@/components/page'
import { Button } from '@/components/ui/button'
import { LoginPage } from '@/features/auth/login-page'
import { BetsPage } from '@/features/bets/bets-page'
import { CalendarPage } from '@/features/calendar/calendar-page'
import { DataLoadingPage } from '@/features/data/data-loading-page'
import { BacktestPage } from '@/features/health/backtest-page'
import { DataHealthPage } from '@/features/health/data-health-page'
import { MatchPage } from '@/features/match/match-page'
import { NotificationsPage } from '@/features/notifications/notifications-page'
import { PaperPage } from '@/features/paper/paper-page'
import { PerformancePage } from '@/features/performance/performance-page'
import { RecorderPage } from '@/features/recorder/recorder-page'
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
      { path: 'recorder', element: <RecorderPage /> },
      { path: 'calendar', element: <CalendarPage /> },
      { path: 'data-health', element: <DataHealthPage /> },
      { path: 'data-health/backtests/:name', element: <BacktestPage /> },
      { path: 'data-loading', element: <DataLoadingPage /> },
      { path: 'notifications', element: <NotificationsPage /> },
      { path: 'settings', element: <SettingsPage /> },
      { path: '*', element: <NotFound /> },
    ],
  },
])
