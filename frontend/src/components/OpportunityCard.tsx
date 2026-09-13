import { useSortable } from '@dnd-kit/sortable'
import { cardDragId } from '../lib/boardOrder'
import { money, type Opportunity } from '../lib/api'

/** The board's card layouts — the same union `OpportunitiesPage` declares. */
export type Layout = 'Default' | 'Compact' | 'Unlabeled'

/**
 * The card's own markup, with no drag wiring — also what the drag preview draws.
 *
 * The selection controls live here rather than on the drag wrapper so the Bulk
 * Actions checkbox is part of the face, and the DragOverlay — which renders a
 * face with no selection props — carries a plain card rather than a checkbox.
 */
export function CardFace({ o, layout, selectable = false, selected = false, onToggle }: {
  o: Opportunity
  layout: Layout
  /** Bulk Actions tab: the card picks rather than opens. */
  selectable?: boolean
  selected?: boolean
  onToggle?: (id: number) => void
}) {
  return (
    <>
      <div className="flex items-start gap-2">
        {selectable && (
          <input
            type="checkbox"
            checked={selected}
            aria-label={'Select ' + o.title}
            // The card's own onClick already toggles; without this the change and
            // the click both fire and the selection lands back where it started.
            onChange={() => {}}
            onClick={(e) => { e.stopPropagation(); onToggle?.(o.id) }}
            style={{ marginTop: 2 }}
          />
        )}
        <div
          className="min-w-0 flex-1 truncate"
          style={{ fontSize: 14, fontWeight: 500, color: 'rgb(52,64,84)' }}
        >
          {o.title}
        </div>
      </div>
      {/* Measured: "Value:" and the amount are TWO spans —
          label 12px/600 rgb(96,113,121), amount 12px/400 rgb(96,113,121).
          Ours rendered them as one 13px/400 string. */}
      {layout !== 'Unlabeled' && (
        <div className="flex items-center gap-1" style={{ marginTop: 6 }}>
          {layout === 'Default' && (
            <span style={{ fontSize: 12, fontWeight: 600, color: 'rgb(96,113,121)' }}>
              Value:
            </span>
          )}
          <span style={{ fontSize: 12, fontWeight: 400, color: 'rgb(96,113,121)' }}>
            {money(o.value_cents)}
          </span>
        </div>
      )}
      {layout === 'Default' && o.business_name && (
        <div className="truncate" style={{ fontSize: 12, color: 'rgb(102,112,133)' }}>
          {o.business_name}
        </div>
      )}
      {layout === 'Default' && (
        <div className="mt-2 flex gap-2" style={{ fontSize: 12, color: 'rgb(152,162,179)' }}>
          {/* GHL renders 7 quick-action icons per card. Call and SMS are stubbed
              per DECISIONS.md, so they are shown but inert. */}
          <span title="Call (stubbed)">☎</span>
          <span title="SMS (stubbed)">💬</span>
          <span title="Notes">📄</span>
          <span title="Tasks">☑</span>
          <span title="Appointments">📅</span>
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
      />
    </div>
  )
}
