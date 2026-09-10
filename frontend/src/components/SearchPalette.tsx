/**
 * The ctrl+K command palette.
 *
 * OURS, not measured. GHL's sidebar search ROW is measured (see Sidebar.tsx, and
 * the ctrlK hint badge on it), but nobody ever opened GHL's search overlay on the
 * live account, so there is no capture to compare this against and it must not be
 * cited as parity. Recorded in DECISIONS.md, 2026-09-10.
 *
 * All of the arithmetic — flattening three groups into one arrow-navigable list,
 * where Enter lands, the wrap at the ends — lives in lib/search.ts so it can be
 * executed by a test instead of pattern-matched.
 */
import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery, keepPreviousData } from '@tanstack/react-query'
import { globalSearch, money } from '../lib/api'
import {
  SEARCH_DEBOUNCE_MS,
  capNote,
  flatten,
  move,
  paletteState,
  target,
} from '../lib/search'
import type { FlatRow, SearchContact, SearchMessage, SearchOpportunity } from '../lib/search'
import { IconChat, IconSearch, IconUser, IconUsers } from './Icon'

const MUTED = 'rgb(102,112,133)'
const TEXT = 'rgb(31,41,55)'
const LINE = 'rgb(234,236,240)'
const SELECTED = 'rgb(239,244,255)'

const GROUP_ICON = {
  contacts: IconUser,
  opportunities: IconUsers,
  messages: IconChat,
} as const

function when(iso: string) {
  return new Date(iso).toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
  })
}

/** The second line of a row: enough to tell two near-identical records apart. */
function subtitle(row: FlatRow): string {
  if (row.group === 'contacts') {
    const c = row.item as SearchContact
    return [c.email, c.phone].filter(Boolean).join(' · ') || 'No email or phone'
  }
  if (row.group === 'opportunities') {
    const o = row.item as SearchOpportunity
    return [
      o.stage_name ?? 'No stage',
      o.status !== 'open' ? o.status : null,
      money(o.value_cents),
      o.contact_name,
    ]
      .filter(Boolean)
      .join(' · ')
  }
  const m = row.item as SearchMessage
  return `${m.contact_name ?? 'Unknown contact'} · ${m.type} · ${when(m.occurred_at)}`
}

function title(row: FlatRow): string {
  if (row.group === 'contacts') return (row.item as SearchContact).name
  if (row.group === 'opportunities') return (row.item as SearchOpportunity).title
  return (row.item as SearchMessage).snippet || '(no text)'
}

export function SearchPalette({
  onClose,
  onOpen,
}: {
  onClose: () => void
  /** Navigate to `view` and open record `id` there. */
  onOpen: (view: string, id: number) => void
}) {
  const [q, setQ] = useState('')
  const [debounced, setDebounced] = useState('')
  const [cursor, setCursor] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLDivElement>(null)

  // Escape must put the caret back where it was, so the palette is not a trap
  // for anyone driving the app from the keyboard.
  const returnTo = useRef<HTMLElement | null>(null)
  useEffect(() => {
    returnTo.current = document.activeElement as HTMLElement | null
    inputRef.current?.focus()
    return () => returnTo.current?.focus?.()
  }, [])

  // Debounced, so refining a query does not put one request per keystroke on the
  // wire. The timer is cleared on every change, so only the pause fires.
  useEffect(() => {
    const t = setTimeout(() => setDebounced(q), SEARCH_DEBOUNCE_MS)
    return () => clearTimeout(t)
  }, [q])

  const term = debounced.trim()
  const results = useQuery({
    queryKey: ['search', term],
    queryFn: () => globalSearch(term),
    enabled: term.length > 0,
    // Keep the previous answer on screen while a narrower one is fetched, rather
    // than flashing "Searching…" between every pair of keystrokes.
    placeholderData: keepPreviousData,
  })

  const rows = useMemo(() => flatten(results.data), [results.data])
  const state = paletteState(q, results.isLoading, results.data)

  // A new result set invalidates the old cursor: leaving it where it was selects
  // whichever record happens to be in that position now, which is how you open
  // the wrong contact.
  useEffect(() => setCursor(0), [term, rows.length])

  useEffect(() => {
    listRef.current
      ?.querySelector(`[data-row="${cursor}"]`)
      ?.scrollIntoView({ block: 'nearest' })
  }, [cursor, rows.length])

  function choose(row?: FlatRow) {
    if (!row) return
    const t = target(row)
    onOpen(t.view, t.id)
    onClose()
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Escape') {
      e.preventDefault()
      onClose()
    } else if (e.key === 'ArrowDown') {
      e.preventDefault()
      setCursor((i) => move(rows.length, i, 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setCursor((i) => move(rows.length, i, -1))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      choose(rows[cursor])
    }
  }

  let flat = -1

  return (
    <div
      role="presentation"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 60,
        backgroundColor: 'rgba(16,24,40,0.5)',
        display: 'flex',
        justifyContent: 'center',
        alignItems: 'flex-start',
        paddingTop: '12vh',
        paddingLeft: 16,
        paddingRight: 16,
      }}
    >
      <div
        role="dialog"
        aria-label="Search"
        onKeyDown={onKeyDown}
        style={{
          width: 600,
          maxWidth: '100%',
          backgroundColor: '#fff',
          borderRadius: 12,
          boxShadow: '0 20px 24px -4px rgba(16,24,40,0.16)',
          overflow: 'hidden',
          display: 'flex',
          flexDirection: 'column',
          maxHeight: '70vh',
        }}
      >
        <div
          className="flex shrink-0 items-center gap-3 px-4"
          style={{ height: 54, borderBottom: `1px solid ${LINE}` }}
        >
          <IconSearch size={18} color={MUTED} />
          <input
            ref={inputRef}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search contacts, opportunities and messages"
            aria-label="Search contacts, opportunities and messages"
            className="flex-1 outline-none"
            style={{ fontSize: 15, color: TEXT, border: 'none' }}
          />
          <button
            onClick={onClose}
            style={{
              fontSize: 11,
              color: MUTED,
              backgroundColor: 'rgb(242,244,247)',
              borderRadius: 4,
              padding: '2px 6px',
              border: 'none',
              cursor: 'pointer',
            }}
          >
            esc
          </button>
        </div>

        <div ref={listRef} className="flex-1 overflow-y-auto" style={{ padding: 4 }}>
          {state === 'idle' && (
            <Note>Type to search contacts, opportunities and message text.</Note>
          )}
          {state === 'loading' && <Note>Searching…</Note>}
          {results.isError && (
            <Note>{(results.error as Error).message}</Note>
          )}
          {state === 'empty' && !results.isError && (
            <Note>
              Nothing matches “{term}”. Call recordings are not searched here — use{' '}
              <code>ghl calls list --q</code> for those.
            </Note>
          )}

          {state === 'results' &&
            results.data?.groups.map((g) => {
              if (!g.items.length) return null
              const Icon = GROUP_ICON[g.type]
              const note = capNote(g)
              return (
                <div key={g.type}>
                  <div
                    style={{
                      padding: '10px 12px 4px',
                      fontSize: 11,
                      fontWeight: 600,
                      letterSpacing: '0.04em',
                      textTransform: 'uppercase',
                      color: MUTED,
                    }}
                  >
                    {g.label}
                  </div>
                  {g.items.map((item) => {
                    flat += 1
                    const index = flat
                    const row = { group: g.type, item } as FlatRow
                    const on = index === cursor
                    return (
                      <button
                        key={`${g.type}-${(item as { id: number }).id}`}
                        data-row={index}
                        onMouseMove={() => setCursor(index)}
                        onClick={() => choose(row)}
                        className="flex w-full items-center gap-3 text-left"
                        style={{
                          padding: '8px 12px',
                          borderRadius: 6,
                          border: 'none',
                          cursor: 'pointer',
                          backgroundColor: on ? SELECTED : 'transparent',
                        }}
                      >
                        <Icon size={16} color={MUTED} />
                        <span className="min-w-0 flex-1">
                          <span
                            className="block truncate"
                            style={{ fontSize: 14, color: TEXT }}
                          >
                            {title(row)}
                          </span>
                          <span
                            className="block truncate"
                            style={{ fontSize: 12, color: MUTED }}
                          >
                            {subtitle(row)}
                          </span>
                        </span>
                      </button>
                    )
                  })}
                  {note && (
                    <div style={{ padding: '2px 12px 8px', fontSize: 12, color: MUTED }}>
                      {note}
                    </div>
                  )}
                </div>
              )
            })}
        </div>

        <div
          className="shrink-0"
          style={{
            borderTop: `1px solid ${LINE}`,
            padding: '8px 16px',
            fontSize: 11,
            color: MUTED,
          }}
        >
          ↑↓ to move · ↵ to open · esc to close
        </div>
      </div>
    </div>
  )
}

function Note({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ padding: '20px 16px', fontSize: 13, color: MUTED }}>{children}</div>
  )
}
