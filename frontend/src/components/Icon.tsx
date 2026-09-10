/**
 * Inline icon set. GHL uses icons everywhere; a text-only rebuild reads as a
 * different product even when every font size matches.
 *
 * Measured icon sizes in Conversations:
 *   sidebar rail   20x20 inside 36x36 buttons
 *   inbox header   24x24 (filter, sort)
 *   inbox tabs     20x20 above the label
 *   row affordance 14x14 (channel glyph, star)
 *   thread header  24x24 at a 40px pitch
 *   panel rail     22x22 at a 40px pitch
 */
type P = { size?: number; color?: string; className?: string; strokeWidth?: number }

const base = (size: number, color: string, strokeWidth: number) => ({
  width: size,
  height: size,
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: color,
  strokeWidth,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
})

const make =
  (path: React.ReactNode, filled = false) =>
  ({ size = 20, color = 'currentColor', className, strokeWidth = 1.8 }: P) =>
    (
      <svg
        {...base(size, filled ? 'none' : color, strokeWidth)}
        fill={filled ? color : 'none'}
        className={className}
        aria-hidden="true"
      >
        {path}
      </svg>
    )

export const IconChat = make(
  <path d="M21 11.5a8.4 8.4 0 0 1-9 8.4 8.7 8.7 0 0 1-3.9-.9L3 21l1.9-5a8.4 8.4 0 0 1-.9-3.9 8.4 8.4 0 0 1 8.4-8.4h.5A8.4 8.4 0 0 1 21 11z" />,
)
export const IconSearch = make(
  <>
    <circle cx="11" cy="11" r="7" />
    <path d="m21 21-4.3-4.3" />
  </>,
)
export const IconUser = make(
  <>
    <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
    <circle cx="12" cy="7" r="4" />
  </>,
)
export const IconUsers = make(
  <>
    <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
    <circle cx="9" cy="7" r="4" />
    <path d="M23 21v-2a4 4 0 0 0-3-3.9" />
  </>,
)
export const IconFunnel = make(<path d="M22 3H2l8 9.5V19l4 2v-8.5L22 3z" />)
export const IconEye = make(
  <>
    <path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7-10-7-10-7z" />
    <circle cx="12" cy="12" r="3" />
  </>,
)
export const IconSort = make(
  <>
    <path d="M7 4v16M7 20l-3-3M7 4l3 3" />
    <path d="M17 20V4M17 4l3 3M17 20l-3-3" />
  </>,
)
export const IconStar = make(
  <path d="m12 3 2.9 5.9 6.4.9-4.6 4.5 1 6.4-5.7-3-5.7 3 1-6.4L3 9.8l6.4-.9L12 3z" />,
)
export const IconStarFilled = make(
  <path d="m12 3 2.9 5.9 6.4.9-4.6 4.5 1 6.4-5.7-3-5.7 3 1-6.4L3 9.8l6.4-.9L12 3z" />,
  true,
)
export const IconPhone = make(
  <path d="M22 16.9v2.6a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3 19.5 19.5 0 0 1-6-6 19.8 19.8 0 0 1-3-8.7A2 2 0 0 1 4.1 2h2.6a2 2 0 0 1 2 1.7c.1 1 .3 1.9.6 2.8a2 2 0 0 1-.4 2.1L7.8 9.8a16 16 0 0 0 6 6l1.2-1.1a2 2 0 0 1 2.1-.5c.9.3 1.8.5 2.8.6a2 2 0 0 1 1.7 2z" />,
)
export const IconMail = make(
  <>
    <rect x="2" y="4" width="20" height="16" rx="2" />
    <path d="m2 7 10 6 10-6" />
  </>,
)
export const IconTrash = make(
  <>
    <path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6" />
  </>,
)
export const IconCalendar = make(
  <>
    <rect x="3" y="5" width="18" height="16" rx="2" />
    <path d="M16 3v4M8 3v4M3 11h18" />
  </>,
)
export const IconInbox = make(
  <>
    <path d="M22 12h-6l-2 3h-4l-2-3H2" />
    <path d="M5.5 5h13l3.5 7v6a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2v-6l3.5-7z" />
  </>,
)
export const IconClock = make(
  <>
    <circle cx="12" cy="12" r="9" />
    <path d="M12 7v5l3 2" />
  </>,
)
export const IconPlus = make(<path d="M12 5v14M5 12h14" />)
export const IconChevronDown = make(<path d="m6 9 6 6 6-6" />)
export const IconExternal = make(
  <>
    <path d="M15 3h6v6" />
    <path d="M10 14 21 3" />
    <path d="M21 14v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5" />
  </>,
)
export const IconGrid = make(
  <>
    <rect x="3" y="3" width="7" height="7" rx="1" />
    <rect x="14" y="3" width="7" height="7" rx="1" />
    <rect x="3" y="14" width="7" height="7" rx="1" />
    <rect x="14" y="14" width="7" height="7" rx="1" />
  </>,
)
export const IconSettings = make(
  <>
    <circle cx="12" cy="12" r="3" />
    <path d="M19.4 15a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-2.7 1.1V21a2 2 0 1 1-4 0v-.1A1.6 1.6 0 0 0 7 19.4a1.6 1.6 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.6 1.6 0 0 0-1.1-2.7H1a2 2 0 1 1 0-4h.1A1.6 1.6 0 0 0 2.6 7a1.6 1.6 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.6 1.6 0 0 0 1.8.3H7a1.6 1.6 0 0 0 1-1.5V1a2 2 0 1 1 4 0v.1a1.6 1.6 0 0 0 2.7 1.1 1.6 1.6 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0-.3 1.8V7a1.6 1.6 0 0 0 1.5 1H23a2 2 0 1 1 0 4h-.1a1.6 1.6 0 0 0-1.5 1z" />
  </>,
)
export const IconPlay = make(
  <>
    <circle cx="12" cy="12" r="9" />
    <path d="M10 8.5v7l5.5-3.5-5.5-3.5z" />
  </>,
)
export const IconCard = make(
  <>
    <rect x="2" y="5" width="20" height="14" rx="2" />
    <path d="M2 10h20" />
  </>,
)
export const IconImage = make(
  <>
    <rect x="3" y="3" width="18" height="18" rx="2" />
    <circle cx="8.5" cy="8.5" r="1.5" />
    <path d="m21 15-5-5L5 21" />
  </>,
)
export const IconChart = make(<path d="M3 3v18h18M7 15l3-4 3 3 5-7" />)
export const IconSparkle = make(
  <path d="M12 3v6M12 15v6M3 12h6M15 12h6M6 6l3 3M15 15l3 3M18 6l-3 3M9 15l-3 3" />,
)
export const IconFilter = make(<path d="M4 5h16M7 12h10M10 19h4" />)

export const IconDownload = make(
  <>
    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
    <path d="M7 10l5 5 5-5M12 15V3" />
  </>,
)
export const IconList = make(
  <>
    <path d="M8 6h13M8 12h13M8 18h13" />
    <path d="M3 6h.01M3 12h.01M3 18h.01" />
  </>,
)

export const IconChevronLeft = make(<path d="m15 18-6-6 6-6" />)
