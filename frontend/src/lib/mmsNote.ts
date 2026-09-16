/**
 * A picture message, as the thread shows it (2026-09-15, amended 2026-09-16).
 *
 * Imports nothing, so node executes it in `backend/tests/test_new_message_ui.py` and
 * `backend/tests/test_message_pictures.py`.
 *
 * WHAT THE EVENT CARRIES. owen-main relays an inbound MMS as `type: "SMS"` with the
 * customer's words, an appended note — `"[2 attachments — view in OWEN]"` — and, since
 * 2026-09-16, `num_media`. The CRM creates an attachment row per picture, fetches the bytes
 * from owen-main and serves them itself, so the thread now shows the PICTURES.
 *
 * THE NOTE IS STILL STRIPPED, and it is still used. owen-main deliberately kept appending
 * it (`integrations/crm/events.message_body`), because an older CRM deploy that cannot show
 * pictures must not render a blank bubble. So:
 *
 *   * pictures on the event  -> show them; the note is machine text and is removed;
 *   * no pictures, a note    -> show the count, exactly as before this feature existed;
 *   * neither                -> just the words.
 *
 * That third case is not hypothetical: it is every message on the thread.
 */

export type MmsSplit = { text: string; attachments: number }

const NOTE = /\s*\[(\d+) attachments? — view in OWEN\]\s*$/

export function splitMmsNote(body: string | null | undefined): MmsSplit {
  const text = String(body ?? '')
  const m = text.match(NOTE)
  if (!m) return { text, attachments: 0 }
  return { text: text.slice(0, m.index).trimEnd(), attachments: Number(m[1]) }
}

/**
 * The fallback line, for a message whose pictures the CRM does not have.
 *
 * Reworded on 2026-09-16: it used to end "The phone system does not pass pictures on to the
 * CRM", which was true and is now false. It says the honest thing for the only case that
 * still reaches it — the count is known and the pictures are not here.
 */
export function attachmentLabel(count: number): string {
  return `${count} picture${count === 1 ? '' : 's'} — not saved to this thread.`
}
