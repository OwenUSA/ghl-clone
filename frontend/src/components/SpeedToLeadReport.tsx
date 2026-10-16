import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import {
  getSpeedToLead, type IncomingOutcome, type SpeedColumn, type SpeedLead, type SpeedReport, type SpeedResult,
  type SpeedRow, type SpeedSummary,
} from '../lib/api'
import { whenLabel } from '../lib/accountTime'
import { csvFilename, toCsv } from '../lib/csv'
import { downloadCsv } from '../lib/download'

/**
 * Speed to lead (2026-10-15) — Reporting's tab, backed by app/dispatch/speed.py.
 *
 * How fast a PERSON first answers a new lead: from the job's creation in Zuper (or the
 * customer's own call / text in the 3 days before) to our first call, text or answered call,
 * in BUSINESS minutes (Mon-Sat 8-6 New York). Targets are the owner's: AHS 2 h, Retail 1 h.
 * Every figure comes from the server; this file only lays it out.
 */

const GREEN = 'rgb(18,183,106)'
const RED = 'rgb(217,45,32)'
const AMBER = 'rgb(247,144,9)'
const GREY = 'rgb(102,112,133)'
const INK = 'rgb(16,24,40)'
const LINE = 'rgb(234,236,240)'

const RESULT_COLOR: Record<SpeedResult, string> = {
  on_time: GREEN, late: AMBER, not_contacted: RED, waiting: GREY, no_phone: GREY,
}

/** "25 min", "4 h 40 min" — business time. */
function minutes(m: number | null | undefined): string {
  if (m === null || m === undefined) return '—'
  if (m < 60) return `${m} min`
  const h = Math.floor(m / 60)
  const r = m % 60
  return r ? `${h} h ${r} min` : `${h} h`
}

/** Business time, and business days (10 h each) once it is that long. */
function span(m: number | null | undefined): string {
  if (m === null || m === undefined) return '—'
  return m >= 600 ? `${minutes(m)} (${(m / 600).toFixed(1)} days)` : minutes(m)
}

const pct = (n: number | null | undefined) => (n === null || n === undefined ? '—' : `${n}%`)
const when = (iso: string | null) => (iso ? whenLabel(new Date(iso)) : '—')

function Tile({ label, value, sub, color }: {
  label: string; value: string; sub?: string; color?: string
}) {
  return (
    <div data-tile className="bg-white"
      style={{ borderRadius: 8, border: `1px solid ${LINE}`, padding: 16, minWidth: 0 }}>
      <div style={{ fontSize: 14, fontWeight: 500, color: 'rgb(52,64,84)' }}>{label}</div>
      <div style={{ fontSize: 34, fontWeight: 500, lineHeight: '44px', color: color ?? INK,
        marginTop: 6 }}>{value}</div>
      {sub && <div style={{ fontSize: 13, color: GREY, marginTop: 4 }}>{sub}</div>}
    </div>
  )
}

function Box({ title, note, children }: {
  title: string; note?: string; children: React.ReactNode
}) {
  return (
    <section className="bg-white"
      style={{ borderRadius: 8, border: `1px solid ${LINE}`, minWidth: 0 }}>
      <div className="px-4" style={{ paddingTop: 14, paddingBottom: 12,
        borderBottom: '1px solid rgb(242,244,247)' }}>
        <div style={{ fontSize: 17, fontWeight: 500, color: INK }}>{title}</div>
        {note && <div style={{ fontSize: 13, color: GREY, marginTop: 2 }}>{note}</div>}
      </div>
      <div style={{ padding: 16, overflowX: 'auto' }}>{children}</div>
    </section>
  )
}

const th: React.CSSProperties = {
  height: 36, fontSize: 13, fontWeight: 500, color: 'rgb(52,64,84)', padding: '0 10px',
  borderBottom: `1px solid ${LINE}`, whiteSpace: 'nowrap', textAlign: 'left',
}
const td: React.CSSProperties = {
  height: 40, fontSize: 14, color: 'rgb(52,64,84)', padding: '0 10px',
  borderBottom: '1px solid rgb(242,244,247)', whiteSpace: 'nowrap',
}

/** On-time rate as a bar, so a week or a person that falls behind stands out. */
function RateBar({ value }: { value: number | null }) {
  if (value === null) return <span style={{ color: GREY }}>—</span>
  const color = value >= 80 ? GREEN : value >= 50 ? AMBER : RED
  return (
    <span className="flex items-center gap-2">
      <span style={{ width: 80, height: 8, borderRadius: 4, backgroundColor: 'rgb(242,244,247)' }}>
        <span style={{ display: 'block', width: `${value}%`, height: 8, borderRadius: 4,
          backgroundColor: color }} />
      </span>
      <span style={{ minWidth: 36 }}>{value}%</span>
    </span>
  )
}

function Breakdown({ rows, first, empty }: { rows: SpeedRow[]; first: string; empty: string }) {
  if (rows.length === 0) return <div style={{ fontSize: 14, color: GREY }}>{empty}</div>
  return (
    <table className="w-full">
      <thead>
        <tr>
          {[first, 'Leads', 'On time', 'Median first response', '90% within', 'Not contacted']
            .map((h) => <th key={h} style={th}>{h}</th>)}
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.name}>
            <td style={td}>{r.name}</td>
            <td style={td}>{r.leads}</td>
            <td style={td}><RateBar value={r.on_time_pct} /></td>
            <td style={td}>{minutes(r.response_median)}</td>
            <td style={td}>{minutes(r.response_p90)}</td>
            <td style={{ ...td, color: r.not_contacted ? RED : GREY }}>{r.not_contacted}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function Headline({ s, targets, group }: {
  s: SpeedSummary; targets: SpeedReport['targets']; group: '' | 'AHS' | 'Retail'
}) {
  const judged = s.on_time + s.late + s.not_contacted
  const target = group === 'AHS' ? `AHS ${minutes(targets.AHS)}`
    : group === 'Retail' ? `Retail ${minutes(targets.Retail)}`
      : `AHS ${minutes(targets.AHS)} · Retail ${minutes(targets.Retail)}`
  return (
    <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(min(190px, 100%), 1fr))' }}>
      <Tile label="Time to first call" value={minutes(s.call_median)}
        sub={`typical (median) · average ${minutes(s.call_avg)} · ${pct(s.called_pct)} of leads called, ${s.not_called} not called yet`} />
      <Tile label="First call picked up" value={pct(s.first_call_answered_pct)}
        color={s.first_call_answered_pct === null ? INK : s.first_call_answered_pct >= 70 ? GREEN : AMBER}
        sub={`${pct(s.first_call_reached_pct)} of first calls became a real conversation (30 s or more)`} />
      <Tile label="Time to reach the customer" value={minutes(s.talk_median)}
        sub={`typical · average ${minutes(s.talk_avg)} · ${pct(s.reached_pct)} reached · ${s.attempts_before_talk_avg ?? '—'} unanswered calls first`} />
      <Tile label="First contact (call or text)" value={minutes(s.response_median)}
        sub={`typical · average ${minutes(s.response_avg)} · a text can come before the first call`} />
      <Tile label="Contacted on time" value={pct(s.on_time_pct)}
        color={s.on_time_pct === null ? INK : s.on_time_pct >= 80 ? GREEN : s.on_time_pct >= 50 ? AMBER : RED}
        sub={`${s.on_time} of ${judged} leads · first contact within ${target}`} />
      <Tile label="Not contacted" value={String(s.not_contacted)}
        color={s.not_contacted ? RED : INK}
        sub={`${s.late} late · ${s.waiting} still within target${s.no_phone ? ` · ${s.no_phone} with no phone` : ''}`} />
      <Tile label="Time to booked" value={minutes(s.booked_median)}
        sub={`typical, lead to Scheduled · average ${minutes(s.booked_avg)} · ${pct(s.booked_pct)} booked`} />
      <Tile label="Leads" value={String(s.leads)}
        sub={`${s.customer_first} reached out to us first · Zuper column moved a typical ${minutes(s.zuper_move_median)} after arriving`} />
    </div>
  )
}

const FILTERS: { key: '' | SpeedResult; label: string }[] = [
  { key: '', label: 'All' }, { key: 'not_contacted', label: 'Not contacted' },
  { key: 'late', label: 'Late' }, { key: 'on_time', label: 'On time' },
  { key: 'waiting', label: 'Waiting' },
]

function leadsCsv(leads: SpeedLead[]): string {
  return toCsv(
    ['Job #', 'Customer', 'Board', 'Lead came in', 'Started by', 'Arrived', 'First call',
      'First call how', 'First call by', 'Minutes to first call', 'Not called yet (minutes so far)',
      'First call picked up', 'First call reached them', 'Minutes to reach', 'Calls to reach',
      'First contact (any)', 'First contact how', 'Minutes to first contact', 'Clock minutes',
      'Target minutes', 'Result', 'Their missed calls', 'Minutes to booked', 'Zuper first move',
      'Moved to', 'Minutes to Zuper move'],
    leads.map((r) => [r.job_number, r.customer_name, r.board, when(r.start_at), r.started_by,
      r.arrived, when(r.first_call_at), r.first_call_how, r.first_call_by, r.call_minutes,
      r.not_called_minutes, yesNo(r.first_call_answered), yesNo(r.first_call_reached),
      r.talk_minutes, r.calls_to_reach, when(r.first_response_at), r.first_response_how,
      r.response_minutes, r.response_clock_minutes, r.target_minutes, r.result_label,
      r.missed_their_calls, r.booked_minutes, when(r.first_move_at), r.first_move_to,
      r.move_minutes]))
}

const yesNo = (v: boolean | null) => (v === null ? '' : v ? 'yes' : 'no')

function pickedUp(r: SpeedLead): { text: string; color: string } {
  if (r.first_call_answered === null) return { text: '—', color: GREY }
  if (r.first_call_reached) return { text: 'Answered — talked', color: GREEN }
  if (r.first_call_answered) return { text: 'Answered, short call', color: AMBER }
  return { text: 'No answer', color: AMBER }
}

function Leads({ leads, showBoard }: { leads: SpeedLead[]; showBoard: boolean }) {
  const [only, setOnly] = useState<'' | SpeedResult>('')
  const shown = only ? leads.filter((l) => l.result === only) : leads
  return (
    <Box title="Latest leads"
      note="Newest first: when each lead came in, how long until the first call (ours on Quo or Zuper, answered or not — or theirs that we picked up), and how long until we actually reached them. Business time, Mon–Sat 8 AM–6 PM.">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        {FILTERS.map((f) => {
          const n = f.key ? leads.filter((l) => l.result === f.key).length : leads.length
          return (
            <button key={f.key || 'all'} onClick={() => setOnly(f.key)}
              aria-pressed={only === f.key}
              style={{ height: 30, padding: '0 12px', borderRadius: 15, fontSize: 13,
                border: `1px solid ${only === f.key ? 'rgb(21,112,239)' : LINE}`,
                color: only === f.key ? 'rgb(21,112,239)' : 'rgb(52,64,84)',
                backgroundColor: only === f.key ? 'rgb(239,244,255)' : '#fff' }}>
              {f.label} · {n}
            </button>
          )
        })}
        <button onClick={() => downloadCsv(csvFilename('speed-to-lead', new Date()), leadsCsv(shown))}
          disabled={shown.length === 0}
          style={{ marginLeft: 'auto', height: 32, padding: '0 12px', borderRadius: 8,
            border: `1px solid ${LINE}`, fontSize: 13, fontWeight: 500, backgroundColor: '#fff',
            color: 'rgb(52,64,84)' }}>
          Export CSV
        </button>
      </div>
      {shown.length === 0 ? <div style={{ fontSize: 14, color: GREY }}>No leads here.</div> : (
        <table className="w-full" aria-label="Speed to lead, every lead">
          <thead>
            <tr>
              {['Job', ...(showBoard ? ['Board'] : []), 'Lead came in', 'First call',
                'Time to first call', 'Picked up?', 'Reached after', 'Calls to reach',
                'First contact (any)', 'Result']
                .map((h) => <th key={h} style={th}>{h}</th>)}
            </tr>
          </thead>
          <tbody>
            {shown.map((r) => {
              const pick = pickedUp(r)
              return (
                <tr key={r.job_uid} data-result={r.result}>
                  <td style={td}>
                    {r.zuper_url
                      ? <a href={r.zuper_url} target="_blank" rel="noreferrer"
                        style={{ color: 'rgb(21,112,239)' }}>#{r.job_number}</a>
                      : `#${r.job_number ?? '?'}`}
                    <div style={{ fontSize: 12, color: GREY }}>{r.customer_name}</div>
                  </td>
                  {showBoard && <td style={td}>{r.board}</td>}
                  <td style={td}>
                    {when(r.start_at)}
                    <div style={{ fontSize: 12, color: GREY }}>{r.started_by}</div>
                  </td>
                  <td style={td}>
                    {r.first_call_at ? <>
                      {when(r.first_call_at)}
                      <div style={{ fontSize: 12, color: GREY }}>{r.first_call_how} · {r.first_call_by}</div>
                    </> : <span style={{ color: GREY }}>—</span>}
                  </td>
                  <td style={{ ...td, fontWeight: 500 }} data-col="time-to-first-call">
                    {r.call_minutes !== null ? minutes(r.call_minutes)
                      : <span style={{ color: RED }}>Not called yet
                        <div style={{ fontSize: 12, fontWeight: 400 }}>{minutes(r.not_called_minutes)} so far</div>
                      </span>}
                  </td>
                  <td style={{ ...td, color: pick.color }}>{pick.text}</td>
                  <td style={td}>{r.talk_minutes !== null ? minutes(r.talk_minutes)
                    : <span style={{ color: GREY }}>not yet</span>}</td>
                  <td style={td}>{r.calls_to_reach}</td>
                  <td style={td}>
                    {minutes(r.response_minutes)}
                    {r.first_response_how && r.first_response_at !== r.first_call_at && (
                      <div style={{ fontSize: 12, color: GREY }}>{r.first_response_how}</div>)}
                  </td>
                  <td style={td}>
                    <span style={{ fontSize: 12, fontWeight: 500, padding: '2px 8px', borderRadius: 10,
                      color: RESULT_COLOR[r.result], border: `1px solid ${RESULT_COLOR[r.result]}` }}>
                      {r.result_label}
                    </span>
                    {r.ai_handled && <div style={{ fontSize: 12, color: GREY }}>AI agent spoke first</div>}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}
    </Box>
  )
}

function ColumnTable({ board, jobs, columns }: {
  board: string; jobs: number; columns: SpeedColumn[]
}) {
  const top = columns[0]
  return (
    <Box title={board}
      note={top ? `Most time goes to “${top.name}” (${top.share_pct}% of all the time jobs spent on this board). ${jobs} column moves in this range.` : undefined}>
      <table className="w-full" aria-label={`Time in each column, ${board}`}>
        <thead>
          <tr>
            {['Column', 'Share of time', 'Jobs in', 'Average stay', 'Median', '90% within',
              'In it now', 'Longest wait now'].map((h) => <th key={h} style={th}>{h}</th>)}
          </tr>
        </thead>
        <tbody>
          {columns.map((x) => (
            <tr key={x.name} data-column={x.name}>
              <td style={td}>{x.name}</td>
              <td style={td}>
                <span className="flex items-center gap-2">
                  <span style={{ width: 90, height: 8, borderRadius: 4, backgroundColor: 'rgb(242,244,247)' }}>
                    <span style={{ display: 'block', width: `${x.share_pct}%`, height: 8,
                      borderRadius: 4, backgroundColor: 'rgb(83,155,245)' }} />
                  </span>
                  {x.share_pct}%
                </span>
              </td>
              <td style={td}>{x.entered}</td>
              <td style={{ ...td, fontWeight: 500 }}>{span(x.avg_minutes)}</td>
              <td style={td}>{span(x.median_minutes)}</td>
              <td style={td}>{span(x.p90_minutes)}</td>
              <td style={td}>{x.now_in}</td>
              <td style={{ ...td, color: (x.longest_now_minutes ?? 0) >= 1200 ? RED : 'rgb(52,64,84)' }}>
                {span(x.longest_now_minutes)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Box>
  )
}

const OUTCOME_COLOR: Record<IncomingOutcome, string> = {
  answered: GREEN, missed: RED, voicemail: AMBER, ai_answered: 'rgb(140,141,222)',
  ai_transferred: 'rgb(83,155,245)', unknown: GREY,
}
const OUTCOME_ORDER: IncomingOutcome[] = [
  'answered', 'missed', 'voicemail', 'ai_answered', 'ai_transferred', 'unknown']

function IncomingCalls({ incoming }: { incoming: SpeedReport['incoming'] }) {
  const rows = incoming.rows
  return (
    <Box title="Incoming calls by line"
      note="Every incoming call in this range on Quo, Zuper Connect and the CRM line — customers, new leads and spam alike — and how it ended. Nothing is forwarded to another number on any line: Quo's forwarding is never used and Zuper Connect has no such status. 'Redirected' here means sent to voicemail, answered by an AI agent, or transferred by the AI agent to a person.">
      {rows.length === 0 || rows[rows.length - 1].total === 0
        ? <div style={{ fontSize: 14, color: GREY }}>No incoming calls in this range.</div> : (
          <table className="w-full" aria-label="Incoming calls by line">
            <thead>
              <tr>
                {['Line', 'Calls', ...OUTCOME_ORDER.map((o) => incoming.labels[o])]
                  .map((h) => <th key={h} style={th}>{h}</th>)}
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.name} data-line={r.name}
                  style={r.name === 'All lines' ? { fontWeight: 500 } : undefined}>
                  <td style={td}>{r.name}</td>
                  <td style={td}>{r.total}</td>
                  {OUTCOME_ORDER.map((o) => (
                    <td key={o} style={{ ...td, color: r[o] ? OUTCOME_COLOR[o] : GREY }}>
                      {r[o]} <span style={{ fontSize: 12, color: GREY }}>
                        ({r[`${o}_pct`] ?? 0}%)</span>
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        )}
    </Box>
  )
}

function MissedCalls({ m }: { m: SpeedReport['missed_calls'] }) {
  return (
    <Box title="Missed calls — how fast we call back"
      note={`Every inbound call nobody answered, from any number (customers, new leads, and some spam). Target ${minutes(m.target_minutes)}. Calls missed again before the callback count as one.`}>
      <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(min(160px, 100%), 1fr))' }}>
        <Tile label="Missed" value={String(m.missed)} sub={`${m.rings} rings in all`} />
        <Tile label="Called back on time" value={pct(m.on_time_pct)}
          sub={`${m.on_time} on time · ${m.late} late`} />
        <Tile label="Median callback" value={minutes(m.callback_median)}
          sub={`90% within ${minutes(m.callback_p90)}`} />
        <Tile label="Never called back" value={String(m.never_called_back)}
          color={m.never_called_back ? RED : INK} sub={`${m.waiting} still within target`} />
      </div>
      {m.by_line.length > 0 && (
        <table className="mt-4 w-full">
          <thead>
            <tr>{['Line', 'Missed', 'Median callback', 'Never called back']
              .map((h) => <th key={h} style={th}>{h}</th>)}</tr>
          </thead>
          <tbody>
            {m.by_line.map((l) => (
              <tr key={l.name}>
                <td style={td}>{l.name}</td><td style={td}>{l.missed}</td>
                <td style={td}>{minutes(l.callback_median)}</td>
                <td style={{ ...td, color: l.never_called_back ? RED : GREY }}>{l.never_called_back}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Box>
  )
}

export function SpeedToLeadReport({ since, until }: { since: string; until: string }) {
  const [group, setGroup] = useState<'' | 'AHS' | 'Retail'>('')
  const q = useQuery({
    queryKey: ['report-speed-to-lead', since, until, group],
    queryFn: () => getSpeedToLead({ since, until, group }),
  })
  const r = q.data
  return (
    <div className="grid gap-4" style={{ gridTemplateColumns: 'minmax(0, 1fr)' }}>
      <div className="flex flex-wrap items-center gap-2">
        {(['', 'AHS', 'Retail'] as const).map((g) => (
          <button key={g || 'all'} onClick={() => setGroup(g)} aria-pressed={group === g}
            style={{ height: 34, padding: '0 14px', borderRadius: 8, fontSize: 14, fontWeight: 500,
              border: `1px solid ${group === g ? 'rgb(21,112,239)' : LINE}`,
              color: group === g ? 'rgb(21,112,239)' : 'rgb(52,64,84)',
              backgroundColor: group === g ? 'rgb(239,244,255)' : '#fff' }}>
            {g === '' ? 'All leads' : g === 'AHS' ? 'AHS - Inspection' : 'Retail (and regional)'}
          </button>
        ))}
      </div>

      {q.error && (
        <div role="alert" style={{ fontSize: 14, color: 'rgb(180,35,24)',
          backgroundColor: 'rgb(254,243,242)', border: '1px solid rgb(253,162,155)',
          borderRadius: 8, padding: '12px 16px' }}>
          This report could not be loaded, so nothing below is your data.
          <div style={{ fontSize: 13, marginTop: 4 }}>{(q.error as Error).message}</div>
        </div>
      )}
      {q.isLoading && <div style={{ fontSize: 14, color: GREY }}>Loading…</div>}

      {r && <>
        <Headline s={group ? r.summary[group] : r.summary.all} targets={r.targets} group={group} />
        <div style={{ fontSize: 13, color: GREY, marginTop: -6 }}>
          Typical = the median: half the leads were faster, half slower. The average is shown
          beside it, because a few very slow leads pull it up a lot.
        </div>

        <Leads leads={r.leads} showBoard={group !== 'AHS'} />

        <div className="grid gap-4" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(min(460px, 100%), 1fr))' }}>
          <Box title="Week by week" note="Is it getting faster?">
            <Breakdown rows={r.by_week.map((w) => ({ ...w, name: `Week of ${w.name}` }))}
              first="Week" empty="No leads in this range." />
          </Box>
          <Box title="When the lead arrived" note="Leads that arrive after hours are measured from 8 AM.">
            <Breakdown rows={r.by_arrival} first="Arrived" empty="No leads in this range." />
          </Box>
          <Box title="Who answered first" note="Quo does not say which person made a call; Zuper does.">
            <Breakdown rows={r.by_person} first="Person" empty="Nobody has answered a lead yet." />
          </Box>
          <Box title="How we first answered">
            <Breakdown rows={r.by_first_response} first="First response" empty="Nobody has answered a lead yet." />
          </Box>
          <Box title="By board">
            <Breakdown rows={r.by_board} first="Board" empty="No leads in this range." />
          </Box>
        </div>


        <div>
          <div style={{ fontSize: 20, fontWeight: 500, color: INK }}>Time in each column</div>
          <div style={{ fontSize: 13, color: GREY, margin: '2px 0 12px' }}>
            Every job that entered a column in this range (all jobs, not only new leads), in
            business time — Mon–Sat 8 AM–6 PM, 10 hours a day. A stay ends at the job’s next
            move; jobs still there count toward the share and the wait now. Paid / Cancelled are
            left out. From Zuper’s own column history; before 2026-09-26 it is the load, not data.
          </div>
          <div className="grid gap-4">
            {r.columns.length === 0
              ? <div style={{ fontSize: 14, color: GREY }}>No column moves in this range.</div>
              : r.columns.map((b) => <ColumnTable key={b.board} {...b} />)}
          </div>
        </div>
        <IncomingCalls incoming={r.incoming} />
        <MissedCalls m={r.missed_calls} />

        <div style={{ fontSize: 13, color: GREY, lineHeight: '20px' }}>
          <b style={{ fontWeight: 500 }}>How this is measured.</b> The clock starts when the job is
          created in Zuper, or at the customer’s own call or text in the 3 days before. It stops
          at the first call, text or answered call by a person — on Quo, the CRM line or Zuper
          Connect. An AI agent answering does not stop it.
          {r.coverage.notes.map((n) => <div key={n}>· {n}</div>)}
        </div>
      </>}
    </div>
  )
}
