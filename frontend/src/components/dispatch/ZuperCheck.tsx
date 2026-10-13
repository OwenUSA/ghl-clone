import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { BLUE, INK, LINE } from '../ai/aiUi'
import {
  bookDispatchSlot, dispatchCheck, dispatchWrites, moveDispatchStage,
  type DispatchCheckFinding, type DispatchCheckFix,
} from '../../lib/api'

const MUTED = 'rgb(102,112,133)'
const RED = 'rgb(180,35,24)'
const AMBER = 'rgb(181,71,8)'
const GREEN = 'rgb(2,122,72)'

const RANGES = [
  { key: 'today', label: 'Today', from: 'today', to: 'today' },
  { key: 'next', label: 'Today + tomorrow', from: 'today', to: 'tomorrow' },
  { key: 'tomorrow', label: 'Tomorrow', from: 'tomorrow', to: 'tomorrow' },
  { key: 'week', label: 'Next 7 days', from: 'today', to: '' },
]

const KIND_LABEL: Record<string, string> = {
  double_booked: 'Double-booked', agreed_other_time: 'Agreed a different time',
  customer_cancelled: 'Customer asked to cancel / move', not_confirmed: 'Not confirmed',
  on_the_way_not_moved: 'On the way, stage not moved', done_not_moved: 'Done, stage not moved',
  no_field_update: 'Nothing from the field', ahs_sent_not_moved: 'Sent to AHS, column behind',
  technician_field: 'Technician field', unanswered: 'Customer waiting for an answer',
  booked_no_time: 'Booked stage, no time', stale_on_the_way: 'Stage left behind',
}

function plusDays(n: number): string {
  // A New York date, as the server reads it (YYYY-MM-DD).
  const d = new Date(new Date().toLocaleString('en-US', { timeZone: 'America/New_York' }))
  d.setDate(d.getDate() + n)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-` +
    `${String(d.getDate()).padStart(2, '0')}`
}

function fixLabel(f: DispatchCheckFix): string {
  if (f.action === 'stage') return `Move to “${f.to}”`
  if (f.action === 'booking') return `Book ${f.label}, ${f.technician}`
  return `Set ${f.field} to ${f.to}`
}

/**
 * Check Zuper (2026-10-13): what Zuper needs today / tomorrow / this week, worked out by fixed
 * rules from every call and text and Zuper itself — no AI, so it costs nothing to open and
 * gives the same answer every time (app/dispatch/briefs.py). Each row shows its evidence.
 *
 * "Do it in Zuper" appears only for a fix the server allows (the phase 3 switches, all OFF by
 * default — `GET /api/dispatch/writes`); otherwise the row says what to change by hand.
 */
export function ZuperCheck() {
  const [range, setRange] = useState('next')
  const r = RANGES.find((x) => x.key === range) ?? RANGES[1]
  const to = r.to || plusDays(6)
  const check = useQuery({ queryKey: ['dispatch-check', r.from, to],
    queryFn: () => dispatchCheck(r.from, to), refetchInterval: 120000,
    refetchOnWindowFocus: true })
  const writes = useQuery({ queryKey: ['dispatch-writes'], queryFn: dispatchWrites })
  const data = check.data
  const byDay = new Map<string, DispatchCheckFinding[]>()
  for (const f of data?.findings ?? []) {
    const k = f.visit_day ?? 'No visit date'
    byDay.set(k, [...(byDay.get(k) ?? []), f])
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        {RANGES.map((x) => (
          <button key={x.key} type="button" onClick={() => setRange(x.key)}
            aria-pressed={range === x.key}
            style={{ padding: '5px 12px', borderRadius: 16, fontSize: 13,
              border: `1px solid ${range === x.key ? BLUE : LINE}`,
              background: range === x.key ? 'rgb(239,244,255)' : '#fff',
              color: range === x.key ? BLUE : INK, fontWeight: range === x.key ? 600 : 400 }}>
            {x.label}
          </button>
        ))}
        <button type="button" onClick={() => check.refetch()}
          style={{ marginLeft: 'auto', fontSize: 13, color: BLUE, background: 'none',
            border: 'none' }}>
          {check.isFetching ? 'Checking…' : 'Check again'}
        </button>
      </div>
      <div style={{ fontSize: 12, color: MUTED }}>
        Read from every call and text (Quo, the CRM line, Zuper Connect) and Zuper itself by
        fixed rules — no AI, nothing changed.
        {data && <> {data.visits_checked} visits and {data.jobs_checked} open jobs checked at
          {' '}{data.checked_at}; {data.skipped_test_jobs} test jobs skipped.</>}
      </div>
      {check.isLoading && <div style={{ color: MUTED, fontSize: 13 }}>Checking…</div>}
      {check.isError && <div style={{ color: RED, fontSize: 13 }}>
        Could not run the check: {(check.error as Error).message}</div>}
      {data && data.findings.length === 0 && (
        <div style={{ color: GREEN, fontSize: 13, padding: 24, textAlign: 'center' }}>
          Nothing to fix: Zuper matches the calls and texts for these days.</div>
      )}
      {[...byDay.entries()].map(([day, list]) => (
        <section key={day} aria-label={day}>
          <h3 style={{ fontSize: 13, fontWeight: 600, color: INK, margin: '8px 0' }}>
            {day === 'No visit date' ? 'Not on the calendar' : dayTitle(day)}
            <span style={{ color: MUTED, fontWeight: 400 }}> · {list.length}</span>
          </h3>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {list.map((f, i) => (
              <Finding key={`${f.job_uid}-${f.kind}-${i}`} f={f}
                can={writes.data?.can ?? null} whyNot={writes.data?.why_not ?? null} />
            ))}
          </div>
        </section>
      ))}
    </div>
  )
}

function dayTitle(iso: string): string {
  const [y, m, d] = iso.split('-').map(Number)
  return new Date(y, m - 1, d).toLocaleDateString('en-US',
    { weekday: 'long', month: 'short', day: 'numeric' })
}

function Finding({ f, can, whyNot }: { f: DispatchCheckFinding
  can: Record<string, boolean> | null; whyNot: Record<string, string | null> | null }) {
  const qc = useQueryClient()
  const [result, setResult] = useState<{ ok: boolean; sentence: string } | null>(null)
  const kind = f.fix?.action ?? null
  const allowed = kind && kind !== 'field' && can ? !!can[kind] : false
  const run = useMutation({
    mutationFn: async () => {
      const fix = f.fix
      if (!fix) throw new Error('nothing to do')
      if (fix.action === 'stage') return moveDispatchStage(f.job_uid, fix.to)
      if (fix.action === 'booking') return bookDispatchSlot(f.job_uid,
        { start: fix.start, end: fix.end, technician: fix.technician })
      throw new Error('change this field in Zuper')
    },
    onSuccess: (r) => {
      setResult(r)
      qc.invalidateQueries({ queryKey: ['dispatch-check'] })
    },
    onError: (e) => setResult({ ok: false, sentence: (e as Error).message }),
  })
  const color = f.severity === 'high' ? RED : f.severity === 'medium' ? AMBER : MUTED
  return (
    <div style={{ background: '#fff', border: `1px solid ${LINE}`, borderLeft: `3px solid ${color}`,
      borderRadius: 8, padding: '10px 14px', fontSize: 13 }}>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'baseline' }}>
        <span style={{ fontWeight: 600, color: INK }}>#{f.job_number} {f.customer}</span>
        <span style={{ color, fontWeight: 600, fontSize: 12 }}>
          {KIND_LABEL[f.kind] ?? f.kind}</span>
        <span style={{ color: MUTED, fontSize: 12 }}>
          {[f.visit, f.assigned, f.board && `${f.board} · ${f.stage}`].filter(Boolean)
            .join(' · ')}</span>
        {f.zuper_url && <a href={f.zuper_url} target="_blank" rel="noreferrer"
          style={{ marginLeft: 'auto', color: BLUE, fontSize: 12 }}>Open in Zuper</a>}
      </div>
      <div style={{ marginTop: 4, color: INK }}>{f.what}</div>
      <div style={{ marginTop: 2, color: MUTED }}>{f.do}</div>
      {f.evidence.length > 0 && (
        <ul style={{ margin: '6px 0 0', paddingLeft: 18, color: MUTED, fontSize: 12 }}>
          {f.evidence.map((e, i) => (
            <li key={i}>
              {e.at} · {[e.line, e.how, e.from === 'customer' ? 'from the customer'
                : e.from === 'us' ? 'from us' : null].filter(Boolean).join(' ')}:{' '}
              <span style={{ color: INK }}>“{e.said}”</span>
            </li>
          ))}
        </ul>
      )}
      {f.fix && (
        <div style={{ marginTop: 8, display: 'flex', gap: 10, alignItems: 'center',
          flexWrap: 'wrap' }}>
          {allowed ? (
            <button type="button" disabled={run.isPending || !!result?.ok}
              onClick={() => run.mutate()}
              style={{ padding: '4px 10px', borderRadius: 6, border: `1px solid ${BLUE}`,
                background: BLUE, color: '#fff', fontSize: 12 }}>
              {run.isPending ? 'Working…' : `Do it in Zuper: ${fixLabel(f.fix)}`}
            </button>
          ) : (
            <span style={{ fontSize: 12, color: INK }}>
              In Zuper: <b>{fixLabel(f.fix)}</b>
              {kind && kind !== 'field' && whyNot?.[kind] && (
                <span style={{ color: MUTED }}> — {whyNot[kind]}</span>)}
            </span>
          )}
          {result && <span style={{ fontSize: 12, color: result.ok ? GREEN : RED }}>
            {result.sentence}</span>}
        </div>
      )}
    </div>
  )
}
