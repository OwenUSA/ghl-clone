/**
 * A picture message, as the thread can show it (2026-09-15).
 *
 * Imports nothing, so node executes it in `backend/tests/test_new_message_ui.py`.
 *
 * WHAT THE EVENT ACTUALLY CARRIES, read from owen-main's source
 * (`integrations/crm/events.py` `message_body` / `to_crm_message_event`): an inbound MMS
 * reaches `POST /api/events` as `type: "SMS"` with the customer's words and an appended
 * note — `"[2 attachments — view in OWEN]"` — and NO media URLs. The CRM's event row has
 * no media column, and owen-main sends none. So the thread cannot show the images; it
 * shows the count as an attachment line, plainly, instead of a bracketed machine note in
 * the middle of the customer's text. Showing the pictures needs owen-main to send the
 * media (a URL the CRM can relay, like call recordings) and a column to keep it in.
 */

export type MmsSplit = { text: string; attachments: number }

const NOTE = /\s*\[(\d+) attachments? — view in OWEN\]\s*$/

export function splitMmsNote(body: string | null | undefined): MmsSplit {
  const text = String(body ?? '')
  const m = text.match(NOTE)
  if (!m) return { text, attachments: 0 }
  return { text: text.slice(0, m.index).trimEnd(), attachments: Number(m[1]) }
}

export function attachmentLabel(count: number): string {
  return `${count} attachment${count === 1 ? '' : 's'} — view in OWEN. `
    + 'The phone system does not pass pictures on to the CRM.'
}
