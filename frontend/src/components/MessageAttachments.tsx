/**
 * Pictures in a thread bubble, and the viewer they open (2026-09-16).
 *
 * The owner's decision: "thumbnails in the bubble, click for a large viewer with
 * next/previous, sender and time".
 *
 * ## No GoHighLevel screenshot shows this
 *
 * `refs/round3` and `refs/opps` have no MMS bubble in them, so the layout follows
 * GoHighLevel's own conventions as they appear everywhere else in this rebuild rather
 * than being invented: the thumbnail's 8px radius and 1px rgb(234,236,240) hairline are
 * the card border used across Opportunities, the viewer's chrome is the same
 * rgb(102,112,133) on white the thread's own header uses, and the overlay is the scrim
 * behind every modal in this product. Every assumption is listed in the sentinel.
 *
 * ## Three states, because there are three things that can be true
 *
 *   * the bytes are here            — the picture, and it opens
 *   * they are on the way           — a placeholder that says so; the thread is polling
 *   * they are not coming           — "Picture unavailable", the sentence saying why, and
 *                                     a Retry when trying again could actually work
 *
 * A failed picture never takes the words with it. The text of the message is rendered by
 * the caller, above this, and nothing here can stop it.
 */
import { useCallback, useEffect, useState } from 'react'

import { type Attachment, retryAttachment } from '../lib/api'
import { IconChevronLeft, IconChevronRight, IconClose, IconImage, IconRetry } from './Icon'

const BORDER = 'rgb(234,236,240)'
const MUTED = 'rgb(102,112,133)'

/** A thumbnail's box. Square so a row of them lines up whatever shape the photos are —
 *  a roof is usually landscape and a leak in a ceiling is usually not. */
const THUMB = 104

export function MessageAttachments({
  items,
  who,
  when,
  onChanged,
}: {
  items: Attachment[]
  /** Who sent it, for the viewer's header: a name, a number, or "You". */
  who: string
  /** The message's own timestamp, already formatted by the thread. */
  when: string
  /** Called after a Retry changes an attachment, so the thread can refetch. */
  onChanged?: () => void
}) {
  const [open, setOpen] = useState<number | null>(null)
  const [retrying, setRetrying] = useState<number | null>(null)

  if (!items.length) return null

  // Only pictures that are actually here can be paged through. A viewer that stopped on a
  // grey box would make next/previous feel broken rather than honest.
  const viewable = items.filter((a) => a.url)

  const onRetry = async (a: Attachment) => {
    setRetrying(a.id)
    try {
      await retryAttachment(a.id)
    } finally {
      setRetrying(null)
      onChanged?.()
    }
  }

  return (
    <>
      <div
        data-testid="message-pictures"
        data-count={items.length}
        className="flex flex-wrap gap-1"
        style={{ marginTop: 6 }}
      >
        {items.map((a) => {
          if (a.url) {
            const index = viewable.findIndex((v) => v.id === a.id)
            return (
              <button
                key={a.id}
                type="button"
                data-testid="picture-thumb"
                data-attachment={a.id}
                onClick={() => setOpen(index)}
                aria-label={`Open picture ${index + 1} of ${viewable.length}`}
                style={{
                  width: THUMB, height: THUMB, borderRadius: 8, overflow: 'hidden',
                  border: `1px solid ${BORDER}`, padding: 0, cursor: 'pointer',
                  backgroundColor: '#fff', display: 'block',
                }}
              >
                <img
                  src={a.url}
                  alt={a.filename ?? 'Picture'}
                  style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}
                />
              </button>
            )
          }
          return (
            <div
              key={a.id}
              data-testid="picture-unavailable"
              data-attachment={a.id}
              data-status={a.status}
              style={{
                width: THUMB * 2, minHeight: THUMB, borderRadius: 8,
                border: `1px solid ${BORDER}`, backgroundColor: 'rgb(249,250,251)',
                padding: 10, fontSize: 12, color: MUTED,
              }}
            >
              <div className="flex items-center gap-1" style={{ marginBottom: 4 }}>
                <IconImage size={14} color={MUTED} />
                <span style={{ fontWeight: 600 }}>
                  {a.pending ? 'Picture on its way…' : 'Picture unavailable'}
                </span>
              </div>
              {a.detail && <div>{a.detail}</div>}
              {a.retryable && (
                <button
                  type="button"
                  data-testid="picture-retry"
                  onClick={() => void onRetry(a)}
                  disabled={retrying === a.id}
                  className="inline-flex items-center gap-1"
                  style={{
                    marginTop: 6, fontSize: 12, fontWeight: 600,
                    color: 'rgb(0,78,235)', opacity: retrying === a.id ? 0.5 : 1,
                  }}
                >
                  <IconRetry size={12} color="rgb(0,78,235)" />
                  {retrying === a.id ? 'Fetching…' : 'Retry'}
                </button>
              )}
            </div>
          )
        })}
      </div>
      {open != null && viewable[open] && (
        <PictureViewer
          items={viewable}
          index={open}
          who={who}
          when={when}
          onIndex={setOpen}
          onClose={() => setOpen(null)}
        />
      )}
    </>
  )
}

/**
 * The large view. One picture, who sent it, when, and a way to the next.
 *
 * Rendered inline rather than through a portal: this app has no portal root and every
 * other overlay in it (the search palette, the in-call window) is a fixed-position div in
 * the tree. Consistency beats introducing a second mechanism for one screen.
 */
function PictureViewer({
  items, index, who, when, onIndex, onClose,
}: {
  items: Attachment[]
  index: number
  who: string
  when: string
  onIndex: (i: number) => void
  onClose: () => void
}) {
  const go = useCallback(
    (delta: number) => {
      // Clamped, not wrapped. Wrapping past the last picture back to the first makes it
      // impossible to tell, without counting, whether you have seen them all.
      const next = Math.min(items.length - 1, Math.max(0, index + delta))
      onIndex(next)
    },
    [index, items.length, onIndex],
  )

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
      if (e.key === 'ArrowRight') go(1)
      if (e.key === 'ArrowLeft') go(-1)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [go, onClose])

  const current = items[index]

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Picture"
      data-testid="picture-viewer"
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, zIndex: 80,
        backgroundColor: 'rgba(16,24,40,0.85)',
        display: 'flex', flexDirection: 'column',
      }}
    >
      {/* The header carries the two facts the owner asked for — who and when — so a
          picture opened from a long thread is still attributable without closing it. */}
      <div
        className="flex shrink-0 items-center justify-between"
        onClick={(e) => e.stopPropagation()}
        style={{ padding: '12px 16px', color: '#fff' }}
      >
        <div style={{ fontSize: 14 }}>
          <span data-testid="viewer-who" style={{ fontWeight: 600 }}>{who}</span>
          <span data-testid="viewer-when" style={{ marginLeft: 8, opacity: 0.75 }}>{when}</span>
        </div>
        <div className="flex items-center gap-3">
          <span data-testid="viewer-counter" style={{ fontSize: 13, opacity: 0.75 }}>
            {index + 1} of {items.length}
          </span>
          <button type="button" onClick={onClose} aria-label="Close" title="Close">
            <IconClose size={20} color="#fff" />
          </button>
        </div>
      </div>

      <div className="flex min-h-0 flex-1 items-center justify-between gap-2"
        style={{ padding: '0 8px 16px' }}>
        <ViewerArrow
          side="previous"
          disabled={index === 0}
          onClick={(e) => { e.stopPropagation(); go(-1) }}
        />
        <img
          data-testid="viewer-image"
          data-attachment={current.id}
          src={current.url ?? ''}
          alt={current.filename ?? 'Picture'}
          onClick={(e) => e.stopPropagation()}
          style={{ maxWidth: '100%', maxHeight: '100%', objectFit: 'contain',
                   borderRadius: 4, backgroundColor: 'rgb(16,24,40)' }}
        />
        <ViewerArrow
          side="next"
          disabled={index === items.length - 1}
          onClick={(e) => { e.stopPropagation(); go(1) }}
        />
      </div>
    </div>
  )
}

function ViewerArrow({ side, disabled, onClick }: {
  side: 'previous' | 'next'
  disabled: boolean
  onClick: (e: React.MouseEvent) => void
}) {
  const Glyph = side === 'next' ? IconChevronRight : IconChevronLeft
  return (
    <button
      type="button"
      data-testid={`viewer-${side}`}
      aria-label={side === 'next' ? 'Next picture' : 'Previous picture'}
      disabled={disabled}
      onClick={onClick}
      className="flex shrink-0 items-center justify-center"
      style={{
        width: 40, height: 40, borderRadius: '50%',
        backgroundColor: 'rgba(255,255,255,0.12)',
        // Kept in place rather than removed when there is nowhere to go: a control that
        // vanishes shifts the picture sideways under the cursor mid-browse.
        opacity: disabled ? 0.25 : 1,
        cursor: disabled ? 'default' : 'pointer',
      }}
    >
      <Glyph size={22} color="#fff" />
    </button>
  )
}
