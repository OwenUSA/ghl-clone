/**
 * The hook every screen reads the CRM's own line through (2026-09-16).
 *
 * Its own file rather than a function in `connectionStatus.ts`, because that module is
 * import-free on purpose so node can execute it in `backend/tests/test_connection_status_ui.py`
 * — and a React hook is not import-free.
 *
 * It shares `['connection-status']` with the status dot, which every signed-in page already
 * mounts and polls once a minute. So the dialer, the composer and the AI settings pay
 * NOTHING for asking: the answer is already in the cache, and a screen opened before the
 * first poll shows the fallback for at most one request rather than a blank.
 */
import { useQuery } from '@tanstack/react-query'

import { fetchConnectionStatus } from './api'
import { ourLine } from './ourLine'

/** The configured line, in E.164. Never empty — see `lib/ourLine.ts`. */
export function useOurLine(): string {
  const { data } = useQuery({
    queryKey: ['connection-status'],
    queryFn: fetchConnectionStatus,
    // Deliberately no refetchInterval: StatusIndicator owns the polling, and a second
    // interval on the same key would double every tab's traffic to the endpoint.
    staleTime: 60_000,
    retry: false,
  })
  return ourLine(data?.our_line)
}
