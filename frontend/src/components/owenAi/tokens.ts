// The Owen AI page's colours — the same values the Dispatch assistant uses (AssistantChat.tsx).
export const INK = 'rgb(16,24,40)'
export const MUTED = 'rgb(102,112,133)'
export const LINE = 'rgb(228,231,236)'
export const BLUE = 'rgb(21,94,239)'
export const RED = 'rgb(180,35,24)'
export const SIDEBAR_BG = 'rgb(244,245,247)'
export const MONO = 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace'
export const NARROW = 900

export const chipBtn: React.CSSProperties = { fontSize: 12, color: MUTED, border: `1px solid ${LINE}`,
  borderRadius: 999, padding: '4px 10px', background: '#fff', flexShrink: 0 }

/** Alert severity chips (2026-11-03) — the CRM's error / warning / gray badges. */
export const SEVERITY_COLORS: Record<'urgent' | 'today' | 'watch', { fg: string; bg: string; dot: string }> = {
  urgent: { fg: 'rgb(180,35,24)', bg: 'rgb(254,243,242)', dot: 'rgb(240,68,56)' },
  today: { fg: 'rgb(181,71,8)', bg: 'rgb(255,250,235)', dot: 'rgb(247,144,9)' },
  watch: { fg: 'rgb(71,84,103)', bg: 'rgb(242,244,247)', dot: 'rgb(152,162,179)' },
}
