/**
 * The inbox list's own arithmetic, and the mark-read transition.
 *
 * This module **imports nothing** — deliberately, like `lib/csv.ts`,
 * `lib/calendarGrid.ts` and `lib/savedViews.ts` before it — so node can execute
 * the real shipped code and `backend/tests/test_inbox_unread.py` can assert what
 * the inbox actually renders rather than pattern-matching the page source.
 *
 * Two bugs live here, and both were invisible to a status-code test:
 *
 *  1. The Unread tab's badge summed unread MESSAGES across conversations while
 *     the tab it sits on filters CONVERSATIONS. One thread holding two unread
 *     texts read "2" above a list of one row.
 *  2. Opening a thread never cleared anything, because nothing in the browser
 *     ever called `PATCH /api/conversations/{id}` — the endpoint has always
 *     accepted `{"read": true}`.
 */

/** The least a row needs for any of this. `ConversationSummary` satisfies it. */
export type UnreadRow = { id: number; unread_count: number }

/**
 * The rows the Unread tab shows: `GET /api/conversations?tab=unread` is
 * `WHERE unread_count > 0`, and this is the same predicate on the client so the
 * badge and the list can never be computed from different rules.
 */
export function unreadRows<T extends UnreadRow>(rows: readonly T[] | undefined): T[] {
  return (rows ?? []).filter((r) => r.unread_count > 0)
}

/**
 * The number on the Unread tab.
 *
 * A COUNT of conversations, not a sum of their unread messages. The badge labels
 * a tab that filters conversations, so it has to agree with the list underneath
 * it: one thread with two unread texts is one row and therefore a "1".
 *
 * The per-row badge in the list keeps the message total, and that is correct
 * there — it labels one conversation, and "2" means two texts nobody has read.
 */
export function unreadTabCount(rows: readonly UnreadRow[] | undefined): number {
  return unreadRows(rows).length
}

/** Zero one conversation's unread count in a cached list. Returns a NEW array. */
export function markReadIn<T extends UnreadRow>(
  rows: readonly T[] | undefined, id: number,
): T[] | undefined {
  if (!rows) return rows as undefined
  if (!rows.some((r) => r.id === id && r.unread_count > 0)) return rows as T[]
  return rows.map((r) => (r.id === id ? { ...r, unread_count: 0 } : r))
}

/**
 * Should the thread on screen be marked read right now?
 *
 * `visible` is `document.visibilityState === 'visible'`, and it is the whole
 * reason this is a function rather than an inline `&&`. A thread left open on a
 * background tab while the dispatcher is out on a roof keeps its badge: nobody
 * read the message that arrived, so nothing may say it was read. Come back to
 * the tab and it clears.
 */
export function shouldMarkRead(
  openId: number | null, unread: number, visible: boolean,
): boolean {
  return openId != null && unread > 0 && visible
}

/**
 * What a mark-read attempt did, expressed as something the caller can apply to
 * every cached conversation list it holds.
 */
export type InboxUpdate = {
  /**
   * The updater handed straight to `setQueriesData`. On failure it is identity —
   * which is the point: the badge must not clear locally when the server never
   * agreed that the thread was read.
   */
  apply: <T extends UnreadRow>(rows: readonly T[] | undefined) => T[] | undefined
  cleared: boolean
  error: Error | null
}

const IDENTITY = <T extends UnreadRow>(rows: readonly T[] | undefined) =>
  rows as T[] | undefined

/**
 * Mark one conversation read: ask the server FIRST, patch the list only if it
 * agreed.
 *
 * Not optimistic, on purpose. An optimistic clear that silently rolls back is
 * indistinguishable from a badge that works, right up until somebody misses a
 * customer's text — and the failure this protects against (a TECH's write
 * refused, the API down) is exactly the one where the operator most needs the
 * badge to stay put. Ten seconds of polling means a stale badge is never stale
 * for long; a lying one is wrong until someone notices.
 */
export async function markConversationRead(
  id: number,
  patch: (id: number) => Promise<unknown>,
): Promise<InboxUpdate> {
  try {
    await patch(id)
  } catch (e) {
    return {
      apply: IDENTITY,
      cleared: false,
      error: e instanceof Error ? e : new Error(String(e)),
    }
  }
  return {
    apply: (rows) => markReadIn(rows, id),
    cleared: true,
    error: null,
  }
}
