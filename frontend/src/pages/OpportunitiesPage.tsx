import {
  DndContext,
  PointerSensor,
  useSensor,
  useSensors,
  useDroppable,
  type DragEndEvent,
} from '@dnd-kit/core'
import { IconDownload, IconGrid, IconList, IconPlus } from '../components/Icon'
import { useDraggable } from '@dnd-kit/core'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { BulkActionsBar } from '../components/BulkActionsBar'
import { ForecastPanel } from '../components/ForecastPanel'
import { ManageSavedViews, SavedViewsRow } from '../components/SavedViews'
import { OpportunityDetail } from '../components/OpportunityDetail'
import { useEffect, useState } from 'react'
import { IconChevronLeft, IconFilter } from '../components/Icon'
import {
  centsFromDollars,
  createOpportunity,
  listContacts,
  listOpportunities,
  listPipelines,
  moveOpportunity,
  money,
  type Opportunity,
  type Pipeline,
} from '../lib/api'
import { csvFilename, opportunitiesCsv } from '../lib/csv'
import { downloadCsv } from '../lib/download'
import type { Me } from '../lib/auth'

/**
 * Measured from captures/opportunities/ and menus.json:
 *   column pitch  240px  (stage-collapse buttons at 450/690/930/1170/...)
 *   card          230 x 114.6, radius 4px, white,
 *                 shadow rgba(16,24,40,0.1) 0 1px 3px
 *   tabs          Opportunities | Forecast | Pipelines | Bulk Actions
 *   toolbar       Advanced filters (1) | Sort (1) | Search opportunities | Manage fields
 *   ⋯ menu        Export | Restore opportunities | Manage smart lists | Dashboard insights
 *   Manage fields Card layout: Default | Compact | Unlabeled;
 *                 Fields (7 of 8): Opportunity owner, Business name, Source, Value,
 *                 Lost reason, + Add fields
 *   default filter Status is any of Open
 *
 * 10 stages, measured by scrolling the board horizontally — the first capture saw
 * only 5. Board scrolls sideways; the document does not scroll.
 */
const TABS = ['Opportunities', 'Forecast', 'Pipelines', 'Bulk Actions']
const OVERFLOW = ['Export', 'Restore opportunities', 'Manage smart lists', 'Dashboard insights']

/**
 * Which tabs actually go somewhere.
 *
 * A tab that switches to a blank panel is worse than one that visibly refuses, so
 * the ones with nothing behind them stay dimmed and say so — the same
 * disabled-rather-than-omitted rule used for Import above and for the Reporting
 * tabs v1 does not implement. This set grows as each is built.
 */
const LIVE_TABS = new Set(['Opportunities', 'Forecast', 'Bulk Actions'])
const LAYOUTS = ['Default', 'Compact', 'Unlabeled'] as const
type Layout = (typeof LAYOUTS)[number]

function Card({ o, layout, onOpen, selectable = false, selected = false, onToggle }: {
  o: Opportunity
  layout: Layout
  onOpen: (id: number) => void
  /** Bulk Actions tab: the card picks rather than opens. */
  selectable?: boolean
  selected?: boolean
  onToggle?: (id: number) => void
}) {
  const { attributes, listeners, setNodeRef, transform, isDragging } = useDraggable({
    id: o.id,
  })
  return (
    <div
      ref={setNodeRef}
      {...listeners}
      {...attributes}
      onClick={() => (selectable && onToggle ? onToggle(o.id) : onOpen(o.id))}
      style={{
        width: 230,
        borderRadius: 4,
        backgroundColor: '#fff',
        boxShadow: 'rgba(16, 24, 40, 0.1) 0px 1px 3px 0px',
        padding: 12,
        marginBottom: 8,
        cursor: 'grab',
        opacity: isDragging ? 0.4 : 1,
        outline: selected ? '2px solid rgb(0,78,235)' : undefined,
        transform: transform
          ? `translate3d(${transform.x}px, ${transform.y}px, 0)`
          : undefined,
      }}
    >
      <div className="flex items-start gap-2">
        {selectable && (
          <input
            type="checkbox"
            checked={selected}
            aria-label={'Select ' + o.title}
            // The card's own onClick already toggles; without this the change and
            // the click both fire and the selection lands back where it started.
            onChange={() => {}}
            onClick={(e) => { e.stopPropagation(); onToggle?.(o.id) }}
            style={{ marginTop: 2 }}
          />
        )}
        <div
          className="min-w-0 flex-1 truncate"
          style={{ fontSize: 14, fontWeight: 500, color: 'rgb(52,64,84)' }}
        >
          {o.title}
        </div>
      </div>
      {/* Measured: "Value:" and the amount are TWO spans —
          label 12px/600 rgb(96,113,121), amount 12px/400 rgb(96,113,121).
          Ours rendered them as one 13px/400 string. */}
      {layout !== 'Unlabeled' && (
        <div className="flex items-center gap-1" style={{ marginTop: 6 }}>
          {layout === 'Default' && (
            <span style={{ fontSize: 12, fontWeight: 600, color: 'rgb(96,113,121)' }}>
              Value:
            </span>
          )}
          <span style={{ fontSize: 12, fontWeight: 400, color: 'rgb(96,113,121)' }}>
            {money(o.value_cents)}
          </span>
        </div>
      )}
      {layout === 'Default' && o.business_name && (
        <div className="truncate" style={{ fontSize: 12, color: 'rgb(102,112,133)' }}>
          {o.business_name}
        </div>
      )}
      {layout === 'Default' && (
        <div className="mt-2 flex gap-2" style={{ fontSize: 12, color: 'rgb(152,162,179)' }}>
          {/* GHL renders 7 quick-action icons per card. Call and SMS are stubbed
              per DECISIONS.md, so they are shown but inert. */}
          <span title="Call (stubbed)">☎</span>
          <span title="SMS (stubbed)">💬</span>
          <span title="Notes">📄</span>
          <span title="Tasks">☑</span>
          <span title="Appointments">📅</span>
        </div>
      )}
    </div>
  )
}

function StageColumn({
  stage,
  opps,
  layout,
  onOpen,
  selectable = false,
  selected,
  onToggle,
}: {
  stage: { id: number; name: string; count: number; value_cents: number }
  opps: Opportunity[]
  layout: Layout
  onOpen: (id: number) => void
  selectable?: boolean
  selected?: Set<number>
  onToggle?: (id: number) => void
}) {
  const { setNodeRef, isOver } = useDroppable({ id: stage.id })
  const total = opps.reduce((s, o) => s + o.value_cents, 0)
  return (
    // 240px pitch measured from GHL's stage-collapse buttons.
    // Only the card list scrolls (vertically). Nesting another scroll container
    // around this gave every column its own horizontal scrollbar.
    <div className="flex h-full shrink-0 flex-col" style={{ width: 240, paddingRight: 10 }}>
      <div
        className="shrink-0"
        style={{
          backgroundColor: '#fff',
          borderRadius: 6,
          padding: '10px 12px',
          border: '1px solid rgb(234,236,240)',
        }}
      >
        {/* Measured: name 14px/**700** rgb(16,24,40); count 12px/400
            rgb(102,112,133); total a SEPARATE 12px/500 rgb(0,0,0) element. */}
        <div className="flex items-center gap-1">
          <div
            className="min-w-0 flex-1 truncate"
            title={stage.name}
            style={{ fontSize: 14, fontWeight: 700, color: 'rgb(16,24,40)' }}
          >
            {stage.name}
          </div>
          {/* measured: GHL renders a "Collapse stage" chevron in each header */}
          <button title="Collapse stage" aria-label="Collapse stage"
            style={{ color: 'rgb(102,112,133)' }}>
            <IconChevronLeft size={16} color="rgb(102,112,133)" />
          </button>
        </div>
        <div className="flex items-center gap-2" style={{ marginTop: 2 }}>
          <span style={{ fontSize: 12, fontWeight: 400, color: 'rgb(102,112,133)' }}>
            {opps.length} opportunities
          </span>
          <span style={{ fontSize: 12, fontWeight: 500, color: 'rgb(0,0,0)' }}>
            {money(total)}
          </span>
        </div>
      </div>
      <div
        ref={setNodeRef}
        className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden"
        style={{
          marginTop: 8,
          borderRadius: 6,
          backgroundColor: isOver ? 'rgb(239,244,255)' : 'transparent',
          padding: 2,
        }}
      >
        {opps.map((o) => (
          <Card key={o.id} o={o} layout={layout} onOpen={onOpen}
            selectable={selectable} selected={selected?.has(o.id) ?? false}
            onToggle={onToggle} />
        ))}
      </div>
    </div>
  )
}

export function OpportunitiesPage({ user, onNavigate }: {
  user: Me
  /** The shell's view switcher. The ⋯ menu's "Dashboard insights" uses it. */
  onNavigate: (view: string) => void
}) {
  const qc = useQueryClient()
  // `POST /api/opportunities` is auth.STAFF, so a TECH's create is refused.
  // Mirror that here rather than let them fill in the form to find out on submit
  // (same precedent as Add Contact).
  const canCreate = user.role !== 'TECH'
  // `GET /api/forecast` is auth.STAFF, matching /api/dashboard whose aggregates it
  // repeats. Dim the tab rather than let a TECH open it onto a refusal.
  const canForecast = user.role !== 'TECH'
  const [tab, setTab] = useState('Opportunities')
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [showAdd, setShowAdd] = useState(false)
  const [view, setView] = useState<'board' | 'list'>('board')
  const [layout, setLayout] = useState<Layout>('Default')
  const [q, setQ] = useState('')
  const [status, setStatus] = useState('open')
  const [showFields, setShowFields] = useState(false)
  // The layout applies live while the modal is open, so the cards preview it. That
  // leaves Cancel nothing to undo unless the layout in effect when it opened is
  // remembered -- without this, Cancel and Apply were the same button twice.
  const [layoutOnOpen, setLayoutOnOpen] = useState<Layout>('Default')
  const [showOverflow, setShowOverflow] = useState(false)
  const [showLists, setShowLists] = useState(false)
  const [openOpp, setOpenOpp] = useState<number | null>(null)

  const pipelines = useQuery({ queryKey: ['pipelines'], queryFn: listPipelines })
  // Falling back to the first pipeline covers both "nothing picked yet" and "the
  // picked one is gone", without an effect that would fight the user's choice.
  const [pipelineId, setPipelineId] = useState<number | null>(null)
  const pipeline = pipelines.data?.find((p) => p.id === pipelineId) ?? pipelines.data?.[0]

  // A menu closes on Escape. Bound only while it is open, so the page is not
  // listening for keystrokes it has no use for.
  useEffect(() => {
    if (!showOverflow) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setShowOverflow(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [showOverflow])

  /** Close the Customize card modal, discarding the previewed layout. */
  const cancelFields = () => {
    setLayout(layoutOnOpen)
    setShowFields(false)
  }

  const opps = useQuery({
    queryKey: ['opportunities', pipeline?.id, q, status],
    queryFn: () => listOpportunities(pipeline!.id, q, status),
    enabled: !!pipeline,
  })

  const move = useMutation({
    mutationFn: (v: { id: number; stage_id: number }) =>
      moveOpportunity(v.id, v.stage_id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['opportunities'] })
      qc.invalidateQueries({ queryKey: ['pipelines'] })
    },
  })

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
  )

  function onDragEnd(e: DragEndEvent) {
    const stageId = Number(e.over?.id)
    const oppId = Number(e.active.id)
    if (!stageId || !oppId) return
    const o = opps.data?.find((x) => x.id === oppId)
    if (!o || o.stage_id === stageId) return
    move.mutate({ id: oppId, stage_id: stageId })
  }

  const total = opps.data?.length ?? 0

  // ---- Bulk Actions ----
  // The tab IS the selection mode: the board stays exactly as it is and the cards
  // start picking instead of opening.
  const selecting = tab === 'Bulk Actions'
  const visible = opps.data ?? []
  // A selection is only ever applied to rows the board is currently showing.
  // Otherwise changing the status filter, the search or the pipeline would leave
  // ids selected that nobody can see, and "Move to stage" would move them. This
  // is also what makes the CSV contain exactly the rows that are ticked.
  const chosen = visible.filter((o) => selected.has(o.id))
  const toggle = (id: number) =>
    setSelected((s) => {
      const next = new Set(s)
      if (!next.delete(id)) next.add(id)
      return next
    })

  /** CSV of the board's CURRENT FILTERED SET — what the ⋯ menu's Export means. */
  const exportFiltered = () => {
    const names = new Map((pipeline?.stages ?? []).map((s) => [s.id, s.name]))
    downloadCsv(
      csvFilename(pipeline?.name ?? 'opportunities', new Date()),
      opportunitiesCsv(visible, (id) => names.get(id) ?? ''),
    )
  }

  /**
   * What each ⋯ item does, or why it does nothing.
   *
   * "Restore opportunities" is the one that stays dead, and deliberately: it
   * implies a trash, and this codebase HAS NO SOFT DELETE. Inventing one as a
   * side quest is how a schema grows a `deleted_at` that half the queries forget
   * to filter on. See DECISIONS.md for what building it would actually take.
   */
  const overflowAction = (item: string): { run?: () => void; title: string } => {
    if (item === 'Export') {
      return {
        run: exportFiltered,
        title: `Download the ${visible.length} opportunit${
          visible.length === 1 ? 'y' : 'ies'} the board is showing as CSV`,
      }
    }
    if (item === 'Manage smart lists') {
      return { run: () => setShowLists(true), title: 'Rename or delete a saved list' }
    }
    if (item === 'Dashboard insights') {
      return { run: () => onNavigate('dashboard'), title: 'Open the Dashboard' }
    }
    return {
      title: 'Restoring needs a trash to restore from, and nothing in this app is '
        + 'soft-deleted — a delete is a delete. See DECISIONS.md.',
    }
  }

  return (
    <div className="flex h-screen min-w-0 flex-1 flex-col" style={{ backgroundColor: 'rgb(249,250,251)' }}>
      <div
        className="flex shrink-0 items-center gap-6 bg-white px-4"
        style={{ height: 90, borderBottom: '1px solid rgb(234,236,240)' }}
      >
        <div style={{ fontSize: 18, fontWeight: 500, color: 'rgb(31,41,55)' }}>
          Opportunities
        </div>
        {TABS.map((t) => {
          const live = LIVE_TABS.has(t) && !(t === 'Forecast' && !canForecast)
          return (
            <button
              key={t}
              onClick={() => live && setTab(t)}
              disabled={!live}
              title={
                LIVE_TABS.has(t)
                  ? live ? undefined : 'Your role cannot view the forecast'
                  : 'Present in GHL; not implemented in v1'
              }
              style={{
                fontSize: 14,
                fontWeight: 500,
                color: tab === t
                  ? 'rgb(56,160,219)'
                  : live ? 'rgb(102,112,133)' : 'rgb(152,162,179)',
                cursor: live ? 'pointer' : 'not-allowed',
              }}
            >
              {t}
            </button>
          )
        })}
      </div>

      {/* pipeline row */}
      <div className="flex shrink-0 items-center gap-3 px-4" style={{ height: 60 }}>
        <select
          value={pipeline?.id ?? ''}
          onChange={(e) => setPipelineId(Number(e.target.value))}
          style={{
            height: 36,
            borderRadius: 6,
            border: 'none',
            padding: '0 10px',
            // measured: pipeline name renders as plain 16px/400 text, not a bordered control
            fontSize: 16,
            fontWeight: 400,
            color: 'rgb(52,64,84)',
            backgroundColor: '#fff',
          }}
        >
          {pipelines.data?.map((p) => (
            <option key={p.id} value={p.id}>{p.name}</option>
          ))}
        </select>
        <div
          style={{
            fontSize: 14,
            fontWeight: 500,
            color: 'rgb(0,78,235)',
            backgroundColor: 'rgb(239,244,255)',
            borderRadius: 16,
            padding: '4px 12px',
          }}
        >
          {total} opportunities
        </div>

        <div className="ml-auto flex items-center gap-2">
          <div className="flex" style={{ border: '1px solid rgb(234,236,240)', borderRadius: 6 }}>
            {(['board', 'list'] as const).map((v) => (
              <button
                key={v}
                onClick={() => setView(v)}
                style={{
                  height: 34,
                  padding: '0 12px',
                  fontSize: 13,
                  fontWeight: 500,
                  backgroundColor: view === v ? 'rgb(239,244,255)' : '#fff',
                  color: view === v ? 'rgb(0,78,235)' : 'rgb(102,112,133)',
                }}
              >
                {v === 'board'
                  ? <IconGrid size={16} color={view === v ? 'rgb(0,78,235)' : 'rgb(96,113,121)'} />
                  : <IconList size={16} color={view === v ? 'rgb(0,78,235)' : 'rgb(96,113,121)'} />}
              </button>
            ))}
          </div>
          <div className="relative">
            <button
              onClick={() => setShowOverflow((s) => !s)}
              aria-haspopup="menu"
              // above the backdrop below, so re-clicking to close still runs this
              // toggle rather than being swallowed as an outside click
              className="relative z-20"
              style={{
                height: 34,
                width: 34,
                borderRadius: 6,
                border: '1px solid rgb(234,236,240)',
                backgroundColor: '#fff',
              }}
            >
              ⋯
            </button>
            {/* measured y=121: Import (blue text) then Add opportunity (blue button) */}
            {showOverflow && (
              <>
                {/* Without this, an outside click meant to dismiss the menu fell
                    through to whatever was under it -- during QA a click at (800,700)
                    landed on a card and opened the detail dialog. Same backdrop idiom
                    as the Customize card modal, just transparent. */}
                <div
                  className="fixed inset-0 z-10"
                  onClick={() => setShowOverflow(false)}
                />
                <div
                  role="menu"
                  className="absolute right-0 z-20 mt-1 bg-white"
                  style={{
                    minWidth: 220,
                    borderRadius: 8,
                    border: '1px solid rgb(234,236,240)',
                    boxShadow: '0 12px 16px -4px rgba(16,24,40,0.08)',
                    padding: 4,
                  }}
                >
                  {OVERFLOW.map((t) => {
                    const item = overflowAction(t)
                    return (
                      <button
                        key={t}
                        role="menuitem"
                        className="block w-full text-left"
                        disabled={!item.run}
                        title={item.title}
                        onClick={() => { setShowOverflow(false); item.run?.() }}
                        style={{
                          padding: '8px 12px',
                          fontSize: 14,
                          color: item.run ? 'rgb(52,64,84)' : 'rgb(152,162,179)',
                          cursor: item.run ? 'pointer' : 'not-allowed',
                        }}
                      >
                        {t}
                      </button>
                    )
                  })}
                </div>
              </>
            )}
          </div>

          <button
            disabled
            title="Opportunity import is not implemented in v1"
            className="flex items-center gap-1"
            style={{
              fontSize: 13, fontWeight: 500, color: 'rgb(0,78,235)',
              opacity: 0.5, cursor: 'not-allowed',
            }}
          >
            <IconDownload size={16} color="rgb(0,78,235)" />
            Import
          </button>
          <button
            onClick={() => setShowAdd(true)}
            disabled={!canCreate || !pipeline}
            title={
              canCreate
                ? pipeline ? undefined : 'No pipeline loaded yet'
                : 'Your role cannot create opportunities'
            }
            className="flex items-center gap-1"
            style={{
              height: 34, padding: '0 12px', borderRadius: 6, fontSize: 13,
              fontWeight: 500, color: '#fff', backgroundColor: 'rgb(0,78,235)',
              ...(canCreate && pipeline ? {} : { opacity: 0.5, cursor: 'not-allowed' }),
            }}
          >
            <IconPlus size={16} color="#fff" />
            Add opportunity
          </button>
        </div>
      </div>

      {/* The Forecast tab replaces the filters and the board; the pipeline row
          above stays, because a forecast is still per-pipeline. */}
      {tab === 'Forecast' && <ForecastPanel pipeline={pipeline} />}

      {/* Bulk Actions keeps the board and the filters; only the bar is added, so
          the selection is made against the set the user is already looking at. */}
      {(tab === 'Opportunities' || selecting) && (
      <>
      {/* saved views — measured 14px/400 rgb(102,112,133) with 16x16 icons */}
      <SavedViewsRow
        user={user}
        now={{ pipelineId: pipeline?.id, status, q }}
        onApply={(f) => {
          // A saved view that named a pipeline puts the board back on it; one
          // that did not leaves the pipeline alone and only re-filters.
          if (f.pipelineId != null) setPipelineId(f.pipelineId)
          setStatus(f.status)
          setQ(f.q)
        }}
        onManage={() => setShowLists(true)}
      />

      {/* toolbar */}
      <div className="flex shrink-0 items-center gap-2 px-4 pb-3">
        <IconFilter size={16} color="rgb(0,78,235)" />
        <select
          value={status}
          onChange={(e) => setStatus(e.target.value)}
          style={{
            height: 34,
            borderRadius: 6,
            border: '1px solid rgb(234,236,240)',
            padding: '0 8px',
            fontSize: 13,
            backgroundColor: '#fff',
          }}
        >
          <option value="open">Status: Open</option>
          <option value="won">Status: Won</option>
          <option value="lost">Status: Lost</option>
          <option value="all">Status: All</option>
        </select>
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search opportunities"
          style={{
            height: 34,
            width: 260,
            borderRadius: 6,
            border: '1px solid rgb(234,236,240)',
            padding: '0 12px',
            fontSize: 14,
          }}
        />
        {/* Manage fields sits alone at the right of the FILTER row (measured y=219);
            Import / Add opportunity live in the pipeline row above (measured y=121). */}
        <button
          onClick={() => { setLayoutOnOpen(layout); setShowFields(true) }}
          className="ml-auto flex items-center gap-2"
          style={{ fontSize: 14, fontWeight: 600, color: 'rgb(71,84,103)' }}
        >
          Manage fields
        </button>
      </div>

      {selecting && (
        <BulkActionsBar
          user={user}
          pipeline={pipeline}
          chosen={chosen}
          visibleCount={visible.length}
          onSelectAll={() => setSelected(new Set(visible.map((o) => o.id)))}
          onClear={() => setSelected(new Set())}
        />
      )}

      {/* board / list */}
      {view === 'board' ? (
        <DndContext sensors={sensors} onDragEnd={onDragEnd}>
          <div className="min-h-0 flex-1 overflow-x-auto overflow-y-hidden px-4 pb-4">
            <div className="flex h-full">
              {pipeline?.stages.map((s) => (
                <StageColumn
                  key={s.id}
                  stage={s}
                  opps={(opps.data ?? []).filter((o) => o.stage_id === s.id)}
                  layout={layout}
                  onOpen={setOpenOpp}
                  selectable={selecting}
                  selected={selected}
                  onToggle={toggle}
                />
              ))}
            </div>
          </div>
        </DndContext>
      ) : (
        <div className="mx-4 mb-4 min-h-0 flex-1 overflow-auto bg-white"
          style={{ borderRadius: 8, border: '1px solid rgb(234,236,240)' }}>
          <table className="w-full">
            <thead>
              <tr>
                {selecting && (
                  <th className="sticky top-0 bg-white"
                    style={{
                      width: 36, borderBottom: '1px solid rgb(234,236,240)',
                    }}>
                    <input
                      type="checkbox"
                      aria-label="Select every visible opportunity"
                      checked={visible.length > 0 && chosen.length === visible.length}
                      onChange={(e) =>
                        setSelected(e.target.checked
                          ? new Set(visible.map((o) => o.id))
                          : new Set())}
                    />
                  </th>
                )}
                {['Opportunity name', 'Stage', 'Value', 'Business name', 'Source'].map((h) => (
                  <th
                    key={h}
                    className="sticky top-0 bg-white text-left"
                    style={{
                      height: 44,
                      padding: '0 12px',
                      fontSize: 13,
                      fontWeight: 700,
                      color: 'rgb(71,84,103)',
                      borderBottom: '1px solid rgb(234,236,240)',
                    }}
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {opps.data?.map((o) => (
                <tr key={o.id}
                  onClick={() => (selecting ? toggle(o.id) : setOpenOpp(o.id))}
                  className="cursor-pointer hover:bg-[rgb(249,250,251)]">
                  {selecting && (
                    <td style={{ borderBottom: '1px solid rgb(242,244,247)', textAlign: 'center' }}>
                      <input
                        type="checkbox"
                        aria-label={'Select ' + o.title}
                        checked={selected.has(o.id)}
                        onChange={() => {}}
                        onClick={(e) => { e.stopPropagation(); toggle(o.id) }}
                      />
                    </td>
                  )}
                  <td style={{ height: 48, padding: '0 12px', fontSize: 14, borderBottom: '1px solid rgb(242,244,247)' }}>
                    {o.title}
                  </td>
                  <td style={{ padding: '0 12px', fontSize: 14, borderBottom: '1px solid rgb(242,244,247)' }}>
                    {pipeline?.stages.find((s) => s.id === o.stage_id)?.name}
                  </td>
                  <td style={{ padding: '0 12px', fontSize: 14, borderBottom: '1px solid rgb(242,244,247)' }}>
                    {money(o.value_cents)}
                  </td>
                  <td style={{ padding: '0 12px', fontSize: 14, borderBottom: '1px solid rgb(242,244,247)' }}>
                    {o.business_name ?? ''}
                  </td>
                  <td style={{ padding: '0 12px', fontSize: 14, borderBottom: '1px solid rgb(242,244,247)' }}>
                    {o.source ?? ''}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      </>
      )}

      {showAdd && pipeline && (
        <AddOpportunityDialog
          pipelines={pipelines.data ?? []}
          initialPipelineId={pipeline.id}
          onClose={() => setShowAdd(false)}
          onDone={(createdInPipelineId) => {
            setShowAdd(false)
            // Show the card that was just filed. It may have gone into a pipeline
            // the board is not looking at, and a board filtered to Won or Lost
            // would hide a new (always Open) opportunity entirely -- which reads
            // as "nothing happened".
            setPipelineId(createdInPipelineId)
            if (status !== 'open' && status !== 'all') setStatus('open')
            qc.invalidateQueries({ queryKey: ['opportunities'] })
            qc.invalidateQueries({ queryKey: ['pipelines'] })
          }}
        />
      )}

      {showLists && (
        <ManageSavedViews user={user} onClose={() => setShowLists(false)} />
      )}

      {openOpp != null && (
        <OpportunityDetail
          opportunityId={openOpp}
          pipeline={pipeline}
          onClose={() => setOpenOpp(null)}
        />
      )}

      {/* Manage fields modal - measured card-layout options */}
      {showFields && (
        <div
          className="fixed inset-0 z-30 flex items-center justify-center"
          style={{ backgroundColor: 'rgba(16,24,40,0.4)' }}
          onClick={cancelFields}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            className="bg-white"
            style={{ width: 520, borderRadius: 8, padding: 20 }}
          >
            <div style={{ fontSize: 18, fontWeight: 600, color: 'rgb(16,24,40)' }}>
              Customize card
            </div>
            <div style={{ fontSize: 13, color: 'rgb(102,112,133)', marginTop: 4 }}>
              Card layout
            </div>
            <div className="mt-2 flex gap-2">
              {LAYOUTS.map((l) => (
                <button
                  key={l}
                  onClick={() => setLayout(l)}
                  style={{
                    height: 34,
                    padding: '0 14px',
                    borderRadius: 6,
                    fontSize: 13,
                    fontWeight: 500,
                    border: '1px solid rgb(234,236,240)',
                    backgroundColor: layout === l ? 'rgb(239,244,255)' : '#fff',
                    color: layout === l ? 'rgb(0,78,235)' : 'rgb(102,112,133)',
                  }}
                >
                  {l}
                </button>
              ))}
            </div>
            <div style={{ fontSize: 13, color: 'rgb(102,112,133)', marginTop: 16 }}>
              Fields
            </div>
            <div style={{ fontSize: 14, color: 'rgb(52,64,84)', marginTop: 6 }}>
              Opportunity owner · Business name · Source · Value · Lost reason
            </div>
            <div className="mt-5 flex justify-end gap-2">
              <button
                onClick={cancelFields}
                style={{ height: 36, padding: '0 14px', borderRadius: 6, border: '1px solid rgb(234,236,240)', fontSize: 14 }}
              >
                Cancel
              </button>
              <button
                onClick={() => setShowFields(false)}
                style={{
                  height: 36,
                  padding: '0 14px',
                  borderRadius: 6,
                  fontSize: 14,
                  color: '#fff',
                  backgroundColor: 'rgb(0,78,235)',
                }}
              >
                Apply
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

/**
 * Add opportunity.
 *
 * GHL's own create form sits behind a write we never click — the live account is
 * read-only (DECISIONS.md) — so unlike the rest of this screen there is no capture
 * to match. Markup, styling, error display and close/submit behaviour therefore
 * follow the in-repo idiom, AddContactDialog in ContactsPage, rather than a second
 * invented dialog.
 */
function AddOpportunityDialog({
  pipelines,
  initialPipelineId,
  onClose,
  onDone,
}: {
  pipelines: Pipeline[]
  initialPipelineId: number
  onClose: () => void
  onDone: (createdInPipelineId: number) => void
}) {
  const [title, setTitle] = useState('')
  const [pipelineId, setPipelineId] = useState(initialPipelineId)
  const [stageId, setStageId] = useState<number | null>(null)
  const [value, setValue] = useState('')
  const [contact, setContact] = useState<{ id: number; name: string } | null>(null)
  const [q, setQ] = useState('')
  const [error, setError] = useState<string | null>(null)

  const pipeline = pipelines.find((p) => p.id === pipelineId) ?? pipelines[0]
  const stages = pipeline?.stages ?? []
  // Default to the first stage, and follow the pipeline when it changes instead of
  // holding a stage id the new pipeline has never heard of — the backend refuses
  // that pair, so keeping it would be a submit-time error for no reason.
  const stage = stages.find((s) => s.id === stageId) ?? stages[0]

  const matches = useQuery({
    queryKey: ['contacts', 'picker', q],
    queryFn: () => listContacts({ page: 1, page_size: 6, q }),
    enabled: !contact && q.trim().length > 0,
  })

  const create = useMutation({
    mutationFn: () =>
      createOpportunity({
        title: title.trim(),
        pipeline_id: pipeline.id,
        stage_id: stage.id,
        contact_id: contact?.id ?? null,
        // Dollars -> integer cents without a float in the middle; see api.ts.
        value_cents: centsFromDollars(value),
      }),
    onSuccess: () => onDone(pipeline.id),
    onError: (e: Error) => setError(e.message),
  })

  // A name is the one field GHL marks required, and `stage` is missing only if the
  // chosen pipeline has no stages at all — there would be nowhere to file the card.
  const ready = !!stage && title.trim().length > 0

  const label = { fontSize: 14, color: 'rgb(102,112,133)' } as const
  const input = {
    width: '100%', height: 36, marginTop: 4, fontSize: 14,
    borderRadius: 6, border: '1px solid rgb(234,236,240)', padding: '0 10px',
    backgroundColor: '#fff',
  } as const

  return (
    <div
      className="fixed inset-0 z-40 flex items-center justify-center"
      style={{ backgroundColor: 'rgba(16,24,40,0.4)' }}
      onClick={onClose}
    >
      <div onClick={(e) => e.stopPropagation()} className="bg-white"
        style={{ width: 460, borderRadius: 8, padding: 20 }}>
        <div style={{ fontSize: 18, fontWeight: 600, color: 'rgb(16,24,40)' }}>
          Add opportunity
        </div>

        <div style={{ marginTop: 12 }}>
          <div style={label}>Opportunity name</div>
          <input
            autoFocus
            value={title}
            maxLength={120}
            onChange={(e) => setTitle(e.target.value)}
            style={input}
          />
        </div>

        <div style={{ marginTop: 12 }}>
          <div style={label}>Contact</div>
          {contact ? (
            <div
              className="flex items-center justify-between"
              style={{ ...input, display: 'flex', paddingRight: 6 }}
            >
              <span className="truncate" style={{ color: 'rgb(52,64,84)' }}>
                {contact.name}
              </span>
              <button
                onClick={() => { setContact(null); setQ('') }}
                style={{ fontSize: 13, fontWeight: 500, color: 'rgb(0,78,235)', padding: '0 6px' }}
              >
                Clear
              </button>
            </div>
          ) : (
            <>
              <input
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="Search contacts"
                style={input}
              />
              {q.trim().length > 0 && (
                <div
                  style={{
                    marginTop: 4, maxHeight: 148, overflowY: 'auto',
                    border: '1px solid rgb(234,236,240)', borderRadius: 6,
                  }}
                >
                  {matches.data?.items.length ? (
                    matches.data.items.map((c) => (
                      <button
                        key={c.id}
                        onClick={() => setContact({ id: c.id, name: c.name })}
                        className="block w-full truncate text-left hover:bg-[rgb(249,250,251)]"
                        style={{ padding: '8px 10px', fontSize: 14, color: 'rgb(52,64,84)' }}
                      >
                        {c.name}
                        {c.phone ? ' · ' + c.phone : ''}
                      </button>
                    ))
                  ) : (
                    <div style={{ padding: '8px 10px', fontSize: 13, color: 'rgb(102,112,133)' }}>
                      {matches.isFetching ? 'Searching…' : 'No matching contact'}
                    </div>
                  )}
                </div>
              )}
              <div style={{ fontSize: 12, color: 'rgb(102,112,133)', marginTop: 4 }}>
                Optional — an opportunity can be filed without a contact.
              </div>
            </>
          )}
        </div>

        <div style={{ marginTop: 12 }}>
          <div style={label}>Pipeline</div>
          <select
            value={pipeline?.id ?? ''}
            onChange={(e) => { setPipelineId(Number(e.target.value)); setStageId(null) }}
            style={input}
          >
            {pipelines.map((p) => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </select>
        </div>

        <div style={{ marginTop: 12 }}>
          <div style={label}>Stage</div>
          <select
            value={stage?.id ?? ''}
            onChange={(e) => setStageId(Number(e.target.value))}
            style={input}
          >
            {stages.map((s) => (
              <option key={s.id} value={s.id}>{s.name}</option>
            ))}
          </select>
        </div>

        <div style={{ marginTop: 12 }}>
          <div style={label}>Value</div>
          <div className="flex items-center gap-1">
            <span style={{ fontSize: 14, color: 'rgb(52,64,84)', marginTop: 4 }}>$</span>
            <input
              type="number"
              min={0}
              step="0.01"
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder="Please Input"
              style={input}
            />
          </div>
        </div>

        {error && (
          <div style={{ fontSize: 13, color: 'rgb(217,45,32)', marginTop: 10 }}>{error}</div>
        )}

        <div className="mt-5 flex justify-end gap-2">
          <button onClick={onClose}
            style={{ height: 36, padding: '0 14px', borderRadius: 6, fontSize: 14, border: '1px solid rgb(234,236,240)' }}>
            Cancel
          </button>
          <button
            onClick={() => { setError(null); create.mutate() }}
            // Disabled while the POST is in flight: a second click would create a
            // second opportunity, and nothing on the server dedupes them.
            disabled={!ready || create.isPending}
            title={ready ? undefined : 'An opportunity needs a name'}
            style={{
              height: 36, padding: '0 14px', borderRadius: 6, fontSize: 14,
              fontWeight: 500, color: '#fff', backgroundColor: 'rgb(0,78,235)',
              ...(ready && !create.isPending ? {} : { opacity: 0.5, cursor: 'not-allowed' }),
            }}
          >
            {create.isPending ? 'Creating…' : 'Create'}
          </button>
        </div>
      </div>
    </div>
  )
}
