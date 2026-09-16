/**
 * "Call a number" — the Conversations dialer (2026-09-14).
 *
 * A small modal in the Create pipeline modal's look (refs/opps/04: white, 8px radius,
 * 16px/600 title with a close cross, 13px/500 labels, 1px rgb(208,213,221) inputs,
 * Cancel + a blue primary in the footer). GoHighLevel's own dialer was never captured,
 * so the keypad's layout is OURS — a phone's 3×4 grid, digits over letters.
 *
 * Every control does something real, and Call is only live when a call can be placed:
 *   - the number field takes typing and paste and formats as you type (`lib/dialPad.ts`);
 *   - the keypad appends, backspace removes the last digit typed;
 *   - "Calling from …" is read-only and comes from the SERVER (`useOurLine`), not a
 *     constant — the line changed on 2026-09-16. It is the only line, and Quo can never
 *     place a call;
 *   - Call is disabled, with the reason as a sentence, while the browser phone is not
 *     Ready or the number cannot be called; a refusal from the server is shown the same
 *     way and places nothing.
 *
 * Once the server has placed the call, owen-main rings THIS browser first. The dialer
 * waits for that leg, then closes: the in-call window (`InCallWindow.tsx`) takes over.
 */
import { useEffect, useRef, useState } from 'react'
import { ApiError, dialNumber } from '../lib/api'
import { useCallLauncher } from '../lib/callLauncher'
import { phoneNotReadyReason } from '../lib/callWindow'
import {
  KEYPAD, backspace, dialProblem, formatDialInput, normaliseNumber,
  pasteNumber, pressKey,
} from '../lib/dialPad'
import { OUTBOUND_INTENT_TTL_MS, outboundIntentPending } from '../lib/outboundIntent'
import { formatPhone } from '../lib/phone'
import { useOurLine } from '../lib/useOurLine'
import { IconBackspace, IconClose, IconPhone } from './Icon'

const INK = 'rgb(16,24,40)'
const TEXT = 'rgb(52,64,84)'
const MUTED = 'rgb(102,112,133)'
const LINE = 'rgb(208,213,221)'
const SOFT_LINE = 'rgb(234,236,240)'
const BLUE = 'rgb(21,94,239)'
const RED = 'rgb(180,35,24)'
const AMBER = 'rgb(181,71,8)'

type Placing =
  | { kind: 'idle' }
  | { kind: 'sending' }
  | { kind: 'waiting'; number: string }
  | { kind: 'refused'; reason: string }

export function CallNumberDialog({ onClose }: { onClose: () => void }) {
  const { launch, status, phase, error } = useCallLauncher()
  const [value, setValue] = useState('')
  const [touched, setTouched] = useState(false)
  const [placing, setPlacing] = useState<Placing>({ kind: 'idle' })
  const inputRef = useRef<HTMLInputElement | null>(null)

  // The line this CRM calls from, as configured on the server. Shown below, and the
  // number the "cannot call itself" refusal follows.
  const line = useOurLine()
  const numberProblem = dialProblem(value, line)
  const phoneProblem = phoneNotReadyReason(status, phase)
  const busy = placing.kind === 'sending' || placing.kind === 'waiting'
  const blocked = phoneProblem ?? numberProblem
  const canCall = !blocked && !busy

  // Our own leg connected: the in-call window has it from here.
  useEffect(() => {
    if (placing.kind === 'waiting' && phase === 'in-call') onClose()
  }, [placing, phase, onClose])

  // Our own leg arrived but could not be answered (almost always the microphone). The
  // hook put the reason in `error`; say it here, where the operator is looking.
  const sawLeg = useRef(false)
  useEffect(() => {
    if (placing.kind !== 'waiting') { sawLeg.current = false; return }
    if (phase === 'connecting') sawLeg.current = true
    else if (phase === 'idle' && sawLeg.current) {
      sawLeg.current = false
      setPlacing({ kind: 'refused', reason: error ?? 'The call could not be connected.' })
    }
  }, [placing, phase, error])

  // No leg within the intent's TTL: the phone system never rang this browser. Say so
  // rather than leave "Calling…" on screen forever.
  useEffect(() => {
    if (placing.kind !== 'waiting') return
    const t = setTimeout(() => {
      if (!outboundIntentPending()) {
        setPlacing({ kind: 'refused', reason:
          'The phone system did not ring this browser within 45 seconds, so nothing was '
          + 'connected. Check the status dot, top right, then try again.' })
      }
    }, OUTBOUND_INTENT_TTL_MS + 500)
    return () => clearTimeout(t)
  }, [placing])

  const edit = (next: string) => {
    setValue(next)
    setTouched(true)
    if (placing.kind === 'refused') setPlacing({ kind: 'idle' })
  }

  const call = async () => {
    if (!canCall) return
    const number = normaliseNumber(value, line) as string
    setPlacing({ kind: 'sending' })
    try {
      const r = await launch({ number }, (ringBrowser) => dialNumber(number, ringBrowser))
      if (!r.placed) setPlacing({ kind: 'refused', reason: r.reason })
      else setPlacing({ kind: 'waiting', number: r.number ?? number })
    } catch (err) {
      setPlacing({ kind: 'refused', reason: err instanceof ApiError
        ? err.message : 'The call could not be placed.' })
    }
  }

  // What the line under the field says, most important first.
  const hint: { text: string; tone: 'muted' | 'amber' | 'red' } | null =
    placing.kind === 'refused' ? { text: placing.reason, tone: 'red' }
      : placing.kind === 'waiting'
        ? { text: phase === 'connecting'
          ? `Connecting your browser to ${formatPhone(placing.number)}…`
          : `Calling ${formatPhone(placing.number)} — the phone system is ringing this browser…`,
        tone: 'muted' }
        : phoneProblem ? { text: phoneProblem, tone: 'amber' }
          : numberProblem ? { text: numberProblem, tone: touched && value ? 'amber' : 'muted' }
            : null

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center"
      style={{ backgroundColor: 'rgba(52,64,84,0.6)' }} onClick={busy ? undefined : onClose}>
      <div role="dialog" aria-modal="true" aria-label="Call a number"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => { if (e.key === 'Escape' && !busy) onClose() }}
        className="flex flex-col bg-white"
        style={{ width: 340, maxWidth: 'calc(100vw - 32px)', borderRadius: 8,
          boxShadow: '0 20px 24px -4px rgba(16,24,40,0.08)' }}>
        <div className="flex items-center" style={{ padding: '18px 16px 10px' }}>
          <div style={{ fontSize: 16, fontWeight: 600, color: INK }}>Call a number</div>
          <button onClick={onClose} aria-label="Close" className="ml-auto" style={{ padding: 4 }}
            disabled={busy}>
            <IconClose size={18} color={TEXT} />
          </button>
        </div>

        <div style={{ padding: '4px 16px 16px' }}>
          <label htmlFor="dial-number" style={{ fontSize: 13, fontWeight: 500, color: TEXT }}>
            Phone number
          </label>
          <div className="flex items-center" style={{ marginTop: 6, height: 40, borderRadius: 6,
            border: `1px solid ${touched && value && numberProblem ? 'rgb(253,162,155)' : LINE}`,
            boxShadow: '0 1px 2px rgba(16,24,40,0.05)' }}>
            <input id="dial-number" ref={inputRef} autoFocus value={value}
              inputMode="tel" autoComplete="off" placeholder="(941) 555-0123"
              aria-invalid={Boolean(touched && value && numberProblem)}
              aria-describedby="dial-hint"
              disabled={busy}
              onChange={(e) => edit(formatDialInput(e.target.value))}
              onPaste={(e) => {
                e.preventDefault()
                edit(pasteNumber(e.clipboardData.getData('text')))
              }}
              onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); void call() } }}
              className="min-w-0 flex-1 outline-none"
              style={{ height: 38, padding: '0 10px', fontSize: 18, fontWeight: 500, color: INK,
                letterSpacing: 0.3, background: 'transparent' }} />
            <button type="button" aria-label="Delete last digit" title="Delete last digit"
              disabled={busy || !value}
              onClick={() => { edit(backspace(value)); inputRef.current?.focus() }}
              className="flex items-center justify-center"
              style={{ width: 36, height: 36, opacity: value ? 1 : 0.4 }}>
              <IconBackspace size={20} color={TEXT} />
            </button>
          </div>
          <div id="dial-hint" role="status" data-tone={hint?.tone}
            style={{ minHeight: 18, fontSize: 12, marginTop: 4,
              color: hint?.tone === 'red' ? RED : hint?.tone === 'amber' ? AMBER : MUTED }}>
            {hint?.text}
          </div>

          <div role="group" aria-label="Keypad" style={{ display: 'grid',
            gridTemplateColumns: 'repeat(3, 1fr)', gap: 8, marginTop: 10 }}>
            {KEYPAD.map((k) => (
              <button key={k.key} type="button" aria-label={`Dial ${k.key}`} disabled={busy}
                onClick={() => { edit(pressKey(value, k.key)); inputRef.current?.focus() }}
                className="flex flex-col items-center justify-center"
                style={{ height: 48, borderRadius: 6, border: `1px solid ${SOFT_LINE}`,
                  backgroundColor: 'rgb(249,250,251)', color: INK }}>
                <span style={{ fontSize: 18, fontWeight: 500, lineHeight: 1 }}>{k.key}</span>
                <span style={{ fontSize: 9, letterSpacing: 1, color: MUTED, minHeight: 11,
                  marginTop: 2 }}>{k.letters}</span>
              </button>
            ))}
          </div>

          <div className="flex items-center" style={{ marginTop: 14, padding: '10px 12px',
            borderRadius: 8, border: `1px solid ${SOFT_LINE}`, fontSize: 13, color: TEXT }}>
            <span style={{ color: MUTED }}>Calling from</span>
            <span data-testid="calling-from" style={{ marginLeft: 'auto', fontWeight: 500, color: INK }}>
              {formatPhone(line)}
            </span>
          </div>
        </div>

        <div className="flex items-center justify-end gap-2"
          style={{ padding: '12px 16px', borderTop: `1px solid ${SOFT_LINE}` }}>
          <button onClick={onClose} disabled={busy}
            style={{ height: 36, padding: '0 14px', borderRadius: 6, fontSize: 14, fontWeight: 600,
              color: TEXT, backgroundColor: '#fff', border: `1px solid ${LINE}` }}>
            Cancel
          </button>
          <button onClick={() => void call()} disabled={!canCall}
            title={blocked ?? `Call ${formatPhone(normaliseNumber(value, line))}`}
            className="flex items-center gap-2"
            style={{ height: 36, padding: '0 16px', borderRadius: 6, fontSize: 14, fontWeight: 600,
              color: '#fff', backgroundColor: BLUE, opacity: canCall ? 1 : 0.5,
              cursor: canCall ? 'pointer' : 'not-allowed' }}>
            <IconPhone size={16} color="#fff" />
            {placing.kind === 'sending' ? 'Calling…' : placing.kind === 'waiting' ? 'Connecting…' : 'Call'}
          </button>
        </div>
      </div>
    </div>
  )
}
