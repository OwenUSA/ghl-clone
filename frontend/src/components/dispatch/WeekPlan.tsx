import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { BLUE, INK, LINE } from '../ai/aiUi'
import {
  dispatchPlan, type DispatchPlan, type DispatchPlanKind, type DispatchPlanTech,
  type DispatchPlanVisit,
} from '../../lib/api'
import { driveLabel, KIND_LABEL, planText, visitRange, visitTitle } from '../../lib/dispatch'

const MUTED = 'rgb(102,112,133)'
const RED = 'rgb(180,35,24)'
const GREEN = 'rgb(2,122,72)'
const SOFT = 'rgb(249,250,251)'
const DAY_NAMES = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
const SOURCE: Record<string, string> = { history: 'from history', settings: 'from settings',
  default: 'default' }

const card: React.CSSProperties = { background: '#fff', border: `1px solid ${LINE}`,
  borderRadius: 8, padding: '12px 16px' }
const link: React.CSSProperties = { color: BLUE, fontWeight: 500 }
const primary: React.CSSProperties = { color: '#fff', background: BLUE, borderRadius: 6,
  padding: '4px 12px', fontWeight: 500, fontSize: 13 }
const field: React.CSSProperties = { border: `1px solid ${LINE}`, borderRadius: 6,
  padding: '2px 6px', fontSize: 12, background: '#fff' }

type Kind = 'all' | DispatchPlanKind

/**
 * "Plan the next business days" (2026-10-01): a DRAFT schedule for every job waiting for a
 * visit, close jobs on the same day, Monday–Saturday, from GET /api/dispatch/plan. It books
 * nothing — the office books each visit in Zuper. Fetched only while the panel is open.
 */
export function WeekPlan() {
  const [open, setOpen] = useState(false)
  const [days, setDays] = useState(6)
  const [kind, setKind] = useState<Kind>('all')
  const q = useQuery({ queryKey: ['dispatch-plan', days, kind],
    queryFn: () => dispatchPlan(days, kind), enabled: open, staleTime: 60000 })

  if (!open) {
    return (
      <div style={{ ...card, display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <div style={{ flex: 1, minWidth: 200 }}>
          <div style={{ fontSize: 14, fontWeight: 600, color: INK }}>Plan the next business days</div>
          <div style={{ fontSize: 13, color: MUTED }}>
            A draft schedule for every job waiting for a visit — close jobs on the same day,
            Monday to Saturday. Nothing is booked.</div>
        </div>
        <button type="button" onClick={() => setOpen(true)} style={primary}>Plan the week</button>
      </div>
    )
  }

  const plan = q.data
  return (
    <div style={card}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 14, fontWeight: 600, color: INK }}>Plan the next business days</span>
        <label style={{ fontSize: 12, color: MUTED }}>Days{' '}
          <select value={days} onChange={(e) => setDays(Number(e.target.value))} style={field}>
            <option value={6}>6</option><option value={12}>12</option>
          </select></label>
        <label style={{ fontSize: 12, color: MUTED }}>Kind{' '}
          <select value={kind} onChange={(e) => setKind(e.target.value as Kind)} style={field}>
            <option value="all">All</option>
            <option value="inspection">Inspections</option>
            <option value="repair">Repairs</option>
          </select></label>
        <span className="ml-auto" />
        {plan && <CopyButton plan={plan} />}
        <button type="button" onClick={() => setOpen(false)} style={{ color: MUTED, fontSize: 13 }}>
          Hide</button>
      </div>
      <div style={{ fontSize: 13, color: INK, marginTop: 6, fontWeight: 500 }}>
        Draft — nothing is booked until you book it in Zuper.</div>
      {q.isFetching && !plan && <div style={{ color: MUTED, fontSize: 13, marginTop: 8 }}>Planning…</div>}
      {q.isError && <div style={{ color: RED, fontSize: 13, marginTop: 8 }}>
        Could not plan: {(q.error as Error).message}</div>}
      {plan && <PlanBody plan={plan} refreshing={q.isFetching} />}
    </div>
  )
}

function CopyButton({ plan }: { plan: DispatchPlan }) {
  const [state, setState] = useState<'idle' | 'copied' | 'failed'>('idle')
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(planText(plan))
      setState('copied')
    } catch {
      setState('failed')
    }
    window.setTimeout(() => setState('idle'), 2000)
  }
  return (
    <button type="button" onClick={copy} style={{ ...link, fontSize: 13,
      color: state === 'failed' ? RED : state === 'copied' ? GREEN : BLUE }}>
      {state === 'copied' ? 'Copied' : state === 'failed' ? 'Could not copy' : 'Copy plan'}</button>
  )
}

function Assumptions({ plan }: { plan: DispatchPlan }) {
  const a = plan.assumptions
  const techs = Array.from(new Set([...Object.keys(a.minutes), ...Object.keys(a.hours)]))
  return (
    <div style={{ fontSize: 12, color: MUTED, marginTop: 4, display: 'flex', flexDirection: 'column',
      gap: 1 }}>
      {techs.map((t) => {
        const m = a.minutes[t]
        const h = a.hours[t]
        const parts: string[] = []
        if (m) parts.push(`inspection ${m.inspection} min, repair ${m.repair} min (${SOURCE[m.source] ?? m.source})`)
        if (h) {
          const dayNames = h.days.map((d) => DAY_NAMES[d] ?? String(d)).join(' ')
          parts.push(`${dayNames} ${h.start}–${h.end}, max ${h.max} a day`)
        }
        return <span key={t}><span style={{ color: INK }}>{t}</span>: {parts.join(' · ')}</span>
      })}
      {a.note && <span>{a.note}</span>}
      <span>{plan.pending} job{plan.pending === 1 ? '' : 's'} waiting for a visit · planned{' '}
        {new Date(plan.generated_at).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit',
          timeZone: 'America/New_York' })}</span>
    </div>
  )
}

function PlanBody({ plan, refreshing }: { plan: DispatchPlan; refreshing: boolean }) {
  return (
    <div style={{ opacity: refreshing ? 0.6 : 1 }}>
      <Assumptions plan={plan} />
      {plan.days.length === 0 && <div style={{ color: MUTED, fontSize: 13, marginTop: 10 }}>
        Nothing to plan.</div>}
      {plan.days.map((d) => (
        <div key={d.date} style={{ marginTop: 14 }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: INK, marginBottom: 6 }}>{d.label}</div>
          {d.techs.length === 0 ? <div style={{ fontSize: 12, color: MUTED }}>No visits.</div> : (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10 }}>
              {d.techs.map((t) => <TechColumn key={t.tech} t={t} />)}
            </div>
          )}
        </div>
      ))}
      {plan.unplaced.length > 0 && (
        <div style={{ marginTop: 16 }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: INK, marginBottom: 4 }}>
            Not placed ({plan.unplaced.length})</div>
          <ul style={{ margin: 0, padding: 0, listStyle: 'none', fontSize: 13 }}>
            {plan.unplaced.map((u) => (
              <li key={u.job_uid} style={{ padding: '3px 0', display: 'flex', gap: 8,
                flexWrap: 'wrap', alignItems: 'baseline' }}>
                <span style={{ color: INK }}>{visitTitle(u)}</span>
                <KindBadge kind={u.kind} />
                <span style={{ color: MUTED }}>{u.reason}</span>
                {u.zuper_url && <a href={u.zuper_url} target="_blank" rel="noreferrer"
                  style={{ ...link, fontSize: 12 }}>Open in Zuper ↗</a>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

function TechColumn({ t }: { t: DispatchPlanTech }) {
  return (
    <div style={{ flex: '1 1 260px', minWidth: 0, maxWidth: '100%', border: `1px solid ${LINE}`,
      borderRadius: 8, padding: '8px 10px', background: SOFT }}>
      <div style={{ fontSize: 13, fontWeight: 600, color: INK }}>{t.tech}</div>
      <div style={{ fontSize: 12, color: MUTED, marginBottom: 6 }}>
        {t.count} of {t.capacity} · {driveLabel(t.drive_minutes)} · from {t.start_from}</div>
      <ol style={{ margin: 0, padding: 0, listStyle: 'none', display: 'flex', flexDirection: 'column',
        gap: 4 }}>
        {t.visits.map((v) => <VisitLine key={`${v.job_uid}-${v.start}`} v={v} />)}
      </ol>
    </div>
  )
}

function VisitLine({ v }: { v: DispatchPlanVisit }) {
  return (
    <li style={{ fontSize: 13 }}>
      {v.drive_minutes_before > 0 && <div style={{ fontSize: 11, color: MUTED }}>
        +{v.drive_minutes_before} min drive</div>}
      <div style={{ background: '#fff', border: `1px solid ${LINE}`, borderRadius: 6,
        padding: '4px 8px', color: v.existing ? MUTED : INK, opacity: v.existing ? 0.75 : 1 }}>
        <div style={{ display: 'flex', gap: 6, alignItems: 'baseline', flexWrap: 'wrap' }}>
          <span style={{ fontWeight: 500 }}>{visitRange(v.start, v.end)}</span>
          <KindBadge kind={v.kind} muted={v.existing} />
          {v.existing && <span style={{ fontSize: 11 }}>booked</span>}
        </div>
        <div style={{ overflowWrap: 'anywhere' }}>{visitTitle(v)}</div>
        {!v.existing && v.zuper_url && <a href={v.zuper_url} target="_blank" rel="noreferrer"
          style={{ ...link, fontSize: 12 }}>Open in Zuper ↗</a>}
      </div>
    </li>
  )
}

function KindBadge({ kind, muted = false }: { kind: string; muted?: boolean }) {
  const repair = kind === 'repair'
  return (
    <span style={{ fontSize: 11, fontWeight: 500, borderRadius: 4, padding: '0 6px',
      color: muted ? MUTED : repair ? 'rgb(181,71,8)' : 'rgb(23,92,211)',
      background: muted ? 'rgb(242,244,247)' : repair ? 'rgb(255,250,235)' : 'rgb(239,248,255)' }}>
      {KIND_LABEL[kind] ?? kind}</span>
  )
}
