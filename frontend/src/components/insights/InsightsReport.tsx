import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { MicButton } from '../MicButton'
import { useState } from 'react'
import {
  insightsAsk, insightsCsvUrl, insightsExcelUrl, insightsPin, insightsReport, insightsResultExcelUrl,
  insightsUnpin, type InsightFilters, type InsightResult, type InsightSpec,
} from '../../lib/api'
import { toCsv } from '../../lib/csv'
import { downloadCsv } from '../../lib/download'
import { change, columnLabel, numericColumn, tileLabel, tileValue, tone } from '../../lib/insights'

/**
 * Reporting -> Insights (agent #10, app/insights): KPIs vs the previous period, customer
 * issues heard in calls and texts, charts, tables, Excel / CSV, "Ask for a KPI" and the KPIs
 * people pinned. Read-only; the Dispatch audience.
 */
const BLUE = 'rgb(21,112,239)'
const GREY = 'rgb(102,112,133)'
const INK = 'rgb(16,24,40)'
const LINE = 'rgb(234,236,240)'
const COLORS = ['rgb(83,155,245)', 'rgb(247,144,9)', 'rgb(18,183,106)', 'rgb(217,45,32)',
  'rgb(140,141,222)']
const RANGES = [['day', 'Day'], ['week', 'Week'], ['month', 'Month'], ['quarter', 'Quarter']]
const TABLES: [string, string][] = [
  ['technicians', 'By technician'], ['callbacks', 'Callbacks'], ['still_leaking', 'Still leaking'],
  ['callback_requests', 'Asked us to come back'], ['complaints', 'Complaints'], ['upsells', 'Upsells'],
  ['ratings', 'Ratings (Reviews & QC)'],
]
const card: React.CSSProperties = { background: 'white', border: `1px solid ${LINE}`, borderRadius: 8,
  padding: 16 }

export function InsightsReport() {
  const [f, setF] = useState<InsightFilters>({ range: 'week' })
  const q = useQuery({ queryKey: ['insights', f], queryFn: () => insightsReport(f) })
  const r = q.data
  const set = (k: keyof InsightFilters, v: string) => setF((o) => ({ ...o, [k]: v || undefined }))
  return (
    <div>
      <div style={{ fontSize: 30, fontWeight: 500, color: INK, marginBottom: 4 }}>Insights</div>
      <div style={{ fontSize: 14, color: GREY, marginBottom: 16 }}>
        What happened on every job and what customers said, from Zuper and every call and text.
        Each number about what a customer said goes back to their exact words.
      </div>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        {RANGES.map(([k, l]) => (
          <button key={k} onClick={() => set('range', k)} style={{ padding: '6px 12px', borderRadius: 6,
            border: `1px solid ${f.range === k ? BLUE : LINE}`, color: f.range === k ? BLUE : INK,
            background: f.range === k ? 'rgb(239,248,255)' : 'white', fontSize: 14 }}>{l}</button>
        ))}
        <input type="date" aria-label="Date in the period" value={f.date ?? ''}
          onChange={(e) => set('date', e.target.value)}
          style={{ border: `1px solid ${LINE}`, borderRadius: 6, padding: '5px 8px', fontSize: 14 }} />
        <select aria-label="Board" value={f.board ?? ''} onChange={(e) => set('board', e.target.value)}
          style={{ border: `1px solid ${LINE}`, borderRadius: 6, padding: '6px 8px', fontSize: 14 }}>
          <option value="">Every board</option>
          {r?.boards.map((b) => <option key={b}>{b}</option>)}
        </select>
        <select aria-label="Technician" value={f.technician ?? ''}
          onChange={(e) => set('technician', e.target.value)}
          style={{ border: `1px solid ${LINE}`, borderRadius: 6, padding: '6px 8px', fontSize: 14 }}>
          <option value="">Every technician</option>
          {(f.technician && r && !r.technicians.includes(f.technician)
            ? [...r.technicians, f.technician] : r?.technicians ?? []).map((t) => <option key={t}>{t}</option>)}
        </select>
        <a href={insightsExcelUrl(f)} download style={{ marginLeft: 'auto', color: BLUE, fontWeight: 500,
          fontSize: 14 }}>Download Excel</a>
      </div>
      {q.isError && <div style={{ color: 'rgb(217,45,32)' }}>The report could not be loaded.</div>}
      {!r ? <div style={{ color: GREY }}>Loading…</div> : (
        <>
          <div style={{ fontSize: 13, color: GREY, marginBottom: 12 }}>
            {r.first === r.last ? r.first : `${r.first} to ${r.last}`} · compared with{' '}
            {r.previous.first} to {r.previous.last}
            {r.reading.sentence && <> · <span style={{ color: 'rgb(181,71,8)' }}>{r.reading.sentence}</span></>}
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))',
            gap: 12, marginBottom: 20 }}>
            {r.tiles.map((t) => {
              const tn = tone(t.value, t.previous, t.lower_is_better)
              return (
                <div key={t.key} style={card}>
                  <div style={{ fontSize: 13, color: GREY }}>{tileLabel(t.label)}</div>
                  <div style={{ fontSize: 24, fontWeight: 600, color: INK }}>{tileValue(t.label, t.value)}</div>
                  <div style={{ fontSize: 12, color: tn === 'good' ? 'rgb(2,122,72)'
                    : tn === 'bad' ? 'rgb(217,45,32)' : GREY }}>
                    {change(t.value, t.previous) || ' '} {t.previous !== null && 'vs before'}
                  </div>
                </div>
              )
            })}
          </div>

          <Section title={`Customer issues (${r.issues.length} open)`}>
            {r.issues.length === 0 ? <Empty>Nobody said it is still leaking, asked us back or complained.</Empty>
              : r.issues.map((i) => (
                <div key={i.id} style={{ borderTop: `1px solid ${LINE}`, padding: '8px 0' }}>
                  <div style={{ fontWeight: 500, color: i.urgent ? 'rgb(217,45,32)' : INK }}>{i.title}</div>
                  <div style={{ fontSize: 13, color: GREY }}>{i.why}</div>
                  <a href="/dispatch" style={{ fontSize: 13, color: BLUE }}>Open in Dispatch</a>
                </div>))}
          </Section>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))',
            gap: 16, marginBottom: 20 }}>
            <div style={card}>
              <ChartTitle>Over the period</ChartTitle>
              <Lines series={r.series} names={['leads', 'inspections', 'repairs', 'callbacks', 'still_leaking']} />
            </div>
            <div style={card}>
              <ChartTitle>Callbacks and still leaking per technician</ChartTitle>
              <Bars labels={r.tables.technicians.map((t) => String(t.technician))}
                groups={[['Callbacks', r.tables.technicians.map((t) => Number(t.callbacks) || 0)],
                  ['Still leaking', r.tables.technicians.map((t) => Number(t.still_leaking) || 0)]]} />
            </div>
            <div style={card}>
              <ChartTitle>Upsell paid ($)</ChartTitle>
              <Bars labels={Object.keys(r.series.upsell_paid ?? {})}
                groups={[['Upsell paid', Object.values(r.series.upsell_paid ?? {})]]} />
            </div>
          </div>

          <Ask />

          {r.pinned.length > 0 && (
            <Section title="Pinned KPIs">
              {r.pinned.map((p) => <Pinned key={p.id} id={p.id} name={p.name} result={p.result} spec={p.spec} />)}
            </Section>
          )}

          {TABLES.map(([key, title]) => (
            <Section key={key} title={title}
              right={<a href={insightsCsvUrl(key, f)} download style={{ color: BLUE, fontSize: 13 }}>CSV</a>}>
              <Table rows={r.tables[key] ?? []} />
            </Section>
          ))}
        </>
      )}
    </div>
  )
}

function Section({ title, right, children }: { title: string; right?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div style={{ ...card, marginBottom: 16 }}>
      <div className="flex items-center" style={{ marginBottom: 8 }}>
        <div style={{ fontWeight: 600, color: INK }}>{title}</div>
        <div style={{ marginLeft: 'auto' }}>{right}</div>
      </div>
      {children}
    </div>
  )
}

const Empty = ({ children }: { children: React.ReactNode }) =>
  <div style={{ fontSize: 13, color: GREY }}>{children}</div>
const ChartTitle = ({ children }: { children: React.ReactNode }) =>
  <div style={{ fontWeight: 600, color: INK, marginBottom: 8, fontSize: 14 }}>{children}</div>

function Table({ rows }: { rows: Record<string, string | number | null>[] }) {
  if (!rows.length) return <Empty>Nothing in this period.</Empty>
  const cols = Object.keys(rows[0]).filter((c) => c !== 'id')
  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
        <thead><tr>{cols.map((c) => <th key={c} style={{ textAlign: 'left', color: GREY, fontWeight: 500,
          padding: '4px 8px', borderBottom: `1px solid ${LINE}` }}>{columnLabel(c)}</th>)}</tr></thead>
        <tbody>{rows.map((r, i) => <tr key={i}>{cols.map((c) => <td key={c} style={{ padding: '4px 8px',
          borderBottom: `1px solid ${LINE}`, color: INK, maxWidth: 420 }}>{r[c] ?? ''}</td>)}</tr>)}</tbody>
      </table>
    </div>
  )
}

function Bars({ labels, groups }: { labels: string[]; groups: [string, number[]][] }) {
  if (!labels.length) return <Empty>Nothing in this period.</Empty>
  const max = Math.max(1, ...groups.flatMap(([, v]) => v))
  const W = 320, H = 160, band = W / labels.length, bw = Math.max(2, (band - 8) / groups.length)
  return (
    <div style={{ overflowX: 'auto' }}>
      <svg viewBox={`0 0 ${W} ${H + 40}`} style={{ width: '100%', minWidth: Math.min(labels.length * 40, 640) }}>
        {labels.map((l, i) => (
          <g key={l + i}>
            {groups.map(([name, v], g) => {
              const h = (v[i] / max) * H
              return <rect key={name} x={i * band + 4 + g * bw} y={H - h} width={bw - 1} height={h}
                fill={COLORS[g % COLORS.length]}><title>{`${l} · ${name}: ${v[i]}`}</title></rect>
            })}
            <text x={i * band + band / 2} y={H + 14} fontSize="9" textAnchor="middle" fill={GREY}>
              {l.length > 12 ? l.slice(0, 11) + '…' : l}</text>
          </g>
        ))}
        {groups.length > 1 && groups.map(([name], g) => (
          <text key={name} x={g * 110} y={H + 34} fontSize="10" fill={COLORS[g % COLORS.length]}>■ {name}</text>
        ))}
      </svg>
    </div>
  )
}

function Lines({ series, names }: { series: Record<string, Record<string, number>>; names: string[] }) {
  const keys = [...new Set(names.flatMap((n) => Object.keys(series[n] ?? {})))].sort()
  if (!keys.length) return <Empty>Nothing in this period.</Empty>
  const shown = names.filter((n) => series[n])
  const max = Math.max(1, ...shown.flatMap((n) => keys.map((k) => series[n][k] ?? 0)))
  const W = 320, H = 150, step = keys.length > 1 ? W / (keys.length - 1) : 0
  return (
    <svg viewBox={`0 0 ${W + 10} ${H + 46}`} style={{ width: '100%' }}>
      {shown.map((n, s) => (
        <polyline key={n} fill="none" stroke={COLORS[s % COLORS.length]} strokeWidth="2"
          points={keys.map((k, i) => `${5 + i * step},${H - ((series[n][k] ?? 0) / max) * H}`).join(' ')} />
      ))}
      <text x="5" y={H + 14} fontSize="9" fill={GREY}>{keys[0]}</text>
      <text x={W + 5} y={H + 14} fontSize="9" fill={GREY} textAnchor="end">{keys[keys.length - 1]}</text>
      {shown.map((n, s) => (
        <text key={n} x={(s % 3) * 110} y={H + 30 + Math.floor(s / 3) * 12} fontSize="10"
          fill={COLORS[s % COLORS.length]}>■ {columnLabel(n)}</text>
      ))}
    </svg>
  )
}

function ResultView({ result }: { result: InsightResult }) {
  const col = numericColumn(result.rows)
  return (
    <div>
      <div style={{ fontSize: 13, color: GREY, marginBottom: 6 }}>{result.title}</div>
      {result.error && <div style={{ color: 'rgb(217,45,32)', fontSize: 13 }}>{result.error}</div>}
      {col > 0 && result.chart === 'bar' && (
        <Bars labels={result.rows.map((r) => String(r[0]))} groups={[[result.columns[col],
          result.rows.map((r) => Number(r[col]) || 0)]]} />)}
      {col > 0 && result.chart === 'line' && (
        <Lines names={['v']} series={{ v: Object.fromEntries(result.rows.map((r) => [String(r[0]), Number(r[col]) || 0])) }} />)}
      <Table rows={result.rows.map((r) => Object.fromEntries(result.columns.map((c, i) => [c, r[i]])))} />
    </div>
  )
}

function downloads(result: InsightResult, name: string, spec: InsightSpec) {
  return (
    <span className="flex gap-3" style={{ fontSize: 13 }}>
      <button style={{ color: BLUE }} onClick={() => downloadCsv(`${name}.csv`, toCsv(result.columns,
        result.rows))}>CSV</button>
      <a href={insightsResultExcelUrl(spec)} download style={{ color: BLUE }}>Excel</a>
    </span>
  )
}

function Pinned({ id, name, result, spec }: { id: number; name: string; result: InsightResult;
  spec: InsightSpec }) {
  const qc = useQueryClient()
  const unpin = useMutation({ mutationFn: () => insightsUnpin(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['insights'] }) })
  return (
    <div style={{ borderTop: `1px solid ${LINE}`, padding: '10px 0' }}>
      <div className="flex items-center gap-3" style={{ marginBottom: 6 }}>
        <div style={{ fontWeight: 500, color: INK }}>{name}</div>
        <div style={{ marginLeft: 'auto' }} className="flex gap-3">
          {downloads(result, name, spec)}
          <button style={{ color: GREY, fontSize: 13 }} onClick={() => unpin.mutate()}>Unpin</button>
        </div>
      </div>
      {unpin.isError && <div style={{ color: 'rgb(217,45,32)', fontSize: 13 }}>{String(unpin.error)}</div>}
      <ResultView result={result} />
    </div>
  )
}

function Ask() {
  const qc = useQueryClient()
  const [question, setQuestion] = useState('')
  const [answer, setAnswer] = useState<{ q: string; reply: string; error: boolean;
    result: InsightResult | null; spec: InsightSpec | null } | null>(null)
  const ask = useMutation({ mutationFn: (q: string) => insightsAsk(q),
    onSuccess: (a, q) => setAnswer({ q, ...a }) })
  const pin = useMutation({ mutationFn: ({ name, spec }: { name: string; spec: InsightSpec }) =>
    insightsPin(name, spec), onSuccess: () => qc.invalidateQueries({ queryKey: ['insights'] }) })
  return (
    <Section title="Ask for a KPI">
      <form className="flex gap-2" onSubmit={(e) => {
        e.preventDefault()
        if (question.trim().length > 1) ask.mutate(question.trim())
      }}>
        <input value={question} onChange={(e) => setQuestion(e.target.value)}
          placeholder="e.g. callbacks per technician last month"
          style={{ flex: 1, minWidth: 0, border: `1px solid ${LINE}`, borderRadius: 6, padding: '8px 10px', fontSize: 14 }} />
        <MicButton value={question} onChange={setQuestion} disabled={ask.isPending} size={36} />
        <button type="submit" disabled={ask.isPending} style={{ background: BLUE, color: 'white',
          borderRadius: 6, padding: '8px 14px', fontSize: 14 }}>{ask.isPending ? 'Working…' : 'Ask'}</button>
      </form>
      {ask.isError && <div style={{ color: 'rgb(217,45,32)', fontSize: 13, marginTop: 8 }}>{String(ask.error)}</div>}
      {answer && (
        <div style={{ marginTop: 12 }}>
          <div style={{ whiteSpace: 'pre-wrap', fontSize: 14, color: answer.error ? 'rgb(217,45,32)' : INK,
            marginBottom: 8 }}>{answer.reply}</div>
          {answer.result && (
            <>
              <div className="flex items-center gap-3" style={{ marginBottom: 6 }}>
                {answer.spec && downloads(answer.result, 'KPI', answer.spec)}
                {answer.spec && (
                  <button style={{ color: BLUE, fontSize: 13 }} disabled={pin.isPending || pin.isSuccess}
                    onClick={() => pin.mutate({ name: answer.q.slice(0, 200), spec: answer.spec! })}>
                    {pin.isSuccess ? 'Pinned' : 'Pin to report'}</button>
                )}
              </div>
              <ResultView result={answer.result} />
            </>
          )}
        </div>
      )}
    </Section>
  )
}
