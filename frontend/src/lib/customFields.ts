/**
 * Which job questions a deal is asked, and how one answer is drawn.
 *
 * Import-free on purpose, like calendarGrid.ts and reminders.ts, so
 * backend/tests/test_custom_fields_ui.py can run it under node and assert real
 * behaviour instead of grepping the source. That matters more here than usual:
 * the whole feature turns on a field being HIDDEN without its answer being lost,
 * and "hidden" is a decision this file makes.
 *
 * The server is still the thing that enforces all of it — `merge_answers` in
 * backend/app/custom_fields.py keeps an answer whatever a client sends. Nothing
 * here is a gate; it decides what to draw.
 */

export type FieldType = 'text' | 'number' | 'dropdown' | 'date' | 'boolean' | 'paragraph'

/** What a yes/no question may show beside its tick (2026-09-14). */
export type LinkedField = 'contact_email' | 'opportunity_address'

export type FieldDef = {
  id: number
  key: string
  label: string
  field_type: FieldType
  options: string[]
  position: number
  entity: string
  archived: boolean
  pipeline_ids: number[]
  /** The modal tab it is drawn under; null = Opportunity details. Optional only
      so fixtures written before groups existed still type-check. */
  group_id?: number | null
  /** The call Checklist's settings (2026-09-14). Optional so older fixtures
      type-check; the server always sends all three. */
  script?: string | null
  /** yes/no only: the REAL value drawn and edited beside the tick. */
  linked_field?: LinkedField | null
  /** dropdown only: the choices that open a details box. */
  details_when?: string[]
}

/** The details box's answer is stored beside the main one. Mirrors DETAILS_SUFFIX
    in backend/app/custom_fields.py; no derived key can contain "__". */
export const DETAILS_SUFFIX = '__details'

export function detailsKey(key: string): string {
  return key + DETAILS_SUFFIX
}

/** Is the details box open for this answer? Only when the CHOSEN option is one the
    field says calls for details — never for a blank, never for another option. */
export function showsDetails(def: FieldDef, value: unknown): boolean {
  if (def.field_type !== 'dropdown' || !def.details_when?.length) return false
  return typeof value === 'string' && def.details_when.includes(value)
}

/** The tab whose progress the board card shows. Mirrors CHECKLIST_GROUP. */
export const CHECKLIST_GROUP = 'Checklist'

export function isChecklistGroup(name: string): boolean {
  return name.trim().toLowerCase() === CHECKLIST_GROUP.toLowerCase()
}

/**
 * "N / M answered" for one tab. M is the questions this pipeline asks in it (the
 * caller passes exactly those — `modalSections` already dropped the rest), N the
 * ones holding an answer. `false` is an answer; a details box is not a question.
 * The server's `custom_fields.progress` counts the same way for the card badge.
 */
export function answeredCount(fields: FieldDef[], answers: Record<string, unknown>):
  { answered: number; total: number } {
  return {
    answered: fields.filter((d) => !isEmptyAnswer(answers[d.key])).length,
    total: fields.length,
  }
}

/** A question drawn across both columns: long text, a script, a linked value or a
    details box would be cramped into half the modal. */
export function spansRow(def: FieldDef): boolean {
  return def.field_type === 'paragraph' || !!def.script?.trim() || !!def.linked_field
    || !!def.details_when?.length
}

/**
 * Namespaces this app does not own: `owen_*` (the telephony project), `workiz_*`
 * (the Workiz import) and `ahs_job_*` (the AHS email relay, 2026-09-14). The values
 * stay on every deal untouched — `owen_call_id` is a live join key, `workiz_id` the
 * import's idempotency key and `ahs_job_id` the relay's — but the owner does not
 * want them on the screen (2026-09-13), so nothing in the modal renders one — except
 * `workiz_tech`, read-only, by the owner's later decision (see `workizTechnicians`).
 * Mirrors `RESERVED_PREFIXES` in backend/app/custom_fields.py.
 */
export const RESERVED_PREFIXES = ['owen_', 'workiz_', 'ahs_job_'] as const
export const JOIN_KEY = 'owen_call_id'

export function isReserved(key: string): boolean {
  return RESERVED_PREFIXES.some((p) => key.startsWith(p))
}

/**
 * The questions asked on a deal in this pipeline, in display order.
 *
 * Attachment is explicit: a field attached to no pipeline is asked nowhere. An
 * empty list never means "everywhere", so detaching the last pipeline cannot turn
 * a narrow question into a global one by accident. A reserved key is never asked,
 * even if a definition somehow claimed one — the server refuses to create it.
 */
export function askedOn(defs: FieldDef[], pipelineId: number | null | undefined):
  FieldDef[] {
  if (pipelineId == null) return []
  return defs
    .filter((d) => !d.archived && d.pipeline_ids.includes(pipelineId))
    .filter((d) => !isReserved(d.key))
    .sort((a, b) => a.position - b.position || a.id - b.id)
}

export type GroupLike = { id: number; name: string; position: number }

/**
 * The modal's left nav, as data: the fields with no group (drawn under
 * Opportunity details) and one entry per group, IN GROUP ORDER, each holding the
 * fields of that group this pipeline asks.
 *
 * Every group appears whatever the pipeline, because a group applies to all of
 * them; one whose fields are all on other pipelines is an empty tab, not a
 * missing one. A field pointing at a group that no longer exists falls back to
 * Opportunity details rather than vanishing — the same place the server puts it.
 */
export function modalSections(
  defs: FieldDef[], groups: GroupLike[], pipelineId: number | null | undefined,
): { ungrouped: FieldDef[]; groups: { group: GroupLike; fields: FieldDef[] }[] } {
  const asked = askedOn(defs, pipelineId)
  const known = new Set(groups.map((g) => g.id))
  const ordered = [...groups].sort((a, b) => a.position - b.position || a.id - b.id)
  return {
    ungrouped: asked.filter((d) => d.group_id == null || !known.has(d.group_id)),
    groups: ordered.map((group) => ({
      group, fields: asked.filter((d) => d.group_id === group.id),
    })),
  }
}

/** Is an answer blank, for "Hide empty fields"? `false` is an answer; `''` is not. */
export function isEmptyAnswer(value: unknown): boolean {
  return value === undefined || value === null
    || (typeof value === 'string' && value.trim() === '')
}

/**
 * The answers an Update should send: only the ones that CHANGED, and never a
 * reserved key. The modal holds the whole blob so its controls can read it, but
 * posting that blob back would put `owen_*` and `workiz_*` values on the wire on
 * every save for no reason. The server keeps them either way; this keeps the
 * request honest about what the user did. A blank where nothing was stored is not
 * a change.
 */
export function changedAnswers(
  stored: Record<string, unknown>, current: Record<string, unknown>,
): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(current)) {
    if (isReserved(key)) continue
    if (value === stored[key]) continue
    if (isEmptyAnswer(value) && isEmptyAnswer(stored[key])) continue
    out[key] = value
  }
  return out
}

/** What goes in the input for one field. Booleans become the select's values. */
export function inputValue(def: FieldDef, value: unknown): string {
  if (value === undefined || value === null) return ''
  if (def.field_type === 'boolean') {
    return value === true ? 'true' : value === false ? 'false' : ''
  }
  return String(value)
}

/**
 * What the input hands back, ready to post.
 *
 * An empty control is `''` and NOT null: the server reads both as "unanswered",
 * and `''` keeps the object shape the form rendered, which is what makes
 * clearing an answer an explicit act rather than an omission.
 */
export function submitValue(def: FieldDef, raw: string): string | number | boolean {
  if (raw === '') return ''
  if (def.field_type === 'boolean') return raw === 'true'
  if (def.field_type === 'number') {
    // Left as the typed string when it is not a number, so the SERVER refuses it
    // with its own sentence naming the field. Silently dropping it here would
    // save the deal and lose what the user typed without saying anything.
    const n = Number(raw)
    return raw.trim() !== '' && Number.isFinite(n) ? n : raw
  }
  return raw
}

/** A stored answer as a person reads it. Used for the read-only rows. */
export function displayValue(def: FieldDef | null, value: unknown): string {
  if (value === null || value === undefined || value === '') return '--'
  if (def?.field_type === 'boolean' || typeof value === 'boolean') {
    return value ? 'Yes' : 'No'
  }
  return String(value)
}

/**
 * A dropdown's options, with the stored answer added if it is no longer offered.
 *
 * Precedent: the appointment panel keeps an unlisted status as an option so
 * opening a booking cannot silently rewrite it. Same reasoning — retiring "Tile"
 * must not turn every deal holding it into a deal holding "Shingle" the first
 * time somebody opens it.
 */
export function optionsFor(def: FieldDef, value: unknown): string[] {
  const stored = value === null || value === undefined ? '' : String(value)
  if (stored && !def.options.includes(stored)) return [...def.options, stored]
  return def.options
}

/** "Inspection — Tue 9:00am", the way a deal names the visit booked for it. */
export function describeBooking(
  title: string, startsAt: string, locale = 'en-US',
): string {
  const at = new Date(startsAt)
  if (Number.isNaN(at.getTime())) return title
  const when = at.toLocaleString(locale, {
    weekday: 'short', hour: 'numeric', minute: '2-digit',
  })
  return `${title} — ${when}`
}

/**
 * The answers a CREATE should send (the Add new opportunity modal, 2026-09-14): only
 * the questions the chosen pipeline asks, their details boxes, and nothing blank.
 *
 * The dispatcher can answer a question and then switch the Pipeline to one that does
 * not ask it. The server refuses an answer to a question the pipeline does not ask —
 * rightly — so posting the whole form would turn a pipeline change into a refused
 * Create with the caller still on the phone. What is dropped here was never saved.
 */
export function answersFor(
  defs: FieldDef[], pipelineId: number | null | undefined, answers: Record<string, unknown>,
): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const def of askedOn(defs, pipelineId)) {
    for (const key of [def.key, ...(def.field_type === 'dropdown' ? [detailsKey(def.key)] : [])]) {
      if (!isEmptyAnswer(answers[key])) out[key] = answers[key]
    }
  }
  return out
}

/**
 * The one reserved key the modal DOES draw (the owner, 2026-09-15): `workiz_tech`, the
 * Workiz job's technician names, written by the import as a list in the job's order.
 * Shown read-only as "Workiz technician(s)"; '' when the card has none, so "Hide empty
 * fields" hides it. Nothing sends it back — `changedAnswers` skips every reserved key,
 * and the server refuses a change to it.
 */
export const WORKIZ_TECH_KEY = 'workiz_tech'

export function workizTechnicians(customFields: Record<string, unknown> | null | undefined): string {
  const raw = customFields?.[WORKIZ_TECH_KEY]
  const names = Array.isArray(raw) ? raw : typeof raw === 'string' ? [raw] : []
  return names
    .filter((n): n is string => typeof n === 'string')
    .map((n) => n.split(/\s+/).filter(Boolean).join(' '))
    .filter(Boolean)
    .join(', ')
}
