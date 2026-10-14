import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { BLUE, INK, LINE } from '../ai/aiUi'
import { dispatchPipeline, type DispatchPipelineJob } from '../../lib/api'

const MUTED = 'rgb(102,112,133)'
const RED = 'rgb(180,35,24)'
const AMBER = 'rgb(181,71,8)'
const GREEN = 'rgb(2,122,72)'

const TONE: Record<string, string> = { update: RED, call: AMBER, waiting: MUTED, ok: GREEN,
  test: MUTED }
const LABEL: Record<string, string> = { update: 'Update Zuper', call: 'Call / follow up',
  waiting: 'Waiting', ok: 'Up to date', test: 'Test job' }

/**
 * Pipeline (2026-10-14): EVERY open job on the AHS boards, column by column in Zuper's order —
 * where each job is, how long it has been there, what it needs, where it should go and why,
 * with the evidence (app/dispatch/briefs.py `pipeline_map`). Fixed rules, no AI, nothing
 * changed in Zuper. "Show only what needs action" hides the waiting / up-to-date rows.
 */
export function Pipeline() {
  const q = useQuery({ queryKey: ['dispatch-pipeline'], queryFn: dispatchPipeline,
    refetchInterval: 180000, refetchOnWindowFocus: true })
  const [onlyAction, setOnlyAction] = useState(true)
  const d = q.data
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <label style={{ fontSize: 13, color: INK, display: 'flex', gap: 6, alignItems: 'center' }}>
          <input type="checkbox" checked={onlyAction} onChange={(e) => setOnlyAction(e.target.checked)} />
          Show only what needs action</label>
        <button type="button" onClick={() => q.refetch()} style={{ marginLeft: 'auto', fontSize: 13,
          color: BLUE, background: 'none', border: 'none' }}>
          {q.isFetching ? 'Checking…' : 'Check again'}</button>
      </div>
      <div style={{ fontSize: 12, color: MUTED }}>
        Every open job on the AHS boards, read from Zuper and every call and text by fixed rules —
        no AI, nothing changed.{d && <> Checked {d.checked_at}.</>}
      </div>
      {q.isLoading && <div style={{ color: MUTED, fontSize: 13 }}>Checking…</div>}
      {q.isError && <div style={{ color: RED, fontSize: 13 }}>
        Could not load the pipeline: {(q.error as Error).message}</div>}
      {d?.boards.map((b) => (
        <section key={b.board} aria-label={b.board}>
          <h3 style={{ fontSize: 15, fontWeight: 600, color: INK, margin: '4px 0 6px' }}>
            {b.board} <span style={{ color: MUTED, fontWeight: 400, fontSize: 13 }}>· {b.total} jobs</span>
          </h3>
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', fontSize: 12, marginBottom: 8 }}>
            {(['update', 'call', 'waiting', 'ok', 'test'] as const).filter((s) => b.by_state[s])
              .map((s) => <span key={s} style={{ color: TONE[s], fontWeight: 600 }}>
                {LABEL[s]}: {b.by_state[s]}</span>)}
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {b.columns.map((col) => {
              const shown = onlyAction ? col.jobs.filter((j) => j.state === 'update' || j.state === 'call')
                : col.jobs
              const rest = col.count - shown.length
              return (
                <div key={col.stage} style={{ border: `1px solid ${LINE}`, borderRadius: 8,
                  background: '#fff' }}>
                  <div style={{ padding: '6px 12px', borderBottom: shown.length ? `1px solid ${LINE}` : 'none',
                    fontSize: 13, fontWeight: 600, color: INK, display: 'flex', gap: 8 }}>
                    {col.stage}<span style={{ color: MUTED, fontWeight: 400 }}>· {col.count}</span>
                    {rest > 0 && <span style={{ color: MUTED, fontWeight: 400, marginLeft: 'auto' }}>
                      {rest} waiting or up to date</span>}
                  </div>
                  {shown.map((j) => <Row key={j.job_uid} j={j} />)}
                </div>
              )
            })}
          </div>
        </section>
      ))}
    </div>
  )
}

function Row({ j }: { j: DispatchPipelineJob }) {
  const [open, setOpen] = useState(false)
  return (
    <div style={{ padding: '7px 12px', borderLeft: `3px solid ${TONE[j.state]}`, fontSize: 13,
      borderTop: `1px solid ${LINE}` }}>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'baseline' }}>
        <span style={{ fontWeight: 600, color: INK }}>#{j.job_number} {j.customer}</span>
        <span style={{ color: TONE[j.state], fontSize: 12, fontWeight: 600 }}>{LABEL[j.state]}</span>
        <span style={{ color: MUTED, fontSize: 12 }}>
          {[j.days_in_stage != null && `${j.days_in_stage} day${j.days_in_stage === 1 ? '' : 's'} here`,
            j.visit, j.assigned].filter(Boolean).join(' · ')}</span>
        {j.zuper_url && <a href={j.zuper_url} target="_blank" rel="noreferrer"
          style={{ marginLeft: 'auto', color: BLUE, fontSize: 12 }}>Open in Zuper</a>}
      </div>
      {j.should && <div style={{ marginTop: 2, color: INK }}>
        <span style={{ color: MUTED }}>Should be:</span> <b>{j.should}</b></div>}
      <div style={{ marginTop: 2, color: MUTED }}>{j.why}</div>
      {(j.evidence.length > 0 || j.also) && (
        <button type="button" onClick={() => setOpen(!open)} aria-expanded={open}
          style={{ fontSize: 12, color: MUTED, textDecoration: 'underline', textUnderlineOffset: 2 }}>
          {open ? 'Less' : 'Evidence'}</button>
      )}
      {open && (
        <ul style={{ margin: '4px 0 0', paddingLeft: 18, color: MUTED, fontSize: 12 }}>
          {j.evidence.map((e, i) => <li key={i}>{e.at} · {[e.line, e.how].filter(Boolean).join(' ')}:
            {' '}<span style={{ color: INK }}>“{e.said}”</span></li>)}
          {j.also?.map((a, i) => <li key={`a${i}`}>Also: {a}</li>)}
        </ul>
      )}
    </div>
  )
}
