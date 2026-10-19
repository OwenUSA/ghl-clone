import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { BLUE, INK, LINE } from '../ai/aiUi'
import { autoReplyStatus, saveAutoReply, type AutoReplyRow } from '../../lib/api'
import { whenLabel } from '../../lib/accountTime'

const MUTED = 'rgb(102,112,133)'
const RED = 'rgb(180,35,24)'
const GREEN = 'rgb(2,122,72)'
const AMBER = 'rgb(181,71,8)'
const CONFIRM = 'TURN ON'
const card: React.CSSProperties = { background: '#fff', border: `1px solid ${LINE}`,
  borderRadius: 8, padding: '12px 16px' }
const field = { border: `1px solid ${LINE}`, borderRadius: 6, padding: '3px 6px', fontSize: 13 }

const KIND: Record<AutoReplyRow['kind'], string> = {
  text: 'text', missed_call: 'missed call', voicemail: 'voicemail' }

function when(iso: string | null): string {
  return iso ? whenLabel(new Date(iso)) : '—'
}

function Row({ r }: { r: AutoReplyRow }) {
  const color = r.state === 'sent' ? GREEN : r.state === 'skipped' ? MUTED : RED
  return (
    <tr style={{ borderTop: `1px solid ${LINE}`, verticalAlign: 'top' }}>
      <td style={{ padding: '6px 8px 6px 0', whiteSpace: 'nowrap' }}>{when(r.sent_at ?? r.at)}</td>
      <td style={{ padding: '6px 8px 6px 0' }}>
        {r.customer ?? 'Unknown number'}{r.job_number ? ` · #${r.job_number}` : ''}
        <div style={{ color: MUTED, fontSize: 12 }}>
          {KIND[r.kind]} waiting since {when(r.waiting_since)}</div></td>
      <td style={{ padding: '6px 8px 6px 0', color, fontWeight: 500 }}>{r.state}</td>
      <td style={{ padding: '6px 0' }}>
        {r.reply && r.state !== 'skipped' && <div>“{r.reply}”</div>}
        <div style={{ color: MUTED, fontSize: 12 }}>
          {[r.state === 'skipped' ? r.reason : null,
            r.written_by === 'template' ? `fixed wording (${r.reason ?? 'AI not used'})` : null,
            r.after_hours ? 'after hours' : null,
            r.upset ? 'customer seemed upset' : null,
            r.needs_person && !r.upset ? 'needs a person' : null,
            r.note === 'added' ? 'note added on the job' : r.note === 'no_job' ? 'no job to note'
              : r.note === 'failed' ? 'the note failed' : null,
            r.error].filter(Boolean).join(' · ')}</div>
      </td>
    </tr>
  )
}

/** Dispatch → Settings: the auto-reply's switch, its settings and what it did. */
export function AutoReplySection({ admin }: { admin: boolean }) {
  const qc = useQueryClient()
  const q = useQuery({ queryKey: ['autoreply'], queryFn: autoReplyStatus, refetchInterval: 30_000 })
  const save = useMutation({ mutationFn: saveAutoReply,
    onSuccess: (d) => { qc.setQueryData(['autoreply'], d); setTyping(null) } })
  const [typing, setTyping] = useState<string | null>(null)
  if (!q.data) return null
  const s = q.data
  const live = s.server_enabled && s.enabled
  return (
    <div style={card}>
      <div style={{ fontSize: 14, fontWeight: 600, color: INK }}>Auto-reply to unanswered Zuper texts</div>
      <div style={{ fontSize: 13, color: MUTED, margin: '4px 0 10px' }}>
        When a customer's text or call to {s.sender} has had no answer for {s.wait_minutes} minutes,
        it sends one polite text from {s.sender}, reading their Zuper conversation, Quo calls and
        texts and the Zuper job first, and adds a private note on the job. It never mentions money,
        never says “approved”, and texts a number at most once a day. {s.sent_today} of{' '}
        {s.daily_cap} sent today.</div>
      {!s.server_enabled && <div style={{ fontSize: 13, color: AMBER, marginBottom: 8 }}>
        Switched off on the server (ZUPER_AUTOREPLY_ENABLED). Nothing is read or sent.</div>}
      <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap', fontSize: 13 }}>
        <button type="button" disabled={!admin || save.isPending}
          onClick={() => save.mutate({ enabled: !s.enabled })}
          style={{ color: '#fff', background: s.enabled ? RED : BLUE, borderRadius: 6,
            padding: '4px 12px', fontWeight: 500 }}>
          {s.enabled ? 'Switch auto-reply off' : 'Switch auto-reply on'}</button>
        <span style={{ color: live ? GREEN : MUTED }}>
          {live ? (s.test_only ? 'On — test customer only' : 'On — answering customers') : 'Off'}</span>
        {s.test_only ? (
          typing === null ? (
            <button type="button" disabled={!admin} onClick={() => setTyping('')}
              style={{ ...field, color: RED }}>Answer real customers…</button>
          ) : (
            <span>Type <b>{CONFIRM}</b>{' '}
              <input autoFocus value={typing} onChange={(e) => setTyping(e.target.value)}
                style={{ ...field, width: 90 }} />{' '}
              <button type="button" disabled={typing.trim() !== CONFIRM || save.isPending}
                onClick={() => save.mutate({ test_only: false, confirm: typing.trim() })}
                style={{ ...field, color: '#fff', background: RED }}>Confirm</button>{' '}
              <button type="button" onClick={() => setTyping(null)} style={field}>Cancel</button></span>
          )
        ) : (
          <button type="button" disabled={!admin} onClick={() => save.mutate({ test_only: true })}
            style={field}>Back to test customer only</button>
        )}
      </div>
      <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap', fontSize: 13,
        marginTop: 10 }}>
        <label>Writes the text{' '}
          <select disabled={!admin} value={s.connection_id ?? ''} style={field}
            onChange={(e) => e.target.value && save.mutate({ connection_id: Number(e.target.value) })}>
            <option value="">— fixed wording only —</option>
            {s.connections.map((c) => <option key={c.id} value={c.id}>{c.name} ({c.provider})</option>)}
          </select></label>
        <label>Model <input disabled={!admin} defaultValue={s.model ?? ''} placeholder="connection's default"
          style={field} onBlur={(e) => e.target.value !== (s.model ?? '') && save.mutate({ model: e.target.value })} /></label>
        <label>Transcribes Zuper calls{' '}
          <select disabled={!admin} value={s.transcribe_connection_id ?? ''} style={field}
            onChange={(e) => save.mutate(e.target.value
              ? { transcribe_connection_id: Number(e.target.value) } : { clear_transcribe: true })}>
            <option value="">— summaries only —</option>
            {s.connections.filter((c) => c.provider === 'openai').map((c) =>
              <option key={c.id} value={c.id}>{c.name}</option>)}
          </select></label>
        <label>Daily limit <input disabled={!admin} type="number" defaultValue={s.daily_cap}
          style={{ ...field, width: 70 }}
          onBlur={(e) => Number(e.target.value) !== s.daily_cap && save.mutate({ daily_cap: Number(e.target.value) })} /></label>
      </div>
      <div style={{ fontSize: 12, color: s.last_error ? AMBER : MUTED, marginTop: 8 }}>
        Last check {when(s.last_run_at)}{s.last_error ? ` — ${s.last_error}` : ''}</div>
      {save.isError && <div style={{ color: RED, fontSize: 13 }}>{(save.error as Error).message}</div>}
      {s.log.length > 0 && (
        <div style={{ overflowX: 'auto', marginTop: 10 }}>
          <table style={{ fontSize: 13, borderCollapse: 'collapse', width: '100%' }}>
            <thead><tr style={{ color: MUTED, textAlign: 'left' }}>
              <th>When</th><th>Customer</th><th>What</th><th>Text</th></tr></thead>
            <tbody>{s.log.map((r) => <Row key={r.id} r={r} />)}</tbody>
          </table>
        </div>
      )}
    </div>
  )
}
