import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import {
  archiveCustomField,
  createCustomField,
  createFieldGroup,
  deleteFieldGroup,
  listCustomFields,
  listFieldGroups,
  listPipelines,
  patchCustomField,
  renameFieldGroup,
  reorderCustomFields,
  reorderFieldGroups,
  restoreCustomField,
  type FieldGroup,
} from '../lib/api'
import type { FieldDef } from '../lib/customFields'
import type { Me } from '../lib/auth'

/**
 * The Opportunities > Custom fields tab: define a job question, choose which
 * pipelines it is asked on, reorder them, archive the ones no longer asked.
 *
 * OUR design. GHL's own custom-fields settings screen was never opened on the
 * live account, so nothing here may be cited as parity — same standing as the
 * Pipelines tab beside it, which this panel is deliberately built to match.
 *
 * The screen is built around what the server already refuses:
 *
 *   * Every control is dead for a role that is not ADMIN, each with a title
 *     saying why — never a form that 403s on submit (d1f7c50, b943f4b).
 *   * Delete says "Archive", because that is what it does. The answers already
 *     recorded are not ours to destroy; the panel says so on the screen rather
 *     than only in a docstring, and an archived field can be restored.
 *   * The TYPE cannot be changed after creation and the key never moves with a
 *     rename, both because answers are already filed under them. Both are shown
 *     as fixed text rather than as a control that does nothing.
 *   * Reorder sends the whole live list as a permutation, arrows not drag, the
 *     same contract as the stage reorder.
 *   * TABS (2026-09-13): a field can sit in one named tab of the opportunity
 *     modal — GoHighLevel's "Roof Inspection", "Photo Checklist" — or in none,
 *     which draws it under Opportunity details. Tabs are created, renamed,
 *     reordered and removed here; removing one moves its fields back to
 *     Opportunity details and touches no answer. Nothing is seeded: the owner
 *     makes his own.
 */

const TYPES: { value: FieldDef['field_type']; label: string }[] = [
  { value: 'text', label: 'Text' },
  { value: 'number', label: 'Number' },
  { value: 'dropdown', label: 'Dropdown (pick one)' },
  { value: 'date', label: 'Date' },
  { value: 'boolean', label: 'Yes / no' },
]

const input = {
  height: 34, borderRadius: 6, border: '1px solid rgb(234,236,240)',
  padding: '0 10px', fontSize: 14, backgroundColor: '#fff',
} as const

const link = (enabled: boolean, colour = 'rgb(0,78,235)') => ({
  fontSize: 13, fontWeight: 500, color: colour,
  ...(enabled ? {} : { opacity: 0.5, cursor: 'not-allowed' }),
})

export function CustomFieldsPanel({ user }: { user: Me }) {
  const qc = useQueryClient()
  const fields = useQuery({ queryKey: ['custom-fields'], queryFn: listCustomFields })
  const pipelines = useQuery({ queryKey: ['pipelines'], queryFn: listPipelines })
  const groups = useQuery({ queryKey: ['custom-field-groups'], queryFn: listFieldGroups })

  const [label, setLabel] = useState('')
  const [type, setType] = useState<FieldDef['field_type']>('text')
  const [options, setOptions] = useState('')
  const [attach, setAttach] = useState<number[]>([])
  const [groupId, setGroupId] = useState('')
  const [draftGroup, setDraftGroup] = useState('')
  const [newGroup, setNewGroup] = useState('')
  const [renaming, setRenaming] = useState<number | null>(null)
  const [groupName, setGroupName] = useState('')
  const [removingGroup, setRemovingGroup] = useState<number | null>(null)
  const [editing, setEditing] = useState<number | null>(null)
  const [draftLabel, setDraftLabel] = useState('')
  const [draftOptions, setDraftOptions] = useState('')
  const [draftPipelines, setDraftPipelines] = useState<number[]>([])
  const [error, setError] = useState<string | null>(null)
  const [confirming, setConfirming] = useState<number | null>(null)

  // Every write endpoint under /api/custom-fields is auth.ADMIN.
  const canManage = user.role === 'ADMIN'
  const why = 'Only an admin can define, edit or archive custom fields'

  const live = (fields.data ?? []).filter((f) => !f.archived)
  const archived = (fields.data ?? []).filter((f) => f.archived)

  const refresh = () => {
    setError(null)
    setEditing(null)
    setConfirming(null)
    setRenaming(null)
    setRemovingGroup(null)
    qc.invalidateQueries({ queryKey: ['custom-fields'] })
    qc.invalidateQueries({ queryKey: ['custom-field-groups'] })
    // The opportunity detail and the Add dialog both render these questions.
    qc.invalidateQueries({ queryKey: ['opportunity'] })
  }
  const failed = (e: Error) => setError(e.message)

  const add = useMutation({
    mutationFn: () => createCustomField({
      label: label.trim(),
      field_type: type,
      options: splitOptions(options),
      pipeline_ids: attach,
      group_id: groupId ? Number(groupId) : null,
    }),
    onSuccess: () => {
      setLabel(''); setOptions(''); setAttach([]); setGroupId(''); refresh()
    },
    onError: failed,
  })

  const save = useMutation({
    mutationFn: (id: number) => patchCustomField(id, {
      label: draftLabel.trim(),
      options: splitOptions(draftOptions),
      pipeline_ids: draftPipelines,
      group_id: draftGroup ? Number(draftGroup) : null,
    }),
    onSuccess: refresh,
    onError: failed,
  })

  const archive = useMutation({
    mutationFn: (id: number) => archiveCustomField(id),
    onSuccess: refresh,
    onError: failed,
  })

  const restore = useMutation({
    mutationFn: (id: number) => restoreCustomField(id),
    onSuccess: refresh,
    onError: failed,
  })

  const reorder = useMutation({
    mutationFn: (ids: number[]) => reorderCustomFields(ids),
    onSuccess: refresh,
    onError: failed,
  })

  const addGroup = useMutation({
    mutationFn: () => createFieldGroup(newGroup.trim()),
    onSuccess: () => { setNewGroup(''); refresh() },
    onError: failed,
  })
  const renameGroup = useMutation({
    mutationFn: (id: number) => renameFieldGroup(id, groupName.trim()),
    onSuccess: refresh,
    onError: failed,
  })
  const reorderGroups = useMutation({
    mutationFn: (ids: number[]) => reorderFieldGroups(ids),
    onSuccess: refresh,
    onError: failed,
  })
  const removeGroup = useMutation({
    mutationFn: (id: number) => deleteFieldGroup(id),
    onSuccess: refresh,
    onError: failed,
  })

  const busy = add.isPending || save.isPending || archive.isPending
    || restore.isPending || reorder.isPending || addGroup.isPending
    || renameGroup.isPending || reorderGroups.isPending || removeGroup.isPending

  const tabs: FieldGroup[] = groups.data ?? []
  const tabName = (id: number | null | undefined) =>
    tabs.find((g) => g.id === id)?.name ?? 'Opportunity details'
  const swapGroup = (index: number, delta: number) => {
    const ids = tabs.map((g) => g.id)
    const to = index + delta
    if (to < 0 || to >= ids.length) return
    ;[ids[index], ids[to]] = [ids[to], ids[index]]
    setError(null)
    reorderGroups.mutate(ids)
  }

  /** Swap two rows and send the whole list, never a single "move" instruction. */
  const swap = (index: number, delta: number) => {
    const ids = live.map((f) => f.id)
    const to = index + delta
    if (to < 0 || to >= ids.length) return
    ;[ids[index], ids[to]] = [ids[to], ids[index]]
    setError(null)
    reorder.mutate(ids)
  }

  const startEdit = (f: FieldDef) => {
    setEditing(f.id)
    setDraftLabel(f.label)
    setDraftOptions(f.options.join('\n'))
    setDraftPipelines(f.pipeline_ids)
    setDraftGroup(f.group_id != null ? String(f.group_id) : '')
    setError(null)
  }

  const pipelineNames = (ids: number[]) => {
    const names = ids
      .map((id) => pipelines.data?.find((p) => p.id === id)?.name)
      .filter(Boolean)
    return names.length ? names.join(', ') : 'No pipeline — asked on no deal'
  }

  return (
    <div className="min-h-0 flex-1 overflow-auto px-4 pb-4">
      <div
        className="bg-white"
        style={{
          borderRadius: 8, border: '1px solid rgb(253,176,34)', padding: 12,
          fontSize: 13, color: 'rgb(52,64,84)',
        }}
      >
        These are the questions asked about a job on the opportunity form. One
        field can be asked on <strong>several pipelines</strong>, and every field is
        optional — nothing here can stop a deal being created. Archiving a field
        takes the question off new deals and <strong>keeps every answer already
        recorded</strong>, still readable on the deals that hold them. Nothing on
        this screen deletes an answer.
      </div>

      {error && (
        <div role="alert" className="bg-white" style={{
          marginTop: 12, borderRadius: 8, border: '1px solid rgb(217,45,32)',
          padding: 12, fontSize: 13, color: 'rgb(180,35,24)',
        }}>
          {error}
        </div>
      )}

      {fields.error && (
        <div role="alert" className="bg-white" style={{
          marginTop: 12, borderRadius: 8, border: '1px solid rgb(217,45,32)',
          padding: 12, fontSize: 13, color: 'rgb(180,35,24)',
        }}>
          {(fields.error as Error).message}
        </div>
      )}

      {/* ---- the modal's tabs ---- */}
      <div className="mt-4 bg-white" style={{
        borderRadius: 8, border: '1px solid rgb(234,236,240)', padding: 12,
      }}>
        <div style={{ fontSize: 16, fontWeight: 600, color: 'rgb(16,24,40)' }}>
          Tabs
        </div>
        <div style={{ fontSize: 12, color: 'rgb(102,112,133)', marginTop: 2 }}>
          Each tab is its own section in the opportunity window, in this order, on
          every pipeline. A field with no tab shows under Opportunity details.
          Removing a tab moves its fields there — no field and no answer is lost.
        </div>
        {tabs.length === 0 && (
          <div style={{ fontSize: 13, color: 'rgb(152,162,179)', marginTop: 10 }}>
            No tabs yet. Nothing is guessed for you.
          </div>
        )}
        {tabs.map((g, i) => (
          <div key={g.id} className="flex items-center gap-3"
            style={{ padding: '8px 0', borderBottom: '1px solid rgb(242,244,247)' }}>
            <div className="min-w-0 flex-1">
              {renaming === g.id ? (
                <input
                  autoFocus
                  value={groupName}
                  maxLength={80}
                  aria-label={'Rename ' + g.name}
                  onChange={(e) => setGroupName(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && groupName.trim()) renameGroup.mutate(g.id)
                    if (e.key === 'Escape') setRenaming(null)
                  }}
                  style={{ ...input, height: 30, width: 260 }}
                />
              ) : (
                <>
                  <span style={{ fontSize: 14, color: 'rgb(52,64,84)' }}>{g.name}</span>
                  <span style={{ fontSize: 12, color: 'rgb(152,162,179)', marginLeft: 8 }}>
                    {g.field_ids.length} field{g.field_ids.length === 1 ? '' : 's'}
                  </span>
                </>
              )}
            </div>
            <button onClick={() => swapGroup(i, -1)}
              disabled={!canManage || i === 0 || busy}
              aria-label={'Move tab ' + g.name + ' up'}
              title={canManage ? 'Move up' : why}
              style={link(canManage && i > 0 && !busy, 'rgb(102,112,133)')}>↑</button>
            <button onClick={() => swapGroup(i, 1)}
              disabled={!canManage || i === tabs.length - 1 || busy}
              aria-label={'Move tab ' + g.name + ' down'}
              title={canManage ? 'Move down' : why}
              style={link(canManage && i < tabs.length - 1 && !busy, 'rgb(102,112,133)')}>↓</button>
            {renaming === g.id ? (
              <>
                <button onClick={() => renameGroup.mutate(g.id)}
                  disabled={!groupName.trim() || busy}
                  style={link(!!groupName.trim() && !busy)}>Save</button>
                <button onClick={() => setRenaming(null)}
                  style={{ fontSize: 13, color: 'rgb(102,112,133)' }}>Cancel</button>
              </>
            ) : (
              <button
                onClick={() => { setError(null); setRenaming(g.id); setGroupName(g.name) }}
                disabled={!canManage || busy}
                title={canManage ? undefined : why}
                style={link(canManage && !busy)}>Rename</button>
            )}
            {removingGroup === g.id ? (
              <button onClick={() => removeGroup.mutate(g.id)} disabled={busy}
                title="Its fields move to Opportunity details, answers and all"
                style={{ ...link(!busy, 'rgb(180,35,24)'), fontWeight: 600 }}>
                Really remove
              </button>
            ) : (
              <button
                onClick={() => { setError(null); setRemovingGroup(g.id) }}
                disabled={!canManage || busy}
                title={canManage
                  ? 'Remove this tab. Its fields move to Opportunity details, answers and all.'
                  : why}
                style={link(canManage && !busy, 'rgb(180,35,24)')}>Remove</button>
            )}
          </div>
        ))}
        <div className="mt-3 flex items-center gap-2">
          <input
            value={newGroup}
            maxLength={80}
            disabled={!canManage}
            title={canManage ? undefined : why}
            aria-label="New tab name"
            placeholder="Roof Inspection"
            onChange={(e) => setNewGroup(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && newGroup.trim() && canManage) addGroup.mutate()
            }}
            style={{ ...input, width: 260, ...(canManage ? {} : { opacity: 0.5 }) }}
          />
          <button
            onClick={() => { setError(null); addGroup.mutate() }}
            disabled={!canManage || !newGroup.trim() || busy}
            title={canManage ? undefined : why}
            style={{
              height: 34, padding: '0 12px', borderRadius: 6, fontSize: 13,
              fontWeight: 500, color: '#fff', backgroundColor: 'rgb(0,78,235)',
              ...(canManage && newGroup.trim() && !busy
                ? {} : { opacity: 0.5, cursor: 'not-allowed' }),
            }}
          >
            Add tab
          </button>
        </div>
      </div>

      <div className="mt-4 grid gap-4"
        style={{ gridTemplateColumns: 'minmax(260px, 340px) 1fr' }}>
        {/* ---- define a new question ---- */}
        <div className="bg-white" style={{
          borderRadius: 8, border: '1px solid rgb(234,236,240)', padding: 12,
        }}>
          <div style={{ fontSize: 16, fontWeight: 600, color: 'rgb(16,24,40)' }}>
            New field
          </div>

          <div style={{ marginTop: 10 }}>
            <div style={{ fontSize: 13, color: 'rgb(102,112,133)' }}>Question</div>
            <input
              value={label}
              maxLength={160}
              disabled={!canManage}
              title={canManage ? undefined : why}
              placeholder="How old is the roof?"
              onChange={(e) => setLabel(e.target.value)}
              style={{ ...input, width: '100%', marginTop: 4,
                ...(canManage ? {} : { opacity: 0.5 }) }}
            />
          </div>

          <div style={{ marginTop: 10 }}>
            <div style={{ fontSize: 13, color: 'rgb(102,112,133)' }}>Type</div>
            <select
              value={type}
              disabled={!canManage}
              title={canManage ? undefined
                : why}
              onChange={(e) => setType(e.target.value as FieldDef['field_type'])}
              style={{ ...input, width: '100%', marginTop: 4,
                ...(canManage ? {} : { opacity: 0.5 }) }}
            >
              {TYPES.map((t) => (
                <option key={t.value} value={t.value}>{t.label}</option>
              ))}
            </select>
            <div style={{ fontSize: 12, color: 'rgb(152,162,179)', marginTop: 4 }}>
              The type is fixed once the field exists — answers are already filed
              against it.
            </div>
          </div>

          {type === 'dropdown' && (
            <div style={{ marginTop: 10 }}>
              <div style={{ fontSize: 13, color: 'rgb(102,112,133)' }}>
                Choices, one per line
              </div>
              <textarea
                value={options}
                disabled={!canManage}
                title={canManage ? undefined : why}
                rows={4}
                placeholder={'Shingle\nTile\nMetal'}
                onChange={(e) => setOptions(e.target.value)}
                style={{ ...input, width: '100%', height: 'auto', padding: 8,
                  marginTop: 4, ...(canManage ? {} : { opacity: 0.5 }) }}
              />
            </div>
          )}

          <div style={{ marginTop: 10 }}>
            <div style={{ fontSize: 13, color: 'rgb(102,112,133)' }}>
              Tab in the opportunity window
            </div>
            <select
              value={groupId}
              disabled={!canManage}
              title={canManage ? undefined : why}
              aria-label="Tab for the new field"
              onChange={(e) => setGroupId(e.target.value)}
              style={{ ...input, width: '100%', marginTop: 4,
                ...(canManage ? {} : { opacity: 0.5 }) }}
            >
              <option value="">Opportunity details</option>
              {tabs.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
            </select>
          </div>

          <div style={{ marginTop: 10 }}>
            <div style={{ fontSize: 13, color: 'rgb(102,112,133)' }}>
              Asked on these pipelines
            </div>
            <PipelineChecks
              all={pipelines.data ?? []}
              chosen={attach}
              disabled={!canManage}
              why={why}
              onToggle={(id, on) =>
                setAttach((v) => (on ? [...v, id] : v.filter((x) => x !== id)))}
            />
            {attach.length === 0 && (
              <div style={{ fontSize: 12, color: 'rgb(180,35,24)', marginTop: 4 }}>
                With none ticked the field is asked on no deal at all.
              </div>
            )}
          </div>

          <button
            onClick={() => { setError(null); add.mutate() }}
            disabled={!canManage || !label.trim() || busy}
            title={canManage ? undefined : why}
            style={{
              marginTop: 12, height: 34, padding: '0 12px', borderRadius: 6,
              fontSize: 13, fontWeight: 500, color: '#fff',
              backgroundColor: 'rgb(0,78,235)',
              ...(canManage && label.trim() && !busy
                ? {} : { opacity: 0.5, cursor: 'not-allowed' }),
            }}
          >
            Add field
          </button>
          <div style={{ fontSize: 12, color: 'rgb(152,162,179)', marginTop: 6 }}>
            A field starting <code>owen_</code> or <code>workiz_</code> is refused:
            those belong to the telephony project and the Workiz import.
          </div>
        </div>

        {/* ---- the fields themselves ---- */}
        <div className="bg-white" style={{
          borderRadius: 8, border: '1px solid rgb(234,236,240)', padding: 12,
        }}>
          <div style={{ fontSize: 16, fontWeight: 600, color: 'rgb(16,24,40)' }}>
            Fields
          </div>
          <div style={{ fontSize: 12, color: 'rgb(102,112,133)', marginTop: 2 }}>
            {live.length} asked · {archived.length} archived
          </div>

          <table className="mt-3 w-full">
            <thead>
              <tr>
                {['Question', 'Type', 'Tab', 'Pipelines', 'Order', ''].map((h) => (
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
              {live.map((f, i) => (
                <tr key={f.id}>
                  <td style={{ padding: '8px 0', fontSize: 14,
                    borderBottom: '1px solid rgb(242,244,247)' }}>
                    {editing === f.id ? (
                      <div>
                        <input
                          autoFocus
                          value={draftLabel}
                          maxLength={160}
                          onChange={(e) => setDraftLabel(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === 'Escape') setEditing(null)
                          }}
                          style={{ ...input, height: 30, width: 240 }}
                        />
                        {f.field_type === 'dropdown' && (
                          <textarea
                            value={draftOptions}
                            rows={3}
                            onChange={(e) => setDraftOptions(e.target.value)}
                            style={{ ...input, width: 240, height: 'auto',
                              padding: 6, marginTop: 6, display: 'block' }}
                          />
                        )}
                        <select
                          value={draftGroup}
                          aria-label={'Tab for ' + f.label}
                          onChange={(e) => setDraftGroup(e.target.value)}
                          style={{ ...input, height: 30, width: 240, marginTop: 6,
                            display: 'block' }}
                        >
                          <option value="">Opportunity details</option>
                          {tabs.map((g) => (
                            <option key={g.id} value={g.id}>{g.name}</option>
                          ))}
                        </select>
                        <PipelineChecks
                          all={pipelines.data ?? []}
                          chosen={draftPipelines}
                          disabled={false}
                          why={why}
                          onToggle={(id, on) => setDraftPipelines((v) =>
                            on ? [...v, id] : v.filter((x) => x !== id))}
                        />
                        <div className="mt-1 flex gap-3">
                          <button
                            onClick={() => save.mutate(f.id)}
                            disabled={!draftLabel.trim() || busy}
                            style={link(!!draftLabel.trim() && !busy)}
                          >
                            Save
                          </button>
                          <button onClick={() => setEditing(null)}
                            style={{ fontSize: 13, color: 'rgb(102,112,133)' }}>
                            Cancel
                          </button>
                        </div>
                      </div>
                    ) : (
                      <>
                        <div style={{ color: 'rgb(52,64,84)' }}>{f.label}</div>
                        <div style={{ fontSize: 12, color: 'rgb(152,162,179)' }}>
                          {/* The storage key, shown because it is what answers are
                              filed under and it never changes with a rename. */}
                          <code>{f.key}</code>
                          {f.field_type === 'dropdown' && f.options.length > 0
                            && ' · ' + f.options.join(' / ')}
                        </div>
                      </>
                    )}
                  </td>
                  <td style={{ fontSize: 14, color: 'rgb(52,64,84)',
                    borderBottom: '1px solid rgb(242,244,247)' }}>
                    {TYPES.find((t) => t.value === f.field_type)?.label
                      ?? f.field_type}
                  </td>
                  <td style={{ fontSize: 13, color: 'rgb(52,64,84)',
                    borderBottom: '1px solid rgb(242,244,247)' }}>
                    {tabName(f.group_id)}
                  </td>
                  <td style={{ fontSize: 13, color: 'rgb(52,64,84)',
                    borderBottom: '1px solid rgb(242,244,247)' }}>
                    {pipelineNames(f.pipeline_ids)}
                  </td>
                  <td style={{ borderBottom: '1px solid rgb(242,244,247)' }}>
                    <button
                      onClick={() => swap(i, -1)}
                      disabled={!canManage || i === 0 || busy}
                      aria-label={'Move ' + f.label + ' up'}
                      title={canManage ? 'Move up' : why}
                      style={link(canManage && i > 0 && !busy, 'rgb(102,112,133)')}
                    >
                      ↑
                    </button>
                    <button
                      onClick={() => swap(i, 1)}
                      disabled={!canManage || i === live.length - 1 || busy}
                      aria-label={'Move ' + f.label + ' down'}
                      title={canManage ? 'Move down' : why}
                      style={{
                        ...link(canManage && i < live.length - 1 && !busy,
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
                      onClick={() => startEdit(f)}
                      disabled={!canManage || busy}
                      title={canManage ? undefined : why}
                      style={link(canManage && !busy)}
                    >
                      Edit
                    </button>
                    {confirming === f.id ? (
                      <button
                        onClick={() => archive.mutate(f.id)}
                        disabled={busy}
                        style={{ ...link(!busy, 'rgb(180,35,24)'), marginLeft: 10,
                          fontWeight: 600 }}
                      >
                        Really archive
                      </button>
                    ) : (
                      <button
                        onClick={() => { setConfirming(f.id); setError(null) }}
                        disabled={!canManage || busy}
                        title={canManage
                          ? 'Stop asking this question. Every answer already '
                            + 'recorded is kept and stays readable.'
                          : why}
                        style={{ ...link(canManage && !busy, 'rgb(180,35,24)'),
                          marginLeft: 10 }}
                      >
                        Archive
                      </button>
                    )}
                  </td>
                </tr>
              ))}
              {live.length === 0 && (
                <tr>
                  <td colSpan={6} style={{ padding: '12px 0', fontSize: 13,
                    color: 'rgb(152,162,179)' }}>
                    No job questions yet. Nothing is guessed for you.
                  </td>
                </tr>
              )}
            </tbody>
          </table>

          {archived.length > 0 && (
            <div style={{ marginTop: 20 }}>
              <div style={{ fontSize: 14, fontWeight: 600, color: 'rgb(16,24,40)' }}>
                Archived
              </div>
              <div style={{ fontSize: 12, color: 'rgb(102,112,133)', marginTop: 2 }}>
                Not asked on new deals. Every answer already given is still on the
                deal that holds it, and comes back editable if the field does.
              </div>
              {archived.map((f) => (
                <div key={f.id} className="mt-2 flex items-center gap-3">
                  <div className="min-w-0 flex-1">
                    <div style={{ fontSize: 14, color: 'rgb(102,112,133)' }}>
                      {f.label}
                    </div>
                    <div style={{ fontSize: 12, color: 'rgb(152,162,179)' }}>
                      <code>{f.key}</code>
                    </div>
                  </div>
                  <button
                    onClick={() => { setError(null); restore.mutate(f.id) }}
                    disabled={!canManage || busy}
                    title={canManage ? 'Ask this question again' : why}
                    style={link(canManage && !busy)}
                  >
                    Restore
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

/** One line per option, blanks dropped — the server de-duplicates and trims. */
function splitOptions(raw: string): string[] {
  return raw.split('\n').map((o) => o.trim()).filter(Boolean)
}

function PipelineChecks({
  all, chosen, disabled, why, onToggle,
}: {
  all: { id: number; name: string }[]
  chosen: number[]
  disabled: boolean
  why: string
  onToggle: (id: number, on: boolean) => void
}) {
  return (
    <div style={{ marginTop: 4 }}>
      {all.map((p) => (
        <label
          key={p.id}
          className="flex items-center gap-2"
          style={{ fontSize: 13, color: 'rgb(52,64,84)', padding: '2px 0',
            ...(disabled ? { opacity: 0.5 } : {}) }}
          title={disabled ? why : undefined}
        >
          <input
            type="checkbox"
            checked={chosen.includes(p.id)}
            disabled={disabled}
            onChange={(e) => onToggle(p.id, e.target.checked)}
          />
          {p.name}
        </label>
      ))}
    </div>
  )
}
