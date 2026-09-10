import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import {
  createPipeline,
  createStage,
  deletePipeline,
  deleteStage,
  listPipelines,
  money,
  renamePipeline,
  renameStage,
  reorderStages,
  type Pipeline,
} from '../lib/api'
import type { Me } from '../lib/auth'

/**
 * The Opportunities > Pipelines tab: create a pipeline, add a stage, rename a
 * stage, reorder the columns, delete what is empty.
 *
 * OUR design. GHL's own pipeline settings screen was never opened on the live
 * account, and the safety contract forbade touching pipelines there
 * (DECISIONS.md, 2026-09-10). The measured thing is the BOARD, and this tab does
 * not restyle it.
 *
 * This edits the structure real customer deals are filed in, so the screen is
 * built around what the server already refuses rather than around a form:
 *
 *   * Delete is DISABLED, with a title naming what is in the way, whenever a
 *     stage holds opportunities or a pipeline still has stages. The server
 *     answers 409 either way — the disabled state is so the admin knows before
 *     clicking, not instead of the check.
 *   * Reorder sends the WHOLE stage list as a permutation, so a board that
 *     changed underneath is refused rather than half-applied. Arrows, not drag:
 *     dragging on this screen would read as dragging a deal.
 *   * Every control is dead for a role that may not manage pipelines, with a
 *     title saying so — never a form that 403s on submit (d1f7c50, b943f4b).
 *
 * Renaming does NOT make names unique. Two stages of the measured pipeline are
 * both called "Call Back" and the `ghl` CLI exits 5 rather than guess between
 * them; the screen says so rather than tidying it away.
 */
export function PipelinesPanel({ user }: { user: Me }) {
  const qc = useQueryClient()
  const pipelines = useQuery({ queryKey: ['pipelines'], queryFn: listPipelines })
  const [chosen, setChosen] = useState<number | null>(null)
  const [newPipeline, setNewPipeline] = useState('')
  const [newStage, setNewStage] = useState('')
  const [editing, setEditing] = useState<number | null>(null)
  const [draft, setDraft] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [confirming, setConfirming] = useState<string | null>(null)

  // Every endpoint on this screen is auth.ADMIN.
  const canManage = user.role === 'ADMIN'
  const why = 'Only an admin can manage pipelines and stages'

  const pipeline = pipelines.data?.find((p) => p.id === chosen) ?? pipelines.data?.[0]
  const stages = pipeline?.stages ?? []
  const deals = stages.reduce((n, s) => n + s.count, 0)

  const refresh = () => {
    setError(null)
    setEditing(null)
    setConfirming(null)
    // The board, the forecast and the saved-list row all read the stage list.
    qc.invalidateQueries({ queryKey: ['pipelines'] })
    qc.invalidateQueries({ queryKey: ['opportunities'] })
    qc.invalidateQueries({ queryKey: ['forecast'] })
  }
  const failed = (e: Error) => setError(e.message)

  const addPipeline = useMutation({
    mutationFn: () => createPipeline(newPipeline.trim()),
    onSuccess: (p) => { setNewPipeline(''); setChosen(p.id); refresh() },
    onError: failed,
  })
  const addStage = useMutation({
    mutationFn: () => createStage(pipeline!.id, newStage.trim()),
    onSuccess: () => { setNewStage(''); refresh() },
    onError: failed,
  })
  const rename = useMutation({
    mutationFn: (v: { kind: 'stage' | 'pipeline'; id: number }) =>
      v.kind === 'stage'
        ? renameStage(v.id, draft.trim())
        : renamePipeline(v.id, draft.trim()),
    onSuccess: refresh,
    onError: failed,
  })
  const drop = useMutation({
    mutationFn: async (v: { kind: 'stage' | 'pipeline'; id: number }) => {
      // Both refuse with 409 while anything is still in the way; neither takes a
      // `force`. The two responses differ, and nothing here reads them.
      if (v.kind === 'stage') await deleteStage(v.id)
      else await deletePipeline(v.id)
    },
    onSuccess: (_r, v) => { if (v.kind === 'pipeline') setChosen(null); refresh() },
    onError: failed,
  })
  const reorder = useMutation({
    mutationFn: (ids: number[]) => reorderStages(pipeline!.id, ids),
    onSuccess: refresh,
    onError: failed,
  })

  /** Swap two columns and send the whole list, never a single "move" instruction. */
  const swap = (index: number, delta: number) => {
    const ids = stages.map((s) => s.id)
    const to = index + delta
    if (to < 0 || to >= ids.length) return
    ;[ids[index], ids[to]] = [ids[to], ids[index]]
    setError(null)
    reorder.mutate(ids)
  }

  const busy = rename.isPending || drop.isPending || reorder.isPending
    || addStage.isPending || addPipeline.isPending

  const input = {
    height: 34, borderRadius: 6, border: '1px solid rgb(234,236,240)',
    padding: '0 10px', fontSize: 14, backgroundColor: '#fff',
  } as const
  const link = (enabled: boolean, colour = 'rgb(0,78,235)') => ({
    fontSize: 13, fontWeight: 500, color: colour,
    ...(enabled ? {} : { opacity: 0.5, cursor: 'not-allowed' }),
  })

  return (
    <div className="min-h-0 flex-1 overflow-auto px-4 pb-4">
      <div
        className="bg-white"
        style={{
          borderRadius: 8, border: '1px solid rgb(253,176,34)', padding: 12,
          fontSize: 13, color: 'rgb(52,64,84)',
        }}
      >
        This is the structure every opportunity is filed in. A stage or a pipeline
        can only be deleted once it is <strong>empty</strong> — nothing here ever
        deletes a deal. Reordering moves columns and leaves every opportunity in
        the stage it is already in. Two stages may share a name, deliberately.
      </div>

      {error && (
        <div role="alert" className="bg-white" style={{
          marginTop: 12, borderRadius: 8, border: '1px solid rgb(217,45,32)',
          padding: 12, fontSize: 13, color: 'rgb(180,35,24)',
        }}>
          {error}
        </div>
      )}

      <div className="mt-4 grid gap-4"
        style={{ gridTemplateColumns: 'minmax(240px, 320px) 1fr' }}>
        {/* ---- pipelines ---- */}
        <div className="bg-white" style={{
          borderRadius: 8, border: '1px solid rgb(234,236,240)', padding: 12,
        }}>
          <div style={{ fontSize: 16, fontWeight: 600, color: 'rgb(16,24,40)' }}>
            Pipelines
          </div>
          {pipelines.data?.map((p) => (
            <PipelineRow
              key={p.id}
              p={p}
              active={pipeline?.id === p.id}
              canManage={canManage}
              why={why}
              busy={busy}
              editing={editing === -p.id}
              draft={draft}
              setDraft={setDraft}
              confirming={confirming === 'pipeline:' + p.id}
              onChoose={() => setChosen(p.id)}
              onEdit={() => { setEditing(-p.id); setDraft(p.name); setError(null) }}
              onCancel={() => setEditing(null)}
              onSave={() => rename.mutate({ kind: 'pipeline', id: p.id })}
              onAskDelete={() => { setConfirming('pipeline:' + p.id); setError(null) }}
              onDelete={() => drop.mutate({ kind: 'pipeline', id: p.id })}
            />
          ))}

          <div className="mt-3 flex gap-2">
            <input
              value={newPipeline}
              maxLength={160}
              disabled={!canManage}
              title={canManage ? undefined : why}
              placeholder="New pipeline name"
              onChange={(e) => setNewPipeline(e.target.value)}
              style={{ ...input, flex: 1, ...(canManage ? {} : { opacity: 0.5 }) }}
            />
            <button
              onClick={() => { setError(null); addPipeline.mutate() }}
              disabled={!canManage || !newPipeline.trim() || busy}
              title={canManage ? undefined : why}
              style={{
                height: 34, padding: '0 12px', borderRadius: 6, fontSize: 13,
                fontWeight: 500, color: '#fff', backgroundColor: 'rgb(0,78,235)',
                ...(canManage && newPipeline.trim() && !busy
                  ? {} : { opacity: 0.5, cursor: 'not-allowed' }),
              }}
            >
              Add
            </button>
          </div>
          <div style={{ fontSize: 12, color: 'rgb(152,162,179)', marginTop: 6 }}>
            A new pipeline starts with no stages. Nothing is guessed for you.
          </div>
        </div>

        {/* ---- stages of the chosen pipeline ---- */}
        <div className="bg-white" style={{
          borderRadius: 8, border: '1px solid rgb(234,236,240)', padding: 12,
        }}>
          <div style={{ fontSize: 16, fontWeight: 600, color: 'rgb(16,24,40)' }}>
            {pipeline ? pipeline.name : 'No pipeline'} — stages
          </div>
          <div style={{ fontSize: 12, color: 'rgb(102,112,133)', marginTop: 2 }}>
            {stages.length} stage{stages.length === 1 ? '' : 's'} · {deals}{' '}
            opportunit{deals === 1 ? 'y' : 'ies'}
          </div>

          <table className="mt-3 w-full">
            <thead>
              <tr>
                {['Stage', 'Opportunities', 'Value', 'Order', ''].map((h) => (
                  <th key={h} className="text-left" style={{
                    height: 36, fontSize: 12, fontWeight: 700,
                    color: 'rgb(71,84,103)',
                    borderBottom: '1px solid rgb(234,236,240)',
                  }}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {stages.map((s, i) => {
                // The server refuses this with 409 and names the count; the
                // disabled state is so the admin knows before clicking.
                const removable = s.count === 0
                const blocked = `“${s.name}” holds ${s.count} opportunit${
                  s.count === 1 ? 'y' : 'ies'}. Move ${
                  s.count === 1 ? 'it' : 'them'} to another stage first.`
                return (
                  <tr key={s.id}>
                    <td style={{ padding: '8px 0', fontSize: 14,
                      borderBottom: '1px solid rgb(242,244,247)' }}>
                      {editing === s.id ? (
                        <div className="flex items-center gap-2">
                          <input
                            autoFocus
                            value={draft}
                            maxLength={160}
                            onChange={(e) => setDraft(e.target.value)}
                            onKeyDown={(e) => {
                              if (e.key === 'Escape') setEditing(null)
                              if (e.key === 'Enter' && draft.trim()) {
                                rename.mutate({ kind: 'stage', id: s.id })
                              }
                            }}
                            style={{ ...input, height: 30, width: 200 }}
                          />
                          <button
                            onClick={() => rename.mutate({ kind: 'stage', id: s.id })}
                            disabled={!draft.trim() || busy}
                            style={link(!!draft.trim() && !busy)}
                          >
                            Save
                          </button>
                          <button onClick={() => setEditing(null)}
                            style={{ fontSize: 13, color: 'rgb(102,112,133)' }}>
                            Cancel
                          </button>
                        </div>
                      ) : (
                        <span style={{ color: 'rgb(52,64,84)' }}>{s.name}</span>
                      )}
                    </td>
                    <td style={{ fontSize: 14, color: 'rgb(52,64,84)',
                      borderBottom: '1px solid rgb(242,244,247)' }}>
                      {s.count}
                    </td>
                    <td style={{ fontSize: 14, color: 'rgb(52,64,84)',
                      borderBottom: '1px solid rgb(242,244,247)' }}>
                      {money(s.value_cents)}
                    </td>
                    <td style={{ borderBottom: '1px solid rgb(242,244,247)' }}>
                      <button
                        onClick={() => swap(i, -1)}
                        disabled={!canManage || i === 0 || busy}
                        aria-label={'Move ' + s.name + ' left'}
                        title={canManage ? 'Move left' : why}
                        style={link(canManage && i > 0 && !busy, 'rgb(102,112,133)')}
                      >
                        ↑
                      </button>
                      <button
                        onClick={() => swap(i, 1)}
                        disabled={!canManage || i === stages.length - 1 || busy}
                        aria-label={'Move ' + s.name + ' right'}
                        title={canManage ? 'Move right' : why}
                        style={{
                          ...link(canManage && i < stages.length - 1 && !busy,
                            'rgb(102,112,133)'),
                          marginLeft: 8,
                        }}
                      >
                        ↓
                      </button>
                    </td>
                    <td className="text-right"
                      style={{ borderBottom: '1px solid rgb(242,244,247)' }}>
                      <button
                        onClick={() => {
                          setEditing(s.id); setDraft(s.name); setError(null)
                        }}
                        disabled={!canManage || busy}
                        title={canManage ? undefined : why}
                        style={link(canManage && !busy)}
                      >
                        Rename
                      </button>
                      {confirming === 'stage:' + s.id ? (
                        <button
                          onClick={() => drop.mutate({ kind: 'stage', id: s.id })}
                          disabled={busy}
                          style={{ ...link(!busy, 'rgb(180,35,24)'), marginLeft: 10,
                            fontWeight: 600 }}
                        >
                          Really delete
                        </button>
                      ) : (
                        <button
                          onClick={() => {
                            setConfirming('stage:' + s.id); setError(null)
                          }}
                          disabled={!canManage || !removable || busy}
                          title={!canManage ? why : removable
                            ? 'Delete this empty stage' : blocked}
                          style={{
                            ...link(canManage && removable && !busy, 'rgb(180,35,24)'),
                            marginLeft: 10,
                          }}
                        >
                          Delete
                        </button>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>

          <div className="mt-3 flex gap-2">
            <input
              value={newStage}
              maxLength={160}
              disabled={!canManage || !pipeline}
              title={canManage ? undefined : why}
              placeholder="New stage name"
              onChange={(e) => setNewStage(e.target.value)}
              style={{ ...input, flex: 1, ...(canManage ? {} : { opacity: 0.5 }) }}
            />
            <button
              onClick={() => { setError(null); addStage.mutate() }}
              disabled={!canManage || !pipeline || !newStage.trim() || busy}
              title={canManage ? undefined : why}
              style={{
                height: 34, padding: '0 12px', borderRadius: 6, fontSize: 13,
                fontWeight: 500, color: '#fff', backgroundColor: 'rgb(0,78,235)',
                ...(canManage && pipeline && newStage.trim() && !busy
                  ? {} : { opacity: 0.5, cursor: 'not-allowed' }),
              }}
            >
              Add stage
            </button>
          </div>
          <div style={{ fontSize: 12, color: 'rgb(152,162,179)', marginTop: 6 }}>
            Added at the end of the board. A new stage is empty, so nothing moves.
          </div>
        </div>
      </div>
    </div>
  )
}

function PipelineRow({
  p, active, canManage, why, busy, editing, draft, setDraft, confirming,
  onChoose, onEdit, onCancel, onSave, onAskDelete, onDelete,
}: {
  p: Pipeline
  active: boolean
  canManage: boolean
  why: string
  busy: boolean
  editing: boolean
  draft: string
  setDraft: (v: string) => void
  confirming: boolean
  onChoose: () => void
  onEdit: () => void
  onCancel: () => void
  onSave: () => void
  onAskDelete: () => void
  onDelete: () => void
}) {
  const deals = p.stages.reduce((n, s) => n + s.count, 0)
  // Empty means EMPTY: the columns are somebody's configuration too, and
  // `Pipeline.stages` cascades delete-orphan, so this is the guard that stands
  // between a mis-click and every deal on the board.
  const removable = p.stages.length === 0 && deals === 0
  const blocked = `“${p.name}” still has ${p.stages.length} stage${
    p.stages.length === 1 ? '' : 's'} and ${deals} opportunit${
    deals === 1 ? 'y' : 'ies'}. Empty it first.`

  return (
    <div
      className="mt-2 flex items-center gap-2"
      style={{
        borderRadius: 6, padding: 8,
        backgroundColor: active ? 'rgb(239,244,255)' : 'transparent',
      }}
    >
      {editing ? (
        <>
          <input
            autoFocus
            value={draft}
            maxLength={160}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Escape') onCancel()
              if (e.key === 'Enter' && draft.trim()) onSave()
            }}
            style={{
              flex: 1, height: 30, borderRadius: 6, fontSize: 14,
              border: '1px solid rgb(234,236,240)', padding: '0 10px',
            }}
          />
          <button onClick={onSave} disabled={!draft.trim() || busy}
            style={{ fontSize: 13, fontWeight: 500, color: 'rgb(0,78,235)' }}>
            Save
          </button>
          <button onClick={onCancel}
            style={{ fontSize: 13, color: 'rgb(102,112,133)' }}>
            Cancel
          </button>
        </>
      ) : (
        <>
          <button onClick={onChoose} className="min-w-0 flex-1 text-left">
            <div className="truncate" style={{
              fontSize: 14, fontWeight: active ? 600 : 400,
              color: active ? 'rgb(0,78,235)' : 'rgb(52,64,84)',
            }}>
              {p.name}
            </div>
            <div style={{ fontSize: 12, color: 'rgb(152,162,179)' }}>
              {p.stages.length} stage{p.stages.length === 1 ? '' : 's'} · {deals}{' '}
              opportunit{deals === 1 ? 'y' : 'ies'}
            </div>
          </button>
          <button
            onClick={onEdit}
            disabled={!canManage || busy}
            title={canManage ? undefined : why}
            style={{
              fontSize: 13, fontWeight: 500, color: 'rgb(0,78,235)',
              ...(canManage && !busy ? {} : { opacity: 0.5, cursor: 'not-allowed' }),
            }}
          >
            Rename
          </button>
          {confirming ? (
            <button onClick={onDelete} disabled={busy}
              style={{ fontSize: 13, fontWeight: 600, color: 'rgb(180,35,24)' }}>
              Really delete
            </button>
          ) : (
            <button
              onClick={onAskDelete}
              disabled={!canManage || !removable || busy}
              title={!canManage ? why
                : removable ? 'Delete this empty pipeline' : blocked}
              style={{
                fontSize: 13, color: 'rgb(180,35,24)',
                ...(canManage && removable && !busy
                  ? {} : { opacity: 0.5, cursor: 'not-allowed' }),
              }}
            >
              Delete
            </button>
          )}
        </>
      )}
    </div>
  )
}
