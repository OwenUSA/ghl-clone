import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import {
  centsFromDollars,
  getOpportunity,
  listCustomFields,
  listUsers,
  patchOpportunity,
  type OpportunityDetail as OppDetail,
  type Pipeline,
} from '../lib/api'
import { describeBooking } from '../lib/customFields'
import { CustomFieldAnswers } from './CustomFieldAnswers'
import { NewAppointmentDialog } from './NewAppointmentDialog'
import type { Me } from '../lib/auth'

/**
 * Opportunity detail.
 *
 * Measured from captures/opportunities/detail_modal.json (1440x900). GHL renders
 * this as a full screen at /opportunities/<id> — clicking a card NAVIGATES, it is
 * not an overlay, and Escape does NOT dismiss it (verified: only browser-back or
 * Cancel closes it). We render a dialog instead; that is a deliberate deviation,
 * recorded in DECISIONS.md, because v1 has no router.
 *
 * Measured layout — two columns at x=492 and x=839, left rail at x=244:
 *   rail          Tasks | Notes | Associated objects
 *   contact       Primary email ("Enter email") | Primary phone ("Enter phone")
 *                 Additional contacts (Max: 10) | Add additional contacts
 *   Opportunity name*  14px/500 rgb(52,64,84), red * rgb(217,45,32) — the only
 *                      required field
 *   Pipeline | Stage
 *   Status ("Open") | Value ("$" + "Please Input")
 *   Owner ("Unassigned") | Followers ("Add followers")
 *   Business name ("Enter business name") | Source ("Enter source")
 *   Expected Close Date ("Select Date") | Tags
 *   custom fields owen_campaign / owen_tracking_number / owen_call_id /
 *                 owen_is_new_caller — written by the telephony project
 *   footer        Created by / Created on / Audit log · Cancel | Update
 *   labels 14px/400 rgb(16,24,40); inputs 15px/400; values 14px rgb(52,64,84)
 *
 * Two blocks below the measured ones are OURS and have no capture behind them:
 * the JOB QUESTIONS (CustomFieldAnswers — the admin's own custom fields, plus the
 * read-only owen_* block that replaced the old free-text list), and APPOINTMENTS,
 * which lists the visits booked for this deal and books another.
 */
const STATUSES = ['open', 'won', 'lost', 'abandoned'] as const

const LABEL = { fontSize: 14, fontWeight: 400, color: 'rgb(16,24,40)' } as const
const INPUT = {
  width: '100%',
  height: 36,
  fontSize: 15,
  color: 'rgb(16,24,40)',
  borderRadius: 6,
  border: '1px solid rgb(234,236,240)',
  padding: '0 10px',
  marginTop: 6,
  backgroundColor: '#fff',
} as const

function Row({ children }: { children: React.ReactNode }) {
  return <div className="mb-5 grid grid-cols-2 gap-6">{children}</div>
}

export function OpportunityDetail({
  opportunityId,
  pipeline,
  user,
  onClose,
}: {
  opportunityId: number
  pipeline?: Pipeline
  /** Booking is STAFF, so the Book button is dead for a TECH with a reason. */
  user: Me
  onClose: () => void
}) {
  const qc = useQueryClient()
  const [draft, setDraft] = useState<Partial<OppDetail>>({})
  const [error, setError] = useState<string | null>(null)
  const [booking, setBooking] = useState(false)

  const { data: o } = useQuery({
    queryKey: ['opportunity', opportunityId],
    queryFn: () => getOpportunity(opportunityId),
  })
  const users = useQuery({ queryKey: ['users'], queryFn: listUsers })
  // Every definition, archived ones included: a deal holding an answer to an
  // archived field still has to be able to LABEL it, or the form renders
  // "roof_age: 14" as a bare key.
  const fields = useQuery({ queryKey: ['custom-fields'], queryFn: listCustomFields })

  useEffect(() => {
    setDraft({})
    setError(null)
    setBooking(false)
  }, [opportunityId])

  const save = useMutation({
    mutationFn: (body: Partial<OppDetail>) => patchOpportunity(opportunityId, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['opportunities'] })
      qc.invalidateQueries({ queryKey: ['pipelines'] })
      qc.invalidateQueries({ queryKey: ['opportunity', opportunityId] })
      qc.invalidateQueries({ queryKey: ['appointments'] })
      onClose()
    },
    onError: (e: Error) => setError(e.message),
  })

  if (!o) return null
  const v = <K extends keyof OppDetail>(k: K): OppDetail[K] =>
    (draft[k] !== undefined ? draft[k] : o[k]) as OppDetail[K]
  const set = (k: keyof OppDetail, val: unknown) =>
    setDraft((d) => ({ ...d, [k]: val }))

  const answers = (v('custom_fields') ?? {}) as Record<string, unknown>
  const canBook = user.role !== 'TECH'

  return (
    <>
    <div
      className="fixed inset-0 z-40 flex items-center justify-center"
      style={{ backgroundColor: 'rgba(16,24,40,0.4)' }}
      onClick={onClose}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="flex bg-white"
        style={{ width: 1000, maxHeight: '86vh', borderRadius: 8, overflow: 'hidden' }}
      >
        {/* left rail — measured labels; none implemented in v1 */}
        <div
          className="shrink-0"
          style={{ width: 200, backgroundColor: 'rgb(249,250,251)', padding: 16 }}
        >
          <div style={{ fontSize: 14, fontWeight: 600, color: 'rgb(16,24,40)', marginBottom: 12 }}>
            {v('title') || 'Opportunity'}
          </div>
          {['Tasks', 'Notes', 'Associated objects'].map((t) => (
            <div
              key={t}
              title="Present in GHL; not implemented in v1"
              style={{ fontSize: 14, color: 'rgb(152,162,179)', padding: '6px 0', cursor: 'not-allowed' }}
            >
              {t}
            </div>
          ))}
          <div style={{ fontSize: 12, color: 'rgb(52,64,84)', marginTop: 24 }}>
            Created by: {o.created_by ?? '--'}
          </div>
          <div style={{ fontSize: 12, color: 'rgb(52,64,84)', marginTop: 4 }}>
            Created on: {new Date(o.created_at).toLocaleString('en-US')}
          </div>
        </div>

        <div className="min-w-0 flex-1 overflow-y-auto" style={{ padding: 20 }}>
          <Row>
            <div>
              <div style={LABEL}>Primary email</div>
              <input readOnly value={o.contact_email ?? ''} placeholder="Enter email"
                style={{ ...INPUT, backgroundColor: 'rgb(249,250,251)' }} />
            </div>
            <div>
              <div style={LABEL}>Primary phone</div>
              <input readOnly value={o.contact_phone ?? ''} placeholder="Enter phone"
                style={{ ...INPUT, backgroundColor: 'rgb(249,250,251)' }} />
            </div>
          </Row>

          <div className="mb-5">
            <div style={{ fontSize: 14, fontWeight: 500, color: 'rgb(52,64,84)' }}>
              Opportunity name <span style={{ color: 'rgb(217,45,32)' }}>*</span>
            </div>
            <input
              value={v('title') ?? ''}
              onChange={(e) => set('title', e.target.value)}
              placeholder="Enter opportunity name"
              style={INPUT}
            />
          </div>

          <Row>
            <div>
              <div style={LABEL}>Pipeline</div>
              <input readOnly value={pipeline?.name ?? ''} style={{ ...INPUT, backgroundColor: 'rgb(249,250,251)' }} />
            </div>
            <div>
              <div style={LABEL}>Stage</div>
              <select
                value={v('stage_id')}
                onChange={(e) => set('stage_id', Number(e.target.value))}
                style={INPUT}
              >
                {pipeline?.stages.map((s) => (
                  <option key={s.id} value={s.id}>{s.name}</option>
                ))}
              </select>
            </div>
          </Row>

          <Row>
            <div>
              <div style={LABEL}>Status</div>
              <select value={v('status')} onChange={(e) => set('status', e.target.value)} style={INPUT}>
                {STATUSES.map((s) => (
                  <option key={s} value={s}>{s[0].toUpperCase() + s.slice(1)}</option>
                ))}
              </select>
            </div>
            <div>
              <div style={LABEL}>Value</div>
              <div className="flex items-center gap-1">
                <span style={{ fontSize: 14, color: 'rgb(52,64,84)', marginTop: 6 }}>$</span>
                <input
                  type="number"
                  min={0}
                  step="0.01"
                  value={(v('value_cents') ?? 0) / 100}
                  onChange={(e) => set('value_cents', centsFromDollars(e.target.value))}
                  placeholder="Please Input"
                  style={INPUT}
                />
              </div>
            </div>
          </Row>

          <Row>
            <div>
              <div style={LABEL}>Owner</div>
              <select
                value={v('owner_id') ?? ''}
                onChange={(e) => set('owner_id', e.target.value ? Number(e.target.value) : null)}
                style={INPUT}
              >
                <option value="">Unassigned</option>
                {users.data?.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
              </select>
            </div>
            <div>
              <div style={LABEL}>Followers</div>
              <input readOnly placeholder="Add followers"
                title="Followers are not modelled in v1"
                style={{ ...INPUT, backgroundColor: 'rgb(249,250,251)' }} />
            </div>
          </Row>

          <Row>
            <div>
              <div style={LABEL}>Business name</div>
              <input value={v('business_name') ?? ''} placeholder="Enter business name"
                onChange={(e) => set('business_name', e.target.value)} style={INPUT} />
            </div>
            <div>
              <div style={LABEL}>Source</div>
              <input value={v('source') ?? ''} placeholder="Enter source"
                onChange={(e) => set('source', e.target.value)} style={INPUT} />
            </div>
          </Row>

          <Row>
            <div>
              <div style={LABEL}>Expected Close Date</div>
              <input type="date" value={v('expected_close_date') ?? ''} placeholder="Select Date"
                onChange={(e) => set('expected_close_date', e.target.value)} style={INPUT} />
            </div>
            <div>
              <div style={LABEL}>Tags</div>
              <input readOnly title="Opportunity tags are not modelled in v1"
                style={{ ...INPUT, backgroundColor: 'rgb(249,250,251)' }} />
            </div>
          </Row>

          <CustomFieldAnswers
            defs={fields.data ?? []}
            pipelineId={o.pipeline_id}
            answers={answers}
            onChange={(next) => set('custom_fields', next)}
          />

          {/* OUR block, no capture behind it: the visits booked for this deal.
              Deliberately not on the board card — the board is already dense. */}
          <div className="mb-4">
            <div style={{ fontSize: 14, fontWeight: 500, color: 'rgb(16,24,40)',
              marginBottom: 8 }}>
              Appointments
            </div>
            {o.appointments.length === 0 ? (
              <div style={{ fontSize: 13, color: 'rgb(152,162,179)' }}>
                No visit booked for this deal yet.
              </div>
            ) : (
              o.appointments.map((a) => (
                <div key={a.id} style={{ fontSize: 14, color: 'rgb(52,64,84)',
                  padding: '4px 0' }}>
                  {describeBooking(a.title, a.starts_at)}
                  <span style={{ fontSize: 12, color: 'rgb(152,162,179)',
                    marginLeft: 8 }}>
                    {a.calendar_name ?? 'No calendar'}
                    {a.status !== 'confirmed' && ' · ' + a.status}
                  </span>
                </div>
              ))
            )}
            <button
              onClick={() => setBooking(true)}
              disabled={!canBook}
              title={canBook
                ? 'Book a visit for this deal'
                : 'Your role cannot create an appointment'}
              style={{
                marginTop: 8, height: 34, padding: '0 12px', borderRadius: 6,
                fontSize: 13, fontWeight: 500, color: 'rgb(0,78,235)',
                border: '1px solid rgb(234,236,240)',
                ...(canBook ? {} : { opacity: 0.5, cursor: 'not-allowed' }),
              }}
            >
              Book appointment
            </button>
          </div>

          {error && (
            <div style={{ fontSize: 13, color: 'rgb(217,45,32)', marginBottom: 8 }}>{error}</div>
          )}

          <div className="flex justify-end gap-2" style={{ borderTop: '1px solid rgb(234,236,240)', paddingTop: 14 }}>
            <button
              onClick={onClose}
              style={{
                height: 36, padding: '0 16px', borderRadius: 6, fontSize: 14, fontWeight: 500,
                color: 'rgb(52,64,84)', border: '1px solid rgb(234,236,240)',
              }}
            >
              Cancel
            </button>
            <button
              onClick={() => { setError(null); save.mutate(draft) }}
              disabled={save.isPending || Object.keys(draft).length === 0}
              style={{
                height: 36, padding: '0 16px', borderRadius: 6, fontSize: 14, fontWeight: 500,
                color: '#fff', backgroundColor: 'rgb(0,78,235)',
                opacity: Object.keys(draft).length === 0 ? 0.5 : 1,
              }}
            >
              Update
            </button>
          </div>
        </div>
      </div>
    </div>

      {/* Deliberately OUTSIDE the backdrop above. Nested inside it, every click in
          the booking dialog would bubble to that backdrop's onClick and close the
          deal behind it — the dialog would vanish mid-typing.

          The EXISTING create dialog, not a second one: it already validates the
          times and schedules the customer's reminders, and a booking made from a
          deal must be identical to one made on the calendar. The deal is locked;
          the contact and the title arrive prefilled and stay editable. */}
      {booking && (
        <NewAppointmentDialog
          initialStart={nextHour()}
          initialEnd={new Date(nextHour().getTime() + 3_600_000)}
          initialTitle={(v('title') ?? '') + ' — inspection'}
          initialContact={o.contact_id != null
            ? { id: o.contact_id, name: o.contact_name ?? '' }
            : null}
          lockedOpportunity={{ id: o.id, title: o.title }}
          user={user}
          onClose={() => setBooking(false)}
          onCreated={() => {
            setBooking(false)
            qc.invalidateQueries({ queryKey: ['opportunity', opportunityId] })
            // The new booking has to show on the calendar without a reload, and
            // the range key changes with every view, so invalidate the family.
            qc.invalidateQueries({ queryKey: ['appointments'] })
          }}
        />
      )}
    </>
  )
}

/** The top of the next hour — a sane default slot for a booking made from a deal. */
function nextHour(): Date {
  const at = new Date()
  at.setMinutes(0, 0, 0)
  at.setHours(at.getHours() + 1)
  return at
}
