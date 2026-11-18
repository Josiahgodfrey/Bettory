import './index.css'

import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { RouterProvider } from 'react-router'
import { toast } from 'sonner'

import { router } from '@/app/router'
import { Toaster } from '@/components/ui/sonner'
import { TooltipProvider } from '@/components/ui/tooltip'
import { AuthProvider } from '@/features/auth/auth-context'
import { ApiError } from '@/lib/api'
import { ThemeProvider } from '@/lib/theme'

const retry = (count: number, error: unknown) =>
  // Never retry what the server refused on purpose (401/403/404/409/422); retry a network failure or 5xx twice.
  count < 2 && (!(error instanceof ApiError) || error.status === 0 || error.status >= 500)

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry, staleTime: 30_000, refetchOnWindowFocus: true } },
  queryCache: new QueryCache(),
  mutationCache: new MutationCache({
    // Every action reports its failure as a notification; the screen shows the details where relevant.
    onError: (error) => toast.error(error instanceof Error ? error.message : String(error)),
  }),
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          <TooltipProvider>
            <RouterProvider router={router} />
            <Toaster position="top-right" closeButton />
          </TooltipProvider>
        </AuthProvider>
      </QueryClientProvider>
    </ThemeProvider>
  </StrictMode>,
)
