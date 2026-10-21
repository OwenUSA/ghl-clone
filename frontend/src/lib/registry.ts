/**
 * The agent registry in the browser (2026-10-21, app/registry): grouping, wording, colours.
 *
 * IMPORT-FREE on purpose, like `aiAgents.ts`: `backend/tests/test_registry_ui.py` runs this file
 * under node. The SERVER enforces every rule (ADMIN only, PROMOTE, the choices per action);
 * this module only decides what is offered.
 */

export type Level = 'never' | 'shadow' | 'approve' | 'auto'
export const LEVELS: Level[] = ['never', 'shadow', 'approve', 'auto']
export const LEVEL_LABEL: Record<Level, string> = {
  never: 'Never', shadow: 'Shadow', approve: 'Approve each', auto: 'Automatic',
}
export const levelLabel = (l: string) => LEVEL_LABEL[l as Level] ?? l

/** What each rung means, in one line, for the page's legend. */
export const LEVEL_HELP: Record<Level, string> = {
  never: 'Does not do it.',
  shadow: 'Works it out and records it. Nothing is sent or changed.',
  approve: 'Asks first. A person approves each one.',
  auto: 'Does it on its own. Every action is logged.',
}

export const LEVEL_TONE: Record<string, { fg: string; bg: string }> = {
  never: { fg: 'rgb(71,84,103)', bg: 'rgb(242,244,247)' },
  shadow: { fg: 'rgb(83,56,158)', bg: 'rgb(244,243,255)' },
  approve: { fg: 'rgb(181,71,8)', bg: 'rgb(255,250,235)' },
  auto: { fg: 'rgb(2,122,72)', bg: 'rgb(236,253,243)' },
}

export const STATUS_TONE: Record<string, { fg: string; bg: string; label: string }> = {
  on: { fg: 'rgb(2,122,72)', bg: 'rgb(236,253,243)', label: 'On' },
  test: { fg: 'rgb(181,71,8)', bg: 'rgb(255,250,235)', label: 'Testing' },
  off: { fg: 'rgb(71,84,103)', bg: 'rgb(242,244,247)', label: 'Off' },
  outside: { fg: 'rgb(54,89,148)', bg: 'rgb(239,244,255)', label: 'Outside the CRM' },
  unknown: { fg: 'rgb(180,35,24)', bg: 'rgb(254,243,242)', label: 'Unknown' },
}

const RANK: Record<string, number> = { never: 0, shadow: 1, approve: 2, auto: 3 }

/** More freedom than before: the server wants a reason and the typed confirmation. */
export const isPromotion = (from: string, to: string) => (RANK[to] ?? 0) > (RANK[from] ?? 0)

export type RoleGroup<E> = { role: number | null; name: string; entries: E[] }

/**
 * Entries under their role, in the boss's order (1–10), with every role listed even when
 * empty — an empty role is information: nothing does that job yet. Entries with no role
 * come last, under "Not assigned to a role".
 */
export function groupByRole<E extends { role: number | null; name: string }>(
  entries: E[], roles: { role: number; name: string }[],
): RoleGroup<E>[] {
  const out: RoleGroup<E>[] = roles.map((r) => ({
    role: r.role, name: r.name,
    entries: entries.filter((e) => e.role === r.role).sort((a, b) => a.name.localeCompare(b.name)),
  }))
  const known = new Set(roles.map((r) => r.role))
  const loose = entries.filter((e) => e.role == null || !known.has(e.role))
  if (loose.length) out.push({ role: null, name: 'Not assigned to a role', entries: loose })
  return out
}

/** How each known setting is labelled and edited. Unknown keys are shown read-only. */
export const SETTING_FIELDS: Record<string, { label: string; kind: 'bool' | 'int' | 'text' | 'long' | 'phones' | 'list' | 'map'; hint?: string }> = {
  test_only: { label: 'Test customers only', kind: 'bool', hint: 'Only the Zuper TEST customers and the numbers below.' },
  test_phones: { label: 'Extra test numbers', kind: 'phones', hint: 'Comma-separated. Treated as the Zuper TEST customer.' },
  text_delay_minutes: { label: 'Minutes before the text', kind: 'int' },
  wait_minutes: { label: 'Minutes after the text before Anisa calls', kind: 'int' },
  text_template: { label: 'The text', kind: 'long', hint: '{first_name} is replaced by the customer\'s first name. No prices, links or "approved".' },
  caller_id: { label: 'Anisa calls from', kind: 'text', hint: 'A number the phone system owns. The CRM line is +19547758492.' },
  call_agent: { label: 'Voice agent (phone system name)', kind: 'text' },
  test_jobs: { label: 'Test jobs (Zuper job numbers)', kind: 'list', hint: 'Comma-separated, e.g. 701, 734.' },
  boards: { label: 'Boards it checks', kind: 'list', hint: 'AHS - Inspection, Retail, AHS - Repair & Review' },
  grace_minutes: { label: 'Minutes after the visit before checking', kind: 'int' },
  recheck_minutes: { label: 'Minutes after the text before the call', kind: 'int' },
  technicians: { label: 'Technician phones', kind: 'map', hint: 'One per line: Zuper name = phone, e.g. Antonio Brown = 954-555-0100' },
}

/** A stored setting as the form shows it: a list comma-separated, a map one `name = value` per line. */
export function settingShown(v: unknown): string | boolean {
  if (Array.isArray(v)) return v.join(', ')
  if (typeof v === 'boolean') return v
  if (v && typeof v === 'object') return Object.entries(v as Record<string, unknown>).map(([k, x]) => `${k} = ${x}`).join('\n')
  return String(v ?? '')
}

/** A settings form value back to what the server takes. */
export function settingValue(kind: string, raw: string | boolean): unknown {
  if (kind === 'bool') return !!raw
  if (kind === 'int') return Number.parseInt(String(raw), 10)
  if (kind === 'phones' || kind === 'list') return String(raw).split(',').map((x) => x.trim()).filter(Boolean)
  if (kind === 'map') {
    const out: Record<string, string> = {}
    for (const line of String(raw).split('\n')) {
      const i = line.indexOf('=')
      if (i > 0 && line.slice(0, i).trim()) out[line.slice(0, i).trim()] = line.slice(i + 1).trim()
    }
    return out
  }
  return String(raw)
}

export type Show = 'all' | 'agents' | 'automations'

export function visible<E extends { kind: string; is_test: boolean; archived: boolean }>(
  entries: E[], show: Show, includeTests: boolean,
): E[] {
  return entries.filter((e) => !e.archived
    && (includeTests || !e.is_test)
    && (show === 'all' || (show === 'agents' ? e.kind === 'agent' : e.kind === 'automation')))
}

/** "$1.20 of $5.00 today", "$0.40 today", "Unknown — the connection has no price". */
export function spendLine(spend: string | null, cap: string | null, unknown: boolean): string {
  if (unknown) return 'Spend unknown: its AI connection has no price'
  const s = spend ?? '$0.00'
  return cap ? `${s} of ${cap} today` : `${s} today`
}
