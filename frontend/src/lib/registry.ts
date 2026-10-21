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
