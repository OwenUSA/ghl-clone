import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import {
  bulkAssignOwner,
  bulkMoveStage,
  listUsers,
  money,
  type Opportunity,
  type Pipeline,
} from '../lib/api'
import { csvFilename, opportunitiesCsv } from '../lib/csv'
import { downloadCsv } from '../lib/download'
import type { Me } from '../lib/auth'

/**
 * The Opportunities > Bulk Actions bar. OUR design — GHL's own Bulk Actions tab
 * was never opened on the live account (DECISIONS.md, 2026-09-10).
 *
 * Three actions, and the absence of a fourth is the important one:
 *
 *   Move to stage   ANY_USER, the same right as dragging one card
 *   Assign owner    STAFF, the same right as editing one opportunity
 *   Export CSV      the selection, client-side, no request at all
 *
 * NO BULK DELETE. `custom_fields.owen_call_id` is the telephony project's join
 * key, opportunities are never cascade-deleted for that reason, and a checkbox
 * column is the easiest way ever invented to lose real customer records. The
 * same call was already made for the Contacts list. It is said on the bar, in
 * words, rather than just left out — a missing control reads as an oversight.
 */

export function BulkActionsBar({
  user,
  pipeline,
  chosen,
  visibleCount,
  onSelectAll,
  onClear,
}: {
  user: Me
  pipeline: Pipeline | undefined
  /** Exactly the selected rows, in board order. The export writes these. */
  chosen: Opportunity[]
  visibleCount: number
  onSelectAll: () => void
  onClear: () => void
}) {
  const qc = useQueryClient()
  const [stageId, setStageId] = useState<number | null>(null)
  const [ownerId, setOwnerId] = useState<string>('')
  const [note, setNote] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  // `POST /api/opportunities/bulk/owner` is STAFF. Disable rather than let a TECH
  // pick an owner and be refused on Apply (precedent: d1f7c50, b943f4b).
  const canAssign = user.role !== 'TECH'
  const users = useQuery({ queryKey: ['users'], queryFn: listUsers, enabled: canAssign })

  const stages = pipeline?.stages ?? []
  const stage = stages.find((s) => s.id === stageId) ?? stages[0]
  const ids = chosen.map((o) => o.id)

  const done = () => {
    qc.invalidateQueries({ queryKey: ['opportunities'] })
    qc.invalidateQueries({ queryKey: ['pipelines'] })
    qc.invalidateQueries({ queryKey: ['forecast'] })
    onClear()
  }

  const move = useMutation({
    mutationFn: () => bulkMoveStage(ids, stage!.id),
    onSuccess: (r) => {
      // Say what actually happened to each half of the selection. "3 moved" when
      // one of the four was already there is how a bulk action loses trust.
      const notified = Object.values(r.automation).filter((a) => a === 'queued').length
      setNote(
        `Moved ${r.moved.length} to ${stage!.name}` +
          (r.unchanged.length ? `, ${r.unchanged.length} already there` : '') +
          `. ${notified} customer${notified === 1 ? '' : 's'} notified.`,
      )
      done()
    },
    onError: (e: Error) => setError(e.message),
  })

  const assign = useMutation({
    mutationFn: () => bulkAssignOwner(ids, ownerId ? Number(ownerId) : null),
    onSuccess: (r) => {
      const who = users.data?.find((u) => u.id === r.owner_id)?.name ?? 'nobody'
      setNote(`Assigned ${r.updated.length} to ${who}.`)
      done()
    },
    onError: (e: Error) => setError(e.message),
  })

  const exportSelection = () => {
    const names = new Map(stages.map((s) => [s.id, s.name]))
    const text = opportunitiesCsv(chosen, (id) => names.get(id) ?? '')
    downloadCsv(csvFilename(pipeline?.name ?? 'opportunities', new Date()), text)
    setNote(`Exported ${chosen.length} row${chosen.length === 1 ? '' : 's'}.`)
  }

  const busy = move.isPending || assign.isPending
  const none = ids.length === 0
  const control = {
    height: 34,
    borderRadius: 6,
    border: '1px solid rgb(234,236,240)',
    padding: '0 8px',
    fontSize: 13,
    backgroundColor: '#fff',
    color: 'rgb(52,64,84)',
  } as const
  const action = (enabled: boolean) => ({
    height: 34,
    padding: '0 12px',
    borderRadius: 6,
    fontSize: 13,
    fontWeight: 500,
    color: '#fff',
    backgroundColor: 'rgb(0,78,235)',
    ...(enabled ? {} : { opacity: 0.5, cursor: 'not-allowed' }),
  })

  return (
    <div
      className="mx-4 mb-3 shrink-0 bg-white"
      style={{ borderRadius: 8, border: '1px solid rgb(0,78,235)', padding: 12 }}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span style={{ fontSize: 14, fontWeight: 600, color: 'rgb(16,24,40)' }}>
          {ids.length} of {visibleCount} selected
        </span>
        <button
          onClick={onSelectAll}
          style={{ fontSize: 13, fontWeight: 500, color: 'rgb(0,78,235)' }}
        >
          Select all
        </button>
        <button
          onClick={onClear}
          disabled={none}
          style={{
            fontSize: 13, fontWeight: 500, color: 'rgb(102,112,133)',
            ...(none ? { opacity: 0.5, cursor: 'not-allowed' } : {}),
          }}
        >
          Clear
        </button>

        <span style={{ width: 1, height: 24, backgroundColor: 'rgb(234,236,240)' }} />

        <select
          value={stage?.id ?? ''}
          onChange={(e) => setStageId(Number(e.target.value))}
          style={control}
        >
          {/* Two stages are called "Call Back"; the key and the value are the id. */}
          {stages.map((s) => (
            <option key={s.id} value={s.id}>{s.name}</option>
          ))}
        </select>
        <button
          onClick={() => { setError(null); setNote(null); move.mutate() }}
          disabled={none || !stage || busy}
          title={none ? 'Nothing is selected' : undefined}
          style={action(!none && !!stage && !busy)}
        >
          {move.isPending ? 'Moving…' : 'Move to stage'}
        </button>

        <span style={{ width: 1, height: 24, backgroundColor: 'rgb(234,236,240)' }} />

        <select
          value={ownerId}
          onChange={(e) => setOwnerId(e.target.value)}
          disabled={!canAssign}
          title={canAssign ? undefined : 'Your role cannot change an opportunity owner'}
          style={{ ...control, ...(canAssign ? {} : { opacity: 0.5 }) }}
        >
          <option value="">Unassigned</option>
          {users.data?.map((u) => (
            <option key={u.id} value={u.id}>{u.name}</option>
          ))}
        </select>
        <button
          onClick={() => { setError(null); setNote(null); assign.mutate() }}
          disabled={none || !canAssign || busy}
          title={canAssign ? undefined : 'Your role cannot change an opportunity owner'}
          style={action(!none && canAssign && !busy)}
        >
          {assign.isPending ? 'Assigning…' : 'Assign owner'}
        </button>

        <span style={{ width: 1, height: 24, backgroundColor: 'rgb(234,236,240)' }} />

        <button
          onClick={exportSelection}
          disabled={none}
          style={{
            height: 34, padding: '0 12px', borderRadius: 6, fontSize: 13,
            fontWeight: 500, color: 'rgb(0,78,235)',
            border: '1px solid rgb(0,78,235)',
            ...(none ? { opacity: 0.5, cursor: 'not-allowed' } : {}),
          }}
        >
          Export CSV
        </button>

        <span className="ml-auto" style={{ fontSize: 12, color: 'rgb(102,112,133)' }}>
          {/* Said out loud: an absent control reads as an oversight. */}
          No bulk delete — opportunities carry the telephony join key and are
          deleted one at a time.
        </span>
      </div>

      {(note || error) && (
        <div
          role={error ? 'alert' : 'status'}
          style={{
            marginTop: 8, fontSize: 13,
            color: error ? 'rgb(180,35,24)' : 'rgb(52,64,84)',
          }}
        >
          {error ?? note}
        </div>
      )}

      {ids.length > 0 && (
        <div style={{ marginTop: 6, fontSize: 12, color: 'rgb(152,162,179)' }}>
          {money(chosen.reduce((sum, o) => sum + o.value_cents, 0))} selected
        </div>
      )}
    </div>
  )
}
