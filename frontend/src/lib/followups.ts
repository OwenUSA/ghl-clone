/**
 * Follow-ups in the browser (2026-11-11, app/cadences): wording only. The SERVER decides every
 * rule (what is due, what stops a follow-up, when a step runs); this only words it. Times are
 * shown in the account's zone (America/New_York), never the laptop's.
 */

export const SHOWS: { key: string; label: string }[] = [
  { key: 'due', label: 'Call now' }, { key: 'open', label: 'Open' }, { key: 'paused', label: 'Paused' },
  { key: 'closed', label: 'Closed' }, { key: 'all', label: 'All' },
]

export const STATE: Record<string, { label: string; fg: string; bg: string }> = {
  open: { label: 'Open', fg: 'rgb(54,89,148)', bg: 'rgb(239,244,255)' },
  due: { label: 'Call now', fg: 'rgb(181,71,8)', bg: 'rgb(255,250,235)' },
  overdue: { label: 'Overdue', fg: 'rgb(180,35,24)', bg: 'rgb(254,243,242)' },
  paused: { label: 'Paused', fg: 'rgb(71,84,103)', bg: 'rgb(242,244,247)' },
  stopped: { label: 'Stopped', fg: 'rgb(2,122,72)', bg: 'rgb(236,253,243)' },
  no_response: { label: 'No response', fg: 'rgb(71,84,103)', bg: 'rgb(242,244,247)' },
}

export function chipFor(r: { state: string; due: boolean; overdue: boolean }) {
  const key = r.overdue ? 'overdue' : r.due ? 'due' : r.state
  return STATE[key] ?? { label: r.state, fg: 'rgb(71,84,103)', bg: 'rgb(242,244,247)' }
}

const FMT = new Intl.DateTimeFormat('en-US', {
  timeZone: 'America/New_York', weekday: 'short', month: 'short', day: 'numeric',
  hour: 'numeric', minute: '2-digit',
})

/** "Wed, Oct 21, 10:00 AM" in New York time. */
export function when(iso: string | null | undefined): string {
  return iso ? FMT.format(new Date(iso)) : '—'
}

/** "in 25 min" / "3 h ago" / "in 2 days". */
export function relative(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return ''
  const mins = Math.round((new Date(iso).getTime() - now) / 60000)
  const a = Math.abs(mins)
  const span = a < 60 ? `${a} min` : a < 48 * 60 ? `${Math.round(a / 60)} h` : `${Math.round(a / 1440)} days`
  return mins >= 0 ? `in ${span}` : `${span} ago`
}

/** The timeline line's marker colour, by what happened. */
export function eventTone(kind: string, state: string | null): string {
  if (state === 'failed') return 'rgb(180,35,24)'
  if (kind === 'reply' || kind === 'call_back' || kind === 'reached' || kind === 'stopped') return 'rgb(2,122,72)'
  if (kind === 'text' || kind === 'ai_call') return state === 'shadow' ? 'rgb(152,162,179)' : 'rgb(21,94,239)'
  if (kind === 'call_due') return 'rgb(181,71,8)'
  return 'rgb(152,162,179)'
}

export const EVENT_LABEL: Record<string, string> = {
  started: 'Started', team_call: 'Team call', team_text: 'Team text', ai_text: 'AI text',
  reply: 'Customer texted', call_back: 'Customer called', reached: 'Reached', text: 'Text',
  ai_call: 'AI call', call_due: 'Call due', step: 'Step', stopped: 'Stopped', closed: 'Closed',
  paused: 'Paused', resumed: 'Resumed', reassigned: 'Reassigned', waiting: 'Waiting', note: 'Note',
}

export const STEP_LABEL: Record<string, string> = { text: 'Text', call_me: 'Call (me)', ai_call: 'AI call' }

/** The digits a person typed, if they make a US number. */
export function tenDigits(raw: string): string | null {
  const d = raw.replace(/\D/g, '')
  const ten = d.length === 11 && d.startsWith('1') ? d.slice(1) : d
  return ten.length === 10 ? ten : null
}
