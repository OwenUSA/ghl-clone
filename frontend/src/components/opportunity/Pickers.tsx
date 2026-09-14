import { useQuery } from '@tanstack/react-query'
import { useCallback, useState } from 'react'
import { listContacts } from '../../lib/api'
import {
  BODY, BORDER, Chevron, DIVIDER, FAINT, INPUT, PLACEHOLDER, PRIMARY, PRIMARY_TINT,
  TEXT, useDismiss,
} from './ui'

/**
 * The modal's select-with-search controls, shaped like GoHighLevel's: a 36px box
 * showing the choice (or chips) with a chevron, opening a list under it.
 *
 * Contacts are searched through the shared `/api/contacts?q=` — the same query the
 * ContactPicker and the Contacts list use, so a phone number typed the way a phone
 * shows it still finds the row (DECISIONS.md, "the last ten digits").
 */
export type Choice = { id: number; name: string }

const LIST: React.CSSProperties = {
  position: 'absolute', left: 0, right: 0, top: 48, zIndex: 20, maxHeight: 240,
  overflowY: 'auto', backgroundColor: '#fff', borderRadius: 8, padding: 4,
  border: '1px solid ' + DIVIDER, boxShadow: '0 12px 16px -4px rgba(16,24,40,0.08)',
}

function Box({ children, onClick, disabled, title, label }: {
  children: React.ReactNode; onClick: () => void; disabled?: boolean; title?: string
  label: string
}) {
  return (
    <button
      type="button"
      aria-label={label}
      aria-haspopup="listbox"
      disabled={disabled}
      title={title}
      onClick={onClick}
      className="flex items-center gap-2 text-left"
      style={{
        ...INPUT, minHeight: 36, height: 'auto', padding: '4px 12px',
        ...(disabled ? { backgroundColor: 'rgb(249,250,251)', cursor: 'not-allowed' } : {}),
      }}
    >
      <span className="flex min-w-0 flex-1 flex-wrap items-center gap-1"
        style={{ minHeight: 26 }}>
        {children}
      </span>
      <Chevron />
    </button>
  )
}

export function Chip({ name, onRemove, disabled }: {
  name: string; onRemove?: () => void; disabled?: boolean
}) {
  return (
    <span className="inline-flex items-center gap-1"
      style={{ fontSize: 13, color: PRIMARY, backgroundColor: PRIMARY_TINT,
        borderRadius: 12, padding: '2px 8px', maxWidth: '100%' }}>
      <span className="truncate">{name}</span>
      {onRemove && !disabled && (
        <span
          role="button"
          tabIndex={0}
          aria-label={'Remove ' + name}
          onClick={(e) => { e.stopPropagation(); onRemove() }}
          onKeyDown={(e) => { if (e.key === 'Enter') { e.stopPropagation(); onRemove() } }}
          style={{ cursor: 'pointer', fontSize: 14, lineHeight: '14px' }}
        >
          ×
        </span>
      )}
    </span>
  )
}

function useContactSearch(q: string, enabled: boolean) {
  const typed = q.trim()
  return useQuery({
    queryKey: ['contacts', 'modal-picker', typed],
    queryFn: () => listContacts({ page: 1, page_size: 8, q: typed }),
    enabled,
  })
}

/** Primary contact: exactly one, required. */
export function ContactSelect({ value, onChange, disabled, title, onCreateNew }: {
  value: Choice | null
  onChange: (c: Choice) => void
  disabled?: boolean
  title?: string
  /** The Add new opportunity modal's "+ New" (2026-09-14): offered at the foot of
      the list with whatever was typed, so the caller can open Add Contact prefilled. */
  onCreateNew?: (typed: string) => void
}) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const close = useCallback(() => setOpen(false), [])
  const ref = useDismiss(open, close)
  const found = useContactSearch(q, open)
  return (
    <div ref={ref} className="relative">
      <Box label="Primary contact name" disabled={disabled} title={title}
        onClick={() => setOpen((v) => !v)}>
        {value
          ? <span className="truncate" style={{ color: TEXT, fontSize: 14 }}>{value.name}</span>
          : <span style={{ color: PLACEHOLDER, fontSize: 14 }}>Select contact</span>}
      </Box>
      {open && (
        <div role="listbox" style={LIST}>
          <input autoFocus value={q} onChange={(e) => setQ(e.target.value)}
            placeholder="Search by name, email or phone"
            style={{ ...INPUT, marginTop: 0, marginBottom: 4 }} />
          {(found.data?.items ?? []).map((c) => (
            <button key={c.id} type="button" role="option"
              aria-selected={value?.id === c.id}
              onClick={() => { onChange({ id: c.id, name: c.name }); setOpen(false); setQ('') }}
              className="block w-full truncate text-left hover:bg-[rgb(249,250,251)]"
              style={{ padding: '8px 10px', fontSize: 14, borderRadius: 6,
                color: value?.id === c.id ? PRIMARY : BODY }}>
              {c.name || '(no name)'}
              {c.phone_display || c.phone ? (
                <span style={{ color: FAINT }}> · {c.phone_display ?? c.phone}</span>
              ) : null}
            </button>
          ))}
          {found.data && found.data.items.length === 0 && (
            <div style={{ padding: '8px 10px', fontSize: 13, color: FAINT }}>
              No contacts match.
            </div>
          )}
          {onCreateNew && (
            <button type="button"
              onClick={() => { setOpen(false); onCreateNew(q.trim()); setQ('') }}
              className="flex w-full items-center gap-2 text-left hover:bg-[rgb(249,250,251)]"
              style={{ padding: '8px 10px', fontSize: 14, fontWeight: 500, borderRadius: 6,
                color: PRIMARY, borderTop: '1px solid ' + DIVIDER }}>
              <svg width={16} height={16} viewBox="0 0 24 24" fill="none" stroke={PRIMARY}
                strokeWidth={2} strokeLinecap="round" aria-hidden="true">
                <path d="M12 5v14M5 12h14" />
              </svg>
              {q.trim() ? `New contact “${q.trim()}”` : 'New contact'}
            </button>
          )}
        </div>
      )}
    </div>
  )
}

/**
 * A set of contacts or users, as chips. `max` stops the picker offering more once
 * the set is full — the server refuses an 11th additional contact either way.
 */
export function MultiSelect({
  label, placeholder, value, onChange, options, searchContacts = false, max,
  exclude = [], disabled, title,
}: {
  label: string
  placeholder: string
  value: Choice[]
  onChange: (next: Choice[]) => void
  /** A fixed list (users). Omit and set `searchContacts` for contacts. */
  options?: Choice[]
  searchContacts?: boolean
  max?: number
  /** Ids never offered — the primary contact, for additional contacts. */
  exclude?: number[]
  disabled?: boolean
  title?: string
}) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const close = useCallback(() => setOpen(false), [])
  const ref = useDismiss(open, close)
  const found = useContactSearch(q, open && searchContacts)
  const chosen = new Set(value.map((v) => v.id))
  const full = max != null && value.length >= max
  const pool: Choice[] = searchContacts
    ? (found.data?.items ?? []).map((c) => ({ id: c.id, name: c.name || '(no name)' }))
    : (options ?? []).filter((o) => !q.trim()
      || o.name.toLowerCase().includes(q.trim().toLowerCase()))
  const offered = pool.filter((o) => !exclude.includes(o.id))

  const toggle = (c: Choice) => {
    if (chosen.has(c.id)) onChange(value.filter((v) => v.id !== c.id))
    else if (!full) onChange([...value, c])
  }

  return (
    <div ref={ref} className="relative">
      <Box label={label} disabled={disabled} title={title} onClick={() => setOpen((v) => !v)}>
        {value.length === 0
          ? <span style={{ color: PLACEHOLDER, fontSize: 14 }}>{placeholder}</span>
          : value.map((v) => (
            <Chip key={v.id} name={v.name} disabled={disabled}
              onRemove={() => onChange(value.filter((x) => x.id !== v.id))} />
          ))}
      </Box>
      {open && (
        <div role="listbox" aria-multiselectable="true" style={LIST}>
          <input autoFocus value={q} onChange={(e) => setQ(e.target.value)}
            placeholder={searchContacts ? 'Search contacts' : 'Search'}
            style={{ ...INPUT, marginTop: 0, marginBottom: 4 }} />
          {full && (
            <div style={{ padding: '6px 10px', fontSize: 12, color: FAINT }}>
              The maximum is {max}. Remove one to add another.
            </div>
          )}
          {offered.map((o) => (
            <button key={o.id} type="button" role="option" aria-selected={chosen.has(o.id)}
              disabled={full && !chosen.has(o.id)}
              onClick={() => toggle(o)}
              className="flex w-full items-center gap-2 text-left hover:bg-[rgb(249,250,251)]"
              style={{ padding: '8px 10px', fontSize: 14, borderRadius: 6, color: BODY,
                ...(full && !chosen.has(o.id) ? { opacity: 0.5, cursor: 'not-allowed' } : {}) }}>
              <input type="checkbox" readOnly checked={chosen.has(o.id)} tabIndex={-1}
                style={{ accentColor: PRIMARY, border: '1px solid ' + BORDER }} />
              <span className="truncate">{o.name}</span>
            </button>
          ))}
          {offered.length === 0 && (searchContacts ? found.data : true) && (
            <div style={{ padding: '8px 10px', fontSize: 13, color: FAINT }}>
              {searchContacts && !q.trim() ? 'Type to search contacts.' : 'Nothing matches.'}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
