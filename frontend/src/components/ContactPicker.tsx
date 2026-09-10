import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { listContacts } from '../lib/api'
import { IconSearch } from './Icon'

/**
 * "Type to search, pick from the results", against the same paginated endpoint
 * the Contacts screen uses. A plain id box would ask the user to know a primary
 * key; a full <select> would render 268 options.
 *
 * ONE component used by both the create dialog and the appointment detail panel,
 * for the same reason the contact panel is one component in two places: two
 * implementations of the same picker drift apart.
 */
const PICKER_INPUT: React.CSSProperties = {
  width: '100%', height: 36, marginTop: 4, fontSize: 14,
  borderRadius: 6, border: '1px solid rgb(234,236,240)', padding: '0 10px',
}

export function ContactPicker({
  picked, onPick, disabled = false,
}: {
  picked: { id: number; name: string } | null
  onPick: (c: { id: number; name: string } | null) => void
  /** A role that cannot write still sees who the booking is with, read-only. */
  disabled?: boolean
}) {
  const [q, setQ] = useState('')
  const results = useQuery({
    queryKey: ['contact-picker', q],
    queryFn: () => listContacts({ page: 1, page_size: 8, q }),
    enabled: q.trim().length > 0 && !picked && !disabled,
  })

  if (picked) {
    return (
      <div className="flex items-center gap-2" style={{ ...PICKER_INPUT, display: 'flex' }}>
        <span className="truncate" style={{ fontSize: 14, color: 'rgb(16,24,40)' }}>
          {picked.name}
        </span>
        {!disabled && (
          <button
            type="button"
            className="ml-auto"
            onClick={() => { onPick(null); setQ('') }}
            style={{ fontSize: 12, fontWeight: 600, color: 'rgb(0,78,235)' }}
          >
            Change
          </button>
        )}
      </div>
    )
  }

  return (
    <div>
      <div className="relative">
        <div className="absolute" style={{ left: 10, top: 14 }}>
          <IconSearch size={14} color="rgb(152,162,179)" />
        </div>
        <input
          value={q}
          disabled={disabled}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search contacts"
          aria-label="Search contacts"
          style={{ ...PICKER_INPUT, paddingLeft: 30 }}
        />
      </div>
      {q.trim() && (
        <div
          className="overflow-y-auto"
          style={{
            marginTop: 4, maxHeight: 168, borderRadius: 6,
            border: '1px solid rgb(234,236,240)',
          }}
        >
          {results.isLoading && (
            <div style={{ padding: 10, fontSize: 13, color: 'rgb(152,162,179)' }}>
              Searching…
            </div>
          )}
          {results.error && (
            <div role="alert" style={{ padding: 10, fontSize: 13, color: 'rgb(217,45,32)' }}>
              {(results.error as Error).message}
            </div>
          )}
          {results.data?.items.length === 0 && (
            <div style={{ padding: 10, fontSize: 13, color: 'rgb(152,162,179)' }}>
              No contact matches “{q}”.
            </div>
          )}
          {results.data?.items.map((c) => (
            <button
              key={c.id}
              type="button"
              className="block w-full truncate text-left hover:bg-[rgb(249,250,251)]"
              onClick={() => onPick({ id: c.id, name: c.name })}
              style={{ padding: '8px 10px', fontSize: 14, color: 'rgb(52,64,84)' }}
            >
              {c.name}
              {c.phone && (
                <span style={{ color: 'rgb(152,162,179)' }}> · {c.phone}</span>
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
