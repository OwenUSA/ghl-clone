import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { owenAiApproveLead, owenAiLeads, owenAiRejectLead, type OwenAiLead } from '../../lib/api'
import { relativeDay } from '../../lib/owenAi'
import { BLUE, INK, LINE, MUTED, RED, chipBtn } from './tokens'

export const LEADS_KEY = ['owen-ai-leads'] as const

/**
 * New customers (2026-11-19): leads Owen pasted in a chat, which Owen AI turned into a customer +
 * a job. Nothing is in Zuper until Approve — then the server creates the customer (or uses the
 * existing one Owen named), the job in its board's first column (no visit, no technician) and the
 * lead's message as a private note, reads the job back and says its number. Nothing is sent to the
 * customer. Shown at the top of the Proposals tab; the tab's badge counts these too.
 */
export function LeadsSection({ onDecided }: { onDecided: (threadId: number | null) => void }) {
  const qc = useQueryClient()
  const q = useQuery({ queryKey: LEADS_KEY, queryFn: owenAiLeads, refetchInterval: 10_000 })
  const [err, setErr] = useState<{ id: number; message: string } | null>(null)
  const done = (l: OwenAiLead) => { qc.invalidateQueries({ queryKey: LEADS_KEY }); onDecided(l.thread_id) }
  const approve = useMutation({
    mutationFn: (id: number) => owenAiApproveLead(id),
    onMutate: () => setErr(null),
    onSuccess: (l) => { done(l); if (l.status === 'failed') setErr({ id: l.id, message: l.error ?? 'Zuper refused it.' }) },
    onError: (e, id) => { setErr({ id, message: (e as Error).message || 'Could not approve it.' }); qc.invalidateQueries({ queryKey: LEADS_KEY }) },
  })
  const reject = useMutation({
    mutationFn: (v: { id: number; why: string }) => owenAiRejectLead(v.id, v.why),
    onMutate: () => setErr(null),
    onSuccess: done,
    onError: (e, v) => setErr({ id: v.id, message: (e as Error).message || 'Could not reject it.' }),
  })
  const leads = q.data?.leads ?? []
  if (!leads.length) return null
  const pending = leads.filter((l) => l.status === 'pending')
  const recent = leads.filter((l) => l.status !== 'pending').slice(0, 8)
  const busyId = approve.isPending ? approve.variables ?? null : reject.isPending ? reject.variables?.id ?? null : null
  const sentence = q.data?.sentence ?? null
  return (
    <section aria-label="New customers" style={{ marginBottom: 18 }}>
      <div style={sectionHead}>New customers{pending.length ? ` · ${pending.length} waiting for you` : ''}</div>
      {sentence && pending.length > 0 && (
        <div role="status" style={{ fontSize: 13, color: 'rgb(181,71,8)', background: 'rgb(255,250,235)',
          border: '1px solid rgb(254,223,137)', borderRadius: 8, padding: '6px 10px', marginBottom: 8 }}>
          Approve is switched off: {sentence}</div>
      )}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {pending.map((l) => (
          <PendingLead key={l.id} l={l} blocked={!!sentence} busy={busyId === l.id}
            onApprove={() => approve.mutateAsync(l.id).then((r) => r.status === 'created', () => false)}
            onReject={(why) => reject.mutateAsync({ id: l.id, why }).then(() => true, () => false)}
            error={err?.id === l.id ? err.message : null} />
        ))}
        {recent.map((l) => <DecidedLead key={l.id} l={l} error={err?.id === l.id ? err.message : null} />)}
      </div>
    </section>
  )
}

const sectionHead: React.CSSProperties = { fontSize: 11, fontWeight: 600, color: MUTED, padding: '0 2px 6px',
  textTransform: 'uppercase', letterSpacing: 0.4 }
const card: React.CSSProperties = { background: '#fff', border: `1px solid ${LINE}`, borderRadius: 10,
  padding: '10px 12px', minWidth: 0 }

function phone(p: string) {
  return p && p.length === 10 ? `(${p.slice(0, 3)}) ${p.slice(3, 6)}-${p.slice(6)}` : p
}

function where(l: OwenAiLead) {
  const a = l.address
  return [a.street, a.city, [a.state, a.zip].filter(Boolean).join(' ')].filter(Boolean).join(', ')
}

function PendingLead({ l, blocked, busy, onApprove, onReject, error }: {
  l: OwenAiLead; blocked: boolean; busy: boolean
  onApprove: () => Promise<boolean>; onReject: (why: string) => Promise<boolean>; error: string | null
}) {
  const [confirming, setConfirming] = useState(false)
  const [rejecting, setRejecting] = useState(false)
  const [why, setWhy] = useState('')
  const [showNote, setShowNote] = useState(false)
  const submitReject = () => {
    const w = why.trim()
    if (!w || busy) return
    void onReject(w).then((ok) => { if (ok) { setRejecting(false); setWhy('') } })
  }
  return (
    <article style={{ ...card, borderLeft: `3px solid ${BLUE}` }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 15, fontWeight: 600, color: INK }}>{l.customer_name}</span>
        <span style={{ fontSize: 12, color: MUTED }}>
          {l.existing_customer ? 'existing Zuper customer' : 'new customer'}</span>
        {l.created_at && <span style={{ fontSize: 12, color: MUTED }} title={l.created_at}>{relativeDay(l.created_at)}</span>}
      </div>
      <div style={{ fontSize: 13, color: INK, marginTop: 4 }}>
        {phone(l.phone)}{l.email ? ` · ${l.email}` : ' · no email'}</div>
      <div style={{ fontSize: 13, color: INK }}>{where(l) || 'No address'}</div>
      <div style={{ fontSize: 13, color: MUTED, marginTop: 4 }}>
        Job <strong style={{ color: INK }}>{l.job_title}</strong> on {l.board} / {l.first_column ?? 'first column'} —
        no visit, no technician, with the lead as a private note.</div>
      {l.duplicates.length > 0 && !l.existing_customer && (
        <div style={{ fontSize: 12, color: 'rgb(181,71,8)', marginTop: 4 }}>
          Owen AI was told these are different people: {l.duplicates.map((d) => `${d.name || '?'} (${(d.why ?? []).join(', ')})`).join('; ')}</div>
      )}
      <button type="button" onClick={() => setShowNote(!showNote)} aria-expanded={showNote}
        style={{ fontSize: 12, color: BLUE, marginTop: 4, padding: 0 }}>{showNote ? 'Hide the note' : 'Show the note'}</button>
      {showNote && <div style={{ fontSize: 13, color: INK, marginTop: 4, whiteSpace: 'pre-wrap',
        background: 'rgb(249,250,251)', borderRadius: 8, padding: '6px 8px' }}>{l.note}</div>}
      {!confirming && !rejecting && (
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 10 }}>
          <button type="button" onClick={() => setConfirming(true)} disabled={blocked || busy}
            title={blocked ? 'Approve is switched off on this server' : undefined}
            style={{ fontSize: 13, color: '#fff', background: blocked || busy ? 'rgb(152,162,179)' : BLUE,
              borderRadius: 8, padding: '5px 14px' }}>Approve</button>
          <button type="button" onClick={() => setRejecting(true)} disabled={busy} style={chipBtn}>Reject</button>
        </div>
      )}
      {confirming && (
        <div role="group" aria-label="Confirm approve" style={{ marginTop: 10, padding: '8px 10px', borderRadius: 8,
          background: 'rgb(239,244,255)', border: '1px solid rgb(178,204,255)', fontSize: 13, color: INK }}>
          Create {l.existing_customer ? 'a job for this customer' : <>this <strong>customer and job</strong></>} in
          Zuper? Nothing is sent to the customer.
          <div style={{ display: 'flex', gap: 6, marginTop: 8 }}>
            <button type="button" disabled={busy}
              onClick={() => { void onApprove().then(() => setConfirming(false)) }}
              style={{ fontSize: 13, color: '#fff', background: busy ? 'rgb(152,162,179)' : BLUE, borderRadius: 8,
                padding: '5px 14px' }}>{busy ? 'Creating in Zuper…' : 'Yes, create it'}</button>
            <button type="button" disabled={busy} onClick={() => setConfirming(false)}
              style={{ fontSize: 12, color: MUTED, padding: '0 6px' }}>Cancel</button>
          </div>
        </div>
      )}
      {rejecting && (
        <form onSubmit={(e) => { e.preventDefault(); submitReject() }} style={{ display: 'flex', gap: 6, marginTop: 10 }}>
          <input autoFocus value={why} onChange={(e) => setWhy(e.target.value)} maxLength={1000}
            placeholder="Why? e.g. already a customer" aria-label="Reason for rejecting"
            onKeyDown={(e) => { if (e.key === 'Escape') { setRejecting(false); setWhy('') } }}
            style={{ flex: 1, minWidth: 0, fontSize: 13, color: INK, border: `1px solid ${BLUE}`, borderRadius: 8,
              padding: '5px 8px', outline: 'none', background: '#fff' }} />
          <button type="submit" disabled={!why.trim() || busy}
            style={{ fontSize: 12, color: '#fff', background: why.trim() && !busy ? RED : 'rgb(152,162,179)',
              borderRadius: 8, padding: '0 10px' }}>Reject</button>
          <button type="button" onClick={() => { setRejecting(false); setWhy('') }}
            style={{ fontSize: 12, color: MUTED, padding: '0 4px' }}>Cancel</button>
        </form>
      )}
      {error && <div role="alert" style={{ color: RED, fontSize: 12, marginTop: 6 }}>{error}</div>}
    </article>
  )
}

function DecidedLead({ l, error }: { l: OwenAiLead; error: string | null }) {
  const label = l.status === 'created' ? `Created — job #${l.job_number ?? '?'}`
    : l.status === 'failed' ? 'Failed' : l.status === 'creating' ? 'Creating…' : 'Rejected'
  const color = l.status === 'created' ? 'rgb(6,118,71)' : l.status === 'failed' ? RED : MUTED
  return (
    <div style={{ ...card, padding: '7px 12px', fontSize: 13, color: INK }}>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'baseline' }}>
        <strong>{l.customer_name}</strong>
        <span style={{ color: MUTED }}>{l.board}</span>
        <span style={{ color, fontWeight: 600 }}>{label}</span>
        {l.job_url && l.status === 'created' && (
          <a href={l.job_url} target="_blank" rel="noreferrer" style={{ color: BLUE, fontSize: 12 }}>Open in Zuper</a>)}
        {l.decided_at && <span style={{ color: MUTED, fontSize: 12 }} title={l.decided_at}>{relativeDay(l.decided_at)}</span>}
      </div>
      {(l.status === 'failed' || l.status === 'rejected') && l.error && (
        <div style={{ fontSize: 12, color: l.status === 'failed' ? RED : MUTED, marginTop: 2 }}>{l.error}</div>)}
      {error && error !== l.error && <div role="alert" style={{ color: RED, fontSize: 12, marginTop: 2 }}>{error}</div>}
    </div>
  )
}
