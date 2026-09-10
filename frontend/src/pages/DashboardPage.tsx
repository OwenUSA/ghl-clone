import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import { Donut } from '../components/Donut'
import { IconCalendar, IconChevronDown, IconSettings } from '../components/Icon'
import {
  type DashboardFunnel,
  type DashboardStats,
  getDashboard,
  getDashboardFunnel,
  listPipelines,
  money,
} from '../lib/api'
import {
  DASHBOARD_RANGES,
  DEFAULT_RANGE,
  rangeCaption,
  rangeWindow,
} from '../lib/dashboardRange'

/**
 * Dashboard — rebuilt from references/ghl/dashboard/*.png + computed styles.
 *
 * Measured structure:
 *   header      "Dashboard ▾" | "+ New" | right: calendar icon + "Last 30 days ▾" | ⋮
 *   row 1       3 cards: Opportunity status | Opportunity value | Conversion rate
 *               card titles 16px/600; each has an "All pipelines" select + a
 *               settings icon on the right
 *   status card DONUT with the total in the centre (24px/500) and a legend to the
 *               right: "Won - 196" / "Open - 41" / "Lost - 35", 12px/400, colour chips
 *   value card  HORIZONTAL bars, y-axis Lost/Open/Won, x-axis $0..$250K,
 *               footer "Total revenue  $258.77K"
 *   conversion  DONUT ring, "72.06%" centred, footer "Won revenue $218.74K"
 *   row 2       "Funnel" (stage bars + Cumulative / Next step conversion columns)
 *               and "Stage distribution"
 *
 * A first version used flat progress bars; GHL uses donuts, which reads as a
 * different product even with the same numbers.
 *
 * WHAT IS MEASURED AND WHAT IS OURS (2026-09-10, and see DECISIONS.md). The
 * layout and typography above are measured and unchanged. Everything the
 * captures never covered is our own design: the contents of the date-range menu,
 * the per-card settings popovers, the "Dashboard" selector menu and the ⋮ menu.
 * None of it may be cited as parity.
 */

/** Range control aside, every card's figures now come from the server. The
 *  browser used to compute two of them, and both were wrong — see api.ts. */
const C = {
  won: 'rgb(83,155,245)',
  open: 'rgb(93,205,235)',
  lost: 'rgb(140,141,222)',
  abandoned: 'rgb(152,162,179)',
  other: 'rgb(208,213,221)',
  empty: 'rgb(234,236,240)',
} as const

const STAGE_COLORS = [C.won, C.open, C.lost, C.abandoned, 'rgb(249,219,175)']

/** The measured empty-field dash, reused for a figure that has no denominator. */
const NONE = '--'

const pct = (v: number | null | undefined) =>
  v === null || v === undefined ? NONE : v.toFixed(2) + '%'

// ---------------------------------------------------------------- card settings

type CardKey = 'status' | 'value' | 'conversion' | 'funnel' | 'distribution'

/**
 * What a card's gear icon opens.
 *
 * The gears were `disabled` placeholders. Each one now carries only settings that
 * genuinely do something to that card — the precedent in this codebase is that a
 * control either works or is disabled with the reason on hover, and a popover of
 * plausible-looking switches that change nothing would be the same lie in a
 * larger font.
 */
type CardSettings = {
  /** Follow the header's range control, or pin this card to all time. */
  range: 'follow' | 'all'
  /** Conversion rate only: which denominator the percentage is out of. */
  basis: 'decided' | 'all'
  /** Funnel only: drop the stages holding nothing. */
  hideEmpty: boolean
  /** Stage distribution only: pipeline order, or biggest slice first. */
  sort: 'stage' | 'largest'
}

const CARD_DEFAULTS: CardSettings = {
  range: 'follow',
  basis: 'decided',
  hideEmpty: false,
  sort: 'stage',
}

const CARD_KEYS: CardKey[] = ['status', 'value', 'conversion', 'funnel', 'distribution']
const STORE = 'ghl.dashboard.cards'

type CardMap = Record<CardKey, CardSettings>

function blankCards(): CardMap {
  return Object.fromEntries(
    CARD_KEYS.map((k) => [k, { ...CARD_DEFAULTS }]),
  ) as CardMap
}

/** A corrupt, absent or blocked store must not take the dashboard down with it,
 *  so every read falls back to the defaults rather than throwing. */
function loadCards(): CardMap {
  const out = blankCards()
  try {
    const saved = JSON.parse(localStorage.getItem(STORE) ?? '{}') as Partial<
      Record<CardKey, Partial<CardSettings>>
    >
    for (const k of CARD_KEYS) Object.assign(out[k], saved[k] ?? {})
  } catch {
    // Fall through with the defaults.
  }
  return out
}

function saveCards(cards: CardMap) {
  try {
    localStorage.setItem(STORE, JSON.stringify(cards))
  } catch {
    // Private browsing, or site data blocked. The settings still apply for
    // this session; they just do not survive a reload.
  }
}

// ---------------------------------------------------------------- primitives

/**
 * A dismissable popover, used by every menu on this screen.
 *
 * Escape and an outside click both close it. That is not decoration: the
 * Opportunities `⋯` menu shipped without a backdrop and a click meant to dismiss
 * it fell through onto a card and opened the detail dialog instead
 * (test_the_opportunities_overflow_menu_can_be_dismissed).
 */
function Popover({
  open, onClose, align = 'right', width = 240, children,
}: {
  open: boolean
  onClose: () => void
  align?: 'left' | 'right'
  width?: number
  children: React.ReactNode
}) {
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null
  return (
    <>
      <div className="fixed inset-0 z-10" onClick={onClose} />
      <div role="menu" className="absolute z-20 bg-white"
        style={{
          top: 38, [align]: 0, width, borderRadius: 8, padding: 8,
          border: '1px solid rgb(234,236,240)',
          boxShadow: '0 12px 16px -4px rgba(16,24,40,0.08), 0 4px 6px -2px rgba(16,24,40,0.03)',
        }}>
        {children}
      </div>
    </>
  )
}

const ITEM: React.CSSProperties = {
  display: 'flex', alignItems: 'center', gap: 8, width: '100%',
  padding: '7px 8px', borderRadius: 6, fontSize: 13, textAlign: 'left',
  color: 'rgb(52,64,84)',
}

function MenuLabel({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      padding: '4px 8px 6px', fontSize: 11, fontWeight: 600,
      letterSpacing: '0.04em', textTransform: 'uppercase', color: 'rgb(102,112,133)',
    }}>{children}</div>
  )
}

/** One radio-ish row in a settings popover. */
function Choice({ checked, onSelect, children }: {
  checked: boolean; onSelect: () => void; children: React.ReactNode
}) {
  return (
    <button role="menuitemradio" aria-checked={checked} onClick={onSelect}
      className="hover:bg-[rgb(249,250,251)]"
      style={{ ...ITEM, fontWeight: checked ? 600 : 400 }}>
      <span style={{ width: 12, color: 'rgb(21,112,239)' }}>{checked ? '✓' : ''}</span>
      {children}
    </button>
  )
}

// ---------------------------------------------------------------- the card shell

/** A card whose query failed must say so, not draw zeros.
 *
 *  `/api/dashboard` is STAFF-only, so a TECH gets a 403 for it while the page
 *  renders perfectly happily -- a donut centred on 0, `Total revenue $0.00`,
 *  `Conversion rate 0.00%`. That is indistinguishable from an empty database.
 */
function Card({ title, right, error, caption, settings, open, onToggle, children }: {
  title: string; right?: React.ReactNode; error?: Error | null
  /** Which window produced these figures. The cards were all-time for as long as
   *  the range control did nothing, so a narrowed card has to say so on its face. */
  caption?: string
  /** The gear's popover contents. A card with none keeps the icon disabled. */
  settings?: React.ReactNode
  /** Whether this card's settings popover is the one showing. The page owns that,
   *  not the card: with a popover per card each gear sat above every other
   *  popover's backdrop, so opening a second settings panel left the first one
   *  open behind it. Verified in a browser. */
  open?: boolean
  onToggle?: (open: boolean) => void
  children: React.ReactNode
}) {
  const setOpen = (v: boolean) => onToggle?.(v)
  return (
    <div className="bg-white" style={{
      borderRadius: 8, border: '1px solid rgb(234,236,240)',
    }}>
      {/* The header must never overflow its card: the title, the caption, the
          pipeline select and the gear all sit on one 64px row, and the select is
          as wide as the longest pipeline name. Left to grow, the gear was drawn
          on top of the next card's border. The title truncates rather than
          wrapping -- it is measured at 16px/600 on ONE line.
          `overflow-hidden` belongs on the TITLE and nowhere else: on the row it
          also clipped the settings popover, which hangs out of this header by
          design and simply vanished. */}
      <div className="flex items-center gap-3 px-4"
        style={{ height: 64, borderBottom: '1px solid rgb(242,244,247)' }}>
        <div style={{
          fontSize: 16, fontWeight: 600, color: 'rgb(16,24,40)', whiteSpace: 'nowrap',
          minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis',
        }}>
          {title}
        </div>
        {caption && (
          <div style={{
            fontSize: 12, color: 'rgb(102,112,133)', whiteSpace: 'nowrap', flexShrink: 0,
          }}>{caption}</div>
        )}
        <div className="ml-auto flex min-w-0 items-center gap-2">
          {right}
          {/* measured: every card carries a settings icon to the right of its select */}
          <span className="relative">
            <button aria-haspopup="menu" aria-expanded={open}
              title={settings ? title + ' settings' : 'This card has no settings'}
              disabled={!settings}
              onClick={() => setOpen(!open)}
              className="relative z-20 flex items-center"
              style={{
                color: 'rgb(102,112,133)',
                cursor: settings ? 'pointer' : 'not-allowed',
                opacity: settings ? 1 : 0.5,
              }}>
              <IconSettings size={18} color="rgb(102,112,133)" />
            </button>
            <Popover open={!!open} onClose={() => setOpen(false)} width={252}>
              <MenuLabel>{title}</MenuLabel>
              {settings}
            </Popover>
          </span>
        </div>
      </div>
      <div className="p-4">
        {error
          ? <div role="alert" style={{ fontSize: 13, color: 'rgb(180,35,24)' }}>
              {error.message}
            </div>
          : children}
      </div>
    </div>
  )
}

function PipelineSelect({ value, onChange, pipelines, allPipelines = true }: {
  value: number | undefined
  onChange: (v: number | undefined) => void
  pipelines: { id: number; name: string }[]
  /** The Funnel and Stage distribution cards draw ONE pipeline stage by stage.
   *  Their select offered "All pipelines" and then rendered the first pipeline
   *  anyway — stages from different pipelines cannot be laid end to end. */
  allPipelines?: boolean
}) {
  return (
    <select
      value={value ?? ''}
      onChange={(e) => onChange(e.target.value ? Number(e.target.value) : undefined)}
      style={{
        height: 34, borderRadius: 6, border: '1px solid rgb(234,236,240)',
        padding: '0 8px', fontSize: 14, color: 'rgb(52,64,84)', backgroundColor: '#fff',
        minWidth: 0, maxWidth: 200,
      }}
    >
      {allPipelines && <option value="">All pipelines</option>}
      {pipelines.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
    </select>
  )
}

// ---------------------------------------------------------------- money axis

/** Round an axis maximum up to 1, 2 or 5 × a power of ten, so the ticks land on
 *  numbers a person would pick. An empty card gets a $0–$100 axis: at the true
 *  maximum of zero the ticks came out `$0 $0 $0 $1 $1 $1`. */
function niceMax(cents: number): number {
  if (cents <= 0) return 10_000
  const pow = 10 ** Math.floor(Math.log10(cents))
  for (const step of [1, 2, 5]) if (cents <= step * pow) return step * pow
  return 10 * pow
}

const trim = (v: number) => (Number.isInteger(v) ? String(v) : v.toFixed(1))

/** `$0` / `$750` / `$26.5K` / `$1.2M`. The old formatter rounded to whole
 *  thousands, so every tick on a pipeline worth under $1,000 read `$0K`. */
function axisLabel(cents: number): string {
  const d = cents / 100
  if (d >= 1_000_000) return '$' + trim(d / 1_000_000) + 'M'
  if (d >= 1000) return '$' + trim(d / 1000) + 'K'
  return '$' + Math.round(d)
}

/** The figures the ⋮ menu writes out, one row per line. */
function toCsv(rows: (string | number)[][]): string {
  return rows
    .map((r) => r.map((c) => '"' + String(c).replace(/"/g, '""') + '"').join(','))
    .join('\r\n')
}

function download(name: string, body: string) {
  const url = URL.createObjectURL(new Blob([body], { type: 'text/csv;charset=utf-8' }))
  const a = document.createElement('a')
  a.href = url
  a.download = name
  a.click()
  URL.revokeObjectURL(url)
}

function dashboardCsv(
  range: string, stats: DashboardStats | undefined, funnel: DashboardFunnel | undefined,
): string {
  const rows: (string | number)[][] = [['Dashboard', range], []]
  if (stats) {
    rows.push(['Opportunities', stats.total])
    for (const k of ['won', 'open', 'lost', 'abandoned']) {
      rows.push([k[0].toUpperCase() + k.slice(1), stats.status[k] ?? 0,
                 money(stats.value_by_status[k] ?? 0)])
    }
    rows.push(['Total revenue', money(stats.total_value_cents)])
    rows.push(['Won revenue', money(stats.won_value_cents)])
    rows.push(['Conversion rate (won/decided)', stats.conversion_rate.toFixed(2) + '%'])
    rows.push(['Conversion rate (won/all)', stats.conversion_rate_all.toFixed(2) + '%'])
  }
  if (funnel?.stages.length) {
    rows.push([], ['Funnel', funnel.pipeline_name ?? ''])
    rows.push(['Stage', 'In stage', 'Value', 'Reached', 'Cumulative', 'Next step'])
    for (const s of funnel.stages) {
      rows.push([s.name, s.count, money(s.value_cents), s.reached,
                 pct(s.cumulative_pct), pct(s.next_step_pct)])
    }
  }
  return toCsv(rows)
}

// ---------------------------------------------------------------- the page

export function DashboardPage() {
  const queryClient = useQueryClient()
  const [range, setRange] = useState<string>(DEFAULT_RANGE)
  const [cards, setCards] = useState<CardMap>(loadCards)
  /** The one menu that is showing, by id, or null. Held here rather than in each
   *  trigger so that opening one closes the others -- every trigger sits above
   *  every backdrop, so per-menu state left two panels open at once. */
  const [menu, setMenu] = useState<string | null>(null)
  const toggle = (id: string) => setMenu((cur) => (cur === id ? null : id))

  const [statusPipe, setStatusPipe] = useState<number | undefined>()
  const [valuePipe, setValuePipe] = useState<number | undefined>()
  // Conversion rate needs its own, or its select re-filters the Opportunity value
  // card and leaves its own number alone. Funnel and Stage distribution DO share
  // one deliberately -- they are two renderings of a single pipeline.
  const [convPipe, setConvPipe] = useState<number | undefined>()
  const [funnelPipe, setFunnelPipe] = useState<number | undefined>()

  const set = (key: CardKey, patch: Partial<CardSettings>) => {
    setCards((prev) => {
      const next = { ...prev, [key]: { ...prev[key], ...patch } }
      saveCards(next)
      return next
    })
    // Close on choosing, so the card's new figure is not hidden behind the panel
    // that changed it.
    setMenu(null)
  }

  // The window every card asks for, unless its own settings pin it to all time.
  //
  // MEMOISED ON `range`, and that is load-bearing rather than an optimisation.
  // `rangeWindow` defaults to `new Date()`, so an unmemoised call returns a
  // start a few milliseconds later on every render. That start is part of every
  // card's query key, so each render invalidated all five queries, whose results
  // re-rendered the page, which produced five more keys — a refetch loop that ran
  // for as long as the page was open and left every card showing the fallback
  // zero, because the key it was reading had already been replaced. Verified in a
  // real browser; no source-level test would have seen it.
  //
  // Fixing the identity also fixes the semantics: the window is pinned to the
  // moment the range was chosen, rather than sliding forward under the reader.
  const win = useMemo(() => rangeWindow(range), [range])
  const winFor = (key: CardKey) => (cards[key].range === 'all' ? {} : win)
  // Shown ONLY when this card's window differs from the header control's, which
  // is the one case the control on screen does not already answer. Captioning
  // every card cost the measured header its width and wrapped "Opportunity
  // status" onto two lines -- the layout of these three cards is measured against
  // GHL and is not ours to spend.
  const captionFor = (key: CardKey) =>
    cards[key].range === 'all' && win.start ? 'All time' : undefined

  const pipelines = useQuery({ queryKey: ['pipelines'], queryFn: listPipelines })
  const status = useQuery({
    queryKey: ['dashboard', statusPipe, winFor('status')],
    queryFn: () => getDashboard({ pipelineId: statusPipe, ...winFor('status') }),
  })
  const value = useQuery({
    queryKey: ['dashboard', valuePipe, winFor('value')],
    queryFn: () => getDashboard({ pipelineId: valuePipe, ...winFor('value') }),
  })
  // Same key shape as the other two, so two cards on the same pipeline and the
  // same window still share one cached request rather than issuing a second.
  const conv = useQuery({
    queryKey: ['dashboard', convPipe, winFor('conversion')],
    queryFn: () => getDashboard({ pipelineId: convPipe, ...winFor('conversion') }),
  })
  const funnel = useQuery({
    queryKey: ['dashboard-funnel', funnelPipe, winFor('funnel')],
    queryFn: () => getDashboardFunnel({ pipelineId: funnelPipe, ...winFor('funnel') }),
  })
  const dist = useQuery({
    queryKey: ['dashboard-funnel', funnelPipe, winFor('distribution')],
    queryFn: () => getDashboardFunnel({ pipelineId: funnelPipe, ...winFor('distribution') }),
  })

  // ---- Opportunity status
  const s = status.data?.status ?? {}
  const segs: { key: string; label: string; n: number; color: string }[] =
    (['won', 'open', 'lost'] as const)
      .map((k) => ({ key: k, label: k[0].toUpperCase() + k.slice(1), n: s[k] ?? 0, color: C[k] }))
  if ((s.abandoned ?? 0) > 0) {
    segs.push({ key: 'abandoned', label: 'Abandoned', n: s.abandoned, color: C.abandoned })
  }
  // The centre reads `total`, so anything the four measured buckets do not
  // account for has to be drawn too -- otherwise the ring and the number in the
  // middle of it disagree, which is how "Abandoned" used to vanish.
  const drawn = segs.reduce((a, x) => a + x.n, 0)
  const unaccounted = (status.data?.total ?? 0) - drawn
  if (unaccounted > 0) {
    segs.push({ key: 'other', label: 'Other', n: unaccounted, color: C.other })
  }

  // ---- Opportunity value
  const byValue = value.data?.value_by_status ?? {}
  const totalRev = value.data?.total_value_cents ?? 0
  const bars = [
    { label: 'Lost', v: byValue.lost ?? 0 },
    { label: 'Open', v: byValue.open ?? 0 },
    { label: 'Won', v: byValue.won ?? 0 },
  ]
  if ((byValue.abandoned ?? 0) > 0) bars.push({ label: 'Abandoned', v: byValue.abandoned })
  const ticks = 5
  const barMax = niceMax(Math.max(...bars.map((b) => b.v), 0))

  // ---- Conversion rate
  const usingAll = cards.conversion.basis === 'all'
  const rate = (usingAll ? conv.data?.conversion_rate_all : conv.data?.conversion_rate) ?? 0

  // ---- Funnel + Stage distribution
  const fstages = funnel.data?.stages ?? []
  const shown = cards.funnel.hideEmpty ? fstages.filter((x) => x.count > 0) : fstages
  const stageMax = Math.max(...fstages.map((x) => x.count), 1)
  // The select follows the pipeline the server actually drew, so it shows the
  // right name before the pipeline list has even arrived.
  const funnelPid = funnelPipe ?? funnel.data?.pipeline_id ?? undefined

  const slices = (dist.data?.stages ?? []).filter((x) => x.count > 0)
  const ordered = cards.distribution.sort === 'largest'
    ? [...slices].sort((a, b) => b.count - a.count)
    : slices

  /** The one setting every card has: obey the header control, or pin to all time. */
  const rangeChoice = (key: CardKey) => (
    <>
      <Choice checked={cards[key].range === 'follow'}
        onSelect={() => set(key, { range: 'follow' })}>
        Follow the date range ({rangeCaption(range)})
      </Choice>
      <Choice checked={cards[key].range === 'all'}
        onSelect={() => set(key, { range: 'all' })}>
        Always show all time
      </Choice>
    </>
  )

  return (
    <div className="flex h-screen min-w-0 flex-1 flex-col overflow-hidden"
      style={{ backgroundColor: 'rgb(249,250,251)' }}>
      <div className="flex shrink-0 items-center gap-3 bg-white px-4"
        style={{ height: 64, borderBottom: '1px solid rgb(234,236,240)' }}>
        {/* The dashboard selector. There is exactly one dashboard, so the menu
            lists one -- which is what the control claims to do. Building custom
            dashboards is a product of its own; "+ New" says so rather than
            pretending. */}
        <span className="relative">
          <button aria-haspopup="menu" aria-expanded={menu === 'dashboards'}
            onClick={() => toggle('dashboards')}
            className="relative z-20 flex items-center gap-1"
            style={{
              height: 36, padding: '0 12px', borderRadius: 6,
              border: '1px solid rgb(234,236,240)',
              fontSize: 14, fontWeight: 600, color: 'rgb(16,24,40)',
            }}>
            Dashboard <IconChevronDown size={16} color="rgb(102,112,133)" />
          </button>
          <Popover open={menu === 'dashboards'} onClose={() => setMenu(null)}
            align="left" width={268}>
            <MenuLabel>Dashboards</MenuLabel>
            <Choice checked onSelect={() => setMenu(null)}>Dashboard</Choice>
            <button disabled role="menuitem"
              title={'Custom dashboards are not built. This app has one dashboard, '
                + 'defined in code — there is no dashboard builder to add a second.'}
              style={{ ...ITEM, color: 'rgb(152,162,179)', cursor: 'not-allowed' }}>
              <span style={{ width: 12 }} />+ New dashboard
            </button>
          </Popover>
        </span>
        <button disabled
          title={'Custom dashboards are not built. This app has one dashboard, '
            + 'defined in code — there is no dashboard builder to add a second.'}
          className="flex items-center gap-1"
          style={{ fontSize: 14, fontWeight: 500, color: 'rgb(21,112,239)', opacity: 0.5, cursor: 'not-allowed' }}>
          + New
        </button>
        <div className="ml-auto flex items-center gap-2">
          <span className="flex items-center gap-2"
            style={{
              height: 36, padding: '0 10px', borderRadius: 6,
              border: '1px solid rgb(234,236,240)', backgroundColor: '#fff',
            }}>
            <IconCalendar size={16} color="rgb(71,84,103)" />
            <select value={range} onChange={(e) => setRange(e.target.value)}
              title="Filters every card by the date the opportunity was created"
              style={{ fontSize: 14, fontWeight: 600, color: 'rgb(16,24,40)', border: 'none', outline: 'none' }}>
              {DASHBOARD_RANGES.map((r) => <option key={r}>{r}</option>)}
            </select>
          </span>
          <span className="relative">
            <button aria-haspopup="menu" aria-expanded={menu === 'overflow'} title="Dashboard actions"
              onClick={() => toggle('overflow')}
              className="relative z-20"
              style={{ color: 'rgb(102,112,133)', fontSize: 18, padding: '0 4px' }}>
              ⋮
            </button>
            <Popover open={menu === 'overflow'} onClose={() => setMenu(null)} width={216}>
              <MenuLabel>Dashboard</MenuLabel>
              <button role="menuitem" style={ITEM} className="hover:bg-[rgb(249,250,251)]"
                onClick={() => {
                  queryClient.invalidateQueries({ queryKey: ['dashboard'] })
                  queryClient.invalidateQueries({ queryKey: ['dashboard-funnel'] })
                  queryClient.invalidateQueries({ queryKey: ['pipelines'] })
                  setMenu(null)
                }}>
                <span style={{ width: 12 }} />Refresh figures
              </button>
              <button role="menuitem" style={ITEM} className="hover:bg-[rgb(249,250,251)]"
                onClick={() => {
                  download('dashboard.csv', dashboardCsv(
                    rangeCaption(range), status.data, funnel.data))
                  setMenu(null)
                }}>
                <span style={{ width: 12 }} />Download as CSV
              </button>
            </Popover>
          </span>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        <div className="grid gap-4" style={{
          gridTemplateColumns: 'repeat(auto-fit, minmax(380px, 1fr))',
        }}>
          <Card title="Opportunity status" error={status.error}
            open={menu === 'status'} onToggle={() => toggle('status')}
            caption={captionFor('status')} settings={rangeChoice('status')}
            right={<PipelineSelect value={statusPipe} onChange={setStatusPipe}
              pipelines={pipelines.data ?? []} />}>
            <div className="flex items-center gap-6">
              <Donut segments={segs} center={String(status.data?.total ?? 0)} />
              <div>
                {segs.map((x) => (
                  <div key={x.key} className="mb-2 flex items-center gap-2"
                    style={{ fontSize: 12, color: 'rgb(52,64,84)' }}>
                    <span style={{
                      width: 16, height: 8, borderRadius: 2,
                      backgroundColor: x.color, display: 'inline-block',
                    }} />
                    {x.label} - {x.n}
                  </div>
                ))}
              </div>
            </div>
          </Card>

          <Card title="Opportunity value" error={value.error}
            open={menu === 'value'} onToggle={() => toggle('value')}
            caption={captionFor('value')} settings={rangeChoice('value')}
            right={<PipelineSelect value={valuePipe} onChange={setValuePipe}
              pipelines={pipelines.data ?? []} />}>
            <div className="flex">
              <div className="shrink-0" style={{ width: 72 }}>
                {bars.map((b) => (
                  <div key={b.label} style={{
                    height: 34, fontSize: 12, color: 'rgb(102,112,133)',
                    display: 'flex', alignItems: 'center', justifyContent: 'flex-end',
                    paddingRight: 8,
                  }}>{b.label}</div>
                ))}
              </div>
              <div className="relative flex-1">
                {/* grid lines behind the bars, as measured */}
                <div className="absolute inset-0 flex justify-between">
                  {Array.from({ length: ticks + 1 }, (_, i) => (
                    <span key={i} style={{ width: 1, backgroundColor: 'rgb(242,244,247)' }} />
                  ))}
                </div>
                {bars.map((b) => (
                  <div key={b.label} title={b.label + ' ' + money(b.v)}
                    style={{ height: 34, display: 'flex', alignItems: 'center' }}>
                    <div style={{
                      width: `${(b.v / barMax) * 100}%`, height: 8,
                      backgroundColor: C.won, borderRadius: 2, minWidth: b.v > 0 ? 4 : 0,
                    }} />
                  </div>
                ))}
                <div className="flex justify-between" style={{ marginTop: 6 }}>
                  {Array.from({ length: ticks + 1 }, (_, i) => (
                    <span key={i} style={{ fontSize: 12, color: 'rgb(102,112,133)' }}>
                      {axisLabel((barMax / ticks) * i)}
                    </span>
                  ))}
                </div>
              </div>
            </div>
            <div className="mt-3 text-center">
              <div style={{ fontSize: 12, color: 'rgb(102,112,133)' }}>Total revenue</div>
              <div style={{ fontSize: 20, fontWeight: 500, color: 'rgb(16,24,40)' }}>
                {money(totalRev)}
              </div>
            </div>
          </Card>

          <Card title="Conversion rate" error={conv.error}
            open={menu === 'conversion'} onToggle={() => toggle('conversion')}
            caption={captionFor('conversion')}
            settings={
              <>
                {rangeChoice('conversion')}
                <div style={{ height: 1, backgroundColor: 'rgb(242,244,247)', margin: '6px 0' }} />
                <MenuLabel>Out of</MenuLabel>
                <Choice checked={!usingAll}
                  onSelect={() => set('conversion', { basis: 'decided' })}>
                  Decided deals (won + lost)
                </Choice>
                <Choice checked={usingAll}
                  onSelect={() => set('conversion', { basis: 'all' })}>
                  Every opportunity
                </Choice>
              </>
            }
            right={<PipelineSelect value={convPipe} onChange={setConvPipe}
              pipelines={pipelines.data ?? []} />}>
            <div className="flex flex-col items-center">
              <Donut
                segments={[
                  { key: 'won', n: rate, color: C.won },
                  { key: 'rest', n: 100 - rate, color: C.empty },
                ]}
                center={`${rate.toFixed(2)}%`}
              />
              <div style={{ fontSize: 12, color: 'rgb(102,112,133)', marginTop: 8 }}>
                {usingAll
                  ? 'Won out of every opportunity'
                  : 'Won out of decided deals (won + lost)'}
              </div>
              <div className="mt-3 text-center">
                <div style={{ fontSize: 12, color: 'rgb(102,112,133)' }}>Won revenue</div>
                <div style={{ fontSize: 20, fontWeight: 500, color: 'rgb(16,24,40)' }}>
                  {money(conv.data?.won_value_cents ?? 0)}
                </div>
              </div>
            </div>
          </Card>
        </div>

        {/* row 2 — Funnel + Stage distribution */}
        <div className="mt-4 grid gap-4" style={{
          gridTemplateColumns: 'repeat(auto-fit, minmax(420px, 1fr))',
        }}>
          <Card title="Funnel" error={funnel.error}
            open={menu === 'funnel'} onToggle={() => toggle('funnel')} caption={captionFor('funnel')}
            settings={
              <>
                {rangeChoice('funnel')}
                <div style={{ height: 1, backgroundColor: 'rgb(242,244,247)', margin: '6px 0' }} />
                <MenuLabel>Stages</MenuLabel>
                <Choice checked={!cards.funnel.hideEmpty}
                  onSelect={() => set('funnel', { hideEmpty: false })}>
                  Show every stage
                </Choice>
                <Choice checked={cards.funnel.hideEmpty}
                  onSelect={() => set('funnel', { hideEmpty: true })}>
                  Hide stages holding nothing
                </Choice>
              </>
            }
            right={<PipelineSelect value={funnelPid} onChange={setFunnelPipe}
              allPipelines={false} pipelines={pipelines.data ?? []} />}>
            <div className="flex" style={{ fontSize: 12, color: 'rgb(102,112,133)' }}>
              <div className="flex-1" />
              <div style={{ width: 90, textAlign: 'center' }}>Cumulative</div>
              <div style={{ width: 90, textAlign: 'center' }}>Next step<br />conversion</div>
            </div>
            {shown.map((st) => (
              <div key={st.id} className="mt-1 flex items-center">
                <div className="flex-1" style={{
                  backgroundColor: 'rgb(242,244,247)', borderRadius: 4, padding: '8px 10px',
                }}>
                  <div style={{ fontSize: 13, color: 'rgb(52,64,84)' }}>{st.name}</div>
                  <div style={{ fontSize: 12, color: 'rgb(102,112,133)' }}>
                    {money(st.value_cents)}
                  </div>
                  <div style={{
                    marginTop: 4, height: 6, borderRadius: 3,
                    width: `${(st.count / stageMax) * 100}%`,
                    backgroundColor: C.won, minWidth: st.count ? 4 : 0,
                  }} />
                </div>
                <div title={`${st.reached} of ${funnel.data?.total ?? 0} reached this stage or a later one`}
                  style={{ width: 90, textAlign: 'center', fontSize: 12, color: 'rgb(52,64,84)' }}>
                  {pct(st.cumulative_pct)}
                </div>
                <div title="Of the deals that reached this stage, the share that went on to the next one"
                  style={{ width: 90, textAlign: 'center', fontSize: 12, color: 'rgb(52,64,84)' }}>
                  {pct(st.next_step_pct)}
                </div>
              </div>
            ))}
            {!funnel.error && shown.length === 0 && (
              <div style={{ fontSize: 14, color: 'rgb(102,112,133)', paddingTop: 8 }}>
                No stages to show for this pipeline.
              </div>
            )}
          </Card>

          <Card title="Stage distribution" error={dist.error}
            open={menu === 'distribution'} onToggle={() => toggle('distribution')}
            caption={captionFor('distribution')}
            settings={
              <>
                {rangeChoice('distribution')}
                <div style={{ height: 1, backgroundColor: 'rgb(242,244,247)', margin: '6px 0' }} />
                <MenuLabel>Order</MenuLabel>
                <Choice checked={cards.distribution.sort === 'stage'}
                  onSelect={() => set('distribution', { sort: 'stage' })}>
                  Pipeline order
                </Choice>
                <Choice checked={cards.distribution.sort === 'largest'}
                  onSelect={() => set('distribution', { sort: 'largest' })}>
                  Largest stage first
                </Choice>
              </>
            }
            right={<PipelineSelect value={funnelPid} onChange={setFunnelPipe}
              allPipelines={false} pipelines={pipelines.data ?? []} />}>
            {ordered.length === 0 ? (
              <div style={{ fontSize: 14, color: 'rgb(102,112,133)' }}>
                No opportunities in this pipeline.
              </div>
            ) : (
              <div className="flex items-center gap-6">
                <Donut
                  size={150}
                  segments={ordered.map((st, i) => ({
                    key: String(st.id),
                    n: st.count,
                    color: STAGE_COLORS[i % STAGE_COLORS.length],
                  }))}
                  center={String(dist.data?.total ?? 0)}
                />
                <div>
                  {ordered.map((st, i) => (
                    <div key={st.id} className="mb-2 flex items-center gap-2"
                      style={{ fontSize: 12, color: 'rgb(52,64,84)' }}>
                      <span style={{
                        width: 16, height: 8, borderRadius: 2, display: 'inline-block',
                        backgroundColor: STAGE_COLORS[i % STAGE_COLORS.length],
                      }} />
                      {st.name} - {st.count}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </Card>
        </div>
      </div>
    </div>
  )
}
