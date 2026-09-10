import { useMutation, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { createContact, listContacts } from '../lib/api'
import type { Contact, ContactDetail } from '../lib/api'
import { NATIONAL_DIGITS, digits, sameNumber } from '../lib/phoneMatch'

/**
 * Add Contact.
 *
 * Lifted out of ContactsPage unchanged in layout, because it is now opened from
 * two places: the Contacts toolbar and the contact picker on the Add-opportunity
 * dialog. One definition of what a contact needs — a second form would drift
 * from this one the first time a field was added to either.
 *
 * Two things came with the move, both for the picker's sake and both harmless
 * on the Contacts page:
 *
 *  - `initial` prefills the form, so a dispatcher who searched for a number and
 *    found nobody does not type it a second time.
 *  - the phone field warns when the number is already on file. It **warns and
 *    never blocks**: a couple share a mobile, and a property manager is the
 *    contact number for a dozen addresses. Refusing the save would be wrong far
 *    more often than it would be right.
 */
export type NewContactFields = {
  first_name: string
  last_name: string
  phone: string
  email: string
  business_name: string
  source: string
}

const EMPTY: NewContactFields = {
  first_name: '', last_name: '', phone: '', email: '', business_name: '', source: '',
}

const FIELDS = [
  ['first_name', 'First name'], ['last_name', 'Last name'],
  ['phone', 'Phone'], ['email', 'Email'],
  ['business_name', 'Business name'], ['source', 'Contact source'],
] as const

export function AddContactDialog({
  initial,
  onClose,
  onCreated,
  onUseExisting,
}: {
  /** Prefill, e.g. the number that was searched for and not found. */
  initial?: Partial<NewContactFields>
  onClose: () => void
  /** The contact that was actually written, so the caller can select it. */
  onCreated: (contact: ContactDetail) => void
  /**
   * Offered beside the duplicate warning when the caller has somewhere to put
   * an existing contact. The Contacts page has nowhere, so it passes nothing
   * and the warning is informational there.
   */
  onUseExisting?: (contact: Contact) => void
}) {
  const [form, setForm] = useState<NewContactFields>({ ...EMPTY, ...initial })
  const [error, setError] = useState<string | null>(null)

  // Only look once there is a whole number to look for. Searching on every
  // keystroke of a half-typed number would warn about people who share nothing
  // but an area code, and a warning that is usually wrong gets ignored.
  const complete = digits(form.phone).length >= NATIONAL_DIGITS
  const dupes = useQuery({
    queryKey: ['contacts', 'duplicate-phone', digits(form.phone).slice(-NATIONAL_DIGITS)],
    queryFn: () => listContacts({ page: 1, page_size: 5, q: form.phone }),
    enabled: complete,
  })
  // The server matched the last ten digits; it also matched names and emails,
  // so confirm each candidate really is the same line before crying duplicate.
  const duplicate = complete
    ? (dupes.data?.items ?? []).find((c) => sameNumber(c.phone, form.phone))
    : undefined

  const create = useMutation({
    mutationFn: () => createContact(form),
    onSuccess: onCreated,
    onError: (e: Error) => setError(e.message),
  })

  return (
    <div
      className="fixed inset-0 z-40 flex items-center justify-center"
      style={{ backgroundColor: 'rgba(16,24,40,0.4)' }}
      onClick={onClose}
    >
      <div onClick={(e) => e.stopPropagation()} className="bg-white"
        style={{ width: 460, borderRadius: 8, padding: 20 }}>
        <div style={{ fontSize: 18, fontWeight: 600, color: 'rgb(16,24,40)' }}>Add Contact</div>
        {FIELDS.map(([k, label]) => (
          <div key={k} style={{ marginTop: 12 }}>
            <div style={{ fontSize: 14, color: 'rgb(102,112,133)' }}>{label}</div>
            <input
              value={form[k]}
              onChange={(e) => setForm((f) => ({ ...f, [k]: e.target.value }))}
              style={{
                width: '100%', height: 36, marginTop: 4, fontSize: 14,
                borderRadius: 6, border: '1px solid rgb(234,236,240)', padding: '0 10px',
              }}
            />
            {k === 'phone' && duplicate && (
              <div
                role="status"
                style={{
                  marginTop: 6, padding: '8px 10px', borderRadius: 6, fontSize: 13,
                  color: 'rgb(122,74,10)', backgroundColor: 'rgb(254,247,235)',
                  border: '1px solid rgb(247,220,180)',
                }}
              >
                <div>
                  {duplicate.name} already has this number.{' '}
                  {/* Said out loud, because the obvious reading of a duplicate
                      warning is "you cannot do this", and here you can. */}
                  Two people can share one — you can still create this contact.
                </div>
                {onUseExisting && (
                  <button
                    onClick={() => onUseExisting(duplicate)}
                    style={{
                      marginTop: 6, fontSize: 13, fontWeight: 500,
                      color: 'rgb(0,78,235)',
                    }}
                  >
                    Use {duplicate.name} instead
                  </button>
                )}
              </div>
            )}
          </div>
        ))}
        {error && <div style={{ fontSize: 13, color: 'rgb(217,45,32)', marginTop: 10 }}>{error}</div>}
        <div className="mt-5 flex justify-end gap-2">
          <button onClick={onClose}
            style={{ height: 36, padding: '0 14px', borderRadius: 6, fontSize: 14, border: '1px solid rgb(234,236,240)' }}>
            Cancel
          </button>
          <button onClick={() => { setError(null); create.mutate() }} disabled={create.isPending}
            style={{ height: 36, padding: '0 14px', borderRadius: 6, fontSize: 14, fontWeight: 500, color: '#fff', backgroundColor: 'rgb(0,78,235)' }}>
            Create
          </button>
        </div>
      </div>
    </div>
  )
}
