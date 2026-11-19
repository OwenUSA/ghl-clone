import { BLUE, INK } from '../ai/aiUi'
import type { DispatchPlan } from '../../lib/api'
import { visitTitle, WAITING_ON_LABEL, waitingGroups } from '../../lib/dispatch'

const MUTED = 'rgb(102,112,133)'
const AMBER = 'rgb(181,71,8)'
const SKY = 'rgb(23,92,211)'

function hm(iso: string): string {
  return new Date(iso).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit',
    timeZone: 'America/New_York' })
}

/**
 * The owner's rules on a plan (2026-11-19), the same under Owen AI's answer, the Dispatch chat
 * and Dispatch → Remake the schedule: the repairs left out until the customer's OK is found
 * (grouped, with what was checked), and the rain flags — next day first, a FLAG only.
 */
export function PlanExtras({ plan }: { plan: DispatchPlan }) {
  const waiting = plan.waiting_ok ?? []
  const w = plan.weather
  return (
    <>
      {waiting.length > 0 && (
        <div style={{ marginTop: 10 }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: INK }}>
            Repairs waiting for the customer's OK ({waiting.length}) — not on the plan</div>
          <div style={{ fontSize: 11, color: MUTED }}>
            A repair goes on the schedule only with an accepted proposal, AHS's authorization or a
            yes from the customer in a call or text.</div>
          {waitingGroups(waiting).map(([on, rows]) => (
            <div key={on} style={{ marginTop: 4 }}>
              <div style={{ fontSize: 12, fontWeight: 600, color: AMBER }}>
                {WAITING_ON_LABEL[on] ?? on} ({rows.length})</div>
              <ul style={{ margin: '2px 0 0', paddingLeft: 18, fontSize: 12, color: INK }}>
                {rows.map((r) => (
                  <li key={r.job_uid}><b>{visitTitle(r)}</b>
                    <span style={{ color: MUTED }}> — {r.stage ? `${r.stage} · ` : ''}{r.checked}</span>
                    {r.zuper_url && <a href={r.zuper_url} target="_blank" rel="noreferrer"
                      style={{ color: BLUE, marginLeft: 6 }}>Zuper ↗</a>}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
      {w && (
        <div style={{ marginTop: 10 }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: INK }}>
            Rain{w.enabled ? ` (${w.flagged?.length ?? 0} flagged)` : ''}</div>
          {!w.enabled ? <div style={{ fontSize: 12, color: MUTED }}>{w.note}</div> : (
            <>
              {(w.flagged ?? []).length === 0
                ? <div style={{ fontSize: 12, color: MUTED }}>
                  No visit has a {w.rain_pct}%+ chance of rain in its hours.</div>
                : <ul style={{ margin: '2px 0 0', paddingLeft: 18, fontSize: 12, color: INK }}>
                  {(w.flagged ?? []).map((f) => (
                    <li key={`${f.job_number}-${f.start}`}>
                      <b style={{ color: f.next_day ? SKY : INK }}>{f.next_day ? 'Next day · ' : ''}
                        {f.label} {hm(f.start)}</b> — {f.tech.split(' ')[0]} · {visitTitle({
                        job_number: f.job_number, customer: f.customer, city: null })}
                      <span style={{ color: MUTED }}> — {f.text}{f.less_certain ? ' (less certain)' : ''}
                        {f.existing ? ' · already booked' : ''}</span>
                    </li>
                  ))}
                </ul>}
              <div style={{ fontSize: 11, color: MUTED }}>
                {w.note}{w.no_forecast ? ` ${w.no_forecast} visit(s) have no forecast yet.` : ''}</div>
            </>
          )}
        </div>
      )}
    </>
  )
}
