/**
 * Attaching pictures to an outgoing text (2026-09-16).
 *
 * One hook and one strip, shared by the thread composer and the "New message" dialog, so
 * the two cannot drift about what a picture costs, what is refused, or what "remove"
 * means. The owner asked for drag, paste or pick; all three land in the same place.
 *
 * ## Why the upload happens on PICK and not on SEND
 *
 * The composer has to SHOW the picture before it goes, and the server has to have the
 * bytes before it can send them. Uploading on pick does both at once: the preview is the
 * same bytes the customer will get, the refusal ("that is not a picture", "that is 8 MB")
 * arrives while the operator is still looking at the file picker, and editing the sentence
 * afterwards does not re-upload a 4 MB photo on every keystroke.
 *
 * The cost is a draft row and a file on disk for a message that may never be sent. That is
 * the right side of the trade — `DELETE /api/attachments/{id}` removes both the moment the
 * operator takes it back off, and a send that is refused KEEPS them, so they do not have to
 * pick the pictures again to fix a phone number.
 *
 * ## The preview is the uploaded picture, not a local blob
 *
 * `URL.createObjectURL(file)` would render instantly and would be a different picture from
 * the one on the server. Pointing the preview at `/api/attachments/{id}` means what the
 * operator is looking at is literally what will be sent — and it exercises the same
 * authenticated bytes route the thread uses, so a broken preview is a real bug found
 * before a customer is involved rather than after.
 */
import { useCallback, useRef, useState } from 'react'

import { ApiError, type Attachment, deleteAttachment, uploadAttachment } from '../lib/api'
import { IconClose, IconImage, IconPaperclip } from './Icon'

/** Kept in step with `attachments.MAX_OUTBOUND_ATTACHMENTS` on the server, which is the
 *  one that actually refuses. This is the explanation, not the gate. */
export const MAX_PICTURES = 5

const REFUSE_COUNT = `A text can carry ${MAX_PICTURES} pictures. Remove one to add another.`

export type PictureTray = {
  items: Attachment[]
  ids: number[]
  busy: boolean
  problem: string | null
  /** Add files from a picker, a drop or a paste. Non-images are refused by the server. */
  add: (files: FileList | File[] | null) => Promise<void>
  remove: (id: number) => Promise<void>
  /** Forget them WITHOUT deleting — after a successful send, when they now belong to a
   *  message and deleting them would take the picture off the thread. */
  clear: () => void
  setProblem: (text: string | null) => void
}

export function usePictureTray(): PictureTray {
  const [items, setItems] = useState<Attachment[]>([])
  const [busy, setBusy] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)

  const add = useCallback(async (files: FileList | File[] | null) => {
    const list = Array.from(files ?? [])
    if (!list.length) return
    setProblem(null)
    setBusy(true)
    try {
      for (const file of list) {
        // Counted against what is ALREADY up rather than against the batch, because each
        // upload is its own request: dropping seven pictures attaches the first five and
        // says why, which is more useful than refusing all seven.
        let stop = false
        setItems((current) => {
          if (current.length >= MAX_PICTURES) stop = true
          return current
        })
        if (stop) {
          setProblem(REFUSE_COUNT)
          break
        }
        try {
          const saved = await uploadAttachment(file)
          setItems((current) => (current.length >= MAX_PICTURES ? current : [...current, saved]))
        } catch (err) {
          // The server's sentence, verbatim: it is the one that knows what it sniffed.
          setProblem(err instanceof ApiError ? err.message : 'That picture could not be added.')
          break
        }
      }
    } finally {
      setBusy(false)
    }
  }, [])

  const remove = useCallback(async (id: number) => {
    // Dropped from the strip first so the click feels immediate; the delete is the
    // server catching up. A delete that fails leaves an orphan draft nobody can see,
    // which is a wasted file rather than a wrong message.
    setItems((current) => current.filter((a) => a.id !== id))
    setProblem(null)
    try {
      await deleteAttachment(id)
    } catch {
      /* the picture is already off the composer; nothing to tell the operator */
    }
  }, [])

  const clear = useCallback(() => {
    setItems([])
    setProblem(null)
  }, [])

  return { items, ids: items.map((a) => a.id), busy, problem, add, remove, clear, setProblem }
}

/** The paperclip. Opens the file picker; `accept` narrows it to the types the server takes. */
export function AttachButton({ tray, disabled, title }: {
  tray: PictureTray
  disabled?: boolean
  title?: string
}) {
  const input = useRef<HTMLInputElement | null>(null)
  const full = tray.items.length >= MAX_PICTURES
  const off = Boolean(disabled) || full || tray.busy
  return (
    <>
      <input
        ref={input}
        type="file"
        data-testid="attach-input"
        accept="image/jpeg,image/png,image/gif,image/webp,image/heic,image/heif"
        multiple
        hidden
        onChange={(e) => {
          void tray.add(e.target.files)
          // Cleared so picking the SAME file twice in a row still fires a change event.
          e.target.value = ''
        }}
      />
      <button
        type="button"
        data-testid="attach-picture"
        aria-label="Attach a picture"
        title={full ? REFUSE_COUNT : (title ?? 'Attach a picture')}
        disabled={off}
        onClick={() => input.current?.click()}
        className="flex shrink-0 items-center justify-center"
        style={{ width: 32, height: 32, opacity: off ? 0.4 : 1,
                 cursor: off ? 'not-allowed' : 'pointer' }}
      >
        <IconPaperclip size={18} color="rgb(102,112,133)" />
      </button>
    </>
  )
}

/**
 * The strip of what is attached, above the message box. Hidden when there is nothing —
 * an empty tray taking up 80px above every composer would be a permanent reminder of a
 * feature almost no message uses.
 */
export function PictureStrip({ tray }: { tray: PictureTray }) {
  if (!tray.items.length && !tray.problem && !tray.busy) return null
  return (
    <div data-testid="picture-strip" style={{ marginBottom: 6 }}>
      {(tray.items.length > 0 || tray.busy) && (
        <div className="flex flex-wrap items-center gap-2">
          {tray.items.map((a) => (
            <div key={a.id} className="relative" style={{ width: 56, height: 56 }}>
              <img
                data-testid="picture-preview"
                data-attachment={a.id}
                src={a.url ?? ''}
                alt={a.filename ?? 'Picture'}
                style={{
                  width: 56, height: 56, objectFit: 'cover', borderRadius: 6,
                  border: '1px solid rgb(234,236,240)', display: 'block',
                }}
              />
              <button
                type="button"
                data-testid="picture-remove"
                data-attachment={a.id}
                aria-label={`Remove ${a.filename ?? 'picture'}`}
                title="Remove"
                onClick={() => void tray.remove(a.id)}
                className="absolute flex items-center justify-center"
                style={{
                  top: -6, right: -6, width: 18, height: 18, borderRadius: '50%',
                  backgroundColor: 'rgb(52,64,84)', border: '1px solid #fff',
                }}
              >
                <IconClose size={11} color="#fff" strokeWidth={2.4} />
              </button>
            </div>
          ))}
          {tray.busy && (
            <span data-testid="picture-busy" className="inline-flex items-center gap-1"
              style={{ fontSize: 12, color: 'rgb(102,112,133)' }}>
              <IconImage size={14} color="rgb(102,112,133)" />
              Adding…
            </span>
          )}
        </div>
      )}
      {tray.problem && (
        <div data-testid="picture-problem" role="status"
          style={{ marginTop: 6, fontSize: 13, color: 'rgb(180,35,24)' }}>
          {tray.problem}
        </div>
      )}
    </div>
  )
}

/**
 * Drag-and-drop and paste, as props to spread onto whatever element should accept them.
 *
 * Returned rather than rendered as a wrapper so each composer keeps its own measured
 * layout: the thread's tray is a 40px row inside an 8px band, and wrapping it in another
 * div to catch a drop would move it.
 */
export function dropHandlers(tray: PictureTray, disabled?: boolean) {
  const images = (list: DataTransferItemList | null | undefined) =>
    Array.from(list ?? []).filter((i) => i.kind === 'file')
      .map((i) => i.getAsFile())
      .filter((f): f is File => Boolean(f))
  return {
    onDragOver: (e: React.DragEvent) => {
      if (disabled) return
      // Without this the browser navigates to the dropped file and the operator loses
      // whatever they had typed.
      e.preventDefault()
    },
    onDrop: (e: React.DragEvent) => {
      if (disabled) return
      const files = Array.from(e.dataTransfer?.files ?? [])
      if (!files.length) return
      e.preventDefault()
      void tray.add(files)
    },
    onPaste: (e: React.ClipboardEvent) => {
      if (disabled) return
      const files = images(e.clipboardData?.items)
      if (!files.length) return
      // Only when there IS an image: pasting text into the message box must still paste.
      e.preventDefault()
      void tray.add(files)
    },
  }
}
