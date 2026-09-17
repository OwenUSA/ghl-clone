import { useQuery, useQueryClient } from '@tanstack/react-query'
import { IconChevronDown, IconPlus, IconSettings } from '../components/Icon'
import { LOCK_REASON, visitLocked } from '../lib/zuper'
import { AppointmentDetailDialog } from '../components/AppointmentDetailDialog'
import { NewAppointmentDialog } from '../components/NewAppointmentDialog'
import { useEffect, useMemo, useRef, useState } from 'react'
import {
  listAppointments, listBlockedTimes, listCalendars, listUsers,
  type Appointment, type BlockedTime,
} from '../lib/api'
import type { Me } from '../lib/auth'
import { PageTabs } from '../components/PageTabs'
import {
  DEFAULT_SLOT_HOUR, FIRST_VISIBLE_HOUR, MIN_HOUR_PX, MONTH_CELL_CHIPS, bucketByOverlap,
  defaultSlot, hourPxFor, isSameDay, layoutDay, queryWindow, rangeLabel, shiftAnchor,
  slotAt, timeRange, visibleDays,
  type CalendarView,
} from '../lib/calendarGrid'
import { isRestricted } from '../lib/access'

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

/**
 * Every hour of the day is drawn, 12 AM to 11 PM, so a 2 AM emergency booking still
 * has somewhere to be drawn. The pane opens scrolled to FIRST_VISIBLE_HOUR (7 AM,
 * where Workiz starts — the owner, 2026-09-15) and each hour is as tall as it can be
 * while 7 AM–7 PM fits without scrolling, never below MIN_HOUR_PX (`hourPxFor`).
 */
const HOURS = Array.from({ length: 24 }, (_, i) => i)

/** One text line inside a block on the hour grid, in px. */
const LINE_PX = 16

/**
 * Blocked off time is drawn grey and hatched, never in a calendar's colour, so it
 * cannot be mistaken for a booking.
 */
const BLOCKED_FILL = 'repeating-linear-gradient(135deg, rgb(242,244,247) 0 6px, rgb(234,236,240) 6px 12px)'

const fmtHour = (h: number) =>
  h === 0 ? '12 AM' : h < 12 ? `${h} AM` : h === 12 ? '12 PM' : `${h - 12} PM`

const fmtChipTime = (d: Date) =>
  d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })

/** A month chip's time: the start, or "until 10:00 AM" on a later day of a multi-day job. */
const chipTime = (a: { starts_at: string; ends_at: string }, cell: Date) =>
  isSameDay(new Date(a.starts_at), cell)
    ? fmtChipTime(new Date(a.starts_at))
    : 'until ' + fmtChipTime(new Date(a.ends_at))

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
  // The appointment whose detail panel is open. Null = closed.
  const [openAppt, setOpenAppt] = useState<number | null>(null)
  // The blocked off time whose tab is open in the Book appointment modal.
  const [openBlocked, setOpenBlocked] = useState<number | null>(null)
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

  // Under the 'appointments' key family on purpose: every place that invalidates
  // ['appointments'] after a write now refreshes the blocks too.
  const blocked = useQuery({
    queryKey: ['appointments', 'blocked-times', window_.start.toISOString(),
               window_.end.toISOString(), selectedUsers, selectedCals],
    queryFn: () =>
      listBlockedTimes({
        start: window_.start.toISOString(),
        end: window_.end.toISOString(),
        user_ids: selectedUsers,
        calendar_ids: selectedCals,
      }),
    // "View by type: Appointments" hides them; All and Blocked slots show them.
    enabled: kind !== 'appointments',
  })
  // By OVERLAP, not by start day: a job running past midnight is on every day it
  // touches, in the month cells and on the hour grid alike.
  const blockedBuckets = useMemo(
    () => bucketByOverlap(dayList, kind === 'appointments' ? [] : blocked.data ?? []),
    [dayList, kind, blocked.data],
  )

  // The hour grid fits 7 AM–7 PM into the pane where the screen allows, and opens
  // scrolled to 7 AM.
  const hourPane = useRef<HTMLDivElement>(null)
  const [hourPx, setHourPx] = useState(MIN_HOUR_PX)
  useEffect(() => {
    const el = hourPane.current
    if (!el) return
    const measure = () => setHourPx(hourPxFor(el.clientHeight))
    measure()
    const watcher = new ResizeObserver(measure)
    watcher.observe(el)
    return () => watcher.disconnect()
  }, [view, tab])
  useEffect(() => {
    if (hourPane.current) hourPane.current.scrollTop = FIRST_VISIBLE_HOUR * hourPx
  }, [view, tab, hourPx])

  const now = new Date()
  const nowOffset = now.getHours() * hourPx + (now.getMinutes() / 60) * hourPx
  // One bucket per drawn cell, so nothing can be fetched and then not drawn.
  const buckets = useMemo(
    () => bucketByOverlap(dayList, appts.data ?? []),
    [dayList, appts.data],
  )
  // Day/Week: each column's bookings AND blocked time laid out side by side in one
  // pass, so neither can be drawn behind the other.
  const layouts = useMemo(
    () => dayList.map((d, i) => layoutDay<GridItem>(d, [
      ...blockedBuckets[i].map((b) => ({ starts_at: b.starts_at, ends_at: b.ends_at, blocked: b })),
      ...buckets[i].map((a) => ({ starts_at: a.starts_at, ends_at: a.ends_at, appt: a })),
    ])),
    [dayList, buckets, blockedBuckets],
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
   * Open a booking's detail panel. NOT gated on the role: reading one is
   * `auth.ANY_USER`, and the panel itself disables the controls a TECH may not
   * use. A tech looking at their own day needs to see the notes and the phone
   * number on the job they are driving to.
   */
  function open(id: number, e: React.MouseEvent) {
    // The day column underneath listens for a double-click to book an empty
    // slot; a click that lands on a booking is not a request for a new one.
    e.stopPropagation()
    setOpenAppt(id)
  }

  /** Open blocked off time on its tab of the Book appointment modal. Anyone may
   *  open one; the modal disables the controls a TECH cannot use. */
  function openBlock(id: number, e: React.MouseEvent) {
    e.stopPropagation()
    setOpenBlocked(id)
  }

  /**
   * Double-click on an empty part of a day column in Day/Week view. The offset
   * inside the column is read against the column's own box, not the hour cell
   * under the pointer, so the prefilled time is the minute that was clicked
   * (snapped to the half hour) rather than the top of the hour.
   */
  function bookFromColumn(d: Date, e: React.MouseEvent<HTMLDivElement>) {
    const box = e.currentTarget.getBoundingClientRect()
    book(slotAt(d, (e.clientY - box.top) / hourPx))
  }

  return (
    <div className="flex h-screen min-w-0 flex-1 flex-col" style={{ backgroundColor: 'rgb(249,250,251)' }}>
      <PageTabs title="Calendars" label="Calendars views" active={tab}
        tabs={[
          ...([['calendar', 'Calendar view'], ['list', 'Appointment list view']] as const).map(
            ([k, label]) => ({ key: k, label, onSelect: () => setTab(k) })),
          { key: 'settings', label: 'Calendar settings' },
        ]} />

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
                    <tr key={a.id} data-appointment={a.id}
                      onClick={(e) => open(a.id, e)}
                      title="Click for details"
                      className="cursor-pointer hover:bg-[rgb(249,250,251)]"
                      style={{
                        opacity: a.status === 'cancelled' ? 0.55 : 1,
                        textDecoration: a.status === 'cancelled' ? 'line-through' : undefined,
                      }}>
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
              blocked={blockedBuckets}
              onOpenBlocked={openBlock}
              month={anchor.getMonth()}
              today={now}
              canCreate={canCreate}
              onBook={book}
              onOpen={open}
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
              <div ref={hourPane} data-hour-pane className="relative min-h-0 flex-1 overflow-y-auto">
                <div className="flex">
                  <div style={{ width: 60 }}>
                    {HOURS.map((h) => (
                      <div key={h} style={{ height: hourPx, fontSize: 11, color: 'rgb(152,162,179)', padding: '2px 6px' }}>
                        {fmtHour(h)}
                      </div>
                    ))}
                  </div>
                  {dayList.map((d, i) => (
                    <div key={d.toISOString()} className="relative flex-1"
                      data-day-column={d.toDateString()}
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
                        <div key={h} style={{ height: hourPx, borderBottom: '1px solid rgb(242,244,247)' }} />
                      ))}
                      {isSameDay(d, now) && (
                        <div style={{
                          position: 'absolute', left: 0, right: 0, top: nowOffset,
                          borderTop: '2px solid rgb(217,45,32)', zIndex: 2,
                        }} />
                      )}
                      {layouts[i].map((p) => {
                        // Real duration for the height; the lane decides left and width.
                        const height = ((p.endMin - p.startMin) / 60) * hourPx
                        // A job carried over from yesterday starts at midnight, far above
                        // where the grid opens: its words go at 7 AM instead.
                        const opensAt = FIRST_VISIBLE_HOUR * hourPx
                        const box: BlockBox = {
                          top: (p.startMin / 60) * hourPx,
                          height,
                          textTop: p.fromBefore && height > opensAt + 2 * LINE_PX ? opensAt : 0,
                          left: `calc(${(p.col / p.cols) * 100}% + 1px)`,
                          width: `calc(${(p.span / p.cols) * 100}% - 3px)`,
                          fromBefore: p.fromBefore,
                          untilAfter: p.untilAfter,
                        }
                        const { blocked: b, appt: a } = p.item
                        return b ? (
                          <BlockedBlock key={'b' + b.id} b={b} box={box}
                            label={timeRange(b.starts_at, b.ends_at)}
                            onOpen={openBlock} />
                        ) : a ? (
                          <AppointmentBlock key={a.id} a={a} box={box} onOpen={open} />
                        ) : null
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
                // "Only assigned data": their own day is the only one they can see, so
                // the Users filter offers only them (every other user would filter to nothing).
                .filter((u) => !isRestricted(user) || u.id === user.id)
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

      {/* Clicking a booking opens it. The panel is keyed on the id so switching
          from one appointment to another remounts it with a clean form rather
          than carrying an unsaved draft across. */}
      {openAppt !== null && (
        <AppointmentDetailDialog
          key={openAppt}
          appointmentId={openAppt}
          user={user}
          onClose={() => setOpenAppt(null)}
          onChanged={() => {
            // An edit or a reschedule has to show on the grid without a manual
            // refresh, and a rescheduled booking may have left the window the
            // current key asks for, so invalidate the whole 'appointments'
            // family rather than this one key.
            qc.invalidateQueries({ queryKey: ['appointments'] })
          }}
        />
      )}

      {openBlocked !== null && (
        <NewAppointmentDialog
          key={'blocked-' + openBlocked}
          blockedTimeId={openBlocked}
          initialStart={now}
          initialEnd={new Date(now.getTime() + 3_600_000)}
          user={user}
          onClose={() => setOpenBlocked(null)}
          onCreated={() => {
            setOpenBlocked(null)
            qc.invalidateQueries({ queryKey: ['appointments'] })
          }}
        />
      )}

      {draft && (
        <NewAppointmentDialog
          initialStart={draft}
          initialEnd={new Date(draft.getTime() + 3_600_000)}
          user={user}
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
  cells, buckets, blocked, month, today, canCreate, onBook, onOpen, onOpenBlocked,
}: {
  cells: Date[]
  buckets: Appointment[][]
  blocked: BlockedTime[][]
  onOpenBlocked: (id: number, e: React.MouseEvent) => void
  month: number
  today: Date
  canCreate: boolean
  onBook: (at: Date) => void
  onOpen: (id: number, e: React.MouseEvent) => void
}) {
  const weeks = cells.length / 7
  // The day whose "+N more" is open, and where on screen it was clicked.
  const [more, setMore] = useState<{ i: number; x: number; y: number } | null>(null)
  useEffect(() => {
    if (!more) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setMore(null) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [more])
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
                {blocked[i].map((b) => (
                  <div
                    key={'b' + b.id}
                    data-blocked-time={b.id}
                    title={`Blocked off: ${b.title} — ${fmtChipTime(new Date(b.starts_at))}`
                           + ' — click to open'}
                    onClick={(e) => onOpenBlocked(b.id, e)}
                    onDoubleClick={(e) => e.stopPropagation()}
                    className="truncate"
                    style={{
                      fontSize: 11, lineHeight: '16px', borderRadius: 3,
                      padding: '1px 4px', color: 'rgb(71,84,103)',
                      background: BLOCKED_FILL, borderLeft: '3px solid rgb(152,162,179)',
                      cursor: 'pointer',
                    }}
                  >
                    {fmtChipTime(new Date(b.starts_at))} {b.title}
                  </div>
                ))}
                {shown.map((a) => (
                  <div
                    key={a.id}
                    data-appointment={a.id}
                    data-zuper-locked={visitLocked(a) || undefined}
                    title={`${a.title} — ${timeRange(a.starts_at, a.ends_at)}`
                           + (a.status === 'cancelled' ? ' (cancelled)' : '')
                           + (visitLocked(a) ? ` — managed in Zuper (${LOCK_REASON})` : '')
                           + ' — click for details'}
                    onClick={(e) => onOpen(a.id, e)}
                    onDoubleClick={(e) => e.stopPropagation()}
                    className="truncate"
                    style={{
                      fontSize: 11, lineHeight: '16px', borderRadius: 3,
                      padding: '1px 4px', color: 'rgb(52,64,84)',
                      backgroundColor: 'rgb(239,244,255)',
                      borderLeft: `3px solid ${a.color}`,
                      cursor: 'pointer',
                      opacity: a.status === 'cancelled' ? 0.55 : 1,
                      textDecoration: a.status === 'cancelled' ? 'line-through' : undefined,
                    }}
                  >
                    {chipTime(a, d)} {a.title}
                  </div>
                ))}
                {hidden > 0 && (
                  <button
                    type="button"
                    data-month-more={d.toDateString()}
                    title={list.slice(MONTH_CELL_CHIPS).map((a) => a.title).join('\n')}
                    onClick={(e) => {
                      e.stopPropagation()
                      const r = e.currentTarget.getBoundingClientRect()
                      setMore({ i, x: r.left, y: r.bottom + 4 })
                    }}
                    onDoubleClick={(e) => e.stopPropagation()}
                    className="self-start"
                    style={{ fontSize: 11, fontWeight: 600, color: 'rgb(0,78,235)' }}
                  >
                    +{hidden} more
                  </button>
                )}
              </div>
            </div>
          )
        })}
      </div>

      {more && (
        <DayPopover
          day={cells[more.i]}
          appointments={buckets[more.i]}
          x={more.x} y={more.y}
          onOpen={(id, e) => { setMore(null); onOpen(id, e) }}
          onClose={() => setMore(null)}
        />
      )}
    </div>
  )
}

/**
 * GoHighLevel's "+N more": a card over the month grid listing EVERY booking of that
 * day, each opening its detail panel. Closed by a click outside or Escape.
 */
function DayPopover({ day, appointments, x, y, onOpen, onClose }: {
  day: Date
  appointments: Appointment[]
  x: number
  y: number
  onOpen: (id: number, e: React.MouseEvent) => void
  onClose: () => void
}) {
  const width = 280
  return (
    <>
      <div className="fixed inset-0" style={{ zIndex: 40 }} onClick={onClose}
        onDoubleClick={(e) => e.stopPropagation()} />
      <div role="dialog" aria-label={`Appointments on ${day.toDateString()}`}
        data-day-popover={day.toDateString()}
        className="fixed bg-white"
        style={{
          zIndex: 41, width, maxHeight: 360, overflowY: 'auto',
          left: Math.max(8, Math.min(x, window.innerWidth - width - 8)),
          top: Math.max(8, Math.min(y, window.innerHeight - 368)),
          borderRadius: 8, border: '1px solid rgb(234,236,240)', padding: 12,
          boxShadow: '0 12px 16px -4px rgba(16,24,40,0.08), 0 4px 6px -2px rgba(16,24,40,0.03)',
        }}>
        <div className="flex items-center justify-between">
          <div style={{ fontSize: 14, fontWeight: 600, color: 'rgb(16,24,40)' }}>
            {day.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' })}
          </div>
          <button onClick={onClose} aria-label="Close" style={{ color: 'rgb(102,112,133)' }}>✕</button>
        </div>
        <div className="mt-2 flex flex-col gap-1">
          {appointments.map((a) => (
            <div key={a.id} data-appointment={a.id}
              onClick={(e) => onOpen(a.id, e)}
              onDoubleClick={(e) => e.stopPropagation()}
              title="Click for details"
              className="cursor-pointer"
              style={{
                fontSize: 12, lineHeight: '16px', borderRadius: 4, padding: '4px 6px',
                backgroundColor: 'rgb(239,244,255)', borderLeft: `3px solid ${a.color}`,
                color: 'rgb(52,64,84)',
                opacity: a.status === 'cancelled' ? 0.55 : 1,
                textDecoration: a.status === 'cancelled' ? 'line-through' : undefined,
              }}>
              <div className="truncate" style={{ fontWeight: 500 }}>{a.title}</div>
              <div className="truncate">{timeRange(a.starts_at, a.ends_at)}</div>
            </div>
          ))}
        </div>
      </div>
    </>
  )
}

/** What one Day/Week column lays out: a booking or blocked off time. */
type GridItem = {
  starts_at: string
  ends_at: string
  appt?: Appointment
  blocked?: BlockedTime
}

/** Where a laid-out block sits in its day column (see `layoutDay`). */
type BlockBox = {
  top: number
  height: number
  left: string
  width: string
  /** How far down the block its text starts (a carried-over job's words sit at 7 AM). */
  textTop: number
  /** Began on an earlier day / carries on past midnight: that edge is squared off. */
  fromBefore: boolean
  untilAfter: boolean
}

const boxStyle = (box: BlockBox): React.CSSProperties => ({
  position: 'absolute', top: box.top, height: box.height, left: box.left, width: box.width,
  borderTopLeftRadius: box.fromBefore ? 0 : 4, borderTopRightRadius: box.fromBefore ? 0 : 4,
  borderBottomLeftRadius: box.untilAfter ? 0 : 4, borderBottomRightRadius: box.untilAfter ? 0 : 4,
  overflow: 'hidden', cursor: 'pointer', fontSize: 12, lineHeight: `${LINE_PX}px`,
  // A hairline of white keeps two side-by-side blocks of the same colour apart.
  boxShadow: '0 0 0 1px #fff',
})

/**
 * One booking on the Day/Week grid. Our design (the 2026-09-13 GHL look), carrying
 * what makes the owner's Workiz week useful, as far as the block's height allows:
 * the title (the customer), the time RANGE, then the Workiz Job # and the card's
 * street and city. A short block puts title and range on one line. Every line
 * truncates with an ellipsis; hovering shows all of it, and a click opens the
 * appointment detail panel.
 */
function AppointmentBlock({ a, box, onOpen }: {
  a: Appointment
  box: BlockBox
  onOpen: (id: number, e: React.MouseEvent) => void
}) {
  const off = a.status === 'cancelled'
  const range = timeRange(a.starts_at, a.ends_at)
  // The card's address; a booking with no card falls back to its meeting location.
  const place = a.opportunity_address ?? a.location
  const extra = [a.workiz_job_id ? `Job #${a.workiz_job_id}` : null, place]
    .filter((x): x is string => !!x)
  const lines = Math.max(1, Math.floor((box.height - box.textTop - 4) / LINE_PX))
  return (
    <div data-appointment={a.id} data-zuper-locked={visitLocked(a) || undefined}
      // Zuper v2: a sent job's visit is scheduled in Zuper — read-only here (the grid has
      // no drag or resize; its detail panel offers no edit or cancel).
      title={[a.title + (off ? ' (cancelled)' : ''), range, ...extra,
              a.calendar_name, visitLocked(a) ? `Managed in Zuper — ${LOCK_REASON}` : null,
              'Click for details'].filter(Boolean).join('\n')}
      onClick={(ev) => onOpen(a.id, ev)}
      onDoubleClick={(ev) => ev.stopPropagation()}
      style={{
        ...boxStyle(box),
        backgroundColor: 'rgb(239,244,255)',
        borderLeft: `3px solid ${a.color}`,
        padding: `${2 + box.textTop}px 4px 2px 6px`, color: 'rgb(52,64,84)',
        // Cancelled is a status, not a delete — the row survives and the Cancelled
        // report tile counts it. It has to be visibly not a live booking, or the
        // slot reads as still taken.
        opacity: off ? 0.55 : 1,
        textDecoration: off ? 'line-through' : undefined,
      }}>
      {lines === 1 ? (
        <div className="truncate">
          <span style={{ fontWeight: 500 }}>{a.title}</span>
          <span data-block-range>{', ' + range}</span>
        </div>
      ) : (
        <>
          <div className="truncate" style={{ fontWeight: 500 }}>{a.title}</div>
          <div className="truncate" data-block-range>{range}</div>
          {extra.slice(0, lines - 2).map((line) => (
            <div key={line} className="truncate" style={{ color: 'rgb(102,112,133)' }}>{line}</div>
          ))}
        </>
      )}
    </div>
  )
}

/** One block of blocked off time on the Day/Week grid: grey, hatched, dashed edge. */
function BlockedBlock({ b, box, label, onOpen }: {
  b: BlockedTime
  box: BlockBox
  label: string
  onOpen: (id: number, e: React.MouseEvent) => void
}) {
  return (
    <div
      data-blocked-time={b.id}
      title={`Blocked off: ${b.title} (${b.calendar_name ?? 'calendar'}) — ${label} — click to open`}
      onClick={(ev) => onOpen(b.id, ev)}
      onDoubleClick={(ev) => ev.stopPropagation()}
      style={{
        ...boxStyle(box),
        background: BLOCKED_FILL, border: '1px dashed rgb(152,162,179)',
        padding: `${2 + box.textTop}px 6px 2px`, color: 'rgb(71,84,103)',
      }}
    >
      <div className="truncate" style={{ fontWeight: 500 }}>Blocked · {b.title}</div>
      <div className="truncate">{label}</div>
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
