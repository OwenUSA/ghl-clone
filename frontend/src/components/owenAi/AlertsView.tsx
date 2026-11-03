import { useState } from 'react'
import type { OwenAiAlert, OwenAiAlertStatus } from '../../lib/api'
import { SEVERITY_LABEL, groupAlerts, relativeDay, type Severity } from '../../lib/owenAi'
import { BLUE, INK, LINE, MONO, MUTED, RED, SEVERITY_COLORS, chipBtn } from './tokens'

const FILTERS: { value: OwenAiAlertStatus; label: string }[] = [
  { value: 'open', label: 'Open' }, { value: 'resolved', label: 'Resolved' }, { value: 'dismissed', label: 'Dismissed' },
]

/**
 * The scheduled audit's findings (2026-11-03): open alerts grouped Urgent / Today / Watch, each
 * with what to do, the evidence, and "Open job chat" / "Dismiss". Resolved and dismissed ones are
 * history: their resolution and when they closed, no buttons. The page polls the open list.
 */
export function AlertsView({ status, onStatus, alerts, loading, error, onOpenJob, opening, onDismiss,
  dismissingId, dismissError, problem }: {
  status: OwenAiAlertStatus; onStatus: (s: OwenAiAlertStatus) => void
  alerts: OwenAiAlert[]; loading: boolean; error: string | null
  onOpenJob: (a: OwenAiAlert) => void; opening: boolean
  onDismiss: (a: OwenAiAlert, why: string) => Promise<boolean>
  dismissingId: number | null; dismissError: { id: number; message: string } | null
  /** Opening the job's chat failed. */
  problem: string | null
}) {
  const groups = groupAlerts(alerts)
  return (
    <div style={{ flex: 1, overflowY: 'auto' }}>
      <div style={{ maxWidth: 980, margin: '0 auto', padding: '12px 12px 24px' }}>
        <div role="tablist" aria-label="Alert status" style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 8 }}>
          {FILTERS.map((f) => (
            <button key={f.value} type="button" role="tab" aria-selected={status === f.value}
              onClick={() => onStatus(f.value)}
              style={{ ...chipBtn, ...(status === f.value ? { color: '#fff', background: INK, borderColor: INK } : {}) }}>
              {f.label}</button>
          ))}
        </div>
        {problem && <div role="alert" style={{ color: RED, fontSize: 13, marginBottom: 6 }}>{problem}</div>}
        {loading && <div style={{ color: MUTED, fontSize: 13 }}>Loading alerts…</div>}
        {error && <div role="alert" style={{ color: RED, fontSize: 13 }}>Could not load alerts: {error}</div>}
        {!loading && !error && alerts.length === 0 && (
          <div style={{ color: MUTED, fontSize: 14, textAlign: 'center', marginTop: 40 }}>
            {status === 'open' ? 'Nothing open — the last audit found no problems.'
              : status === 'resolved' ? 'No resolved alerts yet.' : 'No dismissed alerts.'}
          </div>
        )}
        {groups.map((g) => (
          <section key={g.severity} aria-label={SEVERITY_LABEL[g.severity]} style={{ marginTop: 14 }}>
            <div style={{ fontSize: 11, fontWeight: 600, color: SEVERITY_COLORS[g.severity].fg, padding: '0 2px 6px',
              textTransform: 'uppercase', letterSpacing: 0.4 }}>
              {SEVERITY_LABEL[g.severity]} · {g.alerts.length}</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {g.alerts.map((a) => (
                <AlertCard key={a.id} a={a} onOpenJob={() => onOpenJob(a)} opening={opening}
                  onDismiss={(why) => onDismiss(a, why)} dismissing={dismissingId === a.id}
                  dismissError={dismissError?.id === a.id ? dismissError.message : null} />
              ))}
            </div>
          </section>
        ))}
      </div>
    </div>
  )
}

function SeverityChip({ severity }: { severity: string }) {
  const s = (severity in SEVERITY_COLORS ? severity : 'watch') as Severity
  const c = SEVERITY_COLORS[s]
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, padding: '1px 8px', borderRadius: 10,
      fontSize: 11, fontWeight: 600, whiteSpace: 'nowrap', color: c.fg, background: c.bg }}>
      <span aria-hidden="true" style={{ width: 6, height: 6, borderRadius: 3, background: c.dot }} />
      {SEVERITY_LABEL[s]}
    </span>
  )
}

function AlertCard({ a, onOpenJob, opening, onDismiss, dismissing, dismissError }: {
  a: OwenAiAlert; onOpenJob: () => void; opening: boolean
  onDismiss: (why: string) => Promise<boolean>; dismissing: boolean; dismissError: string | null
}) {
  const [evidence, setEvidence] = useState(false)
  const [asking, setAsking] = useState(false)
  const [why, setWhy] = useState('')
  const open = a.status === 'open'
  const submit = () => {
    const w = why.trim()
    if (!w || dismissing) return
    void onDismiss(w).then((ok) => { if (ok) { setAsking(false); setWhy('') } })
  }
  return (
    <article style={{ background: '#fff', border: `1px solid ${LINE}`, borderRadius: 10, padding: '10px 12px',
      borderLeft: `3px solid ${SEVERITY_COLORS[(a.severity in SEVERITY_COLORS ? a.severity : 'watch') as Severity].dot}`,
      minWidth: 0 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <SeverityChip severity={a.severity} />
        <span style={{ fontSize: 13, fontWeight: 600, color: INK }}>Job #{a.job_number ?? '?'}</span>
        <span style={{ fontSize: 11, color: MUTED, fontFamily: MONO }} title="Audit rule">{a.rule_id}</span>
      </div>
      <div style={{ fontSize: 14, color: INK, marginTop: 6, lineHeight: '20px', wordBreak: 'break-word' }}>{a.title}</div>
      {a.action && (
        <div style={{ fontSize: 13, color: INK, marginTop: 4, lineHeight: '19px', whiteSpace: 'pre-wrap',
          wordBreak: 'break-word' }}><strong>Do:</strong> {a.action}</div>
      )}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginTop: 6, fontSize: 12,
        color: MUTED }}>
        {a.first_seen_at && <span title={a.first_seen_at}>since {relativeDay(a.first_seen_at)}</span>}
        <span>seen {a.times_seen}×</span>
        {!open && a.closed_at && (
          <span title={a.closed_at}>{a.status === 'resolved' ? 'resolved' : 'dismissed'} {relativeDay(a.closed_at)}</span>
        )}
        {a.evidence && (
          <button type="button" onClick={() => setEvidence(!evidence)} aria-expanded={evidence}
            style={{ fontSize: 12, color: MUTED, textDecoration: 'underline', textUnderlineOffset: 2 }}>
            {evidence ? 'Hide evidence' : 'Evidence'}</button>
        )}
      </div>
      {evidence && a.evidence && (
        <div style={{ marginTop: 6, padding: '6px 8px', background: 'rgb(249,250,251)', border: `1px solid ${LINE}`,
          borderRadius: 6, fontSize: 12, lineHeight: '18px', color: 'rgb(52,64,84)', whiteSpace: 'pre-wrap',
          wordBreak: 'break-word', maxHeight: 320, overflowY: 'auto' }}>{a.evidence}</div>
      )}
      {!open && a.resolution && (
        <div style={{ marginTop: 6, fontSize: 12, lineHeight: '18px', color: 'rgb(52,64,84)', whiteSpace: 'pre-wrap',
          wordBreak: 'break-word' }}>{a.resolution}</div>
      )}
      {open && (
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 8 }}>
          <button type="button" onClick={onOpenJob} disabled={opening}
            style={{ ...chipBtn, color: BLUE, borderColor: 'rgb(178,204,255)' }}>Open job chat</button>
          {!asking && (
            <button type="button" onClick={() => setAsking(true)} style={chipBtn}>Dismiss</button>
          )}
        </div>
      )}
      {open && asking && (
        <form onSubmit={(e) => { e.preventDefault(); submit() }} style={{ display: 'flex', gap: 6, marginTop: 6 }}>
          <input autoFocus value={why} onChange={(e) => setWhy(e.target.value)} maxLength={1000}
            placeholder="Why? e.g. handled by phone" aria-label="Reason for dismissing"
            onKeyDown={(e) => { if (e.key === 'Escape') { setAsking(false); setWhy('') } }}
            style={{ flex: 1, minWidth: 0, fontSize: 13, color: INK, border: `1px solid ${BLUE}`, borderRadius: 8,
              padding: '5px 8px', outline: 'none', background: '#fff' }} />
          <button type="submit" disabled={!why.trim() || dismissing}
            style={{ fontSize: 12, color: '#fff', background: why.trim() && !dismissing ? BLUE : 'rgb(152,162,179)',
              borderRadius: 8, padding: '0 10px' }}>{dismissing ? 'Dismissing…' : 'Dismiss'}</button>
          <button type="button" onClick={() => { setAsking(false); setWhy('') }}
            style={{ fontSize: 12, color: MUTED, padding: '0 4px' }}>Cancel</button>
        </form>
      )}
      {dismissError && <div role="alert" style={{ color: RED, fontSize: 12, marginTop: 4 }}>{dismissError}</div>}
    </article>
  )
}
