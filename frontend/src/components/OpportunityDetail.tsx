import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import {
  addContactTag,
  centsFromDollars,
  deleteOpportunity,
  getContact,
  getOpportunity,
  listCustomFields,
  listFieldGroups,
  listPipelines,
  listUsers,
  patchContact,
  patchOpportunity,
  removeContactTag,
  type OpportunityDetail as OppDetail,
  type OpportunityPatch,
  type Pipeline,
} from '../lib/api'
import {
  answeredCount, changedAnswers, isChecklistGroup, isEmptyAnswer, modalSections,
} from '../lib/customFields'
import {
  ADDRESS_FIELDS, addressChanges, addressForm, contactFallback, withContactAddress,
  type AddressForm,
} from '../lib/opportunityAddress'
import { takeModalTab, type ModalTab } from '../lib/opportunityModal'
import { SETTINGS_SECTIONS } from '../lib/settingsSections'
import { AppointmentDetailDialog } from './AppointmentDetailDialog'
import { CustomFieldAnswers, type LinkedValues } from './CustomFieldAnswers'
import { NewAppointmentDialog } from './NewAppointmentDialog'
import { AppointmentTab } from './opportunity/AppointmentTab'
import { AssociatedTab } from './opportunity/AssociatedTab'
import { PhotosTab } from './CompanyCamPhotos'
import { NotesTab } from './opportunity/NotesTab'
import { Chip, ContactSelect, MultiSelect, type Choice } from './opportunity/Pickers'
import { TasksTab } from './opportunity/TasksTab'
import {
  BODY, BUTTON, DANGER, DIVIDER, ErrorLine, FAINT, HEADING, INPUT, Label, MUTED,
  PRIMARY, PRIMARY_BUTTON, PRIMARY_TINT, Select, TEXT, dead, footerStamp,
} from './opportunity/ui'
import type { Me } from '../lib/auth'
import { techOnOwnJob } from '../lib/access'

/**
 * The opportunity modal — GoHighLevel's "Edit "<name>"" dialog, rebuilt from the
 * owner's screenshots 11, 12 and 22 (2026-09-13). It replaces our earlier
 * two-column form (screenshot 13).
 *
 *   Edit "<name>"                                                          ✕
 *   Add and edit opportunity details, tasks, notes and appointments.
 *   ─────────────────────────────────────────────────────────────────────────
 *   Opportunity details   │ Contact details  ▣              ☐ Hide empty fields
 *   <custom tab> …        │ Primary contact name * | Primary email
 *   Book or update appt.  │ Primary phone          | Additional contacts (Max: 10)
 *   Tasks                 │ Opportunity details
 *   Notes                 │ Opportunity name *
 *   Associated objects    │ Pipeline | Stage · Status | Value · Owner | Followers
 *                         │ Business name | Source · Expected close date | Tags
 *   ⚙ Manage fields       │ …custom fields with no group
 *                         │ Address                          [Use contact address]
 *                         │ Street address | City · State | Zip code
 *   ─────────────────────────────────────────────────────────────────────────
 *   Created by: …                                       [🗑] [Cancel] [Update]
 *   Created on: Sep 13 2026, 9:17am (EDT)
 *
 * Deliberate departures from the screenshots, each one the owner's:
 *   * No Payments tab — Payments is out of the product (DECISIONS.md, 2026-09-10).
 *   * No "Audit log" id in the footer — this app keeps no audit log, and a made-up
 *     id would be a lie.
 *   * No `owen_*` / `workiz_*` field, anywhere — his old account's fields. Their
 *     values stay on the deal untouched: Update sends only the answers that
 *     CHANGED, and the server keeps every reserved key regardless.
 *
 * GoHighLevel opens this as a page at /opportunities/<id>; we render a dialog,
 * because there is no router (DECISIONS.md, "Deliberate deviation").
 */
const STATUSES = ['open', 'won', 'lost', 'abandoned'] as const

/** Settings → Custom Fields, from the one table of Settings sections. */
export const CUSTOM_FIELDS_PATH = SETTINGS_SECTIONS.find((s) => s.key === 'custom-fields')!.path

type Form = {
  title: string
  contact: Choice | null
  email: string | null
  phone: string | null
  additional: Choice[]
  pipelineId: number
  /** null after a pipeline change, until a stage in the new pipeline is chosen. */
  stageId: number | null
  status: string
  value: string
  ownerId: string
  followers: Choice[]
  businessName: string
  source: string
  closeDate: string
  /** The deal's own probability as typed, '' for none. */
  probability: string
  answers: Record<string, unknown>
  /** The JOB's address — the card's own, never seeded from the contact. */
  address: AddressForm
}

function formFrom(o: OppDetail): Form {
  return {
    title: o.title,
    contact: o.contact_id != null ? { id: o.contact_id, name: o.contact_name ?? '' } : null,
    email: null,
    phone: null,
    additional: o.additional_contacts.map((c) => ({ id: c.id, name: c.name })),
    pipelineId: o.pipeline_id,
    stageId: o.stage_id,
    status: o.status,
    value: String((o.value_cents ?? 0) / 100),
    ownerId: o.owner_id != null ? String(o.owner_id) : '',
    followers: o.followers.map((f) => ({ id: f.id, name: f.name })),
    businessName: o.business_name ?? '',
    source: o.source ?? '',
    closeDate: o.expected_close_date ?? '',
    probability: o.probability != null ? String(o.probability) : '',
    answers: { ...(o.custom_fields ?? {}) },
    address: addressForm(o),
  }
}

const sameIds = (a: Choice[], b: { id: number }[]) =>
  a.length === b.length && a.every((x, i) => x.id === b[i].id)

/** Only what changed. Never a reserved key, never an answer nobody touched.
    `probabilityShown` is false when the (new) pipeline does not use
    opportunity-level probability: a field that is not drawn sends nothing. */
function changes(o: OppDetail, f: Form, probabilityShown = false): OpportunityPatch {
  const body: OpportunityPatch = {}
  if (probabilityShown) {
    const typed = f.probability.trim() === '' ? null : Number(f.probability)
    if (typed !== o.probability) body.probability = typed
  }
  if (f.title !== o.title) body.title = f.title
  if (f.contact && f.contact.id !== o.contact_id) body.contact_id = f.contact.id
  if (f.pipelineId !== o.pipeline_id) body.pipeline_id = f.pipelineId
  if (f.stageId != null && (f.stageId !== o.stage_id || f.pipelineId !== o.pipeline_id)) {
    body.stage_id = f.stageId
  }
  if (f.status !== o.status) body.status = f.status
  const cents = centsFromDollars(f.value)
  if (cents !== o.value_cents) body.value_cents = cents
  const owner = f.ownerId ? Number(f.ownerId) : null
  if (owner !== o.owner_id) body.owner_id = owner
  if (!sameIds(f.followers, o.followers)) body.follower_ids = f.followers.map((x) => x.id)
  if (!sameIds(f.additional, o.additional_contacts)) {
    body.additional_contact_ids = f.additional.map((x) => x.id)
  }
  if (f.businessName !== (o.business_name ?? '')) body.business_name = f.businessName || null
  if (f.source !== (o.source ?? '')) body.source = f.source || null
  if (f.closeDate !== (o.expected_close_date ?? '')) {
    body.expected_close_date = f.closeDate || null
  }
  const answers = changedAnswers(o.custom_fields ?? {}, f.answers)
  if (Object.keys(answers).length) body.custom_fields = answers
  Object.assign(body, addressChanges(o, f.address))
  return body
}

/** One entry of the modal's left nav. Shared with the Add new opportunity modal. */
export function NavItem({ label, active, onClick }: { label: string; active: boolean; onClick: () => void }) {
  return (
    <button type="button" onClick={onClick} aria-current={active ? 'page' : undefined}
      className="block w-full truncate text-left"
      style={{
        fontSize: 13, lineHeight: '18px', padding: '6px 8px', borderRadius: 6, marginBottom: 2,
        color: active ? 'rgb(31,58,138)' : MUTED, fontWeight: active ? 600 : 400,
        backgroundColor: active ? PRIMARY_TINT : 'transparent',
      }}>
      {label}
    </button>
  )
}

export function OpportunityDetail({
  opportunityId,
  pipeline: _boardPipeline,
  user,
  onClose,
}: {
  opportunityId: number
  /** The board's pipeline. Kept for the page's call; the modal loads every
      pipeline itself, because Pipeline is editable here. */
  pipeline?: Pipeline
  /** Booking, editing and deleting are role-gated; see each control. */
  user: Me
  onClose: () => void
}) {
  const qc = useQueryClient()
  const [request, setRequest] = useState(() => takeModalTab(opportunityId))
  const [tab, setTab] = useState<ModalTab>(request.tab)
  const [form, setForm] = useState<Form | null>(null)
  // Which deal the form was seeded from — see the seeding effect below.
  const [seededFor, setSeededFor] = useState<number | null>(null)
  const [hideEmpty, setHideEmpty] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [booking, setBooking] = useState(false)
  const [openAppointment, setOpenAppointment] = useState<number | null>(null)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [newTag, setNewTag] = useState('')
  const scroller = useRef<HTMLDivElement>(null)

  const { data: o } = useQuery({
    queryKey: ['opportunity', opportunityId],
    queryFn: () => getOpportunity(opportunityId),
  })
  const users = useQuery({ queryKey: ['users'], queryFn: listUsers })
  const pipelines = useQuery({ queryKey: ['pipelines'], queryFn: listPipelines })
  // Every definition, archived ones included; askedOn() decides what is drawn.
  const fields = useQuery({ queryKey: ['custom-fields'], queryFn: listCustomFields })
  const groups = useQuery({ queryKey: ['custom-field-groups'], queryFn: listFieldGroups })
  const contact = useQuery({
    queryKey: ['contact', form?.contact?.id],
    queryFn: () => getContact(form!.contact!.id),
    enabled: form?.contact != null,
  })

  // Seed the form once per deal; a background refetch must not wipe an edit. The
  // page keeps this component mounted when the ctrl+K palette asks for another
  // deal, so a change of id starts again, on that deal's own requested tab.
  useEffect(() => {
    if (!o || o.id !== opportunityId || seededFor === opportunityId) return
    if (seededFor != null) {
      const next = takeModalTab(opportunityId)
      setRequest(next)
      setTab(next.tab)
      setError(null)
      setConfirmDelete(false)
      setBooking(false)
      setOpenAppointment(null)
    }
    setForm(formFrom(o))
    setSeededFor(opportunityId)
  }, [o, opportunityId, seededFor])

  // The card's tags icon: land on Opportunity details, scrolled to Tags.
  useEffect(() => {
    if (request.focus !== 'tags' || !form) return
    const el = scroller.current?.querySelector('[data-field="tags"]')
    el?.scrollIntoView({ block: 'center' })
  }, [request.focus, form])

  const canEdit = user.role !== 'TECH'
  const canDelete = user.role === 'ADMIN'
  const canBook = user.role !== 'TECH'
  // "Only assigned data" (2026-09-15): a technician on their own job — and it IS their
  // own, or the server would have answered 404 — answers its questions, moves its stage
  // and reads and adds its notes. Nothing else here; the server refuses the rest.
  const techJob = techOnOwnJob(user)
  const canAnswer = canEdit || techJob
  const canStage = canEdit || techJob
  const seesNotes = user.role !== 'TECH' || techJob
  // A field a technician cannot change reads as read-only, like the Select's disabled look.
  const readOnly: React.CSSProperties = canEdit ? {} : { backgroundColor: 'rgb(249,250,251)',
    cursor: 'not-allowed' }
  const why = techJob
    ? 'A technician can answer this job’s questions and change its stage, not its other details'
    : 'Your role cannot edit opportunities'

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['opportunities'] })
    qc.invalidateQueries({ queryKey: ['pipelines'] })
    qc.invalidateQueries({ queryKey: ['opportunity', opportunityId] })
    qc.invalidateQueries({ queryKey: ['appointments'] })
  }

  const save = useMutation({
    mutationFn: async () => {
      if (!o || !form) return
      // The contact's own email and phone are the CONTACT's, so they are saved on
      // the contact — the same write the contact panel makes.
      if (form.contact && contact.data && (form.email != null || form.phone != null)) {
        const patch: { email?: string | null; phone?: string | null } = {}
        if (form.email != null && form.email !== (contact.data.email ?? '')) {
          patch.email = form.email || null
        }
        if (form.phone != null && form.phone !== (contact.data.phone ?? '')) {
          patch.phone = form.phone || null
        }
        if (Object.keys(patch).length) {
          await patchContact(form.contact.id, patch)
          qc.invalidateQueries({ queryKey: ['contact', form.contact.id] })
          qc.invalidateQueries({ queryKey: ['contacts'] })
        }
      }
      // The SAME rule the screen used to decide Update was live: probability is
      // sent only when the (new) pipeline draws the field. Computing it here
      // separately once made Update close the modal having sent nothing.
      const shown = !!pipelines.data?.find((p) => p.id === form.pipelineId)
        ?.use_opportunity_probability
      const body = changes(o, form, shown)
      if (Object.keys(body).length) await patchOpportunity(opportunityId, body)
    },
    onSuccess: () => { invalidate(); onClose() },
    onError: (e: Error) => setError(e.message),
  })

  const remove = useMutation({
    mutationFn: () => deleteOpportunity(opportunityId),
    onSuccess: () => { invalidate(); onClose() },
    onError: (e: Error) => { setConfirmDelete(false); setError(e.message) },
  })

  const tagAdd = useMutation({
    mutationFn: (name: string) => addContactTag(form!.contact!.id, name),
    onSuccess: () => { setNewTag(''); tagsChanged() },
    onError: (e: Error) => setError(e.message),
  })
  const tagRemove = useMutation({
    mutationFn: (tagId: number) => removeContactTag(form!.contact!.id, tagId),
    onSuccess: () => tagsChanged(),
    onError: (e: Error) => setError(e.message),
  })
  const tagsChanged = () => {
    qc.invalidateQueries({ queryKey: ['contact', form?.contact?.id] })
    qc.invalidateQueries({ queryKey: ['opportunity', opportunityId] })
    qc.invalidateQueries({ queryKey: ['opportunities'] })
  }

  return (
    <>
    <div
      className="fixed inset-0 z-40 flex items-center justify-center"
      style={{ backgroundColor: 'rgba(16,24,40,0.4)' }}
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={o ? `Edit "${o.title}"` : 'Opportunity'}
        onClick={(e) => e.stopPropagation()}
        className="flex flex-col bg-white"
        style={{ width: 904, maxWidth: '96vw', height: 878, maxHeight: '94vh', borderRadius: 12,
          boxShadow: '0 20px 24px -4px rgba(16,24,40,0.08)' }}
      >
        {!o || !form || seededFor !== opportunityId ? (
          <div style={{ padding: 24, fontSize: 14, color: FAINT }}>Loading…</div>
        ) : (() => {
          const pipelineList = pipelines.data ?? []
          const current = pipelineList.find((p) => p.id === form.pipelineId)
          const moved = form.pipelineId !== o.pipeline_id
          const sections = modalSections(fields.data ?? [], groups.data ?? [], form.pipelineId)
          const set = <K extends keyof Form>(k: K, v: Form[K]) =>
            setForm((f) => (f ? { ...f, [k]: v } : f))
          const email = form.email ?? contact.data?.email ?? o.contact_email ?? ''
          const phone = form.phone ?? contact.data?.phone ?? o.contact_phone ?? ''
          const tags = contact.data?.tags ?? o.contact_tags
          // Greyed contact address while the card has none. The contact whose
          // address is offered is the one the form has chosen, once it has loaded.
          const fallback = contact.data && contact.data.id === form.contact?.id
            ? contactFallback(form.address, contact.data) : null
          const shows = (value: unknown) => !hideEmpty || !isEmptyAnswer(value)
          // Probability is drawn only when the deal's (new) pipeline weighs each deal
          // on its own probability — GoHighLevel's "Use opportunity-level probability".
          const probabilityShown = !!current?.use_opportunity_probability
          const body = changes(o, form, probabilityShown)
          const badProbability = probabilityShown && form.probability.trim() !== ''
            && !(Number.isInteger(Number(form.probability))
              && Number(form.probability) >= 0 && Number(form.probability) <= 100)
          const contactDirty = (form.email != null && form.email !== (contact.data?.email ?? ''))
            || (form.phone != null && form.phone !== (contact.data?.phone ?? ''))
          const dirty = Object.keys(body).length > 0 || contactDirty
          const problem = !form.title.trim() ? 'Opportunity name is required'
            : !form.contact && canEdit ? 'Choose a primary contact'
              : form.stageId == null ? `Choose a stage in ${current?.name ?? 'the new pipeline'}`
                : badProbability ? 'Probability is a whole number from 0 to 100'
                  : null
          const formTab = tab === 'details' || tab.startsWith('group:')
          const group = tab.startsWith('group:')
            ? sections.groups.find((g) => 'group:' + g.group.id === tab) : undefined
          // "N / M answered" on the Checklist tab (2026-09-14): M is only what THIS
          // deal's (new) pipeline asks there, N what the form holds right now.
          const progress = group && isChecklistGroup(group.group.name) && group.fields.length
            ? answeredCount(group.fields, form.answers) : null
          // A linked yes/no draws the REAL values: the Primary email above (saved on
          // the contact) and the Address group below (saved on the card).
          const linked: LinkedValues = {
            email: { value: email, onChange: (v) => set('email', v),
              disabled: !form.contact || !canEdit,
              reason: canEdit ? undefined : 'A technician can tick this, not change the contact’s email',
              hint: form.contact ? null : 'Choose a primary contact to record an email.' },
            address: { value: form.address, onChange: (v) => set('address', v),
              disabled: !canEdit,
              reason: canEdit ? undefined : 'A technician can tick this, not change the job’s address' },
          }

          return (
            <>
              {/* ---- header ---- */}
              <div style={{ padding: '24px 24px 0' }}>
                <div className="flex items-start gap-4">
                  <div className="min-w-0 flex-1 truncate"
                    style={{ fontSize: 18, fontWeight: 600, color: TEXT, lineHeight: '28px' }}>
                    Edit "{o.title}"
                  </div>
                  <button type="button" aria-label="Close" onClick={onClose}
                    className="flex items-center justify-center" style={{ width: 28, height: 28 }}>
                    <svg width={20} height={20} viewBox="0 0 24 24" fill="none" stroke={BODY}
                      strokeWidth={2} strokeLinecap="round" aria-hidden="true">
                      <path d="M18 6 6 18M6 6l12 12" />
                    </svg>
                  </button>
                </div>
                <div style={{ fontSize: 14, color: MUTED, marginTop: 14, paddingBottom: 12,
                  borderBottom: '1px solid ' + DIVIDER }}>
                  Add and edit opportunity details, tasks, notes and appointments.
                </div>
              </div>

              {/* ---- body ---- */}
              <div className="flex min-h-0 flex-1" style={{ padding: '0 24px' }}>
                <nav className="flex shrink-0 flex-col" aria-label="Opportunity sections"
                  style={{ width: 196, borderRight: '1px solid ' + DIVIDER, paddingTop: 12,
                    paddingRight: 12 }}>
                  <div className="min-h-0 flex-1 overflow-y-auto">
                    <NavItem label="Opportunity details" active={tab === 'details'}
                      onClick={() => setTab('details')} />
                    {sections.groups.map(({ group: g }) => (
                      <NavItem key={g.id} label={g.name} active={tab === 'group:' + g.id}
                        onClick={() => setTab(`group:${g.id}`)} />
                    ))}
                    <NavItem label="Book or update appointment" active={tab === 'appointment'}
                      onClick={() => setTab('appointment')} />
                    <NavItem label="Tasks" active={tab === 'tasks'} onClick={() => setTab('tasks')} />
                    {/* STAFF only, like every other door to a note. */}
                    {seesNotes && (
                      <NavItem label="Notes" active={tab === 'notes'} onClick={() => setTab('notes')} />
                    )}
                    <NavItem label="Associated objects" active={tab === 'associated'}
                      onClick={() => setTab('associated')} />
                    {/* CompanyCam job photos (2026-09-14): every role, like the card. */}
                    <NavItem label="Photos" active={tab === 'photos'} onClick={() => setTab('photos')} />
                  </div>
                  {/* LINKS TO Settings → Custom Fields (brief §2). A real navigation to
                      the deep link App.tsx already honours on load (`viewFromPath`,
                      and SettingsPage's `sectionFromPath`), so no fenced file needed
                      to change. It reloads the app, which would drop an unsaved edit
                      without a word — so an unsaved edit is asked about first. */}
                  <button type="button"
                    onClick={() => {
                      if (dirty && !window.confirm(
                        'You have unsaved changes to this opportunity. Leave and discard them?')) return
                      window.location.assign(CUSTOM_FIELDS_PATH)
                    }}
                    className="flex items-center gap-2"
                    style={{ fontSize: 14, fontWeight: 500, color: PRIMARY, padding: '16px 0' }}>
                    <svg width={16} height={16} viewBox="0 0 24 24" fill="none" stroke={PRIMARY}
                      strokeWidth={1.8} aria-hidden="true">
                      <circle cx="12" cy="12" r="3" />
                      <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" />
                    </svg>
                    Manage fields
                  </button>
                </nav>

                <div ref={scroller} className="min-w-0 flex-1 overflow-y-auto"
                  style={{ padding: '12px 4px 16px 16px' }}>
                  {formTab && (
                    <div className="flex items-center justify-between"
                      style={{ paddingBottom: 12, borderBottom: '1px solid ' + DIVIDER, marginBottom: 16 }}>
                      <div className="flex items-center gap-2" style={HEADING}>
                        {group ? group.group.name : 'Contact details'}
                        {progress && (
                          <span data-checklist-progress
                            style={{ fontSize: 13, fontWeight: 400, color: FAINT, marginLeft: 4 }}>
                            {progress.answered} / {progress.total} answered
                          </span>
                        )}
                        {!group && (
                          <svg width={18} height={18} viewBox="0 0 24 24" fill="none" stroke={BODY}
                            strokeWidth={1.7} aria-hidden="true">
                            <rect x="3" y="3" width="18" height="18" rx="3" />
                            <circle cx="12" cy="10" r="3" /><path d="M7 17.5a5.5 5.5 0 0 1 10 0" />
                          </svg>
                        )}
                      </div>
                      <label className="flex items-center" style={{ fontSize: 14, color: BODY, gap: 8 }}>
                        <input type="checkbox" checked={hideEmpty}
                          onChange={(e) => setHideEmpty(e.target.checked)}
                          style={{ width: 16, height: 16, accentColor: PRIMARY }} />
                        Hide empty fields
                      </label>
                    </div>
                  )}

                  {tab === 'details' && (
                    <fieldset disabled={!canAnswer} title={canAnswer ? undefined : why}>
                      {techJob && (
                        <div role="note" data-tech-job-note
                          style={{ fontSize: 13, color: MUTED, backgroundColor: PRIMARY_TINT,
                            borderRadius: 8, padding: '8px 12px', marginBottom: 16 }}>
                          You can answer this job’s questions and change its stage. Its other
                          details are read-only for a technician.
                        </div>
                      )}
                      <div className="grid grid-cols-2 gap-x-3">
                        <div style={{ marginBottom: 16 }}>
                          <Label required>Primary contact name</Label>
                          <ContactSelect value={form.contact} disabled={!canEdit}
                            onChange={(c) => setForm((f) => f && ({
                              ...f, contact: c, email: null, phone: null,
                              additional: f.additional.filter((x) => x.id !== c.id),
                            }))} />
                        </div>
                        {shows(email) && (
                          <div style={{ marginBottom: 16 }}>
                            <Label>Primary email</Label>
                            <input value={email} aria-label="Primary email" placeholder="Enter email"
                              disabled={!form.contact || !canEdit}
                              onChange={(e) => set('email', e.target.value)} style={{ ...INPUT, ...readOnly }} />
                          </div>
                        )}
                        {shows(phone) && (
                          <div style={{ marginBottom: 16 }}>
                            <Label>Primary phone</Label>
                            <input value={phone} aria-label="Primary phone" placeholder="Enter phone"
                              disabled={!form.contact || !canEdit}
                              onChange={(e) => set('phone', e.target.value)} style={{ ...INPUT, ...readOnly }} />
                          </div>
                        )}
                        {(!hideEmpty || form.additional.length > 0) && (
                          <div style={{ marginBottom: 16 }}>
                            <Label>Additional contacts (Max: 10)</Label>
                            <MultiSelect label="Additional contacts" placeholder="Add additional contacts"
                              value={form.additional} onChange={(v) => set('additional', v)}
                              searchContacts max={10} disabled={!canEdit}
                              exclude={form.contact ? [form.contact.id] : []} />
                          </div>
                        )}
                      </div>

                      <div style={{ ...HEADING, paddingBottom: 10, marginTop: 4, marginBottom: 16,
                        borderBottom: '1px solid ' + DIVIDER }}>
                        Opportunity details
                      </div>

                      <div style={{ marginBottom: 16 }}>
                        <Label required>Opportunity name</Label>
                        <input value={form.title} maxLength={120} aria-label="Opportunity name"
                          disabled={!canEdit} title={canEdit ? undefined : why}
                          placeholder="Enter opportunity name"
                          onChange={(e) => set('title', e.target.value)} style={{ ...INPUT, ...readOnly }} />
                      </div>

                      <div className="grid grid-cols-2 gap-x-3">
                        <div style={{ marginBottom: 16 }}>
                          {/* Only pipelines this user can access: GET /api/pipelines
                              never returns one they cannot (pipeline_access.py), and
                              the server refuses a move into one regardless. */}
                          <Label>Pipeline</Label>
                          <Select value={form.pipelineId} ariaLabel="Pipeline" disabled={!canEdit}
                            onChange={(v) => setForm((f) => f && ({
                              ...f, pipelineId: Number(v),
                              // A stage belongs to one pipeline: moving back restores the
                              // deal's own stage, moving away asks for a new one.
                              stageId: Number(v) === o.pipeline_id ? o.stage_id : null,
                            }))}>
                            {pipelineList.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                          </Select>
                        </div>
                        <div style={{ marginBottom: 16 }}>
                          <Label required={moved}>Stage</Label>
                          <Select value={form.stageId ?? ''} ariaLabel="Stage" disabled={!canStage}
                            onChange={(v) => set('stageId', v ? Number(v) : null)}>
                            {form.stageId == null && <option value="">Select stage</option>}
                            {(current?.stages ?? []).map((s) => (
                              <option key={s.id} value={s.id}>{s.name}</option>
                            ))}
                          </Select>
                          {moved && form.stageId == null && (
                            <div style={{ fontSize: 12, color: DANGER, marginTop: 4 }}>
                              Choose a stage in {current?.name}.
                            </div>
                          )}
                        </div>
                        <div style={{ marginBottom: 16 }}>
                          <Label>Status</Label>
                          <Select value={form.status} ariaLabel="Status" disabled={!canEdit}
                            onChange={(v) => set('status', v)}>
                            {STATUSES.map((s) => (
                              <option key={s} value={s}>{s[0].toUpperCase() + s.slice(1)}</option>
                            ))}
                          </Select>
                        </div>
                        <div style={{ marginBottom: 16 }}>
                          <Label>Value</Label>
                          <div className="relative">
                            <span className="pointer-events-none absolute"
                              style={{ left: 12, top: 17, fontSize: 14, color: TEXT }}>$</span>
                            <input type="number" min={0} step="0.01" value={form.value}
                              disabled={!canEdit} title={canEdit ? undefined : why}
                              aria-label="Value" placeholder="0"
                              onChange={(e) => set('value', e.target.value)}
                              style={{ ...INPUT, paddingLeft: 26, ...readOnly }} />
                          </div>
                          {/* centsFromDollars( is what Update sends — see changes(). */}
                        </div>
                        {probabilityShown && (!hideEmpty || form.probability !== '') && (
                          <div style={{ marginBottom: 16 }}>
                            <Label>Probability</Label>
                            <div className="relative">
                              <input type="number" min={0} max={100} step={1}
                                value={form.probability} aria-label="Probability"
                                disabled={!canEdit} title={canEdit ? undefined : why}
                                placeholder="Enter probability"
                                onChange={(e) => set('probability', e.target.value)}
                                style={{ ...INPUT, paddingRight: 30, ...readOnly }} />
                              <span className="pointer-events-none absolute"
                                style={{ right: 12, top: 17, fontSize: 14, color: FAINT }}>%</span>
                            </div>
                          </div>
                        )}
                        {(!hideEmpty || form.ownerId) && (
                          <div style={{ marginBottom: 16 }}>
                            <Label>Owner</Label>
                            <Select value={form.ownerId} ariaLabel="Owner" disabled={!canEdit}
                              onChange={(v) => set('ownerId', v)}>
                              <option value="">Unassigned</option>
                              {(users.data ?? []).map((u) => (
                                <option key={u.id} value={u.id}>{u.name}</option>
                              ))}
                            </Select>
                          </div>
                        )}
                        {(!hideEmpty || form.followers.length > 0) && (
                          <div style={{ marginBottom: 16 }}>
                            <Label>Followers</Label>
                            <MultiSelect label="Followers" placeholder="Add followers"
                              value={form.followers} onChange={(v) => set('followers', v)}
                              disabled={!canEdit}
                              options={(users.data ?? [])
                                .filter((u) => (u as { is_active?: boolean }).is_active !== false)
                                .map((u) => ({ id: u.id, name: u.name }))} />
                          </div>
                        )}
                        {shows(form.businessName) && (
                          <div style={{ marginBottom: 16 }}>
                            <Label>Business name</Label>
                            <input value={form.businessName} aria-label="Business name"
                              disabled={!canEdit} title={canEdit ? undefined : why}
                              placeholder="Enter business name"
                              onChange={(e) => set('businessName', e.target.value)}
                              style={{ ...INPUT, ...readOnly }} />
                          </div>
                        )}
                        {shows(form.source) && (
                          <div style={{ marginBottom: 16 }}>
                            <Label>Source</Label>
                            <input value={form.source} aria-label="Source" placeholder="Enter source"
                              disabled={!canEdit} title={canEdit ? undefined : why}
                              onChange={(e) => set('source', e.target.value)}
                              style={{ ...INPUT, ...readOnly }} />
                          </div>
                        )}
                        {shows(form.closeDate) && (
                          <div style={{ marginBottom: 16 }}>
                            <Label>Expected close date</Label>
                            <input type="date" value={form.closeDate} aria-label="Expected close date"
                              disabled={!canEdit} title={canEdit ? undefined : why}
                              onChange={(e) => set('closeDate', e.target.value)}
                              style={{ ...INPUT, ...readOnly }} />
                          </div>
                        )}
                        {(!hideEmpty || tags.length > 0) && (
                          <div style={{ marginBottom: 16 }} data-field="tags">
                            <Label>Tags</Label>
                            {/* The PRIMARY CONTACT's tags: an opportunity has none of its
                                own. Saved at once, like the contact panel's tag row. */}
                            <div className="flex flex-wrap items-center gap-1"
                              style={{ ...INPUT, height: 'auto', minHeight: 36, padding: '4px 8px' }}>
                              {tags.map((t) => (
                                <Chip key={t.id} name={t.name} disabled={!canEdit || !form.contact}
                                  onRemove={() => tagRemove.mutate(t.id)} />
                              ))}
                              {canEdit && form.contact && (
                                <input value={newTag} aria-label="Add tag"
                                  placeholder={tags.length ? '' : 'Add tags'}
                                  onChange={(e) => setNewTag(e.target.value)}
                                  onKeyDown={(e) => {
                                    if (e.key === 'Enter' && newTag.trim()) {
                                      e.preventDefault(); tagAdd.mutate(newTag.trim())
                                    }
                                  }}
                                  className="min-w-0 flex-1"
                                  style={{ border: 'none', outline: 'none', fontSize: 14,
                                    height: 26, color: TEXT }} />
                              )}
                            </div>
                          </div>
                        )}
                      </div>

                      <CustomFieldAnswers
                        defs={fields.data ?? []}
                        pipelineId={form.pipelineId}
                        fields={sections.ungrouped}
                        answers={form.answers}
                        hideEmpty={hideEmpty}
                        heading={null}
                        columns={2}
                        disabled={!canAnswer}
                        disabledReason={why}
                        linked={linked}
                        onChange={(next) => set('answers', next)}
                      />

                      {/* The job's address (2026-09-14). Hide empty fields hides the
                          whole group when the card has none — the contact's greyed
                          address is a hint about an empty field, not a value. */}
                      {(!hideEmpty || ADDRESS_FIELDS.some(([k]) => form.address[k].trim())) && (
                        <div data-field="address">
                          <div className="flex items-center justify-between"
                            style={{ ...HEADING, paddingBottom: 10, marginTop: 4, marginBottom: 16,
                              borderBottom: '1px solid ' + DIVIDER }}>
                            Address
                            {fallback && canEdit && (
                              <button type="button"
                                onClick={() => set('address', withContactAddress(form.address, contact.data))}
                                style={{ fontSize: 14, fontWeight: 500, color: PRIMARY }}>
                                Use contact address
                              </button>
                            )}
                          </div>
                          {fallback && (
                            <div style={{ fontSize: 13, color: FAINT, marginTop: -8, marginBottom: 12 }}>
                              No address on this opportunity. Showing the contact's address.
                            </div>
                          )}
                          <div className="grid grid-cols-2 gap-x-3">
                            {ADDRESS_FIELDS.map(([key, label, placeholder, max]) =>
                              (!hideEmpty || form.address[key].trim()) && (
                                <div key={key} style={{ marginBottom: 16,
                                  gridColumn: key === 'address_street' ? 'span 2' : undefined }}>
                                  <Label>{label}</Label>
                                  <input value={form.address[key]} aria-label={label} maxLength={max}
                                    disabled={!canEdit} title={canEdit ? undefined : why}
                                    // The contact's value, greyed, while the card has none.
                                    placeholder={fallback?.[key] || placeholder}
                                    onChange={(e) => set('address',
                                      { ...form.address, [key]: e.target.value })}
                                    style={{ ...INPUT, ...readOnly }} />
                                </div>
                              ))}
                          </div>
                        </div>
                      )}
                    </fieldset>
                  )}

                  {group && (
                    group.fields.length === 0 ? (
                      <div style={{ fontSize: 14, color: FAINT }}>
                        No field in {group.group.name} is asked on {current?.name ?? 'this pipeline'}.
                      </div>
                    ) : (
                      <CustomFieldAnswers
                        defs={fields.data ?? []}
                        pipelineId={form.pipelineId}
                        fields={group.fields}
                        answers={form.answers}
                        hideEmpty={hideEmpty}
                        heading={null}
                        // One column for the Checklist, read in order as a call script.
                        columns={isChecklistGroup(group.group.name) ? 1 : 2}
                        disabled={!canAnswer}
                        disabledReason={why}
                        linked={linked}
                        onChange={(next) => set('answers', next)}
                      />
                    )
                  )}

                  {tab === 'appointment' && (
                    <AppointmentTab appointments={o.appointments} canBook={canBook}
                      onBook={() => setBooking(true)} onOpen={setOpenAppointment} />
                  )}
                  {tab === 'tasks' && (
                    <TasksTab opportunityId={o.id} user={user} startAdding={request.adding} />
                  )}
                  {tab === 'notes' && seesNotes && <NotesTab opportunityId={o.id} user={user} />}
                  {tab === 'associated' && (
                    <AssociatedTab o={o} onOpenAppointment={setOpenAppointment} />
                  )}
                  {tab === 'photos' && <PhotosTab opportunityId={o.id} />}
                </div>
              </div>

              {/* ---- footer ---- */}
              <div className="flex items-end gap-3"
                style={{ margin: '0 24px', padding: '16px 0 20px', borderTop: '1px solid ' + DIVIDER }}>
                <div className="min-w-0 flex-1" style={{ fontSize: 12, lineHeight: '18px', color: BODY }}>
                  <div>Created by: <span style={{ color: PRIMARY }}>{o.created_by ?? '--'}</span></div>
                  <div>Created on: {footerStamp(o.created_at)}</div>
                </div>
                {formTab && (
                  <div className="flex flex-col items-end">
                    <ErrorLine error={error ?? (dirty ? problem : null)} />
                    {confirmDelete ? (
                      <div role="alertdialog" className="flex items-center gap-2" style={{ marginTop: 8 }}>
                        <span style={{ fontSize: 13, color: 'rgb(180,35,24)', maxWidth: 360 }}>
                          Delete this opportunity? Its notes and tasks go with it; its
                          appointments are kept, unlinked.
                        </span>
                        <button type="button" style={BUTTON} onClick={() => setConfirmDelete(false)}>
                          Keep it
                        </button>
                        <button type="button" disabled={remove.isPending}
                          style={{ ...PRIMARY_BUTTON, backgroundColor: DANGER, borderColor: DANGER }}
                          onClick={() => remove.mutate()}>
                          {remove.isPending ? 'Deleting…' : 'Delete'}
                        </button>
                      </div>
                    ) : (
                      <div className="flex items-center gap-2" style={{ marginTop: 8 }}>
                        <button type="button" aria-label="Delete opportunity"
                          disabled={!canDelete}
                          title={canDelete ? 'Delete opportunity' : 'Only an admin can delete an opportunity'}
                          onClick={() => { setError(null); setConfirmDelete(true) }}
                          className="flex items-center justify-center"
                          style={{ width: 72, height: 40, borderRadius: 8, backgroundColor: '#fff',
                            border: '1px solid rgb(253,162,155)', ...dead(canDelete) }}>
                          <svg width={20} height={20} viewBox="0 0 24 24" fill="none" stroke={DANGER}
                            strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                            <path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6M10 11v6M14 11v6" />
                          </svg>
                        </button>
                        <button type="button" onClick={onClose} style={{ ...BUTTON, width: 115 }}>
                          Cancel
                        </button>
                        <button
                          type="button"
                          onClick={() => { setError(null); save.mutate() }}
                          disabled={!canAnswer || !dirty || !!problem || save.isPending}
                          title={!canAnswer ? why : problem ?? undefined}
                          style={{ ...PRIMARY_BUTTON, width: 115,
                            ...dead(canAnswer && dirty && !problem && !save.isPending) }}
                        >
                          {save.isPending ? 'Updating…' : 'Update'}
                        </button>
                      </div>
                    )}
                  </div>
                )}
              </div>
            </>
          )
        })()}
      </div>
    </div>

      {/* Deliberately OUTSIDE the backdrop above. Nested inside it, every click in
          the booking dialog would bubble to that backdrop's onClick and close the
          deal behind it — the dialog would vanish mid-typing.

          The EXISTING create dialog, not a second one: it already validates the
          times and schedules the customer's reminders, and a booking made from a
          deal must be identical to one made on the calendar. The deal is locked;
          the contact and the title arrive prefilled and stay editable. */}
      {booking && o && (
        <NewAppointmentDialog
          initialStart={nextHour()}
          initialEnd={new Date(nextHour().getTime() + 3_600_000)}
          initialTitle={(form?.title ?? o.title) + ' — inspection'}
          initialContact={o.contact_id != null
            ? { id: o.contact_id, name: o.contact_name ?? '' }
            : null}
          // With its SAVED address: that is what "Calendar default" will store.
          lockedOpportunity={{ id: o.id, title: o.title, address_street: o.address_street,
            address_city: o.address_city, address_state: o.address_state,
            address_postal_code: o.address_postal_code }}
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

      {/* "Update" on a linked booking: the shared appointment panel, which already
          reschedules and moves the reminders (DECISIONS.md, 2026-09-10). */}
      {openAppointment != null && (
        <AppointmentDetailDialog
          appointmentId={openAppointment}
          user={user}
          onClose={() => setOpenAppointment(null)}
          onChanged={() => {
            qc.invalidateQueries({ queryKey: ['opportunity', opportunityId] })
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
