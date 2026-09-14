/**
 * The softphone's incoming-call card.
 *
 * OURS, not measured. GHL's own softphone was never captured on the live account, so
 * there is nothing to compare this against and it must not be cited as parity —
 * recorded in DECISIONS.md, 2026-09-11.
 *
 * Mounted ONCE in App.tsx, not per page. A call rings the browser, not a screen: the
 * card has to appear whether the user is looking at Contacts, Conversations or a report.
 *
 * ## Where the registration state went (2026-09-14)
 *
 * This used to draw a floating bottom-right dock ("Ready for calls / Switch off") and the
 * in-call bar. The owner replaced both with a subtle top-right status dot on every page:
 * `components/StatusIndicator.tsx`. The registration state is still stated in plain words
 * and still comes only from a SIP.js registration callback — it is simply behind a hover
 * now, with the dot's colour always visible. Switch off / on and Mute / Hang up live in
 * that hover card, with exactly the behaviour they had here.
 *
 * ## Answering does not cancel the mobiles from here
 *
 * It does not need to. The telephony project's ring group hangs up every other leg before
 * it bridges the winner — first answer wins is already implemented, tested and live. This
 * component answers its own leg and nothing else.
 */
import { useEffect, useState } from 'react'
import { useSoftphoneContext } from '../lib/softphoneContext'
import { lookupCaller, type CallerContact } from '../lib/softphoneApi'
import { formatPhone } from '../lib/phone'

const TEXT = 'rgb(31,41,55)'
const MUTED = 'rgb(102,112,133)'
const LINE = 'rgb(234,236,240)'
const GREEN = 'rgb(23,124,73)'
const RED = 'rgb(180,35,24)'

function Action({
  onClick,
  children,
  tone = 'plain',
}: {
  onClick: () => void
  children: React.ReactNode
  tone?: 'plain' | 'go' | 'stop'
}) {
  const background = tone === 'go' ? GREEN : tone === 'stop' ? RED : '#fff'
  const color = tone === 'plain' ? TEXT : '#fff'
  return (
    <button
      onClick={onClick}
      style={{
        border: tone === 'plain' ? `1px solid ${LINE}` : 'none',
        background,
        color,
        borderRadius: 8,
        padding: '6px 12px',
        fontSize: 13,
        fontWeight: 500,
        cursor: 'pointer',
      }}
    >
      {children}
    </button>
  )
}

/** mm:ss since the call was answered. */
export function Elapsed({ since }: { since: number }) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [])
  const total = Math.max(0, Math.floor((now - since) / 1000))
  return (
    <span>
      {String(Math.floor(total / 60)).padStart(2, '0')}:{String(total % 60).padStart(2, '0')}
    </span>
  )
}

/**
 * Resolve the ringing number to a contact. Best-effort: the digits are shown immediately
 * and the name replaces them if one is found, so a slow or failed lookup can never delay
 * somebody answering the phone.
 */
export function useCaller(number: string | null, active: boolean) {
  const [contact, setContact] = useState<CallerContact | null>(null)
  useEffect(() => {
    if (!active || !number) {
      setContact(null)
      return
    }
    let cancelled = false
    lookupCaller(number)
      .then((r) => !cancelled && setContact(r.contact))
      .catch(() => !cancelled && setContact(null))
    return () => {
      cancelled = true
    }
  }, [number, active])
  return contact
}

export function Softphone() {
  const { state, answer, decline } = useSoftphoneContext()
  const ringing = state.phase === 'ringing'
  const contact = useCaller(state.peer, ringing)

  if (!ringing) return null

  const who = contact?.name || formatPhone(state.peer) || 'Unknown caller'
  const alsoNumber = contact?.name ? formatPhone(state.peer) : null

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 90,
        backgroundColor: 'rgba(16,24,40,0.5)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 16,
      }}
    >
      <div
        role="dialog"
        aria-label="Incoming call"
        style={{
          width: 420,
          maxWidth: '100%',
          background: '#fff',
          borderRadius: 12,
          boxShadow: '0 20px 24px -4px rgba(16,24,40,0.16)',
          padding: 24,
          textAlign: 'center',
        }}
      >
        <div
          style={{
            fontSize: 12,
            letterSpacing: 1,
            textTransform: 'uppercase',
            color: MUTED,
          }}
        >
          Incoming call
        </div>
        <div style={{ fontSize: 22, fontWeight: 600, color: TEXT, marginTop: 8 }}>
          {who}
        </div>
        {alsoNumber && (
          <div style={{ fontSize: 14, color: MUTED, marginTop: 2 }}>{alsoNumber}</div>
        )}
        {contact?.business_name && (
          <div style={{ fontSize: 13, color: MUTED, marginTop: 2 }}>
            {contact.business_name}
          </div>
        )}
        {!contact && (
          <div style={{ fontSize: 13, color: MUTED, marginTop: 6 }}>
            Not in your contacts
          </div>
        )}
        {state.dialed && (
          <div style={{ fontSize: 13, color: MUTED, marginTop: 10 }}>
            to {formatPhone(state.dialed) || state.dialed}
          </div>
        )}
        <div
          style={{
            display: 'flex',
            gap: 12,
            justifyContent: 'center',
            marginTop: 20,
          }}
        >
          <Action tone="stop" onClick={() => void decline()}>
            Decline
          </Action>
          <Action tone="go" onClick={() => void answer()}>
            Answer
          </Action>
        </div>
        <div style={{ fontSize: 12, color: MUTED, marginTop: 12 }}>
          Declining leaves the mobiles ringing.
        </div>
      </div>
    </div>
  )
}
