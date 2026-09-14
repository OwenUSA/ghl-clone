/**
 * The top-right status dot, on every page. Replaces the floating softphone dock.
 *
 * The owner (2026-09-14): "i want something more subtle ... on the top right that shows on
 * every page or view, but very subtle and if i hover onto it it should show any more
 * details if needed, but it should always show if we are online or not so i know if
 * everything working good".
 *
 * Placed where GoHighLevel puts its small round phone button, beside the bell and avatar
 * (refs/opps/02). A phone glyph with a coloured dot: the WORST of three checks — this
 * browser's phone, the link to the phone system, Quo sync (`lib/connectionStatus.ts`).
 * While a call is live it becomes GHL's filled green phone button with the call timer.
 *
 * Hover OR keyboard focus opens the card. The card names each check with its state and a
 * plain sentence, and carries the controls the dock had, with the same behaviour: Switch
 * off / Switch on / Try again / Use this tab, and Mute / Hang up during a call.
 *
 * Mounted ONCE in App.tsx inside the SoftphoneProvider — never in a page.
 */
import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { fetchConnectionStatus } from '../lib/api'
import { useSoftphoneContext } from '../lib/softphoneContext'
import {
  TONE_COLOR,
  overall,
  phoneAction,
  phoneCheck,
  serverChecks,
  type Check,
} from '../lib/connectionStatus'
import { formatPhone } from '../lib/phone'
import { IconPhone } from './Icon'
import { Elapsed, useCaller } from './Softphone'

const TEXT = 'rgb(31,41,55)'
const MUTED = 'rgb(102,112,133)'
const LINE = 'rgb(234,236,240)'
const BORDER = 'rgb(208,213,221)'
const BLUE = 'rgb(21,94,239)'
const LIVE = 'rgb(23,124,73)'
const RED = 'rgb(180,35,24)'

/** How long the pointer may be outside the button and card before the card closes, so
 *  crossing the 6px gap between them does not flicker it shut. */
const CLOSE_DELAY_MS = 200

function useOnline(): boolean {
  const [online, setOnline] = useState(() =>
    typeof navigator === 'undefined' ? true : navigator.onLine !== false)
  useEffect(() => {
    const on = () => setOnline(true)
    const off = () => setOnline(false)
    window.addEventListener('online', on)
    window.addEventListener('offline', off)
    return () => {
      window.removeEventListener('online', on)
      window.removeEventListener('offline', off)
    }
  }, [])
  return online
}

function Button({ onClick, children, tone = 'plain' }: {
  onClick: () => void
  children: React.ReactNode
  tone?: 'plain' | 'primary' | 'danger'
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        height: 28,
        padding: '0 10px',
        borderRadius: 6,
        fontSize: 13,
        fontWeight: 500,
        cursor: 'pointer',
        border: tone === 'plain' ? `1px solid ${BORDER}` : 'none',
        background: tone === 'primary' ? BLUE : tone === 'danger' ? RED : '#fff',
        color: tone === 'plain' ? TEXT : '#fff',
      }}
    >
      {children}
    </button>
  )
}

function Row({ check, children }: { check: Check; children?: React.ReactNode }) {
  return (
    <div data-check={check.key} style={{ padding: '10px 0', borderTop: `1px solid ${LINE}` }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span aria-hidden style={{ width: 8, height: 8, borderRadius: 999, flexShrink: 0,
          backgroundColor: TONE_COLOR[check.tone] }} />
        <span style={{ fontWeight: 500, flex: 1, color: TEXT }}>{check.title}</span>
        <span style={{ fontSize: 12, color: check.tone === 'grey' ? MUTED : TONE_COLOR[check.tone] }}>
          {check.word}
        </span>
      </div>
      <div style={{ color: MUTED, fontSize: 12, marginTop: 2, paddingLeft: 16 }}>{check.sentence}</div>
      {check.details.map((d) => (
        <div key={d} style={{ color: MUTED, fontSize: 11, marginTop: 2, paddingLeft: 16 }}>{d}</div>
      ))}
      {children && <div style={{ marginTop: 8, paddingLeft: 16, display: 'flex', gap: 8 }}>{children}</div>}
    </div>
  )
}

export function StatusIndicator() {
  const { state, setOnline, hangup, toggleMute } = useSoftphoneContext()
  const online = useOnline()
  const [hovered, setHovered] = useState(false)
  const [focused, setFocused] = useState(false)
  const [now, setNow] = useState(() => Date.now())
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const wrapRef = useRef<HTMLDivElement | null>(null)
  const buttonRef = useRef<HTMLButtonElement | null>(null)
  const open = hovered || focused

  // About once a minute, and whenever the window regains focus. The backend caches for 30s
  // and asks owen-main at most once per window, however many tabs are polling.
  const query = useQuery({
    queryKey: ['connection-status'],
    queryFn: fetchConnectionStatus,
    refetchInterval: 60_000,
    refetchOnWindowFocus: 'always',
    staleTime: 0,
    retry: false,
  })

  // react-query's own "window focus" is `visibilitychange`, which fires on a tab switch but
  // not when somebody clicks back into the browser from another app. Both should re-check.
  const { refetch } = query
  useEffect(() => {
    const onFocus = () => void refetch()
    window.addEventListener('focus', onFocus)
    return () => window.removeEventListener('focus', onFocus)
  }, [refetch])

  // The "3 min ago" lines are relative, so keep them honest while the card is open.
  useEffect(() => {
    if (!open) return
    setNow(Date.now())
    const t = setInterval(() => setNow(Date.now()), 15_000)
    return () => clearInterval(t)
  }, [open])

  useEffect(() => () => {
    if (closeTimer.current) clearTimeout(closeTimer.current)
  }, [])

  const inCall = state.phase === 'in-call'
  const contact = useCaller(state.peer, inCall)

  const phone = phoneCheck({ status: state.status, operator: state.operator, error: state.error })
  const server = serverChecks(query.data ?? null, {
    failed: query.isError,
    offline: !online,
    nowMs: now,
  })
  const checks = [phone, ...server]
  const summary = overall(checks, state.phase)
  const action = phoneAction(state.status)
  const live = summary.tone === 'live'

  const enter = () => {
    if (closeTimer.current) clearTimeout(closeTimer.current)
    setHovered(true)
  }
  const leave = () => {
    if (closeTimer.current) clearTimeout(closeTimer.current)
    closeTimer.current = setTimeout(() => setHovered(false), CLOSE_DELAY_MS)
  }

  const who = contact?.name || formatPhone(state.peer) || 'Unknown caller'
  const label = `Connection status: ${summary.headline}`

  return (
    <div
      ref={wrapRef}
      style={{ position: 'fixed', top: 9, right: 16, zIndex: 70 }}
      onMouseEnter={enter}
      onMouseLeave={leave}
      onFocus={() => setFocused(true)}
      onBlur={(e) => {
        if (!wrapRef.current?.contains(e.relatedTarget as Node | null)) setFocused(false)
      }}
      onKeyDown={(e) => {
        if (e.key === 'Escape' && open) {
          setHovered(false)
          setFocused(false)
          buttonRef.current?.blur()
        }
      }}
    >
      <button
        ref={buttonRef}
        type="button"
        aria-label={label}
        aria-expanded={open}
        aria-controls="ghl-status-card"
        data-tone={summary.tone}
        className={live ? 'ghl-live-pulse' : undefined}
        style={{
          position: 'relative',
          height: 32,
          minWidth: 32,
          padding: live ? '0 10px 0 8px' : 0,
          borderRadius: 999,
          border: 'none',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          gap: 6,
          cursor: 'pointer',
          background: live ? LIVE : open ? 'rgb(242,244,247)' : 'transparent',
          color: live ? '#fff' : 'rgb(71,84,103)',
          fontSize: 12,
          fontWeight: 500,
          fontVariantNumeric: 'tabular-nums',
        }}
      >
        <IconPhone size={live ? 16 : 18} />
        {live && state.answeredAt && <Elapsed since={state.answeredAt} />}
        {!live && (
          <span
            aria-hidden
            style={{
              position: 'absolute',
              top: 5,
              right: 5,
              width: 8,
              height: 8,
              borderRadius: 999,
              backgroundColor: TONE_COLOR[summary.tone as keyof typeof TONE_COLOR],
              boxShadow: '0 0 0 2px #fff',
            }}
          />
        )}
      </button>

      {open && (
        <div
          id="ghl-status-card"
          role="dialog"
          aria-label="Connection status"
          style={{
            position: 'absolute',
            top: 38,
            right: 0,
            width: 320,
            maxWidth: 'calc(100vw - 32px)',
            background: '#fff',
            border: `1px solid ${LINE}`,
            borderRadius: 8,
            boxShadow: '0 12px 16px -4px rgba(16,24,40,0.08), 0 4px 6px -2px rgba(16,24,40,0.03)',
            padding: '12px 14px 4px',
            fontSize: 13,
            color: TEXT,
          }}
        >
          <div style={{ fontWeight: 600, fontSize: 14, paddingBottom: 10 }}>{summary.headline}</div>

          {inCall && (
            <div data-check="call" style={{ padding: '10px 0', borderTop: `1px solid ${LINE}` }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span aria-hidden style={{ width: 8, height: 8, borderRadius: 999, backgroundColor: LIVE }} />
                <span style={{ fontWeight: 500, flex: 1, minWidth: 0, overflow: 'hidden',
                  textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{who}</span>
                {state.answeredAt && (
                  <span style={{ color: MUTED, fontSize: 12, fontVariantNumeric: 'tabular-nums' }}>
                    <Elapsed since={state.answeredAt} />
                  </span>
                )}
              </div>
              <div style={{ marginTop: 8, paddingLeft: 16, display: 'flex', gap: 8 }}>
                <Button onClick={toggleMute}>{state.muted ? 'Unmute' : 'Mute'}</Button>
                <Button tone="danger" onClick={() => void hangup()}>Hang up</Button>
              </div>
            </div>
          )}

          <Row check={phone}>
            {!inCall && action && (
              <Button tone={action.online ? 'primary' : 'plain'} onClick={() => setOnline(action.online)}>
                {action.label}
              </Button>
            )}
          </Row>
          {server.map((c) => <Row key={c.key} check={c} />)}
        </div>
      )}
    </div>
  )
}
