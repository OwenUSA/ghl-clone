import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import App from './App'
import { ApiError, Unauthorized } from './lib/api'
import './index.css'

// Polling, not WebSockets (DECISIONS.md).
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchInterval: 30_000,
      staleTime: 10_000,
      // Never retry a request the server has already refused. api.ts tries a
      // token refresh once on a 401 and gives up only when that fails, and any
      // other 4xx is a decision that will be made again — retrying just replays
      // a request that cannot succeed, three times per query across every query
      // on the page. It also buries the failure: a STAFF-only report 403'd to a
      // TECH spent seven seconds in backoff showing zeros before anything said so.
      retry: (count, error) =>
        !(error instanceof Unauthorized)
        && !(error instanceof ApiError && error.status < 500)
        && count < 3,
    },
  },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
)
