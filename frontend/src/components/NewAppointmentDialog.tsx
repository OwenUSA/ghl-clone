import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import {
  createAppointment, listCalendars, listUsers,
} from '../lib/api'
import {
  dateInputValue, fromInputs, timeInputValue,
} from '../lib/calendarGrid'
import { ContactPicker } from './ContactPicker'

/**
 * Create a booking. ONE dialog with two entry points — the `New` button and a
 * double-click on an empty slot — so the two cannot drift apart.
 *
 * GHL's own appointment modal was never captured (captures/calendars/ holds the
 * Week view only), so unlike Contacts or the opportunity detail this layout is
 * OURS. It is deliberately built out of the same primitives as AddContactDialog
 * rather than guessed at from memory of GHL. See the 2026-09-09 amendment in
 * DECISIONS.md.
 *
 * Fields are exactly `AppointmentCreate` in backend/app/main.py. Creating is
 * STAFF, so the callers gate the entry points on the role — reaching this
 * component at all means the role was already checked.
 *
 * Editing and rescheduling live in AppointmentDetailDialog, which is what a
 * click on a booking opens. This dialog only ever creates: `POST` and `PATCH`
 * take different field sets (`status` among them) and rescheduling has to retire
 * the customer's superseded reminder jobs, which a create never does.
 */
const INPUT: React.CSSProperties = {
  width: '100%', height: 36, marginTop: 4, fontSize: 14,
  borderRadius: 6, border: '1px solid rgb(234,236,240)', padding: '0 10px',
}

const LABEL: React.CSSProperties = { fontSize: 14, color: 'rgb(102,112,133)' }

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ marginTop: 12 }}>
      <div style={LABEL}>{label}</div>
      {children}
    </div>
  )
}

export function NewAppointmentDialog({
  initialStart, initialEnd, onClose, onCreated,
}: {
  initialStart: Date
  initialEnd: Date
  onClose: () => void
  onCreated: () => void
}) {
  const [title, setTitle] = useState('')
  const [contact, setContact] = useState<{ id: number; name: string } | null>(null)
  const [calendarId, setCalendarId] = useState<string>('')
  const [userId, setUserId] = useState<string>('')
  const [notes, setNotes] = useState('')
  const [startDate, setStartDate] = useState(dateInputValue(initialStart))
  const [startTime, setStartTime] = useState(timeInputValue(initialStart))
  const [endDate, setEndDate] = useState(dateInputValue(initialEnd))
  const [endTime, setEndTime] = useState(timeInputValue(initialEnd))
  const [error, setError] = useState<string | null>(null)

  const calendars = useQuery({ queryKey: ['calendars'], queryFn: listCalendars })
  const users = useQuery({ queryKey: ['users'], queryFn: listUsers })

  // Escape closes, matching the opportunity overflow menu's precedent. The
  // backdrop click is on the wrapper below.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const starts = useMemo(() => fromInputs(startDate, startTime), [startDate, startTime])
  const ends = useMemo(() => fromInputs(endDate, endTime), [endDate, endTime])

  /**
   * The same three rules the backend enforces, checked here so the user is told
   * before a round-trip. The backend remains the thing that actually enforces
   * them — this is a courtesy, not the gate.
   */
  function invalid(): string | null {
    if (!title.trim()) return 'Give the appointment a title.'
    if (!starts || !ends) return 'Enter a start and an end.'
    if (ends <= starts) return 'The end has to be after the start.'
    return null
  }

  const create = useMutation({
    mutationFn: () =>
      createAppointment({
        title: title.trim(),
        starts_at: starts!.toISOString(),
        ends_at: ends!.toISOString(),
        contact_id: contact?.id ?? null,
        calendar_id: calendarId ? Number(calendarId) : null,
        assigned_user_id: userId ? Number(userId) : null,
        notes: notes.trim() || null,
      }),
    onSuccess: onCreated,
    // `e.message` is already a sentence: api.ts's readable() turns our own
    // HTTPException detail into one and never surfaces the wire body.
    onError: (e: Error) => setError(e.message),
  })

  function submit() {
    const why = invalid()
    setError(why)
    if (!why) create.mutate()
  }

  return (
    <div
      className="fixed inset-0 z-40 flex items-center justify-center"
      style={{ backgroundColor: 'rgba(16,24,40,0.4)' }}
      onClick={onClose}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        onDoubleClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label="New appointment"
        className="bg-white"
        style={{ width: 460, maxHeight: '88vh', overflowY: 'auto', borderRadius: 8, padding: 20 }}
      >
        <div style={{ fontSize: 18, fontWeight: 600, color: 'rgb(16,24,40)' }}>
          New appointment
        </div>

        <Field label="Title">
          <input
            autoFocus
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Roof inspection"
            style={INPUT}
          />
        </Field>

        <Field label="Contact">
          <ContactPicker picked={contact} onPick={setContact} />
        </Field>

        <Field label="Calendar">
          <select
            value={calendarId}
            onChange={(e) => setCalendarId(e.target.value)}
            style={INPUT}
          >
            <option value="">No calendar</option>
            {calendars.data?.map((c) => (
              <option key={c.id} value={c.id}>{c.name}</option>
            ))}
          </select>
        </Field>

        <Field label="Assigned to">
          <select value={userId} onChange={(e) => setUserId(e.target.value)} style={INPUT}>
            <option value="">Unassigned</option>
            {users.data?.map((u) => (
              <option key={u.id} value={u.id}>{u.name}</option>
            ))}
          </select>
        </Field>

        <div className="flex gap-3">
          <Field label="Starts">
            <div className="flex gap-2">
              <input type="date" value={startDate}
                onChange={(e) => setStartDate(e.target.value)} style={INPUT} />
              <input type="time" value={startTime}
                onChange={(e) => setStartTime(e.target.value)} style={INPUT} />
            </div>
          </Field>
          <Field label="Ends">
            <div className="flex gap-2">
              <input type="date" value={endDate}
                onChange={(e) => setEndDate(e.target.value)} style={INPUT} />
              <input type="time" value={endTime}
                onChange={(e) => setEndTime(e.target.value)} style={INPUT} />
            </div>
          </Field>
        </div>

        <Field label="Status">
          {/* Disabled rather than omitted, so the gap stays visible — the same
              choice DECISIONS.md records for the unimplemented Conversations
              filter types. `AppointmentCreate` has no `status`, so every booking
              made here lands on the model default. Offering a picker the server
              ignores would be worse than showing what will happen. */}
          <select
            disabled
            value="confirmed"
            title="POST /api/appointments does not accept a status; a new booking is always confirmed"
            style={{ ...INPUT, color: 'rgb(152,162,179)', backgroundColor: 'rgb(249,250,251)' }}
          >
            <option value="confirmed">Confirmed</option>
          </select>
        </Field>

        <Field label="Notes">
          <textarea
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            rows={2}
            style={{ ...INPUT, height: 'auto', padding: 8 }}
          />
        </Field>

        {error && (
          <div role="alert" style={{ fontSize: 13, color: 'rgb(217,45,32)', marginTop: 10 }}>
            {error}
          </div>
        )}

        <div className="mt-5 flex justify-end gap-2">
          <button
            onClick={onClose}
            style={{
              height: 36, padding: '0 14px', borderRadius: 6, fontSize: 14,
              border: '1px solid rgb(234,236,240)',
            }}
          >
            Cancel
          </button>
          <button
            onClick={submit}
            disabled={create.isPending}
            style={{
              height: 36, padding: '0 14px', borderRadius: 6, fontSize: 14,
              fontWeight: 500, color: '#fff', backgroundColor: 'rgb(0,78,235)',
              opacity: create.isPending ? 0.6 : 1,
            }}
          >
            {create.isPending ? 'Creating…' : 'Create'}
          </button>
        </div>
      </div>
    </div>
  )
}
