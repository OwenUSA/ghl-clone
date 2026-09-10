/**
 * What a saved view does to the Opportunities board.
 *
 * Written free of React and of any import, like `calendarGrid.ts` and `csv.ts`,
 * so node can execute the real module — see `backend/tests/test_saved_views.py`.
 * "Applying a saved view actually changes what the board asks for" is behaviour,
 * and a saved view that quietly resolved back to the filters already in effect
 * would look completely fine on screen.
 */

/** The three things the Opportunities board filters by. */
export type BoardFilters = {
  pipelineId: number | undefined
  status: string
  q: string
}

/** The board's own default, and the built-in "Open opportunities" list. */
export const DEFAULT_FILTERS = { status: 'open', q: '' }

/** Only the parts of a saved view that decide what the board shows. */
export type ViewFilters = {
  pipeline_id: number | null
  status: string
  q: string
}

/**
 * The filters this view puts on the board.
 *
 * A view that named a pipeline switches to it; one that did not leaves the
 * pipeline alone and only re-filters, so "Won deals" is useful on whichever
 * board you are looking at.
 */
export function applyView(view: ViewFilters, now: BoardFilters): BoardFilters {
  return {
    pipelineId: view.pipeline_id ?? now.pipelineId,
    status: view.status,
    q: view.q,
  }
}

/** Is the board currently showing exactly what this view describes? */
export function matchesView(view: ViewFilters, now: BoardFilters): boolean {
  if (view.pipeline_id !== null && view.pipeline_id !== now.pipelineId) return false
  return view.status === now.status && view.q === now.q.trim()
}

/** True when the board is on its own default and no saved view is in effect. */
export function isDefaultView(now: BoardFilters): boolean {
  return now.status === DEFAULT_FILTERS.status && now.q.trim() === DEFAULT_FILTERS.q
}

/** "Status: won · matching “skylight” · in Dream Team Roofing AHS" */
export function describeView(
  view: ViewFilters & { pipeline_name?: string | null },
): string {
  const bits = ['Status: ' + view.status]
  if (view.q) bits.push('matching “' + view.q + '”')
  if (view.pipeline_name) bits.push('in ' + view.pipeline_name)
  return bits.join(' · ')
}
