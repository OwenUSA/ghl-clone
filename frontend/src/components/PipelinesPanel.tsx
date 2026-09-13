import {
  DndContext,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
  type DragEndEvent,
} from '@dnd-kit/core'
import { SortableContext, useSortable, verticalListSortingStrategy } from '@dnd-kit/sortable'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import {
  deletePipelineMovingDeals,
  duplicatePipeline,
  getPipelinePermissions,
  listPipelineRows,
  listUsers,
  reorderPipelines,
  setPipelinePermissions,
} from '../lib/api'
import type { Me } from '../lib/auth'
import {
  orderAfterDrag,
  orderForPosition,
  paginate,
  pipelineLink,
  searchPipelines,
  updatedOn,
  type PipelineRow,
} from '../lib/pipelines'
import { PipelineModal } from './PipelineModal'
import {
  IconActions,
  IconCalendarSmall,
  IconDuplicate,
  IconGrip,
  IconHash,
  IconKebab,
  IconKey,
  IconLink,
  IconPencil,
  IconPlus,
  IconSearchSmall,
  IconTextBox,
  IconTrashOutline,
} from './PipelineIcons'

const INK = 'rgb(16,24,40)'
const TEXT = 'rgb(52,64,84)'
const MUTED = 'rgb(102,112,133)'
const LINE = 'rgb(234,236,240)'
const BLUE = 'rgb(21,94,239)'

const ROWS_PER_PAGE = [10, 20, 50]

type Dialog =
  | { kind: 'create' }
  | { kind: 'edit'; pipeline: PipelineRow }
  | { kind: 'permissions'; pipeline: PipelineRow }
  | { kind: 'position'; pipeline: PipelineRow }
  | { kind: 'delete'; pipeline: PipelineRow }

/**
 * Opportunities > Pipelines — GoHighLevel's screen (refs/opps/02 and 03).
 *
 * "Pipelines" and its subtitle, + Create pipeline, a search box, and a table:
 * drag handle · # · Pipeline name · Total stages · Updated on · Actions ⋮, then
 * rows per page and pagination. Dragging a row reorders the pipelines, and the
 * board's pipeline selector follows that order.
 *
 * The ⋮ menu is all real: Edit (the modal, populated) · Duplicate · Manage
 * permissions · Copy link (a URL that opens the board on the pipeline, and works
 * on a fresh page load) · Move to position · Delete (moves the deals somewhere
 * first; never deletes one).
 *
 * Roles, which the server enforces and this screen mirrors so nothing 403s on
 * submit: DISPATCHER creates, edits, duplicates and reorders; ADMIN alone deletes
 * and manages permissions; TECH reads. A disabled control says why in its title.
 */
export function PipelinesPanel({ user }: { user: Me }) {
  const qc = useQueryClient()
  const pipelines = useQuery({ queryKey: ['pipelines'], queryFn: listPipelineRows })
  const [q, setQ] = useState('')
  const [page, setPage] = useState(1)
  const [perPage, setPerPage] = useState(20)
  const [menuFor, setMenuFor] = useState<number | null>(null)
  const [dialog, setDialog] = useState<Dialog | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const canEdit = user.role !== 'TECH'
  const canAdmin = user.role === 'ADMIN'
  const noEdit = 'Your role cannot change pipelines'
  const noAdmin = 'Only an admin can do this'

  const all = pipelines.data ?? []
  const ids = all.map((p) => p.id)
  const found = searchPipelines(all, q)
  const slice = paginate(found, page, perPage)

  const refresh = () => {
    setError(null)
    for (const key of ['pipelines', 'opportunities', 'forecast', 'dashboard-funnel', 'saved-views']) {
      qc.invalidateQueries({ queryKey: [key] })
    }
  }
  const failed = (e: Error) => setError(e.message)

  const reorder = useMutation({
    mutationFn: reorderPipelines,
    onMutate: async (order: number[]) => {
      // The row moves the instant it is dropped, and snaps back if refused.
      await qc.cancelQueries({ queryKey: ['pipelines'] })
      const previous = qc.getQueryData<PipelineRow[]>(['pipelines'])
      if (previous) {
        const byId = new Map(previous.map((p) => [p.id, p]))
        qc.setQueryData(['pipelines'], order.map((id) => byId.get(id)).filter(Boolean))
      }
      return { previous }
    },
    onError: (e: Error, _v, ctx) => {
      if (ctx?.previous) qc.setQueryData(['pipelines'], ctx.previous)
      failed(e)
    },
    onSettled: refresh,
  })

  const duplicate = useMutation({
    mutationFn: duplicatePipeline,
    onSuccess: (p) => { refresh(); setNotice(`Created “${p.name}”.`) },
    onError: failed,
  })

  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 4 } }))
  const onDragEnd = (e: DragEndEvent) => {
    const order = orderAfterDrag(ids, Number(e.active.id), e.over ? Number(e.over.id) : null)
    if (order) reorder.mutate(order)
  }

  // A menu closes on Escape, like the board's ⋯ menu.
  useEffect(() => {
    if (menuFor == null) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setMenuFor(null) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [menuFor])

  const copyLink = async (p: PipelineRow) => {
    const url = pipelineLink(window.location.origin, p.id)
    try {
      await navigator.clipboard.writeText(url)
      setNotice(`Link to “${p.name}” copied.`)
    } catch {
      // No clipboard (an insecure origin, a denied permission): hand it over to copy by hand.
      window.prompt('Copy this link', url)
    }
  }

  return (
    <div className="min-h-0 flex-1 overflow-auto" style={{ padding: '18px 13px 16px' }}>
      <div className="flex items-start">
        <div>
          <div style={{ fontSize: 20, fontWeight: 600, color: INK, lineHeight: '28px' }}>Pipelines</div>
          <div style={{ fontSize: 12, color: 'rgb(71,84,103)', marginTop: 6 }}>
            Use pipelines to track opportunities and sales progress across stages.
          </div>
        </div>
        <button onClick={() => setDialog({ kind: 'create' })} disabled={!canEdit}
          title={canEdit ? undefined : noEdit}
          className="ml-auto flex items-center gap-2"
          style={{ height: 30, marginTop: 10, padding: '0 10px', borderRadius: 4, fontSize: 13,
            fontWeight: 500, color: '#fff', backgroundColor: BLUE,
            ...(canEdit ? {} : { opacity: 0.5, cursor: 'not-allowed' }) }}>
          <IconPlus size={14} color="#fff" /> Create pipeline
        </button>
      </div>

      {(error || notice) && (
        <div role={error ? 'alert' : 'status'} className="flex items-start gap-3"
          style={{ marginTop: 12, fontSize: 13, borderRadius: 8, padding: '8px 12px',
            color: error ? 'rgb(180,35,24)' : 'rgb(2,122,72)',
            backgroundColor: error ? 'rgb(254,243,242)' : 'rgb(236,253,243)',
            border: `1px solid ${error ? 'rgb(253,162,155)' : 'rgb(166,244,197)'}` }}>
          <span className="flex-1">{error ?? notice}</span>
          <button aria-label="Dismiss" onClick={() => { setError(null); setNotice(null) }}>✕</button>
        </div>
      )}

      <div className="bg-white" style={{ marginTop: 12, border: `1px solid ${LINE}`, borderRadius: 4 }}>
        <div className="flex items-center justify-end" style={{ height: 41, padding: '0 8px',
          borderBottom: `1px solid ${LINE}` }}>
          <label className="flex items-center gap-1" style={{ height: 26, width: 108, padding: '0 6px',
            border: '1px solid rgb(208,213,221)', borderRadius: 4 }}>
            <IconSearchSmall size={12} color={MUTED} />
            <input value={q} placeholder="Search" aria-label="Search pipelines"
              onChange={(e) => { setQ(e.target.value); setPage(1) }}
              style={{ width: '100%', fontSize: 11, outline: 'none', color: INK }} />
          </label>
        </div>

        <div style={{ overflowX: 'auto' }}>
          <table className="w-full" style={{ borderCollapse: 'collapse', minWidth: 760 }}>
            <thead>
              <tr style={{ height: 36, backgroundColor: 'rgb(249,250,251)' }}>
                <Th width={80} />
                <Th width={106} center>#</Th>
                <Th><IconTextBox size={14} color={MUTED} />Pipeline name</Th>
                <Th width={260}><IconHash size={13} color={MUTED} />Total stages</Th>
                <Th width={446}><IconCalendarSmall size={14} color={MUTED} />Updated on</Th>
                <Th width={137}><IconActions size={14} color={MUTED} />Actions</Th>
              </tr>
            </thead>
            <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd}>
              <SortableContext items={slice.rows.map((p) => p.id)} strategy={verticalListSortingStrategy}>
                <tbody>
                  {slice.rows.map((p) => (
                    <PipelineTableRow key={p.id} p={p} index={ids.indexOf(p.id) + 1}
                      canDrag={canEdit && !reorder.isPending} dragBlocked={canEdit ? undefined : noEdit}
                      menuOpen={menuFor === p.id}
                      onMenu={() => setMenuFor((m) => (m === p.id ? null : p.id))}
                      onCloseMenu={() => setMenuFor(null)}
                      actions={[
                        { label: 'Edit', icon: <IconPencil size={16} color={TEXT} />,
                          blocked: canEdit ? null : noEdit,
                          run: () => setDialog({ kind: 'edit', pipeline: p }) },
                        { label: 'Duplicate', icon: <IconDuplicate size={16} color={TEXT} />,
                          blocked: canEdit ? null : noEdit, run: () => duplicate.mutate(p.id) },
                        { label: 'Manage permissions', icon: <IconKey size={16} color={TEXT} />,
                          blocked: canAdmin ? null : noAdmin,
                          run: () => setDialog({ kind: 'permissions', pipeline: p }) },
                        { label: 'Copy link', icon: <IconLink size={16} color={TEXT} />,
                          blocked: null, run: () => { void copyLink(p) } },
                        { label: 'Move to position', icon: <IconGrip size={16} color={TEXT} />,
                          blocked: canEdit ? null : noEdit,
                          run: () => setDialog({ kind: 'position', pipeline: p }) },
                        { label: 'Delete', icon: <IconTrashOutline size={16} color={TEXT} />,
                          blocked: canAdmin ? null : noAdmin,
                          run: () => setDialog({ kind: 'delete', pipeline: p }) },
                      ]} />
                  ))}
                  {pipelines.isSuccess && slice.rows.length === 0 && (
                    <tr>
                      <td colSpan={6} style={{ padding: 24, textAlign: 'center', fontSize: 13, color: MUTED }}>
                        {q.trim() ? `No pipelines match “${q.trim()}”.` : 'No pipelines yet.'}
                      </td>
                    </tr>
                  )}
                  {pipelines.isError && (
                    <tr>
                      <td colSpan={6} role="alert" style={{ padding: 24, fontSize: 13, color: 'rgb(180,35,24)' }}>
                        {(pipelines.error as Error).message}
                      </td>
                    </tr>
                  )}
                </tbody>
              </SortableContext>
            </DndContext>
          </table>
        </div>

        <div className="flex flex-wrap items-center justify-end gap-2"
          style={{ minHeight: 44, padding: '6px 8px', fontSize: 11, color: INK }}>
          <label className="flex items-center gap-1">
            Rows per page
            <select value={perPage} aria-label="Rows per page"
              onChange={(e) => { setPerPage(Number(e.target.value)); setPage(1) }}
              style={{ height: 22, fontSize: 11, border: '1px solid rgb(208,213,221)', borderRadius: 4,
                padding: '0 2px', backgroundColor: '#fff', marginLeft: 4 }}>
              {ROWS_PER_PAGE.map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
          </label>
          <span>{slice.from} - {slice.to} of {slice.total}</span>
          <PageButton label="Previous" disabled={slice.page <= 1} onClick={() => setPage(slice.page - 1)} />
          {Array.from({ length: slice.pages }, (_, i) => i + 1).map((n) => (
            <button key={n} onClick={() => setPage(n)} aria-current={n === slice.page ? 'page' : undefined}
              style={{ minWidth: 22, height: 22, borderRadius: 4, fontSize: 11,
                border: `1px solid ${n === slice.page ? BLUE : 'transparent'}`, color: INK }}>
              {n}
            </button>
          ))}
          <PageButton label="Next" disabled={slice.page >= slice.pages} onClick={() => setPage(slice.page + 1)} />
          <span>Page {slice.page} of {slice.pages}</span>
        </div>
      </div>

      {(dialog?.kind === 'create' || dialog?.kind === 'edit') && (
        <PipelineModal user={user}
          pipeline={dialog.kind === 'edit' ? dialog.pipeline : null}
          otherNames={all.filter((p) => dialog.kind !== 'edit' || p.id !== dialog.pipeline.id)
            .map((p) => p.name)}
          onClose={() => setDialog(null)}
          onSaved={(p) => {
            setDialog(null)
            setNotice(dialog.kind === 'edit' ? `Updated “${p.name}”.` : `Created “${p.name}”.`)
            refresh()
          }} />
      )}
      {dialog?.kind === 'permissions' && (
        <PermissionsDialog pipeline={dialog.pipeline} onClose={() => setDialog(null)}
          onSaved={(msg) => { setDialog(null); setNotice(msg); refresh() }} />
      )}
      {dialog?.kind === 'position' && (
        <MoveToPositionDialog pipeline={dialog.pipeline} ids={ids} onClose={() => setDialog(null)}
          onMove={(order) => { setDialog(null); if (order) reorder.mutate(order) }} />
      )}
      {dialog?.kind === 'delete' && (
        <DeletePipelineDialog pipeline={dialog.pipeline} pipelines={all} onClose={() => setDialog(null)}
          onDeleted={(msg) => { setDialog(null); setNotice(msg); refresh() }} />
      )}
    </div>
  )
}

function Th({ children, width, center }: { children?: React.ReactNode; width?: number; center?: boolean }) {
  return (
    <th style={{ width, padding: '0 12px', fontSize: 12, fontWeight: 500, color: INK,
      textAlign: center ? 'center' : 'left', borderRight: `1px solid ${LINE}`,
      borderBottom: `1px solid ${LINE}`, whiteSpace: 'nowrap' }}>
      <span className="inline-flex items-center gap-2">{children}</span>
    </th>
  )
}

function PageButton({ label, disabled, onClick }: { label: string; disabled: boolean; onClick: () => void }) {
  return (
    <button onClick={onClick} disabled={disabled}
      style={{ height: 22, padding: '0 6px', borderRadius: 4, fontSize: 10,
        border: `1px solid ${LINE}`, color: disabled ? 'rgb(208,213,221)' : TEXT,
        cursor: disabled ? 'not-allowed' : 'pointer' }}>
      {label}
    </button>
  )
}

type Action = { label: string; icon: React.ReactNode; blocked: string | null; run: () => void }

function PipelineTableRow({ p, index, canDrag, dragBlocked, menuOpen, onMenu, onCloseMenu, actions }: {
  p: PipelineRow
  /** 1-based rank among every pipeline the user can see, not just this page. */
  index: number
  canDrag: boolean
  dragBlocked?: string
  menuOpen: boolean
  onMenu: () => void
  onCloseMenu: () => void
  actions: Action[]
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id: p.id, disabled: !canDrag })
  const when = updatedOn(p.updated_at)
  // The menu is drawn `position: fixed` at the button, so the table's horizontal
  // scroll container cannot clip it and the rows below cannot paint over it.
  const [anchor, setAnchor] = useState<{ top: number; right: number } | null>(null)
  const cell = { fontSize: 12, color: INK, padding: '0 12px' } as const
  return (
    <tr ref={setNodeRef} style={{
      height: 36, backgroundColor: isDragging ? 'rgb(249,250,251)' : '#fff',
      transform: transform ? `translate3d(0, ${transform.y}px, 0)` : undefined, transition,
      position: 'relative', zIndex: isDragging ? 2 : undefined,
      boxShadow: isDragging ? '0 4px 8px rgba(16,24,40,0.12)' : undefined,
    }}>
      <td style={{ ...cell, textAlign: 'center' }}>
        <span {...attributes} {...listeners} aria-label={`Drag ${p.name} to reorder`} title={dragBlocked}
          style={{ display: 'inline-flex', cursor: canDrag ? 'grab' : 'not-allowed', touchAction: 'none' }}>
          <IconGrip size={16} />
        </span>
      </td>
      <td style={{ ...cell, textAlign: 'center' }}>{index}</td>
      <td style={cell}>{p.name}</td>
      <td style={{ ...cell, textAlign: 'right' }}>{p.stages.length}</td>
      <td style={cell}>
        {when
          ? <>{when.date} <span style={{ color: MUTED }}>/ {when.time}</span></>
          : <span style={{ color: MUTED }} title="Not changed since change dates began to be recorded">—</span>}
      </td>
      <td style={{ ...cell, textAlign: 'center', position: 'relative' }}>
        <button onClick={(e) => {
          const r = e.currentTarget.getBoundingClientRect()
          setAnchor({ top: r.bottom + 4, right: window.innerWidth - r.right })
          onMenu()
        }} aria-haspopup="menu" aria-expanded={menuOpen}
          aria-label={`Actions for ${p.name}`}
          style={{ width: 22, height: 22, borderRadius: 4, display: 'inline-flex', alignItems: 'center',
            justifyContent: 'center', backgroundColor: menuOpen ? 'rgb(242,244,247)' : 'transparent' }}>
          <IconKebab size={14} />
        </button>
        {menuOpen && anchor && (
          <>
            <div className="fixed inset-0 z-30" onClick={onCloseMenu} />
            <div role="menu" className="fixed z-30 bg-white text-left"
              style={{ right: Math.max(8, anchor.right - 4), top: anchor.top, width: 178, padding: 4, borderRadius: 6,
                border: `1px solid ${LINE}`, boxShadow: '0 12px 16px -4px rgba(16,24,40,0.1)' }}>
              {actions.map((a) => (
                <button key={a.label} role="menuitem" disabled={!!a.blocked} title={a.blocked ?? undefined}
                  onClick={() => { onCloseMenu(); a.run() }}
                  className="flex w-full items-center gap-2 hover:bg-[rgb(249,250,251)]"
                  style={{ height: 25, padding: '0 6px', borderRadius: 4, fontSize: 12, color: TEXT,
                    opacity: a.blocked ? 0.45 : 1, cursor: a.blocked ? 'not-allowed' : 'pointer' }}>
                  {a.icon}{a.label}
                </button>
              ))}
            </div>
          </>
        )}
      </td>
    </tr>
  )
}

function DialogShell({ title, children, footer, onClose, width = 440 }: {
  title: string; children: React.ReactNode; footer: React.ReactNode; onClose: () => void; width?: number
}) {
  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center"
      style={{ backgroundColor: 'rgba(52,64,84,0.6)' }} onClick={onClose}>
      <div role="dialog" aria-modal="true" aria-label={title} onClick={(e) => e.stopPropagation()}
        className="bg-white" style={{ width, maxWidth: 'calc(100vw - 32px)', borderRadius: 8, padding: 20 }}>
        <div style={{ fontSize: 16, fontWeight: 600, color: INK }}>{title}</div>
        {children}
        <div className="mt-5 flex justify-end gap-2">{footer}</div>
      </div>
    </div>
  )
}

const secondary = { height: 36, padding: '0 14px', borderRadius: 6, border: '1px solid rgb(208,213,221)',
  fontSize: 14, fontWeight: 600, color: TEXT, backgroundColor: '#fff' } as const
const primary = (enabled: boolean, danger = false) => ({
  height: 36, padding: '0 14px', borderRadius: 6, fontSize: 14, fontWeight: 600, color: '#fff',
  backgroundColor: danger ? 'rgb(217,45,32)' : BLUE, opacity: enabled ? 1 : 0.5,
  cursor: enabled ? 'pointer' : 'not-allowed',
}) as const

/**
 * Manage permissions: which users can access this pipeline. Nobody ticked means
 * everyone can. Admins always can, so they are shown ticked and fixed. A user
 * without access sees neither the pipeline nor any deal in it, anywhere.
 */
function PermissionsDialog({ pipeline, onClose, onSaved }: {
  pipeline: PipelineRow; onClose: () => void; onSaved: (msg: string) => void
}) {
  const users = useQuery({ queryKey: ['users'], queryFn: listUsers })
  const current = useQuery({ queryKey: ['pipeline-permissions', pipeline.id],
    queryFn: () => getPipelinePermissions(pipeline.id) })
  const [chosen, setChosen] = useState<Set<number> | null>(null)
  const [error, setError] = useState<string | null>(null)
  const picked = chosen ?? new Set(current.data?.user_ids ?? [])
  const save = useMutation({
    mutationFn: () => setPipelinePermissions(pipeline.id, [...picked]),
    onSuccess: (r) => onSaved(r.everyone
      ? `Everyone can access “${pipeline.name}”.`
      : `${r.user_ids.length} user${r.user_ids.length === 1 ? '' : 's'} (plus admins) can access “${pipeline.name}”.`),
    onError: (e: Error) => setError(e.message),
  })
  const rows = (users.data ?? []) as (Awaited<ReturnType<typeof listUsers>>[number] & { is_active?: boolean })[]
  return (
    <DialogShell title="Manage permissions" onClose={onClose} width={460}
      footer={<>
        <button onClick={onClose} style={secondary}>Cancel</button>
        <button onClick={() => save.mutate()} disabled={!current.isSuccess || save.isPending}
          style={primary(current.isSuccess && !save.isPending)}>
          {save.isPending ? 'Saving…' : 'Save'}
        </button>
      </>}>
      <div style={{ fontSize: 13, color: MUTED, marginTop: 6, lineHeight: 1.5 }}>
        Choose who can access <b style={{ color: TEXT }}>{pipeline.name}</b>. With nobody selected,
        everyone can. Admins always can. Anyone left out will not see this pipeline or any of its
        opportunities anywhere in the CRM; their role still decides what they can do.
      </div>
      <div style={{ marginTop: 12, border: `1px solid ${LINE}`, borderRadius: 8, maxHeight: 280, overflowY: 'auto' }}>
        {rows.map((u) => {
          const admin = u.role === 'ADMIN'
          return (
            <label key={u.id} className="flex items-center gap-3"
              style={{ padding: '9px 12px', borderBottom: `1px solid rgb(242,244,247)`, fontSize: 13,
                color: TEXT, cursor: admin ? 'default' : 'pointer' }}>
              <input type="checkbox" checked={admin || picked.has(u.id)} disabled={admin}
                title={admin ? 'Admins always have access' : undefined}
                onChange={(e) => {
                  const next = new Set(picked)
                  if (e.target.checked) next.add(u.id)
                  else next.delete(u.id)
                  setChosen(next)
                }} />
              <span className="flex-1">{u.name}{u.is_active === false ? ' (deactivated)' : ''}</span>
              <span style={{ fontSize: 12, color: MUTED }}>{admin ? 'Admin · always' : u.role.toLowerCase()}</span>
            </label>
          )
        })}
      </div>
      <div role="status" style={{ fontSize: 12, color: MUTED, marginTop: 8 }}>
        {picked.size === 0 ? 'Everyone can access this pipeline.' : `${picked.size} selected, plus admins.`}
      </div>
      {(error || users.error || current.error) && (
        <div role="alert" style={{ fontSize: 13, color: 'rgb(180,35,24)', marginTop: 8 }}>
          {error ?? ((users.error ?? current.error) as Error).message}
        </div>
      )}
    </DialogShell>
  )
}

function MoveToPositionDialog({ pipeline, ids, onClose, onMove }: {
  pipeline: PipelineRow; ids: number[]; onClose: () => void; onMove: (order: number[] | null) => void
}) {
  const now = ids.indexOf(pipeline.id) + 1
  const [value, setValue] = useState(String(now))
  const n = Number(value)
  const valid = /^\d+$/.test(value) && n >= 1 && n <= ids.length
  return (
    <DialogShell title="Move to position" onClose={onClose}
      footer={<>
        <button onClick={onClose} style={secondary}>Cancel</button>
        <button disabled={!valid} onClick={() => onMove(orderForPosition(ids, pipeline.id, n))}
          style={primary(valid)}>Move</button>
      </>}>
      <label style={{ display: 'block', fontSize: 13, fontWeight: 500, color: TEXT, marginTop: 12 }}>
        New position for “{pipeline.name}” (1–{ids.length})
        <input type="number" min={1} max={ids.length} value={value} autoFocus
          onChange={(e) => setValue(e.target.value)}
          style={{ display: 'block', width: 120, height: 36, marginTop: 6, borderRadius: 6, padding: '0 10px',
            border: `1px solid ${valid ? 'rgb(208,213,221)' : 'rgb(253,162,155)'}`, fontSize: 14 }} />
      </label>
      <div style={{ fontSize: 12, color: MUTED, marginTop: 6 }}>
        Currently #{now}. The board’s pipeline selector follows this order.
      </div>
    </DialogShell>
  )
}

/**
 * Delete a pipeline. Empty: confirm. Holding deals: choose a pipeline AND a stage
 * that receive every one of them first — no opportunity is ever deleted, and the
 * move sends no notification.
 */
function DeletePipelineDialog({ pipeline, pipelines, onClose, onDeleted }: {
  pipeline: PipelineRow; pipelines: PipelineRow[]; onClose: () => void; onDeleted: (msg: string) => void
}) {
  const deals = pipeline.stages.reduce((sum, s) => sum + s.count, 0)
  const others = pipelines.filter((p) => p.id !== pipeline.id && p.stages.length > 0)
  const [toPipeline, setToPipeline] = useState<number | ''>(others[0]?.id ?? '')
  const target = others.find((p) => p.id === toPipeline)
  const [toStage, setToStage] = useState<number | ''>(target?.stages[0]?.id ?? '')
  const [error, setError] = useState<string | null>(null)
  const ready = deals === 0 || (toPipeline !== '' && toStage !== ''
    && !!target?.stages.some((s) => s.id === toStage))
  const del = useMutation({
    mutationFn: () => deletePipelineMovingDeals(pipeline.id, deals ? Number(toStage) : undefined),
    onSuccess: (r) => onDeleted(r.moved_opportunities.length
      ? `Deleted “${pipeline.name}” and moved ${r.moved_opportunities.length} opportunit${
        r.moved_opportunities.length === 1 ? 'y' : 'ies'} to ${target?.name}.`
      : `Deleted “${pipeline.name}”.`),
    onError: (e: Error) => setError(e.message),
  })
  const select = { display: 'block', width: '100%', height: 36, marginTop: 6, borderRadius: 6,
    border: '1px solid rgb(208,213,221)', padding: '0 10px', fontSize: 14, backgroundColor: '#fff' } as const
  return (
    <DialogShell title={`Delete “${pipeline.name}”?`} onClose={onClose} width={460}
      footer={<>
        <button onClick={onClose} style={secondary}>Cancel</button>
        <button disabled={!ready || del.isPending || (deals > 0 && others.length === 0)}
          onClick={() => { setError(null); del.mutate() }}
          style={primary(ready && !del.isPending && !(deals > 0 && others.length === 0), true)}>
          {del.isPending ? 'Deleting…' : 'Delete pipeline'}
        </button>
      </>}>
      {deals === 0 ? (
        <div style={{ fontSize: 13, color: TEXT, marginTop: 8, lineHeight: 1.5 }}>
          It has {pipeline.stages.length} stage{pipeline.stages.length === 1 ? '' : 's'} and no
          opportunities. The pipeline and its stages are removed; saved lists that pointed at it stop
          choosing a pipeline. This cannot be undone.
        </div>
      ) : (
        <>
          <div style={{ fontSize: 13, color: TEXT, marginTop: 8, lineHeight: 1.5 }}>
            It holds {deals} opportunit{deals === 1 ? 'y' : 'ies'}. Choose where
            {deals === 1 ? ' it goes' : ' they go'} — no opportunity is deleted, everything recorded on
            {deals === 1 ? ' it' : ' them'} is kept, and nobody is notified about the move.
          </div>
          {others.length === 0 ? (
            <div role="alert" style={{ fontSize: 13, color: 'rgb(180,35,24)', marginTop: 10 }}>
              There is no other pipeline with a stage to move them to. Create one first.
            </div>
          ) : (
            <>
              <label style={{ display: 'block', fontSize: 13, fontWeight: 500, color: TEXT, marginTop: 12 }}>
                Move opportunities to pipeline
                <select value={toPipeline} style={select} onChange={(e) => {
                  const id = Number(e.target.value)
                  setToPipeline(id)
                  setToStage(others.find((p) => p.id === id)?.stages[0]?.id ?? '')
                }}>
                  {others.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                </select>
              </label>
              <label style={{ display: 'block', fontSize: 13, fontWeight: 500, color: TEXT, marginTop: 10 }}>
                Stage
                <select value={toStage} style={select} onChange={(e) => setToStage(Number(e.target.value))}>
                  {(target?.stages ?? []).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                </select>
              </label>
            </>
          )}
        </>
      )}
      {error && <div role="alert" style={{ fontSize: 13, color: 'rgb(180,35,24)', marginTop: 10 }}>{error}</div>}
    </DialogShell>
  )
}
