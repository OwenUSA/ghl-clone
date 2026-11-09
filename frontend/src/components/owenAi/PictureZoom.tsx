import { useEffect, useState } from 'react'
import { BLUE, LINE, MUTED } from './tokens'

/**
 * A step-picture thumbnail that opens a full-width viewer on click (2026-11-09), like the
 * inspection report's photos. Only ever given a same-origin /api/owen-ai/proposals/… URL
 * (lib/owenAi PICTURE_LINK, or the server's own `pictures[].url`); Escape or a click closes it.
 */
export function PictureThumb({ src, label, width = 260 }: { src: string; label: string; width?: number }) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button type="button" onClick={() => setOpen(true)} title={`${label} — click to zoom`}
        style={{ display: 'inline-flex', flexDirection: 'column', gap: 4, margin: '4px 8px 4px 0', padding: 0,
          background: 'transparent', border: 0, cursor: 'zoom-in', verticalAlign: 'top', maxWidth: '100%' }}>
        <img src={src} alt={label} loading="lazy"
          style={{ width, maxWidth: '100%', aspectRatio: '3 / 1', objectFit: 'cover', borderRadius: 6,
            border: `1px solid ${LINE}`, background: 'rgb(243,244,246)' }} />
        <span style={{ fontSize: 12, color: MUTED, textAlign: 'left' }}>{label}</span>
      </button>
      {open && <PictureZoom src={src} label={label} onClose={() => setOpen(false)} />}
    </>
  )
}

export function PictureZoom({ src, label, onClose }: { src: string; label: string; onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  return (
    <div role="dialog" aria-modal="true" aria-label={label} onClick={onClose}
      style={{ position: 'fixed', inset: 0, zIndex: 1000, background: 'rgba(16,24,40,0.85)', display: 'flex',
        flexDirection: 'column', alignItems: 'center', justifyContent: 'center', padding: 16, cursor: 'zoom-out' }}>
      <img src={src} alt={label} style={{ maxWidth: '100%', maxHeight: 'calc(100vh - 80px)', borderRadius: 6,
        background: '#fff' }} />
      <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginTop: 10, color: '#fff', fontSize: 13 }}>
        <span>{label}</span>
        <button type="button" onClick={onClose}
          style={{ fontSize: 13, color: '#fff', background: BLUE, borderRadius: 8, padding: '4px 12px' }}>Close</button>
      </div>
    </div>
  )
}
