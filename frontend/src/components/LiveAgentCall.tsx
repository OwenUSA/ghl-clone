/**
 * "AI is on a call with …" — the live AI-agent call banner (2026-09-23, voice agents slice E).
 *
 * The owner's requirement behind take-over, from owen-main's spec: "I want a person to listen
 * to the call and be able to take control if the agent is not working properly." While an
 * agent is on a call this shows who with, for how long, and two buttons:
 *
 *  - **Listen** rings THIS user's browser line; answering it lets them hear the call, and
 *    nobody on the call hears them.
 *  - **Take over** stops the agent and puts this user on the call with the customer. It
 *    cannot be undone, so it asks first.
 *
 * Both ring the browser phone, so both are offered only while that phone is Ready and idle —
 * a ring that arrives at a switched-off phone rings nobody, and the call would carry on with
 * the agent while the dispatcher waited for a phone that never rang.
 *
 * ADMIN / DISPATCHER only, by `canOpenAiAgents` (the server enforces the same rule in
 * app/live_calls.py). For anybody else the query is disabled: no polling at all.
 *
 * Mounted ONCE in App.tsx beside the status dot and the bell, never in a page — a live call
 * matters whatever page is open. It sits to their left and draws nothing while no call is live.
 */
import { useMutation, useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import {
  listLiveCalls, listenToLiveCall, takeOverLiveCall, type LiveCall,
} from '../lib/api'
import type { Me } from '../lib/auth'
import { canOpenAiAgents } from '../lib/aiAgents'
import { formatPhone } from '../lib/phone'
import { useSoftphoneContext } from '../lib/softphoneContext'
import { Elapsed } from './Softphone'
import { Confirm } from './ZuperMoneyPanel'

const TEXT = 'rgb(16,24,40)'
const MUTED = 'rgb(102,112,133)'
const LINE = 'rgb(208,213,221)'
const PURPLE = 'rgb(105,56,239)'
const RED = 'rgb(180,35,24)'

/** How often the banner asks. The task's number, and a take-over decision is made in
 *  seconds: at 60s like the bell, a two-minute bad call could be half over before it showed. */
const POLL_MS = 5000

function callerLabel(c: LiveCall): string {
  if (c.contact?.name) return c.contact.name
  if (c.caller_number) return formatPhone(c.caller_number)
  return 'an unknown caller'
}

/** When the call started, in ms. owen-main's `started_at` when it has one; otherwise the
 *  session's own duration counted back from when this answer arrived — the call row can
 *  trail the session by a moment, and a timer that read 00:00 would be a lie. */
function startedAt(c: LiveCall, answeredAt: number): number {
  const t = c.started_at ? Date.parse(c.started_at) : NaN
  if (!Number.isNaN(t)) return t
  return answeredAt - (c.duration_s ?? 0) * 1000
}

export function LiveAgentCall({ user }: { user: Me }) {
  const allowed = canOpenAiAgents(user)
  const live = useQuery({
    queryKey: ['live-calls'], queryFn: listLiveCalls, enabled: allowed,
    refetchInterval: allowed ? POLL_MS : false, refetchOnWindowFocus: allowed,
  })
  if (!allowed) return null
  const calls = live.data?.calls ?? []
  if (calls.length === 0) return null
  return (
    <div aria-live="polite" style={{ position: 'fixed', top: 9, right: 92, zIndex: 70,
      display: 'flex', flexDirection: 'column', gap: 6, alignItems: 'flex-end',
      maxWidth: 'calc(100vw - 120px)' }}>
      {calls.map((c) => <LiveCallRow key={c.linkedid} call={c} since={startedAt(c, live.dataUpdatedAt)} />)}
    </div>
  )
}

function LiveCallRow({ call, since }: { call: LiveCall; since: number }) {
  const { state } = useSoftphoneContext()
  const [confirming, setConfirming] = useState(false)
  const [note, setNote] = useState<string | null>(null)
  const listen = useMutation({
    mutationFn: () => listenToLiveCall(call.linkedid),
    onSuccess: () => setNote('Your browser phone is ringing — answer it to listen.'),
    onError: (e: Error) => setNote(e.message),
  })
  const takeover = useMutation({
    mutationFn: () => takeOverLiveCall(call.linkedid),
    onSuccess: () => { setConfirming(false); setNote('Your browser phone is ringing — answer it to take the call.') },
    onError: (e: Error) => { setConfirming(false); setNote(e.message) },
  })
  const phoneReady = state.status === 'ready' && state.phase === 'idle'
  // Why the buttons are off, in the order that matters. A phone that is not Ready outranks
  // whatever the last click said: a stale "That call has already ended" over a switched-off
  // phone hid the one thing the user had to fix (seen in tests/browser_live_calls.py). While
  // the browser is RINGING — which is what Listen just asked for — the last note stands.
  const why = phoneReady ? undefined
    : state.status !== 'ready' ? 'Switch your browser phone on (the phone icon, top right) — this rings it.'
    : state.phase === 'ringing' ? undefined
    : 'You are on a call — hang up first, and the phone system rings you again.'
  const shown = why ?? note
  const busy = listen.isPending || takeover.isPending
  const who = callerLabel(call)

  return (
    <div role="status" style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap',
      padding: '5px 6px 5px 12px', background: '#fff', border: `1px solid ${LINE}`, borderRadius: shown ? 16 : 999,
      boxShadow: '0 4px 8px -2px rgba(16,24,40,0.10)', fontSize: 13, color: TEXT }}>
      <span aria-hidden style={{ width: 8, height: 8, borderRadius: 999, backgroundColor: PURPLE }} />
      <span>
        AI is on a call with <strong style={{ fontWeight: 600 }}>{who}</strong>
        <span style={{ color: MUTED }}> · <Elapsed since={since} />{call.agent ? ` · ${call.agent}` : ''}</span>
      </span>
      <button type="button" disabled={!phoneReady || busy} title={why}
        onClick={() => { setNote(null); listen.mutate() }}
        style={pill(!phoneReady || busy, PURPLE, false)}>
        {listen.isPending ? 'Ringing…' : 'Listen'}
      </button>
      <button type="button" disabled={!phoneReady || busy} title={why}
        onClick={() => { setNote(null); setConfirming(true) }}
        style={pill(!phoneReady || busy, RED, true)}>
        Take over
      </button>
      {shown && (
        <span style={{ flexBasis: '100%', fontSize: 12, color: MUTED, padding: '0 8px 2px 18px' }}>
          {shown}
        </span>
      )}
      {confirming && (
        <Confirm title="Take over this call?" action="Take over" danger busy={takeover.isPending}
          onConfirm={() => takeover.mutate()} onCancel={() => setConfirming(false)}>
          The AI agent stops talking and your browser phone rings; when you answer you are on the
          call with {who}. This cannot be handed back to the agent.
        </Confirm>
      )}
    </div>
  )
}

function pill(disabled: boolean, color: string, filled: boolean): React.CSSProperties {
  return {
    height: 26, padding: '0 12px', borderRadius: 999, fontSize: 13, fontWeight: 500,
    border: `1px solid ${color}`, color: filled ? '#fff' : color,
    backgroundColor: filled ? color : '#fff',
    opacity: disabled ? 0.45 : 1, cursor: disabled ? 'not-allowed' : 'pointer',
  }
}
