import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import App from './App'
import { Unauthorized } from './lib/api'
import './index.css'

// Polling, not WebSockets (DECISIONS.md).
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchInterval: 30_000,
      staleTime: 10_000,
      // Never retry an auth failure. api.ts already tries a token refresh once
      // and gives up only when that fails, so retrying just replays a request
      // that cannot succeed — three times per query, across every query on the
      // page, while the user is looking at the login form.
      retry: (count, error) => !(error instanceof Unauthorized) && count < 3,
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
