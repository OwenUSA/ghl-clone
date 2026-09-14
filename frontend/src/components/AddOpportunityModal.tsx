import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import {
  centsFromDollars,
  createOpportunity,
  getContact,
  listCustomFields,
  listFieldGroups,
  listUsers,
  patchContact,
  type Pipeline,
} from '../lib/api'
import type { Me } from '../lib/auth'
import {
  answeredCount, answersFor, isChecklistGroup, isEmptyAnswer, modalSections,
} from '../lib/customFields'
import {
  ADDRESS_FIELDS, addressForm, contactFallback, withContactAddress, type AddressForm,
} from '../lib/opportunityAddress'
import { prefillFrom } from '../lib/phoneMatch'
import { AddContactDialog } from './AddContactDialog'
import { CustomFieldAnswers, type LinkedValues } from './CustomFieldAnswers'
import { CUSTOM_FIELDS_PATH, NavItem } from './OpportunityDetail'
import { ContactSelect, MultiSelect, type Choice } from './opportunity/Pickers'
import {
  BODY, BUTTON, DIVIDER, ErrorLine, FAINT, HEADING, INPUT, Label, MUTED, PRIMARY,
  PRIMARY_BUTTON, Select, TEXT, dead,
} from './opportunity/ui'

/**
 * Add new opportunity — GoHighLevel's create modal, built from the owner's screenshot
 * (refs/round3/38), replacing our small "Add opportunity" form (refs/round3/37).
 *
 *   Add new opportunity                                                     ✕
 *   Create new opportunity by filling in details and selecting a contact
 *   ─────────────────────────────────────────────────────────────────────────
 *   Opportunity details   │ Contact details
 *   Checklist  0 / 16     │ Primary contact name * [+ New] | Primary email
 *   <other custom tabs>   │ Primary phone
 *                         │ Opportunity details
 *                         │ Opportunity name *
 *                         │ Pipeline | Stage · Status | Value · Owner | Followers
 *   ⚙ Manage fields       │ Business name | Source · custom fields · Address
 *   ─────────────────────────────────────────────────────────────────────────
 *                                                        [Cancel] [Create]
 *
 * The SAME family as the edit modal (OpportunityDetail): its nav item, its inputs,
 * its pickers and the one CustomFieldAnswers component, so a question is drawn and
 * validated identically in both. What is different is only what a create needs:
 *
 *   * **Primary contact is REQUIRED here** (the owner, 2026-09-14). The API does not
 *     require one — the AHS relay, the Workiz import and the CLI create cards the
 *     modal never sees — so the rule lives in this form. "+ New" opens the real Add
 *     Contact dialog and selects what it made.
 *   * **One Create files everything.** The dispatcher fills the Checklist during the
 *     call and presses Create once: status, owner, followers, business name, source,
 *     the address and every answer go in one POST. A changed Primary email or phone
 *     is the CONTACT's, so it is saved on the contact first, through the same PATCH
 *     the contact panel uses — a refused email stops before any card exists.
 *   * **Only the chosen pipeline's answers are sent** (`answersFor`): a question
 *     answered before the Pipeline was changed would otherwise be refused.
 *
 * No tasks, notes, appointment or associated-objects tab: those need a card to hang
 * on, and GoHighLevel's create modal (screenshot 38) shows none of them either.
 */
const STATUSES = ['open', 'won', 'lost', 'abandoned'] as const

export function AddOpportunityDialog({
  pipelines,
  initialPipelineId,
  user,
  onClose,
  onDone,
}: {
  pipelines: Pipeline[]
  initialPipelineId: number
  /** For the "+ New" contact role gate: `POST /api/contacts` is STAFF-only. */
  user: Me
  onClose: () => void
  /** The pipeline and status it was filed with, so the board can show it. */
  onDone: (createdInPipelineId: number, createdStatus: string) => void
}) {
  const qc = useQueryClient()
  const [tab, setTab] = useState<'details' | `group:${number}`>('details')
  const [title, setTitle] = useState('')
  const [contact, setContact] = useState<Choice | null>(null)
  // null = untouched: show the contact's own. A string is an edit to the CONTACT.
  const [email, setEmail] = useState<string | null>(null)
  const [phone, setPhone] = useState<string | null>(null)
  const [pipelineId, setPipelineId] = useState(initialPipelineId)
  const [stageId, setStageId] = useState<number | null>(null)
  const [status, setStatus] = useState<string>('open')
  const [value, setValue] = useState('')
  const [ownerId, setOwnerId] = useState('')
  const [followers, setFollowers] = useState<Choice[]>([])
  const [businessName, setBusinessName] = useState('')
  const [source, setSource] = useState('')
  const [answers, setAnswers] = useState<Record<string, unknown>>({})
  const [address, setAddress] = useState<AddressForm>(() => addressForm(null))
  const [adding, setAdding] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  // The job questions, and the tabs they are drawn under.
  const fields = useQuery({ queryKey: ['custom-fields'], queryFn: listCustomFields })
  const groups = useQuery({ queryKey: ['custom-field-groups'], queryFn: listFieldGroups })
  const users = useQuery({ queryKey: ['users'], queryFn: listUsers })
  const contactDetail = useQuery({
    queryKey: ['contact', contact?.id],
    queryFn: () => getContact(contact!.id),
    enabled: contact != null,
  })
  const loaded = contactDetail.data && contactDetail.data.id === contact?.id
    ? contactDetail.data : null

  const pipeline = pipelines.find((p) => p.id === pipelineId) ?? pipelines[0]
  const stages = pipeline?.stages ?? []
  // Default to the first stage, and follow the pipeline when it changes instead of
  // holding a stage id the new pipeline has never heard of — the backend refuses
  // that pair, so keeping it would be a submit-time error for no reason.
  const stage = stages.find((s) => s.id === stageId) ?? stages[0]
  const sections = modalSections(fields.data ?? [], groups.data ?? [], pipeline?.id)

  const shownEmail = email ?? loaded?.email ?? ''
  const shownPhone = phone ?? loaded?.phone ?? ''
  const emailChanged = email != null && !!loaded && email.trim() !== (loaded.email ?? '')
  const phoneChanged = phone != null && !!loaded && phone.trim() !== (loaded.phone ?? '')
  const fallback = loaded ? contactFallback(address, loaded) : null

  const pickContact = (c: Choice) => {
    setContact(c)
    setEmail(null)
    setPhone(null)
    // GoHighLevel names a new opportunity after its contact until somebody types a
    // name; a name already typed is never replaced.
    if (!title.trim()) setTitle(c.name)
  }

  const create = useMutation({
    mutationFn: async () => {
      if (contact && (emailChanged || phoneChanged)) {
        await patchContact(contact.id, {
          ...(emailChanged ? { email: email!.trim() || null } : {}),
          ...(phoneChanged ? { phone: phone!.trim() || null } : {}),
        })
        qc.invalidateQueries({ queryKey: ['contact', contact.id] })
        qc.invalidateQueries({ queryKey: ['contacts'] })
      }
      const t = (v: string) => v.trim() || null
      return createOpportunity({
        title: title.trim(),
        pipeline_id: pipeline.id,
        stage_id: stage.id,
        contact_id: contact?.id ?? null,
        // Dollars -> integer cents without a float in the middle; see api.ts.
        value_cents: centsFromDollars(value),
        status,
        owner_id: ownerId ? Number(ownerId) : null,
        follower_ids: followers.map((f) => f.id),
        business_name: t(businessName),
        source: t(source),
        address_street: t(address.address_street),
        address_city: t(address.address_city),
        address_state: t(address.address_state),
        address_postal_code: t(address.address_postal_code),
        custom_fields: answersFor(fields.data ?? [], pipeline.id, answers),
      })
    },
    onSuccess: () => onDone(pipeline.id, status),
    onError: (e: Error) => setError(e.message),
  })

  // The owner's rule for THIS form: a contact is required, and so is a name. A
  // missing stage means the pipeline has none — there is nowhere to file the card.
  const problem = !contact ? 'Choose a primary contact'
    : !title.trim() ? 'Opportunity name is required'
      : !stage ? `${pipeline?.name ?? 'This pipeline'} has no stages`
        : null
  const ready = problem == null
  const touched = !!(contact || title.trim())

  const group = tab.startsWith('group:')
    ? sections.groups.find((g) => 'group:' + g.group.id === tab) : undefined
  const progressOf = (g: (typeof sections.groups)[number]) =>
    isChecklistGroup(g.group.name) && g.fields.length ? answeredCount(g.fields, answers) : null
  const linked: LinkedValues = {
    email: { value: shownEmail, onChange: setEmail, disabled: !contact,
      hint: contact ? null : 'Choose a primary contact to record an email.' },
    address: { value: address, onChange: setAddress, disabled: false },
  }
  const dirty = touched || Object.values(answers).some((v) => !isEmptyAnswer(v))
  const field = (children: React.ReactNode, span = false) => (
    <div style={{ marginBottom: 16, gridColumn: span ? 'span 2' : undefined }}>{children}</div>
  )
  const sectionHeading = (text: string, extra?: React.ReactNode) => (
    <div className="flex items-center justify-between"
      style={{ ...HEADING, paddingBottom: 12, marginBottom: 16, borderBottom: '1px solid ' + DIVIDER }}>
      <span className="flex items-center gap-2">{text}</span>
      {extra}
    </div>
  )

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
          aria-label="Add new opportunity"
          onClick={(e) => e.stopPropagation()}
          className="flex flex-col bg-white"
          style={{ width: 904, maxWidth: '96vw', height: 878, maxHeight: '94vh', borderRadius: 12,
            boxShadow: '0 20px 24px -4px rgba(16,24,40,0.08)' }}
        >
          {/* ---- header ---- */}
          <div style={{ padding: '24px 24px 0' }}>
            <div className="flex items-start gap-4">
              <div className="min-w-0 flex-1 truncate"
                style={{ fontSize: 18, fontWeight: 600, color: TEXT, lineHeight: '28px' }}>
                Add new opportunity
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
              Create new opportunity by filling in details and selecting a contact
            </div>
          </div>

          {/* ---- body ---- */}
          <div className="flex min-h-0 flex-1" style={{ padding: '0 24px' }}>
            <nav className="flex shrink-0 flex-col" aria-label="New opportunity sections"
              style={{ width: 196, borderRight: '1px solid ' + DIVIDER, paddingTop: 12,
                paddingRight: 12 }}>
              <div className="min-h-0 flex-1 overflow-y-auto">
                <NavItem label="Opportunity details" active={tab === 'details'}
                  onClick={() => setTab('details')} />
                {sections.groups.map(({ group: g }) => (
                  <NavItem key={g.id} label={g.name} active={tab === 'group:' + g.id}
                    onClick={() => setTab(`group:${g.id}`)} />
                ))}
              </div>
              {/* The edit modal's link to Settings → Custom Fields. It reloads the
                  app, so a half-filled form is asked about first. */}
              <button type="button"
                onClick={() => {
                  if (dirty && !window.confirm(
                    'This opportunity has not been created yet. Leave and discard it?')) return
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

            <div className="min-w-0 flex-1 overflow-y-auto" style={{ padding: '12px 4px 16px 16px' }}>
              {tab === 'details' && (
                <>
                  {sectionHeading('Contact details')}
                  <div className="grid grid-cols-2 gap-x-3">
                    {field(<>
                      <div className="flex items-center justify-between">
                        <Label required>Primary contact name</Label>
                        {/* The "+ New" the old dialog had, kept: a caller who is not on
                            file must not stop the job. Same Add Contact dialog. */}
                        <button type="button" onClick={() => setAdding('')}
                          disabled={user.role === 'TECH'}
                          title={user.role === 'TECH' ? 'Your role cannot create contacts'
                            : 'Add a contact'}
                          style={{ fontSize: 13, fontWeight: 500, color: PRIMARY, lineHeight: '18px',
                            ...dead(user.role !== 'TECH') }}>
                          + New
                        </button>
                      </div>
                      <ContactSelect value={contact} onChange={pickContact}
                        onCreateNew={user.role === 'TECH' ? undefined : (typed) => setAdding(typed)} />
                    </>)}
                    {field(<>
                      <Label>Primary email</Label>
                      <input value={shownEmail} aria-label="Primary email" placeholder="Enter email"
                        type="email" disabled={!contact}
                        title={contact ? undefined : 'Choose a primary contact first'}
                        onChange={(e) => setEmail(e.target.value)}
                        style={{ ...INPUT, ...(contact ? {} : { backgroundColor: 'rgb(249,250,251)' }) }} />
                    </>)}
                    {field(<>
                      <Label>Primary phone</Label>
                      <input value={shownPhone} aria-label="Primary phone" placeholder="Enter phone"
                        disabled={!contact}
                        title={contact ? undefined : 'Choose a primary contact first'}
                        onChange={(e) => setPhone(e.target.value)}
                        style={{ ...INPUT, ...(contact ? {} : { backgroundColor: 'rgb(249,250,251)' }) }} />
                    </>)}
                  </div>

                  <div style={{ marginTop: 4 }}>{sectionHeading('Opportunity details')}</div>
                  {field(<>
                    <Label required>Opportunity name</Label>
                    <input value={title} maxLength={120} aria-label="Opportunity name"
                      placeholder="Enter opportunity name"
                      onChange={(e) => setTitle(e.target.value)} style={INPUT} />
                  </>)}
                  <div className="grid grid-cols-2 gap-x-3">
                    {field(<>
                      <Label>Pipeline</Label>
                      <Select value={pipeline?.id ?? ''} ariaLabel="Pipeline"
                        onChange={(v) => { setPipelineId(Number(v)); setStageId(null) }}>
                        {pipelines.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                      </Select>
                    </>)}
                    {field(<>
                      <Label>Stage</Label>
                      <Select value={stage?.id ?? ''} ariaLabel="Stage"
                        onChange={(v) => setStageId(Number(v))}>
                        {stages.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                      </Select>
                    </>)}
                    {field(<>
                      <Label>Status</Label>
                      <Select value={status} ariaLabel="Status" onChange={setStatus}>
                        {STATUSES.map((s) => (
                          <option key={s} value={s}>{s[0].toUpperCase() + s.slice(1)}</option>
                        ))}
                      </Select>
                    </>)}
                    {field(<>
                      <Label>Value</Label>
                      <div className="relative">
                        <span className="pointer-events-none absolute"
                          style={{ left: 12, top: 17, fontSize: 14, color: TEXT }}>$</span>
                        <input type="number" min={0} step="0.01" value={value} aria-label="Value"
                          placeholder="Please Input" onChange={(e) => setValue(e.target.value)}
                          style={{ ...INPUT, paddingLeft: 26 }} />
                      </div>
                    </>)}
                    {field(<>
                      <Label>Owner</Label>
                      <Select value={ownerId} ariaLabel="Owner" onChange={setOwnerId}>
                        <option value="">Unassigned</option>
                        {(users.data ?? []).map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
                      </Select>
                    </>)}
                    {field(<>
                      <Label>Followers</Label>
                      <MultiSelect label="Followers" placeholder="Add followers"
                        value={followers} onChange={setFollowers}
                        options={(users.data ?? [])
                          .filter((u) => (u as { is_active?: boolean }).is_active !== false)
                          .map((u) => ({ id: u.id, name: u.name }))} />
                    </>)}
                    {field(<>
                      <Label>Business name</Label>
                      <input value={businessName} maxLength={200} aria-label="Business name"
                        placeholder="Enter business name"
                        onChange={(e) => setBusinessName(e.target.value)} style={INPUT} />
                    </>)}
                    {field(<>
                      <Label>Source</Label>
                      <input value={source} maxLength={120} aria-label="Source"
                        placeholder="Enter source"
                        onChange={(e) => setSource(e.target.value)} style={INPUT} />
                    </>)}
                  </div>

                  <CustomFieldAnswers
                    defs={fields.data ?? []}
                    pipelineId={pipeline?.id}
                    fields={sections.ungrouped}
                    answers={answers}
                    heading={null}
                    columns={2}
                    linked={linked}
                    onChange={setAnswers}
                  />

                  <div data-field="address">
                    {sectionHeading('Address', fallback && (
                      <button type="button"
                        onClick={() => setAddress(withContactAddress(address, loaded))}
                        style={{ fontSize: 14, fontWeight: 500, color: PRIMARY }}>
                        Use contact address
                      </button>
                    ))}
                    {fallback && (
                      <div style={{ fontSize: 13, color: FAINT, marginTop: -8, marginBottom: 12 }}>
                        No address on this opportunity. Showing the contact's address.
                      </div>
                    )}
                    <div className="grid grid-cols-2 gap-x-3">
                      {ADDRESS_FIELDS.map(([key, label, placeholder, max]) => field(
                        <>
                          <Label>{label}</Label>
                          <input value={address[key]} aria-label={label} maxLength={max}
                            placeholder={fallback?.[key] || placeholder}
                            onChange={(e) => setAddress({ ...address, [key]: e.target.value })}
                            style={INPUT} />
                        </>, key === 'address_street'))}
                    </div>
                  </div>
                </>
              )}

              {group && (() => {
                const progress = progressOf(group)
                return (
                  <>
                    {sectionHeading(group.group.name, progress && (
                      <span data-checklist-progress
                        style={{ fontSize: 13, fontWeight: 400, color: FAINT }}>
                        {progress.answered} / {progress.total} answered
                      </span>
                    ))}
                    {group.fields.length === 0 ? (
                      <div style={{ fontSize: 14, color: FAINT }}>
                        No field in {group.group.name} is asked on {pipeline?.name ?? 'this pipeline'}.
                      </div>
                    ) : (
                      <CustomFieldAnswers
                        defs={fields.data ?? []}
                        pipelineId={pipeline?.id}
                        fields={group.fields}
                        answers={answers}
                        heading={null}
                        columns={2}
                        linked={linked}
                        onChange={setAnswers}
                      />
                    )}
                  </>
                )
              })()}
            </div>
          </div>

          {/* ---- footer ---- */}
          <div className="flex items-center justify-end gap-3"
            style={{ margin: '0 24px', padding: '16px 0 20px', borderTop: '1px solid ' + DIVIDER }}>
            <div className="min-w-0 flex-1 text-right">
              <ErrorLine error={error ?? (touched ? problem : null)} />
            </div>
            <button type="button" onClick={onClose} style={{ ...BUTTON, width: 115 }}>
              Cancel
            </button>
            <button
              type="button"
              onClick={() => { setError(null); create.mutate() }}
              // Disabled while the POST is in flight: a second click would create a
              // second opportunity, and nothing on the server dedupes them.
              disabled={!ready || create.isPending}
              title={problem ?? undefined}
              style={{ ...PRIMARY_BUTTON, width: 115, ...dead(ready && !create.isPending) }}
            >
              {create.isPending ? 'Creating…' : 'Create'}
            </button>
          </div>
        </div>
      </div>

      {/* Outside the backdrop, so a click inside Add Contact does not bubble to it
          and close this modal mid-call. */}
      {adding != null && (
        <AddContactDialog
          initial={prefillFrom(adding)}
          onClose={() => setAdding(null)}
          onCreated={(c) => {
            setAdding(null)
            pickContact({ id: c.id, name: c.name })
            qc.invalidateQueries({ queryKey: ['contacts'] })
          }}
          onUseExisting={(c) => { setAdding(null); pickContact({ id: c.id, name: c.name }) }}
        />
      )}
    </>
  )
}
