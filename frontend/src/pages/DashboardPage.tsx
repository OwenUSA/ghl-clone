import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Donut } from '../components/Donut'
import { IconCalendar, IconChevronDown } from '../components/Icon'
import { getDashboard, listPipelines, money } from '../lib/api'
import { IconSettings } from '../components/Icon'

/**
 * Dashboard — rebuilt from references/ghl/dashboard/*.png + computed styles.
 *
 * Measured structure:
 *   header      "Dashboard ▾" | "+ New" | right: calendar icon + "Last 30 days ▾" | ⋮
 *   row 1       3 cards: Opportunity status | Opportunity value | Conversion rate
 *               card titles 16px/600; each has an "All pipelines" select + a
 *               settings icon on the right
 *   status card DONUT with the total in the centre (24px/500) and a legend to the
 *               right: "Won - 196" / "Open - 41" / "Lost - 35", 12px/400, colour chips
 *   value card  HORIZONTAL bars, y-axis Lost/Open/Won, x-axis $0..$250K,
 *               footer "Total revenue  $258.77K"
 *   conversion  DONUT ring, "72.06%" centred, footer "Won revenue $218.74K"
 *   row 2       "Funnel" (stage bars + Cumulative / Next step conversion columns)
 *               and "Stage distribution"
 *
 * A first version used flat progress bars; GHL uses donuts, which reads as a
 * different product even with the same numbers.
 */
const RANGES = ['Last 7 days', 'Last 30 days', 'Last 90 days', 'All time']

const C = {
  won: 'rgb(83,155,245)',
  open: 'rgb(93,205,235)',
  lost: 'rgb(140,141,222)',
  abandoned: 'rgb(152,162,179)',
} as const

function Card({ title, right, children }: {
  title: string; right?: React.ReactNode; children: React.ReactNode
}) {
  return (
    <div className="bg-white" style={{
      borderRadius: 8, border: '1px solid rgb(234,236,240)',
    }}>
      <div className="flex items-center gap-3 px-4"
        style={{ height: 64, borderBottom: '1px solid rgb(242,244,247)' }}>
        <div style={{ fontSize: 16, fontWeight: 600, color: 'rgb(16,24,40)' }}>
          {title}
        </div>
        <div className="ml-auto flex items-center gap-2">
          {right}
          {/* measured: every card carries a settings icon to the right of its select */}
          <button title="Card settings (not implemented in v1)"
            style={{ color: 'rgb(102,112,133)', cursor: 'not-allowed' }} disabled>
            <IconSettings size={18} color="rgb(102,112,133)" />
          </button>
        </div>
      </div>
      <div className="p-4">{children}</div>
    </div>
  )
}

function PipelineSelect({ value, onChange, pipelines }: {
  value: number | undefined
  onChange: (v: number | undefined) => void
  pipelines: { id: number; name: string }[]
}) {
  return (
    <select
      value={value ?? ''}
      onChange={(e) => onChange(e.target.value ? Number(e.target.value) : undefined)}
      style={{
        height: 34, borderRadius: 6, border: '1px solid rgb(234,236,240)',
        padding: '0 8px', fontSize: 14, color: 'rgb(52,64,84)', backgroundColor: '#fff',
      }}
    >
      <option value="">All pipelines</option>
      {pipelines.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
    </select>
  )
}

export function DashboardPage() {
  const [range, setRange] = useState('Last 30 days')
  const [statusPipe, setStatusPipe] = useState<number | undefined>()
  const [valuePipe, setValuePipe] = useState<number | undefined>()
  // Conversion rate needs its own, or its select re-filters the Opportunity value
  // card and leaves its own number alone. Funnel and Stage distribution DO share
  // one deliberately -- they are two renderings of a single pipeline.
  const [convPipe, setConvPipe] = useState<number | undefined>()
  const [funnelPipe, setFunnelPipe] = useState<number | undefined>()

  const pipelines = useQuery({ queryKey: ['pipelines'], queryFn: listPipelines })
  const status = useQuery({
    queryKey: ['dashboard', statusPipe], queryFn: () => getDashboard(statusPipe),
  })
  const value = useQuery({
    queryKey: ['dashboard', valuePipe], queryFn: () => getDashboard(valuePipe),
  })
  // Same key shape as the other two, so two cards on the same pipeline still share
  // one cached request rather than issuing a second.
  const conv = useQuery({
    queryKey: ['dashboard', convPipe], queryFn: () => getDashboard(convPipe),
  })

  const s = status.data?.status ?? {}
  const segs = (['won', 'open', 'lost'] as const)
    .map((k) => ({ key: k, n: s[k] ?? 0, color: C[k] }))

  const totalRev = value.data?.total_value_cents ?? 0
  const wonRev = value.data?.won_value_cents ?? 0
  const barMax = Math.max(totalRev, 1)
  const bars = [
    { label: 'Lost', v: 0 },
    { label: 'Open', v: totalRev - wonRev },
    { label: 'Won', v: wonRev },
  ]
  const ticks = 5
  const kFmt = (cents: number) => '$' + Math.round(cents / 100 / 1000) + 'K'

  const pipe = pipelines.data?.find((p) => p.id === (funnelPipe ?? pipelines.data?.[0]?.id))
  const stages = pipe?.stages ?? []
  const stageMax = Math.max(...stages.map((x) => x.count), 1)
  const funnelTotal = stages.reduce((a, x) => a + x.count, 0) || 1

  return (
    <div className="flex h-screen min-w-0 flex-1 flex-col overflow-hidden"
      style={{ backgroundColor: 'rgb(249,250,251)' }}>
      <div className="flex shrink-0 items-center gap-3 bg-white px-4"
        style={{ height: 64, borderBottom: '1px solid rgb(234,236,240)' }}>
        <button className="flex items-center gap-1"
          style={{
            height: 36, padding: '0 12px', borderRadius: 6,
            border: '1px solid rgb(234,236,240)',
            fontSize: 14, fontWeight: 600, color: 'rgb(16,24,40)',
          }}>
          Dashboard <IconChevronDown size={16} color="rgb(102,112,133)" />
        </button>
        <button disabled title="Custom dashboards are not implemented in v1"
          className="flex items-center gap-1"
          style={{ fontSize: 14, fontWeight: 500, color: 'rgb(21,112,239)', opacity: 0.5, cursor: 'not-allowed' }}>
          + New
        </button>
        <div className="ml-auto flex items-center gap-2">
          <span className="flex items-center gap-2"
            style={{
              height: 36, padding: '0 10px', borderRadius: 6,
              border: '1px solid rgb(234,236,240)', backgroundColor: '#fff',
            }}>
            <IconCalendar size={16} color="rgb(71,84,103)" />
            <select value={range} onChange={(e) => setRange(e.target.value)}
              title="All seeded records fall in one window, so range does not change the numbers yet"
              style={{ fontSize: 14, fontWeight: 600, color: 'rgb(16,24,40)', border: 'none', outline: 'none' }}>
              {RANGES.map((r) => <option key={r}>{r}</option>)}
            </select>
          </span>
          <span style={{ color: 'rgb(102,112,133)', fontSize: 18 }}>⋮</span>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        <div className="grid gap-4" style={{
          gridTemplateColumns: 'repeat(auto-fit, minmax(380px, 1fr))',
        }}>
          <Card title="Opportunity status"
            right={<PipelineSelect value={statusPipe} onChange={setStatusPipe}
              pipelines={pipelines.data ?? []} />}>
            <div className="flex items-center gap-6">
              <Donut segments={segs} center={String(status.data?.total ?? 0)} />
              <div>
                {segs.map((x) => (
                  <div key={x.key} className="mb-2 flex items-center gap-2"
                    style={{ fontSize: 12, color: 'rgb(52,64,84)' }}>
                    <span style={{
                      width: 16, height: 8, borderRadius: 2,
                      backgroundColor: x.color, display: 'inline-block',
                    }} />
                    {x.key[0].toUpperCase() + x.key.slice(1)} - {x.n}
                  </div>
                ))}
              </div>
            </div>
          </Card>

          <Card title="Opportunity value"
            right={<PipelineSelect value={valuePipe} onChange={setValuePipe}
              pipelines={pipelines.data ?? []} />}>
            <div className="flex">
              <div className="shrink-0" style={{ width: 48 }}>
                {bars.map((b) => (
                  <div key={b.label} style={{
                    height: 34, fontSize: 12, color: 'rgb(102,112,133)',
                    display: 'flex', alignItems: 'center', justifyContent: 'flex-end',
                    paddingRight: 8,
                  }}>{b.label}</div>
                ))}
              </div>
              <div className="relative flex-1">
                {/* grid lines behind the bars, as measured */}
                <div className="absolute inset-0 flex justify-between">
                  {Array.from({ length: ticks + 1 }, (_, i) => (
                    <span key={i} style={{ width: 1, backgroundColor: 'rgb(242,244,247)' }} />
                  ))}
                </div>
                {bars.map((b) => (
                  <div key={b.label} style={{ height: 34, display: 'flex', alignItems: 'center' }}>
                    <div style={{
                      width: `${(b.v / barMax) * 100}%`, height: 8,
                      backgroundColor: C.won, borderRadius: 2, minWidth: b.v > 0 ? 4 : 0,
                    }} />
                  </div>
                ))}
                <div className="flex justify-between" style={{ marginTop: 6 }}>
                  {Array.from({ length: ticks + 1 }, (_, i) => (
                    <span key={i} style={{ fontSize: 12, color: 'rgb(102,112,133)' }}>
                      {kFmt((barMax / ticks) * i)}
                    </span>
                  ))}
                </div>
              </div>
            </div>
            <div className="mt-3 text-center">
              <div style={{ fontSize: 12, color: 'rgb(102,112,133)' }}>Total revenue</div>
              <div style={{ fontSize: 20, fontWeight: 500, color: 'rgb(16,24,40)' }}>
                {money(totalRev)}
              </div>
            </div>
          </Card>

          <Card title="Conversion rate"
            right={<PipelineSelect value={convPipe} onChange={setConvPipe}
              pipelines={pipelines.data ?? []} />}>
            <div className="flex flex-col items-center">
              <Donut
                segments={[
                  { key: 'won', n: conv.data?.conversion_rate ?? 0, color: C.won },
                  { key: 'rest', n: 100 - (conv.data?.conversion_rate ?? 0), color: 'rgb(234,236,240)' },
                ]}
                center={`${(conv.data?.conversion_rate ?? 0).toFixed(2)}%`}
              />
              <div className="mt-3 text-center">
                <div style={{ fontSize: 12, color: 'rgb(102,112,133)' }}>Won revenue</div>
                <div style={{ fontSize: 20, fontWeight: 500, color: 'rgb(16,24,40)' }}>
                  {money(conv.data?.won_value_cents ?? 0)}
                </div>
              </div>
            </div>
          </Card>
        </div>

        {/* row 2 — Funnel + Stage distribution */}
        <div className="mt-4 grid gap-4" style={{
          gridTemplateColumns: 'repeat(auto-fit, minmax(420px, 1fr))',
        }}>
          <Card title="Funnel"
            right={<PipelineSelect value={funnelPipe} onChange={setFunnelPipe}
              pipelines={pipelines.data ?? []} />}>
            <div className="flex" style={{ fontSize: 12, color: 'rgb(102,112,133)' }}>
              <div className="flex-1" />
              <div style={{ width: 90, textAlign: 'center' }}>Cumulative</div>
              <div style={{ width: 90, textAlign: 'center' }}>Next step<br />conversion</div>
            </div>
            {stages.map((st, i) => {
              const next = stages[i + 1]
              const nextConv = st.count ? ((next?.count ?? 0) / st.count) * 100 : 0
              return (
                <div key={st.id} className="mt-1 flex items-center">
                  <div className="flex-1" style={{
                    backgroundColor: 'rgb(242,244,247)', borderRadius: 4, padding: '8px 10px',
                  }}>
                    <div style={{ fontSize: 13, color: 'rgb(52,64,84)' }}>{st.name}</div>
                    <div style={{ fontSize: 12, color: 'rgb(102,112,133)' }}>
                      {money(st.value_cents)}
                    </div>
                    <div style={{
                      marginTop: 4, height: 6, borderRadius: 3,
                      width: `${(st.count / stageMax) * 100}%`,
                      backgroundColor: C.won, minWidth: st.count ? 4 : 0,
                    }} />
                  </div>
                  <div style={{ width: 90, textAlign: 'center', fontSize: 12, color: 'rgb(52,64,84)' }}>
                    {((st.count / funnelTotal) * 100).toFixed(2)}%
                  </div>
                  <div style={{ width: 90, textAlign: 'center', fontSize: 12, color: 'rgb(52,64,84)' }}>
                    {nextConv.toFixed(2)}%
                  </div>
                </div>
              )
            })}
          </Card>

          <Card title="Stage distribution"
            right={<PipelineSelect value={funnelPipe} onChange={setFunnelPipe}
              pipelines={pipelines.data ?? []} />}>
            {stages.filter((x) => x.count > 0).length === 0 ? (
              <div style={{ fontSize: 14, color: 'rgb(102,112,133)' }}>
                No opportunities in this pipeline.
              </div>
            ) : (
              <div className="flex items-center gap-6">
                <Donut
                  size={150}
                  segments={stages.filter((x) => x.count > 0).map((st, i) => ({
                    key: String(st.id),
                    n: st.count,
                    color: [C.won, C.open, C.lost, C.abandoned, 'rgb(249,219,175)'][i % 5],
                  }))}
                  center={String(stages.reduce((a, x) => a + x.count, 0))}
                />
                <div>
                  {stages.filter((x) => x.count > 0).map((st, i) => (
                    <div key={st.id} className="mb-2 flex items-center gap-2"
                      style={{ fontSize: 12, color: 'rgb(52,64,84)' }}>
                      <span style={{
                        width: 16, height: 8, borderRadius: 2, display: 'inline-block',
                        backgroundColor: [C.won, C.open, C.lost, C.abandoned, 'rgb(249,219,175)'][i % 5],
                      }} />
                      {st.name} - {st.count}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </Card>
        </div>
      </div>
    </div>
  )
}
