import { useEffect, useRef, useState } from 'react'

/**
 * The opportunity modal's primitives, measured by eye from the owner's
 * GoHighLevel screenshots 11 and 22 (1x, 2026-09-13). No capture JSON exists for
 * this modal, so these are screenshot readings, not computed styles — see the
 * assumption list in .qa/state/oppmodal-done.
 *
 *   labels   14px/500 rgb(52,64,84), required * rgb(217,45,32)
 *   inputs   36px tall, radius 6, 1px rgb(208,213,221), 14px rgb(16,24,40)
 *   headings 16px/500 rgb(52,64,84)
 *   nav      13px rgb(71,84,103); active 13px/600 on rgb(239,244,255)
 *   primary  rgb(21,94,239)
 */
export const TEXT = 'rgb(16,24,40)'
export const BODY = 'rgb(52,64,84)'
export const MUTED = 'rgb(71,84,103)'
export const FAINT = 'rgb(102,112,133)'
export const PLACEHOLDER = 'rgb(152,162,179)'
export const BORDER = 'rgb(208,213,221)'
export const DIVIDER = 'rgb(234,236,240)'
export const PRIMARY = 'rgb(21,94,239)'
export const PRIMARY_TINT = 'rgb(239,244,255)'
export const DANGER = 'rgb(217,45,32)'

export const LABEL: React.CSSProperties = { fontSize: 14, fontWeight: 500, color: BODY }

export const INPUT: React.CSSProperties = {
  width: '100%', height: 36, marginTop: 8, fontSize: 14, color: TEXT,
  borderRadius: 6, border: '1px solid ' + BORDER, padding: '0 12px',
  backgroundColor: '#fff', outline: 'none',
}

export const HEADING: React.CSSProperties = { fontSize: 16, fontWeight: 500, color: BODY }

export const BUTTON: React.CSSProperties = {
  height: 40, padding: '0 18px', borderRadius: 8, fontSize: 14, fontWeight: 500,
  color: BODY, border: '1px solid ' + BORDER, backgroundColor: '#fff',
}

export const PRIMARY_BUTTON: React.CSSProperties = {
  ...BUTTON, color: '#fff', fontWeight: 600, border: '1px solid ' + PRIMARY,
  backgroundColor: PRIMARY,
}

export const dead = (enabled: boolean): React.CSSProperties =>
  enabled ? {} : { opacity: 0.5, cursor: 'not-allowed' }

export function Label({ children, required = false }: {
  children: React.ReactNode; required?: boolean
}) {
  return (
    <div style={LABEL}>
      {children}
      {required && <span style={{ color: DANGER, marginLeft: 4 }}>*</span>}
    </div>
  )
}

export function Chevron({ color = FAINT }: { color?: string }) {
  return (
    <svg width={16} height={16} viewBox="0 0 24 24" fill="none" stroke={color}
      strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="m6 9 6 6 6-6" />
    </svg>
  )
}

/** A native <select> dressed as GoHighLevel's: no system arrow, a thin chevron. */
export function Select({ value, onChange, children, disabled, title, ariaLabel }: {
  value: string | number
  onChange: (value: string) => void
  children: React.ReactNode
  disabled?: boolean
  title?: string
  ariaLabel?: string
}) {
  return (
    <div className="relative">
      <select
        value={value}
        aria-label={ariaLabel}
        disabled={disabled}
        title={title}
        onChange={(e) => onChange(e.target.value)}
        style={{
          ...INPUT, appearance: 'none', paddingRight: 34,
          ...(disabled ? { backgroundColor: 'rgb(249,250,251)', cursor: 'not-allowed' } : {}),
        }}
      >
        {children}
      </select>
      <span className="pointer-events-none absolute" style={{ right: 12, top: 18 }}>
        <Chevron />
      </span>
    </div>
  )
}

/** "Sep 13 2026, 9:17am (EDT)" — GoHighLevel's footer stamp. */
export function footerStamp(iso: string, timeZone?: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '--'
  const parts = new Intl.DateTimeFormat('en-US', {
    month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric',
    minute: '2-digit', timeZoneName: 'short', timeZone,
  }).formatToParts(d)
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? ''
  return `${get('month')} ${get('day')} ${get('year')}, ${get('hour')}:${get('minute')}`
    + `${get('dayPeriod').toLowerCase()} (${get('timeZoneName')})`
}

/** "Sep 13 2026, 9:17 AM (EDT)" — a note card's stamp (screenshot 22). */
export function noteStamp(iso: string, timeZone?: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '--'
  const parts = new Intl.DateTimeFormat('en-US', {
    month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric',
    minute: '2-digit', timeZoneName: 'short', timeZone,
  }).formatToParts(d)
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? ''
  return `${get('month')} ${get('day')} ${get('year')}, ${get('hour')}:${get('minute')} `
    + `${get('dayPeriod').toUpperCase()} (${get('timeZoneName')})`
}

/** The full-width light-blue "+ Add note" / "+ Add task" bar (screenshot 22). */
export function AddBar({ label, onClick, disabled = false, title }: {
  label: string; onClick: () => void; disabled?: boolean; title?: string
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={title}
      className="flex w-full items-center justify-center gap-2"
      style={{
        height: 36, borderRadius: 8, backgroundColor: PRIMARY_TINT, color: PRIMARY,
        fontSize: 14, fontWeight: 500, ...dead(!disabled),
      }}
    >
      <svg width={16} height={16} viewBox="0 0 24 24" fill="none" stroke={PRIMARY}
        strokeWidth={2} strokeLinecap="round" aria-hidden="true">
        <path d="M12 5v14M5 12h14" />
      </svg>
      {label}
    </button>
  )
}

/** The ⋮ menu at the right of a note or a task. */
export function KebabMenu({ items, label }: {
  label: string
  items: { label: string; onClick: () => void; danger?: boolean }[]
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onDown)
    window.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      window.removeEventListener('keydown', onKey)
    }
  }, [open])
  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        aria-label={label}
        aria-haspopup="menu"
        onClick={() => setOpen((v) => !v)}
        className="flex items-center justify-center"
        style={{ width: 24, height: 24, borderRadius: 6, color: FAINT }}
      >
        <svg width={16} height={16} viewBox="0 0 24 24" fill={FAINT} aria-hidden="true">
          <circle cx="12" cy="5" r="1.6" /><circle cx="12" cy="12" r="1.6" />
          <circle cx="12" cy="19" r="1.6" />
        </svg>
      </button>
      {open && (
        <div role="menu" className="absolute right-0 z-10 bg-white"
          style={{ top: 26, minWidth: 120, borderRadius: 8, padding: 4,
            border: '1px solid ' + DIVIDER, boxShadow: '0 12px 16px -4px rgba(16,24,40,0.08)' }}>
          {items.map((item) => (
            <button key={item.label} role="menuitem" type="button"
              onClick={() => { setOpen(false); item.onClick() }}
              className="block w-full text-left hover:bg-[rgb(249,250,251)]"
              style={{ padding: '8px 12px', fontSize: 14, borderRadius: 6,
                color: item.danger ? DANGER : BODY }}>
              {item.label}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

export function ErrorLine({ error }: { error: string | null }) {
  if (!error) return null
  return (
    <div role="alert" style={{ fontSize: 13, color: DANGER, marginTop: 8 }}>{error}</div>
  )
}

/** A dropdown list anchored under a field, closing on an outside click or Escape. */
export function useDismiss(open: boolean, close: () => void) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) close()
    }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') close() }
    document.addEventListener('mousedown', onDown)
    window.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      window.removeEventListener('keydown', onKey)
    }
  }, [open, close])
  return ref
}
