import { useSortable } from '@dnd-kit/sortable'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { cardDragId } from '../lib/boardOrder'
import {
  ApiError,
  callContact,
  listUsers,
  money,
  openContactConversation,
  patchOpportunity,
  type Opportunity,
} from '../lib/api'
import { me } from '../lib/auth'
import { canOpenRecords, openRecord } from '../lib/openRecord'
import { cardAddressLine } from '../lib/opportunityAddress'
import { CARD_ICON_REQUEST, checklistBadge, requestModalTab } from '../lib/opportunityModal'

/** The board's card layouts — the same union `OpportunitiesPage` declares. */
export type Layout = 'Default' | 'Compact' | 'Unlabeled'

/**
 * GoHighLevel's card, rebuilt from the owner's screenshots 15-21 (2026-09-13).
 *
 *   title (truncated) · circular assign-owner button · checkbox
 *   Value:  $100.00
 *   12 Palm Ave, Bradenton          (the job's address — only when the card has one)
 *   call · view conversations · tags(n) · notes(n) · add task · add an appointment
 *
 * Every icon is a thin outline SVG — no emoji anywhere on the card, by the owner's
 * instruction — grey at rest and blue on hover, with a dark tooltip above it.
 * Every icon DOES something; one that cannot work for this card is not drawn:
 *
 *   * Call and View conversations need a primary contact. Call goes through the
 *     existing click-to-call path (`POST /api/contacts/{id}/call`) and says what
 *     happened; View conversations opens the contact's thread in Conversations,
 *     and is drawn only once the shell has registered a way to switch pages
 *     (lib/openRecord.ts). Neither opens the modal.
 *   * Tags lists the tag names in its tooltip and opens Opportunity details at
 *     Tags. Notes lists each note's first line and opens the Notes tab. Neither
 *     icon is drawn for a TECH's notes — the server never sends a TECH a count.
 *   * Add task opens Tasks with the add form ready; Add an appointment opens Book
 *     or update appointment.
 *
 * A badge appears only when a count is above zero. The icons stop the click from
 * reaching the card, so only a click on the card BODY opens Opportunity details.
 */
const GREY = 'rgb(102,112,133)'
const BLUE = 'rgb(21,94,239)'

type Tip = React.ReactNode

/** The dark tooltip, drawn in a portal: the board column clips its overflow. */
function Tooltip({ anchor, children }: { anchor: HTMLElement; children: Tip }) {
  const ref = useRef<HTMLDivElement>(null)
  const [at, setAt] = useState<{ left: number; top: number } | null>(null)
  useLayoutEffect(() => {
    const a = anchor.getBoundingClientRect()
    const box = ref.current?.getBoundingClientRect()
    const width = box?.width ?? 0
    const height = box?.height ?? 0
    setAt({
      left: Math.max(4, Math.min(window.innerWidth - width - 4,
        a.left + a.width / 2 - width / 2)),
      top: Math.max(4, a.top - height - 8),
    })
  }, [anchor, children])
  return createPortal(
    <div
      ref={ref}
      role="tooltip"
      style={{
        position: 'fixed', zIndex: 60, left: at?.left ?? -9999, top: at?.top ?? -9999,
        backgroundColor: 'rgb(16,24,40)', color: '#fff', fontSize: 13, lineHeight: '18px',
        fontWeight: 400, borderRadius: 6, padding: '6px 10px', maxWidth: 260,
        pointerEvents: 'none', whiteSpace: 'pre-line',
        boxShadow: '0 4px 8px -2px rgba(16,24,40,0.1)',
      }}
    >
      {children}
    </div>,
    document.body)
}

function CardIcon({ label, tip, badge, onClick, children }: {
  label: string
  tip: Tip
  badge?: number
  onClick?: () => void
  children: (color: string) => React.ReactNode
}) {
  const ref = useRef<HTMLButtonElement>(null)
  const [hover, setHover] = useState(false)
  return (
    <button
      ref={ref}
      type="button"
      aria-label={label}
      onClick={(e) => { e.stopPropagation(); onClick?.() }}
      // A press on an icon is not the start of a drag.
      onPointerDown={(e) => e.stopPropagation()}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      onFocus={() => setHover(true)}
      onBlur={() => setHover(false)}
      className="relative flex items-center justify-center"
      style={{ width: 20, height: 20, cursor: onClick ? 'pointer' : 'grab' }}
    >
      {children(hover && onClick ? BLUE : GREY)}
      {badge != null && badge > 0 && (
        <span
          data-badge={label}
          style={{
            position: 'absolute', top: -6, left: 9, minWidth: 14, height: 14,
            padding: '0 3px', borderRadius: 7, backgroundColor: BLUE, color: '#fff',
            fontSize: 10, fontWeight: 600, lineHeight: '14px', textAlign: 'center',
            boxSizing: 'border-box',
          }}
        >
          {badge}
        </span>
      )}
      {hover && onClick && ref.current && <Tooltip anchor={ref.current}>{tip}</Tooltip>}
    </button>
  )
}

/**
 * The Checklist's progress, at the right end of the icon row (2026-09-14): a small
 * grey "3/16" beside a clipboard tick, green once every question is answered. It is a
 * COUNT, never an answer — the card still shows no custom field (2026-09-11). A click
 * opens the modal on the Checklist tab.
 */
function ChecklistBadge({ badge, onClick }: {
  badge: NonNullable<ReturnType<typeof checklistBadge>>
  onClick?: () => void
}) {
  const ref = useRef<HTMLButtonElement>(null)
  const [hover, setHover] = useState(false)
  const color = badge.done ? 'rgb(2,122,72)' : hover && onClick ? BLUE : 'rgb(102,112,133)'
  return (
    <button
      ref={ref}
      type="button"
      aria-label={badge.tip}
      data-card-checklist
      onClick={(e) => { e.stopPropagation(); onClick?.() }}
      onPointerDown={(e) => e.stopPropagation()}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      onFocus={() => setHover(true)}
      onBlur={() => setHover(false)}
      className="flex items-center"
      style={{ marginLeft: 'auto', gap: 3, height: 20, fontSize: 11, fontWeight: 500,
        lineHeight: '20px', color, cursor: onClick ? 'pointer' : 'grab' }}
    >
      {svg(color, <>
        <rect x="6" y="4" width="12" height="17" rx="2" />
        <path d="M9 4V3h6v1M9.5 13l2 2 3.5-4" />
      </>, 14)}
      {badge.text}
      {hover && onClick && ref.current && <Tooltip anchor={ref.current}>{badge.tip}</Tooltip>}
    </button>
  )
}

// ---- the six line icons, 16px on a 24 grid, stroke 1.5 (GoHighLevel's weight) ----

const svg = (color: string, body: React.ReactNode, size = 16) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color}
    strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {body}
  </svg>
)

export const CardIcons = {
  call: (c: string) => svg(c, <>
    <path d="M14.05 2a9 9 0 0 1 8 7.94M14.05 6A5 5 0 0 1 18 10" />
    <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.8 19.8 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.12 4.18 2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.13.96.36 1.9.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.91.34 1.85.57 2.81.7A2 2 0 0 1 22 16.92z" />
  </>),
  conversations: (c: string) => svg(c, <>
    <path d="M21 11.5a8.4 8.4 0 0 1-9 8.4 8.7 8.7 0 0 1-3.9-.9L3 21l1.9-5a8.4 8.4 0 0 1-.9-3.9A8.4 8.4 0 0 1 12.4 3.7h.5A8.4 8.4 0 0 1 21 11z" />
    <path d="M8 11.5h.01M12 11.5h.01M16 11.5h.01" strokeWidth={2.2} />
  </>),
  tags: (c: string) => svg(c, <>
    <path d="M20.59 13.41 13.42 20.58a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z" />
    <path d="M7 7h.01" strokeWidth={2.2} />
  </>),
  notes: (c: string) => svg(c, <>
    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
    <path d="M14 2v6h6M16 13H8M16 17H8M10 9H8" />
  </>),
  task: (c: string) => svg(c, <>
    <rect x="3" y="3" width="18" height="18" rx="3" />
    <path d="m8.5 12 2.5 2.5 4.5-5" />
  </>),
  appointment: (c: string) => svg(c, <>
    <rect x="3" y="4" width="18" height="18" rx="3" />
    <path d="M16 2v4M8 2v4M3 10h18M12 13.5v5M9.5 16h5" />
  </>),
  assignOwner: (c: string) => svg(c, <>
    <path d="M15 20v-1.5a3.5 3.5 0 0 0-3.5-3.5h-4A3.5 3.5 0 0 0 4 18.5V20" />
    <circle cx="9.5" cy="8" r="3.5" />
    <path d="M19 8v5M16.5 10.5h5" />
  </>, 14),
}

/** The owner's name for the tooltip, and the picker that assigns one in place. */
function OwnerButton({ o }: { o: Opportunity }) {
  const qc = useQueryClient()
  const ref = useRef<HTMLButtonElement>(null)
  const [open, setOpen] = useState(false)
  const [hover, setHover] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const session = useQuery({ queryKey: ['me'], queryFn: me, staleTime: Infinity })
  // PATCH /api/opportunities/{id}/detail is auth.STAFF. A TECH gets the control
  // dead with the reason, never a picker that 403s (d1f7c50, b943f4b).
  const canAssign = session.data?.user.role !== 'TECH'
  const users = useQuery({ queryKey: ['users'], queryFn: listUsers, enabled: open })

  const assign = useMutation({
    mutationFn: (ownerId: number | null) => patchOpportunity(o.id, { owner_id: ownerId }),
    onSuccess: () => {
      setOpen(false)
      qc.invalidateQueries({ queryKey: ['opportunities'] })
      qc.invalidateQueries({ queryKey: ['opportunity', o.id] })
    },
    onError: (e: Error) => setError(e.message),
  })

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  const rect = open ? ref.current?.getBoundingClientRect() : undefined
  const tip = canAssign
    ? (o.owner_name ? 'Owner: ' + o.owner_name : 'Assign owner')
    : 'Your role cannot change the owner'
  const initials = (o.owner_name ?? '').split(/\s+/).filter(Boolean)
    .map((w) => w[0]).slice(0, 2).join('').toUpperCase()

  return (
    <>
      <button
        ref={ref}
        type="button"
        aria-label={o.owner_name ? 'Owner ' + o.owner_name : 'Assign owner'}
        aria-haspopup="menu"
        disabled={!canAssign}
        title={canAssign ? undefined : tip}
        onPointerDown={(e) => e.stopPropagation()}
        onClick={(e) => { e.stopPropagation(); setError(null); setOpen((v) => !v) }}
        onMouseEnter={() => setHover(true)}
        onMouseLeave={() => setHover(false)}
        className="flex shrink-0 items-center justify-center"
        style={{
          width: 24, height: 24, borderRadius: 12,
          border: '1px solid ' + (hover && canAssign ? BLUE : 'rgb(152,162,179)'),
          backgroundColor: o.owner_name ? 'rgb(239,244,255)' : '#fff',
          color: BLUE, fontSize: 10, fontWeight: 600,
          cursor: canAssign ? 'pointer' : 'not-allowed',
        }}
      >
        {initials || CardIcons.assignOwner(hover && canAssign ? BLUE : GREY)}
      </button>
      {hover && canAssign && !open && ref.current && (
        <Tooltip anchor={ref.current}>{tip}</Tooltip>
      )}
      {open && rect && createPortal(
        <>
          <div className="fixed inset-0" style={{ zIndex: 55 }}
            onPointerDown={(e) => e.stopPropagation()}
            onClick={(e) => { e.stopPropagation(); setOpen(false) }} />
          <div
            role="menu"
            onClick={(e) => e.stopPropagation()}
            style={{
              position: 'fixed', zIndex: 56,
              left: Math.min(rect.left, window.innerWidth - 228), top: rect.bottom + 4,
              width: 220, maxHeight: 280, overflowY: 'auto', backgroundColor: '#fff',
              borderRadius: 8, border: '1px solid rgb(234,236,240)', padding: 4,
              boxShadow: '0 12px 16px -4px rgba(16,24,40,0.08)',
            }}
          >
            <div style={{ fontSize: 12, fontWeight: 600, color: GREY,
              padding: '6px 10px' }}>
              Assign owner
            </div>
            {[{ id: null as number | null, name: 'Unassigned' },
              ...(users.data ?? []).filter((u) =>
                (u as { is_active?: boolean }).is_active !== false)].map((u) => (
              <button
                key={u.id ?? 'none'}
                role="menuitemradio"
                aria-checked={o.owner_id === u.id}
                disabled={assign.isPending}
                onClick={() => assign.mutate(u.id)}
                className="block w-full text-left hover:bg-[rgb(249,250,251)]"
                style={{
                  padding: '8px 10px', fontSize: 14, borderRadius: 6,
                  color: o.owner_id === u.id ? BLUE : 'rgb(52,64,84)',
                  fontWeight: o.owner_id === u.id ? 600 : 400,
                }}
              >
                {u.name}
              </button>
            ))}
            {users.isLoading && (
              <div style={{ fontSize: 13, color: GREY, padding: '6px 10px' }}>Loading…</div>
            )}
            {error && (
              <div role="alert" style={{ fontSize: 12, color: 'rgb(180,35,24)',
                padding: '6px 10px' }}>
                {error}
              </div>
            )}
          </div>
        </>,
        document.body)}
    </>
  )
}

/** A short-lived line under the card saying what Call or Conversations did. */
function useFlash() {
  const [flash, setFlash] = useState<{ text: string; bad: boolean } | null>(null)
  useEffect(() => {
    if (!flash) return
    const t = window.setTimeout(() => setFlash(null), 6000)
    return () => window.clearTimeout(t)
  }, [flash])
  return [flash, setFlash] as const
}

/**
 * The card's own markup, with no drag wiring — also what the drag preview draws.
 *
 * The selection controls live here rather than on the drag wrapper so the Bulk
 * Actions checkbox is part of the face, and the DragOverlay — which renders a
 * face with no selection props — carries a plain card rather than a checkbox.
 */
export function CardFace({
  o, layout, selectable = false, selected = false, onToggle, onOpen,
}: {
  o: Opportunity
  layout: Layout
  /** Bulk Actions tab: the card picks rather than opens. */
  selectable?: boolean
  selected?: boolean
  onToggle?: (id: number) => void
  /** Opens the modal. Absent on the drag preview, whose icons do nothing. */
  onOpen?: (id: number) => void
}) {
  const [flash, setFlash] = useFlash()
  const [busy, setBusy] = useState(false)
  const live = !!onOpen

  const openOn = (icon: keyof typeof CARD_ICON_REQUEST) => {
    if (!onOpen) return
    requestModalTab(o.id, CARD_ICON_REQUEST[icon])
    onOpen(o.id)
  }

  const call = async () => {
    if (o.contact_id == null || busy) return
    setBusy(true)
    try {
      const r = await callContact(o.contact_id)
      setFlash({ text: r.reason, bad: !r.placed })
    } catch (err) {
      setFlash({ text: err instanceof ApiError ? err.message
        : 'The call could not be placed.', bad: true })
    } finally {
      setBusy(false)
    }
  }

  const conversations = async () => {
    if (o.contact_id == null || busy) return
    setBusy(true)
    try {
      const r = await openContactConversation(o.contact_id)
      openRecord('conversations', r.conversation_id)
    } catch (err) {
      setFlash({ text: err instanceof ApiError ? err.message
        : 'The conversation could not be opened.', bad: true })
    } finally {
      setBusy(false)
    }
  }

  const notesVisible = o.notes_count !== undefined
  const address = cardAddressLine(o)
  const checklist = checklistBadge(o.checklist)

  return (
    <>
      <div className="flex items-start gap-2">
        <div
          className="min-w-0 flex-1 truncate"
          title={o.title}
          style={{ fontSize: 14, fontWeight: 500, color: 'rgb(52,64,84)', lineHeight: '24px' }}
        >
          {o.title}
        </div>
        {live && <OwnerButton o={o} />}
        {selectable && (
          <input
            type="checkbox"
            checked={selected}
            aria-label={'Select ' + o.title}
            // The card's own onClick already toggles; without this the change and
            // the click both fire and the selection lands back where it started.
            onChange={() => {}}
            onClick={(e) => { e.stopPropagation(); onToggle?.(o.id) }}
            style={{ marginTop: 5, width: 16, height: 16 }}
          />
        )}
      </div>
      {/* Measured: "Value:" and the amount are TWO spans —
          label 12px/600 rgb(96,113,121), amount 12px/400 rgb(96,113,121).
          Ours rendered them as one 13px/400 string. */}
      {layout !== 'Unlabeled' && (
        <div className="flex items-center gap-1" style={{ marginTop: 6 }}>
          {layout === 'Default' && (
            <span style={{ fontSize: 12, fontWeight: 600, color: 'rgb(96,113,121)',
              minWidth: 72 }}>
              Value:
            </span>
          )}
          <span style={{ fontSize: 12, fontWeight: 400, color: 'rgb(96,113,121)' }}>
            {money(o.value_cents)}
          </span>
        </div>
      )}
      {/* The job's address (2026-09-14): ONE subtle grey line under Value, "street,
          city", cut with an ellipsis. Drawn only when the card has an address and
          only where Value is — the rest of GoHighLevel's card is unchanged. */}
      {layout !== 'Unlabeled' && address && (
        <div className="truncate" title={address} data-card-address
          style={{ fontSize: 12, lineHeight: '18px', color: 'rgb(152,162,179)', marginTop: 2 }}>
          {address}
        </div>
      )}
      {layout === 'Default' && o.business_name && (
        <div className="truncate" style={{ fontSize: 12, color: 'rgb(102,112,133)' }}>
          {o.business_name}
        </div>
      )}
      {layout === 'Default' && (
        <div className="flex items-center" style={{ marginTop: 12, gap: 4 }}
          data-card-icons>
          {o.contact_id != null && (
            <CardIcon label="Call" tip="Call" onClick={live ? call : undefined}>
              {CardIcons.call}
            </CardIcon>
          )}
          {/* Only when the shell can switch pages — see lib/openRecord.ts. */}
          {o.contact_id != null && canOpenRecords() && (
            <CardIcon label="View conversations" tip="View conversations"
              onClick={live ? conversations : undefined}>
              {CardIcons.conversations}
            </CardIcon>
          )}
          <CardIcon
            label="Tags"
            badge={o.tags?.length ?? 0}
            tip={o.tags?.length ? o.tags.join('\n') : 'No tags'}
            onClick={live ? () => openOn('tags') : undefined}
          >
            {CardIcons.tags}
          </CardIcon>
          {notesVisible && (
            <CardIcon
              label="Notes"
              badge={o.notes_count}
              tip={o.note_previews?.length ? (
                <div style={{ maxWidth: 240 }}>
                  {o.note_previews.map((line, i) => (
                    <div key={i} className="truncate">{line}</div>
                  ))}
                </div>
              ) : 'No notes'}
              onClick={live ? () => openOn('notes') : undefined}
            >
              {CardIcons.notes}
            </CardIcon>
          )}
          <CardIcon label="Add task" tip="Add task" badge={o.open_tasks_count}
            onClick={live ? () => openOn('task') : undefined}>
            {CardIcons.task}
          </CardIcon>
          <CardIcon label="Add an appointment" tip="Add an appointment"
            onClick={live ? () => openOn('appointment') : undefined}>
            {CardIcons.appointment}
          </CardIcon>
          {checklist && (
            <ChecklistBadge badge={checklist} onClick={live && onOpen ? () => {
              requestModalTab(o.id, checklist.request)
              onOpen(o.id)
            } : undefined} />
          )}
        </div>
      )}
      {flash && (
        <div
          role={flash.bad ? 'alert' : 'status'}
          onClick={(e) => e.stopPropagation()}
          style={{ marginTop: 8, fontSize: 12, lineHeight: '16px',
            color: flash.bad ? 'rgb(180,35,24)' : 'rgb(2,122,72)' }}
        >
          {flash.text}
        </div>
      )}
    </>
  )
}

export const CARD_SURFACE = {
  width: 230,
  borderRadius: 4,
  backgroundColor: '#fff',
  boxShadow: 'rgba(16, 24, 40, 0.1) 0px 1px 3px 0px',
  padding: 12,
} as const

/**
 * A sortable card.
 *
 * `useSortable` rather than `useDraggable`: the cards inside a column need to be
 * a sortable list, not just cargo for a drop target, or the column can only
 * answer "a card was dropped on me somewhere" and a reorder has no index.
 */
export function Card({
  o, layout, onOpen, dragging, selectable = false, selected = false, onToggle,
}: {
  o: Opportunity
  layout: Layout
  onOpen: (id: number) => void
  /** True from the start of a drag until the next press — see the page. */
  dragging: React.RefObject<boolean>
  /** Bulk Actions tab: the card picks rather than opens. */
  selectable?: boolean
  selected?: boolean
  onToggle?: (id: number) => void
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } =
    useSortable({ id: cardDragId(o.id) })
  return (
    <div
      ref={setNodeRef}
      {...listeners}
      {...attributes}
      // A drag ends with a click on the card it started from, which opened the
      // opportunity dialog on top of the board every single time a card was
      // moved. The drop is the gesture; the dialog is not part of it.
      // Released on the next press rather than on a timer: a click is dispatched
      // after the drop, and there is no delay that is reliably longer than the
      // browser's own gap and reliably shorter than a deliberate second click.
      onPointerDownCapture={() => { dragging.current = false }}
      // Selecting takes precedence over opening, exactly as it does on the
      // checkbox: in Bulk Actions the whole card is the picker.
      onClick={() => {
        if (dragging.current) return
        if (selectable && onToggle) onToggle(o.id)
        else onOpen(o.id)
      }}
      style={{
        ...CARD_SURFACE,
        marginBottom: 8,
        cursor: 'grab',
        // `touch-action: none` is what makes the pointer sensor work on a
        // trackpad and a touchscreen — without it the browser claims the gesture
        // as a scroll and the card never lifts.
        touchAction: 'none',
        // The card being dragged is drawn by the overlay instead. Leaving a hole
        // rather than a ghost is what makes the gap the other cards open up read
        // as "it will land here".
        opacity: isDragging ? 0 : 1,
        transform: transform
          ? `translate3d(${transform.x}px, ${transform.y}px, 0)`
          : undefined,
        transition,
        outline: selected ? '2px solid rgb(0,78,235)' : undefined,
      }}
    >
      <CardFace
        o={o}
        layout={layout}
        selectable={selectable}
        selected={selected}
        onToggle={onToggle}
        // In Bulk Actions the card is a picker, so its icons open nothing.
        onOpen={selectable ? undefined : onOpen}
      />
    </div>
  )
}
