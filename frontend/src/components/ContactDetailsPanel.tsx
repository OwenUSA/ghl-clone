import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { IconChevronDown, IconExternal } from './Icon'
import {
  PANE,
  addContactTag,
  getContact,
  listUsers,
  patchContact,
  removeContactTag,
  type ContactDetail,
} from '../lib/api'

/**
 * Contact Details panel.
 *
 * Every value measured from captures/conversations/*__1440x900__top__*.json:
 *   panel        x=1088  w=299.2  h=792  bg rgb(247,249,253)  radius 8px
 *                (+52px icon rail to its right, not reproduced in v1)
 *   header       "Contact Details" 14px/500 rgb(16,24,40)
 *   name/phone   14px/600 rgb(16,24,40)
 *   link         "View contact details" 14px/400 rgb(56,160,219)
 *   Owner/Followers labels 14px/500 rgb(71,84,103)
 *   owner value  "Unassigned" 13px/500 rgb(152,162,179)
 *   "Tags (1)"   14px/500 rgb(71,84,103); chip 12px/500, ✕ 10px
 *   tabs         All fields | DND | Actions — active 14px/500 rgb(16,24,40),
 *                inactive rgb(71,84,103)
 *   section      "Contact" 14px/500 rgb(16,24,40)
 *   field label  14px/400 rgb(102,112,133)
 *   field value  14px/500 rgb(52,64,84); EMPTY RENDERS "--" (measured)
 *   field pitch  63px (First name y=472.4 -> Last name y=535.4)
 *   footer       "Created by: <x>" 11px
 *
 * Fields save on blur via PATCH. GHL autosaves here too, but note the safety
 * contract forbids programmatic blur on GHL's own editors — that rule is about
 * their account, not ours.
 */
const TABS = ['All fields', 'DND', 'Actions'] as const
type Tab = (typeof TABS)[number]

const LABEL = { fontSize: 14, fontWeight: 400, color: 'rgb(102,112,133)' } as const
const VALUE = { fontSize: 14, fontWeight: 500, color: 'rgb(52,64,84)' } as const

function Field({
  label,
  value,
  onSave,
  type = 'text',
}: {
  label: string
  value: string | null
  onSave: (v: string) => void
  type?: string
}) {
  const [draft, setDraft] = useState(value ?? '')
  const [editing, setEditing] = useState(false)
  useEffect(() => setDraft(value ?? ''), [value])

  return (
    <div style={{ marginBottom: 18 }}>
      <div style={LABEL}>{label}</div>
      {editing ? (
        <input
          autoFocus
          type={type}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={() => {
            setEditing(false)
            if (draft !== (value ?? '')) onSave(draft)
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter') (e.target as HTMLInputElement).blur()
            if (e.key === 'Escape') {
              setDraft(value ?? '')
              setEditing(false)
            }
          }}
          style={{
            ...VALUE,
            width: '100%',
            marginTop: 4,
            height: 30,
            borderRadius: 6,
            border: '1px solid rgb(234,236,240)',
            padding: '0 8px',
            backgroundColor: '#fff',
          }}
        />
      ) : (
        <div
          role="button"
          tabIndex={0}
          onClick={() => setEditing(true)}
          onKeyDown={(e) => e.key === 'Enter' && setEditing(true)}
          style={{ ...VALUE, marginTop: 4, minHeight: 21, cursor: 'text' }}
        >
          {/* GHL renders "--" for an empty field, not blank space. Measured. */}
          {value && value.trim() ? value : '--'}
        </div>
      )}
    </div>
  )
}

export function ContactDetailsPanel({
  contactId,
  onClose,
}: {
  contactId: number
  onClose?: () => void
}) {
  const qc = useQueryClient()
  const [tab, setTab] = useState<Tab>('All fields')
  const [search, setSearch] = useState('')
  const [newTag, setNewTag] = useState('')

  const { data: c, isLoading } = useQuery({
    queryKey: ['contact', contactId],
    queryFn: () => getContact(contactId),
  })
  const users = useQuery({ queryKey: ['users'], queryFn: listUsers })

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['contact', contactId] })
    qc.invalidateQueries({ queryKey: ['contacts'] })
  }
  const patch = useMutation({
    mutationFn: (body: Partial<ContactDetail>) => patchContact(contactId, body),
    onSuccess: invalidate,
  })
  const tagAdd = useMutation({
    mutationFn: (name: string) => addContactTag(contactId, name),
    onSuccess: invalidate,
  })
  const tagRemove = useMutation({
    mutationFn: (tagId: number) => removeContactTag(contactId, tagId),
    onSuccess: invalidate,
  })

  const fields = [
    { label: 'First name', key: 'first_name', value: c?.first_name ?? null },
    { label: 'Last name', key: 'last_name', value: c?.last_name ?? null },
    { label: 'Email', key: 'email', value: c?.email ?? null },
    { label: 'Phone', key: 'phone', value: c?.phone ?? null },
    { label: 'Date of birth', key: 'date_of_birth', value: c?.date_of_birth ?? null },
    { label: 'Contact source', key: 'source', value: c?.source ?? null },
    { label: 'Contact type', key: 'contact_type', value: c?.contact_type ?? null },
  ].filter((f) => f.label.toLowerCase().includes(search.toLowerCase()))

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
      <div className="flex shrink-0 items-center justify-between px-4" style={{ height: 44 }}>
        <div style={{ fontSize: 14, fontWeight: 500, color: 'rgb(16,24,40)' }}>
          Contact Details
        </div>
        {/* Measured: GHL's panel header always carries a 16px icon beside the
            title. Ours had none when rendered without an onClose handler. */}
        <button
          onClick={onClose}
          aria-label={onClose ? 'Close' : 'Collapse panel'}
          title={onClose ? 'Close' : 'Collapse panel'}
          style={{ color: 'rgb(71,84,103)' }}
        >
          <IconChevronDown size={16} color="rgb(71,84,103)" />
        </button>
      </div>

      {isLoading || !c ? (
        <div className="px-4" style={{ fontSize: 14 }}>Loading…</div>
      ) : (
        <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-4">
          <div
            className="mb-3 bg-white"
            style={{ borderRadius: 8, padding: 12, border: '1px solid rgb(234,236,240)' }}
          >
            <div className="flex items-center justify-between gap-2">
              <div className="min-w-0 truncate"
                style={{ fontSize: 14, fontWeight: 600, color: 'rgb(16,24,40)' }}>
                {c.name?.trim() || c.phone || '--'}
              </div>
              {/* measured: a TEXT link "View contact details" 14px/400
                  rgb(56,160,219) plus a 16x16 external-link icon */}
              <span
                className="flex items-center gap-1"
                title="Full contact page is not built in v1"
                style={{
                  fontSize: 14, fontWeight: 400, color: 'rgb(56,160,219)',
                  cursor: 'not-allowed', opacity: 0.7,
                }}
              >
                View contact details
                <IconExternal size={16} color="rgb(71,84,103)" />
              </span>
            </div>

            <div className="mt-3 flex gap-6">
              <div className="flex-1">
                <div style={{ fontSize: 14, fontWeight: 500, color: 'rgb(71,84,103)' }}>Owner</div>
                <select
                  value={c.owner_id ?? ''}
                  onChange={(e) =>
                    patch.mutate({
                      owner_id: e.target.value ? Number(e.target.value) : null,
                    } as Partial<ContactDetail>)
                  }
                  style={{
                    marginTop: 4,
                    width: '100%',
                    height: 28,
                    fontSize: 13,
                    fontWeight: 500,
                    color: c.owner_id ? 'rgb(52,64,84)' : 'rgb(152,162,179)',
                    borderRadius: 6,
                    border: '1px solid rgb(234,236,240)',
                    backgroundColor: '#fff',
                  }}
                >
                  <option value="">Unassigned</option>
                  {users.data?.map((u) => (
                    <option key={u.id} value={u.id}>{u.name}</option>
                  ))}
                </select>
              </div>
              <div className="flex-1">
                <div style={{ fontSize: 14, fontWeight: 500, color: 'rgb(71,84,103)' }}>
                  Followers
                </div>
                <div
                  title="Followers are not modelled in v1"
                  style={{ marginTop: 8, fontSize: 13, fontWeight: 500, color: 'rgb(152,162,179)' }}
                >
                  --
                </div>
              </div>
            </div>

            <div className="mt-4">
              <div style={{ fontSize: 14, fontWeight: 500, color: 'rgb(71,84,103)' }}>
                Tags ({c.tags.length})
              </div>
              <div className="mt-2 flex flex-wrap gap-1">
                {c.tags.map((t) => (
                  <span
                    key={t.id}
                    className="inline-flex items-center gap-1"
                    style={{
                      fontSize: 12,
                      fontWeight: 500,
                      color: 'rgb(71,84,103)',
                      backgroundColor: 'rgb(242,244,247)',
                      borderRadius: 6,
                      padding: '2px 6px',
                    }}
                  >
                    {t.name}
                    <button
                      onClick={() => tagRemove.mutate(t.id)}
                      aria-label={`Remove tag ${t.name}`}
                      style={{ fontSize: 10, color: 'rgb(71,84,103)' }}
                    >
                      ✕
                    </button>
                  </span>
                ))}
              </div>
              <input
                value={newTag}
                onChange={(e) => setNewTag(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && newTag.trim()) {
                    tagAdd.mutate(newTag.trim())
                    setNewTag('')
                  }
                }}
                placeholder="Add tag"
                style={{
                  marginTop: 6,
                  width: '100%',
                  height: 28,
                  fontSize: 13,
                  borderRadius: 6,
                  border: '1px solid rgb(234,236,240)',
                  padding: '0 8px',
                }}
              />
            </div>
          </div>

          {/* Measured as a SEGMENTED control (boxed), not an underline row:
              All fields x=1118.2, DND x=1215, Actions x=1288.6 */}
          <div className="flex" style={{
            backgroundColor: 'rgb(242,244,247)', borderRadius: 6, padding: 2,
          }}>
            {TABS.map((t) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                className="flex-1"
                style={{
                  fontSize: 14,
                  fontWeight: 500,
                  height: 30,
                  borderRadius: 4,
                  color: tab === t ? 'rgb(16,24,40)' : 'rgb(71,84,103)',
                  backgroundColor: tab === t ? '#fff' : 'transparent',
                  boxShadow: tab === t ? '0 1px 3px rgba(16,24,40,0.1)' : undefined,
                }}
              >
                {t}
              </button>
            ))}
          </div>

          {tab === 'All fields' && (
            <>
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search fields and folders"
                style={{
                  marginTop: 12,
                  width: '100%',
                  height: 32,
                  fontSize: 14,
                  // Measured on GHL; ours was inheriting the body colour
                  // rgb(96,113,121) until the extractor learned to see placeholders.
                  color: 'rgb(52,64,84)',
                  borderRadius: 6,
                  border: '1px solid rgb(234,236,240)',
                  padding: '0 10px',
                  backgroundColor: '#fff',
                }}
              />
              <div style={{ fontSize: 14, fontWeight: 500, color: 'rgb(16,24,40)', margin: '16px 0 12px' }}>
                Contact
              </div>
              {fields.length === 0 && (
                <div style={{ fontSize: 14, color: 'rgb(102,112,133)' }}>
                  No fields match “{search}”.
                </div>
              )}
              {fields.map((f) => (
                <Field
                  key={f.key}
                  label={f.label}
                  value={f.value}
                  onSave={(v) => patch.mutate({ [f.key]: v } as Partial<ContactDetail>)}
                />
              ))}
              {/* measured collapsible sections below the Contact block */}
              <Section title="General Info">
                <Row label="Business name" value={c.business_name} />
                <Row label="Contact type" value={c.contact_type} />
              </Section>
              <Section title="Additional Info">
                <Row label="Do Not Disturb" value={c.dnd ? 'On' : 'Off'} />
                <Row
                  label="Owner"
                  value={c.owner_name ?? 'Unassigned'}
                />
              </Section>

              {/* measured: "Created by:" is its own 11px label beside the value */}
              <div className="mt-3" style={{ fontSize: 11, color: 'rgb(96,113,121)' }}>
                <span>Created by:</span>{' '}
                <span style={{ color: 'rgb(24,100,171)' }}>{c.created_by ?? '--'}</span>
              </div>
              <div style={{ fontSize: 11, color: 'rgb(96,113,121)' }}>
                Created on: {new Date(c.created_at).toLocaleString('en-US')}
              </div>
              <button
                title="Audit log is not implemented in v1"
                disabled
                className="mt-3 flex w-full items-center justify-center gap-1"
                style={{
                  height: 32, borderRadius: 6, fontSize: 13, fontWeight: 500,
                  color: 'rgb(52,64,84)', border: '1px solid rgb(234,236,240)',
                  backgroundColor: '#fff', opacity: 0.6, cursor: 'not-allowed',
                }}
              >
                Audit logs
                <IconExternal size={14} color="rgb(52,64,84)" />
              </button>
            </>
          )}

          {tab === 'DND' && (
            <div style={{ marginTop: 16 }}>
              <label className="flex items-center gap-2" style={{ fontSize: 14 }}>
                <input
                  type="checkbox"
                  checked={c.dnd}
                  onChange={(e) => patch.mutate({ dnd: e.target.checked } as Partial<ContactDetail>)}
                />
                Do Not Disturb (all channels)
              </label>
              <div style={{ fontSize: 12, color: 'rgb(102,112,133)', marginTop: 8 }}>
                With DND on, automations must not message this contact. Outbound is stubbed in
                v1, so this is recorded but has nothing to suppress yet.
              </div>
            </div>
          )}

          {tab === 'Actions' && (
            <div style={{ marginTop: 16, fontSize: 14, color: 'rgb(152,162,179)' }}>
              Actions are not implemented in v1.
            </div>
          )}
        </div>
      )}
    </div>
  )
}

/** Collapsible section — measured on GHL's panel ("General Info", "Additional Info"). */
function Section({ title, children }: { title: string; children: React.ReactNode }) {
  const [open, setOpen] = useState(false)
  return (
    <div style={{ borderTop: '1px solid rgb(234,236,240)', marginTop: 12, paddingTop: 12 }}>
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between text-left"
        style={{ fontSize: 14, fontWeight: 500, color: 'rgb(16,24,40)' }}
      >
        {title}
        <IconChevronDown
          size={16}
          color="rgb(102,112,133)"
          className={open ? 'rotate-180' : undefined}
        />
      </button>
      {open && <div className="mt-3">{children}</div>}
    </div>
  )
}

function Row({ label, value }: { label: string; value: string | null }) {
  return (
    <div style={{ marginBottom: 12 }}>
      <div style={LABEL}>{label}</div>
      <div style={{ ...VALUE, marginTop: 4 }}>{value && value.trim() ? value : '--'}</div>
    </div>
  )
}
