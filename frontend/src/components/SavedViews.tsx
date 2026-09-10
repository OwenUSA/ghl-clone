import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import {
  createSavedView,
  deleteSavedView,
  listSavedViews,
  patchSavedView,
} from '../lib/api'
import {
  applyView,
  describeView,
  isDefaultView,
  matchesView,
  type BoardFilters,
} from '../lib/savedViews'
import { IconList, IconPlus } from './Icon'
import type { Me } from '../lib/auth'

/**
 * The `+ List` row above the Opportunities board, and the "Manage smart lists"
 * dialog behind the ⋯ menu.
 *
 * OUR design. Both were measured as labels only — `captures/opportunities/` holds
 * the board, and no saved-list screen was ever opened on the live account
 * (DECISIONS.md, 2026-09-10).
 *
 * A view holds exactly the filters the board has: a pipeline, the status filter
 * and the search term. Nothing speculative — a saved filter for a control that
 * does not exist would be a promise the board cannot keep.
 *
 * "Open opportunities" is the board's own default and is drawn here as a built-in
 * chip rather than a row in the table, so there is nothing to delete and no seed
 * to keep in step with the code.
 */

const CHIP_TEXT = { fontSize: 14, fontWeight: 400 } as const

export function SavedViewsRow({
  user,
  now,
  onApply,
  onManage,
}: {
  user: Me
  now: BoardFilters
  onApply: (filters: BoardFilters) => void
  onManage: () => void
}) {
  const qc = useQueryClient()
  const views = useQuery({ queryKey: ['saved-views'], queryFn: listSavedViews })
  const [naming, setNaming] = useState(false)
  const [name, setName] = useState('')
  const [error, setError] = useState<string | null>(null)

  // `POST /api/saved-views` is STAFF, and a saved view is shared with everyone.
  const canSave = user.role !== 'TECH'

  const create = useMutation({
    mutationFn: () =>
      createSavedView({
        name: name.trim(),
        // The pipeline is saved with the view, so applying it later puts the
        // board back where it was rather than filtering whatever is open.
        pipeline_id: now.pipelineId ?? null,
        status: now.status,
        q: now.q.trim(),
      }),
    onSuccess: () => {
      setNaming(false)
      setName('')
      qc.invalidateQueries({ queryKey: ['saved-views'] })
    },
    onError: (e: Error) => setError(e.message),
  })

  // The built-in default: no saved view is active and the board is unfiltered.
  const isDefault = isDefaultView(now)
  const activeSaved = views.data?.find((v) => matchesView(v, now))

  const chip = (active: boolean) => ({
    ...CHIP_TEXT,
    color: active ? 'rgb(0,78,235)' : 'rgb(102,112,133)',
    fontWeight: active ? 500 : 400,
  })

  return (
    <div className="flex shrink-0 flex-wrap items-center gap-6 px-4"
      style={{ minHeight: 42 }}>
      <button
        className="flex items-center gap-2"
        onClick={() => onApply({ pipelineId: now.pipelineId, status: 'open', q: '' })}
        title="The board's default: every open opportunity in this pipeline"
      >
        <IconList size={16} color={isDefault && !activeSaved
          ? 'rgb(0,78,235)' : 'rgb(102,112,133)'} />
        <span style={chip(isDefault && !activeSaved)}>Open opportunities</span>
      </button>

      {views.data?.map((v) => (
        <button
          key={v.id}
          className="flex items-center gap-2"
          onClick={() => onApply(applyView(v, now))}
          title={describeView(v)}
        >
          <IconList size={16} color={activeSaved?.id === v.id
            ? 'rgb(0,78,235)' : 'rgb(102,112,133)'} />
          <span style={chip(activeSaved?.id === v.id)}>{v.name}</span>
        </button>
      ))}

      {naming ? (
        <div className="flex items-center gap-2">
          <input
            autoFocus
            value={name}
            maxLength={80}
            placeholder="Name this list"
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Escape') { setNaming(false); setError(null) }
              if (e.key === 'Enter' && name.trim()) { setError(null); create.mutate() }
            }}
            style={{
              height: 30, borderRadius: 6, border: '1px solid rgb(234,236,240)',
              padding: '0 10px', fontSize: 14,
            }}
          />
          <button
            onClick={() => { setError(null); create.mutate() }}
            disabled={!name.trim() || create.isPending}
            style={{
              height: 30, padding: '0 10px', borderRadius: 6, fontSize: 13,
              fontWeight: 500, color: '#fff', backgroundColor: 'rgb(0,78,235)',
              ...(name.trim() && !create.isPending
                ? {} : { opacity: 0.5, cursor: 'not-allowed' }),
            }}
          >
            {create.isPending ? 'Saving…' : 'Save'}
          </button>
          <button
            onClick={() => { setNaming(false); setError(null) }}
            style={{ fontSize: 13, color: 'rgb(102,112,133)' }}
          >
            Cancel
          </button>
          <span style={{ fontSize: 12, color: 'rgb(152,162,179)' }}>
            {describeFilters(now)}
          </span>
        </div>
      ) : (
        <button
          className="flex items-center gap-2"
          onClick={() => { setNaming(true); setError(null) }}
          disabled={!canSave}
          title={canSave
            ? 'Save the filters the board is showing as a named list'
            : 'Your role cannot save a shared list'}
          style={canSave ? undefined : { opacity: 0.5, cursor: 'not-allowed' }}
        >
          <IconPlus size={16} color="rgb(102,112,133)" />
          <span style={CHIP_TEXT}>List</span>
        </button>
      )}

      <button
        onClick={onManage}
        className="ml-auto"
        style={{ fontSize: 13, color: 'rgb(102,112,133)' }}
      >
        Manage smart lists
      </button>

      {error && (
        <div role="alert" style={{ fontSize: 13, color: 'rgb(180,35,24)' }}>
          {error}
        </div>
      )}
    </div>
  )
}

function describeFilters(f: BoardFilters): string {
  const bits = ['Status: ' + f.status]
  if (f.q.trim()) bits.push('matching “' + f.q.trim() + '”')
  return 'will save ' + bits.join(', ')
}

/**
 * Manage smart lists. Rename and delete; the filters themselves are changed by
 * applying a list, adjusting the board and saving again — a form that re-edits
 * every filter twice would be a second, drifting copy of the toolbar.
 */
export function ManageSavedViews({ user, onClose }: { user: Me; onClose: () => void }) {
  const qc = useQueryClient()
  const views = useQuery({ queryKey: ['saved-views'], queryFn: listSavedViews })
  const [editing, setEditing] = useState<number | null>(null)
  const [name, setName] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [confirming, setConfirming] = useState<number | null>(null)

  // `DELETE /api/saved-views/{id}` is ADMIN: everyone reads, staff write, admin
  // deletes (CLAUDE.md). A saved list is shared, so removing one takes another
  // person's list away.
  const canDelete = user.role === 'ADMIN'
  const canRename = user.role !== 'TECH'

  const rename = useMutation({
    mutationFn: (id: number) => patchSavedView(id, { name: name.trim() }),
    onSuccess: () => {
      setEditing(null)
      qc.invalidateQueries({ queryKey: ['saved-views'] })
    },
    onError: (e: Error) => setError(e.message),
  })

  const remove = useMutation({
    mutationFn: (id: number) => deleteSavedView(id),
    onSuccess: () => {
      setConfirming(null)
      qc.invalidateQueries({ queryKey: ['saved-views'] })
    },
    onError: (e: Error) => setError(e.message),
  })

  return (
    <div
      className="fixed inset-0 z-40 flex items-center justify-center"
      style={{ backgroundColor: 'rgba(16,24,40,0.4)' }}
      onClick={onClose}
    >
      <div onClick={(e) => e.stopPropagation()} className="bg-white"
        style={{ width: 520, borderRadius: 8, padding: 20 }}>
        <div style={{ fontSize: 18, fontWeight: 600, color: 'rgb(16,24,40)' }}>
          Manage smart lists
        </div>
        <div style={{ fontSize: 13, color: 'rgb(102,112,133)', marginTop: 4 }}>
          A list is the board's filters under a name. “Open opportunities” is the
          board's own default and is always there.
        </div>

        <div style={{ marginTop: 12, maxHeight: 320, overflowY: 'auto' }}>
          {views.data?.length ? views.data.map((v) => (
            <div key={v.id} className="flex items-center gap-2"
              style={{ padding: '8px 0', borderBottom: '1px solid rgb(242,244,247)' }}>
              {editing === v.id ? (
                <>
                  <input
                    autoFocus
                    value={name}
                    maxLength={80}
                    onChange={(e) => setName(e.target.value)}
                    style={{
                      flex: 1, height: 32, borderRadius: 6, fontSize: 14,
                      border: '1px solid rgb(234,236,240)', padding: '0 10px',
                    }}
                  />
                  <button
                    onClick={() => { setError(null); rename.mutate(v.id) }}
                    disabled={!name.trim() || rename.isPending}
                    style={{
                      fontSize: 13, fontWeight: 500, color: 'rgb(0,78,235)',
                      ...(name.trim() ? {} : { opacity: 0.5, cursor: 'not-allowed' }),
                    }}
                  >
                    Save
                  </button>
                  <button onClick={() => setEditing(null)}
                    style={{ fontSize: 13, color: 'rgb(102,112,133)' }}>
                    Cancel
                  </button>
                </>
              ) : (
                <>
                  <div className="min-w-0 flex-1">
                    <div className="truncate"
                      style={{ fontSize: 14, color: 'rgb(52,64,84)' }}>
                      {v.name}
                    </div>
                    <div style={{ fontSize: 12, color: 'rgb(152,162,179)' }}>
                      {describeView(v)}
                    </div>
                  </div>
                  <button
                    onClick={() => { setEditing(v.id); setName(v.name); setError(null) }}
                    disabled={!canRename}
                    title={canRename ? undefined : 'Your role cannot rename a shared list'}
                    style={{
                      fontSize: 13, fontWeight: 500, color: 'rgb(0,78,235)',
                      ...(canRename ? {} : { opacity: 0.5, cursor: 'not-allowed' }),
                    }}
                  >
                    Rename
                  </button>
                  {confirming === v.id ? (
                    <button
                      onClick={() => { setError(null); remove.mutate(v.id) }}
                      disabled={remove.isPending}
                      style={{ fontSize: 13, fontWeight: 600, color: 'rgb(180,35,24)' }}
                    >
                      {remove.isPending ? 'Deleting…' : 'Really delete'}
                    </button>
                  ) : (
                    <button
                      onClick={() => { setConfirming(v.id); setError(null) }}
                      disabled={!canDelete}
                      title={canDelete
                        ? undefined
                        : 'Only an admin can delete a shared list'}
                      style={{
                        fontSize: 13, color: 'rgb(180,35,24)',
                        ...(canDelete ? {} : { opacity: 0.5, cursor: 'not-allowed' }),
                      }}
                    >
                      Delete
                    </button>
                  )}
                </>
              )}
            </div>
          )) : (
            <div style={{ fontSize: 14, color: 'rgb(102,112,133)' }}>
              No saved lists yet. Filter the board and use “+ List”.
            </div>
          )}
        </div>

        {error && (
          <div role="alert" style={{ fontSize: 13, color: 'rgb(180,35,24)', marginTop: 10 }}>
            {error}
          </div>
        )}

        <div className="mt-5 flex justify-end">
          <button onClick={onClose}
            style={{
              height: 36, padding: '0 14px', borderRadius: 6, fontSize: 14,
              border: '1px solid rgb(234,236,240)',
            }}>
            Close
          </button>
        </div>
      </div>
    </div>
  )
}
