import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useLayoutEffect, useRef, useState } from 'react'
import {
  createNote, deleteNote, listNotes, patchNote,
  type ContactThreadNote, type OpportunityNote,
} from '../../lib/api'
import {
  CONTACT_NOTES, filterNotes, type NoteRow, type NoteSort,
} from '../../lib/notesView'
import {
  AddBar, BODY, BORDER, BUTTON, DIVIDER, ErrorLine, FAINT, HEADING, INPUT, KebabMenu,
  MUTED, PRIMARY, PRIMARY_BUTTON, dead, noteStamp, useDismiss,
} from './ui'

/**
 * The Notes tab, from the owner's screenshot 22.
 *
 *   Notes                      [filter•] [sort•] [ Search notes ]
 *   [                 + Add note                 ]
 *   ┌ body, clamped · Show more / Show less
 *   ├──────────────────────────────────────
 *   └ Sep 13 2026, 9:17 AM (EDT) · Created by: Owen                ⋮
 *
 * This deal's own notes are editable (⋮ Edit / Delete). Under them, the contact's
 * thread NOTE events — including the Workiz merge notes — are shown read-only and
 * carry no ⋮, because they belong to the contact's conversation, not to this deal.
 *
 * GoHighLevel's small chip left of ⋮ counts a note's associations. This app has
 * no note associations, so the chip is NOT drawn (listed as not built).
 *
 * STAFF only: the modal does not offer this tab to a TECH at all, and the server
 * answers a TECH 403 on every note route regardless.
 */
const CLAMP_LINES = 7

function NoteBody({ text }: { text: string }) {
  const ref = useRef<HTMLDivElement>(null)
  const [expanded, setExpanded] = useState(false)
  const [overflows, setOverflows] = useState(false)
  useLayoutEffect(() => {
    const el = ref.current
    if (el && !expanded) setOverflows(el.scrollHeight > el.clientHeight + 1)
  }, [text, expanded])
  return (
    <>
      <div
        ref={ref}
        style={{
          fontSize: 14, lineHeight: '18px', color: BODY, whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
          ...(expanded ? {} : {
            display: '-webkit-box', WebkitLineClamp: CLAMP_LINES,
            WebkitBoxOrient: 'vertical', overflow: 'hidden',
          }),
        }}
      >
        {text}
      </div>
      {(overflows || expanded) && (
        <button type="button" onClick={() => setExpanded((v) => !v)}
          style={{ marginTop: 6, fontSize: 14, color: PRIMARY }}>
          {expanded ? 'Show less' : 'Show more'}
        </button>
      )}
    </>
  )
}

function NoteCard({ row, onEdit, onDelete }: {
  row: NoteRow
  onEdit?: () => void
  onDelete?: () => void
}) {
  return (
    <div style={{ border: '1px solid ' + BORDER, borderRadius: 10, padding: '14px 16px',
      marginTop: 14 }} data-note-kind={row.kind}>
      <NoteBody text={row.body} />
      <div className="flex items-end justify-between"
        style={{ borderTop: '1px solid ' + DIVIDER, marginTop: 12, paddingTop: 8 }}>
        <div style={{ fontSize: 12, lineHeight: '16px', color: MUTED }}>
          <div>{noteStamp(row.at)}</div>
          <div>
            {row.kind === 'deal'
              ? 'Created by: ' + (row.author ?? '--')
              : "From the contact's conversation"}
          </div>
        </div>
        {onEdit && onDelete && (
          <KebabMenu label="Note actions" items={[
            { label: 'Edit', onClick: onEdit },
            { label: 'Delete', onClick: onDelete, danger: true },
          ]} />
        )}
      </div>
    </div>
  )
}

function IconButton({ label, active, onClick, children }: {
  label: string; active: boolean; onClick: () => void; children: React.ReactNode
}) {
  return (
    <button type="button" aria-label={label} aria-pressed={active} onClick={onClick}
      className="relative flex items-center justify-center"
      style={{ width: 32, height: 32, borderRadius: 6 }}>
      {children}
      {active && (
        <span data-active-dot style={{ position: 'absolute', top: 5, right: 5, width: 6,
          height: 6, borderRadius: 3, backgroundColor: PRIMARY }} />
      )}
    </button>
  )
}

export function NotesTab({ opportunityId }: { opportunityId: number }) {
  const qc = useQueryClient()
  const notes = useQuery({
    queryKey: ['opportunity-notes', opportunityId],
    queryFn: () => listNotes(opportunityId),
  })
  const [q, setQ] = useState('')
  const [author, setAuthor] = useState<string>('')
  const [sort, setSort] = useState<NoteSort>('newest')
  const [menu, setMenu] = useState<'filter' | 'sort' | null>(null)
  const closeMenu = useCallback(() => setMenu(null), [])
  const menuRef = useDismiss(menu !== null, closeMenu)
  const [composing, setComposing] = useState(false)
  const [draft, setDraft] = useState('')
  const [editing, setEditing] = useState<number | null>(null)
  const [editDraft, setEditDraft] = useState('')
  const [confirming, setConfirming] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)

  const refresh = () => {
    setError(null)
    qc.invalidateQueries({ queryKey: ['opportunity-notes', opportunityId] })
    // The board card's badge and tooltip read the same notes.
    qc.invalidateQueries({ queryKey: ['opportunities'] })
  }
  const add = useMutation({
    mutationFn: () => createNote(opportunityId, draft),
    onSuccess: () => { setDraft(''); setComposing(false); refresh() },
    onError: (e: Error) => setError(e.message),
  })
  const save = useMutation({
    mutationFn: (id: number) => patchNote(id, editDraft),
    onSuccess: () => { setEditing(null); refresh() },
    onError: (e: Error) => setError(e.message),
  })
  const remove = useMutation({
    mutationFn: (id: number) => deleteNote(id),
    onSuccess: () => { setConfirming(null); refresh() },
    onError: (e: Error) => setError(e.message),
  })

  const rows: NoteRow[] = [
    ...(notes.data?.notes ?? []).map((n: OpportunityNote): NoteRow => ({
      kind: 'deal', id: n.id, body: n.body, at: n.created_at, author: n.created_by,
    })),
    ...(notes.data?.contact_notes ?? []).map((n: ContactThreadNote): NoteRow => ({
      kind: 'contact', id: n.id, body: n.body, at: n.occurred_at, author: null,
    })),
  ]
  const authors = [...new Set(rows.filter((r) => r.kind === 'deal')
    .map((r) => r.author ?? '--'))].sort()
  const shown = filterNotes(rows, { q, author: author || null, sort })
  const busy = add.isPending || save.isPending || remove.isPending

  return (
    <div>
      <div className="flex items-center gap-2">
        <div style={{ ...HEADING, fontWeight: 600 }} className="flex-1">Notes</div>
        <div ref={menuRef} className="relative flex items-center gap-1">
          <IconButton label="Filter notes" active={author !== ''}
            onClick={() => setMenu((m) => (m === 'filter' ? null : 'filter'))}>
            <svg width={20} height={20} viewBox="0 0 24 24" fill="none" stroke={MUTED}
              strokeWidth={1.7} strokeLinejoin="round" aria-hidden="true">
              <path d="M22 3H2l8 9.5V19l4 2v-8.5L22 3z" />
            </svg>
          </IconButton>
          <IconButton label="Sort notes" active={sort !== 'newest'}
            onClick={() => setMenu((m) => (m === 'sort' ? null : 'sort'))}>
            <svg width={20} height={20} viewBox="0 0 24 24" fill="none" stroke={MUTED}
              strokeWidth={1.7} strokeLinecap="round" aria-hidden="true">
              <path d="M4 6h16M7 12h10M10 18h4" />
            </svg>
          </IconButton>
          {menu && (
            <div role="menu" className="absolute right-0 z-20 bg-white"
              style={{ top: 36, minWidth: 200, borderRadius: 8, padding: 4,
                border: '1px solid ' + DIVIDER,
                boxShadow: '0 12px 16px -4px rgba(16,24,40,0.08)' }}>
              <div style={{ fontSize: 12, fontWeight: 600, color: FAINT, padding: '6px 10px' }}>
                {menu === 'filter' ? 'Created by' : 'Sort by'}
              </div>
              {(menu === 'filter'
                ? [{ key: '', label: 'Everyone' },
                   ...authors.map((a) => ({ key: a, label: a })),
                   { key: CONTACT_NOTES, label: "Contact's conversation" }]
                : [{ key: 'newest', label: 'Newest first' },
                   { key: 'oldest', label: 'Oldest first' }]).map((item) => {
                const on = menu === 'filter' ? author === item.key : sort === item.key
                return (
                  <button key={item.key} type="button" role="menuitemradio" aria-checked={on}
                    onClick={() => {
                      if (menu === 'filter') setAuthor(item.key)
                      else setSort(item.key as NoteSort)
                      setMenu(null)
                    }}
                    className="block w-full text-left hover:bg-[rgb(249,250,251)]"
                    style={{ padding: '8px 10px', fontSize: 14, borderRadius: 6,
                      color: on ? PRIMARY : BODY, fontWeight: on ? 600 : 400 }}>
                    {item.label}
                  </button>
                )
              })}
            </div>
          )}
        </div>
        <div className="relative" style={{ width: 212 }}>
          <svg className="pointer-events-none absolute" style={{ left: 12, top: 10 }}
            width={16} height={16} viewBox="0 0 24 24" fill="none" stroke={FAINT}
            strokeWidth={1.8} strokeLinecap="round" aria-hidden="true">
            <circle cx="11" cy="11" r="7" /><path d="m21 21-4.3-4.3" />
          </svg>
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search notes"
            aria-label="Search notes"
            style={{ ...INPUT, marginTop: 0, height: 36, paddingLeft: 36 }} />
        </div>
      </div>

      <div style={{ marginTop: 14 }}>
        {composing ? (
          <div style={{ border: '1px solid ' + BORDER, borderRadius: 10, padding: 12 }}>
            <textarea autoFocus value={draft} onChange={(e) => setDraft(e.target.value)}
              rows={5} placeholder="Write a note" aria-label="New note"
              style={{ ...INPUT, marginTop: 0, height: 'auto', padding: 10, resize: 'vertical' }} />
            <div className="mt-2 flex justify-end gap-2">
              <button type="button" style={{ ...BUTTON, height: 36 }}
                onClick={() => { setComposing(false); setDraft('') }}>Cancel</button>
              <button type="button" disabled={!draft.trim() || busy}
                style={{ ...PRIMARY_BUTTON, height: 36, ...dead(!!draft.trim() && !busy) }}
                onClick={() => add.mutate()}>
                {add.isPending ? 'Saving…' : 'Save note'}
              </button>
            </div>
          </div>
        ) : (
          <AddBar label="Add note" onClick={() => { setError(null); setComposing(true) }} />
        )}
      </div>

      <ErrorLine error={error ?? (notes.error ? (notes.error as Error).message : null)} />

      {notes.isLoading && (
        <div style={{ marginTop: 14, fontSize: 13, color: FAINT }}>Loading notes…</div>
      )}
      {notes.data && shown.length === 0 && (
        <div style={{ marginTop: 18, fontSize: 14, color: FAINT, textAlign: 'center' }}>
          {rows.length === 0 ? 'No notes yet.' : 'No notes match.'}
        </div>
      )}

      {shown.map((row) => row.kind === 'deal' && editing === row.id ? (
        <div key={'edit' + row.id} style={{ border: '1px solid ' + PRIMARY, borderRadius: 10,
          padding: 12, marginTop: 14 }}>
          <textarea autoFocus value={editDraft} onChange={(e) => setEditDraft(e.target.value)}
            rows={6} aria-label="Edit note"
            style={{ ...INPUT, marginTop: 0, height: 'auto', padding: 10, resize: 'vertical' }} />
          <div className="mt-2 flex justify-end gap-2">
            <button type="button" style={{ ...BUTTON, height: 36 }}
              onClick={() => setEditing(null)}>Cancel</button>
            <button type="button" disabled={!editDraft.trim() || busy}
              style={{ ...PRIMARY_BUTTON, height: 36, ...dead(!!editDraft.trim() && !busy) }}
              onClick={() => save.mutate(row.id)}>Save</button>
          </div>
        </div>
      ) : row.kind === 'deal' && confirming === row.id ? (
        <div key={'del' + row.id} role="alertdialog" style={{ border: '1px solid rgb(253,162,155)',
          backgroundColor: 'rgb(254,243,242)', borderRadius: 10, padding: 14, marginTop: 14 }}>
          <div style={{ fontSize: 14, color: 'rgb(180,35,24)' }}>
            Delete this note? It cannot be brought back.
          </div>
          <div className="mt-2 flex justify-end gap-2">
            <button type="button" style={{ ...BUTTON, height: 36 }}
              onClick={() => setConfirming(null)}>Keep it</button>
            <button type="button" disabled={busy}
              style={{ ...PRIMARY_BUTTON, height: 36, backgroundColor: 'rgb(217,45,32)',
                borderColor: 'rgb(217,45,32)', ...dead(!busy) }}
              onClick={() => remove.mutate(row.id)}>Delete note</button>
          </div>
        </div>
      ) : (
        <NoteCard
          key={row.kind + row.id}
          row={row}
          onEdit={row.kind === 'deal' ? () => {
            setError(null); setEditing(row.id); setEditDraft(row.body)
          } : undefined}
          onDelete={row.kind === 'deal' ? () => {
            setError(null); setConfirming(row.id)
          } : undefined}
        />
      ))}
    </div>
  )
}
