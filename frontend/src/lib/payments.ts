/**
 * Payments in the browser (2026-10-21, app/payments): money and status wording.
 *
 * IMPORT-FREE on purpose, like `registry.ts`: `backend/tests/test_payments_ui.py` runs it under
 * node. The SERVER decides every rule (what is due, what may be undone); this only words it.
 */

export function money(cents: number | null | undefined): string {
  if (cents == null) return '—'
  const sign = cents < 0 ? '-' : ''
  const c = Math.abs(Math.round(cents))
  return `${sign}$${Math.floor(c / 100).toLocaleString('en-US')}.${String(c % 100).padStart(2, '0')}`
}

export const STATUS: Record<string, { label: string; fg: string; bg: string }> = {
  overdue: { label: 'Overdue', fg: 'rgb(180,35,24)', bg: 'rgb(254,243,242)' },
  due: { label: 'Due', fg: 'rgb(181,71,8)', bg: 'rgb(255,250,235)' },
  upcoming: { label: 'Upcoming', fg: 'rgb(54,89,148)', bg: 'rgb(239,244,255)' },
  paid: { label: 'Paid', fg: 'rgb(2,122,72)', bg: 'rgb(236,253,243)' },
  waived: { label: 'Waived', fg: 'rgb(71,84,103)', bg: 'rgb(242,244,247)' },
  cancelled: { label: 'Cancelled', fg: 'rgb(71,84,103)', bg: 'rgb(242,244,247)' },
  quoted: { label: 'Quoted', fg: 'rgb(83,56,158)', bg: 'rgb(244,243,255)' },
  agreed: { label: 'Agreed', fg: 'rgb(54,89,148)', bg: 'rgb(239,244,255)' },
  collecting: { label: 'Collecting', fg: 'rgb(181,71,8)', bg: 'rgb(255,250,235)' },
}
export const statusOf = (s: string) => STATUS[s] ?? { label: s, fg: 'rgb(71,84,103)', bg: 'rgb(242,244,247)' }

export const DUE_RULE: Record<string, string> = {
  before_start: 'before the job', after_completion: 'after the job', on_date: 'on a date',
}

/** "Due Oct 23" / "Due when the job is scheduled" / "Paid Oct 21 by Zelle". */
export function partLine(p: {
  status: string; due_on: string | null; due_rule: string; paid_on: string | null; method: string | null
}): string {
  const d = (iso: string) => new Date(iso + 'T12:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
  if (p.status === 'paid') return `Paid${p.paid_on ? ' ' + d(p.paid_on) : ''}${p.method ? ' by ' + p.method : ''}`
  if (p.status === 'waived') return 'Waived'
  if (p.due_on) return `Due ${d(p.due_on)}`
  return p.due_rule === 'before_start' ? 'Due before the job — not scheduled yet'
    : p.due_rule === 'after_completion' ? 'Due when the job is finished' : 'No date yet'
}

export const FILTERS: { key: string; label: string }[] = [
  { key: 'open', label: 'Open' }, { key: 'overdue', label: 'Overdue' }, { key: 'due', label: 'Due' },
  { key: 'upcoming', label: 'Upcoming' }, { key: 'quoted', label: 'Quoted only' },
  { key: 'paid', label: 'Paid' }, { key: 'cancelled', label: 'Cancelled' }, { key: 'all', label: 'All' },
]
