import { LEAD_OUTCOMES } from '../../lib/zuper'
import { INPUT, Label, Select } from './ui'

/**
 * "Lead outcome" (Zuper v2, 2026-09-16) — CRM-only: why a lead closed without booking.
 * Drawn by the opportunity modal and Add opportunity when the status is Lost or Abandoned
 * (or an outcome is already set). Required on an unbooked card; "Other" needs the note.
 * The rule itself is `leadOutcomeProblem` in lib/zuper.ts, the same sentence the server gives.
 */
export function LeadOutcomeFields({ outcome, note, required, disabled = false, title,
  onOutcome, onNote }: {
  outcome: string
  note: string
  required: boolean
  disabled?: boolean
  title?: string
  onOutcome: (v: string) => void
  onNote: (v: string) => void
}) {
  return (
    <div data-field="lead-outcome" style={{ marginBottom: 16, gridColumn: 'span 2' }}>
      <div className="grid grid-cols-2 gap-x-3">
        <div>
          <Label required={required}>Lead outcome</Label>
          <Select value={outcome} ariaLabel="Lead outcome" disabled={disabled} title={title}
            onChange={onOutcome}>
            <option value="">Choose an outcome</option>
            {LEAD_OUTCOMES.map((o) => <option key={o} value={o}>{o}</option>)}
          </Select>
        </div>
        {(outcome === 'Other' || note) && (
          <div>
            <Label required={outcome === 'Other'}>Outcome note</Label>
            <textarea value={note} aria-label="Lead outcome note" disabled={disabled}
              placeholder={outcome === 'Other' ? 'Say what happened' : 'Optional'}
              maxLength={500} rows={2}
              onChange={(e) => onNote(e.target.value)}
              style={{ ...INPUT, height: 'auto', minHeight: 36, padding: '8px 12px',
                resize: 'vertical' }} />
          </div>
        )}
      </div>
    </div>
  )
}
