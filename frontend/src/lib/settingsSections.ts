/**
 * Settings is split into sections. Import-free, so `test_pipelines_ui.py` can run
 * it under node and prove Custom Fields is reachable by URL as well as by click.
 *
 * Custom Fields moved here from the Opportunities tab row on 2026-09-13 — the
 * same `CustomFieldsPanel`, relocated, so the Opportunities tabs match
 * GoHighLevel's four again.
 */
export const SETTINGS_SECTIONS = [
  { key: 'account', label: 'My account', path: '/settings' },
  { key: 'custom-fields', label: 'Custom Fields', path: '/settings/custom-fields' },
  // ADMIN only: SettingsPage does not draw it for anyone else (2026-09-14).
  { key: 'companycam', label: 'CompanyCam', path: '/settings/companycam' },
  // ADMIN only, like CompanyCam (2026-09-15): GoHighLevel's staff list, and the home of
  // each user's "Only assigned data" switch.
  { key: 'my-staff', label: 'My Staff', path: '/settings/my-staff' },
  // ADMIN only (2026-09-15): AI provider connections, "Pause all AI agents", the on-call phone.
  { key: 'ai-connections', label: 'AI Connections', path: '/settings/ai-connections' },
] as const

/** Sections only an ADMIN is shown. SettingsPage does not draw their tabs for anyone else. */
export const ADMIN_SECTIONS: readonly string[] = ['companycam', 'my-staff', 'ai-connections']

export type SettingsSection = (typeof SETTINGS_SECTIONS)[number]['key']

/** The section a path names; anything else under /settings is the account section. */
export function sectionFromPath(pathname: string): SettingsSection {
  const path = pathname.replace(/\/+$/, '').toLowerCase()
  const hit = SETTINGS_SECTIONS.find((s) => s.path === path)
  return hit ? hit.key : 'account'
}

/** Which top-level view a fresh page load of `pathname` opens, or null for the default. */
export function viewFromPath(pathname: string): string | null {
  const path = pathname.replace(/\/+$/, '').toLowerCase()
  if (path === '/opportunities') return 'opportunities'
  if (path === '/ai-agents') return 'ai-agents'
  if (path === '/settings' || path.startsWith('/settings/')) return 'settings'
  return null
}
