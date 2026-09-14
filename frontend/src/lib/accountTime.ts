/**
 * Wall-clock time in the ACCOUNT's timezone, whatever timezone the browser is in.
 *
 * GoHighLevel's Book appointment modal reads "Showing slots in this timezone:
 * (Account timezone)" and picks times in that zone. The account is Dream Team
 * Roofing, Bradenton FL — America/New_York (the owner is in Miami, the same zone).
 * A dispatcher whose laptop is set to another zone must still book 1:30 PM Eastern
 * when they pick 1:30 PM, and a booking must read back as 1:30 PM Eastern on both
 * sides of a daylight-saving change.
 *
 * Like calendarGrid.ts this module imports nothing, so the backend test suite
 * runs the real file under node with the process timezone deliberately set to
 * somewhere else (backend/tests/test_account_time.py).
 *
 * Nothing here hardcodes an offset. Every offset is read from the platform's own
 * zone database through Intl, so EDT (GMT-04:00) and EST (GMT-05:00) come out of
 * the date, not out of this file.
 */
export const ACCOUNT_TIME_ZONE = 'America/New_York'

/** Month is 1-12, hour 0-23. */
export type Wall = { year: number; month: number; day: number; hour: number; minute: number }

const partsCache = new Map<string, Intl.DateTimeFormat>()

function numericFormat(timeZone: string): Intl.DateTimeFormat {
  let f = partsCache.get(timeZone)
  if (!f) {
    f = new Intl.DateTimeFormat('en-US', {
      timeZone, hourCycle: 'h23', year: 'numeric', month: 'numeric', day: 'numeric',
      hour: 'numeric', minute: 'numeric', second: 'numeric',
    })
    partsCache.set(timeZone, f)
  }
  return f
}

/** The wall-clock reading of an instant in `timeZone`. */
export function wallOf(instant: Date, timeZone: string = ACCOUNT_TIME_ZONE): Wall {
  const parts = numericFormat(timeZone).formatToParts(instant)
  const get = (t: string) => Number(parts.find((p) => p.type === t)?.value ?? 0)
  return {
    year: get('year'), month: get('month'), day: get('day'),
    // Some engines print midnight as hour 24 even with h23.
    hour: get('hour') % 24, minute: get('minute'),
  }
}

/** Minutes the zone is ahead of UTC at that instant: -240 for EDT, -300 for EST. */
export function offsetMinutes(instant: Date, timeZone: string = ACCOUNT_TIME_ZONE): number {
  const w = wallOf(instant, timeZone)
  const asUtc = Date.UTC(w.year, w.month - 1, w.day, w.hour, w.minute)
  const whole = Math.floor(instant.getTime() / 60_000) * 60_000
  return Math.round((asUtc - whole) / 60_000)
}

/**
 * The instant at which `timeZone`'s clock reads `w`.
 *
 * Two DST edge cases, both decided rather than left to chance:
 *   * a time that does not exist (2:30 AM on the spring-forward day) lands on the
 *     instant an hour later on the wall — 3:30 AM — never on a different day;
 *   * a time that happens twice (1:30 AM on the fall-back day) is the FIRST one,
 *     the daylight-time reading.
 */
export function instantOf(w: Wall, timeZone: string = ACCOUNT_TIME_ZONE): Date {
  const guess = Date.UTC(w.year, w.month - 1, w.day, w.hour, w.minute)
  const before = offsetMinutes(new Date(guess - 86_400_000), timeZone)
  const after = offsetMinutes(new Date(guess + 86_400_000), timeZone)
  // Try the earlier offset first, so an ambiguous time resolves to its first reading.
  for (const off of before >= after ? [before, after] : [after, before]) {
    const t = new Date(guess - off * 60_000)
    const back = wallOf(t, timeZone)
    if (back.hour === w.hour && back.minute === w.minute && back.day === w.day) return t
  }
  // The wall time does not exist: skip forward by the size of the gap.
  return new Date(guess - Math.min(before, after) * 60_000)
}

const pad = (n: number) => String(n).padStart(2, '0')

/**
 * "GMT-04:00 America/New_York (EDT)" in summer, "GMT-05:00 America/New_York (EST)"
 * in winter — the label in GoHighLevel's timezone control, for the given instant.
 */
export function zoneLabel(instant: Date, timeZone: string = ACCOUNT_TIME_ZONE): string {
  const off = offsetMinutes(instant, timeZone)
  const sign = off < 0 ? '-' : '+'
  const abs = Math.abs(off)
  const gmt = `GMT${sign}${pad(Math.floor(abs / 60))}:${pad(abs % 60)}`
  const name = new Intl.DateTimeFormat('en-US', { timeZone, timeZoneName: 'short' })
    .formatToParts(instant).find((p) => p.type === 'timeZoneName')?.value ?? ''
  // Zones without a well-known abbreviation print "GMT-3"; that says nothing new.
  return name && !name.startsWith('GMT') && !name.startsWith('UTC')
    ? `${gmt} ${timeZone} (${name})` : `${gmt} ${timeZone}`
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct',
                'Nov', 'Dec']

/** "1:30 PM" for minutes-past-midnight. */
export function clockLabel(minutes: number): string {
  const h = Math.floor(minutes / 60) % 24
  const m = minutes % 60
  return `${h % 12 === 0 ? 12 : h % 12}:${pad(m)} ${h < 12 ? 'AM' : 'PM'}`
}

/** "Sep 15, 2026, 1:30 PM" — the Start time / End time box in screenshot 29. */
export function pickerLabel(instant: Date, timeZone: string = ACCOUNT_TIME_ZONE): string {
  const w = wallOf(instant, timeZone)
  return `${MONTHS[w.month - 1]} ${w.day}, ${w.year}, ${clockLabel(w.hour * 60 + w.minute)}`
}

/** "Tue, Sep 15, 1:30 PM" — a booking's summary line, in the account's zone. */
export function whenLabel(instant: Date, timeZone: string = ACCOUNT_TIME_ZONE): string {
  const w = wallOf(instant, timeZone)
  const weekday = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][
    new Date(Date.UTC(w.year, w.month - 1, w.day)).getUTCDay()]
  return `${weekday}, ${MONTHS[w.month - 1]} ${w.day}, ${clockLabel(w.hour * 60 + w.minute)}`
}

/** Minutes between the time-list rows. */
export const SLOT_STEP_MINUTES = 15

/**
 * Every row of the time list: the whole day, 12:00 AM to 11:45 PM, every 15
 * minutes. The owner asked for at least 5:00 AM to 11:00 PM; a roofer's
 * emergency tarp at 2 AM is a real booking too, so nothing is cut off.
 */
export function timeSlots(step: number = SLOT_STEP_MINUTES): { minutes: number; label: string }[] {
  const out = []
  for (let m = 0; m < 24 * 60; m += step) out.push({ minutes: m, label: clockLabel(m) })
  return out
}

/** Month `month` (1-12) as whole Sunday-first weeks of {year, month, day}. */
export function monthDays(year: number, month: number): { year: number; month: number; day: number }[] {
  const first = new Date(Date.UTC(year, month - 1, 1))
  const lead = first.getUTCDay()
  const count = new Date(Date.UTC(year, month, 0)).getUTCDate()
  const weeks = Math.ceil((lead + count) / 7)
  return Array.from({ length: weeks * 7 }, (_, i) => {
    const d = new Date(Date.UTC(year, month - 1, 1 + i - lead))
    return { year: d.getUTCFullYear(), month: d.getUTCMonth() + 1, day: d.getUTCDate() }
  })
}

/** The same wall-clock minute on another calendar day. */
export function withDay(w: Wall, d: { year: number; month: number; day: number }): Wall {
  return { ...w, year: d.year, month: d.month, day: d.day }
}

/** The same calendar day at another minute-of-day. */
export function withMinutes(w: Wall, minutes: number): Wall {
  return { ...w, hour: Math.floor(minutes / 60), minute: minutes % 60 }
}

/** Some common zones for the "Showing slots in this timezone" control. */
export const US_TIME_ZONES = [
  'America/New_York', 'America/Chicago', 'America/Denver', 'America/Phoenix',
  'America/Los_Angeles', 'America/Anchorage', 'Pacific/Honolulu', 'America/Puerto_Rico',
]

/**
 * The contact's property address on one line, exactly as the server stores it for
 * "Calendar default" (`contact_address` in backend/app/main.py). Only ever used to
 * SHOW what will be stored — the server resolves the location itself.
 */
export function addressLine(c: {
  address_street?: string | null; address_city?: string | null
  address_state?: string | null; address_postal_code?: string | null
} | null | undefined): string | null {
  if (!c) return null
  const t = (v?: string | null) => (v ?? '').trim()
  const region = [t(c.address_state), t(c.address_postal_code)].filter(Boolean).join(' ')
  return [t(c.address_street), t(c.address_city), region].filter(Boolean).join(', ') || null
}

/**
 * What "Calendar default" will store (2026-09-14): the OPPORTUNITY's address when the
 * booking is for a card that has one, else the contact's — `_resolve_location` in
 * backend/app/main.py, the same preference in the same order.
 */
export function meetingDefaultAddress(
  opportunity: Parameters<typeof addressLine>[0],
  contact: Parameters<typeof addressLine>[0],
): string | null {
  return addressLine(opportunity) ?? addressLine(contact)
}

/** "2026-09-15" / "13:30" for native inputs, read in the account's zone. */
export function dateInput(instant: Date, timeZone: string = ACCOUNT_TIME_ZONE): string {
  const w = wallOf(instant, timeZone)
  return `${w.year}-${pad(w.month)}-${pad(w.day)}`
}

export function timeInput(instant: Date, timeZone: string = ACCOUNT_TIME_ZONE): string {
  const w = wallOf(instant, timeZone)
  return `${pad(w.hour)}:${pad(w.minute)}`
}

/** The inverse of the two above; null when either input is incomplete. */
export function fromDateTimeInputs(date: string, time: string,
                                   timeZone: string = ACCOUNT_TIME_ZONE): Date | null {
  const d = /^(\d{4})-(\d{2})-(\d{2})$/.exec(date)
  const t = /^(\d{2}):(\d{2})/.exec(time)
  if (!d || !t) return null
  return instantOf({ year: +d[1], month: +d[2], day: +d[3], hour: +t[1], minute: +t[2] },
    timeZone)
}
