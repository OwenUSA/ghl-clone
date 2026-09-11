/**
 * The softphone's whole user interface: a registration dock, an incoming-call card and
 * an in-call bar.
 *
 * OURS, not measured. GHL's own softphone was never captured on the live account, so
 * there is nothing to compare this against and it must not be cited as parity —
 * recorded in DECISIONS.md, 2026-09-11.
 *
 * Mounted ONCE in App.tsx, not per page. A call rings the browser, not a screen: the
 * card has to appear whether the user is looking at Contacts, Conversations or a report.
 *
 * ## The dock exists because of a specific failure
 *
 * This browser is one leg of a ring group whose other legs are two mobile phones. If the
 * browser silently stops being registered, the call still rings the mobiles and nobody
 * finds out the desk was dead — possibly for weeks. So the dock is always on screen while
 * the phone is meant to be on, it states the registration in plain words, and every word
 * it says comes from a SIP.js registration callback rather than from "we asked it to
 * register and nothing threw".
 *
 * ## Answering does not cancel the mobiles from here
 *
 * It does not need to. The telephony project's ring group hangs up every other leg before
 * it bridges the winner — first answer wins is already implemented, tested and live. This
 * component answers its own leg and nothing else.
 */
import { useEffect, useState } from 'react'
import { useSoftphoneContext } from '../lib/softphoneContext'
import { formatPhone, lookupCaller, type CallerContact } from '../lib/softphoneApi'
import type { SoftphoneStatus } from '../lib/softphone'

const TEXT = 'rgb(31,41,55)'
const MUTED = 'rgb(102,112,133)'
const LINE = 'rgb(234,236,240)'
const GREEN = 'rgb(23,124,73)'
const AMBER = 'rgb(181,102,10)'
const RED = 'rgb(180,35,24)'
const GREY = 'rgb(152,162,179)'

/** One sentence per state, written for somebody deciding whether to trust their desk. */
const SAYS: Record<SoftphoneStatus, { dot: string; label: string; hint: string }> = {
  unavailable: { dot: GREY, label: '', hint: '' },
  'no-operator': {
    dot: GREY,
    label: 'No softphone',
    hint: 'Your account is not set up to take calls in the browser.',
  },
  off: {
    dot: GREY,
    label: 'Phone off',
    hint: 'Calls ring the mobiles only.',
  },
  connecting: { dot: AMBER, label: 'Connecting…', hint: 'Not taking calls yet.' },
  ready: {
    dot: GREEN,
    label: 'Ready for calls',
    hint: 'This browser rings alongside the mobiles.',
  },
  reconnecting: {
    dot: AMBER,
    label: 'Reconnecting…',
    hint: 'NOT taking calls right now.',
  },
  elsewhere: {
    dot: GREY,
    label: 'Open in another tab',
    hint: 'Only one tab can be the phone.',
  },
  failed: { dot: RED, label: 'Not registered', hint: 'This browser cannot take calls.' },
}

function Dot({ color }: { color: string }) {
  return (
    <span
      aria-hidden
      style={{
        width: 8,
        height: 8,
        borderRadius: 999,
        backgroundColor: color,
        display: 'inline-block',
        flexShrink: 0,
      }}
    />
  )
}

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
function Elapsed({ since }: { since: number }) {
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
function useCaller(number: string | null, active: boolean) {
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
  const { state, setOnline, answer, decline, hangup, toggleMute } = useSoftphoneContext()
  const ringing = state.phase === 'ringing'
  const contact = useCaller(state.peer, ringing || state.phase === 'in-call')

  // A deployment with no phone system configured shows nothing at all. Every user of
  // every other deployment would otherwise carry a permanent grey badge explaining the
  // absence of a feature they never asked about.
  if (state.status === 'unavailable') return null

  const says = SAYS[state.status]
  const who = contact?.name || formatPhone(state.peer) || 'Unknown caller'
  const alsoNumber = contact?.name ? formatPhone(state.peer) : null

  return (
    <>
      {ringing && (
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
      )}

      <div
        aria-label="Softphone"
        style={{
          position: 'fixed',
          right: 16,
          bottom: 16,
          zIndex: 80,
          width: 288,
          background: '#fff',
          border: `1px solid ${LINE}`,
          borderRadius: 12,
          boxShadow: '0 12px 16px -4px rgba(16,24,40,0.12)',
          padding: 12,
          fontSize: 13,
          color: TEXT,
        }}
      >
        {state.phase === 'in-call' ? (
          <>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <Dot color={GREEN} />
              <span style={{ fontWeight: 600, flex: 1, minWidth: 0 }}>
                <span
                  style={{
                    display: 'block',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {who}
                </span>
              </span>
              {state.answeredAt && (
                <span style={{ color: MUTED, fontVariantNumeric: 'tabular-nums' }}>
                  <Elapsed since={state.answeredAt} />
                </span>
              )}
            </div>
            <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
              <Action onClick={toggleMute}>{state.muted ? 'Unmute' : 'Mute'}</Action>
              <Action tone="stop" onClick={() => void hangup()}>
                Hang up
              </Action>
            </div>
          </>
        ) : (
          <>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <Dot color={says.dot} />
              <span style={{ fontWeight: 600, flex: 1 }}>{says.label}</span>
              {state.status === 'ready' && (
                <button
                  onClick={() => setOnline(false)}
                  style={{
                    border: 'none',
                    background: 'transparent',
                    color: MUTED,
                    fontSize: 12,
                    cursor: 'pointer',
                    padding: 0,
                  }}
                >
                  Switch off
                </button>
              )}
            </div>
            <div style={{ color: MUTED, marginTop: 4, fontSize: 12 }}>{says.hint}</div>
            {state.error && (
              <div style={{ color: RED, marginTop: 6, fontSize: 12 }}>{state.error}</div>
            )}
            {state.status === 'ready' && state.operator && (
              <div style={{ color: MUTED, marginTop: 6, fontSize: 11 }}>
                Registered as {state.operator}
              </div>
            )}
            {(state.status === 'off' || state.status === 'failed') && (
              <div style={{ marginTop: 10 }}>
                <Action tone="go" onClick={() => setOnline(true)}>
                  {state.status === 'failed' ? 'Try again' : 'Switch on'}
                </Action>
              </div>
            )}
            {state.status === 'elsewhere' && (
              <div style={{ marginTop: 10 }}>
                <Action onClick={() => setOnline(true)}>Use this tab</Action>
              </div>
            )}
          </>
        )}
      </div>
    </>
  )
}
