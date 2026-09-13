import {
  DndContext,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
  type DragEndEvent,
} from '@dnd-kit/core'
import { SortableContext, useSortable, verticalListSortingStrategy } from '@dnd-kit/sortable'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import {
  createPipelineWithSettings,
  updatePipeline,
} from '../lib/api'
import type { Me } from '../lib/auth'
import {
  STAGE_PALETTE,
  blankStage,
  createBody,
  draftFrom,
  moveItem,
  newDraft,
  removeStage,
  stageHeader,
  updateBody,
  validateDraft,
  type ColorMode,
  type PipelineDraft,
  type PipelineRow,
  type StageDraft,
} from '../lib/pipelines'
import {
  IconChevronDown,
  IconClose,
  IconGrip,
  IconInfo,
  IconPlus,
  IconReportFunnel,
  IconReportPie,
  IconTrashOutline,
} from './PipelineIcons'

const INK = 'rgb(16,24,40)'
const TEXT = 'rgb(52,64,84)'
const MUTED = 'rgb(102,112,133)'
const LINE = 'rgb(208,213,221)'
const SOFT_LINE = 'rgb(234,236,240)'
const BLUE = 'rgb(21,94,239)'

/**
 * Create pipeline / Edit pipeline — GoHighLevel's modal (refs/opps/04).
 *
 * Every control on it is REAL:
 *   * the name is required and unique (case-insensitive; the server has the last
 *     word with a 409),
 *   * "Use opportunity-level probability" decides which probability the Forecast
 *     weights each deal at,
 *   * the three display colour modes decide how the board's stage headers draw,
 *   * each stage row's funnel and pie icons are independent "Show in reports"
 *     switches that the Dashboard's Funnel and Stage distribution cards obey,
 *   * the chevron opens the stage's colour, used by Colored dot and Background tint,
 *   * Probability (%) feeds the Forecast,
 *   * the trash deletes the stage — and a stage holding deals asks which stage of
 *     this pipeline receives them. NO DEAL IS EVER DELETED, and nothing is written
 *     until Update: Cancel discards the whole edit, removals included.
 *
 * Roles: DISPATCHER may create and edit. Deleting a stored stage is ADMIN, so its
 * trash is disabled with the reason rather than letting Update 403.
 */
export function PipelineModal({ user, pipeline, otherNames, onClose, onSaved }: {
  user: Me
  /** null = Create. */
  pipeline: PipelineRow | null
  /** Names of every other pipeline the browser can see, for the uniqueness hint. */
  otherNames: string[]
  onClose: () => void
  onSaved: (p: PipelineRow) => void
}) {
  const qc = useQueryClient()
  const editing = pipeline != null
  const [draft, setDraft] = useState<PipelineDraft>(() =>
    pipeline ? draftFrom(pipeline) : newDraft())
  const [touched, setTouched] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [deleting, setDeleting] = useState<StageDraft | null>(null)
  const [colorFor, setColorFor] = useState<string | null>(null)

  const canDeleteStored = user.role === 'ADMIN'
  const { ok, problems } = validateDraft(draft, otherNames)

  const save = useMutation({
    mutationFn: () => (pipeline
      ? updatePipeline(pipeline.id, updateBody(draft))
      : createPipelineWithSettings(createBody(draft))),
    onSuccess: (p) => {
      // The board, its headers, the forecast and both report cards read this.
      for (const key of ['pipelines', 'opportunities', 'forecast', 'dashboard-funnel']) {
        qc.invalidateQueries({ queryKey: [key] })
      }
      onSaved(p)
    },
    onError: (e: Error) => setError(e.message),
  })

  const set = (patch: Partial<PipelineDraft>) => setDraft((d) => ({ ...d, ...patch }))
  const setStage = (key: string, patch: Partial<StageDraft>) =>
    setDraft((d) => ({ ...d, stages: d.stages.map((s) => (s.key === key ? { ...s, ...patch } : s)) }))

  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 4 } }))
  const onDragEnd = (e: DragEndEvent) => {
    if (!e.over || e.active.id === e.over.id) return
    setDraft((d) => {
      const keys = d.stages.map((s) => s.key)
      return { ...d, stages: moveItem(d.stages, keys.indexOf(String(e.active.id)), keys.indexOf(String(e.over!.id))) }
    })
  }

  const trash = (s: StageDraft) => {
    if (s.id == null) {
      setDraft((d) => removeStage(d, s.key))
      return
    }
    setDeleting(s)
  }

  const input = (invalid: boolean) => ({
    height: 36, width: '100%', borderRadius: 6, fontSize: 14, color: INK,
    padding: '0 12px', outline: 'none',
    border: `1px solid ${invalid ? 'rgb(253,162,155)' : LINE}`,
    boxShadow: '0 1px 2px rgba(16,24,40,0.05)',
  })

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center"
      style={{ backgroundColor: 'rgba(52,64,84,0.6)' }} onClick={onClose}>
      <div role="dialog" aria-modal="true" aria-label={editing ? 'Edit pipeline' : 'Create pipeline'}
        onClick={(e) => e.stopPropagation()}
        className="flex flex-col bg-white"
        style={{ width: 680, maxWidth: 'calc(100vw - 32px)', maxHeight: 'calc(100vh - 32px)',
          borderRadius: 8, boxShadow: '0 20px 24px -4px rgba(16,24,40,0.08)' }}>
        <div className="flex items-center" style={{ padding: '18px 16px 10px' }}>
          <div style={{ fontSize: 16, fontWeight: 600, color: INK }}>
            {editing ? 'Edit pipeline' : 'Create pipeline'}
          </div>
          <button onClick={onClose} aria-label="Close" className="ml-auto" style={{ padding: 4 }}>
            <IconClose size={18} color={TEXT} />
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto" style={{ padding: '4px 16px 16px' }}>
          <label htmlFor="pipeline-name" style={{ fontSize: 13, fontWeight: 500, color: TEXT }}>
            Pipeline name <span style={{ color: 'rgb(217,45,32)' }}>*</span>
          </label>
          <input id="pipeline-name" autoFocus value={draft.name} maxLength={160}
            placeholder="Marketing pipeline"
            onChange={(e) => { set({ name: e.target.value }); setTouched(true) }}
            onBlur={() => setTouched(true)}
            aria-invalid={touched && !!problems.name}
            style={{ ...input(touched && !!problems.name), marginTop: 6 }} />
          <div style={{ fontSize: 12, marginTop: 4,
            color: touched && problems.name ? 'rgb(217,45,32)' : MUTED }}>
            {touched && problems.name
              ? problems.name
              : 'Use a unique, descriptive name so you can find this pipeline later'}
          </div>

          <div className="flex items-center" style={{ marginTop: 10, border: `1px solid ${SOFT_LINE}`,
            borderRadius: 8, padding: '14px 16px' }}>
            <div className="flex-1">
              <div style={{ fontSize: 13, fontWeight: 600, color: INK }}>
                Use opportunity-level probability
              </div>
              <div style={{ fontSize: 12, color: MUTED, marginTop: 2 }}>
                When enabled, each opportunity uses its own probability. When disabled,
                probability is based on the stage.
              </div>
            </div>
            <button role="switch" aria-checked={draft.use_opportunity_probability}
              aria-label="Use opportunity-level probability"
              onClick={() => set({ use_opportunity_probability: !draft.use_opportunity_probability })}
              style={{ width: 36, height: 20, borderRadius: 10, position: 'relative', flexShrink: 0,
                backgroundColor: draft.use_opportunity_probability ? BLUE : 'rgb(234,236,240)',
                transition: 'background-color 120ms' }}>
              <span style={{ position: 'absolute', top: 2, width: 16, height: 16, borderRadius: 8,
                left: draft.use_opportunity_probability ? 18 : 2, backgroundColor: '#fff',
                boxShadow: '0 1px 3px rgba(16,24,40,0.1)', transition: 'left 120ms' }} />
            </button>
          </div>

          <div className="flex flex-wrap items-center gap-3" style={{ marginTop: 10,
            border: `1px solid ${SOFT_LINE}`, borderRadius: 8, padding: '14px 16px' }}>
            <div className="flex-1" style={{ minWidth: 200 }}>
              <div style={{ fontSize: 13, fontWeight: 600, color: INK }}>Set pipeline display colors</div>
              <div style={{ fontSize: 12, color: MUTED, marginTop: 2 }}>
                Choose how stage colors appear across your pipeline views
              </div>
            </div>
            <div role="radiogroup" aria-label="Pipeline display colors" className="flex gap-2">
              {([['none', 'Default (no color)'], ['dot', 'Colored dot'], ['tint', 'Background tint']] as
                [ColorMode, string][]).map(([mode, label]) => {
                const on = draft.color_mode === mode
                const look = stageHeader(mode, STAGE_PALETTE[0], 0)
                return (
                  <button key={mode} role="radio" aria-checked={on} onClick={() => set({ color_mode: mode })}
                    style={{ width: 103, height: 52, borderRadius: 6, textAlign: 'center',
                      border: `1px solid ${on ? BLUE : SOFT_LINE}`,
                      boxShadow: on ? '0 0 0 1px ' + BLUE : undefined }}>
                    <span className="inline-flex items-center gap-1" style={{ fontSize: 11, fontWeight: 600,
                      color: INK, padding: '1px 6px', borderRadius: 4,
                      backgroundColor: mode === 'tint' ? look.background : 'transparent' }}>
                      {mode === 'dot' && (
                        <span style={{ width: 7, height: 7, borderRadius: 4, backgroundColor: BLUE }} />
                      )}
                      Stage name
                    </span>
                    <div style={{ fontSize: 11, marginTop: 3, color: on ? 'rgb(68,76,231)' : TEXT }}>
                      {label}
                    </div>
                  </button>
                )
              })}
            </div>
          </div>

          <div className="flex items-center" style={{ marginTop: 18 }}>
            <div style={{ fontSize: 14, fontWeight: 600, color: INK }}>
              Pipeline stages ({draft.stages.length})
            </div>
            <button className="ml-auto flex items-center gap-1"
              onClick={() => setDraft((d) => ({ ...d, stages: [...d.stages, blankStage(d.stages.length)] }))}
              style={{ fontSize: 13, fontWeight: 500, color: BLUE }}>
              <IconPlus size={15} color={BLUE} /> Add stage
            </button>
          </div>

          <div style={{ marginTop: 8, border: `1px solid ${SOFT_LINE}`, borderRadius: 8,
            padding: '8px 12px', minHeight: 300 }}>
            <div className="flex items-center" style={{ fontSize: 12, fontWeight: 500, color: TEXT,
              height: 26 }}>
              <span style={{ width: 24 }} />
              <span className="flex-1">Stage name</span>
              <span className="flex items-center gap-1" style={{ width: 146, paddingLeft: 10 }}
                title="The funnel icon keeps this stage in the Dashboard Funnel; the pie icon keeps it in Stage distribution">
                Show in reports <IconInfo size={13} color={MUTED} />
              </span>
              <span className="flex items-center gap-1" style={{ width: 116 }}
                title="Weights this stage's open value in the Forecast">
                Probability (%) <IconInfo size={13} color={MUTED} />
              </span>
              <span style={{ width: 28 }} />
            </div>
            {problems.general && (
              <div style={{ fontSize: 12, color: 'rgb(217,45,32)', padding: '8px 0' }}>{problems.general}</div>
            )}
            <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd}>
              <SortableContext items={draft.stages.map((s) => s.key)} strategy={verticalListSortingStrategy}>
                {draft.stages.map((s) => (
                  <StageRowEditor key={s.key} stage={s} mode={draft.color_mode}
                    problem={problems.stages[s.key]}
                    colorOpen={colorFor === s.key}
                    onColorToggle={() => setColorFor((k) => (k === s.key ? null : s.key))}
                    onChange={(patch) => setStage(s.key, patch)}
                    onTrash={() => trash(s)}
                    trashBlocked={s.id != null && !canDeleteStored
                      ? 'Only an admin can delete a stage' : null} />
                ))}
              </SortableContext>
            </DndContext>
          </div>

          {error && (
            <div role="alert" style={{ marginTop: 10, fontSize: 13, color: 'rgb(180,35,24)',
              backgroundColor: 'rgb(254,243,242)', border: '1px solid rgb(253,162,155)',
              borderRadius: 8, padding: '8px 12px' }}>
              {error}
            </div>
          )}
        </div>

        <div className="flex justify-end gap-3" style={{ padding: '14px 16px',
          borderTop: `1px solid ${SOFT_LINE}` }}>
          <button onClick={onClose} style={{ height: 36, padding: '0 14px', borderRadius: 6,
            border: `1px solid ${LINE}`, fontSize: 14, fontWeight: 600, color: TEXT,
            backgroundColor: '#fff' }}>
            Cancel
          </button>
          <button onClick={() => { setError(null); setTouched(true); if (ok) save.mutate() }}
            disabled={!ok || save.isPending}
            title={ok ? undefined : problems.name ?? problems.general
              ?? Object.values(problems.stages)[0]}
            style={{ height: 36, padding: '0 16px', borderRadius: 6, fontSize: 14, fontWeight: 600,
              color: '#fff', backgroundColor: ok && !save.isPending ? BLUE : 'rgb(178,204,255)',
              cursor: ok && !save.isPending ? 'pointer' : 'not-allowed' }}>
            {save.isPending ? (editing ? 'Updating…' : 'Creating…') : editing ? 'Update' : 'Create'}
          </button>
        </div>
      </div>

      {deleting && (
        <DeleteStageDialog stage={deleting} draft={draft}
          onCancel={() => setDeleting(null)}
          onConfirm={(moveTo) => {
            setDraft((d) => removeStage(d, deleting.key, moveTo))
            setDeleting(null)
          }} />
      )}
    </div>
  )
}

function StageRowEditor({ stage, mode, problem, colorOpen, onColorToggle, onChange, onTrash,
  trashBlocked }: {
  stage: StageDraft
  mode: ColorMode
  problem: string | undefined
  colorOpen: boolean
  onColorToggle: () => void
  onChange: (patch: Partial<StageDraft>) => void
  onTrash: () => void
  trashBlocked: string | null
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id: stage.key })
  const toggle = (on: boolean, label: string) => ({
    'aria-pressed': on,
    title: on ? `Shown in ${label}` : `Hidden from ${label}`,
    style: { width: 26, height: 26, borderRadius: 4, display: 'inline-flex', alignItems: 'center',
      justifyContent: 'center', position: 'relative' as const, opacity: on ? 1 : 0.45,
      backgroundColor: on ? 'transparent' : 'rgb(242,244,247)' },
  })
  const slash = <span aria-hidden style={{ position: 'absolute', width: 18, height: 1.5,
    backgroundColor: 'rgb(71,84,103)', transform: 'rotate(-45deg)' }} />
  return (
    <div ref={setNodeRef} style={{
      transform: transform ? `translate3d(0, ${transform.y}px, 0)` : undefined, transition,
      position: 'relative', zIndex: isDragging ? 2 : undefined,
      backgroundColor: '#fff', boxShadow: isDragging ? '0 4px 8px rgba(16,24,40,0.12)' : undefined,
      borderBottom: '1px solid rgb(242,244,247)', padding: '5px 0',
    }}>
      <div className="flex items-center">
        <span {...attributes} {...listeners} aria-label={`Drag ${stage.name || 'stage'} to reorder`}
          style={{ width: 24, cursor: 'grab', touchAction: 'none', display: 'inline-flex' }}>
          <IconGrip size={16} />
        </span>
        <div className="flex flex-1 items-center gap-2">
          {mode !== 'none' && (
            <span aria-hidden style={{ width: 8, height: 8, borderRadius: 4, flexShrink: 0,
              backgroundColor: stage.color }} />
          )}
          <input value={stage.name} maxLength={160} aria-label="Stage name"
            aria-invalid={!!problem}
            onChange={(e) => onChange({ name: e.target.value })}
            style={{ width: '100%', height: 26, borderRadius: 4, fontSize: 13, color: 'rgb(16,24,40)',
              padding: '0 6px', outline: 'none',
              border: `1px solid ${problem ? 'rgb(253,162,155)' : 'rgb(208,213,221)'}` }} />
        </div>
        <div className="relative flex items-center gap-1" style={{ width: 146, paddingLeft: 10 }}>
          <button {...toggle(stage.show_in_funnel, 'funnel reports')}
            onClick={() => onChange({ show_in_funnel: !stage.show_in_funnel })}>
            <IconReportFunnel size={16} color="rgb(52,64,84)" />{!stage.show_in_funnel && slash}
          </button>
          <button {...toggle(stage.show_in_pie, 'pie chart reports')}
            onClick={() => onChange({ show_in_pie: !stage.show_in_pie })}>
            <IconReportPie size={16} color="rgb(52,64,84)" />{!stage.show_in_pie && slash}
          </button>
          <button onClick={onColorToggle} aria-haspopup="dialog" aria-expanded={colorOpen}
            title="Stage color" className="ml-auto flex items-center gap-1" style={{ padding: '4px 6px' }}>
            {mode !== 'none' && (
              <span aria-hidden style={{ width: 10, height: 10, borderRadius: 3, backgroundColor: stage.color }} />
            )}
            <IconChevronDown size={15} color="rgb(71,84,103)" />
          </button>
          {colorOpen && (
            <div role="dialog" aria-label="Stage color" className="absolute right-0 z-10 bg-white"
              style={{ top: 30, width: 176, padding: 10, borderRadius: 8, border: '1px solid rgb(234,236,240)',
                boxShadow: '0 12px 16px -4px rgba(16,24,40,0.08)' }}>
              <div style={{ fontSize: 12, fontWeight: 500, color: 'rgb(52,64,84)', marginBottom: 6 }}>
                Stage color
              </div>
              <div className="flex flex-wrap gap-2">
                {STAGE_PALETTE.map((c) => (
                  <button key={c} aria-label={`Color ${c}`} aria-pressed={stage.color.toUpperCase() === c}
                    onClick={() => { onChange({ color: c }); onColorToggle() }}
                    style={{ width: 22, height: 22, borderRadius: 11, backgroundColor: c,
                      boxShadow: stage.color.toUpperCase() === c ? `0 0 0 2px #fff, 0 0 0 4px ${c}` : undefined }} />
                ))}
              </div>
              <label className="flex items-center gap-2" style={{ fontSize: 12, color: 'rgb(102,112,133)', marginTop: 8 }}>
                Custom
                <input type="color" value={stage.color} aria-label="Custom stage color"
                  onChange={(e) => onChange({ color: e.target.value.toUpperCase() })}
                  style={{ width: 32, height: 22, border: 'none', background: 'none' }} />
              </label>
              {mode === 'none' && (
                <div style={{ fontSize: 11, color: 'rgb(152,162,179)', marginTop: 6 }}>
                  Shown on the board when the pipeline uses Colored dot or Background tint.
                </div>
              )}
            </div>
          )}
        </div>
        <div className="flex items-center" style={{ width: 116 }}>
          <input value={stage.probability} inputMode="numeric" aria-label="Probability (%)"
            aria-invalid={!!problem && stage.name.trim() !== ''}
            placeholder="—"
            onChange={(e) => onChange({ probability: e.target.value.replace(/[^\d]/g, '').slice(0, 3) })}
            style={{ width: 80, height: 26, borderRadius: 4, fontSize: 13, padding: '0 6px', outline: 'none',
              color: 'rgb(16,24,40)', border: '1px solid rgb(208,213,221)' }} />
          <span style={{ fontSize: 13, color: 'rgb(102,112,133)', marginLeft: 6 }}>%</span>
        </div>
        <button onClick={onTrash} disabled={!!trashBlocked}
          aria-label={`Delete stage ${stage.name}`}
          title={trashBlocked ?? (stage.count > 0
            ? `Delete — its ${stage.count} opportunit${stage.count === 1 ? 'y moves' : 'ies move'} to a stage you choose`
            : 'Delete stage')}
          style={{ width: 28, display: 'inline-flex', justifyContent: 'center',
            opacity: trashBlocked ? 0.4 : 1, cursor: trashBlocked ? 'not-allowed' : 'pointer' }}>
          <IconTrashOutline size={17} color="rgb(52,64,84)" />
        </button>
      </div>
      {problem && (
        <div style={{ fontSize: 11, color: 'rgb(217,45,32)', paddingLeft: 24, marginTop: 2 }}>{problem}</div>
      )}
    </div>
  )
}

/**
 * The trash on a stored stage. Empty: confirm. Holding deals: choose the stage of
 * THIS pipeline that receives them. Nothing moves until the modal's Update.
 */
function DeleteStageDialog({ stage, draft, onCancel, onConfirm }: {
  stage: StageDraft
  draft: PipelineDraft
  onCancel: () => void
  onConfirm: (moveTo?: number) => void
}) {
  const targets = draft.stages.filter((s) => s.id != null && s.key !== stage.key)
  const [moveTo, setMoveTo] = useState<number | ''>(targets[0]?.id ?? '')
  const holds = stage.count > 0
  const ready = !holds || moveTo !== ''
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ backgroundColor: 'rgba(52,64,84,0.4)' }}
      onClick={(e) => { e.stopPropagation(); onCancel() }}>
      <div role="alertdialog" aria-label="Delete stage" onClick={(e) => e.stopPropagation()}
        className="bg-white" style={{ width: 420, borderRadius: 8, padding: 20 }}>
        <div style={{ fontSize: 16, fontWeight: 600, color: INK }}>Delete stage “{stage.name}”?</div>
        {holds ? (
          <>
            <div style={{ fontSize: 13, color: TEXT, marginTop: 8, lineHeight: 1.5 }}>
              It holds {stage.count} opportunit{stage.count === 1 ? 'y' : 'ies'}. Choose the stage
              {stage.count === 1 ? ' it moves' : ' they move'} to — no opportunity is deleted, and no
              customer or crew notification is sent for the move.
            </div>
            {targets.length === 0 ? (
              <div role="alert" style={{ fontSize: 13, color: 'rgb(180,35,24)', marginTop: 10 }}>
                Save another stage in this pipeline first — there is nowhere to move them.
              </div>
            ) : (
              <label style={{ display: 'block', fontSize: 13, fontWeight: 500, color: TEXT, marginTop: 12 }}>
                Move opportunities to
                <select value={moveTo} onChange={(e) => setMoveTo(Number(e.target.value))}
                  style={{ display: 'block', width: '100%', height: 36, marginTop: 6, borderRadius: 6,
                    border: `1px solid ${LINE}`, padding: '0 10px', fontSize: 14, backgroundColor: '#fff' }}>
                  {targets.map((t) => <option key={t.key} value={t.id!}>{t.name}</option>)}
                </select>
              </label>
            )}
          </>
        ) : (
          <div style={{ fontSize: 13, color: TEXT, marginTop: 8 }}>
            This stage is empty. It is removed when you click Update.
          </div>
        )}
        <div className="mt-5 flex justify-end gap-2">
          <button onClick={onCancel} style={{ height: 36, padding: '0 14px', borderRadius: 6,
            border: `1px solid ${LINE}`, fontSize: 14, color: TEXT }}>Cancel</button>
          <button disabled={!ready || (holds && targets.length === 0)}
            onClick={() => onConfirm(holds ? Number(moveTo) : undefined)}
            style={{ height: 36, padding: '0 14px', borderRadius: 6, fontSize: 14, fontWeight: 600,
              color: '#fff', backgroundColor: 'rgb(217,45,32)',
              opacity: ready && !(holds && targets.length === 0) ? 1 : 0.5 }}>
            Delete stage
          </button>
        </div>
      </div>
    </div>
  )
}
