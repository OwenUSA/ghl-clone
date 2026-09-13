import { useEffect, useRef, useState } from 'react'
import { IconChevronDown, IconPlus } from './Icon'
import {
  choosable,
  initialHighlight,
  moveHighlight,
  pickerRows,
} from '../lib/pageTabs'

const TEXT = 'rgb(52,64,84)'
const MUTED = 'rgb(152,162,179)'
const LINE = 'rgb(208,213,221)'
const BLUE = 'rgb(21,94,239)'

/**
 * GoHighLevel's pipeline dropdown (refs/round3/31), replacing the native select.
 *
 * A bordered trigger with the pipeline name and a chevron. The list opens under
 * it: a grey "All pipelines" label that cannot be chosen, every pipeline the
 * board can show (the caller passes the same access-filtered list the board
 * reads), a check on the selected one, then "+ New pipeline" for the roles that
 * may create one. Arrow keys move, Enter chooses, Escape and an outside click close.
 */
export function PipelinePicker({ pipelines, selectedId, role, onSelect, onNew }: {
  pipelines: { id: number; name: string }[]
  selectedId: number | null | undefined
  role: string
  onSelect: (id: number) => void
  onNew: () => void
}) {
  const [open, setOpen] = useState(false)
  const [hi, setHi] = useState(-1)
  const box = useRef<HTMLDivElement>(null)
  const list = useRef<HTMLDivElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const rows = pickerRows(pipelines, selectedId, role)
  const current = pipelines.find((p) => p.id === selectedId)

  useEffect(() => {
    if (!open) return
    list.current?.focus()
    const onDown = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [open])

  const show = () => {
    setHi(initialHighlight(rows))
    setOpen(true)
  }
  const close = () => {
    setOpen(false)
    trigger.current?.focus()
  }
  const choose = (i: number) => {
    const row = rows[i]
    if (!choosable(row)) return
    close()
    if (row.kind === 'pipeline') onSelect(row.id)
    else if (row.kind === 'new') onNew()
  }

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault()
      setHi((h) => moveHighlight(rows, h, e.key === 'ArrowDown' ? 1 : -1))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      choose(hi)
    } else if (e.key === 'Escape') {
      e.preventDefault()
      close()
    } else if (e.key === 'Tab') {
      setOpen(false)
    }
  }

  return (
    <div ref={box} className="relative">
      <button
        ref={trigger}
        type="button"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label="Pipeline"
        onClick={() => (open ? close() : show())}
        onKeyDown={(e) => {
          if (!open && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) {
            e.preventDefault()
            show()
          }
        }}
        className="flex items-center bg-white"
        style={{
          height: 40, width: 232, gap: 8, padding: '0 12px', borderRadius: 8,
          border: `1px solid ${LINE}`, fontSize: 16, fontWeight: 400, color: TEXT,
        }}
      >
        <span className="min-w-0 flex-1 truncate text-left" title={current?.name}>
          {current?.name ?? ''}
        </span>
        <IconChevronDown size={16} color="rgb(102,112,133)" />
      </button>

      {open && (
        <div
          ref={list}
          role="listbox"
          aria-label="Pipelines"
          tabIndex={-1}
          aria-activedescendant={hi >= 0 ? `pipeline-picker-${hi}` : undefined}
          onKeyDown={onKey}
          className="absolute left-0 z-30 bg-white outline-none"
          style={{
            top: 44, width: 232, borderRadius: 8, padding: '4px 0',
            border: '1px solid rgb(234,236,240)',
            boxShadow: '0 12px 16px -4px rgba(16,24,40,0.08), 0 4px 6px -2px rgba(16,24,40,0.03)',
          }}
        >
          {rows.map((r, i) => {
            if (r.kind === 'label') {
              return (
                <div key="label" role="presentation" aria-disabled="true"
                  style={{ padding: '8px 12px', fontSize: 14, color: MUTED, cursor: 'default' }}>
                  {r.text}
                </div>
              )
            }
            if (r.kind === 'divider') {
              return <div key="divider" role="separator"
                style={{ height: 1, margin: '4px 0', backgroundColor: 'rgb(234,236,240)' }} />
            }
            const lit = i === hi
            if (r.kind === 'new') {
              return (
                <div key="new" id={`pipeline-picker-${i}`} role="option" aria-selected={false}
                  onMouseEnter={() => setHi(i)} onClick={() => choose(i)}
                  className="flex items-center"
                  style={{
                    gap: 8, padding: '8px 12px', fontSize: 14, fontWeight: 500, color: BLUE,
                    cursor: 'pointer', backgroundColor: lit ? 'rgb(249,250,251)' : undefined,
                  }}>
                  <IconPlus size={16} color={BLUE} />
                  {r.text}
                </div>
              )
            }
            return (
              <div key={r.id} id={`pipeline-picker-${i}`} role="option" aria-selected={r.selected}
                onMouseEnter={() => setHi(i)} onClick={() => choose(i)}
                className="flex items-center"
                style={{
                  gap: 8, padding: '8px 12px', fontSize: 14,
                  color: r.selected ? BLUE : TEXT, cursor: 'pointer',
                  backgroundColor: r.selected
                    ? 'rgb(239,244,255)'
                    : lit ? 'rgb(249,250,251)' : undefined,
                }}>
                <span className="min-w-0 flex-1 truncate" title={r.name}>{r.name}</span>
                {r.selected && (
                  <svg width={16} height={16} viewBox="0 0 24 24" fill="none" stroke={BLUE}
                    strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
                    <path d="M20 6 9 17l-5-5" />
                  </svg>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
