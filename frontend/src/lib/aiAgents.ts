/**
 * AI Agents in the browser (2026-09-15): what to draw, and how to word it.
 *
 * IMPORT-FREE on purpose, like `access.ts` and `pipelines.ts`:
 * `backend/tests/test_ai_agents_ui.py` runs this very file under node. The SERVER
 * enforces every rule here (app/ai/api.py); this module only decides what is offered, so
 * nothing is drawn that the server would refuse.
 */

export type AiUser = { role: string; only_assigned_data?: boolean | null }

/** ADMIN and DISPATCHER, and never a user with "Only assigned data" on (the rule of
    access.ts `isRestricted`, copied so this file stays import-free). */
export function canOpenAiAgents(user: AiUser | null | undefined): boolean {
  if (!user) return false
  if (user.role === 'ADMIN') return true
  return user.role === 'DISPATCHER' && !user.only_assigned_data
}

export const isAiAdmin = (user: AiUser | null | undefined) => !!user && user.role === 'ADMIN'

export type AiTab = 'agents' | 'knowledge' | 'templates' | 'logs'

/** The module's tabs. Agent Logs is not drawn for a dispatcher (logs are ADMIN-only). */
export function aiTabs(user: AiUser | null | undefined): { key: AiTab; label: string }[] {
  if (!canOpenAiAgents(user)) return []
  const tabs: { key: AiTab; label: string }[] = [
    { key: 'agents', label: 'Agents' },
    { key: 'knowledge', label: 'Knowledge Base' },
    { key: 'templates', label: 'Templates' },
  ]
  if (isAiAdmin(user)) tabs.push({ key: 'logs', label: 'Agent Logs' })
  return tabs
}

export type Mode = 'off' | 'suggest' | 'auto'
export const MODE_LABEL: Record<Mode, string> = { off: 'Off', suggest: 'Suggest', auto: 'Auto-pilot' }

export const modeLabel = (m: string) => MODE_LABEL[m as Mode] ?? m

/** A dispatcher may switch an agent OFF (the emergency stop) and nothing else. */
export function modesFor(user: AiUser | null | undefined): Mode[] {
  if (isAiAdmin(user)) return ['off', 'suggest', 'auto']
  return canOpenAiAgents(user) ? ['off'] : []
}

/** Switching to Auto-pilot must be confirmed; the API refuses it (409) otherwise. */
export const needsConfirm = (from: string, to: string) => to === 'auto' && from !== 'auto'

export const MODE_TONE: Record<string, { fg: string; bg: string }> = {
  off: { fg: 'rgb(71,84,103)', bg: 'rgb(242,244,247)' },
  suggest: { fg: 'rgb(181,71,8)', bg: 'rgb(255,250,235)' },
  auto: { fg: 'rgb(2,122,72)', bg: 'rgb(236,253,243)' },
}

export const BUILDER_SECTIONS = [
  { key: 'basics', label: 'Basics' },
  { key: 'persona', label: 'Persona & goals' },
  { key: 'rules', label: 'Rules' },
  { key: 'knowledge', label: 'Knowledge' },
  { key: 'actions', label: 'Actions' },
  { key: 'triggers', label: 'Triggers' },
  { key: 'schedule', label: 'Schedule & behaviour' },
  { key: 'connection', label: 'AI connection' },
  { key: 'escalation', label: 'Escalation' },
  { key: 'advanced', label: 'Advanced' },
  { key: 'prompt', label: 'Compiled prompt' },
  { key: 'versions', label: 'Versions' },
] as const

export type BuilderSection = (typeof BUILDER_SECTIONS)[number]['key'] | 'call'

/**
 * A VOICE agent's sections (2026-09-25). No triggers, schedule, AI connection or escalation:
 * a call starts it through the phone system's flow, which also holds the business hours, and
 * owen-voice uses its own model key. "Call settings" holds what owen-main needs instead.
 */
export const VOICE_SECTIONS: { key: BuilderSection; label: string }[] = [
  { key: 'basics', label: 'Basics' },
  { key: 'call', label: 'Call settings' },
  { key: 'persona', label: 'Persona & goals' },
  { key: 'rules', label: 'Rules' },
  { key: 'knowledge', label: 'In-call knowledge' },
  { key: 'actions', label: 'Actions' },
  { key: 'advanced', label: 'Advanced' },
  { key: 'prompt', label: 'What the phone system gets' },
  { key: 'versions', label: 'Versions' },
]

export function sectionsFor(channel: string): { key: BuilderSection; label: string }[] {
  return channel === 'voice' ? VOICE_SECTIONS : [...BUILDER_SECTIONS]
}

export const CHANNEL_LABEL: Record<string, string> = { text: 'Text / Chat', voice: 'Voice' }
export const channelLabel = (c: string) => CHANNEL_LABEL[c] ?? c

/** The catalogue's actions a channel may be given — never another channel's. */
export function actionsFor<T extends { channels: string[] }>(actions: T[], channel: string): T[] {
  return actions.filter((a) => a.channels.includes(channel))
}

/** owen-main's KNOWLEDGE_MAX_CHARS. Over it, Publish is refused (the server says so too). */
export const VOICE_KNOWLEDGE_MAX = 6000

export function knowledgeBudget(text: string | null | undefined): { chars: number; over: number; label: string } {
  const chars = (text ?? '').trim().length
  const over = Math.max(0, chars - VOICE_KNOWLEDGE_MAX)
  const n = (x: number) => x.toLocaleString('en-US')
  return { chars, over,
    label: `${n(chars)} / ${n(VOICE_KNOWLEDGE_MAX)} characters` + (over ? ` — ${n(over)} over; it cannot be published` : '') }
}

export type PhoneSystemState = {
  status: string; live: boolean; label: string; detail: string; can_retry: boolean
  version?: number; live_version?: number | null; pushed_at?: string | null
  owen_version?: number | null; attempts?: number; last_error?: string | null; imported?: boolean
}

/** Green only when owen-main confirmed it; amber while it is on its way; red when it stopped. */
export function phoneSystemTone(status: string): { fg: string; bg: string } {
  if (status === 'live') return { fg: 'rgb(2,122,72)', bg: 'rgb(236,253,243)' }
  if (status === 'refused' || status === 'failed') return { fg: 'rgb(180,35,24)', bg: 'rgb(254,243,242)' }
  if (status === 'unpublished') return { fg: 'rgb(71,84,103)', bg: 'rgb(242,244,247)' }
  return { fg: 'rgb(181,71,8)', bg: 'rgb(255,250,235)' }
}

/** Poll while the answer is still coming. */
export const phoneSystemPending = (status: string | undefined) =>
  status === 'pending' || status === 'retrying'

// ---------------- the draft ----------------

export type Trigger = { type: string; minutes?: number; pipeline_id?: number; stage_id?: number }

export type Schedule = { enabled: boolean; timezone: string; days: string[]; start: string; end: string }

export type Draft = {
  channel: string
  persona: string
  goals: string[]
  rules_do: string[]
  rules_dont: string[]
  knowledge_base_ids: number[]
  actions: string[]
  triggers: Trigger[]
  schedule: Schedule
  sleep_on_staff_reply: boolean
  wait_minutes: number
  max_messages: number
  connection_id: number | null
  model: string
  extra_instructions: string
  escalation_user_ids: number[]
  // Voice only (2026-09-25) — the CRM's names for owen-main's settings; app/ai/voice.py maps them.
  owen_agent?: string
  greeting?: string
  voice?: string
  llm_base_url?: string
  stt_provider?: string
  tts_provider?: string
  engine?: string
  knowledge_text?: string
  guardrails?: { max_call_seconds?: number | null; max_silence_seconds?: number | null; [k: string]: unknown }
  transfer_targets?: Record<string, { kind?: string; target?: string }>
  context_provider?: Record<string, unknown> | null
  custom_tools?: Record<string, unknown>[]
  owen_settings?: Record<string, unknown>
}

export function setItem(list: string[], i: number, value: string): string[] {
  return list.map((v, j) => (j === i ? value : v))
}
export const addItem = (list: string[]) => [...list, '']
export const removeItem = <T,>(list: T[], i: number) => list.filter((_, j) => j !== i)
export function moveItem<T>(list: T[], i: number, delta: -1 | 1): T[] {
  const j = i + delta
  if (i < 0 || j < 0 || i >= list.length || j >= list.length) return list
  const out = [...list]
  ;[out[i], out[j]] = [out[j], out[i]]
  return out
}

/** Toggle an id or name in a set-like list, keeping the given catalogue order. */
export function toggle<T>(list: T[], value: T, order?: T[]): T[] {
  const next = list.includes(value) ? list.filter((v) => v !== value) : [...list, value]
  return order ? order.filter((v) => next.includes(v)) : next
}

/** What is sent: blank list entries dropped (the server drops them too). */
export function draftForSave(d: Draft): Draft {
  const clean = (l: string[]) => l.map((s) => s.trim()).filter(Boolean)
  return { ...d, goals: clean(d.goals), rules_do: clean(d.rules_do), rules_dont: clean(d.rules_dont) }
}

export const sameDraft = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b)

export const TRIGGER_LABEL: Record<string, string> = {
  missed_call: 'Unanswered inbound call',
  inbound_text: 'Inbound text unanswered',
  stage_entered: 'Opportunity enters stage',
  appointment_booked: 'Appointment booked',
  appointment_rescheduled: 'Appointment rescheduled',
  appointment_cancelled: 'Appointment cancelled',
  manual: 'Manual',
  test: 'Try-it',
}

export function triggerSummary(t: Trigger, stageName?: (id: number) => string | undefined): string {
  const label = TRIGGER_LABEL[t.type] ?? t.type
  if (t.type === 'inbound_text') return `${label} for ${t.minutes ?? 10} min`
  if (t.type === 'stage_entered') {
    const name = t.stage_id != null ? stageName?.(t.stage_id) : undefined
    return name ? `${label}: ${name}` : label
  }
  return label
}

/** A new trigger of this type, with the defaults the server applies. */
export function newTrigger(type: string): Trigger {
  if (type === 'inbound_text') return { type, minutes: 10 }
  return { type }
}

export const DAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'] as const
const DAY_LABEL: Record<string, string> = {
  mon: 'Mon', tue: 'Tue', wed: 'Wed', thu: 'Thu', fri: 'Fri', sat: 'Sat', sun: 'Sun',
}
export const dayLabel = (d: string) => DAY_LABEL[d] ?? d

function clock(hhmm: string): string {
  const [h, m] = hhmm.split(':').map(Number)
  const suffix = h >= 12 ? 'PM' : 'AM'
  const h12 = h % 12 === 0 ? 12 : h % 12
  return `${h12}:${String(m).padStart(2, '0')} ${suffix}`
}

export function scheduleSummary(s: Schedule | null | undefined): string {
  if (!s || !s.enabled) return 'Any time'
  if (s.days.length === 0) return 'Never (no days selected)'
  const days = s.days.length === 7 ? 'Every day'
    : s.days.join(',') === 'mon,tue,wed,thu,fri' ? 'Mon–Fri'
      : s.days.map(dayLabel).join(', ')
  return `${days}, ${clock(s.start)} – ${clock(s.end)} Eastern`
}

// ---------------- numbers ----------------

/** "$0.001825" style from the API's decimal string. Unknown is "—", never "$0". */
export function formatCost(cost: string | null | undefined): string {
  if (cost == null || cost === '') return '—'
  const n = Number(cost)
  if (!Number.isFinite(n)) return '—'
  if (n === 0) return '$0.00'
  if (n < 0.01) return '$' + n.toFixed(4)
  return '$' + n.toFixed(2)
}

export function formatTokens(t: { input: number; output: number; cache_write?: number; cache_read?: number }
  | null | undefined): string {
  if (!t) return '—'
  const k = (n: number) => (n >= 10000 ? (n / 1000).toFixed(1) + 'k' : String(n))
  const cache = (t.cache_write ?? 0) + (t.cache_read ?? 0)
  return `${k(t.input)} in · ${k(t.output)} out` + (cache ? ` · ${k(cache)} cache` : '')
}

export function formatLatency(ms: number | null | undefined): string {
  if (ms == null) return '—'
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`
}

export function formatBytes(n: number | null | undefined): string {
  if (n == null) return '—'
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

export const OUTCOME_LABEL: Record<string, string> = {
  queued: 'Queued', running: 'Running', completed: 'Completed', escalated: 'Escalated',
  refused: 'Refused', error: 'Error', skipped: 'Skipped',
}

export const OUTCOME_TONE: Record<string, { fg: string; bg: string }> = {
  queued: { fg: 'rgb(71,84,103)', bg: 'rgb(242,244,247)' },
  running: { fg: 'rgb(21,94,239)', bg: 'rgb(239,244,255)' },
  completed: { fg: 'rgb(2,122,72)', bg: 'rgb(236,253,243)' },
  escalated: { fg: 'rgb(181,71,8)', bg: 'rgb(255,250,235)' },
  refused: { fg: 'rgb(180,35,24)', bg: 'rgb(254,243,242)' },
  error: { fg: 'rgb(180,35,24)', bg: 'rgb(254,243,242)' },
  skipped: { fg: 'rgb(102,112,133)', bg: 'rgb(249,250,251)' },
}

export const outcomeLabel = (o: string) => OUTCOME_LABEL[o] ?? o

export const ACTION_STATUS_LABEL: Record<string, string> = {
  executed: 'Done', suggested: 'Suggested', refused: 'Refused', would: 'Would',
}

/** The Try-it card. `summary` is the server's sentence ("Move “Roof” to stage “Inspection”"). */
export const wouldText = (card: { summary?: string; label?: string }) =>
  'Would: ' + (card.summary || card.label || 'act')

// ---------------- files ----------------

export const MAX_FILE_BYTES = 10 * 1024 * 1024

/** Why a chosen file cannot be uploaded, or null. The server extracts PDF and .docx only. */
export function fileProblem(name: string, size: number): string | null {
  const lower = name.toLowerCase()
  if (!lower.endsWith('.pdf') && !lower.endsWith('.docx')) {
    return 'Only PDF and Word (.docx) files can be added — paste other text as an article.'
  }
  if (size > MAX_FILE_BYTES) return 'That file is larger than 10 MB.'
  if (size === 0) return 'That file is empty.'
  return null
}

// ---------------- connections ----------------

export type ConnectionForm = {
  name: string
  provider: string
  base_url: string
  api_key: string
  default_model: string
  price_input: string
  price_output: string
}

const PRICE = /^\d+(\.\d{1,6})?$/

/** Sentences for what stops the form saving. `editing`: a blank key keeps the stored one. */
export function connectionProblems(f: ConnectionForm, editing: boolean): string[] {
  const out: string[] = []
  if (!f.name.trim()) out.push('Give the connection a name.')
  if (!['anthropic', 'openai', 'openai_compatible'].includes(f.provider)) out.push('Choose a provider.')
  if (f.provider === 'openai_compatible') {
    if (!f.base_url.trim()) out.push('An OpenAI-compatible connection needs its base URL.')
    else if (!/^https?:\/\//.test(f.base_url.trim())) out.push('The base URL must start with https://.')
  }
  if (!editing && !f.api_key.trim()) out.push('Enter the API key.')
  if (!f.default_model.trim()) out.push('Choose a default model.')
  for (const [v, what] of [[f.price_input, 'input'], [f.price_output, 'output']] as const) {
    if (v.trim() && !PRICE.test(v.trim())) out.push(`The ${what} price must be dollars, like 2.50.`)
  }
  return out
}

/** The request body. The base URL is only sent for a compatible server; a blank key on
    an edit is left out, so the stored key is kept; blank prices are unknown (null). */
export function connectionBody(f: ConnectionForm, editing: boolean): Record<string, unknown> {
  const body: Record<string, unknown> = {
    name: f.name.trim(),
    default_model: f.default_model.trim(),
    price_input: f.price_input.trim() || null,
    price_output: f.price_output.trim() || null,
    base_url: f.provider === 'openai_compatible' ? f.base_url.trim() : null,
  }
  if (!editing) body.provider = f.provider
  if (f.api_key.trim()) body.api_key = f.api_key.trim()
  return body
}

export const DEFAULT_MODEL: Record<string, string> = {
  anthropic: 'claude-sonnet-5', openai: 'gpt-5-mini', openai_compatible: '',
}

/** Prices to prefill when the model is known; null when it is not (leave what is typed). */
export function knownPrice(prices: Record<string, { input: string | null; output: string | null }>,
  model: string): { input: string; output: string } | null {
  const hit = prices[model.trim()]
  if (!hit || hit.input == null || hit.output == null) return null
  const trim = (s: string) => String(Number(s))
  return { input: trim(hit.input), output: trim(hit.output) }
}

// ---------------- metrics ----------------

export type Bar = { label: string; value: number; pct: number; text: string }

/** Rows -> bars scaled to the largest value (0-100). */
export function bars<T>(rows: T[], label: (r: T) => string, value: (r: T) => number,
  text: (r: T) => string = (r) => String(value(r))): Bar[] {
  const max = Math.max(0, ...rows.map(value))
  return rows.map((r) => ({
    label: label(r), value: value(r), text: text(r),
    pct: max > 0 ? Math.round((value(r) / max) * 100) : 0,
  }))
}

/** Outcome counts in a fixed order, zero ones left out. */
export function outcomeRows(outcomes: Record<string, number>): { key: string; label: string; count: number }[] {
  return Object.keys(OUTCOME_LABEL)
    .filter((k) => (outcomes[k] ?? 0) > 0)
    .map((k) => ({ key: k, label: OUTCOME_LABEL[k], count: outcomes[k] }))
}

/** "Sep 15, 2:05 PM" in the account zone. */
export function stamp(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleString('en-US', {
    timeZone: 'America/New_York', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
  })
}
