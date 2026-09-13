import { useState } from 'react'
import { ApiError, openContactConversation, type OpportunityDetail } from '../../lib/api'
import { describeBooking } from '../../lib/customFields'
import { canOpenRecords, openRecord } from '../../lib/openRecord'
import { BODY, BORDER, ErrorLine, FAINT, HEADING, MUTED, PRIMARY } from './ui'

/**
 * Associated objects: the primary contact, the additional contacts, the linked
 * appointments and the contact's conversation, each linking through.
 *
 * Contacts and the conversation open on their own pages through the shell's
 * `openRecord` (lib/openRecord.ts) and are drawn as links only when the shell has
 * registered it. Appointments open the shared appointment panel over the modal.
 */
function Row({ title, detail, action, onAction }: {
  title: string; detail?: string; action?: string; onAction?: () => void
}) {
  return (
    <div className="flex items-center gap-3"
      style={{ border: '1px solid ' + BORDER, borderRadius: 10, padding: '10px 14px', marginTop: 8 }}>
      <div className="min-w-0 flex-1">
        <div className="truncate" style={{ fontSize: 14, fontWeight: 500, color: BODY }}>{title}</div>
        {detail && <div className="truncate" style={{ fontSize: 12, color: MUTED }}>{detail}</div>}
      </div>
      {action && onAction && (
        <button type="button" onClick={onAction}
          style={{ fontSize: 14, fontWeight: 500, color: PRIMARY }}>{action}</button>
      )}
    </div>
  )
}

function Group({ label, count, children }: { label: string; count: number; children: React.ReactNode }) {
  return (
    <div style={{ marginTop: 18 }}>
      <div style={{ fontSize: 14, fontWeight: 600, color: MUTED }}>{label} ({count})</div>
      {count === 0
        ? <div style={{ fontSize: 13, color: FAINT, marginTop: 6 }}>None</div>
        : children}
    </div>
  )
}

export function AssociatedTab({ o, onOpenAppointment }: {
  o: OpportunityDetail
  onOpenAppointment: (id: number) => void
}) {
  const [error, setError] = useState<string | null>(null)
  const links = canOpenRecords()
  const people = (email: string | null, phone: string | null) =>
    [email, phone].filter(Boolean).join(' · ') || undefined

  const openThread = async () => {
    if (o.contact_id == null) return
    setError(null)
    try {
      const id = o.conversation_id
        ?? (await openContactConversation(o.contact_id)).conversation_id
      openRecord('conversations', id)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'The conversation could not be opened.')
    }
  }

  return (
    <div>
      <div style={{ ...HEADING, fontWeight: 600 }}>Associated objects</div>
      <ErrorLine error={error} />

      <Group label="Primary contact" count={o.contact_id != null ? 1 : 0}>
        <Row title={o.contact_name || '(no name)'}
          detail={people(o.contact_email, o.contact_phone)}
          action={links ? 'Open contact' : undefined}
          onAction={() => o.contact_id != null && openRecord('contacts', o.contact_id)} />
      </Group>

      <Group label="Additional contacts" count={o.additional_contacts.length}>
        {o.additional_contacts.map((c) => (
          <Row key={c.id} title={c.name || '(no name)'} detail={people(c.email, c.phone)}
            action={links ? 'Open contact' : undefined}
            onAction={() => openRecord('contacts', c.id)} />
        ))}
      </Group>

      <Group label="Appointments" count={o.appointments.length}>
        {o.appointments.map((a) => (
          <Row key={a.id} title={describeBooking(a.title, a.starts_at)}
            detail={(a.calendar_name ?? 'No calendar') + ' · ' + a.status}
            action="Open" onAction={() => onOpenAppointment(a.id)} />
        ))}
      </Group>

      <Group label="Conversation" count={o.contact_id != null ? 1 : 0}>
        <Row title={'Conversation with ' + (o.contact_name || 'this contact')}
          detail={o.conversation_id == null ? 'No messages yet' : undefined}
          action={links ? 'Open conversation' : undefined}
          onAction={openThread} />
      </Group>
    </div>
  )
}
