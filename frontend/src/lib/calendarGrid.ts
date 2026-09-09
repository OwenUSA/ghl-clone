/**
 * Calendar range arithmetic, kept free of React and of any import so it can be
 * executed directly by the backend test suite (`node` imports this .ts file —
 * see backend/tests/test_calendar_grid.py). The page must not re-derive any of
 * it: the range label, the query window and the drawn cells all have to come
 * from the same three functions or they drift apart, which is exactly the bug
 * this module was written to fix.
 *
 * The bug: Month view set `days = 35`, which fed the label, the query window and
 * the prev/next arrows, while both grid renders capped at `slice(0, 7)`. So
 * Month view drew a week, fetched five weeks, and paged past 28 days nobody ever
 * saw. Day view and Week view are measured against GHL and are unchanged.
 */
export type CalendarView = 'Day view' | 'Week view' | 'Month view'

/** Appointment chips drawn in a month cell before the rest collapse to "+N more". */
export const MONTH_CELL_CHIPS = 3

/** Default start hour for a booking created from a month cell or the New button. */
export const DEFAULT_SLOT_HOUR = 9

/**
 * Day arithmetic goes through the `new Date(y, m, d)` constructor, never through
 * adding 86_400_000ms. The constructor normalises overflow (`d = 0` is the last
 * day of the previous month, `m = 12` is next January) and, unlike millisecond
 * addition, it lands on the same wall-clock time across a DST boundary — where
 * "+24h" would land at 23:00 or 01:00 and silently duplicate or skip a day.
 */
const day = (d: Date, offset = 0) =>
  new Date(d.getFullYear(), d.getMonth(), d.getDate() + offset)

export const startOfDay = (d: Date) => day(d)

/** Weeks start SUNDAY, as measured on GHL (the header reads "26 Sun".."01 Sat"). */
export const startOfWeek = (d: Date) => day(d, -d.getDay())

export const startOfMonth = (d: Date) => new Date(d.getFullYear(), d.getMonth(), 1)

export const daysInMonth = (d: Date) =>
  new Date(d.getFullYear(), d.getMonth() + 1, 0).getDate()

export const isSameDay = (a: Date, b: Date) => a.toDateString() === b.toDateString()

/**
 * Every cell of a conventional month grid: whole Sunday-to-Saturday weeks that
 * cover every day of the anchor's month, so the length is always a multiple of 7
 * (28, 35 or 42). The leading and trailing cells belong to the neighbouring
 * months and are drawn dimmed rather than left blank — a blank cell reads as
 * "no appointments" and one with an appointment in it would be a lie.
 */
export function monthCells(anchor: Date): Date[] {
  const first = startOfMonth(anchor)
  const lead = first.getDay()
  const weeks = Math.ceil((lead + daysInMonth(anchor)) / 7)
  return Array.from({ length: weeks * 7 }, (_, i) => day(first, i - lead))
}

/** The days the grid actually draws. Nothing else may decide this. */
export function visibleDays(view: CalendarView, anchor: Date): Date[] {
  if (view === 'Day view') return [startOfDay(anchor)]
  if (view === 'Week view') {
    const s = startOfWeek(anchor)
    return Array.from({ length: 7 }, (_, i) => day(s, i))
  }
  return monthCells(anchor)
}

/**
 * The window to ask the API for: exactly the days that are drawn, no more.
 * `end` is midnight after the last visible day, so an appointment starting at
 * 23:30 on the final cell is still inside the range.
 */
export function queryWindow(view: CalendarView, anchor: Date) {
  const days = visibleDays(view, anchor)
  return { start: days[0], end: day(days[days.length - 1], 1) }
}

/** Prev/next moves by one of whatever is on screen — a day, a week, a month. */
export function shiftAnchor(view: CalendarView, anchor: Date, dir: number): Date {
  if (view === 'Day view') return day(anchor, dir)
  if (view === 'Week view') return day(anchor, 7 * dir)
  // The 1st, not the same day-of-month: stepping from 31 Jan by one month would
  // otherwise normalise to 3 March and skip February entirely.
  return new Date(anchor.getFullYear(), anchor.getMonth() + dir, 1)
}

const shortMonth = (d: Date) => d.toLocaleDateString('en-US', { month: 'short' })

/**
 * The toolbar label. Day and Week keep their measured formats verbatim; Month
 * names the month the grid is a grid *of*, which is the only honest label for a
 * range whose first and last cells belong to other months.
 */
export function rangeLabel(view: CalendarView, anchor: Date): string {
  if (view === 'Month view') {
    return anchor.toLocaleDateString('en-US', { month: 'long', year: 'numeric' })
  }
  const days = visibleDays(view, anchor)
  const start = days[0]
  if (days.length === 1) {
    return start.toLocaleDateString('en-US', {
      month: 'short', day: 'numeric', year: 'numeric',
    })
  }
  const end = days[days.length - 1]
  return `${shortMonth(start)} ${start.getDate()} – ${shortMonth(end)} ${end.getDate()}, ${end.getFullYear()}`
}

/**
 * Group appointments into the cells that draw them, one bucket per visible day
 * and in the order the API returned them (which is by `starts_at`).
 *
 * Returned as a parallel array rather than a lookup so a caller cannot render a
 * day the grouping never considered.
 */
export function bucketByDay<T extends { starts_at: string }>(
  days: Date[], appointments: T[],
): T[][] {
  const slot = new Map<string, number>()
  days.forEach((d, i) => slot.set(d.toDateString(), i))
  const out: T[][] = days.map(() => [])
  for (const a of appointments) {
    const i = slot.get(new Date(a.starts_at).toDateString())
    if (i !== undefined) out[i].push(a)
  }
  return out
}

/**
 * What the New button and a month-cell double-click prefill.
 *
 * Today when today is on screen, otherwise the first day that is — booking onto
 * a date the user cannot see is how an appointment lands somewhere nobody looks.
 */
export function defaultSlot(days: Date[], now: Date): Date {
  const target = days.find((d) => isSameDay(d, now)) ?? days[0]
  return new Date(target.getFullYear(), target.getMonth(), target.getDate(),
                  DEFAULT_SLOT_HOUR, 0, 0, 0)
}

/** Round a fractional hour to the nearest half hour — a slot, not a timestamp. */
export function slotAt(d: Date, hours: number): Date {
  const minutes = Math.max(0, Math.min(24 * 60 - 30, Math.round(hours * 2) * 30))
  return new Date(d.getFullYear(), d.getMonth(), d.getDate(),
                  Math.floor(minutes / 60), minutes % 60, 0, 0)
}

/** `<input type="date">` and `<input type="time">` want local parts, not an ISO UTC string. */
export const dateInputValue = (d: Date) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`

export const timeInputValue = (d: Date) =>
  `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`

/** Parse those two inputs back into a local Date; null if either is blank or unparseable. */
export function fromInputs(dateValue: string, timeValue: string): Date | null {
  const dm = /^(\d{4})-(\d{2})-(\d{2})$/.exec(dateValue)
  const tm = /^(\d{2}):(\d{2})/.exec(timeValue)
  if (!dm || !tm) return null
  const d = new Date(+dm[1], +dm[2] - 1, +dm[3], +tm[1], +tm[2], 0, 0)
  return Number.isNaN(d.getTime()) ? null : d
}
