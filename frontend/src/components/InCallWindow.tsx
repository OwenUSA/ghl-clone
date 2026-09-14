/**
 * The in-call window — ONE for every call this browser is on (2026-09-14).
 *
 * A call placed from the Conversations dialer, from a thread's or a card's phone icon,
 * and an inbound call answered on the incoming-call card all end up here. Ported from
 * owen-main's `InCallModal.tsx`, in this CRM's look (the Opportunities card/dropdown:
 * white, 1px rgb(234,236,240), 8px radius, soft shadow) — GoHighLevel's own in-call UI was
 * never captured, so the layout is OURS.
 *
 * What it has, and why each is real:
 *   - who: the contact's name (from the call that was placed, else the caller lookup),
 *     else the formatted number; a running timer from the moment this leg connected;
 *   - a recording indicator — owen-main records every call it bridges on the bound DID;
 *   - Mute, Keypad (DTMF down the line, and the tone played locally), Hang up;
 *   - Audio: the microphone and speaker this browser uses, switched live.
 *
 * What it does NOT have: Hold and Transfer. Both act on an Asterisk channel through
 * owen-main's ARI control API (`/api/telephony/control/*`), which authenticates an
 * owen-main user login, not the CRM link key, and the CRM has no channel id for an
 * inbound call at all. Rendering them would be decoration — they are listed in
 * DECISIONS.md with exactly what owen-main would need to expose.
 *
 * After the call it stays, showing the duration and ONE next step: "Open conversation"
 * for a contact, "Add as contact" (the existing form, prefilled — it never saves by
 * itself) for a number nobody holds.
 *
 * Mounted ONCE in App.tsx, inside the SoftphoneProvider — never in a page.
 */
import { useEffect, useRef, useState } from 'react'
import { ApiError, openContactConversation } from '../lib/api'
import type { Me } from '../lib/auth'
import { canPickSpeaker, getAudioPref, setAudioPref, useAudioDevices } from '../lib/audioDevices'
import { calledTarget } from '../lib/callLauncher'
import { afterCallActions, callTitle, formatCallDuration } from '../lib/callWindow'
import { KEYPAD } from '../lib/dialPad'
import { playDtmfTone } from '../lib/dtmfTone'
import { canOpenRecords, openRecord } from '../lib/openRecord'
import { formatPhone } from '../lib/phone'
import { useSoftphoneContext } from '../lib/softphoneContext'
import { AddContactDialog } from './AddContactDialog'
import {
  IconClose, IconDialpad, IconMic, IconMicOff, IconPhoneOff, IconPlus, IconSettings,
} from './Icon'
import { canAddContact } from './NumberDetailsPanel'
import { useCaller } from './Softphone'

const INK = 'rgb(16,24,40)'
const TEXT = 'rgb(52,64,84)'
const MUTED = 'rgb(102,112,133)'
const LINE = 'rgb(234,236,240)'
const BORDER = 'rgb(208,213,221)'
const BLUE = 'rgb(21,94,239)'
const RED = 'rgb(217,45,32)'

function RoundButton({ label, onClick, active = false, tone = 'plain', disabled = false, children }: {
  label: string
  onClick: () => void
  active?: boolean
  tone?: 'plain' | 'danger'
  disabled?: boolean
  children: React.ReactNode
}) {
  const danger = tone === 'danger'
  return (
    <div className="flex flex-col items-center" style={{ gap: 4, width: 64 }}>
      <button type="button" onClick={onClick} aria-label={label} title={label}
        aria-pressed={danger ? undefined : active} disabled={disabled}
        className="flex items-center justify-center"
        style={{ width: 44, height: 44, borderRadius: 999,
          border: danger ? 'none' : `1px solid ${active ? BLUE : BORDER}`,
          backgroundColor: danger ? RED : active ? 'rgb(239,244,255)' : '#fff',
          opacity: disabled ? 0.5 : 1, cursor: disabled ? 'not-allowed' : 'pointer' }}>
        {children}
      </button>
      <span style={{ fontSize: 11, color: danger ? RED : active ? BLUE : MUTED }}>{label}</span>
    </div>
  )
}

function Ticker({ since }: { since: number }) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [])
  return <span data-testid="call-timer">{formatCallDuration(now - since)}</span>
}

export function InCallWindow({ user }: { user: Me }) {
  const {
    state, hangup, toggleMute, sendDtmf, switchMicrophone, switchSpeaker, dismissLastCall,
  } = useSoftphoneContext()
  // `connecting` (our own leg being answered) is shown by the dialer that placed it.
  const live = state.phase === 'in-call'
  const after = !live && state.phase === 'idle' ? state.lastCall : null
  const peer = live ? state.peer : after?.peer ?? null
  const direction = live ? state.direction : after?.direction ?? null

  const [keypad, setKeypad] = useState(false)
  const [audio, setAudio] = useState(false)
  const [entry, setEntry] = useState('')
  const [note, setNote] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)
  const [, redraw] = useState(0)
  const { inputs, outputs } = useAudioDevices(live && audio)

  // Reset per call, so nothing from the last call leaks into the next.
  const callKey = live ? `${state.peer}:${state.direction}` : null
  const lastKey = useRef<string | null>(null)
  useEffect(() => {
    if (callKey && callKey !== lastKey.current) {
      setKeypad(false)
      setAudio(false)
      setEntry('')
      setNote(null)
      setAdding(false)
    }
    lastKey.current = callKey
  }, [callKey])

  // Who: what the screen that placed the call knew, else the caller lookup.
  const placed = calledTarget(peer)
  const looked = useCaller(peer, Boolean(peer) && (live || after !== null))
  const contactId = placed?.contactId ?? looked?.id ?? null
  const contactName = placed?.contactName ?? looked?.name ?? null
  const number = formatPhone(peer)
  const title = callTitle(contactName, number)

  if (!live && !after) return null

  const press = (digit: string) => {
    playDtmfTone(digit)
    setEntry((e) => (e + digit).slice(-24))
    void sendDtmf(digit).then((ok) => {
      if (!ok) setNote('That tone could not be sent — the call may have ended.')
    })
  }

  const pick = (kind: 'mic' | 'speaker', deviceId: string) => {
    setAudioPref(kind, deviceId)
    redraw((n) => n + 1)
    if (kind === 'speaker') void switchSpeaker()
    else void switchMicrophone().then((ok) => {
      if (!ok) {
        setNote('That microphone could not be opened — the call is still on the one it had.')
      }
    })
  }

  const openConversation = async () => {
    try {
      const conv = placed?.conversationId
        ?? (contactId != null ? (await openContactConversation(contactId)).conversation_id : null)
      if (conv != null) openRecord('conversations', conv)
      dismissLastCall()
    } catch (err) {
      setNote(err instanceof ApiError ? err.message : 'The conversation could not be opened.')
    }
  }

  const next = afterCallActions(contactId)
  const staff = canAddContact(user)

  return (
    <div role="dialog" aria-label={live ? 'Call in progress' : 'Call ended'}
      data-phase={state.phase}
      style={{ position: 'fixed', top: 52, right: 16, zIndex: 60, width: 320,
        maxWidth: 'calc(100vw - 32px)', background: '#fff', border: `1px solid ${LINE}`,
        borderRadius: 8, color: TEXT, fontSize: 13,
        boxShadow: '0 12px 16px -4px rgba(16,24,40,0.08), 0 4px 6px -2px rgba(16,24,40,0.03)' }}>
      <div className="flex items-center" style={{ gap: 10, padding: '12px 14px' }}>
        <div className="flex shrink-0 items-center justify-center" aria-hidden
          style={{ width: 36, height: 36, borderRadius: 999, backgroundColor: 'rgb(185,230,254)',
            fontSize: 12, fontWeight: 600, color: 'rgb(71,84,103)' }}>
          {contactName ? contactName.trim().slice(0, 2).toUpperCase() : '+1'}
        </div>
        <div className="min-w-0 flex-1">
          <div data-testid="call-title" className="truncate"
            style={{ fontSize: 14, fontWeight: 600, color: INK }}>{title}</div>
          {contactName && number && (
            <div className="truncate" style={{ fontSize: 12, color: MUTED }}>{number}</div>
          )}
          <div className="flex items-center" style={{ gap: 4, fontSize: 12, color: MUTED,
            whiteSpace: 'nowrap', fontVariantNumeric: 'tabular-nums' }}>
            <span>{direction === 'outbound' ? 'Outbound call' : 'Inbound call'}</span>
            <span aria-hidden>·</span>
            {live && state.answeredAt && <Ticker since={state.answeredAt} />}
            {after && <span data-testid="call-duration">Ended · {formatCallDuration(after.durationMs)}</span>}
          </div>
        </div>
        {live && (
          <span data-testid="recording" title="This call is recorded by the phone system"
            className="flex shrink-0 items-center" style={{ gap: 4, fontSize: 11, fontWeight: 600,
              color: RED }}>
            <span aria-hidden style={{ width: 8, height: 8, borderRadius: 999, backgroundColor: RED }} />
            REC
          </span>
        )}
        {after && (
          <button type="button" aria-label="Close" onClick={dismissLastCall} style={{ padding: 4 }}>
            <IconClose size={16} color={TEXT} />
          </button>
        )}
      </div>

      {live && (
        <div className="flex justify-between" style={{ padding: '4px 14px 12px',
          borderTop: `1px solid ${LINE}`, paddingTop: 12 }}>
          <RoundButton label={state.muted ? 'Unmute' : 'Mute'} active={state.muted}
            onClick={toggleMute}>
            {state.muted ? <IconMicOff size={20} color={BLUE} /> : <IconMic size={20} color={TEXT} />}
          </RoundButton>
          <RoundButton label="Keypad" active={keypad}
            onClick={() => { setKeypad((v) => !v); setAudio(false) }}>
            <IconDialpad size={20} color={keypad ? BLUE : TEXT} />
          </RoundButton>
          <RoundButton label="Audio" active={audio}
            onClick={() => { setAudio((v) => !v); setKeypad(false) }}>
            <IconSettings size={20} color={audio ? BLUE : TEXT} />
          </RoundButton>
          <RoundButton label="Hang up" tone="danger" onClick={() => void hangup()}>
            <IconPhoneOff size={20} color="#fff" />
          </RoundButton>
        </div>
      )}

      {live && keypad && (
        <div style={{ padding: '0 14px 14px' }}>
          <div data-testid="dtmf-entry" aria-live="polite" className="flex items-center justify-end"
            style={{ height: 32, padding: '0 10px', borderRadius: 6, border: `1px solid ${LINE}`,
              backgroundColor: 'rgb(249,250,251)', fontSize: 18, fontWeight: 500, color: entry ? INK : MUTED,
              letterSpacing: 1, overflow: 'hidden', whiteSpace: 'nowrap' }}>
            {entry || ' '}
          </div>
          <div role="group" aria-label="Call keypad" style={{ display: 'grid',
            gridTemplateColumns: 'repeat(3, 1fr)', gap: 6, marginTop: 8 }}>
            {KEYPAD.map((k) => (
              <button key={k.key} type="button" aria-label={`Send ${k.key}`} onClick={() => press(k.key)}
                className="flex flex-col items-center justify-center"
                style={{ height: 40, borderRadius: 6, border: `1px solid ${LINE}`,
                  backgroundColor: '#fff', color: INK }}>
                <span style={{ fontSize: 16, fontWeight: 500, lineHeight: 1 }}>{k.key}</span>
                {k.letters && <span style={{ fontSize: 8, letterSpacing: 1, color: MUTED }}>{k.letters}</span>}
              </button>
            ))}
          </div>
        </div>
      )}

      {live && audio && (
        <div className="flex flex-col" style={{ gap: 8, padding: '0 14px 14px' }}>
          <label className="flex flex-col" style={{ gap: 4 }}>
            <span style={{ fontSize: 12, fontWeight: 500, color: TEXT }}>Microphone</span>
            <select aria-label="Microphone" value={getAudioPref('mic')}
              onChange={(e) => pick('mic', e.target.value)}
              style={{ height: 32, borderRadius: 6, border: `1px solid ${BORDER}`, padding: '0 8px' }}>
              <option value="">System default</option>
              {inputs.map((d) => <option key={d.deviceId} value={d.deviceId}>{d.label}</option>)}
            </select>
          </label>
          {canPickSpeaker() && (
            <label className="flex flex-col" style={{ gap: 4 }}>
              <span style={{ fontSize: 12, fontWeight: 500, color: TEXT }}>Speaker</span>
              <select aria-label="Speaker" value={getAudioPref('speaker')}
                onChange={(e) => pick('speaker', e.target.value)}
                style={{ height: 32, borderRadius: 6, border: `1px solid ${BORDER}`, padding: '0 8px' }}>
                <option value="">System default</option>
                {outputs.map((d) => <option key={d.deviceId} value={d.deviceId}>{d.label}</option>)}
              </select>
            </label>
          )}
        </div>
      )}

      {after && (
        <div style={{ padding: '12px 14px', borderTop: `1px solid ${LINE}` }}>
          {next.openConversation && canOpenRecords() && (
            <button type="button" onClick={() => void openConversation()}
              style={{ width: '100%', height: 36, borderRadius: 6, fontSize: 14, fontWeight: 600,
                color: '#fff', backgroundColor: BLUE }}>
              Open conversation
            </button>
          )}
          {next.addContact && (
            <button type="button" onClick={() => setAdding(true)} disabled={!staff}
              title={staff ? 'Save this number as a contact — its history moves with it'
                : 'Only staff can add contacts'}
              className="flex items-center justify-center gap-1"
              style={{ width: '100%', height: 36, borderRadius: 6, fontSize: 14, fontWeight: 600,
                color: '#fff', backgroundColor: BLUE,
                ...(staff ? {} : { opacity: 0.5, cursor: 'not-allowed' }) }}>
              <IconPlus size={16} color="#fff" />
              Add as contact
            </button>
          )}
          {next.addContact && (
            <div style={{ fontSize: 12, color: MUTED, marginTop: 6 }}>
              This number is not a contact. The call is kept on its own thread in Conversations.
            </div>
          )}
        </div>
      )}

      {(note || (live && state.error)) && (
        <div role="status" style={{ padding: '0 14px 12px', fontSize: 12, color: note ? RED : MUTED }}>
          {note ?? state.error}
        </div>
      )}

      {adding && after && (
        <AddContactDialog
          initial={{ phone: peer ?? '' }}
          onClose={() => setAdding(false)}
          onCreated={(c) => {
            setAdding(false)
            const adopted = (c as { adopted_number_thread?: { conversation_id: number | null } | null })
              .adopted_number_thread?.conversation_id
            if (adopted != null && canOpenRecords()) openRecord('conversations', adopted)
            dismissLastCall()
          }}
        />
      )}
    </div>
  )
}
