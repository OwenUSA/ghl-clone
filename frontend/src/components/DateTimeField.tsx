import { useCallback, useEffect, useRef, useState } from 'react'
import {
  ACCOUNT_TIME_ZONE, instantOf, monthDays, pickerLabel, timeSlots, wallOf, withDay,
  withMinutes,
} from '../lib/accountTime'
import {
  BODY, BORDER, DIVIDER, FAINT, INPUT, PLACEHOLDER, PRIMARY, PRIMARY_TINT, TEXT, useDismiss,
} from './opportunity/ui'

/**
 * GoHighLevel's Start time / End time box (screenshot 29): "Sep 15, 2026, 1:30 PM"
 * with a calendar glyph at the right. It opens a month on the left and the time
 * list on the right — the popover itself is not in the screenshot, so its shape is
 * the conventional one and is listed as an assumption in .qa/state/appt-done.
 *
 * The value is an INSTANT; what is shown and picked is the wall clock in
 * `timeZone` (the account's, America/New_York, unless the modal's timezone control
 * says otherwise). The browser's own zone plays no part — see lib/accountTime.ts.
 *
 * The time list is the whole day in 15-minute rows, so it covers the owner's
 * "at least 5 AM to 11 PM" and the 2 AM emergency tarp too.
 */
const SLOTS = timeSlots()
const WEEKDAYS = ['Su', 'Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa']
const MONTH_NAMES = ['January', 'February', 'March', 'April', 'May', 'June', 'July',
  'August', 'September', 'October', 'November', 'December']

export function DateTimeField({ label, value, onChange, timeZone = ACCOUNT_TIME_ZONE,
  disabled = false, title }: {
  label: string
  value: Date
  onChange: (next: Date) => void
  timeZone?: string
  disabled?: boolean
  title?: string
}) {
  const [open, setOpen] = useState(false)
  const close = useCallback(() => setOpen(false), [])
  const ref = useDismiss(open, close)
  const wall = wallOf(value, timeZone)
  const [shown, setShown] = useState({ year: wall.year, month: wall.month })
  const list = useRef<HTMLDivElement>(null)
  const box = useRef<HTMLButtonElement>(null)
  // The popover is FIXED to the viewport, placed from the box's own rect: the
  // modal's columns scroll, and an absolutely placed popover inside one would be
  // clipped by it. It opens upward when there is no room below.
  const [at, setAt] = useState<{ left: number; top: number }>({ left: 0, top: 0 })
  const minutes = wall.hour * 60 + wall.minute

  // Opening scrolls the time list to the chosen time, not to midnight.
  useEffect(() => {
    if (!open || !list.current) return
    const row = list.current.querySelector<HTMLElement>('[aria-selected="true"]')
    if (row) list.current.scrollTop = row.offsetTop - 80
  }, [open])

  const step = (dir: number) => setShown((s) => {
    const m = s.month + dir
    return m < 1 ? { year: s.year - 1, month: 12 }
      : m > 12 ? { year: s.year + 1, month: 1 } : { year: s.year, month: m }
  })

  return (
    <div ref={ref} className="relative min-w-0 flex-1">
      <div style={{ fontSize: 14, color: BODY }}>{label}</div>
      <button
        type="button"
        aria-label={label}
        aria-haspopup="dialog"
        disabled={disabled}
        title={title}
        ref={box}
        onClick={() => {
          setShown({ year: wall.year, month: wall.month })
          const r = box.current?.getBoundingClientRect()
          if (r) {
            const height = 330
            const below = r.bottom + 4 + height <= window.innerHeight
            setAt({ left: Math.max(8, Math.min(r.left, window.innerWidth - 380)),
                    top: below ? r.bottom + 4 : Math.max(8, r.top - 4 - height) })
          }
          setOpen((v) => !v)
        }}
        className="flex items-center justify-between gap-4 text-left"
        style={{ ...INPUT, height: 36, padding: '0 8px 0 10px', ...(disabled
          ? { backgroundColor: 'rgb(249,250,251)', cursor: 'not-allowed' } : {}) }}
      >
        <span className="truncate" style={{ color: TEXT }}>{pickerLabel(value, timeZone)}</span>
        <svg width={14} height={14} viewBox="0 0 24 24" fill="none" stroke={PLACEHOLDER} className="shrink-0"
          strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <rect x="3" y="4" width="18" height="17" rx="2" /><path d="M3 9h18M8 2v4M16 2v4" />
        </svg>
      </button>

      {open && (
        <div role="dialog" aria-label={label + ' picker'} className="flex bg-white"
          style={{ position: 'fixed', zIndex: 60, top: at.top, left: at.left, borderRadius: 8,
            border: '1px solid ' + DIVIDER,
            boxShadow: '0 12px 16px -4px rgba(16,24,40,0.08), 0 4px 6px -2px rgba(16,24,40,0.03)' }}>
          <div style={{ padding: 12, width: 252 }}>
            <div className="flex items-center justify-between" style={{ marginBottom: 8 }}>
              <button type="button" aria-label="Previous month" onClick={() => step(-1)}
                style={{ width: 28, height: 28, borderRadius: 6, color: FAINT }}>‹</button>
              <div style={{ fontSize: 14, fontWeight: 600, color: BODY }}>
                {MONTH_NAMES[shown.month - 1]} {shown.year}
              </div>
              <button type="button" aria-label="Next month" onClick={() => step(1)}
                style={{ width: 28, height: 28, borderRadius: 6, color: FAINT }}>›</button>
            </div>
            <div className="grid" style={{ gridTemplateColumns: 'repeat(7, 1fr)', rowGap: 2 }}>
              {WEEKDAYS.map((d) => (
                <div key={d} style={{ fontSize: 12, fontWeight: 500, color: FAINT,
                  textAlign: 'center', height: 28, lineHeight: '28px' }}>{d}</div>
              ))}
              {monthDays(shown.year, shown.month).map((d) => {
                const chosen = d.year === wall.year && d.month === wall.month && d.day === wall.day
                const outside = d.month !== shown.month
                return (
                  <button key={`${d.year}-${d.month}-${d.day}`} type="button"
                    aria-label={`${MONTH_NAMES[d.month - 1]} ${d.day}, ${d.year}`}
                    aria-pressed={chosen}
                    onClick={() => onChange(instantOf(withDay(wall, d), timeZone))}
                    style={{ height: 32, borderRadius: 16, fontSize: 13,
                      color: chosen ? '#fff' : outside ? PLACEHOLDER : BODY,
                      backgroundColor: chosen ? PRIMARY : 'transparent' }}>
                    {d.day}
                  </button>
                )
              })}
            </div>
          </div>
          <div ref={list} role="listbox" aria-label={label + ' time'}
            style={{ width: 112, maxHeight: 300, overflowY: 'auto', padding: 4,
              borderLeft: '1px solid ' + BORDER }}>
            {SLOTS.map((s) => (
              <button key={s.minutes} type="button" role="option"
                aria-selected={s.minutes === minutes}
                onClick={() => { onChange(instantOf(withMinutes(wall, s.minutes), timeZone)); close() }}
                className="block w-full text-left hover:bg-[rgb(249,250,251)]"
                style={{ padding: '6px 10px', fontSize: 13, borderRadius: 6,
                  color: s.minutes === minutes ? PRIMARY : BODY,
                  backgroundColor: s.minutes === minutes ? PRIMARY_TINT : undefined }}>
                {s.label}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
