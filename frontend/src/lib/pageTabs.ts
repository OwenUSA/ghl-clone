/**
 * The page tab bar and the Opportunities pipeline dropdown, with no React in them.
 *
 * IMPORT-FREE on purpose, like `pipelines.ts`: `backend/tests/test_page_tabs.py`
 * runs this exact file under node, so the tab colours, the dropdown's rows and its
 * keyboard movement are asserted by executing the shipped code.
 */

/**
 * Measured from refs/opps/05-08 and pinned by refs/round3/23: 14px/500, inactive
 * rgb(71,84,103), active rgb(56,160,219) with a 2px underline in the same blue
 * that runs the width of the label plus its padding, flush with the bar.
 */
export const TAB_BLUE = 'rgb(56,160,219)'
export const TAB_INK = 'rgb(71,84,103)'
export const TAB_DIM = 'rgb(152,162,179)'

/**
 * One tab. `onSelect` absent = the tab is a label today and does nothing when
 * clicked, which is exactly what it did before the bar was shared. `blocked` is a
 * role refusal: rendered dimmed with the reason on hover.
 */
export type PageTab = {
  key: string
  label: string
  onSelect?: () => void
  blocked?: string
}

export function tabLook(active: boolean, blocked: boolean) {
  return {
    color: active ? TAB_BLUE : blocked ? TAB_DIM : TAB_INK,
    borderBottom: '2px solid ' + (active ? TAB_BLUE : 'transparent'),
  }
}

// ---------------- the pipeline dropdown (refs/round3/31) ----------------

export type PickerRow =
  | { kind: 'label'; text: string }
  | { kind: 'pipeline'; id: number; name: string; selected: boolean }
  | { kind: 'divider' }
  | { kind: 'new'; text: string }

/** `POST /api/pipelines` is auth.STAFF — DISPATCHER and ADMIN. */
export const canCreatePipeline = (role: string) => role === 'ADMIN' || role === 'DISPATCHER'

/**
 * The open list, top to bottom. `pipelines` is the list the board already reads
 * from `GET /api/pipelines`, which the server filters by per-pipeline access — so
 * a pipeline the user cannot open is never offered here either.
 */
export function pickerRows(
  pipelines: { id: number; name: string }[],
  selectedId: number | null | undefined,
  role: string,
): PickerRow[] {
  const rows: PickerRow[] = [{ kind: 'label', text: 'All pipelines' }]
  for (const p of pipelines) {
    rows.push({ kind: 'pipeline', id: p.id, name: p.name, selected: p.id === selectedId })
  }
  if (canCreatePipeline(role)) {
    rows.push({ kind: 'divider' }, { kind: 'new', text: 'New pipeline' })
  }
  return rows
}

/** Only pipelines and "+ New pipeline" can be highlighted or chosen. */
export const choosable = (row: PickerRow | undefined) =>
  row != null && (row.kind === 'pipeline' || row.kind === 'new')

/**
 * The row an arrow key lands on. -1 = nothing highlighted yet: Down goes to the
 * first choosable row, Up to the last. Stops at either end rather than wrapping,
 * and never lands on the label or the divider.
 */
export function moveHighlight(rows: PickerRow[], from: number, delta: 1 | -1): number {
  let i = from < 0 ? (delta === 1 ? -1 : rows.length) : from
  for (;;) {
    i += delta
    if (i < 0 || i >= rows.length) return from
    if (choosable(rows[i])) return i
  }
}

/** Where the highlight starts when the list opens: on the selected pipeline. */
export const initialHighlight = (rows: PickerRow[]) =>
  rows.findIndex((r) => r.kind === 'pipeline' && r.selected)
