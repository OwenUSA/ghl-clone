import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Donut } from '../components/Donut'
import { IconCalendar, IconFilter, IconGrid, IconSearch } from '../components/Icon'
import { getAppointmentReport, getCallReport, listCalendars } from '../lib/api'

/**
 * Reporting — rebuilt from captures/reporting/*.png + computed styles.
 *
 * A first version used horizontal bar charts and two separate date inputs.
 * Measured structure is different in several ways:
 *   page title        "Call report" / "Appointment reporting" at 30px/500
 *   date range        ONE control: `2026-07-26 → 2026-08-01` + calendar icon
 *                     (Call report also carries an info (i) icon beside it)
 *   Call report right "All numbers" select · "Filters" (bordered + icon)
 *                     · refresh icon button · layout icon button
 *   Incoming/Outgoing UNDERLINED tabs, not a segmented toggle
 *   charts            DONUTS with a centred "Total / N", not bars
 *   duration strip    INSIDE each card, grey rounded:
 *                     "Avg. call duration: 0s | Total call duration: 0s"
 *   Top call sources  donut on the left + table on the right
 *   Appointment       8 status tiles (label 16px/500, count 48px/500), then
 *                     "Channel" (donut + legend) and "Source" (with a "Funnel"
 *                     select and a "No data found" empty state), then
 *                     "Outcomes" and "Top 5 most booked calendars"
 *
 * NOT IMPLEMENTED, deliberately (shown disabled with the reason on hover):
 *   Google Ads / Meta Ads / Local Marketing Audit — third-party integrations.
 *   Attribution report — rendered empty on the live account.
 *   Custom reports — GHL shows an empty state; the builder is a separate product.
 */
type TabDef = { key: string; label: string; off?: string }

const TABS: TabDef[] = [
  { key: 'custom', label: 'Custom reports', off: 'Report builder is a separate product; not in v1' },
  { key: 'google', label: 'Google Ads', off: 'Third-party integration — excluded' },
  { key: 'meta', label: 'Meta Ads (Facebook Ads) report', off: 'Third-party integration — excluded' },
  { key: 'attribution', label: 'Attribution report', off: 'Rendered empty on the live account — nothing measured to copy' },
  { key: 'call', label: 'Call report' },
  { key: 'appointment', label: 'Appointment report' },
  { key: 'audit', label: 'Local Marketing Audit', off: 'Third-party integration — excluded' },
]

const PALETTE = ['rgb(83,155,245)', 'rgb(93,205,235)', 'rgb(140,141,222)',
  'rgb(18,183,106)', 'rgb(247,144,9)', 'rgb(217,45,32)', 'rgb(152,162,179)']

const TILE_ORDER = ['booked', 'confirmed', 'cancelled', 'new', 'showed',
  'no-show', 'invalid', 'rescheduled'] as const
const TILE_LABEL: Record<string, string> = {
  booked: 'Booked', confirmed: 'Confirmed', cancelled: 'Cancelled', new: 'New',
  showed: 'Showed', 'no-show': 'No-show', invalid: 'Invalid',
  rescheduled: 'Rescheduled',
}

const dur = (s: number) => (s >= 60 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${s}s`)
const iso = (d: Date) => d.toISOString().slice(0, 10)

/** Measured: a single range control with an arrow between the two dates. */
function DateRange({
  start, end, onStart, onEnd, info,
}: {
  start: string; end: string
  onStart: (v: string) => void; onEnd: (v: string) => void
  info?: boolean
}) {
  return (
    <div className="flex items-center gap-2">
      <div className="flex items-center gap-2"
        style={{
          height: 40, padding: '0 12px', borderRadius: 8,
          border: '1px solid rgb(234,236,240)', backgroundColor: '#fff',
        }}>
        <input type="date" value={start} onChange={(e) => onStart(e.target.value)}
          style={{ fontSize: 14, color: 'rgb(52,64,84)', border: 'none', outline: 'none' }} />
        <span style={{ color: 'rgb(102,112,133)' }}>→</span>
        <input type="date" value={end} onChange={(e) => onEnd(e.target.value)}
          style={{ fontSize: 14, color: 'rgb(52,64,84)', border: 'none', outline: 'none' }} />
        <IconCalendar size={16} color="rgb(102,112,133)" />
      </div>
      {info && (
        <span title="Range applies to the report below"
          style={{
            width: 18, height: 18, borderRadius: '50%', fontSize: 12,
            border: '1px solid rgb(152,162,179)', color: 'rgb(152,162,179)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
          }}>i</span>
      )}
    </div>
  )
}

function Card({ title, right, children, pad = 16 }: {
  title: string; right?: React.ReactNode; children: React.ReactNode; pad?: number
}) {
  return (
    <div className="bg-white" style={{ borderRadius: 8, border: '1px solid rgb(234,236,240)' }}>
      <div className="flex items-center gap-3 px-4"
        style={{ height: 64, borderBottom: '1px solid rgb(242,244,247)' }}>
        <div style={{ fontSize: 18, fontWeight: 500, color: 'rgb(16,24,40)' }}>{title}</div>
        <div className="ml-auto">{right}</div>
      </div>
      <div style={{ padding: pad }}>{children}</div>
    </div>
  )
}

/** Measured: GHL's empty state is a magnifier bubble above "No data found". */
function NoData() {
  return (
    <div className="flex flex-col items-center justify-center" style={{ padding: 40 }}>
      <div className="flex items-center justify-center"
        style={{ width: 40, height: 40, borderRadius: '50%', backgroundColor: 'rgb(239,244,255)' }}>
        <IconSearch size={18} color="rgb(21,112,239)" />
      </div>
      <div style={{ fontSize: 16, color: 'rgb(52,64,84)', marginTop: 12 }}>No data found</div>
    </div>
  )
}

function Legend({ items }: { items: { label: string; n: number; color: string }[] }) {
  return (
    <div>
      {items.map((i) => (
        <div key={i.label} className="mb-2 flex items-center gap-2"
          style={{ fontSize: 12, color: 'rgb(52,64,84)' }}>
          <span style={{ width: 16, height: 8, borderRadius: 2, backgroundColor: i.color }} />
          {i.label} - {i.n}
        </div>
      ))}
    </div>
  )
}

/** Measured: grey rounded strip at the bottom of each call chart card. */
function DurationStrip({ avg, total }: { avg: number; total: number }) {
  return (
    <div className="flex flex-wrap items-center justify-center gap-2"
      style={{
        marginTop: 16, padding: '12px 16px', borderRadius: 8,
        backgroundColor: 'rgb(247,249,253)',
      }}>
      <span style={{ fontSize: 16, fontWeight: 400, color: 'rgb(52,64,84)' }}>
        Avg. call duration:
      </span>
      <span style={{ fontSize: 16, fontWeight: 500, color: 'rgb(16,24,40)' }}>{dur(avg)}</span>
      <span style={{ fontSize: 14, color: 'rgb(152,162,179)' }}>|</span>
      <span style={{ fontSize: 16, fontWeight: 400, color: 'rgb(52,64,84)' }}>
        Total call duration:
      </span>
      <span style={{ fontSize: 16, fontWeight: 500, color: 'rgb(16,24,40)' }}>{dur(total)}</span>
    </div>
  )
}

const IconBtn = ({ title, children }: { title: string; children: React.ReactNode }) => (
  <button title={title} disabled
    style={{
      width: 40, height: 40, borderRadius: 8, border: '1px solid rgb(234,236,240)',
      backgroundColor: '#fff', display: 'flex', alignItems: 'center',
      justifyContent: 'center', opacity: 0.6, cursor: 'not-allowed',
    }}>
    {children}
  </button>
)

export function ReportingPage() {
  const [tab, setTab] = useState('call')
  const [direction, setDirection] = useState<'INBOUND' | 'OUTBOUND'>('INBOUND')
  const [startDate, setStartDate] = useState(() => iso(new Date(Date.now() - 90 * 86_400_000)))
  const [endDate, setEndDate] = useState(() => iso(new Date(Date.now() + 30 * 86_400_000)))
  const [calendarId, setCalendarId] = useState<number | ''>('')

  const calendars = useQuery({ queryKey: ['calendars'], queryFn: listCalendars })
  const calls = useQuery({
    queryKey: ['report-calls', startDate, endDate, direction],
    queryFn: () => getCallReport({ start: startDate, end: endDate, direction }),
    enabled: tab === 'call',
  })
  const appts = useQuery({
    queryKey: ['report-appts', startDate, endDate, calendarId],
    queryFn: () => getAppointmentReport({
      start: startDate, end: endDate,
      calendar_ids: calendarId ? [Number(calendarId)] : [],
    }),
    enabled: tab === 'appointment',
  })

  const segs = (m: Record<string, number>) =>
    Object.entries(m).map(([k, n], i) => ({ key: k, n, color: PALETTE[i % PALETTE.length] }))
  const legend = (m: Record<string, number>) =>
    Object.entries(m).map(([k, n], i) => ({ label: k, n, color: PALETTE[i % PALETTE.length] }))

  const byStatus = calls.data?.by_status ?? {}
  const firstBy = calls.data?.first_time_by_status ?? {}
  const sources = calls.data?.top_sources ?? []
  const srcMap = Object.fromEntries(sources.map((s) => [s.source, s.calls]))

  return (
    <div className="flex h-screen min-w-0 flex-1 flex-col overflow-hidden"
      style={{ backgroundColor: 'rgb(249,250,251)' }}>
      <div className="flex shrink-0 items-center gap-6 overflow-x-auto bg-white px-4"
        style={{ height: 90, borderBottom: '1px solid rgb(234,236,240)' }}>
        <div className="shrink-0" style={{ fontSize: 18, fontWeight: 500, color: 'rgb(31,41,55)' }}>
          Reporting
        </div>
        {TABS.map((t) => (
          <button key={t.key} disabled={!!t.off} title={t.off}
            onClick={() => setTab(t.key)} className="shrink-0"
            style={{
              fontSize: 14, fontWeight: 500, lineHeight: '25.6px',
              paddingBottom: 4,
              color: t.off ? 'rgb(152,162,179)'
                : tab === t.key ? 'rgb(21,112,239)' : 'rgb(75,85,99)',
              borderBottom: tab === t.key ? '2px solid rgb(21,112,239)' : '2px solid transparent',
              cursor: t.off ? 'not-allowed' : 'pointer',
            }}>
            {t.label}
          </button>
        ))}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-6">
        {tab === 'call' && (
          <>
            <div style={{ fontSize: 30, fontWeight: 500, color: 'rgb(16,24,40)', marginBottom: 16 }}>
              Call report
            </div>
            <div className="mb-4 flex flex-wrap items-center gap-3">
              <DateRange start={startDate} end={endDate}
                onStart={setStartDate} onEnd={setEndDate} info />
              <div className="ml-auto flex items-center gap-3">
                <select disabled title="Per-number filtering needs the telephony integration"
                  style={{
                    height: 40, borderRadius: 8, border: '1px solid rgb(234,236,240)',
                    padding: '0 10px', fontSize: 15, color: 'rgb(102,112,133)',
                    backgroundColor: '#fff',
                  }}>
                  <option>All numbers</option>
                </select>
                <button disabled title="Filter builder is not implemented in v1"
                  className="flex items-center gap-2"
                  style={{
                    height: 40, padding: '0 14px', borderRadius: 8,
                    border: '1px solid rgb(234,236,240)', backgroundColor: '#fff',
                    fontSize: 14, fontWeight: 500, color: 'rgb(52,64,84)',
                    opacity: 0.7, cursor: 'not-allowed',
                  }}>
                  <IconFilter size={16} color="rgb(52,64,84)" /> Filters
                </button>
                <IconBtn title="Refresh (not implemented in v1)">
                  <span style={{ color: 'rgb(102,112,133)' }}>⟳</span>
                </IconBtn>
                <IconBtn title="Column layout (not implemented in v1)">
                  <IconGrid size={16} color="rgb(102,112,133)" />
                </IconBtn>
              </div>
            </div>

            {/* Incoming / Outgoing — measured as underlined tabs */}
            <div className="mb-4 flex gap-6"
              style={{ borderBottom: '1px solid rgb(234,236,240)' }}>
              {(['INBOUND', 'OUTBOUND'] as const).map((d) => (
                <button key={d} onClick={() => setDirection(d)}
                  style={{
                    fontSize: 14, fontWeight: 500, paddingBottom: 8,
                    color: direction === d ? 'rgb(21,112,239)' : 'rgb(102,112,133)',
                    borderBottom: direction === d
                      ? '2px solid rgb(21,112,239)' : '2px solid transparent',
                  }}>
                  {d === 'INBOUND' ? 'Incoming' : 'Outgoing'}
                </button>
              ))}
            </div>

            <div className="grid gap-4" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(380px, 1fr))' }}>
              <Card title="Call by status">
                <div className="flex items-center justify-center gap-8">
                  <Donut segments={segs(byStatus)} caption="Total"
                    center={String(calls.data?.total_calls ?? 0)} />
                  {Object.keys(byStatus).length > 0 && <Legend items={legend(byStatus)} />}
                </div>
                <DurationStrip avg={calls.data?.avg_duration_seconds ?? 0}
                  total={calls.data?.total_duration_seconds ?? 0} />
              </Card>

              <Card title="First-time calls by status">
                <div className="flex items-center justify-center gap-8">
                  <Donut segments={segs(firstBy)} caption="Total"
                    center={String(Object.values(firstBy).reduce((a, b) => a + b, 0))} />
                  {Object.keys(firstBy).length > 0 && <Legend items={legend(firstBy)} />}
                </div>
                <DurationStrip avg={calls.data?.avg_duration_seconds ?? 0}
                  total={calls.data?.total_duration_seconds ?? 0} />
              </Card>
            </div>

            {/* measured: donut on the left, table on the right */}
            <div className="mt-4">
              <Card title="Top call sources">
                {sources.length === 0 ? <NoData /> : (
                  <div className="flex flex-wrap items-center gap-8">
                    <Donut segments={segs(srcMap)} caption="Total"
                      center={String(calls.data?.total_calls ?? 0)} />
                    <table className="min-w-[420px] flex-1">
                      <thead>
                        <tr>
                          {['Source', 'Total calls', 'Won deals', 'Avg duration'].map((h) => (
                            <th key={h} className="text-left"
                              style={{
                                height: 40, fontSize: 14, fontWeight: 500,
                                color: 'rgb(52,64,84)',
                                borderBottom: '1px solid rgb(234,236,240)',
                              }}>{h}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {sources.map((s, i) => (
                          <tr key={s.source}>
                            <td style={{ height: 44, fontSize: 14, color: 'rgb(52,64,84)', borderBottom: '1px solid rgb(242,244,247)' }}>
                              <span className="flex items-center gap-2">
                                <span style={{
                                  width: 10, height: 10, borderRadius: 2,
                                  backgroundColor: PALETTE[i % PALETTE.length],
                                }} />
                                {s.source}
                              </span>
                            </td>
                            <td style={{ fontSize: 14, borderBottom: '1px solid rgb(242,244,247)' }}>{s.calls}</td>
                            <td style={{ fontSize: 14, borderBottom: '1px solid rgb(242,244,247)' }}>{s.won}</td>
                            <td style={{ fontSize: 14, borderBottom: '1px solid rgb(242,244,247)' }}>{dur(s.avg_duration)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </Card>
            </div>
          </>
        )}

        {tab === 'appointment' && (
          <>
            <div style={{ fontSize: 30, fontWeight: 500, color: 'rgb(16,24,40)', marginBottom: 16 }}>
              Appointment reporting
            </div>
            <div className="mb-4 flex flex-wrap items-center gap-3">
              <DateRange start={startDate} end={endDate}
                onStart={setStartDate} onEnd={setEndDate} />
              <div className="ml-auto flex items-center gap-3">
                <select value={calendarId}
                  onChange={(e) => setCalendarId(e.target.value ? Number(e.target.value) : '')}
                  style={{
                    height: 40, borderRadius: 8, border: '1px solid rgb(234,236,240)',
                    padding: '0 10px', fontSize: 15, backgroundColor: '#fff',
                  }}>
                  <option value="">All calendars</option>
                  {calendars.data?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                </select>
                <select disabled title="Grouping is not implemented in v1"
                  style={{
                    height: 40, borderRadius: 8, border: '1px solid rgb(234,236,240)',
                    padding: '0 10px', fontSize: 15, color: 'rgb(102,112,133)',
                    backgroundColor: '#fff',
                  }}>
                  <option>Date added</option>
                </select>
                <IconBtn title="Column layout (not implemented in v1)">
                  <IconGrid size={16} color="rgb(102,112,133)" />
                </IconBtn>
              </div>
            </div>

            {/* 8 tiles in a row — measured label 16px/500, count 48px/500 */}
            <div className="grid gap-3"
              style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))' }}>
              {TILE_ORDER.map((k) => (
                <div key={k} className="bg-white"
                  style={{ borderRadius: 8, border: '1px solid rgb(234,236,240)' }}>
                  <div style={{
                    fontSize: 16, fontWeight: 500, color: 'rgb(52,64,84)',
                    padding: '16px 16px 0',
                  }}>{TILE_LABEL[k]}</div>
                  <div style={{
                    fontSize: 48, fontWeight: 500, color: 'rgb(16,24,40)',
                    lineHeight: '58px', padding: '8px 16px 16px',
                  }}>{appts.data?.tiles[k] ?? 0}</div>
                </div>
              ))}
            </div>

            <div className="mt-4 grid gap-4"
              style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(420px, 1fr))' }}>
              <Card title="Channel">
                {(appts.data?.total ?? 0) === 0 ? <NoData /> : (
                  <div className="flex items-center justify-center gap-10">
                    <Donut
                      segments={(appts.data?.by_channel ?? []).map((c, i) => ({
                        key: c.channel, n: c.count, color: PALETTE[i % PALETTE.length],
                      }))}
                      center={String(appts.data?.total ?? 0)}
                    />
                    <Legend items={(appts.data?.by_channel ?? []).map((c, i) => ({
                      label: c.channel, n: c.count, color: PALETTE[i % PALETTE.length],
                    }))} />
                  </div>
                )}
              </Card>

              <Card title="Source" right={
                <select disabled title="Funnel grouping is not implemented in v1"
                  style={{
                    height: 36, borderRadius: 8, border: '1px solid rgb(234,236,240)',
                    padding: '0 10px', fontSize: 15, color: 'rgb(102,112,133)',
                    backgroundColor: '#fff',
                  }}>
                  <option>Funnel</option>
                </select>
              }>
                {(appts.data?.by_source ?? []).length === 0 ? <NoData /> : (
                  <div className="flex items-center justify-center gap-10">
                    <Donut
                      segments={(appts.data?.by_source ?? []).map((s, i) => ({
                        key: s.source, n: s.count, color: PALETTE[i % PALETTE.length],
                      }))}
                      center={String(appts.data?.total ?? 0)}
                    />
                    <Legend items={(appts.data?.by_source ?? []).map((s, i) => ({
                      label: s.source, n: s.count, color: PALETTE[i % PALETTE.length],
                    }))} />
                  </div>
                )}
              </Card>
            </div>

            <div className="mt-4 grid gap-4"
              style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(420px, 1fr))' }}>
              <Card title="Outcomes">
                {Object.keys(appts.data?.outcomes ?? {}).length === 0 ? <NoData /> : (
                  <div className="flex items-center justify-center gap-10">
                    <Donut segments={segs(appts.data?.outcomes ?? {})}
                      center={String(Object.values(appts.data?.outcomes ?? {})
                        .reduce((a, b) => a + b, 0))} />
                    <Legend items={legend(appts.data?.outcomes ?? {})} />
                  </div>
                )}
              </Card>

              <Card title="Top 5 most booked calendars">
                {(appts.data?.by_calendar ?? []).length === 0 ? <NoData /> : (
                  <table className="w-full">
                    <thead>
                      <tr>
                        {['Calendar', 'Appointments'].map((h) => (
                          <th key={h} className="text-left"
                            style={{
                              height: 40, fontSize: 14, fontWeight: 500,
                              color: 'rgb(52,64,84)',
                              borderBottom: '1px solid rgb(234,236,240)',
                            }}>{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {appts.data?.by_calendar.map((c) => (
                        <tr key={c.calendar}>
                          <td style={{ height: 44, fontSize: 14, color: 'rgb(52,64,84)', borderBottom: '1px solid rgb(242,244,247)' }}>{c.calendar}</td>
                          <td style={{ fontSize: 14, borderBottom: '1px solid rgb(242,244,247)' }}>{c.count}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </Card>
            </div>
          </>
        )}

        {!['call', 'appointment'].includes(tab) && (
          <div className="bg-white"
            style={{ borderRadius: 8, border: '1px solid rgb(234,236,240)', padding: 32 }}>
            <div style={{ fontSize: 24, fontWeight: 600, color: 'rgb(16,24,40)' }}>
              Not implemented in v1
            </div>
            <div style={{ fontSize: 16, color: 'rgb(102,112,133)', marginTop: 8 }}>
              {TABS.find((t) => t.key === tab)?.off}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
