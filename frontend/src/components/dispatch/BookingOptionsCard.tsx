import { INK, LINE } from '../ai/aiUi'
import type { DispatchBookingOptions } from '../../lib/api'
import { KIND_LABEL, visitRange } from '../../lib/dispatch'

const MUTED = 'rgb(102,112,133)'
const ORANGE = 'rgb(234,88,12)'
const ORANGE_BG = 'rgb(255,241,230)'
const GREEN = 'rgb(2,122,72)'
const AMBER = 'rgb(181,71,8)'
const RED = 'rgb(180,35,24)'

const FIT_TONE: Record<string, string> = { 'Great fit': GREEN, 'Good fit': AMBER, Detour: RED }

function dayLabel(iso: string): string {
  return new Date(iso).toLocaleDateString('en-US', { weekday: 'short', month: 'numeric',
    day: 'numeric', timeZone: 'America/New_York' })
}
function timeLabel(iso: string): string {
  return new Date(iso).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit',
    timeZone: 'America/New_York' })
}

/**
 * The 3 best times to book one job, under the assistant's answer (2026-10-14, the
 * booking_options tool): each with technician, day, time, how well it fits, his day and why.
 * What the customer said about when (from calls and texts) is listed first — the options
 * already respect it. Nothing is booked here: the office sets it in Zuper.
 */
export function BookingOptionsCard({ data }: { data: DispatchBookingOptions }) {
  return (
    <div style={{ marginTop: 12, border: `1px solid ${LINE}`, borderRadius: 12, background: '#fff',
      padding: '12px 14px' }}>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'baseline' }}>
        <span style={{ fontWeight: 600, color: INK, fontSize: 14 }}>
          Options for #{data.job_number} {data.customer}</span>
        <span style={{ fontSize: 12, color: MUTED }}>
          {[KIND_LABEL[data.kind] ?? data.kind, data.city, data.stage && `now: ${data.stage}`,
            data.visit_now && `visit in Zuper: ${data.visit_now}`].filter(Boolean).join(' · ')}</span>
      </div>
      {!!data.limits?.length && (
        <div style={{ marginTop: 6, fontSize: 12, color: INK, background: 'rgb(249,250,251)',
          borderRadius: 8, padding: '6px 8px' }}>
          <b>What the customer said about when</b> (respected below):
          <ul style={{ margin: '2px 0 0', paddingLeft: 18 }}>
            {data.limits.map((l, i) => <li key={i}>{l}</li>)}</ul>
        </div>
      )}
      {data.note && <div style={{ marginTop: 6, fontSize: 13, color: data.options.length ? MUTED : RED }}>
        {data.note}</div>}
      {data.options.length > 0 && (
        <ol style={{ margin: '8px 0 0', padding: 0, listStyle: 'none', display: 'flex',
          flexDirection: 'column', gap: 6 }}>
          {data.options.map((o, i) => (
            <li key={`${o.tech}-${o.start}`} style={{ display: 'flex', gap: 10, alignItems: 'flex-start',
              border: `1px solid ${i === 0 ? 'rgb(251,146,60)' : LINE}`,
              background: i === 0 ? ORANGE_BG : '#fff', borderRadius: 10, padding: '8px 10px' }}>
              <span style={{ width: 22, height: 22, borderRadius: '50%', flex: 'none',
                background: i === 0 ? ORANGE : 'rgb(229,231,235)', color: i === 0 ? '#fff' : INK,
                fontSize: 12, fontWeight: 700, display: 'flex', alignItems: 'center',
                justifyContent: 'center' }}>{i + 1}</span>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'baseline' }}>
                  <span style={{ fontWeight: 600, color: INK }}>
                    {dayLabel(o.start)} · {visitRange(o.start, o.end)}</span>
                  <span style={{ color: INK }}>{o.tech}</span>
                  <span style={{ fontSize: 12, fontWeight: 600, color: FIT_TONE[o.fit] ?? MUTED }}>
                    {o.fit}</span>
                  {i === 0 && <span style={{ fontSize: 11, fontWeight: 700, color: ORANGE }}>
                    RECOMMENDED</span>}
                </div>
                <div style={{ fontSize: 12, color: MUTED, marginTop: 2 }}>{o.why}</div>
                <div style={{ fontSize: 12, color: MUTED }}>
                  {o.day_count ? `${o.day_count} visit${o.day_count === 1 ? '' : 's'} booked that day`
                    + (o.day_ends ? `, last one ends ${timeLabel(o.day_ends)}` : '')
                    : 'Nothing booked that day yet'}</div>
              </div>
            </li>
          ))}
        </ol>
      )}
      <div style={{ marginTop: 8, fontSize: 11, color: MUTED }}>
        Agree the time with the customer, then set it in Zuper: date, time and the Technician
        field. Driving is an estimate (straight line).</div>
    </div>
  )
}
