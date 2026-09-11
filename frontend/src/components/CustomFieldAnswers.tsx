import {
  askedOn,
  displayValue,
  inputValue,
  keptButNotAsked,
  optionsFor,
  submitValue,
  telephonyAnswers,
  type FieldDef,
} from '../lib/customFields'

/**
 * The job questions, answered.
 *
 * One component, used by BOTH the opportunity detail form and the Add
 * opportunity dialog, so the two cannot end up asking different questions or
 * validating them differently. It is deliberately NOT used on the board card:
 * the board is already dense and has to stay scannable.
 *
 * Three blocks, in this order, and each one exists for a reason:
 *
 *   1. **Asked** — the live controls for the fields attached to this deal's
 *      pipeline, in the admin's display order.
 *   2. **Kept, not asked** — answers this deal holds for a field the pipeline
 *      does not ask, or one that has been archived. Shown READ-ONLY rather than
 *      dropped: an answer nobody can see is an answer nobody knows they have,
 *      and this is the visible half of "moving a deal never discards anything".
 *   3. **From OWEN** — the `owen_*` keys the telephony project writes. Visible,
 *      read-only, and unreachable from the custom-fields UI. `owen_call_id` is
 *      the live join key; the backend refuses a change to it too.
 *
 * Every control is a plain input of the same shape the rest of the form uses.
 * There is no `required` marker anywhere, because no field can be required: an
 * inbound call at 2am has to become a deal regardless.
 */

const LABEL = { fontSize: 14, color: 'rgb(16,24,40)' } as const
const INPUT: React.CSSProperties = {
  width: '100%',
  height: 36,
  fontSize: 15,
  color: 'rgb(16,24,40)',
  borderRadius: 6,
  border: '1px solid rgb(234,236,240)',
  padding: '0 10px',
  marginTop: 6,
  backgroundColor: '#fff',
}
const READONLY: React.CSSProperties = { ...INPUT, backgroundColor: 'rgb(249,250,251)' }
const HEADING = {
  fontSize: 14, fontWeight: 500, color: 'rgb(16,24,40)', marginBottom: 8,
} as const
const NOTE = { fontSize: 12, color: 'rgb(102,112,133)' } as const

export function CustomFieldAnswers({
  defs,
  pipelineId,
  answers,
  onChange,
  disabled = false,
  disabledReason,
  showKept = true,
}: {
  defs: FieldDef[]
  pipelineId: number | null | undefined
  answers: Record<string, unknown>
  /** Called with the WHOLE object. The caller posts it back as one blob. */
  onChange: (next: Record<string, unknown>) => void
  disabled?: boolean
  disabledReason?: string
  /** The Add dialog has no answers to keep yet, so it hides those two blocks. */
  showKept?: boolean
}) {
  const asked = askedOn(defs, pipelineId)
  const kept = showKept ? keptButNotAsked(defs, answers, pipelineId) : []
  const owen = showKept ? telephonyAnswers(answers) : []

  const set = (key: string, value: unknown) => onChange({ ...answers, [key]: value })

  if (!asked.length && !kept.length && !owen.length) return null

  return (
    <div className="mb-4">
      {asked.length > 0 && (
        <>
          <div style={HEADING}>Job questions</div>
          {asked.map((def) => (
            <div key={def.key} style={{ marginBottom: 12 }}>
              <div style={LABEL}>{def.label}</div>
              {def.field_type === 'dropdown' ? (
                <select
                  value={inputValue(def, answers[def.key])}
                  disabled={disabled}
                  title={disabled ? disabledReason : undefined}
                  onChange={(e) => set(def.key, submitValue(def, e.target.value))}
                  style={disabled ? READONLY : INPUT}
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
                  style={disabled ? READONLY : INPUT}
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
                  onChange={(e) => set(def.key, submitValue(def, e.target.value))}
                  style={disabled ? READONLY : INPUT}
                />
              )}
            </div>
          ))}
        </>
      )}

      {kept.length > 0 && (
        <div style={{ marginTop: 16 }}>
          <div style={HEADING}>Recorded earlier</div>
          <div style={{ ...NOTE, marginBottom: 8 }}>
            Kept on this deal and not editable here. Nothing is ever deleted: put
            the field back on this pipeline, or un-archive it, and the answer
            becomes editable again.
          </div>
          {kept.map((row) => (
            <div key={row.key} style={{ marginBottom: 12 }}>
              <div style={LABEL}>{row.def?.label ?? row.key}</div>
              <input
                readOnly
                value={displayValue(row.def, row.value)}
                title={row.why}
                style={READONLY}
              />
              <div style={NOTE}>{row.why}</div>
            </div>
          ))}
        </div>
      )}

      {owen.length > 0 && (
        <div style={{ marginTop: 16 }}>
          <div style={HEADING}>From OWEN</div>
          {owen.map(([key, value]) => (
            <div key={key} style={{ marginBottom: 12 }}>
              <div style={LABEL}>{key}</div>
              {/* READ-ONLY, all four of them. They are written by the telephony
                  project; owen_call_id is the join key and the backend refuses a
                  change to it. The other three were editable here until now,
                  which meant one careless save could rewrite call attribution
                  for records this form knows nothing about. */}
              <input
                readOnly
                value={displayValue(null, value)}
                title={key === 'owen_call_id'
                  ? 'The join key to the telephony project — not editable here'
                  : 'Written by the telephony project — not editable here'}
                style={READONLY}
              />
            </div>
          ))}
          <div style={NOTE}>
            owen_* fields are written by the telephony project; owen_call_id is the
            join key. They cannot be defined, renamed or archived as custom fields.
          </div>
        </div>
      )}
    </div>
  )
}
