import {
  askedOn,
  inputValue,
  isEmptyAnswer,
  optionsFor,
  submitValue,
  type FieldDef,
} from '../lib/customFields'

/**
 * The job questions, answered.
 *
 * One component, used by BOTH the opportunity modal and the Add opportunity
 * dialog, so the two cannot end up asking different questions or validating them
 * differently. It is deliberately NOT used on the board card: the board is already
 * dense and has to stay scannable.
 *
 * It draws live controls and NOTHING else. Until 2026-09-13 it also drew a
 * "Recorded earlier" block and a read-only "From OWEN" block; the owner asked for
 * both to go — the `owen_*` and `workiz_*` keys are his old account's fields and he
 * does not want them on screen. Their VALUES are untouched on every deal: the
 * modal sends only the answers that changed (`changedAnswers`, which skips every
 * reserved key), and `merge_answers` on the server keeps every reserved key and
 * every answer to a field this form is not showing.
 *
 * There is no `required` marker anywhere, because no field can be required: an
 * inbound call at 2am has to become a deal regardless.
 */

const LABEL = { fontSize: 14, fontWeight: 500, color: 'rgb(52,64,84)' } as const
const INPUT: React.CSSProperties = {
  width: '100%',
  height: 36,
  fontSize: 14,
  color: 'rgb(16,24,40)',
  borderRadius: 6,
  border: '1px solid rgb(208,213,221)',
  padding: '0 12px',
  marginTop: 8,
  backgroundColor: '#fff',
}
const READONLY: React.CSSProperties = { ...INPUT, backgroundColor: 'rgb(249,250,251)' }
// A thin chevron instead of the system arrow, matching the modal's other selects.
const SELECT: React.CSSProperties = {
  appearance: 'none', paddingRight: 34,
  backgroundImage: "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' "
    + "width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='%23667085' "
    + "stroke-width='1.8' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpath "
    + "d='m6 9 6 6 6-6'/%3E%3C/svg%3E\")",
  backgroundRepeat: 'no-repeat', backgroundPosition: 'right 12px center',
}
const HEADING = {
  fontSize: 14, fontWeight: 500, color: 'rgb(16,24,40)', marginBottom: 8,
} as const

export function CustomFieldAnswers({
  defs,
  pipelineId,
  answers,
  onChange,
  fields,
  hideEmpty = false,
  heading = 'Job questions',
  columns = 1,
  disabled = false,
  disabledReason,
}: {
  defs: FieldDef[]
  pipelineId: number | null | undefined
  answers: Record<string, unknown>
  /** Called with the WHOLE object. The caller posts it back as one blob. */
  onChange: (next: Record<string, unknown>) => void
  /** Draw exactly these (one modal tab's fields). Default: every field asked. */
  fields?: FieldDef[]
  /** The modal's "Hide empty fields" checkbox. */
  hideEmpty?: boolean
  heading?: string | null
  /** The modal lays its fields out two to a row, as GoHighLevel does. */
  columns?: 1 | 2
  disabled?: boolean
  disabledReason?: string
  /** Retired 2026-09-13 with the kept-answers block it switched. Still
      accepted so the Add dialog's call compiles; it draws nothing either way. */
  showKept?: boolean
}) {
  const asked = (fields ?? askedOn(defs, pipelineId))
    .filter((def) => !hideEmpty || !isEmptyAnswer(answers[def.key]))

  const set = (key: string, value: unknown) => onChange({ ...answers, [key]: value })

  if (!asked.length) return null

  return (
    <div className="mb-4">
      {heading && <div style={HEADING}>{heading}</div>}
      <div className={columns === 2 ? 'grid grid-cols-2 gap-x-3' : undefined}>
        {asked.map((def) => (
          <div key={def.key} style={{ marginBottom: 16 }}
            data-field-key={def.key}>
            <div style={LABEL}>{def.label}</div>
            {def.field_type === 'dropdown' ? (
              <select
                value={inputValue(def, answers[def.key])}
                disabled={disabled}
                title={disabled ? disabledReason : undefined}
                onChange={(e) => set(def.key, submitValue(def, e.target.value))}
                style={{ ...(disabled ? READONLY : INPUT), ...SELECT }}
              >
                {/* Every field is optional, so "no answer" is always offered. */}
                <option value="">--</option>
                {optionsFor(def, answers[def.key]).map((o) => (
                  <option key={o} value={o}>{o}</option>
                ))}
              </select>
            ) : def.field_type === 'boolean' ? (
              <select
                value={inputValue(def, answers[def.key])}
                disabled={disabled}
                title={disabled ? disabledReason : undefined}
                onChange={(e) => set(def.key, submitValue(def, e.target.value))}
                style={{ ...(disabled ? READONLY : INPUT), ...SELECT }}
              >
                <option value="">--</option>
                <option value="true">Yes</option>
                <option value="false">No</option>
              </select>
            ) : (
              <input
                type={def.field_type === 'date' ? 'date'
                  : def.field_type === 'number' ? 'number' : 'text'}
                value={inputValue(def, answers[def.key])}
                disabled={disabled}
                title={disabled ? disabledReason : undefined}
                placeholder={def.field_type === 'text' ? 'Enter ' + def.label.toLowerCase()
                  : undefined}
                onChange={(e) => set(def.key, submitValue(def, e.target.value))}
                style={disabled ? READONLY : INPUT}
              />
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
