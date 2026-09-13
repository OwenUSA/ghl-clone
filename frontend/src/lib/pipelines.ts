/**
 * The Pipelines tab's logic, with no React in it.
 *
 * IMPORT-FREE on purpose, like `calendarGrid.ts` and `customFields.ts`:
 * `backend/tests/test_pipelines_ui.py` runs this exact file under node, so the
 * search, the paging, the drag reorder, "Move to position", the deep link, the
 * modal's validation and the board's colour modes are asserted by executing the
 * shipped code rather than by reading it.
 */

export type ColorMode = 'none' | 'dot' | 'tint'

export type StageRow = {
  id: number
  name: string
  position: number
  count: number
  value_cents: number
  color: string | null
  probability: number | null
  show_in_funnel: boolean
  show_in_pie: boolean
}

export type PipelineRow = {
  id: number
  name: string
  position: number
  color_mode: ColorMode
  use_opportunity_probability: boolean
  updated_at: string | null
  stages: StageRow[]
}

/** Mirrors STAGE_PALETTE in backend/app/main.py — a new stage's colour by position. */
export const STAGE_PALETTE = [
  '#2E90FA', '#12B76A', '#F79009', '#7A5AF8', '#F04438',
  '#06AED4', '#EE46BC', '#667085', '#EAAA08', '#15B79E',
]

export const defaultStageColor = (index: number) =>
  STAGE_PALETTE[((index % STAGE_PALETTE.length) + STAGE_PALETTE.length) % STAGE_PALETTE.length]

/**
 * GoHighLevel's Create pipeline modal opens with these four rows. They are a
 * starting point the user edits before anything is sent — the API itself still
 * creates a pipeline with no stages unless it is given some.
 */
export const STARTER_STAGES: { name: string; probability: number }[] = [
  { name: 'New Lead', probability: 20 },
  { name: 'Contacted', probability: 40 },
  { name: 'Proposal Sent', probability: 60 },
  { name: 'Closed', probability: 80 },
]

// ---------------------------------------------------------------- the board

/** `#RRGGBB` at `alpha`, as the rgba() a Background tint header is painted with. */
export function tint(hex: string, alpha: number): string {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim())
  if (!m) return 'transparent'
  const n = parseInt(m[1], 16)
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${alpha})`
}

/**
 * How a stage column header is drawn under the pipeline's display colour mode.
 *
 * `dot` is the colour of the dot before the name, or null for none; `background`
 * is the header's fill. A stage with no stored colour (every stage created before
 * stages had one) borrows the palette colour for its position, so switching a
 * pipeline to Colored dot never draws a column without one.
 */
export function stageHeader(
  mode: ColorMode | undefined, color: string | null | undefined, index: number,
): { dot: string | null; background: string; border: string } {
  const c = color || defaultStageColor(index)
  if (mode === 'dot') return { dot: c, background: '#fff', border: 'rgb(234,236,240)' }
  if (mode === 'tint') return { dot: null, background: tint(c, 0.12), border: tint(c, 0.35) }
  return { dot: null, background: '#fff', border: 'rgb(234,236,240)' }
}

// ---------------------------------------------------------------- the table

/** The search box: case-insensitive, on the name, trimmed. */
export function searchPipelines<T extends { name: string }>(rows: T[], q: string): T[] {
  const term = q.trim().toLowerCase()
  return term ? rows.filter((r) => r.name.toLowerCase().includes(term)) : rows
}

export type PageSlice<T> = {
  rows: T[]
  /** 1-based, inclusive; 0 when there is nothing to show. */
  from: number
  to: number
  total: number
  page: number
  pages: number
}

/** Rows per page and a page number, clamped so a shrinking list never shows an empty page. */
export function paginate<T>(rows: T[], page: number, perPage: number): PageSlice<T> {
  const size = Math.max(1, Math.floor(perPage))
  const pages = Math.max(1, Math.ceil(rows.length / size))
  const current = Math.min(Math.max(1, Math.floor(page)), pages)
  const start = (current - 1) * size
  const slice = rows.slice(start, start + size)
  return {
    rows: slice,
    from: slice.length ? start + 1 : 0,
    to: start + slice.length,
    total: rows.length,
    page: current,
    pages,
  }
}

/** One item moved from index `from` to index `to`; everything else keeps its order. */
export function moveItem<T>(items: T[], from: number, to: number): T[] {
  if (from < 0 || from >= items.length) return items.slice()
  const next = items.slice()
  const [it] = next.splice(from, 1)
  next.splice(Math.min(Math.max(0, to), next.length), 0, it)
  return next
}

/**
 * The full order after dragging `activeId` onto `overId`, or null when nothing
 * changes — so a drop back where it started sends no request at all.
 */
export function orderAfterDrag(ids: number[], activeId: number, overId: number | null): number[] | null {
  if (overId == null || activeId === overId) return null
  const from = ids.indexOf(activeId)
  const to = ids.indexOf(overId)
  if (from < 0 || to < 0) return null
  return moveItem(ids, from, to)
}

/**
 * "Move to position": the full order with `id` at 1-based `position`. Out-of-range
 * positions clamp to the ends. Null when it is already there.
 */
export function orderForPosition(ids: number[], id: number, position: number): number[] | null {
  const from = ids.indexOf(id)
  if (from < 0 || !Number.isFinite(position)) return null
  const to = Math.min(Math.max(1, Math.floor(position)), ids.length) - 1
  if (to === from) return null
  return moveItem(ids, from, to)
}

/** "Jul 14, 2026" and "2:46 PM" — the two halves of the Updated on column. */
export function updatedOn(iso: string | null): { date: string; time: string } | null {
  if (!iso) return null
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return null
  return {
    date: d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }),
    time: d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' }),
  }
}

// ---------------------------------------------------------------- copy link

/** The URL "Copy link" puts on the clipboard: the board, opened on this pipeline. */
export const pipelineLink = (origin: string, pipelineId: number) =>
  `${origin.replace(/\/+$/, '')}/opportunities?pipeline=${pipelineId}`

/** The pipeline a deep link names, or null. Anything that is not a positive id is ignored. */
export function pipelineFromSearch(search: string): number | null {
  const raw = new URLSearchParams(search).get('pipeline')
  if (!raw || !/^\d+$/.test(raw)) return null
  const n = Number(raw)
  return n > 0 ? n : null
}

// ---------------------------------------------------------------- the modal

export type StageDraft = {
  /** The stored stage, or null for a row added in the modal. */
  id: number | null
  /** Stable React key; ids do not exist yet for new rows. */
  key: string
  name: string
  color: string
  /** As typed. '' means no probability. */
  probability: string
  show_in_funnel: boolean
  show_in_pie: boolean
  /** Deals in the stored stage — decides whether deleting it needs a destination. */
  count: number
}

export type PipelineDraft = {
  name: string
  use_opportunity_probability: boolean
  color_mode: ColorMode
  stages: StageDraft[]
  /** Stored stage id -> the kept stage id its deals move to. */
  moves: Record<number, number>
}

let seq = 0
const newKey = () => `new-${++seq}`

export function blankStage(index: number, name = '', probability = ''): StageDraft {
  return {
    id: null, key: newKey(), name, color: defaultStageColor(index),
    probability, show_in_funnel: true, show_in_pie: true, count: 0,
  }
}

/** The Create modal: GoHighLevel's four starter rows. */
export function newDraft(): PipelineDraft {
  return {
    name: '',
    use_opportunity_probability: false,
    color_mode: 'none',
    stages: STARTER_STAGES.map((s, i) => blankStage(i, s.name, String(s.probability))),
    moves: {},
  }
}

/** The Edit modal, populated from the stored pipeline. */
export function draftFrom(p: PipelineRow): PipelineDraft {
  return {
    name: p.name,
    use_opportunity_probability: p.use_opportunity_probability,
    color_mode: p.color_mode ?? 'none',
    stages: [...p.stages]
      .sort((a, b) => a.position - b.position || a.id - b.id)
      .map((s, i) => ({
        id: s.id,
        key: `stage-${s.id}`,
        name: s.name,
        color: s.color || defaultStageColor(i),
        probability: s.probability == null ? '' : String(s.probability),
        show_in_funnel: s.show_in_funnel,
        show_in_pie: s.show_in_pie,
        count: s.count,
      })),
    moves: {},
  }
}

export type DraftProblems = {
  name: string | null
  stages: Record<string, string>
  general: string | null
}

/**
 * Why Create/Update is disabled, field by field. Nothing is a problem → `ok`.
 *
 * The uniqueness check runs against the names the browser can see; the server
 * checks against every pipeline and has the last word (409).
 *
 * STAGE names are deliberately NOT checked for uniqueness: the measured pipeline
 * holds two distinct "Call Back" stages and the `ghl` CLI exits 5 rather than
 * guess between them. Only the PIPELINE name must be unique.
 */
export function validateDraft(
  d: PipelineDraft, otherNames: string[],
): { ok: boolean; problems: DraftProblems } {
  const problems: DraftProblems = { name: null, stages: {}, general: null }
  const name = d.name.trim()
  if (!name) problems.name = 'Pipeline name is required'
  else if (otherNames.some((n) => n.trim().toLowerCase() === name.toLowerCase())) {
    problems.name = 'A pipeline with this name already exists'
  } else if (name.length > 160) problems.name = 'Keep the name under 160 characters'

  if (d.stages.length === 0) problems.general = 'A pipeline needs at least one stage'
  for (const s of d.stages) {
    if (!s.name.trim()) problems.stages[s.key] = 'Stage name is required'
    else if (s.probability.trim() !== '') {
      const t = s.probability.trim()
      if (!/^\d+$/.test(t) || Number(t) > 100) {
        problems.stages[s.key] = 'Probability is a whole number from 0 to 100'
      }
    }
  }
  const ok = !problems.name && !problems.general && Object.keys(problems.stages).length === 0
  return { ok, problems }
}

/** Stored stages the draft no longer lists — what Update would delete. */
export function removedStages(p: PipelineRow | null, d: PipelineDraft): StageRow[] {
  if (!p) return []
  const kept = new Set(d.stages.map((s) => s.id).filter((id): id is number => id != null))
  return p.stages.filter((s) => !kept.has(s.id))
}

/** Remove a row. A stored stage holding deals needs `moveTo`, a kept stored stage. */
export function removeStage(d: PipelineDraft, key: string, moveTo?: number): PipelineDraft {
  const row = d.stages.find((s) => s.key === key)
  if (!row) return d
  const moves = { ...d.moves }
  // A stage that was itself a destination can no longer receive anything.
  if (row.id != null) {
    for (const [from, to] of Object.entries(moves)) if (to === row.id) delete moves[Number(from)]
    if (row.count > 0 && moveTo != null) moves[row.id] = moveTo
  }
  return { ...d, stages: d.stages.filter((s) => s.key !== key), moves }
}

const stageBody = (s: StageDraft) => ({
  ...(s.id != null ? { id: s.id } : {}),
  name: s.name.trim(),
  color: s.color,
  probability: s.probability.trim() === '' ? null : Number(s.probability.trim()),
  show_in_funnel: s.show_in_funnel,
  show_in_pie: s.show_in_pie,
})

/** POST /api/pipelines */
export function createBody(d: PipelineDraft) {
  return {
    name: d.name.trim(),
    use_opportunity_probability: d.use_opportunity_probability,
    color_mode: d.color_mode,
    stages: d.stages.map((s) => ({ ...stageBody(s), id: undefined })),
  }
}

/** PATCH /api/pipelines/{id} — the whole list, in order, plus where removed deals go. */
export function updateBody(d: PipelineDraft) {
  return {
    name: d.name.trim(),
    use_opportunity_probability: d.use_opportunity_probability,
    color_mode: d.color_mode,
    stages: d.stages.map(stageBody),
    stage_moves: d.moves,
  }
}
