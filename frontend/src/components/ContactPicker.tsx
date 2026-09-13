import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { listContacts } from '../lib/api'
import type { Me } from '../lib/auth'
import { prefillFrom } from '../lib/phoneMatch'
import { AddContactDialog } from './AddContactDialog'
import { IconPlus } from './Icon'

/**
 * Pick a contact, by name or by phone number, and create one on the spot.
 *
 * Two things the owner hit in real use drove this:
 *
 *  1. Typing a phone number the way the phone shows it found nobody. That half
 *     is the server's (`backend/app/phone_match.py`): the last ten digits match
 *     whatever format the row is stored in. This component just asks
 *     `/api/contacts?q=` and so inherits it — there is no second search here.
 *  2. A contact who is not in the CRM yet stopped the job. The dispatcher had
 *     to abandon the opportunity, go to Contacts, create the person, come back
 *     and start again. The `+` opens the real Add Contact dialog and selects
 *     what it created.
 *
 * Deliberately dumb about where it is used: it holds no opportunity state and
 * takes the selection as a prop, so the next screen to adopt it (the appointment
 * dialog, the global search) needs no changes here. It is currently adopted in
 * ONE place, the Add-opportunity dialog — the other pickers are being edited on
 * other branches right now and converting them is a separate, later change.
 *
 * 2026-09-13: the Book appointment modal adopts it with `variant="search"`, which
 * draws GoHighLevel's "Search by name, email, or phone" box (screenshot 29) — a
 * magnifier, the placeholder and a chevron, with the matches in a list under it.
 * Same query, same inline Add Contact; only the look differs.
 */
export type PickedContact = { id: number; name: string }

export function ContactPicker({
  value,
  onChange,
  user,
  hint,
  placeholder = 'Search contacts',
  disabled = false,
  variant = 'default',
}: {
  value: PickedContact | null
  onChange: (contact: PickedContact | null) => void
  /** For the role gate on creating: `POST /api/contacts` is STAFF-only. */
  user: Me
  /** Shown under the field when nothing is selected, e.g. "Optional". */
  hint?: string
  placeholder?: string
  /** A role that cannot write still sees who the booking is with, read-only. */
  disabled?: boolean
  /** `search`: GoHighLevel's Select Contact box, used by the Book appointment modal. */
  variant?: 'default' | 'search'
}) {
  const [q, setQ] = useState('')
  const [adding, setAdding] = useState(false)
  // What was just created, named. Creating a customer record is a real side
  // effect and it happens inside a dialog that then closes — without this the
  // only evidence is a name appearing in a field the user was already looking
  // past. Cleared when the selection changes, so it can never describe a
  // contact that is no longer the one selected.
  const [created, setCreated] = useState<string | null>(null)
  const qc = useQueryClient()

  // Mirrors `POST /api/contacts` (auth.STAFF) rather than letting a TECH fill in
  // six fields to be told 403 on submit — the precedent set by d1f7c50/b943f4b.
  const canCreate = user.role !== 'TECH' && !disabled
  const createTitle = canCreate ? 'Add a contact' : 'Your role cannot create contacts'

  const typed = q.trim()
  const matches = useQuery({
    queryKey: ['contacts', 'picker', typed],
    queryFn: () => listContacts({ page: 1, page_size: 6, q: typed }),
    enabled: !value && typed.length > 0 && !disabled,
  })
  const items = matches.data?.items ?? []
  // `isFetching` alone flickers "no contacts match" on every keystroke while the
  // next request is in flight; this asks whether we have an answer for what is
  // in the box right now.
  const answered = !matches.isFetching && matches.data !== undefined

  const select = (contact: PickedContact, note: string | null = null) => {
    onChange(contact)
    setCreated(note)
    setAdding(false)
    setQ('')
  }

  const input = {
    width: '100%', height: 36, marginTop: 4, fontSize: 14,
    borderRadius: 6, border: '1px solid rgb(234,236,240)', padding: '0 10px',
    backgroundColor: '#fff',
  } as const

  const plusButton = (label: string) => (
    <button
      onClick={() => setAdding(true)}
      disabled={!canCreate}
      title={createTitle}
      aria-label={createTitle}
      style={{
        height: 36, marginTop: 4, padding: '0 10px', borderRadius: 6,
        border: '1px solid rgb(234,236,240)', backgroundColor: '#fff',
        fontSize: 13, fontWeight: 500, color: 'rgb(0,78,235)',
        display: 'flex', alignItems: 'center', gap: 4, whiteSpace: 'nowrap',
        ...(canCreate ? {} : { opacity: 0.5, cursor: 'not-allowed' }),
      }}
    >
      <IconPlus size={16} color="rgb(0,78,235)" />
      {label}
    </button>
  )

  // The pieces both looks share, so the search variant cannot drift from the
  // default one: the no-match sentence with its `+`, the "created" note, and the
  // real Add Contact dialog.
  const noMatch = (
    <div style={{ padding: '8px 10px' }}>
      <div style={{ fontSize: 13, color: 'rgb(102,112,133)' }}>
        {answered ? `No contacts match “${typed}”.` : 'Searching…'}
      </div>
      {/* The `+` again, here, where the dead end actually is: this is the moment
          the dispatcher learns the person is not on file, and sending them to
          look for a button elsewhere is the whole friction being removed. The
          form opens prefilled. */}
      {answered && plusButton('Add “' + typed + '”')}
    </div>
  )

  const createdNote = created && (
    <div role="status" style={{ fontSize: 12, color: 'rgb(2,122,72)', marginTop: 4 }}>
      {created} created and selected
    </div>
  )

  const addDialog = adding && (
    <AddContactDialog
      // Whatever was typed into the search goes into the form: a number into
      // the phone field, a word into the name. Nobody should have to type a
      // phone number twice to record the person it belongs to.
      initial={prefillFrom(typed)}
      onClose={() => setAdding(false)}
      onCreated={(contact) => {
        select({ id: contact.id, name: contact.name }, contact.name)
        // The Contacts list and every other picker are now stale by exactly
        // one row.
        qc.invalidateQueries({ queryKey: ['contacts'] })
      }}
      // The duplicate warning offers the contact already on file. Selecting
      // it is the same act as picking it from the list above, so it ends the
      // dialog the same way — without creating anything.
      onUseExisting={(contact) => select({ id: contact.id, name: contact.name })}
    />
  )

  if (variant === 'search') {
    const box = {
      ...input, marginTop: 0, height: 38, borderRadius: 8,
      border: '1px solid rgb(208,213,221)', display: 'flex', alignItems: 'center',
      gap: 8, padding: '0 10px',
    } as const
    const glass = (
      <svg width={18} height={18} viewBox="0 0 24 24" fill="none" stroke="rgb(52,64,84)"
        strokeWidth={1.8} strokeLinecap="round" aria-hidden="true">
        <circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" />
      </svg>
    )
    const chevron = (
      <svg width={18} height={18} viewBox="0 0 24 24" fill="none" stroke="rgb(52,64,84)"
        strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="m6 9 6 6 6-6" />
      </svg>
    )
    return (
      <div className="relative">
        {value ? (
          <div style={box}>
            <span className="min-w-0 flex-1 truncate" style={{ color: 'rgb(16,24,40)' }}>
              {value.name}
            </span>
            {!disabled && (
              <button type="button" aria-label="Clear contact"
                onClick={() => { onChange(null); setCreated(null); setQ('') }}
                style={{ fontSize: 16, lineHeight: '16px', color: 'rgb(102,112,133)' }}>
                ×
              </button>
            )}
          </div>
        ) : (
          <label style={{ ...box, ...(disabled
            ? { backgroundColor: 'rgb(249,250,251)', cursor: 'not-allowed' } : {}) }}>
            {glass}
            <input
              value={q}
              disabled={disabled}
              aria-label="Select Contact"
              onChange={(e) => setQ(e.target.value)}
              placeholder={placeholder}
              className="min-w-0 flex-1"
              style={{ border: 'none', outline: 'none', fontSize: 14, height: 34,
                backgroundColor: 'transparent', color: 'rgb(16,24,40)' }}
            />
            {chevron}
          </label>
        )}

        {!value && typed.length > 0 && (
          <div role="listbox" className="absolute bg-white"
            style={{ left: 0, right: 0, top: 42, zIndex: 20, maxHeight: 220,
              overflowY: 'auto', borderRadius: 8, padding: 4,
              border: '1px solid rgb(234,236,240)',
              boxShadow: '0 12px 16px -4px rgba(16,24,40,0.08)' }}>
            {items.map((c) => (
              <button key={c.id} type="button" role="option" aria-selected={false}
                onClick={() => select({ id: c.id, name: c.name })}
                className="block w-full text-left hover:bg-[rgb(249,250,251)]"
                style={{ padding: '8px 10px', fontSize: 14, borderRadius: 6,
                  color: 'rgb(52,64,84)' }}>
                <div className="truncate">{c.name || '(no name)'}</div>
                {(c.email || c.phone) && (
                  <div className="truncate" style={{ fontSize: 12, color: 'rgb(102,112,133)' }}>
                    {[c.email, c.phone].filter(Boolean).join(' · ')}
                  </div>
                )}
              </button>
            ))}
            {items.length === 0 && noMatch}
          </div>
        )}

        {createdNote}
        {addDialog}
      </div>
    )
  }

  return (
    <>
      {value ? (
        <div
          className="flex items-center justify-between"
          style={{ ...input, display: 'flex', paddingRight: 6 }}
        >
          <span className="truncate" style={{ color: 'rgb(52,64,84)' }}>
            {value.name}
          </span>
          {!disabled && (
            <button
              onClick={() => { onChange(null); setCreated(null); setQ('') }}
              style={{ fontSize: 13, fontWeight: 500, color: 'rgb(0,78,235)', padding: '0 6px' }}
            >
              Clear
            </button>
          )}
        </div>
      ) : disabled ? (
        // Read-only with nothing picked: say so rather than offering a search
        // box that cannot be typed into.
        <div style={{ ...input, display: 'flex', alignItems: 'center', color: 'rgb(152,162,179)' }}>
          No contact
        </div>
      ) : (
        <>
          <div className="flex items-start gap-2">
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder={placeholder}
              style={input}
            />
            {plusButton('New')}
          </div>

          {typed.length > 0 && (
            <div
              style={{
                marginTop: 4, maxHeight: 148, overflowY: 'auto',
                border: '1px solid rgb(234,236,240)', borderRadius: 6,
              }}
            >
              {items.length > 0 ? (
                items.map((c) => (
                  <button
                    key={c.id}
                    onClick={() => select({ id: c.id, name: c.name })}
                    className="block w-full truncate text-left hover:bg-[rgb(249,250,251)]"
                    style={{ padding: '8px 10px', fontSize: 14, color: 'rgb(52,64,84)' }}
                  >
                    {c.name}
                    {c.phone ? ' · ' + c.phone : ''}
                  </button>
                ))
              ) : noMatch}
            </div>
          )}

          {hint && (
            <div style={{ fontSize: 12, color: 'rgb(102,112,133)', marginTop: 4 }}>
              {hint}
            </div>
          )}
        </>
      )}

      {createdNote}
      {addDialog}
    </>
  )
}
