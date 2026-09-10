/**
 * The Dashboard's date-range control.
 *
 * Its own module, importing nothing, so `backend/tests/test_dashboard_range.py`
 * can execute it under node — the same arrangement as `calendarGrid.ts`, and for
 * the same reason: this is arithmetic, and a regex asserting that the page
 * "mentions rangeWindow" would pass against a version that computes the wrong
 * window. See the docstring on that test file.
 *
 * ALL TIME IS THE DEFAULT, and that is a decision, not an oversight (owner,
 * 2026-09-10). The control offered "Last 30 days" while `/api/dashboard` took no
 * date parameter at all, so every card was really showing all time. Production's
 * opportunities were created between 8 and 16 August; making the control work
 * with its old default would have emptied the screen, and a fix that reads as a
 * regression is worse than the bug.
 *
 * The windows are rolling — "Last 7 days" is the 7×24 hours up to now, not the
 * last seven calendar days — and they are left open at the top end. An
 * opportunity cannot be created in the future, so an `end` would only add a
 * clock-skew edge for nothing.
 */

/** The order they appear in the control. All time first, because it is the default. */
export const DASHBOARD_RANGES = [
  'All time',
  'Last 7 days',
  'Last 30 days',
  'Last 90 days',
  'Last 12 months',
] as const

export type DashboardRange = (typeof DASHBOARD_RANGES)[number]

export const DEFAULT_RANGE: DashboardRange = 'All time'

/** Days back for every range that has a start. "All time" is absent on purpose. */
const DAYS: Record<string, number> = {
  'Last 7 days': 7,
  'Last 30 days': 30,
  'Last 90 days': 90,
  // 365 rather than a calendar year: a rolling window, like the others.
  'Last 12 months': 365,
}

const DAY_MS = 86_400_000

/** What the API is asked for. Empty means all time — no parameters at all. */
export type RangeWindow = { start?: string; end?: string }

/**
 * The window a range label selects, as UTC ISO timestamps.
 *
 * An unknown label returns the all-time window rather than throwing: a stale
 * value in localStorage should show every deal, never an empty dashboard.
 */
export function rangeWindow(label: string, now: Date = new Date()): RangeWindow {
  const days = DAYS[label]
  if (!days) return {}
  return { start: new Date(now.getTime() - days * DAY_MS).toISOString() }
}

/** `true` when this label asks for everything — used to label the cards. */
export function isAllTime(label: string): boolean {
  return !DAYS[label]
}

/**
 * The sentence under a card that says which window produced its figures.
 *
 * The cards are read as all-time by habit — they were all-time for as long as the
 * control did nothing — so a narrowed card has to say so on its face rather than
 * only in the header control.
 */
export function rangeCaption(label: string): string {
  return isAllTime(label) ? 'All time' : label
}
