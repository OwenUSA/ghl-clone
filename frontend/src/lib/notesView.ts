/**
 * What the Notes tab shows: search, filter by author, sort.
 *
 * Import-free, like customFields.ts and calendarGrid.ts, so
 * backend/tests/test_opportunity_card_ui.py runs the real function under node. The
 * filter and sort icons each wear a blue dot when active; a dot over a filter that
 * does not actually narrow anything is the failure this guards.
 */
export type NoteRow = {
  /** `deal` — this opportunity's own note; `contact` — a NOTE on the thread. */
  kind: 'deal' | 'contact'
  id: number
  body: string
  at: string
  author: string | null
}

export type NoteSort = 'newest' | 'oldest'

/** The filter value that selects the contact's thread notes. */
export const CONTACT_NOTES = '__contact__'

export function filterNotes(
  rows: NoteRow[],
  { q, author, sort }: { q: string; author: string | null; sort: NoteSort },
): NoteRow[] {
  const term = q.trim().toLowerCase()
  const kept = rows.filter((r) => {
    if (term && !r.body.toLowerCase().includes(term)) return false
    if (author === CONTACT_NOTES) return r.kind === 'contact'
    if (author) return r.kind === 'deal' && (r.author ?? '--') === author
    return true
  })
  const time = (r: NoteRow) => {
    const t = new Date(r.at).getTime()
    return Number.isNaN(t) ? 0 : t
  }
  // This deal's own notes first, then the contact's — each group in the chosen
  // order. The deal's notes are the ones this tab is about.
  return kept.sort((a, b) => {
    if (a.kind !== b.kind) return a.kind === 'deal' ? -1 : 1
    const d = time(a) - time(b)
    return (sort === 'newest' ? -d : d) || b.id - a.id
  })
}
