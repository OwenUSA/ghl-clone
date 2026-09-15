import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import {
  createTask, deleteTask, listTasks, listUsers, patchTask,
  type OpportunityTask, type TaskInput,
} from '../../lib/api'
import type { Me } from '../../lib/auth'
import { techOnOwnJob } from '../../lib/access'
import {
  AddBar, BODY, BORDER, BUTTON, DIVIDER, ErrorLine, FAINT, HEADING, INPUT, KebabMenu,
  Label, MUTED, PRIMARY, PRIMARY_BUTTON, Select, dead, noteStamp,
} from './ui'

/**
 * The Tasks tab. No GoHighLevel screenshot shows it, so it is built in the Notes
 * tab's shape (screenshot 22) — heading, a light-blue "+ Add task" bar, bordered
 * cards with a ⋮ menu — and listed as an assumption.
 *
 * A task is a title, a description, a due date and time, an assignee and done or
 * not done. It notifies NOBODY: the server enqueues no job for any task write.
 * Reading is open to every role; writing is STAFF, so a TECH sees the list with
 * the controls dead and a reason, never a form that 403s (d1f7c50, b943f4b).
 *
 * "Only assigned data" (2026-09-15): a technician on their own job may ADD a task and
 * tick off or reopen a task that is theirs — assigned to them or added by them. No
 * edit, no delete, and someone else's task stays dead with the reason.
 */

type Draft = { title: string; description: string; due: string; assignee: string }

const EMPTY: Draft = { title: '', description: '', due: '', assignee: '' }

/** `datetime-local` wants local wall-clock time with no zone. */
function toLocalInput(iso: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
    + `T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function toBody(d: Draft): TaskInput {
  return {
    title: d.title.trim(),
    description: d.description.trim() || null,
    // The browser reads a datetime-local value as LOCAL time, which is what the
    // dispatcher typed; toISOString sends it on as UTC.
    due_at: d.due ? new Date(d.due).toISOString() : null,
    assigned_user_id: d.assignee ? Number(d.assignee) : null,
  }
}

function TaskForm({ initial, users, busy, onCancel, onSave, saveLabel }: {
  initial: Draft
  users: { id: number; name: string }[]
  busy: boolean
  onCancel: () => void
  onSave: (d: Draft) => void
  saveLabel: string
}) {
  const [d, setD] = useState(initial)
  const set = (k: keyof Draft, v: string) => setD((x) => ({ ...x, [k]: v }))
  return (
    <div style={{ border: '1px solid ' + PRIMARY, borderRadius: 10, padding: 14, marginTop: 14 }}>
      <Label required>Title</Label>
      <input autoFocus value={d.title} maxLength={255} aria-label="Task title"
        onChange={(e) => set('title', e.target.value)} placeholder="Enter task title"
        style={INPUT} />
      <div style={{ marginTop: 12 }}>
        <Label>Description</Label>
        <textarea value={d.description} rows={3} aria-label="Task description"
          onChange={(e) => set('description', e.target.value)} placeholder="Enter description"
          style={{ ...INPUT, height: 'auto', padding: 10, resize: 'vertical' }} />
      </div>
      <div className="grid grid-cols-2 gap-3" style={{ marginTop: 12 }}>
        <div>
          <Label>Due date and time</Label>
          <input type="datetime-local" value={d.due} aria-label="Due date and time"
            onChange={(e) => set('due', e.target.value)} style={INPUT} />
        </div>
        <div>
          <Label>Assignee</Label>
          <Select value={d.assignee} onChange={(v) => set('assignee', v)} ariaLabel="Assignee">
            <option value="">Unassigned</option>
            {users.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
          </Select>
        </div>
      </div>
      <div className="mt-3 flex justify-end gap-2">
        <button type="button" style={{ ...BUTTON, height: 36 }} onClick={onCancel}>Cancel</button>
        <button type="button" disabled={!d.title.trim() || busy}
          style={{ ...PRIMARY_BUTTON, height: 36, ...dead(!!d.title.trim() && !busy) }}
          onClick={() => onSave(d)}>
          {saveLabel}
        </button>
      </div>
    </div>
  )
}

export function TasksTab({ opportunityId, user, startAdding = false }: {
  opportunityId: number
  user: Me
  /** The card's "Add task" icon opens this tab with the form already showing. */
  startAdding?: boolean
}) {
  const qc = useQueryClient()
  const canWrite = user.role !== 'TECH'
  const techJob = techOnOwnJob(user)
  const canAdd = canWrite || techJob
  const why = 'Your role cannot add or change tasks'
  const mine = (t: OpportunityTask) =>
    t.assigned_user_id === user.id || t.created_by_id === user.id
  const canTick = (t: OpportunityTask) => canWrite || (techJob && mine(t))
  const tickWhy = techJob
    ? 'A technician can complete only their own tasks — this one is someone else’s'
    : why
  const tasks = useQuery({
    queryKey: ['opportunity-tasks', opportunityId],
    queryFn: () => listTasks(opportunityId),
  })
  const users = useQuery({ queryKey: ['users'], queryFn: listUsers })
  const [adding, setAdding] = useState(startAdding && canAdd)
  const [editing, setEditing] = useState<number | null>(null)
  const [confirming, setConfirming] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)

  const refresh = () => {
    setError(null)
    qc.invalidateQueries({ queryKey: ['opportunity-tasks', opportunityId] })
    qc.invalidateQueries({ queryKey: ['opportunities'] })
  }
  const failed = (e: Error) => setError(e.message)
  const add = useMutation({
    mutationFn: (d: Draft) => createTask(opportunityId, toBody(d)),
    onSuccess: () => { setAdding(false); refresh() }, onError: failed,
  })
  const save = useMutation({
    mutationFn: ({ id, body }: { id: number; body: TaskInput }) => patchTask(id, body),
    onSuccess: () => { setEditing(null); refresh() }, onError: failed,
  })
  const remove = useMutation({
    mutationFn: (id: number) => deleteTask(id),
    onSuccess: () => { setConfirming(null); refresh() }, onError: failed,
  })
  const busy = add.isPending || save.isPending || remove.isPending
  const staff = (users.data ?? []).filter((u) =>
    (u as { is_active?: boolean }).is_active !== false)

  const open = (tasks.data ?? []).filter((t) => !t.done)
  const done = (tasks.data ?? []).filter((t) => t.done)

  const card = (t: OpportunityTask) => {
    if (editing === t.id) {
      return (
        <TaskForm key={t.id} users={staff} busy={busy} saveLabel="Save"
          initial={{ title: t.title, description: t.description ?? '',
            due: toLocalInput(t.due_at), assignee: t.assigned_user_id ? String(t.assigned_user_id) : '' }}
          onCancel={() => setEditing(null)}
          onSave={(d) => save.mutate({ id: t.id, body: toBody(d) })} />
      )
    }
    const overdue = !t.done && t.due_at != null && new Date(t.due_at).getTime() < Date.now()
    return (
      <div key={t.id} data-task-id={t.id}
        style={{ border: '1px solid ' + BORDER, borderRadius: 10, padding: '12px 14px', marginTop: 12 }}>
        <div className="flex items-start gap-3">
          <input
            type="checkbox"
            checked={t.done}
            disabled={!canTick(t) || busy}
            title={canTick(t) ? (t.done ? 'Reopen this task' : 'Mark as done') : tickWhy}
            aria-label={(t.done ? 'Reopen ' : 'Complete ') + t.title}
            onChange={() => save.mutate({ id: t.id, body: { done: !t.done } })}
            style={{ marginTop: 3, width: 16, height: 16, accentColor: PRIMARY,
              ...dead(canTick(t)) }}
          />
          <div className="min-w-0 flex-1">
            <div style={{ fontSize: 14, fontWeight: 500, color: BODY,
              textDecoration: t.done ? 'line-through' : undefined }}>
              {(t as { priority?: string }).priority === 'urgent' && (
                <span style={{ marginRight: 6, padding: '1px 6px', borderRadius: 10, fontSize: 11,
                  fontWeight: 600, color: 'rgb(180,35,24)', backgroundColor: 'rgb(254,243,242)' }}>
                  Urgent
                </span>
              )}
              {t.title}
            </div>
            {t.description && (
              <div style={{ fontSize: 13, color: MUTED, marginTop: 2, whiteSpace: 'pre-wrap' }}>
                {t.description}
              </div>
            )}
            <div className="flex flex-wrap gap-x-4" style={{ fontSize: 12, color: FAINT, marginTop: 6 }}>
              <span style={overdue ? { color: 'rgb(180,35,24)' } : undefined}>
                Due: {t.due_at ? noteStamp(t.due_at) : '--'}
              </span>
              <span>Assignee: {t.assigned_user_name ?? 'Unassigned'}</span>
              {(t as { created_by_ai?: string | null }).created_by_ai && (
                <span>Created by: {(t as { created_by_ai?: string | null }).created_by_ai}</span>
              )}
            </div>
          </div>
          {canWrite && (
            <KebabMenu label="Task actions" items={[
              { label: 'Edit', onClick: () => { setError(null); setEditing(t.id) } },
              { label: t.done ? 'Reopen' : 'Mark as done',
                onClick: () => save.mutate({ id: t.id, body: { done: !t.done } }) },
              { label: 'Delete', danger: true, onClick: () => setConfirming(t.id) },
            ]} />
          )}
        </div>
        {confirming === t.id && (
          <div role="alertdialog" className="flex items-center justify-between gap-2"
            style={{ borderTop: '1px solid ' + DIVIDER, marginTop: 10, paddingTop: 10 }}>
            <span style={{ fontSize: 13, color: 'rgb(180,35,24)' }}>Delete this task?</span>
            <span className="flex gap-2">
              <button type="button" style={{ ...BUTTON, height: 32 }}
                onClick={() => setConfirming(null)}>Keep it</button>
              <button type="button" disabled={busy}
                style={{ ...PRIMARY_BUTTON, height: 32, backgroundColor: 'rgb(217,45,32)',
                  borderColor: 'rgb(217,45,32)' }}
                onClick={() => remove.mutate(t.id)}>Delete task</button>
            </span>
          </div>
        )}
      </div>
    )
  }

  return (
    <div>
      <div style={{ ...HEADING, fontWeight: 600 }}>Tasks</div>
      <div style={{ marginTop: 14 }}>
        {adding ? (
          <TaskForm initial={EMPTY} users={staff} busy={busy} saveLabel="Add task"
            onCancel={() => setAdding(false)} onSave={(d) => add.mutate(d)} />
        ) : (
          <AddBar label="Add task" disabled={!canAdd} title={canAdd ? undefined : why}
            onClick={() => { setError(null); setAdding(true) }} />
        )}
      </div>
      <ErrorLine error={error ?? (tasks.error ? (tasks.error as Error).message : null)} />
      {tasks.data && tasks.data.length === 0 && !adding && (
        <div style={{ marginTop: 18, fontSize: 14, color: FAINT, textAlign: 'center' }}>
          No tasks yet.
        </div>
      )}
      {open.map(card)}
      {done.length > 0 && (
        <div style={{ fontSize: 13, fontWeight: 600, color: MUTED, marginTop: 20 }}>
          Completed ({done.length})
        </div>
      )}
      {done.map(card)}
    </div>
  )
}
