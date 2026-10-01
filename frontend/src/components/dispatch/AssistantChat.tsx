import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { dispatchChat, type DispatchSuggestion } from '../../lib/api'
import { blocks, greeting, SUGGESTED_QUESTIONS, type Inline } from '../../lib/dispatch'

const INK = 'rgb(16,24,40)'
const MUTED = 'rgb(102,112,133)'
const LINE = 'rgb(228,231,236)'
const ACCENT = 'rgb(217,119,87)'
const BLUE = 'rgb(21,94,239)'
const SERIF = 'ui-serif, Georgia, Cambria, "Times New Roman", Times, serif'

export type ChatMsg = { role: 'user' | 'assistant'; content: string;
  suggestions?: DispatchSuggestion[]; error?: boolean }

/**
 * The Dispatch assistant (2026-10-01): the page's first tab, laid out like a Claude / ChatGPT
 * chat — a greeting and one composer when empty, a centred conversation with the composer
 * pinned below once it starts — with the office's common questions one click away.
 *
 * It reads (POST /api/dispatch/chat, the same boards this user may see) and can only RECORD a
 * suggestion; it never changes Zuper or contacts anyone. The conversation lives in the page, so
 * switching tabs keeps it; "New chat" clears it.
 */
export function AssistantChat({ name, messages, setMessages, onOpenSuggestions }: {
  name: string
  messages: ChatMsg[]
  setMessages: (m: ChatMsg[]) => void
  onOpenSuggestions: () => void
}) {
  const qc = useQueryClient()
  const [draft, setDraft] = useState('')
  const bottom = useRef<HTMLDivElement>(null)
  const ask = useMutation({
    mutationFn: (next: ChatMsg[]) => dispatchChat(next.filter((m) => !m.error)
      .map(({ role, content }) => ({ role, content }))),
    onSuccess: (r, next) => {
      setMessages([...next, { role: 'assistant', content: r.reply, suggestions: r.suggestions }])
      if (r.suggestions.length) qc.invalidateQueries({ queryKey: ['dispatch-suggestions'] })
    },
    onError: (e, next) => setMessages([...next, { role: 'assistant', error: true,
      content: (e as Error).message || 'The assistant could not answer.' }]),
  })

  useEffect(() => { bottom.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }) },
    [messages.length, ask.isPending])

  const send = (text: string) => {
    const t = text.trim()
    if (!t || ask.isPending) return
    const next: ChatMsg[] = [...messages, { role: 'user', content: t }]
    setMessages(next)
    setDraft('')
    ask.mutate(next)
  }

  const composer = (
    <Composer value={draft} onChange={setDraft} onSend={() => send(draft)} busy={ask.isPending}
      placeholder={messages.length ? 'Reply to the assistant…' : 'How can I help today?'} />
  )

  if (messages.length === 0) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center" style={{ padding: '24px 16px' }}>
        <style>{KEYFRAMES}</style>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 22 }}>
          <Spark size={30} />
          <h1 style={{ fontFamily: SERIF, fontSize: 34, fontWeight: 400, color: INK, margin: 0 }}>
            {greeting(name)}</h1>
        </div>
        <div style={{ width: '100%', maxWidth: 680 }}>
          {composer}
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 14, justifyContent: 'center' }}>
            {SUGGESTED_QUESTIONS.map((q) => (
              <button key={q} type="button" onClick={() => send(q)} className="hover:bg-[rgb(243,244,246)]"
                style={{ border: `1px solid ${LINE}`, borderRadius: 999, padding: '6px 12px',
                  fontSize: 13, color: INK, background: '#fff' }}>{q}</button>
            ))}
          </div>
          <div style={{ textAlign: 'center', fontSize: 12, color: MUTED, marginTop: 16 }}>
            Reads Zuper and every call and text. It never changes Zuper or contacts a customer.
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <style>{KEYFRAMES}</style>
      <div style={{ flex: 1, overflowY: 'auto' }}>
        <div style={{ maxWidth: 760, margin: '0 auto', padding: '24px 16px 8px' }}>
          <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 8 }}>
            <button type="button" onClick={() => { setMessages([]); setDraft('') }}
              disabled={ask.isPending} style={{ fontSize: 12, color: MUTED, border: `1px solid ${LINE}`,
                borderRadius: 999, padding: '4px 10px', background: '#fff' }}>+ New chat</button>
          </div>
          {messages.map((m, n) => m.role === 'user' ? (
            <div key={n} style={{ display: 'flex', justifyContent: 'flex-end', margin: '14px 0' }}>
              <div style={{ maxWidth: '80%', background: 'rgb(240,238,232)', color: INK,
                borderRadius: 18, padding: '10px 16px', fontSize: 15, lineHeight: '22px',
                whiteSpace: 'pre-wrap' }}>{m.content}</div>
            </div>
          ) : (
            <div key={n} style={{ display: 'flex', gap: 12, margin: '18px 0' }}>
              <div style={{ paddingTop: 2 }}><Spark size={20} /></div>
              <div style={{ flex: 1, minWidth: 0, fontSize: 15, lineHeight: '24px',
                color: m.error ? 'rgb(180,35,24)' : INK }}>
                <Answer text={m.content} />
                {!!m.suggestions?.length && (
                  <button type="button" onClick={onOpenSuggestions} style={{ marginTop: 8, fontSize: 13,
                    color: BLUE, border: `1px solid ${LINE}`, borderRadius: 8, padding: '6px 10px',
                    background: '#fff' }}>
                    {m.suggestions.length} suggested change{m.suggestions.length === 1 ? '' : 's'} to
                    review in Suggestions →</button>
                )}
              </div>
            </div>
          ))}
          {ask.isPending && (
            <div style={{ display: 'flex', gap: 12, margin: '18px 0', alignItems: 'center' }}>
              <span style={{ animation: 'dispatch-spin 2.4s linear infinite', display: 'inline-flex' }}>
                <Spark size={20} /></span>
              <span style={{ color: MUTED, fontSize: 14 }}>Looking through the jobs and calls
                <span className="dispatch-dots" /></span>
            </div>
          )}
          <div ref={bottom} />
        </div>
      </div>
      <div style={{ padding: '8px 16px 18px', background: 'linear-gradient(transparent, rgb(249,250,251) 30%)' }}>
        <div style={{ maxWidth: 760, margin: '0 auto' }}>
          {composer}
          <div style={{ textAlign: 'center', fontSize: 11, color: MUTED, marginTop: 6 }}>
            Written by AI — check it against the job in Zuper.</div>
        </div>
      </div>
    </div>
  )
}

function Composer({ value, onChange, onSend, busy, placeholder }: {
  value: string; onChange: (v: string) => void; onSend: () => void; busy: boolean
  placeholder: string
}) {
  const ref = useRef<HTMLTextAreaElement>(null)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`
  }, [value])
  const ready = value.trim().length > 0 && !busy
  return (
    <div style={{ background: '#fff', border: `1px solid ${LINE}`, borderRadius: 20,
      boxShadow: '0 4px 20px rgba(16,24,40,0.06)', padding: '14px 14px 10px 18px' }}>
      <textarea ref={ref} value={value} rows={1} autoFocus placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); onSend() } }}
        style={{ width: '100%', resize: 'none', border: 'none', outline: 'none', fontSize: 15,
          lineHeight: '22px', color: INK, background: 'transparent', maxHeight: 200 }} />
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: 6 }}>
        <span style={{ fontSize: 12, color: MUTED }}>Dispatch assistant · reads only</span>
        <button type="button" aria-label="Send" disabled={!ready} onClick={onSend}
          style={{ width: 32, height: 32, borderRadius: 10, display: 'flex', alignItems: 'center',
            justifyContent: 'center', background: ready ? ACCENT : 'rgb(234,236,240)',
            color: ready ? '#fff' : 'rgb(152,162,179)', transition: 'background 120ms' }}>
          <svg width={16} height={16} viewBox="0 0 24 24" fill="none" stroke="currentColor"
            strokeWidth={2.2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M12 19V5M5 12l7-7 7 7" /></svg>
        </button>
      </div>
    </div>
  )
}

function Parts({ parts }: { parts: Inline[] }) {
  return <>{parts.map((p, i) => p.bold ? <strong key={i}>{p.text}</strong> : <span key={i}>{p.text}</span>)}</>
}

function Answer({ text }: { text: string }) {
  return (
    <>
      {blocks(text).map((b, i) => b.kind === 'p' ? (
        <p key={i} style={{ margin: '0 0 10px' }}><Parts parts={b.parts} /></p>
      ) : b.kind === 'h' ? (
        <div key={i} style={{ fontWeight: 600, margin: '12px 0 6px' }}><Parts parts={b.parts} /></div>
      ) : (
        <div key={i} style={{ margin: '0 0 10px' }}>
          {b.items.map((it, j) => (
            <div key={j} style={{ display: 'flex', gap: 8, margin: '3px 0' }}>
              <span style={{ color: MUTED, minWidth: 16, textAlign: 'right' }}>
                {b.kind === 'ol' ? `${j + 1}.` : '•'}</span>
              <span style={{ flex: 1 }}><Parts parts={it} /></span>
            </div>
          ))}
        </div>
      ))}
    </>
  )
}

function Spark({ size }: { size: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true">
      {Array.from({ length: 8 }, (_, i) => (
        <line key={i} x1="12" y1="12" x2="12" y2="2" stroke={ACCENT} strokeWidth="2.4"
          strokeLinecap="round" transform={`rotate(${i * 45} 12 12)`} />
      ))}
    </svg>
  )
}

const KEYFRAMES = `
@keyframes dispatch-spin { to { transform: rotate(360deg) } }
@keyframes dispatch-dots { 0% { content: '' } 25% { content: '.' } 50% { content: '..' } 75% { content: '...' } }
.dispatch-dots::after { content: ''; animation: dispatch-dots 1.2s steps(1) infinite; }
`
