import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { BLUE, INK, LINE } from './ai/aiUi'
import {
  followupCreate, followupForPhone, followupSequences, followupsStaff,
} from '../lib/api'
import { instantOf } from '../lib/accountTime'
import { relative, tenDigits, when } from '../lib/followups'
import { canOpenRecords, openRecord } from '../lib/openRecord'

const MUTED = 'rgb(102,112,133)'
const RED = 'rgb(180,35,24)'
const btn: React.CSSProperties = { height: 28, padding: '0 10px', borderRadius: 4, fontSize: 13,
  fontWeight: 500, border: `1px solid ${LINE}`, background: '#fff', color: INK }
const primary: React.CSSProperties = { ...btn, background: BLUE, borderColor: BLUE, color: '#fff' }
const input: React.CSSProperties = { border: `1px solid ${LINE}`, borderRadius: 4, padding: '5px 8px',
  fontSize: 13, color: INK, fontFamily: 'inherit', width: '100%' }

/** "2026-10-23" + "10:00" read in the account's zone (America/New_York), as an ISO instant. */
function startAt(date: string, time: string): string | null {
  if (!date) return null
  const [y, m, d] = date.split('-').map(Number)
  const [h, min] = (time || '09:00').split(':').map(Number)
  return instantOf({ year: y, month: m, day: d, hour: h, minute: min }).toISOString()
}

/**
 * Start following up a number (2026-11-11, app/cadences): the sequence (blank = the board's
 * default), when the first step counts from (blank = now, e.g. "call me Friday"), a note and who
 * owns it (blank = the person adding it).
 */
export function NewFollowupForm({ phone = '', onDone }: { phone?: string; onDone: (id: number | null) => void }) {
  const qc = useQueryClient()
  const seqs = useQuery({ queryKey: ['followup-sequences'], queryFn: followupSequences, staleTime: 60000 })
  const staff = useQuery({ queryKey: ['followups-staff'], queryFn: followupsStaff, staleTime: 300000 })
  const [f, setF] = useState({ phone, sequence: '', date: '', time: '09:00', note: '', assignee: '' })
  const [error, setError] = useState<string | null>(null)
  const create = useMutation({
    mutationFn: () => {
      const ten = tenDigits(f.phone)
      if (!ten) throw new Error('Type a 10-digit phone number.')
      return followupCreate({ phone: ten, sequence_id: f.sequence ? Number(f.sequence) : null,
        start_at: startAt(f.date, f.time), note: f.note || null,
        assigned_user_id: f.assignee ? Number(f.assignee) : null })
    },
    onSuccess: (d) => {
      qc.invalidateQueries({ queryKey: ['followups'] })
      qc.invalidateQueries({ queryKey: ['followup-phone'] })
      onDone(d.id)
    },
    onError: (e: Error) => setError(e.message),
  })
  const label: React.CSSProperties = { display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12, color: MUTED }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{ fontSize: 16, fontWeight: 600, color: INK }}>Follow up</div>
      <label style={label}>Phone number
        <input style={input} value={f.phone} placeholder="(954) 555-0123" onChange={(e) => setF({ ...f, phone: e.target.value })} />
      </label>
      <label style={label}>Sequence
        <select style={input} value={f.sequence} onChange={(e) => setF({ ...f, sequence: e.target.value })}>
          <option value="">The board's default</option>
          {seqs.data?.sequences.filter((s) => s.active).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
      </label>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
        <label style={label}>Start on (blank = now)
          <input type="date" style={input} value={f.date} onChange={(e) => setF({ ...f, date: e.target.value })} />
        </label>
        <label style={label}>at (New York time)
          <input type="time" style={input} value={f.time} disabled={!f.date} onChange={(e) => setF({ ...f, time: e.target.value })} />
        </label>
      </div>
      <label style={label}>Assigned to
        <select style={input} value={f.assignee} onChange={(e) => setF({ ...f, assignee: e.target.value })}>
          <option value="">Me</option>
          {staff.data?.users.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
        </select>
      </label>
      <label style={label}>Note
        <textarea rows={2} style={input} value={f.note} placeholder="e.g. asked us to call back Friday morning"
          onChange={(e) => setF({ ...f, note: e.target.value })} />
      </label>
      {error && <div role="alert" style={{ color: RED, fontSize: 13 }}>{error}</div>}
      <div style={{ display: 'flex', gap: 8 }}>
        <button type="button" style={primary} disabled={create.isPending} onClick={() => create.mutate()}>Start follow-up</button>
        <button type="button" style={btn} onClick={() => onDone(null)}>Cancel</button>
      </div>
    </div>
  )
}

/**
 * The "Follow up" button beside a contact or a number-only thread: shows the open follow-up if
 * there is one ("Following up · Call (me) Wed 10:00 AM"), else starts one in a small dialog.
 * Drawn only for the people who can open Follow-ups (ADMIN + unrestricted DISPATCHER).
 */
export function FollowUpButton({ phone, allowed }: { phone: string | null | undefined; allowed: boolean }) {
  const ten = phone ? tenDigits(phone) : null
  const [open, setOpen] = useState(false)
  const q = useQuery({ queryKey: ['followup-phone', ten], queryFn: () => followupForPhone(ten!),
    enabled: allowed && !!ten, staleTime: 30000 })
  if (!allowed || !ten) return null
  const fu = q.data?.followup
  return (
    <>
      {fu ? (
        <button type="button" style={{ ...btn, color: BLUE }} title={fu.next_at ? `${fu.next_step} ${when(fu.next_at)}` : undefined}
          onClick={() => canOpenRecords() && openRecord('followups', fu.id)}>
          Following up{fu.next_step ? ` · ${fu.next_step} ${relative(fu.next_at)}` : ''}
        </button>
      ) : (
        <button type="button" style={btn} onClick={() => setOpen(true)}>Follow up</button>
      )}
      {open && (
        <div role="dialog" aria-modal="true" aria-label="Follow up"
          style={{ position: 'fixed', inset: 0, zIndex: 90, background: 'rgba(16,24,40,0.35)', display: 'flex',
            alignItems: 'center', justifyContent: 'center', padding: 16 }}
          onMouseDown={(e) => { if (e.target === e.currentTarget) setOpen(false) }}>
          <div style={{ background: '#fff', borderRadius: 8, padding: 18, width: 'min(460px, 100%)', boxShadow: '0 12px 32px rgba(16,24,40,0.2)' }}>
            <NewFollowupForm phone={ten} onDone={() => { setOpen(false); q.refetch() }} />
          </div>
        </div>
      )}
    </>
  )
}
