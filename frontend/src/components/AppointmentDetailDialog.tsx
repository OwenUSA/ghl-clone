import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  cancelAppointment, getAppointment, listCalendars, listUsers, patchAppointment,
  type AppointmentDetail, type AppointmentPatch,
} from '../lib/api'
import type { Me } from '../lib/auth'
import { dateInputValue, fromInputs, timeInputValue } from '../lib/calendarGrid'
import { reminderSentence } from '../lib/reminders'
import { ContactPicker } from './ContactPicker'

/**
 * Appointment detail: what a click on a booking opens. Read it, edit it,
 * reschedule it, cancel it.
 *
 * THIS LAYOUT IS OURS, NOT MEASURED GHL. `captures/calendars/` holds one 1440x900
 * capture of the Week view and nothing else — GHL's appointment detail modal is
 * on the "still uncaptured … modals, drawers" list in DECISIONS.md and was never
 * opened on the live account. So there is no HTML, no screenshot and no computed
 * style to match, and nothing here may be cited as parity. It is built out of the
 * same primitives as NewAppointmentDialog and the opportunity detail, which is
 * the in-repo idiom, and it is recorded as ours in the 2026-09-10 amendment to
 * DECISIONS.md. Re-measure if a live GHL session is ever opened again.
 *
 * The grid it opens from IS measured, and is untouched: this adds an interaction,
 * not a redesign.
 *
 * RESCHEDULING IS DONE HERE, THROUGH THE FORM. Drag-to-move on the grid is
 * deliberately out of scope — the owner chose the form.
 *
 * The reminder trap, which is why editing was deferred once: reminder jobs dedupe
 * on a key that includes the start time, so a move has to retire the superseded
 * jobs or the customer silently gets NO reminder (CLAUDE.md). That is the
 * backend's job and it is asserted there by counting rows in `jobs`
 * (test_messaging.py). What this component owes the user is to SHOW what
 * happened: `PATCH` returns `automation`, and the panel prints it, so a
 * dispatcher who moves a roof inspection can see the reminder moved with it
 * instead of taking it on faith.
 */

/** The measured Appointment report tiles (DECISIONS.md), which is the status set
 *  a booking can be moved between. `blocked` is deliberately NOT offered: it is
 *  what the Manage view's "Blocked slots" filter selects, i.e. a slot that is not
 *  an appointment at all, and turning a customer's booking into one from a status
 *  dropdown would quietly move it out of the Appointments view. A booking that
 *  already carries an unlisted status keeps it as an option, so opening one
 *  cannot silently rewrite it. */
const STATUSES = ['booked', 'confirmed', 'new', 'showed', 'no-show',
                  'cancelled', 'invalid', 'rescheduled'] as const

const INPUT: React.CSSProperties = {
  width: '100%', height: 36, marginTop: 4, fontSize: 14,
  borderRadius: 6, border: '1px solid rgb(234,236,240)', padding: '0 10px',
}

const LABEL: React.CSSProperties = { fontSize: 14, color: 'rgb(102,112,133)' }

const OFF_INPUT: React.CSSProperties = {
  color: 'rgb(102,112,133)', backgroundColor: 'rgb(249,250,251)',
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ marginTop: 12 }}>
      <div style={LABEL}>{label}</div>
      {children}
    </div>
  )
}

/** The form's own shape: everything as the strings the inputs hold, so "did the
 *  user change this" is a string comparison and a start time that only differs by
 *  the microseconds the database kept cannot read as a reschedule. */
type Form = {
  title: string
  contact: { id: number; name: string } | null
  calendarId: string
  userId: string
  startDate: string
  startTime: string
  endDate: string
  endTime: string
  status: string
  notes: string
}

function formOf(a: AppointmentDetail): Form {
  const s = new Date(a.starts_at)
  const e = new Date(a.ends_at)
  return {
    title: a.title,
    contact: a.contact_id ? { id: a.contact_id, name: a.contact_name ?? '' } : null,
    calendarId: a.calendar_id ? String(a.calendar_id) : '',
    userId: a.assigned_user_id ? String(a.assigned_user_id) : '',
    startDate: dateInputValue(s),
    startTime: timeInputValue(s),
    endDate: dateInputValue(e),
    endTime: timeInputValue(e),
    status: a.status,
    notes: a.notes ?? '',
  }
}

const when = (d: Date) =>
  d.toLocaleString('en-US', {
    weekday: 'short', month: 'short', day: 'numeric',
    hour: 'numeric', minute: '2-digit',
  })

const clock = (d: Date) =>
  d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })

export function AppointmentDetailDialog({
  appointmentId, user, onClose, onChanged,
}: {
  appointmentId: number
  user: Me
  onClose: () => void
  /** Called after any write lands, so the host can refetch the grid. */
  onChanged: () => void
}) {
  const qc = useQueryClient()
  const [draft, setDraft] = useState<Form | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState<string | null>(null)
  const [confirmingCancel, setConfirmingCancel] = useState(false)

  // `PATCH` and `DELETE /api/appointments/{id}` are both auth.STAFF. Mirror that
  // here rather than let a TECH fill the form and be refused on submit — the
  // precedent d1f7c50 (Add Contact) and b943f4b set, and the same flag shape the
  // page already uses for `New`. Reading is ANY_USER, so a TECH still sees it.
  const canWrite = user.role !== 'TECH'
  const why = 'Editing an appointment is staff-only, and your role is TECH'

  const detail = useQuery({
    queryKey: ['appointment', appointmentId],
    queryFn: () => getAppointment(appointmentId),
  })
  const calendars = useQuery({ queryKey: ['calendars'], queryFn: listCalendars })
  const users = useQuery({ queryKey: ['users'], queryFn: listUsers })

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const a = detail.data
  // The draft wins once the user has touched anything. Every query in this app
  // refetches on a 30s interval (main.tsx), so re-seeding the form from `a` on
  // every change of `a` would wipe a half-typed edit twice a minute.
  const form = draft ?? (a ? formOf(a) : null)
  const set = (patch: Partial<Form>) =>
    form && setDraft({ ...form, ...patch })

  const starts = form ? fromInputs(form.startDate, form.startTime) : null
  const ends = form ? fromInputs(form.endDate, form.endTime) : null

  /**
   * Only what changed. `PATCH` uses `exclude_unset`, so an absent key leaves the
   * stored value alone — and an unchanged `starts_at` must stay absent, or every
   * save of a typo'd title would look like a reschedule and churn the customer's
   * reminders.
   */
  function changes(): AppointmentPatch | null {
    if (!a || !form) return null
    const was = formOf(a)
    const body: AppointmentPatch = {}
    if (form.title !== was.title) body.title = form.title.trim()
    if (form.status !== was.status) body.status = form.status
    if (form.notes !== was.notes) body.notes = form.notes.trim() || null
    if ((form.contact?.id ?? null) !== (was.contact?.id ?? null))
      body.contact_id = form.contact?.id ?? null
    if (form.calendarId !== was.calendarId)
      body.calendar_id = form.calendarId ? Number(form.calendarId) : null
    if (form.userId !== was.userId)
      body.assigned_user_id = form.userId ? Number(form.userId) : null
    // Both ends go together when either moves: the backend validates end-after-
    // start against the STORED values, so sending a new start alone can be
    // refused by an old end that the user can no longer see is the problem.
    if (form.startDate !== was.startDate || form.startTime !== was.startTime
        || form.endDate !== was.endDate || form.endTime !== was.endTime) {
      body.starts_at = starts!.toISOString()
      body.ends_at = ends!.toISOString()
    }
    return body
  }

  const body = a && form ? changes() : null
  const dirty = !!body && Object.keys(body).length > 0

  /** The same rules the backend enforces, so the user hears them before a
   *  round-trip. The backend stays the thing that actually enforces them. */
  function invalid(): string | null {
    if (!form) return null
    if (!form.title.trim()) return 'Give the appointment a title.'
    if (!starts || !ends) return 'Enter a start and an end.'
    if (ends <= starts) return 'The end has to be after the start.'
    return null
  }

  const save = useMutation({
    mutationFn: (patch: AppointmentPatch) => patchAppointment(appointmentId, patch),
    onSuccess: (updated) => {
      // Re-seed from what the server actually stored, so the panel shows the
      // saved record and not the draft that produced it.
      setDraft(formOf(updated))
      // ...and put it in the cache, because `a` — not the draft — is what the
      // summary line and the CANCEL CONFIRMATION are written from. Without this
      // the loaded record stays stale until the 30s refetch, so editing a
      // booking and then cancelling it showed a confirmation naming the OLD
      // title at the OLD time: a destructive prompt describing a slot that no
      // longer exists, which is exactly the prompt a user is entitled to trust.
      // `setQueryData` rather than `invalidateQueries`: the PATCH response IS
      // the fresh record, so refetching it would be a round-trip to learn what
      // we were just told, with a window of staleness in the middle.
      qc.setQueryData(['appointment', appointmentId], updated)
      setError(null)
      setSaved(reminderSentence(updated.automation))
      onChanged()
    },
    // `e.message` is a sentence: api.ts's readable() turns our own HTTPException
    // detail into one and never lets the wire body reach the screen.
    onError: (e: Error) => { setSaved(null); setError(e.message) },
  })

  const cancel = useMutation({
    mutationFn: () => cancelAppointment(appointmentId),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ['appointment', appointmentId] })
      onChanged()
      setConfirmingCancel(false)
      setError(null)
      setDraft(null)
      setSaved(
        res.reminders_cancelled > 0
          ? `Cancelled. ${res.reminders_cancelled} pending reminder${
              res.reminders_cancelled === 1 ? '' : 's'} withdrawn, so the customer `
            + 'will not be told the crew is coming.'
          : 'Cancelled. There were no pending reminders to withdraw.',
      )
    },
    onError: (e: Error) => { setConfirmingCancel(false); setError(e.message) },
  })

  function submit() {
    const bad = invalid()
    if (bad) { setSaved(null); setError(bad); return }
    const patch = changes()
    if (patch && Object.keys(patch).length) save.mutate(patch)
  }

  const busy = save.isPending || cancel.isPending

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
        aria-label="Appointment details"
        className="bg-white"
        style={{ width: 460, maxHeight: '88vh', overflowY: 'auto', borderRadius: 8, padding: 20 }}
      >
        <div className="flex items-center justify-between">
          <div style={{ fontSize: 18, fontWeight: 600, color: 'rgb(16,24,40)' }}>
            Appointment
          </div>
          <button onClick={onClose} aria-label="Close"
            style={{ fontSize: 16, color: 'rgb(102,112,133)' }}>✕</button>
        </div>

        {detail.error && (
          <div role="alert" style={{ fontSize: 13, color: 'rgb(217,45,32)', marginTop: 12 }}>
            {(detail.error as Error).message}
          </div>
        )}
        {!a && !detail.error && (
          <div style={{ fontSize: 14, color: 'rgb(152,162,179)', marginTop: 12 }}>
            Loading…
          </div>
        )}

        {a && form && (
          <>
            {/* What it is, in one line, before any of the controls: the panel has
                to answer "what did I just click on" without being read as a form. */}
            <div style={{
              marginTop: 12, padding: 10, borderRadius: 6,
              backgroundColor: 'rgb(249,250,251)',
            }}>
              <div className="truncate" style={{ fontSize: 14, fontWeight: 600, color: 'rgb(16,24,40)' }}>
                {a.title}
              </div>
              <div style={{ fontSize: 13, color: 'rgb(102,112,133)', marginTop: 2 }}>
                {when(new Date(a.starts_at))} – {clock(new Date(a.ends_at))}
              </div>
              <div style={{ fontSize: 13, color: 'rgb(102,112,133)', marginTop: 2 }}>
                {a.contact_name ?? 'No contact'}
                {' · '}
                {a.calendar_name ?? 'No calendar'}
                {' · '}
                <span style={{
                  color: a.status === 'cancelled' ? 'rgb(217,45,32)' : 'rgb(52,64,84)',
                  fontWeight: 500,
                }}>{a.status}</span>
              </div>
            </div>

            {!canWrite && (
              <div style={{ fontSize: 13, color: 'rgb(102,112,133)', marginTop: 10 }}>
                {why}. You can read the booking but not change it.
              </div>
            )}

            <Field label="Title">
              <input
                autoFocus={canWrite}
                value={form.title}
                disabled={!canWrite}
                onChange={(e) => set({ title: e.target.value })}
                title={canWrite ? undefined : why}
                style={canWrite ? INPUT : { ...INPUT, ...OFF_INPUT }}
              />
            </Field>

            <Field label="Contact">
              <ContactPicker
                value={form.contact}
                disabled={!canWrite}
                user={user}
                onChange={(c) => set({ contact: c })}
              />
            </Field>

            <Field label="Calendar">
              <select
                value={form.calendarId}
                disabled={!canWrite}
                onChange={(e) => set({ calendarId: e.target.value })}
                title={canWrite ? undefined : why}
                style={canWrite ? INPUT : { ...INPUT, ...OFF_INPUT }}
              >
                <option value="">No calendar</option>
                {calendars.data?.map((c) => (
                  <option key={c.id} value={c.id}>{c.name}</option>
                ))}
              </select>
            </Field>

            <Field label="Assigned to">
              <select
                value={form.userId}
                disabled={!canWrite}
                onChange={(e) => set({ userId: e.target.value })}
                title={canWrite ? undefined : why}
                style={canWrite ? INPUT : { ...INPUT, ...OFF_INPUT }}
              >
                <option value="">Unassigned</option>
                {users.data?.map((u) => (
                  <option key={u.id} value={u.id}>{u.name}</option>
                ))}
              </select>
            </Field>

            {/* Reschedule. Same two-input shape as the create dialog, because it
                is the same question; the difference is entirely in what the
                backend does with a start time that moved. */}
            <div className="flex gap-3">
              <Field label="Starts">
                <div className="flex gap-2">
                  <input type="date" value={form.startDate} disabled={!canWrite}
                    aria-label="Start date"
                    onChange={(e) => set({ startDate: e.target.value })}
                    style={canWrite ? INPUT : { ...INPUT, ...OFF_INPUT }} />
                  <input type="time" value={form.startTime} disabled={!canWrite}
                    aria-label="Start time"
                    onChange={(e) => set({ startTime: e.target.value })}
                    style={canWrite ? INPUT : { ...INPUT, ...OFF_INPUT }} />
                </div>
              </Field>
              <Field label="Ends">
                <div className="flex gap-2">
                  <input type="date" value={form.endDate} disabled={!canWrite}
                    aria-label="End date"
                    onChange={(e) => set({ endDate: e.target.value })}
                    style={canWrite ? INPUT : { ...INPUT, ...OFF_INPUT }} />
                  <input type="time" value={form.endTime} disabled={!canWrite}
                    aria-label="End time"
                    onChange={(e) => set({ endTime: e.target.value })}
                    style={canWrite ? INPUT : { ...INPUT, ...OFF_INPUT }} />
                </div>
              </Field>
            </div>

            <Field label="Status">
              {/* Live here, unlike the create dialog: `AppointmentPatch` DOES
                  accept a status and validates it against the measured set, so
                  this control is not one the server ignores. */}
              <select
                value={form.status}
                disabled={!canWrite}
                onChange={(e) => set({ status: e.target.value })}
                title={canWrite ? undefined : why}
                style={canWrite ? INPUT : { ...INPUT, ...OFF_INPUT }}
              >
                {(STATUSES as readonly string[]).includes(form.status)
                  ? null
                  : <option value={form.status}>{form.status}</option>}
                {STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
              </select>
            </Field>

            <Field label="Notes">
              <textarea
                value={form.notes}
                rows={2}
                disabled={!canWrite}
                onChange={(e) => set({ notes: e.target.value })}
                title={canWrite ? undefined : why}
                style={{
                  ...(canWrite ? INPUT : { ...INPUT, ...OFF_INPUT }),
                  height: 'auto', padding: 8,
                }}
              />
            </Field>

            {error && (
              <div role="alert" style={{ fontSize: 13, color: 'rgb(217,45,32)', marginTop: 10 }}>
                {error}
              </div>
            )}
            {saved && !error && (
              <div role="status" style={{ fontSize: 13, color: 'rgb(4,116,129)', marginTop: 10 }}>
                {saved}
              </div>
            )}

            {/* Cancelling is irreversible and customer-facing, so the confirmation
                NAMES the appointment — title, day and time — rather than asking
                "are you sure". Same two-step shape as the contact panel's delete. */}
            {confirmingCancel ? (
              <div
                role="alert"
                style={{
                  marginTop: 16, padding: 12, borderRadius: 6,
                  border: '1px solid rgb(254,205,202)',
                  backgroundColor: 'rgb(254,243,242)',
                }}
              >
                <div style={{ fontSize: 13, color: 'rgb(180,35,24)' }}>
                  Cancel <strong>{a.title}</strong>
                  {a.contact_name ? <> with <strong>{a.contact_name}</strong></> : null}
                  {' on '}<strong>{when(new Date(a.starts_at))}</strong>?
                  {' '}This cannot be undone from here, and any reminder the customer
                  was going to get is withdrawn with it.
                </div>
                <div className="mt-3 flex justify-end gap-2">
                  <button
                    onClick={() => setConfirmingCancel(false)}
                    style={{
                      height: 32, padding: '0 12px', borderRadius: 6, fontSize: 13,
                      border: '1px solid rgb(234,236,240)', backgroundColor: '#fff',
                    }}
                  >
                    Keep it
                  </button>
                  <button
                    onClick={() => cancel.mutate()}
                    disabled={cancel.isPending}
                    style={{
                      height: 32, padding: '0 12px', borderRadius: 6, fontSize: 13,
                      fontWeight: 500, color: '#fff', backgroundColor: 'rgb(217,45,32)',
                      opacity: cancel.isPending ? 0.6 : 1,
                    }}
                  >
                    {cancel.isPending ? 'Cancelling…' : 'Cancel appointment'}
                  </button>
                </div>
              </div>
            ) : (
              <div className="mt-5 flex items-center gap-2">
                <button
                  onClick={() => { setSaved(null); setConfirmingCancel(true) }}
                  disabled={!canWrite || a.status === 'cancelled' || busy}
                  title={canWrite
                    ? (a.status === 'cancelled'
                       ? 'This appointment is already cancelled'
                       : 'Cancel this appointment')
                    : why}
                  style={{
                    height: 36, padding: '0 12px', borderRadius: 6, fontSize: 14,
                    fontWeight: 500, color: 'rgb(180,35,24)',
                    border: '1px solid rgb(254,205,202)', backgroundColor: '#fff',
                    opacity: (!canWrite || a.status === 'cancelled') ? 0.5 : 1,
                    cursor: (!canWrite || a.status === 'cancelled')
                      ? 'not-allowed' : 'pointer',
                  }}
                >
                  Cancel appointment
                </button>
                <div className="ml-auto flex gap-2">
                  <button
                    onClick={onClose}
                    style={{
                      height: 36, padding: '0 14px', borderRadius: 6, fontSize: 14,
                      border: '1px solid rgb(234,236,240)',
                    }}
                  >
                    Close
                  </button>
                  <button
                    onClick={submit}
                    disabled={!canWrite || !dirty || save.isPending}
                    title={canWrite
                      ? (dirty ? 'Save these changes' : 'Nothing has been changed yet')
                      : why}
                    style={{
                      height: 36, padding: '0 14px', borderRadius: 6, fontSize: 14,
                      fontWeight: 500, color: '#fff', backgroundColor: 'rgb(0,78,235)',
                      opacity: (!canWrite || !dirty || save.isPending) ? 0.5 : 1,
                      cursor: (!canWrite || !dirty) ? 'not-allowed' : 'pointer',
                    }}
                  >
                    {save.isPending ? 'Saving…' : 'Save changes'}
                  </button>
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
