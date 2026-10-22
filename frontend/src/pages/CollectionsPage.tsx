import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { MicButton } from '../components/MicButton'
import { useEffect, useRef, useState } from 'react'
import { PageTabs } from '../components/PageTabs'
import { BLUE, INK, LINE, PAGE_BG } from '../components/ai/aiUi'
import {
  paymentChatHistory, paymentChatSend, paymentCreate, paymentGet, paymentMarkPaid,
  paymentSuggestion, paymentUndo, paymentUpdate, paymentUploadProof, paymentWaive, paymentsList,
  type PaymentChatMessage, type PaymentDetail,
} from '../lib/api'
import type { Me } from '../lib/auth'
import { canOpenAiAgents } from '../lib/aiAgents'
import { DUE_RULE, FILTERS, money, partLine, statusOf } from '../lib/payments'

const MUTED = 'rgb(102,112,133)'
const RED = 'rgb(180,35,24)'
const card: React.CSSProperties = { background: '#fff', border: `1px solid ${LINE}`, borderRadius: 8,
  padding: '12px 14px' }
const btn: React.CSSProperties = { height: 28, padding: '0 10px', borderRadius: 4, fontSize: 13,
  fontWeight: 500, border: `1px solid ${LINE}`, background: '#fff', color: INK }
const primary: React.CSSProperties = { ...btn, background: BLUE, borderColor: BLUE, color: '#fff' }
const input: React.CSSProperties = { border: `1px solid ${LINE}`, borderRadius: 4, padding: '5px 8px',
  fontSize: 13, color: INK, fontFamily: 'inherit', width: '100%' }

function Chip({ s }: { s: string }) {
  const t = statusOf(s)
  return <span style={{ fontSize: 12, fontWeight: 600, color: t.fg, background: t.bg, borderRadius: 10,
    padding: '1px 8px', whiteSpace: 'nowrap' }}>{t.label}</span>
}

/**
 * Collections (2026-10-21, app/payments): what each customer agreed to pay and when, what is due and
 * overdue, the proof, suggestions from Zuper and screenshots, and a chat that updates it all.
 * Every change can be undone (the latest one). Nothing here writes to Zuper or texts anyone.
 * Named "Collections", not "Payments": GoHighLevel's Payments module was removed on 2026-09-10
 * and stays removed (tests/test_frontend_nav.py) - this page charges nothing, it tracks.
 */
export function CollectionsPage({ user }: { user: Me }) {
  const allowed = canOpenAiAgents(user)
  const [show, setShow] = useState('open')
  const [selected, setSelected] = useState<number | null>(
    () => Number(new URLSearchParams(window.location.search).get('agreement')) || null)
  const [creating, setCreating] = useState(false)
  const list = useQuery({ queryKey: ['payments', show], queryFn: () => paymentsList(show),
    enabled: allowed, refetchInterval: 60000 })
  if (!allowed) return null
  const s = list.data?.summary
  return (
    <div className="flex min-w-0 flex-1 flex-col" style={{ height: '100vh', overflow: 'hidden', backgroundColor: PAGE_BG }}>
      <PageTabs title="Collections" label="Collections" active={show}
        tabs={FILTERS.map((f) => ({ key: f.key, label: f.label, onSelect: () => setShow(f.key) }))} />
      <div style={{ padding: '8px 20px', fontSize: 13, color: MUTED, display: 'flex', gap: 18, flexWrap: 'wrap',
        borderBottom: `1px solid ${LINE}`, background: '#fff', fontVariantNumeric: 'tabular-nums' }}>
        {s && <>
          <span>Overdue <strong style={{ color: s.overdue_cents ? RED : INK }}>{money(s.overdue_cents)}</strong></span>
          <span>Due now <strong style={{ color: INK }}>{money(s.due_cents)}</strong></span>
          <span>{s.open} open agreement{s.open === 1 ? '' : 's'} · {s.quoted} quoted, not agreed</span>
          {s.suggestions > 0 && <span style={{ color: BLUE }}>{s.suggestions} payment{s.suggestions === 1 ? '' : 's'} to confirm</span>}
        </>}
        <button type="button" style={{ ...primary, marginLeft: 'auto' }} onClick={() => setCreating(true)}>New agreement</button>
      </div>
      <div style={{ flex: 1, minHeight: 0, display: 'grid', gridTemplateColumns: 'minmax(260px, 340px) minmax(0, 1fr) minmax(280px, 360px)' }}>
        <div style={{ overflowY: 'auto', padding: 12, display: 'flex', flexDirection: 'column', gap: 6, borderRight: `1px solid ${LINE}` }}>
          {list.isLoading && <div style={{ color: MUTED, fontSize: 13 }}>Loading…</div>}
          {!list.isLoading && !list.data?.agreements.length && <div style={{ color: MUTED, fontSize: 13 }}>Nothing here.</div>}
          {list.data?.agreements.map((a) => (
            <button key={a.id} type="button" onClick={() => { setSelected(a.id); setCreating(false) }}
              style={{ ...card, textAlign: 'left', borderColor: selected === a.id ? BLUE : LINE }}>
              <div style={{ display: 'flex', gap: 8, alignItems: 'baseline' }}>
                <span style={{ fontWeight: 600, color: INK, fontSize: 14 }}>{a.customer_name ?? 'Customer'}</span>
                {a.job_number && <span style={{ fontSize: 12, color: MUTED }}>#{a.job_number}</span>}
                <span style={{ marginLeft: 'auto' }}><Chip s={a.status} /></span>
              </div>
              <div style={{ fontSize: 12, color: MUTED, marginTop: 2, fontVariantNumeric: 'tabular-nums' }}>
                {a.agreed_cents ? `Agreed ${money(a.agreed_cents)}` : a.quoted_cents ? `Quoted ${money(a.quoted_cents)}` : 'No amount yet'}
                {a.owed_now_cents > 0 && <> · <span style={{ color: RED }}>{money(a.owed_now_cents)} to collect</span></>}
              </div>
            </button>
          ))}
        </div>
        <div style={{ overflowY: 'auto', padding: 16 }}>
          {creating ? <NewAgreement onDone={(id) => { setCreating(false); if (id) setSelected(id) }} />
            : selected != null ? <Detail id={selected} /> : <div style={{ color: MUTED, fontSize: 13 }}>
              Pick an agreement, or tell the assistant on the right what was agreed or paid.</div>}
        </div>
        <Chat onOpen={setSelected} />
      </div>
    </div>
  )
}

function useRefresh() {
  const qc = useQueryClient()
  return (d?: PaymentDetail) => {
    qc.invalidateQueries({ queryKey: ['payments'] })
    if (d) qc.setQueryData(['payment', d.id], d)
  }
}

function NewAgreement({ onDone }: { onDone: (id: number | null) => void }) {
  const refresh = useRefresh()
  const [f, setF] = useState({ job_number: '', customer_name: '', quoted: '', agreed: '', description: '', split: '50/50' })
  const [error, setError] = useState<string | null>(null)
  const create = useMutation({ mutationFn: () => paymentCreate({ ...f, split: f.agreed ? f.split : undefined }),
    onSuccess: (d) => { refresh(d); onDone(d.id) }, onError: (e: Error) => setError(e.message) })
  const field = (k: keyof typeof f, label: string, placeholder = '') => (
    <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12, color: MUTED }} htmlFor={`pay-new-${k}`}>
      {label}
      <input id={`pay-new-${k}`} style={input} value={f[k]} placeholder={placeholder}
        onChange={(e) => setF({ ...f, [k]: e.target.value })} />
    </label>
  )
  return (
    <div style={{ ...card, display: 'flex', flexDirection: 'column', gap: 10, maxWidth: 560 }}>
      <div style={{ fontSize: 16, fontWeight: 600, color: INK }}>New agreement</div>
      {field('job_number', 'Zuper job #', '701')}
      {field('customer_name', 'Customer (if there is no job)')}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
        {field('quoted', 'Quoted ($)', '8,000')}
        {field('agreed', 'Agreed ($)', '7,500')}
      </div>
      {field('description', 'What for', 'Tile repair, Better option')}
      <label style={{ fontSize: 12, color: MUTED }} htmlFor="pay-new-split">How it is paid
        <select id="pay-new-split" style={{ ...input, marginTop: 4 }} value={f.split} onChange={(e) => setF({ ...f, split: e.target.value })}>
          <option value="50/50">50% before the job, 50% after</option>
          <option value="30/70">30% before, 70% after</option>
          <option value="full">All after the job</option>
        </select>
      </label>
      {error && <div role="alert" style={{ color: RED, fontSize: 13 }}>{error}</div>}
      <div style={{ display: 'flex', gap: 8 }}>
        <button type="button" style={primary} disabled={create.isPending} onClick={() => create.mutate()}>Save</button>
        <button type="button" style={btn} onClick={() => onDone(null)}>Cancel</button>
      </div>
    </div>
  )
}

function Detail({ id }: { id: number }) {
  const refresh = useRefresh()
  const q = useQuery({ queryKey: ['payment', id], queryFn: () => paymentGet(id), refetchInterval: 60000 })
  const [error, setError] = useState<string | null>(null)
  const [paying, setPaying] = useState<number | null>(null)
  const [amount, setAmount] = useState('')
  const [method, setMethod] = useState('')
  const fileRef = useRef<HTMLInputElement>(null)
  const ok = (d: PaymentDetail) => { setError(null); setPaying(null); refresh(d) }
  const bad = (e: Error) => setError(e.message)
  const mark = useMutation({ mutationFn: (pid: number) => paymentMarkPaid(id, pid, { amount: amount || undefined, method: method || undefined }), onSuccess: ok, onError: bad })
  const waive = useMutation({ mutationFn: (pid: number) => paymentWaive(id, pid), onSuccess: ok, onError: bad })
  const undo = useMutation({ mutationFn: (cid: number) => paymentUndo(cid), onSuccess: ok, onError: bad })
  const sugg = useMutation({ mutationFn: (v: { sid: number; how: 'accept' | 'dismiss' }) => paymentSuggestion(v.sid, v.how), onSuccess: ok, onError: bad })
  const cancel = useMutation({ mutationFn: () => paymentUpdate(id, { cancel: true }), onSuccess: ok, onError: bad })
  const proof = useMutation({ mutationFn: (f: File) => paymentUploadProof(id, f), onSuccess: ok, onError: bad })
  const d = q.data
  if (!d) return <div style={{ color: MUTED, fontSize: 13 }}>Loading…</div>
  const open = d.suggestions.filter((s) => s.state === 'open')
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12, maxWidth: 760 }}>
      <div style={{ display: 'flex', gap: 10, alignItems: 'baseline', flexWrap: 'wrap' }}>
        <h2 style={{ fontSize: 20, fontWeight: 700, color: INK, margin: 0 }}>{d.customer_name ?? 'Customer'}</h2>
        {d.job_number && <span style={{ color: MUTED, fontSize: 13 }}>Job #{d.job_number} · {d.board}</span>}
        <Chip s={d.state} />
        {d.state !== 'cancelled' && <button type="button" style={{ ...btn, marginLeft: 'auto' }} onClick={() => cancel.mutate()}>Cancel agreement</button>}
      </div>
      <div style={{ fontSize: 14, color: INK, fontVariantNumeric: 'tabular-nums' }}>
        {d.quoted_cents != null && <>Quoted <strong>{money(d.quoted_cents)}</strong> · </>}
        {d.agreed_cents != null ? <>Agreed <strong>{money(d.agreed_cents)}</strong></> : 'Not agreed yet'}
        {d.terms && <span style={{ color: MUTED }}> · {d.terms}</span>}
        {d.description && <div style={{ color: MUTED, fontSize: 13, marginTop: 2 }}>{d.description}</div>}
      </div>
      {error && <div role="alert" style={{ color: RED, fontSize: 13 }}>{error}</div>}
      {open.length > 0 && (
        <div style={{ ...card, borderColor: BLUE, display: 'flex', flexDirection: 'column', gap: 8 }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: BLUE }}>To confirm</div>
          {open.map((s) => (
            <div key={s.id} style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', fontSize: 13 }}>
              <span style={{ flex: '1 1 260px' }}>{s.evidence}{s.amount_cents ? ` Mark ${money(s.amount_cents)} paid?` : ''}</span>
              {s.proof_id && <a style={{ color: BLUE }} href={d.proofs.find((p) => p.id === s.proof_id)?.url} target="_blank" rel="noreferrer">See the picture</a>}
              <button type="button" style={primary} onClick={() => sugg.mutate({ sid: s.id, how: 'accept' })}>Confirm</button>
              <button type="button" style={btn} onClick={() => sugg.mutate({ sid: s.id, how: 'dismiss' })}>Dismiss</button>
            </div>
          ))}
        </div>
      )}
      <div style={{ ...card, padding: 0 }}>
        {d.parts.length === 0 && <div style={{ padding: 12, fontSize: 13, color: MUTED }}>No payment parts yet — set the agreed amount and how it is paid.</div>}
        {d.parts.map((p, i) => (
          <div key={p.id} style={{ padding: '10px 14px', borderTop: i ? `1px solid ${LINE}` : 'none', display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
            <div style={{ flex: '1 1 220px' }}>
              <div style={{ fontSize: 14, fontWeight: 600, color: INK }}>{p.label} · {money(p.amount_cents)}</div>
              <div style={{ fontSize: 12, color: MUTED }}>{partLine(p)} · {DUE_RULE[p.due_rule] ?? p.due_rule}</div>
            </div>
            <Chip s={p.status} />
            {!['paid', 'waived', 'cancelled'].includes(p.status) && (paying === p.id ? (
              <span style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                <input aria-label="Amount paid" id={`pay-amount-${p.id}`} style={{ ...input, width: 90 }} placeholder={String(p.amount_cents / 100)} value={amount} onChange={(e) => setAmount(e.target.value)} />
                <input aria-label="How it was paid" id={`pay-method-${p.id}`} style={{ ...input, width: 100 }} placeholder="Zelle, check…" value={method} onChange={(e) => setMethod(e.target.value)} />
                <button type="button" style={primary} onClick={() => mark.mutate(p.id)}>Save</button>
                <button type="button" style={btn} onClick={() => setPaying(null)}>×</button>
              </span>
            ) : <>
              <button type="button" style={btn} onClick={() => { setPaying(p.id); setAmount(''); setMethod('') }}>Mark paid</button>
              <button type="button" style={btn} onClick={() => waive.mutate(p.id)}>Waive</button>
            </>)}
          </div>
        ))}
      </div>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <span style={{ fontSize: 13, fontWeight: 600, color: INK }}>Proof</span>
        <input ref={fileRef} type="file" accept="image/*" style={{ display: 'none' }} id="pay-proof-file"
          onChange={(e) => { const f = e.target.files?.[0]; if (f) proof.mutate(f); e.target.value = '' }} />
        <button type="button" style={btn} onClick={() => fileRef.current?.click()}>Add a screenshot</button>
      </div>
      {d.proofs.length > 0 && <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        {d.proofs.map((p) => (
          <a key={p.id} href={p.url} target="_blank" rel="noreferrer" title={p.state.replace('_', ' ')}>
            <img src={p.url} alt="Payment proof" loading="lazy" style={{ width: 96, height: 96, objectFit: 'cover', borderRadius: 6,
              border: `2px solid ${p.state === 'to_review' ? BLUE : LINE}` }} />
          </a>
        ))}
      </div>}
      <div>
        <div style={{ fontSize: 13, fontWeight: 600, color: INK, marginBottom: 6 }}>History</div>
        {d.history.map((h) => (
          <div key={h.id} style={{ fontSize: 13, color: h.undone_at ? MUTED : INK, padding: '3px 0', display: 'flex', gap: 8 }}>
            <span style={{ color: MUTED, whiteSpace: 'nowrap' }}>{h.created_at ? new Date(h.created_at).toLocaleString() : ''}</span>
            <span style={{ textDecoration: h.undone_at ? 'line-through' : 'none' }}>{h.summary} <span style={{ color: MUTED }}>({h.via})</span></span>
            {h.undoable && <button type="button" style={{ ...btn, height: 22 }} onClick={() => undo.mutate(h.id)}>Undo</button>}
          </div>
        ))}
      </div>
    </div>
  )
}

function Chat({ onOpen }: { onOpen: (id: number) => void }) {
  const qc = useQueryClient()
  const hist = useQuery({ queryKey: ['payments-chat'], queryFn: paymentChatHistory })
  const [msgs, setMsgs] = useState<PaymentChatMessage[]>([])
  const [text, setText] = useState('')
  const end = useRef<HTMLDivElement>(null)
  useEffect(() => { if (hist.data) setMsgs(hist.data.messages) }, [hist.data])
  useEffect(() => { end.current?.scrollIntoView({ block: 'end' }) }, [msgs])
  const sendIt = useMutation({
    mutationFn: (t: string) => paymentChatSend(t),
    onMutate: (t) => setMsgs((m) => [...m, { id: -Date.now(), role: 'user', text: t, error: false, created_at: null, changes: [] }]),
    onSuccess: (r) => { setMsgs((m) => [...m, ...r.messages]); qc.invalidateQueries({ queryKey: ['payments'] }); qc.invalidateQueries({ queryKey: ['payment'] }) },
  })
  const undo = useMutation({ mutationFn: (cid: number) => paymentUndo(cid),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['payments'] }); qc.invalidateQueries({ queryKey: ['payment'] }); hist.refetch() } })
  return (
    <div style={{ borderLeft: `1px solid ${LINE}`, background: '#fff', display: 'flex', flexDirection: 'column', minHeight: 0 }}>
      <div style={{ padding: '10px 12px', borderBottom: `1px solid ${LINE}`, fontSize: 14, fontWeight: 600, color: INK }}>
        Payments assistant
        <div style={{ fontSize: 12, fontWeight: 400, color: MUTED }}>“Smith paid the 50% deposit, $1,500 by Zelle” · “we quoted Jones $8,000”</div>
      </div>
      <div style={{ flex: 1, overflowY: 'auto', padding: 12, display: 'flex', flexDirection: 'column', gap: 8 }}>
        {msgs.map((m) => (
          <div key={m.id} style={{ alignSelf: m.role === 'user' ? 'flex-end' : 'flex-start', maxWidth: '90%',
            background: m.role === 'user' ? 'rgb(239,244,255)' : 'rgb(249,250,251)', borderRadius: 8, padding: '6px 10px',
            fontSize: 13, color: m.error ? RED : INK, whiteSpace: 'pre-wrap' }}>
            {m.text}
            {m.changes.map((c) => (
              <div key={c.id} style={{ fontSize: 12, marginTop: 4, color: c.undone_at ? MUTED : 'rgb(2,122,72)' }}>
                {c.undone_at ? 'Undone: ' : 'Done: '}{c.summary}{' '}
                {c.agreement_id != null && <button type="button" style={{ color: BLUE, fontSize: 12 }} onClick={() => onOpen(c.agreement_id!)}>Open</button>}
                {!c.undone_at && <> · <button type="button" style={{ color: BLUE, fontSize: 12 }} onClick={() => undo.mutate(c.id)}>Undo</button></>}
              </div>
            ))}
          </div>
        ))}
        {sendIt.isPending && <div style={{ fontSize: 12, color: MUTED }}>Working…</div>}
        <div ref={end} />
      </div>
      <form style={{ display: 'flex', gap: 6, padding: 10, borderTop: `1px solid ${LINE}` }}
        onSubmit={(e) => { e.preventDefault(); const t = text.trim(); if (t) { sendIt.mutate(t); setText('') } }}>
        <input id="payments-chat-input" aria-label="Message the payments assistant" style={input} value={text}
          onChange={(e) => setText(e.target.value)} placeholder="Tell it what was agreed or paid…" />
        <MicButton value={text} onChange={setText} disabled={sendIt.isPending} size={36} />
        <button type="submit" style={primary} disabled={sendIt.isPending}>Send</button>
      </form>
    </div>
  )
}
