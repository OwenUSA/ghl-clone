"""One-off: wire Dashboard/Launchpad into App and add Calendars filter groups."""
import pathlib

FE = pathlib.Path("frontend/src")

# --- App.tsx ---
p = FE / "App.tsx"
s = p.read_text(encoding="utf-8")
if "DashboardPage" not in s:
    s = s.replace(
        "import { CalendarsPage } from './pages/CalendarsPage'",
        "import { CalendarsPage } from './pages/CalendarsPage'\n"
        "import { DashboardPage } from './pages/DashboardPage'\n"
        "import { LaunchpadPage } from './pages/LaunchpadPage'")
    s = s.replace(
        "      ) : active === 'calendars' ? (\n        <CalendarsPage />",
        "      ) : active === 'calendars' ? (\n        <CalendarsPage />\n"
        "      ) : active === 'dashboard' ? (\n        <DashboardPage />\n"
        "      ) : active === 'launchpad' ? (\n        <LaunchpadPage onNavigate={setActive} />")
    p.write_text(s, encoding="utf-8")
    print("App.tsx wired")

# --- api.ts: appointment filters ---
p = FE / "lib/api.ts"
s = p.read_text(encoding="utf-8")
if "calendar_ids" not in s:
    s = s.replace(
        "  kind: string\n  user_ids: number[]\n}) {\n"
        "  const sp = new URLSearchParams({ start: p.start, end: p.end, kind: p.kind })\n"
        "  if (p.user_ids.length) sp.set('user_ids', p.user_ids.join(','))",
        "  kind: string\n  user_ids: number[]\n  calendar_ids?: number[]\n"
        "  pipeline_ids?: number[]\n}) {\n"
        "  const sp = new URLSearchParams({ start: p.start, end: p.end, kind: p.kind })\n"
        "  if (p.user_ids.length) sp.set('user_ids', p.user_ids.join(','))\n"
        "  if (p.calendar_ids?.length) sp.set('calendar_ids', p.calendar_ids.join(','))\n"
        "  if (p.pipeline_ids?.length) sp.set('pipeline_ids', p.pipeline_ids.join(','))")
    p.write_text(s, encoding="utf-8")
    print("api.ts wired")

# --- CalendarsPage: filter groups ---
p = FE / "pages/CalendarsPage.tsx"
s = p.read_text(encoding="utf-8")
if "FilterGroup" not in s:
    s = s.replace(
        "import { listAppointments, listUsers, type Appointment } from '../lib/api'",
        "import {\n  listAppointments, listCalendars, listPipelines, listUsers,\n"
        "  type Appointment,\n} from '../lib/api'")
    s = s.replace(
        "  const [selectedUsers, setSelectedUsers] = useState<number[]>([])",
        "  const [selectedUsers, setSelectedUsers] = useState<number[]>([])\n"
        "  const [selectedCals, setSelectedCals] = useState<number[]>([])\n"
        "  const [selectedPipes, setSelectedPipes] = useState<number[]>([])\n"
        "  const [filterQ, setFilterQ] = useState('')")
    s = s.replace(
        "  const users = useQuery({ queryKey: ['users'], queryFn: listUsers })",
        "  const users = useQuery({ queryKey: ['users'], queryFn: listUsers })\n"
        "  const calendars = useQuery({ queryKey: ['calendars'], queryFn: listCalendars })\n"
        "  const pipelines = useQuery({ queryKey: ['pipelines'], queryFn: listPipelines })")
    s = s.replace(
        "    queryKey: ['appointments', start.toISOString(), days, kind, selectedUsers],",
        "    queryKey: ['appointments', start.toISOString(), days, kind,\n"
        "               selectedUsers, selectedCals, selectedPipes],")
    s = s.replace(
        "        kind,\n        user_ids: selectedUsers,\n      }),",
        "        kind,\n        user_ids: selectedUsers,\n"
        "        calendar_ids: selectedCals,\n        pipeline_ids: selectedPipes,\n      }),")

    old_start = s.index('            <div className="mt-4 flex items-center justify-between">')
    old_end = s.index("          </div>\n        )}\n      </div>\n    </div>\n  )\n}")
    new_block = '''            <div className="mt-4 flex items-center justify-between">
              <div style={{ fontSize: 14, fontWeight: 600, color: 'rgb(16,24,40)' }}>Filters</div>
              <button
                onClick={() => { setSelectedUsers([]); setSelectedCals([]); setSelectedPipes([]) }}
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

            <FilterGroup
              title="Pipelines"
              note="Not in GHL - added for this build"
              count={selectedPipes.length}
              items={(pipelines.data ?? [])
                .filter((pp) => pp.name.toLowerCase().includes(filterQ.toLowerCase()))
                .map((pp) => ({ id: pp.id, label: pp.name }))}
              selected={selectedPipes}
              onToggle={(id, on) =>
                setSelectedPipes((v) => (on ? [...v, id] : v.filter((x) => x !== id)))}
            />
'''
    s = s[:old_start] + new_block + s[old_end:]

    s += '''
function FilterGroup({
  title, items, selected, onToggle, count, note,
}: {
  title: string
  items: { id: number; label: string; color?: string }[]
  selected: number[]
  onToggle: (id: number, on: boolean) => void
  count: number
  note?: string
}) {
  const [open, setOpen] = useState(true)
  return (
    <div style={{ marginTop: 14 }}>
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-1 text-left"
        style={{ fontSize: 13, fontWeight: 600, color: 'rgb(52,64,84)' }}
      >
        <span>{open ? '-' : '+'}</span>
        {title}
        {count > 0 && (
          <span style={{
            fontSize: 11, color: '#fff', backgroundColor: 'rgb(21,112,239)',
            borderRadius: 4, padding: '0 5px', marginLeft: 4,
          }}>{count}</span>
        )}
        {note && (
          <span title={note}
            style={{ marginLeft: 'auto', fontSize: 11, color: 'rgb(152,162,179)' }}>
            not in GHL
          </span>
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
'''
    p.write_text(s, encoding="utf-8")
    print("CalendarsPage wired")
