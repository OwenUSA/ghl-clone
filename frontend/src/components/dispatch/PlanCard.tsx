import { useQuery } from '@tanstack/react-query'
import { lazy, Suspense, useMemo, useState } from 'react'
import { BLUE, INK, LINE } from '../ai/aiUi'
import { dispatchPlanExcelUrl, getDispatchPlan, type DispatchPlan, type DispatchPlanVisit }
  from '../../lib/api'
import { visitRange, visitTitle } from '../../lib/dispatch'

// The map (and its library) is fetched only when someone presses "Show map".
const PlanMap = lazy(() => import('./PlanMap').then((m) => ({ default: m.PlanMap })))

const MUTED = 'rgb(102,112,133)'
const FAINT = 'rgb(156,163,175)'
const ORANGE = 'rgb(234,88,12)'
const ORANGE_BG = 'rgb(255,241,230)'
const ORANGE_LINE = 'rgb(251,146,60)'
const RED = 'rgb(185,28,28)'
const AMBER = 'rgb(181,71,8)'

function weekOf(iso: string): string {
  const d = new Date(`${iso}T12:00:00Z`)
  d.setUTCDate(d.getUTCDate() - ((d.getUTCDay() + 6) % 7))
  return d.toISOString().slice(0, 10)
}
function addDays(iso: string, n: number): string {
  const d = new Date(`${iso}T12:00:00Z`)
  d.setUTCDate(d.getUTCDate() + n)
  return d.toISOString().slice(0, 10)
}
function short(iso: string, opts: Intl.DateTimeFormatOptions): string {
  return new Date(`${iso}T12:00:00Z`).toLocaleDateString('en-US', { ...opts, timeZone: 'UTC' })
}
function hm(iso: string | null | undefined): string {
  return iso ? new Date(iso).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit',
    timeZone: 'America/New_York' }) : ''
}

/**
 * The suggested schedule under the assistant's answer (2026-10-14, modelled on the office's
 * tech-schedule prototype): technicians × Mon–Sat, week by week — grey = already booked,
 * orange = suggested — with each day's load, end time and driving; who needs a decision; the
 * customers whose calls say when they cannot be there; who was left out and why; the map on
 * demand. A DRAFT: nothing is booked, the office sets each visit in Zuper.
 */
export function PlanCard({ planId }: { planId: number }) {
  const q = useQuery({ queryKey: ['dispatch-plan', planId], queryFn: () => getDispatchPlan(planId),
    staleTime: Infinity })
  if (q.isLoading) return <Shell><span style={{ color: MUTED, fontSize: 13 }}>Loading the plan…</span></Shell>
  if (q.isError || !q.data) return <Shell><span style={{ color: MUTED, fontSize: 13 }}>
    The plan could not be loaded (it may belong to another person).</span></Shell>
  return <PlanBody plan={q.data} planId={planId} />
}

function Shell({ children }: { children: React.ReactNode }) {
  return <div style={{ marginTop: 12, border: `1px solid ${LINE}`, borderRadius: 12, background: '#fff',
    padding: '12px 14px' }}>{children}</div>
}

function PlanBody({ plan, planId }: { plan: DispatchPlan; planId: number }) {
  const weeks = useMemo(() => Array.from(new Set(plan.days.map((d) => weekOf(d.date)))).sort(), [plan])
  const [week, setWeek] = useState(0)
  const [showMap, setShowMap] = useState(false)
  const monday = weeks[week]
  const dates = useMemo(() => (monday ? Array.from({ length: 6 }, (_, i) => addDays(monday, i)) : []),
    [monday])
  const byDate = new Map(plan.days.map((d) => [d.date, d]))
  const techs = Array.from(new Set(plan.days.flatMap((d) => d.techs.map((t) => t.tech))))
  const all = plan.days.flatMap((d) => d.techs.flatMap((t) => t.visits))
  const suggested = all.filter((v) => !v.existing)
  const callFirst = suggested.filter((v) => v.tentative).length
  const drive = plan.days.reduce((n, d) => n + d.techs.reduce((m, t) => m + t.drive_minutes, 0), 0)
  return (
    <Shell>
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
        <span style={{ fontWeight: 600, color: INK, fontSize: 14 }}>Suggested schedule · plan #{planId}</span>
        <span style={{ fontSize: 12, color: MUTED }}>a draft — nothing is booked</span>
        <span style={{ marginLeft: 'auto', display: 'flex', gap: 10, fontSize: 12 }}>
          <a href={dispatchPlanExcelUrl(planId)} download style={{ color: BLUE, fontWeight: 500 }}>Excel</a>
          <a href={`/dispatch?tab=book&plan=${planId}`} style={{ color: BLUE, fontWeight: 500 }}>
            Full calendar</a>
        </span>
      </div>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 8 }}>
        <Stat label="suggested" value={suggested.length} tone={ORANGE} bg={ORANGE_BG} line={ORANGE_LINE} />
        <Stat label="already booked" value={all.length - suggested.length} />
        <Stat label="call first" value={callFirst} tone={callFirst ? AMBER : undefined} />
        <Stat label="need a decision" value={plan.unplaced.length} tone={plan.unplaced.length ? RED : undefined} />
        <Stat label="driving" value={`${(drive / 60).toFixed(1)}h`} />
        {!!plan.flags?.length && <Stat label="customers with limits" value={plan.flags.length} />}
      </div>

      {weeks.length > 0 && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 12 }}>
          <NavBtn label="‹" title="Previous week" disabled={week === 0} onClick={() => setWeek(week - 1)} />
          <div style={{ minWidth: 190, textAlign: 'center' }}>
            <div style={{ fontWeight: 600, color: INK, fontSize: 14 }}>
              {short(dates[0], { month: 'short', day: 'numeric' })} – {short(dates[5], { month: 'short',
                day: 'numeric' })}</div>
            <div style={{ fontSize: 11, color: MUTED }}>Week {week + 1} of {weeks.length}</div>
          </div>
          <NavBtn label="›" title="Next week" disabled={week >= weeks.length - 1}
            onClick={() => setWeek(week + 1)} />
          <span style={{ fontSize: 12, color: MUTED, marginLeft: 8 }}>
            grey = booked · <b style={{ color: ORANGE }}>orange = suggested</b></span>
        </div>
      )}

      {weeks.length > 0 && (
        <div style={{ overflowX: 'auto', marginTop: 8 }} role="region" aria-label="Week plan">
          <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 980, tableLayout: 'fixed' }}>
            <colgroup><col style={{ width: 120 }} />{dates.map((d) => <col key={d} />)}</colgroup>
            <thead>
              <tr>
                <th style={th}>Technician</th>
                {dates.map((d) => {
                  const n = byDate.get(d)?.techs.reduce((k, t) => k + t.visits.filter((v) => !v.existing).length, 0) ?? 0
                  return (
                    <th key={d} style={th}>
                      <span style={{ color: INK, fontSize: 13 }}>{short(d, { weekday: 'short', month: 'short', day: 'numeric' })}</span>
                      <div style={{ minHeight: 18, marginTop: 2 }}>
                        {n > 0 ? <Pill>+{n} suggested</Pill>
                          : !byDate.get(d) && <span style={{ color: FAINT, fontWeight: 400 }}>not planned</span>}</div>
                    </th>
                  )
                })}
              </tr>
            </thead>
            <tbody>
              {techs.map((tech) => (
                <tr key={tech}>
                  <td style={{ ...td, fontWeight: 600, color: INK, whiteSpace: 'nowrap' }}>{tech}</td>
                  {dates.map((d) => {
                    const t = byDate.get(d)?.techs.find((x) => x.tech === tech)
                    const visits = t?.visits ?? []
                    const booked = visits.filter((v) => v.existing).length
                    const sug = visits.length - booked
                    const fill = t ? Math.min(100, (t.count / Math.max(1, t.capacity)) * 100) : 0
                    return (
                      <td key={d} style={td}>
                        {t && visits.length ? (
                          <>
                            <div style={{ height: 4, background: 'rgb(241,242,244)', borderRadius: 3,
                              overflow: 'hidden', marginBottom: 4, display: 'flex' }}>
                              <span style={{ width: `${(booked / Math.max(1, t.capacity)) * 100}%`, background: 'rgb(203,213,225)' }} />
                              <span style={{ width: `${Math.max(0, fill - (booked / Math.max(1, t.capacity)) * 100)}%`, background: ORANGE }} />
                            </div>
                            <div style={{ fontSize: 11, color: MUTED }}>
                              {booked} booked {sug > 0 && <Pill>+{sug}</Pill>}</div>
                            <div style={{ fontSize: 11, color: MUTED }}>
                              ends {hm(t.last_end ?? visits[visits.length - 1]?.end)} · ~{t.drive_minutes} min drive</div>
                            <div style={{ marginTop: 4, display: 'flex', flexDirection: 'column', gap: 2 }}>
                              {visits.map((v, i) => <Stop key={`${v.job_uid}-${v.start}`} v={v} n={i + 1} />)}
                            </div>
                          </>
                        ) : <span style={{ fontSize: 11, color: FAINT }}>{t ? 'Free' : '—'}</span>}
                      </td>
                    )
                  })}
                </tr>
              ))}
              {plan.unplaced.length > 0 && (
                <tr>
                  <td style={{ ...td, background: 'rgb(255,250,250)' }}>
                    <b style={{ color: INK, fontSize: 12 }}>Needs a decision</b></td>
                  <td colSpan={dates.length} style={{ ...td, background: 'rgb(255,250,250)' }}>
                    {plan.unplaced.map((u) => (
                      <div key={u.job_uid} style={{ fontSize: 12, padding: '1px 0' }}>
                        <span style={{ color: RED, fontWeight: 600 }}>! {visitTitle(u)}</span>
                        <span style={{ color: MUTED }}> — {u.reason}</span>
                        {u.zuper_url && <a href={u.zuper_url} target="_blank" rel="noreferrer"
                          style={{ color: BLUE, marginLeft: 6 }}>Zuper ↗</a>}
                      </div>
                    ))}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {!!plan.flags?.length && (
        <Section title={`Customers with times they cannot do (${plan.flags.length}) — from their calls and texts`}>
          {plan.flags.map((f) => (
            <li key={`${f.job_number}`}><b>{visitTitle(f)}</b>
              <span style={{ color: MUTED }}> — {f.limits.join('; ')}</span></li>
          ))}
        </Section>
      )}
      {!!plan.left_out?.length && (
        <Section title={`Left out of the plan (${plan.left_out.length})`}>
          {plan.left_out.map((x) => (
            <li key={`${x.job_number}`}><b>{visitTitle(x)}</b>
              <span style={{ color: MUTED }}> — {x.reason}</span></li>
          ))}
        </Section>
      )}

      {weeks.length > 0 && (
        <div style={{ marginTop: 10 }}>
          <button type="button" onClick={() => setShowMap(!showMap)} aria-expanded={showMap}
            style={{ fontSize: 13, color: BLUE, fontWeight: 500 }}>
            {showMap ? 'Hide map' : 'Show map'}</button>
          {showMap && (
            <Suspense fallback={<div style={{ fontSize: 12, color: MUTED }}>Loading the map…</div>}>
              <PlanMap plan={plan} dates={dates} />
            </Suspense>
          )}
        </div>
      )}
      <div style={{ marginTop: 8, fontSize: 11, color: MUTED }}>
        A draft. Confirm each suggested visit with the customer, then set it in Zuper (date, time,
        Technician field). Driving is an estimate.</div>
    </Shell>
  )
}

const th: React.CSSProperties = { borderBottom: `1px solid ${LINE}`, padding: '6px 8px', textAlign: 'left',
  verticalAlign: 'top', fontSize: 12, color: MUTED, fontWeight: 600 }
const td: React.CSSProperties = { borderBottom: `1px solid ${LINE}`, padding: '6px 8px',
  verticalAlign: 'top', textAlign: 'left' }

function Stop({ v, n }: { v: DispatchPlanVisit; n: number }) {
  const sug = !v.existing
  const tip = [visitTitle(v), visitRange(v.start, v.end), v.why, ...(v.limits ?? [])]
    .filter(Boolean).join(' · ')
  return (
    <div title={tip} style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 12, lineHeight: 1.3,
      padding: '2px 4px', borderRadius: 5, minWidth: 0,
      background: sug ? ORANGE_BG : 'transparent', border: `1px solid ${sug ? ORANGE_LINE : 'transparent'}`,
      color: sug ? 'rgb(154,52,18)' : 'rgb(75,85,99)', fontWeight: sug ? 600 : 400 }}>
      <span style={{ width: 15, height: 15, borderRadius: '50%', flex: 'none', fontSize: 9, fontWeight: 700,
        display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
        background: sug ? ORANGE : 'rgb(229,231,235)', color: sug ? '#fff' : MUTED }}>{n}</span>
      <span style={{ color: sug ? 'rgb(194,65,12)' : FAINT, flex: 'none' }}>{hm(v.start)}</span>
      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1, minWidth: 0 }}>
        {v.job_number ? `#${v.job_number} ` : ''}{v.customer ?? 'Booked'}</span>
      {sug && v.tentative && <span style={{ fontSize: 9, color: AMBER, flex: 'none' }}>call</span>}
    </div>
  )
}

function Stat({ label, value, tone, bg, line }: { label: string; value: number | string; tone?: string
  bg?: string; line?: string }) {
  return (
    <div style={{ border: `1px solid ${line ?? LINE}`, background: bg ?? '#fff', borderRadius: 10,
      padding: '4px 10px', minWidth: 84 }}>
      <div style={{ fontSize: 17, fontWeight: 700, color: tone ?? INK }}>{value}</div>
      <div style={{ fontSize: 11, color: MUTED }}>{label}</div>
    </div>
  )
}

function Pill({ children }: { children: React.ReactNode }) {
  return <span style={{ display: 'inline-block', background: ORANGE, color: '#fff', borderRadius: 999,
    padding: '0 7px', fontSize: 11, fontWeight: 700, whiteSpace: 'nowrap' }}>{children}</span>
}

function NavBtn({ label, title, disabled, onClick }: { label: string; title: string; disabled: boolean
  onClick: () => void }) {
  return <button type="button" title={title} aria-label={title} disabled={disabled} onClick={onClick}
    style={{ width: 30, height: 30, border: `1px solid ${LINE}`, borderRadius: 8, background: '#fff',
      fontSize: 17, opacity: disabled ? 0.4 : 1 }}>{label}</button>
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ marginTop: 10 }}>
      <div style={{ fontSize: 13, fontWeight: 600, color: INK }}>{title}</div>
      <ul style={{ margin: '4px 0 0', paddingLeft: 18, fontSize: 12, color: INK }}>{children}</ul>
    </div>
  )
}
