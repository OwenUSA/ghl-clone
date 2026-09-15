import { useEffect } from 'react'
import { IconClose } from '../PipelineIcons'
import {
  BODY, BORDER, DANGER, DIVIDER, FAINT, INPUT, LABEL, MUTED, PRIMARY, PRIMARY_TINT, TEXT,
} from '../opportunity/ui'

/**
 * The AI Agents module's primitives (2026-09-15). No GoHighLevel screenshot of this module
 * exists on the server, so everything is built from the Opportunities rebuild's parts:
 * the Pipelines list (refs/opps/02) for tables and page headers, the Create pipeline modal
 * (refs/opps/04) for dialogs, the opportunity modal's left nav (refs/opps/22) for sections.
 */
export { BODY, BORDER, DANGER, DIVIDER, FAINT, INPUT, LABEL, MUTED, PRIMARY, PRIMARY_TINT, TEXT }

export const INK = 'rgb(16,24,40)'
export const LINE = 'rgb(234,236,240)'
export const BLUE = 'rgb(21,94,239)'
export const PAGE_BG = 'rgb(249,250,251)'

export const smallButton: React.CSSProperties = {
  height: 30, padding: '0 10px', borderRadius: 4, fontSize: 13, fontWeight: 500,
  color: BODY, border: `1px solid ${BORDER}`, backgroundColor: '#fff',
  display: 'inline-flex', alignItems: 'center', gap: 6, whiteSpace: 'nowrap',
}

export const primarySmall: React.CSSProperties = {
  ...smallButton, color: '#fff', border: `1px solid ${BLUE}`, backgroundColor: BLUE,
}

export const textButton: React.CSSProperties = {
  fontSize: 13, fontWeight: 500, color: BLUE, background: 'transparent', padding: 0,
}

export const textarea: React.CSSProperties = {
  ...INPUT, height: 'auto', minHeight: 88, padding: '8px 12px', lineHeight: 1.5, resize: 'vertical',
}

export function PageHeader({ title, subtitle, children }: {
  title: string; subtitle?: string; children?: React.ReactNode
}) {
  return (
    <div className="flex items-start gap-12">
      <div className="min-w-0">
        <div style={{ fontSize: 20, fontWeight: 600, color: INK, lineHeight: '28px' }}>{title}</div>
        {subtitle && (
          <div style={{ fontSize: 12, color: MUTED, marginTop: 6 }}>{subtitle}</div>
        )}
      </div>
      <div className="ml-auto flex items-center gap-8" style={{ marginTop: 10 }}>{children}</div>
    </div>
  )
}

export function Chip({ text, fg, bg, title }: { text: string; fg: string; bg: string; title?: string }) {
  return (
    <span title={title} style={{ display: 'inline-block', padding: '1px 8px', borderRadius: 10,
      fontSize: 12, fontWeight: 500, color: fg, backgroundColor: bg, whiteSpace: 'nowrap' }}>
      {text}
    </span>
  )
}

export function Notice({ error, notice, onDismiss }: {
  error?: string | null; notice?: string | null; onDismiss: () => void
}) {
  if (!error && !notice) return null
  return (
    <div role={error ? 'alert' : 'status'} className="flex items-start gap-12"
      style={{ marginTop: 12, fontSize: 13, borderRadius: 8, padding: '8px 12px',
        color: error ? 'rgb(180,35,24)' : 'rgb(2,122,72)',
        backgroundColor: error ? 'rgb(254,243,242)' : 'rgb(236,253,243)',
        border: `1px solid ${error ? 'rgb(253,162,155)' : 'rgb(166,244,197)'}` }}>
      <span className="flex-1" style={{ whiteSpace: 'pre-wrap' }}>{error ?? notice}</span>
      <button type="button" aria-label="Dismiss" onClick={onDismiss} style={{ padding: 2 }}>
        <IconClose size={14} color={error ? 'rgb(180,35,24)' : 'rgb(2,122,72)'} />
      </button>
    </div>
  )
}

export function Th({ children, width, align = 'left' }: {
  children?: React.ReactNode; width?: number | string; align?: 'left' | 'right' | 'center'
}) {
  return (
    <th style={{ width, padding: '0 12px', height: 36, fontSize: 12, fontWeight: 500, color: INK,
      textAlign: align, borderRight: `1px solid ${LINE}`, borderBottom: `1px solid ${LINE}`,
      whiteSpace: 'nowrap', backgroundColor: PAGE_BG }}>
      {children}
    </th>
  )
}

export function Td({ children, align = 'left', style }: {
  children?: React.ReactNode; align?: 'left' | 'right' | 'center'; style?: React.CSSProperties
}) {
  return (
    <td style={{ padding: '10px 12px', fontSize: 13, color: INK, textAlign: align,
      borderBottom: `1px solid ${LINE}`, verticalAlign: 'top', ...style }}>
      {children}
    </td>
  )
}

/** A white bordered card holding a table, like the Pipelines list. */
export function TableCard({ toolbar, children, footer }: {
  toolbar?: React.ReactNode; children: React.ReactNode; footer?: React.ReactNode
}) {
  return (
    <div className="bg-white" style={{ marginTop: 12, border: `1px solid ${LINE}`, borderRadius: 4 }}>
      {toolbar && (
        <div className="flex flex-wrap items-center gap-8" style={{ minHeight: 41, padding: '6px 8px',
          borderBottom: `1px solid ${LINE}` }}>
          {toolbar}
        </div>
      )}
      <div style={{ overflowX: 'auto' }}>{children}</div>
      {footer}
    </div>
  )
}

/** The Create pipeline modal's frame (refs/opps/04). */
export function Modal({ title, subtitle, onClose, children, footer, width = 560 }: {
  title: string; subtitle?: string; onClose: () => void; children: React.ReactNode
  footer?: React.ReactNode; width?: number
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ backgroundColor: 'rgba(52,64,84,0.6)' }} onMouseDown={onClose}>
      <div role="dialog" aria-modal="true" aria-label={title} onMouseDown={(e) => e.stopPropagation()}
        className="flex flex-col bg-white"
        style={{ width, maxWidth: 'calc(100vw - 32px)', maxHeight: 'calc(100vh - 32px)',
          borderRadius: 8, boxShadow: '0 20px 24px -4px rgba(16,24,40,0.08)' }}>
        <div className="flex items-start" style={{ padding: '18px 16px 10px' }}>
          <div>
            <div style={{ fontSize: 16, fontWeight: 600, color: INK }}>{title}</div>
            {subtitle && <div style={{ fontSize: 13, color: FAINT, marginTop: 4 }}>{subtitle}</div>}
          </div>
          <button type="button" onClick={onClose} aria-label="Close" className="ml-auto" style={{ padding: 4 }}>
            <IconClose size={18} color={TEXT} />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto" style={{ padding: '6px 16px 16px' }}>{children}</div>
        {footer && (
          <div className="flex items-center justify-end gap-8"
            style={{ padding: '12px 16px', borderTop: `1px solid ${LINE}` }}>
            {footer}
          </div>
        )}
      </div>
    </div>
  )
}

export function Field({ label, required, hint, children }: {
  label: string; required?: boolean; hint?: string; children: React.ReactNode
}) {
  return (
    <label className="block" style={{ marginTop: 14 }}>
      <span style={LABEL}>
        {label}
        {required && <span style={{ color: DANGER, marginLeft: 4 }}>*</span>}
      </span>
      {children}
      {hint && <span className="block" style={{ fontSize: 12, color: FAINT, marginTop: 4 }}>{hint}</span>}
    </label>
  )
}

/** The pipeline modal's toggle. */
export function Switch({ checked, onChange, label }: {
  checked: boolean; onChange: (v: boolean) => void; label: string
}) {
  return (
    <button type="button" role="switch" aria-checked={checked} aria-label={label}
      onClick={() => onChange(!checked)}
      style={{ width: 36, height: 20, borderRadius: 10, padding: 2, flexShrink: 0,
        backgroundColor: checked ? BLUE : 'rgb(234,236,240)', transition: 'background-color .15s' }}>
      <span style={{ display: 'block', width: 16, height: 16, borderRadius: 8, backgroundColor: '#fff',
        boxShadow: '0 1px 2px rgba(16,24,40,0.2)', transform: checked ? 'translateX(16px)' : 'none',
        transition: 'transform .15s' }} />
    </button>
  )
}

export function Confirm({ title, body, confirmLabel, danger, busy, onCancel, onConfirm }: {
  title: string; body: React.ReactNode; confirmLabel: string; danger?: boolean; busy?: boolean
  onCancel: () => void; onConfirm: () => void
}) {
  return (
    <div className="fixed inset-0 flex items-center justify-center" style={{ zIndex: 60,
      backgroundColor: 'rgba(52,64,84,0.4)' }} onMouseDown={onCancel}>
      <div role="alertdialog" aria-label={title} onMouseDown={(e) => e.stopPropagation()}
        className="bg-white" style={{ width: 440, maxWidth: 'calc(100vw - 32px)', borderRadius: 8, padding: 20 }}>
        <div style={{ fontSize: 16, fontWeight: 600, color: INK }}>{title}</div>
        <div style={{ fontSize: 13, color: TEXT, marginTop: 8, lineHeight: 1.5 }}>{body}</div>
        <div className="flex justify-end gap-8" style={{ marginTop: 18 }}>
          <button type="button" onClick={onCancel} style={smallButton}>Cancel</button>
          <button type="button" onClick={onConfirm} disabled={busy}
            style={{ ...primarySmall, ...(danger ? { backgroundColor: DANGER, borderColor: DANGER } : {}),
              opacity: busy ? 0.6 : 1 }}>
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}

/** A kebab menu with icons, anchored to the right (refs/opps/03). */
export function RowMenu({ open, onToggle, onClose, items, label }: {
  open: boolean; onToggle: () => void; onClose: () => void; label: string
  items: { label: string; icon?: React.ReactNode; run: () => void; danger?: boolean }[]
}) {
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    const onDown = () => onClose()
    window.addEventListener('keydown', onKey)
    const t = setTimeout(() => document.addEventListener('mousedown', onDown), 0)
    return () => {
      window.removeEventListener('keydown', onKey)
      clearTimeout(t)
      document.removeEventListener('mousedown', onDown)
    }
  }, [open, onClose])
  return (
    <div className="relative inline-block">
      <button type="button" aria-label={label} aria-haspopup="menu" aria-expanded={open}
        onClick={onToggle}
        className="flex items-center justify-center"
        style={{ width: 26, height: 26, borderRadius: 4, backgroundColor: open ? 'rgb(242,244,247)' : 'transparent' }}>
        <svg width={16} height={16} viewBox="0 0 24 24" fill="rgb(71,84,103)" aria-hidden="true">
          <circle cx="12" cy="5" r="1.6" /><circle cx="12" cy="12" r="1.6" /><circle cx="12" cy="19" r="1.6" />
        </svg>
      </button>
      {open && (
        <div role="menu" onMouseDown={(e) => e.stopPropagation()}
          className="absolute right-0 bg-white" style={{ top: 30, zIndex: 20, minWidth: 180,
            padding: 4, borderRadius: 6, border: `1px solid ${LINE}`,
            boxShadow: '0 12px 16px -4px rgba(16,24,40,0.08)' }}>
          {items.map((item) => (
            <button key={item.label} role="menuitem" type="button"
              onClick={() => { onClose(); item.run() }}
              className="flex w-full items-center gap-8 text-left hover:bg-[rgb(249,250,251)]"
              style={{ padding: '7px 10px', fontSize: 14, borderRadius: 4,
                color: item.danger ? DANGER : TEXT }}>
              {item.icon}
              {item.label}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

export function SectionTitle({ children, right }: { children: React.ReactNode; right?: React.ReactNode }) {
  return (
    <div className="flex items-center" style={{ marginBottom: 4 }}>
      <div style={{ fontSize: 16, fontWeight: 500, color: BODY }}>{children}</div>
      {right && <div className="ml-auto">{right}</div>}
    </div>
  )
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <div style={{ padding: 24, textAlign: 'center', fontSize: 13, color: MUTED }}>{children}</div>
}

/** Sub-tabs inside a tab: the Settings tab row's look, smaller. */
export function SubTabs<T extends string>({ tabs, active, onSelect, label }: {
  tabs: { key: T; label: string }[]; active: T; onSelect: (k: T) => void; label: string
}) {
  return (
    <div role="tablist" aria-label={label} className="flex" style={{ gap: 24, borderBottom: `1px solid ${LINE}` }}>
      {tabs.map((t) => (
        <button key={t.key} type="button" role="tab" aria-selected={active === t.key}
          onClick={() => onSelect(t.key)}
          style={{ height: 36, fontSize: 14, fontWeight: 500,
            color: active === t.key ? 'rgb(56,160,219)' : 'rgb(71,84,103)',
            borderBottom: '2px solid ' + (active === t.key ? 'rgb(56,160,219)' : 'transparent') }}>
          {t.label}
        </button>
      ))}
    </div>
  )
}
