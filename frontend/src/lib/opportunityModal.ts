/**
 * Which tab the opportunity modal opens on.
 *
 * The board card's icons open the modal ON THE MATCHING TAB (the owner's spec,
 * 2026-09-13, screenshot 22): notes -> Notes, tags -> Opportunity details scrolled
 * to Tags, add task -> Tasks with the add form ready, add appointment -> Book or
 * update appointment. A click on the card body opens Opportunity details.
 *
 * The page owns WHETHER the modal is open (`OpportunitiesPage` holds the id) and
 * the card only has `onOpen(id)`. So the card leaves a one-shot request here just
 * before calling it, and the modal takes it when it mounts. Taking clears it, so a
 * later open from the ctrl+K palette or a list row lands on Opportunity details,
 * not on whatever tab the last icon asked for.
 *
 * Import-free, so backend/tests/test_opportunity_card_ui.py runs it under node.
 */

export type ModalTab =
  | 'details'
  | 'appointment'
  | 'tasks'
  | 'notes'
  | 'associated'
  | `group:${number}`

export type ModalRequest = {
  tab: ModalTab
  /** Scroll the details tab to this field once it renders (the tags icon). */
  focus?: 'tags'
  /** Open the Tasks tab with the add form already showing (the add-task icon). */
  adding?: boolean
}

/** What each card icon asks for. Call and View conversations open no modal. */
export const CARD_ICON_REQUEST: Record<'tags' | 'notes' | 'task' | 'appointment',
  ModalRequest> = {
  tags: { tab: 'details', focus: 'tags' },
  notes: { tab: 'notes' },
  task: { tab: 'tasks', adding: true },
  appointment: { tab: 'appointment' },
}

let pending: { id: number; request: ModalRequest } | null = null

export function requestModalTab(opportunityId: number, request: ModalRequest): void {
  pending = { id: opportunityId, request }
}

/**
 * The request for THIS opportunity, once. A request left for a different deal is
 * discarded rather than applied to the wrong one.
 */
export function takeModalTab(opportunityId: number): ModalRequest {
  const hit = pending
  pending = null
  return hit && hit.id === opportunityId ? hit.request : { tab: 'details' }
}
