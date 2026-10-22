import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import {
  approveInspectionReport, discardInspectionReport, editInspectionReport, inspectionReport,
  inspectionReports, rebuildInspectionReport, type InspectionReportDetail,
} from '../../lib/api'
import { BLUE, INK, LINE } from '../ai/aiUi'

const MUTED = 'rgb(102,112,133)'
const RED = 'rgb(180,35,24)'
const COPPER = 'rgb(176,90,36)'
const STATE_LABEL: Record<string, string> = {
  building: 'Building…', queued: 'Rebuilding…', draft: 'Draft — to review', approved: 'Approved',
  discarded: 'Discarded', failed: 'Failed',
}
const btn: React.CSSProperties = { height: 30, padding: '0 12px', borderRadius: 4, fontSize: 13,
  fontWeight: 500, border: `1px solid ${LINE}`, background: '#fff', color: INK }
const primary: React.CSSProperties = { ...btn, background: BLUE, borderColor: BLUE, color: '#fff' }
const input: React.CSSProperties = { width: '100%', border: `1px solid ${LINE}`, borderRadius: 4,
  padding: '6px 8px', fontSize: 14, color: INK, fontFamily: 'inherit' }

/**
 * Dispatch → Reports (2026-10-21, app/inspection_report): the post-inspection report drafts.
 * Review, edit, approve. Approving records who checked it — NOTHING is sent to the customer.
 */
export function InspectionReports({ isAdmin, openId }: { isAdmin: boolean; openId: number | null }) {
  const [selected, setSelected] = useState<number | null>(openId)
  const list = useQuery({ queryKey: ['inspection-reports'], queryFn: () => inspectionReports(),
    refetchInterval: 30000 })
  const rows = list.data?.reports ?? []
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'minmax(220px, 300px) 1fr', gap: 16,
      alignItems: 'start' }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        <div style={{ fontSize: 12, color: MUTED }}>Drafts are built when an inspection's
          pictures stop for 30 minutes. Nothing is sent to customers.</div>
        {list.isLoading && <div style={{ fontSize: 13, color: MUTED }}>Loading…</div>}
        {!list.isLoading && !rows.length && <div style={{ fontSize: 13, color: MUTED }}>
          No reports yet.</div>}
        {rows.map((r) => (
          <button key={r.id} type="button" onClick={() => setSelected(r.id)}
            style={{ textAlign: 'left', background: '#fff', borderRadius: 6, padding: '8px 10px',
              border: `1px solid ${selected === r.id ? BLUE : LINE}` }}>
            <div style={{ fontSize: 14, fontWeight: 600, color: INK }}>
              #{r.job_number} {r.customer_name}</div>
            <div style={{ fontSize: 12, color: r.state === 'failed' ? RED : MUTED }}>
              {STATE_LABEL[r.state] ?? r.state} · {r.photo_count} pictures ·{' '}
              {r.visit_at ? new Date(r.visit_at).toLocaleDateString() : ''}</div>
          </button>
        ))}
      </div>
      {selected != null ? <Review id={selected} isAdmin={isAdmin} /> : (
        <div style={{ fontSize: 13, color: MUTED }}>Pick a report on the left.</div>)}
    </div>
  )
}

function Review({ id, isAdmin }: { id: number; isAdmin: boolean }) {
  const qc = useQueryClient()
  const q = useQuery({ queryKey: ['inspection-report', id], queryFn: () => inspectionReport(id) })
  const [draft, setDraft] = useState<InspectionReportDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => { if (q.data) setDraft(q.data) }, [q.data])
  const done = (r: InspectionReportDetail) => {
    setDraft(r); setError(null)
    qc.invalidateQueries({ queryKey: ['inspection-reports'] })
    qc.setQueryData(['inspection-report', id], r)
  }
  const fail = (e: Error) => setError(e.message)
  const save = useMutation({ mutationFn: () => editInspectionReport(id, {
    title: draft?.title ?? '', summary: draft?.summary ?? '', findings: draft?.findings ?? [],
    photos: (draft?.photos ?? []).map((p) => ({ sha: p.sha, caption: p.caption ?? '' })) }),
  onSuccess: done, onError: fail })
  const approve = useMutation({ mutationFn: () => approveInspectionReport(id), onSuccess: done, onError: fail })
  const discard = useMutation({ mutationFn: () => discardInspectionReport(id), onSuccess: done, onError: fail })
  const rebuild = useMutation({ mutationFn: () => rebuildInspectionReport(id), onSuccess: done, onError: fail })
  if (!draft) return <div style={{ fontSize: 13, color: MUTED }}>Loading…</div>
  const editable = draft.state === 'draft'
  const set = (patch: Partial<InspectionReportDetail>) => setDraft({ ...draft, ...patch })
  const move = (i: number, d: number) => {
    const p = [...draft.photos]; const j = i + d
    if (j < 0 || j >= p.length) return
    ;[p[i], p[j]] = [p[j], p[i]]; set({ photos: p })
  }
  const unused = draft.candidates.filter((c) => !draft.photos.some((p) => p.sha === c.sha))
  return (
    <article style={{ background: '#fff', border: `1px solid ${LINE}`, borderRadius: 8,
      padding: '18px 22px', display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        <span style={{ fontSize: 12, fontWeight: 600, letterSpacing: '.06em', color: COPPER }}>
          DREAM TEAM ROOFING · {STATE_LABEL[draft.state] ?? draft.state}</span>
        <span style={{ marginLeft: 'auto', display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {editable && <button type="button" style={btn} disabled={save.isPending}
            onClick={() => save.mutate()}>Save changes</button>}
          {editable && <button type="button" style={primary} disabled={approve.isPending}
            onClick={() => approve.mutate()}>Approve</button>}
          {(editable || draft.state === 'failed') && <button type="button" style={btn}
            onClick={() => discard.mutate()}>Discard</button>}
          {isAdmin && (editable || draft.state === 'failed') && <button type="button" style={btn}
            onClick={() => rebuild.mutate()}>Rebuild</button>}
          {(editable || draft.state === 'approved') && <a style={{ ...btn, display: 'inline-flex',
            alignItems: 'center' }} href={draft.pdf_url}>Download PDF</a>}
        </span>
      </div>
      {error && <div role="alert" style={{ color: RED, fontSize: 13 }}>{error}</div>}
      {draft.state === 'failed' && <div style={{ color: RED, fontSize: 13 }}>
        It could not be built: {draft.error}</div>}
      <div style={{ fontSize: 13, color: MUTED }}>#{draft.job_number} {draft.customer_name}
        {draft.address ? ` · ${draft.address}` : ''} · inspected{' '}
        {draft.visit_at ? new Date(draft.visit_at).toLocaleString() : ''}
        {draft.technician ? ` by ${draft.technician}` : ''}</div>
      <input aria-label="Title" id="report-title" disabled={!editable} value={draft.title ?? ''}
        onChange={(e) => set({ title: e.target.value })}
        style={{ ...input, fontSize: 22, fontWeight: 700, border: editable ? input.border : 'none' }} />
      <label style={{ fontSize: 12, color: MUTED }} htmlFor="report-summary">Summary</label>
      <textarea id="report-summary" disabled={!editable} rows={3} value={draft.summary ?? ''}
        onChange={(e) => set({ summary: e.target.value })} style={input} />
      <div style={{ fontSize: 12, color: MUTED }}>What we found</div>
      {draft.findings.map((f, i) => (
        <div key={i} style={{ display: 'flex', gap: 6 }}>
          <input aria-label={`Finding ${i + 1}`} id={`report-finding-${i}`} disabled={!editable}
            value={f} style={input} onChange={(e) => set({
              findings: draft.findings.map((x, j) => (j === i ? e.target.value : x)) })} />
          {editable && <button type="button" style={btn} aria-label="Remove finding"
            onClick={() => set({ findings: draft.findings.filter((_, j) => j !== i) })}>×</button>}
        </div>
      ))}
      {editable && <div><button type="button" style={btn}
        onClick={() => set({ findings: [...draft.findings, ''] })}>Add a finding</button></div>}
      <div style={{ fontSize: 12, color: MUTED }}>Pictures in the report</div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))',
        gap: 12 }}>
        {draft.photos.map((p, i) => (
          <figure key={p.sha} style={{ margin: 0, display: 'flex', flexDirection: 'column', gap: 6 }}>
            <img src={p.url} alt={p.caption ?? `Picture ${i + 1}`} loading="lazy"
              style={{ width: '100%', aspectRatio: '4 / 3', objectFit: 'cover', borderRadius: 6,
                border: `1px solid ${LINE}` }} />
            <input aria-label={`Caption ${i + 1}`} id={`report-caption-${i}`} disabled={!editable}
              value={p.caption ?? ''} style={{ ...input, fontSize: 13 }}
              onChange={(e) => set({ photos: draft.photos.map((x, j) =>
                (j === i ? { ...x, caption: e.target.value } : x)) })} />
            {editable && <div style={{ display: 'flex', gap: 6 }}>
              <button type="button" style={btn} onClick={() => move(i, -1)} aria-label="Move earlier">↑</button>
              <button type="button" style={btn} onClick={() => move(i, 1)} aria-label="Move later">↓</button>
              <button type="button" style={btn} disabled={draft.photos.length <= 1}
                onClick={() => set({ photos: draft.photos.filter((_, j) => j !== i) })}>Remove</button>
            </div>}
          </figure>
        ))}
      </div>
      {editable && unused.length > 0 && <>
        <div style={{ fontSize: 12, color: MUTED }}>Other pictures from the visit — click to add</div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {unused.map((c) => (
            <button key={c.sha} type="button" title="Add to the report"
              onClick={() => set({ photos: [...draft.photos, { sha: c.sha, caption: '', url: c.url }] })}
              style={{ padding: 0, border: `1px solid ${LINE}`, borderRadius: 4 }}>
              <img src={c.url} alt="A picture from the visit" loading="lazy"
                style={{ width: 96, height: 72, objectFit: 'cover', display: 'block', borderRadius: 4 }} />
            </button>
          ))}
        </div>
      </>}
    </article>
  )
}
