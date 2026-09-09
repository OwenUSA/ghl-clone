import { useQuery, useQueryClient } from '@tanstack/react-query'
import { IconChevronDown, IconPlus, IconSettings } from '../components/Icon'
import { NewAppointmentDialog } from '../components/NewAppointmentDialog'
import { useMemo, useState } from 'react'
import {
  listAppointments, listCalendars, listUsers,
  type Appointment,
} from '../lib/api'
import type { Me } from '../lib/auth'
import {
  DEFAULT_SLOT_HOUR, MONTH_CELL_CHIPS, bucketByDay, defaultSlot, isSameDay,
  queryWindow, rangeLabel, shiftAnchor, slotAt, visibleDays,
  type CalendarView,
} from '../lib/calendarGrid'

/**
 * Measured from captures/diag_cal.png + captures/calendars/ (1440x900):
 *   tabs      Calendar view | Appointment list view | Calendar settings
 *   toolbar   Today | ‹ Jul 26 – Aug 1, 2026 › | Week view ▾ | Meetings ▾ | ☀ |
 *             Manage view | + New
 *   grid      GMT-04:00 gutter, day columns "26 Sun".."01 Sat", All day row,
 *             hourly rows, red current-time line
 *   panel     Manage view: View by type (All / Appointments / Blocked slots),
 *             Show buffer time toggle, Filters + search, Users, Calendars
 *   week      starts SUNDAY (26 Sun ... 01 Sat)
 *
 * Day view and Week view are the measured ones and are unchanged.
 *
 * MONTH VIEW IS OURS, NOT GHL'S. `captures/calendars/` holds the Week view only;
 * GHL's Month view was never opened on the live account, so there is nothing to
 * match it against. A conventional weeks-by-days grid of day cells was built at
 * the owner's request — see the 2026-09-09 amendment in DECISIONS.md. Do not
 * mistake it for measured parity, and re-measure if a live session is ever
 * opened again.
 *
 * The old Month view was a bug, not a design: `days = 35` drove the label, the
 * query window and the arrows while both grid renders did `slice(0, 7)`, so it
 * was pixel-identical to Week view, fetched five times the data, and paged past
 * 28 days that were fetched and never drawn.
 */
const VIEWS = ['Day view', 'Week view', 'Month view'] as const

const HOURS = Array.from({ length: 24 }, (_, i) => i)
/** One hour of the vertical grid, in px. The current-time line shares it. */
const HOUR_PX = 48

const fmtHour = (h: number) =>
  h === 0 ? '12 AM' : h < 12 ? `${h} AM` : h === 12 ? '12 PM' : `${h - 12} PM`

const fmtChipTime = (d: Date) =>
  d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })

export function CalendarsPage({ user }: { user: Me }) {
  const [view, setView] = useState<CalendarView>('Week view')
  const [anchor, setAnchor] = useState(() => new Date())
  const [kind, setKind] = useState<'all' | 'appointments' | 'blocked'>('all')
  const [showPanel, setShowPanel] = useState(true)
  const [tab, setTab] = useState<'calendar' | 'list'>('calendar')
  const [selectedUsers, setSelectedUsers] = useState<number[]>([])
  const [selectedCals, setSelectedCals] = useState<number[]>([])
  const [filterQ, setFilterQ] = useState('')
  // The slot a double-click or the New button asked to book. Null = closed.
  const [draft, setDraft] = useState<Date | null>(null)
  const qc = useQueryClient()

  // `POST /api/appointments` is auth.STAFF, so a TECH's create is refused with
  // `role TECH may not do this`. Mirror that here rather than let them fill the
  // dialog and find out on submit — the same gate ContactsPage puts on Add
  // Contact, derived from the role so the two cannot drift apart.
  const canCreate = user.role !== 'TECH'

  // ONE source for what is drawn, what is fetched and what the label says. They
  // used to be derived separately, which is how Month view came to draw a week,
  // fetch five, and page by 35 days.
  const dayList = useMemo(() => visibleDays(view, anchor), [view, anchor])
  const window_ = useMemo(() => queryWindow(view, anchor), [view, anchor])

  const users = useQuery({ queryKey: ['users'], queryFn: listUsers })
  const calendars = useQuery({ queryKey: ['calendars'], queryFn: listCalendars })
  const appts = useQuery({
    queryKey: ['appointments', window_.start.toISOString(),
               window_.end.toISOString(), kind, selectedUsers, selectedCals],
    queryFn: () =>
      listAppointments({
        start: window_.start.toISOString(),
        end: window_.end.toISOString(),
        kind,
        user_ids: selectedUsers,
        calendar_ids: selectedCals,
      }),
  })

  const now = new Date()
  const nowOffset = now.getHours() * HOUR_PX + (now.getMinutes() / 60) * HOUR_PX
  // One bucket per drawn cell, so nothing can be fetched and then not drawn.
  const buckets = useMemo(
    () => bucketByDay(dayList, appts.data ?? []),
    [dayList, appts.data],
  )

  function shift(dir: number) {
    setAnchor((a) => shiftAnchor(view, a, dir))
  }

  /** Open the create dialog on a slot. A no-op for a role that cannot create. */
  function book(at: Date) {
    if (!canCreate) return
    setDraft(at)
  }

  /**
   * Double-click on an empty part of a day column in Day/Week view. The offset
   * inside the column is read against the column's own box, not the hour cell
   * under the pointer, so the prefilled time is the minute that was clicked
   * (snapped to the half hour) rather than the top of the hour.
   */
  function bookFromColumn(d: Date, e: React.MouseEvent<HTMLDivElement>) {
    const box = e.currentTarget.getBoundingClientRect()
    book(slotAt(d, (e.clientY - box.top) / HOUR_PX))
  }

  return (
    <div className="flex h-screen min-w-0 flex-1 flex-col" style={{ backgroundColor: 'rgb(249,250,251)' }}>
      <div
        className="flex shrink-0 items-center gap-6 bg-white px-4"
        style={{ height: 90, borderBottom: '1px solid rgb(234,236,240)' }}
      >
        <div style={{ fontSize: 18, fontWeight: 500, color: 'rgb(31,41,55)' }}>Calendars</div>
        {([['calendar', 'Calendar view'], ['list', 'Appointment list view']] as const).map(
          ([k, label]) => (
            <button
              key={k}
              onClick={() => setTab(k)}
              style={{
                fontSize: 14,
                fontWeight: 500,
                color: tab === k ? 'rgb(56,160,219)' : 'rgb(102,112,133)',
              }}
            >
              {label}
            </button>
          ),
        )}
        <div style={{ fontSize: 14, fontWeight: 500, color: 'rgb(102,112,133)' }}>
          Calendar settings
        </div>
      </div>

      {/* toolbar */}
      <div className="flex shrink-0 items-center gap-2 px-4" style={{ height: 60 }}>
        <button
          onClick={() => setAnchor(new Date())}
          style={{
            height: 36, padding: '0 14px', borderRadius: 6,
            border: '1px solid rgb(234,236,240)', backgroundColor: '#fff',
            fontSize: 14, fontWeight: 600, color: 'rgb(52,64,84)',
          }}
        >
          Today
        </button>
        <div
          className="flex items-center"
          style={{ border: '1px solid rgb(234,236,240)', borderRadius: 6, backgroundColor: '#fff' }}
        >
          <button onClick={() => shift(-1)} aria-label="Previous" style={{ padding: '0 10px', height: 36 }}>‹</button>
          <div style={{ minWidth: 200, textAlign: 'center', fontSize: 14, fontWeight: 600, color: 'rgb(52,64,84)' }}>
            {rangeLabel(view, anchor)}
          </div>
          <button onClick={() => shift(1)} aria-label="Next" style={{ padding: '0 10px', height: 36 }}>›</button>
        </div>
        <select
          value={view}
          onChange={(e) => setView(e.target.value as CalendarView)}
          style={{
            height: 36, borderRadius: 6, border: '1px solid rgb(234,236,240)',
            padding: '0 10px', fontSize: 14, fontWeight: 600,
            color: 'rgb(52,64,84)', backgroundColor: '#fff',
          }}
        >
          {VIEWS.map((v) => <option key={v}>{v}</option>)}
        </select>

        <select
          title="Event type filter (measured in GHL as 'Meetings')"
          style={{
            height: 36, borderRadius: 6, border: '1px solid rgb(234,236,240)',
            padding: '0 10px', fontSize: 14, fontWeight: 600,
            color: 'rgb(0,78,235)', backgroundColor: '#fff',
          }}
        >
          <option>Meetings</option>
        </select>

        <div className="ml-auto flex items-center gap-2">
          <button
            onClick={() => setShowPanel((s) => !s)}
            style={{
              height: 37, padding: '0 12px', borderRadius: 8,
              border: '0.8px solid rgb(234,236,240)', backgroundColor: '#fff',
              fontSize: 14, fontWeight: 600, color: 'rgb(0,78,235)',
              display: 'flex', alignItems: 'center', gap: 8,
            }}
          >
            <IconSettings size={16} color="rgb(0,78,235)" />
            Manage view
          </button>
          {/* Same dialog as a double-click on an empty slot, prefilled with a
              day that is actually on screen. Disabled for a TECH, with a title
              saying why — the precedent Add Contact set. */}
          <button
            disabled={!canCreate}
            onClick={() => book(defaultSlot(dayList, now))}
            title={canCreate
              ? 'Create an appointment'
              : 'Creating an appointment is staff-only, and your role is TECH'}
            style={{
              height: 36, padding: '0 12px', borderRadius: 6, fontSize: 14,
              fontWeight: 500, color: '#fff', backgroundColor: 'rgb(0,78,235)',
              opacity: canCreate ? 1 : 0.5,
              cursor: canCreate ? 'pointer' : 'not-allowed',
              display: 'flex', alignItems: 'center', gap: 6,
            }}
          >
            <IconPlus size={16} color="#fff" />
            New
          </button>
        </div>
      </div>

      <div className="flex min-h-0 flex-1 gap-3 px-4 pb-4">
        <div
          className="flex min-w-0 flex-1 flex-col overflow-hidden bg-white"
          style={{ borderRadius: 8, border: '1px solid rgb(234,236,240)' }}
        >
          {tab === 'list' ? (
            <div className="min-h-0 flex-1 overflow-auto">
              <table className="w-full">
                <thead>
                  <tr>
                    {['Title', 'Contact', 'Starts', 'Ends', 'Status'].map((h) => (
                      <th key={h} className="sticky top-0 bg-white text-left"
                        style={{
                          height: 44, padding: '0 12px', fontSize: 13, fontWeight: 700,
                          color: 'rgb(71,84,103)', borderBottom: '1px solid rgb(234,236,240)',
                        }}>
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {appts.data?.length === 0 && (
                    <tr><td colSpan={5} style={{ padding: 24, fontSize: 14 }}>
                      No appointments in this range.
                    </td></tr>
                  )}
                  {appts.data?.map((a: Appointment) => (
                    <tr key={a.id} className="hover:bg-[rgb(249,250,251)]">
                      <td style={{ height: 48, padding: '0 12px', fontSize: 14, borderBottom: '1px solid rgb(242,244,247)' }}>{a.title}</td>
                      <td style={{ padding: '0 12px', fontSize: 14, borderBottom: '1px solid rgb(242,244,247)' }}>{a.contact_name ?? ''}</td>
                      <td style={{ padding: '0 12px', fontSize: 14, borderBottom: '1px solid rgb(242,244,247)' }}>{new Date(a.starts_at).toLocaleString('en-US')}</td>
                      <td style={{ padding: '0 12px', fontSize: 14, borderBottom: '1px solid rgb(242,244,247)' }}>{new Date(a.ends_at).toLocaleTimeString('en-US')}</td>
                      <td style={{ padding: '0 12px', fontSize: 14, borderBottom: '1px solid rgb(242,244,247)' }}>{a.status}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : view === 'Month view' ? (
            <MonthGrid
              cells={dayList}
              buckets={buckets}
              month={anchor.getMonth()}
              today={now}
              canCreate={canCreate}
              onBook={book}
            />
          ) : (
            <>
              {/* day header */}
              <div className="flex shrink-0" style={{ borderBottom: '1px solid rgb(234,236,240)' }}>
                <div style={{ width: 60, fontSize: 11, color: 'rgb(152,162,179)', padding: '8px 4px' }}>
                  GMT{-new Date().getTimezoneOffset() / 60}
                </div>
                {dayList.map((d) => {
                  const isToday = isSameDay(d, now)
                  return (
                    <div key={d.toISOString()} className="flex-1 text-center"
                      style={{
                        padding: '8px 0', fontSize: 14, fontWeight: 500,
                        color: isToday ? 'rgb(0,78,235)' : 'rgb(102,112,133)',
                      }}>
                      {String(d.getDate()).padStart(2, '0')}{' '}
                      {d.toLocaleDateString('en-US', { weekday: 'short' })}
                    </div>
                  )
                })}
              </div>

              {/* hour grid - this pane scrolls, not the document */}
              <div className="relative min-h-0 flex-1 overflow-y-auto">
                <div className="flex">
                  <div style={{ width: 60 }}>
                    {HOURS.map((h) => (
                      <div key={h} style={{ height: HOUR_PX, fontSize: 11, color: 'rgb(152,162,179)', padding: '2px 6px' }}>
                        {fmtHour(h)}
                      </div>
                    ))}
                  </div>
                  {dayList.map((d, i) => (
                    <div key={d.toISOString()} className="relative flex-1"
                      // Double-click an EMPTY part of the column to book that slot.
                      // The appointment blocks below stop the event, so a
                      // double-click on a booking is not a create.
                      onDoubleClick={(e) => bookFromColumn(d, e)}
                      title={canCreate ? 'Double-click an empty slot to book it' : undefined}
                      style={{
                        borderLeft: '1px solid rgb(242,244,247)',
                        cursor: canCreate ? 'copy' : 'default',
                      }}>
                      {HOURS.map((h) => (
                        <div key={h} style={{ height: HOUR_PX, borderBottom: '1px solid rgb(242,244,247)' }} />
                      ))}
                      {isSameDay(d, now) && (
                        <div style={{
                          position: 'absolute', left: 0, right: 0, top: nowOffset,
                          borderTop: '2px solid rgb(217,45,32)',
                        }} />
                      )}
                      {buckets[i].map((a) => {
                        const s = new Date(a.starts_at)
                        const e = new Date(a.ends_at)
                        const top = s.getHours() * HOUR_PX + (s.getMinutes() / 60) * HOUR_PX
                        const h = Math.max(24, ((e.getTime() - s.getTime()) / 3_600_000) * HOUR_PX)
                        return (
                          <div key={a.id} title={a.title}
                            onDoubleClick={(ev) => ev.stopPropagation()}
                            style={{
                              position: 'absolute', left: 2, right: 2, top, height: h,
                              backgroundColor: 'rgb(239,244,255)',
                              borderLeft: `3px solid ${a.color}`,
                              borderRadius: 4, padding: '2px 6px', overflow: 'hidden',
                              fontSize: 12, color: 'rgb(52,64,84)',
                            }}>
                            <div className="truncate" style={{ fontWeight: 500 }}>{a.title}</div>
                            <div className="truncate">{fmtChipTime(s)}</div>
                          </div>
                        )
                      })}
                    </div>
                  ))}
                </div>
              </div>
            </>
          )}
        </div>

        {/* Manage view panel */}
        {showPanel && (
          <div className="w-[300px] shrink-0 overflow-y-auto bg-white"
            style={{ borderRadius: 8, border: '1px solid rgb(234,236,240)', padding: 16 }}>
            <div className="flex items-center justify-between">
              <div style={{ fontSize: 16, fontWeight: 600, color: 'rgb(16,24,40)' }}>Manage view</div>
              <button onClick={() => setShowPanel(false)} aria-label="Close">✕</button>
            </div>

            <div style={{ marginTop: 16, borderRadius: 8, backgroundColor: 'rgb(249,250,251)', padding: 12 }}>
              <div style={{ fontSize: 14, fontWeight: 600, color: 'rgb(16,24,40)' }}>View by type</div>
              {([['all', 'All'], ['appointments', 'Appointments'], ['blocked', 'Blocked slots']] as const).map(
                ([k, label]) => (
                  <label key={k} className="mt-2 flex items-center gap-2" style={{ fontSize: 14 }}>
                    <input type="radio" checked={kind === k} onChange={() => setKind(k)} />
                    {label}
                  </label>
                ),
              )}
              <label className="mt-3 flex items-center gap-2" style={{ fontSize: 14 }}>
                <input type="checkbox" disabled title="Buffer time is not modelled in v1" />
                <span style={{ color: 'rgb(152,162,179)' }}>Show buffer time</span>
              </label>
            </div>

            <div className="mt-4 flex items-center justify-between">
              <div style={{ fontSize: 14, fontWeight: 600, color: 'rgb(16,24,40)' }}>Filters</div>
              <button
                onClick={() => { setSelectedUsers([]); setSelectedCals([]) }}
                style={{ fontSize: 12, fontWeight: 600, color: 'rgb(0,78,235)' }}>
                Clear all
              </button>
            </div>

            <input
              value={filterQ}
              onChange={(e) => setFilterQ(e.target.value)}
              placeholder="Search users, calendars, or groups"
              style={{
                marginTop: 10, width: '100%', height: 34, fontSize: 14,
                borderRadius: 6, border: '1px solid rgb(234,236,240)', padding: '0 10px',
              }}
            />

            <FilterGroup
              title="Users"
              count={selectedUsers.length}
              items={(users.data ?? [])
                .filter((u) => u.name.toLowerCase().includes(filterQ.toLowerCase()))
                .map((u) => ({ id: u.id, label: u.name }))}
              selected={selectedUsers}
              onToggle={(id, on) =>
                setSelectedUsers((v) => (on ? [...v, id] : v.filter((x) => x !== id)))}
            />

            <FilterGroup
              title="Calendars"
              count={selectedCals.length}
              items={(calendars.data ?? [])
                .filter((c) => c.name.toLowerCase().includes(filterQ.toLowerCase()))
                .map((c) => ({ id: c.id, label: c.name, color: c.color }))}
              selected={selectedCals}
              onToggle={(id, on) =>
                setSelectedCals((v) => (on ? [...v, id] : v.filter((x) => x !== id)))}
            />

          </div>
        )}
      </div>

      {draft && (
        <NewAppointmentDialog
          initialStart={draft}
          initialEnd={new Date(draft.getTime() + 3_600_000)}
          onClose={() => setDraft(null)}
          onCreated={() => {
            setDraft(null)
            // The new booking has to appear without a manual refresh, and the
            // range key changes with every view/anchor, so invalidate the whole
            // 'appointments' family rather than this one key.
            qc.invalidateQueries({ queryKey: ['appointments'] })
          }}
        />
      )}
    </div>
  )
}

/**
 * OUR OWN month grid — GHL's was never captured (DECISIONS.md, 2026-09-09).
 *
 * Whole Sunday-to-Saturday weeks of day cells, so every day of the month is on
 * screen. The 24-hour vertical grid Day and Week view use cannot take a month:
 * 35 columns of it is not a layout, which is why the old code capped at 7 and
 * silently dropped four weeks.
 *
 * Cells outside the anchor month are drawn dimmed rather than blank — a blank
 * cell reads as "nothing booked", and one of those days may well have a booking.
 */
function MonthGrid({
  cells, buckets, month, today, canCreate, onBook,
}: {
  cells: Date[]
  buckets: Appointment[][]
  month: number
  today: Date
  canCreate: boolean
  onBook: (at: Date) => void
}) {
  const weeks = cells.length / 7
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0" style={{ borderBottom: '1px solid rgb(234,236,240)' }}>
        {cells.slice(0, 7).map((d) => (
          <div key={d.toISOString()} className="flex-1 text-center"
            style={{ padding: '8px 0', fontSize: 13, fontWeight: 600, color: 'rgb(102,112,133)' }}>
            {d.toLocaleDateString('en-US', { weekday: 'short' })}
          </div>
        ))}
      </div>

      <div
        className="grid min-h-0 flex-1 overflow-y-auto"
        style={{
          gridTemplateColumns: 'repeat(7, minmax(0, 1fr))',
          gridAutoRows: `minmax(${Math.max(96, Math.floor(560 / weeks))}px, auto)`,
        }}
      >
        {cells.map((d, i) => {
          const outside = d.getMonth() !== month
          const isToday = isSameDay(d, today)
          const list = buckets[i]
          const shown = list.slice(0, MONTH_CELL_CHIPS)
          const hidden = list.length - shown.length
          return (
            <div
              key={d.toISOString()}
              data-month-cell={d.toDateString()}
              // Double-click a day cell to book it. A month cell has no hour
              // under the pointer, so the slot falls back to the default hour.
              onDoubleClick={() => onBook(new Date(d.getFullYear(), d.getMonth(), d.getDate(),
                                                   DEFAULT_SLOT_HOUR, 0, 0, 0))}
              title={canCreate ? 'Double-click to book this day' : undefined}
              className="flex min-w-0 flex-col overflow-hidden"
              style={{
                borderTop: '1px solid rgb(242,244,247)',
                borderLeft: '1px solid rgb(242,244,247)',
                backgroundColor: outside ? 'rgb(252,252,253)' : '#fff',
                padding: 6,
                cursor: canCreate ? 'copy' : 'default',
              }}
            >
              <div
                className="shrink-0 self-end"
                style={{
                  fontSize: 12,
                  fontWeight: isToday ? 700 : 500,
                  minWidth: 22, textAlign: 'center', lineHeight: '18px',
                  borderRadius: 9,
                  color: isToday ? '#fff'
                    : outside ? 'rgb(190,197,209)' : 'rgb(52,64,84)',
                  backgroundColor: isToday ? 'rgb(0,78,235)' : 'transparent',
                }}
              >
                {d.getDate()}
              </div>

              <div className="mt-1 flex min-h-0 flex-col gap-1 overflow-hidden">
                {shown.map((a) => (
                  <div
                    key={a.id}
                    data-appointment={a.id}
                    title={`${a.title} — ${fmtChipTime(new Date(a.starts_at))}`}
                    onDoubleClick={(e) => e.stopPropagation()}
                    className="truncate"
                    style={{
                      fontSize: 11, lineHeight: '16px', borderRadius: 3,
                      padding: '1px 4px', color: 'rgb(52,64,84)',
                      backgroundColor: 'rgb(239,244,255)',
                      borderLeft: `3px solid ${a.color}`,
                    }}
                  >
                    {fmtChipTime(new Date(a.starts_at))} {a.title}
                  </div>
                ))}
                {hidden > 0 && (
                  <div
                    title={list.slice(MONTH_CELL_CHIPS).map((a) => a.title).join('\n')}
                    onDoubleClick={(e) => e.stopPropagation()}
                    style={{ fontSize: 11, fontWeight: 600, color: 'rgb(0,78,235)' }}
                  >
                    +{hidden} more
                  </div>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function FilterGroup({
  title, items, selected, onToggle, count,
}: {
  title: string
  items: { id: number; label: string; color?: string }[]
  selected: number[]
  onToggle: (id: number, on: boolean) => void
  count: number
}) {
  const [open, setOpen] = useState(true)
  return (
    <div style={{ marginTop: 14 }}>
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-1 text-left"
        style={{ fontSize: 13, fontWeight: 600, color: 'rgb(52,64,84)' }}
      >
        <IconChevronDown
          size={14}
          color="rgb(102,112,133)"
          className={open ? undefined : '-rotate-90'}
        />
        {title}
        {count > 0 && (
          <span style={{
            fontSize: 11, color: '#fff', backgroundColor: 'rgb(21,112,239)',
            borderRadius: 4, padding: '0 5px', marginLeft: 4,
          }}>{count}</span>
        )}
      </button>
      {open && items.map((it) => (
        <label key={it.id} className="mt-2 flex items-center gap-2"
          style={{ fontSize: 14, color: 'rgb(102,112,133)' }}>
          <input
            type="checkbox"
            checked={selected.includes(it.id)}
            onChange={(e) => onToggle(it.id, e.target.checked)}
          />
          {it.color && (
            <span style={{
              width: 8, height: 8, borderRadius: 2, backgroundColor: it.color,
              display: 'inline-block',
            }} />
          )}
          <span className="truncate">{it.label}</span>
        </label>
      ))}
      {open && items.length === 0 && (
        <div style={{ fontSize: 13, color: 'rgb(152,162,179)', marginTop: 6 }}>None</div>
      )}
    </div>
  )
}
