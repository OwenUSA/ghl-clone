import { useQuery } from '@tanstack/react-query'
import { getForecast, money, type Pipeline } from '../lib/api'

/**
 * The Opportunities > Forecast tab: projected revenue by stage.
 *
 * OUR design, not measured parity. `captures/opportunities/` holds the BOARD
 * only — GHL's Forecast tab was never opened on the live account, so there is
 * nothing to match and inventing a GHL-shaped screen would imply a measurement
 * that does not exist. Recorded in DECISIONS.md (2026-09-10).
 *
 * Every figure comes from `GET /api/forecast`, which shares its arithmetic with
 * `GET /api/dashboard` — same conversion rate, same totals — and repeats the
 * per-stage `count`/`value_cents` that feed the Dashboard funnel. Nothing is
 * recomputed here, deliberately: two screens quoting different numbers for one
 * pipeline is the failure this tab is most likely to cause.
 *
 * The projection rule is stated on the screen rather than left implicit, because
 * a weighted figure whose weighting is invisible reads as a promise.
 */
export function ForecastPanel({ pipeline }: { pipeline: Pipeline | undefined }) {
  const forecast = useQuery({
    queryKey: ['forecast', pipeline?.id],
    queryFn: () => getForecast(pipeline!.id),
    enabled: !!pipeline,
  })

  const f = forecast.data

  const cell = {
    padding: '0 12px',
    fontSize: 14,
    color: 'rgb(52,64,84)',
    borderBottom: '1px solid rgb(242,244,247)',
  } as const
  const head = {
    height: 44,
    padding: '0 12px',
    fontSize: 13,
    fontWeight: 700,
    color: 'rgb(71,84,103)',
    borderBottom: '1px solid rgb(234,236,240)',
    whiteSpace: 'nowrap',
  } as const

  return (
    <div className="min-h-0 flex-1 overflow-auto px-4 pb-4">
      {/* A failed query must say so rather than draw zeros — a forecast of $0.00
          is indistinguishable from an empty pipeline. Same rule as the Dashboard
          cards. */}
      {forecast.error ? (
        <div
          role="alert"
          className="bg-white"
          style={{
            borderRadius: 8,
            border: '1px solid rgb(234,236,240)',
            padding: 16,
            fontSize: 14,
            color: 'rgb(180,35,24)',
          }}
        >
          {(forecast.error as Error).message}
        </div>
      ) : !f ? (
        <div style={{ fontSize: 14, color: 'rgb(102,112,133)', padding: 16 }}>
          Loading forecast…
        </div>
      ) : (
        <>
          <div
            className="grid gap-4"
            style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))' }}
          >
            <Tile label="Open value" value={money(f.totals.open_value_cents)}
              note={`${f.totals.open_count} open opportunit${
                f.totals.open_count === 1 ? 'y' : 'ies'}`} />
            <Tile label="Weighted forecast"
              value={money(f.totals.weighted_value_cents)}
              note={`open value at ${f.conversion_rate.toFixed(2)}%`} />
            <Tile label="Won so far" value={money(f.totals.won_value_cents)}
              note={`${f.totals.won_count} won`} />
            <Tile label="Projected revenue"
              value={money(f.totals.projected_value_cents)}
              note="won + weighted" highlight />
          </div>

          <div style={{ fontSize: 12, color: 'rgb(102,112,133)', marginTop: 10 }}>
            Weighted at this pipeline&apos;s conversion rate —{' '}
            {f.status.won ?? 0} won of {(f.status.won ?? 0) + (f.status.lost ?? 0)}{' '}
            decided ={' '}
            <span style={{ fontWeight: 600, color: 'rgb(52,64,84)' }}>
              {f.conversion_rate.toFixed(2)}%
            </span>
            . The same rate the Dashboard shows. Stage history is not recorded, so
            there is no per-stage win rate to weight with.
          </div>

          <div
            className="mt-4 bg-white"
            style={{ borderRadius: 8, border: '1px solid rgb(234,236,240)' }}
          >
            <table className="w-full">
              <thead>
                <tr>
                  <th className="text-left" style={head}>Stage</th>
                  <th className="text-right" style={head}>Opportunities</th>
                  <th className="text-right" style={head}>Stage value</th>
                  <th className="text-right" style={head}>Open</th>
                  <th className="text-right" style={head}>Open value</th>
                  <th className="text-right" style={head}>Weighted</th>
                  <th className="text-right" style={head}>Won</th>
                  <th className="text-right" style={head}>Projected</th>
                </tr>
              </thead>
              <tbody>
                {f.stages.map((s) => (
                  // Two stages of the measured pipeline are both called "Call
                  // Back", so the key is the id and never the name.
                  <tr key={s.stage_id}>
                    <td className="truncate" style={{ ...cell, height: 44 }}>{s.name}</td>
                    <td className="text-right" style={cell}>{s.count}</td>
                    <td className="text-right" style={cell}>{money(s.value_cents)}</td>
                    <td className="text-right" style={cell}>{s.open_count}</td>
                    <td className="text-right" style={cell}>
                      {money(s.open_value_cents)}
                    </td>
                    <td className="text-right" style={cell}>
                      {money(s.weighted_value_cents)}
                    </td>
                    <td className="text-right" style={cell}>
                      {money(s.won_value_cents)}
                    </td>
                    <td className="text-right"
                      style={{ ...cell, fontWeight: 600, color: 'rgb(16,24,40)' }}>
                      {money(s.projected_value_cents)}
                    </td>
                  </tr>
                ))}
                <tr>
                  <td style={{ ...cell, height: 44, fontWeight: 700 }}>Total</td>
                  <td className="text-right" style={{ ...cell, fontWeight: 700 }}>
                    {f.totals.count}
                  </td>
                  <td className="text-right" style={{ ...cell, fontWeight: 700 }}>
                    {money(f.totals.value_cents)}
                  </td>
                  <td className="text-right" style={{ ...cell, fontWeight: 700 }}>
                    {f.totals.open_count}
                  </td>
                  <td className="text-right" style={{ ...cell, fontWeight: 700 }}>
                    {money(f.totals.open_value_cents)}
                  </td>
                  <td className="text-right" style={{ ...cell, fontWeight: 700 }}>
                    {money(f.totals.weighted_value_cents)}
                  </td>
                  <td className="text-right" style={{ ...cell, fontWeight: 700 }}>
                    {money(f.totals.won_value_cents)}
                  </td>
                  <td className="text-right"
                    style={{ ...cell, fontWeight: 700, color: 'rgb(16,24,40)' }}>
                    {money(f.totals.projected_value_cents)}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  )
}

function Tile({ label, value, note, highlight }: {
  label: string; value: string; note: string; highlight?: boolean
}) {
  return (
    <div className="bg-white" style={{
      borderRadius: 8,
      border: '1px solid ' + (highlight ? 'rgb(0,78,235)' : 'rgb(234,236,240)'),
      padding: 16,
    }}>
      <div style={{ fontSize: 13, color: 'rgb(102,112,133)' }}>{label}</div>
      <div style={{
        fontSize: 24, fontWeight: 500, marginTop: 4,
        color: highlight ? 'rgb(0,78,235)' : 'rgb(16,24,40)',
      }}>
        {value}
      </div>
      <div style={{ fontSize: 12, color: 'rgb(152,162,179)', marginTop: 2 }}>
        {note}
      </div>
    </div>
  )
}
