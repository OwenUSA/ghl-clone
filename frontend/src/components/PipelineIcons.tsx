/**
 * The icons the Pipelines tab, its row menu and its modal draw, matched to the
 * GoHighLevel screenshots (refs/opps/02-04). Kept out of Icon.tsx so another
 * branch appending to that file cannot collide with these.
 */
type P = { size?: number; color?: string; strokeWidth?: number }

const svg = (size: number, color: string, strokeWidth: number) => ({
  width: size, height: size, viewBox: '0 0 24 24', fill: 'none', stroke: color,
  strokeWidth, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const,
  'aria-hidden': true,
})

const make = (path: React.ReactNode) =>
  ({ size = 16, color = 'currentColor', strokeWidth = 1.8 }: P) =>
    <svg {...svg(size, color, strokeWidth)}>{path}</svg>

/** Six dots — the drag handle on a table row and on a stage row. */
export function IconGrip({ size = 16, color = 'rgb(152,162,179)' }: P) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill={color} aria-hidden>
      {[6, 12, 18].map((y) => (
        <g key={y}>
          <circle cx="9" cy={y} r="1.6" />
          <circle cx="15" cy={y} r="1.6" />
        </g>
      ))}
    </svg>
  )
}

export function IconKebab({ size = 16, color = 'rgb(71,84,103)' }: P) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill={color} aria-hidden>
      <circle cx="12" cy="5" r="1.7" /><circle cx="12" cy="12" r="1.7" />
      <circle cx="12" cy="19" r="1.7" />
    </svg>
  )
}

export const IconPencil = make(
  <><path d="M12 20h9" /><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5z" /></>)
export const IconDuplicate = make(
  <><rect x="9" y="9" width="12" height="12" rx="2" strokeDasharray="3 2" />
    <path d="M5 15H4a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v1" /></>)
export const IconKey = make(
  <><circle cx="7.5" cy="15.5" r="4.5" /><path d="m10.7 12.3 9.8-9.8M17 6l3 3M14 9l2 2" /></>)
export const IconLink = make(
  <><path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7" />
    <path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7" /></>)
export const IconTrashOutline = make(
  <><path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6M10 11v6M14 11v6" /></>)
export const IconClose = make(<path d="M18 6 6 18M6 6l12 12" />)
export const IconPlus = make(<path d="M12 5v14M5 12h14" />)
export const IconSearchSmall = make(<><circle cx="11" cy="11" r="7" /><path d="m21 21-4.3-4.3" /></>)
export const IconChevronDown = make(<path d="m6 9 6 6 6-6" />)
export const IconInfo = make(<><circle cx="12" cy="12" r="9" /><path d="M12 11v5M12 8h.01" /></>)
export const IconHash = make(<path d="M4 9h16M4 15h16M10 3 8 21M16 3l-2 18" />)
export const IconCalendarSmall = make(
  <><rect x="3" y="5" width="18" height="16" rx="2" /><path d="M16 3v4M8 3v4M3 11h18" /></>)
/** The boxed "A" GoHighLevel puts before a text column's heading. */
export const IconTextBox = make(
  <><rect x="3" y="3" width="18" height="18" rx="2" /><path d="m8 17 4-10 4 10M9.5 13.5h5" /></>)
/** The Actions heading's cursor-in-a-circle. */
export const IconActions = make(
  <><path d="M21 12a9 9 0 1 0-9 9" /><path d="m13 13 8 3-3.5 1.5L16 21z" /></>)
/** "Show in reports": funnel. */
export const IconReportFunnel = make(<><path d="M4 5h16l-6 7v6l-4 2v-8z" /><path d="M7 9h10" /></>)
/** "Show in reports": pie. */
export const IconReportPie = make(
  <><circle cx="12" cy="12" r="9" /><path d="M12 3v9l6.4 6.4M12 12H3" /></>)
