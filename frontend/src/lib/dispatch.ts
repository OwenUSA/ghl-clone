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

// ---------------- the assistant (2026-10-01) ----------------

/** "Good morning, Owen" — New York time, whatever the laptop's zone. First name only. */
export function greeting(name: string | null | undefined, now: Date = new Date()): string {
  const hour = Number(now.toLocaleString('en-US', { hour: 'numeric', hour12: false,
    timeZone: 'America/New_York' })) % 24
  const part = hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening'
  const first = (name ?? '').trim().split(/\s+/)[0]
  return first ? `${part}, ${first}` : part
}

/** The questions the office asks most, offered as one-click chips. */
export const SUGGESTED_QUESTIONS: readonly string[] = [
  'What is urgent right now?',
  'What are the next steps for today?',
  'Which new jobs has nobody called yet?',
  'Who is waiting on a repair date?',
  'Which missed calls and texts should I return?',
  'What visits are booked for tomorrow?',
  'Which inspections are done and need the customer call?',
  'Where did we talk to a customer but Zuper was not updated?',
]

/** A piece of a line: plain text, or **bold**. */
export type Inline = { text: string; bold: boolean }
/** A block of an answer: a paragraph, a bulleted list, a numbered list, a heading or a table. */
export type Block =
  | { kind: 'p'; parts: Inline[] }
  | { kind: 'h'; parts: Inline[] }
  | { kind: 'ul' | 'ol'; items: Inline[][] }
  | { kind: 'table'; head: Inline[][]; rows: Inline[][][] }

/** The "|---|---|" line under a table's header. */
const SEPARATOR = /^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?$/

function cells(line: string): string[] {
  return line.replace(/^\|/, '').replace(/\|$/, '').split('|').map((c) => c.trim())
}

/** A table written on ONE line ("| a | b | |---|---| | 1 | 2 |"), back into its rows. */
function unfold(line: string): string[] {
  if (!/\|\s*:?-{3,}/.test(line) || (line.match(/\|/g) ?? []).length < 6) return [line]
  const parts = line.split(/\|\s+\|/)
  return parts.map((r, i) => `${i === 0 ? '' : '|'}${r}${i === parts.length - 1 ? '' : '|'}`.trim())
}

export function inline(text: string): Inline[] {
  const out: Inline[] = []
  const re = /\*\*([^*]+)\*\*/g
  let last = 0
  let m: RegExpExecArray | null
  while ((m = re.exec(text))) {
    if (m.index > last) out.push({ text: text.slice(last, m.index), bold: false })
    out.push({ text: m[1], bold: true })
    last = m.index + m[0].length
  }
  if (last < text.length) out.push({ text: text.slice(last), bold: false })
  return out.length ? out : [{ text, bold: false }]
}

/**
 * The assistant's answer as blocks, from the small bit of Markdown models write: paragraphs,
 * "- " / "* " bullets, "1. " numbered lists, "#" headings and **bold**. Rendered as React
 * elements, never as HTML — an answer cannot inject markup into the page.
 */
export function blocks(text: string): Block[] {
  const out: Block[] = []
  let para: string[] = []
  const flush = () => {
    if (para.length) out.push({ kind: 'p', parts: inline(para.join(' ')) })
    para = []
  }
  let table: { head: string[]; rows: string[][] } | null = null
  let pending: string | null = null          // a "| a | b |" line that may start a table
  const flushTable = () => {
    if (table) {
      out.push({ kind: 'table', head: table.head.map(inline),
        rows: table.rows.map((r) => r.map(inline)) })
    }
    table = null
  }
  const lines = (text ?? '').split(/\r?\n/).flatMap((raw) => unfold(raw.trim()))
  for (const line of lines) {
    if (table) {
      if (line.startsWith('|')) { table.rows.push(cells(line)); continue }
      flushTable()
    }
    if (pending !== null) {
      if (SEPARATOR.test(line)) {
        flush()
        table = { head: cells(pending), rows: [] }
        pending = null
        continue
      }
      para.push(pending)
      pending = null
    }
    if (line.startsWith('|') && line.endsWith('|') && line.length > 2) { pending = line; continue }
    const bullet = /^[-*•]\s+(.*)$/.exec(line)
    const numbered = /^\d+[.)]\s+(.*)$/.exec(line)
    const heading = /^#{1,6}\s+(.*)$/.exec(line)
    if (!line) { flush(); continue }
    if (heading) { flush(); out.push({ kind: 'h', parts: inline(heading[1]) }); continue }
    if (bullet || numbered) {
      flush()
      const kind = bullet ? 'ul' : 'ol'
      const item = inline((bullet ?? numbered)![1])
      const prev = out[out.length - 1]
      if (prev && prev.kind === kind) prev.items.push(item)
      else out.push({ kind, items: [item] })
      continue
    }
    para.push(line)
  }
  if (pending !== null) para.push(pending)
  flushTable()
  flush()
  return out
}

// ---------------- saved chats (2026-10-01) ----------------

export type ChatGroup = 'Today' | 'Yesterday' | 'Previous 7 days' | 'Older'
export const CHAT_GROUPS: readonly ChatGroup[] = ['Today', 'Yesterday', 'Previous 7 days', 'Older']

/** The New York calendar day of an instant, as days since 1970 — so "yesterday" is the office's. */
function nyDay(d: Date): number {
  const key = d.toLocaleDateString('en-CA', { timeZone: 'America/New_York' }) // YYYY-MM-DD
  const [y, m, day] = key.split('-').map(Number)
  return Math.floor(Date.UTC(y, m - 1, day) / 86400000)
}

/** Which sidebar group a chat belongs in, by its updated_at in New York time. */
export function chatGroup(updatedAt: string, now: Date = new Date()): ChatGroup {
  const days = nyDay(now) - nyDay(new Date(updatedAt))
  if (days <= 0) return 'Today'
  if (days === 1) return 'Yesterday'
  if (days <= 7) return 'Previous 7 days'
  return 'Older'
}

/** Chats grouped for the sidebar, newest first inside each group; empty groups are left out. */
export function groupChats<T extends { updated_at: string }>(chats: T[],
  now: Date = new Date()): { group: ChatGroup; chats: T[] }[] {
  const sorted = [...chats].sort((a, b) => Date.parse(b.updated_at) - Date.parse(a.updated_at))
  return CHAT_GROUPS.map((group) => ({ group,
    chats: sorted.filter((c) => chatGroup(c.updated_at, now) === group) }))
    .filter((g) => g.chats.length > 0)
}

/** A step's tool in plain words. */
export const STEP_LABELS: Record<string, string> = {
  list_items: 'Listed open items', find_jobs: 'Searched jobs', get_job: 'Opened job',
  find_slots: 'Looked for visit slots', suggest_change: 'Recorded a suggestion',
}
export function stepLabel(tool: string): string {
  return STEP_LABELS[tool] ?? tool.replace(/_/g, ' ')
}

/** A step's arguments on one line: `queue: missed · limit: 10`. Empty values are left out. */
export function stepArgs(args: Record<string, unknown> | null | undefined): string {
  return Object.entries(args ?? {})
    .filter(([, v]) => v !== null && v !== undefined && v !== '')
    .map(([k, v]) => `${k}: ${typeof v === 'string' ? v : JSON.stringify(v)}`)
    .join(' · ')
}

/** Text cut to `max` characters with an ellipsis. */
export function clip(text: string | null | undefined, max = 300): string {
  const t = text ?? ''
  return t.length > max ? `${t.slice(0, max).trimEnd()}…` : t
}

// ---------------- the week plan (2026-10-01) ----------------

function nyParts(iso: string): { time: string; ampm: string } {
  const s = new Date(iso).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit',
    timeZone: 'America/New_York' }) // "8:00 AM"
  const [time, ampm = ''] = s.split(/\s+/)
  return { time, ampm }
}

/** "8:00–10:00 AM", or "11:00 AM–1:00 PM" across noon — New York time. */
export function visitRange(start: string, end: string): string {
  const a = nyParts(start)
  const b = nyParts(end)
  return a.ampm === b.ampm ? `${a.time}–${b.time} ${b.ampm}`
    : `${a.time} ${a.ampm}–${b.time} ${b.ampm}`
}

/** "~55 min driving" / "~1 h 20 min driving". */
export function driveLabel(minutes: number): string {
  const m = Math.max(0, Math.round(minutes))
  if (m < 60) return `~${m} min driving`
  const h = Math.floor(m / 60)
  const r = m % 60
  return `~${h} h${r ? ` ${r} min` : ''} driving`
}

export const KIND_LABEL: Record<string, string> = { inspection: 'Inspection', repair: 'Repair' }

type PlanVisitLike = { job_number: string | null; customer: string | null; city: string | null
  kind: string; start: string; end: string; existing: boolean; drive_minutes_before: number }

/** "#273 Jane Doe — Weston", leaving out what is unknown. */
export function visitTitle(v: { job_number: string | null; customer: string | null;
  city: string | null }): string {
  const head = [v.job_number ? `#${v.job_number}` : '', v.customer ?? ''].filter(Boolean).join(' ')
  return [head || 'Job', v.city].filter(Boolean).join(' — ')
}

/** The plan as plain text, for the clipboard. */
export function planText(plan: {
  days: { label: string; techs: { tech: string; start_from: string; count: number;
    capacity: number; drive_minutes: number; visits: PlanVisitLike[] }[] }[]
  unplaced: { job_number: string | null; customer: string | null; city: string | null;
    kind: string; reason: string }[]
}): string {
  const lines: string[] = ['DRAFT — nothing is booked until it is booked in Zuper.', '']
  for (const d of plan.days) {
    lines.push(d.label)
    for (const t of d.techs) {
      lines.push(`  ${t.tech} — ${t.count} of ${t.capacity}, ${driveLabel(t.drive_minutes)}, from ${t.start_from}`)
      for (const v of t.visits) {
        const drive = v.drive_minutes_before ? ` (+${v.drive_minutes_before} min drive)` : ''
        lines.push(`    ${visitRange(v.start, v.end)}  ${visitTitle(v)} [${KIND_LABEL[v.kind] ?? v.kind}]` +
          `${v.existing ? ' (booked)' : ''}${drive}`)
      }
    }
    lines.push('')
  }
  if (plan.unplaced.length) {
    lines.push('Not placed')
    for (const u of plan.unplaced) {
      lines.push(`  ${visitTitle(u)} [${KIND_LABEL[u.kind] ?? u.kind}] — ${u.reason}`)
    }
  }
  return lines.join('\n').trimEnd() + '\n'
}

/** Asked for the user when they attach a spreadsheet and type nothing. */
export const DEFAULT_FILE_QUESTION = 'Read this file from top to bottom and compare every row with '
  + 'Zuper. Tell me what is different, what is missing and what needs attention.'

/** At most this many spreadsheets on one message. */
export const MAX_CHAT_FILES = 3

/** "N rows in M sheets" — the chip under an attached spreadsheet. */
export function fileSummary(rows: number, sheets?: number): string {
  const r = `${rows.toLocaleString('en-US')} row${rows === 1 ? '' : 's'}`
  if (sheets == null) return r
  return `${r} in ${sheets} sheet${sheets === 1 ? '' : 's'}`
}

/** True when the picked file's name says it is one the assistant can read. The server decides
 * for certain; this only saves an upload that is bound to be refused. */
export function isSpreadsheetName(name: string): boolean {
  return /\.(xlsx|xlsm|csv)$/i.test(name)
}
