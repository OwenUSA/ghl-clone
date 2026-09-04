/**
 * Donut chart. Shared by Dashboard and Reporting.
 *
 * Measured on GHL: reporting donuts render a thick ring with a centred label
 * ("Total" over the count on Call report; a bare count on Appointment report).
 * An empty report still draws the grey ring — it does not collapse to nothing.
 */
export function Donut({
  segments,
  center,
  caption,
  size = 168,
  thickness = 22,
}: {
  segments: { key: string; n: number; color: string }[]
  center: string
  caption?: string
  size?: number
  thickness?: number
}) {
  const total = segments.reduce((s, x) => s + x.n, 0)
  const r = (size - thickness) / 2
  const circ = 2 * Math.PI * r
  let offset = 0

  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
      <circle cx={size / 2} cy={size / 2} r={r} fill="none"
        stroke="rgb(222,226,233)" strokeWidth={thickness} />
      {total > 0 && segments.map((s) => {
        const len = (s.n / total) * circ
        const el = (
          <circle
            key={s.key}
            cx={size / 2} cy={size / 2} r={r} fill="none"
            stroke={s.color} strokeWidth={thickness}
            strokeDasharray={`${len} ${circ - len}`}
            strokeDashoffset={-offset}
            transform={`rotate(-90 ${size / 2} ${size / 2})`}
          />
        )
        offset += len
        return el
      })}
      {caption && (
        <text x="50%" y="44%" textAnchor="middle" dominantBaseline="middle"
          style={{ fontSize: 16, fill: 'rgb(52,64,84)' }}>{caption}</text>
      )}
      <text
        x="50%"
        y={caption ? '58%' : '50%'}
        textAnchor="middle"
        dominantBaseline="middle"
        style={{
          fontSize: caption ? 16 : 24,
          fontWeight: caption ? 400 : 500,
          fill: 'rgb(16,24,40)',
        }}
      >
        {center}
      </text>
    </svg>
  )
}
