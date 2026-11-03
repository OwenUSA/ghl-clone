/**
 * Owen AI in the browser (2026-11-02): what to draw, and how to word it.
 *
 * IMPORT-FREE on purpose, like `aiAgents.ts` and `speech.ts`, so it can be run under node by
 * a backend/tests/*_ui.py file. The SERVER decides who may use the module: every
 * /api/owen-ai route answers 404 unless the signed-in user is THE owner. The browser asks
 * GET /api/owen-ai/status once and draws the module only when that succeeds.
 */

export type OwenAiKind = 'daily' | 'job' | 'task' | 'audit'

/** The status query's retry rule: a 404 is the server saying "not yours" — never ask again.
    Anything else that is not a 4xx (a blip, a restart) is tried once more. */
export function owenAiRetry(failureCount: number, error: unknown): boolean {
  const status = (error as { status?: unknown } | null)?.status
  if (typeof status === 'number' && status >= 400 && status < 500) return false
  return failureCount < 1
}

/** The module is drawn only when /status answered. Loading, a 404 or any failure: hidden. */
export function owenAiVisible(q: { isSuccess: boolean; data?: unknown }): boolean {
  return q.isSuccess && q.data != null
}

export type ThreadLike = { id: number; kind: string; updated_at: string | null; archived?: boolean }
export type ThreadGroupName = 'Daily' | 'Jobs' | 'Other' | 'Audits'
// Audits (2026-11-03) are the scheduled sweep's conversations: read-only, listed last.
export const THREAD_GROUPS: readonly ThreadGroupName[] = ['Daily', 'Jobs', 'Other', 'Audits']

export function threadGroup(kind: string): ThreadGroupName {
  if (kind === 'daily') return 'Daily'
  if (kind === 'job') return 'Jobs'
  if (kind === 'audit') return 'Audits'
  return 'Other'
}

const time = (iso: string | null | undefined) => {
  const t = iso ? Date.parse(iso) : NaN
  return Number.isNaN(t) ? 0 : t
}

/** Daily / Jobs / Other / Audits, each newest first by updated_at. Empty groups are left out. */
export function groupThreads<T extends ThreadLike>(threads: T[]): { group: ThreadGroupName; threads: T[] }[] {
  return THREAD_GROUPS.map((group) => ({
    group,
    threads: threads.filter((t) => threadGroup(t.kind) === group)
      .sort((a, b) => time(b.updated_at) - time(a.updated_at) || b.id - a.id),
  })).filter((g) => g.threads.length > 0)
}

export type MessageLike = { role: string; status: string; created_at: string | null }

/** A conversation is busy while any of its messages is queued or running. */
export function threadBusy(t: { busy?: boolean; messages?: MessageLike[] } | null | undefined): boolean {
  if (!t) return false
  if (t.messages) return t.messages.some((m) => m.status === 'queued' || m.status === 'running')
  return !!t.busy
}

/** When the work being waited on began: the oldest queued or running message. */
export function busySince(messages: MessageLike[] | null | undefined): string | null {
  const m = (messages ?? []).find((x) => x.status === 'queued' || x.status === 'running')
  return m?.created_at ?? null
}

/** "0:07", "2:15", "1:02:03" since `iso`. Never negative. */
export function elapsed(iso: string | null | undefined, now: number = Date.now()): string {
  const s = Math.max(0, Math.floor((now - time(iso)) / 1000))
  if (!iso) return '0:00'
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const ss = String(s % 60).padStart(2, '0')
  return h ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${m}:${ss}`
}

/** "4.2 s" / "1 m 05 s". Unknown is null (left out of the footer). */
export function duration(ms: number | null | undefined): string | null {
  if (ms == null || !Number.isFinite(ms)) return null
  if (ms < 1000) return `${Math.round(ms)} ms`
  const s = ms / 1000
  if (s < 60) return `${s.toFixed(1)} s`
  return `${Math.floor(s / 60)} m ${String(Math.round(s % 60)).padStart(2, '0')} s`
}

/** The line under an answer: model · duration · turns. Cost is deliberately not shown. */
export function runFooter(m: { model?: string | null; duration_ms?: number | null; num_turns?: number | null }): string {
  const parts: string[] = []
  if (m.model) parts.push(m.model)
  const d = duration(m.duration_ms)
  if (d) parts.push(d)
  if (m.num_turns != null) parts.push(`${m.num_turns} turn${m.num_turns === 1 ? '' : 's'}`)
  return parts.join(' · ')
}

export const toolsLabel = (n: number) => `${n} tool${n === 1 ? '' : 's'} used`

/** A tool's input as readable JSON. */
export function prettyJson(value: unknown): string {
  if (typeof value === 'string') {
    try { return JSON.stringify(JSON.parse(value), null, 2) } catch { return value }
  }
  try { return JSON.stringify(value ?? null, null, 2) ?? String(value) } catch { return String(value) }
}

/** A result shortened for the step list. */
export function excerpt(value: unknown, max = 600): string {
  const text = typeof value === 'string' ? value : prettyJson(value)
  return text.length > max ? text.slice(0, max).trimEnd() + '…' : text
}

/** "1234" / "#1234" typed into the Job box, as the number the server stores. Empty → null. */
export function jobNumber(typed: string): string | null {
  const n = typed.trim().replace(/^#+/, '').trim()
  return n ? n.slice(0, 40) : null
}

/** A code fence split out of an answer, so the rest can go through the table/list parser. */
export type Segment = { kind: 'text'; text: string } | { kind: 'code'; lang: string; text: string }

export function splitFences(text: string): Segment[] {
  const out: Segment[] = []
  const lines = (text ?? '').split(/\r?\n/)
  let buf: string[] = []
  let code: { lang: string; lines: string[] } | null = null
  const flushText = () => { if (buf.join('').trim()) out.push({ kind: 'text', text: buf.join('\n') }); buf = [] }
  for (const line of lines) {
    const fence = /^\s*```\s*([\w+-]*)\s*$/.exec(line)
    if (code) {
      if (fence && !fence[1]) { out.push({ kind: 'code', lang: code.lang, text: code.lines.join('\n') }); code = null }
      else code.lines.push(line)
      continue
    }
    if (fence) { flushText(); code = { lang: fence[1], lines: [] }; continue }
    buf.push(line)
  }
  if (code) out.push({ kind: 'code', lang: code.lang, text: code.lines.join('\n') })
  flushText()
  return out
}

/** Inline `code` spans inside a run of text. */
export function codeSpans(text: string): { text: string; code: boolean }[] {
  const out: { text: string; code: boolean }[] = []
  const re = /`([^`]+)`/g
  let last = 0
  let m: RegExpExecArray | null
  while ((m = re.exec(text))) {
    if (m.index > last) out.push({ text: text.slice(last, m.index), code: false })
    out.push({ text: m[1], code: true })
    last = m.index + m[0].length
  }
  if (last < text.length) out.push({ text: text.slice(last), code: false })
  return out.length ? out : [{ text, code: false }]
}

// ---- the audit sweep's alerts (2026-11-03) -----------------------------------------------

/** The page's left-column switch. */
export type OwenAiTab = 'chats' | 'alerts'

export type Severity = 'urgent' | 'today' | 'watch'
export const SEVERITIES: readonly Severity[] = ['urgent', 'today', 'watch']
export const SEVERITY_LABEL: Record<Severity, string> = { urgent: 'Urgent', today: 'Today', watch: 'Watch' }

/** Urgent / Today / Watch, in the server's order within each. Empty groups are left out;
    an unknown severity is shown as Watch rather than dropped. */
export function groupAlerts<T extends { severity: string }>(alerts: T[]): { severity: Severity; alerts: T[] }[] {
  const of = (s: string): Severity => (SEVERITIES as readonly string[]).includes(s) ? s as Severity : 'watch'
  return SEVERITIES.map((severity) => ({ severity, alerts: alerts.filter((a) => of(a.severity) === severity) }))
    .filter((g) => g.alerts.length > 0)
}

/** The number on the Alerts tab: what needs doing today or sooner. Red when anything is urgent. */
export function alertsBadge(c: { urgent?: number; today?: number } | null | undefined): { n: number; red: boolean } {
  const urgent = c?.urgent ?? 0
  return { n: urgent + (c?.today ?? 0), red: urgent > 0 }
}

const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/** "just now", "3 h ago", "yesterday", "Mon", "Oct 12" — for "since …" and closed times. */
export function relativeDay(iso: string | null | undefined, now: number = Date.now()): string {
  const t = time(iso)
  if (!t) return ''
  const mins = Math.floor((now - t) / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins} min ago`
  const d = new Date(t)
  const startOfToday = new Date(now); startOfToday.setHours(0, 0, 0, 0)
  if (t >= startOfToday.getTime()) return `${Math.floor(mins / 60)} h ago`
  const days = Math.ceil((startOfToday.getTime() - t) / 86400000)
  if (days <= 1) return 'yesterday'
  if (days < 7) return DAYS[d.getDay()]
  return `${MONTHS[d.getMonth()]} ${d.getDate()}`
}

/** A scheduled audit's machine prompt, as one small line: "Audit #801". */
export function auditPromptLabel(content: string): string {
  const m = /#\s*([\w-]+)/.exec(content ?? '')
  return m ? `Audit #${m[1]}` : 'Audit run'
}
