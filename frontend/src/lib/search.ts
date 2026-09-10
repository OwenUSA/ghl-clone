/**
 * The ctrl+K palette's pure logic: the wire shape of GET /api/search, flattening
 * the grouped response into one navigable list, where Enter on a row goes, and
 * how the arrow keys move.
 *
 * Deliberately free of React and of every import, so node can execute this exact
 * module and backend/tests/test_search_ui.py can assert what it DOES rather than
 * what its source looks like. Same precedent, and the same reasoning, as
 * calendarGrid.ts — see the docstring on backend/tests/test_calendar_grid.py.
 */

export type SearchContact = {
  id: number
  name: string
  email: string | null
  phone: string | null
}

export type SearchOpportunity = {
  id: number
  title: string
  status: string
  value_cents: number
  pipeline_id: number
  stage_id: number
  stage_name: string | null
  contact_name: string | null
}

export type SearchMessage = {
  id: number
  conversation_id: number
  contact_id: number
  contact_name: string | null
  type: string
  direction: 'INBOUND' | 'OUTBOUND'
  occurred_at: string
  snippet: string
}

export type GroupKey = 'contacts' | 'opportunities' | 'messages'

export type SearchGroup = {
  type: GroupKey
  label: string
  /** Capped at `limit`. `total` is the real number of matches. */
  items: (SearchContact | SearchOpportunity | SearchMessage)[]
  total: number
  truncated: boolean
}

export type SearchResponse = {
  q: string
  limit: number
  total: number
  groups: SearchGroup[]
}

/**
 * How long the box stays quiet before asking the server.
 *
 * One request per keystroke would put four or five in flight for a three-letter
 * query, and the last one to arrive is not necessarily the last one sent.
 */
export const SEARCH_DEBOUNCE_MS = 200

export type FlatRow =
  | { group: 'contacts'; item: SearchContact }
  | { group: 'opportunities'; item: SearchOpportunity }
  | { group: 'messages'; item: SearchMessage }

/**
 * Every selectable row, in rendered order, across all groups.
 *
 * The arrow keys walk one list even though the eye sees three, so the flattened
 * order here has to be the order the groups are drawn in.
 */
export function flatten(res?: SearchResponse | null): FlatRow[] {
  const out: FlatRow[] = []
  for (const g of res?.groups ?? []) {
    for (const item of g.items) out.push({ group: g.type, item } as FlatRow)
  }
  return out
}

/**
 * Where Enter on a row should land.
 *
 * A message opens the CONVERSATION it was said in, not the message: there is no
 * per-message screen, and being dropped into the thread is what the person
 * searching for a half-remembered sentence actually wants.
 */
export function target(row: FlatRow): { view: string; id: number } {
  if (row.group === 'contacts') return { view: 'contacts', id: row.item.id }
  if (row.group === 'opportunities') return { view: 'opportunities', id: row.item.id }
  return { view: 'conversations', id: row.item.conversation_id }
}

/**
 * Move the selection by `delta`, wrapping at both ends.
 *
 * Wrapping, not clamping: Down on the last row of a short list reaching the first
 * is how every command palette behaves, and clamping leaves the key feeling dead.
 * Returns 0 for an empty list so the caller never holds an index into nothing.
 */
export function move(count: number, current: number, delta: number): number {
  if (count <= 0) return 0
  return (((current + delta) % count) + count) % count
}

/** What the palette should be showing right now. */
export type PaletteState = 'idle' | 'loading' | 'empty' | 'results'

export function paletteState(
  q: string,
  loading: boolean,
  res?: SearchResponse | null,
): PaletteState {
  if (!q.trim()) return 'idle'
  // `loading` is "nothing to show yet", not "a request is in flight" — a query
  // being refined keeps the previous results on screen instead of flashing.
  if (loading) return 'loading'
  return flatten(res).length ? 'results' : 'empty'
}

/**
 * The sentence a capped group prints under its rows, or null when it fits.
 *
 * The cap is said out loud on purpose. A palette that silently shows the first
 * five of forty teaches people that the record they want is not in the system.
 */
export function capNote(g: SearchGroup): string | null {
  if (!g.truncated) return null
  return `Showing ${g.items.length} of ${g.total} — keep typing to narrow`
}
