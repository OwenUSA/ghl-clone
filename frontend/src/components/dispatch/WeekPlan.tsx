import { useMutation, useQuery } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'
import { BLUE, INK, LINE } from '../ai/aiUi'
import {
  dispatchPlanExcelUrl, getDispatchPlan, makeDispatchPlan, type DispatchPlan,
  type DispatchPlanKind, type DispatchPlanMode, type DispatchPlanTech, type DispatchPlanVisit,
} from '../../lib/api'
import { driveLabel, KIND_LABEL, planText, visitRange, visitTitle } from '../../lib/dispatch'
import { CustomerAvailability } from './CustomerAvailability'

const MUTED = 'rgb(102,112,133)'
const RED = 'rgb(180,35,24)'
const GREEN = 'rgb(2,122,72)'
const AMBER = 'rgb(181,71,8)'
const AMBER_BG = 'rgb(255,250,235)'
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
type View = 'calendar' | 'list'

/** The plan to open from the address bar (`?plan=12`), e.g. a link the assistant gave. */
function planFromUrl(): number | null {
  const v = Number(new URLSearchParams(window.location.search).get('plan'))
  return Number.isInteger(v) && v > 0 ? v : null
}

/**
 * Remake the schedule (2026-10-01; calendar 2026-10-08): a DRAFT plan for every job that needs
 * a visit — new inspections, AHS-approved repairs, reschedules, visits that did not happen —
 * fitted Monday to Saturday for the technicians with as many jobs and as little driving as
 * possible. Each plan is kept, so this calendar, the Excel and the assistant's answer show the
 * same one. Nothing is booked: the office books each visit in Zuper.
 */
export function WeekPlan() {
  const [planId, setPlanId] = useState<number | null>(planFromUrl)
  const [open, setOpen] = useState(planId !== null)
  const [days, setDays] = useState(6)
  const [kind, setKind] = useState<Kind>('all')
  const [mode, setMode] = useState<DispatchPlanMode>('most_jobs')
  const [boards, setBoards] = useState<'ahs' | 'all'>('ahs')
  const [unreached, setUnreached] = useState(true)
  const [view, setView] = useState<View>('calendar')
  const [limitsChanged, setLimitsChanged] = useState(false)
  const loaded = useQuery({ queryKey: ['dispatch-plan', planId],
    queryFn: () => getDispatchPlan(planId as number), enabled: planId !== null,
    staleTime: Infinity })
  const make = useMutation({
    mutationFn: () => makeDispatchPlan({ days, kind, mode, boards, include_unreached: unreached }),
    onSuccess: (p) => { if (p.id) setPlanId(p.id); setLimitsChanged(false) },
  })
  const plan = make.data ?? loaded.data

  if (!open) {
    return (
      <div style={{ ...card, display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <div style={{ flex: 1, minWidth: 200 }}>
          <div style={{ fontSize: 14, fontWeight: 600, color: INK }}>Remake the schedule</div>
          <div style={{ fontSize: 13, color: MUTED }}>
            Every job that needs a visit — new inspections, AHS-approved repairs, reschedules —
            fitted Monday to Saturday with the least driving. Nothing is booked.</div>
        </div>
        <button type="button" onClick={() => setOpen(true)} style={primary}>Plan the week</button>
      </div>
    )
  }

  return (
    <div style={card}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 14, fontWeight: 600, color: INK }}>Remake the schedule</span>
        <label style={{ fontSize: 12, color: MUTED }}>Days{' '}
          <select value={days} onChange={(e) => setDays(Number(e.target.value))} style={field}
            aria-label="Business days">
            <option value={6}>6 (a week)</option><option value={12}>12 (two weeks)</option>
            <option value={18}>18 (three weeks)</option>
          </select></label>
        <label style={{ fontSize: 12, color: MUTED }}>Jobs{' '}
          <select value={kind} onChange={(e) => setKind(e.target.value as Kind)} style={field}
            aria-label="Kind of visit">
            <option value="all">All</option>
            <option value="inspection">Inspections</option>
            <option value="repair">Repairs</option>
          </select></label>
        <label style={{ fontSize: 12, color: MUTED }}>Aim{' '}
          <select value={mode} onChange={(e) => setMode(e.target.value as DispatchPlanMode)}
            style={field} aria-label="Aim">
            <option value="most_jobs">Most jobs, least driving</option>
            <option value="oldest_first">Oldest waiting first</option>
          </select></label>
        <label style={{ fontSize: 12, color: MUTED }}>Boards{' '}
          <select value={boards} onChange={(e) => setBoards(e.target.value as 'ahs' | 'all')}
            style={field} aria-label="Boards">
            <option value="ahs">AHS Inspection + Repair</option>
            <option value="all">All boards</option>
          </select></label>
        <label style={{ fontSize: 12, color: MUTED, display: 'flex', alignItems: 'center', gap: 4 }}>
          <input type="checkbox" checked={unreached} onChange={(e) => setUnreached(e.target.checked)} />
          Include customers not reached yet</label>
        <button type="button" onClick={() => make.mutate()} disabled={make.isPending}
          style={{ ...primary, opacity: make.isPending ? 0.6 : 1 }}>
          {make.isPending ? 'Planning…' : plan ? 'Plan again' : 'Make the plan'}</button>
        <span className="ml-auto" />
        <button type="button" onClick={() => setOpen(false)} style={{ color: MUTED, fontSize: 13 }}>
          Hide</button>
      </div>
      <div style={{ fontSize: 13, color: INK, marginTop: 6, fontWeight: 500 }}>
        Draft — nothing is booked until you book it in Zuper.</div>
      {(make.isPending || loaded.isFetching) && !plan &&
        <div style={{ color: MUTED, fontSize: 13, marginTop: 8 }}>Planning…</div>}
      {make.isError && <div style={{ color: RED, fontSize: 13, marginTop: 8 }}>
        Could not plan: {(make.error as Error).message}</div>}
      {loaded.isError && !make.data && <div style={{ color: RED, fontSize: 13, marginTop: 8 }}>
        Could not open plan #{planId}: {(loaded.error as Error).message}</div>}
      <CustomerAvailability onChanged={() => setLimitsChanged(true)} />
      {limitsChanged && plan && <div style={{ fontSize: 12, color: AMBER, marginTop: 6 }}>
        The customers' limits changed — press “Plan again” to apply them.</div>}
      {plan && <PlanBody plan={plan} view={view} setView={setView} />}
    </div>
  )
}

function Summary({ plan }: { plan: DispatchPlan }) {
  const s = plan.summary
  const b = plan.baseline
  if (!s) return null
  const saved = b ? b.drive_minutes - s.drive_minutes : 0
  return (
    <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', marginTop: 10, fontSize: 13 }}>
      <Stat label="Visits planned" value={String(s.placed)} />
      <Stat label="Not placed" value={String(s.not_placed)} tone={s.not_placed ? RED : undefined} />
      <Stat label="Call first" value={String(s.tentative)} tone={s.tentative ? AMBER : undefined} />
      <Stat label="Driving" value={driveLabel(s.drive_minutes).replace(' driving', '')} />
      {plan.availability && <Stat label="Customers' calls read"
        value={`${plan.availability.read} of ${plan.availability.jobs}`}
        tone={plan.availability.not_read_yet ? AMBER : undefined} />}
      {b && (b.placed < s.placed || saved > 0) && (
        <Stat label="vs a simple plan" tone={GREEN} value={[
          b.placed < s.placed ? `+${s.placed - b.placed} visits` : '',
          saved > 0 ? `${saved} min less driving` : ''].filter(Boolean).join(', ')} />
      )}
    </div>
  )
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div style={{ border: `1px solid ${LINE}`, borderRadius: 8, padding: '6px 10px', background: SOFT }}>
      <div style={{ fontSize: 11, color: MUTED }}>{label}</div>
      <div style={{ fontSize: 15, fontWeight: 600, color: tone ?? INK }}>{value}</div>
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
    <div style={{ fontSize: 12, color: MUTED, marginTop: 8, display: 'flex', flexDirection: 'column',
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
      <span>{plan.id ? `Plan #${plan.id} · ` : ''}{plan.pending} job{plan.pending === 1 ? '' : 's'}
        {' '}need a visit · planned{' '}
        {new Date(plan.generated_at).toLocaleString('en-US', { weekday: 'short', hour: 'numeric',
          minute: '2-digit', timeZone: 'America/New_York' })}</span>
    </div>
  )
}

/** Monday of the week a "YYYY-MM-DD" date falls in, as "YYYY-MM-DD". */
function weekOf(iso: string): string {
  const d = new Date(`${iso}T12:00:00Z`)
  const back = (d.getUTCDay() + 6) % 7
  d.setUTCDate(d.getUTCDate() - back)
  return d.toISOString().slice(0, 10)
}

function addDays(iso: string, n: number): string {
  const d = new Date(`${iso}T12:00:00Z`)
  d.setUTCDate(d.getUTCDate() + n)
  return d.toISOString().slice(0, 10)
}

function PlanBody({ plan, view, setView }: { plan: DispatchPlan; view: View
  setView: (v: View) => void }) {
  const weeks = useMemo(() => Array.from(new Set(plan.days.map((d) => weekOf(d.date)))).sort(),
    [plan])
  const [week, setWeek] = useState(0)
  useEffect(() => { setWeek(0) }, [plan])
  return (
    <div>
      <Summary plan={plan} />
      <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap', marginTop: 10 }}>
        <div role="tablist" style={{ display: 'flex', border: `1px solid ${LINE}`, borderRadius: 6 }}>
          {(['calendar', 'list'] as View[]).map((v) => (
            <button key={v} type="button" role="tab" aria-selected={view === v} onClick={() => setView(v)}
              style={{ fontSize: 13, padding: '3px 10px', fontWeight: 500,
                color: view === v ? '#fff' : INK, background: view === v ? BLUE : '#fff',
                borderRadius: 5 }}>{v === 'calendar' ? 'Calendar' : 'List'}</button>
          ))}
        </div>
        {plan.id && <a href={dispatchPlanExcelUrl(plan.id)} download style={{ ...link, fontSize: 13 }}>
          Download Excel</a>}
        <CopyButton plan={plan} />
      </div>
      <Assumptions plan={plan} />
      {plan.days.length === 0 && <div style={{ color: MUTED, fontSize: 13, marginTop: 10 }}>
        Nothing to plan.</div>}
      {view === 'calendar' && weeks.length > 0 && (
        <>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 12 }}>
            <button type="button" disabled={week === 0} onClick={() => setWeek(week - 1)}
              aria-label="Previous week" style={{ ...link, opacity: week === 0 ? 0.4 : 1 }}>‹ Previous</button>
            <span style={{ fontSize: 13, fontWeight: 600, color: INK }}>
              Week of {new Date(`${weeks[week]}T12:00:00Z`).toLocaleDateString('en-US',
                { month: 'short', day: 'numeric', timeZone: 'UTC' })}
              {weeks.length > 1 ? ` (${week + 1} of ${weeks.length})` : ''}</span>
            <button type="button" disabled={week >= weeks.length - 1} onClick={() => setWeek(week + 1)}
              aria-label="Next week" style={{ ...link, opacity: week >= weeks.length - 1 ? 0.4 : 1 }}>
              Next ›</button>
          </div>
          <WeekGrid plan={plan} monday={weeks[week]} />
        </>
      )}
      {view === 'list' && plan.days.map((d) => (
        <div key={d.date} style={{ marginTop: 14 }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: INK, marginBottom: 6 }}>{d.label}</div>
          {d.techs.length === 0 ? <div style={{ fontSize: 12, color: MUTED }}>No visits.</div> : (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10 }}>
              {d.techs.map((t) => <TechColumn key={t.tech} t={t} />)}
            </div>
          )}
        </div>
      ))}
      <NotPlaced plan={plan} />
    </div>
  )
}

/** One week: a row per technician, Monday to Saturday across. */
function WeekGrid({ plan, monday }: { plan: DispatchPlan; monday: string }) {
  const dates = Array.from({ length: 6 }, (_, i) => addDays(monday, i))
  const byDate = new Map(plan.days.map((d) => [d.date, d]))
  const techs = Array.from(new Set(plan.days.flatMap((d) => d.techs.map((t) => t.tech))))
  const cols = `120px repeat(6, minmax(170px, 1fr))`
  return (
    <div style={{ overflowX: 'auto', marginTop: 8 }} role="region" aria-label="Week calendar">
      <div style={{ display: 'grid', gridTemplateColumns: cols, gap: 6, minWidth: 1140 }}>
        <div />
        {dates.map((d) => {
          const day = byDate.get(d)
          return (
            <div key={d} style={{ fontSize: 12, fontWeight: 600, color: day ? INK : MUTED,
              padding: '2px 4px' }}>
              {new Date(`${d}T12:00:00Z`).toLocaleDateString('en-US', { weekday: 'short',
                month: 'numeric', day: 'numeric', timeZone: 'UTC' })}
              {!day && <span style={{ fontWeight: 400 }}> · not planned</span>}
            </div>
          )
        })}
        {techs.map((tech) => (
          <TechRow key={tech} tech={tech} dates={dates} byDate={byDate} />
        ))}
      </div>
    </div>
  )
}

function TechRow({ tech, dates, byDate }: { tech: string; dates: string[]
  byDate: Map<string, DispatchPlan['days'][number]> }) {
  return (
    <>
      <div style={{ fontSize: 13, fontWeight: 600, color: INK, padding: '6px 4px' }}>{tech}</div>
      {dates.map((d) => {
        const t = byDate.get(d)?.techs.find((x) => x.tech === tech)
        return (
          <div key={d} data-testid={`cell-${tech}-${d}`} style={{ border: `1px solid ${LINE}`,
            borderRadius: 8, background: SOFT, padding: 6, minHeight: 64, minWidth: 0 }}>
            {t && t.visits.length > 0 ? (
              <>
                <div style={{ fontSize: 11, color: MUTED, marginBottom: 4 }}>
                  {t.count} of {t.capacity} · {driveLabel(t.drive_minutes)}</div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                  {t.visits.map((v) => <CalendarVisit key={`${v.job_uid}-${v.start}`} v={v} />)}
                </div>
              </>
            ) : <div style={{ fontSize: 11, color: MUTED }}>Free</div>}
          </div>
        )
      })}
    </>
  )
}

function CalendarVisit({ v }: { v: DispatchPlanVisit }) {
  const tentative = !v.existing && v.tentative
  const title = [v.why, ...(v.limits ?? [])].filter(Boolean).join(' · ')
  return (
    <div title={title || undefined} style={{ background: tentative ? AMBER_BG : '#fff',
      border: `1px solid ${tentative ? 'rgb(254,200,75)' : LINE}`, borderRadius: 6,
      padding: '3px 6px', fontSize: 12, color: v.existing ? MUTED : INK }}>
      {v.drive_minutes_before > 0 && <div style={{ fontSize: 10, color: MUTED }}>
        +{v.drive_minutes_before} min drive</div>}
      <div style={{ display: 'flex', gap: 4, alignItems: 'baseline', flexWrap: 'wrap' }}>
        <span style={{ fontWeight: 600 }}>{visitRange(v.start, v.end)}</span>
        <KindBadge kind={v.kind} muted={v.existing} />
      </div>
      <div style={{ overflowWrap: 'anywhere' }}>{visitTitle(v)}</div>
      {v.existing && <div style={{ fontSize: 10 }}>Booked</div>}
      {tentative && <div style={{ fontSize: 10, color: AMBER, fontWeight: 600 }}>Call first</div>}
      {!!v.limits?.length && <div style={{ fontSize: 10, color: MUTED }}>{v.limits.join('; ')}</div>}
      {!v.existing && v.zuper_url && <a href={v.zuper_url} target="_blank" rel="noreferrer"
        style={{ ...link, fontSize: 11 }}>Open in Zuper ↗</a>}
    </div>
  )
}

function NotPlaced({ plan }: { plan: DispatchPlan }) {
  if (plan.unplaced.length === 0) return null
  return (
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
        {t.visits.map((v) => <li key={`${v.job_uid}-${v.start}`}><CalendarVisit v={v} /></li>)}
      </ol>
    </div>
  )
}

function KindBadge({ kind, muted = false }: { kind: string; muted?: boolean }) {
  const repair = kind === 'repair'
  return (
    <span style={{ fontSize: 10, fontWeight: 500, borderRadius: 4, padding: '0 5px',
      color: muted ? MUTED : repair ? AMBER : 'rgb(23,92,211)',
      background: muted ? 'rgb(242,244,247)' : repair ? AMBER_BG : 'rgb(239,248,255)' }}>
      {KIND_LABEL[kind] ?? kind}</span>
  )
}
