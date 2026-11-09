import { useEffect, useRef, useState } from 'react'
import { MicButton } from '../MicButton'
import type { OwenAiMessage, OwenAiStep, OwenAiThreadDetail } from '../../lib/api'
import { auditPromptLabel, busySince, elapsed, excerpt, prettyJson, runFooter, threadBusy, toolsLabel } from '../../lib/owenAi'
import { Markdown } from './Markdown'
import { BLUE, INK, LINE, MONO, MUTED, RED } from './tokens'

/**
 * One Owen AI conversation: the messages, "Owen AI is working…" while an answer is queued or
 * running (the page polls the thread every 2 s meanwhile), and the composer. Sending is
 * disabled while busy — the server refuses a second question with a 409 anyway.
 *
 * An audit conversation (2026-11-03, made by the scheduled sweep) is read-only: no composer, and
 * its machine prompts shrink to one grey line ("Audit #801") that opens to the full prompt.
 */
export function Conversation({ thread, loading, error, onSend, sending, sendError, disabledReason }: {
  thread: OwenAiThreadDetail | undefined; loading: boolean; error: string | null
  onSend: (text: string) => Promise<boolean>; sending: boolean; sendError: string | null
  /** Set when the module is off: the composer explains instead of sending. */
  disabledReason: string | null
}) {
  const [draft, setDraft] = useState('')
  const bottom = useRef<HTMLDivElement>(null)
  const messages = thread?.messages ?? []
  const busy = threadBusy(thread)
  const since = busySince(messages)
  const now = useNow(busy)
  const audit = thread?.kind === 'audit'

  useEffect(() => { bottom.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }) },
    [messages.length, busy, thread?.id, sendError])

  const blocked = busy || sending || !!disabledReason || !thread
  const send = () => {
    const t = draft.trim()
    if (!t || blocked) return
    setDraft('')
    // A refused send (409, a blip) puts the words back rather than losing them.
    void onSend(t).then((ok) => { if (!ok) setDraft((d) => d || t) })
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div style={{ flex: 1, overflowY: 'auto' }}>
        <div style={{ maxWidth: 980, margin: '0 auto', padding: '20px 12px 8px' }}>
          {loading && <div style={{ color: MUTED, fontSize: 13 }}>Loading the conversation…</div>}
          {error && <div role="alert" style={{ color: RED, fontSize: 13 }}>Could not load this conversation: {error}</div>}
          {thread && !loading && messages.length === 0 && (
            <div style={{ color: MUTED, fontSize: 14, textAlign: 'center', marginTop: 40 }}>
              {audit ? 'This audit has not written anything yet.'
                : thread.kind === 'daily' ? 'Ask about today — the jobs, the calls, what needs doing.'
                : thread.kind === 'job' ? `Ask anything about job #${thread.job_number ?? thread.job_uid ?? ''}.`
                  : 'Ask Owen AI anything.'}
            </div>
          )}
          {messages.map((m) => m.role === 'user'
            ? (audit || m.kind === 'audit' ? <AuditPrompt key={m.id} m={m} /> : <UserBubble key={m.id} m={m} />)
            : m.role === 'system' ? <SystemNote key={m.id} m={m} />
            : <AssistantMessage key={m.id} m={m} />)}
          {busy && (
            <div role="status" style={{ display: 'flex', alignItems: 'center', gap: 8, margin: '18px 0',
              color: MUTED, fontSize: 14 }}>
              <span className="owen-ai-pulse" aria-hidden="true"
                style={{ width: 8, height: 8, borderRadius: 4, background: BLUE }} />
              <span>Owen AI is working…</span>
              <span style={{ fontVariantNumeric: 'tabular-nums' }}>{elapsed(since, now)}</span>
            </div>
          )}
          {sendError && (
            <div role="alert" style={errorBox}>{sendError}</div>
          )}
          <div ref={bottom} />
        </div>
      </div>
      {audit ? (
        <div style={{ padding: '8px 12px 14px', fontSize: 12, color: MUTED, textAlign: 'center' }}>
          A scheduled audit — read only. Open a job's chat from Alerts to ask about it.</div>
      ) : (
        <div style={{ padding: '8px 12px 18px', background: 'linear-gradient(transparent, rgb(249,250,251) 30%)' }}>
          <div style={{ maxWidth: 980, margin: '0 auto' }}>
            <Composer value={draft} onChange={setDraft} onSend={send} blocked={blocked}
              placeholder={disabledReason ?? (busy ? 'Owen AI is working — wait for the answer…'
                : messages.length ? 'Reply to Owen AI…' : 'How can I help?')} />
          </div>
        </div>
      )}
    </div>
  )
}

const errorBox: React.CSSProperties = { margin: '10px 0', padding: '8px 12px', fontSize: 13, lineHeight: '20px',
  color: RED, background: 'rgb(254,243,242)', border: '1px solid rgb(254,205,202)', borderRadius: 8,
  whiteSpace: 'pre-wrap' }

/** A clock that ticks every second while `on`, so the elapsed time moves between polls. */
function useNow(on: boolean): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!on) return
    setNow(Date.now())
    const t = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(t)
  }, [on])
  return now
}

function UserBubble({ m }: { m: OwenAiMessage }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', margin: '14px 0' }}>
      <div style={{ maxWidth: '80%', background: 'rgb(240,238,232)', color: INK, borderRadius: 18,
        padding: '10px 16px', fontSize: 15, lineHeight: '22px', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
        {m.content}</div>
      {/* The runner marks the QUESTION as failed when no answer could be written (a restart). */}
      {m.status === 'error' && m.error && (
        <div role="alert" style={{ ...errorBox, maxWidth: '80%' }}>{m.error}</div>
      )}
    </div>
  )
}

/** A note the CRM posted (a proposal draft approved / rejected): one grey line. Owen AI reads
    it with the owner's next message. */
function SystemNote({ m }: { m: OwenAiMessage }) {
  return (
    <div role="note" style={{ margin: '14px auto', maxWidth: '90%', fontSize: 12, lineHeight: '18px', color: MUTED,
      textAlign: 'center', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{m.content}</div>
  )
}

/** A scheduled audit's machine prompt: one small grey line, the whole prompt on click. */
function AuditPrompt({ m }: { m: OwenAiMessage }) {
  const [open, setOpen] = useState(false)
  return (
    <div style={{ margin: '16px 0 4px', fontSize: 12, color: MUTED, minWidth: 0 }}>
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} title={open ? undefined : m.content}
        style={{ fontSize: 12, color: MUTED, display: 'inline-flex', alignItems: 'center', gap: 6 }}>
        <span aria-hidden="true" style={{ width: 6, height: 6, borderRadius: 3, background: 'rgb(208,213,221)' }} />
        {auditPromptLabel(m.content)}
      </button>
      {open && <div style={{ marginTop: 4, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{m.content}</div>}
      {m.status === 'error' && m.error && <div role="alert" style={errorBox}>{m.error}</div>}
    </div>
  )
}

function AssistantMessage({ m }: { m: OwenAiMessage }) {
  const [open, setOpen] = useState(false)
  const steps = m.steps ?? []
  const footer = runFooter(m)
  return (
    <div style={{ margin: '18px 0', fontSize: 15, lineHeight: '24px', color: INK, minWidth: 0 }}>
      {m.content && <Markdown text={m.content} />}
      {m.error && <div role="alert" style={errorBox}>{m.error}</div>}
      {(steps.length > 0 || footer) && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 4, flexWrap: 'wrap', fontSize: 12,
          color: MUTED }}>
          {steps.length > 0 && (
            <button type="button" onClick={() => setOpen(!open)} aria-expanded={open}
              style={{ fontSize: 12, color: MUTED, textDecoration: 'underline', textUnderlineOffset: 2 }}>
              {open ? 'Hide tools' : toolsLabel(steps.length)}</button>
          )}
          {footer && <span>{footer}</span>}
        </div>
      )}
      {open && <Steps steps={steps} />}
    </div>
  )
}

function Steps({ steps }: { steps: OwenAiStep[] }) {
  return (
    <ol style={{ listStyle: 'none', margin: '8px 0 0', padding: 0, display: 'flex', flexDirection: 'column', gap: 10 }}>
      {steps.map((s, i) => (
        <li key={i} style={{ fontSize: 12, lineHeight: '18px', color: INK }}>
          <div><span style={{ color: MUTED }}>{i + 1}.</span>{' '}
            <span style={{ fontWeight: 600, fontFamily: MONO }}>{s.tool}</span></div>
          <pre style={pre}>{prettyJson(s.input)}</pre>
          {s.error ? (
            <pre style={{ ...pre, color: RED, background: 'rgb(254,243,242)' }}>{excerpt(s.error)}</pre>
          ) : s.result != null && s.result !== '' ? (
            <pre style={{ ...pre, color: 'rgb(71,84,103)' }}>{excerpt(s.result)}</pre>
          ) : null}
        </li>
      ))}
    </ol>
  )
}

const pre: React.CSSProperties = { margin: '4px 0 0', padding: '6px 8px', background: 'rgb(243,244,246)',
  maxHeight: 320, overflowY: 'auto', border: `1px solid ${LINE}`, borderRadius: 6, fontSize: 11,
  lineHeight: '16px', whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontFamily: MONO }

function Composer({ value, onChange, onSend, blocked, placeholder }: {
  value: string; onChange: (v: string) => void; onSend: () => void; blocked: boolean; placeholder: string
}) {
  const ref = useRef<HTMLTextAreaElement>(null)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`
  }, [value])
  const ready = value.trim().length > 0 && !blocked
  return (
    <div style={{ background: '#fff', border: `1px solid ${LINE}`, borderRadius: 20,
      boxShadow: '0 4px 20px rgba(16,24,40,0.06)', padding: '14px 14px 10px 18px' }}>
      <textarea ref={ref} value={value} rows={1} placeholder={placeholder} aria-label="Message Owen AI"
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); onSend() }
        }}
        style={{ width: '100%', resize: 'none', border: 'none', outline: 'none', fontSize: 15, lineHeight: '22px',
          color: INK, background: 'transparent', maxHeight: 200 }} />
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: 6 }}>
        <span style={{ fontSize: 12, color: MUTED, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          Enter to send · Shift+Enter for a new line</span>
        <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <MicButton value={value} onChange={onChange} disabled={blocked} />
          <button type="button" aria-label="Send" disabled={!ready} onClick={onSend}
            style={{ width: 32, height: 32, borderRadius: 10, display: 'flex', alignItems: 'center',
              justifyContent: 'center', background: ready ? BLUE : 'rgb(234,236,240)',
              color: ready ? '#fff' : 'rgb(152,162,179)', transition: 'background 120ms' }}>
            <svg width={16} height={16} viewBox="0 0 24 24" fill="none" stroke="currentColor"
              strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M12 19V5M5 12l7-7 7 7" /></svg>
          </button>
        </span>
      </div>
    </div>
  )
}
