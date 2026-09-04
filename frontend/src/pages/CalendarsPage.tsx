import { useQuery } from '@tanstack/react-query'
import { IconChevronDown, IconPlus, IconSettings } from '../components/Icon'
import { useMemo, useState } from 'react'
import {
  listAppointments, listCalendars, listUsers,
  type Appointment,
} from '../lib/api'

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
 * The "New" button is present but inert: creating a booking is out of v1 scope
 * and would be the first mutating control on this screen.
 */
const VIEWS = ['Day view', 'Week view', 'Month view'] as const
type View = (typeof VIEWS)[number]

const HOURS = Array.from({ length: 24 }, (_, i) => i)
const DAY_MS = 86_400_000

function startOfWeek(d: Date) {
  const x = new Date(d)
  x.setHours(0, 0, 0, 0)
  x.setDate(x.getDate() - x.getDay()) // Sunday, as measured
  return x
}

const fmtHour = (h: number) =>
  h === 0 ? '12 AM' : h < 12 ? `${h} AM` : h === 12 ? '12 PM' : `${h - 12} PM`

const fmtRange = (start: Date, days: number) => {
  const end = new Date(start.getTime() + (days - 1) * DAY_MS)
  const m = (d: Date) => d.toLocaleDateString('en-US', { month: 'short' })
  if (days === 1) {
    return start.toLocaleDateString('en-US', {
      month: 'short', day: 'numeric', year: 'numeric',
    })
  }
  return `${m(start)} ${start.getDate()} – ${m(end)} ${end.getDate()}, ${end.getFullYear()}`
}

export function CalendarsPage() {
  const [view, setView] = useState<View>('Week view')
  const [anchor, setAnchor] = useState(() => new Date())
  const [kind, setKind] = useState<'all' | 'appointments' | 'blocked'>('all')
  const [showPanel, setShowPanel] = useState(true)
  const [tab, setTab] = useState<'calendar' | 'list'>('calendar')
  const [selectedUsers, setSelectedUsers] = useState<number[]>([])
  const [selectedCals, setSelectedCals] = useState<number[]>([])
  const [filterQ, setFilterQ] = useState('')

  const days = view === 'Day view' ? 1 : view === 'Week view' ? 7 : 35
  const start = useMemo(
    () => (view === 'Day view'
      ? new Date(new Date(anchor).setHours(0, 0, 0, 0))
      : startOfWeek(anchor)),
    [anchor, view],
  )
  const end = new Date(start.getTime() + days * DAY_MS)

  const users = useQuery({ queryKey: ['users'], queryFn: listUsers })
  const calendars = useQuery({ queryKey: ['calendars'], queryFn: listCalendars })
  const appts = useQuery({
    queryKey: ['appointments', start.toISOString(), days, kind,
               selectedUsers, selectedCals],
    queryFn: () =>
      listAppointments({
        start: start.toISOString(),
        end: end.toISOString(),
        kind,
        user_ids: selectedUsers,
        calendar_ids: selectedCals,
      }),
  })

  const dayList = Array.from({ length: days }, (_, i) => new Date(start.getTime() + i * DAY_MS))
  const now = new Date()
  const nowOffset = now.getHours() * 48 + (now.getMinutes() / 60) * 48

  function shift(dir: number) {
    setAnchor((a) => new Date(a.getTime() + dir * days * DAY_MS))
  }

  const byDay = (d: Date) =>
    (appts.data ?? []).filter((a) => {
      const s = new Date(a.starts_at)
      return s.toDateString() === d.toDateString()
    })

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
            {fmtRange(start, days)}
          </div>
          <button onClick={() => shift(1)} aria-label="Next" style={{ padding: '0 10px', height: 36 }}>›</button>
        </div>
        <select
          value={view}
          onChange={(e) => setView(e.target.value as View)}
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
          <button
            disabled
            title="Creating a booking is out of v1 scope"
            style={{
              height: 36, padding: '0 12px', borderRadius: 6, fontSize: 14,
              fontWeight: 500, color: '#fff', backgroundColor: 'rgb(0,78,235)',
              opacity: 0.5, cursor: 'not-allowed',
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
          ) : (
            <>
              {/* day header */}
              <div className="flex shrink-0" style={{ borderBottom: '1px solid rgb(234,236,240)' }}>
                <div style={{ width: 60, fontSize: 11, color: 'rgb(152,162,179)', padding: '8px 4px' }}>
                  GMT{-new Date().getTimezoneOffset() / 60}
                </div>
                {dayList.slice(0, view === 'Month view' ? 7 : days).map((d) => {
                  const isToday = d.toDateString() === now.toDateString()
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
                      <div key={h} style={{ height: 48, fontSize: 11, color: 'rgb(152,162,179)', padding: '2px 6px' }}>
                        {fmtHour(h)}
                      </div>
                    ))}
                  </div>
                  {dayList.slice(0, view === 'Month view' ? 7 : days).map((d) => (
                    <div key={d.toISOString()} className="relative flex-1"
                      style={{ borderLeft: '1px solid rgb(242,244,247)' }}>
                      {HOURS.map((h) => (
                        <div key={h} style={{ height: 48, borderBottom: '1px solid rgb(242,244,247)' }} />
                      ))}
                      {d.toDateString() === now.toDateString() && (
                        <div style={{
                          position: 'absolute', left: 0, right: 0, top: nowOffset,
                          borderTop: '2px solid rgb(217,45,32)',
                        }} />
                      )}
                      {byDay(d).map((a) => {
                        const s = new Date(a.starts_at)
                        const e = new Date(a.ends_at)
                        const top = s.getHours() * 48 + (s.getMinutes() / 60) * 48
                        const h = Math.max(24, ((e.getTime() - s.getTime()) / 3_600_000) * 48)
                        return (
                          <div key={a.id} title={a.title}
                            style={{
                              position: 'absolute', left: 2, right: 2, top, height: h,
                              backgroundColor: 'rgb(239,244,255)',
                              borderLeft: '3px solid rgb(0,78,235)',
                              borderRadius: 4, padding: '2px 6px', overflow: 'hidden',
                              fontSize: 12, color: 'rgb(52,64,84)',
                            }}>
                            <div className="truncate" style={{ fontWeight: 500 }}>{a.title}</div>
                            <div className="truncate">
                              {s.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })}
                            </div>
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
