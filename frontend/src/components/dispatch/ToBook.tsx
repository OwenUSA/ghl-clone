import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { BLUE, INK, LINE } from '../ai/aiUi'
import { dispatchToBook, type DispatchToBookJob } from '../../lib/api'

const MUTED = 'rgb(102,112,133)'
const RED = 'rgb(180,35,24)'
const AMBER = 'rgb(181,71,8)'
const GREEN = 'rgb(2,122,72)'

// What each group asks of the office, by colour: ready to book, a call to make, nothing to do.
const TONE: Record<string, string> = {
  book: GREEN, confirm_time: GREEN, new_date: AMBER, call_and_book: AMBER, keep_calling: AMBER,
  visit_happened: BLUE, on_other_job: MUTED, on_hold: MUTED, stale: RED,
}

/**
 * To book (2026-10-13): every job that still needs a visit, each with ONE verdict worked out
 * by fixed rules from 60 days of calls and texts and Zuper (app/dispatch/briefs.py `to_book`):
 * agreed → book, already on the customer's other job, the visit happened (update the stage),
 * offered days, asked to move, on hold, keep calling, call and book, gone quiet. No AI, so it
 * costs nothing and gives the same answer every time. Nothing is changed in Zuper.
 */
export function ToBook() {
  const q = useQuery({ queryKey: ['dispatch-to-book'], queryFn: dispatchToBook,
    refetchInterval: 180000, refetchOnWindowFocus: true })
  const d = q.data
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'flex', gap: 8, alignItems: 'baseline', flexWrap: 'wrap' }}>
        <span style={{ fontSize: 12, color: MUTED }}>
          Every job that still needs a visit, judged from its calls and texts (Quo, the CRM
          line, Zuper Connect — 60 days) and Zuper, by fixed rules — no AI, nothing changed.
          {d && <> {d.jobs_waiting} jobs, checked {d.checked_at}.</>}
        </span>
        <button type="button" onClick={() => q.refetch()}
          style={{ marginLeft: 'auto', fontSize: 13, color: BLUE, background: 'none',
            border: 'none' }}>{q.isFetching ? 'Checking…' : 'Check again'}</button>
      </div>
      {q.isLoading && <div style={{ color: MUTED, fontSize: 13 }}>Checking…</div>}
      {q.isError && <div style={{ color: RED, fontSize: 13 }}>
        Could not load the list: {(q.error as Error).message}</div>}
      {d && d.groups.length === 0 && (
        <div style={{ color: GREEN, fontSize: 13, padding: 24, textAlign: 'center' }}>
          Nothing is waiting for a booking.</div>
      )}
      {d?.groups.map((g) => (
        <section key={g.verdict} aria-label={g.title}>
          <h3 style={{ fontSize: 13, fontWeight: 600, color: TONE[g.verdict] ?? INK,
            margin: '8px 0' }}>
            {g.title}<span style={{ color: MUTED, fontWeight: 400 }}> · {g.count}</span></h3>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {g.jobs.map((j) => <Job key={j.job_number ?? j.customer ?? ''} j={j}
              tone={TONE[g.verdict] ?? MUTED} />)}
          </div>
        </section>
      ))}
    </div>
  )
}

function Job({ j, tone }: { j: DispatchToBookJob; tone: string }) {
  const [open, setOpen] = useState(false)
  return (
    <div style={{ background: '#fff', border: `1px solid ${LINE}`, borderLeft: `3px solid ${tone}`,
      borderRadius: 8, padding: '8px 12px', fontSize: 13 }}>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'baseline' }}>
        <span style={{ fontWeight: 600, color: INK }}>#{j.job_number} {j.customer}</span>
        <span style={{ color: MUTED, fontSize: 12 }}>
          {[j.board && `${j.board} · ${j.stage}`, j.visit, j.assigned].filter(Boolean)
            .join(' · ')}</span>
        {j.zuper_url && <a href={j.zuper_url} target="_blank" rel="noreferrer"
          style={{ marginLeft: 'auto', color: BLUE, fontSize: 12 }}>Open in Zuper</a>}
      </div>
      <div style={{ marginTop: 3, color: INK }}>{j.say}</div>
      {(j.evidence.length > 0 || j.availability || j.last_contact) && (
        <button type="button" onClick={() => setOpen(!open)} aria-expanded={open}
          style={{ marginTop: 2, fontSize: 12, color: MUTED, textDecoration: 'underline',
            textUnderlineOffset: 2 }}>{open ? 'Less' : 'Evidence'}</button>
      )}
      {open && (
        <ul style={{ margin: '4px 0 0', paddingLeft: 18, color: MUTED, fontSize: 12 }}>
          {j.evidence.map((e, i) => (
            <li key={i}>{e.at} · {[e.line, e.how].filter(Boolean).join(' ')}:{' '}
              <span style={{ color: INK }}>“{e.said}”</span></li>
          ))}
          {j.last_contact && <li>Last contact: {j.last_contact}</li>}
          {j.availability && <li>What they said about availability: {j.availability}</li>}
          {j.why_waiting && <li>Why it is on this list: {j.why_waiting}</li>}
        </ul>
      )}
    </div>
  )
}
