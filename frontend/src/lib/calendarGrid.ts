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

// ---------------------------------------------------------------------------
// Laying out the Day and Week grid (2026-09-15)
// ---------------------------------------------------------------------------

/** Anything with a start and an end: an appointment, or blocked off time. */
export type Timed = { starts_at: string; ends_at: string }

/** The height a zero-length item is drawn with, in minutes — so it can be seen. */
export const MIN_BLOCK_MINUTES = 15

/** Minutes past local midnight, by the WALL CLOCK, so a DST day still lines up
 *  with the hour labels drawn beside it. */
const wallMinutes = (d: Date) => d.getHours() * 60 + d.getMinutes() + d.getSeconds() / 60

/**
 * Every visible day an item touches, not only the day it starts on: a job from
 * Monday 7:30 AM to Tuesday 5 PM is drawn on both days. `[start, end)` — ending
 * exactly at midnight does not put it on the next day. A zero-length item belongs
 * to the day it starts on.
 */
export function bucketByOverlap<T extends Timed>(days: Date[], items: T[]): T[][] {
  return days.map((d) => {
    const from = day(d).getTime()
    const to = day(d, 1).getTime()
    return items.filter((a) => {
      const s = new Date(a.starts_at).getTime()
      const e = new Date(a.ends_at).getTime()
      return e > s ? s < to && e > from : s >= from && s < to
    })
  })
}

export type Placed<T> = {
  item: T
  /** Minutes past midnight of THIS day where the drawn segment starts / ends. */
  startMin: number
  endMin: number
  /** Lane, how many lanes it spans, and how many lanes its overlap group has. */
  col: number
  span: number
  cols: number
  /** The item began on an earlier day / carries on into a later one. */
  fromBefore: boolean
  untilAfter: boolean
}

/**
 * Side-by-side layout for one day column, the way GoHighLevel and Workiz draw it.
 *
 * 1. Each item is clipped to this day: a multi-day item draws from its start to
 *    midnight on its first day, the whole day in between, and from midnight to its
 *    end on its last day.
 * 2. Items that overlap — directly or through a chain — form one group. Touching is
 *    not overlapping: 12–1 and 1–3 sit in the same lane.
 * 3. Within a group each item takes the first lane that is free when it starts
 *    (earliest first, longest first on a tie), and the group's width is split into
 *    as many lanes as it needed.
 * 4. An item then widens into the lanes to its right that nothing overlapping it
 *    uses, so a short visit beside a long one does not leave dead space.
 *
 * No two placed items that overlap in time share any horizontal space, so nothing
 * is ever drawn behind another block. The height is the real duration; only a
 * zero-length item is given MIN_BLOCK_MINUTES.
 */
export function layoutDay<T extends Timed>(d: Date, items: T[]): Placed<T>[] {
  const from = day(d).getTime()
  const to = day(d, 1).getTime()
  const segs: Placed<T>[] = []
  for (const item of items) {
    const s = new Date(item.starts_at)
    const e = new Date(item.ends_at)
    const zero = e.getTime() <= s.getTime()
    if (zero ? !(s.getTime() >= from && s.getTime() < to)
             : !(s.getTime() < to && e.getTime() > from)) continue
    const fromBefore = s.getTime() < from
    const untilAfter = e.getTime() > to
    const startMin = fromBefore ? 0 : wallMinutes(s)
    let endMin = untilAfter || e.getTime() === to ? 24 * 60 : wallMinutes(e)
    if (zero || endMin <= startMin) endMin = Math.min(24 * 60, startMin + MIN_BLOCK_MINUTES)
    segs.push({ item, startMin, endMin, col: 0, span: 1, cols: 1, fromBefore, untilAfter })
  }
  segs.sort((a, b) => a.startMin - b.startMin || (b.endMin - b.startMin) - (a.endMin - a.startMin))

  const overlaps = (a: Placed<T>, b: Placed<T>) => a.startMin < b.endMin && b.startMin < a.endMin
  let group: Placed<T>[] = []
  let groupEnd = -1
  let laneEnds: number[] = []
  const close = () => {
    for (const g of group) {
      g.cols = laneEnds.length
      let span = 1
      while (g.col + span < g.cols &&
             !group.some((o) => o !== g && o.col === g.col + span && overlaps(o, g))) span++
      g.span = span
    }
    group = []
    laneEnds = []
  }
  for (const seg of segs) {
    if (group.length && seg.startMin >= groupEnd) close()
    let lane = laneEnds.findIndex((end) => end <= seg.startMin)
    if (lane === -1) { lane = laneEnds.length; laneEnds.push(seg.endMin) } else laneEnds[lane] = seg.endMin
    seg.col = lane
    group.push(seg)
    groupEnd = group.length === 1 ? seg.endMin : Math.max(groupEnd, seg.endMin)
  }
  if (group.length) close()
  return segs
}

const clock = (d: Date) => {
  const h = d.getHours() % 12 || 12
  return `${h}:${String(d.getMinutes()).padStart(2, '0')}`
}
const meridiem = (d: Date) => (d.getHours() < 12 ? 'AM' : 'PM')
const weekday = (d: Date) => d.toLocaleDateString('en-US', { weekday: 'short' })

/**
 * A block's time range, as GoHighLevel writes it: "7:30 – 10:00 AM" when both ends
 * share AM/PM, "11:30 AM – 1:00 PM" when they do not, and the weekday on both ends
 * when the item runs past midnight: "Mon 7:30 AM – Tue 5:00 PM".
 */
export function timeRange(startIso: string, endIso: string): string {
  const s = new Date(startIso)
  const e = new Date(endIso)
  if (e.getTime() <= s.getTime()) return `${clock(s)} ${meridiem(s)}`
  // Ending exactly at the next midnight is still a same-day block ("– 12:00 AM").
  const toMidnight = e.getTime() === day(s, 1).getTime()
  const endLabel = `${clock(e)} ${meridiem(e)}`
  if (!isSameDay(s, e) && !toMidnight) {
    return `${weekday(s)} ${clock(s)} ${meridiem(s)} – ${weekday(e)} ${endLabel}`
  }
  return meridiem(s) === meridiem(e) && !toMidnight
    ? `${clock(s)} – ${endLabel}`
    : `${clock(s)} ${meridiem(s)} – ${endLabel}`
}

/** How many hours the Day and Week grid opens on without scrolling: 7 AM to 7 PM. */
export const FIRST_VISIBLE_HOUR = 7
export const VISIBLE_HOURS = 12
/** Never draw an hour smaller than this, whatever the screen: below it the blocks
 *  stop being readable, and scrolling is the better trade. */
export const MIN_HOUR_PX = 48

/** The hour height that fits 7 AM–7 PM into a pane this tall, where the screen allows. */
export const hourPxFor = (paneHeight: number) =>
  Math.max(MIN_HOUR_PX, Math.floor(paneHeight / VISIBLE_HOURS))
