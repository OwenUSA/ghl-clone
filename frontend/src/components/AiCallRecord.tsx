/**
 * What an AI agent did on a call, shown under the call it answered (2026-09-22).
 *
 * This exists because of what the supervised phase actually is. An agent answers a
 * customer on the company's behalf and the only person who can judge whether that went
 * well is a dispatcher reading it afterwards — so the three things they need are the ones
 * drawn here: WHO answered, HOW it ended, and WHAT the agent wrote down. The transcript
 * is next to it already; this is the summary above the words.
 *
 * Deliberately NOT a form. What the agent captured is a record of what was said, not a
 * customer record — it stays exactly as heard until a person decides to make a contact out
 * of it. Nothing in this component writes anything.
 */
import type { AiCall } from '../lib/api'

const TEXT = 'rgb(52,64,84)'
const MUTED = 'rgb(102,112,133)'
const LINE = 'rgb(234,236,240)'

/** The order a dispatcher reads them in: who, where, what, how urgent, then the rest. */
const FIELD_ORDER = ['name', 'phone', 'address', 'intent', 'urgency', 'notes']

const FIELD_LABEL: Record<string, string> = {
  name: 'Name',
  phone: 'Phone',
  address: 'Address',
  intent: 'What they want',
  urgency: 'Urgency',
  notes: 'Notes',
}

/**
 * How the call ended, in words a person uses. The raw port names are owen-main's
 * vocabulary and mean nothing to a dispatcher.
 *
 * `failed` is the one worth spelling out: it is not the agent's fault and it is not a
 * hangup — it is the agent never getting to speak (at capacity, over the daily cost cap,
 * or the voice service unreachable), and the caller went to voicemail instead.
 */
const OUTCOME_LABEL: Record<string, string> = {
  end_call: 'Ended by the agent',
  transfer: 'Handed to a person',
  transferred: 'Handed to a person',
  default: 'Caller hung up',
  failed: 'Agent could not take it — sent to voicemail',
}

/** Emergency is the one an operator must not scroll past. */
function urgencyTone(value: string): string | undefined {
  return /emerg/i.test(value) ? 'rgb(180,35,24)' : undefined
}

export function AiCallRecord({ call }: { call: AiCall | null | undefined }) {
  if (!call) return null
  const agent = (call.agent ?? '').trim()
  const captured = call.captured ?? {}
  // Unknown keys are shown too, after the known ones: owen-main owns this vocabulary and a
  // field it starts sending should appear here rather than wait for a frontend release.
  const keys = [
    ...FIELD_ORDER.filter(k => (captured[k] ?? '').toString().trim()),
    ...Object.keys(captured).filter(
      k => !FIELD_ORDER.includes(k) && (captured[k] ?? '').toString().trim()),
  ]
  const outcome = call.outcome ? (OUTCOME_LABEL[call.outcome] ?? call.outcome) : ''
  if (!agent && !keys.length && !outcome) return null

  return (
    <div style={{ marginTop: 8, borderTop: `1px solid ${LINE}`, paddingTop: 8, fontSize: 13 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
        {agent && (
          <span
            title={call.version ? `Version ${call.version}` : undefined}
            style={{
              padding: '1px 6px', borderRadius: 10, fontSize: 11, whiteSpace: 'nowrap',
              color: 'rgb(83,56,158)', backgroundColor: 'rgb(244,243,255)',
            }}
          >
            Answered by AI: {agent}
          </span>
        )}
        {outcome && <span style={{ color: MUTED, fontSize: 12 }}>{outcome}</span>}
        {call.campaign && (
          <span style={{ color: MUTED, fontSize: 12 }}>· {call.campaign}</span>
        )}
      </div>

      {keys.length > 0 && (
        <div style={{ marginTop: 6 }}>
          <div style={{ color: MUTED, fontSize: 12, marginBottom: 2 }}>
            What the agent recorded
          </div>
          {keys.map(k => {
            const value = (captured[k] ?? '').toString()
            return (
              <div key={k} style={{ display: 'flex', gap: 6, lineHeight: '18px' }}>
                <span style={{ color: MUTED, minWidth: 104 }}>
                  {FIELD_LABEL[k] ?? k}
                </span>
                <span style={{ color: (k === 'urgency' ? urgencyTone(value) : null) ?? TEXT }}>
                  {value}
                </span>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
