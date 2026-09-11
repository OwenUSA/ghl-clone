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

export type FieldType = 'text' | 'number' | 'dropdown' | 'date' | 'boolean'

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
}

/** The telephony project's namespace. Never editable, never archivable, never ours. */
export const RESERVED_PREFIX = 'owen_'
export const JOIN_KEY = 'owen_call_id'

export function isReserved(key: string): boolean {
  return key.startsWith(RESERVED_PREFIX)
}

/**
 * The questions asked on a deal in this pipeline, in display order.
 *
 * Attachment is explicit: a field attached to no pipeline is asked nowhere. An
 * empty list never means "everywhere", so detaching the last pipeline cannot turn
 * a narrow question into a global one by accident.
 */
export function askedOn(defs: FieldDef[], pipelineId: number | null | undefined):
  FieldDef[] {
  if (pipelineId == null) return []
  return defs
    .filter((d) => !d.archived && d.pipeline_ids.includes(pipelineId))
    .sort((a, b) => a.position - b.position || a.id - b.id)
}

/**
 * Answers the deal holds that the form is NOT going to draw as a live control:
 * a field this pipeline does not ask, and an archived one.
 *
 * They are rendered read-only rather than dropped, because an answer nobody can
 * see is an answer nobody knows they still have. `owen_*` is excluded — it has
 * its own section and its own rules.
 */
export function keptButNotAsked(
  defs: FieldDef[],
  answers: Record<string, unknown>,
  pipelineId: number | null | undefined,
): { def: FieldDef | null; key: string; value: unknown; why: string }[] {
  const asked = new Set(askedOn(defs, pipelineId).map((d) => d.key))
  const byKey = new Map(defs.map((d) => [d.key, d]))
  return Object.keys(answers)
    .filter((key) => !asked.has(key) && !isReserved(key))
    .filter((key) => answers[key] !== null && answers[key] !== '')
    .map((key) => {
      const def = byKey.get(key) ?? null
      return {
        def,
        key,
        value: answers[key],
        why: def == null
          ? 'Recorded against no field definition'
          : def.archived
            ? 'This field is archived'
            : 'Not asked on this pipeline',
      }
    })
    .sort((a, b) => a.key.localeCompare(b.key))
}

/** The `owen_*` answers, in a stable order, for the read-only telephony block. */
export function telephonyAnswers(answers: Record<string, unknown>):
  [string, unknown][] {
  return Object.keys(answers).filter(isReserved).sort()
    .map((key) => [key, answers[key]])
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
