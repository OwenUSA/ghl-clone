/**
 * Kanban drop arithmetic, kept free of React and of any import so it can be
 * executed directly by the backend test suite (`node` imports this .ts file —
 * see backend/tests/test_board_order.py). The page must not re-derive any of it.
 *
 * Two bugs it exists to fix, both in OpportunitiesPage's `onDragEnd`:
 *
 *   1. `if (o.stage_id === stageId) return` — a drop back into the column the
 *      card came from was ignored outright, so cards could not be reordered.
 *   2. the PATCH carried no position, so `moveOpportunity`'s default of 0 sent
 *      every cross-column drop to the TOP of the target column rather than
 *      where it was released.
 *
 * The awkward part, and the reason this is arithmetic rather than an index:
 * `position` is an absolute rank among ALL of a stage's cards, while the board
 * draws a column filtered by status and by the search box. Dropping "between the
 * two cards I can see" is therefore not "at index 1" — a won card the filter is
 * hiding may sit between them. So the drop is anchored to the card it landed
 * BELOW, whose absolute position the server now tells us, and hidden cards keep
 * their own relative order around it.
 */

/** The fields of an opportunity this module needs. The page passes whole rows. */
export type BoardCard = { id: number; stage_id: number; position: number }

/** What the PATCH body carries. `null` means "this drop changes nothing". */
export type BoardMove = { id: number; stage_id: number; position: number }

/**
 * dnd-kit ids.
 *
 * Namespaced because a stage id and an opportunity id are both small integers
 * from different sequences: stage 3 and opportunity 3 both exist, and a bare
 * number would make a drop on a column indistinguishable from a drop on a card.
 */
export const cardDragId = (id: number) => 'card:' + id
export const stageDropId = (id: number) => 'stage:' + id

const parse = (prefix: string, id: string | number): number | null => {
  const s = String(id)
  if (!s.startsWith(prefix)) return null
  const n = Number(s.slice(prefix.length))
  return Number.isFinite(n) ? n : null
}

export const cardIdFrom = (id: string | number) => parse('card:', id)
export const stageIdFrom = (id: string | number) => parse('stage:', id)

/** Board order: `position` ascending, `id` as the tiebreak — same as the API's. */
const byPosition = (a: BoardCard, b: BoardCard) =>
  a.position - b.position || a.id - b.id

/** The cards of one stage, in the order the column draws them. */
export const stageCards = <T extends BoardCard>(cards: T[], stageId: number) =>
  cards.filter((c) => c.stage_id === stageId).sort(byPosition)

/**
 * The `position` to send for a card that should end up at `destIndex` of the
 * destination column as the user can see it.
 *
 * The server inserts at `position` among the stage's siblings ordered by
 * position, so what it wants is "how many of this stage's other cards belong
 * above me". Counting the visible ones would be wrong wherever the status filter
 * or the search box is hiding a card, so the anchor is the visible card directly
 * above the drop and the answer is "just below that one" — which is `anchor + 1`
 * absolute positions in, except when the card is moving down its own column, in
 * which case its own removal has already shifted the anchor up by one.
 */
export function positionForDrop(
  cards: BoardCard[], cardId: number, destStageId: number, destIndex: number,
): number | null {
  const card = cards.find((c) => c.id === cardId)
  if (!card) return null
  const others = stageCards(cards, destStageId).filter((c) => c.id !== cardId)
  const index = Math.max(0, Math.min(destIndex, others.length))
  if (index === 0) return 0
  const anchor = others[index - 1]
  const sameStage = card.stage_id === destStageId
  return anchor.position + (sameStage && card.position < anchor.position ? 0 : 1)
}

/**
 * Resolve one dnd-kit drop into the move to send, or `null` for no move.
 *
 * `overId` is whatever the card was released on: another card, or a column's own
 * droppable when the column is empty or the card was dropped below the last one.
 *
 * A drop that changes nothing returns `null` and the page issues no request at
 * all. That is not only an efficiency point: `PATCH /api/opportunities` runs the
 * stage-change automation, and the fewer no-op writes reach it the smaller the
 * surface for Rule 4 to text a customer about a move that did not happen. (The
 * automation guards this itself — it returns "stage unchanged" — and the guard,
 * not this, is what makes a reorder safe.)
 */
export function moveForDrop(
  cards: BoardCard[], activeId: string | number, overId: string | number | null,
): BoardMove | null {
  if (overId == null) return null
  const cardId = cardIdFrom(activeId)
  if (cardId == null) return null
  const card = cards.find((c) => c.id === cardId)
  if (!card) return null

  const overCardId = cardIdFrom(overId)
  let destStageId: number
  let destIndex: number

  if (overCardId != null) {
    const over = cards.find((c) => c.id === overCardId)
    if (!over) return null
    destStageId = over.stage_id
    // The dragged card takes the slot of the card it was dropped on, which is
    // that card's index in the column as drawn now. For a move down its own
    // column this is also what `arrayMove` would compute.
    destIndex = stageCards(cards, destStageId).findIndex((c) => c.id === overCardId)
  } else {
    const stageId = stageIdFrom(overId)
    if (stageId == null) return null
    destStageId = stageId
    // The column itself, not a card: the empty area below the last card. Land at
    // the bottom — and if the card is already in this column, the bottom is one
    // slot higher than the count, because it is leaving its current slot.
    const drawn = stageCards(cards, destStageId)
    destIndex = card.stage_id === destStageId ? drawn.length - 1 : drawn.length
  }

  const position = positionForDrop(cards, cardId, destStageId, destIndex)
  if (position == null) return null
  if (destStageId === card.stage_id && position === card.position) return null
  return { id: cardId, stage_id: destStageId, position }
}

/**
 * The board as it will be once the server has applied `move` — used to draw the
 * card in its new place the instant it is dropped, before the PATCH answers.
 *
 * This mirrors `move_opportunity` exactly, including its re-pack of both stages,
 * so that a successful move refetches to the arrangement already on screen and
 * nothing jumps. `backend/tests/test_board_order.py` runs the same scenarios
 * through the real endpoint and through this function and compares them, because
 * "the optimistic board and the server agree" is the whole reason a silent
 * failure cannot hide here.
 *
 * Cards the filter is hiding are absent from `cards` and so are not returned,
 * but their absolute positions still shift on the server in exactly this way,
 * which is why the arithmetic is written on positions rather than on indices.
 */
export function applyMove<T extends BoardCard>(cards: T[], move: BoardMove): T[] {
  const card = cards.find((c) => c.id === move.id)
  if (!card) return cards
  const from = card.stage_id
  const was = card.position

  return cards
    .map((c) => {
      if (c.id === move.id) {
        return { ...c, stage_id: move.stage_id, position: move.position }
      }
      let p = c.position
      // Closing the gap the card left behind...
      if (c.stage_id === from && p > was) p -= 1
      // ...and opening one for it where it landed.
      if (c.stage_id === move.stage_id && p >= move.position) p += 1
      return p === c.position ? c : { ...c, position: p }
    })
    .sort(byPosition)
}
