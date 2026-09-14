/**
 * CompanyCam photos — the logic the Photos tab, the viewer and the contact panel share.
 *
 * Import-free, so backend/tests/test_companycam_ui.py executes it under node: the
 * order photos are drawn in, where next/previous land, and the date written under a
 * photo are behaviour, not markup.
 *
 * The browser never holds a CompanyCam URL for an image or the API token: every
 * `thumbnail_url` / `image_url` is a path on this CRM, which relays the bytes
 * (backend/app/companycam_api.py says why).
 */

export type CompanyCamState = 'ok' | 'off' | 'unavailable'

export type CompanyCamProject = {
  id: string
  name: string | null
  address: string
  photo_count: number | null
  project_url: string | null
  link_method: string | null
}

export type CompanyCamPhoto = {
  id: string
  project_id: string | null
  captured_at: string | null
  creator_name: string | null
  description: string | null
  annotated: boolean
  thumbnail_url: string
  image_url: string
}

export type CompanyCamProjects = {
  state: CompanyCamState
  message: string | null
  projects: CompanyCamProject[]
}

export type CompanyCamPhotoPage = {
  state: CompanyCamState
  message: string | null
  photos: CompanyCamPhoto[]
  page: number
  has_more: boolean
}

export type ContactCompanyCamProjects = {
  state: 'ok' | 'off'
  projects: { id: string; name: string | null; opportunities: { id: number; title: string }[] }[]
}

/** The account's zone: a photo taken at 9am in Bradenton reads 9am on any laptop. */
export const ACCOUNT_TIME_ZONE = 'America/New_York'

export const UNAVAILABLE = 'Photos are unavailable right now'
export const OFF = 'CompanyCam is not connected on this deployment'

/**
 * Every loaded page of one project, as one list: newest first, each photo once.
 * CompanyCam's own page order is not measured, so the order is decided here.
 */
export function mergePages(pages: { photos: CompanyCamPhoto[] }[]): CompanyCamPhoto[] {
  const seen = new Set<string>()
  const out: CompanyCamPhoto[] = []
  for (const page of pages) {
    for (const p of page.photos) {
      if (seen.has(p.id)) continue
      seen.add(p.id)
      out.push(p)
    }
  }
  const at = (p: CompanyCamPhoto) => (p.captured_at ? Date.parse(p.captured_at) : 0)
  return out.sort((a, b) => at(b) - at(a))
}

/**
 * Where next / previous land in the viewer. Stops at both ends rather than wrapping:
 * wrapping from the newest photo to the oldest one reads as a jump in time.
 * `null` means the button is not drawn.
 */
export function neighbours(index: number, count: number, hasMore: boolean): {
  prev: number | null
  next: number | null
  /** Next is past the last loaded photo: load the next page, then move. */
  needsMore: boolean
} {
  const prev = index > 0 ? index - 1 : null
  if (index < count - 1) return { prev, next: index + 1, needsMore: false }
  return { prev, next: hasMore ? index + 1 : null, needsMore: hasMore }
}

/** "Sep 13 2026, 9:17 AM (EDT)" — the note card's stamp, in the account's zone. */
export function photoStamp(iso: string | null, timeZone = ACCOUNT_TIME_ZONE): string {
  if (!iso) return 'Date unknown'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return 'Date unknown'
  const parts = new Intl.DateTimeFormat('en-US', {
    month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric',
    minute: '2-digit', timeZoneName: 'short', timeZone,
  }).formatToParts(d)
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? ''
  return `${get('month')} ${get('day')} ${get('year')}, ${get('hour')}:${get('minute')} `
    + `${get('dayPeriod').toUpperCase()} (${get('timeZoneName')})`
}

/** The sentence a tab shows instead of photos, or null when there are photos to show. */
export function tabMessage(state: CompanyCamState | undefined, projects: number,
                           failed = false): string | null {
  if (failed || state === 'unavailable') return UNAVAILABLE
  if (state === 'off') return OFF
  if (state === 'ok' && projects === 0) return 'No CompanyCam project is linked to this opportunity yet.'
  return null
}

/** "12 photos" / "1 photo" / "" when CompanyCam did not say. */
export function photoCount(n: number | null | undefined): string {
  if (n == null) return ''
  return `${n} photo${n === 1 ? '' : 's'}`
}
