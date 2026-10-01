/**
 * The Dispatch page's wording (2026-09-30). Pure, so tests/test_dispatch_ui.py runs it under
 * node with a fixed clock.
 */

/** "Overdue 2 h", "Due in 40 min", "Due Thu 6:00 PM" — from the item's limit. */
export function dueLabel(dueAt: string | null, now: Date = new Date()): string {
  if (!dueAt) return ''
  const due = new Date(dueAt)
  const mins = Math.round((due.getTime() - now.getTime()) / 60000)
  const span = (m: number) => (m < 60 ? `${m} min` : m < 48 * 60 ? `${Math.round(m / 60)} h`
    : `${Math.round(m / 1440)} days`)
  if (mins <= 0) return `Overdue ${span(-mins)}`
  if (mins < 24 * 60) return `Due in ${span(mins)}`
  const day = due.toLocaleDateString('en-US', { weekday: 'short', timeZone: 'America/New_York' })
  const time = due.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit',
    timeZone: 'America/New_York' })
  return `Due ${day} ${time}`
}

/** (954) 914-7244 from ten digits; anything else as it came. */
export function formatPhone(digits: string | null): string {
  if (!digits || !/^\d{10}$/.test(digits)) return digits ?? ''
  return `(${digits.slice(0, 3)}) ${digits.slice(3, 6)}-${digits.slice(6)}`
}

/** Tab names short enough for one line; the server's longer label is the tooltip. */
export const SHORT: Record<string, string> = {
  new: 'New jobs', after_visit: 'After visit', missed: 'Missed', book: 'Book',
  zuper: 'Update Zuper', visits: 'Today', stale: 'Quiet',
}

/** A tab's label: "Missed 4" (with "!" when some are overdue). */
export function queueLabel(q: { key?: string; label: string; open: number; urgent: number }): string {
  const name = (q.key && SHORT[q.key]) || q.label
  if (!q.open) return name
  return `${name} ${q.open}${q.urgent ? '!' : ''}`
}

/** "Last checked 2 min ago" for the heartbeat line. */
export function checkedLabel(at: string | null, now: Date = new Date()): string {
  if (!at) return 'Not checked yet'
  const mins = Math.max(0, Math.round((now.getTime() - new Date(at).getTime()) / 60000))
  return mins < 1 ? 'Checked just now' : mins < 60 ? `Checked ${mins} min ago`
    : `Checked ${Math.round(mins / 60)} h ago`
}

/** "Thu Oct 2, 1:00 PM - Antonio Brown (+8 min driving, next to #273 Weston)" */
export function slotLabel(s: { tech: string; start: string; extra_drive_minutes: number;
  next_to: string | null }): string {
  const d = new Date(s.start)
  const day = d.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric',
    timeZone: 'America/New_York' })
  const time = d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit',
    timeZone: 'America/New_York' })
  const extra = s.extra_drive_minutes ? `+${s.extra_drive_minutes} min driving` : 'no extra driving'
  return `${day}, ${time} - ${s.tech} (${extra}${s.next_to ? `, next to ${s.next_to}` : ''})`
}
