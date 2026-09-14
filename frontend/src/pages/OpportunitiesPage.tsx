import {
  DndContext,
  DragOverlay,
  PointerSensor,
  closestCorners,
  useSensor,
  useSensors,
  useDroppable,
  type DragEndEvent,
  type DragStartEvent,
} from '@dnd-kit/core'
import { SortableContext, verticalListSortingStrategy } from '@dnd-kit/sortable'
import { IconDownload, IconGrid, IconList, IconPlus } from '../components/Icon'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { BulkActionsBar } from '../components/BulkActionsBar'
import { ForecastPanel } from '../components/ForecastPanel'
import { ManageSavedViews, SavedViewsRow } from '../components/SavedViews'
import { PipelinesPanel } from '../components/PipelinesPanel'
import { PageTabs } from '../components/PageTabs'
import { PipelinePicker } from '../components/PipelinePicker'
import { PipelineModal } from '../components/PipelineModal'
// GoHighLevel's Add new opportunity modal (2026-09-14), in the edit modal's family.
import { AddOpportunityDialog } from '../components/AddOpportunityModal'
import { OpportunityDetail } from '../components/OpportunityDetail'
import { CARD_SURFACE, Card, CardFace } from '../components/OpportunityCard'
import { useEffect, useRef, useState } from 'react'
import { IconChevronLeft, IconFilter } from '../components/Icon'
import {
  applyMove,
  cardDragId,
  cardIdFrom,
  moveForDrop,
  stageCards,
  stageDropId,
  type BoardMove,
} from '../lib/boardOrder'
import {
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
import type { Focus } from '../lib/focus'
import { pipelineFromSearch, stageHeader, type ColorMode } from '../lib/pipelines'

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
// Exactly GoHighLevel's four (refs/opps/05-08). "Custom fields" sat here from
// 2026-09-11 until 2026-09-13, when it moved to Settings > Custom Fields — the
// same panel, relocated, so the tab row matches the original again.
const TABS = ['Opportunities', 'Forecast', 'Pipelines', 'Bulk Actions']
const OVERFLOW = ['Export', 'Restore opportunities', 'Manage smart lists', 'Dashboard insights']

/**
 * All four tabs go somewhere now. The one thing a tab can still be dead for is a
 * ROLE: `/api/forecast` is STAFF, so a TECH gets it dimmed with a title rather
 * than a blank pane or a 403 — the disabled-rather-than-omitted rule used for
 * Import above and for the Reporting tabs v1 does not implement.
 *
 * Pipelines deliberately stays LIVE for every role: the panel itself disables the
 * controls a non-admin cannot use, which is more useful than hiding the structure
 * from the people who work it every day.
 */
const LAYOUTS = ['Default', 'Compact', 'Unlabeled'] as const
type Layout = (typeof LAYOUTS)[number]

function StageColumn({
  stage,
  index,
  colorMode,
  opps,
  layout,
  onOpen,
  dragging,
  selectable = false,
  selected,
  onToggle,
}: {
  stage: { id: number; name: string; count: number; value_cents: number; color?: string | null }
  /** Column order — picks the palette colour for a stage that has none stored. */
  index: number
  /** The pipeline's "Set pipeline display colors" — see lib/pipelines.ts stageHeader. */
  colorMode: ColorMode | undefined
  opps: Opportunity[]
  layout: Layout
  onOpen: (id: number) => void
  dragging: React.RefObject<boolean>
  selectable?: boolean
  selected?: Set<number>
  onToggle?: (id: number) => void
}) {
  // The column is still a droppable in its own right, for the empty space below
  // the last card — and for a column with no cards at all, which has nothing
  // sortable in it to drop onto.
  const { setNodeRef, isOver } = useDroppable({ id: stageDropId(stage.id) })
  const total = opps.reduce((s, o) => s + o.value_cents, 0)
  const look = stageHeader(colorMode, stage.color, index)
  return (
    // 240px pitch measured from GHL's stage-collapse buttons.
    // Only the card list scrolls (vertically). Nesting another scroll container
    // around this gave every column its own horizontal scrollbar.
    <div className="flex h-full shrink-0 flex-col" style={{ width: 240, paddingRight: 10 }}>
      <div
        className="shrink-0"
        data-color-mode={colorMode ?? 'none'}
        style={{
          backgroundColor: look.background,
          borderRadius: 6,
          padding: '10px 12px',
          border: '1px solid ' + look.border,
        }}
      >
        {/* Measured: name 14px/**700** rgb(16,24,40); count 12px/400
            rgb(102,112,133); total a SEPARATE 12px/500 rgb(0,0,0) element. */}
        <div className="flex items-center gap-1">
          {look.dot && (
            <span aria-hidden style={{
              width: 8, height: 8, borderRadius: 4, flexShrink: 0, marginRight: 2,
              backgroundColor: look.dot,
            }} />
          )}
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
      <SortableContext
        items={opps.map((o) => cardDragId(o.id))}
        strategy={verticalListSortingStrategy}
      >
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
            <Card key={o.id} o={o} layout={layout} onOpen={onOpen} dragging={dragging}
              selectable={selectable} selected={selected?.has(o.id) ?? false}
              onToggle={onToggle} />
          ))}
        </div>
      </SortableContext>
    </div>
  )
}

export function OpportunitiesPage({ user, focus, onNavigate }: {
  user: Me
  /** A record the ctrl+K palette asked for. */
  focus?: Focus | null
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
  // The pipeline dropdown's "+ New pipeline" opens the same Create pipeline modal
  // as the Pipelines tab (refs/round3/32).
  const [creatingPipeline, setCreatingPipeline] = useState(false)

  // The ctrl+K palette asked for one record. Opening it here, rather than
  // teaching the palette how each page's detail panel works, keeps a searched
  // record and a clicked row on exactly the same path.
  useEffect(() => {
    if (focus) setOpenOpp(focus.id)
  }, [focus])

  const pipelines = useQuery({ queryKey: ['pipelines'], queryFn: listPipelines })
  // Falling back to the first pipeline covers both "nothing picked yet" and "the
  // picked one is gone", without an effect that would fight the user's choice.
  // Copy link on the Pipelines tab hands out /opportunities?pipeline=<id>; a fresh
  // load of that URL lands here and opens the board on it. A pipeline the user
  // cannot access is simply not in the list, so it falls back to the first.
  const [pipelineId, setPipelineId] = useState<number | null>(
    () => pipelineFromSearch(window.location.search))
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

  // The exact cache entry the board is drawing. The optimistic update writes it
  // and the rollback restores it, so both have to name the same key the query
  // above uses -- a near-miss would leave the card looking moved for ever.
  const boardKey = ['opportunities', pipeline?.id, q, status]

  const [moveError, setMoveError] = useState<string | null>(null)
  // The card under the cursor, drawn by the overlay so it can leave its column.
  const [dragged, setDragged] = useState<Opportunity | null>(null)
  // True from the moment a drag starts until the next press. A drop ends with a
  // click on the card, which would otherwise open the opportunity dialog on top
  // of the board after every move.
  const dragging = useRef(false)

  /**
   * Move a card, on screen first.
   *
   * The card has to move the instant it is dropped -- but only for as long as
   * the server agrees. Yesterday's CSRF bug (20cc838) made every one of these
   * saves fail while the board went on showing the card in its new column, so a
   * refused move must visibly snap back and say why, not quietly disagree with
   * the database until the next reload.
   */
  const move = useMutation({
    mutationFn: (v: BoardMove) => moveOpportunity(v.id, v.stage_id, v.position),
    onMutate: async (v: BoardMove) => {
      // A refetch already in flight would land on top of the optimistic board
      // with the old order and undo it half a second after the drop.
      await qc.cancelQueries({ queryKey: ['opportunities'] })
      const previous = qc.getQueryData<Opportunity[]>(boardKey)
      if (previous) qc.setQueryData(boardKey, applyMove(previous, v))
      setMoveError(null)
      return { previous }
    },
    onError: (err: Error, _v, ctx) => {
      // Put it back exactly where it was picked up from...
      if (ctx?.previous) qc.setQueryData(boardKey, ctx.previous)
      // ...and say so. `err.message` is already a sentence written for a person
      // (api.ts `readable`), never the wire body.
      setMoveError(err.message)
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ['opportunities'] })
      qc.invalidateQueries({ queryKey: ['pipelines'] })
    },
  })

  const sensors = useSensors(
    // 5px before a press becomes a drag, so a card can still be clicked open.
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
  )

  function onDragStart(e: DragStartEvent) {
    dragging.current = true
    const id = cardIdFrom(e.active.id)
    setDragged((opps.data ?? []).find((o) => o.id === id) ?? null)
  }

  function onDragEnd(e: DragEndEvent) {
    setDragged(null)
    // Dropped on nothing, or dropped back where it already was: no request at
    // all. `moveForDrop` decides both -- see lib/boardOrder.ts.
    const next = moveForDrop(opps.data ?? [], e.active.id, e.over?.id ?? null)
    if (next) move.mutate(next)
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
      <PageTabs title="Opportunities" label="Opportunities views" active={tab}
        tabs={TABS.map((t) => ({
          key: t,
          label: t,
          onSelect: () => setTab(t),
          blocked: t === 'Forecast' && !canForecast ? 'Your role cannot view the forecast' : undefined,
        }))} />

      {/* pipeline row */}
      <div className="flex shrink-0 items-center gap-3 px-4" style={{
        height: 60,
        // Not on the Pipelines tab, which has its own header (refs/opps/02).
        display: tab === 'Pipelines' ? 'none' : undefined,
      }}>
        <PipelinePicker
          pipelines={pipelines.data ?? []}
          selectedId={pipeline?.id}
          role={user.role}
          onSelect={(id) => setPipelineId(id)}
          onNew={() => setCreatingPipeline(true)}
        />
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

      {/* Managing the structure is not filtering the board, so the pipeline row
          above stays and everything below it is replaced. */}
      {tab === 'Pipelines' && <PipelinesPanel user={user} />}

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

      {/* A refused move -- a 403, a stage that is not in this pipeline, the backend
          down -- has already snapped the card back by the time this renders. It
          says which, in words, because the failure it is here to catch was one
          that showed nothing at all. */}
      {moveError && (
        <div
          role="alert"
          className="mx-4 mb-2 flex shrink-0 items-start gap-3"
          style={{
            fontSize: 13,
            color: 'rgb(180,35,24)',
            backgroundColor: 'rgb(254,243,242)',
            border: '1px solid rgb(253,162,155)',
            borderRadius: 8,
            padding: '8px 12px',
          }}
        >
          <span className="flex-1">The card was put back. {moveError}</span>
          <button
            onClick={() => setMoveError(null)}
            aria-label="Dismiss"
            style={{ fontSize: 13, fontWeight: 600, color: 'rgb(180,35,24)' }}
          >
            ✕
          </button>
        </div>
      )}

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
        <DndContext
          sensors={sensors}
          // Corners, not the pointer: a card is 230px wide and the columns are
          // 240px apart, so pointer-only collision made the gap between two
          // columns a dead zone that dropped the card back where it came from.
          collisionDetection={closestCorners}
          onDragStart={onDragStart}
          onDragEnd={onDragEnd}
          onDragCancel={() => setDragged(null)}
        >
          <div className="min-h-0 flex-1 overflow-x-auto overflow-y-hidden px-4 pb-4">
            <div className="flex h-full">
              {pipeline?.stages.map((s, i) => (
                <StageColumn
                  key={s.id}
                  stage={s}
                  index={i}
                  colorMode={(pipeline as { color_mode?: ColorMode }).color_mode}
                  opps={stageCards(opps.data ?? [], s.id)}
                  layout={layout}
                  onOpen={setOpenOpp}
                  dragging={dragging}
                  selectable={selecting}
                  selected={selected}
                  onToggle={toggle}
                />
              ))}
            </div>
          </div>
          {/* Drawn under the cursor so a card can be carried between columns --
              the board scrolls sideways, and a card that stayed inside its own
              scrolling column could not be seen crossing to the next one. */}
          <DragOverlay dropAnimation={null}>
            {dragged && (
              <div
                style={{
                  ...CARD_SURFACE,
                  cursor: 'grabbing',
                  boxShadow: 'rgba(16, 24, 40, 0.18) 0px 8px 16px 0px',
                }}
              >
                <CardFace o={dragged} layout={layout} />
              </div>
            )}
          </DragOverlay>
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
          user={user}
          onClose={() => setShowAdd(false)}
          onDone={(createdInPipelineId, createdStatus) => {
            setShowAdd(false)
            // Show the card that was just filed. It may have gone into a pipeline
            // the board is not looking at, and a board filtered to another status
            // would hide it entirely -- which reads as "nothing happened". The Add
            // modal has a Status since 2026-09-14, so follow the card's own.
            setPipelineId(createdInPipelineId)
            if (status !== createdStatus && status !== 'all') setStatus(createdStatus)
            qc.invalidateQueries({ queryKey: ['opportunities'] })
            qc.invalidateQueries({ queryKey: ['pipelines'] })
          }}
        />
      )}

      {creatingPipeline && (
        <PipelineModal user={user} pipeline={null}
          otherNames={(pipelines.data ?? []).map((p) => p.name)}
          onClose={() => setCreatingPipeline(false)}
          onSaved={(p) => {
            // The modal has already invalidated ['pipelines']; seed the new row so the
            // board lands on it now rather than on the first pipeline until the
            // refetch arrives.
            qc.setQueryData<Pipeline[]>(['pipelines'], (old) =>
              old && !old.some((x) => x.id === p.id) ? [...old, p as unknown as Pipeline] : old)
            setPipelineId(p.id)
            setCreatingPipeline(false)
          }} />
      )}

      {showLists && (
        <ManageSavedViews user={user} onClose={() => setShowLists(false)} />
      )}

      {openOpp != null && (
        <OpportunityDetail
          opportunityId={openOpp}
          pipeline={pipeline}
          user={user}
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
