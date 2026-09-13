import { describeBooking } from '../../lib/customFields'
import type { LinkedAppointment } from '../../lib/api'
import { AddBar, BODY, BORDER, FAINT, HEADING, MUTED, PRIMARY } from './ui'

/**
 * Book or update appointment. The EXISTING flows, not new ones: "Book" opens the
 * shared create dialog locked to this deal, and "Update" opens the shared
 * appointment panel, which already reschedules and moves the reminders correctly
 * (DECISIONS.md, 2026-09-10). Both dialogs are rendered by OpportunityDetail
 * itself, OUTSIDE its backdrop — this tab only asks for them.
 */
export function AppointmentTab({ appointments, canBook, onBook, onOpen }: {
  appointments: LinkedAppointment[]
  canBook: boolean
  onBook: () => void
  onOpen: (appointmentId: number) => void
}) {
  return (
    <div>
      <div style={{ ...HEADING, fontWeight: 600 }}>Book or update appointment</div>
      <div style={{ marginTop: 14 }}>
        <AddBar label="Book appointment" onClick={onBook} disabled={!canBook}
          title={canBook ? 'Book a visit for this deal' : 'Your role cannot create an appointment'} />
      </div>
      {appointments.length === 0 && (
        <div style={{ marginTop: 18, fontSize: 14, color: FAINT, textAlign: 'center' }}>
          No visit booked for this deal yet.
        </div>
      )}
      {appointments.map((a) => (
        <div key={a.id} className="flex items-center gap-3"
          style={{ border: '1px solid ' + BORDER, borderRadius: 10, padding: '12px 14px',
            marginTop: 12 }}>
          <div className="min-w-0 flex-1">
            <div className="truncate" style={{ fontSize: 14, fontWeight: 500, color: BODY,
              textDecoration: a.status === 'cancelled' ? 'line-through' : undefined }}>
              {describeBooking(a.title, a.starts_at)}
            </div>
            <div style={{ fontSize: 12, color: MUTED, marginTop: 2 }}>
              {new Date(a.starts_at).toLocaleDateString('en-US',
                { month: 'short', day: 'numeric', year: 'numeric' })}
              {' · '}{a.calendar_name ?? 'No calendar'}{' · '}{a.status}
            </div>
          </div>
          <button type="button" onClick={() => onOpen(a.id)}
            style={{ fontSize: 14, fontWeight: 500, color: PRIMARY }}>
            Update
          </button>
        </div>
      ))}
    </div>
  )
}
