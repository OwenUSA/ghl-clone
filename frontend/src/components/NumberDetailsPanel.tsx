import { useState } from 'react'
import { AddContactDialog, type NewContactFields } from './AddContactDialog'
import { IconPlus } from './Icon'
import { PANE, type AdoptedThread, type ContactDetail, type ConversationSummary } from '../lib/api'
import type { Me } from '../lib/auth'

/**
 * The right-hand pane for a NUMBER-ONLY thread (2026-09-13): a phone number that
 * called or texted and is not a contact.
 *
 * The owner's rule is that such a number is never saved as a contact on its own, so
 * this pane must not look like an empty contact — a "Contact Details" card full of
 * "--" would read as a contact with its fields blanked. It shows what is TRUE: the
 * number, the name Quo's own contact book has for it (labelled as Quo's), that it is
 * not a contact, and the one action that changes that.
 *
 * Styling is the Contact Details panel's measured shell (bg, radius, the 44px header
 * and 14px/500 title, the label/value type) so the two kinds of thread sit in the
 * same frame. The content below the header is OURS: GoHighLevel has no number-only
 * thread to measure.
 */
const LABEL = { fontSize: 14, fontWeight: 400, color: 'rgb(102,112,133)' } as const
const VALUE = { fontSize: 14, fontWeight: 500, color: 'rgb(52,64,84)' } as const

/** "Bob Builder" -> first/last for the form's prefill. Quo's name is a SUGGESTION. */
export function prefillFromQuo(row: Pick<ConversationSummary, 'contact_phone' | 'quo_name'>):
  Partial<NewContactFields> {
  const name = (row.quo_name ?? '').trim()
  const [first, ...rest] = name ? name.split(/\s+/) : ['']
  return { phone: row.contact_phone ?? '', first_name: first, last_name: rest.join(' ') }
}

export function NotAContactPill() {
  return (
    <span
      title="This number is not saved as a contact."
      style={{
        padding: '1px 8px', borderRadius: 10, fontSize: 11, fontWeight: 500,
        color: 'rgb(71,84,103)', backgroundColor: 'rgb(242,244,247)',
        border: '1px solid rgb(234,236,240)', whiteSpace: 'nowrap',
      }}
    >
      Not a contact
    </span>
  )
}

export function FromQuo() {
  return (
    <span style={{ fontSize: 12, fontWeight: 400, color: 'rgb(102,112,133)', whiteSpace: 'nowrap' }}>
      from Quo
    </span>
  )
}

/** Staff create contacts (POST /api/contacts is STAFF), so a TECH gets the button
 *  disabled with the reason rather than a form that 403s on save. */
export function canAddContact(user: Me) {
  return user.role !== 'TECH'
}

export function NumberDetailsPanel({
  row, user, onAdopted,
}: {
  row: ConversationSummary
  user: Me
  /** Saving the contact moved this thread's history onto the contact's thread. */
  onAdopted: (contact: ContactDetail & { adopted_number_thread?: AdoptedThread }) => void
}) {
  const [adding, setAdding] = useState(false)
  const allowed = canAddContact(user)
  const phone = row.phone_display ?? row.contact_phone ?? ''

  return (
    <div
      className="flex h-full flex-col overflow-hidden"
      style={{
        width: PANE.panel,
        backgroundColor: 'rgb(247,249,253)',
        borderRadius: 8,
        borderLeft: '1px solid rgb(234,236,240)',
      }}
    >
      <div className="flex shrink-0 items-center px-4" style={{ height: 44 }}>
        <div style={{ fontSize: 14, fontWeight: 500, color: 'rgb(16,24,40)' }}>
          Contact Details
        </div>
      </div>

      <div className="mx-3 shrink-0 bg-white" style={{
        borderRadius: 8, border: '1px solid rgb(234,236,240)', padding: 12,
      }}>
        <div className="flex items-center gap-2" style={{ flexWrap: 'wrap' }}>
          <span style={{ fontSize: 14, fontWeight: 600, color: 'rgb(16,24,40)' }}>
            {row.quo_name || phone}
          </span>
          {row.quo_name && <FromQuo />}
        </div>
        <div className="mt-2"><NotAContactPill /></div>
        <div style={{ ...LABEL, marginTop: 12 }}>
          Calls and texts from this number are kept on this thread. It has not been
          saved as a contact, and nothing saves it automatically.
        </div>
        <button
          onClick={() => setAdding(true)}
          disabled={!allowed}
          title={allowed ? 'Save this number as a contact' : 'Only staff can add contacts'}
          className="mt-3 flex items-center justify-center gap-1"
          style={{
            width: '100%', height: 36, borderRadius: 6, fontSize: 14, fontWeight: 500,
            color: '#fff', backgroundColor: 'rgb(0,78,235)',
            ...(allowed ? {} : { opacity: 0.5, cursor: 'not-allowed' }),
          }}
        >
          <IconPlus size={16} color="#fff" />
          Add as contact
        </button>
      </div>

      <div className="px-4" style={{ marginTop: 20 }}>
        <div style={{ fontSize: 14, fontWeight: 500, color: 'rgb(16,24,40)' }}>Number</div>
        <div style={{ ...LABEL, marginTop: 12 }}>Phone</div>
        <div style={{ ...VALUE, marginTop: 4 }}>{phone || '--'}</div>
        {row.quo_name && (
          <>
            <div style={{ ...LABEL, marginTop: 16 }}>Name in Quo</div>
            <div style={{ ...VALUE, marginTop: 4 }}>{row.quo_name}</div>
          </>
        )}
      </div>

      {adding && (
        <AddContactDialog
          initial={prefillFromQuo(row)}
          onClose={() => setAdding(false)}
          onCreated={(c) => { setAdding(false); onAdopted(c) }}
        />
      )}
    </div>
  )
}
