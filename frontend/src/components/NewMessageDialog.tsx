/**
 * "New message" — text any number from the Conversations page (2026-09-15).
 *
 * The sibling of "Call a number" (`CallNumberDialog.tsx`) and the same modal family: the
 * Create pipeline modal's look (refs/opps/04 — white, 8px radius, 16px/600 title with a
 * close cross, 13px/500 labels, 1px rgb(208,213,221) inputs, Cancel + a blue primary).
 * GoHighLevel's compose screen was never captured, so the layout is OURS.
 *
 * Every control does something real, and Send is live only when a text can be sent:
 *   - the number field takes typing and paste and formats as you type (`lib/dialPad.ts`),
 *     with the reason it cannot be texted under it;
 *   - the message box counts characters and carrier segments (`lib/smsSegments.ts`);
 *   - "Sending from (954) 482-9099" is read-only: it is the only line. Quo (OpenPhone) is
 *     never a sender, so it is never offered;
 *   - a refusal the SERVER gives before writing anything (a restricted technician and a
 *     number that is not one of their customers) is shown here, and the dialog stays.
 *
 * Once the text is recorded — queued, or refused / failed by the phone system, which the
 * thread shows under the bubble — the page opens that thread: the contact's conversation
 * when a contact holds the number, else the number's own thread (never a new contact).
 */
import { useRef, useState } from 'react'
import { ApiError, sendNewMessage, type NewMessageResult } from '../lib/api'
import { isRestricted, type AccessUser } from '../lib/access'
import {
  CALLING_FROM, formatDialInput, normaliseTextNumber, pasteNumber, textProblem,
} from '../lib/dialPad'
import { formatPhone } from '../lib/phone'
import { segmentInfo, segmentLabel } from '../lib/smsSegments'
import { IconChat, IconClose } from './Icon'

const INK = 'rgb(16,24,40)'
const TEXT = 'rgb(52,64,84)'
const MUTED = 'rgb(102,112,133)'
const LINE = 'rgb(208,213,221)'
const SOFT_LINE = 'rgb(234,236,240)'
const BLUE = 'rgb(21,94,239)'
const RED = 'rgb(180,35,24)'
const AMBER = 'rgb(181,71,8)'

/** The longest text the server accepts (`NewMessageIn.body`), about ten segments. */
export const MAX_BODY = 1600

export function NewMessageDialog({ user, onClose, onSent }: {
  user: AccessUser
  onClose: () => void
  onSent: (r: NewMessageResult) => void
}) {
  const [number, setNumber] = useState('')
  const [touched, setTouched] = useState(false)
  const [body, setBody] = useState('')
  const [sending, setSending] = useState(false)
  const [refused, setRefused] = useState<string | null>(null)
  const messageRef = useRef<HTMLTextAreaElement | null>(null)

  const numberProblem = textProblem(number)
  const bodyProblem = body.trim() ? null : 'Type the message to send.'
  const blocked = numberProblem ?? bodyProblem
  const canSend = !blocked && !sending
  const segments = segmentInfo(body)

  const editNumber = (next: string) => {
    setNumber(next)
    setTouched(true)
    setRefused(null)
  }

  const submit = async () => {
    if (!canSend) return
    const to = normaliseTextNumber(number) as string
    setSending(true)
    setRefused(null)
    try {
      const r = await sendNewMessage(to, body)
      if (!r.recorded) setRefused(r.reason)
      else onSent(r)
    } catch (err) {
      setRefused(err instanceof ApiError ? err.message : 'The message could not be sent.')
    } finally {
      setSending(false)
    }
  }

  const hint: { text: string; tone: 'muted' | 'amber' | 'red' } | null =
    refused ? { text: refused, tone: 'red' }
      : numberProblem ? { text: numberProblem, tone: touched && number ? 'amber' : 'muted' }
        : isRestricted(user)
          ? { text: 'With “Only assigned data” on you can text only the customers on your own jobs.',
            tone: 'muted' }
          : null

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center"
      style={{ backgroundColor: 'rgba(52,64,84,0.6)' }} onClick={sending ? undefined : onClose}>
      <div role="dialog" aria-modal="true" aria-label="New message"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => { if (e.key === 'Escape' && !sending) onClose() }}
        className="flex flex-col bg-white"
        style={{ width: 420, maxWidth: 'calc(100vw - 32px)', borderRadius: 8,
          boxShadow: '0 20px 24px -4px rgba(16,24,40,0.08)' }}>
        <div className="flex items-center" style={{ padding: '18px 16px 10px' }}>
          <div style={{ fontSize: 16, fontWeight: 600, color: INK }}>New message</div>
          <button onClick={onClose} aria-label="Close" className="ml-auto" style={{ padding: 4 }}
            disabled={sending}>
            <IconClose size={18} color={TEXT} />
          </button>
        </div>

        <div style={{ padding: '4px 16px 16px' }}>
          <label htmlFor="new-message-number" style={{ fontSize: 13, fontWeight: 500, color: TEXT }}>
            To <span style={{ color: RED }}>*</span>
          </label>
          <input id="new-message-number" autoFocus value={number}
            inputMode="tel" autoComplete="off" placeholder="(941) 555-0123"
            aria-invalid={Boolean(touched && number && numberProblem)}
            aria-describedby="new-message-hint"
            disabled={sending}
            onChange={(e) => editNumber(formatDialInput(e.target.value))}
            onPaste={(e) => {
              e.preventDefault()
              editNumber(pasteNumber(e.clipboardData.getData('text')))
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter') { e.preventDefault(); messageRef.current?.focus() }
            }}
            className="w-full outline-none"
            style={{ marginTop: 6, height: 40, padding: '0 12px', borderRadius: 6, fontSize: 15,
              fontWeight: 500, color: INK, letterSpacing: 0.2,
              border: `1px solid ${touched && number && numberProblem ? 'rgb(253,162,155)' : LINE}`,
              boxShadow: '0 1px 2px rgba(16,24,40,0.05)' }} />
          <div id="new-message-hint" role="status" data-tone={hint?.tone}
            style={{ minHeight: 18, fontSize: 12, marginTop: 4,
              color: hint?.tone === 'red' ? RED : hint?.tone === 'amber' ? AMBER : MUTED }}>
            {hint?.text}
          </div>

          <label htmlFor="new-message-body"
            style={{ display: 'block', marginTop: 8, fontSize: 13, fontWeight: 500, color: TEXT }}>
            Message <span style={{ color: RED }}>*</span>
          </label>
          <textarea id="new-message-body" ref={messageRef} value={body} rows={5}
            maxLength={MAX_BODY} disabled={sending} placeholder="Type a message"
            onChange={(e) => { setBody(e.target.value); setRefused(null) }}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); void submit() }
            }}
            className="w-full outline-none"
            style={{ marginTop: 6, padding: '8px 12px', borderRadius: 6, fontSize: 14, color: INK,
              resize: 'vertical', border: `1px solid ${LINE}`, lineHeight: '20px',
              boxShadow: '0 1px 2px rgba(16,24,40,0.05)' }} />
          <div className="flex items-center" style={{ marginTop: 4, fontSize: 12, color: MUTED }}>
            <span data-testid="segment-count"
              title={segments.segments > 1
                ? 'Carriers split a long text into segments and join them back up on the phone.'
                : undefined}>
              {segmentLabel(body)}
            </span>
            <span className="ml-auto">{body.length}/{MAX_BODY}</span>
          </div>

          <div className="flex items-center" style={{ marginTop: 14, padding: '10px 12px',
            borderRadius: 8, border: `1px solid ${SOFT_LINE}`, fontSize: 13, color: TEXT }}>
            <span style={{ color: MUTED }}>Sending from</span>
            <span data-testid="sending-from" style={{ marginLeft: 'auto', fontWeight: 500, color: INK }}>
              {formatPhone(CALLING_FROM)}
            </span>
          </div>
        </div>

        <div className="flex items-center justify-end gap-2"
          style={{ padding: '12px 16px', borderTop: `1px solid ${SOFT_LINE}` }}>
          <button onClick={onClose} disabled={sending}
            style={{ height: 36, padding: '0 14px', borderRadius: 6, fontSize: 14, fontWeight: 600,
              color: TEXT, backgroundColor: '#fff', border: `1px solid ${LINE}` }}>
            Cancel
          </button>
          <button onClick={() => void submit()} disabled={!canSend}
            title={blocked ?? `Text ${formatPhone(normaliseTextNumber(number))}`}
            className="flex items-center gap-2"
            style={{ height: 36, padding: '0 16px', borderRadius: 6, fontSize: 14, fontWeight: 600,
              color: '#fff', backgroundColor: BLUE, opacity: canSend ? 1 : 0.5,
              cursor: canSend ? 'pointer' : 'not-allowed' }}>
            <IconChat size={16} color="#fff" />
            {sending ? 'Sending…' : 'Send'}
          </button>
        </div>
      </div>
    </div>
  )
}
