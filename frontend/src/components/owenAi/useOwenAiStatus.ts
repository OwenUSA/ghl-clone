import { useQuery } from '@tanstack/react-query'
import { owenAiStatus } from '../../lib/api'
import { owenAiRetry, owenAiVisible } from '../../lib/owenAi'

export const OWEN_AI_STATUS_KEY = ['owen-ai-status'] as const

/**
 * GET /api/owen-ai/status — the module's one gate in the browser. The server answers 404 to
 * everyone but the owner, so a failure means "not drawn": the Sidebar row and the App view
 * both read `visible`. A 404 is never retried (`owenAiRetry`) and the answer is cached, so
 * other users pay one request per session.
 *
 * `poll` (the page itself) keeps the status strip fresh; the sidebar does not poll.
 */
export function useOwenAiStatus({ enabled = true, poll = false }: { enabled?: boolean; poll?: boolean } = {}) {
  const q = useQuery({
    queryKey: OWEN_AI_STATUS_KEY,
    queryFn: owenAiStatus,
    enabled,
    retry: owenAiRetry,
    staleTime: poll ? 30_000 : 5 * 60_000,
    refetchOnWindowFocus: false,
    refetchInterval: (query) => (poll && query.state.status === 'success' ? 30_000 : false),
  })
  return { ...q, visible: owenAiVisible(q) }
}
