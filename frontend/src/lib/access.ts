/**
 * "Only assigned data" and My Staff, as the browser sees them (2026-09-15).
 *
 * Import-free, so `test_only_assigned_data_ui.py` runs this very file under node. The
 * SERVER enforces every rule here (app/assigned_access.py, app/main.py); this module
 * only decides what to draw, so nothing is offered that the server would refuse.
 */

export type Role = 'ADMIN' | 'DISPATCHER' | 'TECH'

export type AccessUser = {
  role: Role | string
  only_assigned_data?: boolean | null
}

/** Limited to their own jobs? Never an ADMIN — the server ignores the switch there. */
export function isRestricted(user: AccessUser | null | undefined): boolean {
  return !!user && !!user.only_assigned_data && user.role !== 'ADMIN'
}

/** A restricted TECHNICIAN: on their own job they may answer its questions, move its
    stage, read and add its notes, add tasks and complete their own. Every job such a
    user can open IS their own — the server 404s the rest. */
export function techOnOwnJob(user: AccessUser | null | undefined): boolean {
  return !!user && user.role === 'TECH' && isRestricted(user)
}

/** Views a restricted user may not open: they show money and other people's work. */
export const REPORTING_VIEWS = ['dashboard', 'reporting'] as const

export const REPORTING_REFUSED =
  'Reporting, the Dashboard and the Forecast are not available with “Only assigned data” on.'

/** Which sidebar keys to draw. */
export function navKeys(user: AccessUser | null | undefined, keys: readonly string[]): string[] {
  if (!isRestricted(user)) return [...keys]
  return keys.filter((k) => !(REPORTING_VIEWS as readonly string[]).includes(k))
}

/** Where the app opens. Dashboard for everyone else; Opportunities — their jobs — for a
    restricted user, and the same for any view they may not open. */
export function viewFor(user: AccessUser | null | undefined, wanted: string): string {
  if (isRestricted(user) && (REPORTING_VIEWS as readonly string[]).includes(wanted)) {
    return 'opportunities'
  }
  return wanted
}

/** The Opportunities tab keys a user sees. Forecast is withheld from a restricted user. */
export function opportunityTabs<T extends string>(user: AccessUser | null | undefined,
  tabs: readonly T[]): T[] {
  return isRestricted(user) ? tabs.filter((t) => t.toLowerCase() !== 'forecast') : [...tabs]
}

// ---------------- My Staff ----------------

export const ROLE_LABELS: Record<Role, string> = {
  ADMIN: 'Admin',
  DISPATCHER: 'Dispatcher',
  TECH: 'Technician',
}

export function roleLabel(role: string): string {
  return ROLE_LABELS[role as Role] ?? role
}

/** The switch's default in Add User: ON for a Technician, OFF otherwise (still toggleable). */
export function defaultOnlyAssigned(role: string): boolean {
  return role === 'TECH'
}

/** "Antonio Brown" -> "AB"; one word -> its first two letters. */
export function initials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean)
  if (words.length === 0) return '?'
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase()
  return (words[0][0] + words[words.length - 1][0]).toUpperCase()
}

/** A user has one `name`. The modal edits it as First / Last: split at the first space. */
export function splitName(name: string): { first: string; last: string } {
  const clean = name.trim().replace(/\s+/g, ' ')
  const at = clean.indexOf(' ')
  return at < 0 ? { first: clean, last: '' } : { first: clean.slice(0, at), last: clean.slice(at + 1) }
}

export function joinName(first: string, last: string): string {
  return [first.trim(), last.trim()].filter(Boolean).join(' ')
}

export type StaffRow = {
  id: number
  name: string
  email: string | null
  phone?: string | null
  role: string
  is_active: boolean
  machine?: boolean
}

/** What the trash icon may do to a row, and why not. The server enforces the same. */
export function deactivateBlock(row: StaffRow, meId: number, rows: StaffRow[]): string | null {
  if (row.id === meId) return 'You cannot deactivate your own account'
  const canSignIn = (r: StaffRow) => r.role === 'ADMIN' && r.is_active && !r.machine
  if (row.is_active && canSignIn(row) && rows.filter(canSignIn).length <= 1) {
    return 'The last active admin cannot be deactivated — make someone else an admin first'
  }
  return null
}

/** Why the Role control is locked in Edit User, or null. */
export function roleBlock(row: StaffRow, meId: number, rows: StaffRow[]): string | null {
  if (row.role !== 'ADMIN') return null
  if (row.id === meId) return 'You cannot change your own role away from admin'
  const canSignIn = (r: StaffRow) => r.role === 'ADMIN' && r.is_active && !r.machine
  if (row.is_active && !row.machine && rows.filter(canSignIn).length <= 1) {
    return 'The last active admin must stay an admin'
  }
  return null
}

/** Client-side paging of the list the server already filtered. */
export function pageOf<T>(rows: T[], page: number, perPage: number): { rows: T[]; pages: number } {
  const pages = Math.max(1, Math.ceil(rows.length / perPage))
  const p = Math.min(Math.max(1, page), pages)
  return { rows: rows.slice((p - 1) * perPage, p * perPage), pages }
}
