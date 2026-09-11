/**
 * The inbox list's own arithmetic, and the mark-read transition.
 *
 * This module **imports nothing** — deliberately, like `lib/csv.ts`,
 * `lib/calendarGrid.ts` and `lib/savedViews.ts` before it — so node can execute
 * the real shipped code and `backend/tests/test_inbox.py` can assert what
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

/**
 * ---------------------------------------------------------------------------
 * The icon rail.
 *
 * Six icons that rendered with no handlers at all. Three of them are genuinely
 * missing controls and are wired to the server; two of them duplicate controls
 * that already exist three inches away, so they drive THE SAME STATE rather
 * than a parallel copy — two controls doing one job drift apart, somebody fixes
 * one of them, and then they disagree invisibly.
 * ---------------------------------------------------------------------------
 */

/** Whose conversations. `mine` is `Contact.owner_id`; there is no assignee on
 *  the conversation itself, deliberately. */
export type InboxScope = 'team' | 'mine'

/** Everything the rail reflects. There is no rail state of its own — that is
 *  the point. Each field already drives a control somewhere on the screen. */
export type RailState = {
  scope: InboxScope
  /** The Unread/All/Recent/Starred tab. The SAME state the tabs write. */
  tab: string
  /** Is the in-place search box open? */
  searching: boolean
}

/**
 * The rail's own keys, in the measured order. `filters` is here so the rail can
 * render it and say why it is dead, rather than quietly getting shorter.
 */
export type RailKey =
  'conversations' | 'search' | 'mine' | 'team' | 'filters' | 'unread'

/**
 * Should this rail row render as active?
 *
 * Every highlight is a true statement about state that is applied right now,
 * and they are independent facts rather than one selection — the scope, the
 * search and the unread filter compose, so more than one row can be lit.
 *
 *  - `conversations` is the screen you are on, so it is always lit. That is the
 *    whole of what it does.
 *  - `team` / `mine` are the scope, mutually exclusive, exactly one lit.
 *  - `search` is lit while the search box is open.
 *  - `unread` is lit when the Unread TAB is selected — the same `tab` state,
 *    read rather than copied, so the rail and the tab row cannot disagree.
 *  - `filters` is never lit: it is disabled. The toolbar control it would have
 *    to share state with is itself unimplemented, so wiring it would mean
 *    inventing a second filter model beside GHL's measured filter builder.
 *
 * Before this, the highlight was a `rail` index nothing else read, so the first
 * row sat permanently blue whatever the list was actually showing.
 */
export function railActive(key: RailKey, s: RailState): boolean {
  switch (key) {
    case 'conversations': return true
    case 'search': return s.searching
    case 'mine': return s.scope === 'mine'
    case 'team': return s.scope === 'team'
    case 'unread': return s.tab === 'unread'
    case 'filters': return false
  }
}

/**
 * What to say when the list comes back empty.
 *
 * An empty pane reads as broken. Each narrowing control gets to explain itself,
 * most specific first: a search that matched nothing is a different problem
 * from an empty queue, and "nothing is assigned to you" is an answer while a
 * blank panel is a bug report.
 */
export function emptyInboxMessage(s: RailState & { q: string }): string {
  const q = s.q.trim()
  if (q) return `No conversations match “${q}”.`
  if (s.scope === 'mine') {
    return s.tab === 'all'
      ? 'No conversations are assigned to you. Contacts are assigned by their '
        + 'Owner, on the contact panel.'
      : `No conversations assigned to you are ${s.tab}.`
  }
  return `No conversations in ${s.tab}.`
}
