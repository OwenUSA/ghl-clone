import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import {
  ApiError, createAppointment, createBlockedTime, deleteBlockedTime, getBlockedTime,
  getContact, listCalendars, patchBlockedTime,
} from '../lib/api'
import {
  ACCOUNT_TIME_ZONE, US_TIME_ZONES, meetingDefaultAddress, zoneLabel,
} from '../lib/accountTime'
import type { Me } from '../lib/auth'
import { ContactPicker } from './ContactPicker'
import { DateTimeField } from './DateTimeField'
import {
  BODY, BORDER, BUTTON, DANGER, DIVIDER, FAINT, INPUT, Label, PRIMARY, PRIMARY_BUTTON,
  Select, TEXT, dead,
} from './opportunity/ui'

/**
 * GoHighLevel's "Book appointment" modal, rebuilt from the owner's screenshot 29
 * (2026-09-13). It replaces our own "New appointment" form (screenshot 28).
 *
 *   Book appointment                                                        ✕
 *   Appointment   Blocked off time
 *   ─────────────────────────────────────────────────────────────────────────
 *   Calendar                                  │ ┌ 👤 Select Contact * ────────┐
 *   Appointment title  {{contact.name}}       │ │ 🔍 Search by name, email, … │
 *   Add description                           │ └─────────────────────────────┘
 *   Date & time                               │ Internal notes
 *   ┌ Showing slots in this timezone: … ─────┐│ [+ Add internal note]
 *   │ GMT-04:00 America/New_York (EDT)       ││
 *   │ Start time           End time          ││
 *   └────────────────────────────────────────┘│
 *   Meeting location  ◉ Calendar default ○ Custom
 *   ─────────────────────────────────────────────────────────────────────────
 *   Status : [✓ Confirmed ▾]                          [Cancel] [Book appointment]
 *
 * The owner's decisions, each a departure from the screenshot or from what the
 * old dialog did:
 *   * No Default / Custom toggle and no working-hours slots: the user always picks
 *     the times, so the Start time / End time pickers are drawn directly.
 *   * No "Recurring event" — not built, so not drawn.
 *   * No Opportunity field and no Assigned to field. The server assigns the
 *     calendar's user. A booking made FROM a deal still links it: the deal arrives
 *     as `lockedOpportunity` and is sent without being drawn.
 *
 * ONE dialog, several doors: the calendar's New button, a double-click on an empty
 * slot, a click on blocked off time (which opens its tab), and the opportunity
 * modal's "Book or update appointment" tab. Creating is STAFF, so the callers gate
 * the doors on the role.
 *
 * The reminders are the server's business and are unchanged: a booking made here
 * schedules exactly what any other POST /api/appointments does. Editing and
 * rescheduling live in AppointmentDetailDialog.
 */
const STATUSES = [
  ['confirmed', 'Confirmed'], ['booked', 'Booked'], ['new', 'New'],
  ['showed', 'Showed'], ['no-show', 'No-show'], ['cancelled', 'Cancelled'],
  ['invalid', 'Invalid'], ['rescheduled', 'Rescheduled'],
] as const

type Tab = 'appointment' | 'blocked'

const SECTION: React.CSSProperties = { marginTop: 20 }

function zonesFor(browserZone: string): string[] {
  const zones = [ACCOUNT_TIME_ZONE, ...US_TIME_ZONES.filter((z) => z !== ACCOUNT_TIME_ZONE)]
  return zones.includes(browserZone) || !browserZone ? zones : [...zones, browserZone]
}

export function NewAppointmentDialog({
  initialStart, initialEnd, user, onClose, onCreated,
  initialTitle = '{{contact.name}}', initialContact = null, lockedOpportunity,
  initialTab = 'appointment', blockedTimeId,
}: {
  initialStart: Date
  initialEnd: Date
  /** The shared ContactPicker gates creating a contact on the role. */
  user: Me
  onClose: () => void
  /** Called after any write lands — a booking, or a block created, edited or deleted. */
  onCreated: () => void
  /** GoHighLevel prefills `{{contact.name}}`; the server resolves it on save. */
  initialTitle?: string
  initialContact?: { id: number; name: string } | null
  /**
   * Opened from an opportunity: the booking is for THIS deal. Not drawn — GoHighLevel's
   * modal has no Opportunity field — but always sent, so the visit is linked.
   */
  lockedOpportunity?: {
    id: number; title: string
    /** The card's saved address, which "Calendar default" prefers to the contact's. */
    address_street?: string | null; address_city?: string | null
    address_state?: string | null; address_postal_code?: string | null
  }
  initialTab?: Tab
  /** Open an existing blocked off time on its tab, to edit or delete it. */
  blockedTimeId?: number
}) {
  const editingBlock = blockedTimeId != null
  const [tab, setTab] = useState<Tab>(editingBlock ? 'blocked' : initialTab)
  const [calendarId, setCalendarId] = useState('')
  const [title, setTitle] = useState(initialTitle)
  const [describing, setDescribing] = useState(false)
  const [description, setDescription] = useState('')
  const browserZone = useMemo(() => Intl.DateTimeFormat().resolvedOptions().timeZone, [])
  const [zone, setZone] = useState(ACCOUNT_TIME_ZONE)
  const [starts, setStarts] = useState(initialStart)
  const [ends, setEnds] = useState(initialEnd)
  const [locationKind, setLocationKind] = useState<'calendar_default' | 'custom'>(
    'calendar_default')
  const [customLocation, setCustomLocation] = useState('')
  const [contact, setContact] = useState<{ id: number; name: string } | null>(initialContact)
  const [noting, setNoting] = useState(false)
  const [notes, setNotes] = useState('')
  const [status, setStatus] = useState('confirmed')
  const [blockTitle, setBlockTitle] = useState('')
  const [blockNotes, setBlockNotes] = useState('')
  const [error, setError] = useState<string | null>(null)
  /** The server's 409 sentence while it waits for "Book anyway". */
  const [overlap, setOverlap] = useState<string | null>(null)
  const [confirmDelete, setConfirmDelete] = useState(false)

  // Creating is STAFF and the doors are gated; a blocked off time can be OPENED by
  // anyone from the grid, so its controls are dead for a TECH, with the reason.
  const canWrite = user.role !== 'TECH'
  const why = 'Changing blocked off time is staff-only, and your role is TECH'

  const calendars = useQuery({ queryKey: ['calendars'], queryFn: listCalendars })
  const picked = useQuery({
    queryKey: ['contact', contact?.id],
    queryFn: () => getContact(contact!.id),
    enabled: contact != null,
  })
  const block = useQuery({
    queryKey: ['blocked-time', blockedTimeId],
    queryFn: () => getBlockedTime(blockedTimeId!),
    enabled: editingBlock,
  })

  // The first calendar is chosen, as GoHighLevel shows one chosen (screenshot 29).
  useEffect(() => {
    if (!calendarId && !editingBlock && calendars.data?.length) {
      setCalendarId(String(calendars.data[0].id))
    }
  }, [calendars.data, calendarId, editingBlock])

  // Seed the Blocked off time tab from the record, once.
  const [seeded, setSeeded] = useState(false)
  useEffect(() => {
    if (!block.data || seeded) return
    setSeeded(true)
    setBlockTitle(block.data.title)
    setBlockNotes(block.data.notes ?? '')
    setCalendarId(String(block.data.calendar_id))
    setStarts(new Date(block.data.starts_at))
    setEnds(new Date(block.data.ends_at))
  }, [block.data, seeded])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  /** Moving the start keeps the length of the visit, as a calendar does. */
  function moveStart(next: Date) {
    const length = ends.getTime() - starts.getTime()
    setStarts(next)
    setEnds(new Date(next.getTime() + (length > 0 ? length : 3_600_000)))
  }

  // The same preference the server applies: the card's address, else the contact's.
  const address = meetingDefaultAddress(lockedOpportunity, picked.data)
  const cardAddress = meetingDefaultAddress(lockedOpportunity, null)
  const hasCalendars = (calendars.data?.length ?? 0) > 0

  /**
   * The rules the backend enforces, checked here so the user hears them before a
   * round-trip. The backend stays the gate — the contact is required only here,
   * because the CLI and the telephony feed may still book without one.
   */
  function invalid(): string | null {
    if (tab === 'appointment') {
      if (hasCalendars && !calendarId) return 'Choose a calendar.'
      if (!contact) return 'Select a contact.'
      if (!title.trim()) return 'Give the appointment a title.'
      if (locationKind === 'custom' && !customLocation.trim())
        return 'Enter the meeting address, or use the calendar default.'
    } else {
      if (!calendarId) return 'Choose a calendar.'
      if (!blockTitle.trim()) return 'Give the blocked off time a title.'
    }
    if (ends <= starts) return 'The end time has to be after the start time.'
    return null
  }

  const create = useMutation({
    mutationFn: (allow: boolean) =>
      createAppointment({
        title: title.trim(),
        starts_at: starts.toISOString(),
        ends_at: ends.toISOString(),
        contact_id: contact?.id ?? null,
        calendar_id: calendarId ? Number(calendarId) : null,
        opportunity_id: lockedOpportunity?.id ?? null,
        notes: notes.trim() || null,
        description: description.trim() || null,
        location_kind: locationKind,
        location: locationKind === 'custom' ? customLocation.trim() : null,
        status,
        allow_blocked_time: allow,
      }),
    onSuccess: onCreated,
    // `e.message` is already a sentence: api.ts's readable() turns our own
    // HTTPException detail into one. A 409 is the blocked-time question, asked
    // in place with a way to answer it; the dialog stays open either way.
    onError: (e: Error) => {
      if (e instanceof ApiError && e.status === 409) setOverlap(e.message)
      else setError(e.message)
    },
  })

  const saveBlock = useMutation({
    mutationFn: () => {
      const body = {
        title: blockTitle.trim(), calendar_id: Number(calendarId),
        starts_at: starts.toISOString(), ends_at: ends.toISOString(),
        notes: blockNotes.trim() || null,
      }
      return editingBlock ? patchBlockedTime(blockedTimeId!, body) : createBlockedTime(body)
    },
    onSuccess: onCreated,
    onError: (e: Error) => setError(e.message),
  })

  const removeBlock = useMutation({
    mutationFn: () => deleteBlockedTime(blockedTimeId!),
    onSuccess: onCreated,
    onError: (e: Error) => { setConfirmDelete(false); setError(e.message) },
  })

  function submit() {
    const bad = invalid()
    setError(bad)
    setOverlap(null)
    if (bad) return
    if (tab === 'appointment') create.mutate(false)
    else saveBlock.mutate()
  }

  const busy = create.isPending || saveBlock.isPending || removeBlock.isPending

  const calendarSelect = (
    <Select value={calendarId} onChange={setCalendarId} ariaLabel="Calendar"
      disabled={tab === 'blocked' && !canWrite} title={tab === 'blocked' && !canWrite ? why : undefined}>
      {!hasCalendars && <option value="">No calendar</option>}
      {calendars.data?.map((c) => (
        <option key={c.id} value={c.id}>{c.name}</option>
      ))}
    </Select>
  )

  const dateAndTime = (
    <div style={SECTION}>
      <Label>Date &amp; time</Label>
      <div style={{ marginTop: 8, padding: 14, borderRadius: 8,
        backgroundColor: 'rgb(249,250,251)' }}>
        <div style={{ fontSize: 14, fontWeight: 500, color: BODY }}>
          Showing slots in this timezone: (Account timezone)
        </div>
        <Select value={zone} onChange={setZone} ariaLabel="Timezone">
          {zonesFor(browserZone).map((z) => (
            <option key={z} value={z}>{zoneLabel(starts, z)}</option>
          ))}
        </Select>
        <div className="flex gap-12" style={{ marginTop: 16 }}>
          <DateTimeField label="Start time" value={starts} onChange={moveStart} timeZone={zone}
            disabled={tab === 'blocked' && !canWrite} />
          <DateTimeField label="End time" value={ends} onChange={setEnds} timeZone={zone}
            disabled={tab === 'blocked' && !canWrite} />
        </div>
      </div>
    </div>
  )

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
        aria-label={editingBlock ? 'Blocked off time' : 'Book appointment'}
        className="flex flex-col bg-white"
        style={{ width: 'min(863px, 96vw)', height: 'min(738px, 94vh)', borderRadius: 8 }}
      >
        <div className="flex shrink-0 items-center justify-between"
          style={{ padding: '24px 24px 0' }}>
          <div style={{ fontSize: 18, fontWeight: 500, color: TEXT }}>
            {editingBlock ? 'Blocked off time' : 'Book appointment'}
          </div>
          <button type="button" onClick={onClose} aria-label="Close"
            className="flex items-center justify-center" style={{ width: 24, height: 24 }}>
            <svg width={20} height={20} viewBox="0 0 24 24" fill="none" stroke={BODY}
              strokeWidth={1.8} strokeLinecap="round" aria-hidden="true">
              <path d="M6 6l12 12M18 6 6 18" />
            </svg>
          </button>
        </div>

        {/* The two tabs. Opening an existing block goes straight to its tab and
            draws no tab row: there is nothing to switch to while editing one. */}
        <div role="tablist" className="flex shrink-0 gap-32"
          style={{ margin: '20px 24px 0', borderBottom: '1px solid ' + DIVIDER }}>
          {(editingBlock ? [['blocked', 'Blocked off time']] as const
            : [['appointment', 'Appointment'], ['blocked', 'Blocked off time']] as const)
            .map(([key, label]) => (
              <button key={key} type="button" role="tab" aria-selected={tab === key}
                onClick={() => { setTab(key); setError(null); setOverlap(null) }}
                style={{ height: 40, fontSize: 14, fontWeight: 600,
                  color: tab === key ? PRIMARY : TEXT, marginBottom: -1,
                  borderBottom: '2px solid ' + (tab === key ? PRIMARY : 'transparent') }}>
                {label}
              </button>
            ))}
        </div>

        <div className="flex min-h-0 flex-1" style={{ padding: '12px 24px 0' }}>
          {tab === 'appointment' ? (
            <>
              <div className="min-h-0 overflow-y-auto" style={{ flex: '0 0 55%', paddingRight: 16,
                paddingBottom: 16, borderRight: '1px solid ' + DIVIDER }}>
                <Label>Calendar</Label>
                {calendarSelect}

                <div style={SECTION}>
                  <Label>Appointment title</Label>
                  {/* The template stays visible until the booking is saved; the
                      server resolves it against the contact and stores the name. */}
                  <input value={title} onChange={(e) => setTitle(e.target.value)}
                    aria-label="Appointment title" style={INPUT} />
                  {describing ? (
                    <textarea value={description} autoFocus rows={3}
                      aria-label="Description" placeholder="Add a description"
                      onChange={(e) => setDescription(e.target.value)}
                      style={{ ...INPUT, height: 'auto', padding: '8px 12px' }} />
                  ) : (
                    <button type="button" onClick={() => setDescribing(true)}
                      style={{ marginTop: 10, fontSize: 14, fontWeight: 500, color: PRIMARY }}>
                      Add description
                    </button>
                  )}
                </div>

                {dateAndTime}

                <div style={SECTION}>
                  <Label>Meeting location</Label>
                  <div role="radiogroup" aria-label="Meeting location" className="flex"
                    style={{ marginTop: 14, gap: 60 }}>
                    {([['calendar_default', 'Calendar default'], ['custom', 'Custom']] as const)
                      .map(([key, label]) => (
                        <label key={key} className="flex items-center gap-8"
                          style={{ fontSize: 14, color: BODY, cursor: 'pointer' }}>
                          <input type="radio" name="meeting-location" checked={locationKind === key}
                            onChange={() => setLocationKind(key)}
                            style={{ width: 16, height: 16, accentColor: PRIMARY }} />
                          {label}
                        </label>
                      ))}
                  </div>
                  {locationKind === 'calendar_default' ? (
                    <div style={{ marginTop: 8, fontSize: 13, color: FAINT }}>
                      {cardAddress ?? (!contact ? "The selected contact's property address."
                        : picked.isLoading ? 'Looking up the address…'
                          : address ?? 'No address on file for this contact.')}
                    </div>
                  ) : (
                    <input value={customLocation} autoFocus aria-label="Custom location"
                      placeholder="Enter address"
                      onChange={(e) => setCustomLocation(e.target.value)} style={INPUT} />
                  )}
                </div>
              </div>

              <div className="min-h-0 overflow-y-auto" style={{ flex: 1, paddingLeft: 16,
                paddingBottom: 16 }}>
                <div style={{ border: '1px solid ' + DIVIDER, borderRadius: 8, padding: 10,
                  backgroundColor: 'rgb(252,252,253)' }}>
                  <div className="flex items-center gap-8" style={{ marginBottom: 10 }}>
                    <svg width={18} height={18} viewBox="0 0 24 24" fill="none" stroke={FAINT}
                      strokeWidth={1.6} strokeLinecap="round" aria-hidden="true">
                      <circle cx="12" cy="8" r="4" /><path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6" />
                    </svg>
                    <Label required>Select Contact</Label>
                  </div>
                  <ContactPicker value={contact} onChange={setContact} user={user}
                    variant="search" placeholder="Search by name, email, or phone" />
                </div>

                {/* STAFF-only, like every internal note in this app. Only staff can
                    reach this dialog to create; the detail panel hides the notes
                    from a TECH and the server returns none to one. */}
                <div style={SECTION}>
                  <Label>Internal notes</Label>
                  {noting ? (
                    <textarea value={notes} autoFocus rows={4} aria-label="Internal note"
                      placeholder="Only your team can see this"
                      onChange={(e) => setNotes(e.target.value)}
                      style={{ ...INPUT, height: 'auto', padding: '8px 12px' }} />
                  ) : (
                    <button type="button" onClick={() => setNoting(true)}
                      className="flex items-center gap-4"
                      style={{ ...BUTTON, height: 32, padding: '0 12px', marginTop: 8,
                        fontSize: 14, borderRadius: 6 }}>
                      <span aria-hidden="true" style={{ fontSize: 16 }}>+</span>Add internal note
                    </button>
                  )}
                </div>
              </div>
            </>
          ) : (
            <div className="min-h-0 overflow-y-auto" style={{ flex: '0 0 55%', paddingRight: 16,
              paddingBottom: 16, borderRight: '1px solid ' + DIVIDER }}>
              {editingBlock && block.isLoading && (
                <div style={{ fontSize: 14, color: FAINT }}>Loading…</div>
              )}
              <Label required>Title</Label>
              <input value={blockTitle} aria-label="Blocked off time title"
                placeholder="e.g. Crew day off" disabled={!canWrite}
                title={canWrite ? undefined : why}
                onChange={(e) => setBlockTitle(e.target.value)}
                style={{ ...INPUT, ...(canWrite ? {} : { backgroundColor: 'rgb(249,250,251)' }) }} />
              <div style={SECTION}>
                <Label required>Calendar</Label>
                {calendarSelect}
              </div>
              {dateAndTime}
              <div style={SECTION}>
                <Label>Note</Label>
                <textarea value={blockNotes} rows={3} aria-label="Blocked off time note"
                  placeholder="Optional" disabled={!canWrite} title={canWrite ? undefined : why}
                  onChange={(e) => setBlockNotes(e.target.value)}
                  style={{ ...INPUT, height: 'auto', padding: '8px 12px' }} />
              </div>
              <div style={{ marginTop: 12, fontSize: 13, color: FAINT }}>
                Nothing is sent to anyone. Booking over this time asks first.
              </div>
            </div>
          )}
        </div>

        {(error || overlap) && (
          <div role="alert" style={{ margin: '0 24px', padding: '10px 12px', borderRadius: 8,
            fontSize: 13, ...(overlap
              ? { color: 'rgb(181,71,8)', backgroundColor: 'rgb(255,250,235)',
                  border: '1px solid rgb(254,223,137)' }
              : { color: DANGER }) }}>
            {overlap ? (
              <div className="flex items-center gap-12">
                <span className="flex-1">{overlap}</span>
                <button type="button" onClick={() => setOverlap(null)}
                  style={{ ...BUTTON, height: 32, fontSize: 13 }}>Change time</button>
                <button type="button" onClick={() => create.mutate(true)}
                  disabled={create.isPending}
                  style={{ ...PRIMARY_BUTTON, height: 32, fontSize: 13 }}>
                  Book anyway
                </button>
              </div>
            ) : error}
          </div>
        )}

        <div className="flex shrink-0 items-center"
          style={{ marginTop: 12, padding: '16px 24px', borderTop: '1px solid ' + DIVIDER }}>
          {tab === 'appointment' && (
            <div className="flex items-center gap-12">
              <span style={{ fontSize: 14, fontWeight: 500, color: BODY }}>Status :</span>
              <div className="relative" style={{ width: 146 }}>
                <svg className="pointer-events-none absolute" style={{ left: 11, top: 10 }}
                  width={18} height={18} viewBox="0 0 24 24" fill="none" stroke={BODY}
                  strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <circle cx="12" cy="12" r="9" /><path d="m8.5 12 2.5 2.5 4.5-5" />
                </svg>
                <select value={status} onChange={(e) => setStatus(e.target.value)}
                  aria-label="Status"
                  style={{ ...INPUT, marginTop: 0, height: 38, appearance: 'none',
                    paddingLeft: 36, paddingRight: 28, fontWeight: 500, color: BODY,
                    borderColor: BORDER }}>
                  {STATUSES.map(([value, label]) => (
                    <option key={value} value={value}>{label}</option>
                  ))}
                </select>
                <svg className="pointer-events-none absolute" style={{ right: 10, top: 11 }}
                  width={16} height={16} viewBox="0 0 24 24" fill="none" stroke={BODY}
                  strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="m6 9 6 6 6-6" />
                </svg>
              </div>
            </div>
          )}

          <div className="ml-auto flex items-center gap-12">
            {editingBlock && (confirmDelete ? (
              <>
                <span style={{ fontSize: 13, color: DANGER }}>
                  Delete “{block.data?.title}”?
                </span>
                <button type="button" onClick={() => setConfirmDelete(false)}
                  style={{ ...BUTTON, height: 36 }}>Keep it</button>
                <button type="button" onClick={() => removeBlock.mutate()} disabled={busy}
                  style={{ ...PRIMARY_BUTTON, height: 36, backgroundColor: DANGER,
                    borderColor: DANGER }}>
                  {removeBlock.isPending ? 'Deleting…' : 'Delete'}
                </button>
              </>
            ) : (
              <button type="button" aria-label="Delete blocked off time"
                disabled={!canWrite} title={canWrite ? 'Delete blocked off time' : why}
                onClick={() => { setError(null); setConfirmDelete(true) }}
                className="flex items-center justify-center"
                style={{ width: 48, height: 36, borderRadius: 8, backgroundColor: '#fff',
                  border: '1px solid rgb(253,162,155)', ...dead(canWrite) }}>
                <svg width={18} height={18} viewBox="0 0 24 24" fill="none" stroke={DANGER}
                  strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6M10 11v6M14 11v6" />
                </svg>
              </button>
            ))}
            <button type="button" onClick={onClose} style={{ ...BUTTON, height: 36, padding: '0 14px' }}>
              Cancel
            </button>
            <button type="button" onClick={submit}
              disabled={busy || (tab === 'blocked' && !canWrite)}
              title={tab === 'blocked' && !canWrite ? why : undefined}
              style={{ ...PRIMARY_BUTTON, height: 36, padding: '0 14px',
                ...dead(!busy && (tab === 'appointment' || canWrite)) }}>
              {tab === 'appointment'
                ? (create.isPending ? 'Booking…' : 'Book appointment')
                : saveBlock.isPending ? 'Saving…'
                  : editingBlock ? 'Update' : 'Block off time'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
