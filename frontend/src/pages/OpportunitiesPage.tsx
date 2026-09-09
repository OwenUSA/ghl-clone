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
import { OpportunityDetail } from '../components/OpportunityDetail'
import { useState } from 'react'
import { IconChevronLeft, IconFilter } from '../components/Icon'
import {
  listOpportunities,
  listPipelines,
  moveOpportunity,
  money,
  type Opportunity,
} from '../lib/api'

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
const LAYOUTS = ['Default', 'Compact', 'Unlabeled'] as const
type Layout = (typeof LAYOUTS)[number]

function Card({ o, layout, onOpen }: { o: Opportunity; layout: Layout; onOpen: (id: number) => void }) {
  const { attributes, listeners, setNodeRef, transform, isDragging } = useDraggable({
    id: o.id,
  })
  return (
    <div
      ref={setNodeRef}
      {...listeners}
      {...attributes}
      onClick={() => onOpen(o.id)}
      style={{
        width: 230,
        borderRadius: 4,
        backgroundColor: '#fff',
        boxShadow: 'rgba(16, 24, 40, 0.1) 0px 1px 3px 0px',
        padding: 12,
        marginBottom: 8,
        cursor: 'grab',
        opacity: isDragging ? 0.4 : 1,
        transform: transform
          ? `translate3d(${transform.x}px, ${transform.y}px, 0)`
          : undefined,
      }}
    >
      <div
        className="truncate"
        style={{ fontSize: 14, fontWeight: 500, color: 'rgb(52,64,84)' }}
      >
        {o.title}
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
}: {
  stage: { id: number; name: string; count: number; value_cents: number }
  opps: Opportunity[]
  layout: Layout
  onOpen: (id: number) => void
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
          <Card key={o.id} o={o} layout={layout} onOpen={onOpen} />
        ))}
      </div>
    </div>
  )
}

export function OpportunitiesPage() {
  const qc = useQueryClient()
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
  const [openOpp, setOpenOpp] = useState<number | null>(null)

  const pipelines = useQuery({ queryKey: ['pipelines'], queryFn: listPipelines })
  const pipeline = pipelines.data?.[0]

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

  return (
    <div className="flex h-screen min-w-0 flex-1 flex-col" style={{ backgroundColor: 'rgb(249,250,251)' }}>
      <div
        className="flex shrink-0 items-center gap-6 bg-white px-4"
        style={{ height: 90, borderBottom: '1px solid rgb(234,236,240)' }}
      >
        <div style={{ fontSize: 18, fontWeight: 500, color: 'rgb(31,41,55)' }}>
          Opportunities
        </div>
        {TABS.map((t, i) => (
          <div
            key={t}
            style={{
              fontSize: 14,
              fontWeight: 500,
              color: i === 0 ? 'rgb(56,160,219)' : 'rgb(102,112,133)',
            }}
          >
            {t}
          </div>
        ))}
      </div>

      {/* pipeline row */}
      <div className="flex shrink-0 items-center gap-3 px-4" style={{ height: 60 }}>
        <select
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
            <option key={p.id}>{p.name}</option>
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
                {OVERFLOW.map((t) => (
                  <div
                    key={t}
                    title="Present in GHL; not implemented in v1"
                    style={{
                      padding: '8px 12px',
                      fontSize: 14,
                      color: 'rgb(152,162,179)',
                      cursor: 'not-allowed',
                    }}
                  >
                    {t}
                  </div>
                ))}
              </div>
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
            disabled
            title="Creating an opportunity is not implemented in v1"
            className="flex items-center gap-1"
            style={{
              height: 34, padding: '0 12px', borderRadius: 6, fontSize: 13,
              fontWeight: 500, color: '#fff', backgroundColor: 'rgb(0,78,235)',
              opacity: 0.5, cursor: 'not-allowed',
            }}
          >
            <IconPlus size={16} color="#fff" />
            Add opportunity
          </button>
        </div>
      </div>

      {/* saved views — measured 14px/400 rgb(102,112,133) with 16x16 icons */}
      <div className="flex shrink-0 items-center gap-6 px-4" style={{ height: 42 }}>
        <div className="flex items-center gap-2">
          <IconList size={16} color="rgb(102,112,133)" />
          <span style={{ fontSize: 14, fontWeight: 400, color: 'rgb(102,112,133)' }}>
            Open opportunities
          </span>
        </div>
        <div className="flex items-center gap-2" title="Saved views are not implemented in v1">
          <IconPlus size={16} color="rgb(102,112,133)" />
          <span style={{ fontSize: 14, fontWeight: 400, color: 'rgb(102,112,133)' }}>
            List
          </span>
        </div>
      </div>

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
                <tr key={o.id} onClick={() => setOpenOpp(o.id)} className="cursor-pointer hover:bg-[rgb(249,250,251)]">
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
