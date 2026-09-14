import {
  askedOn,
  detailsKey,
  inputValue,
  isEmptyAnswer,
  optionsFor,
  showsDetails,
  spansRow,
  submitValue,
  type FieldDef,
} from '../lib/customFields'
import { ADDRESS_FIELDS, type AddressForm } from '../lib/opportunityAddress'

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
 *
 * The call Checklist (2026-09-14) added four things, each a setting the owner edits
 * in Settings → Custom Fields rather than anything hard-coded here:
 *   * a PARAGRAPH answer, drawn as a textarea;
 *   * a SCRIPT under any question — what the dispatcher says;
 *   * a yes/no that shows a REAL value beside its tick: the contact's email or the
 *     card's address, edited in place. Those inputs are the modal's own Primary
 *     email / Address state, passed in as `linked`, so they save through the contact
 *     PATCH and the detail PATCH with those endpoints' validation and role rules.
 *     Nothing is copied into the answers;
 *   * a DETAILS box under a dropdown, open only while the chosen option calls for it,
 *     answered under `<key>__details`. Choosing another option hides it and keeps
 *     what was typed — nothing is ever cleared by a click that did not ask to.
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
const SCRIPT: React.CSSProperties = {
  fontSize: 13, lineHeight: '18px', color: 'rgb(102,112,133)', marginTop: 4,
  whiteSpace: 'pre-wrap',
}
const TICK: React.CSSProperties = {
  display: 'flex', alignItems: 'center', gap: 8, height: 36, marginTop: 8,
  padding: '0 12px', borderRadius: 6, border: '1px solid rgb(208,213,221)',
  fontSize: 14, color: 'rgb(52,64,84)', whiteSpace: 'nowrap', backgroundColor: '#fff',
}

/** The real values a linked yes/no draws beside its tick. */
export type LinkedValues = {
  email: { value: string; onChange: (v: string) => void; disabled: boolean
    /** Why the email cannot be typed yet (no contact chosen), shown under it. */
    hint?: string | null }
  address: { value: AddressForm; onChange: (v: AddressForm) => void; disabled: boolean }
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
  linked,
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
  /** The contact's email and the card's address, for a linked yes/no. Without it a
      linked question is drawn as its tick alone. */
  linked?: LinkedValues
  /** Retired 2026-09-13 with the kept-answers block it switched. Still
      accepted so older calls compile; it draws nothing either way. */
  showKept?: boolean
}) {
  const asked = (fields ?? askedOn(defs, pipelineId))
    .filter((def) => !hideEmpty || !isEmptyAnswer(answers[def.key]))

  const set = (key: string, value: unknown) => onChange({ ...answers, [key]: value })
  const title = disabled ? disabledReason : undefined

  if (!asked.length) return null

  const select = (def: FieldDef, children: React.ReactNode) => (
    <select
      value={inputValue(def, answers[def.key])}
      disabled={disabled}
      title={title}
      aria-label={def.label}
      onChange={(e) => set(def.key, submitValue(def, e.target.value))}
      style={{ ...(disabled ? READONLY : INPUT), ...SELECT }}
    >
      {/* Every field is optional, so "no answer" is always offered. */}
      <option value="">--</option>
      {children}
    </select>
  )

  /** The tick of a linked yes/no: ticked = Yes, unticked = no answer yet. */
  const tick = (def: FieldDef) => (
    <label style={{ ...TICK, ...(disabled ? { backgroundColor: 'rgb(249,250,251)' } : {}) }}
      title={title}>
      <input type="checkbox" checked={answers[def.key] === true} disabled={disabled}
        aria-label={def.label}
        onChange={(e) => set(def.key, e.target.checked ? true : '')}
        style={{ width: 16, height: 16, accentColor: 'rgb(21,94,239)' }} />
      Verified
    </label>
  )

  const control = (def: FieldDef) => {
    if (def.field_type === 'boolean' && def.linked_field === 'contact_email') {
      return (
        <>
          <div className="flex items-start gap-3">
            <div className="min-w-0 flex-1">
              {linked && (
                <input value={linked.email.value} aria-label={def.label + ': email'}
                  placeholder="Enter email" type="email"
                  disabled={disabled || linked.email.disabled} title={title}
                  onChange={(e) => linked.email.onChange(e.target.value)}
                  style={disabled || linked.email.disabled ? READONLY : INPUT} />
              )}
            </div>
            {tick(def)}
          </div>
          {linked?.email.hint && <div style={SCRIPT}>{linked.email.hint}</div>}
        </>
      )
    }
    if (def.field_type === 'boolean' && def.linked_field === 'opportunity_address') {
      const off = disabled || !linked || linked.address.disabled
      return (
        <>
          <div className="flex items-start gap-3">
            <div className="min-w-0 flex-1">
              {linked && (
                <input value={linked.address.value.address_street}
                  aria-label={def.label + ': Street address'} placeholder="Street address"
                  maxLength={255} disabled={off} title={title}
                  onChange={(e) => linked.address.onChange(
                    { ...linked.address.value, address_street: e.target.value })}
                  style={off ? READONLY : INPUT} />
              )}
            </div>
            {tick(def)}
          </div>
          {linked && (
            <div className="grid gap-x-3" style={{ gridTemplateColumns: '2fr 1fr 1fr' }}>
              {ADDRESS_FIELDS.filter(([k]) => k !== 'address_street').map(
                ([k, label, , max]) => (
                  <input key={k} value={linked.address.value[k]} maxLength={max}
                    aria-label={def.label + ': ' + label} placeholder={label}
                    disabled={off} title={title}
                    onChange={(e) => linked.address.onChange(
                      { ...linked.address.value, [k]: e.target.value })}
                    style={off ? READONLY : INPUT} />
                ))}
            </div>
          )}
        </>
      )
    }
    if (def.field_type === 'dropdown') {
      return (
        <>
          {select(def, optionsFor(def, answers[def.key]).map((o) => (
            <option key={o} value={o}>{o}</option>
          )))}
          {showsDetails(def, answers[def.key]) && (
            <div data-details-for={def.key} style={{ marginTop: 12 }}>
              <div style={{ ...LABEL, fontSize: 13 }}>Details</div>
              <textarea
                value={inputValue({ ...def, field_type: 'text' }, answers[detailsKey(def.key)])}
                disabled={disabled}
                title={title}
                aria-label={def.label + ': details'}
                placeholder="Enter details"
                rows={2}
                maxLength={2000}
                onChange={(e) => set(detailsKey(def.key), e.target.value)}
                style={{ ...(disabled ? READONLY : INPUT), height: 'auto', padding: '8px 12px' }}
              />
            </div>
          )}
        </>
      )
    }
    if (def.field_type === 'boolean') {
      return select(def, <>
        <option value="true">Yes</option>
        <option value="false">No</option>
      </>)
    }
    if (def.field_type === 'paragraph') {
      return (
        <textarea
          value={inputValue(def, answers[def.key])}
          disabled={disabled}
          title={title}
          aria-label={def.label}
          placeholder={'Enter ' + def.label.toLowerCase()}
          rows={4}
          maxLength={5000}
          onChange={(e) => set(def.key, submitValue(def, e.target.value))}
          style={{ ...(disabled ? READONLY : INPUT), height: 'auto', padding: '8px 12px',
            resize: 'vertical' }}
        />
      )
    }
    return (
      <input
        type={def.field_type === 'date' ? 'date'
          : def.field_type === 'number' ? 'number' : 'text'}
        value={inputValue(def, answers[def.key])}
        disabled={disabled}
        title={title}
        aria-label={def.label}
        placeholder={def.field_type === 'text' ? 'Enter ' + def.label.toLowerCase()
          : undefined}
        onChange={(e) => set(def.key, submitValue(def, e.target.value))}
        style={disabled ? READONLY : INPUT}
      />
    )
  }

  return (
    <div className="mb-4">
      {heading && <div style={HEADING}>{heading}</div>}
      <div className={columns === 2 ? 'grid grid-cols-2 gap-x-3' : undefined}>
        {asked.map((def) => (
          <div key={def.key} data-field-key={def.key}
            style={{ marginBottom: 16,
              gridColumn: columns === 2 && spansRow(def) ? 'span 2' : undefined }}>
            <div style={LABEL}>{def.label}</div>
            {/* The script: what to say. Under the question, above the answer. */}
            {def.script?.trim() && (
              <div style={SCRIPT} data-script>{def.script}</div>
            )}
            {control(def)}
          </div>
        ))}
      </div>
    </div>
  )
}
